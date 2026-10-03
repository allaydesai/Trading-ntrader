"""Catalog coverage from instrument metadata (S1.4)."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.mcp_server.catalogs import (
    SUPPORTED_TIMEFRAMES,
    describe_coverage,
    timeframe_coverage,
    timeframe_spec,
)
from src.mcp_server.errors import ToolFailure

pytestmark = pytest.mark.unit

UTC = timezone.utc


def _row(**overrides):
    fields = dict(
        ticker="QQQ",
        nautilus_id="QQQ.NASDAQ",
        catalog_name="firstrate-etf",
        asset_class="ETF",
        name="Invesco QQQ",
        date_range_start=datetime(2000, 1, 3, tzinfo=UTC),
        date_range_end=datetime(2026, 5, 1, tzinfo=UTC),
        date_range_end_daily=datetime(2026, 4, 30, tzinfo=UTC),
        date_range_end_hourly=None,
        date_range_end_minute=None,
        date_range_end_5min=None,
        date_range_end_30min=None,
        bar_count_daily=6600,
        bar_count_hourly=0,
        bar_count_minute=0,
        bar_count_5min=0,
        bar_count_30min=0,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_timeframe_spec():
    assert timeframe_spec("1-DAY") == "1-DAY-LAST"
    assert timeframe_spec("30-minute") == "30-MINUTE-LAST"
    assert set(SUPPORTED_TIMEFRAMES) == {"1-DAY", "1-HOUR", "30-MINUTE", "5-MINUTE", "1-MINUTE"}


def test_unsupported_timeframe():
    with pytest.raises(ToolFailure) as exc:
        timeframe_spec("1-WEEK")
    assert exc.value.code == "unsupported_timeframe"
    assert "1-DAY" in exc.value.fix


def test_coverage_lists_only_timeframes_with_bars():
    coverage = describe_coverage(_row())
    assert coverage["backtestable"] is True
    assert list(coverage["timeframes"]) == ["1-DAY"]
    assert coverage["timeframes"]["1-DAY"] == {
        "start": "2000-01-03T00:00:00+00:00",
        "end": "2026-04-30T00:00:00+00:00",
        "bars": 6600,
    }


def test_per_timeframe_end_falls_back_to_overall_end():
    coverage = timeframe_coverage(_row(date_range_end_daily=None), "1-DAY")
    assert coverage is not None
    assert coverage["end"] == "2026-05-01T00:00:00+00:00"


def test_missing_timeframe_has_no_coverage():
    assert timeframe_coverage(_row(), "1-HOUR") is None


def test_unqualified_instrument_is_not_backtestable():
    assert describe_coverage(_row(nautilus_id=None))["backtestable"] is False
