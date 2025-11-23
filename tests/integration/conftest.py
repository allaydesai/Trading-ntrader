"""Integration test fixtures and configuration.

This module provides fixtures for integration tests that use real Nautilus
framework components, including C extensions that require special cleanup.

IMPORTANT: Integration tests MUST run with subprocess isolation (--forked flag)
to prevent C extension crashes from cascading between tests.

Why --forked is Required:
--------------------------
Nautilus Trader uses C/Cython extensions that maintain internal state. When a
test crashes or leaves the C extension in a bad state, subsequent tests in the
same process can experience cascade failures or segfaults.

The pytest-forked plugin runs each integration test in a separate subprocess,
ensuring complete isolation. If a test crashes, only that subprocess fails -
other tests continue normally in fresh processes.

Running Integration Tests:
--------------------------
Always use the Makefile target which includes --forked:
    make test-integration

Or run directly with pytest:
    pytest tests/integration/ -n auto --forked

NEVER run integration tests without --forked in CI/CD or shared environments.

Reference: design.md Section 2.1 - Subprocess Isolation Pattern
"""

import gc

import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue


@pytest.fixture(autouse=True)
def integration_cleanup():
    """Enhanced cleanup for integration tests with C extensions.

    This fixture runs automatically after every integration test to clean up
    Nautilus C extension state and prevent memory leaks.

    Cleanup Strategy:
    - Double gc.collect() to handle cyclic references in C extensions
    - Explicit cleanup for Nautilus engine instances (handled in tests via dispose())
    - Runs after each test via autouse=True

    Note: Even with this cleanup, --forked isolation is still required to
    prevent cascade failures from C extension crashes.

    Reference: design.md Section 5.2 - Memory Management
    """
    yield  # Test runs here

    # Force aggressive garbage collection for C extension cleanup
    gc.collect()
    gc.collect()  # Second pass ensures cyclic references are broken


def setup_backtest_venue(
    engine: BacktestEngine,
    venue_name: str = "BINANCE",
    starting_balances: list[str] = None,
):
    """Helper to set up a venue in the backtest engine.

    Args:
        engine: The BacktestEngine instance
        venue_name: Name of the venue (default: BINANCE)
        starting_balances: List of starting balances (default: ["1000000 USDT"])

    Note:
        This must be called before adding instruments to the engine.
        Nautilus requires venues to be registered before instruments can be added.
    """
    if starting_balances is None:
        starting_balances = ["1000000 USDT"]

    engine.add_venue(
        venue=Venue(venue_name),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        starting_balances=starting_balances,
    )
