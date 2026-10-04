"""Per-timeframe coverage starts: column mapping, the honest shared start, the backfill."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.firstrate.coverage import (
    TIMEFRAME_START_FIELDS,
    backfill_row,
    earliest_start,
    start_field_for_timeframe,
)

pytestmark = pytest.mark.unit

UTC = timezone.utc
DAILY = datetime(2000, 1, 4, 5, 0, tzinfo=UTC)
MINUTE = datetime(2005, 6, 1, 14, 30, tzinfo=UTC)


def _row(**overrides):
    fields = dict(
        ticker="QQQ",
        nautilus_id="QQQ.NASDAQ",
        date_range_start=None,
        bar_count_daily=0,
        bar_count_hourly=0,
        bar_count_minute=0,
        bar_count_5min=0,
        bar_count_30min=0,
        **{name: None for name in TIMEFRAME_START_FIELDS.values()},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_every_timeframe_has_a_start_column():
    assert TIMEFRAME_START_FIELDS == {
        "1-DAY": "date_range_start_daily",
        "1-HOUR": "date_range_start_hourly",
        "1-MINUTE": "date_range_start_minute",
        "5-MINUTE": "date_range_start_5min",
        "30-MINUTE": "date_range_start_30min",
    }
    assert start_field_for_timeframe("5-MINUTE-LAST") == "date_range_start_5min"
    assert start_field_for_timeframe("noop") == "date_range_start_daily"


def test_earliest_start_is_the_minimum_of_the_known_starts():
    row = _row(date_range_start_daily=DAILY, date_range_start_minute=MINUTE)
    assert earliest_start(row) == DAILY


def test_earliest_start_keeps_the_shared_start_when_no_timeframe_has_one():
    assert earliest_start(_row(date_range_start=MINUTE)) == MINUTE
    assert earliest_start(_row()) is None


class TestBackfillRow:
    def test_sets_the_start_of_each_timeframe_with_bars(self):
        row = _row(bar_count_daily=6600, bar_count_minute=500, date_range_start=MINUTE)
        starts = {("QQQ.NASDAQ", "1-DAY"): DAILY, ("QQQ.NASDAQ", "1-MINUTE"): MINUTE}

        outcome = backfill_row(row, lambda nid, tf: starts.get((nid, tf)))

        assert row.date_range_start_daily == DAILY
        assert row.date_range_start_minute == MINUTE
        assert row.date_range_start_hourly is None
        assert row.date_range_start == DAILY, "the shared start becomes the true earliest"
        assert outcome.changed is True
        assert outcome.missing == []

    def test_the_partition_is_looked_up_by_the_rows_own_nautilus_id(self):
        """A stale partition under an old venue must not supply the start."""
        row = _row(nautilus_id="QQQ.ARCA", bar_count_daily=1)
        asked = []

        backfill_row(row, lambda nid, tf: asked.append((nid, tf)))

        assert asked == [("QQQ.ARCA", "1-DAY")]

    def test_a_timeframe_with_bars_but_no_partition_is_reported_not_guessed(self):
        row = _row(bar_count_hourly=10, date_range_start=MINUTE)

        outcome = backfill_row(row, lambda nid, tf: None)

        assert row.date_range_start_hourly is None
        assert row.date_range_start == MINUTE
        assert outcome.changed is False
        assert outcome.missing == ["1-HOUR"]

    def test_an_unqualified_row_is_left_alone(self):
        row = _row(nautilus_id=None, bar_count_daily=10)

        outcome = backfill_row(row, lambda nid, tf: DAILY)

        assert row.date_range_start_daily is None
        assert outcome.changed is False

    def test_a_row_already_correct_is_unchanged(self):
        row = _row(bar_count_daily=1, date_range_start_daily=DAILY, date_range_start=DAILY)

        assert backfill_row(row, lambda nid, tf: DAILY).changed is False
