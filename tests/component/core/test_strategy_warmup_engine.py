"""Story 4.4 — both built-in strategies warm from history before they subscribe.

Component tier: a real ``MessageBus``/``Cache``/``DataEngine`` and a real
strategy, with ``nautilus_trader.test_kit.mocks.data.MockMarketDataClient``
standing in for the broker's history. The **non-live** ``DataEngine`` is
synchronous, so a request, its response and the strategy's callback all run
inside ``strategy.start()`` — which is what lets these tests assert ordering
without a loop. A ``LiveDataEngine`` would claim the Nautilus C logging
subsystem, which the autouse fixture below exists to catch.

Measured against ``nautilus-trader 1.220.0`` before these tests were written
(story Measured facts F1, F4, F5, F9):

- history reaches ``handle_bars`` → registered indicators → ``on_historical_data``,
  **never** ``on_bar``;
- the in-progress bar a live history ends with (``ts_init`` in the future) is
  trimmed by the engine's ``_check_bounds`` before the strategy sees it;
- a live bar reaches ``handle_bar``, which feeds registered indicators **before**
  calling ``on_bar`` — so a strategy that also fed them by hand would count
  every live bar twice.
"""

import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.config import StrategyConfig
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.indicators.base import Indicator
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import ClientId, TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.mocks.data import MockMarketDataClient
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy
from structlog.testing import capture_logs

from src.core.live_session_warmup import (
    _REQUEST_BARS_PARAMETERS,
    COMPLETED_EVENT,
    WarmupWatch,
)
from src.core.live_strategy_guard import FAILED_EVENT as GUARD_FAILED_STRATEGY_EVENT
from src.core.live_strategy_guard import StrategyGuard
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
from src.core.strategy_warmup import warmup_lookback

pytestmark = pytest.mark.component

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
BAR_TYPE = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
MINUTE = 60_000_000_000
#: A fixed clock reading (2023-11-14T22:13:20Z). History sits in the minutes
#: before it; live bars in the minutes after.
NOW = 1_700_000_000 * 1_000_000_000
T0_DT = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
FAST, SLOW = 3, 5


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-101``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a "
        "TradingNode."
    )


