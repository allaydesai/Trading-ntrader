"""Integration proof: identity survives repeated stop/start cycles (Story 2.6,
Task 8, AC #5).

CI ``--ignore``s ``tests/integration/db`` (this directory has no
``__init__.py``), so nothing here gates a PR — this file is behavioural
*evidence*, not a gate, exactly like ``test_session_service.py`` beside it.

Drives ``SessionService.transition`` directly rather than the full
``LiveSessionRunner`` — the runner's ``-> running``/``-> stopped`` calls *are*
calls to this same service (through the ``SqlSessionRecord`` adapter), and the
identity property under test (AC #5) is a database property, not a runner one.
The component tier (``test_session_runner_stop.py::TestIdentityAcrossStopStartCycles``)
already proves the runner calls the record port the right number of times
against a double; this file proves what a real Postgres row does in response.
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from src.config import get_settings
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services.session_service import SessionService


def _spec() -> SessionSpec:
    return SessionSpec(
        strategies=(
            StrategySpec.from_overrides(
                strategy_id="sma_crossover",
                overrides={"fast_period": 12},
                settings=get_settings(),
                bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
            ),
        )
    )


def _row_count(session, name: str) -> int:
    return session.execute(
        text("SELECT count(*) FROM trading_sessions WHERE name = :name"),
        {"name": name},
    ).scalar()


@pytest.mark.integration
class TestIdentitySurvivesThreeStopStartCycles:
    """Not a CI gate — see the module docstring."""

    def test_three_cycles_leave_one_row_with_stable_identity(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="stop-start-cycles", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        session_id = created.session_id
        original_name = created.name
        original_spec = created.spec

        last_started_ats: list[datetime] = []
        last_stopped_ats: list[datetime] = []

        for _ in range(3):
            started = service.transition(session_id, to=SessionStatus.RUNNING)
            sync_db_session.commit()
            last_started_ats.append(started.last_started_at)

            stopped = service.transition(session_id, to=SessionStatus.STOPPED)
            sync_db_session.commit()
            last_stopped_ats.append(stopped.last_stopped_at)

        assert _row_count(sync_db_session, "stop-start-cycles") == 1

        final = repository.find_by_session_id(session_id)
        assert final.session_id == session_id
        assert final.name == original_name
        assert final.spec == original_spec  # byte-identical throughout
        assert final.status is SessionStatus.STOPPED
        assert final.sealed_at is None
        assert final.sealed_run_id is None

        # `last_started_at`/`last_stopped_at` advance on every cycle.
        assert len(set(last_started_ats)) == 3
        assert len(set(last_stopped_ats)) == 3
        assert last_started_ats == sorted(last_started_ats)
        assert last_stopped_ats == sorted(last_stopped_ats)

    def test_the_stopped_session_is_still_startable_afterwards(self, sync_db_session):
        """``stopped -> running`` succeeds — stop never seals (AC #3)."""
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="stop-then-restart", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        service.transition(created.session_id, to=SessionStatus.RUNNING)
        sync_db_session.commit()
        service.transition(created.session_id, to=SessionStatus.STOPPED)
        sync_db_session.commit()

        restarted = service.transition(created.session_id, to=SessionStatus.RUNNING)
        sync_db_session.commit()

        assert restarted.status is SessionStatus.RUNNING
        assert restarted.session_id == created.session_id
