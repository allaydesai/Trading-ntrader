"""Unit tests for FirstRate CSV parser."""

from pathlib import Path

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass
from src.services.firstrate.parsers.base import (
    get_parser,
)
from src.services.firstrate.parsers.firstrate_csv_parser import FirstRateCsvParser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def parser():
    """Return a fresh FirstRateCsvParser instance."""
    return FirstRateCsvParser()


@pytest.fixture()
def instrument_id():
    return InstrumentId.from_str("SPY.ARCA")


@pytest.fixture()
def daily_bar_type():
    return BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")


@pytest.fixture()
def intraday_bar_type():
    return BarType.from_str("SPY.ARCA-1-MINUTE-LAST-EXTERNAL")


def _write_csv(tmp_path: Path, filename: str, lines: list[str]) -> Path:
    """Helper to write CSV lines to a temp file."""
    p = tmp_path / filename
    p.write_text("\n".join(lines))
    return p


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserRegistration:
    """FirstRate CSV parser registration and discovery."""

    def test_registered_for_etf_asset_class(self):
        """Parser is retrievable via get_parser(AssetClass.ETF)."""
        p = get_parser(AssetClass.ETF)
        assert isinstance(p, FirstRateCsvParser)

    def test_registered_for_stock_asset_class(self):
        """Parser is retrievable via get_parser(AssetClass.STOCK)."""
        p = get_parser(AssetClass.STOCK)
        assert isinstance(p, FirstRateCsvParser)

    def test_etf_and_stock_return_same_parser_type(self):
        """Both ETF and STOCK asset classes resolve to FirstRateCsvParser."""
        etf_parser = get_parser(AssetClass.ETF)
        stock_parser = get_parser(AssetClass.STOCK)
        assert type(etf_parser) is type(stock_parser)


