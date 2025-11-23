"""
Unit tests for trade Pydantic models and calculation functions.

Tests cover:
- TradeCreate validation
- Profit calculation for long positions
- Profit calculation for short positions
- Trade model ORM conversion
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.models.trade import (
    Trade,
    TradeCreate,
    calculate_trade_metrics,
)


class TestTradeCreateValidation:
    """Test TradeCreate model validation."""

    def test_valid_trade_create(self):
        """Test creating a valid TradeCreate instance."""
        trade = TradeCreate(
            backtest_run_id=1,
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

        assert trade.backtest_run_id == 1
        assert trade.instrument_id == "AAPL"
        assert trade.order_side == "BUY"
        assert trade.quantity == Decimal("100")

    def test_invalid_order_side(self):
        """Test that invalid order_side raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TradeCreate(
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-123",
                venue_order_id="order-456",
                order_side="INVALID",  # Invalid side
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            )
        assert "order_side" in str(exc_info.value)

    def test_negative_quantity_rejected(self):
        """Test that negative quantity is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TradeCreate(
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-123",
                venue_order_id="order-456",
                order_side="BUY",
                quantity=Decimal("-100"),  # Negative quantity
                entry_price=Decimal("150.00"),
                entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            )
        assert "quantity" in str(exc_info.value).lower()

    def test_zero_entry_price_rejected(self):
        """Test that zero entry price is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TradeCreate(
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-123",
                venue_order_id="order-456",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("0"),  # Zero price
                entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            )
        assert "entry_price" in str(exc_info.value).lower()

    def test_negative_exit_price_rejected(self):
        """Test that negative exit price is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TradeCreate(
                backtest_run_id=1,
                instrument_id="AAPL",
                trade_id="trade-123",
                venue_order_id="order-456",
                order_side="BUY",
                quantity=Decimal("100"),
                entry_price=Decimal("150.00"),
                exit_price=Decimal("-10.00"),  # Negative price
                entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            )
        assert "exit_price" in str(exc_info.value).lower()


class TestCalculateProfitLongPosition:
    """Test profit calculation for long (BUY) positions."""

    def test_profitable_long_trade_no_costs(self):
        """Test profit calculation for profitable long trade without costs."""
        trade = TradeCreate(
            backtest_run_id=1,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("160.00"),
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        metrics = calculate_trade_metrics(trade)

        # Expected: (160 - 150) * 100 = 1000
        assert metrics["profit_loss"] == Decimal("1000.00")
        # Expected: 1000 / (150 * 100) * 100 = 6.67%
        assert abs(metrics["profit_pct"] - Decimal("6.666666666666666666666666667")) < Decimal(
            "0.01"
        )
        # Expected: 1 hour = 3600 seconds
        assert metrics["holding_period_seconds"] == 3600

    def test_profitable_long_trade_with_commission(self):
        """Test profit calculation for long trade with commission."""
        trade = TradeCreate(
            backtest_run_id=1,
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

        metrics = calculate_trade_metrics(trade)

        # Expected: (160 - 150) * 100 - 5 = 995
        assert metrics["profit_loss"] == Decimal("995.00")

    def test_losing_long_trade(self):
        """Test profit calculation for losing long trade."""
        trade = TradeCreate(
            backtest_run_id=1,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("140.00"),  # Exit lower than entry
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        metrics = calculate_trade_metrics(trade)

        # Expected: (140 - 150) * 100 = -1000
        assert metrics["profit_loss"] == Decimal("-1000.00")
        assert metrics["profit_pct"] < 0


class TestCalculateProfitShortPosition:
    """Test profit calculation for short (SELL) positions."""

    def test_profitable_short_trade(self):
        """Test profit calculation for profitable short trade."""
        trade = TradeCreate(
            backtest_run_id=1,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="SELL",  # Short position
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("140.00"),  # Exit lower = profit for short
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        metrics = calculate_trade_metrics(trade)

        # Expected: (150 - 140) * 100 = 1000
        assert metrics["profit_loss"] == Decimal("1000.00")
        assert metrics["profit_pct"] > 0

    def test_losing_short_trade(self):
        """Test profit calculation for losing short trade."""
        trade = TradeCreate(
            backtest_run_id=1,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="SELL",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=Decimal("160.00"),  # Exit higher = loss for short
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        )

        metrics = calculate_trade_metrics(trade)

        # Expected: (150 - 160) * 100 = -1000
        assert metrics["profit_loss"] == Decimal("-1000.00")
        assert metrics["profit_pct"] < 0


class TestTradeModelFromAttributes:
    """Test Trade model ORM conversion (from_attributes)."""

    def test_trade_from_orm_dict(self):
        """Test creating Trade from ORM-like dictionary."""
        orm_data = {
            "id": 1,
            "backtest_run_id": 1,
            "instrument_id": "AAPL",
            "trade_id": "trade-123",
            "venue_order_id": "order-456",
            "client_order_id": None,
            "order_side": "BUY",
            "quantity": Decimal("100"),
            "entry_price": Decimal("150.00"),
            "exit_price": Decimal("160.00"),
            "commission_amount": Decimal("5.00"),
            "commission_currency": "USD",
            "fees_amount": Decimal("0.00"),
            "profit_loss": Decimal("995.00"),
            "profit_pct": Decimal("6.63"),
            "holding_period_seconds": 3600,
            "entry_timestamp": datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            "exit_timestamp": datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
            "created_at": datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        }

        trade = Trade.model_validate(orm_data)

        assert trade.id == 1
        assert trade.instrument_id == "AAPL"
        assert trade.profit_loss == Decimal("995.00")
        assert trade.holding_period_seconds == 3600


class TestOpenTradeMetrics:
    """Test metric calculation for open trades (no exit yet)."""

    def test_open_trade_returns_none_metrics(self):
        """Test that open trade (no exit) returns None for metrics."""
        trade = TradeCreate(
            backtest_run_id=1,
            instrument_id="AAPL",
            trade_id="trade-123",
            venue_order_id="order-456",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150.00"),
            exit_price=None,  # Trade still open
            entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
            exit_timestamp=None,
        )

        metrics = calculate_trade_metrics(trade)

        assert metrics["profit_loss"] is None
        assert metrics["profit_pct"] is None
        assert metrics["holding_period_seconds"] is None