class _HistoryClient(MockMarketDataClient):
    """Answers every history request with ``bars``; records subscriptions.

    ``answers=False`` is the adapter's silent failure (story F2): the request
    is received and no response is ever sent, so the strategy's callback never
    fires.
    """

    def __init__(self, *args, answers: bool = True, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.answers = answers
        self.requests: list = []
        self.subscribed: list[BarType] = []

    def request_bars(self, request) -> None:
        self.requests.append(request)
        if self.answers:
            super().request_bars(request)

    def subscribe_bars(self, command) -> None:
        self.subscribed.append(command.bar_type)

    def unsubscribe_bars(self, command) -> None:
        pass


def _bar(ts: int, close: float, *, ts_init: int | None = None) -> Bar:
    price = Price.from_str(f"{close:.2f}")
    return Bar(
        BAR_TYPE,
        price,
        price,
        price,
        price,
        Quantity.from_int(1_000),
        ts,
        ts if ts_init is None else ts_init,
    )


def _history(closes: list[float]) -> list[Bar]:
    """``closes`` as the minutes immediately before ``NOW``, oldest first."""
    return [_bar(NOW - (len(closes) - i) * MINUTE, close) for i, close in enumerate(closes)]


class _Harness:
    """One strategy registered on a real bus, cache and synchronous engine."""

    def __init__(self, strategy, *, history: list[Bar], answers: bool = True, watch=None) -> None:
        self.clock = TestClock()
        self.clock.set_time(NOW)
        trader_id = TraderId("TESTER-000")
        self.msgbus = MessageBus(trader_id=trader_id, clock=self.clock)
        self.cache = Cache(database=None)
        self.cache.add_instrument(AAPL)
        portfolio = Portfolio(self.msgbus, self.cache, self.clock)
        self.engine = DataEngine(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.client = _HistoryClient(
            ClientId("NASDAQ"), self.msgbus, self.cache, self.clock, answers=answers
        )
        self.client.bars = list(history)
        self.engine.register_client(self.client)
        self.engine.start()
        self.strategy = strategy
        self.orders: list[str] = []
        # Record order intent without an execution engine: these are the only
        # two order-creating calls either built-in makes on a signal.
        strategy.submit_order = lambda order, *a, **k: self.orders.append(order.side_string())
        strategy.close_position = lambda position, *a, **k: self.orders.append("CLOSE")
        if watch is not None:
            # The runner's window: instrumented before registration.
            watch.instrument(strategy, spec_strategy_id="spec")
        strategy.register(trader_id, portfolio, self.msgbus, self.cache, self.clock)
        self.seen_at_bar: list[bool] = []
        base_on_bar = strategy.on_bar

        def on_bar(bar):
            self.seen_at_bar.append(strategy.indicators_initialized())
            return base_on_bar(bar)

        strategy.on_bar = on_bar

    def start(self) -> None:
        self.strategy.start()

    def live(self, minutes_after_now: int, close: float) -> None:
        self.clock.set_time(NOW + minutes_after_now * MINUTE)
        self.engine.process(_bar(NOW + minutes_after_now * MINUTE, close))


def _crossover() -> SMACrossover:
    return SMACrossover(
        SMAConfig(
            instrument_id=AAPL.id,
            bar_type=BAR_TYPE,
            fast_period=FAST,
            slow_period=SLOW,
        )
    )


def _momentum(*, allow_short: bool = False) -> SMAMomentum:
    return SMAMomentum(
        SMAMomentumConfig(
            instrument_id=AAPL.id,
            bar_type=BAR_TYPE,
            trade_size=Decimal("10"),
            order_id_tag="002",
            fast_period=FAST,
            slow_period=SLOW,
            allow_short=allow_short,
        )
    )


#: Flat history, then a jump: at the last history bar fast == slow (100), and
#: the first live bar lifts fast above slow — a golden cross ON that bar, which
#: only a strategy whose crossover baseline came from history can see.
FLAT = [100.0] * SLOW
JUMP = 130.0

#: A history that crosses up and back down inside itself. Warm-up must never
#: trade on it.
CROSSING = [100.0, 100.0, 100.0, 100.0, 100.0, 120.0, 130.0, 90.0, 80.0, 70.0]

STRATEGIES = [pytest.param(_crossover, id="sma_crossover"), pytest.param(_momentum, id="momentum")]


@pytest.mark.parametrize("make", STRATEGIES)
class TestIndicatorsAreWarmAtTheFirstLiveBar:
    """AC #4 — every registered indicator reports initialised at that bar."""

    def test_every_registered_indicator_is_initialised_when_the_first_bar_arrives(self, make):
        harness = _Harness(make(), history=_history(FLAT))
        harness.start()

        harness.live(1, 100.0)

        assert harness.seen_at_bar == [True]
        assert len(harness.strategy.registered_indicators) == 2

    def test_the_request_asks_for_the_computed_window(self, make):
        """AC #1c — the lookback is D-H's, from the strategy's longest period."""
        harness = _Harness(make(), history=_history(FLAT))
        harness.start()

        (request,) = harness.client.requests
        assert request.bar_type == BAR_TYPE
        assert NOW - request.start.value == pytest.approx(
            warmup_lookback(BAR_TYPE, SLOW).total_seconds() * 1_000_000_000, abs=MINUTE
        )

    def test_one_live_bar_advances_each_indicator_by_exactly_one(self, make):
        """F9: a strategy that still fed its indicators by hand in ``on_bar``
        would read ``SLOW + 2`` here — ``initialized`` alone cannot tell."""
        harness = _Harness(make(), history=_history(FLAT))
        harness.start()
        before = [indicator.count for indicator in harness.strategy.registered_indicators]

        harness.live(1, 100.0)

        after = [indicator.count for indicator in harness.strategy.registered_indicators]
        assert before == [SLOW, SLOW]
        assert after == [SLOW + 1, SLOW + 1]

    def test_the_in_progress_history_bar_never_reaches_the_indicators(self, make):
        """F4: a history ending "now" includes the forming bar; the engine drops it."""
        forming = _bar(NOW - 1, 999.0, ts_init=NOW + 30_000_000_000)
        harness = _Harness(make(), history=[*_history(FLAT), forming])
        harness.start()

        assert [i.count for i in harness.strategy.registered_indicators] == [SLOW, SLOW]


@pytest.mark.parametrize("make", STRATEGIES)
class TestTheLiveStreamStartsOnlyAfterHistory:
    """AC #1 — ``subscribe_bars`` runs from the history callback, and only there."""

    def test_nothing_is_subscribed_while_the_history_request_is_unanswered(self, make):
        harness = _Harness(make(), history=_history(FLAT), answers=False)
        harness.start()

        assert harness.client.requests, "no history request was issued"
        assert harness.client.subscribed == []
        assert harness.strategy.has_pending_requests()

    def test_the_answer_is_what_subscribes(self, make):
        harness = _Harness(make(), history=_history(FLAT))
        harness.start()

        assert harness.client.subscribed == [BAR_TYPE]
        assert not harness.strategy.has_pending_requests()

    def test_a_short_history_still_subscribes_with_cold_indicators(self, make):
        """D-C: the strategy still refuses to signal until its own
        ``initialized`` checks pass — today's behaviour, now after history."""
        harness = _Harness(make(), history=_history(FLAT[:2]))
        harness.start()

        assert harness.client.subscribed == [BAR_TYPE]
        assert not harness.strategy.indicators_initialized()


@pytest.mark.parametrize("make", STRATEGIES)
class TestTheCrossoverBaselineComesFromHistory:
    """D-F — warm means the first live bar can signal, and history never trades."""

    def test_the_first_live_bar_can_signal(self, make):
        harness = _Harness(make(), history=_history(FLAT))
        harness.start()

        harness.live(1, JUMP)

        assert harness.orders == ["BUY"]

    def test_a_crossover_inside_history_submits_nothing(self, make):
        harness = _Harness(make(), history=_history(CROSSING))
        harness.start()

        assert harness.orders == []
        assert harness.client.subscribed == [BAR_TYPE]

    def test_cold_indicators_record_no_baseline(self, make):
        """A backtest's history is empty (F1): nothing may be carried over,
        so the first bars behave exactly as they did before this story."""
        harness = _Harness(make(), history=[])
        harness.start()

        for minute, close in enumerate([100.0] * SLOW + [JUMP], start=1):
            harness.live(minute, close)

        # Five flat bars initialise both averages at 100 on the fifth; the
        # jump on the sixth is then a golden cross against that baseline.
        assert harness.orders == ["BUY"]


class TestMomentumIsARealMovingAverage:
    """D-G (ruled A) — the F8 defect, pinned so it cannot return."""

    def test_the_averages_track_a_window_not_a_running_sum(self):
        harness = _Harness(_momentum(), history=_history([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
        harness.start()

        fast, slow = harness.strategy.registered_indicators
        # The deque version measured 7.0 (the running sum / 3) for the last
        # three of 1..6; a moving average is 5.0.
        assert fast.value == pytest.approx(5.0)
        assert slow.value == pytest.approx(4.0)

    def test_a_death_cross_exits_a_long_position_only_when_long(self):
        """Long-only default: a death cross with no position submits nothing."""
        harness = _Harness(_momentum(), history=_history([130.0] * SLOW))
        harness.start()

        harness.live(1, 60.0)

        assert harness.orders == []


class _RaisingIndicator(Indicator):
    """A registered indicator that raises on every bar (story F14).

    A Python subclass: ``Actor._handle_indicators_for_bar`` dispatches
    ``indicator.handle_bar`` through Cython's ``cpdef`` override check, so this
    method is what runs — measured before this test was written.
    """

    def __init__(self) -> None:
        super().__init__([])

    def handle_bar(self, bar) -> None:
        raise RuntimeError("indicator raised on a live bar")


class _IndicatorStrategy(Strategy):
    """Registers one indicator for ``BAR_TYPE`` and subscribes — no warm-up."""

    def __init__(self, tag: str, indicator: Indicator) -> None:
        super().__init__(StrategyConfig(order_id_tag=tag))
        self.indicator = indicator
        self.bars: list[str] = []

    def on_start(self) -> None:
        self.register_indicator_for_bars(BAR_TYPE, self.indicator)
        self.subscribe_bars(BAR_TYPE)

    def on_bar(self, bar) -> None:
        self.bars.append(str(bar.close))


class TestARaisingRegisteredIndicatorIsContained:
    """AC #8 — the Story 2.7 debt routed here (``deferred-work.md:1852-1856``).

    ``Actor.handle_bar`` runs ``_handle_indicators_for_bar`` **before** its own
    ``try`` (``common/actor.pyx:3735-3744``), so a raising registered indicator
    escapes ``handle_bar`` entirely. Story 2.7 wrapped ``handle_bar`` rather
    than ``on_bar`` for exactly this reason; Story 4.4 is the first story that
    registers indicators, so this is the first time it can be proven rather
    than argued.
    """

    def _two_strategies(self, *, guarded: bool):
        clock = TestClock()
        clock.set_time(NOW)
        trader_id = TraderId("TESTER-000")
        bus = MessageBus(trader_id=trader_id, clock=clock)
        cache = Cache(database=None)
        cache.add_instrument(AAPL)
        portfolio = Portfolio(bus, cache, clock)
        engine = DataEngine(msgbus=bus, cache=cache, clock=clock)
        engine.start()
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=lambda: T0_DT)
        raiser = _IndicatorStrategy("001", _RaisingIndicator())
        sibling = _IndicatorStrategy("002", SimpleMovingAverage(2))
        for name, strategy in (("raiser", raiser), ("sibling", sibling)):
            strategy.register(trader_id, portfolio, bus, cache, clock)
            if guarded:
                guard.wrap(strategy, spec_strategy_id=name)
            strategy.start()
        return bus, guard, raiser, sibling

    def test_the_guard_contains_it_and_the_sibling_keeps_its_bar(self):
        bus, guard, raiser, sibling = self._two_strategies(guarded=True)

        with capture_logs() as logs:
            bus.publish(f"data.bars.{BAR_TYPE}", _bar(NOW + MINUTE, 101.0))

        failed = [entry for entry in logs if entry["event"] == GUARD_FAILED_STRATEGY_EVENT]
        assert [(e["spec_strategy_id"], e["handler"]) for e in failed] == [("raiser", "handle_bar")]
        assert raiser.state.name == "DEGRADED"
        assert raiser.bars == [], "the raise happens before on_bar, which must not run"
        assert sibling.bars == ["101.00"]
        assert sibling.indicator.count == 1

    def test_without_the_guard_the_raise_escapes_the_publish(self):
        """The anti-tautology twin: the same harness, unguarded, goes red."""
        bus, _guard, raiser, _sibling = self._two_strategies(guarded=False)

        with pytest.raises(RuntimeError, match="indicator raised"):
            bus.publish(f"data.bars.{BAR_TYPE}", _bar(NOW + MINUTE, 101.0))
        assert raiser.state.name == "RUNNING"


class _TimelineLog:
    """A structlog-shaped logger writing into the same list as the strategy's
    own actions, so one ordered record holds both."""

    def __init__(self, timeline: list) -> None:
        self._timeline = timeline

    def info(self, event: str, **fields) -> None:
        self._timeline.append((event, "info", fields))

    def warning(self, event: str, **fields) -> None:
        self._timeline.append((event, "warning", fields))

    def error(self, event: str, **fields) -> None:
        self._timeline.append((event, "error", fields))


def _watched(make, history: list[Bar]) -> tuple[_Harness, list]:
    """A real strategy, instrumented by a real ``WarmupWatch`` before it is
    registered (the runner's window), with ``subscribe_bars`` and ``on_bar``
    both writing into the watch's timeline."""
    timeline: list = []
    watch = WarmupWatch(log=_TimelineLog(timeline), deadline_seconds=75.0)
    strategy = make()
    base_subscribe = strategy.subscribe_bars

    def subscribe_bars(bar_type, *args, **kwargs):
        timeline.append(("subscribe_bars", None, {}))
        return base_subscribe(bar_type, *args, **kwargs)

    strategy.subscribe_bars = subscribe_bars
    harness = _Harness(strategy, history=history, watch=watch)
    base_on_bar = strategy.on_bar

    def on_bar(bar):
        timeline.append(("bar", strategy.indicators_initialized(), {}))
        return base_on_bar(bar)

    strategy.on_bar = on_bar
    return harness, timeline


@pytest.mark.parametrize("make", STRATEGIES)
class TestTheWatchedStartIsWarmBeforeTheFirstBar:
    """AC #4 in one chain (code review 2026-09-22): the runner's watch, a real
    ``DataEngine`` and history client, a real strategy, and a live bar — the
    captured order is ``warmup.completed`` → ``subscribe_bars`` → the bar."""

    def test_the_milestone_the_subscription_and_the_bar_arrive_in_that_order(self, make):
        harness, timeline = _watched(make, _history(FLAT))
        harness.start()

        harness.live(1, 100.0)

        assert [entry[0] for entry in timeline] == [COMPLETED_EVENT, "subscribe_bars", "bar"]
        completed_level, completed = timeline[0][1], timeline[0][2]
        assert completed_level == "info"
        assert completed["indicators_initialized"] is True
        assert completed["not_initialized"] == []
        assert timeline[2][1] is True, "an indicator was cold at the first live bar"

    def test_a_short_history_is_a_warning_and_still_subscribes(self, make):
        harness, timeline = _watched(make, _history(FLAT[:2]))
        harness.start()

        assert [entry[0] for entry in timeline] == [COMPLETED_EVENT, "subscribe_bars"]
        assert timeline[0][1] == "warning"
        assert timeline[0][2]["indicators_initialized"] is False
        assert len(timeline[0][2]["not_initialized"]) == 2


class TestTheWrapperBindsTheRealRequestBars:
    """The watch rebinds positional arguments by name (code review 2026-09-22):
    both halves pinned against the installed wheel, not a hand-written double."""

    def test_the_parameter_tuple_is_the_real_signature(self):
        parameters = tuple(inspect.signature(Actor.request_bars).parameters)

        assert parameters[0] == "self"
        assert parameters[1:] == _REQUEST_BARS_PARAMETERS

    def test_a_positional_callback_reaches_the_real_method_wrapped(self):
        timeline: list = []
        watch = WarmupWatch(log=_TimelineLog(timeline), deadline_seconds=75.0)
        harness = _Harness(_crossover(), history=_history(FLAT), watch=watch)
        loaded: list = []
        start = harness.clock.utc_now() - timedelta(days=5)

        harness.strategy.request_bars(BAR_TYPE, start, None, 0, None, loaded.append)

        assert [entry[0] for entry in timeline] == [COMPLETED_EVENT]
        assert len(loaded) == 1


def test_the_lookback_is_whole_days_for_minute_bars():
    """AC #6's broker-double half: never F3's pre-open seconds window."""
    lookback = warmup_lookback(BAR_TYPE, SLOW)

    assert lookback >= timedelta(days=1)
    assert lookback.seconds == 0 and lookback.microseconds == 0
