"""Unit tests for ``SessionService`` (Story 2.3).

Pure orchestration tests — no database, no Nautilus. The repository is a
``MagicMock(spec=SyncTradingSessionRepository)`` returning detached
``TradingSession`` rows built with an explicit ``status=`` (the ORM's
``default=SessionStatus.CREATED`` is Python-side and only applies at flush,
so an unattached row built without it has ``status is None``). Time is an
injected ``FakeClock`` returning an aware ``datetime`` — there is no
``freezegun``/``time-machine`` dependency in this repo.
"""

import ast
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from src.db.exceptions import InvalidSessionTransition, RecordNotFoundError
from src.db.models.trading_session import TradingSession
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.session import SessionStatus
from src.services import session_service as service_module
from src.services.session_service import DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS, SessionService

pytestmark = pytest.mark.unit

SESSION_ID = UUID("11111111-1111-1111-1111-111111111111")


class FakeClock:
    """An aware-``datetime`` clock the test advances explicitly.

    Adapted from ``tests/unit/core/test_live_connection_monitor.py``'s
    monotonic-float ``FakeClock`` — this one returns a wall-clock ``datetime``
    because ``SessionService`` compares a heartbeat written by a *different
    process* against now, where monotonic seconds are meaningless.
    """

    def __init__(self, start: datetime = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def _session_row(
    status: SessionStatus,
    *,
    session_id: UUID = SESSION_ID,
    name: str = "alpha-session",
    last_heartbeat_at: datetime | None = None,
) -> TradingSession:
    """A detached ``TradingSession`` with every column the service reads set explicitly."""
    return TradingSession(
        session_id=session_id,
        name=name,
        status=status,
        spec={},
        last_heartbeat_at=last_heartbeat_at,
    )


def _repository(row: TradingSession | None, *, by_name: TradingSession | None = None) -> MagicMock:
    repository = MagicMock(spec=SyncTradingSessionRepository)
    repository.find_by_session_id.return_value = row
    repository.find_by_name.return_value = by_name
    return repository


class TestLegalTransitionsSucceedAndStampTimestamps:
    """AC #2: the four legal edges, and Dev Notes' timestamp-column map."""

    @pytest.mark.parametrize(
        "current, target, stamped_columns",
        [
            (
                SessionStatus.CREATED,
                SessionStatus.RUNNING,
                ("last_started_at", "last_heartbeat_at"),
            ),
            (SessionStatus.RUNNING, SessionStatus.STOPPED, ("last_stopped_at",)),
            (
                SessionStatus.STOPPED,
                SessionStatus.RUNNING,
                ("last_started_at", "last_heartbeat_at"),
            ),
            (SessionStatus.STOPPED, SessionStatus.SEALED, ("sealed_at",)),
        ],
        ids=["created_to_running", "running_to_stopped", "stopped_to_running", "stopped_to_sealed"],
    )
    def test_transition_succeeds_and_stamps_its_timestamp_columns(
        self, current, target, stamped_columns
    ):
        clock = FakeClock()
        row = _session_row(current)
        service = SessionService(_repository(row), time_source=clock)

        result = service.transition(row.session_id, to=target)

        assert result.status is target
        for column in stamped_columns:
            assert getattr(result, column) == clock.now

    def test_stopped_to_running_overwrites_the_previous_start_without_clearing_last_stopped_at(
        self,
    ):
        """Dev Notes: ``last_stopped_at`` is not cleared on restart."""
        clock = FakeClock()
        previous_stop = clock.now - timedelta(hours=1)
        row = _session_row(SessionStatus.STOPPED)
        row.last_stopped_at = previous_stop
        service = SessionService(_repository(row), time_source=clock)

        result = service.transition(row.session_id, to=SessionStatus.RUNNING)

        assert result.last_stopped_at == previous_stop
        assert result.last_started_at == clock.now


class TestReclaimAndRefusal:
    """AC #4/#5/#6: the one sanctioned ``running -> running`` path."""

    def test_a_stale_heartbeat_reclaims_and_logs_session_reclaimed(self):
        clock = FakeClock()
        stale_heartbeat = clock.now - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS + 1)
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=stale_heartbeat)
        service = SessionService(_repository(row), time_source=clock)

        with capture_logs() as captured:
            result = service.transition(row.session_id, to=SessionStatus.RUNNING)

        assert result.status is SessionStatus.RUNNING
        assert result.last_started_at == clock.now
        assert result.last_heartbeat_at == clock.now
        reclaimed = [entry for entry in captured if entry["event"] == "session.reclaimed"]
        assert len(reclaimed) == 1
        assert reclaimed[0]["name"] == row.name

    def test_a_fresh_heartbeat_refuses_naming_the_session(self):
        clock = FakeClock()
        fresh_heartbeat = clock.now - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS - 1)
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=fresh_heartbeat)
        service = SessionService(_repository(row), time_source=clock)

        with pytest.raises(InvalidSessionTransition, match=row.name):
            service.transition(row.session_id, to=SessionStatus.RUNNING)

        assert row.status is SessionStatus.RUNNING

    def test_a_heartbeat_exactly_at_the_threshold_is_fresh_not_stale(self):
        """Strictly ``>``, not ``>=`` — matching ``live_connection_monitor.py``."""
        clock = FakeClock()
        boundary_heartbeat = clock.now - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS)
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=boundary_heartbeat)
        service = SessionService(_repository(row), time_source=clock)

        with pytest.raises(InvalidSessionTransition):
            service.transition(row.session_id, to=SessionStatus.RUNNING)

    def test_a_null_heartbeat_on_a_running_session_reclaims(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=None)
        service = SessionService(_repository(row), time_source=clock)

        result = service.transition(row.session_id, to=SessionStatus.RUNNING)

        assert result.status is SessionStatus.RUNNING
        assert result.last_heartbeat_at == clock.now

    def test_a_future_heartbeat_clamps_to_age_zero_and_refuses(self):
        """Clock skew between processes must never read as stale."""
        clock = FakeClock()
        future_heartbeat = clock.now + timedelta(seconds=30)
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=future_heartbeat)
        service = SessionService(_repository(row), time_source=clock)

        with pytest.raises(InvalidSessionTransition, match="0s old"):
            service.transition(row.session_id, to=SessionStatus.RUNNING)

    def test_reclaim_stamps_last_heartbeat_at_which_is_load_bearing_for_ac6(self):
        """The stamp is what makes a concurrent loser re-read a fresh heartbeat."""
        clock = FakeClock()
        stale_heartbeat = clock.now - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS + 1)
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=stale_heartbeat)
        service = SessionService(_repository(row), time_source=clock)

        service.transition(row.session_id, to=SessionStatus.RUNNING)

        assert row.last_heartbeat_at == clock.now


