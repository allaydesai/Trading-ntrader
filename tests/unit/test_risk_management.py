"""Unit tests for risk management logic."""

import pytest
from decimal import Decimal

from src.core.risk_management import RiskManagementLogic, RiskLevel


@pytest.mark.unit
class TestRiskManagementInitialization:
    """Test risk management logic initialization."""

    def test_valid_initialization(self):
        """Test valid initialization with custom parameters."""
        logic = RiskManagementLogic(
            max_position_risk=Decimal("0.03"),
            max_portfolio_risk=Decimal("0.10"),
            max_positions=15,
        )

        assert logic.max_position_risk == Decimal("0.03")
        assert logic.max_portfolio_risk == Decimal("0.10")
        assert logic.max_positions == 15

    def test_default_initialization(self):
        """Test initialization with default parameters."""
        logic = RiskManagementLogic()

        assert logic.max_position_risk == Decimal("0.02")
        assert logic.max_portfolio_risk == Decimal("0.06")
        assert logic.max_positions == 10

    def test_invalid_position_risk_raises_error(self):
        """Test that invalid position risk raises ValueError."""
        with pytest.raises(ValueError, match="Max position risk must be between 0 and 1"):
            RiskManagementLogic(max_position_risk=Decimal("1.5"))

    def test_invalid_portfolio_risk_raises_error(self):
        """Test that invalid portfolio risk raises ValueError."""
        with pytest.raises(ValueError, match="Max portfolio risk must be between 0 and 1"):
            RiskManagementLogic(max_portfolio_risk=Decimal("0"))

    def test_invalid_max_positions_raises_error(self):
        """Test that invalid max positions raises ValueError."""
        with pytest.raises(ValueError, match="Max positions must be at least 1"):
            RiskManagementLogic(max_positions=0)


@pytest.mark.unit
class TestPositionRiskValidation:
    """Test individual position risk validation."""

    def test_acceptable_position_risk(self):
        """Test that acceptable risk passes validation."""
        logic = RiskManagementLogic(max_position_risk=Decimal("0.02"))

        is_valid = logic.validate_position_risk(
            position_value=Decimal("1000"),
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.05"),
        )

        # Risk: 1000 * 0.05 = 50
        # % of account: 50 / 10000 = 0.005 (0.5%)
        # 0.5% < 2% max → valid
        assert is_valid is True

    def test_excessive_position_risk(self):
        """Test that excessive risk fails validation."""
        logic = RiskManagementLogic(max_position_risk=Decimal("0.02"))

        is_valid = logic.validate_position_risk(
            position_value=Decimal("5000"),
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.05"),
        )

        # Risk: 5000 * 0.05 = 250
        # % of account: 250 / 10000 = 0.025 (2.5%)
        # 2.5% > 2% max → invalid
        assert is_valid is False

    def test_exact_limit_is_valid(self):
        """Test that risk exactly at limit is valid."""
        logic = RiskManagementLogic(max_position_risk=Decimal("0.02"))

        is_valid = logic.validate_position_risk(
            position_value=Decimal("2000"),
            account_balance=Decimal("10000"),
            stop_loss_percent=Decimal("0.10"),
        )

        # Risk: 2000 * 0.10 = 200
        # % of account: 200 / 10000 = 0.02 (2%)
        # 2% == 2% max → valid
        assert is_valid is True

    def test_invalid_account_balance(self):
        """Test that invalid account balance raises error."""
        logic = RiskManagementLogic()

        with pytest.raises(ValueError, match="Account balance must be positive"):
            logic.validate_position_risk(
                position_value=Decimal("1000"),
                account_balance=Decimal("0"),
                stop_loss_percent=Decimal("0.05"),
            )


@pytest.mark.unit
class TestPortfolioRiskCalculation:
    """Test portfolio risk calculation."""

    def test_calculate_portfolio_risk_multiple_positions(self):
        """Test portfolio risk with multiple positions."""
        logic = RiskManagementLogic()

        risk = logic.calculate_portfolio_risk(
            position_risks=[
                Decimal("100"),
                Decimal("150"),
                Decimal("200"),
            ],
            account_balance=Decimal("10000"),
        )

        # Total risk: 100 + 150 + 200 = 450
        # % of account: 450 / 10000 = 0.045 (4.5%)
        assert risk == Decimal("0.0450")

    def test_calculate_portfolio_risk_single_position(self):
        """Test portfolio risk with single position."""
        logic = RiskManagementLogic()

        risk = logic.calculate_portfolio_risk(
            position_risks=[Decimal("200")], account_balance=Decimal("10000")
        )

        assert risk == Decimal("0.0200")

    def test_calculate_portfolio_risk_no_positions(self):
        """Test portfolio risk with no positions."""
        logic = RiskManagementLogic()

        risk = logic.calculate_portfolio_risk(
            position_risks=[], account_balance=Decimal("10000")
        )

        assert risk == Decimal("0")


