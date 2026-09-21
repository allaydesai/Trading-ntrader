"""Component tests for the order path's wiring into ``LiveSessionRunner``
(Story 3.2, Tasks 2.3 and 5).

Component tier: drives a ``TestLiveNode`` double, never a real ``TradingNode``
(see ``test_session_runner_phases.py``'s module docstring for why). Story
3.1's review finding drives this file's existence: a wiring claim pinned only
by tests that call the helper directly survives the call site being deleted.
Every test here constructs a real ``LiveSessionRunner`` and proves the runner
itself reaches the call site under test.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from nautilus_trader.common.component import is_logging_initialized

from src.config import IBKRSettings
from src.core import live_session_runner
from src.core.live_connection_monitor import ConnectionState, ConnectionStatus
from src.core.live_gate import GateDecision, GateMode
from src.core.live_order_path import ORDER_EVENTS_TOPIC
from src.core.live_order_rejections import RejectionTally
from src.core.live_session_runner import LiveSessionRunner
from src.core.live_trade_recorder import POSITION_EVENTS_TOPIC, TradeRecorder
from src.models.session import SessionSpec, StrategySpec
from tests.component.doubles import TestLiveNode

pytestmark = pytest.mark.component

SESSION_ID = UUID("55555555-5555-5555-5555-555555555555")
STARTED_AT = datetime(2026, 8, 19, 14, 30, 0, tzinfo=timezone.utc)
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

UP = ConnectionStatus(connected=True, detail="socket up, client ready")
DOWN = ConnectionStatus(connected=False, detail="socket down")


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_session_runner_phases.py:61-78``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the runner's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in (
        "IBKR_RATE_LIMIT",
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IB_MAX_CONNECTION_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings() -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode="paper",
        tws_account="DU4076626",
        ntrader_real_money_account="",
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
    )


def _spec() -> SessionSpec:
    return SessionSpec(
        strategies=(
            StrategySpec(strategy_id="sma_crossover", parameters={}, bar_types=(AAPL_1MIN,)),
        )
    )


async def _permitting_verifier(node, settings, **kwargs) -> GateDecision:
    return GateDecision(permitted=True, mode=GateMode.PAPER)


class FakeClock:
    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SpyRecord:
    """Mirrors ``test_session_runner_phases.SpyRecord`` for this file's needs."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: list[str] = []
        #: Story 3.7's fourth port method.
        self.rejections: list[dict] = []

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")

    def record_strategy_failure(
        self,
        *,
        strategy_id: str,
        spec_strategy_id: str,
        error_type: str,
        handler: str,
        at: datetime,
        detail: str | None = None,
        all_failed: bool = False,
    ) -> None:
        self.calls.append("record_strategy_failure")
        self.failures.append(spec_strategy_id)

    def record_order_rejections(
        self,
        *,
        rejected: int,
        denied: int,
        consecutive: int,
        first_at: datetime,
        last_at: datetime,
        last_kind: str,
        last_client_order_id: str,
        last_instrument_id: str,
        last_strategy_id: str,
        last_reason: str,
        last_reconciliation: bool,
    ) -> None:
        """Story 3.7's fourth port method. Duck-typed here, but kept complete so
        the double stays an honest ``SessionRecordPort`` rather than one that
        happens to satisfy the calls this file makes today.
        """
        self.calls.append("record_order_rejections")
        self.rejections.append({"rejected": rejected, "consecutive": consecutive})


async def _never_sleeps(seconds: float) -> None:
    import asyncio

    await asyncio.Event().wait()


def _runner(
    node: TestLiveNode, *, record=None, connection_reader=None, **overrides
) -> LiveSessionRunner:
    def _factory(settings_arg, **kwargs):
        return node

    options = {
        "session_id": SESSION_ID,
        "spec": _spec(),
        "record": record if record is not None else SpyRecord(),
        "started_at": STARTED_AT,
        "connect_timeout": 2.0,
        "node_factory": _factory,
        "account_verifier": _permitting_verifier,
        "client_builder": lambda *a, **k: None,
        "sleeper": _never_sleeps,
    }
    if connection_reader is not None:
        options["connection_reader"] = connection_reader
    options.update(overrides)
    return LiveSessionRunner(_settings(), **options)


