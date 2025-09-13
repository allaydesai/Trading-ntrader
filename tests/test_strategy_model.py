"""Tests for strategy models."""

import pytest
from decimal import Decimal

from src.models.strategy import (
    TradingStrategy,
    StrategyType,
    SMAParameters,
    StrategyStatus,
)


def test_sma_parameters_validation():
    """Test SMA parameters validation."""
    # Valid parameters
    params = SMAParameters(
        fast_period=10, slow_period=20, trade_size=Decimal("1000000")
    )
    assert params.fast_period == 10
    assert params.slow_period == 20
    assert params.trade_size == Decimal("1000000")

    # Invalid: slow period not greater than fast
    with pytest.raises(
        ValueError, match="Slow period must be greater than fast period"
    ):
        SMAParameters(fast_period=20, slow_period=10)

    # Invalid: negative period
    with pytest.raises(ValueError):
        SMAParameters(fast_period=-5, slow_period=20)


def test_trading_strategy_creation():
    """Test trading strategy creation."""
    sma_params = {"fast_period": 10, "slow_period": 20, "trade_size": "1000000"}

    strategy = TradingStrategy(
        name="SMA Crossover Test",
        strategy_type=StrategyType.SMA_CROSSOVER,
        parameters=sma_params,
    )

    assert strategy.name == "SMA Crossover Test"
    assert strategy.strategy_type == StrategyType.SMA_CROSSOVER
    assert strategy.parameters == sma_params
    assert strategy.status == StrategyStatus.DRAFT
    assert strategy.is_active is True


def test_strategy_status_transitions():
    """Test strategy status transitions."""
    strategy = TradingStrategy(
        name="Test Strategy",
        strategy_type=StrategyType.SMA_CROSSOVER,
        parameters={"fast_period": 10, "slow_period": 20, "trade_size": "1000000"},
    )

    # Initial state
    assert strategy.status == StrategyStatus.DRAFT

    # Activate
    strategy.activate()
    assert strategy.status == StrategyStatus.ACTIVE
    assert strategy.is_active is True

    # Archive
    strategy.archive()
    assert strategy.status == StrategyStatus.ARCHIVED
    assert strategy.is_active is False

    # Cannot reactivate archived strategy
    with pytest.raises(ValueError, match="Cannot activate archived strategy"):
        strategy.activate()
