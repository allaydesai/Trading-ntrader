"""Tests for strategy models."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models.strategy import (
    SMAParameters,
    StrategyStatus,
    TradingStrategy,
)


@pytest.mark.component
def test_sma_parameters_validation():
    """Test SMA parameters validation."""
    # Valid parameters
    params = SMAParameters(
        fast_period=10,
        slow_period=20,
        portfolio_value=Decimal("1000000"),
        position_size_pct=Decimal("10.0"),
    )
    assert params.fast_period == 10
    assert params.slow_period == 20
    assert params.portfolio_value == Decimal("1000000")

    # Invalid: slow period not greater than fast
    with pytest.raises(ValueError, match="Slow period must be greater than fast period"):
        SMAParameters(
            fast_period=20,
            slow_period=10,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )

    # Invalid: negative period
    with pytest.raises(ValueError):
        SMAParameters(
            fast_period=-5,
            slow_period=20,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )


@pytest.mark.component
def test_sma_parameters_edge_cases():
    """Test SMA parameters edge case validations."""
    # Test minimum valid values
    params = SMAParameters(
        fast_period=1,
        slow_period=2,
        portfolio_value=Decimal("0.01"),
        position_size_pct=Decimal("0.1"),
    )
    assert params.fast_period == 1
    assert params.slow_period == 2

    # Test maximum valid values
    params = SMAParameters(
        fast_period=199,
        slow_period=200,
        portfolio_value=Decimal("999999999"),
        position_size_pct=Decimal("100.0"),
    )
    assert params.fast_period == 199
    assert params.slow_period == 200

    # Test zero period
    with pytest.raises(ValidationError):
        SMAParameters(
            fast_period=0,
            slow_period=20,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )

    # Test equal periods
    with pytest.raises(ValueError, match="Slow period must be greater than fast period"):
        SMAParameters(
            fast_period=20,
            slow_period=20,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )

    # Test out of range periods
    with pytest.raises(ValidationError):
        SMAParameters(
            fast_period=201,
            slow_period=202,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )

    # Test negative portfolio value
    with pytest.raises(ValidationError):
        SMAParameters(
            fast_period=10,
            slow_period=20,
            portfolio_value=Decimal("-1000"),
            position_size_pct=Decimal("10.0"),
        )

    # Test invalid position size pct
    with pytest.raises(ValidationError):
        SMAParameters(
            fast_period=10,
            slow_period=20,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("101.0"),  # > 100
        )


@pytest.mark.component
def test_trading_strategy_creation():
    """Test trading strategy creation."""
    sma_params = {
        "fast_period": 10,
        "slow_period": 20,
        "portfolio_value": "1000000",
        "position_size_pct": "10.0",
    }

    strategy = TradingStrategy(
        name="SMA Crossover Test",
        strategy_type="sma_crossover",
        parameters=sma_params,
    )

    assert strategy.name == "SMA Crossover Test"
    assert strategy.strategy_type == "sma_crossover"
    assert strategy.parameters == sma_params
    assert strategy.status == StrategyStatus.DRAFT
    assert strategy.is_active is True
    assert isinstance(strategy.id, UUID)
    assert isinstance(strategy.created_at, datetime)
    assert isinstance(strategy.updated_at, datetime)


@pytest.mark.component
def test_trading_strategy_validation_errors():
    """Test trading strategy validation errors."""
    # Test empty name
    with pytest.raises(ValidationError):
        TradingStrategy(
            name="",
            strategy_type="sma_crossover",
            parameters={
                "fast_period": 10,
                "slow_period": 20,
                "portfolio_value": "1000000",
                "position_size_pct": "10.0",
            },
        )

    # Test name too long
    with pytest.raises(ValidationError):
        TradingStrategy(
            name="x" * 101,  # Exceeds 100 character limit
            strategy_type="sma_crossover",
            parameters={
                "fast_period": 10,
                "slow_period": 20,
                "portfolio_value": "1000000",
                "position_size_pct": "10.0",
            },
        )

    # Test empty parameters
    with pytest.raises(ValueError, match="At least one parameter required"):
        TradingStrategy(
            name="Test Strategy",
            strategy_type="sma_crossover",
            parameters={},
        )

    # Test invalid SMA parameters
    with pytest.raises(ValueError, match="Invalid Sma Crossover parameters"):
        TradingStrategy(
            name="Test Strategy",
            strategy_type="sma_crossover",
            parameters={
                "fast_period": 20,
                "slow_period": 10,  # Invalid: slow < fast
                "portfolio_value": "1000000",
                "position_size_pct": "10.0",
            },
        )


@pytest.mark.component
def test_strategy_parameter_validation_by_type():
    """Test that parameters are validated according to strategy type."""
    # Valid SMA parameters
    valid_sma = TradingStrategy(
        name="SMA Strategy",
        strategy_type="sma_crossover",
        parameters={
            "fast_period": 10,
            "slow_period": 20,
            "portfolio_value": "1000000",
            "position_size_pct": "10.0",
        },
    )
    assert valid_sma.strategy_type == "sma_crossover"

    # Test that unknown strategy types skip validation (allows external strategies)
    # This enables strategies in custom/ directory to work without modification
    custom_strategy = TradingStrategy(
        name="Custom Strategy",
        strategy_type="custom_external_strategy",
        parameters={
            "some_param": 42,
            "another_param": "value",
        },
    )
    assert custom_strategy.strategy_type == "custom_external_strategy"


@pytest.mark.component
def test_strategy_status_transitions():
    """Test strategy status transitions."""
    strategy = TradingStrategy(
        name="Test Strategy",
        strategy_type="sma_crossover",
        parameters={
            "fast_period": 10,
            "slow_period": 20,
            "portfolio_value": "1000000",
            "position_size_pct": "10.0",
        },
    )

    # Initial state
    assert strategy.status == StrategyStatus.DRAFT
    assert strategy.is_active is True

    # Activate
    initial_updated_at = strategy.updated_at
    strategy.activate()
    assert strategy.status == StrategyStatus.ACTIVE
    assert strategy.is_active is True
    assert strategy.updated_at > initial_updated_at

    # Archive
    activate_updated_at = strategy.updated_at
    strategy.archive()
    assert strategy.status == StrategyStatus.ARCHIVED
    assert strategy.is_active is False
    assert strategy.updated_at > activate_updated_at

    # Cannot reactivate archived strategy
    with pytest.raises(ValueError, match="Cannot activate archived strategy"):
        strategy.activate()


@pytest.mark.component
def test_strategy_can_activate_from_draft():
    """Test that draft strategies can be activated."""
    strategy = TradingStrategy(
        name="Test Strategy",
        strategy_type="sma_crossover",
        parameters={
            "fast_period": 10,
            "slow_period": 20,
            "portfolio_value": "1000000",
            "position_size_pct": "10.0",
        },
    )

    assert strategy.status == StrategyStatus.DRAFT
    strategy.activate()
    assert strategy.status == StrategyStatus.ACTIVE

    # Can activate again from active status
    strategy.activate()
    assert strategy.status == StrategyStatus.ACTIVE


@pytest.mark.component
def test_update_timestamp():
    """Test manual timestamp updates."""
    strategy = TradingStrategy(
        name="Test Strategy",
        strategy_type="sma_crossover",
        parameters={
            "fast_period": 10,
            "slow_period": 20,
            "portfolio_value": "1000000",
            "position_size_pct": "10.0",
        },
    )

    initial_updated_at = strategy.updated_at
    strategy.update_timestamp()
    assert strategy.updated_at > initial_updated_at


@pytest.mark.component
def test_strategy_model_config():
    """Test model configuration and serialization."""
    strategy = TradingStrategy(
        name="Test Strategy",
        strategy_type="sma_crossover",
        parameters={
            "fast_period": 10,
            "slow_period": 20,
            "portfolio_value": "1000000",
            "position_size_pct": "10.0",
        },
    )

    # Test JSON serialization
    json_data = strategy.model_dump()

    # Check that strategy_type is serialized as string
    assert json_data["strategy_type"] == "sma_crossover"
    assert json_data["status"] == "draft"

    # Check UUID serialization (UUID objects remain as UUID in model_dump by default)
    assert isinstance(json_data["id"], UUID)

    # Test deserialization
    recreated = TradingStrategy.model_validate(json_data)
    assert recreated.name == strategy.name
    assert recreated.strategy_type == strategy.strategy_type
    assert recreated.parameters == strategy.parameters


@pytest.mark.component
def test_strategy_status_enum_values():
    """Test StrategyStatus enum values."""
    # Test StrategyStatus enum
    assert StrategyStatus.DRAFT == "draft"
    assert StrategyStatus.ACTIVE == "active"
    assert StrategyStatus.ARCHIVED == "archived"
