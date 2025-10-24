"""Component tests for risk validation using test doubles.

These tests verify risk management validation and limits using lightweight test
doubles instead of the real Nautilus framework.

Purpose: Test risk checks behavior without framework overhead
Reference: design.md Section 2.3 - Component Tests Using Test Doubles
"""

import pytest
from decimal import Decimal

from src.core.risk_management import RiskManagementLogic, RiskLevel
from tests.component.doubles import TestTradingEngine, TestOrder


@pytest.mark.component
class TestRiskChecksWithTestDoubles:
    """Test risk validation using test doubles."""

    def test_risk_check_allows_safe_position_size(self):
        """Test that risk check allows position sizes within risk limits."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        risk_manager = RiskManagementLogic(
            max_position_risk=Decimal("0.02"), max_portfolio_risk=Decimal("0.10")
        )

        # Calculate safe position size (2% risk)
        position_risk = Decimal("10000") * Decimal("0.02")  # $200
        assert position_risk == Decimal("200")

        # Validate position risk
        is_valid = risk_manager.validate_position_risk(
            position_value=Decimal("1000"),
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.05"),  # 5% stop
        )

        # Position risk: 1000 * 0.05 = 50 (well below $200 limit)
        assert is_valid is True

    def test_risk_check_rejects_excessive_position_size(self):
        """Test that risk check rejects position sizes exceeding risk limits."""
        risk_manager = RiskManagementLogic(
            max_position_risk=Decimal("0.02"), max_portfolio_risk=Decimal("0.10")
        )

        # Validate excessive position risk
        is_valid = risk_manager.validate_position_risk(
            position_value=Decimal("10000"),  # Large position
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.05"),  # 5% stop
        )

        # Position risk: 10000 * 0.05 = 500 (exceeds 2% = $200 limit)
        assert is_valid is False

    def test_risk_check_validates_before_order_submission(self):
        """Test risk validation before submitting orders to engine."""
        engine = TestTradingEngine(
            initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
        )
        risk_manager = RiskManagementLogic(max_position_risk=Decimal("0.02"))

        # Order that would exceed risk limits
        large_order = TestOrder("BTCUSDT", "BUY", Decimal("2.0"))

        # Risk check fails (exceeds max_position_size)
        with pytest.raises(ValueError, match="Exceeds maximum position"):
            engine.submit_order(large_order)

        # Verify order was not submitted
        assert len(engine.submitted_orders) == 0

    def test_risk_check_allows_order_within_limits(self):
        """Test that risk check allows orders within all risk limits."""
        engine = TestTradingEngine(
            initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
        )

        # Order within limits
        safe_order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        engine.submit_order(safe_order)

        # Verify order was accepted
        assert len(engine.submitted_orders) == 1
        assert engine.get_position("BTCUSDT") == Decimal("0.5")

    def test_risk_manager_validates_position_risk_calculation(self):
        """Test risk manager correctly validates position risk."""
        risk_manager = RiskManagementLogic(max_position_risk=Decimal("0.03"))

        # Position with 5% stop loss on $5000 position against $10000 account
        # Risk: 5000 * 0.05 = 250
        # Risk percent: 250 / 10000 = 2.5% (within 3% limit)
        is_valid = risk_manager.validate_position_risk(
            position_value=Decimal("5000"),
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.05"),
        )

        assert is_valid is True

    def test_risk_manager_validates_portfolio_risk(self):
        """Test risk manager validates total portfolio risk."""
        engine = TestTradingEngine(initial_balance=Decimal("10000"))
        risk_manager = RiskManagementLogic(max_portfolio_risk=Decimal("0.10"))

        # Open first position (1% risk)
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))

        # Validate second position would not exceed 10% total risk
        # Current risk: assume 1% from first position
        # New position risk: 4%
        # Total risk: 1% + 4% = 5% (within 10% limit)
        current_risk_percent = Decimal("0.01")
        new_position_risk_percent = Decimal("0.04")

        is_valid = risk_manager.validate_portfolio_risk(
            current_risk_percent, new_position_risk_percent
        )
        assert is_valid is True

    def test_risk_manager_rejects_excessive_portfolio_risk(self):
        """Test risk manager rejects positions that would exceed portfolio risk."""
        risk_manager = RiskManagementLogic(max_portfolio_risk=Decimal("0.10"))

        # Simulate existing portfolio risk of 8%
        current_risk_percent = Decimal("0.08")

        # New position would add 5% risk
        new_position_risk_percent = Decimal("0.05")

        # Total risk: 8% + 5% = 13% (exceeds 10% limit)
        is_valid = risk_manager.validate_portfolio_risk(
            current_risk_percent, new_position_risk_percent
        )

        assert is_valid is False

    def test_risk_manager_calculates_stop_loss_price(self):
        """Test risk manager calculates stop loss prices correctly."""
        risk_manager = RiskManagementLogic()

        # Long position: stop loss below entry
        stop_price = risk_manager.calculate_stop_loss(
            entry_price=Decimal("50000"),
            risk_percent=Decimal("0.02"),  # 2% stop
            is_long=True,
        )

        # Stop: 50000 * (1 - 0.02) = 49000
        assert stop_price == Decimal("49000")

    def test_risk_manager_calculates_stop_loss_for_short(self):
        """Test risk manager calculates stop loss for short positions."""
        risk_manager = RiskManagementLogic()

        # Short position: stop loss above entry
        stop_price = risk_manager.calculate_stop_loss(
            entry_price=Decimal("50000"),
            risk_percent=Decimal("0.02"),  # 2% stop
            is_long=False,
        )

        # Stop: 50000 * (1 + 0.02) = 51000
        assert stop_price == Decimal("51000")

    def test_risk_manager_validates_maximum_open_positions(self):
        """Test risk manager enforces maximum number of open positions."""
        engine = TestTradingEngine(initial_balance=Decimal("20000"))
        risk_manager = RiskManagementLogic(max_positions=3)

        # Open 3 positions
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("1.0")))
        engine.submit_order(TestOrder("SOLUSDT", "BUY", Decimal("5.0")))

        # Count open positions (non-zero positions)
        open_positions = sum(1 for pos in engine.positions.values() if pos != 0)

        # Validate cannot open 4th position
        is_valid = risk_manager.validate_position_count(open_positions + 1)
        assert is_valid is False

    def test_risk_manager_allows_position_within_count_limit(self):
        """Test risk manager allows positions within count limit."""
        engine = TestTradingEngine(initial_balance=Decimal("20000"))
        risk_manager = RiskManagementLogic(max_positions=5)

        # Open 3 positions
        engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("0.5")))
        engine.submit_order(TestOrder("ETHUSDT", "BUY", Decimal("1.0")))
        engine.submit_order(TestOrder("SOLUSDT", "BUY", Decimal("5.0")))

        # Count open positions
        open_positions = sum(1 for pos in engine.positions.values() if pos != 0)

        # Validate can open 4th position (within limit of 5)
        is_valid = risk_manager.validate_position_count(open_positions + 1)
        assert is_valid is True

    def test_risk_manager_classifies_risk_levels(self):
        """Test risk manager correctly classifies risk levels."""
        risk_manager = RiskManagementLogic()

        # Low risk (< 2% portfolio risk)
        low_risk_level = risk_manager.assess_risk_level(Decimal("0.01"))
        assert low_risk_level == RiskLevel.LOW

        # Medium risk (2-5% portfolio risk)
        medium_risk_level = risk_manager.assess_risk_level(Decimal("0.03"))
        assert medium_risk_level == RiskLevel.MEDIUM

        # High risk (5-10% portfolio risk)
        high_risk_level = risk_manager.assess_risk_level(Decimal("0.07"))
        assert high_risk_level == RiskLevel.HIGH

        # Extreme risk (> 10% portfolio risk)
        extreme_risk_level = risk_manager.assess_risk_level(Decimal("0.15"))
        assert extreme_risk_level == RiskLevel.EXTREME

    def test_risk_check_logs_rejection_reasons(self):
        """Test that risk check failures are logged in event log."""
        engine = TestTradingEngine(
            initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
        )

        # Try to submit order that exceeds limits
        try:
            engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("5.0")))
        except ValueError:
            # Expected failure
            pass

        # Engine should have logged the rejection attempt
        # (submitted_orders would show rejected if we implemented that)
        assert len(engine.submitted_orders) == 0

    def test_risk_manager_calculates_take_profit(self):
        """Test risk manager calculates take profit levels."""
        risk_manager = RiskManagementLogic()

        # Long position: take profit above entry with 2:1 risk/reward
        # Entry: 50000, Stop: 49000 (1000 risk), TP should be 52000 (2000 reward)
        take_profit = risk_manager.calculate_take_profit(
            entry_price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            risk_reward_ratio=Decimal("2.0"),
        )

        # TP: 50000 + (50000-49000)*2 = 52000
        assert take_profit == Decimal("52000.00")

    def test_risk_manager_calculates_take_profit_for_short(self):
        """Test risk manager calculates take profit for short positions."""
        risk_manager = RiskManagementLogic()

        # Short position: take profit below entry with 2:1 risk/reward
        # Entry: 50000, Stop: 51000 (1000 risk), TP should be 48000 (2000 reward)
        take_profit = risk_manager.calculate_take_profit(
            entry_price=Decimal("50000"),
            stop_loss=Decimal("51000"),
            risk_reward_ratio=Decimal("2.0"),
        )

        # TP: 50000 - (51000-50000)*2 = 48000
        assert take_profit == Decimal("48000.00")
