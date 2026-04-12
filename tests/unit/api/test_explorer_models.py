"""Unit tests for explorer Pydantic models."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.api.models.explorer import ExplorerPageState, TickerListResponse, TickerRow


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
