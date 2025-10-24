"""Pure SMA trading logic with no framework dependencies."""

from decimal import Decimal
from enum import Enum


class CrossoverSignal(str, Enum):
    """Enumeration of crossover signals."""

    GOLDEN_CROSS = "golden_cross"  # Fast crosses above slow (bullish)
    DEATH_CROSS = "death_cross"  # Fast crosses below slow (bearish)
    NO_CROSS = "no_cross"  # No crossover detected


class SMATradingLogic:
    """
    Pure business logic for SMA crossover trading strategy.

    This class contains no framework dependencies and can be tested
    with simple Decimal values, making unit tests fast and isolated.
    """

    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        """
        Initialize SMA trading logic.

        Args:
            fast_period: Period for fast moving average
            slow_period: Period for slow moving average

        Raises:
            ValueError: If periods are invalid
        """
        if fast_period <= 0 or slow_period <= 0:
            raise ValueError("Periods must be positive")
        if fast_period >= slow_period:
            raise ValueError("Fast period must be less than slow period")

        self.fast_period = fast_period
        self.slow_period = slow_period

    def detect_crossover(
        self,
        prev_fast: Decimal,
        prev_slow: Decimal,
        curr_fast: Decimal,
        curr_slow: Decimal,
    ) -> CrossoverSignal:
        """
        Detect SMA crossover signals.

        Args:
            prev_fast: Previous fast SMA value
            prev_slow: Previous slow SMA value
            curr_fast: Current fast SMA value
            curr_slow: Current slow SMA value

        Returns:
            CrossoverSignal indicating the type of crossover detected

        Example:
            >>> logic = SMATradingLogic()
            >>> signal = logic.detect_crossover(
            ...     Decimal("99"), Decimal("100"),
            ...     Decimal("101"), Decimal("100")
            ... )
            >>> signal == CrossoverSignal.GOLDEN_CROSS
            True
        """
        # Golden cross: fast crosses above slow (bullish)
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return CrossoverSignal.GOLDEN_CROSS

        # Death cross: fast crosses below slow (bearish)
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return CrossoverSignal.DEATH_CROSS

        return CrossoverSignal.NO_CROSS

    def should_enter_long(self, fast_sma: Decimal, slow_sma: Decimal) -> bool:
        """
        Determine if conditions favor long entry.

        Args:
            fast_sma: Current fast SMA value
            slow_sma: Current slow SMA value

        Returns:
            True if should enter long position

        Example:
            >>> logic = SMATradingLogic()
            >>> logic.should_enter_long(Decimal("105"), Decimal("100"))
            True
        """
        return fast_sma > slow_sma

    def should_enter_short(self, fast_sma: Decimal, slow_sma: Decimal) -> bool:
        """
        Determine if conditions favor short entry.

        Args:
            fast_sma: Current fast SMA value
            slow_sma: Current slow SMA value

        Returns:
            True if should enter short position

        Example:
            >>> logic = SMATradingLogic()
            >>> logic.should_enter_short(Decimal("95"), Decimal("100"))
            True
        """
        return fast_sma < slow_sma

    def calculate_position_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
    ) -> Decimal:
        """
        Calculate position size based on risk parameters.

        Args:
            account_balance: Account balance to risk
            risk_percent: Percentage of balance to risk (0.02 = 2%)
            entry_price: Entry price for the position
            stop_price: Stop loss price

        Returns:
            Position size in units

        Raises:
            ValueError: If prices are negative or risk parameters invalid

        Example:
            >>> logic = SMATradingLogic()
            >>> size = logic.calculate_position_size(
            ...     Decimal("10000"), Decimal("0.02"),
            ...     Decimal("100"), Decimal("95")
            ... )
            >>> size
            Decimal('40')
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 < risk_percent <= 1):
            raise ValueError("Risk percent must be between 0 and 1")
        if entry_price <= 0 or stop_price <= 0:
            raise ValueError("Prices must be positive")

        risk_amount = account_balance * risk_percent
        price_risk = abs(entry_price - stop_price)

        if price_risk == 0:
            return Decimal("0")

        return risk_amount / price_risk
