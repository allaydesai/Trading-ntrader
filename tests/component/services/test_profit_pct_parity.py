"""Both production ``profit_pct`` formulas store the same sign (Story 5.6, debt item 1).

``profit_pct`` is side-unaware on the backtest path —
``(exit − entry) / entry * 100`` whatever the side, so a profitable SHORT reads
negative — and the live recorder reproduces it verbatim for parity (Story 3.5,
Hazard #6). Story 5.6 dispositions it rather than fixing it (PO instruction): a
fix must change both sides together, and the views are frozen (AR44).

The integration tests read hand-seeded rows, so they prove only that the views
show the stored sign (code review P3). This pins the two **writers**: one real
closed Nautilus ``Position`` goes down both paths —

- the backtest's: ``ReportProvider.generate_positions_report([position])`` →
  ``save_trades_from_positions`` (its repository captures the row, no database);
- the recorder's: ``aggregate_closed_position`` over the position's own
  ``PositionClosed`` event.

A one-sided fix — either formula made side-aware alone — turns this red.
"""

import itertools
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
from nautilus_trader.model.events.order import OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs

from src.core.live_trade_recorder import aggregate_closed_position
from src.db.models.trade import Trade
from src.services.backtest_persistence import BacktestPersistenceService

pytestmark = pytest.mark.component

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
STRATEGY_ID = StrategyId("SMACrossover-000")
_IDS = itertools.count()


def _fill(order_id: ClientOrderId, side: OrderSide, px: str, ts: int) -> OrderFilled:
    return OrderFilled(
        trader_id=TraderId("TESTER-000"),
        strategy_id=STRATEGY_ID,
        instrument_id=AAPL.id,
        client_order_id=order_id,
        venue_order_id=VenueOrderId(f"V-{order_id}"),
        account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
        trade_id=TradeId(f"T-{next(_IDS)}"),
        position_id=PositionId(f"{AAPL.id}-{STRATEGY_ID}"),
        order_side=side,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_int(10),
        last_px=Price.from_str(px),
        currency=USD,
        commission=Money(Decimal("1.00"), USD),
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=ts,
        ts_init=ts,
    )


def _round_trip(side: OrderSide, entry: str, exit_: str) -> Position:
    """10 shares opened at ``entry`` and closed at ``exit_``: a real, closed position."""
    opposite = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
    position = Position(AAPL, _fill(ClientOrderId(f"O-{next(_IDS)}"), side, entry, 1_000_000_000))
    position.apply(_fill(ClientOrderId(f"O-{next(_IDS)}"), opposite, exit_, 2_000_000_000))
    assert position.is_closed
    return position


async def _backtest_row(position: Position) -> Trade:
    repository = AsyncMock()

    saved = await BacktestPersistenceService(repository).save_trades_from_positions(
        backtest_run_id=1,
        positions_report_df=ReportProvider.generate_positions_report([position]),
    )

    assert saved == 1
    [[rows], _] = repository.bulk_create_trades.call_args
    [row] = rows
    return row


class TestBothWritersStoreTheSameSign:
    async def test_a_profitable_short_reads_negative_on_both_paths(self):
        """Sold 10 at 100, bought back at 95: a 5-point win that reads −5%."""
        position = _round_trip(OrderSide.SELL, "100.00", "95.00")

        backtest = await _backtest_row(position)
        recorded = aggregate_closed_position(TestEventStubs.position_closed(position), position)

        assert backtest.profit_loss is not None and backtest.profit_loss > 0
        assert recorded.profit_loss > 0
        assert backtest.profit_pct == recorded.profit_pct == Decimal("-5")

    async def test_a_profitable_long_reads_positive_on_both_paths(self):
        """The control: the same formula, the same sign, when the side agrees with it."""
        position = _round_trip(OrderSide.BUY, "100.00", "105.00")

        backtest = await _backtest_row(position)
        recorded = aggregate_closed_position(TestEventStubs.position_closed(position), position)

        assert backtest.profit_loss is not None and backtest.profit_loss > 0
        assert recorded.profit_loss > 0
        assert backtest.profit_pct == recorded.profit_pct == Decimal("5")
