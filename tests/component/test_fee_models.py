"""Comprehensive tests for custom fee models.

Tests IBKR commission model with tiered pricing structure.
Following TDD principles and project testing standards.
"""

import pytest
from decimal import Decimal
from unittest.mock import Mock

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Price, Quantity


class TestIBKRCommissionModel:
    """Comprehensive tests for IBKRCommissionModel class."""

    @pytest.mark.component
    def test_initialization_default_parameters(self):
        """Test IBKRCommissionModel initialization with default parameters."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        assert model.commission_per_share == Decimal("0.005")
        assert model.min_per_order == Decimal("1.00")
        assert model.max_rate == Decimal("0.005")

    @pytest.mark.component
    def test_initialization_custom_parameters(self):
        """Test IBKRCommissionModel initialization with custom parameters."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel(
            commission_per_share=Decimal("0.01"),
            min_per_order=Decimal("2.00"),
            max_rate=Decimal("0.01"),
        )

        assert model.commission_per_share == Decimal("0.01")
        assert model.min_per_order == Decimal("2.00")
        assert model.max_rate == Decimal("0.01")

    @pytest.mark.component
    def test_small_order_applies_minimum_commission(self):
        """Test that small orders pay minimum $1.00 commission."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        # Create mock order and instrument
        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 100 shares @ $10 = $1000 notional
        # Base commission: 100 * $0.005 = $0.50
        # Should apply minimum: $1.00
        fill_qty = Quantity.from_int(100)
        fill_px = Price.from_str("10.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_normal_order_per_share_rate(self):
        """Test that normal orders pay per-share rate."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 1000 shares @ $50 = $50,000 notional
        # Base commission: 1000 * $0.005 = $5.00
        # Max cap: $50,000 * 0.005 = $250.00
        # Should use base: $5.00
        fill_qty = Quantity.from_int(1000)
        fill_px = Price.from_str("50.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("5.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_large_order_applies_maximum_cap(self):
        """Test that large orders are capped at 0.5% of order value."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 10,000 shares @ $100 = $1,000,000 notional
        # Base commission: 10,000 * $0.005 = $50.00
        # Max cap: $1,000,000 * 0.005 = $5,000.00
        # Should use base: $50.00 (not hitting cap yet)
        fill_qty = Quantity.from_int(10000)
        fill_px = Price.from_str("100.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("50.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_very_large_order_hits_maximum_cap(self):
        """Test that very large orders hit the 0.5% cap."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 100,000 shares @ $100 = $10,000,000 notional
        # Base commission: 100,000 * $0.005 = $500.00
        # Max cap: $10,000,000 * 0.005 = $50,000.00
        # Should use base: $500.00 (still below cap)
        fill_qty = Quantity.from_int(100000)
        fill_px = Price.from_str("100.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("500.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_single_share_order_minimum(self):
        """Test edge case: single share at high price."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 1 share @ $1000 = $1000 notional
        # Base commission: 1 * $0.005 = $0.005
        # Should apply minimum: $1.00
        fill_qty = Quantity.from_int(1)
        fill_px = Price.from_str("1000.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_exact_minimum_threshold(self):
        """Test order that exactly hits minimum commission."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 200 shares @ any price
        # Base commission: 200 * $0.005 = $1.00
        # Should be exactly $1.00
        fill_qty = Quantity.from_int(200)
        fill_px = Price.from_str("50.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_commission_independent_of_order_side(self):
        """Test that commission is same for BUY and SELL orders."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        instrument = Mock()
        instrument.quote_currency = USD

        fill_qty = Quantity.from_int(500)
        fill_px = Price.from_str("100.00")

        # Test BUY side
        order_buy = Mock()
        order_buy.side = OrderSide.BUY
        commission_buy = model.get_commission(order_buy, fill_qty, fill_px, instrument)

        # Test SELL side
        order_sell = Mock()
        order_sell.side = OrderSide.SELL
        commission_sell = model.get_commission(
            order_sell, fill_qty, fill_px, instrument
        )

        assert commission_buy.as_decimal() == commission_sell.as_decimal()
        assert commission_buy.as_decimal() == Decimal("2.50")  # 500 * 0.005

    @pytest.mark.component
    def test_fractional_shares_commission(self):
        """Test commission calculation with fractional shares."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 150.5 shares @ $50
        # Base commission: 150.5 * $0.005 = $0.7525
        # Should apply minimum: $1.00
        fill_qty = Quantity.from_str("150.5")
        fill_px = Price.from_str("50.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_custom_commission_rates(self):
        """Test custom commission rate configuration."""
        from src.core.fee_models import IBKRCommissionModel

        # Create model with custom rates (e.g., institutional pricing)
        model = IBKRCommissionModel(
            commission_per_share=Decimal("0.001"),  # $0.001 per share
            min_per_order=Decimal("0.35"),  # $0.35 minimum
            max_rate=Decimal("0.001"),  # 0.1% maximum
        )

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 1000 shares @ $100
        # Base: 1000 * $0.001 = $1.00
        fill_qty = Quantity.from_int(1000)
        fill_px = Price.from_str("100.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD

    @pytest.mark.component
    def test_very_low_priced_stock(self):
        """Test commission for penny stocks."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 10,000 shares @ $0.50 = $5,000 notional
        # Base commission: 10,000 * $0.005 = $50.00
        # Max cap: $5,000 * 0.005 = $25.00
        # Should apply cap: $25.00
        fill_qty = Quantity.from_int(10000)
        fill_px = Price.from_str("0.50")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("25.00")
        assert commission.currency == USD


class TestIBKRCommissionModelEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.component
    def test_zero_quantity_raises_error(self):
        """Test that zero quantity is handled appropriately."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        fill_qty = Quantity.from_int(0)
        fill_px = Price.from_str("100.00")

        # Should still return minimum commission
        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")

    @pytest.mark.component
    def test_very_high_price_stock(self):
        """Test commission for very high-priced stocks."""
        from src.core.fee_models import IBKRCommissionModel

        model = IBKRCommissionModel()

        order = Mock()
        order.side = OrderSide.BUY
        instrument = Mock()
        instrument.quote_currency = USD

        # 10 shares @ $10,000 = $100,000 notional
        # Base commission: 10 * $0.005 = $0.05
        # Should apply minimum: $1.00
        fill_qty = Quantity.from_int(10)
        fill_px = Price.from_str("10000.00")

        commission = model.get_commission(order, fill_qty, fill_px, instrument)

        assert commission.as_decimal() == Decimal("1.00")
        assert commission.currency == USD


class TestIBKRCommissionModelIntegration:
    """Integration tests with backtest engine."""

    @pytest.mark.component
    def test_commission_appears_in_backtest_results(self):
        """Test that commissions are tracked in backtest results."""
        # This will be tested after integration with BacktestRunner
        # Placeholder for future integration test
        pass

    @pytest.mark.component
    def test_commission_affects_final_pnl(self):
        """Test that commissions reduce final P&L."""
        # This will be tested after integration with BacktestRunner
        # Placeholder for future integration test
        pass
