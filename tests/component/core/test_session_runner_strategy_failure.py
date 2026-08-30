"""One strategy fails; the session and its siblings do not (Story 2.7).

Component tier, and deliberately so for **both** halves of this file.

The wiring half drives a ``TestLiveNode`` double, exactly like
``test_session_runner_phases.py``. The dispatch half builds a **real**
``MessageBus``, ``Cache``, ``Portfolio`` and two real, registered, started
``Strategy`` objects — which sounds like integration-tier work and is not.
MEASURED: that arrangement leaves ``is_logging_initialized()`` **False** at
every step, because nothing here constructs a ``NautilusKernel``. The autouse
fixture below is what keeps that claim honest rather than assumed; putting this
under ``--forked`` would cost minutes per run for nothing.

What this tier **cannot** prove is that the process survives. A ``TestLiveNode``
has no message bus, no data engine and no kernel, and even the real
``MessageBus`` here has no ``LiveDataEngine`` behind it to reach
``os._exit(1)``. That proof is a subprocess return code, in
``tests/integration/core/test_live_strategy_failure_survives.py``.

The raiser is a **real** ``sma_crossover`` fed a ``0.00`` close, not a
monkeypatched ``raise Exception("boom")``: it raises ``decimal.DivisionByZero``
at ``sma_crossover.py:150`` through the real ``Actor.handle_bar`` re-raise,
which is the machinery under test. Nautilus accepts a zero-priced ``Bar``
(measured).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import structlog
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, is_logging_initialized
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_gate import GateDecision, GateMode
from src.core.live_session_node import materialise_strategy
from src.core.live_session_runner import LiveSessionRunner
from src.core.live_strategy_guard import NoStrategyStartedError, StrategyGuard
from src.core.strategy_registry import StrategyRegistry
from src.models.session import SessionSpec, StrategySpec
from tests.component.doubles import TestIBAccountsClient, TestLiveNode

pytestmark = pytest.mark.component

SESSION_ID = UUID("77777777-7777-7777-7777-777777777777")
STARTED_AT = datetime(2026, 8, 23, 14, 3, 11, tzinfo=timezone.utc)
PAPER_ACCOUNT = "DU4076626"
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
BAR_TYPE = BarType.from_str(AAPL_1MIN)

#: Four clean closes and then a zero. ``sma_crossover`` at ``fast=2, slow=3``
#: has both SMAs initialised by bar 3, crosses down into
#: ``_generate_sell_signal`` on the zero, and divides by it at
#: ``sma_crossover.py:150`` (``raw_qty = position_value / current_price``).
CLOSES = ("90.00", "95.00", "100.00", "105.00", "0.00")


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Copied verbatim from ``tests/component/core/test_session_runner_phases.py``.
    The assertion is on the *delta*, not the absolute state: under ``-n auto``
    this file shares a worker process with the rest of the component tier, and
    something else in that tier does initialise C logging. What this file must
    never do is *change* the state — which is exactly what would happen if a
    test here ever constructed a real ``TradingNode``.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the runner's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep the shell out of the fields the runner's collaborators read."""
    for name in (
        "IBKR_RATE_LIMIT",
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IB_MAX_CONNECTION_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


@pytest.fixture(autouse=True)
def _restore_the_strategy_registry():
    """Registration is process-global and survives into every later test.

    Modelled on ``tests/component/api/test_run_backtest_routes.py:15-52``.
    Nothing in this file registers a probe strategy today, but the snapshot is
    cheap and the failure it prevents — a strategy leaking into an unrelated
    test in the same xdist worker — is silent and remote from its cause.
    ``StrategyRegistry.clear()`` is deliberately **not** used: it would discard
    the real registrations this file depends on.

    ⚠️ Discovery is forced **before** the snapshot is taken. Taking it first
    captures an empty registry (discovery is lazy, via ``_ensure_discovered``),
    and restoring *that* at teardown wipes the real registrations for every
    later test in the same xdist worker — which is the same class of
    process-global leak this fixture exists to prevent, pointing the other way.
    """
    StrategyRegistry.discover()
    strategies = StrategyRegistry._strategies.copy()
    aliases = StrategyRegistry._aliases.copy()
    discovered = StrategyRegistry._discovered
    yield
    StrategyRegistry._strategies = strategies
    StrategyRegistry._aliases = aliases
    StrategyRegistry._discovered = discovered


def _settings(*, mode: str = "paper", port: int = 4002, account: str = PAPER_ACCOUNT):
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=port,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode=mode,
        tws_account=account,
        ntrader_real_money_account="",
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
    )


