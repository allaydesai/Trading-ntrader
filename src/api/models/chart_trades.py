"""
Pydantic models for Trades API.

Defines request/response models for trade markers
in TradingView-compatible format.
"""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class TradeSide(str, Enum):
    """Trade side enum."""

    BUY = "buy"
    SELL = "sell"


class TradeMarker(BaseModel):
    """
    Single trade marker for chart overlay.

    Attributes:
        time: ISO 8601 date format
        side: Trade side (buy/sell)
        price: Execution price
        quantity: Trade quantity
        pnl: Realized P&L for this trade

    Example:
        >>> marker = TradeMarker(
        ...     time="2024-01-15",
        ...     side="buy",
        ...     price=185.50,
        ...     quantity=100,
        ...     pnl=0.0
        ... )
    """

    time: str = Field(..., description="ISO 8601 date format")
    side: str = Field(..., description="Trade side (buy/sell)")
    price: float = Field(..., gt=0, description="Execution price")
    quantity: int = Field(..., gt=0, description="Trade quantity")
    pnl: float = Field(..., description="Realized P&L")


class TradesResponse(BaseModel):
    """
    Trade markers response for backtest run.

    Attributes:
        run_id: Backtest run UUID
        trade_count: Total number of trades
        trades: List of trade markers sorted by time ascending

    Example:
        >>> response = TradesResponse(
        ...     run_id=uuid4(),
        ...     trade_count=2,
        ...     trades=[marker1, marker2]
        ... )
    """

    run_id: UUID = Field(..., description="Backtest run ID")
    trade_count: int = Field(..., ge=0, description="Total number of trades")
    trades: list[TradeMarker] = Field(default_factory=list, description="Trade markers")
