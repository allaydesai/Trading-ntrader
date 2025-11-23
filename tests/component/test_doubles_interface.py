"""Interface tests for test doubles.

These tests verify that test doubles have the required methods and behavior
to serve as lightweight replacements for Nautilus components in component tests.

Purpose: Contract verification without complex protocol testing
Reference: design.md Section 2.3 - Test Double Verification Strategy
"""

from decimal import Decimal

import pytest

from tests.component.doubles import TestOrder, TestPosition, TestTradingEngine


@pytest.mark.component
class TestTestTradingEngineInterface:
    """Verify TestTradingEngine has required methods and basic behavior."""

    def test_engine_has_submit_order_method(self):
        """Test that TestTradingEngine can submit orders."""
        engine = TestTradingEngine()
        order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))

        order_id = engine.submit_order(order)

        assert isinstance(order_id, str)
        assert len(engine.submitted_orders) == 1
        assert engine.submitted_orders[0] == order

    def test_engine_has_get_position_method(self):
        """Test that TestTradingEngine can retrieve positions."""
        engine = TestTradingEngine()
        order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))

        engine.submit_order(order)
        position = engine.get_position("BTCUSDT")

        assert position == Decimal("0.5")

    def test_engine_has_get_balance_method(self):
        """Test that TestTradingEngine tracks balance."""
        initial_balance = Decimal("10000")
        engine = TestTradingEngine(initial_balance=initial_balance)

        balance = engine.get_balance()

        assert balance == initial_balance

    def test_engine_fills_market_orders_automatically(self):
        """Test that market orders are auto-filled."""
        engine = TestTradingEngine()
        order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"), order_type="MARKET")

        engine.submit_order(order)

        assert order.status == "FILLED"
        assert engine.get_position("BTCUSDT") == Decimal("1.0")

    def test_engine_tracks_multiple_orders(self):
        """Test that engine tracks multiple orders correctly."""
        engine = TestTradingEngine()

        order1 = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))
        order2 = TestOrder("ETHUSDT", "BUY", Decimal("2.0"))

        engine.submit_order(order1)
        engine.submit_order(order2)

        assert len(engine.submitted_orders) == 2
        assert engine.get_position("BTCUSDT") == Decimal("1.0")
        assert engine.get_position("ETHUSDT") == Decimal("2.0")

    def test_engine_respects_position_limits(self):
        """Test that engine enforces maximum position size."""
        engine = TestTradingEngine(max_position_size=Decimal("1.0"))

        order = TestOrder("BTCUSDT", "BUY", Decimal("2.0"))

        with pytest.raises(ValueError, match="Exceeds maximum position"):
            engine.submit_order(order)

    def test_engine_has_event_log(self):
        """Test that engine maintains event log."""
        engine = TestTradingEngine()
        order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))

        engine.submit_order(order)

        assert len(engine.event_log) > 0
        assert any("ORDER_SUBMITTED" in event for event in engine.event_log)
        assert any("ORDER_FILLED" in event for event in engine.event_log)


