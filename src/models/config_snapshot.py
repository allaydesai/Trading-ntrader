"""
Pydantic model for strategy configuration snapshots.

This module defines the validation model for JSONB-stored strategy configurations.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field


class StrategyConfigSnapshot(BaseModel):
    """
    Validation model for strategy configuration snapshots.

    This model validates the structure of configuration data before it is
    stored as JSONB in the database, ensuring reproducibility of backtests.

    Attributes:
        strategy_path: Fully qualified Python path to strategy class
        config_path: Relative path to YAML configuration file
        version: Schema version for future compatibility
        config: Strategy-specific parameters as key-value pairs

    Example:
        >>> snapshot = StrategyConfigSnapshot(
        ...     strategy_path="src.strategies.sma_crossover.SMAStrategyConfig",
        ...     config_path="config/strategies/sma_crossover.yaml",
        ...     version="1.0",
        ...     config={"fast_period": 10, "slow_period": 50}
        ... )
        >>> snapshot.strategy_path
        'src.strategies.sma_crossover.SMAStrategyConfig'
    """

    strategy_path: str = Field(
        ..., description="Fully qualified Python path to strategy class", min_length=1
    )
    config_path: str = Field(
        ..., description="Relative path to YAML configuration file", min_length=1
    )
    version: str = Field(
        default="1.0", description="Schema version for future compatibility"
    )
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Strategy-specific parameters"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
                    "config_path": "config/strategies/sma_crossover.yaml",
                    "version": "1.0",
                    "config": {
                        "fast_period": 10,
                        "slow_period": 50,
                        "risk_percent": 2.0,
                    },
                }
            ]
        }
    }
