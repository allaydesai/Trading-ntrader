"""
Models for the backtest run form page.

Provides Pydantic models for form data validation and strategy dropdown options.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

VALID_DATA_SOURCES = {"catalog", "ibkr", "kraken", "mock"}
VALID_TIMEFRAMES = (
    "1-MINUTE",
    "5-MINUTE",
    "15-MINUTE",
    "1-HOUR",
    "4-HOUR",
    "1-DAY",
    "1-WEEK",
)


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


class StrategyParamField(BaseModel):
    """Represents a single strategy parameter for dynamic form rendering."""

    name: str
    field_type: str  # "integer", "number", "string", "boolean"
    default: Any = None
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    required: bool = False


def _extract_type_and_constraints(
    prop: dict[str, Any],
) -> tuple[str, float | None, float | None]:
    """Extract field_type, minimum, maximum from a JSON schema property."""
    minimum = None
    maximum = None

    def _first_not_none(*values: Any) -> float | None:
        for v in values:
            if v is not None:
                return v
        return None

    # Handle anyOf pattern (Pydantic Decimal fields)
    if "anyOf" in prop:
        for variant in prop["anyOf"]:
            if variant.get("type") == "number":
                minimum = _first_not_none(variant.get("minimum"), variant.get("exclusiveMinimum"))
                maximum = _first_not_none(variant.get("maximum"), variant.get("exclusiveMaximum"))
                return "number", minimum, maximum
        return "string", None, None

    field_type = prop.get("type", "string")
    minimum = _first_not_none(prop.get("minimum"), prop.get("exclusiveMinimum"))
    maximum = _first_not_none(prop.get("maximum"), prop.get("exclusiveMaximum"))
    return field_type, minimum, maximum


def schema_to_fields(param_model: type[BaseModel]) -> list[StrategyParamField]:
    """Convert a Pydantic model's JSON schema to a list of StrategyParamField."""
    schema = param_model.model_json_schema()
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    fields: list[StrategyParamField] = []
    for name, prop in properties.items():
        field_type, minimum, maximum = _extract_type_and_constraints(prop)
        fields.append(
            StrategyParamField(
                name=name,
                field_type=field_type,
                default=prop.get("default"),
                description=prop.get("description", prop.get("title", "")),
                minimum=minimum,
                maximum=maximum,
                required=name in required_fields,
            )
        )
    return fields
