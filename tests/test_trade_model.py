"""Test suite for enhanced TradeModel with Nautilus Position integration."""

import pytest
from decimal import Decimal
from datetime import datetime, timedelta
from unittest.mock import Mock

from src.models.trade import TradeModel


class TestTradeModel:
    """Test TradeModel functionality."""

    def test_trade_model_creation(self):
        """Test basic TradeModel creation."""
        trade = TradeModel(
            position_id="test-123",
            instrument_id="AAPL.NASDAQ",
            entry_time=datetime(2024, 1, 15, 10, 30),
            entry_price=Decimal("150.50"),
            quantity=Decimal("100"),
            side="LONG",
        )

        assert trade.position_id == "test-123"
        assert trade.instrument_id == "AAPL.NASDAQ"
        assert trade.entry_price == Decimal("150.50")
        assert trade.quantity == Decimal("100")
        assert trade.side == "LONG"
        assert trade.is_open is True
        assert trade.is_closed is False

    def test_trade_model_validation(self):
        """Test TradeModel validation rules."""
        from pydantic import ValidationError

        # Test invalid entry price (must be non-negative)
        with pytest.raises(ValidationError):
            TradeModel(
                position_id="test",
                instrument_id="AAPL",
                entry_time=datetime.now(),
                entry_price=Decimal("-100"),
                quantity=Decimal("100"),
                side="LONG",
            )

        # Test invalid quantity (must be non-negative)
        with pytest.raises(ValidationError):
            TradeModel(
                position_id="test",
                instrument_id="AAPL",
                entry_time=datetime.now(),
                entry_price=Decimal("100"),
                quantity=Decimal("-100"),
                side="LONG",
            )

    def test_pnl_calculation_long_position(self):
        """Test PnL calculations for long positions."""
        trade = TradeModel(
            position_id="long-test",
            instrument_id="AAPL",
            entry_time=datetime.now(),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
        )

        # Close the trade at a profit
        trade.update_exit_data(exit_price=Decimal("110.00"))

        # Test gross PnL
        gross_pnl = trade.calculate_gross_pnl()
        assert gross_pnl == Decimal("1000.00")  # (110 - 100) * 100

        # Test percentage return
        pnl_pct = trade.calculate_pnl_percentage()
        assert pnl_pct == Decimal("10.00")  # 10% gain

        # Test net PnL (no commission/slippage)
        net_pnl = trade.calculate_net_pnl()
        assert net_pnl == Decimal("1000.00")

        assert trade.is_winning_trade is True

    def test_pnl_calculation_short_position(self):
        """Test PnL calculations for short positions."""
        trade = TradeModel(
            position_id="short-test",
            instrument_id="AAPL",
            entry_time=datetime.now(),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="SHORT",
        )

        # Close the trade at a profit (price went down)
        trade.update_exit_data(exit_price=Decimal("90.00"))

        # Test gross PnL
        gross_pnl = trade.calculate_gross_pnl()
        assert gross_pnl == Decimal("1000.00")  # (100 - 90) * 100

        # Test percentage return
        pnl_pct = trade.calculate_pnl_percentage()
        assert pnl_pct == Decimal("10.00")  # 10% gain

        assert trade.is_winning_trade is True

    def test_pnl_with_costs(self):
        """Test PnL calculations including commission and slippage."""
        trade = TradeModel(
            position_id="cost-test",
            instrument_id="AAPL",
            entry_time=datetime.now(),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
            commission=Decimal("10.00"),
            slippage=Decimal("5.00"),
        )

        trade.update_exit_data(exit_price=Decimal("110.00"))

        # Gross PnL should be 1000
        gross_pnl = trade.calculate_gross_pnl()
        assert gross_pnl == Decimal("1000.00")

        # Net PnL should account for costs
        net_pnl = trade.calculate_net_pnl()
        assert net_pnl == Decimal("985.00")  # 1000 - 10 - 5

    def test_trade_duration(self):
        """Test trade duration calculations."""
        entry_time = datetime(2024, 1, 15, 10, 30)
        exit_time = datetime(2024, 1, 15, 12, 45)

        trade = TradeModel(
            position_id="duration-test",
            instrument_id="AAPL",
            entry_time=entry_time,
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
            exit_time=exit_time,
            exit_price=Decimal("105.00"),
        )

        # Test duration calculations
        assert trade.duration_seconds == 8100  # 2 hours 15 minutes = 8100 seconds
        assert trade.duration_hours == 2.25  # 2.25 hours

    def test_to_dict_conversion(self):
        """Test conversion to dictionary for export."""
        trade = TradeModel(
            position_id="dict-test",
            instrument_id="AAPL",
            entry_time=datetime(2024, 1, 15, 10, 30),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
            strategy_name="test_strategy",
            commission=Decimal("5.00"),
        )

        trade.update_exit_data(exit_price=Decimal("105.00"))

        trade_dict = trade.to_dict()

        assert trade_dict["position_id"] == "dict-test"
        assert trade_dict["instrument_id"] == "AAPL"
        assert trade_dict["strategy_name"] == "test_strategy"
        assert trade_dict["side"] == "LONG"
        assert trade_dict["entry_price"] == "100.00"
        assert trade_dict["exit_price"] == "105.00"
        assert trade_dict["quantity"] == "100"
        assert trade_dict["commission"] == "5.00"
        assert trade_dict["is_winning_trade"] is True

    def test_string_representations(self):
        """Test string and repr methods."""
        trade = TradeModel(
            position_id="str-test",
            instrument_id="AAPL",
            entry_time=datetime.now(),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
        )

        # Test open trade string
        str_repr = str(trade)
        assert "AAPL" in str_repr
        assert "LONG" in str_repr
        assert "OPEN" in str_repr

        # Test repr
        repr_str = repr(trade)
        assert "TradeModel" in repr_str
        assert "str-test" in repr_str


