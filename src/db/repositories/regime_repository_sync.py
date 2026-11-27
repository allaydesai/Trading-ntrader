"""Regime calendar data access (sync only: the research MCP is the only reader).

Sync-only is a recorded exception to ``docs/agent/persistence.md`` (Allay, 2026-11-26):
no async caller exists. The surface is **insert and append only**. There is no method
that deletes anything or rewrites a stored value; the one write to an existing label row,
``fill_ground_truth``, turns ``null`` values into values, which is all the database
trigger allows. Versions only ever move forward through ``advance_build``.

Ground-truth rows are read only through ``ground_truth_labels``, which the governance
test ``tests/unit/governance/test_regime_ground_truth_boundary.py`` limits to named files.
``detector_labels`` refuses a ground-truth version, so it can never be a back door.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from src.db.models.regime import (
    RegimeCalendarBuild,
    RegimeCalendarVersion,
    RegimeLabel,
    RegimeReport,
)


@dataclass(frozen=True)
class LabelRow:
    """One stored session as read, possibly as of an earlier build."""

    session_date: date
    labels: dict[str, Any]
    bar_hash: str
    inserted_in_build: int
    set_in_build: dict[str, Any]


class SyncRegimeRepository:
    """Calendar versions, their labels and build history, on a sync session."""

    def __init__(self, session: Session):
        self.session = session

    # -- versions ---------------------------------------------------------------------

    def latest_version(
        self, reference: str, catalog: str, label_set: str
    ) -> RegimeCalendarVersion | None:
        """The highest version number for (reference, catalog, label set)."""
        stmt = (
            select(RegimeCalendarVersion)
            .where(
                RegimeCalendarVersion.reference == reference,
                RegimeCalendarVersion.catalog == catalog,
                RegimeCalendarVersion.label_set == label_set,
            )
            .order_by(RegimeCalendarVersion.version.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def find_version(
        self, reference: str, catalog: str, label_set: str, version: int
    ) -> RegimeCalendarVersion | None:
        """A version by its number, scoped to (reference, catalog, label set)."""
        stmt = select(RegimeCalendarVersion).where(
            RegimeCalendarVersion.reference == reference,
            RegimeCalendarVersion.catalog == catalog,
            RegimeCalendarVersion.label_set == label_set,
            RegimeCalendarVersion.version == version,
        )
        return self.session.scalars(stmt).first()

    def latest_revision(
        self,
        reference: str,
        catalog: str,
        label_set: str,
        definition_hash: str,
        *,
        for_update: bool = False,
    ) -> RegimeCalendarVersion | None:
        """The newest input revision of one definition; ``for_update`` locks its row."""
        stmt = (
            select(RegimeCalendarVersion)
            .where(
                RegimeCalendarVersion.reference == reference,
                RegimeCalendarVersion.catalog == catalog,
                RegimeCalendarVersion.label_set == label_set,
                RegimeCalendarVersion.definition_hash == definition_hash,
            )
            .order_by(RegimeCalendarVersion.input_revision.desc())
            .limit(1)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self.session.scalars(stmt).first()

    def add_version(self, row: RegimeCalendarVersion, *, git_commit: str | None) -> None:
        """Insert a new version at build 1 and record that build. ``version`` is assigned
        as the next number in its (reference, catalog, label set) scope."""
        latest = self.latest_version(row.reference, row.catalog, row.label_set)
        row.version = (latest.version + 1) if latest else 1
        row.build_no = 1
        row.git_commit = git_commit
        self.session.add(row)
        self.session.flush()
        self._record_build(row, git_commit)

    def advance_build(
        self, row: RegimeCalendarVersion, data_end: date, *, git_commit: str | None
    ) -> None:
        """Move a version to its next build (``build_no`` + 1, ``data_end`` forward)."""
        row.build_no += 1
        row.data_end = data_end
        self.session.flush()
        self._record_build(row, git_commit)

    def _record_build(self, row: RegimeCalendarVersion, git_commit: str | None) -> None:
        self.session.add(
            RegimeCalendarBuild(
                version_id=row.id, build_no=row.build_no, data_end=row.data_end,
                git_commit=git_commit,
            )
        )  # fmt: skip
        self.session.flush()

    def find_build(self, version_id: int, build_no: int) -> RegimeCalendarBuild | None:
        """One build's history row."""
        return self.session.get(RegimeCalendarBuild, (version_id, build_no))

    def build_data_end(self, version_id: int, build_no: int) -> date | None:
        """The ``data_end`` a build recorded, from the append-only history."""
        stmt = select(RegimeCalendarBuild.data_end).where(
            RegimeCalendarBuild.version_id == version_id,
            RegimeCalendarBuild.build_no == build_no,
        )
        return self.session.scalar(stmt)

    # -- labels -----------------------------------------------------------------------

    def append_labels(self, version_id: int, rows: Iterable[dict[str, Any]]) -> int:
        """Insert new session rows (``session_date``, ``labels``, ``set_in_build``,
        ``bar_hash``, ``inserted_in_build``). Returns the count."""
        values = [{"version_id": version_id, **row} for row in rows]
        if values:
            self.session.execute(insert(RegimeLabel), values)
        return len(values)

    def fill_ground_truth(self, version_id: int, fills: Iterable[dict[str, Any]]) -> int:
        """Write filled ground-truth payloads (``session_date``, ``labels``,
        ``set_in_build``). The trigger rejects anything but ``null`` → value."""
        values = [{"version_id": version_id, **fill} for fill in fills]
        if values:
            self.session.execute(update(RegimeLabel), values)
        return len(values)

    def count_labels(self, version_id: int) -> int:
        """Stored rows of a version."""
        stmt = select(func.count()).where(RegimeLabel.version_id == version_id)
        return int(self.session.scalar(stmt) or 0)

    def detector_labels(self, version: RegimeCalendarVersion, **window: Any) -> list[LabelRow]:
        """Detector rows by date range (see ``_read_labels``). Refuses a ground-truth version."""
        if version.label_set != "detector":
            raise ValueError("detector_labels reads detector versions only")
        return self._read_labels(version, **window)

    def ground_truth_labels(self, version: RegimeCalendarVersion, **window: Any) -> list[LabelRow]:
        """Ground-truth (hindsight) rows by date range. Permitted callers only."""
        if version.label_set != "ground_truth":
            raise ValueError("ground_truth_labels reads ground-truth versions only")
        return self._read_labels(version, **window)

    def _read_labels(
        self,
        version: RegimeCalendarVersion,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of_build: int | None = None,
        limit: int | None = None,
    ) -> list[LabelRow]:
        """Rows in ``[start, end]`` in date order. As of build *n*: rows inserted later
        are hidden, and values set later read as ``None``."""
        stmt = select(RegimeLabel).where(RegimeLabel.version_id == version.id)
        if start is not None:
            stmt = stmt.where(RegimeLabel.session_date >= start)
        if end is not None:
            stmt = stmt.where(RegimeLabel.session_date <= end)
        if as_of_build is not None:
            stmt = stmt.where(RegimeLabel.inserted_in_build <= as_of_build)
        stmt = stmt.order_by(RegimeLabel.session_date)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_as_of(row, as_of_build) for row in self.session.scalars(stmt)]


