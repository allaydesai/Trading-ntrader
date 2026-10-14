"""
Pydantic model for strategy configuration snapshots.

This module defines the validation model for JSONB-stored strategy configurations.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer


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
        bar_type: Bar-type spec string (e.g. "1-DAY-LAST"); persisted so the
            detail-page "Back to Explorer" fallback can recover the explorer
            timeframe label. Nullable for back-compat with rows written before
            the field was added.
        metrics_basis: What drawdown, Sharpe, Sortino and volatility were computed
            from: ``"mark_to_market"`` (daily equity at bar closes) or ``"realised"``
            (closed-position returns). Nullable: rows before the field are realised.
        fill_seed: Seed of the fill model's random draws. Nullable: rows before the
            field ran unseeded and cannot be reproduced exactly.

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
    version: str = Field(default="1.0", description="Schema version for future compatibility")
    config: Dict[str, Any] = Field(default_factory=dict, description="Strategy-specific parameters")
    bar_type: str | None = Field(
        default=None, description="Bar-type spec (e.g. '1-DAY-LAST') for explorer round-trip"
    )
    session_conditions: dict[str, Any] | None = Field(
        default=None,
        description="A sealed paper session's execution conditions (Epic 5, AR7); "
        "absent on a backtest",
    )

    @model_serializer(mode="wrap")
    def _omit_empty_session_conditions(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Drop ``session_conditions`` when it is empty (Story 5.2, PO ruling R2a).

        A backtest never carries it, so every backtest snapshot dumps exactly the
        keys it always has — byte-identical rows, no ``null`` row in the config
        panel, which renders every top-level key. A sealed session's survives.

        ``equity_curve`` is deliberately **not** a field: unknown keys are still
        ignored, so a backtest's curve is dropped here exactly as it always has
        been (Story 5.2 F3). Keeping it would change every new backtest row and
        its chart — a both-sides decision routed in deferred-work, not this one.
        """
        data = handler(self)
        if not self.session_conditions:
            data.pop("session_conditions", None)
        if self.cost_multiplier is None:
            data.pop("cost_multiplier", None)
        return data

    cost_multiplier: float | None = Field(
        default=None,
        description="Commission scale factor of a cost-stress run (research MCP phase 4). "
        "None on every run at the broker's schedule, and omitted from the dump then, so "
        "ordinary rows stay byte-identical.",
    )

    metrics_basis: str | None = Field(
        default=None,
        description="What risk metrics were computed from: 'mark_to_market' or 'realised'. "
        "None on rows written before the field existed (realised).",
    )

    fill_seed: int | None = Field(
        default=None,
        description="Seed of the fill model's random draws. None on rows written before "
        "the field existed (unseeded, not exactly reproducible).",
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