class TestConnectionObservedBeforeTrading:
    """Task 2.3 — ``AWAITING_CONNECTION`` must never overlap the ``trading``
    phase, so the order-path suppression predicate is never withheld purely
    because nothing has observed the connection yet.
    """

    def test_the_monitor_is_no_longer_awaiting_connection_when_trading_starts(self, monkeypatch):
        observed_state_at_trading = []
        original_trading = LiveSessionRunner._phase_trading

        def _spy_trading(self):
            assert self._monitor is not None
            observed_state_at_trading.append(self._monitor.state)
            return original_trading(self)

        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _spy_trading)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=lambda settings: UP)

        runner.run()

        assert observed_state_at_trading == [ConnectionState.RECOVERING]

    def test_submission_is_permitted_when_trading_starts_on_a_healthy_connection(self, monkeypatch):
        """Review fix, 2026-08-30 — the positive half of this class's own claim.

        Every other test here asserts ``submission_withheld is True`` on a
        failure path; nothing asserted it is ``False`` on the happy path. So a
        change to the staleness window, or to when ``observe()`` stamps its
        reading, would suppress every order in every session with this whole
        suite green. Asserted *at trading time* rather than after ``run()``,
        because that is the instant the wiring exists to guarantee.
        """
        withheld_at_trading = []
        original_trading = LiveSessionRunner._phase_trading

        def _spy_trading(self):
            assert self._monitor is not None
            withheld_at_trading.append(self._monitor.submission_withheld)
            return original_trading(self)

        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _spy_trading)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=lambda settings: UP)

        runner.run()

        assert withheld_at_trading == [False], (
            "a healthy connection must permit submission by the time strategies start — "
            "otherwise the session trades nothing and no other test would notice"
        )

    def test_the_wrap_consults_the_runners_real_connection_monitor(self, monkeypatch):
        """Review fix, 2026-08-30. Every ``test_live_order_path.py`` case uses
        a stub whose ``submission_withheld`` is a plain attribute, so nothing
        pinned that the wrap works against the *real* property. Were
        ``ConnectionMonitor.submission_withheld`` to become a method,
        ``bool(bound_method)`` is ``True`` and every order in every session
        would be withheld forever with those tests still passing.

        This drives a real ``ConnectionMonitor`` through the runner and proves
        the wrapped method reads it live: flipping the monitor's state after
        installation changes what the next call does.
        """
        installed: list = []
        original_install = live_session_runner.install_order_path

        def _capture(strategy, monitor, log):
            installed.append((strategy, monitor))
            return original_install(strategy, monitor, log)

        monkeypatch.setattr(live_session_runner, "install_order_path", _capture)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=lambda settings: UP)
        runner.run()

        strategy, monitor = installed[0]
        assert monitor is runner._monitor
        assert isinstance(monitor.submission_withheld, bool), (
            "the wrap does `bool(monitor.submission_withheld)` — a non-bool (a bound method, "
            "say) is truthy and would silently withhold every order for the session's life"
        )
        assert monitor.submission_withheld is False
        monitor.observe(DOWN)
        monitor.observe(DOWN)
        assert monitor.submission_withheld is True
        assert strategy.submit_order(object()) is None, "a withheld call returns None"

    def test_the_reader_receives_the_runners_own_settings(self):
        received = []

        def _reader(settings):
            received.append(settings)
            return UP

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=_reader)

        runner.run()

        assert len(received) >= 1
        assert received[0].ibkr_host == "127.0.0.1"

    def test_a_failed_read_does_not_abort_the_startup_sequence(self):
        """AR42: a raise here must not cost the session its start. The
        monitor stays unobserved, which is the safe direction —
        ``submission_withheld`` stays True until a later tick succeeds.
        """

        def _raising_reader(settings):
            raise RuntimeError("adapter private flag read failed")

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=_raising_reader)

        runner.run()  # must not raise

        assert runner.trader_started is True
        assert runner._monitor is not None
        assert runner._monitor.state is ConnectionState.AWAITING_CONNECTION
        assert runner._monitor.submission_withheld is True

    def test_a_disconnected_reading_leaves_submission_withheld(self):
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, connection_reader=lambda settings: DOWN)

        runner.run()

        assert runner._monitor is not None
        # DOWN before ever connecting is not a loss (AWAITING_CONNECTION
        # stays put — see live_connection_monitor.py's own contract), and
        # AWAITING_CONNECTION withholds submission either way.
        assert runner._monitor.submission_withheld is True


