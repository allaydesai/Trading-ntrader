"""Tests for bar type parsing utilities."""

from src.utils.bar_type_utils import parse_bar_type_spec


class TestParseBarTypeSpec:
    """Tests for parse_bar_type_spec function."""

    def test_parse_full_bar_type_with_external(self):
        """Test parsing full bar type string with EXTERNAL suffix."""
        result = parse_bar_type_spec("AMD.NASDAQ-1-DAY-LAST-EXTERNAL")
        assert result == "1-DAY-LAST"

    def test_parse_full_bar_type_without_suffix(self):
        """Test parsing full bar type string without aggregation source."""
        result = parse_bar_type_spec("AAPL.NASDAQ-1-HOUR-MID")
        assert result == "1-HOUR-MID"

    def test_parse_minute_bars(self):
        """Test parsing minute bar type."""
        result = parse_bar_type_spec("SPY.ARCA-5-MINUTE-LAST-INTERNAL")
        assert result == "5-MINUTE-LAST"

    def test_parse_tick_bars(self):
        """Test parsing tick-based bar type."""
        result = parse_bar_type_spec("QQQ.NASDAQ-100-TICK-LAST")
        assert result == "100-TICK-LAST"

    def test_parse_empty_string(self):
        """Test parsing empty string returns default."""
        result = parse_bar_type_spec("")
        assert result == "1-DAY-LAST"

    def test_parse_none_returns_default(self):
        """Test parsing None-like empty string returns default."""
        result = parse_bar_type_spec("")
        assert result == "1-DAY-LAST"

    def test_parse_invalid_format(self):
        """Test parsing invalid format returns default."""
        result = parse_bar_type_spec("invalid")
        assert result == "1-DAY-LAST"

    def test_parse_partial_format(self):
        """Test parsing partial format with insufficient parts returns default."""
        result = parse_bar_type_spec("AAPL-1-DAY")  # Only 3 parts
        assert result == "1-DAY-LAST"

    def test_parse_simple_spec_format(self):
        """Test that already-simple format with 4 parts works."""
        # This would be: ["AAPL.NASDAQ", "1", "DAY", "LAST"]
        result = parse_bar_type_spec("AAPL.NASDAQ-1-DAY-LAST")
        assert result == "1-DAY-LAST"
