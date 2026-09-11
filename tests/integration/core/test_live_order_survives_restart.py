"""The real-Redis twin of the restart/resume restore (Story 3.4, AC #3/#4).

Integration tier under ``--forked``: constructs a real ``CacheDatabaseAdapter``
against a real Redis, the same shape ``test_live_cache_namespace.py`` uses —
Rust-backed, opens a real socket, and initialises Nautilus's C logging. The
component tier (``test_live_order_recovery.py``) proves the restore mechanism
against ``MockCacheDatabase``; this file proves the one thing a mock cannot:
that the round trip through the real ``CacheDatabaseAdapter`` and msgpack
serializer actually preserves order state byte-for-byte, because the one
live fill this project has had died in exactly that serializer
(``src/core/live_exec_avg_px.py``).

No ``TradingNode`` is constructed — a live IB gateway is not needed to prove
the restore. Every trader ID is derived from a fresh ``uuid4()`` so no key
written here can ever collide with another run
(``test_live_cache_namespace.py``'s namespace-isolation discipline).
"""

from uuid import uuid4

import msgspec
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.common import Environment
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine, DataEngineConfig
from nautilus_trader.execution.engine import ExecEngineConfig, ExecutionEngine
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import TraderId, VenueOrderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine, RiskEngineConfig
from nautilus_trader.serialization.serializer import MsgSpecSerializer
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.trading.trader import Trader

from src.config import RedisSettings
from src.core.live_cache import RedisUnreachableError, build_cache_config, check_redis_reachable
from src.core.live_session_node import materialise_strategy
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.models.session import StrategySpec

pytestmark = pytest.mark.integration

AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
SMA_SPEC = StrategySpec(
    strategy_id="sma_crossover",
    parameters={"fast_period": 2, "slow_period": 3},
    bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",),
)


def _redis_settings() -> RedisSettings:
    return RedisSettings(_env_file=None)


@pytest.fixture(scope="module", autouse=True)
def _require_redis():
    """Skip the module cleanly when Redis is not running.

    Copied from ``test_live_cache_namespace.py:44-57`` — load-bearing rather
    than a courtesy: CI has no Redis service and would otherwise fail every
    build.
    """
    settings = _redis_settings()
    try:
        check_redis_reachable(settings.redis_host, settings.redis_port, timeout=2.0)
    except RedisUnreachableError as exc:
        pytest.skip(f"Redis is not reachable, skipping order-survives-restart tests: {exc}")


def _adapter(trader_id: TraderId, config) -> CacheDatabaseAdapter:
    """Built the way ``NautilusKernel`` does (``kernel.py:303-311``) — a
    fresh ``instance_id`` per call, exactly as a new process run gets.
    """
    return CacheDatabaseAdapter(
        trader_id=trader_id,
        instance_id=UUID4(),
        serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True),
        config=config,
    )


def _new_engines(trader_id: TraderId, database) -> dict:
    clock = TestClock()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache(database=database)
    cache.add_instrument(AAPL_EQUITY)
    portfolio = Portfolio(msgbus, cache, clock)
    data_engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock, config=DataEngineConfig())
    risk_engine = RiskEngine(
        portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock, config=RiskEngineConfig()
    )
    exec_engine = ExecutionEngine(
        msgbus=msgbus, cache=cache, clock=clock, config=ExecEngineConfig()
    )
    trader = Trader(
        trader_id=trader_id,
        instance_id=UUID4(),
        msgbus=msgbus,
        cache=cache,
        portfolio=portfolio,
        data_engine=data_engine,
        risk_engine=risk_engine,
        exec_engine=exec_engine,
        clock=clock,
        environment=Environment.BACKTEST,
    )
    return {
        "cache": cache,
        "risk_engine": risk_engine,
        "exec_engine": exec_engine,
        "trader": trader,
    }


