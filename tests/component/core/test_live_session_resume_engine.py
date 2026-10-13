"""Story 4.5 — a restart across an open position, against a real ``LiveExecutionEngine``.

Component tier: Story 4.2's IB-shaped NETTING client and ``_Harness`` (a real
message bus, ``Cache``, ``Portfolio`` and ``LiveExecutionEngine``) under the
session's **own** exec config — never a ``TradingNode`` (the C-logging guard
below enforces it).

Story 4.2's framework canaries pin what Nautilus does under its own defaults:
the adapter's fabricated per-position ``FILLED`` order is imported as
``EXTERNAL`` and every mid-position restart leaves ``S / EXTERNAL /
INTERNAL-DIFF`` on the instrument; a later restart after a shrink aborts the
process. The first two classes are the twins under what a session actually runs
with (D-A): neither happens.

The rest is the chained proof (AC #1–#3): **process A** — a real strategy,
real risk and execution engines, a real ``DataEngine`` serving history — enters
and is stopped; **process B** — fresh engines over the *same* cache database,
the way a restarted process rejoins its Redis namespace — runs Nautilus's own
startup pass and Story 4.2's ``reconcile_at_startup`` against a broker holding
the position, then restarts a real strategy with the same id. The real-Redis
twin of the round trip is ``tests/integration/core/test_resume_round_trip_redis.py``.
"""

