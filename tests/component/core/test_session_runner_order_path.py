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
from src.core.live_session_runner import LiveSessionRunner
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