def _sma_spec(**parameters):
    return StrategySpec(
        strategy_id="sma_crossover",
        parameters=parameters or {"fast_period": 2, "slow_period": 3},
        bar_types=(AAPL_1MIN,),
    )


def _momentum_spec():
    return StrategySpec(strategy_id="momentum", parameters={}, bar_types=(AAPL_1MIN,))


def _spec_for(order):
    """A session spec whose strategies appear in ``order``.

    The pair is fixed at ``sma_crossover`` + ``momentum``, the only in-repo pair
    that is collision-free in **both** orders: measured against a real
    ``Trader``, ``mean_reversion`` + ``sma_crossover`` raises a ``RuntimeError``
    for an ``order_id_tag`` conflict on ``'001'`` (finding #10).
    """
    by_name = {"sma_crossover": _sma_spec(), "momentum": _momentum_spec()}
    return SessionSpec(strategies=tuple(by_name[name] for name in order))


class FakeClock:
    """The repo idiom — an injected aware-``datetime`` clock, no ``freezegun``."""

    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SpyRecord:
    """A ``SessionRecordPort`` double. Duck-typed; the runner never checks it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: list[dict] = []

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")

    def record_strategy_failure(self, **fields) -> None:
        self.calls.append("record_strategy_failure")
        self.failures.append(fields)


def _pairs(logs) -> list[tuple[str, str]]:
    """The ordered ``(phase, status)`` pairs, the idiom this suite asserts on."""
    return [
        (entry["phase"], entry["status"])
        for entry in logs
        if entry.get("phase") and entry.get("status")
    ]


async def _never_sleeps(seconds: float) -> None:
    """A sleeper that never returns, so the heartbeat never ticks in these tests."""
    await asyncio.Event().wait()


def _permitting_verifier(mode: GateMode = GateMode.PAPER):
    async def _verify(node, settings, **kwargs) -> GateDecision:
        return GateDecision(permitted=True, mode=mode)

    return _verify


@pytest.fixture
def registered_accounts(monkeypatch):
    """Put an account-naming client in the adapter's process-global cache."""

    def _register(settings: IBKRSettings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


def _runner(node: TestLiveNode, *, settings=None, record=None, **overrides) -> LiveSessionRunner:
    """Build a runner whose node factory returns ``node``."""
    options = {
        "session_id": SESSION_ID,
        "spec": _spec_for(("sma_crossover",)),
        "record": record if record is not None else SpyRecord(),
        "started_at": STARTED_AT,
        "connect_timeout": 2.0,
        "node_factory": lambda settings_arg, **kwargs: node,
        "account_verifier": _permitting_verifier(),
        "client_builder": lambda *a, **k: None,
        "sleeper": _never_sleeps,
    }
    options.update(overrides)
    return LiveSessionRunner(settings if settings is not None else _settings(), **options)


def _bar(index: int, close: str) -> Bar:
    price = Price.from_str(close)
    return Bar(
        bar_type=BAR_TYPE,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=index * 60_000_000_000,
        ts_init=index * 60_000_000_000,
    )


class RealDispatch:
    """A real ``MessageBus`` with two real, registered, started strategies.

    ⚠️ The instrument is seeded into the ``Cache`` **before** ``start()``.
    Without it, ``SMAMomentum.on_start`` (``src/core/strategies/sma_momentum.py:63-72``)
    finds ``cache.instrument(...)`` is ``None``, calls ``self.stop()`` and
    returns **before** ``subscribe_bars``. MEASURED: ``bar subs: 1``, momentum
    ``STOPPED`` — the survivor never subscribes and AC #2's assertion becomes
    untestable rather than false. :meth:`assert_both_running` is the guard on
    the guard.
    """

    def __init__(self, order, *, guard: StrategyGuard | None) -> None:
        clock = LiveClock()
        trader_id = TraderId("TESTER-000")
        self.bus = MessageBus(trader_id=trader_id, clock=clock)
        self.cache = Cache(database=None)
        self.cache.add_instrument(TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"))
        portfolio = Portfolio(self.bus, self.cache, clock)

        self.order = order
        #: What each strategy's ``on_bar`` actually processed. Inside
        #: ``handle_bar``'s ``if state == RUNNING`` gate, so it stops advancing
        #: for a strategy once it is ``DEGRADED``.
        self.seen: dict[str, list[str]] = {name: [] for name in order}
        #: What the containment boundary passed *through* to ``handle_bar``.
        #: Outside that gate, so it keeps advancing after a degrade — which is
        #: AC #9's call-through clause.
        self.delivered: dict[str, list[str]] = {name: [] for name in order}
        self.strategies: list = []
        for name, spec in zip(order, _spec_for(order).strategies):
            strategy = materialise_strategy(spec)
            strategy.register(trader_id, portfolio, self.bus, self.cache, clock)
            self._count_bars(strategy, name)
            if guard is not None:
                guard.wrap(strategy, spec_strategy_id=name)
            self.strategies.append(strategy)

    def _count_bars(self, strategy, name):
        """Count at both levels, then call through at both.

        Two counters because the two facts are different and the difference is
        load-bearing. ``on_bar`` sits **inside** ``Actor.handle_bar``'s
        ``if state == RUNNING`` gate and inside its ``try``, so it counts what
        the strategy processed and it increments before the real
        ``sma_crossover`` divides by zero — that is AC #2's "every other
        strategy receives that same bar". ``handle_bar`` sits **outside** that
        gate, so it counts what the boundary handed on, which is what stays
        true after a degrade.

        Both are patched **before** ``guard.wrap``, so the guard's wrapper is
        outermost and the bus subscribes it: ``subscribe_bars`` binds
        ``self.handle_bar`` during ``on_start`` (``common/actor.pyx:1804-1806``),
        which is after all of this.
        """
        base_on_bar = strategy.on_bar
        seen = self.seen[name]

        def on_bar(bar):
            seen.append(str(bar.close))
            return base_on_bar(bar)

        strategy.on_bar = on_bar

        base_handle_bar = strategy.handle_bar
        delivered = self.delivered[name]

        def handle_bar(bar):
            delivered.append(str(bar.close))
            return base_handle_bar(bar)

        strategy.handle_bar = handle_bar

    def start(self):
        for strategy in self.strategies:
            strategy.start()
        return self

    def assert_both_running(self):
        for name, strategy in zip(self.order, self.strategies):
            assert strategy.is_running, f"{name} is {strategy.state} and never subscribed"
        assert len(self.bus.subscriptions(f"data.bars.{BAR_TYPE}")) == 2

    def publish(self, index, close):
        self.bus.publish(f"data.bars.{BAR_TYPE}", _bar(index, close))

    def publish_clean_bars(self):
        for index, close in enumerate(CLOSES[:-1]):
            self.publish(index, close)

    @property
    def zero_bar_index(self):
        return len(CLOSES) - 1


class TestTheGuardIsInstalledBeforeAddStrategy:
    """AC #1 — the ordering the whole design rests on.

    ``Strategy.register()`` subscribes the **bound** ``self.handle_event``
    during ``Trader.add_strategy`` (``trading/strategy.pyx:314-315``), so a
    wrapper installed after that call would never be reached for order and
    position events at all.
    """

    def test_every_strategy_is_wrapped_before_it_is_added_to_the_trader(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        wrapped_at_add: list[bool] = []
        add_strategy = node.trader.add_strategy

        def spy(strategy):
            # An *instance* attribute shadowing the class method is what the
            # guard installs, so its presence in __dict__ is the observation.
            wrapped_at_add.append("handle_bar" in strategy.__dict__)
            add_strategy(strategy)

        node.trader.add_strategy = spy

        _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum"))).run()

        assert wrapped_at_add == [True, True]

    def test_both_handlers_are_wrapped_on_the_real_strategy_objects(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)

        _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum"))).run()

        for strategy in node.trader.added_strategies:
            assert "handle_bar" in strategy.__dict__
            assert "handle_event" in strategy.__dict__

    def test_the_runner_exposes_the_contained_failures_it_has_seen(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)

        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)
        runner.run()

        assert runner.contained_failures == ()


def _fail_starts_for(node, failures):
    """Make ``start_strategy`` raise for the given 0-based call indices.

    Patched on the double's *instance* rather than added to
    ``tests/component/doubles/test_live_node.py``: that double is shared with
    ``test_live_check_driver.py``, ``test_epic1_ac_cli.py`` and
    ``test_epic1_ac_data.py``, and not touching it is strictly safer than
    touching it carefully.
    """
    calls = {"n": 0}
    start_strategy = node.trader.start_strategy

    def failing(strategy_id):
        index = calls["n"]
        calls["n"] += 1
        if index in failures:
            raise failures[index]
        start_strategy(strategy_id)

    node.trader.start_strategy = failing


def _spy_recovery_verbs(node):
    """Record which lifecycle verb the runner calls on a failed strategy.

    The spies deliberately do **not** call through: the point of the
    assertion is *which verb was chosen*, not what Nautilus does with it on
    an unregistered strategy.
    """
    verbs: list[str] = []
    add_strategy = node.trader.add_strategy

    def spy(strategy):
        for verb in ("fault", "degrade", "stop"):
            setattr(strategy, verb, lambda v=verb: verbs.append(v))
        add_strategy(strategy)

    node.trader.add_strategy = spy
    return verbs


class TestAFailedStartDoesNotStopTheOthers:
    """AC #5 — ``_phase_trading``'s loop is per-spec, not all-or-nothing.

    Today it is a bare ``for`` loop; ``Component.start()`` logs and re-raises
    (``common/component.pyx:1888-1902``), so measured against a real ``Trader``
    one bad ``on_start`` leaves ``strategy states: {S-000: 'STARTING',
    S-001: 'READY'}`` and the trader stuck in ``STARTING`` — every later spec
    starved by the first.
    """

    def test_the_second_spec_still_starts_when_the_first_fails_to(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("on_start blew up")})

        with capture_logs() as logs:
            _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum"))).run()

        assert len(node.trader.added_strategies) == 2
        assert len(node.trader.started_strategies) == 1
        assert ("trading", "ok") in _pairs(logs)
        assert [e for e in logs if e["event"] == "session.started"]

    def test_the_failed_spec_is_recorded_exactly_like_a_runtime_failure(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("on_start blew up")})

        runner = _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum")))
        with capture_logs() as logs:
            runner.run()

        (failure,) = runner.contained_failures
        assert failure.spec_strategy_id == "sma_crossover"
        assert failure.error_type == "RuntimeError"
        assert failure.handler == "start"
        assert [e for e in logs if e["event"] == "strategy.start_failed"]

    def test_an_add_strategy_conflict_is_contained_by_the_same_except(self, registered_accounts):
        """Finding #10: ``Trader.add_strategy`` assigns ``order_id_tag`` and
        raises a ``RuntimeError`` when the tag is already taken — a latent
        startup failure for an operator's choice of strategy order, on a spec
        that validated perfectly at create time.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        calls = {"n": 0}
        add_strategy = node.trader.add_strategy

        def failing(strategy):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("order_id_tag conflict for '001'")
            add_strategy(strategy)

        node.trader.add_strategy = failing

        runner = _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum")))
        runner.run()

        assert len(node.trader.started_strategies) == 1
        assert [f.spec_strategy_id for f in runner.contained_failures] == ["sma_crossover"]

    def test_the_recovery_verb_is_fault_never_degrade_and_never_stop(self, registered_accounts):
        """AC #5 and AC #7's verb choice, in one assertion.

        ``degrade()`` is **illegal** from ``STARTING`` and is *silently
        swallowed* (``_trigger_fsm`` catches ``InvalidStateTrigger``, logs and
        returns — ``common/component.pyx:2130-2134``), so a strategy isolated
        with it would look contained and not be. ``stop()`` remains the wrong
        verb regardless of what ``on_stop()`` does for any given strategy
        today (Story 3.1): it runs strategy-owned teardown code over an
        unrelated startup bug — the wrong time for any such side effect to
        run (NFR14, AR43).
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        verbs = _spy_recovery_verbs(node)
        _fail_starts_for(node, {0: RuntimeError("on_start blew up")})

        _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum"))).run()

        assert verbs == ["fault"]


