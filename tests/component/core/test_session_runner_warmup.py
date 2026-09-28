"""Story 4.4 — the runner arms the warm-up watch and waits on it (D-B, D-D).

Component tier with a ``TestLiveNode``, like ``test_session_runner_phases.py``.
The double's ``start_strategy`` only records, so it never runs ``on_start``;
:func:`_start_issues_a_history_request` stands in for what both built-ins do
there — call ``request_bars`` with a callback — on the strategy the runner
actually materialised, instrumented and added. ``request_bars`` itself is
replaced *before* the runner instruments it (a real one needs a registered
strategy), so what the watch wraps is a fake whose answer each test controls.

The engine-level half — a real ``DataEngine`` answering a real strategy's
request — is ``test_strategy_warmup_engine.py``'s; the watch's own branches are
unit-tested in ``tests/unit/core/test_live_session_warmup.py``.
"""

import asyncio
import signal
import time
from datetime import datetime, timezone
from uuid import UUID

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

import src.core.live_session_runner as runner_module
import src.core.live_session_warmup as warmup_module
from src.config import IBKRSettings
from src.core.live_gate import GateDecision, GateMode
from src.core.live_session_record import SessionReclaimedError
from src.core.live_session_warmup import COMPLETED_EVENT, FAILED_EVENT, SKIPPED_EVENT
from src.core.live_strategy_guard import START_FAILED_EVENT, NoStrategyStartedError
from src.core.strategy_registry import StrategyRegistry
from src.models.session import SessionSpec, StrategySpec
from tests.component.doubles import TestIBAccountsClient, TestLiveNode, flat_broker_state_reader

pytestmark = pytest.mark.component

SESSION_ID = UUID("44444444-4444-4444-4444-444444444444")
STARTED_AT = datetime(2026, 9, 22, 13, 25, 0, tzinfo=timezone.utc)
PAPER_ACCOUNT = "DU4076626"
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
#: Long enough that no test's node stops itself mid-warm-up (a finished run
#: task ends the wait, by design); every test's answers arrive well inside it.
RUN_SECONDS = 2.0


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_session_runner_phases.py``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the runner's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _restore_the_strategy_registry():
    """See ``test_session_runner_strategy_failure.py`` — discovery first, then snapshot."""
    StrategyRegistry.discover()
    strategies = StrategyRegistry._strategies.copy()
    aliases = StrategyRegistry._aliases.copy()
    discovered = StrategyRegistry._discovered
    yield
    StrategyRegistry._strategies = strategies
    StrategyRegistry._aliases = aliases
    StrategyRegistry._discovered = discovered


@pytest.fixture(autouse=True)
def _short_deadline(monkeypatch):
    """``ibkr_request_timeout=0`` in :func:`_settings`, so the margin is the deadline."""
    monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 0.3)


@pytest.fixture
def registered_accounts(monkeypatch):
    def _register(settings: IBKRSettings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


def _settings() -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode="paper",
        tws_account=PAPER_ACCOUNT,
        ntrader_real_money_account="",
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
        ibkr_request_timeout=0,
    )


def _spec(*names: str) -> SessionSpec:
    parameters = {"sma_crossover": {"fast_period": 2, "slow_period": 3}, "momentum": {}}
    return SessionSpec(
        strategies=tuple(
            StrategySpec(strategy_id=name, parameters=parameters[name], bar_types=(AAPL_1MIN,))
            for name in names
        )
    )


