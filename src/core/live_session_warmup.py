"""Watch each strategy's history warm-up, and make a stalled one loud (Story 4.4).

Owns: :class:`WarmupWatch` — the runner-side instrumentation around a
strategy's own ``request_bars`` — the ``warmup.*`` records, and
:class:`WarmupFailedError`, which the runner's existing start-failure path
contains.

Does not own: the warm-up itself. Each strategy registers its indicators,
requests history and subscribes from its own ``on_start`` and history callback
(AR40 — mode-agnostic, no ``is_live``); the history window is
``src/core/strategy_warmup.py``'s. Nor the containment verbs: a failed warm-up
is raised into ``LiveSessionRunner._start_strategy``'s ``except``, which already
records it through ``StrategyGuard.record_start_failure`` and ``fault()``\\ s
the strategy — this module adds no second containment path.

**How AR39 and AR40 are both honoured** (story "Why", reason 1). AR39 fixes the
runner's phases — ``… reconcile → warmup → subscribe → trading`` — while AR40
puts warm-up in ``on_start``, which runs during ``trading``. So the ``warmup``
phase *arms* this watch and touches nothing on the node; the warming happens
inside each strategy's start, and the runner does not declare
``session.started`` until every started strategy's warm-up has settled —
completed, contained, or skipped because the strategy asked for no history.

**Why a deadline is load-bearing, measured against ``nautilus-trader 1.220.0``**
(F2). The IB adapter can finish a history request without ever sending a
response — contract not in its provider, empty history, a timeout resolved as
``[]``, or an identical ``(bar_type, end-second)`` request already in flight —
and then the strategy's callback never fires. A strategy whose
``subscribe_bars`` lives in that callback would sit silent for the whole run.
The deadline is the adapter's own timeout plus a margin, so the watch never
gives up on a request the adapter is still waiting for.

**The ordering guarantee is structural** (D-C). ``warmup.completed`` is logged
inside the wrapped callback, *before* the strategy's own callback runs — and it
is the strategy's callback that subscribes. No live bar can reach a strategy
that has not subscribed.

**Containment scope, stated rather than implied** (F6, D-E). A raise on the
history response path escapes into ``LiveDataEngine``'s response queue, whose
exception handler — with ``graceful_shutdown_on_exception=True`` — stops the
whole node. The wrapper contains a raise from the **strategy's own callback**.
It does **not** reach a registered indicator raising on a historical bar: that
happens in ``Actor.handle_bars``, before any callback, outside every boundary
this repo installs (``GUARDED_HANDLERS`` is ``handle_bar``/``handle_event``).

**The history/live seam** (Story 4.5, D-F — PO ruling B). A strategy joins the
bar stream only in its history callback, so a bar the history already held and
the adapter publishes afterwards (IB publishes bar X on X+1's first update)
would reach the registered indicators twice, and a bar published while the
request was in flight is never seen at all. The watch records where the
history ended — from an instance-level ``handle_bars`` wrapper, because
``Cache.add_bars`` keeps only history newer than a live bar already cached, so
``cache.bar()`` cannot say (measured, Story 4.5 Task 1.4) — then drops any live
bar at or before that point (``warmup.seam_duplicate_dropped``) and names every
bar the strategy missed (``warmup.seam_gap``, one WARNING each, read from the
cache that ``DataEngine`` fills whether or not the strategy has subscribed).
Missed bars are **never replayed**: a replayed bar could signal on a stale
price. Every piece of it is contained — it can decline to act, never raise.

**Framework-free by contract** — duck-typed strategies, standard library, the
D3 exit-outcome markers. No ``nautilus_trader``, no ``src.db``/``src.services``.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from src.core.exit_outcome import LiveCheckOutcome

#: Added to ``ibkr_request_timeout`` — the adapter answers a timed-out request
#: at that timeout, so the watch waits strictly longer before abandoning it.
WARMUP_DEADLINE_MARGIN_SECONDS = 15.0

#: How often a waiting ``settle`` re-reads its requests and the stop flag. A
#: stop is noticed within one interval.
WARMUP_POLL_SECONDS = 0.05

#: How often a waiting ``settle`` calls its ``on_poll`` hook (the runner's
#: connection re-observation). Once a second, not every poll: the hook reads
#: adapter state and logs its own failures, and twenty reads a second for up to
#: a deadline's worth of waiting would flood the log for no gain.
ON_POLL_INTERVAL_SECONDS = 1.0

COMPLETED_EVENT = "warmup.completed"
FAILED_EVENT = "warmup.failed"
SKIPPED_EVENT = "warmup.skipped"
DISCARDED_EVENT = "warmup.discarded"
#: Story 4.5 (D-F): a live bar the history already held, dropped.
SEAM_DUPLICATE_EVENT = "warmup.seam_duplicate_dropped"
#: Story 4.5 (D-F): a bar published while the history request was in flight,
#: which the strategy never saw — named, never replayed.
SEAM_GAP_EVENT = "warmup.seam_gap"

#: ``Actor.request_bars``'s parameters, in order (``common/actor.pyx:3078-3088``),
#: so a positional ``callback`` is found as reliably as a keyword one. Pinned
#: against ``inspect.signature(Actor.request_bars)`` by
#: ``test_strategy_warmup_engine.py``, so a wheel that reorders them goes red.
_REQUEST_BARS_PARAMETERS = (
    "bar_type",
    "start",
    "end",
    "limit",
    "client_id",
    "callback",
    "update_catalog",
    "params",
)
_INSTALLED_MARKER = "_ntrader_warmup_instrumented"


def warmup_deadline_seconds(settings: Any) -> float:
    """The longest a strategy's warm-up may take before it is contained."""
    return float(settings.ibkr_request_timeout) + WARMUP_DEADLINE_MARGIN_SECONDS


