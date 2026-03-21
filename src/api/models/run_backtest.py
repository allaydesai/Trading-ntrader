"""
Models for the backtest run form page.

Provides Pydantic models for form data validation and strategy dropdown options.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

VALID_DATA_SOURCES = {"catalog", "ibkr", "kraken", "mock"}
VALID_TIMEFRAMES = {
    "1-MINUTE",
    "5-MINUTE",
    "15-MINUTE",
    "1-HOUR",
    "4-HOUR",
    "1-DAY",
    "1-WEEK",
}


class StrategyOption(BaseModel):
    """Strategy choice for the form dropdown."""

    name: str
    description: str
    aliases: list[str] = Field(default_factory=list)


class BacktestRunFormData(BaseModel):
    """Validated form submission data for running a backtest."""

    strategy: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)
    start_date: date
    end_date: date
    data_source: str = Field(default="catalog")
    timeframe: str = Field(default="1-DAY")
    starting_balance: Decimal = Field(default=Decimal("1000000"))
    timeout_seconds: int = Field(default=300)
    strategy_params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_form(self) -> "BacktestRunFormData":
        """Validate cross-field constraints."""
        if self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        if self.data_source not in VALID_DATA_SOURCES:
            raise ValueError(
                f"Invalid data_source '{self.data_source}'. "
                f"Must be one of: {', '.join(sorted(VALID_DATA_SOURCES))}"
            )
        if self.timeframe not in VALID_TIMEFRAMES:
            raise ValueError(
                f"Invalid timeframe '{self.timeframe}'. "
                f"Must be one of: {', '.join(sorted(VALID_TIMEFRAMES))}"
            )
        if self.starting_balance <= 0:
            raise ValueError("starting_balance must be greater than 0")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return self
