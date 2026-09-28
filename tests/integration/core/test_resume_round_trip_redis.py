"""The real-Redis twin of Story 4.5's resume round trip (AC #3).

Integration tier under ``--forked``: a real ``CacheDatabaseAdapter`` against a
real Redis, built the way ``NautilusKernel`` builds it — a fresh
``instance_id`` per process run, the same session-derived ``trader_id`` — the
``test_live_order_survives_restart.py`` shape (Story 3.4). The component tier
(``test_live_session_resume_engine.py``) proves the resume against
``MockCacheDatabase``, which hands back the very objects it was given; this file
proves the one thing it cannot: that a ``Position`` opened in one process run
comes back through the real adapter and msgpack serializer **whole** — its
strategy id, its opening order, both runs' fills and commissions — so the
trade closed in the next run reads as one round trip (Task 1.3 measured it
before this test was written).

No ``TradingNode`` is constructed and no broker is involved.
"""

from decimal import Decimal
from itertools import count
from uuid import uuid4

import pytest
import structlog
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
from nautilus_trader.model.events import OrderAccepted, OrderFilled, OrderSubmitted
from nautilus_trader.model.identifiers import TradeId, TraderId, VenueOrderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from src.config import RedisSettings
from src.core.live_cache import RedisUnreachableError, build_cache_config, check_redis_reachable
from src.core.live_session_node import materialise_strategy
from src.core.live_trade_recorder import POSITION_EVENTS_TOPIC, RecordedTrade, TradeRecorder
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from tests.integration.core.test_live_order_survives_restart import (
    AAPL_EQUITY,
    SMA_SPEC,
    _adapter,
    _new_engines,
)

pytestmark = pytest.mark.integration

_TRADES = count(1)


@pytest.fixture(scope="module", autouse=True)
def _require_redis():
    """Skip cleanly without Redis — CI has none (``test_live_cache_namespace.py``)."""
    settings = RedisSettings(_env_file=None)
    try:
        check_redis_reachable(settings.redis_host, settings.redis_port, timeout=2.0)
    except RedisUnreachableError as exc:
        pytest.skip(f"Redis is not reachable, skipping the resume round trip: {exc}")


def _strategy() -> SMACrossover:
    """The same spec in both runs: ``order_id_tag`` resolves to ``000`` each time."""
    return SMACrossover(
        config=SMAConfig(
            instrument_id=AAPL_EQUITY.id,
            bar_type=materialise_strategy(SMA_SPEC).bar_type,
            fast_period=2,
            slow_period=3,
            order_id_tag="000",
        )
    )


def _run(engines: dict, strategy) -> None:
    engines["cache"].add_account(TestExecStubs.margin_account())
    engines["trader"].add_strategy(strategy)
    engines["risk_engine"].start()
    engines["exec_engine"].start()


def _fill(engines: dict, order, px: str, commission: str, ts: int) -> None:
    """The broker accepting and filling ``order`` in full."""
    venue_order_id = VenueOrderId(f"V-{order.client_order_id}")
    common = {
        "trader_id": order.trader_id,
        "strategy_id": order.strategy_id,
        "instrument_id": order.instrument_id,
        "client_order_id": order.client_order_id,
        "account_id": TestExecStubs.margin_account().id,
        "ts_event": ts,
        "ts_init": ts,
    }
    engine = engines["exec_engine"]
    engine.process(OrderSubmitted(event_id=UUID4(), **common))
    engine.process(OrderAccepted(venue_order_id=venue_order_id, event_id=UUID4(), **common))
    engine.process(
        OrderFilled(
            venue_order_id=venue_order_id,
            trade_id=TradeId(f"T-{next(_TRADES)}"),
            position_id=None,
            order_side=order.side,
            order_type=OrderType.MARKET,
            last_qty=order.quantity,
            last_px=Price.from_str(px),
            currency=USD,
            commission=Money(Decimal(commission), USD),
            liquidity_side=LiquiditySide.TAKER,
            event_id=UUID4(),
            **common,
        )
    )


class TestAPositionResumesWholeThroughARealRedis:
    def test_the_round_trip_across_two_process_runs_reads_as_one_trade(self):
        trader_id = TraderId(f"PAPER-{uuid4().hex[:8]}")
        config = build_cache_config(RedisSettings(_env_file=None))
        opened_at, closed_at = 1_790_344_800_000_000_000, 1_790_602_200_000_000_000

        # Process A: enter 10 at 100.00, commission 1.25, then the process ends.
        adapter_a = _adapter(trader_id, config)
        try:
            engines_a = _new_engines(trader_id, adapter_a)
            strategy_a = _strategy()
            _run(engines_a, strategy_a)
            entry = strategy_a.order_factory.market(
                AAPL_EQUITY.id, OrderSide.BUY, Quantity.from_int(10)
            )
            strategy_a.submit_order(entry)
            _fill(engines_a, entry, "100.00", "1.25", opened_at)
            assert [p.quantity for p in engines_a["cache"].positions_open()] == [
                Quantity.from_int(10)
            ]
        finally:
            adapter_a.close()

        # Process B: a new adapter (fresh instance_id), the same trader_id.
        adapter_b = _adapter(trader_id, config)
        try:
            engines_b = _new_engines(trader_id, adapter_b)
            engines_b["exec_engine"].load_cache()
            strategy_b = _strategy()
            engines_b["trader"].add_strategy(strategy_b)
            engines_b["risk_engine"].start()
            engines_b["exec_engine"].start()
            recorded: list[RecordedTrade] = []

            def sink(trade: RecordedTrade) -> bool:
                recorded.append(trade)
                return True

            recorder = TradeRecorder(engines_b["cache"], structlog.get_logger("test"), sink=sink)
            engines_b["trader"]._msgbus.subscribe(
                POSITION_EVENTS_TOPIC, recorder.handle_position_event
            )

            # The restarted strategy's own book is the position process A opened.
            (resumed,) = engines_b["cache"].positions_open(
                instrument_id=AAPL_EQUITY.id, strategy_id=strategy_b.id
            )
            assert resumed.opening_order_id == entry.client_order_id

            # Its exit: 10 at 105.00, commission 1.30.
            exit_order = strategy_b.order_factory.market(
                AAPL_EQUITY.id, OrderSide.SELL, Quantity.from_int(10)
            )
            strategy_b.submit_order(exit_order, position_id=resumed.id)
            _fill(engines_b, exit_order, "105.00", "1.30", closed_at)
        finally:
            adapter_b.close()

        (trade,) = recorded
        assert trade.strategy_id == str(strategy_b.id)
        assert trade.trade.venue_order_id == str(entry.client_order_id), "entry not from run A"
        assert trade.trade.client_order_id == str(exit_order.client_order_id)
        assert trade.trade.entry_price == Decimal("100.00000000")
        assert trade.trade.exit_price == Decimal("105.00000000")
        assert trade.trade.quantity == Decimal("10.00000000")
        assert trade.trade.commission_amount == Decimal("2.55"), "a run's commission was lost"
        assert trade.fill_count == 2
        assert trade.holding_period_seconds == (closed_at - opened_at) // 1_000_000_000