class TestNautilusPositionIntegration:
    """Test integration with Nautilus Position objects."""

    def create_mock_position(self, is_closed=False, is_long=True):
        """Create a mock Nautilus Position for testing."""
        position = Mock()

        # Basic properties
        position.id = Mock()
        position.id.__str__ = Mock(return_value="pos-123")
        position.instrument_id = Mock()
        position.instrument_id.__str__ = Mock(return_value="AAPL.NASDAQ")

        # Timestamps
        position.opened_time = datetime(2024, 1, 15, 10, 30)
        position.is_closed = is_closed
        position.closed_time = datetime(2024, 1, 15, 12, 30) if is_closed else None

        # Position data
        position.avg_px_open = 150.50
        position.avg_px_close = 155.75 if is_closed else None
        position.quantity = 100 if is_long else -100
        position.is_long = is_long

        # Financial data
        position.commission = 5.50
        position.realized_pnl = (
            525.0 - 5.50 if is_closed else None
        )  # 525 gross - commission

        return position

    def test_from_nautilus_position_open_long(self):
        """Test creating TradeModel from open long Nautilus Position."""
        position = self.create_mock_position(is_closed=False, is_long=True)

        trade = TradeModel.from_nautilus_position(position, "test_strategy")

        assert trade.position_id == "pos-123"
        assert trade.instrument_id == "AAPL.NASDAQ"
        assert trade.strategy_name == "test_strategy"
        assert trade.side == "LONG"
        assert trade.entry_price == Decimal("150.50")
        assert trade.quantity == Decimal("100")
        assert trade.commission == Decimal("5.50")
        assert trade.is_open is True
        assert trade.exit_time is None
        assert trade.exit_price is None
        assert trade.realized_pnl is None

    def test_from_nautilus_position_closed_long(self):
        """Test creating TradeModel from closed long Nautilus Position."""
        position = self.create_mock_position(is_closed=True, is_long=True)

        trade = TradeModel.from_nautilus_position(position, "test_strategy")

        assert trade.position_id == "pos-123"
        assert trade.side == "LONG"
        assert trade.entry_price == Decimal("150.50")
        assert trade.exit_price == Decimal("155.75")
        assert trade.quantity == Decimal("100")
        assert trade.commission == Decimal("5.50")
        assert trade.realized_pnl == Decimal("519.50")  # Should match Nautilus PnL
        assert trade.is_closed is True
        assert trade.exit_time is not None

    def test_from_nautilus_position_short(self):
        """Test creating TradeModel from short Nautilus Position."""
        position = self.create_mock_position(is_closed=True, is_long=False)

        trade = TradeModel.from_nautilus_position(position)

        assert trade.side == "SHORT"
        assert trade.quantity == Decimal("100")  # Should be absolute value

    def test_from_nautilus_position_edge_cases(self):
        """Test edge cases in Nautilus Position conversion."""
        position = Mock()

        # Test with None/missing values
        position.id = None
        position.instrument_id = None
        position.opened_time = None
        position.avg_px_open = None
        position.quantity = None
        position.is_long = True
        position.is_closed = False
        position.closed_time = None
        position.avg_px_close = None
        position.commission = None
        position.realized_pnl = None

        trade = TradeModel.from_nautilus_position(position)

        # Should handle None values gracefully
        assert trade.position_id == "unknown"
        assert trade.instrument_id == "unknown"
        assert trade.entry_price == Decimal("0")
        assert trade.quantity == Decimal("0")
        assert trade.commission == Decimal("0")

    def test_nautilus_position_pnl_consistency(self):
        """Test that PnL calculations are consistent with Nautilus."""
        position = self.create_mock_position(is_closed=True, is_long=True)

        trade = TradeModel.from_nautilus_position(position)

        # Verify our calculations match Nautilus
        our_gross_pnl = trade.calculate_gross_pnl()
        expected_gross_pnl = (Decimal("155.75") - Decimal("150.50")) * Decimal("100")
        assert our_gross_pnl == expected_gross_pnl

        # Net PnL should match Nautilus realized_pnl
        our_net_pnl = trade.calculate_net_pnl()
        assert our_net_pnl == Decimal("519.50")  # Should match trade.realized_pnl


