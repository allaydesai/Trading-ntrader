"""Regime calendar storage against Postgres: uniqueness and the append-only triggers (1.8).

Built by ``Base.metadata.create_all`` in a scratch schema, whose ``after_create`` hooks
install the same trigger SQL the migration does (pinned by
``tests/unit/db/test_regime_migration.py``).
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db.models.regime import RegimeCalendarBuild, RegimeCalendarVersion, RegimeLabel
from src.db.repositories.regime_repository_sync import SyncRegimeRepository

pytestmark = pytest.mark.integration

D0 = date(2020, 1, 2)
GT_NULL = {
    "leg_minor": None,
    "leg_intermediate": None,
    "leg_major": None,
    "ranging": None,
    "volatility": None,
}


def _version(
    label_set="detector",
    *,
    definition_hash="h" * 64,
    revision=1,
    reason=None,
    reference="SPY",
    catalog="firstrate-etf",
    data_end=D0 + timedelta(days=2),
):
    return RegimeCalendarVersion(
        reference=reference,
        catalog=catalog,
        label_set=label_set,
        definition={"label_set": label_set},
        definition_hash=definition_hash,
        input_revision=revision,
        revision_reason=reason,
        frozen_stats={"volatility_median": 0.15} if label_set == "ground_truth" else {},
        data_start=D0,
        data_end=data_end,
        git_commit="c" * 40,
    )


def _row(day: int, labels: dict, build: int = 1) -> dict:
    return {
        "session_date": D0 + timedelta(days=day),
        "labels": labels,
        "set_in_build": {k: (None if v is None else build) for k, v in labels.items()},
        "bar_hash": f"{day:032d}",
        "inserted_in_build": build,
    }


def refused(session, statement, match: str) -> None:
    with pytest.raises(DBAPIError, match=match):
        with session.begin_nested():
            session.execute(statement)


@pytest.fixture
def repo(sync_db_session):
    return SyncRegimeRepository(sync_db_session)


@pytest.fixture
def detector(repo):
    version = _version("detector")
    repo.add_version(version, git_commit="c" * 40)
    repo.append_labels(version.id, [_row(d, {"sma200": "up"}) for d in range(3)])
    return version


@pytest.fixture
def truth(repo):
    version = _version("ground_truth", definition_hash="g" * 64)
    repo.add_version(version, git_commit="c" * 40)
    labels = {**GT_NULL, "leg_minor": "up"}
    repo.append_labels(version.id, [_row(d, labels) for d in range(3)])
    return version


def _where(version, day: int):
    return (
        RegimeLabel.version_id == version.id,
        RegimeLabel.session_date == D0 + timedelta(days=day),
    )


class TestVersionsAndLabels:
    def test_insert_and_read_by_date_range(self, repo, detector):
        rows = repo.detector_labels(detector, start=D0 + timedelta(days=1), end=D0 + timedelta(2))
        assert [r.session_date for r in rows] == [D0 + timedelta(1), D0 + timedelta(2)]
        assert rows[0].labels == {"sma200": "up"}
        assert detector.version == 1 and detector.build_no == 1
        assert repo.build_data_end(detector.id, 1) == detector.data_end

    def test_readers_refuse_the_other_label_set(self, repo, detector, truth):
        with pytest.raises(ValueError):
            repo.detector_labels(truth)
        with pytest.raises(ValueError):
            repo.ground_truth_labels(detector)

    def test_identity_is_unique(self, sync_db_session, detector):
        duplicate = _version("detector")  # same reference, catalog, set, hash and revision
        duplicate.version, duplicate.build_no = 9, 1
        with pytest.raises(IntegrityError, match="uq_regime_version_identity"):
            with sync_db_session.begin_nested():
                sync_db_session.add(duplicate)
                sync_db_session.flush()

    def test_revision_two_needs_a_reason(self, repo, sync_db_session, detector):
        with pytest.raises(DBAPIError, match="ck_regime_versions_revision_reason"):
            with sync_db_session.begin_nested():
                repo.add_version(_version("detector", revision=2), git_commit=None)
        revised = _version("detector", revision=2, reason="reference_bars_changed")
        repo.add_version(revised, git_commit=None)
        assert revised.version == 2 and revised.input_revision == 2

    def test_numbering_and_latest_are_scoped(self, repo, detector):
        other = _version("detector", catalog="other-catalog")
        repo.add_version(other, git_commit=None)
        qqq = _version("detector", reference="QQQ")
        repo.add_version(qqq, git_commit=None)
        assert other.version == 1 and qqq.version == 1
        assert repo.latest_version("SPY", "firstrate-etf", "detector").id == detector.id
        assert repo.latest_version("SPY", "other-catalog", "detector").id == other.id
        assert repo.latest_version("SPY", "firstrate-etf", "ground_truth") is None


class TestDetectorRowsAreImmutable:
    def test_update_is_refused(self, sync_db_session, detector):
        stmt = update(RegimeLabel).where(*_where(detector, 0)).values(labels={"sma200": "down"})
        refused(sync_db_session, stmt, "detector row is immutable")

    def test_delete_is_refused(self, sync_db_session, detector):
        refused(sync_db_session, delete(RegimeLabel).where(*_where(detector, 0)), "append-only")


class TestGroundTruthRows:
    def _fill(self, version, day, labels, set_in_build):
        return (
            update(RegimeLabel)
            .where(*_where(version, day))
            .values(labels=labels, set_in_build=set_in_build)
        )

    def test_null_to_value_is_allowed_with_the_current_build(self, repo, sync_db_session, truth):
        repo.advance_build(truth, truth.data_end, git_commit=None)
        labels = {**GT_NULL, "leg_minor": "up", "ranging": True}
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 1, "ranging": 2}
        repo.fill_ground_truth(
            truth.id, [{"session_date": D0, "labels": labels, "set_in_build": sets}]
        )
        (row,) = repo.ground_truth_labels(truth, end=D0)
        assert row.labels["ranging"] is True and row.set_in_build["ranging"] == 2

    def test_fill_must_record_the_current_build(self, sync_db_session, truth):
        labels = {**GT_NULL, "leg_minor": "up", "ranging": True}
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 1, "ranging": 7}
        refused(sync_db_session, self._fill(truth, 0, labels, sets), "current build")

    def test_value_to_other_value_is_refused(self, sync_db_session, truth):
        labels = {**GT_NULL, "leg_minor": "down"}
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 1}
        refused(sync_db_session, self._fill(truth, 0, labels, sets), "already set")

    def test_value_back_to_null_is_refused(self, sync_db_session, truth):
        sets = {k: None for k in GT_NULL}
        refused(sync_db_session, self._fill(truth, 0, GT_NULL, sets), "already set")

    def test_set_in_build_entry_never_changes(self, sync_db_session, truth):
        labels = {**GT_NULL, "leg_minor": "up"}
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 5}
        refused(sync_db_session, self._fill(truth, 0, labels, sets), "already set")
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 1, "ranging": 1}
        refused(sync_db_session, self._fill(truth, 0, labels, sets), "unset ranging")

    def test_bar_hash_and_keys_never_change(self, sync_db_session, truth):
        stmt = update(RegimeLabel).where(*_where(truth, 0)).values(bar_hash="x" * 32)
        refused(sync_db_session, stmt, "bar_hash")
        stmt = (
            update(RegimeLabel)
            .where(*_where(truth, 0))
            .values(labels={**GT_NULL, "leg_minor": "up", "extra": None})
        )
        refused(sync_db_session, stmt, "label keys")

    def test_delete_is_refused(self, sync_db_session, truth):
        refused(sync_db_session, delete(RegimeLabel).where(*_where(truth, 0)), "append-only")


class TestVersionRowIsImmutable:
    def test_delete_is_refused(self, sync_db_session, detector):
        stmt = delete(RegimeCalendarVersion).where(RegimeCalendarVersion.id == detector.id)
        refused(sync_db_session, stmt, "never deleted|violates foreign key")

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("definition", {"label_set": "detector", "x": 1}),
            ("definition_hash", "z" * 64),
            ("input_revision", 2),
            ("revision_reason", "median_recut"),
            ("frozen_stats", {"volatility_median": 0.2}),
            ("reference", "QQQ"),
            ("catalog", "other"),
            ("label_set", "ground_truth"),
            ("version", 5),
            ("data_start", date(2019, 1, 2)),
        ],
    )
    def test_identity_columns_never_change(self, sync_db_session, detector, column, value):
        stmt = (
            update(RegimeCalendarVersion)
            .where(RegimeCalendarVersion.id == detector.id)
            .values({column: value, "build_no": detector.build_no + 1})
        )
        refused(sync_db_session, stmt, "only data_end and build_no")

    def test_data_end_forward_and_build_increment_are_allowed(self, repo, detector):
        repo.advance_build(detector, detector.data_end + timedelta(days=5), git_commit=None)
        assert detector.build_no == 2
        assert repo.build_data_end(detector.id, 2) == detector.data_end

    def test_data_end_backwards_or_build_skip_is_refused(self, sync_db_session, detector):
        where = RegimeCalendarVersion.id == detector.id
        stmt = update(RegimeCalendarVersion).where(where).values(data_end=D0, build_no=2)
        refused(sync_db_session, stmt, "moves forward")
        stmt = update(RegimeCalendarVersion).where(where).values(build_no=3)
        refused(sync_db_session, stmt, "increments by one")

    def test_builds_are_append_only(self, sync_db_session, detector):
        where = RegimeCalendarBuild.version_id == detector.id
        refused(sync_db_session, update(RegimeCalendarBuild).where(where).values(build_no=9),
                "append-only")  # fmt: skip
        refused(sync_db_session, delete(RegimeCalendarBuild).where(where), "append-only")


class TestReadAsOfBuild:
    def test_later_values_and_rows_are_hidden(self, repo, truth):
        repo.advance_build(truth, D0 + timedelta(days=3), git_commit=None)
        repo.append_labels(truth.id, [_row(3, GT_NULL, build=2)])  # all-null row, build 2
        labels = {**GT_NULL, "leg_minor": "up", "volatility": "high"}
        sets = {**{k: None for k in GT_NULL}, "leg_minor": 1, "volatility": 2}
        repo.fill_ground_truth(
            truth.id, [{"session_date": D0, "labels": labels, "set_in_build": sets}]
        )
        now = repo.ground_truth_labels(truth)
        then = repo.ground_truth_labels(truth, as_of_build=1)
        assert len(now) == 4 and len(then) == 3
        assert now[0].labels["volatility"] == "high"
        assert then[0].labels["volatility"] is None and then[0].labels["leg_minor"] == "up"
        assert repo.build_data_end(truth.id, 1) == D0 + timedelta(days=2)
        assert repo.build_data_end(truth.id, 2) == D0 + timedelta(days=3)
