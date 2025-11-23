"""
Pydantic models for trade tracking and performance analytics.

This module defines validation models for trade data, equity curves,
drawdown analysis, and trade statistics.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TradeBase(BaseModel):
    """Base trade model with common fields."""

    instrument_id: str = Field(..., min_length=1, max_length=50)
    trade_id: str
    venue_order_id: str
    client_order_id: Optional[str] = None

    order_side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: Decimal = Field(..., gt=0, decimal_places=8)
    entry_price: Decimal = Field(..., gt=0, decimal_places=8)
    exit_price: Optional[Decimal] = Field(None, gt=0, decimal_places=8)

    commission_amount: Optional[Decimal] = Field(None, decimal_places=8)
    commission_currency: Optional[str] = Field(None, max_length=10)
    fees_amount: Optional[Decimal] = Field(default=Decimal("0.00"), decimal_places=8)

    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None

    @field_validator("entry_price", "exit_price")
    @classmethod
    def validate_prices_positive(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Validate that prices are positive."""
        if v is not None and v <= 0:
            raise ValueError("Prices must be positive")
        return v

    model_config = {"json_encoders": {Decimal: str, datetime: lambda v: v.isoformat()}}


class TradeCreate(TradeBase):
    """Model for creating new trades from Nautilus Trader FillReports."""

    backtest_run_id: int


class Trade(TradeBase):
    """Complete trade model including computed fields."""

    id: int
    backtest_run_id: int

    profit_loss: Optional[Decimal] = None
    profit_pct: Optional[Decimal] = None
    holding_period_seconds: Optional[int] = None

    created_at: datetime

    model_config = {"from_attributes": True}


class TradeListResponse(BaseModel):
    """Paginated trade list response."""

    trades: list[Trade]
    total_count: int
    page: int
    page_size: int
    total_pages: int


class EquityCurvePoint(BaseModel):
    """
    Single point on equity curve showing account balance at a timestamp.

    Generated from cumulative trade P&L, not stored in database.
    """

    timestamp: datetime
    balance: Decimal = Field(..., decimal_places=2)
    cumulative_return_pct: Decimal = Field(..., decimal_places=4)
    trade_number: Optional[int] = None

    model_config = {"json_encoders": {Decimal: str, datetime: lambda v: v.isoformat()}}


class EquityCurveResponse(BaseModel):
    """API response for equity curve data."""

    points: list[EquityCurvePoint]
    initial_capital: Decimal
    final_balance: Decimal
    total_return_pct: Decimal


class DrawdownPeriod(BaseModel):
    """Single drawdown period from peak to trough."""

    peak_timestamp: datetime
    peak_balance: Decimal
    trough_timestamp: datetime
    trough_balance: Decimal
    drawdown_amount: Decimal
    drawdown_pct: Decimal = Field(..., decimal_places=4)
    duration_days: int
    recovery_timestamp: Optional[datetime] = None
    recovered: bool = False


class DrawdownMetrics(BaseModel):
    """Maximum drawdown analysis."""

    max_drawdown: Optional[DrawdownPeriod] = None
    top_drawdowns: list[DrawdownPeriod] = Field(default_factory=list, max_length=5)
    current_drawdown: Optional[DrawdownPeriod] = None
    total_drawdown_periods: int


class TradeStatistics(BaseModel):
    """Aggregate trade performance statistics."""

    # Trade counts
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int = 0

    # Win rate
    win_rate: Decimal = Field(..., ge=0, le=100, decimal_places=2)

    # Profit metrics
    total_profit: Decimal
    total_loss: Decimal
    net_profit: Decimal
    average_win: Decimal
    average_loss: Decimal
    largest_win: Decimal
    largest_loss: Decimal

    # Risk metrics
    profit_factor: Optional[Decimal] = None  # total_profit / abs(total_loss)
    expectancy: Decimal  # average profit per trade

    # Streaks
    max_consecutive_wins: int
    max_consecutive_losses: int

    # Holding periods
    avg_holding_period_hours: Decimal
    max_holding_period_hours: int
    min_holding_period_hours: int

    # Monthly breakdown (optional)
    monthly_breakdown: Optional[dict[str, Decimal]] = None


def calculate_trade_metrics(trade: TradeCreate) -> dict:
    """
    Calculate derived fields for a trade.

    Args:
        trade: TradeCreate model with entry/exit details

    Returns:
        Dictionary with profit_loss, profit_pct, holding_period_seconds

    Example:
        >>> from datetime import timezone
        >>> trade = TradeCreate(
        ...     backtest_run_id=1,
        ...     instrument_id="AAPL",
        ...     trade_id="trade-123",
        ...     venue_order_id="order-456",
        ...     order_side="BUY",
        ...     quantity=Decimal("100"),
        ...     entry_price=Decimal("150.00"),
        ...     exit_price=Decimal("160.00"),
        ...     commission_amount=Decimal("5.00"),
        ...     entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        ...     exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        ... )
        >>> metrics = calculate_trade_metrics(trade)
        >>> metrics["profit_loss"]
        Decimal('995.00')
    """
    if trade.exit_price is None:
        # Trade still open
        return {
            "profit_loss": None,
            "profit_pct": None,
            "holding_period_seconds": None,
        }

    # Calculate gross P&L (before costs)
    if trade.order_side == "BUY":
        # Long position: profit when exit > entry
        gross_pnl = (trade.exit_price - trade.entry_price) * trade.quantity
    else:  # SELL
        # Short position: profit when entry > exit
        gross_pnl = (trade.entry_price - trade.exit_price) * trade.quantity

    # Subtract costs
    total_costs = (trade.commission_amount or Decimal("0")) + (
        trade.fees_amount or Decimal("0")
    )
    net_pnl = gross_pnl - total_costs

    # Calculate percentage return
    entry_value = trade.entry_price * trade.quantity
    pnl_pct = (net_pnl / entry_value) * 100 if entry_value > 0 else Decimal("0")

    # Calculate holding period
    holding_period = None
    if trade.exit_timestamp and trade.entry_timestamp:
        delta = trade.exit_timestamp - trade.entry_timestamp
        holding_period = int(delta.total_seconds())

    return {
        "profit_loss": net_pnl,
        "profit_pct": pnl_pct,
        "holding_period_seconds": holding_period,
    }