class WarmupFailedError(Exception):
    """A started strategy's history did not arrive, or its callback raised.

    Raised by :meth:`WarmupWatch.settle` into the runner's per-strategy start
    ``try``, which contains it; it reaches the CLI only as the cause of
    ``NoStrategyStartedError`` when no strategy survived. Marked per D3 rather
    than listed as unmarked (story D-J): its message is this module's own text.
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True


@dataclass(eq=False)
class _Request:
    """One warm-up request, keyed on the call rather than the returned id (F12:
    a synchronous engine runs the callback before ``request_bars`` returns).

    ``eq=False``: requests compare by identity. Two identical calls in one clock
    tick are equal by value, and removing "the" failed one by value could drop
    the other (code review 2026-09-22).
    """

    spec_strategy_id: str
    bar_type: str
    requested_from: str
    issued_at: float
    state: str = "pending"  # pending | completed | failed | abandoned


@dataclass(eq=False)
class _Seam:
    """Where one strategy's warm-up history ended, per bar type (D-F).

    ``armed`` until the strategy's warm-up **settles** — every request answered,
    failed or abandoned (code review 2026-09-28: it used to disarm at the first
    answer, so a second warm-up request recorded nothing). A history requested
    after that is not the seam and moves no watermark. ``delivered`` holds the
    live bars the strategy actually received while armed — one that subscribed
    before its history answered is not missing them (review).
    """

    spec_strategy_id: str
    watermarks: dict[str, int] = field(default_factory=dict)
    bar_types: dict[str, Any] = field(default_factory=dict)
    delivered: set[tuple[str, int]] = field(default_factory=set)
    armed: bool = True
    duplicate_logged: bool = False


class WarmupWatch:
    """Instrument strategies' history requests and wait for them to settle.

    Args:
        log: A structlog logger, already bound to ``session_id``.
        deadline_seconds: See :func:`warmup_deadline_seconds`.
        stop_requested: Read on every poll; ``True`` ends the wait early with no
            failure (a stop signal, a reclaim, a node whose run task ended).
        on_poll: Called at most once per :data:`ON_POLL_INTERVAL_SECONDS` while
            waiting — the runner re-observes the broker connection here, so a
            strategy already live is withheld on a disconnect (NFR10) while a
            later one warms. Must not raise.
        clock: Monotonic seconds; injected so a test never waits 75 s.
        sleeper: How :meth:`settle` waits between polls.
        poll_seconds: See :data:`WARMUP_POLL_SECONDS`.
    """

    def __init__(
        self,
        *,
        log: Any,
        deadline_seconds: float,
        stop_requested: Callable[[], bool] = lambda: False,
        on_poll: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_seconds: float = WARMUP_POLL_SECONDS,
    ) -> None:
        self._log = log
        self._deadline_seconds = deadline_seconds
        self._stop_requested = stop_requested
        self._on_poll = on_poll
        self._clock = clock
        self._sleeper = sleeper
        self._poll_seconds = poll_seconds
        self._requests: dict[str, list[_Request]] = {}
        self._settled: set[str] = set()
        self._seams: dict[str, _Seam] = {}

    def instrument(self, strategy: Any, *, spec_strategy_id: str) -> None:
        """Wrap ``strategy.request_bars`` — call **before** ``add_strategy``.

        The ``install_order_path`` window: the strategy's Python ``on_start``
        reads ``self.request_bars`` as an instance attribute, so replacing it
        after materialisation and before start is what every warm-up request
        sees. Idempotent. Once the strategy has settled, the wrapper passes
        every call straight through — a mid-session request is not a warm-up —
        and so it does for a call the real signature would refuse anyway.
        Also guards the history/live seam (Story 4.5, D-F): see the module
        docstring; installed here, after the strategy guard's own
        ``handle_bar`` wrapper, so the seam filter sits outside it.
        """
        if getattr(strategy, _INSTALLED_MARKER, False):
            return
        base = strategy.request_bars
        requests = self._requests.setdefault(spec_strategy_id, [])
        seam = self._seams[spec_strategy_id] = _install_seam(self._log, strategy, spec_strategy_id)

        def request_bars(*args: Any, **kwargs: Any) -> Any:
            if spec_strategy_id in self._settled or len(args) > len(_REQUEST_BARS_PARAMETERS):
                return base(*args, **kwargs)
            bound: dict[str, Any] = dict(zip(_REQUEST_BARS_PARAMETERS, args, strict=False))
            bound.update(kwargs)
            request = _Request(
                spec_strategy_id=spec_strategy_id,
                bar_type=str(bound.get("bar_type")),
                requested_from=_iso(bound.get("start")),
                issued_at=self._clock(),
            )
            requests.append(request)
            bound["callback"] = _wrap_callback(
                self._log, self._clock, strategy, request, bound.get("callback"), seam
            )
            try:
                return base(**bound)
            except BaseException:
                requests.remove(request)  # `on_start`'s own failure, not a stall
                raise

        setattr(strategy, "request_bars", request_bars)
        setattr(strategy, _INSTALLED_MARKER, True)

    def pending(self, spec_strategy_id: str) -> int:
        """How many of a strategy's warm-up requests have not been answered."""
        return sum(r.state == "pending" for r in self._requests.get(spec_strategy_id, []))

    def settle_blocking(self, loop: Any, spec_strategy_id: str) -> bool:
        """Run ``loop`` until :meth:`settle` returns — the runner's entry point.

        The runner's ``trading`` phase is synchronous and the history response
        arrives on the loop, the ``_phase_node_connect`` pattern.
        """
        return bool(loop.run_until_complete(self.settle(spec_strategy_id)))

    async def settle(
        self, spec_strategy_id: str, *, stop_requested: Callable[[], bool] | None = None
    ) -> bool:
        """Wait until every warm-up request of one strategy has settled.

        Returns:
            ``True`` once each request completed (or none was issued —
            ``warmup.skipped``); ``False`` when a stop was requested first, in
            which case nothing is logged as failed. Either way, and on a raise,
            every request still pending is abandoned on the way out, so no late
            answer can subscribe the strategy afterwards.

        Raises:
            WarmupFailedError: A request passed the deadline unanswered, or the
                strategy's own history callback raised.
        """
        stopping = stop_requested or self._stop_requested
        requests = self._requests.get(spec_strategy_id, [])
        deadline, polled_at = self._deadline_seconds, self._clock()
        try:
            if not requests:
                self._log.info(SKIPPED_EVENT, strategy_id=spec_strategy_id)
                return True
            while True:
                if any(r.state == "failed" for r in requests):
                    raise _failure(spec_strategy_id, "its history callback raised", deadline)
                if all(r.state == "completed" for r in requests):
                    return True
                if stopping():
                    return False
                if self._expire(requests):
                    raise _failure(spec_strategy_id, "no history arrived in time", deadline)
                if (
                    self._on_poll is not None
                    and self._clock() - polled_at >= ON_POLL_INTERVAL_SECONDS
                ):
                    polled_at = self._clock()
                    self._on_poll()
                await self._sleeper(self._poll_seconds)
        finally:
            self._settled.add(spec_strategy_id)
            _abandon(requests)
            _disarm(self._seams.get(spec_strategy_id))

    def _expire(self, requests: list[_Request]) -> bool:
        """Abandon every request past the deadline, one ``warmup.failed`` each."""
        now = self._clock()
        expired = [
            r
            for r in requests
            if r.state == "pending" and now - r.issued_at >= self._deadline_seconds
        ]
        for request in expired:
            request.state = "abandoned"
            self._log.error(
                FAILED_EVENT,
                strategy_id=request.spec_strategy_id,
                bar_type=request.bar_type,
                reason="no_response",
                waited_seconds=round(now - request.issued_at, 1),
            )
        return bool(expired)


