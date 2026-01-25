"""Tests for Strategy Factory implementation."""

from decimal import Decimal

import pytest
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_factory import StrategyFactory, StrategyLoader


class TestStrategyFactory:
    """Test cases for StrategyFactory class."""

    @pytest.mark.component
    def test_create_strategy_class_valid_path(self):
        """Test creating strategy class from valid path."""
        strategy_class = StrategyFactory.create_strategy_class(
            "src.core.strategies.sma_crossover:SMACrossover"
        )

        assert strategy_class is not None
        assert issubclass(strategy_class, Strategy)
        assert strategy_class.__name__ == "SMACrossover"

    @pytest.mark.component
    def test_create_strategy_class_invalid_format(self):
        """Test creating strategy class with invalid path format."""
        with pytest.raises(ValueError, match="Strategy path must be in format"):
            StrategyFactory.create_strategy_class("invalid_path")

    @pytest.mark.component
    def test_create_strategy_class_missing_module(self):
        """Test creating strategy class from non-existent module."""
        with pytest.raises(ImportError, match="Cannot import module"):
            StrategyFactory.create_strategy_class("nonexistent.module:ClassName")

    @pytest.mark.component
    def test_create_strategy_class_missing_class(self):
        """Test creating strategy class with non-existent class."""
        with pytest.raises(AttributeError, match="Class .* not found"):
            StrategyFactory.create_strategy_class(
                "src.core.strategies.sma_crossover:NonExistentClass"
            )

    @pytest.mark.component
    def test_create_config_class_valid_path(self):
        """Test creating config class from valid path."""
        config_class = StrategyFactory.create_config_class(
            "src.core.strategies.sma_crossover:SMAConfig"
        )

        assert config_class is not None
        assert issubclass(config_class, StrategyConfig)
        assert config_class.__name__ == "SMAConfig"

    @pytest.mark.component
    def test_create_config_class_invalid_format(self):
        """Test creating config class with invalid path format."""
        with pytest.raises(ValueError, match="Config path must be in format"):
            StrategyFactory.create_config_class("invalid_path")

    @pytest.mark.component
    def test_create_strategy_from_config_sma(self):
        """Test creating SMA strategy from configuration."""
        strategy = StrategyFactory.create_strategy_from_config(
            strategy_path="src.core.strategies.sma_crossover:SMACrossover",
            config_path="src.core.strategies.sma_crossover:SMAConfig",
            config_params={
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "fast_period": 10,
                "slow_period": 20,
                "portfolio_value": Decimal("1000000"),
                "position_size_pct": Decimal("10.0"),
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "SMACrossover"

    @pytest.mark.component
    def test_create_strategy_from_config_momentum(self):
        """Test creating Momentum strategy from configuration."""
        strategy = StrategyFactory.create_strategy_from_config(
            strategy_path="src.core.strategies.sma_momentum:SMAMomentum",
            config_path="src.core.strategies.sma_momentum:SMAMomentumConfig",
            config_params={
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "trade_size": Decimal("1000000"),
                "order_id_tag": "002",
                "fast_period": 20,
                "slow_period": 50,
                "warmup_days": 1,
                "allow_short": False,
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "SMAMomentum"

    @pytest.mark.component
    def test_create_strategy_from_config_invalid_params(self):
        """Test creating strategy with invalid configuration parameters."""
        with pytest.raises(Exception):  # Could be ValidationError or ValueError
            StrategyFactory.create_strategy_from_config(
                strategy_path="src.core.strategies.sma_crossover:SMACrossover",
                config_path="src.core.strategies.sma_crossover:SMAConfig",
                config_params={
                    "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                    "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                    "fast_period": -10,  # Invalid
                    "slow_period": 20,
                    "portfolio_value": Decimal("1000000"),
                    "position_size_pct": Decimal("10.0"),
                },
            )

    @pytest.mark.component
    def test_get_strategy_name_from_path_sma(self):
        """Test determining strategy name for SMA crossover."""
        strategy_name = StrategyFactory.get_strategy_name_from_path(
            "src.core.strategies.sma_crossover:SMACrossover"
        )
        assert strategy_name == "sma_crossover"

    @pytest.mark.component
    def test_get_strategy_name_from_path_momentum(self):
        """Test determining strategy name for Momentum."""
        strategy_name = StrategyFactory.get_strategy_name_from_path(
            "src.core.strategies.sma_momentum:SMAMomentum"
        )
        assert strategy_name == "momentum"

    @pytest.mark.component
    def test_get_strategy_name_from_path_unknown(self):
        """Test determining strategy name for unknown strategy."""
        with pytest.raises(ValueError, match="Cannot determine strategy from path"):
            StrategyFactory.get_strategy_name_from_path(
                "src.core.strategies.unknown:UnknownStrategy"
            )

    @pytest.mark.component
    def test_validate_strategy_config_sma_valid(self):
        """Test validating valid SMA configuration."""
        result = StrategyFactory.validate_strategy_config(
            "sma_crossover",
            {
                "fast_period": 10,
                "slow_period": 20,
                "portfolio_value": "1000000",
                "position_size_pct": "10.0",
            },
        )
        assert result is True

    @pytest.mark.component
    def test_validate_strategy_config_momentum_valid(self):
        """Test validating valid Momentum configuration."""
        result = StrategyFactory.validate_strategy_config(
            "momentum",
            {
                "fast_period": 20,
                "slow_period": 50,
                "trade_size": "1000000",
            },
        )
        assert result is True


class TestStrategyLoader:
    """Test cases for StrategyLoader class."""

    @pytest.mark.component
    def test_create_strategy_sma(self):
        """Test creating SMA strategy using loader."""
        strategy = StrategyLoader.create_strategy(
            "sma_crossover",
            {
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "fast_period": 10,
                "slow_period": 20,
                "portfolio_value": Decimal("1000000"),
                "position_size_pct": Decimal("10.0"),
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "SMACrossover"

    @pytest.mark.component
    def test_create_strategy_momentum(self):
        """Test creating Momentum strategy using loader."""
        strategy = StrategyLoader.create_strategy(
            "momentum",
            {
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "trade_size": Decimal("1000000"),
                "order_id_tag": "002",
                "fast_period": 20,
                "slow_period": 50,
                "warmup_days": 1,
                "allow_short": False,
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "SMAMomentum"

    @pytest.mark.component
    def test_create_strategy_unsupported_type(self):
        """Test creating strategy with unsupported type."""
        with pytest.raises(ValueError, match="Unsupported strategy type"):
            StrategyLoader.create_strategy("FAKE_STRATEGY", {})

    @pytest.mark.component
    def test_get_available_strategies(self):
        """Test getting available strategies."""
        strategies = StrategyLoader.get_available_strategies()

        assert isinstance(strategies, dict)
        assert "sma_crossover" in strategies
        assert "momentum" in strategies

        # Check structure
        for strategy_name, mapping in strategies.items():
            assert "strategy_path" in mapping
            assert "config_path" in mapping

    @pytest.mark.component
    def test_validate_strategy_type_valid(self):
        """Test validating supported strategy types."""
        assert StrategyLoader.validate_strategy_type("sma_crossover") is True
        assert StrategyLoader.validate_strategy_type("momentum") is True

    @pytest.mark.component
    def test_strategy_mappings_structure(self):
        """Test that strategy mappings have correct structure."""
        mappings = StrategyLoader.get_available_strategies()

        for strategy_name, mapping in mappings.items():
            assert isinstance(strategy_name, str)
            assert "strategy_path" in mapping
            assert "config_path" in mapping
            assert ":" in mapping["strategy_path"]
            assert ":" in mapping["config_path"]
