"""Resume a strategy mid-position: what it may start beside (Story 4.5).

Owns: refusing an engine cache the framework would abort on
(:func:`refuse_imported_position_orders`, decision D-D), refusing a strategy
whose instrument carries a holding no strategy owns (:meth:`ResumeCheck.
refuse_unowned`, D-C), and the ``strategy.resumed`` record a strategy that
restarts holding its own position emits (:meth:`ResumeCheck.note_resumed`,
D-E). Does not own: proving the cache matches the broker (Story 4.2's
``live_startup_reconcile``, which runs first and refuses a *contradicted*
strategy position), which positions a strategy acts on (each built-in reads
its own book, ``strategy_id=self.id`` — D-B), or where in the startup
sequence these run (the runner: D-D inside ``node:connect``, D-C and D-E inside
``trading``, AR39's order and names unchanged).

**What "hydrated from reconciliation, not reconstructed from local state"
means here.** A restarted strategy's position is the engine cache's own —
restored from the session's Redis namespace (AR10) — and it is kept only
because Story 4.2 proved its side and quantity equal the broker's; a
contradicted one refuses the start. The strategy never rebuilds it. What the
broker cannot say is *which strategy* opened a holding, so a holding the cache
cannot attribute (a trade made by hand in TWS, an engine cache that lost it)
arrives from reconciliation as ``INTERNAL-DIFF`` — once, since the session's
engine drops the IB adapter's fabricated per-position order (D-A,
``live_node_builder.EXEC_ENGINE_FILTER_UNCLAIMED_EXTERNAL_ORDERS``).

**D-C, ruled by the PO (2026-09-28, option B).** A strategy whose instrument
carries such a holding is refused — contained through Story 2.7's start-failure
path, so its siblings still start and a session where none can fails closed
(exit 1). Starting it anyway would let it read its own book as flat and enter
beside the holding, doubling the exposure if the holding was in fact its own
(FR38). The holding is never traded, flattened or resized by this system; the
remedy is manual, in TWS. Synthetic positions that net to zero — the
``EXTERNAL +N / INTERNAL-DIFF −N`` pair a pre-4.5 restart left — own nothing
and refuse nothing.

**D-D.** Before this story every restart holding a position imported the
adapter's fabricated ``FILLED`` order (``client_order_id == instrument id``) as
``EXTERNAL``. Once cached, a later restart after that position shrank makes the
framework's own pass underflow the order's ``leaves_qty`` and the **process
aborts on a Rust panic** inside ``node:connect`` (Story 4.2, measured 1.4S) —
nothing after ``run_async()`` can catch it. D-A stops new namespaces caching
the order; an already-cached one is not helped (the filter never sees a known
order), so it is refused here, **before** ``run_async()``: explicit, logged,
fail-closed, naming the remedy — a new session. No database change: the
refusal is a typed exception the runner's own teardown handles.

Framework objects arrive duck-typed; the one ``nautilus_trader`` import parses
a spec's bar type exactly as ``materialise_strategy`` does. Calls no order
method (``LIVE_MODULE_GLOBS`` scans it) and never writes the cache.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from nautilus_trader.model.data import BarType

from src.core.exit_outcome import LiveCheckOutcome
from src.core.live_startup_reconcile import cached_positions
from src.core.live_trade_recorder import unix_nanos_to_utc
from src.models.position_reconciliation import StartupReconciliation, split_by_owner

#: D-E. A strategy that restarts holding its own position (AR41 ``strategy.*``).
RESUMED_EVENT = "strategy.resumed"
#: D-C. One strategy refused for a holding no strategy owns on its instrument.
RESUME_REFUSED_EVENT = "strategy.resume_refused"
#: D-D. The whole start refused, before the framework's pass, for a pre-4.5 cache.
SESSION_RESUME_REFUSED_EVENT = "session.resume_refused"
#: D-E's diagnostic: the record could not be written. Never raised past.
RECORD_FAILED_EVENT = "strategy.resume_record_failed"

UNOWNED_POSITION = "unowned_position"
LEGACY_POSITION_IMPORT = "legacy_position_import"

#: Nautilus's stamp for an order the cache did not know (``live/execution_engine.py``).
_EXTERNAL = "EXTERNAL"
_ZERO = Decimal(0)


class ResumeRefusedError(RuntimeError):
    """A resume this system will not perform, and why (exit 1, our own text).

    Raised for D-D out of ``node:connect`` (the whole start is refused) and for
    D-C inside ``_start_strategy``'s containment (only that strategy is).
    Operator-safe: instruments and quantities only, never an account id (NFR26).

    Attributes:
        reason: :data:`UNOWNED_POSITION` or :data:`LEGACY_POSITION_IMPORT`.
        instrument_ids: The instruments the refusal is about.
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True

    def __init__(self, reason: str, message: str, instrument_ids: Sequence[str]) -> None:
        super().__init__(message)
        self.reason = reason
        self.instrument_ids = tuple(instrument_ids)


def imported_position_orders(orders: Iterable[Any]) -> tuple[str, ...]:
    """Instruments carrying the IB adapter's fabricated per-position order.

    Its shape is exact (Story 4.2, F4): strategy ``EXTERNAL`` and a
    ``client_order_id`` equal to the instrument id. A manual order's id comes
    from its ``orderRef`` or is generated, and no strategy's order is ever
    ``EXTERNAL`` — so nothing else matches.
    """
    return tuple(
        sorted(
            {
                str(order.instrument_id)
                for order in orders
                if str(order.strategy_id) == _EXTERNAL
                and str(order.client_order_id) == str(order.instrument_id)
            }
        )
    )


