"""Test doubles for trading engine.

This module provides a lightweight test double implementation of a trading engine.
This test double is used in component tests to avoid the overhead of the real
Nautilus BacktestEngine and C extension dependencies.

Design Pattern: Test Double (Fake)
Purpose: Enable fast component testing without Nautilus framework overhead
Reference: design.md Section 2.3 - Test Double Design
"""

from decimal import Decimal
from typing import Dict, List, Optional

from .test_order import TestOrder
from .test_position import TestPosition


class TestTradingEngine:
    """
    Lightweight test double for trading engine.

    This test double provides a simplified trading engine implementation for
    component testing. It tracks orders, positions, and balance without the
    complexity of the Nautilus BacktestEngine.

    Attributes:
        submitted_orders: List of all submitted orders
        positions: Dictionary mapping symbol to current position
        balance: Current account balance
        event_log: Log of all engine events
        max_position_size: Maximum allowed position size per symbol

    Example:
        >>> engine = TestTradingEngine(initial_balance=Decimal("10000"))
        >>> order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        >>> order_id = engine.submit_order(order)
        >>> engine.get_position("BTCUSDT")
        Decimal('0.5')
    """

    def __init__(
        self,
        initial_balance: Decimal = Decimal("10000"),
        max_position_size: Optional[Decimal] = None,
    ) -> None:
        """
        Initialize test trading engine.

        Args:
            initial_balance: Starting account balance
            max_position_size: Maximum position size per symbol (None for unlimited)
        """
        self.submitted_orders: List[TestOrder] = []
        self.positions: Dict[str, Decimal] = {}
        self.balance = initial_balance
        self.event_log: List[str] = []
        self.max_position_size = max_position_size
        self._order_counter = 0

    def submit_order(self, order: TestOrder) -> str:
        """
        Submit an order to the engine.

        Args:
            order: Order to submit

        Returns:
            Order ID assigned by the engine

        Raises:
            ValueError: If order exceeds maximum position size

        Example:
            >>> engine = TestTradingEngine()
            >>> order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))
            >>> order_id = engine.submit_order(order)
            >>> order.status
            'FILLED'
        """
        # Check position limits
        if self.max_position_size is not None:
            current_pos = self.get_position(order.symbol)
            new_quantity = order.quantity if order.side == "BUY" else -order.quantity
            new_position = abs(current_pos + new_quantity)

            if new_position > self.max_position_size:
                raise ValueError(
                    f"Exceeds maximum position size: {new_position} > {self.max_position_size}"
                )

        # Assign order ID
        self._order_counter += 1
        order_id = f"TEST_{self._order_counter}"
        order.order_id = order_id

        # Add to submitted orders
        self.submitted_orders.append(order)
        self.event_log.append(f"ORDER_SUBMITTED: {order_id}")

        # Auto-fill market orders (simplified simulation)
        if order.order_type == "MARKET":
            self._fill_order(order)

        return order_id

    def _fill_order(self, order: TestOrder) -> None:
        """
        Simulate order fill.

        Args:
            order: Order to fill
        """
        # Update position
        multiplier = Decimal("1") if order.side == "BUY" else Decimal("-1")
        current_pos = self.positions.get(order.symbol, Decimal("0"))
        self.positions[order.symbol] = current_pos + (order.quantity * multiplier)

        # Update order status
        order.status = "FILLED"
        self.event_log.append(f"ORDER_FILLED: {order.order_id} - {order.symbol}")

    def get_position(self, symbol: str) -> Decimal:
        """
        Get current position for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current position quantity (positive for long, negative for short)

        Example:
            >>> engine = TestTradingEngine()
            >>> engine.get_position("BTCUSDT")
            Decimal('0')
        """
        return self.positions.get(symbol, Decimal("0"))

    def get_position_details(self, symbol: str, current_price: Decimal) -> Optional[TestPosition]:
        """
        Get detailed position information.

        Args:
            symbol: Trading symbol
            current_price: Current market price for calculating PnL

        Returns:
            TestPosition object with position details, or None if no position

        Example:
            >>> engine = TestTradingEngine()
            >>> order = TestOrder("BTCUSDT", "BUY", Decimal("1"), Decimal("50000"))
            >>> engine.submit_order(order)
            'TEST_1'
            >>> position = engine.get_position_details("BTCUSDT", Decimal("51000"))
            >>> position.unrealized_pnl
            Decimal('1000.00')
        """
        quantity = self.get_position(symbol)
        if quantity == 0:
            return None

        # Calculate average entry price from filled orders
        total_quantity = Decimal("0")
        total_value = Decimal("0")

        for order in self.submitted_orders:
            if order.symbol == symbol and order.status == "FILLED":
                fill_price = order.price if order.price else current_price
                multiplier = Decimal("1") if order.side == "BUY" else Decimal("-1")
                total_quantity += order.quantity * multiplier
                total_value += order.quantity * fill_price * multiplier

        entry_price = abs(total_value / total_quantity) if total_quantity != 0 else current_price

        return TestPosition(
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            current_price=current_price,
        )

    def get_balance(self) -> Decimal:
        """
        Get current account balance.

        Returns:
            Current account balance

        Example:
            >>> engine = TestTradingEngine(initial_balance=Decimal("10000"))
            >>> engine.get_balance()
            Decimal('10000')
        """
        return self.balance

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel a pending order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if order was cancelled, False if not found or already filled

        Example:
            >>> engine = TestTradingEngine()
            >>> order = TestOrder("BTCUSDT", "BUY", Decimal("1"), Decimal("50000"), "LIMIT")
            >>> order_id = engine.submit_order(order)
            >>> engine.cancel_order(order_id)
            True
        """
        for order in self.submitted_orders:
            if order.order_id == order_id and order.status == "PENDING":
                order.status = "CANCELLED"
                self.event_log.append(f"ORDER_CANCELLED: {order_id}")
                return True

        return False

    def reset(self) -> None:
        """
        Reset engine to initial state.

        Clears all orders, positions, and event log while preserving balance.

        Example:
            >>> engine = TestTradingEngine()
            >>> engine.submit_order(TestOrder("BTCUSDT", "BUY", Decimal("1")))
            'TEST_1'
            >>> engine.reset()
            >>> len(engine.submitted_orders)
            0
        """
        self.submitted_orders.clear()
        self.positions.clear()
        self.event_log.clear()
        self._order_counter = 0
