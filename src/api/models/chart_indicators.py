"""
Pydantic models for Indicators API.

Defines request/response models for indicator series
in TradingView-compatible format.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class IndicatorPoint(BaseModel):
    """
    Single indicator value point.

    Attributes:
        time: ISO 8601 date format
        value: Indicator value

    Example:
        >>> point = IndicatorPoint(time="2024-01-15", value=185.5)
    """

    time: str = Field(..., description="ISO 8601 date format")
    value: float = Field(..., description="Indicator value")


class IndicatorsResponse(BaseModel):
    """
    Indicator series response for backtest run.

    Attributes:
        run_id: Backtest run UUID
        indicators: Dictionary of named indicator series

    Example:
        >>> response = IndicatorsResponse(
        ...     run_id=uuid4(),
        ...     indicators={
        ...         "sma_fast": [point1, point2],
        ...         "sma_slow": [point3, point4]
        ...     }
        ... )
    """

    run_id: UUID = Field(..., description="Backtest run ID")
    indicators: dict[str, list[IndicatorPoint]] = Field(
        default_factory=dict, description="Named indicator series"
    )
