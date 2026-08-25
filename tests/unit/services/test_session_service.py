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
    last_started_at: datetime | None = None,
    last_bar_at: datetime | None = None,
    runtime_flags: dict | None = None,
) -> TradingSession:
    """A detached ``TradingSession`` with every column the service reads set explicitly."""
    return TradingSession(
        session_id=session_id,
        name=name,
        status=status,
        spec={},
        last_heartbeat_at=last_heartbeat_at,
        last_started_at=last_started_at,
        last_bar_at=last_bar_at,
        runtime_flags=runtime_flags,
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

    def test_a_row_named_another_rows_session_id_resolves_to_the_uuid_match(self):
        """Story 2.8's resolve-precedence pin: ``find_by_session_id`` before
        ``find_by_name``, deliberately and without an ambiguity error
        (deferred-work.md, "resolve()'s UUID-before-name precedence can
        silently shadow" — Story 2.3 Judgment call #6). Row A's ``session_id``
        and row B's ``name`` collide on the same string; ``resolve()`` must
        return A, the UUID match, and never even ask the name lookup.
        Rejecting UUID-shaped ``--name`` values at creation stays open for the
        Epic 2 retro.
        """
        row_a = _session_row(SessionStatus.CREATED, session_id=SESSION_ID, name="row-a")
        row_b_shadowed_by_the_uuid = _session_row(
            SessionStatus.CREATED, session_id=uuid4(), name=str(SESSION_ID)
        )
        repository = _repository(row_a, by_name=row_b_shadowed_by_the_uuid)
        service = SessionService(repository)

        found = service.resolve(str(SESSION_ID))

        assert found is row_a
        repository.find_by_name.assert_not_called()


class TestTheLockedReadIsActuallyRequested:
    """AC #6: the safety-critical half that lives in *this* module.

    ``for_update=True`` is what makes the reclaim decision serialisable. Before
    this test the argument had no unit-tier coverage at all: deleting it from
    ``transition()`` left every unit test green, and the only test that would
    have noticed sits in ``tests/integration/db/``, which CI ``--ignore``s.
    """

    def test_transition_reads_the_row_with_the_lock_requested(self):
        row = _session_row(SessionStatus.CREATED)
        repository = _repository(row)
        service = SessionService(repository, time_source=FakeClock())

        service.transition(SESSION_ID, to=SessionStatus.RUNNING)

        repository.find_by_session_id.assert_called_once_with(SESSION_ID, for_update=True)

    def test_resolve_reads_without_the_lock(self):
        """``resolve()`` is a plain lookup — it must not hold rows for writers."""
        row = _session_row(SessionStatus.CREATED)
        repository = _repository(row)

        SessionService(repository).resolve(str(SESSION_ID))

        repository.find_by_session_id.assert_called_once_with(SESSION_ID)


class TestTheTransitionTableIsComplete:
    """Every ``SessionStatus`` must appear as a key, or it is silently terminal.

    ``_LEGAL_TRANSITIONS.get(current, frozenset())`` turns a missing key into a
    session that can never move again — a stuck state NFR11 forbids — and does
    it without raising. A fifth status member added later would strand sessions
    with no test going red; this is that test.
    """

    def test_every_session_status_is_a_key_in_the_legal_transition_table(self):
        assert set(service_module._LEGAL_TRANSITIONS) == set(SessionStatus)

    def test_every_target_in_the_table_is_a_real_session_status(self):
        for targets in service_module._LEGAL_TRANSITIONS.values():
            assert targets <= set(SessionStatus)


class TestStatusValuesAreNormalisedBeforeAnyDecision:
    """``SessionStatus`` is a ``StrEnum``, so equality and identity disagree.

    ``"running" in frozenset({SessionStatus.RUNNING})`` is ``True`` while
    ``"running" is SessionStatus.RUNNING`` is ``False``. The reclaim guard uses
    identity and the legality check uses membership, so an un-normalised raw
    string took a *different path through the same decision* — skipping the
    heartbeat guard entirely.
    """

    def test_a_raw_string_target_still_reaches_the_reclaim_guard(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=clock.now)
        service = SessionService(_repository(row), time_source=clock)

        with pytest.raises(InvalidSessionTransition, match="another process appears live"):
            service.transition(SESSION_ID, to="running")

    def test_a_raw_string_target_stores_a_real_enum_member_not_a_string(self):
        row = _session_row(SessionStatus.CREATED)
        service = SessionService(_repository(row), time_source=FakeClock())

        result = service.transition(SESSION_ID, to="running")

        assert result.status is SessionStatus.RUNNING

    def test_an_unknown_target_raises_the_documented_exception(self):
        row = _session_row(SessionStatus.CREATED)
        service = SessionService(_repository(row), time_source=FakeClock())

        with pytest.raises(InvalidSessionTransition, match="not a valid session status"):
            service.transition(SESSION_ID, to="paused")

    def test_a_row_whose_stored_status_is_none_raises_instead_of_attributeerror(self):
        """The module docstring documents this state; the code must survive it.

        An unattached row has ``status is None`` because the ORM default is
        applied at flush. Interpolating ``current.value`` into the refusal
        message raised ``AttributeError``, escaping every
        ``except InvalidSessionTransition`` handler.
        """
        row = TradingSession(session_id=SESSION_ID, name="alpha-session", spec={})
        assert row.status is None
        service = SessionService(_repository(row), time_source=FakeClock())

        with pytest.raises(InvalidSessionTransition, match="not a valid session status"):
            service.transition(SESSION_ID, to=SessionStatus.RUNNING)


class TestResolveSurvivesNonStringIdentifiers:
    """AC #8: ``resolve()`` raises ``RecordNotFoundError``, and only that.

    ``UUID()`` raises ``TypeError`` for ``None`` and ``bytes`` and
    ``AttributeError`` for an ``int`` or an already-parsed ``UUID`` — none of
    which the original ``except ValueError`` caught, so each escaped as a raw
    traceback and broke the documented ``Raises:`` contract.
    """

    @pytest.mark.parametrize(
        "identifier",
        [None, 123, b"not-a-uuid", uuid4()],
        ids=["none", "int", "bytes", "uuid_object"],
    )
    def test_a_non_string_identifier_raises_record_not_found(self, identifier):
        service = SessionService(_repository(None))

        with pytest.raises(RecordNotFoundError):
            service.resolve(identifier)


class TestTheThresholdCannotSilentlyDisableTheReclaim:
    """A threshold that no heartbeat can ever exceed is the failure the
    constructor's validation exists to prevent — ``math.isfinite`` alone
    catches ``nan`` and ``inf`` but not ``1e300``, which disables the reclaim
    just as completely.
    """

    @pytest.mark.parametrize(
        "threshold",
        [1e300, True, "90", None],
        ids=["huge_but_finite", "bool_true", "numeric_string", "none"],
    )
    def test_a_threshold_that_would_disable_the_reclaim_is_rejected(self, threshold):
        with pytest.raises(ValueError, match="heartbeat_stale_after_seconds"):
            SessionService(_repository(None), heartbeat_stale_after_seconds=threshold)

    def test_the_upper_bound_is_accepted_and_one_second_past_it_is_not(self):
        limit = service_module.MAX_HEARTBEAT_STALE_AFTER_SECONDS

        SessionService(_repository(None), heartbeat_stale_after_seconds=limit)

        with pytest.raises(ValueError, match="heartbeat_stale_after_seconds"):
            SessionService(_repository(None), heartbeat_stale_after_seconds=limit + 1)


class TestARefusedReclaimIsLogged:
    """A second process attempting to take over a live session is exactly the
    signal an operator needs (AR41). Raising alone drops it entirely whenever
    the caller swallows the exception.
    """

    def test_a_refused_reclaim_logs_a_warning_naming_the_session(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_heartbeat_at=clock.now)
        service = SessionService(_repository(row), time_source=clock)

        with capture_logs() as logs:
            with pytest.raises(InvalidSessionTransition):
                service.transition(SESSION_ID, to=SessionStatus.RUNNING)

        refusals = [entry for entry in logs if entry["event"] == "session.reclaim_refused"]
        assert len(refusals) == 1
        assert refusals[0]["log_level"] == "warning"
        assert refusals[0]["name"] == "alpha-session"
        assert refusals[0]["session_id"] == str(SESSION_ID)
        assert refusals[0]["heartbeat_age_seconds"] == 0.0


class TestTheAR37StatusAssignmentGuard:
    """AC #1: only ``session_service.py`` may assign ``TradingSession.status``.

    A structural AST scan across **every** module under ``src/`` — immune to
    docstrings, comments, structlog kwargs like ``status="failed"`` and local
    variables named ``status``, unlike a bare grep, which the Dev Notes measured
    at 20 pre-existing ``status = `` hits (Pre-verified finding 8).

    The scan deliberately carries **no import filter**. An earlier form
    inspected only modules whose ``from ... import TradingSession`` ended in
    ``trading_session``; measured, that examined 4 of 192 files and was blind to
    the ``src.db.models`` re-export, to any module that receives a row as a
    parameter without importing the class, and to ``src/cli/commands/live.py``
    — the one CLI module that actually holds live ``TradingSession`` objects. A
    planted probe using the re-export passed it. Widening costs an allowlist of
    two pre-existing Pydantic models whose ``self.status`` is a wholly unrelated
    field, which is a far better trade than a guard that does not look.

    Known limits, stated rather than implied (the Epic 1 retro's rule about not
    overstating what a guard protects): this sees *attribute assignment* only.
    ``setattr(row, "status", x)``, ``update().values(status=...)``, a bulk
    ``query.update({...})`` and raw SQL are not assignments and would not be
    caught. AR37 is enforced here for the form a developer would actually
    reach for, not proven for every conceivable one.
    """

    ALLOWED_ASSIGNER = "src/services/session_service.py"

    #: Pre-existing ``self.status =`` writes on Pydantic models — catalog
    #: metadata and strategy definitions. Neither is a ``trading_sessions`` row,
    #: and neither is this story's to change (Pre-verified finding 8).
    UNRELATED_STATUS_MODELS = frozenset(
        {"src/models/catalog_metadata.py", "src/models/strategy.py"}
    )

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

    def test_only_session_service_assigns_a_status_attribute_anywhere_under_src(self, project_root):
        scanned = 0
        offenders = set()
        for path in sorted((project_root / "src").rglob("*.py")):
            scanned += 1
            if self._assigns_a_status_attribute(ast.parse(path.read_text())):
                offenders.add(str(path.relative_to(project_root)))

        # Without this, a scan that walked nothing — a moved fixture, an
        # uninitialised submodule, a renamed tree — would pass vacuously.
        assert scanned > 100, f"the guard only walked {scanned} files; it is not scanning src/"
        assert offenders == {self.ALLOWED_ASSIGNER} | set(self.UNRELATED_STATUS_MODELS)


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

    def test_no_bare_assignment_of_a_sessionstatus_member_anywhere_under_src(self, project_root):
        """Expected offenders: **none**, including this module.

        ``transition()`` assigns the validated ``to`` parameter, never a
        ``SessionStatus.`` literal, so the correct expectation is the empty set
        — not "only ``session_service.py``", which is what the story predicted
        and what an earlier ``<=`` (subset) assertion quietly accommodated. A
        subset assertion is satisfied by the empty set, so it would also have
        passed if the scan had silently stopped matching anything.
        """
        pattern = re.compile(r"\.status\s*=\s*SessionStatus\.")
        offenders = self._offenders(project_root / "src", project_root, pattern)
        assert offenders == set()

    def test_no_bare_status_assignment_outside_this_module_anywhere_under_src(self, project_root):
        """Scans all of ``src/``, not a hard-coded five directories.

        The earlier form listed ``services``/``cli``/``core``/``db``/``api``,
        leaving ``src/models`` and ``src/utils`` — and any package added later —
        outside both this gate and the AST guard simultaneously.
        """
        pattern = re.compile(r"\.status\s*=[^=]")
        offenders = self._offenders(project_root / "src", project_root, pattern)
        assert offenders == {
            "src/services/session_service.py",
            *TestTheAR37StatusAssignmentGuard.UNRELATED_STATUS_MODELS,
        }

    def test_this_module_never_commits_or_rolls_back_the_transaction(self):
        source = Path(service_module.__file__).read_text()
        assert "commit()" not in source
        assert "rollback()" not in source


class TestRecordActivity:
    """AR32's liveness write (Story 2.5, AC #5): the runner's heartbeat path."""

    def _service(self, row, clock: FakeClock) -> tuple[SessionService, MagicMock]:
        repository = _repository(row)
        return SessionService(repository, time_source=clock), repository

    def test_an_explicit_at_is_what_lands_in_last_heartbeat_at(self):
        """The runner's own clock must reach the column.

        Without this the ``time_source`` seam on the adapter is decorative and
        Tasks 6 and 8 cannot drive the cadence deterministically.
        """
        clock = FakeClock()
        started = clock.now
        stamped = clock.now.replace(microsecond=123456)
        row = _session_row(SessionStatus.RUNNING, last_started_at=started)
        service, _ = self._service(row, clock)

        service.record_activity(SESSION_ID, started_at=started, at=stamped)

        assert row.last_heartbeat_at == stamped

    def test_without_at_the_injected_time_source_is_used(self):
        clock = FakeClock()
        started = clock.now
        row = _session_row(SessionStatus.RUNNING, last_started_at=started)
        service, _ = self._service(row, clock)
        clock.advance(45)

        service.record_activity(SESSION_ID, started_at=started)

        assert row.last_heartbeat_at == clock.now

    def test_bar_seen_at_is_the_bars_observed_time_not_now(self):
        clock = FakeClock()
        started = clock.now
        row = _session_row(SessionStatus.RUNNING, last_started_at=started)
        service, _ = self._service(row, clock)
        observed = clock.now + timedelta(seconds=7)
        clock.advance(30)

        service.record_activity(SESSION_ID, started_at=started, bar_seen_at=observed)

        assert row.last_bar_at == observed
        assert row.last_heartbeat_at == clock.now

    def test_no_bar_leaves_last_bar_at_completely_alone(self):
        """A quiet interval must not overwrite the last real bar's timestamp."""
        clock = FakeClock()
        started = clock.now
        previous_bar = clock.now - timedelta(minutes=5)
        row = _session_row(SessionStatus.RUNNING, last_started_at=started, last_bar_at=previous_bar)
        service, _ = self._service(row, clock)
        clock.advance(30)

        service.record_activity(SESSION_ID, started_at=started)

        assert row.last_bar_at == previous_bar

    def test_it_returns_the_row_it_stamped(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        service, _ = self._service(row, clock)

        assert service.record_activity(SESSION_ID, started_at=clock.now) is row

    def test_an_unknown_session_raises_record_not_found(self):
        clock = FakeClock()
        service, _ = self._service(None, clock)

        with pytest.raises(RecordNotFoundError, match=str(SESSION_ID)):
            service.record_activity(SESSION_ID, started_at=clock.now)

    def test_the_read_does_not_take_the_row_lock(self):
        """A heartbeat every 30s must never block ``live status``.

        Mirrors ``test_resolve_reads_without_the_lock``: ``transition`` needs
        ``FOR UPDATE`` because it decides across a read-then-write window; a
        heartbeat writes one column and has no such window.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        service, repository = self._service(row, clock)

        service.record_activity(SESSION_ID, started_at=clock.now)

        assert repository.find_by_session_id.call_args.kwargs.get("for_update") in (None, False)

    @pytest.mark.parametrize(
        "status", [SessionStatus.CREATED, SessionStatus.STOPPED, SessionStatus.SEALED]
    )
    def test_a_session_that_is_not_running_refuses_the_heartbeat(self, status):
        """A heartbeat for a stopped session resurrects the liveness signal the
        reclaim depends on, making the row permanently un-reclaimable.
        """
        clock = FakeClock()
        row = _session_row(status, last_started_at=clock.now)
        service, _ = self._service(row, clock)

        with pytest.raises(InvalidSessionTransition, match="not running"):
            service.record_activity(SESSION_ID, started_at=clock.now)

        assert row.last_heartbeat_at is None

    def test_a_row_whose_last_started_at_moved_forward_refuses(self):
        """The mid-run reclaim guard: another process now owns this session."""
        clock = FakeClock()
        mine = clock.now
        theirs = clock.now + timedelta(seconds=120)
        row = _session_row(SessionStatus.RUNNING, last_started_at=theirs)
        service, _ = self._service(row, clock)

        with pytest.raises(InvalidSessionTransition, match="reclaimed"):
            service.record_activity(SESSION_ID, started_at=mine)

        assert row.last_heartbeat_at is None

    def test_a_row_whose_last_started_at_matches_is_accepted(self):
        """Equal, not merely older — this is the ordinary happy path."""
        clock = FakeClock()
        started = clock.now
        row = _session_row(SessionStatus.RUNNING, last_started_at=started)
        service, _ = self._service(row, clock)

        service.record_activity(SESSION_ID, started_at=started)

        assert row.last_heartbeat_at == started

    def test_a_null_last_started_at_is_accepted_rather_than_treated_as_a_reclaim(self):
        """``transition(to=RUNNING)`` always stamps it, so ``None`` means a row
        written by something older — refusing would strand it, and there is no
        evidence of a competitor.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=None)
        service, _ = self._service(row, clock)

        service.record_activity(SESSION_ID, started_at=clock.now)

        assert row.last_heartbeat_at == clock.now

    def test_the_reclaim_refusal_is_logged_with_both_instants(self):
        clock = FakeClock()
        theirs = clock.now + timedelta(seconds=120)
        row = _session_row(SessionStatus.RUNNING, last_started_at=theirs)
        service, _ = self._service(row, clock)

        with capture_logs() as logs:
            with pytest.raises(InvalidSessionTransition):
                service.record_activity(SESSION_ID, started_at=clock.now)

        events = [e for e in logs if e["event"] == "session.activity_refused"]
        assert len(events) == 1
        assert events[0]["log_level"] == "error"
        assert events[0]["session_id"] == str(SESSION_ID)


class TestTheHeartbeatIntervalConstantIsShareable:
    """*Judgment call #2*: the runner cannot import a SQLAlchemy-importing module.

    ``DEFAULT_HEARTBEAT_INTERVAL_SECONDS`` is declared in the framework-free
    ``src/models/session.py`` and re-exported here, so Story 2.3's callers and
    Story 2.5's runner read one number. Both are asserted, so removing either
    half fails.
    """

    def test_the_two_import_paths_yield_the_same_object(self):
        from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS as from_models
        from src.services.session_service import DEFAULT_HEARTBEAT_INTERVAL_SECONDS as from_service

        assert from_models == 30.0
        assert from_service is from_models

    def test_the_framework_free_module_really_is_importable_without_sqlalchemy(self):
        code = (
            "import sys, src.models.session;"
            "print(','.join(sorted(m for m in ('sqlalchemy', 'nautilus_trader') "
            "if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(service_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""


class TestTransitionOwnershipGuard:
    """Review fix (2026-08-21): the stop path arms the same reclaim guard the
    heartbeat uses. Without it, a dispossessed incumbent exiting inside the
    one-interval detection window would transition the **successor's** running
    row to ``stopped`` — and the successor would then kill itself at its next
    heartbeat.
    """

    def _service(self, row, clock: FakeClock) -> SessionService:
        return SessionService(_repository(row), time_source=clock)

    def test_a_stop_with_a_stale_started_at_refuses_as_reclaimed(self):
        clock = FakeClock()
        mine = clock.now
        theirs = clock.now + timedelta(seconds=120)
        row = _session_row(SessionStatus.RUNNING, last_started_at=theirs)
        service = self._service(row, clock)

        with pytest.raises(InvalidSessionTransition, match="reclaimed"):
            service.transition(SESSION_ID, to=SessionStatus.STOPPED, started_at=mine)

        assert row.status is SessionStatus.RUNNING, "the successor's row must not move"
        assert row.last_stopped_at is None

    def test_a_matching_started_at_is_accepted(self):
        """Equal, not merely older — the ordinary single-process teardown."""
        clock = FakeClock()
        started = clock.now
        row = _session_row(SessionStatus.RUNNING, last_started_at=started)
        service = self._service(row, clock)

        service.transition(SESSION_ID, to=SessionStatus.STOPPED, started_at=started)

        assert row.status is SessionStatus.STOPPED

    def test_a_null_row_started_at_is_accepted(self):
        """A row written by something older carries no evidence of a competitor."""
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=None)
        service = self._service(row, clock)

        service.transition(SESSION_ID, to=SessionStatus.STOPPED, started_at=clock.now)

        assert row.status is SessionStatus.STOPPED

    def test_omitting_started_at_preserves_story_23_behaviour(self):
        """The claim path and operator tooling pass no ``started_at`` and are
        allowed to move a row whose ``last_started_at`` is newer — a claim is
        exactly the operation that takes a stale session.
        """
        clock = FakeClock()
        row = _session_row(
            SessionStatus.RUNNING, last_started_at=clock.now + timedelta(seconds=120)
        )
        service = self._service(row, clock)

        service.transition(SESSION_ID, to=SessionStatus.STOPPED)

        assert row.status is SessionStatus.STOPPED

    def test_the_ownership_refusal_outranks_the_edge_refusal(self):
        """A caller that lost the session must hear "reclaimed", not "cannot
        move from stopped to stopped" — the remedies differ (walk away versus
        investigate).
        """
        clock = FakeClock()
        mine = clock.now
        row = _session_row(
            SessionStatus.STOPPED, last_started_at=clock.now + timedelta(seconds=120)
        )
        service = self._service(row, clock)

        with pytest.raises(InvalidSessionTransition, match="reclaimed"):
            service.transition(SESSION_ID, to=SessionStatus.STOPPED, started_at=mine)

    def test_the_refusal_is_logged_with_both_instants(self):
        clock = FakeClock()
        theirs = clock.now + timedelta(seconds=120)
        row = _session_row(SessionStatus.RUNNING, last_started_at=theirs)
        service = self._service(row, clock)

        with capture_logs() as logs:
            with pytest.raises(InvalidSessionTransition):
                service.transition(SESSION_ID, to=SessionStatus.STOPPED, started_at=clock.now)

        events = [e for e in logs if e["event"] == "session.activity_refused"]
        assert len(events) == 1
        assert events[0]["row_started_at"] == theirs.isoformat()


class TestRuntimeFlagsAreClearedOnEveryRunningEdge:
    """Story 2.7, AC #4 — a failure from a previous process run is never
    reported against the current one.

    Without this, ``live status`` shows a strategy that failed three process
    runs ago against a session that is currently healthy — a stale fact
    presented as a live one, which is worse than no fact at all. Mutation #11.
    """

    @staticmethod
    def _service(row, clock):
        return SessionService(_repository(row), time_source=clock)

    @pytest.mark.parametrize("current", [SessionStatus.CREATED, SessionStatus.STOPPED])
    def test_a_transition_to_running_clears_runtime_flags(self, current):
        clock = FakeClock()
        row = _session_row(current, runtime_flags={"v": 1, "failed_strategies": [{"a": 1}]})

        self._service(row, clock).transition(SESSION_ID, to=SessionStatus.RUNNING)

        assert row.runtime_flags is None

    def test_a_reclaim_of_a_stale_running_session_also_clears_them(self):
        """The reclaim path reaches ``running`` too, and a successor must not
        inherit its predecessor's failures.
        """
        clock = FakeClock()
        stale = clock.now - timedelta(seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS + 1)
        row = _session_row(
            SessionStatus.RUNNING,
            last_heartbeat_at=stale,
            runtime_flags={"v": 1, "failed_strategies": [{"a": 1}]},
        )

        self._service(row, clock).transition(SESSION_ID, to=SessionStatus.RUNNING)

        assert row.runtime_flags is None

    @pytest.mark.parametrize(
        "current, target",
        [
            (SessionStatus.RUNNING, SessionStatus.STOPPED),
            (SessionStatus.STOPPED, SessionStatus.SEALED),
        ],
    )
    def test_no_other_edge_touches_runtime_flags(self, current, target):
        """Only ``-> running`` clears. A stop must leave the run's failures
        readable, or an operator investigating why a session stopped trading
        loses the evidence at exactly the moment they need it.
        """
        clock = FakeClock()
        flags = {"v": 1, "failed_strategies": [{"a": 1}]}
        row = _session_row(current, runtime_flags=flags, last_heartbeat_at=clock.now)

        self._service(row, clock).transition(SESSION_ID, to=target)

        assert row.runtime_flags == flags

    def test_clearing_is_harmless_when_there_was_nothing_to_clear(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.CREATED)

        self._service(row, clock).transition(SESSION_ID, to=SessionStatus.RUNNING)

        assert row.runtime_flags is None


class TestRecordStrategyFailure:
    """Story 2.7, AC #4 — the third write a running session may make.

    It never assigns ``status`` (AR37's AST guard matches only
    ``t.attr == "status"``, so this is invisible to it) and it goes through the
    same ownership guard the heartbeat uses, because a dispossessed process
    writing a failure into its successor's row is the same defect as a
    dispossessed process stamping its heartbeat.
    """

    @staticmethod
    def _service(row, clock):
        return SessionService(_repository(row), time_source=clock)

    @staticmethod
    def _failure(clock, **overrides):
        fields = {
            "strategy_id": "SMACrossover-000",
            "spec_strategy_id": "sma_crossover",
            "error_type": "DivisionByZero",
            "handler": "handle_bar",
            "at": clock.now,
            "detail": "[<class 'decimal.DivisionByZero'>]",
        }
        fields.update(overrides)
        return fields

    def test_the_first_failure_creates_the_versioned_document(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)

        self._service(row, clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock)
        )

        assert row.runtime_flags["v"] == 1
        assert row.runtime_flags["all_failed"] is False
        (entry,) = row.runtime_flags["failed_strategies"]
        assert entry["strategy_id"] == "SMACrossover-000"
        assert entry["spec_strategy_id"] == "sma_crossover"
        assert entry["error_type"] == "DivisionByZero"
        assert entry["handler"] == "handle_bar"
        assert entry["at"] == clock.now.isoformat()
        assert entry["detail"] == "[<class 'decimal.DivisionByZero'>]"

    def test_no_traceback_reaches_the_column(self):
        """The traceback belongs in the structlog sink. A column that carried
        one would grow without bound and would be the least redacted place in
        the system.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)

        self._service(row, clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock)
        )

        assert "traceback" not in row.runtime_flags["failed_strategies"][0]

    def test_a_second_failure_appends_rather_than_replacing(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        service = self._service(row, clock)

        service.record_strategy_failure(SESSION_ID, started_at=clock.now, **self._failure(clock))
        service.record_strategy_failure(
            SESSION_ID,
            started_at=clock.now,
            **self._failure(clock, spec_strategy_id="momentum", strategy_id="SMAMomentum-001"),
        )

        assert [e["spec_strategy_id"] for e in row.runtime_flags["failed_strategies"]] == [
            "sma_crossover",
            "momentum",
        ]

    def test_the_document_is_reassigned_not_mutated_in_place(self):
        """⚠️ SQLAlchemy does not track in-place mutation of a plain ``JSONB``
        column: ``row.runtime_flags["failed_strategies"].append(...)`` silently
        never persists. This asserts the object identity actually changes, which
        is the only thing that makes the write reach Postgres.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        service = self._service(row, clock)
        service.record_strategy_failure(SESSION_ID, started_at=clock.now, **self._failure(clock))
        first_document = row.runtime_flags
        first_list = row.runtime_flags["failed_strategies"]

        service.record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock, spec_strategy_id="momentum")
        )

        assert row.runtime_flags is not first_document
        assert row.runtime_flags["failed_strategies"] is not first_list

    def test_all_failed_is_carried_when_the_caller_says_so(self):
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)

        self._service(row, clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, all_failed=True, **self._failure(clock)
        )

        assert row.runtime_flags["all_failed"] is True

    def test_all_failed_is_never_downgraded_by_a_later_write(self):
        """Once every strategy has failed, a subsequent write must not report
        the session as partially alive again.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        service = self._service(row, clock)
        service.record_strategy_failure(
            SESSION_ID, started_at=clock.now, all_failed=True, **self._failure(clock)
        )

        service.record_strategy_failure(
            SESSION_ID,
            started_at=clock.now,
            all_failed=False,
            **self._failure(clock, spec_strategy_id="momentum"),
        )

        assert row.runtime_flags["all_failed"] is True

    def test_a_reclaimed_row_refuses_the_write(self):
        clock = FakeClock()
        mine = clock.now
        row = _session_row(
            SessionStatus.RUNNING, last_started_at=clock.now + timedelta(seconds=120)
        )

        with pytest.raises(InvalidSessionTransition, match="reclaimed"):
            self._service(row, clock).record_strategy_failure(
                SESSION_ID, started_at=mine, **self._failure(clock)
            )

        assert row.runtime_flags is None

    @pytest.mark.parametrize(
        "status", [SessionStatus.CREATED, SessionStatus.STOPPED, SessionStatus.SEALED]
    )
    def test_a_row_that_is_not_running_refuses_the_write(self, status):
        clock = FakeClock()
        row = _session_row(status, last_started_at=clock.now)

        with pytest.raises(InvalidSessionTransition):
            self._service(row, clock).record_strategy_failure(
                SESSION_ID, started_at=clock.now, **self._failure(clock)
            )

        assert row.runtime_flags is None

    def test_an_unknown_session_raises_record_not_found(self):
        clock = FakeClock()
        service = SessionService(_repository(None), time_source=clock)

        with pytest.raises(RecordNotFoundError):
            service.record_strategy_failure(
                SESSION_ID, started_at=clock.now, **self._failure(clock)
            )

    def test_the_write_never_assigns_status(self):
        """AR37 is per-module, and this module is the one assigner — but this
        particular write has no business touching ``status`` at all, and a
        session whose strategy failed is still ``running`` (AC #1's letter).
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)

        self._service(row, clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock)
        )

        assert row.status is SessionStatus.RUNNING

    def test_keys_this_write_does_not_own_survive_the_rebuild(self):
        """Story 2.8's ``connection_lost_at`` is already planned as an addition
        to this document. A rebuild that kept only the keys this function knows
        about would silently erase it on every strategy failure — "append,
        never replace" must hold for the whole document, not just the two keys
        named here (review fix, 2026-08-23).
        """
        clock = FakeClock()
        row = _session_row(
            SessionStatus.RUNNING,
            last_started_at=clock.now,
            runtime_flags={"v": 1, "connection_lost_at": "2026-08-23T14:00:00+00:00"},
        )

        self._service(row, clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock)
        )

        assert row.runtime_flags["connection_lost_at"] == "2026-08-23T14:00:00+00:00"
        assert len(row.runtime_flags["failed_strategies"]) == 1

    def test_the_read_is_locked_because_the_write_rebuilds_the_document(self):
        """Unlike ``record_activity``, this write takes ``FOR UPDATE`` (review
        fix, 2026-08-23 — reversing the earlier pinned no-lock decision): it is
        a read-modify-write of the whole ``runtime_flags`` document, so an
        unlocked read lets a dispossessed incumbent rebuild the doc from a
        pre-reclaim snapshot — stamping stale failures into the successor's
        entire run and erasing entries the successor already recorded. The lock
        serialises the reclaim guard with the ``-> running`` transition that
        clears the column. The heartbeat's no-lock rationale does not carry
        over: that write is two scalar stamps every 30 seconds; this one is
        rare, because the guard latches per strategy.
        """
        clock = FakeClock()
        row = _session_row(SessionStatus.RUNNING, last_started_at=clock.now)
        repository = _repository(row)

        SessionService(repository, time_source=clock).record_strategy_failure(
            SESSION_ID, started_at=clock.now, **self._failure(clock)
        )

        (_args, kwargs) = repository.find_by_session_id.call_args
        assert kwargs.get("for_update", False) is True