def _duplicate_of(restored, strategy_id, trader_id: TraderId) -> MarketOrder:
    """The corrected duplicate-resubmission shape (measured, Task 1 /
    ``test_live_order_recovery.py``'s module docstring, correction #2): a
    **new** ``INITIALIZED`` order carrying the restored order's
    ``client_order_id`` — resubmitting the restored object itself raises
    ``ValueError`` on the INITIALIZED precondition, never reaching the
    duplicate check.
    """
    return MarketOrder(
        trader_id=trader_id,
        strategy_id=strategy_id,
        instrument_id=restored.instrument_id,
        client_order_id=restored.client_order_id,
        order_side=restored.side,
        quantity=restored.quantity,
        init_id=UUID4(),
        ts_init=0,
    )


class TestAnOrderSurvivesARealRedisRestart:
    def test_the_reloaded_order_is_byte_equal_but_a_different_object(self):
        trader_id = TraderId(f"PAPER-{uuid4().hex[:8]}")
        config = build_cache_config(_redis_settings())

        # Process A
        adapter_a = _adapter(trader_id, config)
        engines_a = _new_engines(trader_id, adapter_a)
        strategy_a = SMACrossover(
            config=SMAConfig(
                instrument_id=AAPL_EQUITY.id,
                bar_type=materialise_strategy(SMA_SPEC).bar_type,
                fast_period=2,
                slow_period=3,
                order_id_tag="000",
            )
        )
        try:
            engines_a["trader"].add_strategy(strategy_a)
            engines_a["risk_engine"].start()
            engines_a["exec_engine"].start()
            order = strategy_a.order_factory.market(
                instrument_id=AAPL_EQUITY.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(7),
            )
            strategy_a.submit_order(order)
            engines_a["exec_engine"].process(TestEventStubs.order_submitted(order))
            engines_a["exec_engine"].process(
                TestEventStubs.order_accepted(order, venue_order_id=VenueOrderId("1"))
            )
            assert order.status_string() == "ACCEPTED"
        finally:
            adapter_a.close()

        # Process B — a brand-new adapter (fresh instance_id), same trader_id.
        adapter_b = _adapter(trader_id, config)
        try:
            engines_b = _new_engines(trader_id, adapter_b)
            engines_b["exec_engine"].load_cache()

            seen: dict = {}

            class _ProbeStrategy(SMACrossover):
                def on_start(self) -> None:
                    seen["open"] = tuple(self.cache.orders_open(strategy_id=self.id))
                    seen["count_before_start"] = self.order_factory.get_client_order_id_count()
                    super().on_start()

            probe = _ProbeStrategy(
                config=SMAConfig(
                    instrument_id=AAPL_EQUITY.id,
                    bar_type=materialise_strategy(SMA_SPEC).bar_type,
                    fast_period=2,
                    slow_period=3,
                    strategy_id="SMACrossover",
                    order_id_tag="000",
                )
            )
            engines_b["trader"].add_strategy(probe)
            engines_b["risk_engine"].start()
            engines_b["exec_engine"].start()
            probe.start()

            assert len(seen["open"]) == 1
            restored = seen["open"][0]
            assert restored is not order, "the reloaded order must be a distinct Python object"
            assert restored.client_order_id == order.client_order_id
            assert restored.venue_order_id == order.venue_order_id
            assert restored.quantity == order.quantity
            assert restored.status_string() == order.status_string() == "ACCEPTED"
            assert seen["count_before_start"] == 1

            next_order = probe.order_factory.market(
                instrument_id=AAPL_EQUITY.id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(1),
            )
            assert str(next_order.client_order_id).endswith("-000-2")

            in_cache = engines_b["cache"].order(order.client_order_id)
            duplicate = _duplicate_of(in_cache, probe.id, trader_id)
            probe.submit_order(duplicate)

            assert duplicate.status_string() == "DENIED"
            assert "duplicate" in duplicate.last_event.reason
        finally:
            adapter_b.close()
