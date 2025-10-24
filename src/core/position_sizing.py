"""Pure position sizing logic with no framework dependencies."""

from decimal import Decimal
from enum import Enum


class SizingMethod(str, Enum):
    """Position sizing calculation methods."""

    FIXED = "fixed"  # Fixed position size
    RISK_BASED = "risk_based"  # Based on account risk percentage
    KELLY = "kelly"  # Kelly Criterion
    VOLATILITY = "volatility"  # Based on volatility


class PositionSizingLogic:
    """
    Pure business logic for position sizing calculations.

    This class contains no framework dependencies and provides
    various position sizing methodologies for risk management.
    """

    def calculate_fixed_size(self, fixed_units: Decimal) -> Decimal:
        """
        Calculate fixed position size.

        Args:
            fixed_units: Fixed number of units to trade

        Returns:
            Position size

        Raises:
            ValueError: If units are negative

        Example:
            >>> logic = PositionSizingLogic()
            >>> logic.calculate_fixed_size(Decimal("100"))
            Decimal('100')
        """
        if fixed_units < 0:
            raise ValueError("Fixed units cannot be negative")
        return fixed_units

    def calculate_risk_based_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
    ) -> Decimal:
        """
        Calculate position size based on risk percentage.

        This is the most common position sizing method, where you
        risk a fixed percentage of your account on each trade.

        Args:
            account_balance: Total account balance
            risk_percent: Percentage of account to risk (0.02 = 2%)
            entry_price: Planned entry price
            stop_price: Stop loss price

        Returns:
            Position size in units

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_risk_based_size(
            ...     Decimal("10000"), Decimal("0.02"),
            ...     Decimal("100"), Decimal("95")
            ... )
            >>> size
            Decimal('40.00')
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

        position_size = risk_amount / price_risk
        return position_size.quantize(Decimal("0.01"))

    def calculate_kelly_size(
        self,
        account_balance: Decimal,
        win_rate: Decimal,
        avg_win: Decimal,
        avg_loss: Decimal,
        kelly_fraction: Decimal = Decimal("0.25"),
    ) -> Decimal:
        """
        Calculate position size using Kelly Criterion.

        Kelly formula: f* = (p * b - q) / b
        where:
        - p = probability of winning
        - q = probability of losing (1 - p)
        - b = ratio of avg win to avg loss

        Args:
            account_balance: Total account balance
            win_rate: Historical win rate (0.6 = 60%)
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount
            kelly_fraction: Fraction of Kelly to use (default 0.25 for safety)

        Returns:
            Position size as percentage of account

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_kelly_size(
            ...     Decimal("10000"), Decimal("0.6"),
            ...     Decimal("150"), Decimal("100"),
            ...     Decimal("0.25")
            ... )
            >>> size > 0
            True
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 <= win_rate <= 1):
            raise ValueError("Win rate must be between 0 and 1")
        if avg_win <= 0 or avg_loss <= 0:
            raise ValueError("Average win and loss must be positive")
        if not (0 < kelly_fraction <= 1):
            raise ValueError("Kelly fraction must be between 0 and 1")

        # Probability of losing
        lose_rate = Decimal("1") - win_rate

        # Win/loss ratio
        win_loss_ratio = avg_win / avg_loss

        # Kelly percentage: (p * b - q) / b
        kelly_pct = (win_rate * win_loss_ratio - lose_rate) / win_loss_ratio

        # Apply fractional Kelly for safety (typically 0.25 or 0.5)
        adjusted_kelly = kelly_pct * kelly_fraction

        # Ensure non-negative position size
        if adjusted_kelly < 0:
            return Decimal("0")

        return (account_balance * adjusted_kelly).quantize(Decimal("0.01"))

    def calculate_volatility_based_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        volatility: Decimal,
        target_volatility: Decimal = Decimal("0.02"),
    ) -> Decimal:
        """
        Calculate position size based on volatility targeting.

        Adjusts position size inversely to volatility to maintain
        consistent risk exposure.

        Args:
            account_balance: Total account balance
            risk_percent: Base risk percentage
            volatility: Current instrument volatility (std dev)
            target_volatility: Target volatility level

        Returns:
            Volatility-adjusted position size

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_volatility_based_size(
            ...     Decimal("10000"), Decimal("0.02"),
            ...     Decimal("0.03"), Decimal("0.02")
            ... )
            >>> size > 0
            True
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 < risk_percent <= 1):
            raise ValueError("Risk percent must be between 0 and 1")
        if volatility <= 0:
            raise ValueError("Volatility must be positive")
        if target_volatility <= 0:
            raise ValueError("Target volatility must be positive")

        # Base position size
        base_size = account_balance * risk_percent

        # Volatility scaling factor
        vol_scale = target_volatility / volatility

        # Adjusted position size
        adjusted_size = base_size * vol_scale

        return adjusted_size.quantize(Decimal("0.01"))

    def validate_position_size(
        self,
        position_size: Decimal,
        max_position_size: Decimal,
        min_position_size: Decimal = Decimal("0"),
    ) -> Decimal:
        """
        Validate and cap position size within limits.

        Args:
            position_size: Calculated position size
            max_position_size: Maximum allowed position size
            min_position_size: Minimum allowed position size

        Returns:
            Validated position size within limits

        Raises:
            ValueError: If limits are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> logic.validate_position_size(
            ...     Decimal("150"), Decimal("100"), Decimal("10")
            ... )
            Decimal('100')
        """
        if min_position_size < 0:
            raise ValueError("Minimum position size cannot be negative")
        if max_position_size < min_position_size:
            raise ValueError("Max position size must be >= min position size")

        if position_size < min_position_size:
            return min_position_size
        if position_size > max_position_size:
            return max_position_size

        return position_size
