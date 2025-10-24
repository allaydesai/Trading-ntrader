"""Unit tests for pure SMA trading logic."""

import pytest
from decimal import Decimal

from src.core.sma_logic import SMATradingLogic, CrossoverSignal


@pytest.mark.unit
class TestSMATradingLogicInitialization:
    """Test SMA trading logic initialization and validation."""

    def test_valid_initialization(self):
        """Test that valid parameters initialize correctly."""
        logic = SMATradingLogic(fast_period=5, slow_period=20)
        assert logic.fast_period == 5
        assert logic.slow_period == 20

    def test_default_initialization(self):
        """Test that default parameters work."""
        logic = SMATradingLogic()
        assert logic.fast_period == 5
        assert logic.slow_period == 20

    def test_negative_period_raises_error(self):
        """Test that negative periods raise ValueError."""
        with pytest.raises(ValueError, match="Periods must be positive"):
            SMATradingLogic(fast_period=-1, slow_period=20)

    def test_zero_period_raises_error(self):
        """Test that zero periods raise ValueError."""
        with pytest.raises(ValueError, match="Periods must be positive"):
            SMATradingLogic(fast_period=0, slow_period=20)

    def test_fast_greater_than_slow_raises_error(self):
        """Test that fast >= slow periods raise ValueError."""
        with pytest.raises(
            ValueError, match="Fast period must be less than slow period"
        ):
            SMATradingLogic(fast_period=20, slow_period=10)

    def test_equal_periods_raise_error(self):
        """Test that equal periods raise ValueError."""
        with pytest.raises(
            ValueError, match="Fast period must be less than slow period"
        ):
            SMATradingLogic(fast_period=10, slow_period=10)


@pytest.mark.unit
class TestCrossoverDetection:
    """Test SMA crossover signal detection."""

    def test_golden_cross_detected(self):
        """Test that golden cross (bullish) is detected correctly."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("99"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("101"),
            curr_slow=Decimal("100"),
        )

        assert signal == CrossoverSignal.GOLDEN_CROSS

    def test_death_cross_detected(self):
        """Test that death cross (bearish) is detected correctly."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("101"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("99"),
            curr_slow=Decimal("100"),
        )

        assert signal == CrossoverSignal.DEATH_CROSS

    def test_no_crossover_when_fast_above_slow(self):
        """Test that no crossover is detected when fast stays above slow."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("105"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("106"),
            curr_slow=Decimal("101"),
        )

        assert signal == CrossoverSignal.NO_CROSS

    def test_no_crossover_when_fast_below_slow(self):
        """Test that no crossover is detected when fast stays below slow."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("95"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("94"),
            curr_slow=Decimal("99"),
        )

        assert signal == CrossoverSignal.NO_CROSS

    def test_golden_cross_at_exact_crossover_point(self):
        """Test golden cross when values are exactly equal."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("100"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("100.01"),
            curr_slow=Decimal("100"),
        )

        assert signal == CrossoverSignal.GOLDEN_CROSS

    def test_death_cross_at_exact_crossover_point(self):
        """Test death cross when values are exactly equal."""
        logic = SMATradingLogic()

        signal = logic.detect_crossover(
            prev_fast=Decimal("100"),
            prev_slow=Decimal("100"),
            curr_fast=Decimal("99.99"),
            curr_slow=Decimal("100"),
        )

        assert signal == CrossoverSignal.DEATH_CROSS


@pytest.mark.unit
class TestLongEntryLogic:
    """Test should_enter_long logic."""

    def test_should_enter_long_when_fast_above_slow(self):
        """Test that long entry is signaled when fast > slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_long(
            fast_sma=Decimal("105"), slow_sma=Decimal("100")
        )

        assert should_enter is True

    def test_should_not_enter_long_when_fast_below_slow(self):
        """Test that long entry is NOT signaled when fast < slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_long(
            fast_sma=Decimal("95"), slow_sma=Decimal("100")
        )

        assert should_enter is False

    def test_should_not_enter_long_when_fast_equals_slow(self):
        """Test that long entry is NOT signaled when fast == slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_long(
            fast_sma=Decimal("100"), slow_sma=Decimal("100")
        )

        assert should_enter is False


