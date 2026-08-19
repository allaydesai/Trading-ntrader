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


def _second_connection(request):
    """A second, independent connection into the same scratch schema.

    pg8000 (pure Python), matching the fixture's own choice, since psycopg2
    segfaults under ``--forked`` once Nautilus is loaded. Returns the session
    and its engine; the caller closes and disposes both.
    """
    settings = get_settings()
    pg8000_url = settings.database_url.replace("postgresql://", "postgresql+pg8000://")
    schema_name = f"test_{_worker_id(request)}".replace("-", "_")
    engine = create_engine(pg8000_url, echo=False)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.execute(text(f"SET search_path TO {schema_name}"))
    return session, engine


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

    def test_an_illegal_transition_never_reaches_the_database_at_all(self, sync_db_session):
        """AC #3: validation precedes mutation, so nothing is written to roll back.

        Deliberately asserts the stored status *before* any rollback. An earlier
        form called ``rollback()`` first and called that evidence of rollback
        semantics — but ``created -> sealed`` raises before the assignment, so
        the rollback was a no-op and deleting it changed nothing. The genuine
        rollback property is the test below.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="illegal-session", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        with pytest.raises(InvalidSessionTransition):
            service.transition(created.session_id, to=SessionStatus.SEALED)

        assert _status_label(sync_db_session, created.session_id) == "created"
        assert created.status is SessionStatus.CREATED  # not even mutated in memory
        sync_db_session.rollback()
        assert _status_label(sync_db_session, created.session_id) == "created"

    def test_a_legal_transition_that_is_never_committed_leaves_the_row_untouched(
        self, sync_db_session
    ):
        """The caller owns the transaction, so a successful return is not durable.

        ``transition()`` mutates the in-memory row and returns; if the caller
        rolls back — which ``get_sync_session()`` does on *any* exception — the
        state change never lands, and any other process still sees the old row.
        This is the rollback property the illegal-edge test above cannot show.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="uncommitted-session", spec=_spec().to_stored())
        sync_db_session.commit()
        service = SessionService(repository)

        result = service.transition(created.session_id, to=SessionStatus.RUNNING)
        assert result.status is SessionStatus.RUNNING  # mutated in memory

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

        # Reach `running` with a stale heartbeat through the guarded path, not a
        # direct `created.status =` — AC #1 says "anywhere in the codebase", and
        # the src/-scoped gates would not have caught it here.
        service_setup = SessionService(repository)
        service_setup.transition(created.session_id, to=SessionStatus.RUNNING)
        created.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(seconds=200)
        sync_db_session.commit()
        session_id = created.session_id

        session_two, engine_two = _second_connection(request)
        service_one = SessionService(SyncTradingSessionRepository(sync_db_session))
        service_two = SessionService(SyncTradingSessionRepository(session_two))
        outcomes: dict[str, str] = {}
        errors: list[BaseException] = []
        winner_holds_the_lock = threading.Event()

        def _winner() -> None:
            try:
                service_one.transition(session_id, to=SessionStatus.RUNNING)
                winner_holds_the_lock.set()
                # Hold the row lock open long enough for the loser to genuinely
                # block on FOR UPDATE, rather than racing it on wall-clock luck.
                time.sleep(0.4)
                sync_db_session.commit()
                outcomes["winner"] = "reclaimed"
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                winner_holds_the_lock.set()

        def _loser() -> None:
            try:
                # An Event, not a sleep: under CI load the loser could otherwise
                # acquire the lock first, invert the roles, and fail with a bare
                # KeyError that reads as a harness bug rather than a real result.
                assert winner_holds_the_lock.wait(timeout=10), "winner never took the lock"
                service_two.transition(session_id, to=SessionStatus.RUNNING)
                outcomes["loser"] = "reclaimed"
            except InvalidSessionTransition:
                outcomes["loser"] = "refused"
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                session_two.rollback()

        winner_thread = threading.Thread(target=_winner)
        loser_thread = threading.Thread(target=_loser)
        winner_thread.start()
        loser_thread.start()
        for thread in (winner_thread, loser_thread):
            thread.join(timeout=20)
            # Never dispose a connection another thread may still be blocked on.
            assert not thread.is_alive(), f"{thread.name} is still running; refusing to tear down"

        session_two.close()
        engine_two.dispose()

        assert not errors, errors
        assert outcomes == {"winner": "reclaimed", "loser": "refused"}

    def test_a_reclaim_is_refused_even_when_the_caller_already_loaded_the_row(
        self, sync_db_session, request
    ):
        """Regression: the locked re-read must overwrite the identity map.

        ``SELECT ... FOR UPDATE`` takes the lock and fetches the current row —
        but without ``populate_existing`` SQLAlchemy returns the instance
        already in the session and *discards* the fetched column values. A
        caller that resolved the row first (the shape AR36 prescribes) then
        decided on pre-lock state.

        Without the fix this test fails two ways at once: the second caller
        reclaims a session someone else already owns, and — with `sealed` as the
        stored state — a terminal session moves back to `running`, breaking
        AC #3. The race test above cannot catch either, because its loser uses a
        fresh session whose identity map has never seen the row, which is the
        one arrangement in which the ORM does return fresh data.
        """
        repository = SyncTradingSessionRepository(sync_db_session)
        created = repository.create(name="identity-map-session", spec=_spec().to_stored())
        sync_db_session.commit()
        session_id = created.session_id

        session_two, engine_two = _second_connection(request)
        try:
            # The second caller resolves first and KEEPS the reference — the
            # identity map is weakly referenced, so a discarded row would be
            # collected and the staleness would hide.
            service_two = SessionService(SyncTradingSessionRepository(session_two))
            held_row = service_two.resolve("identity-map-session")
            assert held_row.status is SessionStatus.CREATED

            # Meanwhile the first caller starts the session and commits.
            SessionService(repository).transition(session_id, to=SessionStatus.RUNNING)
            sync_db_session.commit()

            # The second caller's locked read must now see `running` with a
            # fresh heartbeat, and refuse.
            with pytest.raises(InvalidSessionTransition, match="another process appears live"):
                service_two.transition(session_id, to=SessionStatus.RUNNING)
        finally:
            session_two.rollback()
            session_two.close()
            engine_two.dispose()

        assert _status_label(sync_db_session, session_id) == "running"
