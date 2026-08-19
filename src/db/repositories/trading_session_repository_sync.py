"""Synchronous repository for trading-session database operations (CLI use).

For async operations (future API endpoints), use trading_session_repository.py.
"""

from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.trading_session import TradingSession


class SyncTradingSessionRepository:
    """Synchronous repository for trading-session operations.

    Used by CLI commands. The spec is write-once: no method here updates a
    row's ``spec`` column, the same discipline ``BacktestRun.config_snapshot``
    already follows — there is no update/setter method of any kind (AC #7).

    Attributes:
        session: Synchronous SQLAlchemy session for database operations.
    """

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: Synchronous SQLAlchemy session.
        """
        self.session = session

    def create(
        self,
        *,
        name: str,
        spec: dict,
        linked_backtest_run_id: Optional[UUID] = None,
    ) -> TradingSession:
        """Create a new trading session with status CREATED.

        Args:
            name: Operator-chosen unique handle.
            spec: The already-serialised spec dict, produced by
                ``SessionSpec.to_stored()`` — never a raw ``SessionSpec``, to
                keep ``src/models/`` out of ``src/db/``.
            linked_backtest_run_id: The backtest this session compares
                against (FR15), or None.

        Returns:
            The created TradingSession with its id assigned.

        Raises:
            DuplicateRecordError: If a session with this name already exists.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            trading_session = TradingSession(
                name=name,
                spec=spec,
                linked_backtest_run_id=linked_backtest_run_id,
            )
            self.session.add(trading_session)
            self.session.flush()
            self.session.refresh(trading_session)
            return trading_session

        except IntegrityError as e:
            if "unique constraint" in str(e.orig).lower():
                raise DuplicateRecordError(f"Session name {name!r} already exists") from e
            raise

        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def find_by_session_id(
        self, session_id: UUID, *, for_update: bool = False
    ) -> Optional[TradingSession]:
        """Find a trading session by its business identifier.

        Args:
            session_id: The session's UUID business key.
            for_update: When True, lock the row with ``SELECT ... FOR UPDATE``
                and overwrite any copy already loaded in this session's
                identity map. ``SessionService.transition()``'s reclaim decision
                reads and writes across a window in which another process may
                attempt the same reclaim (Story 2.3 AC #6). The lock alone is
                not enough for exactly one of them to win: it must be paired
                with the reclaim's ``last_heartbeat_at`` stamp, so the loser
                re-reads a fresh heartbeat and takes the ordinary refusal.

                ``populate_existing`` is load-bearing, not decoration. Without
                it the ORM returns the instance already in the identity map and
                discards the freshly locked row's column values, so a caller
                that had already loaded this row — via ``resolve()``, the shape
                AR36 prescribes — would decide on **pre-lock** state. Verified:
                two processes both reclaimed one session, and a ``sealed``
                session was moved back to ``running``.

        Returns:
            The TradingSession, or None if not found.
        """
        stmt = select(TradingSession).where(TradingSession.session_id == session_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = self.session.execute(stmt)
        return result.scalar_one_or_none()

    def find_by_name(self, name: str) -> Optional[TradingSession]:
        """Find a trading session by its unique name.

        Args:
            name: The operator-chosen unique handle.

        Returns:
            The TradingSession, or None if not found.
        """
        stmt = select(TradingSession).where(TradingSession.name == name)
        result = self.session.execute(stmt)
        return result.scalar_one_or_none()

    def find_all(self) -> List[TradingSession]:
        """List every trading session, newest first.

        The ordering is deliberate, not incidental: Story 2.8's ``live list``
        renders whatever this returns, and an unordered scan lets Postgres
        reorder rows after any UPDATE or VACUUM — so the same command run twice
        could list differently. ``id`` breaks ties within a ``created_at``
        (``now()`` is transaction-scoped, so two sessions created in one
        transaction share a timestamp).

        Returns:
            All TradingSession rows, most recently created first.
        """
        stmt = select(TradingSession).order_by(
            TradingSession.created_at.desc(), TradingSession.id.desc()
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())
