"""Unit tests for chart data response models (Story 2-2, Task 1)."""

import pytest

from src.api.models.chart_timeseries import Candle
from src.api.models.explorer import ChartDataResponse, ExplorerTimeframe


@pytest.mark.unit
class TestExplorerTimeframe:
    """Tests for ExplorerTimeframe enum mapping."""

    def test_daily_label(self):
        assert ExplorerTimeframe.DAILY.label == "D"

    def test_hourly_label(self):
        assert ExplorerTimeframe.HOURLY.label == "1H"

    def test_five_min_label(self):
        assert ExplorerTimeframe.FIVE_MIN.label == "5m"

    def test_one_min_label(self):
        assert ExplorerTimeframe.ONE_MIN.label == "1m"

    def test_daily_bar_type_spec(self):
        assert ExplorerTimeframe.DAILY.bar_type_spec == "1-DAY-LAST"

    def test_hourly_bar_type_spec(self):
        assert ExplorerTimeframe.HOURLY.bar_type_spec == "1-HOUR-LAST"

    def test_five_min_bar_type_spec(self):
        assert ExplorerTimeframe.FIVE_MIN.bar_type_spec == "5-MINUTE-LAST"

    def test_one_min_bar_type_spec(self):
        assert ExplorerTimeframe.ONE_MIN.bar_type_spec == "1-MINUTE-LAST"

    def test_from_label_valid(self):
        assert ExplorerTimeframe.from_label("D") == ExplorerTimeframe.DAILY
        assert ExplorerTimeframe.from_label("1H") == ExplorerTimeframe.HOURLY
        assert ExplorerTimeframe.from_label("5m") == ExplorerTimeframe.FIVE_MIN
        assert ExplorerTimeframe.from_label("1m") == ExplorerTimeframe.ONE_MIN

    def test_from_label_invalid_returns_daily(self):
        """Invalid label should default to DAILY."""
        assert ExplorerTimeframe.from_label("INVALID") == ExplorerTimeframe.DAILY

    def test_bar_count_field_mapping(self):
        assert ExplorerTimeframe.DAILY.bar_count_field == "bar_count_daily"
        assert ExplorerTimeframe.HOURLY.bar_count_field == "bar_count_hourly"
        assert ExplorerTimeframe.FIVE_MIN.bar_count_field == "bar_count_5min"
        assert ExplorerTimeframe.ONE_MIN.bar_count_field == "bar_count_minute"

    def test_daily_initial_window_days(self):
        """Daily windows to ~5 years of data."""
        assert ExplorerTimeframe.DAILY.initial_window_days == 1825

    def test_hourly_initial_window_days(self):
        assert ExplorerTimeframe.HOURLY.initial_window_days == 180

    def test_five_min_initial_window_days(self):
        assert ExplorerTimeframe.FIVE_MIN.initial_window_days == 30

    def test_one_min_initial_window_days(self):
        assert ExplorerTimeframe.ONE_MIN.initial_window_days == 7


@pytest.mark.unit
class TestChartDataResponse:
    """Tests for ChartDataResponse model."""

    def test_empty_bars(self):
        resp = ChartDataResponse(
            bars=[],
            instrument_id="SPY.ARCA",
            timeframe="1-DAY",
            bar_count=0,
        )
        assert resp.bars == []
        assert resp.instrument_id == "SPY.ARCA"
        assert resp.timeframe == "1-DAY"
        assert resp.bar_count == 0

    def test_with_candles(self):
        candle = Candle(
            time=1704067200,
            open=473.25,
            high=475.10,
            low=472.80,
            close=474.50,
            volume=45000000,
        )
        resp = ChartDataResponse(
            bars=[candle],
            instrument_id="SPY.ARCA",
            timeframe="1-DAY",
            bar_count=1,
        )
        assert len(resp.bars) == 1
        assert resp.bars[0].time == 1704067200
        assert resp.bars[0].open == 473.25

    def test_serialization_format(self):
        candle = Candle(
            time=1704067200,
            open=473.25,
            high=475.10,
            low=472.80,
            close=474.50,
            volume=45000000,
        )
        resp = ChartDataResponse(
            bars=[candle],
            instrument_id="SPY.ARCA",
            timeframe="1-DAY",
            bar_count=1,
        )
        data = resp.model_dump()
        assert data["bars"][0]["time"] == 1704067200
        assert data["instrument_id"] == "SPY.ARCA"
        assert data["timeframe"] == "1-DAY"
        assert data["bar_count"] == 1

    def test_requires_instrument_id(self):
        with pytest.raises(Exception):
            ChartDataResponse(bars=[], timeframe="1-DAY", bar_count=0)

    def test_requires_timeframe(self):
        with pytest.raises(Exception):
            ChartDataResponse(bars=[], instrument_id="SPY.ARCA", bar_count=0)
