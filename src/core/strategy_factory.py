"""Strategy factory for dynamic loading of trading strategies."""

import importlib
from typing import Any, Dict, Type

from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from pydantic import ValidationError

from src.models.strategy import StrategyType


class StrategyFactory:
    """Factory for dynamically loading and creating trading strategy instances."""

    @staticmethod
    def create_strategy_class(strategy_path: str) -> Type[Strategy]:
        """
        Load strategy class from module path.

        Parameters
        ----------
        strategy_path : str
            Module path in format "module.path:ClassName"

        Returns
        -------
        Type[Strategy]
            The strategy class

        Raises
        ------
        ImportError
            If module or class cannot be imported
        AttributeError
            If class does not exist in module
        """
        if ":" not in strategy_path:
            raise ValueError("Strategy path must be in format 'module.path:ClassName'")

        module_path, class_name = strategy_path.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Cannot import module '{module_path}': {e}")

        try:
            strategy_class = getattr(module, class_name)
        except AttributeError as e:
            raise AttributeError(
                f"Class '{class_name}' not found in module '{module_path}': {e}"
            )

        if not issubclass(strategy_class, Strategy):
            raise TypeError(
                f"Class '{class_name}' must inherit from nautilus_trader.trading.strategy.Strategy"
            )

        return strategy_class

    @staticmethod
    def create_config_class(config_path: str) -> Type[StrategyConfig]:
        """
        Load strategy config class from module path.

        Parameters
        ----------
        config_path : str
            Module path in format "module.path:ClassName"

        Returns
        -------
        Type[StrategyConfig]
            The config class

        Raises
        ------
        ImportError
            If module or class cannot be imported
        AttributeError
            If class does not exist in module
        """
        if ":" not in config_path:
            raise ValueError("Config path must be in format 'module.path:ClassName'")

        module_path, class_name = config_path.rsplit(":", 1)

        try:
            module = importlib.import_module(module_path)
        except ImportError as e:
            raise ImportError(f"Cannot import module '{module_path}': {e}")

        try:
            config_class = getattr(module, class_name)
        except AttributeError as e:
            raise AttributeError(
                f"Class '{class_name}' not found in module '{module_path}': {e}"
            )

        if not issubclass(config_class, StrategyConfig):
            raise TypeError(
                f"Class '{class_name}' must inherit from nautilus_trader.trading.strategy.StrategyConfig"
            )

        return config_class

    @staticmethod
    def create_strategy_from_config(
        strategy_path: str, config_path: str, config_params: Dict[str, Any]
    ) -> Strategy:
        """
        Create a strategy instance from configuration.

        Parameters
        ----------
        strategy_path : str
            Module path to strategy class in format "module.path:ClassName"
        config_path : str
            Module path to config class in format "module.path:ClassName"
        config_params : Dict[str, Any]
            Configuration parameters

        Returns
        -------
        Strategy
            Configured strategy instance

        Raises
        ------
        ValidationError
            If configuration parameters are invalid
        ImportError
            If strategy or config classes cannot be imported
        """
        # Load classes
        strategy_class = StrategyFactory.create_strategy_class(strategy_path)
        config_class = StrategyFactory.create_config_class(config_path)

        # Create and validate configuration
        try:
            config = config_class(**config_params)
        except ValidationError as e:
            # Re-raise the original ValidationError
            raise e

        # Create strategy instance
        strategy = strategy_class(config)
        return strategy

    @staticmethod
    def get_strategy_type_from_path(strategy_path: str) -> StrategyType:
        """
        Determine strategy type from strategy path.

        Parameters
        ----------
        strategy_path : str
            Module path to strategy class

        Returns
        -------
        StrategyType
            The strategy type enum value

        Raises
        ------
        ValueError
            If strategy type cannot be determined
        """
        # Extract strategy name from path
        if ":" in strategy_path:
            strategy_name = strategy_path.split(":")[-1].lower()
        else:
            strategy_name = strategy_path.split(".")[-1].lower()

        # Map strategy names to types
        strategy_mappings = {
            "smacrossover": StrategyType.SMA_CROSSOVER,
            "sma_crossover": StrategyType.SMA_CROSSOVER,
            "meanreversion": StrategyType.MEAN_REVERSION,
            "mean_reversion": StrategyType.MEAN_REVERSION,
            "meanreversionstrategy": StrategyType.MEAN_REVERSION,
            "rsimeanrev": StrategyType.MEAN_REVERSION,
            "rsi_mean_reversion": StrategyType.MEAN_REVERSION,
            "momentum": StrategyType.MOMENTUM,
            "momentumstrategy": StrategyType.MOMENTUM,
            "smamomentum": StrategyType.MOMENTUM,
            "sma_momentum": StrategyType.MOMENTUM,
        }

        for key, strategy_type in strategy_mappings.items():
            if key in strategy_name:
                return strategy_type

        raise ValueError(f"Cannot determine strategy type from path: {strategy_path}")

    @staticmethod
    def validate_strategy_config(
        strategy_type: StrategyType, config_params: Dict[str, Any]
    ) -> bool:
        """
        Validate configuration parameters for a specific strategy type.

        Parameters
        ----------
        strategy_type : StrategyType
            The strategy type to validate against
        config_params : Dict[str, Any]
            Configuration parameters to validate

        Returns
        -------
        bool
            True if configuration is valid

        Raises
        ------
        ValidationError
            If configuration is invalid
        """
        from src.models.strategy import (
            SMAParameters,
            MeanReversionParameters,
            MomentumParameters,
        )

        # Map strategy types to parameter classes
        param_classes = {
            StrategyType.SMA_CROSSOVER: SMAParameters,
            StrategyType.MEAN_REVERSION: MeanReversionParameters,
            StrategyType.MOMENTUM: MomentumParameters,
        }

        if strategy_type not in param_classes:
            raise ValueError(f"Unknown strategy type: {strategy_type}")

        param_class = param_classes[strategy_type]

        try:
            # Validate parameters
            param_class.model_validate(config_params)
            return True
        except ValidationError as e:
            # Re-raise the original ValidationError with additional context
            raise e