class TestASessionWithNoLiveStrategyFails:
    """AC #5's last clause — reporting it as started would be a false green."""

    def test_the_trading_phase_fails_when_no_strategy_starts_at_all(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(
            node, {0: RuntimeError("first blew up"), 1: RuntimeError("second blew up")}
        )

        runner = _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum")))
        with capture_logs() as logs:
            with pytest.raises(NoStrategyStartedError):
                runner.run()

        assert ("trading", "failed") in _pairs(logs)
        assert ("trading", "ok") not in _pairs(logs)
        assert not [e for e in logs if e["event"] == "session.started"]
        assert runner.trader_started is False

    def test_both_failures_are_recorded_and_all_failed_is_reported(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(
            node, {0: RuntimeError("first blew up"), 1: RuntimeError("second blew up")}
        )

        runner = _runner(node, settings=settings, spec=_spec_for(("sma_crossover", "momentum")))
        with capture_logs() as logs:
            with pytest.raises(NoStrategyStartedError):
                runner.run()

        assert [f.spec_strategy_id for f in runner.contained_failures] == [
            "sma_crossover",
            "momentum",
        ]
        assert [e for e in logs if e["event"] == "session.all_strategies_failed"]

    def test_the_session_is_still_marked_stopped_on_the_way_out(self, registered_accounts):
        """AR38's single teardown path is not bypassed by this new failure."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("boom")})
        record = SpyRecord()

        runner = _runner(node, settings=settings, record=record, spec=_spec_for(("sma_crossover",)))
        with pytest.raises(NoStrategyStartedError):
            runner.run()

        assert record.calls[-1] == "mark_stopped"

    def test_the_message_names_the_session_rather_than_a_bare_type(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("boom")})

        with pytest.raises(NoStrategyStartedError) as raised:
            _runner(node, settings=settings, spec=_spec_for(("sma_crossover",))).run()

        assert "sma_crossover" in str(raised.value)


class TestTheTeardownFlushesTheQueue:
    """The end-of-run window, closed (review fix, 2026-08-23).

    The steady-state tick is the *routine* path to ``runtime_flags``, but a
    failure contained within the last interval before a stop — or queued by
    the all-failed start path, where ``run()`` raises before the tick loop
    ever starts — used to be lost forever: the ``-> stopped`` transition made
    it permanent because the service refuses writes against a non-``running``
    row by design. The runner's ``finally`` now drains the guard once more,
    **before** ``mark_stopped``, while the row is still ours to write.
    """

    def test_a_start_failure_reaches_the_record_before_mark_stopped(self, registered_accounts):
        """The heartbeat never ticks in these tests (`_never_sleeps`), so the
        only route this failure has to the record is the teardown flush.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("on_start blew up")})
        record = SpyRecord()

        runner = _runner(
            node, settings=settings, record=record, spec=_spec_for(("sma_crossover", "momentum"))
        )
        runner.run()

        (failure,) = record.failures
        assert failure["spec_strategy_id"] == "sma_crossover"
        assert failure["handler"] == "start"
        assert failure["all_failed"] is False
        # Ordering is the point: the write lands while the row still reads
        # `running` — after it, the service would refuse.
        flush_index = record.calls.index("record_strategy_failure")
        assert flush_index < record.calls.index("mark_stopped")

    def test_the_all_failed_start_path_persists_both_failures(self, registered_accounts):
        """`run()` raises `NoStrategyStartedError` before the tick loop ever
        starts, so without the flush nothing on this path could ever reach
        `runtime_flags` — the exact residual the review named.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(
            node, {0: RuntimeError("first blew up"), 1: RuntimeError("second blew up")}
        )
        record = SpyRecord()

        runner = _runner(
            node, settings=settings, record=record, spec=_spec_for(("sma_crossover", "momentum"))
        )
        with pytest.raises(NoStrategyStartedError):
            runner.run()

        assert [f["spec_strategy_id"] for f in record.failures] == ["sma_crossover", "momentum"]
        assert all(f["all_failed"] is True for f in record.failures)

    def test_a_db_failure_in_the_flush_does_not_replace_the_runs_outcome(self, registered_accounts):
        """AR42 on the flush path: the teardown behind it — `mark_stopped`
        above all — must still run.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        _fail_starts_for(node, {0: RuntimeError("boom")})
        record = SpyRecord()

        def _raise(**fields):
            record.calls.append("record_strategy_failure")
            raise RuntimeError("postgres is down")

        record.record_strategy_failure = _raise

        runner = _runner(
            node, settings=settings, record=record, spec=_spec_for(("sma_crossover", "momentum"))
        )
        runner.run()

        assert record.calls[-1] == "mark_stopped"