@pytest.mark.unit
class TestPortfolioRiskValidation:
    """Test portfolio risk validation when adding new positions."""

    def test_validate_adding_position_within_limit(self):
        """Test that adding position within limit is valid."""
        logic = RiskManagementLogic(max_portfolio_risk=Decimal("0.06"))

        is_valid = logic.validate_portfolio_risk(
            current_portfolio_risk=Decimal("0.04"),
            new_position_risk=Decimal("0.015"),
        )

        # Total: 0.04 + 0.015 = 0.055 (5.5%)
        # 5.5% < 6% max → valid
        assert is_valid is True

    def test_validate_adding_position_exceeds_limit(self):
        """Test that adding position exceeding limit is invalid."""
        logic = RiskManagementLogic(max_portfolio_risk=Decimal("0.06"))

        is_valid = logic.validate_portfolio_risk(
            current_portfolio_risk=Decimal("0.05"),
            new_position_risk=Decimal("0.02"),
        )

        # Total: 0.05 + 0.02 = 0.07 (7%)
        # 7% > 6% max → invalid
        assert is_valid is False

    def test_validate_exact_limit_is_valid(self):
        """Test that exactly reaching limit is valid."""
        logic = RiskManagementLogic(max_portfolio_risk=Decimal("0.06"))

        is_valid = logic.validate_portfolio_risk(
            current_portfolio_risk=Decimal("0.04"),
            new_position_risk=Decimal("0.02"),
        )

        # Total: 0.04 + 0.02 = 0.06 (6%)
        # 6% == 6% max → valid
        assert is_valid is True


@pytest.mark.unit
class TestPositionCountValidation:
    """Test position count validation."""

    def test_validate_position_count_below_limit(self):
        """Test that position count below limit is valid."""
        logic = RiskManagementLogic(max_positions=10)

        is_valid = logic.validate_position_count(current_positions=8)
        assert is_valid is True

    def test_validate_position_count_at_limit(self):
        """Test that position count at limit is invalid."""
        logic = RiskManagementLogic(max_positions=10)

        is_valid = logic.validate_position_count(current_positions=10)
        assert is_valid is False

    def test_validate_position_count_zero(self):
        """Test that zero positions is valid."""
        logic = RiskManagementLogic(max_positions=10)

        is_valid = logic.validate_position_count(current_positions=0)
        assert is_valid is True

    def test_negative_position_count_raises_error(self):
        """Test that negative position count raises error."""
        logic = RiskManagementLogic()

        with pytest.raises(ValueError, match="Current positions cannot be negative"):
            logic.validate_position_count(current_positions=-1)


@pytest.mark.unit
class TestStopLossCalculation:
    """Test stop loss price calculation."""

    def test_calculate_stop_loss_long_position(self):
        """Test stop loss for long position."""
        logic = RiskManagementLogic()

        stop = logic.calculate_stop_loss(
            entry_price=Decimal("100"), risk_percent=Decimal("0.05"), is_long=True
        )

        # Long: entry - risk
        # 100 - (100 * 0.05) = 95
        assert stop == Decimal("95.00")

    def test_calculate_stop_loss_short_position(self):
        """Test stop loss for short position."""
        logic = RiskManagementLogic()

        stop = logic.calculate_stop_loss(
            entry_price=Decimal("100"), risk_percent=Decimal("0.05"), is_long=False
        )

        # Short: entry + risk
        # 100 + (100 * 0.05) = 105
        assert stop == Decimal("105.00")

    def test_calculate_stop_loss_tight_stop(self):
        """Test stop loss with tight stop (1%)."""
        logic = RiskManagementLogic()

        stop = logic.calculate_stop_loss(
            entry_price=Decimal("100"), risk_percent=Decimal("0.01"), is_long=True
        )

        assert stop == Decimal("99.00")

    def test_invalid_entry_price(self):
        """Test that invalid entry price raises error."""
        logic = RiskManagementLogic()

        with pytest.raises(ValueError, match="Entry price must be positive"):
            logic.calculate_stop_loss(
                entry_price=Decimal("0"), risk_percent=Decimal("0.05"), is_long=True
            )


