"""Strategy registry for auto-discovery of trading strategies.

This module provides a decorator-based registration system that allows strategies
to self-register when their modules are imported. This eliminates the need to
manually update multiple files when adding a new strategy.

Usage:
    from src.core.strategy_registry import register_strategy, StrategyRegistry

    @register_strategy(
        name="my_strategy",
        description="My custom trading strategy",
    )
    class MyStrategy(Strategy):
        ...

    class MyStrategyConfig(StrategyConfig):
        ...

    class MyParameters(BaseModel):
        ...

    # Register config and params separately (after class definitions)
    StrategyRegistry.set_config("my_strategy", MyStrategyConfig)
    StrategyRegistry.set_param_model("my_strategy", MyParameters)
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from pydantic import BaseModel


@dataclass
class StrategyDefinition:
    """Complete definition of a registered strategy."""

    name: str
    description: str
    strategy_class: Type[Strategy]
    config_class: Optional[Type[StrategyConfig]] = None
    param_model: Optional[Type[BaseModel]] = None
    default_config: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)

    @property
    def strategy_path(self) -> str:
        """Get the import path for the strategy class."""
        module = self.strategy_class.__module__
        class_name = self.strategy_class.__name__
        return f"{module}:{class_name}"

    @property
    def config_path(self) -> Optional[str]:
        """Get the import path for the config class."""
        if self.config_class is None:
            return None
        module = self.config_class.__module__
        class_name = self.config_class.__name__
        return f"{module}:{class_name}"


class StrategyRegistry:
    """
    Central registry for all trading strategies.

    This class maintains a registry of all available strategies and provides
    methods for registration, lookup, and discovery.
    """

    _strategies: Dict[str, StrategyDefinition] = {}
    _aliases: Dict[str, str] = {}  # Maps alias -> canonical name
    _discovered: bool = False

    @classmethod
    def register(
        cls,
        name: str,
        strategy_class: Type[Strategy],
        description: str = "",
        config_class: Optional[Type[StrategyConfig]] = None,
        param_model: Optional[Type[BaseModel]] = None,
        default_config: Optional[Dict[str, Any]] = None,
        aliases: Optional[List[str]] = None,
    ) -> None:
        """
        Register a strategy in the registry.

        Parameters
        ----------
        name : str
            Canonical name for the strategy (e.g., "sma_crossover")
        strategy_class : Type[Strategy]
            The strategy class
        description : str
            Human-readable description
        config_class : Optional[Type[StrategyConfig]]
            The configuration class for this strategy
        param_model : Optional[Type[BaseModel]]
            Pydantic model for validating parameters
        default_config : Optional[Dict[str, Any]]
            Default configuration template
        aliases : Optional[List[str]]
            Alternative names that resolve to this strategy
        """
        definition = StrategyDefinition(
            name=name,
            description=description,
            strategy_class=strategy_class,
            config_class=config_class,
            param_model=param_model,
            default_config=default_config or {},
            aliases=aliases or [],
        )

        cls._strategies[name] = definition

        # Register aliases
        for alias in definition.aliases:
            cls._aliases[alias.lower()] = name
        # Also register the canonical name as an alias (for consistency)
        cls._aliases[name.lower()] = name
        # Register without underscores as alias
        cls._aliases[name.replace("_", "").lower()] = name

    @classmethod
    def set_config(cls, name: str, config_class: Type[StrategyConfig]) -> None:
        """Set the config class for a registered strategy."""
        if name not in cls._strategies:
            raise KeyError(f"Strategy '{name}' not registered")
        cls._strategies[name].config_class = config_class

    @classmethod
    def set_param_model(cls, name: str, param_model: Type[BaseModel]) -> None:
        """Set the parameter model for a registered strategy."""
        if name not in cls._strategies:
            raise KeyError(f"Strategy '{name}' not registered")
        cls._strategies[name].param_model = param_model

    @classmethod
    def set_default_config(cls, name: str, default_config: Dict[str, Any]) -> None:
        """Set the default configuration for a registered strategy."""
        if name not in cls._strategies:
            raise KeyError(f"Strategy '{name}' not registered")
        cls._strategies[name].default_config = default_config

    @classmethod
    def get(cls, name: str) -> StrategyDefinition:
        """
        Get a strategy definition by name or alias.

        Parameters
        ----------
        name : str
            Strategy name or alias

        Returns
        -------
        StrategyDefinition
            The strategy definition

        Raises
        ------
        KeyError
            If strategy is not found
        """
        cls._ensure_discovered()

        # Try direct lookup first
        if name in cls._strategies:
            return cls._strategies[name]

        # Try alias lookup
        canonical = cls._aliases.get(name.lower())
        if canonical and canonical in cls._strategies:
            return cls._strategies[canonical]

        # Try fuzzy matching (without underscores)
        normalized = name.replace("_", "").replace("-", "").lower()
        canonical = cls._aliases.get(normalized)
        if canonical and canonical in cls._strategies:
            return cls._strategies[canonical]

        raise KeyError(f"Strategy '{name}' not found. Available: {list(cls._strategies.keys())}")

    @classmethod
    def get_all(cls) -> Dict[str, StrategyDefinition]:
        """Get all registered strategies."""
        cls._ensure_discovered()
        return cls._strategies.copy()

    @classmethod
    def get_names(cls) -> List[str]:
        """Get all registered strategy names."""
        cls._ensure_discovered()
        return list(cls._strategies.keys())

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if a strategy exists by name or alias."""
        cls._ensure_discovered()
        if name in cls._strategies:
            return True
        canonical = cls._aliases.get(name.lower())
        return canonical is not None and canonical in cls._strategies

    @classmethod
    def list_strategies(cls) -> List[Dict[str, Any]]:
        """
        List all strategies with their metadata.

        Returns
        -------
        List[Dict[str, Any]]
            List of strategy info dictionaries
        """
        cls._ensure_discovered()
        return [
            {
                "name": defn.name,
                "description": defn.description,
                "strategy_path": defn.strategy_path,
                "config_path": defn.config_path,
                "aliases": defn.aliases,
            }
            for defn in cls._strategies.values()
        ]

    @classmethod
    def discover(cls, force: bool = False) -> int:
        """
        Discover and import all strategy modules.

        This method scans the strategies directory and imports all Python
        modules, which triggers their @register_strategy decorators.

        Also scans the custom/ subdirectory for external strategies
        (e.g., from git submodules).

        Parameters
        ----------
        force : bool
            Force re-discovery even if already done

        Returns
        -------
        int
            Number of strategies discovered
        """
        if cls._discovered and not force:
            return len(cls._strategies)

        import importlib
        import importlib.util
        import warnings

        # Get the strategies directory
        strategies_dir = Path(__file__).parent / "strategies"

        if not strategies_dir.exists():
            return 0

        # Import all Python files in the strategies directory
        for py_file in strategies_dir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue

            module_name = f"src.core.strategies.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                # Log but don't fail - some modules might have optional dependencies
                warnings.warn(f"Could not import strategy module {module_name}: {e}")

        # Also scan custom/ subdirectory for external strategies
        custom_dir = strategies_dir / "custom"
        if custom_dir.exists():
            for py_file in custom_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue

                module_name = f"src.core.strategies.custom.{py_file.stem}"
                try:
                    importlib.import_module(module_name)
                except ImportError as e:
                    warnings.warn(f"Could not import custom strategy {module_name}: {e}")

        cls._discovered = True
        return len(cls._strategies)

    @classmethod
    def _ensure_discovered(cls) -> None:
        """Ensure strategies have been discovered."""
        if not cls._discovered:
            cls.discover()

    @classmethod
    def clear(cls) -> None:
        """Clear all registered strategies (useful for testing)."""
        cls._strategies.clear()
        cls._aliases.clear()
        cls._discovered = False


