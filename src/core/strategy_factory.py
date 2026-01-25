"""Strategy factory for dynamic loading of trading strategies."""

import importlib
from decimal import Decimal
from typing import Any, Dict, List, Type

from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from pydantic import ValidationError

from src.core.strategy_registry import StrategyRegistry


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
            raise AttributeError(f"Class '{class_name}' not found in module '{module_path}': {e}")

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
            raise AttributeError(f"Class '{class_name}' not found in module '{module_path}': {e}")

        if not issubclass(config_class, StrategyConfig):
            raise TypeError(
                f"Class '{class_name}' must inherit from "
                f"nautilus_trader.trading.strategy.StrategyConfig"
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
    def get_strategy_name_from_path(strategy_path: str) -> str:
        """
        Determine strategy name from strategy path using registry lookup.

        Parameters
        ----------
        strategy_path : str
            Module path to strategy class

        Returns
        -------
        str
            The canonical strategy name

        Raises
        ------
        ValueError
            If strategy type cannot be determined
        """
        # Extract strategy class name from path
        if ":" in strategy_path:
            class_name = strategy_path.split(":")[-1].lower()
        else:
            class_name = strategy_path.split(".")[-1].lower()

        # Try to find matching strategy in registry
        StrategyRegistry.discover()
        for name, defn in StrategyRegistry.get_all().items():
            # Check if class name matches
            if defn.strategy_class.__name__.lower() == class_name:
                return name
            # Check aliases
            for alias in defn.aliases:
                if alias.lower() == class_name or alias.replace("_", "").lower() == class_name:
                    return name

        raise ValueError(f"Cannot determine strategy from path: {strategy_path}")

    @staticmethod
    def validate_strategy_config(strategy_type: str, config_params: Dict[str, Any]) -> bool:
        """
        Validate configuration parameters for a specific strategy type.

        Parameters
        ----------
        strategy_type : str
            The strategy type name to validate against
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
        ValueError
            If strategy type is unknown
        """
        # Get param model from registry
        StrategyRegistry.discover()
        try:
            definition = StrategyRegistry.get(strategy_type)
            if definition.param_model is None:
                # No param model defined, skip validation
                return True

            # Validate parameters
            definition.param_model(**config_params)
            return True
        except KeyError:
            raise ValueError(f"Unknown strategy type: {strategy_type}")
        except ValidationError:
            # Re-raise the original ValidationError
            raise


class StrategyLoader:
    """High-level strategy loader using auto-discovery registry."""

    @classmethod
    def _get_mapping(cls, strategy_name: str) -> Dict[str, Any]:
        """
        Get strategy mapping from registry.

        Parameters
        ----------
        strategy_name : str
            Strategy name (canonical or alias)

        Returns
        -------
        Dict[str, Any]
            Dictionary with strategy_path, config_path, param_model
        """
        defn = StrategyRegistry.get(strategy_name)
        return {
            "strategy_path": defn.strategy_path,
            "config_path": defn.config_path,
            "param_model": defn.param_model,
        }

    @classmethod
    def _resolve_strategy_name(cls, strategy_type: str) -> str:
        """Resolve a strategy type string to canonical strategy name."""
        return str(strategy_type)

    @classmethod
    def create_strategy(cls, strategy_type: str, config_params: Dict[str, Any]) -> Strategy:
        """
        Create a strategy instance using registry mappings.

        Parameters
        ----------
        strategy_type : str
            The strategy type name (e.g., 'sma_crossover', 'momentum')
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
        strategy_name = cls._resolve_strategy_name(strategy_type)

        if not StrategyRegistry.exists(strategy_name):
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        mapping = cls._get_mapping(strategy_name)

        return StrategyFactory.create_strategy_from_config(
            strategy_path=mapping["strategy_path"],
            config_path=mapping["config_path"],
            config_params=config_params,
        )

    @classmethod
    def build_strategy_params(
        cls, strategy_type: str, overrides: Dict[str, Any], settings: Any
    ) -> Dict[str, Any]:
        """
        Build strategy parameters dynamically using Pydantic introspection.

        Resolves parameters in the following order:
        1. Explicit overrides (if provided)
        2. Global settings (via _settings_map metadata)
        3. Model defaults

        Parameters
        ----------
        strategy_type : str
            The strategy type name to build params for
        overrides : Dict[str, Any]
            Dictionary of explicit parameter overrides
        settings : Any
            Global settings object to pull defaults from

        Returns
        -------
        Dict[str, Any]
            Validated and fully resolved configuration dictionary

        Raises
        ------
        ValueError
            If strategy type is unknown or validation fails
        """
        strategy_name = cls._resolve_strategy_name(strategy_type)

        if not StrategyRegistry.exists(strategy_name):
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        # Get the Pydantic model for this strategy from registry
        mapping = cls._get_mapping(strategy_name)
        param_model_cls = mapping["param_model"]

        # Get the settings map if defined
        settings_map = getattr(param_model_cls, "_settings_map", {})
        if not isinstance(settings_map, dict):
            settings_map = settings_map.default if hasattr(settings_map, "default") else {}

        final_params = {}

        # Iterate over all fields in the model
        for field_name, field_info in param_model_cls.model_fields.items():
            # 1. Check for override
            if field_name in overrides and overrides[field_name] is not None:
                val = overrides[field_name]
                # Handle Decimal conversion for string inputs (common in CLI/Web)
                if field_info.annotation == Decimal and isinstance(val, (str, int, float)):
                    final_params[field_name] = Decimal(str(val))
                else:
                    final_params[field_name] = val
                continue

            # 2. Check for global setting mapping
            # _settings_map is a simple dict, check if key exists directly
            if field_name in settings_map:
                setting_key = settings_map[field_name]
                if hasattr(settings, setting_key):
                    setting_val = getattr(settings, setting_key)
                    # Convert Decimal if needed (though settings are usually typed)
                    if field_info.annotation == Decimal and not isinstance(setting_val, Decimal):
                        final_params[field_name] = Decimal(str(setting_val))
                    else:
                        final_params[field_name] = setting_val
                    continue

            # 3. Fallback to Pydantic default (implicit via model instantiation)
            # We don't need to do anything here; the model constructor will use default
            pass

        try:
            # Create model instance to trigger validation and defaults
            model_instance = param_model_cls(**final_params)

            # Return as dict for Nautilus
            return model_instance.model_dump()

        except ValidationError as e:
            raise ValueError(f"Configuration validation failed for {strategy_type}: {e}")

    @classmethod
    def get_available_strategies(cls) -> Dict[str, Dict[str, str | None]]:
        """
        Get all available strategy types and their mappings.

        Returns
        -------
        Dict[str, Dict[str, str | None]]
            Dictionary mapping strategy names to their paths
        """
        result = {}
        for name, defn in StrategyRegistry.get_all().items():
            result[name] = {
                "strategy_path": defn.strategy_path,
                "config_path": defn.config_path,
            }
        return result

    @classmethod
    def validate_strategy_type(cls, strategy_type: str) -> bool:
        """
        Check if a strategy type is supported.

        Parameters
        ----------
        strategy_type : str
            The strategy type name to check

        Returns
        -------
        bool
            True if strategy type is supported
        """
        strategy_name = cls._resolve_strategy_name(strategy_type)
        return StrategyRegistry.exists(strategy_name)

    @classmethod
    def get_strategy_names(cls) -> List[str]:
        """
        Get list of all available strategy names.

        Returns
        -------
        List[str]
            List of strategy names
        """
        return StrategyRegistry.get_names()

    @classmethod
    def list_available(cls) -> Dict[str, Dict[str, str | None]]:
        """
        List available strategies with descriptions for CLI.

        Returns
        -------
        Dict[str, Dict[str, str | None]]
            Dictionary mapping strategy names to info
        """
        return {
            defn.name: {
                "description": defn.description,
                "strategy_path": defn.strategy_path,
                "config_path": defn.config_path,
            }
            for defn in StrategyRegistry.get_all().values()
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
        if not StrategyRegistry.exists(strategy_type_str):
            raise ValueError(
                f"Invalid strategy type: {strategy_type_str}. "
                f"Available: {StrategyRegistry.get_names()}"
            )

        defn = StrategyRegistry.get(strategy_type_str)

        return {
            "strategy_path": defn.strategy_path,
            "config_path": defn.config_path,
            "config": defn.default_config,
        }