@pytest.mark.component
class TestTestOrderInterface:
    """Verify TestOrder has required attributes and validation."""

    def test_order_has_required_attributes(self):
        """Test that TestOrder captures necessary order attributes."""
        order = TestOrder(
            symbol="BTCUSDT",
            side="BUY",
            quantity=Decimal("1.5"),
            price=Decimal("50000.00"),
            order_type="LIMIT",
        )

        assert order.symbol == "BTCUSDT"
        assert order.side == "BUY"
        assert order.quantity == Decimal("1.5")
        assert order.price == Decimal("50000.00")
        assert order.order_type == "LIMIT"
        assert order.status == "PENDING"

    def test_order_validates_side(self):
        """Test that TestOrder validates order side."""
        with pytest.raises(ValueError, match="Invalid side"):
            TestOrder("BTCUSDT", "INVALID", Decimal("1.0"))

    def test_order_validates_order_type(self):
        """Test that TestOrder validates order type."""
        with pytest.raises(ValueError, match="Invalid order_type"):
            TestOrder("BTCUSDT", "BUY", Decimal("1.0"), order_type="INVALID")

    def test_order_validates_quantity(self):
        """Test that TestOrder validates quantity is positive."""
        with pytest.raises(ValueError, match="Quantity must be positive"):
            TestOrder("BTCUSDT", "BUY", Decimal("-1.0"))

    def test_limit_order_requires_price(self):
        """Test that LIMIT orders must have a price."""
        with pytest.raises(ValueError, match="LIMIT orders must have a price"):
            TestOrder("BTCUSDT", "BUY", Decimal("1.0"), order_type="LIMIT")

    def test_order_validates_price_is_positive(self):
        """Test that TestOrder validates price is positive."""
        with pytest.raises(ValueError, match="Price must be positive"):
            TestOrder("BTCUSDT", "BUY", Decimal("1.0"), price=Decimal("-100"))

    def test_market_order_default_values(self):
        """Test that market orders use default values correctly."""
        order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))

        assert order.order_type == "MARKET"
        assert order.price is None
        assert order.status == "PENDING"
        assert order.order_id is None


@pytest.mark.component
class TestTestPositionInterface:
    """Verify TestPosition has required attributes and calculations."""

    def test_position_has_required_attributes(self):
        """Test that TestPosition has necessary position attributes."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("1.5"),
            entry_price=Decimal("50000.00"),
            current_price=Decimal("51000.00"),
        )

        assert position.symbol == "BTCUSDT"
        assert position.quantity == Decimal("1.5")
        assert position.entry_price == Decimal("50000.00")
        assert position.current_price == Decimal("51000.00")
        assert position.unrealized_pnl is not None

    def test_position_calculates_pnl_for_long(self):
        """Test that position correctly calculates PnL for long positions."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("2.0"),
            entry_price=Decimal("50000.00"),
            current_price=Decimal("51000.00"),
        )

        # PnL = (51000 - 50000) * 2.0 = 2000
        assert position.unrealized_pnl == Decimal("2000.00")

    def test_position_calculates_pnl_for_short(self):
        """Test that position correctly calculates PnL for short positions."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("-2.0"),
            entry_price=Decimal("50000.00"),
            current_price=Decimal("51000.00"),
        )

        # PnL for short = -(51000 - 50000) * 2.0 = -2000
        assert position.unrealized_pnl == Decimal("-2000.00")

    def test_position_identifies_long(self):
        """Test that position correctly identifies long positions."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
        )

        assert position.is_long() is True
        assert position.is_short() is False
        assert position.is_flat() is False

    def test_position_identifies_short(self):
        """Test that position correctly identifies short positions."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("-1.0"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
        )

        assert position.is_long() is False
        assert position.is_short() is True
        assert position.is_flat() is False

    def test_position_identifies_flat(self):
        """Test that position correctly identifies flat positions."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("0"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
        )

        assert position.is_flat() is True
        assert position.is_long() is False
        assert position.is_short() is False

    def test_position_updates_price(self):
        """Test that position can update current price and recalculate PnL."""
        position = TestPosition(
            symbol="BTCUSDT",
            quantity=Decimal("1.0"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
        )

        position.update_price(Decimal("52000"))

        assert position.current_price == Decimal("52000")
        assert position.unrealized_pnl == Decimal("2000")

    def test_position_validates_entry_price(self):
        """Test that position validates entry price is positive."""
        with pytest.raises(ValueError, match="Entry price must be positive"):
            TestPosition("BTCUSDT", Decimal("1"), Decimal("-100"), Decimal("50000"))

    def test_position_validates_current_price(self):
        """Test that position validates current price is positive."""
        with pytest.raises(ValueError, match="Current price must be positive"):
            TestPosition("BTCUSDT", Decimal("1"), Decimal("50000"), Decimal("-100"))
