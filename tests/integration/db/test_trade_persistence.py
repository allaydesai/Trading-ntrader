"""
Integration tests for trade persistence to database.

Tests cover:
- Saving trades from FillReport data
- Bulk trade insertion performance
- Database constraints and relationships
"""

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade as TradeDB
from src.models.trade import TradeCreate, calculate_trade_metrics


@pytest.mark.asyncio
class TestTradePersistence:
    """Test saving trades to database."""

    async def test_save_single_trade(self, db_session: AsyncSession):
        """Test saving a single trade to database."""
        # Create a backtest run first
        backtest_run = BacktestRun(
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 1, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="test",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={},
        )
        db_session.add(backtest_run)
        await db_session.flush()

        # Create trade data
        trade_data = TradeCreate(
            backtest_run_id=backtest_run.id,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("160.00"),
            commission_amount=Decimal("5.00"),
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        # Calculate metrics
        metrics = calculate_trade_metrics(trade_data)

        # Create database model
        trade_db = TradeDB(
            backtest_run_id=trade_data.backtest_run_id,
            instrument_id=trade_data.instrument_id,
            trade_id=trade_data.trade_id,
            venue_order_id=trade_data.venue_order_id,
            client_order_id=trade_data.client_order_id,
            order_side=trade_data.order_side,
            quantity=trade_data.quantity,
            entry_price=trade_data.entry_price,
            exit_price=trade_data.exit_price,
            commission_amount=trade_data.commission_amount,
            commission_currency=trade_data.commission_currency,
            fees_amount=trade_data.fees_amount,
            profit_loss=metrics["profit_loss"],
            profit_pct=metrics["profit_pct"],
            holding_period_seconds=metrics["holding_period_seconds"],
            entry_timestamp=trade_data.entry_timestamp,
            exit_timestamp=trade_data.exit_timestamp,
        )

        db_session.add(trade_db)
        await db_session.commit()

        # Verify trade was saved
        result = await db_session.execute(
            select(TradeDB).where(TradeDB.id == trade_db.id)
        )
        saved_trade = result.scalar_one()

        assert saved_trade.instrument_id == "AAPL"
        assert saved_trade.profit_loss == Decimal("995.00")
        assert saved_trade.holding_period_seconds == 3600

    async def test_save_trades_with_relationship(self, db_session: AsyncSession):
        """Test that trades are properly linked to backtest run."""
        # Create a backtest run
        backtest_run = BacktestRun(
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 1, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="test",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={},
        )
        db_session.add(backtest_run)
        await db_session.flush()

        # Create multiple trades
        for i in range(3):
            trade_db = TradeDB(
                backtest_run_id=backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{i}",
                venue_order_id=f"order-{i}",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("160.00"),
                profit_loss=Decimal("1000.00"),
                profit_pct=Decimal("6.67"),
                holding_period_seconds=3600,
                entry_timestamp=datetime(2025, 1, 1, 10 + i, 0, 0, tzinfo=timezone.utc),
                exit_timestamp=datetime(2025, 1, 1, 11 + i, 0, 0, tzinfo=timezone.utc),
            )
            db_session.add(trade_db)

        await db_session.commit()

        # Verify relationship
        result = await db_session.execute(
            select(BacktestRun).where(BacktestRun.id == backtest_run.id)
        )
        run_with_trades = result.scalar_one()

        assert len(run_with_trades.trades) == 3
        assert all(t.backtest_run_id == backtest_run.id for t in run_with_trades.trades)

    async def test_cascade_delete_trades(self, db_session: AsyncSession):
        """Test that trades are deleted when backtest run is deleted."""
        # Create backtest run with trades
        backtest_run = BacktestRun(
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 1, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="test",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={},
        )
        db_session.add(backtest_run)
        await db_session.flush()

        trade_db = TradeDB(
            backtest_run_id=backtest_run.id,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(trade_db)
        await db_session.commit()

        trade_id = trade_db.id

        # Delete backtest run
        await db_session.delete(backtest_run)
        await db_session.commit()

        # Verify trade was also deleted (cascade)
        result = await db_session.execute(select(TradeDB).where(TradeDB.id == trade_id))
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
class TestBulkTradeInsertion:
    """Test bulk trade insertion performance."""

    async def test_bulk_insert_500_trades_performance(self, db_session: AsyncSession):
        """Test that bulk inserting 500 trades completes in under 5 seconds."""
        # Create a backtest run
        backtest_run = BacktestRun(
            strategy_name="Test Strategy",
            strategy_type="test",
            instrument_symbol="AAPL",
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2025, 1, 31, tzinfo=timezone.utc),
            initial_capital=Decimal("100000.00"),
            data_source="test",
            execution_status="success",
            execution_duration_seconds=Decimal("10.5"),
            config_snapshot={},
        )
        db_session.add(backtest_run)
        await db_session.flush()

        # Generate 500 trades
        trades = []
        for i in range(500):
            trade = TradeDB(
                backtest_run_id=backtest_run.id,
                instrument_id="AAPL",
                trade_id=f"trade-{i}",
                venue_order_id=f"order-{i}",
                order_side="BUY" if i % 2 == 0 else "SELL",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00") + Decimal(i % 10),
                exit_price=Decimal("160.00") + Decimal(i % 10),
                profit_loss=Decimal("1000.00"),
                profit_pct=Decimal("6.67"),
                holding_period_seconds=3600,
                entry_timestamp=datetime(
                    2025, 1, 1, 10, i % 60, 0, tzinfo=timezone.utc
                ),
                exit_timestamp=datetime(
                    2025, 1, 1, 11, i % 60, 0, tzinfo=timezone.utc
                ),
            )
            trades.append(trade)

        # Measure bulk insert time
        start_time = time.time()
        db_session.add_all(trades)
        await db_session.commit()
        elapsed_time = time.time() - start_time

        # Verify performance requirement: < 5 seconds
        assert (
            elapsed_time < 5.0
        ), f"Bulk insert took {elapsed_time:.2f}s (should be < 5s)"

        # Verify all trades were saved
        result = await db_session.execute(
            select(TradeDB).where(TradeDB.backtest_run_id == backtest_run.id)
        )
        saved_trades = result.scalars().all()
        assert len(saved_trades) == 500
