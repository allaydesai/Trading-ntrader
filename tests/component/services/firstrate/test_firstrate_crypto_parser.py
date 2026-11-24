"""Component tests for the FirstRate crypto parser (crypto M2, task 2.1).

FirstRate crypto files use the equity layout (intraday ``YYYY-MM-DD HH:MM:SS,O,H,L,C,V``,
daily ``YYYY-MM-DD,O,H,L,C,V``) but, unlike every other FirstRate series, are in **UTC**:
no localisation and no DST rules. Bars are stamped at the bar open; a daily row dated ``D``
opens at ``D 00:00 UTC`` (decision C10). Prices are emitted at the coin spec's precision (4),
rounded half-even and counted. Volume is coin units at 8 decimals (the M1 spike: the bar
volume precision must equal the instrument size precision). Gaps longer than ten bars,
zero-volume bars and the switch to whole-coin volumes are counted, never dropped (C15).
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass
from src.services.firstrate.parsers import get_parser
from src.services.firstrate.parsers.firstrate_crypto_parser import FirstRateCryptoParser
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser
from src.services.firstrate.parsers.firstrate_futures_parser import FirstRateFuturesParser
from src.services.firstrate.parsers.firstrate_fx_parser import FirstRateFxParser

pytestmark = pytest.mark.component

BTC_HOUR = BarType.from_str("BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL")
BTC_MIN = BarType.from_str("BTC/USD.KRAKEN-1-MINUTE-LAST-EXTERNAL")
BTC_DAY = BarType.from_str("BTC/USD.KRAKEN-1-DAY-LAST-EXTERNAL")
SOL_DAY = BarType.from_str("SOL/USD.KRAKEN-1-DAY-LAST-EXTERNAL")


def _write(tmp_path: Path, lines: list[str], name: str = "BTC_full_1hour.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def _utc_ns(text: str) -> int:
    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000


def _parse(tmp_path: Path, lines: list[str], bar_type: BarType = BTC_HOUR, **kw):
    parser = FirstRateCryptoParser(**kw)
    path = _write(tmp_path, lines)
    return parser, parser.parse_file(path, bar_type.instrument_id, bar_type)


def _hours(day: str, count: int = 24, volume: str = "1.5") -> list[str]:
    return [f"{day} {h:02d}:00:00,100,101,99,100.5,{volume}" for h in range(count)]


class TestRegistration:
    def test_registered_for_crypto(self):
        assert isinstance(get_parser(AssetClass.CRYPTO), FirstRateCryptoParser)

    def test_other_parsers_unchanged(self):
        assert type(get_parser(AssetClass.FUTURES)) is FirstRateFuturesParser
        assert type(get_parser(AssetClass.FX)) is FirstRateFxParser
        assert isinstance(get_parser(AssetClass.STOCK), FirstRateCsvParser)
        assert isinstance(get_parser(AssetClass.ETF), FirstRateCsvParser)

    def test_maps_coin_to_kraken_id(self):
        parser = FirstRateCryptoParser()
        assert parser.map_instrument_id("BTC", "1-HOUR-LAST") == InstrumentId.from_str(
            "BTC/USD.KRAKEN"
        )
        assert str(parser.map_instrument_id("sol", "1-DAY-LAST")) == "SOL/USD.KRAKEN"


class TestRowShapes:
    def test_intraday_row(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["2024-07-01 14:00:00,62000.5,62100,61950.25,62050,12.3456"])
        assert bar.ts_event == bar.ts_init == _utc_ns("2024-07-01T14:00:00")
        assert (str(bar.open), str(bar.high), str(bar.low), str(bar.close)) == (
            "62000.5000",
            "62100.0000",
            "61950.2500",
            "62050.0000",
        )
        assert str(bar.volume) == "12.34560000"

    def test_daily_row_opens_at_utc_midnight(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["2026-05-01,78000,79000,77000,78500,14000.5"], BTC_DAY)
        assert bar.ts_event == bar.ts_init == _utc_ns("2026-05-01T00:00:00")
        assert str(bar.close) == "78500.0000"


class TestUtc:
    @pytest.mark.parametrize("dst_sunday", ["2024-03-10", "2024-11-03", "2021-03-14"])
    def test_us_dst_sunday_has_24_unshifted_hours(self, tmp_path, dst_sunday):
        _, bars = _parse(tmp_path, _hours(dst_sunday))
        assert [b.ts_event for b in bars] == [
            _utc_ns(f"{dst_sunday}T{h:02d}:00:00") for h in range(24)
        ]

    def test_winter_and_summer_rows_are_not_shifted(self, tmp_path):
        rows = ["2025-01-05 09:30:00,1,1,1,1,1", "2025-07-06 09:30:00,1,1,1,1,1"]
        _, bars = _parse(tmp_path, rows)
        assert [b.ts_event for b in bars] == [
            _utc_ns("2025-01-05T09:30:00"),
            _utc_ns("2025-07-06T09:30:00"),
        ]


class TestPrecision:
    def test_five_decimal_price_rounds_half_even_and_counts(self, tmp_path):
        rows = [
            "2016-08-03 10:00:00,534.96827,535,534.5,534.96825,1.2",  # ...825 is a tie
            "2016-08-03 10:01:00,534.9683,535,534.5,534.97,1.2",
        ]
        parser, bars = _parse(tmp_path, rows, BTC_MIN)
        assert (str(bars[0].open), str(bars[0].close)) == ("534.9683", "534.9682")
        assert {b.open.precision for b in bars} == {4}
        assert parser.anomalies.rounded_rows == 1

    def test_stripped_zeros_are_padded_not_counted(self, tmp_path):
        parser, [bar] = _parse(tmp_path, ["2024-01-01 00:00:00,42000.1,42001,41999,42000,3"])
        assert str(bar.open) == "42000.1000"
        assert parser.anomalies.rounded_rows == 0


class TestVolume:
    def test_fractional_volume_keeps_eight_decimals(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["2024-01-01 00:00:00,1,1,1,1,0.00012345"])
        assert bar.volume.precision == 8
        assert str(bar.volume) == "0.00012345"

    def test_whole_and_zero_volume_still_have_eight_decimals(self, tmp_path):
        rows = ["2026-04-01 00:00:00,1,1,1,1,2", "2026-04-01 00:01:00,1,1,1,1,0"]
        parser, bars = _parse(tmp_path, rows, BTC_MIN)
        assert [str(b.volume) for b in bars] == ["2.00000000", "0.00000000"]
        assert {b.volume.precision for b in bars} == {8}
        assert parser.anomalies.zero_volume == 1


class TestAnomalies:
    def test_gaps_longer_than_ten_bars_are_counted(self, tmp_path):
        rows = [
            "2024-01-01 00:00:00,1,1,1,1,1",
            "2024-01-01 11:00:00,1,1,1,1,1",  # 10 missing hours: not counted
            "2024-01-01 23:00:00,1,1,1,1,1",  # 11 missing hours: a gap
            "2024-01-03 00:00:00,1,1,1,1,1",  # 24 missing hours: a gap
        ]
        parser, bars = _parse(tmp_path, rows)
        assert len(bars) == 4
        assert parser.anomalies.gaps == 2
        assert parser.anomalies.longest_gap_bars == 24

    def test_gap_across_a_chunk_boundary_is_counted(self, tmp_path):
        rows = ["2024-01-01 00:00:00,1,1,1,1,1", "2024-01-02 00:00:00,1,1,1,1,1"]
        parser = FirstRateCryptoParser(chunk_rows=1)
        list(parser.iter_chunks(_write(tmp_path, rows), BTC_HOUR))
        assert (parser.anomalies.gaps, parser.anomalies.longest_gap_bars) == (1, 23)

    def test_whole_coin_volume_regime_is_found(self, tmp_path):
        rows = [
            "2026-03-20 22:00:00,1,1,1,1,3",  # whole but followed by a fractional bar
            "2026-03-20 23:00:00,1,1,1,1,0.5",
            "2026-03-21 00:00:00,1,1,1,1,2",
            "2026-03-21 01:00:00,1,1,1,1,0",
            "2026-03-21 02:00:00,1,1,1,1,7",
        ]
        parser = FirstRateCryptoParser(chunk_rows=2)
        list(parser.iter_chunks(_write(tmp_path, rows), BTC_HOUR))
        assert parser.anomalies.whole_volume_from_ns == _utc_ns("2026-03-21T00:00:00")

    def test_no_regime_when_the_last_bar_is_fractional(self, tmp_path):
        rows = ["2026-03-20 22:00:00,1,1,1,1,3", "2026-03-20 23:00:00,1,1,1,1,0.5"]
        parser, _ = _parse(tmp_path, rows)
        assert parser.anomalies.whole_volume_from_ns is None

    def test_no_regime_when_every_volume_is_whole(self, tmp_path):
        parser, _ = _parse(tmp_path, ["2026-03-21 00:00:00,1,1,1,1,2"])
        assert parser.anomalies.whole_volume_from_ns is None


class TestChunking:
    def test_chunks_cover_every_row_in_order(self, tmp_path):
        path = _write(tmp_path, _hours("2024-07-01", 10))
        parser = FirstRateCryptoParser(chunk_rows=3)
        chunks = list(parser.iter_chunks(path, BTC_HOUR))
        assert [len(c.bars) for c in chunks] == [3, 3, 3, 1]
        stamps = [b.ts_init for c in chunks for b in c.bars]
        assert stamps == sorted(stamps) and len(set(stamps)) == 10
        assert parser.last_validation.row_count == 10

    def test_counters_total_across_chunks(self, tmp_path):
        rows = [
            "2016-08-03 10:00:00,534.96827,535,534.5,534.9,0",
            "2016-08-03 10:01:00,534.9,535,534.5,534.96827,0",
            "2016-08-03 10:02:00,534.9,535,534.5,534.9,1",
        ]
        parser = FirstRateCryptoParser(chunk_rows=1)
        list(parser.iter_chunks(_write(tmp_path, rows), BTC_MIN))
        assert (parser.anomalies.rounded_rows, parser.anomalies.zero_volume) == (2, 2)

    def test_counters_reset_between_files(self, tmp_path):
        parser = FirstRateCryptoParser()
        list(parser.iter_chunks(_write(tmp_path, ["2024-01-01 00:00:00,1,1,1,1,0"]), BTC_HOUR))
        other = _write(tmp_path, ["2024-01-01 00:00:00,1,1,1,1,1"], "ETH_full_1hour.txt")
        list(parser.iter_chunks(other, BTC_HOUR))
        assert parser.anomalies.zero_volume == 0

    def test_duplicate_timestamp_keeps_last(self, tmp_path):
        rows = ["2024-07-01 10:00:00,1,2,1,1,1", "2024-07-01 10:00:00,1,3,1,2,2"]
        _, bars = _parse(tmp_path, rows)
        assert len(bars) == 1 and str(bars[0].close) == "2.0000"

    def test_rows_behind_previous_chunk_are_dropped_and_counted(self, tmp_path):
        rows = _hours("2024-07-01", 4) + ["2024-07-01 01:00:00,1,1,1,1,99"]
        parser = FirstRateCryptoParser(chunk_rows=4)
        chunks = list(parser.iter_chunks(_write(tmp_path, rows), BTC_HOUR))
        assert [len(c.bars) for c in chunks] == [4, 0]
        assert chunks[1].out_of_order == 1


class TestValidation:
    def test_invalid_rows_dropped_and_reported(self, tmp_path):
        rows = [
            "",
            "2024-07-01 10:00:00,100,101,99,100,1\r",
            "2024-07-01 11:00:00,100,99,101,100,1",  # high < low
            "2024-07-01 12:00:00,-100,101,-99,100,1",  # non-positive
            "2024-07-01 13:00:00,abc,101,99,100,1",  # non-numeric
            "20240701 14:00:00,100,101,99,100,1",  # not an ISO date
            "2024-07-01 25:00:00,100,101,99,100,1",  # not a time
            "2024-07-01 15:00:00,100,101,99,100,-5",  # negative volume
            "2024-07-01 16:00:00,100,101,99",  # short row
            "2024-07-01 17:00:00,100,101,99,100,7",
        ]
        parser, bars = _parse(tmp_path, rows)
        assert [b.volume.as_double() for b in bars] == [1, 7]
        validation = parser.last_validation
        assert validation.row_count == 9
        assert validation.invalid_rows == 7
        assert not validation.valid and validation.errors

    def test_empty_file(self, tmp_path):
        path = tmp_path / "BTC_full_1hour.txt"
        path.write_text("\n\n")
        parser = FirstRateCryptoParser()
        assert parser.parse_file(path, BTC_HOUR.instrument_id, BTC_HOUR) == []
        assert parser.last_validation.row_count == 0
        assert parser.anomalies.rounded_rows == 0
        assert parser.anomalies.whole_volume_from_ns is None

    def test_precision_comes_from_the_coin_spec(self, tmp_path):
        _, [bar] = _parse(tmp_path, ["2024-01-01,150.12345,151,149,150,1000"], SOL_DAY)
        assert str(bar.open) == "150.1234"  # half-even on the decimal value