class TestTheRunnerReachesTheOrderPathCallSites:
    """Task 5.2 — Story 3.1's review finding, applied here: a wiring claim
    pinned only by a test that calls the helper directly survives the call
    site being deleted (904 tests stayed green when Story 3.1's own call
    site was removed). Every test in this class constructs a real
    ``LiveSessionRunner`` and proves the runner itself reaches the call site.
    """

    def test_the_runner_calls_install_order_path_before_add_strategy(self, monkeypatch):
        calls: list[tuple[object, object, object]] = []
        original = live_session_runner.install_order_path

        def _spy(strategy, monitor, log):
            calls.append((strategy, monitor, log))
            return original(strategy, monitor, log)

        monkeypatch.setattr(live_session_runner, "install_order_path", _spy)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert len(calls) == 1, (
            "the runner never reached install_order_path — the order path's suppression wrap "
            "is unwired"
        )
        strategy, monitor, log = calls[0]
        assert monitor is runner._monitor
        assert log is runner._log
        assert strategy in node.trader.added_strategies

    def test_install_order_path_runs_before_add_strategy_on_one_timeline(self, monkeypatch):
        """Review fix, 2026-08-30. The sibling test asserts
        ``strategy in node.trader.added_strategies`` — membership, not order —
        so moving ``install_order_path`` *after* ``add_strategy`` (or after
        ``start_strategy``, which would leave ``on_start()`` order calls
        unwrapped) left it green. The ordering is load-bearing and normative
        in the module docstring, so it gets the interleaved-timeline treatment
        ``test_session_runner_phases.py`` already uses for the subscribe/
        add_strategy pair.
        """
        timeline: list[str] = []
        original_install = live_session_runner.install_order_path

        def _spy_install(strategy, monitor, log):
            timeline.append("install_order_path")
            return original_install(strategy, monitor, log)

        monkeypatch.setattr(live_session_runner, "install_order_path", _spy_install)

        node = TestLiveNode(run_seconds=0.01)
        original_add = node.trader.add_strategy

        def _spy_add(strategy):
            timeline.append("add_strategy")
            return original_add(strategy)

        node.trader.add_strategy = _spy_add
        original_start = node.trader.start_strategy

        def _spy_start(strategy_id):
            timeline.append("start_strategy")
            return original_start(strategy_id)

        node.trader.start_strategy = _spy_start

        _runner(node).run()

        assert timeline.count("install_order_path") == 1
        assert timeline.index("install_order_path") < timeline.index("add_strategy"), timeline
        assert timeline.index("install_order_path") < timeline.index("start_strategy"), timeline

    def test_the_runner_hands_the_observer_the_traded_aggregations(self, monkeypatch):
        """Review fix, 2026-08-30: NFR1's latency anchor may only be set by a
        bar type a strategy actually trades, so the runner must tell the
        observer which those are — each spec entry's ``bar_types[0]``, the one
        ``materialise_strategy`` hands the strategy.
        """
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._order_observer is not None
        assert runner._order_observer._traded_bar_types == frozenset({AAPL_1MIN})

    def test_deleting_the_call_site_leaves_the_order_creating_methods_unwrapped(self, monkeypatch):
        """The mutation itself (M2 in Task 9's sweep, proven here so the
        story's own claim is checkable): with the call site removed, the
        strategy's `submit_order` is the real Nautilus method, not a wrapper.
        """
        monkeypatch.setattr(live_session_runner, "install_order_path", lambda *a, **k: None)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner.run()

        strategy = node.trader.added_strategies[0]
        assert strategy.submit_order.__name__ != "wrapped"

    def test_the_runner_wires_the_order_event_observer_before_trading(self, monkeypatch):
        observed_state = []
        original_trading = LiveSessionRunner._phase_trading

        def _spy_trading(self):
            observed_state.append(self._order_observer is not None)
            return original_trading(self)

        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _spy_trading)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert observed_state == [True]
        assert runner._order_observer is not None
        topics = [topic for topic, _ in node.trader.subscriptions]
        assert "events.order*" in topics