@pytest.mark.unit
class TestShortEntryLogic:
    """Test should_enter_short logic."""

    def test_should_enter_short_when_fast_below_slow(self):
        """Test that short entry is signaled when fast < slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_short(
            fast_sma=Decimal("95"), slow_sma=Decimal("100")
        )

        assert should_enter is True

    def test_should_not_enter_short_when_fast_above_slow(self):
        """Test that short entry is NOT signaled when fast > slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_short(
            fast_sma=Decimal("105"), slow_sma=Decimal("100")
        )

        assert should_enter is False

    def test_should_not_enter_short_when_fast_equals_slow(self):
        """Test that short entry is NOT signaled when fast == slow."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_short(
            fast_sma=Decimal("100"), slow_sma=Decimal("100")
        )

        assert should_enter is False


@pytest.mark.unit
class TestPositionSizeCalculation:
    """Test position sizing calculations."""

    def test_calculate_position_size_standard_case(self):
        """Test position size calculation with standard inputs."""
        logic = SMATradingLogic()

        position_size = logic.calculate_position_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: 100 - 95 = 5
        # Position size: 200 / 5 = 40
        assert position_size == Decimal("40")

    def test_calculate_position_size_with_large_stop(self):
        """Test position size with larger stop loss."""
        logic = SMATradingLogic()

        position_size = logic.calculate_position_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: 100 - 90 = 10
        # Position size: 200 / 10 = 20
        assert position_size == Decimal("20")

    def test_calculate_position_size_short_position(self):
        """Test position size for short position (stop above entry)."""
        logic = SMATradingLogic()

        position_size = logic.calculate_position_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("105"),
        )

        # Price risk uses absolute value: |100 - 105| = 5
        assert position_size == Decimal("40")

    def test_zero_position_size_when_no_price_risk(self):
        """Test that zero position size is returned when entry == stop."""
        logic = SMATradingLogic()

        position_size = logic.calculate_position_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),
        )

        assert position_size == Decimal("0")

    def test_negative_balance_raises_error(self):
        """Test that negative account balance raises ValueError."""
        logic = SMATradingLogic()

        with pytest.raises(ValueError, match="Account balance must be positive"):
            logic.calculate_position_size(
                account_balance=Decimal("-1000"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_invalid_risk_percent_raises_error(self):
        """Test that invalid risk percent raises ValueError."""
        logic = SMATradingLogic()

        with pytest.raises(ValueError, match="Risk percent must be between 0 and 1"):
            logic.calculate_position_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("1.5"),  # 150% is invalid
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_negative_price_raises_error(self):
        """Test that negative prices raise ValueError."""
        logic = SMATradingLogic()

        with pytest.raises(ValueError, match="Prices must be positive"):
            logic.calculate_position_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("-100"),
                stop_price=Decimal("95"),
            )


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_very_small_decimal_values(self):
        """Test that very small decimal values work correctly."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_long(
            fast_sma=Decimal("0.00001"), slow_sma=Decimal("0.000009")
        )

        assert should_enter is True

    def test_very_large_decimal_values(self):
        """Test that very large decimal values work correctly."""
        logic = SMATradingLogic()

        should_enter = logic.should_enter_long(
            fast_sma=Decimal("1000000"), slow_sma=Decimal("999999")
        )

        assert should_enter is True

    def test_high_precision_decimals(self):
        """Test that high-precision decimals maintain accuracy."""
        logic = SMATradingLogic()

        position_size = logic.calculate_position_size(
            account_balance=Decimal("10000.123456"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100.5555"),
            stop_price=Decimal("95.4444"),
        )

        # Should calculate without rounding errors
        assert position_size > Decimal("0")