class StrategyLoader:
    """High-level strategy loader with built-in strategy mappings."""

    # Built-in strategy mappings
    STRATEGY_MAPPINGS = {
        StrategyType.SMA_CROSSOVER: {
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "config_path": "src.core.strategies.sma_crossover:SMAConfig",
        },
        StrategyType.MEAN_REVERSION: {
            "strategy_path": "src.core.strategies.rsi_mean_reversion:RSIMeanRev",
            "config_path": "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig",
        },
        StrategyType.MOMENTUM: {
            "strategy_path": "src.core.strategies.sma_momentum:SMAMomentum",
            "config_path": "src.core.strategies.sma_momentum:SMAMomentumConfig",
        },
    }

    @classmethod
    def create_strategy(
        cls, strategy_type: StrategyType, config_params: Dict[str, Any]
    ) -> Strategy:
        """
        Create a strategy instance using built-in mappings.

        Parameters
        ----------
        strategy_type : StrategyType
            The type of strategy to create
        config_params : Dict[str, Any]
            Configuration parameters

        Returns
        -------
        Strategy
            Configured strategy instance

        Raises
        ------
        ValueError
            If strategy type is not supported
        ValidationError
            If configuration parameters are invalid
        """
        if strategy_type not in cls.STRATEGY_MAPPINGS:
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        mapping = cls.STRATEGY_MAPPINGS[strategy_type]

        return StrategyFactory.create_strategy_from_config(
            strategy_path=mapping["strategy_path"],
            config_path=mapping["config_path"],
            config_params=config_params,
        )

    @classmethod
    def get_available_strategies(cls) -> Dict[StrategyType, Dict[str, str]]:
        """
        Get all available strategy types and their mappings.

        Returns
        -------
        Dict[StrategyType, Dict[str, str]]
            Dictionary mapping strategy types to their paths
        """
        return cls.STRATEGY_MAPPINGS.copy()

    @classmethod
    def validate_strategy_type(cls, strategy_type: StrategyType) -> bool:
        """
        Check if a strategy type is supported.

        Parameters
        ----------
        strategy_type : StrategyType
            The strategy type to check

        Returns
        -------
        bool
            True if strategy type is supported
        """
        return strategy_type in cls.STRATEGY_MAPPINGS

    @classmethod
    def list_available(cls) -> Dict[str, Dict[str, str]]:
        """
        List available strategies with descriptions for CLI.

        Returns
        -------
        Dict[str, Dict[str, str]]
            Dictionary mapping strategy names to info
        """
        descriptions = {
            StrategyType.SMA_CROSSOVER: "Simple Moving Average Crossover Strategy",
            StrategyType.MEAN_REVERSION: "RSI Mean Reversion Strategy with Trend Filter",
            StrategyType.MOMENTUM: "SMA Momentum Strategy (Golden/Death Cross)",
        }

        return {
            strategy_type.value: {
                "description": descriptions[strategy_type],
                "strategy_path": mapping["strategy_path"],
                "config_path": mapping["config_path"],
            }
            for strategy_type, mapping in cls.STRATEGY_MAPPINGS.items()
        }

    @classmethod
    def create_template(cls, strategy_type_str: str) -> Dict[str, Any]:
        """
        Create a YAML template for a strategy type.

        Parameters
        ----------
        strategy_type_str : str
            String representation of strategy type

        Returns
        -------
        Dict[str, Any]
            Template configuration dictionary

        Raises
        ------
        ValueError
            If strategy type is invalid
        """
        try:
            strategy_type = StrategyType(strategy_type_str)
        except ValueError:
            raise ValueError(f"Invalid strategy type: {strategy_type_str}")

        if strategy_type not in cls.STRATEGY_MAPPINGS:
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        mapping = cls.STRATEGY_MAPPINGS[strategy_type]

        # Default template configurations for each strategy type
        default_configs = {
            StrategyType.SMA_CROSSOVER: {
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
                "fast_period": 10,
                "slow_period": 20,
                "trade_size": 1000000,
            },
            StrategyType.MEAN_REVERSION: {
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
                "trade_size": 1000000,
                "order_id_tag": "001",
                "rsi_period": 2,
                "rsi_buy_threshold": 10.0,
                "exit_rsi": 50.0,
                "sma_trend_period": 200,
                "warmup_days": 400,
                "cooldown_bars": 0,
            },
            StrategyType.MOMENTUM: {
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
                "trade_size": 1000000,
                "order_id_tag": "002",
                "fast_period": 20,
                "slow_period": 50,
                "warmup_days": 1,
                "allow_short": False,
            },
        }

        return {
            "strategy_path": mapping["strategy_path"],
            "config_path": mapping["config_path"],
            "config": default_configs[strategy_type],
        }