def _wrap_callback(
    log: Any,
    clock: Callable[[], float],
    strategy: Any,
    request: _Request,
    inner: Any,
    seam: _Seam | None = None,
) -> Callable[[Any], None]:
    """The callback Nautilus calls once the history has fed the indicators.

    Logs ``warmup.completed`` **before** the strategy's own callback, which is
    the one that subscribes (D-C) — so when that callback then raises, the
    record pair is ``warmup.completed`` (the history arrived) followed by
    ``warmup.failed reason=callback_raised``. A response for a request that is
    no longer pending — abandoned at the deadline, by a stop, or by a settle
    that failed on a sibling request — or for a strategy the runner has already
    faulted (``on_start`` raised after requesting) is discarded without running
    the strategy's callback, so a late answer can never subscribe a contained
    strategy. Everything, the logging included, sits inside the ``try``: a
    raise here would reach ``LiveDataEngine``'s response queue and stop the
    whole node (F6).
    """

    def callback(request_id: Any) -> None:
        try:
            if request.state != "pending" or getattr(strategy, "is_faulted", False):
                request.state = "abandoned" if request.state == "pending" else request.state
                log.info(
                    DISCARDED_EVENT,
                    strategy_id=request.spec_strategy_id,
                    bar_type=request.bar_type,
                )
                return
            _log_completed(log, clock, strategy, request)
            request.state = "completed"
            if seam is not None:
                _report_gap(log, strategy, seam, request.bar_type)
            if inner is not None:
                inner(request_id)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except BaseException as exc:  # noqa: BLE001 - F6: never into the response queue
            request.state = "failed"
            log.error(
                FAILED_EVENT,
                strategy_id=request.spec_strategy_id,
                bar_type=request.bar_type,
                reason="callback_raised",
                error_type=type(exc).__name__,
            )

    return callback


