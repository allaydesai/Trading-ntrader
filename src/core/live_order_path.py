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
module only *consults* it), or anything about a fill, a rejection or a
lifecycle beyond ``OrderSubmitted`` (Story 3.3).

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

**Latency anchor**: the venue bar close (``bar.ts_event``), not handler entry
— NFR1 measures the live-vs-backtest divergence window, which includes
delivery lag. Known hazards, stated rather than hidden: ``live_bars.received``
appears twice per bar (Nautilus C logger + structlog), and the first bar
after subscribe can be a backfill bar (P3) — a live-transcript reader must
read latency from steady-state bars, not the first one. ``OrderSubmitted`` is
generated by the **execution client** (the adapter), not by
``Strategy.submit_order`` itself, which only publishes the order's own
``init_event_c()`` (``OrderInitialized``) on the same topic first (measured,
Task 1.2) — so this module filters on event *type*, never merely on topic.
"""

from collections.abc import Callable
from typing import Any

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
#: this module emits (AR36).
SUPPRESSED_EVENT = "order.suppressed"
SUBMITTED_EVENT = "order.submitted"

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

#: Beyond this, a bar-close-to-submit interval is not a measurement — it is a
#: bad anchor (an unset `ts_event` of 0, a backfill bar, a clock step). NFR1's
#: target is under one second; an hour is generous enough that nothing
#: legitimate is discarded, and it catches the epoch-zero case (~55 years) and
#: every negative. Out-of-band values are still logged, under a *different*
#: key — see :meth:`OrderEventObserver._log_submitted`.
MAX_PLAUSIBLE_LATENCY_NS = 3_600_000_000_000


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
    - :meth:`handle_order_event` — ignores anything but ``OrderSubmitted``.
      Story 3.3 owns the rest of the order lifecycle; this is also what makes
      an ``OrderRejected`` delivered today contained rather than crashing
      (AC #3's rejection-tolerance guard). ``submit_order`` itself publishes
      the order's own ``OrderInitialized`` on the **same topic** first
      (measured, Task 1.2) — filtering is on event *type*, never merely on
      topic.

    Both handlers contain every exception internally and never raise: a raise
    from a msgbus handler re-enters ``MessageBus.publish_c``, which has no
    ``try`` around ``sub.handler(msg)``, and ends at Nautilus's own silent
    ``os._exit(1)`` (the Story 2.7 lesson).

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
        #: instrument_id -> (venue close instant, the bar type it came from)
        self._last_bar: dict[str, tuple[int, str]] = {}

    def note_bar(self, bar: Any) -> None:
        """Record the venue close instant for this bar's instrument."""
        try:
            bar_type = str(bar.bar_type)
            if self._traded_bar_types is not None and bar_type not in self._traded_bar_types:
                return
            self._last_bar[str(bar.bar_type.instrument_id)] = (bar.ts_event, bar_type)
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                "order.observer_failed",
                stage="note_bar",
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def handle_order_event(self, event: Any) -> None:
        """Handle one ``events.order*`` delivery."""
        try:
            if type(event).__name__ != "OrderSubmitted":
                return
            self._log_submitted(event)
        except Exception as exc:  # noqa: BLE001 - a msgbus handler must never raise
            self._log.error(
                "order.observer_failed",
                stage="handle_order_event",
                error_type=type(exc).__name__,
                exc_info=True,
            )

    def _log_submitted(self, event: Any) -> None:
        """The latency anchor is the venue bar close (``bar.ts_event``), not
        handler entry — NFR1 measures the live-vs-backtest divergence window,
        which includes delivery lag. Absent, never fabricated, when no bar
        has been observed yet for this instrument.

        Every emitted latency names the ``bar_type`` it was measured from, and
        an interval outside :data:`MAX_PLAUSIBLE_LATENCY_NS` is recorded under
        ``implausible_latency_ms`` instead — visible, but impossible to
        mistake for the number NFR1 is judged on (review 2026-08-30). Note the
        subtraction crosses clock domains: ``bar.ts_event`` is the venue's
        instant and ``event.ts_init`` the local host's, so host/venue skew
        lands in the value whole. That is why the anchor is logged beside it.
        """
        fields: dict[str, Any] = {
            "client_order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
        }
        anchor = self._last_bar.get(str(event.instrument_id))
        if anchor is not None:
            bar_ts_event, bar_type = anchor
            interval_ns = event.ts_init - bar_ts_event
            fields["latency_anchor_bar_type"] = bar_type
            key = (
                "bar_close_to_submit_ms"
                if 0 <= interval_ns <= MAX_PLAUSIBLE_LATENCY_NS
                else "implausible_latency_ms"
            )
            fields[key] = interval_ns / 1_000_000
        self._log.info(SUBMITTED_EVENT, **fields)
