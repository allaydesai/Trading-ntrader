"""Custom fee models for trading commissions.

Implements commission structures for various brokers and venues.
"""

from decimal import Decimal

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.orders import Order


class IBKRCommissionModel(FeeModel):
    """
    Interactive Brokers US Equities Tiered Commission Model.

    Implements IBKR's tiered commission structure for US equity trading:
    - Base rate: $0.005 per share
    - Minimum: $1.00 per order
    - Maximum: 0.5% of order value

    This model provides realistic commission costs for backtesting strategies
    that would be executed through Interactive Brokers.

    Reference:
        https://www.interactivebrokers.com/en/pricing/commissions-stocks.php

    Example:
        >>> from decimal import Decimal
        >>> from nautilus_trader.model.currencies import USD
        >>> model = IBKRCommissionModel()
        >>> # 500 shares @ $100 = commission of $2.50
        >>> commission = model.get_commission(order, Quantity(500), Price(100), instrument)
        >>> assert commission == Money(2.50, USD)
    """

    def __init__(
        self,
        commission_per_share: Decimal = Decimal("0.005"),
        min_per_order: Decimal = Decimal("1.00"),
        max_rate: Decimal = Decimal("0.005"),
    ):
        """
        Initialize IBKR commission model.

        Args:
            commission_per_share: Commission rate per share (default: $0.005)
            min_per_order: Minimum commission per order (default: $1.00)
            max_rate: Maximum commission as % of order value (default: 0.005 = 0.5%)

        Raises:
            ValueError: If any parameter is negative
        """
        super().__init__()

        if commission_per_share < 0:
            raise ValueError("commission_per_share cannot be negative")
        if min_per_order < 0:
            raise ValueError("min_per_order cannot be negative")
        if max_rate < 0:
            raise ValueError("max_rate cannot be negative")

        self.commission_per_share = commission_per_share
        self.min_per_order = min_per_order
        self.max_rate = max_rate

    def get_commission(
        self,
        order: Order,
        fill_qty: Quantity,
        fill_px: Price,
        instrument: Instrument,
    ) -> Money:
        """
        Calculate commission for a trade.

        Implements tiered commission structure:
        1. Calculate base commission: quantity * per_share_rate
        2. Calculate notional value: quantity * price
        3. Calculate max cap: notional_value * max_rate
        4. Return: max(minimum, min(base_commission, max_cap))

        Args:
            order: The order being filled
            fill_qty: The fill quantity
            fill_px: The fill price
            instrument: The instrument being traded

        Returns:
            Money: The calculated commission in the instrument's quote currency

        Note:
            Commission is applied per fill, which is important for orders
            that are filled in multiple parts (partial fills).
        """
        # Convert Nautilus types to Decimal for calculations
        quantity = Decimal(str(fill_qty))
        price = Decimal(str(fill_px))

        # Calculate base commission: $0.005 per share
        base_commission = quantity * self.commission_per_share

        # Calculate notional value of the order
        notional_value = quantity * price

        # Calculate maximum commission (0.5% of order value)
        max_commission = notional_value * self.max_rate

        # Apply commission rules:
        # 1. At least minimum commission
        # 2. At most max_rate % of order value
        commission_amount = max(self.min_per_order, min(base_commission, max_commission))

        # Return commission in instrument's quote currency
        return Money(commission_amount, instrument.quote_currency)

    def __repr__(self) -> str:
        """Return string representation of the commission model."""
        return (
            f"IBKRCommissionModel("
            f"per_share={self.commission_per_share}, "
            f"min={self.min_per_order}, "
            f"max_rate={self.max_rate})"
        )