@pytest.mark.unit
class TestTakeProfitCalculation:
    """Test take profit price calculation."""

    def test_calculate_take_profit_long_2to1(self):
        """Test take profit for long with 2:1 ratio."""
        logic = RiskManagementLogic()

        tp = logic.calculate_take_profit(
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            risk_reward_ratio=Decimal("2.0"),
        )

        # Risk: 100 - 95 = 5
        # Reward: 5 * 2 = 10
        # TP: 100 + 10 = 110
        assert tp == Decimal("110.00")

    def test_calculate_take_profit_short_2to1(self):
        """Test take profit for short with 2:1 ratio."""
        logic = RiskManagementLogic()

        tp = logic.calculate_take_profit(
            entry_price=Decimal("100"),
            stop_loss=Decimal("105"),
            risk_reward_ratio=Decimal("2.0"),
        )

        # Risk: |100 - 105| = 5
        # Reward: 5 * 2 = 10
        # TP: 100 - 10 = 90 (short, so subtract)
        assert tp == Decimal("90.00")

    def test_calculate_take_profit_3to1(self):
        """Test take profit with 3:1 risk/reward ratio."""
        logic = RiskManagementLogic()

        tp = logic.calculate_take_profit(
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            risk_reward_ratio=Decimal("3.0"),
        )

        # Risk: 5, Reward: 5 * 3 = 15
        # TP: 100 + 15 = 115
        assert tp == Decimal("115.00")


@pytest.mark.unit
class TestRiskLevelAssessment:
    """Test risk level classification."""

    def test_low_risk_level(self):
        """Test that low risk is classified correctly."""
        logic = RiskManagementLogic()

        level = logic.assess_risk_level(Decimal("0.01"))
        assert level == RiskLevel.LOW

    def test_medium_risk_level(self):
        """Test that medium risk is classified correctly."""
        logic = RiskManagementLogic()

        level = logic.assess_risk_level(Decimal("0.03"))
        assert level == RiskLevel.MEDIUM

    def test_high_risk_level(self):
        """Test that high risk is classified correctly."""
        logic = RiskManagementLogic()

        level = logic.assess_risk_level(Decimal("0.07"))
        assert level == RiskLevel.HIGH

    def test_extreme_risk_level(self):
        """Test that extreme risk is classified correctly."""
        logic = RiskManagementLogic()

        level = logic.assess_risk_level(Decimal("0.15"))
        assert level == RiskLevel.EXTREME


@pytest.mark.unit
class TestRiskRewardValidation:
    """Test risk/reward ratio validation."""

    def test_validate_good_risk_reward(self):
        """Test that good risk/reward ratio passes validation."""
        logic = RiskManagementLogic()

        is_valid = logic.validate_risk_reward(
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("110"),
            min_risk_reward=Decimal("1.5"),
        )

        # Risk: 5, Reward: 10
        # Ratio: 10/5 = 2.0
        # 2.0 >= 1.5 → valid
        assert is_valid is True

    def test_validate_poor_risk_reward(self):
        """Test that poor risk/reward ratio fails validation."""
        logic = RiskManagementLogic()

        is_valid = logic.validate_risk_reward(
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("102"),
            min_risk_reward=Decimal("1.5"),
        )

        # Risk: 5, Reward: 2
        # Ratio: 2/5 = 0.4
        # 0.4 < 1.5 → invalid
        assert is_valid is False

    def test_validate_exact_minimum_is_valid(self):
        """Test that exactly meeting minimum is valid."""
        logic = RiskManagementLogic()

        is_valid = logic.validate_risk_reward(
            entry_price=Decimal("100"),
            stop_loss=Decimal("95"),
            take_profit=Decimal("107.50"),
            min_risk_reward=Decimal("1.5"),
        )

        # Risk: 5, Reward: 7.5
        # Ratio: 7.5/5 = 1.5
        # 1.5 >= 1.5 → valid
        assert is_valid is True

    def test_zero_risk_returns_false(self):
        """Test that zero risk returns false."""
        logic = RiskManagementLogic()

        is_valid = logic.validate_risk_reward(
            entry_price=Decimal("100"),
            stop_loss=Decimal("100"),  # No risk!
            take_profit=Decimal("110"),
            min_risk_reward=Decimal("1.5"),
        )

        assert is_valid is False
