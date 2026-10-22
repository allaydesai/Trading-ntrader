"""Where each timeframe's bars begin: column mapping, the shared start, and the backfill.

``catalog_instruments`` records a first-bar timestamp per timeframe. The shared
``date_range_start`` is derived from them (the earliest), so it is true for the
instrument as a whole but says nothing exact about any one timeframe.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog

#: Timeframe ``"{step}-{aggregation}"`` → ``CatalogInstrument.date_range_start_*`` field.
TIMEFRAME_START_FIELDS = {
    "1-DAY": "date_range_start_daily",
    "1-HOUR": "date_range_start_hourly",
    "1-MINUTE": "date_range_start_minute",
    "5-MINUTE": "date_range_start_5min",
    "30-MINUTE": "date_range_start_30min",
}

#: Reads the first-bar time of ``(nautilus_id, timeframe)``; None when there is no partition.
StartReader = Callable[[str, str], datetime | None]


def timeframe_key(timeframe: str) -> str:
    """Extract ``"{step}-{aggregation}"`` from a full timeframe spec.

    Examples::

        >>> timeframe_key("1-DAY-LAST")
        '1-DAY'
        >>> timeframe_key("5-MINUTE-LAST")
        '5-MINUTE'
        >>> timeframe_key("noop")
        'noop'
    """
    parts = timeframe.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return timeframe


def start_field_for_timeframe(timeframe: str) -> str:
    """The ``date_range_start_*`` attribute for a timeframe (daily for unknown ones)."""
    return TIMEFRAME_START_FIELDS.get(timeframe_key(timeframe), "date_range_start_daily")


def from_ns(ts_ns: int) -> datetime:
    """A nanosecond timestamp as a UTC datetime, as the import stores bar times."""
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)


def earliest_start(row: Any) -> datetime | None:
    """The earliest per-timeframe start; the row's shared start when none is recorded."""
    values = (getattr(row, name) for name in TIMEFRAME_START_FIELDS.values())
    starts = [start for start in values if isinstance(start, datetime)]
    return min(starts) if starts else row.date_range_start


@dataclass
class RowOutcome:
    """What the backfill did to one row."""

    changed: bool = False
    missing: list[str] = field(default_factory=list)


@dataclass
class BackfillSummary:
    """Totals of one catalog backfill."""

    rows: int = 0
    changed: int = 0
    unqualified: int = 0
    missing: list[tuple[str, str]] = field(default_factory=list)


def partition_start_reader(catalog: ParquetDataCatalog) -> StartReader:
    """Read first-bar times from the catalog's parquet file names (no file is opened).

    Nautilus names each file ``<first ts_init>_<last ts_init>.parquet``, the same
    first-bar time the import records, so this matches what a re-import would store.
    """

    def read(nautilus_id: str, timeframe: str) -> datetime | None:
        intervals = catalog.get_intervals(Bar, f"{nautilus_id}-{timeframe}-LAST-EXTERNAL")
        return from_ns(intervals[0][0]) if intervals else None

    return read


def backfill_row(row: Any, start_of: StartReader) -> RowOutcome:
    """Set the row's per-timeframe starts from its own partitions, and its shared start.

    Only timeframes the row counts bars for are looked up, and only under the
    row's current ``nautilus_id``: partitions left under an old venue are not its data.
    """
    outcome = RowOutcome()
    if not row.nautilus_id:
        return outcome
    before = (row.date_range_start, *(getattr(row, f) for f in TIMEFRAME_START_FIELDS.values()))
    for timeframe, start_field in TIMEFRAME_START_FIELDS.items():
        if not getattr(row, start_field.replace("date_range_start_", "bar_count_")):
            continue
        start = start_of(row.nautilus_id, timeframe)
        if start is None:
            outcome.missing.append(timeframe)
        else:
            setattr(row, start_field, start)
    row.date_range_start = earliest_start(row)
    after = (row.date_range_start, *(getattr(row, f) for f in TIMEFRAME_START_FIELDS.values()))
    outcome.changed = after != before
    return outcome


def backfill_catalog(rows: Iterable[Any], start_of: StartReader) -> BackfillSummary:
    """Backfill every row of a catalog; the caller owns the transaction."""
    summary = BackfillSummary()
    for row in rows:
        summary.rows += 1
        if not row.nautilus_id:
            summary.unqualified += 1
            continue
        outcome = backfill_row(row, start_of)
        summary.changed += outcome.changed
        summary.missing.extend((row.ticker, timeframe) for timeframe in outcome.missing)
    return summary