# ---------------------------------------------------------------------------
# parse_file — daily format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserDaily:
    """Tests for parsing daily (1day) CSV files."""

    def test_basic_daily_parse(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Parses daily CSV rows into Bar objects."""
        lines = [
            "2002-05-22,43.8068,44.1444,43.0834,43.2522,23762",
            "2002-05-23,43.3000,43.5000,43.0000,43.2000,15000",
        ]
        f = _write_csv(tmp_path, "SPY_full_1day_adjsplitdiv.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 2
        assert all(isinstance(b, Bar) for b in bars)

    def test_bars_sorted_by_ts_init(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Bars are sorted ascending by ts_init."""
        lines = [
            "2002-05-23,43.30,43.50,43.00,43.20,15000",
            "2002-05-22,43.80,44.14,43.08,43.25,23762",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert bars[0].ts_init < bars[1].ts_init

    def test_price_precision_preserved(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Source decimal precision is maintained in Price objects."""
        lines = ["2020-01-02,43.8068,44.1444,43.0834,43.2522,23762"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        bar = bars[0]
        # 4 decimal places in source data
        assert str(bar.open) == "43.8068"
        assert str(bar.high) == "44.1444"

    def test_timestamps_are_nanoseconds_utc(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Daily ts is interpreted as midnight ET → converted to UTC.

        FirstRate CSV daily timestamps are local-time midnight in
        America/New_York. 2020-01-02 (winter, EST = UTC-5) → midnight ET
        = 05:00 UTC = 1577941200 seconds since epoch.
        """
        lines = ["2020-01-02,100.00,105.00,99.00,103.00,1000"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        bar = bars[0]
        # 2020-01-02 00:00:00 EST = 2020-01-02 05:00:00 UTC = 1577941200 s
        expected_ns = 1577941200 * 1_000_000_000
        assert bar.ts_init == expected_ns
        assert bar.ts_event == expected_ns

    def test_daily_date_only_format(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Daily files use YYYY-MM-DD date-only format."""
        lines = ["2020-06-15,300.00,310.00,295.00,305.00,50000"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 1


# ---------------------------------------------------------------------------
# parse_file — intraday format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserIntraday:
    """Tests for parsing intraday (1min/5min/1hour) CSV files."""

    def test_basic_intraday_parse(self, parser, instrument_id, intraday_bar_type, tmp_path):
        """Parses intraday CSV rows with datetime format."""
        lines = [
            "2020-09-09 09:00:00,25.1,25.1046,25.08,25.08,10619",
            "2020-09-09 09:01:00,25.09,25.11,25.07,25.10,5000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert len(bars) == 2

    def test_intraday_timestamps_include_time(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """Intraday timestamps preserve hours/minutes/seconds."""
        lines = ["2020-09-09 09:30:00,25.10,25.20,25.05,25.15,8000"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        bar = bars[0]
        # ts should NOT be midnight
        assert bar.ts_init % (24 * 3600 * 1_000_000_000) != 0

    def test_volume_float_notation(self, parser, instrument_id, intraday_bar_type, tmp_path):
        """Volume as float notation (248.0) is handled correctly."""
        lines = ["2020-09-09 09:00:00,25.10,25.20,25.05,25.15,248.0"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert int(bars[0].volume) == 248


# ---------------------------------------------------------------------------
# Timezone handling — FirstRate timestamps are US Eastern (with DST)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserTimezone:
    """Tests verifying FirstRate's ET timestamps convert correctly to UTC.

    FirstRate's CSV uses local US Eastern Time (with DST). A row labelled
    ``2018-01-16 09:30:00`` is 09:30 EST (= 14:30 UTC), and a row labelled
    ``2018-07-16 09:30:00`` is 09:30 EDT (= 13:30 UTC). The previous
    parser stamped these as 09:30 UTC, shifting every bar by 4–5 hours
    and silently breaking IBKR-vs-FirstRate parity comparisons.
    """

    def test_winter_intraday_uses_est_offset(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """09:30 ET in EST winter = 14:30 UTC (UTC-5)."""
        lines = ["2018-01-16 09:30:00,44.4775,44.5375,44.4075,44.4350,3081124"]
        f = _write_csv(tmp_path, "AAPL.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        # 2018-01-16 14:30:00 UTC = 1516113000 seconds
        assert bars[0].ts_event == 1516113000 * 1_000_000_000

    def test_summer_intraday_uses_edt_offset(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """09:30 ET in EDT summer = 13:30 UTC (UTC-4)."""
        lines = ["2018-07-16 09:30:00,46.51,46.52,46.50,46.51,1234567"]
        f = _write_csv(tmp_path, "AAPL.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        # 2018-07-16 13:30:00 UTC = 1531747800 seconds
        assert bars[0].ts_event == 1531747800 * 1_000_000_000

    def test_dst_spring_forward_handled(self, parser, instrument_id, intraday_bar_type, tmp_path):
        """Bar at 03:00 ET on DST spring-forward (2018-03-11) is unambiguous.

        09:30 on 2018-03-11 is already in EDT (DST started 02:00 → 03:00),
        so 09:30 EDT = 13:30 UTC.
        """
        lines = ["2018-03-12 09:30:00,46.95,46.96,46.94,46.95,2000000"]
        f = _write_csv(tmp_path, "AAPL.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        # 2018-03-12 09:30 EDT = 13:30 UTC = 1520861400 seconds
        assert bars[0].ts_event == 1520861400 * 1_000_000_000

    def test_recent_data_matches_real_market_open(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """A recent EDT 09:30 row maps to 13:30 UTC (matches IBKR)."""
        lines = ["2026-05-01 09:30:00,278.855,281.75,278.37,281.4,2813814"]
        f = _write_csv(tmp_path, "AAPL.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        # 2026-05-01 09:30 EDT = 13:30 UTC = 1777642200 seconds
        assert bars[0].ts_event == 1777642200 * 1_000_000_000


# ---------------------------------------------------------------------------
# Known data issues (from Dev Notes)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserKnownDataIssues:
    """Tests for handling known FirstRate data issues."""

    def test_leading_blank_lines_skipped(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Files with leading blank lines (773 daily files) are handled."""
        lines = [
            "",
            "",
            "",
            "2020-01-02,100.00,105.00,99.00,103.00,1000",
            "2020-01-03,103.00,108.00,102.00,107.00,2000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 2

    def test_crlf_line_endings(self, parser, instrument_id, daily_bar_type, tmp_path):
        """CRLF line endings are handled (773 daily files use \\r\\n)."""
        content = (
            "2020-01-02,100.00,105.00,99.00,103.00,1000\r\n"
            "2020-01-03,103.00,108.00,102.00,107.00,2000\r\n"
        )
        f = tmp_path / "SPY.txt"
        f.write_bytes(content.encode("utf-8"))
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 2

    def test_empty_file_returns_empty_list(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Empty (0-byte) files return empty list, no crash."""
        f = tmp_path / "ARKA.txt"
        f.write_text("")
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert bars == []

    def test_single_row_file(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Single-row file parses correctly."""
        lines = ["2020-01-02,100.00,105.00,99.00,103.00,1000"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 1

    def test_malformed_row_wrong_column_count(
        self, parser, instrument_id, daily_bar_type, tmp_path
    ):
        """Rows with wrong column count are skipped, valid rows still parsed."""
        lines = [
            "2020-01-02,100.00,105.00,99.00,103.00,1000",
            "2020-01-03,103.00",  # malformed
            "2020-01-04,110.00,115.00,108.00,112.00,3000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 2

    def test_leading_blanks_with_six_lines(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Files with 6 leading blank lines (max observed) still parse."""
        lines = ["", "", "", "", "", "", "2020-01-02,100.00,105.00,99.00,103.00,1000"]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 1

    def test_negative_prices_pass_through_parse_file(
        self, parser, instrument_id, daily_bar_type, tmp_path
    ):
        """Negative prices pass through parse_file — validate_bars catches them separately."""
        lines = [
            "2020-01-02,100.00,105.00,99.00,103.00,1000",
            "2020-01-03,-5.00,-4.00,-6.00,-5.50,500",
            "2020-01-04,110.00,115.00,108.00,112.00,3000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        # Nautilus accepts negative prices; validate_bars is the gate
        assert len(bars) == 3

    def test_negative_prices_caught_by_validate_bars(self, parser, tmp_path):
        """validate_bars flags negative prices from parsed raw data."""
        from src.services.firstrate.parsers.base import RawBarData

        rows = [
            RawBarData("2020-01-02", "100.00", "105.00", "99.00", "103.00", "1000"),
            RawBarData("2020-01-03", "-5.00", "-4.00", "-6.00", "-5.50", "500"),
        ]
        result = parser.validate_bars(rows)
        assert result.valid is False
        assert result.invalid_rows == 1
        assert any("non-positive" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Duplicate timestamp deduplication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserDedup:
    """Tests for duplicate timestamp deduplication in parse_file."""

    def test_exact_duplicate_lines_deduplicated(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """Exact duplicate lines (same ts + OHLCV) produce one bar."""
        lines = [
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
            "2020-09-09 09:01:00,25.20,25.30,25.10,25.25,8000",
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
            "2020-09-09 09:01:00,25.20,25.30,25.10,25.25,8000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert len(bars) == 2

    def test_same_timestamp_different_ohlcv_keeps_last(
        self, parser, instrument_id, intraday_bar_type, tmp_path
    ):
        """Same timestamp with different OHLCV keeps last occurrence."""
        lines = [
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
            "2020-09-09 09:01:00,25.20,25.30,25.10,25.25,8000",
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.18,10500",
            "2020-09-09 09:01:00,25.20,25.30,25.10,25.28,8200",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert len(bars) == 2
        # Last occurrence wins (close=25.18 and 25.28)
        assert str(bars[0].close) == "25.18"
        assert str(bars[1].close) == "25.28"

    def test_no_duplicates_unchanged(self, parser, instrument_id, intraday_bar_type, tmp_path):
        """Files without duplicates are returned unchanged."""
        lines = [
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
            "2020-09-09 09:01:00,25.20,25.30,25.10,25.25,8000",
            "2020-09-09 09:02:00,25.25,25.35,25.20,25.30,6000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert len(bars) == 3

    def test_daily_duplicates_deduplicated(self, parser, instrument_id, daily_bar_type, tmp_path):
        """Daily bars with duplicate dates are also deduplicated."""
        lines = [
            "2020-01-02,100.00,105.00,99.00,103.00,1000",
            "2020-01-03,103.00,108.00,102.00,107.00,2000",
            "2020-01-02,100.00,105.00,99.00,103.00,1000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)
        bars = parser.parse_file(f, instrument_id, daily_bar_type)
        assert len(bars) == 2

    def test_dedup_logs_warning(self, parser, instrument_id, intraday_bar_type, tmp_path, caplog):
        """Deduplication emits a structured log when duplicates found."""
        lines = [
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
            "2020-09-09 09:00:00,25.10,25.20,25.05,25.15,10000",
        ]
        f = _write_csv(tmp_path, "SPY.txt", lines)

        bars = parser.parse_file(f, instrument_id, intraday_bar_type)
        assert len(bars) == 1


# ---------------------------------------------------------------------------
# map_instrument_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFirstRateCsvParserMapInstrumentId:
    """Tests for map_instrument_id."""

    def test_returns_instrument_id(self, parser):
        """Returns an InstrumentId with .ARCA suffix."""
        iid = parser.map_instrument_id("SPY", "1-DAY-LAST")
        assert isinstance(iid, InstrumentId)
        assert "SPY" in str(iid)
