"""Unit tests for position sizing logic."""

from decimal import Decimal

import pytest

from src.core.position_sizing import PositionSizingLogic


@pytest.mark.unit
class TestFixedSizeCalculation:
    """Test fixed position size calculation."""

    def test_fixed_size_positive(self):
        """Test that fixed size returns the same value."""
        logic = PositionSizingLogic()
        size = logic.calculate_fixed_size(Decimal("100"))
        assert size == Decimal("100")

    def test_fixed_size_zero(self):
        """Test that zero fixed size is valid."""
        logic = PositionSizingLogic()
        size = logic.calculate_fixed_size(Decimal("0"))
        assert size == Decimal("0")

    def test_fixed_size_negative_raises_error(self):
        """Test that negative fixed size raises ValueError."""
        logic = PositionSizingLogic()
        with pytest.raises(ValueError, match="Fixed units cannot be negative"):
            logic.calculate_fixed_size(Decimal("-10"))


@pytest.mark.unit
class TestRiskBasedSizeCalculation:
    """Test risk-based position sizing."""

    def test_risk_based_size_standard_case(self):
        """Test standard risk-based calculation."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: |100 - 95| = 5
        # Size: 200 / 5 = 40
        assert size == Decimal("40.00")

    def test_risk_based_size_with_small_stop(self):
        """Test risk-based sizing with tight stop loss."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
        )

        # Risk: 10000 * 0.01 = 100
        # Price risk: 1
        # Size: 100 / 1 = 100
        assert size == Decimal("100.00")

    def test_risk_based_size_short_position(self):
        """Test risk-based sizing for short position."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("105"),
        )

        # abs(100 - 105) = 5, same calculation
        assert size == Decimal("40.00")

    def test_zero_price_risk_returns_zero(self):
        """Test that zero price risk returns zero size."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),
        )

        assert size == Decimal("0")

    def test_invalid_account_balance(self):
        """Test that invalid account balance raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Account balance must be positive"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("-100"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_invalid_risk_percent(self):
        """Test that invalid risk percent raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Risk percent must be between 0 and 1"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("1.5"),
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_negative_prices_raise_error(self):
        """Test that negative prices raise error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Prices must be positive"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("-100"),
                stop_price=Decimal("95"),
            )


@pytest.mark.unit
class TestKellySizeCalculation:
    """Test Kelly Criterion position sizing."""

    def test_kelly_size_profitable_system(self):
        """Test Kelly sizing with profitable system parameters."""
        logic = PositionSizingLogic()

        size = logic.calculate_kelly_size(
            account_balance=Decimal("10000"),
            win_rate=Decimal("0.6"),
            avg_win=Decimal("150"),
            avg_loss=Decimal("100"),
            kelly_fraction=Decimal("0.25"),
        )

        # Kelly formula: (0.6 * 1.5 - 0.4) / 1.5 = 0.3333
        # Fractional: 0.3333 * 0.25 = 0.0833
        # Size: 10000 * 0.0833 ≈ 833
        assert size > Decimal("800")
        assert size < Decimal("900")

    def test_kelly_size_losing_system_returns_zero(self):
        """Test that losing system returns zero size."""
        logic = PositionSizingLogic()

        size = logic.calculate_kelly_size(
            account_balance=Decimal("10000"),
            win_rate=Decimal("0.3"),  # Low win rate
            avg_win=Decimal("100"),
            avg_loss=Decimal("150"),  # Larger losses
            kelly_fraction=Decimal("0.25"),
        )

        assert size == Decimal("0")

    def test_kelly_invalid_win_rate(self):
        """Test that invalid win rate raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Win rate must be between 0 and 1"):
            logic.calculate_kelly_size(
                account_balance=Decimal("10000"),
                win_rate=Decimal("1.5"),
                avg_win=Decimal("150"),
                avg_loss=Decimal("100"),
            )

    def test_kelly_invalid_kelly_fraction(self):
        """Test that invalid Kelly fraction raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Kelly fraction must be between 0 and 1"):
            logic.calculate_kelly_size(
                account_balance=Decimal("10000"),
                win_rate=Decimal("0.6"),
                avg_win=Decimal("150"),
                avg_loss=Decimal("100"),
                kelly_fraction=Decimal("1.5"),
            )


@pytest.mark.unit
class TestVolatilityBasedSizing:
    """Test volatility-based position sizing."""

    def test_volatility_scaling_up(self):
        """Test that low volatility increases position size."""
        logic = PositionSizingLogic()

        size = logic.calculate_volatility_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            volatility=Decimal("0.01"),  # Low volatility
            target_volatility=Decimal("0.02"),
        )

        # Base: 10000 * 0.02 = 200
        # Scale: 0.02 / 0.01 = 2.0
        # Size: 200 * 2.0 = 400
        assert size == Decimal("400.00")

    def test_volatility_scaling_down(self):
        """Test that high volatility decreases position size."""
        logic = PositionSizingLogic()

        size = logic.calculate_volatility_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            volatility=Decimal("0.04"),  # High volatility
            target_volatility=Decimal("0.02"),
        )

        # Base: 200
        # Scale: 0.02 / 0.04 = 0.5
        # Size: 200 * 0.5 = 100
        assert size == Decimal("100.00")

    def test_volatility_invalid_parameters(self):
        """Test that invalid volatility parameters raise errors."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Volatility must be positive"):
            logic.calculate_volatility_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("0.02"),
                volatility=Decimal("-0.01"),
                target_volatility=Decimal("0.02"),
            )


@pytest.mark.unit
class TestPositionSizeValidation:
    """Test position size validation and limiting."""

    def test_validate_within_limits(self):
        """Test that size within limits passes validation."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("50"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("50")

    def test_validate_caps_at_maximum(self):
        """Test that oversized position is capped at maximum."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("150"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("100")

    def test_validate_raises_to_minimum(self):
        """Test that undersized position is raised to minimum."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("5"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("10")

    def test_validate_zero_minimum(self):
        """Test validation with zero minimum (default)."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("50"), max_position_size=Decimal("100")
        )

        assert validated == Decimal("50")

    def test_validate_invalid_limits(self):
        """Test that invalid limits raise ValueError."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Max position size must be >= min"):
            logic.validate_position_size(
                position_size=Decimal("50"),
                max_position_size=Decimal("10"),
                min_position_size=Decimal("20"),
            )