class TestTheRunnerReachesTheTradeRecorderCallSite:
    """Story 3.5, Task 4.3 — the same wiring-claim discipline
    ``TestTheRunnerReachesTheOrderPathCallSites`` established: a real
    ``LiveSessionRunner`` against a ``TestLiveNode``, never the helper called
    directly.
    """

    def test_the_runner_subscribes_a_trade_recorder_bound_to_the_nodes_cache(self):
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._trade_recorder is not None
        assert isinstance(runner._trade_recorder, TradeRecorder)
        assert runner._trade_recorder._cache is node.cache
        assert runner._trade_recorder._log is runner._log

        matching = [
            handler
            for topic, handler in node.trader.subscriptions
            if topic == POSITION_EVENTS_TOPIC
        ]
        assert len(matching) == 1
        assert matching[0].__self__ is runner._trade_recorder
        assert matching[0].__func__ is TradeRecorder.handle_position_event

    def test_the_subscription_precedes_add_strategy_on_one_timeline(self, monkeypatch):
        timeline: list[str] = []
        original_subscribe = live_session_runner.LiveSessionRunner._subscribe

        def _spy_subscribe(self, topic, handler):
            if topic == POSITION_EVENTS_TOPIC:
                timeline.append("subscribe_trade_recorder")
            return original_subscribe(self, topic, handler)

        monkeypatch.setattr(live_session_runner.LiveSessionRunner, "_subscribe", _spy_subscribe)

        node = TestLiveNode(run_seconds=0.01)
        original_add = node.trader.add_strategy

        def _spy_add(strategy):
            timeline.append("add_strategy")
            return original_add(strategy)

        node.trader.add_strategy = _spy_add

        _runner(node).run()

        assert timeline.count("subscribe_trade_recorder") == 1
        assert timeline.index("subscribe_trade_recorder") < timeline.index("add_strategy"), timeline

    def test_no_position_topic_is_subscribed_when_the_wiring_is_mutated_away(self, monkeypatch):
        """Non-vacuity: proves the primary test's topic assertion can
        genuinely fail. Skipping ``_phase_subscribe`` outright is not usable
        here — ``_serve()`` asserts ``self._steady_state is not None``, which
        that phase alone sets — so the mutation instead retargets the topic
        constant the runner subscribes under, leaving the rest of the phase
        (including the steady state) intact.
        """
        monkeypatch.setattr(live_session_runner, "POSITION_EVENTS_TOPIC", "events.mutated*")

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner.run()

        assert runner._trade_recorder is not None
        topics = [topic for topic, _ in node.trader.subscriptions]
        assert POSITION_EVENTS_TOPIC not in topics
        assert "events.mutated*" in topics

    def test_the_recorder_holds_the_trade_sink_and_the_runners_own_callback(self):
        """Story 3.6, Task 8.1: the recorder the runner subscribes must hold
        exactly what ``LiveSessionRunner(..., trade_sink=...)`` was given, and
        an ``on_ownership_lost`` bound to this runner.
        """
        node = TestLiveNode(run_seconds=0.01)
        sink_calls = []

        def sink(recorded) -> bool:
            # Honours the sink's `-> bool` contract (review 2026-09-12) —
            # a bare `list.append` returned `None`, which the recorder would
            # have logged as `inserted="None"` without anything noticing.
            sink_calls.append(recorded)
            return True

        runner = _runner(node, trade_sink=sink)

        runner.run()

        assert runner._trade_recorder is not None
        assert runner._trade_recorder._sink is sink
        assert runner._trade_recorder._on_ownership_lost == runner._note_ownership_lost

    def test_trade_sink_omitted_leaves_the_recorders_sink_none(self):
        """Non-change contract: every existing runner test that never passes
        ``trade_sink`` must keep working unchanged.
        """
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._trade_recorder is not None
        assert runner._trade_recorder._sink is None

    def test_invoking_the_callback_sets_ownership_lost_and_schedules_node_stop(self):
        """The callback's actual effect (Story 3.6, D-C): setting the flag and
        scheduling a stop through the same ``call_soon_threadsafe`` handoff
        signals use — proven with a real event loop, not a full ``run()``, so
        the assertion is about the callback's own mechanics.
        """
        import asyncio

        node = TestLiveNode(run_forever=False)
        runner = _runner(node)
        loop = asyncio.new_event_loop()
        try:
            runner._loop = loop
            runner._node = node
            runner._trader_started = True

            runner._note_ownership_lost()
            loop.run_until_complete(asyncio.sleep(0))

            assert runner._ownership_lost is True
            assert node.stopped is True
            assert runner._signals.signal_name is None, "must never look like an OS signal"
            assert runner.ownership_lost is True
        finally:
            loop.close()


