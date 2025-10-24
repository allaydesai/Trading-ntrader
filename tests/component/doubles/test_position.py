"""Test doubles for position entities.

This module provides lightweight test double implementations for position-related
entities. These test doubles are used in component tests to avoid the overhead
of real Nautilus position objects and C extension dependencies.

Design Pattern: Test Double (Fake)
Purpose: Enable fast component testing without Nautilus framework overhead
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class TestPosition:
    """
    Simplified position representation for component testing.

    This test double provides a lightweight alternative to Nautilus Position
    objects, enabling fast component tests without C extension dependencies.

    Attributes:
        symbol: Trading symbol (e.g., "BTCUSDT")
        quantity: Position quantity (positive for long, negative for short)
        entry_price: Average entry price
        current_price: Current market price
        unrealized_pnl: Unrealized profit/loss (calculated)

    Example:
        >>> position = TestPosition(
        ...     symbol="BTCUSDT",
        ...     quantity=Decimal("1.5"),
        ...     entry_price=Decimal("50000.00"),
        ...     current_price=Decimal("51000.00")
        ... )
        >>> position.unrealized_pnl
        Decimal('1500.00')
    """

    symbol: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Optional[Decimal] = None

    def __post_init__(self) -> None:
        """Calculate unrealized PnL after initialization."""
        if self.entry_price <= 0:
            raise ValueError(f"Entry price must be positive, got {self.entry_price}")

        if self.current_price <= 0:
            raise ValueError(f"Current price must be positive, got {self.current_price}")

        # Calculate unrealized PnL
        if self.unrealized_pnl is None:
            price_diff = self.current_price - self.entry_price
            self.unrealized_pnl = price_diff * abs(self.quantity)

            # Adjust sign for short positions
            if self.quantity < 0:
                self.unrealized_pnl = -self.unrealized_pnl

    def is_long(self) -> bool:
        """Check if this is a long position."""
        return self.quantity > 0

    def is_short(self) -> bool:
        """Check if this is a short position."""
        return self.quantity < 0

    def is_flat(self) -> bool:
        """Check if position is flat (no exposure)."""
        return self.quantity == 0

    def update_price(self, new_price: Decimal) -> None:
        """
        Update current price and recalculate unrealized PnL.

        Args:
            new_price: New market price

        Raises:
            ValueError: If new_price is not positive

        Example:
            >>> position = TestPosition("BTCUSDT", Decimal("1"), Decimal("50000"), Decimal("50000"))
            >>> position.update_price(Decimal("51000"))
            >>> position.unrealized_pnl
            Decimal('1000.00')
        """
        if new_price <= 0:
            raise ValueError(f"Price must be positive, got {new_price}")

        self.current_price = new_price

        # Recalculate unrealized PnL
        price_diff = self.current_price - self.entry_price
        self.unrealized_pnl = price_diff * abs(self.quantity)

        # Adjust sign for short positions
        if self.quantity < 0:
            self.unrealized_pnl = -self.unrealized_pnl