import asyncio
import itertools
import subprocess
import sys
import textwrap
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common import Environment
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.live.config import LiveExecEngineConfig
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import LiquiditySide, OmsType, OrderSide, OrderType
from nautilus_trader.model.events import OrderAccepted, OrderFilled
from nautilus_trader.model.identifiers import (
    ClientId,
    ClientOrderId,
    PositionId,
    StrategyId,
    TradeId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine
from nautilus_trader.test_kit.mocks.cache_database import MockCacheDatabase
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from nautilus_trader.trading.trader import Trader
from structlog.testing import capture_logs

from src.core.live_session_resume import (
    LEGACY_POSITION_IMPORT,
    RESUMED_EVENT,
    ResumeCheck,
    ResumeRefusedError,
    imported_position_orders,
    refuse_imported_position_orders,
)
from src.core.live_session_warmup import COMPLETED_EVENT, WarmupWatch
from src.core.live_startup_reconcile import (
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
    reconcile_at_startup,
)
from src.core.live_trade_recorder import (
    AGGREGATED_EVENT,
    POSITION_EVENTS_TOPIC,
    RecordedTrade,
    TradeRecorder,
    unix_nanos_to_utc,
)
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
from src.models.session import StrategySpec
from tests.component.core.test_live_runtime_reconcile_engine import _session_exec_config
from tests.component.core.test_live_startup_reconcile_engine import (
    ACCOUNT,
    NAUTILUS_DEFAULT_FILTER,
    NVDA,
    STRATEGY,
    TRADER,
    _Harness,
    _IBShapedClient,
    _state,
)
from tests.component.core.test_strategy_warmup_engine import (
    AAPL,
    BAR_TYPE,
    FAST,
    FLAT,
    JUMP,
    MINUTE,
    NOW,
    SLOW,
    _bar,
    _HistoryClient,
)

pytestmark = pytest.mark.component

#: Pulls the fast average under the slow one against the flat history's
#: baseline — a death cross on the first live bar of process B.
DROP = 70.0
_TRADE_IDS = itertools.count(1)


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


@pytest.fixture
def harness():
    """Story 4.2's harness with explicit engine settings (e.g. Nautilus's default)."""
    made: list[_Harness] = []

    def _make(broker=None, **engine_config) -> _Harness:
        made.append(_Harness(broker, **engine_config))
        return made[-1]

    yield _make
    for each in made:
        if not each.loop.is_closed():
            each.close()


@pytest.fixture
def session_harness():
    """A harness built with exactly the exec settings a real session gets."""
    made: list[_Harness] = []

    def _make(broker=None) -> _Harness:
        made.append(_Harness(broker, **_session_exec_config()))
        return made[-1]

    yield _make
    for each in made:
        if not each.loop.is_closed():
            each.close()


class TestTheSessionImportsTheBrokerOnce:
    """AC #2 / D-A — the resumed position is the only one on the instrument."""

    def test_the_session_config_carries_the_filter(self):
        assert _session_exec_config()["filter_unclaimed_external_orders"] is True

    def test_a_mid_position_restart_leaves_only_the_strategys_own_position(self, session_harness):
        h = session_harness({NVDA.id: (10, 100.0)})
        h.seed(NVDA, 10)

        assert h.native() is True

        assert h.open_positions() == {str(STRATEGY): Decimal("10")}
        assert h.cache.order(ClientOrderId(str(NVDA.id))) is None, "fabricated order imported"
        result = h.reconcile(_state((NVDA.id, "10", "100")))
        assert result.synthetic_positions == 0
        assert result.framework_resolved == () and result.reconcile_resolved == ()

    def test_a_holding_the_cache_cannot_attribute_is_imported_once_at_the_brokers_price(
        self, session_harness
    ):
        """Lost cache, manual trade: the position pass imports it as
        ``INTERNAL-DIFF`` — once, priced from the broker's average (F3)."""
        h = session_harness({NVDA.id: (10, 101.25)})

        assert h.native() is True

        assert h.open_positions() == {"INTERNAL-DIFF": Decimal("10")}
        (position,) = h.cache.positions_open()
        assert Decimal(str(position.avg_px_open)) == Decimal("101.25")


class TestABrokerHoldingThatCoversTheStrategyAtStartup:
    """Story 4.5, PO ruling 2026-09-28 (review Decision 2: A — startup only), on
    a real engine under the session's config: the strategy restarts holding +10
    and IBKR holds more on the same side (a share bought by hand). The
    framework imports the excess once as ``INTERNAL-DIFF``; the phase passes and
    leaves the excess to D-C. Every other disagreement still refuses."""

    def test_the_covered_excess_passes_and_is_left_for_the_resume_check(self, session_harness):
        h = session_harness({NVDA.id: (15, 100.0)})
        h.seed(NVDA, 10)

        assert h.native() is True
        result = h.reconcile(_state((NVDA.id, "15", "100")))

        assert h.open_positions() == {str(STRATEGY): Decimal("10"), "INTERNAL-DIFF": Decimal("5")}
        assert result.synthetic_positions == 1

    @pytest.mark.parametrize(
        "broker",
        [4, 0, -5],
        ids=["broker-holds-less", "broker-flat", "opposite-side"],
    )
    def test_every_other_disagreement_still_refuses_the_session(self, session_harness, broker):
        h = session_harness({NVDA.id: (broker, 100.0)} if broker else {})
        h.seed(NVDA, 10)
        assert h.native() is True
        held = ((NVDA.id, str(broker), "100"),) if broker else ()

        with pytest.raises(ReconciliationFailedError) as caught:
            h.reconcile(_state(*held))

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED


#: ``TestTheShrunkReEntryAbortCanary``'s script, under the session's **whole**
#: exec config (code review 2026-09-28: the twin used to vary the filter alone).
_SESSION_SHRUNK_REENTRY = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})
    from tests.component.core.test_live_runtime_reconcile_engine import _session_exec_config
    from tests.component.core.test_live_startup_reconcile_engine import _Harness, NVDA
    h = _Harness({{NVDA.id: (10, 100.0)}}, **_session_exec_config())
    assert h.native() is True
    print("FIRST_RESTART_OK", flush=True)
    h.client.broker[NVDA.id] = (4, 100.0)
    h.native()
    print("SECOND_RESTART_RETURNED", flush=True)
    """
)


class TestAPre45NamespaceIsRecognisedOnARealEngine:
    """D-D against an order the framework **actually** imported (code review
    2026-09-28: the predicate had only been fed string doubles). Under
    Nautilus's default the adapter's fabricated order is cached — exactly what
    a pre-4.5 namespace holds — and the check names its instrument; under the
    session's config there is nothing to find."""

    def test_the_fabricated_order_nautilus_cached_is_what_d_d_refuses(self, harness):
        h = harness({NVDA.id: (10, 100.0)}, **NAUTILUS_DEFAULT_FILTER)
        assert h.native() is True

        assert imported_position_orders(h.cache.orders()) == (str(NVDA.id),)
        with pytest.raises(ResumeRefusedError) as caught:
            refuse_imported_position_orders(h.cache, structlog.get_logger("test"))
        assert caught.value.reason == LEGACY_POSITION_IMPORT

    def test_a_namespace_built_under_the_session_config_has_none(self, session_harness):
        h = session_harness({NVDA.id: (10, 100.0)})
        h.seed(NVDA, 10)
        assert h.native() is True

        assert imported_position_orders(h.cache.orders()) == ()
        refuse_imported_position_orders(h.cache, structlog.get_logger("test"))


