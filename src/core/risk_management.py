"""Pure risk management logic with no framework dependencies."""

from decimal import Decimal
from enum import Enum


class RiskLevel(str, Enum):
    """Risk level classifications."""

    LOW = "low"  # Conservative risk
    MEDIUM = "medium"  # Moderate risk
    HIGH = "high"  # Aggressive risk
    EXTREME = "extreme"  # Excessive risk


class RiskManagementLogic:
    """
    Pure business logic for risk management and validation.

    This class contains no framework dependencies and provides
    risk checks, position limits, and portfolio risk calculations.
    """

    def __init__(
        self,
        max_position_risk: Decimal = Decimal("0.02"),
        max_portfolio_risk: Decimal = Decimal("0.06"),
        max_positions: int = 10,
    ):
        """
        Initialize risk management logic.

        Args:
            max_position_risk: Maximum risk per position (default 2%)
            max_portfolio_risk: Maximum total portfolio risk (default 6%)
            max_positions: Maximum number of concurrent positions

        Raises:
            ValueError: If risk parameters are invalid
        """
        if not (0 < max_position_risk <= 1):
            raise ValueError("Max position risk must be between 0 and 1")
        if not (0 < max_portfolio_risk <= 1):
            raise ValueError("Max portfolio risk must be between 0 and 1")
        if max_positions < 1:
            raise ValueError("Max positions must be at least 1")

        self.max_position_risk = max_position_risk
        self.max_portfolio_risk = max_portfolio_risk
        self.max_positions = max_positions

    def validate_position_risk(
        self,
        position_value: Decimal,
        account_balance: Decimal,
        stop_loss_percent: Decimal,
    ) -> bool:
        """
        Validate if position risk is within acceptable limits.

        Args:
            position_value: Total value of position
            account_balance: Current account balance
            stop_loss_percent: Stop loss as percentage of position value

        Returns:
            True if position risk is acceptable

        Example:
            >>> logic = RiskManagementLogic(max_position_risk=Decimal("0.02"))
            >>> logic.validate_position_risk(
            ...     Decimal("5000"), Decimal("10000"), Decimal("0.05")
            ... )
            False  # 5% risk on 50% of account exceeds 2% max
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if position_value < 0:
            raise ValueError("Position value cannot be negative")
        if not (0 < stop_loss_percent <= 1):
            raise ValueError("Stop loss percent must be between 0 and 1")

        # Calculate actual risk amount
        risk_amount = position_value * stop_loss_percent

        # Calculate risk as percentage of account
        risk_percent = risk_amount / account_balance

        return risk_percent <= self.max_position_risk

    def calculate_portfolio_risk(
        self, position_risks: list[Decimal], account_balance: Decimal
    ) -> Decimal:
        """
        Calculate total portfolio risk exposure.

        Args:
            position_risks: List of risk amounts for each position
            account_balance: Current account balance

        Returns:
            Portfolio risk as percentage of account

        Example:
            >>> logic = RiskManagementLogic()
            >>> risk = logic.calculate_portfolio_risk(
            ...     [Decimal("100"), Decimal("150"), Decimal("200")],
            ...     Decimal("10000")
            ... )
            >>> risk
            Decimal('0.0450')
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")

        total_risk = sum(position_risks)
        return total_risk / account_balance

    def validate_portfolio_risk(
        self, current_portfolio_risk: Decimal, new_position_risk: Decimal
    ) -> bool:
        """
        Validate if adding new position exceeds portfolio risk limit.

        Args:
            current_portfolio_risk: Current portfolio risk percentage
            new_position_risk: New position risk percentage

        Returns:
            True if total risk is within limits

        Example:
            >>> logic = RiskManagementLogic(max_portfolio_risk=Decimal("0.06"))
            >>> logic.validate_portfolio_risk(
            ...     Decimal("0.04"), Decimal("0.015")
            ... )
            True  # 4% + 1.5% = 5.5% < 6% limit
        """
        total_risk = current_portfolio_risk + new_position_risk
        return total_risk <= self.max_portfolio_risk

    def validate_position_count(self, current_positions: int) -> bool:
        """
        Validate if current position count is within limits.

        Args:
            current_positions: Number of current open positions

        Returns:
            True if can open new position

        Example:
            >>> logic = RiskManagementLogic(max_positions=10)
            >>> logic.validate_position_count(9)
            True
            >>> logic.validate_position_count(10)
            False
        """
        if current_positions < 0:
            raise ValueError("Current positions cannot be negative")

        return current_positions < self.max_positions

    def calculate_stop_loss(
        self, entry_price: Decimal, risk_percent: Decimal, is_long: bool = True
    ) -> Decimal:
        """
        Calculate stop loss price based on risk percentage.

        Args:
            entry_price: Entry price for position
            risk_percent: Risk as percentage of entry price
            is_long: True for long position, False for short

        Returns:
            Stop loss price

        Example:
            >>> logic = RiskManagementLogic()
            >>> stop = logic.calculate_stop_loss(
            ...     Decimal("100"), Decimal("0.05"), is_long=True
            ... )
            >>> stop
            Decimal('95.00')
        """
        if entry_price <= 0:
            raise ValueError("Entry price must be positive")
        if not (0 < risk_percent <= 1):
            raise ValueError("Risk percent must be between 0 and 1")

        risk_amount = entry_price * risk_percent

        if is_long:
            stop_price = entry_price - risk_amount
        else:
            stop_price = entry_price + risk_amount

        return stop_price.quantize(Decimal("0.01"))

    def calculate_take_profit(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        risk_reward_ratio: Decimal = Decimal("2.0"),
    ) -> Decimal:
        """
        Calculate take profit price based on risk/reward ratio.

        Args:
            entry_price: Entry price for position
            stop_loss: Stop loss price
            risk_reward_ratio: Reward relative to risk (default 2:1)

        Returns:
            Take profit price

        Example:
            >>> logic = RiskManagementLogic()
            >>> tp = logic.calculate_take_profit(
            ...     Decimal("100"), Decimal("95"), Decimal("2.0")
            ... )
            >>> tp
            Decimal('110.00')
        """
        if entry_price <= 0 or stop_loss <= 0:
            raise ValueError("Prices must be positive")
        if risk_reward_ratio <= 0:
            raise ValueError("Risk/reward ratio must be positive")

        risk = abs(entry_price - stop_loss)
        reward = risk * risk_reward_ratio

        # Determine if long or short position
        is_long = entry_price > stop_loss

        if is_long:
            take_profit = entry_price + reward
        else:
            take_profit = entry_price - reward

        return take_profit.quantize(Decimal("0.01"))

    def assess_risk_level(self, portfolio_risk_percent: Decimal) -> RiskLevel:
        """
        Assess overall risk level based on portfolio risk.

        Args:
            portfolio_risk_percent: Current portfolio risk percentage

        Returns:
            Risk level classification

        Example:
            >>> logic = RiskManagementLogic()
            >>> logic.assess_risk_level(Decimal("0.01"))
            <RiskLevel.LOW: 'low'>
        """
        if portfolio_risk_percent < Decimal("0.02"):
            return RiskLevel.LOW
        elif portfolio_risk_percent < Decimal("0.05"):
            return RiskLevel.MEDIUM
        elif portfolio_risk_percent < Decimal("0.10"):
            return RiskLevel.HIGH
        else:
            return RiskLevel.EXTREME

    def validate_risk_reward(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        min_risk_reward: Decimal = Decimal("1.5"),
    ) -> bool:
        """
        Validate if trade meets minimum risk/reward ratio.

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            min_risk_reward: Minimum acceptable risk/reward ratio

        Returns:
            True if trade meets minimum risk/reward criteria

        Example:
            >>> logic = RiskManagementLogic()
            >>> logic.validate_risk_reward(
            ...     Decimal("100"), Decimal("95"), Decimal("110"),
            ...     Decimal("1.5")
            ... )
            True  # 10 reward / 5 risk = 2:1 ratio
        """
        if entry_price <= 0 or stop_loss <= 0 or take_profit <= 0:
            raise ValueError("Prices must be positive")
        if min_risk_reward <= 0:
            raise ValueError("Minimum risk/reward must be positive")

        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)

        if risk == 0:
            return False

        risk_reward_ratio = reward / risk
        return risk_reward_ratio >= min_risk_reward