class SpyRecord:
    """A ``SessionRecordPort`` double — all four methods, kwargs-only."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")

    def record_strategy_failure(self, **fields) -> None:
        self.calls.append("record_strategy_failure")

    def record_order_rejections(self, **fields) -> None:
        self.calls.append("record_order_rejections")


async def _never_sleeps(seconds: float) -> None:
    await asyncio.Event().wait()


def _permitting_verifier():
    async def _verify(node, settings, **kwargs) -> GateDecision:
        return GateDecision(permitted=True, mode=GateMode.PAPER)

    return _verify


def _runner(node: TestLiveNode, spec: SessionSpec) -> runner_module.LiveSessionRunner:
    node.run_seconds_from_first_strategy = True  # Story 4.2: see the double's docstring
    return runner_module.LiveSessionRunner(
        _settings(),
        session_id=SESSION_ID,
        spec=spec,
        record=SpyRecord(),
        started_at=STARTED_AT,
        connect_timeout=2.0,
        node_factory=lambda settings_arg, **kwargs: node,
        account_verifier=_permitting_verifier(),
        broker_state_reader=flat_broker_state_reader,
        client_builder=lambda *a, **k: None,
        sleeper=_never_sleeps,
    )


class _History:
    """The fake base ``request_bars``, answering per strategy class name."""

    def __init__(self, answer_after: dict[str, float | None]) -> None:
        #: Seconds until the answer; ``None`` never answers (story F2).
        self.answer_after = answer_after
        self.subscribed: list[str] = []

    def install(self, monkeypatch) -> None:
        materialise = runner_module.materialise_strategy

        def materialise_with_fake_history(strategy_spec, *args):
            strategy = materialise(strategy_spec, *args)
            name = type(strategy).__name__
            delay = self.answer_after.get(name)

            def request_bars(bar_type, start, *, callback=None):
                # The runner's own loop — set, but not running: `_phase_trading`
                # is synchronous and only runs it inside the warm-up wait.
                if delay is not None:
                    asyncio.get_event_loop().call_later(delay, callback, "request-1")
                return "request-1"

            strategy.request_bars = request_bars
            strategy.on_history_loaded = lambda request_id: self.subscribed.append(name)
            return strategy

        monkeypatch.setattr(runner_module, "materialise_strategy", materialise_with_fake_history)


def _start_issues_a_history_request(node: TestLiveNode, *, skip: frozenset[str] = frozenset()):
    """What ``on_start`` does, done by the double's ``start_strategy``."""
    start_strategy = node.trader.start_strategy

    def starting(strategy_id):
        start_strategy(strategy_id)
        strategy = node.trader.added_strategies[-1]
        if type(strategy).__name__ not in skip:
            strategy.request_bars(AAPL_1MIN, "start", callback=strategy.on_history_loaded)

    node.trader.start_strategy = starting


def _events(logs) -> list[str]:
    return [entry["event"] for entry in logs]