class TestASiblingKeepsReceivingBars:
    """AC #2 — in **either** registration order.

    Dispatch is registration-ordered within a priority
    (``common/component.pyx:2795``, ``sorted(subs_list, reverse=True)``), so a
    single-order test passes vacuously against a design with no containment at
    all whenever the raiser happens to be last. The negative control below
    measures exactly that: unguarded, raiser-**last** leaves the sibling with
    all five bars, and raiser-**first** leaves it with four.
    """

    @pytest.mark.parametrize(
        "order",
        [("sma_crossover", "momentum"), ("momentum", "sma_crossover")],
        ids=["raiser-first", "raiser-last"],
    )
    def test_the_sibling_receives_the_bar_that_killed_the_raiser(self, order):
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=FakeClock())
        dispatch = RealDispatch(order, guard=guard).start()
        dispatch.assert_both_running()
        dispatch.publish_clean_bars()

        before = dispatch.bus.pub_count
        dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        assert dispatch.seen["momentum"] == list(CLOSES)
        assert dispatch.seen["sma_crossover"] == list(CLOSES)
        # `component.pyx:2785` increments only when no subscriber raised. The
        # delta is deliberately not pinned: `pub_count` is cumulative (it reads
        # 6 before the first bar) and one guarded bar ending in `degrade()`
        # adds three, not one.
        assert dispatch.bus.pub_count > before

    @pytest.mark.parametrize(
        "order",
        [("sma_crossover", "momentum"), ("momentum", "sma_crossover")],
        ids=["raiser-first", "raiser-last"],
    )
    def test_the_raiser_is_degraded_and_the_sibling_is_untouched(self, order):
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=FakeClock())
        dispatch = RealDispatch(order, guard=guard).start()
        dispatch.publish_clean_bars()
        dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        # `.name`, not `str(...)`: `ComponentState.__str__` renders the integer
        # (`'11'`), so a string comparison against "DEGRADED" fails for the
        # right value.
        states = dict(zip(order, (s.state.name for s in dispatch.strategies)))
        assert states["sma_crossover"] == "DEGRADED"
        assert states["momentum"] == "RUNNING"

    def test_the_failure_is_the_real_division_not_a_synthetic_raise(self):
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=FakeClock())
        dispatch = RealDispatch(("sma_crossover", "momentum"), guard=guard).start()
        dispatch.publish_clean_bars()

        with capture_logs() as logs:
            dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        (failure,) = guard.failures
        assert failure.error_type == "DivisionByZero"
        assert failure.spec_strategy_id == "sma_crossover"
        assert failure.handler == "handle_bar"
        assert str(failure.strategy_id).startswith("SMACrossover")
        failed = [entry for entry in logs if entry["event"] == "strategy.failed"]
        assert len(failed) == 1
        assert "sma_crossover.py" in failed[0]["traceback"]

    @pytest.mark.parametrize(
        "order",
        [("sma_crossover", "momentum"), ("momentum", "sma_crossover")],
        ids=["raiser-first", "raiser-last"],
    )
    def test_without_the_guard_the_publish_raises_and_the_bus_never_completes_it(self, order):
        """The negative control. Without it the positive test above could pass
        against a design with no containment at all.

        Pinned on ``pub_count`` rather than on the sibling's count, because the
        sibling's count is **order-dependent** — measured, unguarded: raiser
        first leaves it at four of five, raiser last at five of five. The bus
        counter is the one fact both orders share.
        """
        dispatch = RealDispatch(order, guard=None).start()
        dispatch.publish_clean_bars()

        before = dispatch.bus.pub_count
        with pytest.raises(Exception) as raised:
            dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        assert type(raised.value).__name__ == "DivisionByZero"
        assert dispatch.bus.pub_count == before

    def test_without_the_guard_a_raiser_registered_first_starves_the_sibling(self):
        """The order-dependent half, stated once rather than hidden.

        This is the defect AC #2 exists to close, and the reason the guard
        cannot live at the engine or the queue: by the time either sees the
        exception, the sibling has already been skipped for that bar.
        """
        dispatch = RealDispatch(("sma_crossover", "momentum"), guard=None).start()
        dispatch.publish_clean_bars()

        with pytest.raises(Exception):
            dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        assert dispatch.seen["momentum"] == list(CLOSES[:-1])
        assert dispatch.seen["sma_crossover"] == list(CLOSES)


