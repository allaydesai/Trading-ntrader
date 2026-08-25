"""Async repository for trading-session database operations (future API use).

For sync operations (CLI use), use trading_session_repository_sync.py.
"""

from typing import List, Optional, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession


class TradingSessionRepository:
    """Async repository for trading-session operations.

    Used by future web API endpoints via FastAPI dependency injection. The
    spec is write-once: no method here updates a row's ``spec`` column, the
    same discipline ``BacktestRun.config_snapshot`` already follows — there is
    no update/setter method of any kind (AC #7).

    Attributes:
        session: Async SQLAlchemy session for database operations.
    """

    def __init__(self, session: AsyncSession):
        """Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session.
        """
        self.session = session

    async def create(
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
            await self.session.flush()
            await self.session.refresh(trading_session)
            return trading_session

        except IntegrityError as e:
            if "unique constraint" in str(e.orig).lower():
                raise DuplicateRecordError(f"Session name {name!r} already exists") from e
            raise

        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    async def find_by_session_id(
        self, session_id: UUID, *, for_update: bool = False
    ) -> Optional[TradingSession]:
        """Find a trading session by its business identifier.

        Args:
            session_id: The session's UUID business key.
            for_update: When True, lock the row with ``SELECT ... FOR UPDATE``
                and overwrite any copy already loaded in this session's
                identity map — the async half of AR9's both-twins rule.

                No caller exists yet: ``SessionService`` is sync-only, so the
                reclaim path this option was added for (Story 2.3 AC #6) reaches
                only the sync twin. It ships here so the two repositories keep
                the identical surface AR9 requires, and so Story 2.5's asyncio
                runner has it if it ever reads this row directly rather than
                bridging to the sync service via ``asyncio.to_thread``.

                ``populate_existing`` is load-bearing wherever this is used:
                without it the ORM returns the instance already in the identity
                map and discards the freshly locked row's column values, so a
                caller that had already loaded the row would decide on pre-lock
                state — demonstrated on the sync twin as two processes both
                reclaiming one session.

        Returns:
            The TradingSession, or None if not found.
        """
        stmt = select(TradingSession).where(TradingSession.session_id == session_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_name(self, name: str) -> Optional[TradingSession]:
        """Find a trading session by its unique name.

        Args:
            name: The operator-chosen unique handle.

        Returns:
            The TradingSession, or None if not found.
        """
        stmt = select(TradingSession).where(TradingSession.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_all(self) -> List[TradingSession]:
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
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def trade_counts_by_session(
        self, session_ids: Optional[Sequence[int]] = None
    ) -> dict[int, tuple[int, int]]:
        """Closed and open trade counts per session (Story 2.8, AC #8).

        The async twin of the sync repository's method of the same name —
        see there for the full rationale (typed-key join, ``LEFT OUTER
        JOIN`` so a trade-less session still reports ``(0, 0)``).

        Args:
            session_ids: Internal ``TradingSession.id`` values to restrict the
                query to, or ``None`` for every session.

        Returns:
            A mapping of ``TradingSession.id`` to ``(closed_count, open_count)``.
        """
        closed = func.count(Trade.id).filter(Trade.exit_timestamp.is_not(None))
        open_ = func.count(Trade.id).filter(Trade.exit_timestamp.is_(None))
        stmt = (
            select(TradingSession.id, closed, open_)
            .select_from(TradingSession)
            .outerjoin(Trade, Trade.session_id == TradingSession.id)
            .group_by(TradingSession.id)
        )
        if session_ids is not None:
            stmt = stmt.where(TradingSession.id.in_(session_ids))
        result = await self.session.execute(stmt)
        return {row[0]: (row[1], row[2]) for row in result}