def _log_completed(log: Any, clock: Callable[[], float], strategy: Any, request: _Request) -> None:
    """``warmup.completed`` — INFO when warm (or nothing is registered to be
    warm), WARNING when the history fell short of an indicator's period."""
    try:
        registered = list(strategy.registered_indicators)
        cold = [repr(i) for i in registered if not i.initialized]
        initialized: bool | None = bool(strategy.indicators_initialized()) if registered else None
    except Exception:  # noqa: BLE001 - reading state must never cost the warm-up
        cold, initialized = [], None
    emit = log.warning if initialized is False else log.info
    emit(
        COMPLETED_EVENT,
        strategy_id=request.spec_strategy_id,
        bar_type=request.bar_type,
        requested_from=request.requested_from,
        elapsed_ms=round((clock() - request.issued_at) * 1000, 1),
        indicators_initialized=initialized,
        not_initialized=cold,
    )


def _install_seam(log: Any, strategy: Any, spec_strategy_id: str) -> _Seam:
    """Wrap ``handle_bars`` (to learn where the history ended) and ``handle_bar``
    (to drop a live bar the history already held) on the instance (D-F).

    ``subscribe_bars`` binds ``self.handle_bar`` when the strategy's callback
    runs, and ``Actor``'s response path calls ``self.handle_bars`` — both honour
    an instance attribute (measured, Story 4.5 Task 1.4). A strategy without
    that surface (a unit-test stub) is left alone.
    """
    seam = _Seam(spec_strategy_id)
    base_bars = getattr(strategy, "handle_bars", None)
    base_bar = getattr(strategy, "handle_bar", None)
    if base_bars is None or base_bar is None:
        return seam

    def handle_bars(bars: Any) -> Any:
        _note_history(seam, bars)
        return base_bars(bars)

    def handle_bar(bar: Any) -> Any:
        if _is_seam_duplicate(log, seam, bar):
            return None
        return base_bar(bar)

    setattr(strategy, "handle_bars", handle_bars)
    setattr(strategy, "handle_bar", handle_bar)
    return seam


