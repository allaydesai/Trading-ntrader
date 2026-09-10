"""The boundary the runner installs around each strategy's order calls
(Story 3.2, AC #4/#5/#6).

Owns: :func:`install_order_path` — the instance-level wrap that suppresses
order-creating strategy calls while the connection is known lost or
unobserved — and :class:`OrderEventObserver`, the first consumer of
Nautilus's own order events, which logs ``order.submitted`` with the
bar-close-to-submit latency NFR1 measures.

Does not own: the wiring (``src/core/live_session_runner.py`` calls
:func:`install_order_path` between ``materialise_strategy`` and
``add_strategy``, and subscribes the observer's handlers in
``_phase_subscribe``), the suppression *predicate* itself
(``src/core/live_connection_monitor.py``'s ``submission_withheld`` — this
module only *consults* it), position events (``events.position.*`` is not
subscribed — Stories 3.5/3.6's trade recorder owns those), or any retry,
persistence, or reconciliation-query machinery (Story 3.4/Epic 4/3.6). The
observer (below) now owns the order's full lifecycle (Story 3.3): every
state a backtest never produces — acceptance, partial and final fills,
rejection, cancellation, expiry, and denial — is logged as it arrives.

**Why a new module, not `live_strategy_guard.py` or the runner.**
``live_strategy_guard.py`` and ``live_session_runner.py`` are both in
``STOP_PATH_MODULES`` (``tests/unit/core/test_live_stop_path_is_inert.py``),
whose scan forbids any **call** to a forbidden order method anywhere in the
module — call-callee collection, so it catches ``self.close_all_positions(...)``,
``node.trader.close_all_positions(...)`` and a bare ``close_all_positions(...)``
alike. This module legitimately calls the wrapped originals, so it stays
deliberately OUT of that list — the runner and the guard call only
:func:`install_order_path`, a name that is not itself forbidden, so their own
scans stay clean.

**Mechanism, measured before writing this** (Story 3.2 Task 1, Dev Agent
Record): instance-level attribute assignment reliably shadows
``submit_order``/``close_position`` for the strategy's own Python call sites —
the same mechanism ``LiveStrategyGuard.wrap`` already uses for
``handle_bar``/``handle_event``. Measured directly against the installed
wheel: ``close_position``'s own internal ``self.submit_order(...)`` call
(``trading/strategy.pyx:1303``) *does* reach a wrapped ``submit_order`` too —
Cython's ``self.method()`` self-call for a ``cpdef`` method on a Python
subclass instance honours a Python-level instance ``__dict__`` override. This
corrects the pre-measurement Dev Notes hypothesis that it would bypass one;
it does not change the mechanism, because suppression must still stop the
*outer* ``close_position`` call (its own cache reads, its own log lines), not
merely rely on an inner call aliasing into an already-wrapped sibling.

**Cancels are deliberately not suppressed.** Cancelling an order while
disconnected creates no new exposure and cannot "blindly trade" — the
opposite of what this boundary exists to prevent. Only the four
order/position-*creating* calls are wrapped; see :data:`ORDER_CREATING_METHODS`.

**The suppression path is contained; the pass-through path is not.**
Consulting the predicate and logging a suppression is new code sitting
in front of a call that used to go straight through, so *that* part is
wrapped in its own ``try``/``except`` — any failure there suppresses (fail
closed) and logs ``order.suppression_failed`` rather than raising into
strategy code. But when the predicate says the order may proceed, the wrapper
calls straight through with **no** ``try`` around it: an exception from the
real Nautilus call must propagate exactly as it would from the unwrapped
method, which is what AC #1's "no live-specific strategy variant" requires.

⚠️ Corrected by code review 2026-08-30. That paragraph originally justified
itself with "Story 2.7's ``handle_bar``/``handle_event`` boundary, already
wrapping every strategy call this module's calls happen inside" — which is
false for a subset of call sites. ``GUARDED_HANDLERS`` is exactly
``("handle_bar", "handle_event")`` (``live_strategy_guard.py:114``);
``on_start``, ``on_stop``, ``on_reset`` and clock/timer callbacks are outside
it, and widening the tuple is a standing ``deferred-work.md`` item.
``custom/sma_crossover_long_only.py:86`` still flattens in ``on_stop``, so
the wrapper's new code (a bool read and a log call) genuinely does run
uncontained on that path. The decision stands — a ``try`` around the
pass-through would swallow real broker errors, which is worse — but it rests
on the new code being trivial, not on a containment that covers it.

**A suppressed call is silent to the strategy, and that is the contract**
(code review 2026-08-30, decided rather than discovered). The wrapper returns
``None``, which is exactly what the real ``cpdef void`` methods return on
success, so a strategy cannot distinguish "withheld" from "submitted". A
strategy that tracked entry state internally would therefore believe it holds
a position that was never opened. The built-ins re-derive from the portfolio
and so are unaffected; nothing downstream is currently scoped to own
strategy-visible suppression feedback, which is recorded in
``deferred-work.md`` rather than left implicit. Giving the strategy a signal
here would put a live-only branch inside strategy code, which AC #1's
zero-diff contract exists to forbid.

**The observer contains everything, always** — a raise from a msgbus handler
re-enters ``MessageBus.publish_c``, which has no ``try`` around
``sub.handler(msg)``, and ends at Nautilus's own silent ``os._exit(1)`` with
zero output (the Story 2.7 lesson). Nothing here may ever let that happen.

**Latency anchors — two, by the NFR1 ruling of 2026-09-10.** For this
adapter ``bar.ts_event`` is the bar's **open**, not its close
(``_ib_bar_to_ts_event``'s own docstring; see ``live_bar_observer.py`` fact
(1)), so the close is ``ts_event + interval``. ``bar_close_to_submit_ms`` is
anchored there: it is the live-vs-backtest divergence window, and it
*includes* IB's structural ~5.5s delivery lag (a completed bar is published
only when the next bar's first update arrives). It is logged, never gated on.
``bar_arrival_to_submit_ms`` is anchored on ``bar.ts_init`` — the wall clock
at which the adapter published the bar into this process — and is the number
NFR1's ``< 1s`` bound is judged on, because it is the only interval this
system controls. Until the ruling the single field measured from the open
under the ``bar_close_*`` name and read 65682ms on the first live fill: one
whole 60s interval of nothing plus the lag. Known hazards, stated rather
than hidden: ``live_bars.received``
appears twice per bar (Nautilus C logger + structlog), and the first bar
after subscribe can be a backfill bar (P3) — a live-transcript reader must
read latency from steady-state bars, not the first one. ``OrderSubmitted`` is
generated by the **execution client** (the adapter), not by
``Strategy.submit_order`` itself, which only publishes the order's own
``init_event_c()`` (``OrderInitialized``) on the same topic first (measured,
Task 1.2) — so this module filters on event *type*, never merely on topic.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any, TypedDict

#: The order/position-*creating* strategy methods this module wraps. A
#: subset of `tests/unit/core/test_live_stop_path_is_inert.py`'s
#: `FORBIDDEN_ORDER_METHODS` (production cannot import from `tests/`, so the
#: relationship is asserted there against a duplicated literal, per
#: CLAUDE.md's "Membership-pinned lists" convention) — `cancel_all_orders`
#: and `cancel_order` are deliberately excluded; see the module docstring.
ORDER_CREATING_METHODS: frozenset[str] = frozenset(
    {"submit_order", "submit_order_list", "close_position", "close_all_positions"}
)

#: AR41's `order.*` namespace. Dotted lowercase past tense, no `close`/
#: `halt`/`kill`/`pause`/`finalize` stem anywhere in an operator-facing string
#: this module emits (AR36). `order.canceled` is single-l, matching the
#: Nautilus event class name (`OrderCanceled`), so a transcript grep for the
#: class name and the structured record agree.
SUPPRESSED_EVENT = "order.suppressed"
SUBMITTED_EVENT = "order.submitted"
ACCEPTED_EVENT = "order.accepted"
REJECTED_EVENT = "order.rejected"
FILLED_EVENT = "order.filled"
CANCELED_EVENT = "order.canceled"
EXPIRED_EVENT = "order.expired"
DENIED_EVENT = "order.denied"

#: Every **order-lifecycle** record :class:`OrderEventObserver` emits — a
#: membership-pinned list (CLAUDE.md Anti-Patterns, the
#: `ORDER_CREATING_METHODS` precedent): the NFR26 anti-field scan
#: (`tests/component/core/test_live_order_path.py`) parametrizes from this
#: tuple and pins it as an exact set, so a dropped or added name is visible
#: rather than silently exempted from the scan.
#:
#: ⚠️ Scope corrected by code review 2026-08-30. This tuple was documented as
#: "every event name the observer can emit", which was false on the day it
#: shipped: the observer also emits ``order.observer_failed`` (twice — see
#: :meth:`OrderEventObserver.note_bar` and
#: :meth:`OrderEventObserver.handle_order_event`), and the module emits
#: ``order.suppressed`` / ``order.suppression_failed`` from the wrapper. Those
#: are diagnostic and boundary records, not lifecycle records, and they are
#: deliberately outside the scan. What the pin below guarantees is narrower
#: than the original wording claimed — see
#: `TestEveryDispatchedRecordNameIsPinned`, which derives the emitted set from
#: the dispatch map's own handlers rather than from a hand-written list, so a
#: *newly added* lifecycle record cannot escape the scan either.
EMITTED_ORDER_EVENTS: tuple[str, ...] = (
    SUBMITTED_EVENT,
    ACCEPTED_EVENT,
    REJECTED_EVENT,
    FILLED_EVENT,
    CANCELED_EVENT,
    EXPIRED_EVENT,
    DENIED_EVENT,
)

#: The wildcard topic `Strategy.register()` publishes order events on —
#: `events.order.{strategy_id}` (`trading/strategy.pyx:314-315`, measured
#: Task 1.2). Matches the `BAR_TOPIC` precedent (`live_session_node.py`).
ORDER_EVENTS_TOPIC = "events.order*"

#: Marks a strategy instance whose order path is already installed, so a
#: second call is a no-op instead of another wrapper layer (review
#: 2026-08-30). `getattr` in the install loop returns the *wrapped* function
#: on a re-install, which would consult the predicate twice and emit two
#: `order.suppressed` records for one call.
_INSTALLED_MARKER = "_ntrader_order_path_installed"

#: Beyond this, a bar-to-submit interval (either anchor) is not a measurement
#: — it is a bad anchor (an unset `ts_event` of 0, a backfill bar, a clock
#: step). NFR1's target is under one second; an hour is generous enough that
#: nothing legitimate is discarded, and it catches the epoch-zero case (~55
#: years) and every negative. Out-of-band values are still logged, under a
#: *different* key — see :meth:`OrderEventObserver._log_submitted`.
MAX_PLAUSIBLE_LATENCY_NS = 3_600_000_000_000

NANOS_PER_SECOND = 1_000_000_000


class _BarAnchor(TypedDict):
    """What :meth:`OrderEventObserver.note_bar` keeps per instrument, and
    what both NFR1 latencies are measured from. ``close_ns`` is derived
    (``ts_event + interval``) because the adapter's ``ts_event`` is the open;
    ``arrival_ns`` is ``bar.ts_init`` verbatim.
    """

    close_ns: int
    arrival_ns: int
    bar_type: str


class _OrderAccumulator(TypedDict):
    """Per-``client_order_id`` state behind ``order.filled``'s ``cum_qty``.

    A ``TypedDict`` rather than a bare ``dict[str, Any]`` (review 2026-08-30):
    the arithmetic here is financial, and under ``Any`` both
    ``cum_qty + last_qty.as_decimal()`` and ``cum_qty >= order_qty`` typecheck
    against a ``Decimal``/``Quantity``/``float`` mix-up alike.

    ``trade_ids`` exists because Nautilus publishes a fill it has itself
    refused to apply — see :meth:`OrderEventObserver._log_filled`.
    """

    cum_qty: Decimal
    order_qty: Decimal | None
    trade_ids: set[str]


def _new_accumulator() -> _OrderAccumulator:
    return {"cum_qty": Decimal(0), "order_qty": None, "trade_ids": set()}


def _put_latency(
    fields: dict[str, Any], interval_ns: int, *, plausible: str, implausible: str
) -> None:
    """Record ``interval_ns`` in ms under ``plausible``, or under ``implausible``
    when it is outside :data:`MAX_PLAUSIBLE_LATENCY_NS` — never both, never
    neither.
    """
    key = plausible if 0 <= interval_ns <= MAX_PLAUSIBLE_LATENCY_NS else implausible
    fields[key] = interval_ns / 1_000_000


def install_order_path(strategy: Any, monitor: Any, log: Any) -> None:
    """Wrap ``strategy``'s order-creating methods with the suppression check.

    Must be called **before** ``Trader.add_strategy`` — the same ordering
    constraint ``StrategyGuard.wrap`` documents for the same reason: Nautilus
    binds these as plain instance attributes and nothing re-reads them later,
    so wrapping after materialisation but before registration is the only
    window in which every subsequent call — including the strategy's own
    internal ones — sees the wrapped version.

    Args:
        strategy: The materialised strategy. Duck-typed — this module never
            imports ``nautilus_trader``, matching ``live_strategy_guard.py``'s
            framework-free discipline.
        monitor: Anything with a ``submission_withheld`` bool property — in
            production a ``ConnectionMonitor``, in tests a stub.
        log: A structlog logger, ideally already bound to ``session_id``.
    """
    if getattr(strategy, _INSTALLED_MARKER, False):
        return
    for method_name in ORDER_CREATING_METHODS:
        base = getattr(strategy, method_name)
        setattr(strategy, method_name, _wrap(method_name, base, strategy, monitor, log))
    setattr(strategy, _INSTALLED_MARKER, True)


def _wrap(
    method_name: str, base: Callable[..., Any], strategy: Any, monitor: Any, log: Any
) -> Callable[..., Any]:
    """Build the instance-level replacement for one order-creating method."""

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            withheld = bool(monitor.submission_withheld)
            if withheld:
                _log_suppressed(log, method_name, strategy, args, kwargs)
        except Exception as exc:  # noqa: BLE001 - the wrapper's own logic must never raise
            log.error(
                "order.suppression_failed",
                method=method_name,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            return None
        if withheld:
            return None
        return base(*args, **kwargs)

    return wrapped


def _log_suppressed(
    log: Any, method_name: str, strategy: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    """Log what this specific call carries, and nothing it does not.

    Never invents a value: a wrapped ``submit_order`` has a real order object
    with a ``client_order_id``; a wrapped ``close_position`` has a position,
    not yet an order, so it never gets one.

    ``strategy_id`` is Task 3.1's fourth mandated field, restored by code
    review 2026-08-30. It is read from ``strategy.id`` **per call**, never
    captured at install time: ``Trader.add_strategy`` rewrites the id when it
    auto-assigns an ``order_id_tag`` (``trading/trader.py:406-412``) and
    :func:`install_order_path` runs before that rewrite, so an install-time
    capture would log the pre-registration id for the life of the session.
    """
    described = _describe(method_name, args, kwargs)
    strategy_id = getattr(strategy, "id", None)
    if strategy_id is not None:
        described["strategy_id"] = str(strategy_id)
    log.warning(SUPPRESSED_EVENT, method=method_name, **described)


def _describe(method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str]:
    described: dict[str, str] = {}
    if method_name == "submit_order":
        order = args[0] if args else kwargs.get("order")
        _add_instrument(described, order)
        _add_client_order_id(described, order)
    elif method_name == "submit_order_list":
        order_list = args[0] if args else kwargs.get("order_list")
        orders = getattr(order_list, "orders", None) or ()
        if orders:
            _add_instrument(described, orders[0])
    elif method_name == "close_position":
        position = args[0] if args else kwargs.get("position")
        _add_instrument(described, position)
    elif method_name == "close_all_positions":
        instrument_id = args[0] if args else kwargs.get("instrument_id")
        if instrument_id is not None:
            described["instrument_id"] = str(instrument_id)
    return described


def _add_instrument(described: dict[str, str], source: Any) -> None:
    instrument_id = getattr(source, "instrument_id", None)
    if instrument_id is not None:
        described["instrument_id"] = str(instrument_id)


def _add_client_order_id(described: dict[str, str], source: Any) -> None:
    client_order_id = getattr(source, "client_order_id", None)
    if client_order_id is not None:
        described["client_order_id"] = str(client_order_id)


class OrderEventObserver:
    """Nautilus's first order-event consumer (Story 3.2, AC #5/#6).

    Two message-bus handlers, wired by the runner in ``_phase_subscribe``
    directly after the existing ``BAR_TOPIC`` subscribe (Task 5):

    - :meth:`note_bar` — records ``instrument_id -> bar.ts_event``, purely to
      anchor NFR1's latency measurement. Independent of
      ``SessionSteadyState.note_bar``'s own liveness counter; this one exists
      for a different reason and neither reads the other.
    - :meth:`handle_order_event` — dispatches on event *type*, never merely on
      topic (``submit_order`` itself publishes the order's own
      ``OrderInitialized`` on the **same topic** first, measured Story 3.2
      Task 1.2), across the order's full lifecycle (Story 3.3):
      ``OrderSubmitted``, ``OrderAccepted``, ``OrderRejected``,
      ``OrderFilled``, ``OrderCanceled``, ``OrderExpired``, and
      ``OrderDenied`` are each logged; ``OrderInitialized`` and
      ``OrderUpdated`` are harvested silently (their ``quantity`` feeds the
      fill-completion accumulator below) and every other type is ignored — a
      closed set, not a default-log. A rejection is logged AND never raises
      (AC #3's rejection-tolerance guard).

    Both handlers contain every exception internally and never raise: a raise
    from a msgbus handler re-enters ``MessageBus.publish_c``, which has no
    ``try`` around ``sub.handler(msg)``, and ends at Nautilus's own silent
    ``os._exit(1)`` (the Story 2.7 lesson).

    **The fill-completion accumulator** (``_orders``, keyed by
    ``str(client_order_id)``): there is no ``OrderPartiallyFilled`` event in
    the installed wheel — a partial fill is an ordinary ``OrderFilled``, so
    ``cum_qty`` is derived by summing ``last_qty`` per order, and
    ``OrderInitialized.quantity`` is harvested (never logged) so a completed
    order's ``order.filled`` record can show ``cum_qty == order_qty`` —
    without it no event carries the order's total and a finished order is
    indistinguishable from one still working. ``OrderUpdated.quantity`` is
    harvested the same silent way, because an amended order's total changes
    and a stale ``order_qty`` makes a complete order read as still working.

    **Entries are never pruned** (policy change, code review 2026-08-30).
    They previously pruned on rejected/denied, on completion, and on
    canceled/expired-with-no-fills. Every one of those prunes destroyed state
    a later event still needed:

    - *Completion prune vs. de-duplication.* Nautilus publishes a fill it has
      itself **refused to apply** — ``ExecutionEngine._apply_event_to_order``
      catches the duplicate-``trade_id`` ``KeyError`` (and the FSM's
      ``InvalidStateTrigger``), logs, and returns, but the
      ``_msgbus.publish_c`` on ``events.order.{strategy_id}`` sits outside
      that guard and runs anyway (``execution/engine.pyx:1357-1369`` then
      ``:1174-1177``; the raise site is ``model/orders/base.pyx:1073``). So
      the framework's own duplicate-fill protection is invisible downstream
      and this observer must keep its own ``trade_ids``. Pruning a *completed*
      order threw that memory away — and a single-fill order redelivered once
      is the commonest shape of the problem, so the prune defeated the
      de-duplication in exactly the case it mattered most.
    - *Canceled/expired prune vs. the late fill.* The old rule retained an
      entry only when fills had already accumulated, to protect a ``cum_qty``
      counting a late fill alone. But an entry holds ``order_qty`` too, and a
      cancel usually arrives with **zero** prior fills — so the common form of
      the very race the rule was written for (a working order filling before
      the cancel reaches the venue) deleted the harvested ``order_qty``, and
      the late fill then re-seeded with no total to compare against.

    The cost is one small entry per ``client_order_id`` touched, living until
    session end — the same unpruned-dict shape as ``_last_bar``, and what
    residual (a) already disclosed for a subset. Still disclosed, still
    unsolved here: ``cum_qty`` and ``trade_ids`` reset across a process
    restart, so a redelivery spanning a restart is not detectable — Story
    3.4/Epic 4's working-order resume is when that matters.

    Args:
        log: A structlog logger already bound to ``session_id``. Contextvars
            reach asyncio tasks created after the bind but are **empty** on
            ``ThreadPoolExecutor`` threads (Task 4.4) — this class never
            relies on ambient context, only on what it is handed.
        traded_bar_types: The aggregations the session's strategies actually
            trade — each spec entry's ``bar_types[0]``, which is the one
            ``materialise_strategy`` hands the strategy. Only these anchor a
            latency. ``None`` (the default) anchors on every bar, preserving
            the original behaviour for a caller that does not know its own
            aggregations.

            Why it exists (code review 2026-08-30): the anchor map is keyed by
            *instrument*, but this observer is subscribed to the wildcard
            ``BAR_TOPIC``. ``StrategySpec.bar_types`` is a tuple and
            ``subscription_bar_types`` flattens across strategies, so one
            instrument can carry two aggregations — and whichever arrived last
            silently became the anchor, measuring NFR1 from a close the signal
            never saw. Keying the map on ``bar_type`` instead cannot work:
            ``OrderSubmitted`` carries only ``instrument_id``, so the order
            event alone cannot resolve which aggregation to look up.
            Restricting what may *become* an anchor closes it from the other
            side.
    """

    def __init__(self, log: Any, traded_bar_types: Any = None) -> None:
        self._log = log
        self._traded_bar_types: frozenset[str] | None = (
            None if traded_bar_types is None else frozenset(str(bt) for bt in traded_bar_types)
        )
        #: instrument_id -> the last traded bar's close/arrival instants
        self._last_bar: dict[str, _BarAnchor] = {}
        #: client_order_id -> accumulator; never pruned, see the class docstring
        self._orders: dict[str, _OrderAccumulator] = {}
        #: Class-name dispatch — a closed set, not a default-log (Task 2.6).
        self._dispatch: dict[str, Callable[[Any], None]] = {
            "OrderInitialized": self._harvest_initialized,
            "OrderUpdated": self._harvest_updated,
            "OrderSubmitted": self._log_submitted,
            "OrderAccepted": self._log_accepted,
            "OrderRejected": self._log_rejected,
            "OrderFilled": self._log_filled,
            "OrderCanceled": self._log_canceled,
            "OrderExpired": self._log_expired,
            "OrderDenied": self._log_denied,
        }

    def note_bar(self, bar: Any) -> None:
        """Record the venue close and arrival instants for this bar's instrument."""
        try:
            bar_type = str(bar.bar_type)
            if self._traded_bar_types is not None and bar_type not in self._traded_bar_types:
                return
            interval_ns = int(bar.bar_type.spec.timedelta.total_seconds() * NANOS_PER_SECOND)
            self._last_bar[str(bar.bar_type.instrument_id)] = {
                "close_ns": bar.ts_event + interval_ns,
                "arrival_ns": bar.ts_init,
                "bar_type": bar_type,
            }
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                "order.observer_failed",
                stage="note_bar",
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def handle_order_event(self, event: Any) -> None:
        """Handle one ``events.order*`` delivery.

        The failure record names the event type and the order (review
        2026-08-30). The dispatch went from one handler to nine behind this
        single ``except``, and ``stage`` + ``error_type`` alone cannot tell an
        operator *which* event type is broken: if an adapter's ``OrderFilled``
        lacks a field a builder reads, every fill in the session raises, no
        ``order.filled`` record is ever written, and the transcript is an
        undifferentiated stream of ``error_type=AttributeError``. Both fields
        are read through ``getattr`` so the diagnostic cannot itself raise on
        the malformed event that brought it here.
        """
        try:
            handler = self._dispatch.get(type(event).__name__)
            if handler is not None:
                handler(event)
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                "order.observer_failed",
                stage="handle_order_event",
                event_type=type(event).__name__,
                client_order_id=str(getattr(event, "client_order_id", None)),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def _log_submitted(self, event: Any) -> None:
        """Two latencies, one submission — see the module docstring's
        "Latency anchors". ``bar_close_to_submit_ms`` (venue close → submit)
        is the divergence window; ``bar_arrival_to_submit_ms`` (bar arrival →
        submit) is what NFR1's bound is judged on. Both absent, never
        fabricated, when no bar has been observed yet for this instrument.

        Every emitted latency names the ``bar_type`` it was measured from, and
        an interval outside :data:`MAX_PLAUSIBLE_LATENCY_NS` is recorded under
        ``implausible_latency_ms`` / ``implausible_arrival_latency_ms``
        instead — visible, but impossible to mistake for the number NFR1 is
        judged on (review 2026-08-30). Note the close subtraction crosses
        clock domains: the close is the venue's instant and ``event.ts_init``
        the local host's, so host/venue skew lands in that value whole. The
        arrival subtraction does not — both instants are this host's clock.
        """
        fields: dict[str, Any] = {
            "client_order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "ts_event": event.ts_event,
        }
        anchor = self._last_bar.get(str(event.instrument_id))
        if anchor is not None:
            fields["latency_anchor_bar_type"] = anchor["bar_type"]
            _put_latency(
                fields,
                event.ts_init - anchor["close_ns"],
                plausible="bar_close_to_submit_ms",
                implausible="implausible_latency_ms",
            )
            _put_latency(
                fields,
                event.ts_init - anchor["arrival_ns"],
                plausible="bar_arrival_to_submit_ms",
                implausible="implausible_arrival_latency_ms",
            )
        self._log.info(SUBMITTED_EVENT, **fields)

    def _harvest_initialized(self, event: Any) -> None:
        """Record ``quantity`` for this ``client_order_id`` and emit nothing
        — the ``:417-428`` silence pin (this is pre-submission, local, and
        ``order.submitted`` already marks lifecycle start). Feeds the
        fill-completion accumulator only; a reconciliation-sourced order
        never publishes this event, so its entry simply lacks ``order_qty``.
        """
        entry = self._orders.setdefault(str(event.client_order_id), _new_accumulator())
        entry["order_qty"] = event.quantity.as_decimal()

    def _harvest_updated(self, event: Any) -> None:
        """Refresh ``order_qty`` from an amended order, and emit nothing.

        Added by code review 2026-08-30. ``order_qty`` was harvested once from
        ``OrderInitialized`` and never refreshed, but ``OrderUpdated`` carries
        the order's *current* quantity (``model/events/order.pyx:4198-4200``)
        and is live-published from two sources: the IBKR adapter regenerates it
        from the ``openOrder`` callback whenever the total quantity, price, or
        trigger differs (``adapters/interactive_brokers/execution.py:968-978``),
        and reconciliation emits it whenever the venue's reported quantity
        differs from the order's (``live/execution_engine.py:1812-1829``, via
        ``_should_update`` at ``:1893-1895``). A stale total breaks completion
        detection both ways: a downsize leaves a complete order reading as
        still working, and an upsize trips completion early.

        Silent, on the :meth:`_harvest_initialized` precedent — an amendment is
        not one of AC #1's lifecycle states, and emitting one would add an
        eighth name to :data:`EMITTED_ORDER_EVENTS` and need an AR41 namespace
        ruling this story has no mandate to make.
        """
        entry = self._orders.setdefault(str(event.client_order_id), _new_accumulator())
        entry["order_qty"] = event.quantity.as_decimal()

    def _log_accepted(self, event: Any) -> None:
        """The acknowledgement identity (AC #5): the first moment a
        venue-side ``venue_order_id`` exists — ``OrderSubmitted``'s is
        hardcoded ``None``.
        """
        fields: dict[str, Any] = {
            "client_order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "strategy_id": str(event.strategy_id),
            "ts_event": event.ts_event,
            "venue_order_id": str(event.venue_order_id),
        }
        if event.reconciliation:
            fields["reconciliation"] = True
        self._log.info(ACCEPTED_EVENT, **fields)

    def _log_rejected(self, event: Any) -> None:
        """``venue_reason`` is ``str(event.reason)`` verbatim — never
        reworded, truncated, or classified (AC #3).

        ⚠️ Open conflict, raised by code review 2026-08-30 and deliberately
        **not** settled here. NFR26 is value-level, not field-level: *"any
        account identifier in it is masked to its last 3 characters"*, where
        "it" is a rendered message (``epics.md:549``, ``:650``). IBKR rejection
        text can embed the account code, so a verbatim ``venue_reason`` is a
        channel NFR26's wording covers and this module's anti-field scan — which
        checks *key names* — cannot see. AC #3's "never reworded, truncated, or
        classified" and NFR26 cannot both hold for such a reason. Kept verbatim
        because AC #3 is unambiguous and NFR26's own acceptance criteria are
        scoped to gate and account-verification messages this system renders,
        not to a venue's own text; ``mask_account``
        (``src/core/live_gate.py:117``) is a whole-value masker and no
        token-level scanner exists. Escalated for an epic-level ruling.
        """
        client_order_id = str(event.client_order_id)
        fields: dict[str, Any] = {
            "client_order_id": client_order_id,
            "instrument_id": str(event.instrument_id),
            "strategy_id": str(event.strategy_id),
            "ts_event": event.ts_event,
            "venue_reason": str(event.reason),
            "due_post_only": bool(event.due_post_only),
        }
        if event.reconciliation:
            fields["reconciliation"] = True
        self._log.warning(REJECTED_EVENT, **fields)

    def _log_filled(self, event: Any) -> None:
        """The AR41 partial-fill representation: no dedicated event exists,
        so ``cum_qty`` is derived by accumulating ``last_qty`` per order and
        emitted as a string — a raw ``Decimal`` reaches the JSON transcript
        as the literal ``"Decimal('30')"`` (the renderer's fallback),
        invisible to a component test that hands logs raw objects.
        ``position_id`` is included only when the venue assigned one.

        Three markers added by code review 2026-08-30, each making a record say
        what it previously left the reader to assume:

        - ``duplicate=True`` — this ``trade_id`` has already been counted, so
          ``cum_qty`` is unchanged and the fill is **not** accumulated. Nautilus
          publishes fills it has itself refused to apply (class docstring), and
          a silent drop would make the redelivery unobservable; NFR21 wants the
          evidence, not just the right total.
        - ``order_qty_unknown=True`` — no ``OrderInitialized``/``OrderUpdated``
          harvest was seen for this order, so ``cum_qty`` is partial knowledge.
          Reachable in a live session, not only across a restart: the runner
          starts the node (``live_session_runner.py:507``) and lets
          reconciliation run before the observer subscribes (``:563``), and a
          reconciliation-sourced order never publishes ``OrderInitialized`` at
          all (``live/execution_engine.py:1758-1762``). Without this marker such
          a record is byte-identical to a first fill on a fresh order.
        - ``over_fill=True`` — ``cum_qty`` exceeded ``order_qty``. The
          completion comparison is ``>=``, so an over-fill previously emitted a
          record shaped exactly like an exact completion.

        The accumulator is committed **after** a successful emit, not before.
        The mutation used to happen first, so a raise anywhere in the twelve
        field reads below left the counter advanced with no record accounting
        for it, and the next fill logged an inflated ``cum_qty``.
        """
        client_order_id = str(event.client_order_id)
        entry = self._orders.setdefault(client_order_id, _new_accumulator())
        trade_id = str(event.trade_id)
        duplicate = trade_id in entry["trade_ids"]
        order_qty = entry["order_qty"]
        cum_qty = entry["cum_qty"] if duplicate else entry["cum_qty"] + event.last_qty.as_decimal()
        fields: dict[str, Any] = {
            "client_order_id": client_order_id,
            "instrument_id": str(event.instrument_id),
            "strategy_id": str(event.strategy_id),
            "ts_event": event.ts_event,
            "fill_qty": str(event.last_qty),
            "cum_qty": str(cum_qty),
            "last_px": str(event.last_px),
            "commission": str(event.commission),
            "currency": str(event.currency),
            "trade_id": trade_id,
            "venue_order_id": str(event.venue_order_id),
        }
        if order_qty is not None:
            fields["order_qty"] = str(order_qty)
            if cum_qty > order_qty:
                fields["over_fill"] = True
        else:
            fields["order_qty_unknown"] = True
        if duplicate:
            fields["duplicate"] = True
        if event.position_id is not None:
            fields["position_id"] = str(event.position_id)
        if event.reconciliation:
            fields["reconciliation"] = True
        self._log.info(FILLED_EVENT, **fields)
        if not duplicate:
            entry["cum_qty"] = cum_qty
            entry["trade_ids"].add(trade_id)

    def _log_canceled(self, event: Any) -> None:
        self._log_terminal(CANCELED_EVENT, event)

    def _log_expired(self, event: Any) -> None:
        self._log_terminal(EXPIRED_EVENT, event)

    def _log_terminal(self, event_name: str, event: Any) -> None:
        """Shared shape for ``OrderCanceled``/``OrderExpired`` — both carry the
        same identifiers and neither carries a reason; ``venue_order_id`` is
        nullable on both — log what exists, never invent.

        No longer prunes the accumulator (review 2026-08-30). The old
        ``_prune_if_no_fills`` retained an entry only once fills had
        accumulated, which deleted the harvested ``order_qty`` in the *common*
        form of the fill-crosses-the-cancel-ack race — the one with no prior
        fill. See the class docstring.
        """
        client_order_id = str(event.client_order_id)
        fields: dict[str, Any] = {
            "client_order_id": client_order_id,
            "instrument_id": str(event.instrument_id),
            "strategy_id": str(event.strategy_id),
            "ts_event": event.ts_event,
        }
        if event.venue_order_id is not None:
            fields["venue_order_id"] = str(event.venue_order_id)
        if event.reconciliation:
            fields["reconciliation"] = True
        self._log.info(event_name, **fields)

    def _log_denied(self, event: Any) -> None:
        """``reason``, deliberately not ``venue_reason``: a denial is local
        (risk/exec engine), no venue was involved.
        """
        client_order_id = str(event.client_order_id)
        fields: dict[str, Any] = {
            "client_order_id": client_order_id,
            "instrument_id": str(event.instrument_id),
            "strategy_id": str(event.strategy_id),
            "ts_event": event.ts_event,
            "reason": str(event.reason),
        }
        if event.reconciliation:
            fields["reconciliation"] = True
        self._log.warning(DENIED_EVENT, **fields)
