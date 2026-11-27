"""Stored regime reports against Postgres: append-only, stamped with real versions (2.6).

Built by ``Base.metadata.create_all`` in a scratch schema, whose ``after_create`` hook
installs the same trigger SQL as migration ``c81e5a4d9f27`` (pinned by
``tests/unit/db/test_regime_migration.py``).
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db.models.regime import RegimeCalendarVersion, RegimeReport
from src.db.repositories.regime_repository_sync import (
    SyncRegimeReportRepository,
    SyncRegimeRepository,
    UnknownCalendarBuild,
)

pytestmark = pytest.mark.integration


def _version(label_set: str = "detector") -> RegimeCalendarVersion:
    return RegimeCalendarVersion(
        reference="SPY",
        catalog="firstrate-etf",
        label_set=label_set,
        definition={"label_set": label_set},
        definition_hash="h" * 64,
        input_revision=1,
        frozen_stats={},
        data_start=date(2020, 1, 2),
        data_end=date(2020, 1, 3),
    )


@pytest.fixture
def version(sync_db_session):
    row = _version()
    SyncRegimeRepository(sync_db_session).add_version(row, git_commit="c" * 40)
    return row


@pytest.fixture
def reports(sync_db_session):
    return SyncRegimeReportRepository(sync_db_session)


def _report(version_ids, **overrides) -> RegimeReport:
    fields = {
        "report_id": uuid4(),
        "kind": "profile",
        "run_ids": ["8a12e455-0641-40e4-b11c-7df30a3aa59d"],
        "calendar_version_ids": version_ids,
        "labels_hash": "a" * 64,
        "label_set": "detector",
        "hindsight": False,
        "params": {"lag_days": 1, "overrides": {}},
        "result": {"families": {}},
        "git_commit": "c" * 40,
    }
    return RegimeReport(**(fields | overrides))


def _refused(session, statement, match):
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(statement)


def test_a_report_is_stored_and_read_back(reports, version):
    row = _report([{"version_id": version.id, "build_no": 1}])
    reports.add_report(row)
    found = reports.find_report(row.report_id)
    assert found is not None and found.id == row.id
    assert found.calendar_version_ids == [{"version_id": version.id, "build_no": 1}]
    assert found.created_at is not None


def test_unknown_version_or_build_is_refused(reports, version):
    with pytest.raises(UnknownCalendarBuild):
        reports.add_report(_report([{"version_id": version.id + 999, "build_no": 1}]))
    with pytest.raises(UnknownCalendarBuild):
        reports.add_report(_report([{"version_id": version.id, "build_no": 2}]))


def test_the_trigger_refuses_unknown_and_empty_version_lists(sync_db_session, version):
    """The database rule holds even past the repository's own check."""
    for ids, match in (
        ([{"version_id": version.id, "build_no": 7}], "unknown calendar"),
        ([], "non-empty"),
    ):
        with pytest.raises(DBAPIError, match=match):
            with sync_db_session.begin_nested():
                sync_db_session.add(_report(ids))
                sync_db_session.flush()


def test_reports_are_append_only(sync_db_session, reports, version):
    row = _report([{"version_id": version.id, "build_no": 1}])
    reports.add_report(row)
    where = RegimeReport.id == row.id
    _refused(sync_db_session, update(RegimeReport).where(where).values(kind="scorecard"),
             "append-only")  # fmt: skip
    _refused(sync_db_session, delete(RegimeReport).where(where), "append-only")


def test_ground_truth_report_must_be_hindsight(sync_db_session, version):
    row = _report([{"version_id": version.id, "build_no": 1}], label_set="ground_truth")
    with pytest.raises(DBAPIError, match="ck_regime_reports_hindsight"):
        with sync_db_session.begin_nested():
            sync_db_session.add(row)
            sync_db_session.flush()


def test_report_ids_are_unique(sync_db_session, reports, version):
    ids = [{"version_id": version.id, "build_no": 1}]
    first = _report(ids)
    reports.add_report(first)
    with pytest.raises(IntegrityError):
        with sync_db_session.begin_nested():
            reports.add_report(_report(ids, report_id=first.report_id))
