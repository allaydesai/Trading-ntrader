"""
YAML Configuration Loader for Strategy Management.

This module provides functionality to load and validate YAML strategy configurations,
following the Nautilus Trader ImportableStrategyConfig pattern.
"""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Type

import yaml
from nautilus_trader.trading.strategy import StrategyConfig


@dataclass
class StrategyConfigWrapper:
    """Wrapper for strategy configuration from YAML."""

    strategy_path: str
    config_path: str
    config: StrategyConfig


class ConfigLoader:
    """Load and validate YAML strategy configurations."""

    @classmethod
    def load_from_file(cls, yaml_file: str) -> StrategyConfigWrapper:
        """
        Load configuration from YAML file.

        Args:
            yaml_file: Path to YAML configuration file

        Returns:
            StrategyConfigWrapper with loaded configuration

        Raises:
            FileNotFoundError: If YAML file doesn't exist
            yaml.YAMLError: If YAML syntax is invalid
            ValueError: If configuration is invalid
        """
        if not Path(yaml_file).exists():
            raise FileNotFoundError(f"Configuration file not found: {yaml_file}")

        with open(yaml_file, "r") as f:
            yaml_content = f.read()

        return cls.load_from_yaml(yaml_content)

    @classmethod
    def load_from_yaml(cls, yaml_content: str) -> StrategyConfigWrapper:
        """
        Load configuration from YAML string.

        Args:
            yaml_content: YAML content as string

        Returns:
            StrategyConfigWrapper with loaded configuration

        Raises:
            yaml.YAMLError: If YAML syntax is invalid
            ValueError: If configuration is invalid
        """
        try:
            yaml_data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"Invalid YAML syntax: {e}")

        if not isinstance(yaml_data, dict):
            raise ValueError("YAML content must be a dictionary")

        cls._validate_yaml_structure(yaml_data)
        return cls._create_config_object(yaml_data)

    @classmethod
    def _validate_yaml_structure(cls, yaml_data: Dict[str, Any]) -> None:
        """
        Validate YAML structure has required fields.

        Args:
            yaml_data: Parsed YAML data

        Raises:
            ValueError: If required fields are missing
        """
        required_fields = ["strategy_path", "config_path", "config"]

        for field in required_fields:
            if field not in yaml_data:
                raise ValueError(f"{field} is required in YAML configuration")

        if not isinstance(yaml_data["config"], dict):
            raise ValueError("config section must be a dictionary")

    @classmethod
    def _create_config_object(cls, yaml_data: Dict[str, Any]) -> StrategyConfigWrapper:
        """
        Create strategy config object from YAML data.

        Args:
            yaml_data: Parsed and validated YAML data

        Returns:
            StrategyConfigWrapper with instantiated configuration

        Raises:
            ImportError: If strategy or config classes can't be imported
            ValueError: If configuration parameters are invalid
        """
        strategy_path = yaml_data["strategy_path"]
        config_path = yaml_data["config_path"]
        config_params = yaml_data["config"]

        # Load the config class dynamically
        config_class = cls._load_config_class(config_path)

        # Handle instrument_id string conversion to InstrumentId
        if "instrument_id" in config_params and isinstance(config_params["instrument_id"], str):
            from nautilus_trader.model.identifiers import InstrumentId

            config_params["instrument_id"] = InstrumentId.from_str(config_params["instrument_id"])

        # Handle bar_type string conversion to BarType
        if "bar_type" in config_params and isinstance(config_params["bar_type"], str):
            from nautilus_trader.model.data import BarType

            config_params["bar_type"] = BarType.from_str(config_params["bar_type"])

        # Handle trade_size conversion to Decimal
        if "trade_size" in config_params:
            from decimal import Decimal

            config_params["trade_size"] = Decimal(str(config_params["trade_size"]))

        try:
            # Instantiate the config with the parameters
            config_instance = config_class(**config_params)
        except Exception as e:
            raise ValueError(f"Invalid configuration parameters: {e}")

        return StrategyConfigWrapper(
            strategy_path=strategy_path, config_path=config_path, config=config_instance
        )

    @classmethod
    def _load_config_class(cls, config_path: str) -> Type[StrategyConfig]:
        """
        Dynamically load configuration class from module path.

        Args:
            config_path: Module path in format "module.path:ClassName"

        Returns:
            Configuration class

        Raises:
            ImportError: If module or class can't be imported
            ValueError: If path format is invalid
        """
        if ":" not in config_path:
            raise ValueError(
                f"Invalid config path format. Expected 'module.path:ClassName', got: {config_path}"
            )

        try:
            module_path, class_name = config_path.rsplit(":", 1)
            module = import_module(module_path)
            config_class = getattr(module, class_name)
            return config_class
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not import config class from {config_path}: {e}")
