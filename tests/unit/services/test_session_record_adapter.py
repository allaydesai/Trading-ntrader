"""Unit tests for ``SqlSessionRecord``, the record port's SQLAlchemy adapter (Story 2.5).

Unit tier and no database: the ``get_sync_session`` factory is injected, so what
is under test is the *transaction discipline* and the routing, not Postgres.
Both are load-bearing:

- **one short-lived transaction per call.** Story 2.3's forward constraint,
  verbatim: *"The runner must let the ``get_sync_session`` block close right
  after the transition… Keeping it open for the life of the session would hold
  the row lock for hours and block every ``live status``."*
- **``mark_stopped`` routes through ``SessionService.transition``**, never
  assigning ``status`` itself (AR37).

The basename is deliberately not ``test_session_service.py``:
``tests/unit/services/`` has no ``__init__.py`` and already collides with
``tests/integration/db/test_session_service.py``.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from src.core.live_session_record import SessionRecordPort
from src.db.models.trading_session import TradingSession
from src.models.session import SessionStatus
from src.services.session_record import SqlSessionRecord

pytestmark = pytest.mark.unit

SESSION_ID = UUID("22222222-2222-2222-2222-222222222222")
STARTED_AT = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


class _RecordingFactory:
    """A ``get_sync_session``-shaped factory that counts enters and exits."""

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self.sessions: list[MagicMock] = []

    @contextmanager
    def __call__(self):
        self.entered += 1
        session = MagicMock(name=f"sync-session-{self.entered}")
        self.sessions.append(session)
        try:
            yield session
        finally:
            self.exited += 1

    @property
    def balanced(self) -> bool:
        return self.entered == self.exited


def _row(status: SessionStatus = SessionStatus.RUNNING, **overrides) -> TradingSession:
    fields = {
        "session_id": SESSION_ID,
        "name": "alpha-session",
        "status": status,
        "spec": {},
        "last_started_at": STARTED_AT,
    }
    fields.update(overrides)
    return TradingSession(**fields)


@pytest.fixture
def patched(monkeypatch):
    """Replace the repository and service the adapter builds per call."""
    from src.services import session_record as adapter_module

    service = MagicMock()
    repository = MagicMock()
    monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
    monkeypatch.setattr(adapter_module, "SessionService", MagicMock(return_value=service))
    return adapter_module, service


class TestItSatisfiesThePort:
    def test_the_adapter_is_a_session_record_port(self):
        assert isinstance(SqlSessionRecord(SESSION_ID, started_at=STARTED_AT), SessionRecordPort)


class TestOneTransactionPerCall:
    """⚠️ Never a long-lived one — see the module docstring."""

    def test_record_activity_opens_and_closes_exactly_one_session(self, patched):
        factory = _RecordingFactory()
        record = SqlSessionRecord(SESSION_ID, started_at=STARTED_AT, session_factory=factory)

        record.record_activity(at=STARTED_AT + timedelta(seconds=30))

        assert (factory.entered, factory.exited) == (1, 1)

    def test_construction_alone_opens_no_session(self, patched):
        factory = _RecordingFactory()

        SqlSessionRecord(SESSION_ID, started_at=STARTED_AT, session_factory=factory)

        assert factory.entered == 0

    def test_three_calls_open_three_separate_sessions(self, patched):
        factory = _RecordingFactory()
        record = SqlSessionRecord(SESSION_ID, started_at=STARTED_AT, session_factory=factory)

        for offset in (30, 60, 90):
            record.record_activity(at=STARTED_AT + timedelta(seconds=offset))

        assert (factory.entered, factory.exited) == (3, 3)
        assert len({id(s) for s in factory.sessions}) == 3

    def test_mark_stopped_also_uses_its_own_short_lived_session(self, patched):
        factory = _RecordingFactory()
        record = SqlSessionRecord(SESSION_ID, started_at=STARTED_AT, session_factory=factory)

        record.mark_stopped()

        assert (factory.entered, factory.exited) == (1, 1)

    def test_a_raising_call_still_closes_its_session(self, patched):
        adapter_module, service = patched
        service.record_activity.side_effect = RuntimeError("db went away")
        factory = _RecordingFactory()
        record = SqlSessionRecord(SESSION_ID, started_at=STARTED_AT, session_factory=factory)

        with pytest.raises(RuntimeError):
            record.record_activity(at=STARTED_AT)

        assert factory.balanced


class TestTheCallersInstantIsWhatLands:
    """The runner's clock must reach the column, not ``datetime.now()``."""

    def test_the_at_argument_is_forwarded_verbatim(self, patched):
        _, service = patched
        stamped = STARTED_AT + timedelta(seconds=17, microseconds=42)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.record_activity(at=stamped)

        assert service.record_activity.call_args.kwargs["at"] == stamped

    def test_the_service_is_built_with_a_time_source_returning_that_same_instant(self, patched):
        adapter_module, _ = patched
        stamped = STARTED_AT + timedelta(seconds=17)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.record_activity(at=stamped)

        time_source = adapter_module.SessionService.call_args.kwargs["time_source"]
        assert time_source() == stamped

    def test_the_bound_session_id_and_started_at_are_what_reach_the_service(self, patched):
        _, service = patched
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.record_activity(at=STARTED_AT, bar_seen_at=STARTED_AT)

        call = service.record_activity.call_args
        assert call.args[0] == SESSION_ID
        assert call.kwargs["started_at"] == STARTED_AT
        assert call.kwargs["bar_seen_at"] == STARTED_AT

    def test_no_bar_forwards_none_rather_than_omitting_the_argument(self, patched):
        _, service = patched
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.record_activity(at=STARTED_AT)

        assert service.record_activity.call_args.kwargs["bar_seen_at"] is None


