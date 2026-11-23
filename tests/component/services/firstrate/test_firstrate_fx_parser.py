"""Component tests for the FirstRate spot FX parser (FX M2, task 2.1).

FirstRate FX files are headerless CSVs in US/Eastern wall clock with a compact date: intraday
rows are ``YYYYMMDD,HH:MM:SS,O,H,L,C,V`` (7 fields) and daily rows ``YYYYMMDD,O,H,L,C,V``
(6 fields). Intraday bars are stamped at the bar open exactly like the futures parser; a daily
row dated ``D`` is stamped at its session open, ``D-1 17:00`` New York (decision D5). Every
price is emitted at the pair spec's precision (5 or 3), and rows changed by rounding or
outside the Sunday 17:00 to Friday 17:00 week are counted, never dropped (D14).
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass
from src.services.firstrate.parsers import get_parser
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser
from src.services.firstrate.parsers.firstrate_futures_parser import FirstRateFuturesParser
from src.services.firstrate.parsers.firstrate_fx_parser import FirstRateFxParser

pytestmark = pytest.mark.component

EUR_HOUR = BarType.from_str("EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL")
EUR_DAY = BarType.from_str("EUR/USD.IDEALPRO-1-DAY-MID-EXTERNAL")
JPY_HOUR = BarType.from_str("USD/JPY.IDEALPRO-1-HOUR-MID-EXTERNAL")
ES_MIN = BarType.from_str("ES.CME-1-MINUTE-LAST-EXTERNAL")


def _write(tmp_path: Path, lines: list[str], name: str = "EURUSD_full_1hour.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def _utc_ns(text: str) -> int:
    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000


def _parse(tmp_path: Path, lines: list[str], bar_type: BarType = EUR_HOUR, **kw):
    parser = FirstRateFxParser(**kw)
    path = _write(tmp_path, lines)
    return parser, parser.parse_file(path, bar_type.instrument_id, bar_type)


class TestRegistration:
    def test_registered_for_fx(self):
        assert isinstance(get_parser(AssetClass.FX), FirstRateFxParser)

    def test_other_parsers_unchanged(self):
        assert type(get_parser(AssetClass.FUTURES)) is FirstRateFuturesParser
        assert isinstance(get_parser(AssetClass.STOCK), FirstRateCsvParser)
        assert isinstance(get_parser(AssetClass.ETF), FirstRateCsvParser)

    def test_maps_pair_to_idealpro_id(self):
        parser = FirstRateFxParser()
        assert parser.map_instrument_id("EURUSD", "1-HOUR-MID") == InstrumentId.from_str(
            "EUR/USD.IDEALPRO"
        )
        assert str(parser.map_instrument_id("usdjpy", "1-DAY-MID")) == "USD/JPY.IDEALPRO"


class TestRowShapes:
    def test_seven_field_intraday_row(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["20250707,10:00:00,1.17,1.1712,1.1695,1.17085,321"])
        assert bar.ts_event == bar.ts_init == _utc_ns("2025-07-07T14:00:00")
        assert (str(bar.open), str(bar.high), str(bar.low), str(bar.close)) == (
            "1.17000",
            "1.17120",
            "1.16950",
            "1.17085",
        )
        assert bar.volume.as_double() == 321

    def test_six_field_daily_row(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["20250708,1.17,1.18,1.16,1.175,1000"], EUR_DAY)
        assert str(bar.close) == "1.17500"
        assert bar.volume.as_double() == 1000

    def test_zero_volume_is_valid(self, tmp_path):
        parser, bars = _parse(tmp_path, ["20250707,10:00:00,1.17,1.17,1.17,1.17,0"])
        assert len(bars) == 1 and bars[0].volume.as_double() == 0
        assert parser.last_validation.valid


class TestTimestamps:
    @pytest.mark.parametrize(
        "wall_clock",
        [
            "2025-03-07 16:59:00",  # last winter Friday minute (EST)
            "2025-03-09 01:30:00",  # before spring-forward
            "2025-03-09 02:30:00",  # non-existent hour (shifted forward, like futures)
            "2025-03-09 17:00:00",  # first EDT Sunday open
            "2025-11-02 01:30:00",  # ambiguous hour (first, daylight occurrence)
            "2025-11-02 17:00:00",  # first EST Sunday open
            "2025-07-06 17:00:00",
            "2025-01-05 17:00:00",
        ],
    )
    def test_et_to_utc_matches_futures_parser(self, tmp_path, wall_clock):
        compact = wall_clock[:10].replace("-", "") + "," + wall_clock[11:]
        _, [fx_bar] = _parse(tmp_path, [f"{compact},1.1,1.2,1.0,1.1,1"])
        futures_path = _write(tmp_path, [f"{wall_clock},10,11,9,10,1"], "ES_full_1min_x.txt")
        [es_bar] = FirstRateFuturesParser().parse_file(futures_path, ES_MIN.instrument_id, ES_MIN)
        assert fx_bar.ts_event == es_bar.ts_event

    def test_sunday_open_is_2100_utc_in_summer_2200_in_winter(self, tmp_path):
        rows = ["20250105,17:00:00,1.1,1.2,1.0,1.1,1", "20250706,17:00:00,1.1,1.2,1.0,1.1,1"]
        _, bars = _parse(tmp_path, rows)
        assert [b.ts_event for b in bars] == [
            _utc_ns("2025-01-05T22:00:00"),
            _utc_ns("2025-07-06T21:00:00"),
        ]

    @pytest.mark.parametrize(
        ("row_date", "expected_utc"),
        [
            ("20250707", "2025-07-06T21:00:00"),  # Monday -> Sunday 17:00 EDT
            ("20250108", "2025-01-07T22:00:00"),  # Wednesday -> Tuesday 17:00 EST
            ("20250310", "2025-03-09T21:00:00"),  # Monday after spring-forward -> EDT open
            ("20251103", "2025-11-02T22:00:00"),  # Monday after fall-back -> EST open
        ],
    )
    def test_daily_row_stamped_at_previous_day_1700_et(self, tmp_path, row_date, expected_utc):
        _, [bar] = _parse(tmp_path, [f"{row_date},1.1,1.2,1.0,1.1,10"], EUR_DAY)
        assert bar.ts_event == bar.ts_init == _utc_ns(expected_utc)


class TestPrecision:
    def test_stripped_zeros_are_padded(self, tmp_path):
        parser, [bar] = _parse(tmp_path, ["20100103,17:00:00,1.43,1.43359,1.42964,1.43241,1"])
        assert str(bar.open) == "1.43000"
        assert {bar.open.precision, bar.close.precision} == {5}
        assert parser.rounded_rows == 0

    def test_usdjpy_float_noise_is_rounded_and_counted(self, tmp_path):
        rows = [
            "20140102,10:00:00,108.33599,108.4,107.9,107.96999,5",
            "20140102,11:00:00,108.336,108.4,108.2,108.3,5",
        ]
        parser, bars = _parse(tmp_path, rows, JPY_HOUR)
        assert str(bars[0].open) == "108.336"
        assert str(bars[0].close) == "107.970"
        assert {b.high.precision for b in bars} == {3}
        assert parser.rounded_rows == 1

    def test_rounding_is_half_even(self, tmp_path):
        rows = ["20140102,10:00:00,108.3365,108.4,108.2,108.3375,5"]
        _, [bar] = _parse(tmp_path, rows, JPY_HOUR, chunk_rows=10)
        assert (str(bar.open), str(bar.close)) == ("108.336", "108.338")

    def test_decimal_ties_round_half_even_not_by_float_scaling(self, tmp_path):
        # 128.0105 * 1000 lands just above 128010.5 in binary, so float rounding gives 128.011.
        rows = ["20140102,10:00:00,128.0105,128.0115,128.0005,128.0105,5"]
        parser, [bar] = _parse(tmp_path, rows, JPY_HOUR)
        assert (str(bar.open), str(bar.high), str(bar.low), str(bar.close)) == (
            "128.010",
            "128.012",
            "128.000",
            "128.010",
        )
        assert parser.rounded_rows == 1

    def test_on_grid_prices_are_unchanged(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["20140102,10:00:00,128.01,128.5,127.999,128.4,5"], JPY_HOUR)
        assert (str(bar.open), str(bar.low)) == ("128.010", "127.999")


class TestSessions:
    def test_out_of_session_bars_are_kept_and_counted(self, tmp_path):
        rows = [
            "20230804,16:00:00,1.1,1.2,1.0,1.1,1",  # Friday 16:00: last in-session hour
            "20230804,17:00:00,1.1,1.2,1.0,1.1,1",  # Friday 17:00: after the close
            "20230805,19:00:00,1.1,1.2,1.0,1.1,1",  # Saturday (the real EURUSD anomaly)
            "20230806,02:00:00,1.1,1.2,1.0,1.1,1",  # Sunday before the open
            "20230806,16:00:00,1.1,1.2,1.0,1.1,1",  # Sunday before the open
            "20230806,17:00:00,1.1,1.2,1.0,1.1,1",  # Sunday open
            "20230809,03:00:00,1.1,1.2,1.0,1.1,1",  # Wednesday night
        ]
        parser, bars = _parse(tmp_path, rows)
        assert len(bars) == 7
        assert parser.session_anomalies == 4

    def test_sunday_dated_daily_row_is_an_anomaly(self, tmp_path):
        rows = ["20230806,1.1,1.2,1.0,1.1,1", "20230807,1.1,1.2,1.0,1.1,1"]
        parser, bars = _parse(tmp_path, rows, EUR_DAY)
        assert len(bars) == 2
        assert parser.session_anomalies == 1


class TestChunking:
    def _rows(self, count: int) -> list[str]:
        return [f"20250707,{h:02d}:00:00,1.1,1.2,1.0,1.1,{h}" for h in range(count)]

    def test_chunks_cover_every_row_in_order(self, tmp_path):
        path = _write(tmp_path, self._rows(10))
        parser = FirstRateFxParser(chunk_rows=3)
        chunks = list(parser.iter_chunks(path, EUR_HOUR))
        assert [len(c.bars) for c in chunks] == [3, 3, 3, 1]
        stamps = [b.ts_init for c in chunks for b in c.bars]
        assert stamps == sorted(stamps) and len(set(stamps)) == 10
        assert parser.last_validation.row_count == 10

    def test_counters_total_across_chunks(self, tmp_path):
        rows = [
            "20230805,19:00:00,108.33599,108.4,108.2,108.3,1",
            "20230806,16:00:00,108.3,108.4,107.9,107.96999,1",
            "20230807,10:00:00,108.3,108.4,108.2,108.3,1",
        ]
        parser = FirstRateFxParser(chunk_rows=1)
        list(parser.iter_chunks(_write(tmp_path, rows), JPY_HOUR))
        assert (parser.rounded_rows, parser.session_anomalies) == (2, 2)

    def test_duplicate_timestamp_keeps_last(self, tmp_path):
        rows = ["20250707,10:00:00,1.1,1.2,1.0,1.1,1", "20250707,10:00:00,1.1,1.3,1.0,1.2,2"]
        _, bars = _parse(tmp_path, rows)
        assert len(bars) == 1 and str(bars[0].close) == "1.20000"

    def test_rows_behind_previous_chunk_are_dropped_and_counted(self, tmp_path):
        rows = self._rows(4) + ["20250707,01:00:00,1.1,1.2,1.0,1.1,99"]
        chunks = list(FirstRateFxParser(chunk_rows=4).iter_chunks(_write(tmp_path, rows), EUR_HOUR))
        assert [len(c.bars) for c in chunks] == [4, 0]
        assert chunks[1].out_of_order == 1


class TestValidation:
    def test_invalid_rows_dropped_and_reported(self, tmp_path):
        rows = [
            "",
            "20250707,10:00:00,1.1,1.2,1.0,1.1,1\r",
            "20250707,11:00:00,1.1,1.0,1.2,1.1,1",  # high < low
            "20250707,12:00:00,-1.1,1.2,-1.0,1.1,1",  # non-positive
            "20250707,13:00:00,abc,1.2,1.0,1.1,1",  # non-numeric
            "2025-07-07,14:00:00,1.1,1.2,1.0,1.1,1",  # not a compact date
            "20250707,25:00:00,1.1,1.2,1.0,1.1,1",  # not a time
            "20250707,15:00:00,1.1,1.2,1.0,1.1,-5",  # negative volume
            "20250707,16:00:00,1.1,1.2,1.0",  # short row
            "20250707,17:00:00,1.1,1.2,1.0,1.1,7",
        ]
        parser, bars = _parse(tmp_path, rows)
        assert [b.volume.as_double() for b in bars] == [1, 7]
        validation = parser.last_validation
        assert validation.row_count == 9
        assert validation.invalid_rows == 7
        assert not validation.valid and validation.errors

    def test_empty_file(self, tmp_path):
        path = tmp_path / "EURUSD_full_1hour.txt"
        path.write_text("\n\n")
        parser = FirstRateFxParser()
        assert parser.parse_file(path, EUR_HOUR.instrument_id, EUR_HOUR) == []
        assert parser.last_validation.row_count == 0
        assert (parser.rounded_rows, parser.session_anomalies) == (0, 0)
