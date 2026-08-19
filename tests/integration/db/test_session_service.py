"""Integration tests for ``SessionService`` against real Postgres (Story 2.3).

CI ``--ignore``s this whole directory (``.github/workflows/ci.yml:168`` and
``:238``), so nothing here gates a PR — this file is behavioural *evidence*,
not a gate. Every falsifiable property already lives in the unit tier
(``tests/unit/services/test_session_service.py``); what only a real database
can prove is covered here: the lowercase enum label actually persisted, a
rollback genuinely leaving the row untouched, and the two-connection reclaim
race that no mock can reproduce.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import get_settings
from src.db.exceptions import InvalidSessionTransition
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


def _worker_id(request) -> str:
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


def _status_label(session, session_id) -> str:
    """Read the raw enum label back with plain SQL, bypassing the ORM's mapping."""
    return session.execute(
        text("SELECT status::text FROM trading_sessions WHERE session_id = :sid"),
        {"sid": str(session_id)},
    ).scalar()


@pytest.mark.integration
class TestSessionServiceTransitionsAgainstRealPostgres:
    """Not a CI gate — see the module docstring. Evidence that the unit-tier
    mocks did not misrepresent how Postgres actually behaves.
    """

    def test_all_four_legal_edges_round_trip_and_persist_lowercase_labels(self, sync_db_session):
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="lifecycle-session", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        service.transition(created.session_id, to=SessionStatus.RUNNING)  # created -> running
        sync_db_session.commit()
        assert _status_label(sync_db_session, created.session_id) == "running"

        service.transition(created.session_id, to=SessionStatus.STOPPED)  # running -> stopped
        sync_db_session.commit()
        assert _status_label(sync_db_session, created.session_id) == "stopped"

        service.transition(created.session_id, to=SessionStatus.RUNNING)  # stopped -> running
        sync_db_session.commit()
        assert _status_label(sync_db_session, created.session_id) == "running"

        service.transition(created.session_id, to=SessionStatus.STOPPED)
        sync_db_session.commit()

        service.transition(created.session_id, to=SessionStatus.SEALED)  # stopped -> sealed
        sync_db_session.commit()
        assert _status_label(sync_db_session, created.session_id) == "sealed"

    def test_an_illegal_transition_leaves_the_stored_status_untouched_after_rollback(
        self, sync_db_session
    ):
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="illegal-session", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        with pytest.raises(InvalidSessionTransition):
            service.transition(created.session_id, to=SessionStatus.SEALED)
        sync_db_session.rollback()

        assert _status_label(sync_db_session, created.session_id) == "created"

    def test_exactly_one_of_two_concurrent_reclaims_wins(self, sync_db_session, request):
        """Story 2.3 AC #6, demonstrated live: a stale-heartbeat ``running`` row
        reclaimed by two processes at once must produce exactly one winner, not
        two processes on one broker account (NFR6). The winner holds the
        ``FOR UPDATE`` lock open (via a deliberate delay before its commit) so
        the loser genuinely blocks on the database, then re-reads a heartbeat
        the winner just stamped — the mechanism Dev Notes' *Pre-verified
        finding 4* demonstrated before this story was written.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="race-session", spec=_spec().to_stored())
        sync_db_session.commit()

        stale_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=200)
        created.status = SessionStatus.RUNNING
        created.last_heartbeat_at = stale_heartbeat
        sync_db_session.commit()
        session_id = created.session_id

        # A second, independent connection into the same scratch schema —
        # pg8000 (pure Python), matching the fixture's own choice, since
        # psycopg2 segfaults under --forked once Nautilus is loaded.
        settings = get_settings()
        pg8000_url = settings.database_url.replace("postgresql://", "postgresql+pg8000://")
        schema_name = f"test_{_worker_id(request)}".replace("-", "_")
        engine_two = create_engine(pg8000_url, echo=False)
        session_two = sessionmaker(bind=engine_two, expire_on_commit=False)()
        session_two.execute(text(f"SET search_path TO {schema_name}"))

        service_one = SessionService(SyncTradingSessionRepository(sync_db_session))
        service_two = SessionService(SyncTradingSessionRepository(session_two))
        outcomes: dict[str, str] = {}

        def _winner() -> None:
            service_one.transition(session_id, to=SessionStatus.RUNNING)
            # Hold the row lock open long enough for the loser to genuinely
            # block on FOR UPDATE, rather than racing it on wall-clock luck.
            time.sleep(0.4)
            sync_db_session.commit()
            outcomes["winner"] = "reclaimed"

        def _loser() -> None:
            time.sleep(0.1)  # let the winner acquire the lock first
            try:
                service_two.transition(session_id, to=SessionStatus.RUNNING)
                outcomes["loser"] = "reclaimed"
            except InvalidSessionTransition:
                outcomes["loser"] = "refused"
            finally:
                session_two.rollback()

        winner_thread = threading.Thread(target=_winner)
        loser_thread = threading.Thread(target=_loser)
        winner_thread.start()
        loser_thread.start()
        winner_thread.join(timeout=10)
        loser_thread.join(timeout=10)

        session_two.close()
        engine_two.dispose()

        assert outcomes["winner"] == "reclaimed"
        assert outcomes["loser"] == "refused"
