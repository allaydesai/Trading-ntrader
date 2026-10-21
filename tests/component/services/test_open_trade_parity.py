"""A sealed session's open trade is the backtest's open trade (Story 5.5, AC 5.5b).

AC 5.5b: each open position is persisted as an open trade with a null exit —
"the same shape backtests already use for positions open at the end of data".
That shape has one source, ``BacktestPersistenceService.save_trades_from_positions``
over Nautilus's own positions report. Here one **real** open ``Position`` goes
down both paths:

- the backtest's: ``ReportProvider.generate_positions_report([position])`` →
  ``save_trades_from_positions`` (its repository captures the rows, no database);
- the seal's: the session view's ``Position`` → ``OpenLeg`` conversion →
  ``open_trade``.

Every column must agree except the owner columns. A partly reduced position is
where they part, by design (PO ruling R1a, D-B): pinned row by row below.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from nautilus_trader.analysis.reporter import ReportProvider
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import AccountId, PositionId, StrategyId, TradeId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from src.core import live_session_view
from src.db.models.trade import Trade
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.seal_open_positions import open_trade

pytestmark = pytest.mark.component

NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
OPENED_NS = 1_790_000_000_000_000_000
SESSION_PK = 7
#: The columns that say who owns a row — the only ones the two paths may differ in.
OWNER_COLUMNS = {"id", "backtest_run_id", "session_id", "created_at"}
COLUMNS = [column.name for column in Trade.__table__.columns if column.name not in OWNER_COLUMNS]


def _fill(side: OrderSide, qty: int, px: str, n: int, commission: str) -> OrderFilled:
    """A real fill with a real commission (the stub takes none, so it is set
    through the event's own dict round trip)."""
    order = TestExecStubs.market_order(
        instrument=NVDA,
        order_side=side,
        quantity=Quantity.from_int(qty),
        strategy_id=StrategyId("S-001"),
    )
    filled = TestEventStubs.order_filled(
        order,
        instrument=NVDA,
        account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
        trade_id=TradeId(f"T-{n}"),
        position_id=PositionId("NVDA.NASDAQ-S-001"),
        last_px=Price.from_str(px),
        ts_event=OPENED_NS + n,
    )
    values = OrderFilled.to_dict(filled)
    values["commission"] = commission
    return OrderFilled.from_dict(values)


async def _backtest_row(position: Position) -> Trade:
    repository = AsyncMock()
    service = BacktestPersistenceService(repository)

    saved = await service.save_trades_from_positions(
        backtest_run_id=1,
        positions_report_df=ReportProvider.generate_positions_report([position]),
    )

    assert saved == 1
    [[rows], _] = repository.bulk_create_trades.call_args
    [row] = rows
    return row


def _seal_row(position: Position) -> Trade:
    return open_trade(live_session_view._leg(position), SESSION_PK)


def _columns(row: Trade) -> dict:
    return {name: getattr(row, name) for name in COLUMNS}


class TestTheSealsOpenTradeIsTheBacktests:
    async def test_an_open_position_becomes_the_same_row_on_both_paths(self):
        position = Position(NVDA, _fill(OrderSide.BUY, 10, "187.37", 1, "1.25 USD"))

        backtest, seal = await _backtest_row(position), _seal_row(position)

        assert _columns(seal) == _columns(backtest)
        assert seal.exit_price is None and seal.exit_timestamp is None
        assert seal.profit_loss == Decimal("-1.25")  # an open leg's P&L is its commission
        assert seal.entry_timestamp == datetime(2026, 9, 21, 14, 13, 20, tzinfo=UTC)
        assert (seal.session_id, seal.backtest_run_id) == (SESSION_PK, None)
        assert (backtest.session_id, backtest.backtest_run_id) == (None, 1)

    async def test_a_short_open_position_matches_too(self):
        position = Position(NVDA, _fill(OrderSide.SELL, 3, "101.10", 1, "0.35 USD"))

        assert _columns(_seal_row(position)) == _columns(await _backtest_row(position))

    async def test_both_paths_name_the_owning_strategy(self):
        """MCP phase 5b: equal-and-None would pass the comparison above, so the value
        is pinned against the real positions report's ``strategy_id`` column."""
        position = Position(NVDA, _fill(OrderSide.BUY, 10, "187.37", 1, "1.25 USD"))

        backtest, seal = await _backtest_row(position), _seal_row(position)

        assert backtest.strategy_id == seal.strategy_id == "S-001"

    def test_the_comparison_is_not_vacuous(self):
        assert {
            "quantity",
            "entry_price",
            "exit_price",
            "order_side",
            "trade_id",
            "strategy_id",
        } <= set(COLUMNS)


class TestAPartlyReducedPositionIsWhereThePathsPart:
    """D-B (PO ruling R1a): a strategy that sold 4 of 10 still holds 6. The
    backtest's open row says 10 and carries the reduction's price as an exit
    price with no exit time — a row 5.2's reconstruction would refuse as
    "neither open nor closed". The seal's row says what remains and has no
    exit. AR12/AR44 leave the backtest path as it is (deferred-work)."""

    async def test_quantity_exit_price_and_profit_pct_are_the_only_differences(self):
        position = Position(NVDA, _fill(OrderSide.BUY, 10, "187.37", 1, "1.25 USD"))
        position.apply(_fill(OrderSide.SELL, 4, "190.00", 2, "0.50 USD"))

        backtest, seal = _columns(await _backtest_row(position)), _columns(_seal_row(position))

        differing = {name for name in COLUMNS if backtest[name] != seal[name]}
        assert differing == {"quantity", "exit_price", "profit_pct"}
        assert (backtest["quantity"], seal["quantity"]) == (Decimal("10"), Decimal("6"))
        assert backtest["exit_price"] == Decimal("190.00000000")
        assert backtest["exit_timestamp"] is None
        assert seal["exit_price"] is None and seal["profit_pct"] is None