# Type variable for the decorator
T = TypeVar("T", bound=Type[Strategy])


def register_strategy(
    name: str,
    description: str = "",
    config_class: Optional[Type[StrategyConfig]] = None,
    param_model: Optional[Type[BaseModel]] = None,
    default_config: Optional[Dict[str, Any]] = None,
    aliases: Optional[List[str]] = None,
) -> Callable[[T], T]:
    """
    Decorator to register a strategy class.

    Usage:
        @register_strategy(
            name="sma_crossover",
            description="Simple Moving Average Crossover Strategy",
            aliases=["sma", "smacrossover"],
        )
        class SMACrossover(Strategy):
            ...

    Parameters
    ----------
    name : str
        Canonical name for the strategy
    description : str
        Human-readable description
    config_class : Optional[Type[StrategyConfig]]
        Config class (can also be set via StrategyRegistry.set_config)
    param_model : Optional[Type[BaseModel]]
        Parameter validation model
    default_config : Optional[Dict[str, Any]]
        Default configuration template
    aliases : Optional[List[str]]
        Alternative names for this strategy

    Returns
    -------
    Callable
        Decorator function
    """

    def decorator(cls: T) -> T:
        StrategyRegistry.register(
            name=name,
            strategy_class=cls,
            description=description,
            config_class=config_class,
            param_model=param_model,
            default_config=default_config,
            aliases=aliases,
        )
        return cls

    return decorator