def _note_history(seam: _Seam, bars: Any) -> None:
    """Record the last warm-up history bar per bar type. Never raises."""
    if not seam.armed:
        return
    try:
        for bar in bars:
            key = str(bar.bar_type)
            seam.watermarks[key] = max(seam.watermarks.get(key, bar.ts_event), bar.ts_event)
            seam.bar_types[key] = bar.bar_type
    except Exception:  # noqa: BLE001 - bookkeeping must never cost the history
        pass


def _is_seam_duplicate(log: Any, seam: _Seam, bar: Any) -> bool:
    """Whether ``bar`` is at or before the history's end — dropped, and said so
    once per strategy (D-F (B)).

    A bar that passes is noted as delivered while the seam is armed. Any
    failure of our own reads as "not a duplicate": the bar is delivered, exactly
    as before this story, and nothing raises into the message bus.
    """
    try:
        key = str(bar.bar_type)
        watermark = seam.watermarks.get(key)
        if watermark is None or bar.ts_event > watermark:
            if seam.armed:
                seam.delivered.add((key, bar.ts_event))
            return False
        if seam.duplicate_logged:
            return True
        seam.duplicate_logged = True
        fields = _seam_fields(seam, key, bar.ts_event, watermark)
    except Exception:  # noqa: BLE001 - a msgbus handler must never raise
        return False
    _emit(log, "info", SEAM_DUPLICATE_EVENT, fields)
    return True


def _report_gap(log: Any, strategy: Any, seam: _Seam, bar_type: str) -> None:
    """One ``warmup.seam_gap`` per live bar of ``bar_type`` — the bar type whose
    history just answered — published after the history ended and never
    delivered to the strategy (``DataEngine`` caches it whether or not the
    strategy has subscribed). Named, never replayed. Never raises."""
    try:
        watermark = seam.watermarks.get(bar_type)
        if watermark is None:
            return
        missed = sorted(
            bar.ts_event
            for bar in strategy.cache.bars(seam.bar_types[bar_type])
            if bar.ts_event > watermark and (bar_type, bar.ts_event) not in seam.delivered
        )
        records = [_seam_fields(seam, bar_type, ts_event, watermark) for ts_event in missed]
    except Exception:  # noqa: BLE001 - reading the cache must never cost the warm-up
        return
    for fields in records:
        _emit(log, "warning", SEAM_GAP_EVENT, fields)


def _disarm(seam: _Seam | None) -> None:
    """The warm-up has settled: no later history moves a watermark."""
    if seam is not None:
        seam.armed = False


def _seam_fields(seam: _Seam, bar_type: str, ts_event: int, watermark: int) -> dict[str, Any]:
    return {
        "strategy_id": seam.spec_strategy_id,
        "bar_type": bar_type,
        "ts_event": _ns_iso(ts_event),
        "last_history_ts_event": _ns_iso(watermark),
    }


def _emit(log: Any, level: str, event: str, fields: dict[str, Any]) -> None:
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - a diagnostic must never change the outcome
        pass


def _ns_iso(ns: int) -> str:
    """UNIX nanoseconds as an ISO UTC timestamp; exact to the microsecond."""
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=ns // 1_000)).isoformat()


def _abandon(requests: list[_Request]) -> None:
    """The wait is over: any answer still in flight must not subscribe."""
    for request in requests:
        if request.state == "pending":
            request.state = "abandoned"


def _failure(spec_strategy_id: str, why: str, deadline_seconds: float) -> WarmupFailedError:
    return WarmupFailedError(
        f"Strategy {spec_strategy_id!r} did not finish warming its indicators from "
        f"history: {why} (deadline {deadline_seconds:.0f}s). It will not trade this "
        "run; see the `warmup.failed` record for the bar type."
    )


def _iso(value: Any) -> str:
    """A request's start as ISO text, whatever datetime-like type carried it."""
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)
