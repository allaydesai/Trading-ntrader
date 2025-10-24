"""Component tests for position management using test doubles.

These tests verify position tracking, position limits, and position sizing logic
using lightweight test doubles instead of the real Nautilus framework.

Purpose: Test position management behavior without framework overhead
Reference: design.md Section 2.3 - Component Tests Using Test Doubles
"""

import pytest
from decimal import Decimal

from src.core.position_sizing import PositionSizingLogic
from tests.component.doubles import TestTradingEngine, TestOrder


@pytest.mark.component
class TestPositionManagerWithTestDoubles:
    """Test position management using test doubles."""

    def test_position_manager_tracks_single_position(self):
        """Test that position manager correctly tracks a single position."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Open position
        order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        engine.submit_order(order)

        # Verify position tracking
        position = engine.get_position("BTCUSDT")
        assert position == Decimal("0.5")

    def test_position_manager_tracks_multiple_positions(self):
        """Test that position manager tracks multiple positions independently."""
        engine = TestTradingEngine(initial_balance=Decimal("20000"))

        # Open multiple positions
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("2.0")))
        engine.submit_order(TestOrder("SOLUSDT", "BUY", Decimal("10.0")))

        # Verify all positions
        assert engine.get_position("BTCUSDT") == Decimal("0.5")
        assert engine.get_position("ETHUSDT") == Decimal("2.0")
        assert engine.get_position("SOLUSDT") == Decimal("10.0")

    def test_position_manager_updates_position_on_additional_buy(self):
        """Test that position manager correctly updates position on additional buys."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Initial position
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        assert engine.get_position("BTCUSDT") == Decimal("0.5")

        # Add to position
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.3")))
        assert engine.get_position("BTCUSDT") == Decimal("0.8")

    def test_position_manager_reduces_position_on_sell(self):
        """Test that position manager correctly reduces position on sells."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Establish position
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("1.0")))
        assert engine.get_position("BTCUSDT") == Decimal("1.0")

        # Partially close position
        engine.submit_order(TestOrder("BTCUSDT", "SELL", Decimal("0.4")))
        assert engine.get_position("BTCUSDT") == Decimal("0.6")

    def test_position_manager_closes_position_completely(self):
        """Test that position manager can completely close a position."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Establish and close position
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("BTCUSDT", "SELL", Decimal("0.5")))

        # Position should be flat
        assert engine.get_position("BTCUSDT") == Decimal("0")

    def test_position_manager_enforces_maximum_position_size(self):
        """Test that position manager enforces maximum position size limits."""
        engine = TestTradingEngine(
            initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
        )

        # Try to exceed maximum position size
        order = TestOrder("BTCUSDT", "BUY", Decimal("2.0"))

        with pytest.raises(ValueError, match="Exceeds maximum position"):
            engine.submit_order(order)

    def test_position_manager_allows_position_within_limits(self):
        """Test that position manager allows positions within size limits."""
        engine = TestTradingEngine(
            initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
        )

        # Submit order within limit
        order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        engine.submit_order(order)

        assert engine.get_position("BTCUSDT") == Decimal("0.5")

    def test_position_manager_uses_fixed_sizing(self):
        """Test position manager using fixed position sizing."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        sizing_logic = PositionSizingLogic()

        # Calculate fixed size
        size = sizing_logic.calculate_fixed_size(Decimal("0.5"))
        assert size == Decimal("0.5")

        # Submit order with fixed size
        engine.submit_order(TestOrder("BTCUSDT", "BUY", size))
        assert engine.get_position("BTCUSDT") == Decimal("0.5")

    def test_position_manager_uses_risk_based_sizing(self):
        """Test position manager using risk-based position sizing."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        sizing_logic = PositionSizingLogic()

        # Calculate risk-based size (2% risk, $1000 stop)
        size = sizing_logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("50000"),
            stop_price=Decimal("49000"),
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: 50000 - 49000 = 1000
        # Size: 200 / 1000 = 0.2
        assert size == Decimal("0.2")

        # Submit order with calculated size
        engine.submit_order(TestOrder("BTCUSDT", "BUY", size))
        assert engine.get_position("BTCUSDT") == Decimal("0.2")

    def test_position_details_calculates_unrealized_pnl(self):
        """Test that position details correctly calculate unrealized PnL."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Open position at $50000
        order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"), Decimal("50000"))
        engine.submit_order(order)

        # Get position details with current price at $51000
        position = engine.get_position_details("BTCUSDT", Decimal("51000"))

        assert position is not None
        assert position.quantity == Decimal("1.0")
        assert position.current_price == Decimal("51000")
        # PnL should be approximately 1000 (51000 - 50000) * 1.0
        assert position.unrealized_pnl == Decimal("1000")

    def test_position_details_handles_short_positions(self):
        """Test that position details correctly handle short positions."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Open short position at $50000
        order = TestOrder("BTCUSDT", "SELL", Decimal("1.0"), Decimal("50000"))
        engine.submit_order(order)

        # Get position details with current price at $51000
        position = engine.get_position_details("BTCUSDT", Decimal("51000"))

        assert position is not None
        assert position.quantity == Decimal("-1.0")  # Negative for short
        assert position.is_short() is True
        # PnL should be negative for losing short position
        assert position.unrealized_pnl == Decimal("-1000")

    def test_position_manager_uses_different_sizing_methods(self):
        """Test that position sizing logic supports different sizing methods."""
        sizing_logic = PositionSizingLogic()

        # Test fixed size
        fixed_size = sizing_logic.calculate_fixed_size(Decimal("0.5"))
        assert fixed_size == Decimal("0.5")

        # Test risk-based size
        risk_size = sizing_logic.calculate_risk_based_size(
            Decimal("10000"), Decimal("0.02"), Decimal("50000"), Decimal("49000")
        )
        assert risk_size == Decimal("0.2")

    def test_position_manager_handles_no_position(self):
        """Test that position manager correctly handles queries for non-existent positions."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Query non-existent position
        position = engine.get_position("BTCUSDT")
        assert position == Decimal("0")

        # Query position details for non-existent position
        details = engine.get_position_details("BTCUSDT", Decimal("50000"))
        assert details is None

    def test_position_manager_tracks_average_entry_price(self):
        """Test that position manager tracks average entry price for multiple entries."""
        engine = TestTradingEngine(initial_balance=Decimal("20000"))

        # First entry at $50000
        engine.submit_order(
            TestOrder("BTCUSDT", "BUY", Decimal("1.0"), Decimal("50000"))
        )

        # Second entry at $52000
        engine.submit_order(
            TestOrder("BTCUSDT", "BUY", Decimal("1.0"), Decimal("52000"))
        )

        # Get position details
        position = engine.get_position_details("BTCUSDT", Decimal("53000"))

        assert position is not None
        assert position.quantity == Decimal("2.0")
        # Average entry: (50000 + 52000) / 2 = 51000
        assert position.entry_price == Decimal("51000")

    def test_position_manager_reset_clears_all_positions(self):
        """Test that reset clears all positions and orders."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))

        # Establish multiple positions
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("2.0")))

        # Reset engine
        engine.reset()

        # All positions should be cleared
        assert engine.get_position("BTCUSDT") == Decimal("0")
        assert engine.get_position("ETHUSDT") == Decimal("0")
        assert len(engine.submitted_orders) == 0
        assert len(engine.event_log) == 0
