"""Enhanced trade model extending Nautilus Position data."""

from decimal import Decimal
from datetime import datetime
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field
from nautilus_trader.model.position import Position


class TradeModel(BaseModel):
    """
    Enhanced trade model extending Nautilus Position data.

    This model provides a structured representation of trading positions
    with additional metadata for analysis and reporting.
    """

    # Core fields from Nautilus Position
    position_id: str = Field(..., description="Nautilus position ID")
    instrument_id: str = Field(..., description="Traded instrument")
    entry_time: datetime = Field(..., description="Position entry timestamp")
    entry_price: Decimal = Field(..., ge=0, description="Entry execution price")
    exit_time: Optional[datetime] = Field(None, description="Position exit timestamp")
    exit_price: Optional[Decimal] = Field(
        None, ge=0, description="Exit execution price"
    )
    quantity: Decimal = Field(..., ge=0, description="Position size")
    side: str = Field(..., description="Position side (LONG/SHORT)")

    # PnL and costs
    commission: Decimal = Field(
        default=Decimal("0"), ge=0, description="Total commission"
    )
    slippage: Decimal = Field(default=Decimal("0"), description="Slippage cost")
    realized_pnl: Optional[Decimal] = Field(None, description="Realized PnL")
    pnl_pct: Optional[Decimal] = Field(None, description="Percentage return")

    # Additional tracking fields
    strategy_name: Optional[str] = Field(
        None, description="Strategy that created trade"
    )
    notes: Optional[str] = Field(None, description="Trade notes")

    # Metadata
    created_at: datetime = Field(
        default_factory=datetime.now, description="Record creation time"
    )
    updated_at: datetime = Field(
        default_factory=datetime.now, description="Record update time"
    )

    model_config = {
        "json_encoders": {Decimal: str, datetime: lambda v: v.isoformat()},
        "use_enum_values": True,
    }

    @classmethod
    def from_nautilus_position(
        cls, position: Position, strategy_name: Optional[str] = None
    ) -> "TradeModel":
        """
        Create TradeModel from Nautilus Position.

        Args:
            position: Nautilus Position instance
            strategy_name: Optional strategy name that created the trade

        Returns:
            TradeModel instance with data from Nautilus Position
        """
        # Extract basic position data
        position_id = str(position.id) if position.id else "unknown"
        instrument_id = (
            str(position.instrument_id) if position.instrument_id else "unknown"
        )

        # Handle timestamps - Nautilus uses nanosecond precision
        entry_time = position.opened_time if position.opened_time else datetime.now()
        exit_time = (
            position.closed_time
            if position.is_closed and position.closed_time
            else None
        )

        # Extract prices and quantities
        entry_price = (
            Decimal(str(position.avg_px_open)) if position.avg_px_open else Decimal("0")
        )
        exit_price = None
        if position.is_closed and position.avg_px_close:
            exit_price = Decimal(str(position.avg_px_close))

        quantity = (
            Decimal(str(abs(position.quantity))) if position.quantity else Decimal("0")
        )

        # Determine side
        side = "LONG" if position.is_long else "SHORT"

        # Extract financial data
        commission = (
            Decimal(str(position.commission)) if position.commission else Decimal("0")
        )
        realized_pnl = None
        if position.is_closed and position.realized_pnl is not None:
            realized_pnl = Decimal(str(position.realized_pnl))

        return cls(
            position_id=position_id,
            instrument_id=instrument_id,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=exit_time,
            exit_price=exit_price,
            quantity=quantity,
            side=side,
            commission=commission,
            realized_pnl=realized_pnl,
            strategy_name=strategy_name,
        )

    def calculate_pnl_percentage(self) -> Optional[Decimal]:
        """
        Calculate percentage return for the trade.

        Returns:
            Percentage return as Decimal, or None if trade is not closed
        """
        if not self.exit_price or not self.entry_price or self.entry_price == 0:
            return None

        if self.side == "LONG":
            pnl_pct = ((self.exit_price - self.entry_price) / self.entry_price) * 100
        else:  # SHORT
            pnl_pct = ((self.entry_price - self.exit_price) / self.entry_price) * 100

        return Decimal(str(pnl_pct))

    def calculate_gross_pnl(self) -> Optional[Decimal]:
        """
        Calculate gross PnL (before costs) for the trade.

        Returns:
            Gross PnL as Decimal, or None if trade is not closed
        """
        if not self.exit_price or not self.entry_price:
            return None

        if self.side == "LONG":
            gross_pnl = (self.exit_price - self.entry_price) * self.quantity
        else:  # SHORT
            gross_pnl = (self.entry_price - self.exit_price) * self.quantity

        return Decimal(str(gross_pnl))

    def calculate_net_pnl(self) -> Optional[Decimal]:
        """
        Calculate net PnL (after costs) for the trade.

        Returns:
            Net PnL as Decimal, or None if trade is not closed
        """
        gross_pnl = self.calculate_gross_pnl()
        if gross_pnl is None:
            return None

        # Subtract costs
        net_pnl = gross_pnl - self.commission - self.slippage

        return Decimal(str(net_pnl))

    def update_exit_data(
        self, exit_price: Decimal, exit_time: Optional[datetime] = None
    ):
        """
        Update trade with exit information.

        Args:
            exit_price: Exit execution price
            exit_time: Exit timestamp (defaults to now)
        """
        self.exit_price = exit_price
        self.exit_time = exit_time or datetime.now()
        self.updated_at = datetime.now()

        # Recalculate derived metrics
        self.pnl_pct = self.calculate_pnl_percentage()
        self.realized_pnl = self.calculate_net_pnl()

    @property
    def is_open(self) -> bool:
        """Check if trade is still open."""
        return self.exit_time is None

    @property
    def is_closed(self) -> bool:
        """Check if trade is closed."""
        return self.exit_time is not None

    @property
    def duration_seconds(self) -> Optional[int]:
        """Get trade duration in seconds."""
        if not self.exit_time:
            return None
        return int((self.exit_time - self.entry_time).total_seconds())

    @property
    def duration_hours(self) -> Optional[float]:
        """Get trade duration in hours."""
        duration_sec = self.duration_seconds
        return duration_sec / 3600 if duration_sec is not None else None

    @property
    def is_winning_trade(self) -> Optional[bool]:
        """Check if trade is profitable."""
        if self.realized_pnl is None:
            return None
        return self.realized_pnl > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for export."""
        return {
            "position_id": self.position_id,
            "instrument_id": self.instrument_id,
            "strategy_name": self.strategy_name,
            "side": self.side,
            "entry_time": self.entry_time.isoformat(),
            "entry_price": str(self.entry_price),
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_price": str(self.exit_price) if self.exit_price else None,
            "quantity": str(self.quantity),
            "commission": str(self.commission),
            "slippage": str(self.slippage),
            "realized_pnl": str(self.realized_pnl) if self.realized_pnl else None,
            "pnl_pct": str(self.pnl_pct) if self.pnl_pct else None,
            "duration_hours": self.duration_hours,
            "is_winning_trade": self.is_winning_trade,
            "notes": self.notes,
        }

    def __str__(self) -> str:
        """String representation of the trade."""
        status = "OPEN" if self.is_open else "CLOSED"
        pnl_str = f"PnL: {self.realized_pnl}" if self.realized_pnl else "PnL: N/A"

        return (
            f"Trade({self.instrument_id} {self.side} {self.quantity}@{self.entry_price} "
            f"[{status}] {pnl_str})"
        )

    def __repr__(self) -> str:
        """Detailed representation of the trade."""
        return (
            f"TradeModel(position_id='{self.position_id}', "
            f"instrument='{self.instrument_id}', side='{self.side}', "
            f"entry_price={self.entry_price}, is_open={self.is_open})"
        )
