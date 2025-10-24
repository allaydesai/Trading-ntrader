"""Component test fixtures and configuration.

This module provides fixtures for component tests using test doubles.
Component tests verify strategy behavior without the overhead of real
Nautilus framework components.

Reference: design.md Section 2.3 - Test Double Design
"""

import pytest
from decimal import Decimal

from tests.component.doubles import TestTradingEngine, TestOrder, TestPosition
from src.core.sma_logic import SMATradingLogic
from src.core.position_sizing import PositionSizingLogic
from src.core.risk_management import RiskManagementLogic


@pytest.fixture
def test_engine():
    """
    Provide a clean TestTradingEngine instance for component tests.

    Returns:
        TestTradingEngine with default initial balance of 10,000

    Example:
        >>> def test_strategy(test_engine):
        ...     order = TestOrder("BTCUSDT", "BUY", Decimal("0.5"))
        ...     test_engine.submit_order(order)
        ...     assert test_engine.get_position("BTCUSDT") == Decimal("0.5")
    """
    return TestTradingEngine(initial_balance=Decimal("10000"))


@pytest.fixture
def test_engine_with_limits():
    """
    Provide a TestTradingEngine with position size limits.

    Returns:
        TestTradingEngine with balance of 10,000 and max position size of 1.0

    Example:
        >>> def test_limits(test_engine_with_limits):
        ...     # This will raise ValueError
        ...     order = TestOrder("BTCUSDT", "BUY", Decimal("2.0"))
        ...     test_engine_with_limits.submit_order(order)
    """
    return TestTradingEngine(
        initial_balance=Decimal("10000"), max_position_size=Decimal("1.0")
    )


@pytest.fixture
def sma_logic():
    """
    Provide SMA trading logic instance with default parameters.

    Returns:
        SMATradingLogic with fast_period=5 and slow_period=20

    Example:
        >>> def test_crossover(sma_logic):
        ...     signal = sma_logic.detect_crossover(
        ...         Decimal("105"), Decimal("100"), Decimal("98"), Decimal("100")
        ...     )
        ...     assert signal == CrossoverSignal.GOLDEN_CROSS
    """
    return SMATradingLogic(fast_period=5, slow_period=20)


@pytest.fixture
def position_sizing_logic():
    """
    Provide position sizing logic instance.

    Returns:
        PositionSizingLogic instance for position size calculations

    Example:
        >>> def test_sizing(position_sizing_logic):
        ...     size = position_sizing_logic.calculate_risk_based_size(
        ...         Decimal("10000"), Decimal("0.02"), Decimal("50000"), Decimal("49000")
        ...     )
        ...     assert size == Decimal("0.2")
    """
    return PositionSizingLogic()


@pytest.fixture
def risk_manager():
    """
    Provide risk management logic instance with default limits.

    Returns:
        RiskManagementLogic with 2% position risk and 10% account risk limits

    Example:
        >>> def test_risk(risk_manager):
        ...     is_valid = risk_manager.validate_position_risk(
        ...         Decimal("1000"), Decimal("10000"), Decimal("0.05")
        ...     )
        ...     assert is_valid is True
    """
    return RiskManagementLogic(
        max_position_risk_percent=Decimal("0.02"),  # 2% max risk per position
        max_account_risk_percent=Decimal("0.10"),  # 10% max total account risk
    )


@pytest.fixture
def sample_test_order():
    """
    Provide a sample TestOrder for testing.

    Returns:
        TestOrder for BTC with quantity 0.5

    Example:
        >>> def test_order(sample_test_order):
        ...     assert sample_test_order.symbol == "BTCUSDT"
        ...     assert sample_test_order.quantity == Decimal("0.5")
    """
    return TestOrder(
        symbol="BTCUSDT", side="BUY", quantity=Decimal("0.5"), order_type="MARKET"
    )


@pytest.fixture
def sample_test_position():
    """
    Provide a sample TestPosition for testing.

    Returns:
        TestPosition for BTC with entry at 50,000 and current at 51,000

    Example:
        >>> def test_position(sample_test_position):
        ...     assert sample_test_position.unrealized_pnl == Decimal("500")
    """
    return TestPosition(
        symbol="BTCUSDT",
        quantity=Decimal("0.5"),
        entry_price=Decimal("50000"),
        current_price=Decimal("51000"),
    )