class TestIllegalTransitionsRaiseAndLeaveStatusUnchanged:
    """AC #3: any edge outside the table raises, including every self-edge but one."""

    @pytest.mark.parametrize(
        "current, target",
        [
            (SessionStatus.CREATED, SessionStatus.SEALED),
            (SessionStatus.CREATED, SessionStatus.STOPPED),
            (SessionStatus.RUNNING, SessionStatus.SEALED),
            (SessionStatus.RUNNING, SessionStatus.CREATED),
            (SessionStatus.STOPPED, SessionStatus.CREATED),
            (SessionStatus.SEALED, SessionStatus.CREATED),
            (SessionStatus.SEALED, SessionStatus.RUNNING),
            (SessionStatus.SEALED, SessionStatus.STOPPED),
            (SessionStatus.CREATED, SessionStatus.CREATED),
            (SessionStatus.STOPPED, SessionStatus.STOPPED),
            (SessionStatus.SEALED, SessionStatus.SEALED),
        ],
        ids=[
            "created_to_sealed",
            "created_to_stopped",
            "running_to_sealed",
            "running_to_created",
            "stopped_to_created",
            "sealed_to_created",
            "sealed_to_running",
            "sealed_to_stopped",
            "created_self_edge",
            "stopped_self_edge",
            "sealed_self_edge",
        ],
    )
    def test_illegal_edge_raises_and_status_is_unchanged(self, current, target):
        row = _session_row(current)
        service = SessionService(_repository(row))

        with pytest.raises(InvalidSessionTransition, match=row.name):
            service.transition(row.session_id, to=target)

        assert row.status is current

    def test_sealed_has_no_outbound_transitions_at_all(self):
        """AC #3: sealed is terminal — every ``SessionStatus`` member as a target raises."""
        row = _session_row(SessionStatus.SEALED)
        service = SessionService(_repository(row))

        for target in SessionStatus:
            with pytest.raises(InvalidSessionTransition):
                service.transition(row.session_id, to=target)
            assert row.status is SessionStatus.SEALED


class TestTransitionOfAnUnknownSession:
    """``transition()`` never returns ``None`` for a miss — always the shared exception."""

    def test_raises_record_not_found_error(self):
        missing_id = uuid4()
        service = SessionService(_repository(None))

        with pytest.raises(RecordNotFoundError, match=str(missing_id)):
            service.transition(missing_id, to=SessionStatus.RUNNING)


class TestConstructorValidatesTheHeartbeatThreshold:
    """A ``nan`` or non-positive threshold would silently disable the reclaim forever."""

    @pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_a_non_finite_or_non_positive_threshold(self, bad_value):
        with pytest.raises(ValueError):
            SessionService(_repository(None), heartbeat_stale_after_seconds=bad_value)

    def test_default_threshold_is_three_times_the_heartbeat_interval(self):
        assert DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS == 90.0