class TestAShrunkRestartNoLongerAborts:
    """AC #6 — the HIGH item Story 4.2 routed here (``deferred-work.md:3362``):
    the same script as ``TestTheShrunkReEntryAbortCanary``, under the session's
    own exec config. The fabricated order is never cached, so there is nothing
    for a later, smaller report to underflow."""

    def test_the_second_restart_returns(self):
        root = str(Path(__file__).resolve().parents[3])
        result = subprocess.run(
            [sys.executable, "-c", _SESSION_SHRUNK_REENTRY.format(root=root)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert "FIRST_RESTART_OK" in result.stdout, result.stderr[-2000:]
        assert "SECOND_RESTART_RETURNED" in result.stdout, result.stderr[-2000:]
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# The chained proof: process A enters, process B resumes it.
# ---------------------------------------------------------------------------


class _RecordingIBClient(_IBShapedClient):
    """Story 4.2's IB-shaped NETTING broker, recording what actually reached it.

    ``submit_order`` generates ``OrderSubmitted`` as the IB adapter does, so an
    order reads ``SUBMITTED`` through the engine's own apply path; nothing here
    fills anything — the tests do, explicitly, as the broker would.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submitted: list = []

    def submit_order(self, command) -> None:
        order = command.order
        self.submitted.append(order)
        self.generate_order_submitted(
            strategy_id=order.strategy_id,
            instrument_id=order.instrument_id,
            client_order_id=order.client_order_id,
            ts_event=self._clock.timestamp_ns(),
        )


class _Process:
    """One process run of a session: fresh engines over a shared cache database.

    ``database`` is what survives between runs — Redis in production, a
    ``MockCacheDatabase`` here (the Story 3.4 precedent); the round trip
    through the real adapter and serializer is the integration twin's.
    """

    def __init__(self, loop, database, broker: dict, *, minute: int) -> None:
        self.loop = loop
        self.clock = TestClock()
        self.clock.set_time(NOW + minute * MINUTE)
        self.msgbus = MessageBus(trader_id=TRADER, clock=self.clock)
        self.cache = Cache(database=database)
        self.cache.add_instrument(AAPL)
        self.portfolio = Portfolio(self.msgbus, self.cache, self.clock)
        self.data_engine = DataEngine(msgbus=self.msgbus, cache=self.cache, clock=self.clock)
        self.history = _HistoryClient(ClientId("NASDAQ"), self.msgbus, self.cache, self.clock)
        self.data_engine.register_client(self.history)
        self.risk_engine = RiskEngine(
            portfolio=self.portfolio, msgbus=self.msgbus, cache=self.cache, clock=self.clock
        )
        self.exec_engine = LiveExecutionEngine(
            loop=loop,
            msgbus=self.msgbus,
            cache=self.cache,
            clock=self.clock,
            config=LiveExecEngineConfig(**_session_exec_config()),
        )
        self.client = _RecordingIBClient(loop, self.msgbus, self.cache, self.clock, broker)
        self.exec_engine.register_client(self.client)
        self.exec_engine.register_default_client(self.client)
        self.client._set_connected(True)
        self.trader = Trader(
            trader_id=TRADER,
            instance_id=UUID4(),
            msgbus=self.msgbus,
            cache=self.cache,
            portfolio=self.portfolio,
            data_engine=self.data_engine,
            risk_engine=self.risk_engine,
            exec_engine=self.exec_engine,
            clock=self.clock,
            environment=Environment.BACKTEST,
        )
        self.minute = minute
        self._strategies: list = []

    def start(self, *, restored: bool) -> None:
        """``NautilusKernel`` order: load the cache, start the engines, reconcile."""
        if restored:
            self.exec_engine.load_cache()
        self.portfolio.update_account(TestEventStubs.margin_account_state(account_id=ACCOUNT))
        self.data_engine.start()
        self.risk_engine.start()
        self.exec_engine.start()
        self.tick()

    def native(self) -> bool:
        """Nautilus's own startup pass, as ``start_async`` runs it in ``node:connect``."""
        ok = self.loop.run_until_complete(
            self.exec_engine.reconcile_execution_state(timeout_secs=10)
        )
        self.portfolio.initialize_orders()
        self.portfolio.initialize_positions()
        return ok

    def reconcile(self, quantity: str, price: str):
        """Story 4.2's ``reconcile`` phase body against the broker's answer."""
        state = _state((AAPL.id, quantity, price))

        async def _read(node, *, log):
            return state

        node = SimpleNamespace(
            cache=self.cache, kernel=SimpleNamespace(exec_engine=self.exec_engine)
        )
        return self.loop.run_until_complete(
            reconcile_at_startup(
                node, log=structlog.get_logger("test"), local_before=None, read_state=_read
            )
        )

    def add(self, strategy) -> None:
        self.trader.add_strategy(strategy)
        # Under `Environment.BACKTEST` the trader hands each strategy a fresh
        # `TestClock` at 0; keep it on the process's time, as a kernel would.
        strategy.clock.set_time(self.clock.timestamp_ns())
        self._strategies.append(strategy)

    def start_strategy(self, strategy) -> None:
        end = self.clock.timestamp_ns()
        self.history.bars = [
            _bar(end - (len(FLAT) - i) * MINUTE, close) for i, close in enumerate(FLAT)
        ]
        self.trader.start_strategy(strategy.id)
        self.tick()

    def live(self, close: float) -> None:
        self.minute += 1
        now = NOW + self.minute * MINUTE
        self.clock.set_time(now)
        for strategy in self._strategies:
            strategy.clock.set_time(now)
        self.data_engine.process(_bar(now, close))
        self.tick()

    def fill(self, order, px: str) -> None:
        """The broker accepting and filling ``order`` in full, at ``px``."""
        venue_order_id = VenueOrderId(f"V-{order.client_order_id}")
        now = self.clock.timestamp_ns()
        self.exec_engine.process(
            OrderAccepted(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                account_id=ACCOUNT,
                event_id=UUID4(),
                ts_event=now,
                ts_init=now,
            )
        )
        self.exec_engine.process(
            OrderFilled(
                trader_id=order.trader_id,
                strategy_id=order.strategy_id,
                instrument_id=order.instrument_id,
                client_order_id=order.client_order_id,
                venue_order_id=venue_order_id,
                account_id=ACCOUNT,
                trade_id=TradeId(f"T-{next(_TRADE_IDS)}"),
                position_id=None,
                order_side=order.side,
                order_type=OrderType.MARKET,
                last_qty=order.quantity,
                last_px=Price.from_str(px),
                currency=USD,
                commission=Money(Decimal("1.00"), USD),
                liquidity_side=LiquiditySide.TAKER,
                event_id=UUID4(),
                ts_event=now,
                ts_init=now,
            )
        )
        self.tick()

    def seed_synthetic(self, owner: str, qty: int) -> None:
        """A pre-4.5 synthetic position (no fabricated order — D-D refuses that
        shape before the framework runs; this is the position it left)."""
        position_id = PositionId(f"{AAPL.id}-{owner}")
        order = TestExecStubs.market_order(
            instrument=AAPL,
            order_side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
            quantity=Quantity.from_int(abs(qty)),
            trader_id=TRADER,
            strategy_id=StrategyId(owner),
            client_order_id=ClientOrderId(f"O-SEED-{owner}"),
        )
        self.cache.add_order(order, position_id)
        order.apply(TestEventStubs.order_submitted(order, account_id=ACCOUNT))
        order.apply(TestEventStubs.order_accepted(order, account_id=ACCOUNT))
        fill = TestEventStubs.order_filled(
            order, AAPL, account_id=ACCOUNT, position_id=position_id, last_px=Price.from_str("130")
        )
        order.apply(fill)
        self.cache.update_order(order)
        self.cache.add_position(Position(AAPL, fill), OmsType.NETTING)
        self.portfolio.initialize_positions()

    def tick(self) -> None:
        """``LiveExecutionEngine`` enqueues commands and events; let it drain."""
        for _ in range(3):
            self.loop.run_until_complete(asyncio.sleep(0))

    def stop(self) -> None:
        self.exec_engine.stop()
        self.tick()

    @property
    def started_at(self):
        return self.clock.utc_now()

    def own_positions(self, strategy) -> dict[str, Decimal]:
        return {
            str(p.id): p.signed_decimal_qty()
            for p in self.cache.positions_open(strategy_id=strategy.id)
        }


def _crossover():
    return SMACrossover(
        SMAConfig(instrument_id=AAPL.id, bar_type=BAR_TYPE, fast_period=FAST, slow_period=SLOW)
    )


def _momentum():
    return SMAMomentum(
        SMAMomentumConfig(
            instrument_id=AAPL.id,
            bar_type=BAR_TYPE,
            trade_size=Decimal("10"),
            order_id_tag="000",
            fast_period=FAST,
            slow_period=SLOW,
        )
    )


BUILT_INS = [pytest.param(_crossover, id="sma_crossover"), pytest.param(_momentum, id="momentum")]
SPEC = StrategySpec(
    strategy_id="sma_crossover",
    parameters={},
    bar_types=(str(BAR_TYPE),),
)


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
    asyncio.set_event_loop(None)


def _process_a_enters(loop, database, make):
    """Process A: a warm strategy enters on a golden cross, is filled, is stopped."""
    a = _Process(loop, database, {}, minute=0)
    a.start(restored=False)
    strategy = make()
    a.add(strategy)
    a.start_strategy(strategy)
    a.live(JUMP)
    (entry,) = a.client.submitted
    assert entry.side == OrderSide.BUY
    a.fill(entry, "130.00")
    held = a.own_positions(strategy)
    assert list(held.values()) == [entry.quantity.as_decimal()], "process A did not enter"
    a.trader.stop_strategy(strategy.id)
    a.tick()
    assert a.client.submitted == [entry], "stopping submitted an order (Story 3.1)"
    a.stop()
    return entry, strategy.id


def _process_b_resumes(loop, database, make, entry, *, synthetic=(), watch=None):
    """Process B: the framework's pass, the ``reconcile`` phase, the resume
    check, then the same strategy restarted — history served, subscribed.

    With ``watch``, the strategy is instrumented the runner's way (before
    ``add_strategy``) and its decisions are marked in the log: ``test.on_bar``
    for every live bar it acts on, ``test.order_submitted`` for every order
    that reaches the broker."""
    quantity = str(entry.quantity.as_decimal())
    b = _Process(loop, database, {AAPL.id: (int(quantity), 130.0)}, minute=100)
    b.start(restored=True)
    for owner, qty in synthetic:
        b.seed_synthetic(owner, qty)
    assert b.native() is True
    result = b.reconcile(quantity, "130.00")
    strategy = make()
    if watch is not None:
        watch.instrument(strategy, spec_strategy_id=SPEC.strategy_id)
        _mark_decisions(strategy, b.client)
    b.add(strategy)
    resume = ResumeCheck(b.cache, result, b.started_at, structlog.get_logger("test"))
    resume.refuse_unowned(SPEC)
    resume.note_resumed(strategy)
    recorded: list[RecordedTrade] = []
    recorder = TradeRecorder(b.cache, structlog.get_logger("test"), sink=_sink(recorded))
    b.msgbus.subscribe(POSITION_EVENTS_TOPIC, recorder.handle_position_event)
    b.start_strategy(strategy)
    return b, strategy, result, recorded


def _sink(recorded: list):
    def _persist(trade: RecordedTrade) -> bool:
        recorded.append(trade)
        return True

    return _persist


def _mark_decisions(strategy, client) -> None:
    """Log a marker for each live bar the strategy acts on and each order that
    reaches the broker, so one captured log holds the whole ordering."""
    log = structlog.get_logger("test")
    on_bar, submit_order = strategy.on_bar, client.submit_order

    def marked_on_bar(bar):
        log.info("test.on_bar")
        return on_bar(bar)

    def marked_submit_order(command):
        log.info("test.order_submitted")
        return submit_order(command)

    strategy.on_bar = marked_on_bar
    client.submit_order = marked_submit_order


@pytest.mark.parametrize("make", BUILT_INS)
class TestAStrategyResumesMidPosition:
    """AC #1 / #2 — the restarted strategy knows it is long and hunts the exit."""

    def test_the_resumed_position_is_the_one_the_broker_reports_and_the_only_one(self, loop, make):
        database = MockCacheDatabase()
        entry, strategy_id = _process_a_enters(loop, database, make)

        with capture_logs() as logs:
            b, strategy, result, _ = _process_b_resumes(loop, database, make, entry)

        assert strategy.id == strategy_id, "the restarted strategy has a different id"
        held = {str(p.strategy_id): p.signed_decimal_qty() for p in b.cache.positions_open()}
        assert held == {str(strategy_id): entry.quantity.as_decimal()}
        assert result.synthetic_positions == 0
        (resumed,) = [e for e in logs if e["event"] == RESUMED_EVENT]
        assert resumed["quantity"] == resumed["broker_quantity"]

    def test_a_same_side_signal_submits_nothing(self, loop, make):
        database = MockCacheDatabase()
        entry, _ = _process_a_enters(loop, database, make)
        b, _strategy, _result, _ = _process_b_resumes(loop, database, make, entry)

        b.live(JUMP)

        assert b.client.submitted == [], "the resumed strategy entered again"

    def test_the_opposite_signal_exits_exactly_its_own_position(self, loop, make):
        database = MockCacheDatabase()
        entry, strategy_id = _process_a_enters(loop, database, make)
        b, strategy, _result, _ = _process_b_resumes(loop, database, make, entry)

        b.live(DROP)

        (exit_order,) = b.client.submitted
        assert exit_order.side == OrderSide.SELL
        assert exit_order.quantity == entry.quantity
        assert exit_order.strategy_id == strategy_id

    def test_beside_a_pre_4_5_triple_it_still_exits_only_its_own(self, loop, make):
        """D-B: ``EXTERNAL +N / INTERNAL-DIFF −N`` beside the strategy (the shape a
        pre-4.5 restart left) is never acted on — one exit, of its own size."""
        database = MockCacheDatabase()
        entry, _ = _process_a_enters(loop, database, make)
        qty = int(entry.quantity.as_decimal())
        b, _strategy, _result, _ = _process_b_resumes(
            loop, database, make, entry, synthetic=(("EXTERNAL", qty), ("INTERNAL-DIFF", -qty))
        )

        b.live(DROP)

        assert [(o.side, o.quantity) for o in b.client.submitted] == [
            (OrderSide.SELL, entry.quantity)
        ]


@pytest.mark.parametrize("make", BUILT_INS)
class TestTheRoundTripReadsAsOneTrade:
    """AC #3 — no entry or exit generated by the interruption."""

    def test_one_trade_with_process_as_entry_and_process_bs_exit(self, loop, make):
        database = MockCacheDatabase()
        entry, strategy_id = _process_a_enters(loop, database, make)
        b, _strategy, _result, recorded = _process_b_resumes(loop, database, make, entry)

        with capture_logs() as logs:
            b.live(DROP)
            (exit_order,) = b.client.submitted
            b.fill(exit_order, "70.00")

        (trade,) = recorded
        assert trade.strategy_id == str(strategy_id)
        assert trade.trade.venue_order_id == str(entry.client_order_id), "entry not from A"
        # Process A filled its entry on its first live bar, at NOW + 1 minute.
        assert trade.trade.entry_timestamp == unix_nanos_to_utc(NOW + MINUTE)
        assert trade.trade.client_order_id == str(exit_order.client_order_id)
        assert trade.trade.entry_price == Decimal("130.00000000")
        assert trade.trade.exit_price == Decimal("70.00000000")
        assert trade.trade.quantity == entry.quantity.as_decimal()
        assert trade.trade.commission_amount == Decimal("2.00")
        assert trade.fill_count == 2
        aggregated = [e for e in logs if e["event"] == AGGREGATED_EVENT]
        assert [e["strategy_id"] for e in aggregated] == [str(strategy_id)]

    def test_the_first_live_decision_follows_reconciliation_and_warmup(self, loop, make):
        """AC #4 in one chain (code review 2026-09-28): ``reconcile.ok`` →
        ``strategy.resumed`` → ``warmup.completed`` → the first bar the strategy
        acts on → the first order — and nothing reaches the broker before the
        warm-up has completed."""
        database = MockCacheDatabase()
        entry, _ = _process_a_enters(loop, database, make)
        watch = WarmupWatch(log=structlog.get_logger("test"), deadline_seconds=75.0)

        with capture_logs() as logs:
            b, _strategy, _result, _ = _process_b_resumes(loop, database, make, entry, watch=watch)
            b.live(DROP)

        wanted = (OK_EVENT, RESUMED_EVENT, COMPLETED_EVENT, "test.on_bar", "test.order_submitted")
        assert [e["event"] for e in logs if e["event"] in wanted] == list(wanted)

    def test_across_both_processes_only_the_entry_and_the_exit_reach_the_broker(self, loop, make):
        database = MockCacheDatabase()
        entry, _ = _process_a_enters(loop, database, make)
        b, _strategy, _result, _ = _process_b_resumes(loop, database, make, entry)

        b.live(JUMP)
        b.live(DROP)
        b.live(DROP)

        assert len(b.client.submitted) == 1, "the restart generated an order"
