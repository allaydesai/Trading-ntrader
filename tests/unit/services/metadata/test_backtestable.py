"""Unit tests for the pure backtestable classifier (Story 3.5)."""

import dataclasses

import pytest

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.backtestable import (
    NonBacktestableTicker,
    is_backtestable,
    total_bars,
)


@pytest.mark.unit
class TestIsBacktestable:
    """`is_backtestable` keys strictly on the venue verdict."""

    def test_resolved_is_backtestable(self):
        assert is_backtestable(ResolutionStatus.RESOLVED) is True

    def test_venue_unresolved_is_not_backtestable(self):
        assert is_backtestable(ResolutionStatus.VENUE_UNRESOLVED) is False

    def test_unresolved_is_not_backtestable(self):
        assert is_backtestable(ResolutionStatus.UNRESOLVED) is False


@pytest.mark.unit
class TestTotalBars:
    """`total_bars` sums the five per-timeframe bar counts (None → 0)."""

    def test_sums_present_counts(self):
        inst = CatalogInstrument(
            ticker="SPY",
            asset_class="ETF",
            catalog_name="firstrate-etf",
            bar_count_daily=2,
            bar_count_minute=3,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_30min=0,
        )
        assert total_bars(inst) == 5

    def test_all_zero_returns_zero(self):
        inst = CatalogInstrument(
            ticker="SPY",
            asset_class="ETF",
            catalog_name="firstrate-etf",
            bar_count_daily=0,
            bar_count_minute=0,
            bar_count_hourly=0,
            bar_count_5min=0,
            bar_count_30min=0,
        )
        assert total_bars(inst) == 0

    def test_unset_counts_treated_as_zero(self):
        # A freshly-constructed ORM object has None bar counts (defaults apply at INSERT).
        inst = CatalogInstrument(ticker="SPY", asset_class="ETF", catalog_name="firstrate-etf")
        assert total_bars(inst) == 0


@pytest.mark.unit
class TestNonBacktestableTicker:
    """The flag record surfaced by the CLI."""

    def test_bars_kept_true_when_retained(self):
        assert NonBacktestableTicker("ZZZ", 10).bars_kept is True

    def test_bars_kept_false_when_none_retained(self):
        assert NonBacktestableTicker("QQQ", 0).bars_kept is False

    def test_is_frozen(self):
        ticker = NonBacktestableTicker("ZZZ", 10)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ticker.ticker = "AAA"  # type: ignore[misc]
