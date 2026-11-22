"""Component tests for the FirstRate futures parser (futures M2).

The parser streams a headerless FirstRate futures file in chunks, accepts the 6-column
intraday and 7-column daily (open interest) layouts, converts US/Eastern wall clock to UTC
exactly like the equity parser, and emits every price of a root at ONE fixed precision.
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

pytestmark = pytest.mark.component

ES_DAY = BarType.from_str("ES.CME-1-DAY-LAST-EXTERNAL")
ES_MIN = BarType.from_str("ES.CME-1-MINUTE-LAST-EXTERNAL")
ZN_HOUR = BarType.from_str("ZN.CBOT-1-HOUR-LAST-EXTERNAL")


def _write(tmp_path: Path, lines: list[str], name: str = "ES_full_1min_x.txt") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return path


def _utc_ns(text: str) -> int:
    dt = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) * 1_000_000_000


class TestRegistration:
    def test_registered_for_futures(self):
        assert isinstance(get_parser(AssetClass.FUTURES), FirstRateFuturesParser)

    def test_equity_parsers_unchanged(self):
        assert isinstance(get_parser(AssetClass.STOCK), FirstRateCsvParser)
        assert isinstance(get_parser(AssetClass.ETF), FirstRateCsvParser)

    def test_maps_root_to_continuous_id(self):
        parser = FirstRateFuturesParser()
        assert parser.map_instrument_id("ES", "1-DAY-LAST") == InstrumentId.from_str("ES.CME")
        assert parser.map_instrument_id("zn", "1-DAY-LAST") == InstrumentId.from_str("ZN.CBOT")


class TestColumns:
    def test_seven_column_daily_drops_open_interest(self, tmp_path):
        path = _write(
            tmp_path,
            [
                "2026-04-29,7100.0,7160.25,7090.5,7150.0,1500000,1968000",
                "2026-04-30,7150.0,7253.5,7133.75,7243.75,1732392,1968318",
            ],
            "ES_full_1day_x.txt",
        )
        bars = FirstRateFuturesParser().parse_file(path, ES_DAY.instrument_id, ES_DAY)
        assert len(bars) == 2
        assert str(bars[1].close) == "7243.75"
        assert bars[1].volume.as_double() == 1732392
        # Daily rows localize to midnight ET, like the equity parser (EDT -> 04:00 UTC).
        assert bars[1].ts_event == _utc_ns("2026-04-30T04:00:00")

    def test_six_and_seven_columns_mix(self, tmp_path):
        path = _write(
            tmp_path,
            ["2026-04-29,1.0,2.0,1.0,2.0,10", "2026-04-30,2.0,3.0,2.0,3.0,10,0"],
            "ES_full_1day_x.txt",
        )
        bars = FirstRateFuturesParser().parse_file(path, ES_DAY.instrument_id, ES_DAY)
        assert len(bars) == 2


class TestFixedPrecision:
    def test_mixed_decimals_padded_to_spec_precision(self, tmp_path):
        path = _write(
            tmp_path,
            [
                "2025-06-02 10:00:00,5918.9,5919.0,5918.5,5918.9,10",
                "2025-06-02 10:01:00,5918.99,5919.25,5918.75,5919.0,12",
                "2025-06-02 10:02:00,5919,5920,5919,5920,3",
            ],
        )
        bars = FirstRateFuturesParser().parse_file(path, ES_MIN.instrument_id, ES_MIN)
        precisions = {p.precision for b in bars for p in (b.open, b.high, b.low, b.close)}
        assert precisions == {2}
        assert [str(b.open) for b in bars] == ["5918.90", "5918.99", "5919.00"]

    def test_excess_decimals_round_to_spec_precision(self, tmp_path):
        path = _write(tmp_path, ["2025-06-02 10:00:00,5918.994,5919.006,5918.5,5918.9,10"])
        bar = FirstRateFuturesParser().parse_file(path, ES_MIN.instrument_id, ES_MIN)[0]
        assert str(bar.open) == "5918.99"
        assert str(bar.high) == "5919.01"

    def test_zn_carries_six_decimals(self, tmp_path):
        path = _write(
            tmp_path,
            ["2008-01-01 19:00:00,82.708329,82.708329,82.639975,82.64,837"],
            "ZN_full_1hour_x.txt",
        )
        bar = FirstRateFuturesParser().parse_file(path, ZN_HOUR.instrument_id, ZN_HOUR)[0]
        assert str(bar.open) == "82.708329"
        assert str(bar.close) == "82.640000"

    def test_explicit_precision_override(self, tmp_path):
        path = _write(tmp_path, ["2025-06-02 10:00:00,1.5,2,1,2,1"])
        chunks = list(FirstRateFuturesParser().iter_chunks(path, ES_MIN, price_precision=4))
        assert str(chunks[0].bars[0].open) == "1.5000"


class TestTimestamps:
    @pytest.mark.parametrize(
        "wall_clock",
        [
            "2025-03-09 01:30:00",  # before spring-forward
            "2025-03-09 03:30:00",  # after spring-forward
            "2025-03-09 18:00:00",  # Globex Sunday open, first EDT session
            "2025-11-02 00:30:00",  # before fall-back
            "2025-11-02 01:30:00",  # ambiguous hour (first occurrence, like zoneinfo fold=0)
            "2025-11-02 18:00:00",  # Globex Sunday open, first EST session
            "2025-07-01 18:00:00",
            "2025-01-02 18:00:00",
        ],
    )
    def test_matches_equity_parser_conversion(self, tmp_path, wall_clock):
        path = _write(tmp_path, [f"{wall_clock},10,11,9,10,1"])
        bar = FirstRateFuturesParser().parse_file(path, ES_MIN.instrument_id, ES_MIN)[0]
        expected = FirstRateCsvParser._parse_timestamp(wall_clock)
        assert bar.ts_event == int(expected.timestamp()) * 1_000_000_000
        assert bar.ts_init == bar.ts_event

    def test_globex_open_is_2200_utc_in_summer_2300_in_winter(self, tmp_path):
        path = _write(
            tmp_path,
            ["2025-01-05 18:00:00,10,11,9,10,1", "2025-07-06 18:00:00,10,11,9,10,1"],
        )
        bars = FirstRateFuturesParser().parse_file(path, ES_MIN.instrument_id, ES_MIN)
        assert [b.ts_event for b in bars] == sorted(
            [_utc_ns("2025-01-05T23:00:00"), _utc_ns("2025-07-06T22:00:00")]
        )


class TestChunking:
    def _ten_rows(self) -> list[str]:
        return [f"2025-06-02 10:{m:02d}:00,10,11,9,10,{m}" for m in range(10)]

    def test_chunks_cover_every_row_in_order(self, tmp_path):
        path = _write(tmp_path, self._ten_rows())
        chunks = list(FirstRateFuturesParser(chunk_rows=3).iter_chunks(path, ES_MIN))
        assert [len(c.bars) for c in chunks] == [3, 3, 3, 1]
        stamps = [b.ts_init for c in chunks for b in c.bars]
        assert stamps == sorted(stamps) and len(set(stamps)) == 10

    def test_duplicate_timestamp_keeps_last_within_chunk(self, tmp_path):
        rows = ["2025-06-02 10:00:00,10,11,9,10,1", "2025-06-02 10:00:00,10,12,9,11,2"]
        path = _write(tmp_path, rows)
        bars = FirstRateFuturesParser().parse_file(path, ES_MIN.instrument_id, ES_MIN)
        assert len(bars) == 1 and str(bars[0].close) == "11.00"

    def test_rows_behind_previous_chunk_are_dropped_and_counted(self, tmp_path):
        rows = self._ten_rows()[:4] + ["2025-06-02 10:01:00,10,11,9,10,99"]
        path = _write(tmp_path, rows)
        chunks = list(FirstRateFuturesParser(chunk_rows=4).iter_chunks(path, ES_MIN))
        assert [len(c.bars) for c in chunks] == [4, 0]
        assert chunks[1].out_of_order == 1


class TestValidation:
    def test_invalid_rows_dropped_and_reported(self, tmp_path):
        path = _write(
            tmp_path,
            [
                "",
                "2025-06-02 10:00:00,10,11,9,10,1\r",
                "2025-06-02 10:01:00,10,9,11,10,1",  # high < low
                "2025-06-02 10:02:00,-1,11,-2,10,1",  # non-positive
                "2025-06-02 10:03:00,abc,11,9,10,1",  # non-numeric
                "not-a-date,10,11,9,10,1",
                "2025-06-02 10:04:00,10,11,9,10,-5",  # negative volume
                "2025-06-02 10:05:00,10,11,9,10",  # short row
                "2025-06-02 10:06:00,10,11,9,10,7",
            ],
        )
        parser = FirstRateFuturesParser()
        bars = parser.parse_file(path, ES_MIN.instrument_id, ES_MIN)
        assert [b.volume.as_double() for b in bars] == [1, 7]
        validation = parser.last_validation
        assert validation.row_count == 8
        assert validation.invalid_rows == 6
        assert not validation.valid
        assert validation.errors

    def test_order_is_checked_before_rounding(self, tmp_path):
        # high < low in the source; at precision 2 all four prices round to 10.00.
        path = _write(tmp_path, ["2025-06-02 10:00:00,10,9.999,10.001,10,1"])
        parser = FirstRateFuturesParser()
        assert parser.parse_file(path, ES_MIN.instrument_id, ES_MIN) == []
        assert parser.last_validation.invalid_rows == 1

    def test_unrepresentable_values_are_rejected_per_row(self, tmp_path):
        path = _write(
            tmp_path,
            [
                "2025-06-02 10:00:00,10,11,9,10,1",
                "2025-06-02 10:01:00,10,11,9,10,1e100",  # beyond QUANTITY_MAX
                "2025-06-02 10:02:00,1e300,1e300,1e300,1e300,1",  # beyond PRICE_MAX
                "2025-06-02 10:03:00,10,11,9,10,inf",
                "2025-06-02 10:04:00,10,11,9,10,2",
            ],
        )
        parser = FirstRateFuturesParser()
        bars = parser.parse_file(path, ES_MIN.instrument_id, ES_MIN)
        assert [b.volume.as_double() for b in bars] == [1, 2]
        assert parser.last_validation.invalid_rows == 3

    def test_empty_file(self, tmp_path):
        path = tmp_path / "ES_full_1min_x.txt"
        path.write_text("\n\n")
        parser = FirstRateFuturesParser()
        assert parser.parse_file(path, ES_MIN.instrument_id, ES_MIN) == []
        assert parser.last_validation.row_count == 0
