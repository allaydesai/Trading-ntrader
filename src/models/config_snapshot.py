"""
Pydantic model for strategy configuration snapshots.

This module defines the validation model for JSONB-stored strategy configurations.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

#: Optional keys dropped from the dump while None, so rows without them stay byte-identical.
_OMITTED_WHEN_UNSET = (
    "cost_multiplier",
    "fill_model",
    "futures_contracts",
    "profit_pct_basis",
    "account_currency",
    "fx_pairs",
    "fee_tier",
    "crypto_pairs",
    "periods_per_year",
    "crypto_peaks",
)


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
        for optional in _OMITTED_WHEN_UNSET:
            if getattr(self, optional) is None:
                data.pop(optional, None)
        return data

    cost_multiplier: float | None = Field(
        default=None,
        description="Commission scale factor of a cost-stress run (research MCP phase 4). "
        "None on every run at the broker's schedule, and omitted from the dump then, so "
        "ordinary rows stay byte-identical.",
    )

    fill_model: str | None = Field(
        default=None,
        description="Fill model of a run that asked for a non-default one ('gap_aware'). None "
        "on every default run, and omitted from the dump then, so ordinary rows stay "
        "byte-identical.",
    )

    futures_contracts: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="Futures runs only (M3): per instrument id, the multiplier, dollar "
        "initial/maintenance margin, per-contract commission and margin as-of the run used. "
        "Omitted from the dump otherwise, so equity rows stay byte-identical.",
    )
    profit_pct_basis: str | None = Field(
        default=None,
        description="Futures runs only: what trades.profit_pct is a return on ('underlying' "
        "= the price move, not margin). Omitted from the dump otherwise.",
    )
    account_currency: str | None = Field(
        default=None,
        description="FX runs only (M3): the single currency the venue account settles in "
        "('USD'); every trade's profit_loss is in it. Omitted from the dump otherwise.",
    )
    fx_pairs: dict[str, dict[str, Any]] | None = Field(
        default=None,
        description="FX runs only: per instrument id, base, quote, pip size, margin %, "
        "commission terms, spread, as-of and conversion pair the run used (filled from M4). "
        "Omitted from the dump otherwise, so equity and futures rows stay byte-identical.",
    )

    # Crypto runs only (M3), each omitted while None so other rows stay byte-identical: the one
    # Kraken Pro fee tier of the run (C21); per pair the tier rates, spread, precision, as-of and
    # USD charged (maker/taker commission, spread); the 365 days risk metrics annualised on (C20).
    fee_tier: int | None = Field(default=None, description="Crypto: Kraken Pro spot fee tier")
    crypto_pairs: dict[str, dict[str, Any]] | None = Field(
        default=None, description="Crypto: per pair, the cost terms used and USD charged"
    )
    periods_per_year: int | None = Field(
        default=None, description="Crypto: days a year the risk metrics annualise on (365)"
    )
    crypto_peaks: dict[str, Any] | None = Field(
        default=None,
        description="Crypto: the exact peak notional and exposure replayed from the run's fills "
        "('basis': 'exact'); omitted when they could not be replayed (upper bound)",
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