class TestTheWatchIsInstalledInTheSameWindowAsTheGuard:
    def test_request_bars_is_instrumented_before_add_strategy(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        instrumented_at_add: list[bool] = []
        add_strategy = node.trader.add_strategy

        def spy(strategy):
            # The watch's own marker, not merely "some instance attribute
            # named request_bars" (code review 2026-09-22).
            instrumented_at_add.append(
                getattr(strategy, "_ntrader_warmup_instrumented", False)
                and "request_bars" in strategy.__dict__
            )
            add_strategy(strategy)

        node.trader.add_strategy = spy

        _runner(node, _spec("sma_crossover", "momentum")).run()

        assert instrumented_at_add == [True, True]

    def test_the_warmup_phase_still_brackets_its_work(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)

        with capture_logs() as logs:
            _runner(TestLiveNode(run_seconds=0.01), _spec("sma_crossover")).run()

        pairs = [(e["phase"], e["status"]) for e in logs if e.get("phase") and e.get("status")]
        assert ("warmup", "started") in pairs and ("warmup", "ok") in pairs
        assert pairs.index(("warmup", "ok")) < pairs.index(("trading", "started"))


class TestSessionStartedWaitsForTheWarmup:
    """D-B — ``session.started`` means every started strategy is warm."""

    def test_the_milestone_precedes_session_started(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        history = _History({"SMACrossover": 0.05})
        history.install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        runner = _runner(node, _spec("sma_crossover"))

        with capture_logs() as logs:
            runner.run()

        events = _events(logs)
        assert events.index(COMPLETED_EVENT) < events.index("session.started")
        assert history.subscribed == ["SMACrossover"]
        assert runner.trader_started

    def test_strategies_warm_one_at_a_time(self, registered_accounts, monkeypatch):
        """NFR15 and F2's same-second dedup: the second strategy is not even
        added until the first has settled.

        Observed at the moment of each ``add_strategy`` (code review
        2026-09-22): the log is read *inside* the spy, so a runner that added
        every strategy and only then waited would see ``[0, 0]`` here, not
        ``[0, 1]`` — the add order alone is sequential under any design.
        """
        settings = _settings()
        registered_accounts(settings)
        # The first answer is the slower one, so a concurrent design would
        # have added the second strategy long before it arrived.
        _History({"SMACrossover": 0.2, "SMAMomentum": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        completed_before_add: list[int] = []
        add_strategy = node.trader.add_strategy

        with capture_logs() as logs:

            def spy(strategy):
                completed_before_add.append(
                    sum(1 for entry in logs if entry["event"] == COMPLETED_EVENT)
                )
                add_strategy(strategy)

            node.trader.add_strategy = spy
            _runner(node, _spec("sma_crossover", "momentum")).run()

        assert completed_before_add == [0, 1]
        completed = [e["strategy_id"] for e in logs if e["event"] == COMPLETED_EVENT]
        assert completed == ["sma_crossover", "momentum"]

    def test_a_strategy_that_requests_nothing_is_skipped_not_waited_for(
        self, registered_accounts, monkeypatch
    ):
        settings = _settings()
        registered_accounts(settings)
        _History({}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node, skip=frozenset({"SMACrossover"}))

        with capture_logs() as logs:
            _runner(node, _spec("sma_crossover")).run()

        assert SKIPPED_EVENT in _events(logs)
        assert "session.started" in _events(logs)


class TestAWarmupThatNeverArrivesIsContained:
    """D-D (ruled A) / AC #7."""

    def test_the_silent_strategy_is_contained_and_its_sibling_trades(
        self, registered_accounts, monkeypatch
    ):
        settings = _settings()
        registered_accounts(settings)
        history = _History({"SMACrossover": None, "SMAMomentum": 0.05})
        history.install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        runner = _runner(node, _spec("sma_crossover", "momentum"))

        with capture_logs() as logs:
            runner.run()

        failed = [e for e in logs if e["event"] == FAILED_EVENT]
        assert [(e["strategy_id"], e["reason"]) for e in failed] == [
            ("sma_crossover", "no_response")
        ]
        start_failed = [e for e in logs if e["event"] == START_FAILED_EVENT]
        assert [e["spec_strategy_id"] for e in start_failed] == ["sma_crossover"]
        assert start_failed[0]["error_type"] == "WarmupFailedError"
        assert [f.spec_strategy_id for f in runner.contained_failures] == ["sma_crossover"]
        assert runner.trader_started, "the sibling should still have started the session"
        assert history.subscribed == ["SMAMomentum"], "the silent strategy must never subscribe"

    def test_the_contained_strategy_is_faulted(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": None, "SMAMomentum": 0.05}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        verbs: list[str] = []
        add_strategy = node.trader.add_strategy

        def spy(strategy):
            if type(strategy).__name__ == "SMACrossover":
                strategy.fault = lambda: verbs.append("fault")
            add_strategy(strategy)

        node.trader.add_strategy = spy

        _runner(node, _spec("sma_crossover", "momentum")).run()

        assert verbs == ["fault"]

    def test_every_strategy_silent_fails_the_start(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": None}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        runner = _runner(node, _spec("sma_crossover"))

        with pytest.raises(NoStrategyStartedError):
            runner.run()

        assert not runner.trader_started


class TestAStopDuringTheWait:
    def test_a_node_that_stops_mid_warmup_ends_the_wait_without_a_failure(
        self, registered_accounts, monkeypatch
    ):
        """The run task finishing is one of the things ``_stopping`` reads.

        The strategy whose wait it ended is **not** counted as started (code
        review 2026-09-22): it never warmed and never subscribed, so a session
        whose only strategy was interrupted must not log ``session.started``.
        """
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": None}).install(monkeypatch)
        # `ibkr_request_timeout=0` + a 30s margin: only the stop can end this wait.
        monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 30.0)
        node = TestLiveNode(run_seconds=0.2)
        _start_issues_a_history_request(node)
        runner = _runner(node, _spec("sma_crossover"))

        with capture_logs() as logs, pytest.raises(NoStrategyStartedError):
            runner.run()

        assert FAILED_EVENT not in _events(logs)
        assert START_FAILED_EVENT not in _events(logs)
        assert "session.started" not in _events(logs)
        assert not runner.trader_started

    def test_a_requested_stop_starts_no_further_strategy(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": None, "SMAMomentum": 0.01}).install(monkeypatch)
        monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 30.0)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        runner = _runner(node, _spec("sma_crossover", "momentum"))
        start_strategy = node.trader.start_strategy
        fired: list[object] = []

        def starting(strategy_id):
            start_strategy(strategy_id)
            strategy = node.trader.added_strategies[-1]
            strategy.request_bars(AAPL_1MIN, "start", callback=strategy.on_history_loaded)
            # The operator's Ctrl-C lands while this strategy is warming — the
            # stop idiom `test_session_runner_stop.py` uses. Once only: a
            # second signal is the runner's force exit, which would take the
            # test process down with it if a regression started a second
            # strategy.
            if fired:
                return
            fired.append(strategy_id)
            asyncio.get_event_loop().call_later(0.1, runner._signals._handle, signal.SIGINT, None)

        node.trader.start_strategy = starting

        with capture_logs() as logs:
            runner.run()

        assert [type(s).__name__ for s in node.trader.added_strategies] == ["SMACrossover"]
        assert runner.stopped_by_signal
        # The interrupted strategy never warmed: no `session.started` for it.
        assert "session.started" not in _events(logs)
        assert not runner.trader_started


class TestAReclaimDuringTheWait:
    """Code review 2026-09-22: the reclaim guard was read once, before the
    first strategy — a warm-up wait now runs the loop for up to a deadline per
    strategy, and a session taken by another process in that window must stop
    starting strategies and never declare itself started."""

    def test_the_wait_ends_and_the_reclaim_is_raised(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": None, "SMAMomentum": 0.01}).install(monkeypatch)
        monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 30.0)
        # A node that would run far longer than the reclaim takes to notice:
        # a wait that ignored the reclaim would sit until this node ended, and
        # the run below would take >= 5 s instead of well under one.
        node = TestLiveNode(run_seconds=5.0)
        runner = _runner(node, _spec("sma_crossover", "momentum"))
        start_strategy = node.trader.start_strategy
        reclaim = SessionReclaimedError("another process now owns this session")

        def starting(strategy_id):
            start_strategy(strategy_id)
            strategy = node.trader.added_strategies[-1]
            strategy.request_bars(AAPL_1MIN, "start", callback=strategy.on_history_loaded)
            heartbeat = runner._startup_heartbeat
            asyncio.get_event_loop().call_later(0.1, setattr, heartbeat, "reclaim", reclaim)

        node.trader.start_strategy = starting

        began = time.monotonic()
        with capture_logs() as logs, pytest.raises(SessionReclaimedError):
            runner.run()

        assert time.monotonic() - began < 3.0, "the wait did not notice the reclaim"
        assert [type(s).__name__ for s in node.trader.added_strategies] == ["SMACrossover"]
        assert "session.started" not in _events(logs)
        assert not runner.trader_started


class TestTheConnectionIsReobservedWhileWaiting:
    """Code review 2026-09-22: an earlier strategy is live while a later one
    warms, and the steady state that re-reads the connection is not running
    yet — so the wait does it, once a second (NFR10)."""

    def test_the_monitor_is_read_again_during_a_long_wait(self, registered_accounts, monkeypatch):
        settings = _settings()
        registered_accounts(settings)
        _History({"SMACrossover": 1.3}).install(monkeypatch)
        monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 30.0)
        node = TestLiveNode(run_seconds=3.0)
        _start_issues_a_history_request(node)
        reads: list[str] = []
        runner = _runner(node, _spec("sma_crossover"))
        base_reader = runner._connection_reader

        def reader(settings_arg):
            reads.append("read")
            return base_reader(settings_arg)

        runner._connection_reader = reader

        runner.run()

        # One read in `_phase_subscribe`, at least one more during the 1.3 s wait.
        assert len(reads) >= 2