class TestTheRunnerReachesTheRejectionTallyCallSite:
    """Story 3.7, Task 3.3 — the same wiring-claim discipline its two siblings
    established: a real ``LiveSessionRunner`` against a ``TestLiveNode``, never
    the tally constructed directly in a test. Story 3.1's review finding is
    what this class exists for — a wiring claim pinned only by a test that
    builds the object itself survives the call site being deleted.
    """

    def test_the_runner_subscribes_a_rejection_tally_on_the_order_topic(self):
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._rejection_tally is not None
        assert isinstance(runner._rejection_tally, RejectionTally)

        matching = [
            handler
            for topic, handler in node.trader.subscriptions
            if topic == ORDER_EVENTS_TOPIC
            and getattr(handler, "__self__", None) is runner._rejection_tally
        ]
        assert len(matching) == 1
        assert matching[0].__func__ is RejectionTally.handle_order_event

    def test_three_handlers_now_sit_on_the_order_topic(self):
        """The observer's two (``note_bar`` is on the bar topic; its
        ``handle_order_event`` is here) plus the tally's one. Pinned as a count
        so a *lost* subscribe is visible, not only a wrong one.
        """
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        on_order = [
            handler for topic, handler in node.trader.subscriptions if topic == "events.order*"
        ]
        assert len(on_order) == 2  # observer.handle_order_event + tally.handle_order_event

    def test_the_tally_is_constructed_with_the_configured_account_and_the_runners_clock(self):
        """NFR26's configured-value clause is armed without the tally ever
        reading settings — the runner already holds the string for the guard
        (``live_session_runner.py:214-216``), so no settings read happens
        inside a msgbus handler.
        """
        node = TestLiveNode(run_seconds=0.01)
        clock = lambda: STARTED_AT  # noqa: E731 - a one-expression injected clock
        runner = _runner(node, time_source=clock)

        runner.run()

        assert runner._rejection_tally is not None
        assert runner._rejection_tally._account == "DU4076626"
        assert runner._rejection_tally._time_source is clock

    def test_the_subscription_is_recorded_so_the_stop_path_cancels_it(self):
        """Through ``self._subscribe``, so ``_unsubscribe`` cancels it without
        anyone having to remember the other end (the review fix of
        2026-08-30 that ``_subscribe`` exists for).
        """
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._rejection_tally is not None
        recorded = [
            handler
            for topic, handler in runner._subscriptions
            if getattr(handler, "__self__", None) is runner._rejection_tally
        ]
        assert len(recorded) == 1

    def test_the_tally_subscribe_happens_inside_the_subscribe_phase(self, monkeypatch):
        """Ordering pin: after the observer's own subscribe, before
        ``report_instrument_shortfall`` — i.e. inside ``_phase_subscribe``, not
        bolted on somewhere later where a phase failure would skip it.
        """
        timeline: list[str] = []
        original_subscribe = live_session_runner.LiveSessionRunner._subscribe
        original_shortfall = live_session_runner.report_instrument_shortfall

        def _spy_subscribe(self, topic, handler):
            owner = type(getattr(handler, "__self__", None)).__name__
            timeline.append(f"subscribe:{owner}.{getattr(handler, '__name__', handler)}")
            return original_subscribe(self, topic, handler)

        def _spy_shortfall(node, bar_types, log):
            timeline.append("shortfall")
            return original_shortfall(node, bar_types, log)

        monkeypatch.setattr(live_session_runner.LiveSessionRunner, "_subscribe", _spy_subscribe)
        monkeypatch.setattr(live_session_runner, "report_instrument_shortfall", _spy_shortfall)

        _runner(TestLiveNode(run_seconds=0.01)).run()

        tally_index = timeline.index("subscribe:RejectionTally.handle_order_event")
        observer_index = timeline.index("subscribe:OrderEventObserver.handle_order_event")
        assert observer_index < tally_index < timeline.index("shortfall")

    def test_the_steady_state_is_handed_the_same_tally_instance(self):
        """Not a second one: the tick must drain the object the bus feeds."""
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner._steady_state is not None
        assert runner._steady_state._tally is runner._rejection_tally

    def test_order_rejections_is_none_after_a_run_that_refused_nothing(self):
        """The clean-run case, and the reason ``live start`` prints nothing."""
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert runner.order_rejections is None