class TestResolve:
    """AC #8: one shared name-or-UUID lookup (AR36)."""

    def test_resolves_by_exact_name(self):
        row = _session_row(SessionStatus.CREATED, name="beta-session")
        repository = _repository(None, by_name=row)
        service = SessionService(repository)

        found = service.resolve("beta-session")

        assert found is row
        repository.find_by_name.assert_called_with("beta-session")

    def test_resolves_by_uuid_string(self):
        row = _session_row(SessionStatus.CREATED, session_id=SESSION_ID)
        repository = _repository(row)
        service = SessionService(repository)

        found = service.resolve(str(SESSION_ID))

        assert found is row
        repository.find_by_session_id.assert_called_with(SESSION_ID)

    def test_a_uuid_that_parses_but_matches_no_row_falls_through_to_name_lookup(self):
        unmatched_uuid = uuid4()
        row = _session_row(SessionStatus.CREATED, name=str(unmatched_uuid))
        repository = _repository(None, by_name=row)
        service = SessionService(repository)

        found = service.resolve(str(unmatched_uuid))

        assert found is row
        repository.find_by_name.assert_called_with(str(unmatched_uuid))

    def test_an_unknown_identifier_raises_record_not_found_naming_it(self):
        repository = _repository(None, by_name=None)
        service = SessionService(repository)

        with pytest.raises(RecordNotFoundError, match="no-such-session"):
            service.resolve("no-such-session")

    def test_a_session_whose_name_is_a_valid_uuid_string_is_reachable(self):
        """UUID-first with fall-through: a miss on the UUID lookup still finds it by name."""
        name_that_looks_like_a_uuid = str(uuid4())
        row = _session_row(SessionStatus.CREATED, name=name_that_looks_like_a_uuid)
        repository = _repository(None, by_name=row)
        service = SessionService(repository)

        found = service.resolve(name_that_looks_like_a_uuid)

        assert found is row


class TestTheAR37StatusAssignmentGuard:
    """AC #1: only ``session_service.py`` may assign ``TradingSession.status``.

    A structural AST scan restricted to modules that import ``TradingSession``
    — immune to docstrings, comments, structlog kwargs like ``status="failed"``,
    and local variables named ``status``, unlike a bare grep, which the Dev
    Notes measured at 20 pre-existing ``status = `` hits and 5 ``.status = ``
    hits, none of them a ``trading_sessions`` write (Pre-verified finding 8).
    """

    ALLOWED_ASSIGNER = "src/services/session_service.py"

    @staticmethod
    def _imports_trading_session(tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.endswith("trading_session")
                and any(alias.name == "TradingSession" for alias in node.names)
            ):
                return True
        return False

    @staticmethod
    def _assigns_a_status_attribute(tree: ast.Module) -> bool:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            else:
                continue
            if any(isinstance(t, ast.Attribute) and t.attr == "status" for t in targets):
                return True
        return False

    def test_only_session_service_assigns_status_on_a_trading_session_importer(self, project_root):
        offenders = []
        for path in sorted((project_root / "src").rglob("*.py")):
            tree = ast.parse(path.read_text())
            if self._imports_trading_session(tree) and self._assigns_a_status_attribute(tree):
                offenders.append(str(path.relative_to(project_root)))

        assert offenders == [self.ALLOWED_ASSIGNER]


class TestImportPurity:
    """AC #7: no ``nautilus_trader``/``ibapi`` import. ``sqlalchemy`` is
    legitimately allowed here, unlike in ``src/models/session.py`` — this
    module is a database-facing service, not a framework-free domain model.
    """

    FORBIDDEN = {"nautilus_trader", "ibapi"}

    def test_top_level_imports_contain_no_live_trading_library(self):
        tree = ast.parse(Path(service_module.__file__).read_text())
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in self.FORBIDDEN
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in self.FORBIDDEN

    def test_importing_the_module_loads_no_nautilus_trader_or_ibapi(self):
        """Story 2.1's review proved the AST form alone is blind to transitive loading."""
        code = (
            "import sys, src.services.session_service;"
            "print(','.join(sorted(m for m in ('nautilus_trader', 'ibapi') if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(service_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""


class TestScopedGrepGates:
    """AC #1: Story 2.2's scoped-grep convention, encoded as a durable test
    rather than a one-off shell command. The epic's literal AC #1 wording
    ("no direct ``status =`` assignment exists outside it") is unsatisfiable
    as a bare grep — 20 pre-existing hits, none of them this story's to touch
    (Pre-verified finding 8) — so these are narrowed to the live path.
    """

    @staticmethod
    def _offenders(root: Path, project_root: Path, pattern: re.Pattern) -> set[str]:
        offenders: set[str] = set()
        for path in sorted(root.rglob("*.py")):
            for line in path.read_text().splitlines():
                if pattern.search(line):
                    offenders.add(str(path.relative_to(project_root)))
                    break
        return offenders

    def test_no_bare_assignment_of_a_sessionstatus_member_outside_this_module(self, project_root):
        pattern = re.compile(r"\.status\s*=\s*SessionStatus\.")
        offenders = self._offenders(project_root / "src", project_root, pattern)
        assert offenders <= {"src/services/session_service.py"}

    def test_no_bare_status_assignment_outside_this_module_across_the_live_path(self, project_root):
        pattern = re.compile(r"\.status\s*=[^=]")
        offenders: set[str] = set()
        for directory in ("services", "cli", "core", "db", "api"):
            offenders |= self._offenders(project_root / "src" / directory, project_root, pattern)
        assert offenders == {"src/services/session_service.py"}

    def test_this_module_never_commits_or_rolls_back_the_transaction(self):
        source = Path(service_module.__file__).read_text()
        assert "commit()" not in source
        assert "rollback()" not in source