class TestTheLatchAcrossRealBars:
    """AC #9 — one record per strategy, and the strategy stays warm."""

    def test_a_strategy_that_would_raise_on_every_bar_is_recorded_once(self):
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=FakeClock())
        dispatch = RealDispatch(("sma_crossover", "momentum"), guard=guard).start()
        dispatch.publish_clean_bars()

        with capture_logs() as logs:
            for extra in range(4):
                dispatch.publish(dispatch.zero_bar_index + extra, "0.00")

        assert len(guard.failures) == 1
        assert len([e for e in logs if e["event"] == "strategy.failed"]) == 1
        assert dispatch.seen["momentum"] == list(CLOSES) + ["0.00"] * 3

    def test_the_boundary_keeps_calling_through_after_the_latch(self):
        """AC #9's call-through clause, at the real-dispatch level.

        Two assertions that look contradictory and are not, which is the whole
        point. The boundary **does** keep handing later bars to ``handle_bar``
        — a wrapper that short-circuited once latched would freeze registered
        indicators, which ``Actor.handle_bar`` updates *before* the ``RUNNING``
        check and *outside* its ``try`` (``actor.pyx:3735-3744``); measured over
        8 bars with an ``EMA(3)``, ``ema.count`` 1 rather than 8. And ``on_bar``
        **does not** run, because ``handle_bar``'s own ``if state == RUNNING``
        gate is false once the strategy is ``DEGRADED`` — which is why calling
        through costs nothing and why the degraded strategy places no orders.

        ``sma_crossover`` registers no indicators today
        (``grep -rn register_indicator src/core/strategies/`` -> zero hits), so
        the indicator half is proved at the unit tier against a stub; this is
        the real-machinery proof of the call-through itself. Story 4.4 makes the
        indicator half live here too.
        """
        guard = StrategyGuard(log=structlog.get_logger("test"), time_source=FakeClock())
        dispatch = RealDispatch(("sma_crossover", "momentum"), guard=guard).start()
        dispatch.publish_clean_bars()
        dispatch.publish(dispatch.zero_bar_index, CLOSES[-1])

        dispatch.publish(dispatch.zero_bar_index + 1, "110.00")

        assert dispatch.delivered["sma_crossover"][-1] == "110.00"
        assert dispatch.seen["sma_crossover"][-1] == "0.00"
        assert dispatch.strategies[0].state.name == "DEGRADED"
        # Still subscribed: `degrade()` is not `remove_strategy()`, which
        # measurably does not unsubscribe anyway (`bar_subs 2 -> 2`).
        assert len(dispatch.bus.subscriptions(f"data.bars.{BAR_TYPE}")) == 2
