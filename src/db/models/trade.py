"""
SQLAlchemy ORM model for individual trade tracking.

This module defines the database schema for persisting individual trade
executions from Nautilus Trader backtests.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.db.models.backtest import BacktestRun

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


class Trade(Base):
    """
    Individual trade execution record.

    Represents a single completed trade execution with entry/exit details,
    costs, and realized profit/loss. Sourced from Nautilus Trader FillReport
    objects after backtest completion.

    Attributes:
        id: Internal database primary key
        backtest_run_id: Foreign key to backtest_runs table
        instrument_id: Trading symbol (e.g., "AAPL", "EURUSD")
        trade_id: Nautilus Trader trade ID
        venue_order_id: Exchange-assigned order ID
        client_order_id: Client-side order ID (optional)
        order_side: Order direction ('BUY' or 'SELL')
        quantity: Number of units traded
        entry_price: Price at which position was opened
        exit_price: Price at which position was closed
        commission_amount: Commission paid for the trade
        commission_currency: Currency of commission
        fees_amount: Additional fees (exchange, regulatory)
        profit_loss: Realized P&L in base currency (calculated)
        profit_pct: P&L as percentage of entry value (calculated)
        holding_period_seconds: Time between entry and exit (calculated)
        entry_timestamp: When trade was entered (UTC)
        exit_timestamp: When trade was exited (UTC)
        created_at: Record creation time (UTC)
        backtest_run: Associated backtest run relationship

    Example:
        >>> trade = Trade(
        ...     backtest_run_id=1,
        ...     instrument_id="AAPL",
        ...     trade_id="trade-123",
        ...     venue_order_id="order-456",
        ...     order_side="BUY",
        ...     quantity=Decimal("100"),
        ...     entry_price=Decimal("150.00"),
        ...     exit_price=Decimal("160.00"),
        ...     entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        ...     exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        ... )
    """

    __tablename__ = "trades"

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Foreign key to backtest_runs
    backtest_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nautilus Trader identifiers
    instrument_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_id: Mapped[str] = mapped_column(String(100), nullable=False)
    venue_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    client_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Trade details
    order_side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    # Costs
    commission_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    commission_currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    fees_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)

    # Calculated fields
    profit_loss: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8), nullable=True)
    profit_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    holding_period_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps (UTC with timezone)
    entry_timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )
    exit_timestamp: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    backtest_run: Mapped["BacktestRun"] = relationship("BacktestRun", back_populates="trades")

    # Table constraints
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("entry_price > 0", name="positive_entry_price"),
        CheckConstraint("exit_price IS NULL OR exit_price > 0", name="positive_exit_price"),
        Index("idx_trades_backtest_time", "backtest_run_id", "entry_timestamp"),
    )

    def __repr__(self) -> str:
        """Return string representation of Trade."""
        return (
            f"<Trade(id={self.id}, instrument={self.instrument_id}, "
            f"side={self.order_side}, pnl={self.profit_loss})>"
        )
