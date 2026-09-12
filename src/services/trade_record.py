"""The SQLAlchemy side of Story 3.6's trade sink — ``live_trade_recorder``'s
persistence handover, injected from the CLI (D-F).

Owns: :class:`SqlTradeRecord`, the adapter ``TradeRecorder``'s ``sink``
callable points at. Talks to the repository **directly**, not through
``SessionService``: a trade write is not a session lifecycle fact (AR37's
single assigner is not involved here), and this adapter has no business
resolving a session's status or edges — only refusing a write the row's own
fencing token no longer sanctions.

Does not own: ``SessionRecordPort`` (unchanged — a trade is not a row fact,
see ``src/core/live_session_record.py``), the recorder's aggregation logic
(``src/core/live_trade_recorder.py``, Story 3.5), or the wiring (the CLI's
``build_session_ports`` constructs this and the runner calls its ``persist``
as ``trade_sink``).

**One transaction per call**, the ``session_record.py`` precedent: a trade
closes a handful of times a day, not every 30 seconds, so there is no
argument here for anything longer-lived.

**``session_pk`` and ``owner_epoch`` are bound at construction, not passed
per call.** The recorder physically cannot write another session's trade nor
forge its own claim to ownership — the same discipline ``SqlSessionRecord``
already follows, applied to the internal PK instead of the UUID business key
because this adapter never resolves the UUID (Story 3.6 Dev Notes: "do not
confuse the two session_ids").

**D-A, stated as a known limit.** The write is synchronous and inline in the
recorder's msgbus handler dispatch — measured at sub-millisecond median
against the local Postgres (Task 1.3: pg8000 0.54ms, psycopg2 0.32ms
median), four orders of magnitude under a 1-minute bar. A wedged Postgres
would stall the loop for one connection/statement timeout on the *next*
close, not on every bar. Not mitigated further here; a per-write engine with
its own timeouts is the escape hatch if a live run ever shows the stall —
route, do not pre-build.

**The fence is the repository's own ``WHERE``, shared with the heartbeat.**
``stamp_activity_if_owner`` is called first, inside the same transaction, as
both the ownership check *and* a liveness refresh (true: the process that
just closed a trade is alive). A rowcount of ``0`` means this process no
longer owns the session, and ``insert_trade_if_absent`` is never reached —
pinned, because writing a trade after the fence already refused would attach
it to a row this process no longer has any claim to describe.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

import structlog

from src.core.live_session_record import SessionReclaimedError
from src.core.live_trade_recorder import RecordedTrade
from src.db.models.trade import Trade as TradeRow
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.models.trade import TradeCreate

logger = structlog.get_logger(__name__)

#: What ``get_sync_session`` is, expressed as a type so it can be injected —
#: the ``session_record.py`` precedent.
SessionFactory = Callable[[], AbstractContextManager[object]]


def _utc_now() -> datetime:
    """The repo's house clock idiom."""
    return datetime.now(timezone.utc)


class SqlTradeRecord:
    """Write one closed trade, fenced by the session's owner epoch.

    Args:
        session_pk: The row's internal ``TradingSession.id`` — never the
            UUID business key. Obtained from the claim
            (``ClaimedSession.session_pk``), never resolved per write.
        owner_epoch: The epoch this process's own ``-> running`` claim
            produced.
        session_factory: The ``get_sync_session`` context manager. Injected
            so the transaction discipline is testable without a database.
        time_source: The clock stamped into the heartbeat refresh. Injected
            for deterministic tests.
    """

    def __init__(
        self,
        session_pk: int,
        *,
        owner_epoch: int,
        session_factory: SessionFactory = get_sync_session,
        time_source: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session_pk = session_pk
        self._owner_epoch = owner_epoch
        self._session_factory = session_factory
        self._time_source = time_source

    def persist(self, recorded: RecordedTrade) -> bool:
        """Validate, fence, then insert — in that order, one transaction.

        Validation happens **before** any transaction opens: a
        ``RecordedTrade`` whose ``TradeBase`` cannot become a valid
        ``TradeCreate`` is a caller bug, not a database failure, and must not
        open a factory block to discover that.

        Raises:
            ValueError: ``recorded.trade`` fails ``TradeCreate`` validation.
            SessionReclaimedError: The heartbeat fence refused — this
                process no longer owns the session. ``insert_trade_if_absent``
                is never called in this case.

        Returns:
            ``True`` when a new row was inserted, ``False`` when the same
            ``(session_id, trade_id, client_order_id)`` key already existed
            — the shape a safe retry after a lost acknowledgement needs.
        """
        trade_create = TradeCreate(
            **recorded.trade.model_dump(),
            session_id=self._session_pk,
            backtest_run_id=None,
        )

        with self._session_factory() as db_session:
            repository = SyncTradingSessionRepository(db_session)  # type: ignore[arg-type]
            rowcount = repository.stamp_activity_if_owner(
                self._session_pk, owner_epoch=self._owner_epoch, at=self._time_source()
            )
            if rowcount == 0:
                raise SessionReclaimedError(
                    f"Session (pk={self._session_pk}) was reclaimed by another process: this "
                    f"trade write's owner_epoch {self._owner_epoch} no longer matches the row's."
                )

            trade_row = TradeRow(
                backtest_run_id=trade_create.backtest_run_id,
                session_id=trade_create.session_id,
                instrument_id=trade_create.instrument_id,
                trade_id=trade_create.trade_id,
                venue_order_id=trade_create.venue_order_id,
                client_order_id=trade_create.client_order_id,
                order_side=trade_create.order_side,
                quantity=trade_create.quantity,
                entry_price=trade_create.entry_price,
                exit_price=trade_create.exit_price,
                commission_amount=trade_create.commission_amount,
                commission_currency=trade_create.commission_currency,
                fees_amount=trade_create.fees_amount,
                entry_timestamp=trade_create.entry_timestamp,
                exit_timestamp=trade_create.exit_timestamp,
                profit_loss=recorded.profit_loss,
                profit_pct=recorded.profit_pct,
                holding_period_seconds=recorded.holding_period_seconds,
            )
            return repository.insert_trade_if_absent(trade_row)
