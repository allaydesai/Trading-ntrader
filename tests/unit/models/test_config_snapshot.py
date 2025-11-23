"""Unit tests for StrategyConfigSnapshot Pydantic model."""

import pytest
from pydantic import ValidationError

from src.models.config_snapshot import StrategyConfigSnapshot


class TestStrategyConfigSnapshot:
    """Test suite for StrategyConfigSnapshot Pydantic validation model."""

    def test_valid_config_snapshot(self):
        """Test StrategyConfigSnapshot with valid data."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.sma_crossover.SMAStrategyConfig",
            config_path="config/strategies/sma_crossover.yaml",
            version="1.0",
            config={"fast_period": 10, "slow_period": 50, "risk_percent": 2.0},
        )

        assert snapshot.strategy_path == "src.strategies.sma_crossover.SMAStrategyConfig"
        assert snapshot.config_path == "config/strategies/sma_crossover.yaml"
        assert snapshot.version == "1.0"
        assert snapshot.config["fast_period"] == 10
        assert snapshot.config["slow_period"] == 50

    def test_missing_required_fields(self):
        """Test StrategyConfigSnapshot raises error for missing required fields."""
        with pytest.raises(ValidationError) as exc_info:
            StrategyConfigSnapshot(
                strategy_path="src.strategies.test.TestStrategy",
                # Missing config_path
                version="1.0",
                config={},
            )

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("config_path",) for error in errors)

    def test_empty_config_allowed(self):
        """Test StrategyConfigSnapshot allows empty config dict."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.test.TestStrategy",
            config_path="config/test.yaml",
            version="1.0",
            config={},
        )

        assert snapshot.config == {}

    def test_nested_config_structure(self):
        """Test StrategyConfigSnapshot with nested configuration."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.complex.ComplexStrategy",
            config_path="config/complex.yaml",
            version="1.0",
            config={
                "risk_management": {"max_position_size": 1000, "stop_loss_pct": 0.05},
                "indicators": {"sma_period": 20, "rsi_period": 14},
            },
        )

        assert "risk_management" in snapshot.config
        assert snapshot.config["risk_management"]["max_position_size"] == 1000

    def test_serialization_to_dict(self):
        """Test StrategyConfigSnapshot serializes to dictionary."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.sma_crossover.SMAStrategyConfig",
            config_path="config/strategies/sma_crossover.yaml",
            version="1.0",
            config={"fast_period": 10, "slow_period": 50},
        )

        snapshot_dict = snapshot.model_dump()

        assert isinstance(snapshot_dict, dict)
        assert snapshot_dict["strategy_path"] == "src.strategies.sma_crossover.SMAStrategyConfig"
        assert "config" in snapshot_dict
