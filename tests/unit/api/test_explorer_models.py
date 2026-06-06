"""Unit tests for explorer Pydantic models."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.api.models.explorer import (
    ExplorerPageState,
    TickerListResponse,
    TickerRow,
    TickerStatsResponse,
)
from src.api.stats_service import _compute_price_range


def _mk_bar(low: float, high: float):
    bar = MagicMock()
    bar.low.as_double.return_value = low
    bar.high.as_double.return_value = high
    return bar


class TestComputePriceRange:
    """Tests for _compute_price_range helper (Story 2-3)."""

    @pytest.mark.unit
    def test_empty_bars_returns_none(self):
        assert _compute_price_range([]) == (None, None)

    @pytest.mark.unit
    def test_single_bar_returns_low_and_high(self):
        bars = [_mk_bar(low=99.5, high=101.25)]
        assert _compute_price_range(bars) == (99.5, 101.25)

    @pytest.mark.unit
    def test_multi_bar_returns_overall_min_and_max(self):
        bars = [
            _mk_bar(low=99.5, high=101.25),
            _mk_bar(low=97.10, high=103.75),
            _mk_bar(low=98.0, high=102.0),
        ]
        assert _compute_price_range(bars) == (97.10, 103.75)


class TestTickerRow:
    """Tests for TickerRow presentation model."""

    @pytest.mark.unit
    def test_basic_ticker_row(self):
        row = TickerRow(
            ticker="AAPL",
            name="Apple Inc.",
            asset_class="STOCK",
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count_daily=1250,
            bar_count_hourly=8750,
            bar_count_5min=93750,
            bar_count_minute=468750,
            nautilus_id="AAPL.XNAS",
        )
        assert row.ticker == "AAPL"
        assert row.asset_class == "STOCK"
        assert row.bar_count_5min == 93750

    @pytest.mark.unit
    def test_coverage_pct_computed(self):
        row = TickerRow(
            ticker="AAPL",
            name="Apple Inc.",
            asset_class="STOCK",
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 1, 1, tzinfo=timezone.utc),
            bar_count_daily=100,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_minute=0,
        )
        assert row.coverage_pct is not None
        assert 0.0 <= row.coverage_pct <= 100.0

    @pytest.mark.unit
    def test_coverage_pct_none_when_no_dates(self):
        row = TickerRow(
            ticker="AAPL",
            name="Apple Inc.",
            asset_class="STOCK",
            date_range_start=None,
            date_range_end=None,
            bar_count_daily=0,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_minute=0,
        )
        assert row.coverage_pct is None

    @pytest.mark.unit
    def test_nullable_fields(self):
        row = TickerRow(
            ticker="TEST",
            name=None,
            asset_class="ETF",
            date_range_start=None,
            date_range_end=None,
            bar_count_daily=0,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_minute=0,
            nautilus_id=None,
        )
        assert row.name is None
        assert row.nautilus_id is None


class TestTickerListResponse:
    """Tests for TickerListResponse."""

    @pytest.mark.unit
    def test_empty_response(self):
        resp = TickerListResponse(
            tickers=[],
            total_count=0,
            page=1,
            page_size=25,
            total_pages=0,
        )
        assert resp.tickers == []
        assert resp.total_pages == 0

    @pytest.mark.unit
    def test_with_tickers(self):
        row = TickerRow(
            ticker="AAPL",
            name="Apple",
            asset_class="STOCK",
            date_range_start=None,
            date_range_end=None,
            bar_count_daily=100,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_minute=0,
        )
        resp = TickerListResponse(
            tickers=[row],
            total_count=1,
            page=1,
            page_size=25,
            total_pages=1,
        )
        assert len(resp.tickers) == 1
        assert resp.total_count == 1


class TestExplorerPageState:
    """Tests for ExplorerPageState."""

    @pytest.mark.unit
    def test_default_state(self):
        state = ExplorerPageState(catalog="us_stocks")
        assert state.catalog == "us_stocks"
        assert state.search == ""
        assert state.asset_class == ""
        assert state.page == 1
        assert state.sort_by == "ticker"

    @pytest.mark.unit
    def test_custom_state(self):
        state = ExplorerPageState(
            catalog="us_stocks",
            search="AA",
            asset_class="STOCK",
            page=3,
            sort_by="date_range",
        )
        assert state.search == "AA"
        assert state.page == 3
        assert state.sort_by == "date_range"

    @pytest.mark.unit
    def test_page_must_be_positive(self):
        with pytest.raises(ValidationError):
            ExplorerPageState(catalog="us_stocks", page=0)


class TestTickerStatsResponse:
    """Tests for TickerStatsResponse model (Story 2-3)."""

    @pytest.mark.unit
    def test_defaults_for_empty_ticker(self):
        resp = TickerStatsResponse(ticker="AAPL", active_tf="D")
        assert resp.ticker == "AAPL"
        assert resp.active_tf == "D"
        assert resp.nautilus_id is None
        assert resp.date_range_start is None
        assert resp.date_range_end is None
        assert resp.bar_count_daily == 0
        assert resp.bar_count_hourly == 0
        assert resp.bar_count_5min == 0
        assert resp.bar_count_minute == 0
        assert resp.price_min is None
        assert resp.price_max is None

    @pytest.mark.unit
    def test_fully_populated(self):
        resp = TickerStatsResponse(
            ticker="AAPL",
            nautilus_id="AAPL.XNAS",
            date_range_start=datetime(2020, 1, 2, tzinfo=timezone.utc),
            date_range_end=datetime(2025, 12, 31, tzinfo=timezone.utc),
            bar_count_daily=1250,
            bar_count_hourly=8750,
            bar_count_5min=93750,
            bar_count_minute=468750,
            price_min=45.50,
            price_max=198.75,
            active_tf="1H",
        )
        assert resp.nautilus_id == "AAPL.XNAS"
        assert resp.bar_count_hourly == 8750
        assert resp.price_min == 45.50
        assert resp.price_max == 198.75
        assert resp.active_tf == "1H"

    @pytest.mark.unit
    def test_price_range_nullable(self):
        resp = TickerStatsResponse(
            ticker="AAPL",
            active_tf="1m",
            price_min=None,
            price_max=None,
        )
        assert resp.price_min is None
        assert resp.price_max is None

    @pytest.mark.unit
    def test_ticker_required(self):
        with pytest.raises(ValidationError):
            TickerStatsResponse(active_tf="D")  # type: ignore[call-arg]

    @pytest.mark.unit
    def test_active_tf_required(self):
        with pytest.raises(ValidationError):
            TickerStatsResponse(ticker="AAPL")  # type: ignore[call-arg]
