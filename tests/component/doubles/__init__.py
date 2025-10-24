"""Test double implementations for component testing.

This package provides lightweight test doubles for Nautilus Trader components,
enabling fast component tests without the overhead of C extensions and real
framework initialization.

Test Doubles Provided:
    - TestOrder: Simplified order representation
    - TestPosition: Simplified position representation
    - TestTradingEngine: Lightweight trading engine simulator

Usage:
    >>> from tests.component.doubles import TestTradingEngine, TestOrder
    >>> engine = TestTradingEngine(initial_balance=Decimal("10000"))
    >>> order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))
    >>> engine.submit_order(order)

Reference: design.md Section 2.3 - Test Double Design
"""

from .test_engine import TestTradingEngine
from .test_order import TestOrder
from .test_position import TestPosition

__all__ = [
    "TestOrder",
    "TestPosition",
    "TestTradingEngine",
]
