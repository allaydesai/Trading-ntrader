"""Tests for Strategy Factory implementation."""

import pytest
from decimal import Decimal
from pydantic import ValidationError

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.strategy_factory import StrategyFactory, StrategyLoader
from src.models.strategy import StrategyType


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
    def test_create_strategy_from_config_mean_reversion(self):
        """Test creating Mean Reversion strategy from configuration."""
        strategy = StrategyFactory.create_strategy_from_config(
            strategy_path="src.core.strategies.rsi_mean_reversion:RSIMeanRev",
            config_path="src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig",
            config_params={
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "trade_size": Decimal("1000000"),
                "order_id_tag": "001",
                "rsi_period": 2,
                "rsi_buy_threshold": 10.0,
                "exit_rsi": 50.0,
                "sma_trend_period": 200,
                "warmup_days": 400,
                "cooldown_bars": 0,
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "RSIMeanRev"

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
        with pytest.raises((ValidationError, ValueError)):
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
    def test_get_strategy_type_from_path_sma(self):
        """Test determining strategy type for SMA crossover."""
        strategy_type = StrategyFactory.get_strategy_type_from_path(
            "src.core.strategies.sma_crossover:SMACrossover"
        )
        assert strategy_type == StrategyType.SMA_CROSSOVER

    @pytest.mark.component
    def test_get_strategy_type_from_path_mean_reversion(self):
        """Test determining strategy type for Mean Reversion."""
        strategy_type = StrategyFactory.get_strategy_type_from_path(
            "src.core.strategies.mean_reversion:MeanReversionStrategy"
        )
        assert strategy_type == StrategyType.MEAN_REVERSION

    @pytest.mark.component
    def test_get_strategy_type_from_path_momentum(self):
        """Test determining strategy type for Momentum."""
        strategy_type = StrategyFactory.get_strategy_type_from_path(
            "src.core.strategies.momentum:MomentumStrategy"
        )
        assert strategy_type == StrategyType.MOMENTUM

    @pytest.mark.component
    def test_get_strategy_type_from_path_unknown(self):
        """Test determining strategy type for unknown strategy."""
        with pytest.raises(ValueError, match="Cannot determine strategy type"):
            StrategyFactory.get_strategy_type_from_path(
                "src.core.strategies.unknown:UnknownStrategy"
            )

    @pytest.mark.component
    def test_validate_strategy_config_sma_valid(self):
        """Test validating valid SMA configuration."""
        result = StrategyFactory.validate_strategy_config(
            StrategyType.SMA_CROSSOVER,
            {"fast_period": 10, "slow_period": 20, "trade_size": "1000000"},
        )
        assert result is True

    @pytest.mark.component
    def test_validate_strategy_config_sma_invalid(self):
        """Test validating invalid SMA configuration."""
        with pytest.raises(ValidationError):
            StrategyFactory.validate_strategy_config(
                StrategyType.SMA_CROSSOVER,
                {
                    "fast_period": -10,  # Invalid
                    "slow_period": 20,
                    "trade_size": "1000000",
                },
            )

    @pytest.mark.component
    def test_validate_strategy_config_mean_reversion_valid(self):
        """Test validating valid Mean Reversion configuration."""
        result = StrategyFactory.validate_strategy_config(
            StrategyType.MEAN_REVERSION,
            {"lookback_period": 20, "num_std_dev": 2.0, "trade_size": "1000000"},
        )
        assert result is True

    @pytest.mark.component
    def test_validate_strategy_config_momentum_valid(self):
        """Test validating valid Momentum configuration."""
        result = StrategyFactory.validate_strategy_config(
            StrategyType.MOMENTUM,
            {
                "rsi_period": 14,
                "oversold_threshold": 30.0,
                "overbought_threshold": 70.0,
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
            StrategyType.SMA_CROSSOVER,
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
    def test_create_strategy_mean_reversion(self):
        """Test creating Mean Reversion strategy using loader."""
        strategy = StrategyLoader.create_strategy(
            StrategyType.MEAN_REVERSION,
            {
                "instrument_id": InstrumentId.from_str("EUR/USD.SIM"),
                "bar_type": "EUR/USD.SIM-15-MINUTE-MID-EXTERNAL",
                "trade_size": Decimal("1000000"),
                "order_id_tag": "001",
                "rsi_period": 2,
                "rsi_buy_threshold": 10.0,
                "exit_rsi": 50.0,
                "sma_trend_period": 200,
                "warmup_days": 400,
                "cooldown_bars": 0,
            },
        )

        assert strategy is not None
        assert isinstance(strategy, Strategy)
        assert strategy.__class__.__name__ == "RSIMeanRev"

    @pytest.mark.component
    def test_create_strategy_momentum(self):
        """Test creating Momentum strategy using loader."""
        strategy = StrategyLoader.create_strategy(
            StrategyType.MOMENTUM,
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
        # Create a fake strategy type for testing
        with pytest.raises(ValueError, match="Unsupported strategy type"):
            StrategyLoader.create_strategy("FAKE_STRATEGY", {})

    @pytest.mark.component
    def test_get_available_strategies(self):
        """Test getting available strategies."""
        strategies = StrategyLoader.get_available_strategies()

        assert isinstance(strategies, dict)
        assert StrategyType.SMA_CROSSOVER in strategies
        assert StrategyType.MEAN_REVERSION in strategies
        assert StrategyType.MOMENTUM in strategies

        # Check structure
        for strategy_type, mapping in strategies.items():
            assert "strategy_path" in mapping
            assert "config_path" in mapping

    @pytest.mark.component
    def test_validate_strategy_type_valid(self):
        """Test validating supported strategy types."""
        assert StrategyLoader.validate_strategy_type(StrategyType.SMA_CROSSOVER) is True
        assert (
            StrategyLoader.validate_strategy_type(StrategyType.MEAN_REVERSION) is True
        )
        assert StrategyLoader.validate_strategy_type(StrategyType.MOMENTUM) is True

    @pytest.mark.component
    def test_strategy_mappings_structure(self):
        """Test that strategy mappings have correct structure."""
        mappings = StrategyLoader.STRATEGY_MAPPINGS

        for strategy_type, mapping in mappings.items():
            assert isinstance(strategy_type, StrategyType)
            assert "strategy_path" in mapping
            assert "config_path" in mapping
            assert ":" in mapping["strategy_path"]
            assert ":" in mapping["config_path"]
