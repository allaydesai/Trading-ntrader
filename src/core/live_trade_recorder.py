"""Turns a closed Nautilus ``Position`` into one recorded trade (Story 3.5,
FR25/FR29).

Owns: the three pure ``Decimal`` conversion helpers, :func:`position_vouches_for`,
:func:`aggregate_closed_position` (reads a ``PositionClosed`` event plus, when
it vouches, the cached ``Position``, and returns a :class:`RecordedTrade`), and
:class:`TradeRecorder` — the second, independent subscriber on
``events.position*`` (:data:`POSITION_EVENTS_TOPIC`) the runner installs
beside :class:`~src.core.live_order_path.OrderEventObserver`.

Does not own: any database write, repository or port (Story 3.6 adds the
persistence side of this same module — ``sink`` is the handover, and stays
``None`` in production this story), the wiring itself
(``src/core/live_session_runner.py:_phase_subscribe`` constructs a
:class:`TradeRecorder` and subscribes its handler), or any rejection,
retry, or reconciliation-query machinery (Stories 3.7/4.2/4.3).

**The event is the snapshot; the ``Position`` is only for commission (facts 2
and 3, corrected by the 2026-09-12 review).** ``PositionClosed`` carries
``avg_px_open``/``avg_px_close`` (already volume-weighted by
``Position._calculate_avg_px``, ``model/position.pyx:757-774``), ``peak_qty``,
``ts_opened``/``ts_closed``/``duration_ns``, ``opening_order_id``/
``closing_order_id``, ``entry`` and ``realized_pnl`` — an immutable copy taken
the instant the position closed. It carries **no** commission at all
(``to_dict()`` has no ``commission*`` key; ``realized_pnl`` is net of it), so
FR29's "sum charged across all constituent fills" has exactly one source:
``Position.commissions()`` (``model/position.pyx:667-676``), looked up by
``event.position_id`` through the cache the runner hands this class.

**Why the cache cannot simply be trusted.** Nautilus queues position events
in ``_pending_position_events`` and publishes them only after the *whole*
fill has been applied (``execution/engine.pyx:1171-1186``). On a **flip**
(a fill larger than the open quantity on the opposite side, ``:1516-1600``)
it closes the original ``Position``, then builds a **new** one under the
same NETTING ``PositionId`` (``_open_position(instrument, None, ...)``) —
so when ``PositionClosed`` is finally dispatched, ``cache.position(id)``
already returns the new, open leg: ``avg_px_close 0.0``, ``ts_closed 0``,
``closing_order_id None``, the residual leg's commission. Measured
2026-09-12 (``tests/component/core/test_live_trade_recorder.py::
TestAgainstARealExecutionEngine``). The closed leg's own commission is
unrecoverable in that case. Hence :func:`position_vouches_for`: the cached
position is used for commission and fill count **only** when it is closed
and its ``closing_order_id`` is the event's; otherwise the leg is still
recorded from the event, with the commission marked unknown (``None``) and
one ``trade.commission_unavailable`` warning saying why. Reading inside the
same synchronous dispatch is still required (a fresh opening fill on the
*next* bar resets the closed position in place — ``_commissions``,
``_events``, ``_trade_ids`` cleared), just no longer sufficient on its own.

**Parity with the backtest path (fact 4).** ``BacktestPersistenceService
.save_trades_from_positions`` builds every backtest trade from
``avg_px_open``, ``avg_px_close``, ``peak_qty``, ``realized_pnl``,
``duration_ns``, ``ts_opened``/``ts_closed``, ``opening_order_id``/
``closing_order_id``, ``entry`` and ``commissions`` — this module reads the
same fields with the same conversions (``Decimal(str(value))``, never
``Decimal(value)``; quantize to 8 dp) so a paper trade and a backtest trade
are computed by one formula, not two. It reproduces that path's odd ID
mapping too (``TradeBase.venue_order_id`` holds the *opening* order id,
``client_order_id`` the *closing* one) — parity means matching it, however
counter-intuitive, and this record additionally carries
``opening_order_id``/``closing_order_id`` under their honest names so the
live transcript stays readable.

**Containment (fact 5).** A raise from any ``MessageBus`` handler re-enters
``MessageBus.publish_c``, which has no per-handler ``try``, and ends the
process at Nautilus's own silent ``os._exit(1)`` — measured, Story 2.7.
:meth:`TradeRecorder.handle_position_event` therefore wraps its entire body
in one ``try`` and never re-raises; :class:`~src.core.live_order_path
.OrderEventObserver` is the template this mirrors.

**One ``PositionId`` outlives many round trips.** Under NETTING,
``str(position.id)`` — reused as ``TradeBase.trade_id`` for backtest
parity — does not identify a trade uniquely across legs of the same
instrument. :attr:`RecordedTrade.trade_key` (``f"{position_id}:
{closing_order_id}"``) is what does, and Story 3.6 keys its idempotent writes
on it, not on ``trade_id``.

**Persistence side (Story 3.6).** ``TradeRecorder.__init__`` takes an
optional ``sink`` callable — ``None`` in the 3.5 production state, and a
:class:`~src.services.trade_record.SqlTradeRecord` bound to the session
everywhere else (D-F). The sink's contract, decided at drafting: a
synchronous call that returns ``bool`` (inserted or already-present) or
raises — ``SessionReclaimedError`` (imported from
``src.core.live_session_record``, a ``src`` root with no database import, so
this module's own no-``sqlalchemy`` boundary is untouched) for "this process
no longer owns the session" (D-C, terminal, never retried), anything else for
an ordinary database hiccup (queued and retried on the next event, AR42). A
``None`` ``commission_amount`` on the record means *unknown*, never *free* —
the ``trades.commission_amount`` column is nullable for exactly this.

**D-E — ``trade.aggregated`` is emitted before the sink is ever called.**
3.5's own ordering lost every computed value from the transcript on a
raising sink; aggregation succeeding and persistence succeeding are two
different facts, and only the first is true by the time this record is
logged.

**D-D — a reconciliation-owned position is not persisted.** A
``PositionClosed`` whose ``str(event.strategy_id)`` is ``"EXTERNAL"`` or
``"INTERNAL-DIFF"`` (measured, Task 1.1: reconciliation's own stamp,
``live/execution_engine.py:1709-1721``) is aggregated and logged exactly as
any other, then skipped loudly instead of reaching the sink — there is no
``strategy_id`` column on ``trades``, and a persisted row would silently join
the strategy's own comparison sample.

No ``nautilus_trader`` import: every framework object arrives duck-typed as
``Any`` and is dispatched by ``type(event).__name__``, the
``OrderEventObserver`` discipline, so the unit tier can exercise the pure
helpers with no C extension loaded at all.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.core.live_session_record import SessionReclaimedError
from src.models.trade import TradeBase

#: The wildcard topic position events publish on
#: (``f"events.position.{strategy_id}"``, ``execution/engine.pyx:1171-1184``,
#: measured Task 1.4) — the ``ORDER_EVENTS_TOPIC`` precedent.
POSITION_EVENTS_TOPIC = "events.position*"

#: AR41's `trade.*` namespace. Dotted lowercase past tense; no `close`/`kill`/
#: `halt`/`pause`/`finalize` stem anywhere in an operator-facing string this
#: module emits (AR36) — `trade.aggregated`, not `trade.closed`, is exactly
#: that discipline applied to this record's own name.
AGGREGATED_EVENT = "trade.aggregated"
#: AR41's normative lifecycle milestone (Story 3.6) — the name is fixed,
#: epics.md:241, not this module's to choose.
PERSISTED_EVENT = "trade.persisted"
RECORDER_FAILED_EVENT = "trade.recorder_failed"
COMMISSION_MIXED_EVENT = "trade.commission_mixed_currency"
COMMISSION_UNAVAILABLE_EVENT = "trade.commission_unavailable"
PERSIST_REFUSED_EVENT = "trade.persist_refused"
PERSIST_SKIPPED_EVENT = "trade.persist_skipped"
#: Review 2026-09-12 (decision 2): a sink that raises ``ValueError``/``TypeError``
#: — ``TradeCreate`` validation, a caller bug — is permanent, so the record is
#: dropped with every aggregated field on this record rather than queued,
#: where it would head-of-line-block every later trade for the session.
PERSIST_DROPPED_EVENT = "trade.persist_dropped"

#: Reconciliation's own strategy_id stamps (D-D, measured Task 1.1):
#: `live/execution_engine.py:1709-1721`. A string-comparison filter, not a
#: reconciliation policy — Story 4.2 owns what reconciliation does with a
#: disagreement and can flip this with a one-line change.
RECONCILIATION_STRATEGY_IDS = frozenset({"EXTERNAL", "INTERNAL-DIFF"})

#: Every **lifecycle** record this module emits — a membership-pinned tuple
#: (CLAUDE.md Anti-Patterns, the `EMITTED_ORDER_EVENTS` precedent):
#: `trade.recorder_failed`, `trade.commission_mixed_currency`,
#: `trade.commission_unavailable`, `trade.persist_refused`,
#: `trade.persist_skipped` and `trade.persist_dropped` are diagnostic/boundary records and stay
#: deliberately outside it, the `order.observer_failed` precedent
#: (`live_order_path.py:150-161`).
EMITTED_TRADE_EVENTS: tuple[str, ...] = (AGGREGATED_EVENT, PERSISTED_EVENT)

#: The quantization every price/quantity conversion uses — the
#: `backtest_persistence.py:489-493` precedent, reproduced for parity.
PRICE_QUANTUM = Decimal("0.00000001")

NANOS_PER_MICROSECOND = 1_000
NANOS_PER_SECOND = 1_000_000_000


def to_price_decimal(value: float) -> Decimal:
    """Convert a Nautilus C-side ``double`` to an exact ``Decimal``.

    ``Decimal(str(value))``, never ``Decimal(value)`` — the latter reproduces
    the binary float's own artifact (``100.1`` becomes a 51-digit number;
    measured, Task 1.1). A NaN is rejected explicitly: contrary to the
    ``backtest_persistence.py:495-496`` comment this story's Task 1
    fresh-interpreter probe cites, ``Decimal("NaN").quantize()`` does **not**
    raise ``InvalidOperation`` under the default decimal context — a quiet
    NaN propagates silently to a NaN result, and only an *ordering*
    comparison on it raises. That comment is measurably wrong; this function
    does not rely on it and checks for NaN itself (``value != value`` is
    ``True`` for, and only for, a float NaN).
    """
    if value != value:
        raise ValueError(f"cannot convert NaN to a Decimal price: {value!r}")
    return Decimal(str(value)).quantize(PRICE_QUANTUM)


def select_commission(
    commissions: Sequence[Any], settlement_code: str
) -> tuple[Decimal, str, tuple[str, ...]]:
    """Pick the commission FR29 reports, never a raise.

    ``Position.commissions()`` already sums same-currency fills into one
    ``Money`` per currency (``model/position.pyx:667-676``), so more than one
    entry means genuinely distinct currencies were charged. The third
    element is the empty tuple unless that happened — a non-empty result is
    exactly the "more than one entry" condition
    :meth:`TradeRecorder.handle_position_event` checks before warning.
    """
    if not commissions:
        return (Decimal("0"), settlement_code, ())
    if len(commissions) == 1:
        money = commissions[0]
        return (money.as_decimal(), money.currency.code, ())
    diagnostics = tuple(str(money) for money in commissions)
    for money in commissions:
        if money.currency.code == settlement_code:
            return (money.as_decimal(), money.currency.code, diagnostics)
    first = commissions[0]
    return (first.as_decimal(), first.currency.code, diagnostics)


def unix_nanos_to_utc(ns: int) -> datetime:
    """Exact, tz-aware, no float arithmetic — sub-microsecond nanos truncate."""
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=ns // NANOS_PER_MICROSECOND)


def position_vouches_for(event: Any, position: Any) -> bool:
    """Is ``position`` the very leg ``event`` closed — closed, and closed by
    the same order? ``False`` for ``None``, for a position that has already
    been re-opened under the same NETTING id (a flip: ``closing_order_id`` is
    ``None`` again), and for one closed by some other order."""
    return (
        position is not None
        and bool(position.is_closed)
        and str(position.closing_order_id) == str(event.closing_order_id)
    )


@dataclass(frozen=True)
class RecordedTrade:
    """One closed round trip, ready for a sink to persist (Story 3.6) or a
    log line to describe (this story).

    ``trade_key`` is what 3.6 must key idempotent writes on — ``trade_id``
    (``str(position_id)``) is not unique across legs of the same instrument
    under NETTING. ``trade.commission_amount`` / ``fill_count`` are ``None``
    when no cached position vouched for the leg (module docstring): unknown,
    not zero.
    """

    trade: TradeBase
    profit_loss: Decimal
    profit_pct: Decimal | None
    holding_period_seconds: int
    position_id: str
    strategy_id: str
    fill_count: int | None
    trade_key: str


def aggregate_closed_position(event: Any, position: Any) -> RecordedTrade:
    """Read a ``PositionClosed`` event (every snapshot field) and, when it
    vouches for the leg, the cached ``Position`` (commission, fill count),
    and return the trade they represent.

    Pure: no log, no cache, no side effect. Raises ``ValueError`` on an
    unconvertible field (no new exception class, retro D3) — the caller
    decides what a raise here means.

    ``profit_pct`` reproduces the backtest formula
    (``(exit - entry) / entry * 100``) verbatim, side-unaware artifacts
    included (Hazard #6) — parity, not a fix; the fix, if any, is Story
    5.6's, on both sides at once.
    """
    entry_price = to_price_decimal(float(event.avg_px_open))
    exit_price = to_price_decimal(float(event.avg_px_close))
    quantity = to_price_decimal(float(event.peak_qty))

    commission_amount: Decimal | None = None
    commission_currency: str | None = None
    fill_count: int | None = None
    if position_vouches_for(event, position):
        commission_amount, commission_currency, _ = select_commission(
            position.commissions(), position.settlement_currency.code
        )
        fill_count = position.event_count

    trade = TradeBase(
        instrument_id=str(event.instrument_id),
        trade_id=str(event.position_id),
        venue_order_id=str(event.opening_order_id),
        client_order_id=str(event.closing_order_id),
        order_side=event.entry.name,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        commission_amount=commission_amount,
        commission_currency=commission_currency,
        entry_timestamp=unix_nanos_to_utc(event.ts_opened),
        exit_timestamp=unix_nanos_to_utc(event.ts_closed),
    )

    profit_pct = ((exit_price - entry_price) / entry_price) * Decimal("100")

    return RecordedTrade(
        trade=trade,
        profit_loss=event.realized_pnl.as_decimal(),
        profit_pct=profit_pct,
        holding_period_seconds=event.duration_ns // NANOS_PER_SECOND,
        position_id=str(event.position_id),
        strategy_id=str(event.strategy_id),
        fill_count=fill_count,
        trade_key=f"{event.position_id}:{event.closing_order_id}",
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


class TradeRecorder:
    """The recorder's own message-bus handler (AC #6).

    Args:
        cache: The live node's cache (``node.cache``, a ``CacheFacade`` —
            Task 1.3), read synchronously inside :meth:`handle_position_event`
            (module docstring: too late is wrong, and even on time is not
            always enough).
        log: A structlog logger already bound to ``session_id``. Never
            ``structlog.get_logger()`` at call time — contextvars are empty on
            executor threads (``live_order_path.py:417-420``).
        sink: Story 3.6's persistence handover. ``None`` in the 3.5
            production state (no sink configured); a callable that returns
            ``bool`` or raises everywhere else.
        on_ownership_lost: Called at most once, with no arguments, the
            instant a sink raises ``SessionReclaimedError`` (D-C). The runner
            wires this to a scheduled ``node.stop()`` via ``loop.call_soon``
            — never called directly from here, which stays framework-free.
            "At most once" is enforced by the ``_reclaimed`` latch (review
            2026-09-12): once set, the sink is never called again — a later
            close is aggregated and logged, then skipped loudly.
    """

    def __init__(
        self,
        cache: Any,
        log: Any,
        sink: Callable[[RecordedTrade], bool] | None = None,
        on_ownership_lost: Callable[[], None] | None = None,
    ) -> None:
        self._cache = cache
        self._log = log
        self._sink = sink
        self._on_ownership_lost = on_ownership_lost
        #: Closed trades whose sink call failed with an ordinary error
        #: (never a reclaim — D-C says that one is terminal, not retried).
        #: Bounded: at most one entry per closed round trip, drained FIFO on
        #: the next ``events.position*`` delivery of any type (AC #4, NFR2).
        self._pending: list[RecordedTrade] = []
        #: Latched the first time a sink raises ``SessionReclaimedError``;
        #: the sink is never called again after that (D-C: terminal).
        self._reclaimed = False
        #: Class-name dispatch — a closed set, the `OrderEventObserver`
        #: precedent (`live_order_path.py:451-461`). The two-directional
        #: `EMITTED_TRADE_EVENTS` pin drives its test builders from this map's
        #: own keys, so a fourth event type cannot join silently.
        self._dispatch: dict[str, Callable[[Any], None]] = {
            "PositionOpened": self._ignore,
            "PositionChanged": self._ignore,
            "PositionClosed": self._record_closed,
        }
        #: Which step is in flight, read by `handle_position_event`'s except
        #: block to name a failure's `stage`. Transient per-call state, valid
        #: because Nautilus dispatches one handler at a time on one thread —
        #: the same assumption the observer's own `_orders` accumulator makes.
        self._stage = "dispatch"

    def _ignore(self, event: Any) -> None:
        """`PositionOpened`/`PositionChanged` produce no record, no sink call."""
        return

    @property
    def pending_trade_keys(self) -> tuple[str, ...]:
        """The ``trade_key`` of every queued, not-yet-persisted trade — what
        the runner names in the transcript when it cannot retry them."""
        return tuple(recorded.trade_key for recorded in self._pending)

    def _trade_fields(self, recorded: RecordedTrade) -> dict[str, str | None]:
        """The field set every persistence-outcome record shares."""
        return {
            "position_id": recorded.position_id,
            "instrument_id": recorded.trade.instrument_id,
            "strategy_id": recorded.strategy_id,
            "entry_price": str(recorded.trade.entry_price),
            "exit_price": str(recorded.trade.exit_price),
            "quantity": str(recorded.trade.quantity),
            "commission": _optional_str(recorded.trade.commission_amount),
            "currency": recorded.trade.commission_currency,
            "realized_pnl": str(recorded.profit_loss),
        }

    def _record_closed(self, event: Any) -> None:
        self._stage = "lookup"
        position = self._cache.position(event.position_id)

        self._stage = "aggregate"
        if position_vouches_for(event, position):
            _, _, mixed_currency = select_commission(
                position.commissions(), position.settlement_currency.code
            )
            if mixed_currency:
                self._log.warning(
                    COMMISSION_MIXED_EVENT,
                    position_id=str(event.position_id),
                    commissions=mixed_currency,
                )
        else:
            self._log.warning(
                COMMISSION_UNAVAILABLE_EVENT,
                position_id=str(event.position_id),
                closing_order_id=str(event.closing_order_id),
                reason=(
                    "no cached position"
                    if position is None
                    else "cached position is not the closed leg (re-opened under the same id)"
                ),
            )
        recorded = aggregate_closed_position(event, position)

        # D-E: a statement about aggregation, which just succeeded — emitted
        # before the sink is ever called, so a raising sink cannot erase the
        # computed values from the transcript.
        self._log.info(
            AGGREGATED_EVENT,
            ts_event=str(event.ts_event),
            trade_key=recorded.trade_key,
            fill_count=_optional_str(recorded.fill_count),
            holding_period_seconds=str(recorded.holding_period_seconds),
            opening_order_id=recorded.trade.venue_order_id,
            closing_order_id=recorded.trade.client_order_id,
            ts_opened=str(event.ts_opened),
            ts_closed=str(event.ts_closed),
            order_side=recorded.trade.order_side,
            **self._trade_fields(recorded),
        )

        if recorded.strategy_id in RECONCILIATION_STRATEGY_IDS:
            # D-D: no `strategy_id` column on `trades` -- a persisted row
            # would silently join the strategy's own comparison sample.
            self._log.warning(
                PERSIST_SKIPPED_EVENT,
                reason="reconciliation_owned",
                trade_key=recorded.trade_key,
                **self._trade_fields(recorded),
            )
            return

        self._persist(recorded, attempt="first")

    def _attempt_sink(
        self, recorded: RecordedTrade, *, attempt: str
    ) -> tuple[str, Exception | None]:
        """Call the sink once. Never raises.

        Returns:
            ``("ok", None)`` on success (logs ``trade.persisted``);
            ``("reclaimed", None)`` when the sink raised
            ``SessionReclaimedError`` (logs ``trade.persist_refused``, latches
            ``_reclaimed``, fires ``on_ownership_lost`` once);
            ``("invalid", exc)`` for ``ValueError``/``TypeError`` — permanent,
            the caller drops it loudly; ``("failed", exc)`` for any other
            exception — the caller queues it for retry.
        """
        assert self._sink is not None
        try:
            inserted = self._sink(recorded)
        except SessionReclaimedError:
            self._log.warning(
                PERSIST_REFUSED_EVENT, trade_key=recorded.trade_key, **self._trade_fields(recorded)
            )
            already_latched, self._reclaimed = self._reclaimed, True
            if self._on_ownership_lost is not None and not already_latched:
                self._on_ownership_lost()
            return "reclaimed", None
        except (ValueError, TypeError) as exc:
            return "invalid", exc
        except Exception as exc:  # noqa: BLE001 - the caller decides what to do
            return "failed", exc
        self._log.info(
            PERSISTED_EVENT,
            trade_key=recorded.trade_key,
            inserted=str(inserted),
            attempt=attempt,
            **self._trade_fields(recorded),
        )
        return "ok", None

    def _log_sink_failure(self, recorded: RecordedTrade, exc: Exception | None) -> None:
        """One ``trade.recorder_failed stage="sink"`` record, traceback attached.

        ``exc_info=exc``, never ``exc_info=True``: this runs after
        ``_attempt_sink``'s ``except`` block has already returned, where
        ``sys.exc_info()`` is empty and ``True`` would attach nothing (review
        2026-09-12, confirmed against structlog's ``format_exc_info``).
        """
        self._log.error(
            RECORDER_FAILED_EVENT,
            stage="sink",
            trade_key=recorded.trade_key,
            error_type=type(exc).__name__ if exc is not None else "",
            pending=str(len(self._pending)),
            exc_info=exc,
        )

    def _log_dropped(self, recorded: RecordedTrade, exc: Exception | None) -> None:
        self._log.error(
            PERSIST_DROPPED_EVENT,
            reason="invalid_record",
            trade_key=recorded.trade_key,
            error_type=type(exc).__name__ if exc is not None else "",
            exc_info=exc,
            **self._trade_fields(recorded),
        )

    def _persist(self, recorded: RecordedTrade, *, attempt: str) -> None:
        self._stage = "sink"
        if self._sink is None:
            return
        if self._reclaimed:
            self._log.warning(
                PERSIST_SKIPPED_EVENT,
                reason="ownership_lost",
                trade_key=recorded.trade_key,
                **self._trade_fields(recorded),
            )
            return
        status, exc = self._attempt_sink(recorded, attempt=attempt)
        if status == "failed":
            self._pending.append(recorded)
            self._log_sink_failure(recorded, exc)
        elif status == "invalid":
            self._log_dropped(recorded, exc)

    def flush_pending(self) -> int:
        """Drain queued trades FIFO, stopping at the first failure (AC #4).

        Called on every ``events.position*`` delivery before dispatch, and
        once more at teardown (``LiveSessionRunner``'s ``finally``). A
        reclaim mid-drain discards the reclaimed entry (D-C: never retried)
        and stops — and once ``_reclaimed`` is latched, nothing is drained
        again (the runner names the leftovers); an ordinary failure leaves it
        and the rest queued; an invalid entry is dropped loudly and the drain
        continues.

        Returns:
            The count still pending after this call — ``0`` on a full drain.
        """
        while self._pending and not self._reclaimed:
            recorded = self._pending[0]
            status, exc = self._attempt_sink(recorded, attempt="retry")
            if status in ("ok", "reclaimed"):
                self._pending.pop(0)
                continue
            if status == "invalid":
                self._pending.pop(0)
                self._log_dropped(recorded, exc)
                continue
            self._log_sink_failure(recorded, exc)
            break
        return len(self._pending)

    def handle_position_event(self, event: Any) -> None:
        """Handle one ``events.position*`` delivery.

        The whole body runs in one ``try`` — a raise here reaches
        ``MessageBus.publish_c`` and ends the process (module docstring,
        fact 5). ``self._stage`` names which step failed; it is set before
        each step that can fail, so an exception raised anywhere is
        attributed to the step it actually happened in.
        """
        event_type = type(event).__name__
        self._stage = "dispatch"
        try:
            self.flush_pending()
            handler = self._dispatch.get(event_type)
            if handler is not None:
                handler(event)
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                RECORDER_FAILED_EVENT,
                stage=self._stage,
                position_id=str(getattr(event, "position_id", None)),
                event_type=event_type,
                error_type=type(exc).__name__,
                exc_info=True,
            )
