"""
Pydantic models for Equity API.

Defines request/response models for equity curve and drawdown data
in TradingView-compatible format.
"""

from uuid import UUID

from pydantic import BaseModel, Field


class EquityPoint(BaseModel):
    """
    Single equity curve point.

    Attributes:
        time: ISO 8601 date format
        value: Portfolio value

    Example:
        >>> point = EquityPoint(time="2024-01-15", value=100500.0)
    """

    time: str = Field(..., description="ISO 8601 date format")
    value: float = Field(..., description="Portfolio value")


class DrawdownPoint(BaseModel):
    """
    Single drawdown point.

    Attributes:
        time: ISO 8601 date format
        value: Percentage from peak (negative number)

    Example:
        >>> point = DrawdownPoint(time="2024-01-15", value=-5.2)
    """

    time: str = Field(..., description="ISO 8601 date format")
    value: float = Field(..., le=0, description="Drawdown percentage (negative)")


class EquityResponse(BaseModel):
    """
    Equity curve and drawdown response.

    Attributes:
        run_id: Backtest run UUID
        equity: List of equity curve points sorted by time ascending
        drawdown: List of drawdown points sorted by time ascending

    Example:
        >>> response = EquityResponse(
        ...     run_id=uuid4(),
        ...     equity=[equity1, equity2],
        ...     drawdown=[drawdown1, drawdown2]
        ... )
    """

    run_id: UUID = Field(..., description="Backtest run ID")
    equity: list[EquityPoint] = Field(
        default_factory=list, description="Equity curve points"
    )
    drawdown: list[DrawdownPoint] = Field(
        default_factory=list, description="Drawdown points"
    )