class TestMarkStoppedRoutesThroughTheStateMachine:
    """AR37: nothing but ``SessionService.transition`` may assign ``status``."""

    def test_it_calls_transition_with_stopped_and_its_own_started_at(self, patched):
        """The bound ``started_at`` arms the stop-path ownership guard
        (review fix, 2026-08-21) — without it a dispossessed incumbent's
        teardown stops the successor's session.
        """
        _, service = patched
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.mark_stopped()

        service.transition.assert_called_once_with(
            SESSION_ID, to=SessionStatus.STOPPED, started_at=STARTED_AT
        )

    def test_the_adapter_module_never_assigns_a_status_attribute(self):
        """Structural, not a grep: a docstring mentioning ``status`` is prose."""
        import ast
        from pathlib import Path

        from src.services import session_record as adapter_module

        tree = ast.parse(Path(adapter_module.__file__).read_text(encoding="utf-8"))
        targets = [
            target
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
        ]
        assert not [t for t in targets if isinstance(t, ast.Attribute) and t.attr == "status"]


class TestItReadsAndWritesARealRowShape:
    """One test that does not stub the service, so the wiring is proven end to end
    against the real ``SessionService`` with only the repository faked.
    """

    def test_a_running_row_is_stamped_with_the_callers_instant(self, monkeypatch):
        from src.services import session_record as adapter_module

        row = _row()
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        stamped = STARTED_AT + timedelta(seconds=30)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.record_activity(at=stamped)

        assert row.last_heartbeat_at == stamped
        assert row.last_bar_at is None

    def test_a_row_reclaimed_by_another_process_refuses(self, monkeypatch):
        """Translated to the **port's** exception, which the runner can catch
        without importing ``src.db`` — AR38 forbids it that import.
        """
        from src.core.live_session_record import SessionReclaimedError
        from src.services import session_record as adapter_module

        row = _row(last_started_at=STARTED_AT + timedelta(minutes=5))
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        with pytest.raises(SessionReclaimedError, match="reclaimed"):
            record.record_activity(at=STARTED_AT + timedelta(seconds=30))

    def test_a_row_that_is_no_longer_running_also_refuses_as_ownership_lost(self, monkeypatch):
        """The deliberate widening — see ``record_activity``'s docstring."""
        from src.core.live_session_record import SessionReclaimedError
        from src.services import session_record as adapter_module

        row = _row(SessionStatus.STOPPED)
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        with pytest.raises(SessionReclaimedError, match="not running"):
            record.record_activity(at=STARTED_AT + timedelta(seconds=30))

    def test_a_missing_row_is_not_translated(self, monkeypatch):
        """``RecordNotFoundError`` is not an ownership question."""
        from src.db.exceptions import RecordNotFoundError
        from src.services import session_record as adapter_module

        repository = MagicMock()
        repository.find_by_session_id.return_value = None
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        with pytest.raises(RecordNotFoundError):
            record.record_activity(at=STARTED_AT)

    def test_mark_stopped_moves_a_running_row_to_stopped(self, monkeypatch):
        from src.services import session_record as adapter_module

        row = _row()
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            uuid4(), started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        record.mark_stopped()

        assert row.status is SessionStatus.STOPPED

    def test_mark_stopped_on_a_reclaimed_row_refuses_and_leaves_it_running(self, monkeypatch):
        """The stop-path ownership guard, end to end against the real service
        (review fix, 2026-08-21): a row whose ``last_started_at`` moved past
        this process's own refuses as reclaimed and is not moved.
        """
        from src.core.live_session_record import SessionReclaimedError
        from src.services import session_record as adapter_module

        row = _row(last_started_at=STARTED_AT + timedelta(minutes=5))
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        with pytest.raises(SessionReclaimedError, match="reclaimed"):
            record.mark_stopped()

        assert row.status is SessionStatus.RUNNING

    def test_mark_stopped_translates_an_edge_refusal_the_same_way(self, monkeypatch):
        """A row already out of ``running`` out of band means the same thing —
        the row is no longer this process's to mark (the deliberate widening,
        applied to the stop path at the 2026-08-21 review).
        """
        from src.core.live_session_record import SessionReclaimedError
        from src.services import session_record as adapter_module

        row = _row(SessionStatus.STOPPED)
        repository = MagicMock()
        repository.find_by_session_id.return_value = row
        monkeypatch.setattr(adapter_module, "SyncTradingSessionRepository", lambda s: repository)
        record = SqlSessionRecord(
            SESSION_ID, started_at=STARTED_AT, session_factory=_RecordingFactory()
        )

        with pytest.raises(SessionReclaimedError):
            record.mark_stopped()