def _as_of(row: RegimeLabel, build: int | None) -> LabelRow:
    labels, set_in = dict(row.labels), dict(row.set_in_build)
    if build is not None:
        for key, set_at in row.set_in_build.items():
            if set_at is not None and set_at > build:
                labels[key], set_in[key] = None, None
    return LabelRow(row.session_date, labels, row.bar_hash, row.inserted_in_build, set_in)


class UnknownCalendarBuild(ValueError):
    """A report names a (version, build) pair the calendar has no record of."""


class SyncRegimeReportRepository:
    """Stored regime reports: insert and read only (the table is append-only)."""

    def __init__(self, session: Session):
        self.session = session

    def add_report(self, row: RegimeReport) -> RegimeReport:
        """Insert a report after checking every (version, build) it names exists.

        The ``regime_reports_append_only`` trigger enforces the same rule; this check
        only turns its error into a typed one before anything is sent.
        """
        if not row.calendar_version_ids:
            raise UnknownCalendarBuild("a report must name the calendar versions it read")
        for entry in row.calendar_version_ids:
            key = (int(entry["version_id"]), int(entry["build_no"]))
            if self.session.get(RegimeCalendarBuild, key) is None:
                raise UnknownCalendarBuild(f"no calendar build {key[1]} of version {key[0]}")
        self.session.add(row)
        self.session.flush()
        return row

    def find_report(self, report_id: UUID) -> RegimeReport | None:
        """A stored report by its public id."""
        stmt = select(RegimeReport).where(RegimeReport.report_id == report_id)
        return self.session.scalars(stmt).first()
