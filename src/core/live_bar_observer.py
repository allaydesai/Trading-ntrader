"""The live bar observer: subscribe, log every bar, and notice a delayed feed.

Owns: the ``LiveBarObserver`` actor a paper session attaches to its node, the
paced dispatch of its subscriptions, and the lag test that decides a feed is no
longer real-time.

Does not own: which bar types a session runs (``live_market_data`` validates
them, the caller chooses them), the node's lifecycle (Epic 2's runner, AR38),
indicator warm-up from history (Epic 4), or any order path — Epic 1 has none.
This actor observes and reports; it never trades.

**Pacing (NFR15).** Subscriptions are dispatched in batches of
``requests_per_second``, one batch per second, through the actor's own clock. A
default 100-line budget against a 45/s rate is three batches, so this is not
hypothetical. ``RateLimiter`` (``src/services/ibkr_client.py:53-102``) is not
reused directly: its ``acquire()`` is a coroutine while ``on_start`` is
synchronous, and importing ``src.services.ibkr_client`` from ``src/core`` would
drag the Nautilus historical client in behind it. The rate itself still comes
from one typed field, ``IBKRSettings.ibkr_rate_limit``, via
:func:`build_bar_observer_config`.

**Delayed-feed detection (AC #1).** Nautilus reports a broker-side downgrade to
delayed data as a log warning and nothing else — IB error 10167 sits in
``WARNING_CODES`` (``adapters/interactive_brokers/client/error.py:36``) and
``process_market_data_type`` only escalates a debug to a warning
(``client/market_data.py:652-656``). Neither is published, raised, or exposed on
anything pollable. So the observer measures how late each delivered bar is.

Two facts about the adapter shape that measurement, and both were read out of
the 1.220.0 source rather than assumed:

1. **``ts_event`` is the bar's OPEN**, not its close —
   ``_ib_bar_to_ts_event``'s own docstring says "ts_event is set to the start of
   the bar period" (``client/market_data.py:1303-1310``). So ``ts_init -
   ts_event`` carries one whole bar interval that says nothing about lateness,
   and :func:`evaluate_bar_freshness` subtracts it. What is reported and
   compared is delay *past the close*.
2. **A published bar can be the previous one.** ``_process_bar_data`` keeps
   ``_bar_type_to_last_bar`` for the life of the subscription and, when a new
   bar arrives, publishes the *previous* one stamped with the wall clock at that
   moment (``client/market_data.py:1141, 1160, 1162-1166``). With
   ``use_rth=True`` nothing closes overnight, so the first bar of each session
   republishes yesterday's last bar and legitimately looks hours late.

Because of (1) the test is **interval-independent**: a real-time bar arrives at
about ``open + interval``, a delayed one at ``open + interval + ~900s``, and the
interval cancels either way — so the same tolerance applies to 1-minute and
1-hour bars alike. (An earlier version of this module claimed a blind spot above
~13-minute intervals; that came from leaving the interval in, and was wrong.)

Because of (2) a *single* late bar is a normal, healthy event, and shutting down
on it would kill a multi-day paper session every morning. So the guard requires
``delayed_data_consecutive_bars`` offenders in a row — a genuinely delayed feed
is late on every bar, an RTH re-open on exactly one.

The guard is silent when *no* bar is delivered at all; that case belongs to the
diagnostic probe and to Story 1.6, not here.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import Clock
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.data import Bar, BarType

from src.config import IBKRSettings
from src.core.live_market_data import (
    DEFAULT_DELAYED_DATA_GRACE_SECONDS,
    NANOS_PER_SECOND,
    LiveMarketDataError,
    resolve_live_bar_types,
)

logger = structlog.get_logger(__name__)

# How many consecutive late bars declare a delayed feed. Above 1 deliberately:
# the IB adapter republishes the previous bar with a fresh wall-clock ``ts_init``
# when a new one arrives, so the first bar after an RTH gap legitimately looks
# hours late. See LiveBarObserver._check_freshness.
DEFAULT_DELAYED_DATA_CONSECUTIVE_BARS = 3


class _SubscriptionPacer:
    """Releases a queue of subscriptions at a bounded rate through a Nautilus clock.

    Extracted from :class:`LiveBarObserver` because the timer-naming rule below
    is subtle enough to deserve its own home, and because a queue-and-clock unit
    is testable without an actor.

    **The timer name must be fresh every batch.** ``LiveClock`` removes a fired
    one-shot timer only *after* its callback returns, so re-registering the same
    name from inside that callback raises ``KeyError: 'name' <n> already
    contained in 'self.timer_names'`` — which Nautilus's timer machinery
    swallows. The visible effect is that batches 1 and 2 dispatch and everything
    after is stranded, with no exception and no log line.
    ``TestClock.advance_time()`` pops the timer *before* invoking the callback,
    so a shared name passes under test and fails in production; that divergence
    is what ``test_pacing_survives_a_clock_that_still_lists_the_fired_timer``
    pins. Fired names do not accumulate — verified against ``LiveClock``, whose
    ``timer_names`` is empty again once the callback returns.
    """

    def __init__(self, *, batch_size: int, timer_prefix: str) -> None:
        self._batch_size = batch_size
        self._timer_prefix = timer_prefix
        self._pending: list[BarType] = []
        self._batch_number = 0
        self.timer_name: str | None = None

    @property
    def pending(self) -> tuple[BarType, ...]:
        """What has not been dispatched yet."""
        return tuple(self._pending)

    def load(self, bar_types: Sequence[BarType]) -> None:
        """Replace the queue. Passing an empty sequence clears it."""
        self._pending = list(bar_types)

    def take_batch(self) -> tuple[BarType, ...]:
        """Remove and return the next batch."""
        batch = tuple(self._pending[: self._batch_size])
        del self._pending[: self._batch_size]
        return batch

    def arm_next(self, clock: Clock, callback: Callable[[object], None]) -> None:
        """Schedule the next batch a second out, or stand down if the queue is empty."""
        self.timer_name = None
        if not self._pending:
            return
        self._batch_number += 1
        self.timer_name = f"{self._timer_prefix}-{self._batch_number}"
        clock.set_time_alert_ns(
            name=self.timer_name,
            alert_time_ns=clock.timestamp_ns() + NANOS_PER_SECOND,
            callback=callback,
        )

    def cancel(self, clock: Clock) -> None:
        """Cancel the armed timer, if there is one this pacer still owns."""
        if self.timer_name is not None and self.timer_name in clock.timer_names:
            clock.cancel_timer(self.timer_name)
        self.timer_name = None


@dataclass(frozen=True)
class BarFreshnessVerdict:
    """A delivered bar reached the session later than real-time data can explain.

    Attributes:
        lag_ns: Raw ``ts_init - ts_event``. Since ``ts_event`` is the bar's
            *open*, this includes one whole bar interval.
        delay_past_close_ns: The part that actually indicates lateness — the lag
            with the bar interval taken out.
        threshold_ns: The configured grace ``delay_past_close_ns`` exceeded.
        reason: Operator-facing sentence, ready to log or hand to a shutdown.
    """

    lag_ns: int
    delay_past_close_ns: int
    threshold_ns: int
    reason: str


def evaluate_bar_freshness(bar: Bar, *, grace_ns: int) -> BarFreshnessVerdict | None:
    """Return a verdict when a bar arrived too late to be real-time, else None.

    Pure: takes a bar and a tolerance, returns a value. The bar interval is
    subtracted because ``ts_event`` is the bar's OPEN — see this module's
    "Delayed-feed detection", fact (1), which is also why the result is
    interval-independent. What is compared and reported is delay past the close.

    A negative lag — ``ts_init`` before ``ts_event`` — is clock nonsense rather
    than a downgrade, and is clamped rather than reported.
    """
    interval_ns = int(bar.bar_type.spec.timedelta.total_seconds() * NANOS_PER_SECOND)
    lag_ns = max(0, bar.ts_init - bar.ts_event)
    delay_past_close_ns = max(0, lag_ns - interval_ns)
    if delay_past_close_ns <= grace_ns:
        return None

    return BarFreshnessVerdict(
        lag_ns=lag_ns,
        delay_past_close_ns=delay_past_close_ns,
        threshold_ns=grace_ns,
        reason=(
            f"Delayed market data suspected on {bar.bar_type}: bar reached this session "
            f"{delay_past_close_ns / NANOS_PER_SECOND:.1f}s after its period ended, tolerance is "
            f"{grace_ns / NANOS_PER_SECOND:.1f}s. IBKR serves delayed data roughly 15 minutes "
            "behind and reports the downgrade only as a log warning (IB code 10167), so this "
            "session is stopping rather than acting on prices it would report as live."
        ),
    )


class LiveBarObserverConfig(ActorConfig, frozen=True):
    """Configuration for :class:`LiveBarObserver`.

    A msgspec Struct, so every field stays JSON-encodable — the node builder
    carries it through an ``ImportableActorConfig``.

    Attributes:
        bar_types: Bar-type strings to subscribe to, validated by
            ``resolve_live_bar_types`` before the actor ever starts.
        requests_per_second: Subscription requests dispatched per second.
            Build this through :func:`build_bar_observer_config` so the value
            comes from ``IBKRSettings.ibkr_rate_limit`` and there is one number
            in play (NFR15).
        delayed_data_grace_seconds: How long after a bar's period ends it may
            still arrive before the feed is suspected of being delayed.
        delayed_data_consecutive_bars: How many bars in a row must exceed that
            grace before the session is stopped. Above 1 by default because a
            single stale-looking bar is a normal, healthy event — see
            :meth:`LiveBarObserver._check_freshness`.
    """

    bar_types: tuple[str, ...] = ()
    requests_per_second: int = 45
    delayed_data_grace_seconds: float = DEFAULT_DELAYED_DATA_GRACE_SECONDS
    delayed_data_consecutive_bars: int = DEFAULT_DELAYED_DATA_CONSECUTIVE_BARS


def build_bar_observer_config(
    settings: IBKRSettings,
    bar_types: Sequence[str],
    *,
    delayed_data_grace_seconds: float = DEFAULT_DELAYED_DATA_GRACE_SECONDS,
) -> LiveBarObserverConfig:
    """Wire an observer config from typed settings.

    The single place ``requests_per_second`` is sourced, so the 45 req/s
    discipline that ``RateLimiter`` applies to historical fetching
    (``src/services/ibkr_client.py:53-102``) governs live subscription traffic
    from the same typed field rather than a second literal (NFR15).
    """
    if settings.ibkr_rate_limit < 1:
        raise LiveMarketDataError(
            f"IBKR_RATE_LIMIT is {settings.ibkr_rate_limit}, which would dispatch no "
            "subscription requests at all. Set a positive number of requests per second."
        )
    # Validated here as well as in the actor, because this is the layer that runs
    # before a socket opens; the actor is only constructed once the kernel builds.
    resolved = resolve_live_bar_types(bar_types)
    return LiveBarObserverConfig(
        bar_types=tuple(str(bar_type) for bar_type in resolved),
        requests_per_second=settings.ibkr_rate_limit,
        delayed_data_grace_seconds=_validate_grace_seconds(delayed_data_grace_seconds),
    )


def _validate_grace_seconds(grace_seconds: float) -> float:
    """Reject a grace that would make the guard fire on healthy data, or not at all.

    A negative grace gives a negative threshold, so a perfectly fresh bar
    (``delay_past_close == 0``) trips the guard and stops the session on its very
    first bar. ``inf``/``nan`` raise ``OverflowError``/``ValueError`` out of the
    actor's constructor instead — deep inside the kernel's actor instantiation,
    where neither the probe nor the CLI would recognise them.
    """
    if grace_seconds != grace_seconds or grace_seconds in (float("inf"), float("-inf")):
        raise LiveMarketDataError(
            f"delayed_data_grace_seconds is {grace_seconds!r}, which is not a usable "
            "tolerance. Set a finite, non-negative number of seconds."
        )
    if grace_seconds < 0:
        raise LiveMarketDataError(
            f"delayed_data_grace_seconds is {grace_seconds}, which would make even a bar "
            "delivered the instant its period ended look delayed and stop the session on "
            "its first bar. Set a non-negative number of seconds."
        )
    return grace_seconds


class LiveBarObserver(Actor):
    """Subscribes to live bars, logs each one, and notices a delayed feed.

    Epic 1 has no order path, so this actor observes and reports — it never
    trades. It exists so that "bars are arriving" is an assertable fact rather
    than something an operator infers from log noise (FR1), and so Story 1.7's
    ``ntrader live check`` has something to print.

    The reasoning behind the two mechanisms it carries — paced subscription
    dispatch and the delayed-feed lag guard, including that guard's blind spot —
    is in this module's docstring under "Pacing" and "Delayed-feed detection".
    Read those before changing either.
    """

    def __init__(self, config: LiveBarObserverConfig) -> None:
        super().__init__(config)
        self._bar_types: tuple[BarType, ...] = resolve_live_bar_types(config.bar_types)
        self._grace_ns = int(
            _validate_grace_seconds(config.delayed_data_grace_seconds) * NANOS_PER_SECOND
        )
        if config.requests_per_second < 1:
            # Not repaired to 1: the kernel rebuilds this config from the
            # serialised ImportableActorConfig dict, so a bad value can reach the
            # actor without passing build_bar_observer_config.
            raise LiveMarketDataError(
                f"requests_per_second is {config.requests_per_second}; a live session cannot "
                "dispatch a non-positive number of subscription requests per second."
            )
        self._required_offences = max(1, config.delayed_data_consecutive_bars)
        self._pacer = _SubscriptionPacer(
            batch_size=config.requests_per_second,
            timer_prefix=f"{type(self).__name__}-{self.id}-pace",
        )
        self._subscribed: list[BarType] = []
        self._counts: dict[str, int] = {}
        self._republished: dict[str, int] = {}
        self._last_ts_event: dict[str, int] = {}
        self._consecutive_offences = 0
        self.delayed_data_suspected = False

    # -- Introspection, so tests and the CLI need not scrape logs -------------

    @property
    def bar_types(self) -> tuple[BarType, ...]:
        """The validated subscription set."""
        return self._bar_types

    @property
    def total_received(self) -> int:
        """Bars delivered across every subscription since start."""
        return sum(self._counts.values())

    def received_count(self, bar_type: BarType | str) -> int:
        """Bars delivered for one subscription."""
        return self._counts.get(str(bar_type), 0)

    def counts_by_bar_type(self) -> dict[str, int]:
        """A copy of the per-subscription counters, republishes excluded."""
        return dict(self._counts)

    @property
    def total_republished(self) -> int:
        """Bars the adapter delivered more than once, and this actor discarded."""
        return sum(self._republished.values())

    # -- Lifecycle -----------------------------------------------------------

    def on_start(self) -> None:
        """Subscribe to every configured bar type, paced (AC #5)."""
        self._pacer.load(self._bar_types)
        self._dispatch_batch()

    def on_stop(self) -> None:
        """Stop pacing and unsubscribe. Idempotent — Nautilus may call it twice.

        Unsubscribes what was actually dispatched, not the whole configured set:
        a session stopped while pacing still had batches queued would otherwise
        cancel streams it never opened.
        """
        self._pacer.load(())
        self._cancel_pacing_timer()
        while self._subscribed:
            self.unsubscribe_bars(self._subscribed.pop())

    def on_reset(self) -> None:
        """Clear observation state so a reset actor does not carry stale counts."""
        self._pacer.load(())
        # Symmetric with on_stop: a reset that left a timer armed would dispatch
        # subscriptions into an actor that has just forgotten it made them.
        self._cancel_pacing_timer()
        self._subscribed.clear()
        self._counts.clear()
        self._republished.clear()
        self._last_ts_event.clear()
        self._consecutive_offences = 0
        self.delayed_data_suspected = False

    # -- Paced subscription dispatch -----------------------------------------

    def _dispatch_batch(self, _event: object = None) -> None:
        """Send one batch of subscriptions, then arm the next.

        Accepts and ignores an argument so it can serve directly as the clock's
        ``TimeEvent`` callback.
        """
        for bar_type in self._pacer.take_batch():
            # No `params={"start_ns": ...}`. `subscribe_historical_bars` already
            # requests ~300 bars with `keepUpToDate=True`, and `_process_bar_data`
            # drops those older than the subscription instant
            # (client/market_data.py:1156-1158). Passing an explicit start moves
            # that cutoff back and lets the whole backfill through — hundreds of
            # bars the freshness guard would each judge on its own. Warm-up from
            # history is Epic 4's story, and wants `request_bars`, not this.
            self.subscribe_bars(bar_type)
            self._subscribed.append(bar_type)
        self._pacer.arm_next(self.clock, self._dispatch_batch)

    def _cancel_pacing_timer(self) -> None:
        """Cancel the outstanding pacing timer, if this actor still has one armed."""
        self._pacer.cancel(self.clock)

    # -- Bar handling --------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        """Record and log a delivered bar, then check the feed is really live.

        A bar whose ``ts_event`` is not newer than the last seen for its bar type
        is a republish: recorded separately, logged at debug, and kept away from
        both the counter and the freshness guard. The adapter publishes a
        completed bar twice and re-delivers one after every RTH gap — see this
        module's "Delayed-feed detection", fact (2). Counting those would inflate
        the evidence this actor exists to provide and make a healthy session look
        delayed.
        """
        key = str(bar.bar_type)

        last_ts_event = self._last_ts_event.get(key)
        if last_ts_event is not None and bar.ts_event <= last_ts_event:
            self._republished[key] = self._republished.get(key, 0) + 1
            logger.debug(
                "live_bars.republished",
                instrument_id=str(bar.bar_type.instrument_id),
                bar_type=key,
                ts_event=_iso_utc(bar.ts_event),
            )
            return

        self._last_ts_event[key] = bar.ts_event
        self._counts[key] = self._counts.get(key, 0) + 1

        ts_event_iso = _iso_utc(bar.ts_event)
        self.log.info(
            f"live_bars.received instrument={bar.bar_type.instrument_id} "
            f"bar_type={key} ts_event={ts_event_iso} close={bar.close}",
        )
        logger.info(
            "live_bars.received",
            instrument_id=str(bar.bar_type.instrument_id),
            bar_type=key,
            ts_event=ts_event_iso,
            close=str(bar.close),
            count=self._counts[key],
        )

        self._check_freshness(bar)

    def _check_freshness(self, bar: Bar) -> None:
        """Stop the session when the feed has been late for several bars running.

        Consecutive rather than first-offender: a genuinely delayed feed is late
        on *every* bar, while a session boundary produces exactly one late-looking
        bar. Requiring several in a row separates them without a session calendar,
        and any bar inside tolerance resets the count. This is defence in depth —
        ``on_bar`` already discards the republish that causes that one — and it is
        what keeps a multi-day paper session alive across an RTH gap. See this
        module's "Delayed-feed detection".
        """
        if self.delayed_data_suspected:
            # Latched: a downgrade must not emit one shutdown command per bar.
            return

        verdict = evaluate_bar_freshness(bar, grace_ns=self._grace_ns)
        if verdict is None:
            self._consecutive_offences = 0
            return

        self._consecutive_offences += 1
        logger.warning(
            "live_bars.late",
            instrument_id=str(bar.bar_type.instrument_id),
            bar_type=str(bar.bar_type),
            delay_past_close_seconds=round(verdict.delay_past_close_ns / NANOS_PER_SECOND, 3),
            tolerance_seconds=round(verdict.threshold_ns / NANOS_PER_SECOND, 3),
            consecutive=self._consecutive_offences,
            required=self._required_offences,
        )
        if self._consecutive_offences < self._required_offences:
            return

        self.delayed_data_suspected = True
        reason = (
            f"{verdict.reason} Seen on {self._consecutive_offences} consecutive bars, so this "
            "is not a session-boundary republish."
        )
        self.log.error(f"live_bars.delayed_data_suspected {reason}")
        logger.error(
            "live_bars.delayed_data_suspected",
            instrument_id=str(bar.bar_type.instrument_id),
            bar_type=str(bar.bar_type),
            lag_seconds=round(verdict.lag_ns / NANOS_PER_SECOND, 3),
            delay_past_close_seconds=round(verdict.delay_past_close_ns / NANOS_PER_SECOND, 3),
            tolerance_seconds=round(verdict.threshold_ns / NANOS_PER_SECOND, 3),
            consecutive=self._consecutive_offences,
        )
        self.shutdown_system(reason)


def _iso_utc(ts_ns: int) -> str:
    """Render a Nautilus nanosecond timestamp as an ISO-8601 UTC string."""
    seconds, nanos = divmod(ts_ns, NANOS_PER_SECOND)
    moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{nanos:09d}Z"