def refuse_imported_position_orders(cache: Any, log: Any) -> None:
    """Refuse a pre-4.5 engine cache before the framework's pass can abort on it (D-D).

    Called by the runner in ``node:connect`` immediately before
    ``node.run_async()`` — the cache was loaded from Redis in
    ``NautilusKernel.__init__`` and nothing has reconciled it yet.

    Raises:
        ResumeRefusedError: :data:`LEGACY_POSITION_IMPORT`, naming every
            instrument and the remedy.
    """
    instrument_ids = imported_position_orders(cache.orders())
    if not instrument_ids:
        return
    _emit(
        log,
        "error",
        SESSION_RESUME_REFUSED_EVENT,
        reason=LEGACY_POSITION_IMPORT,
        instrument_ids=list(instrument_ids),
        remedy="create a new session",
    )
    raise ResumeRefusedError(
        LEGACY_POSITION_IMPORT,
        "This session's engine cache holds an order Nautilus imported from a broker "
        f"position before Story 4.5 ({', '.join(instrument_ids)}). At a later restart, once "
        "that position has shrunk, the framework aborts the whole process on it, so no "
        "strategy was started and nothing was traded. Create a new session for this "
        "strategy. (This session's engine cache is disposable, but clearing it by hand also "
        "loses its restored order-id counter: docs/agent/nautilus.md, 'Startup "
        "reconciliation'.)",
        instrument_ids,
    )


class ResumeCheck:
    """What each strategy of this start may resume beside (D-C, D-E).

    Built once in ``trading``, after ``reconcile`` produced its proof; the
    cache is read at each call, because an earlier strategy may already be
    trading while a later one is checked. The **broker** figure in every record
    and message is the reconcile-time read (the proof's), not a fresh one —
    worded so (code review 2026-09-28): the running session's cycle is what
    keeps the two aligned afterwards.

    Args:
        cache: The node's cache.
        reconciliation: The ``reconcile`` phase's proof — the broker's view.
        started_at: This process run's ``-> running`` instant.
        log: The session-bound logger.
    """

    def __init__(
        self,
        cache: Any,
        reconciliation: StartupReconciliation,
        started_at: datetime,
        log: Any,
    ) -> None:
        self._cache = cache
        self._held = {held.instrument_id: held for held in reconciliation.broker.positions}
        self._started_at = started_at
        self._log = log

    def refuse_unowned(self, strategy_spec: Any) -> None:
        """Refuse the strategy if its instrument carries a holding no strategy owns.

        Raises:
            ResumeRefusedError: :data:`UNOWNED_POSITION` — raised inside
                ``_start_strategy``'s ``try``, whose ``except`` contains it.
        """
        instrument_id = str(BarType.from_str(strategy_spec.bar_types[0]).instrument_id)
        owned, unowned = split_by_owner(cached_positions(self._cache), instrument_id)
        if unowned == _ZERO:
            return
        broker = self._broker_quantity(instrument_id)
        _emit(
            self._log,
            "error",
            RESUME_REFUSED_EVENT,
            spec_strategy_id=strategy_spec.strategy_id,
            instrument_id=instrument_id,
            reason=UNOWNED_POSITION,
            unowned_quantity=str(unowned),
            strategy_quantity=str(owned),
            broker_quantity=str(broker),
        )
        raise ResumeRefusedError(
            UNOWNED_POSITION,
            f"Strategy {strategy_spec.strategy_id!r} was not started: IBKR reported "
            f"{broker:+} of {instrument_id} at this start's reconciliation, and {unowned:+} of "
            f"it belongs to no strategy of this session (its strategies hold {owned:+}) — a "
            "trade made by hand, or an engine cache that no longer records it. This system "
            "never trades a holding it cannot attribute and never adjusts it: remove the "
            "holding by hand in TWS, or run this strategy on another instrument.",
            (instrument_id,),
        )

    def note_resumed(self, strategy: Any) -> None:
        """One ``strategy.resumed`` per open position the strategy owns. Never raises."""
        try:
            positions = self._cache.positions_open(strategy_id=strategy.id)
            open_orders = len(self._cache.orders_open(strategy_id=strategy.id))
            for position in positions:
                self._log.info(RESUMED_EVENT, **self._fields(strategy, position, open_orders))
        except Exception as exc:  # noqa: BLE001 - AR42: a record must not stop a start
            _emit(self._log, "warning", RECORD_FAILED_EVENT, error_type=type(exc).__name__)

    def _fields(self, strategy: Any, position: Any, open_orders: int) -> dict[str, Any]:
        instrument_id = str(position.instrument_id)
        held = self._held.get(instrument_id)
        opened = unix_nanos_to_utc(position.ts_opened)
        price = None if held is None or held.average_price is None else str(held.average_price)
        return {
            "strategy_id": str(strategy.id),
            "instrument_id": instrument_id,
            "position_id": str(position.id),
            "side": "LONG" if position.is_long else "SHORT",
            "quantity": str(position.signed_decimal_qty()),
            "avg_px_open": str(position.avg_px_open),
            "ts_opened": opened.isoformat(),
            "opened_before_this_run": opened < self._started_at,
            "broker_quantity": str(self._broker_quantity(instrument_id)),
            "broker_average_price": price,
            "open_orders": open_orders,
        }

    def _broker_quantity(self, instrument_id: str) -> Decimal:
        held = self._held.get(instrument_id)
        return _ZERO if held is None else held.quantity


def _emit(log: Any, level: str, event: str, **fields: Any) -> None:
    """Log without ever raising: a failing sink must not replace the typed
    refusal the caller is owed (the ``live_startup_reconcile`` precedent)."""
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
        pass