class TestTradeModelIntegration:
    """Test TradeModel integration with the broader system."""

    def test_trade_model_json_serialization(self):
        """Test that TradeModel can be serialized to JSON."""
        trade = TradeModel(
            position_id="json-test",
            instrument_id="AAPL",
            entry_time=datetime(2024, 1, 15, 10, 30),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
        )

        # Should be able to serialize to JSON
        json_data = trade.model_dump_json()
        assert isinstance(json_data, str)

        # Should be able to deserialize from JSON
        trade_from_json = TradeModel.model_validate_json(json_data)
        assert trade_from_json.position_id == trade.position_id
        assert trade_from_json.entry_price == trade.entry_price

    def test_trade_model_for_reports(self):
        """Test TradeModel data structure for reporting."""
        trades = []

        # Create sample trades
        for i in range(5):
            trade = TradeModel(
                position_id=f"report-test-{i}",
                instrument_id="AAPL",
                entry_time=datetime.now() - timedelta(hours=i),
                entry_price=Decimal(f"{100 + i}.00"),
                quantity=Decimal("100"),
                side="LONG" if i % 2 == 0 else "SHORT",
                strategy_name="test_strategy",
            )

            # Close some trades
            if i < 3:
                exit_price = (
                    Decimal(f"{105 + i}.00")
                    if trade.side == "LONG"
                    else Decimal(f"{95 + i}.00")
                )
                trade.update_exit_data(exit_price)

            trades.append(trade)

        # Test aggregation for reports
        closed_trades = [t for t in trades if t.is_closed]
        open_trades = [t for t in trades if t.is_open]

        assert len(closed_trades) == 3
        assert len(open_trades) == 2

        # Test profit/loss aggregation
        total_pnl = sum(t.realized_pnl for t in closed_trades if t.realized_pnl)
        winning_trades = [t for t in closed_trades if t.is_winning_trade]

        assert len(winning_trades) > 0  # Should have profitable trades
        assert isinstance(total_pnl, Decimal)

    def test_trade_model_update_scenarios(self):
        """Test various trade update scenarios."""
        trade = TradeModel(
            position_id="update-test",
            instrument_id="AAPL",
            entry_time=datetime.now(),
            entry_price=Decimal("100.00"),
            quantity=Decimal("100"),
            side="LONG",
        )

        initial_updated_at = trade.updated_at

        # Update with exit data
        trade.update_exit_data(Decimal("105.00"))

        # Should have updated timestamp and calculated fields
        assert trade.updated_at > initial_updated_at
        assert trade.exit_price == Decimal("105.00")
        assert trade.pnl_pct == Decimal("5.00")
        assert trade.realized_pnl == Decimal("500.00")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
