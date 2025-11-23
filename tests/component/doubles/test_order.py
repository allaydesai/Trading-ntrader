"""Test doubles for order entities.

This module provides lightweight test double implementations for order-related
entities. These test doubles are used in component tests to avoid the overhead
of real Nautilus order objects and C extension dependencies.

Design Pattern: Test Double (Fake)
Purpose: Enable fast component testing without Nautilus framework overhead
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class TestOrder:
    """
    Simplified order representation for component testing.

    This test double provides a lightweight alternative to Nautilus Order
    objects, enabling fast component tests without C extension dependencies.

    Attributes:
        symbol: Trading symbol (e.g., "BTCUSDT")
        side: Order side ("BUY" or "SELL")
        quantity: Order quantity as Decimal
        price: Limit price (None for market orders)
        order_type: Order type ("MARKET" or "LIMIT")
        status: Order status ("PENDING", "FILLED", "CANCELLED")
        order_id: Unique order identifier (assigned by engine)

    Example:
        >>> order = TestOrder(
        ...     symbol="BTCUSDT",
        ...     side="BUY",
        ...     quantity=Decimal("0.5"),
        ...     price=Decimal("50000.00"),
        ...     order_type="LIMIT"
        ... )
        >>> order.status
        'PENDING'
    """

    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: Decimal
    price: Optional[Decimal] = None
    order_type: str = "MARKET"  # "MARKET" or "LIMIT"
    status: str = "PENDING"  # "PENDING", "FILLED", "CANCELLED"
    order_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate order attributes."""
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side: {self.side}. Must be 'BUY' or 'SELL'")

        if self.order_type not in ("MARKET", "LIMIT"):
            raise ValueError(f"Invalid order_type: {self.order_type}. Must be 'MARKET' or 'LIMIT'")

        if self.quantity <= 0:
            raise ValueError(f"Quantity must be positive, got {self.quantity}")

        if self.order_type == "LIMIT" and self.price is None:
            raise ValueError("LIMIT orders must have a price")

        if self.price is not None and self.price <= 0:
            raise ValueError(f"Price must be positive, got {self.price}")
