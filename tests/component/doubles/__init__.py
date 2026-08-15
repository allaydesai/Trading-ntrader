"""Test double implementations for component testing.

This package provides lightweight test doubles for Nautilus Trader components,
enabling fast component tests without the overhead of C extensions and real
framework initialization.

Test Doubles Provided:
    - TestOrder: Simplified order representation
    - TestPosition: Simplified position representation
    - TestTradingEngine: Lightweight trading engine simulator
    - TestIBConnection: Interactive Brokers client connection flags
    - TestIBConnectionWithoutFlags: A cached client missing those flags
    - TestIBConnectionWithPlainFlags: Flags refactored to bare booleans
    - TestIBConnectionWithRaisingFlags: Flag reads that raise
    - TestLiveNode: TradingNode stand-in for the one-shot connectivity check
    - TestBarObserver: A real LiveBarObserver with pre-seeded counters
    - TestIBAccountsClient: An IB_CLIENTS entry that names accounts
    - TestInstrument: The instrument id a cached instrument exposes

Usage:
    >>> from tests.component.doubles import TestTradingEngine, TestOrder
    >>> engine = TestTradingEngine(initial_balance=Decimal("10000"))
    >>> order = TestOrder("BTCUSDT", "BUY", Decimal("1.0"))
    >>> engine.submit_order(order)

Reference: design.md Section 2.3 - Test Double Design
"""

from .test_engine import TestTradingEngine
from .test_ib_connection import (
    TestIBConnection,
    TestIBConnectionWithoutFlags,
    TestIBConnectionWithPlainFlags,
    TestIBConnectionWithRaisingFlags,
)
from .test_live_node import (
    TestBarObserver,
    TestIBAccountsClient,
    TestInstrument,
    TestLiveNode,
)
from .test_order import TestOrder
from .test_position import TestPosition

__all__ = [
    "TestBarObserver",
    "TestIBAccountsClient",
    "TestIBConnection",
    "TestIBConnectionWithPlainFlags",
    "TestIBConnectionWithRaisingFlags",
    "TestIBConnectionWithoutFlags",
    "TestInstrument",
    "TestLiveNode",
    "TestOrder",
    "TestPosition",
    "TestTradingEngine",
]
