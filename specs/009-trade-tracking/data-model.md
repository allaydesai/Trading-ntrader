# Data Model: Individual Trade Tracking & Equity Curve Generation

**Feature**: 009-trade-tracking
**Created**: 2025-01-22
**Purpose**: Define entity schemas, relationships, validation rules, and state transitions for trade tracking

---

## Entity Overview

```
BacktestRun (existing)
    ↓ 1:N
Trade (new)
    ↓ derived
EquityCurvePoint (computed, not persisted)
    ↓ derived
DrawdownMetrics (computed, not persisted)
    ↓ derived
TradeStatistics (computed, not persisted)
```

---

## Entity 1: Trade

**Purpose**: Represents a single completed trade execution from a backtest, capturing entry/exit details, costs, and realized profit/loss.

### Database Schema (SQLAlchemy)

```python
from sqlalchemy import (
    Column, Integer, String, NUMERIC, TIMESTAMP, ForeignKey,
    CheckConstraint, Index
)
from sqlalchemy.orm import relationship
from src.db.base import Base

class Trade(Base):
    """
    Individual trade execution record.

    A trade represents a complete round trip: entry (buy/sell) and exit (sell/buy).
    Sourced from Nautilus Trader FillReport objects after backtest completion.
    """
    __tablename__ = "trades"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign key to backtest run
    backtest_run_id = Column(
        Integer,
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Nautilus Trader identifiers
    instrument_id = Column(String(50), nullable=False, index=True)
    trade_id = Column(String(100), nullable=False)
    venue_order_id = Column(String(100), nullable=False)
    client_order_id = Column(String(100), nullable=True)

    # Trade details
    order_side = Column(String(10), nullable=False)  # 'BUY' or 'SELL'
    quantity = Column(NUMERIC(20, 8), nullable=False)
    entry_price = Column(NUMERIC(20, 8), nullable=False)
    exit_price = Column(NUMERIC(20, 8), nullable=True)

    # Costs
    commission_amount = Column(NUMERIC(20, 8), nullable=True)
    commission_currency = Column(String(10), nullable=True)
    fees_amount = Column(NUMERIC(20, 8), nullable=True, default=0)

    # Calculated fields
    profit_loss = Column(NUMERIC(20, 8), nullable=True)
    profit_pct = Column(NUMERIC(10, 4), nullable=True)
    holding_period_seconds = Column(Integer, nullable=True)

    # Timestamps (UTC with timezone)
    entry_timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    exit_timestamp = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default="CURRENT_TIMESTAMP")

    # Relationships
    backtest_run = relationship("BacktestRun", back_populates="trades")

    # Constraints
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("entry_price > 0", name="positive_entry_price"),
        CheckConstraint("exit_price IS NULL OR exit_price > 0", name="positive_exit_price"),
        Index("idx_trades_backtest_time", "backtest_run_id", "entry_timestamp"),
    )
```

### Pydantic Models

```python
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from datetime import datetime
from typing import Optional

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
    fees_amount: Optional[Decimal] = Field(default=Decimal('0.00'), decimal_places=8)

    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None

    @field_validator('entry_price', 'exit_price')
    @classmethod
    def validate_prices_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Prices must be positive')
        return v

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }
    }

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

    model_config = {
        "from_attributes": True
    }

class TradeListResponse(BaseModel):
    """Paginated trade list response."""
    trades: list[Trade]
    total_count: int
    page: int
    page_size: int
    total_pages: int
```

### Field Definitions

| Field | Type | Nullable | Description | Validation |
|-------|------|----------|-------------|------------|
| `id` | Integer | No | Auto-incrementing primary key | |
| `backtest_run_id` | Integer | No | Foreign key to backtest_runs table | ON DELETE CASCADE |
| `instrument_id` | String(50) | No | Trading symbol (e.g., "AAPL", "EURUSD") | |
| `trade_id` | String(100) | No | Nautilus Trader trade ID (unique per venue) | |
| `venue_order_id` | String(100) | No | Exchange-assigned order ID | |
| `client_order_id` | String(100) | Yes | Client-side order ID (optional) | |
| `order_side` | String(10) | No | Order direction: 'BUY' or 'SELL' | Enum constraint |
| `quantity` | Numeric(20,8) | No | Number of units traded | > 0 |
| `entry_price` | Numeric(20,8) | No | Price at which position was opened | > 0 |
| `exit_price` | Numeric(20,8) | Yes | Price at which position was closed | > 0 or NULL |
| `commission_amount` | Numeric(20,8) | Yes | Commission paid for the trade | >= 0 |
| `commission_currency` | String(10) | Yes | Currency of commission (e.g., "USD") | |
| `fees_amount` | Numeric(20,8) | Yes | Additional fees (exchange, regulatory) | >= 0, default 0 |
| `profit_loss` | Numeric(20,8) | Yes | Realized P&L in base currency | Calculated |
| `profit_pct` | Numeric(10,4) | Yes | P&L as percentage of entry value | Calculated |
| `holding_period_seconds` | Integer | Yes | Time between entry and exit | Calculated |
| `entry_timestamp` | Timestamp(TZ) | No | When trade was entered (UTC) | |
| `exit_timestamp` | Timestamp(TZ) | Yes | When trade was exited (UTC) | >= entry_timestamp |
| `created_at` | Timestamp(TZ) | No | Record creation time (UTC) | Auto-set |

### Calculated Fields

Calculated fields are computed during trade creation and stored in the database:

```python
def calculate_trade_metrics(trade: TradeCreate) -> dict:
    """
    Calculate derived fields for a trade.

    Args:
        trade: TradeCreate model with entry/exit details

    Returns:
        Dictionary with profit_loss, profit_pct, holding_period_seconds
    """
    if trade.exit_price is None:
        # Trade still open
        return {
            "profit_loss": None,
            "profit_pct": None,
            "holding_period_seconds": None
        }

    # Calculate gross P&L (before costs)
    if trade.order_side == "BUY":
        # Long position: profit when exit > entry
        gross_pnl = (trade.exit_price - trade.entry_price) * trade.quantity
    else:  # SELL
        # Short position: profit when entry > exit
        gross_pnl = (trade.entry_price - trade.exit_price) * trade.quantity

    # Subtract costs
    total_costs = (trade.commission_amount or Decimal('0')) + (trade.fees_amount or Decimal('0'))
    net_pnl = gross_pnl - total_costs

    # Calculate percentage return
    entry_value = trade.entry_price * trade.quantity
    pnl_pct = (net_pnl / entry_value) * 100 if entry_value > 0 else Decimal('0')

    # Calculate holding period
    holding_period = None
    if trade.exit_timestamp and trade.entry_timestamp:
        delta = trade.exit_timestamp - trade.entry_timestamp
        holding_period = int(delta.total_seconds())

    return {
        "profit_loss": net_pnl,
        "profit_pct": pnl_pct,
        "holding_period_seconds": holding_period
    }
```

### Indexes

```sql
-- Primary lookup: trades for a specific backtest
CREATE INDEX idx_trades_backtest_run_id ON trades(backtest_run_id);

-- Sorting by entry time (most common query)
CREATE INDEX idx_trades_entry_timestamp ON trades(entry_timestamp);

-- Filtering by symbol
CREATE INDEX idx_trades_instrument_id ON trades(instrument_id);

-- Composite index for common query pattern (backtest + time range)
CREATE INDEX idx_trades_backtest_time ON trades(backtest_run_id, entry_timestamp DESC);
```

---

## Entity 2: EquityCurvePoint

**Purpose**: Represents account balance at a specific point in time. Generated on-demand from trades, not persisted to database.

### Pydantic Model

```python
from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime

class EquityCurvePoint(BaseModel):
    """
    Single point on equity curve showing account balance at a timestamp.

    Generated from cumulative trade P&L, not stored in database.
    """
    timestamp: datetime
    balance: Decimal = Field(..., decimal_places=2)
    cumulative_return_pct: Decimal = Field(..., decimal_places=4)
    trade_number: Optional[int] = None

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }
    }

class EquityCurveResponse(BaseModel):
    """API response for equity curve data."""
    points: list[EquityCurvePoint]
    initial_capital: Decimal
    final_balance: Decimal
    total_return_pct: Decimal
```

### Generation Logic

```python
def generate_equity_curve(
    trades: list[Trade],
    initial_capital: Decimal
) -> EquityCurveResponse:
    """
    Generate equity curve from trade history.

    Args:
        trades: List of completed trades sorted by exit_timestamp
        initial_capital: Starting account balance

    Returns:
        EquityCurveResponse with time-series balance data
    """
    balance = initial_capital
    points = [
        EquityCurvePoint(
            timestamp=trades[0].entry_timestamp if trades else datetime.now(),
            balance=balance,
            cumulative_return_pct=Decimal('0.00'),
            trade_number=0
        )
    ]

    # Sort trades by exit timestamp
    sorted_trades = sorted(
        [t for t in trades if t.exit_timestamp is not None],
        key=lambda t: t.exit_timestamp
    )

    for idx, trade in enumerate(sorted_trades, start=1):
        balance += trade.profit_loss or Decimal('0')
        cum_return = ((balance - initial_capital) / initial_capital) * 100

        points.append(
            EquityCurvePoint(
                timestamp=trade.exit_timestamp,
                balance=balance,
                cumulative_return_pct=cum_return,
                trade_number=idx
            )
        )

    final_balance = balance
    total_return = ((final_balance - initial_capital) / initial_capital) * 100

    return EquityCurveResponse(
        points=points,
        initial_capital=initial_capital,
        final_balance=final_balance,
        total_return_pct=total_return
    )
```

---

## Entity 3: DrawdownMetrics

**Purpose**: Represents maximum drawdown analysis from equity curve. Computed on-demand.

### Pydantic Model

```python
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
    max_drawdown: DrawdownPeriod
    top_drawdowns: list[DrawdownPeriod] = Field(max_length=5)
    current_drawdown: Optional[DrawdownPeriod] = None
    total_drawdown_periods: int
```

### Calculation Logic

```python
def calculate_drawdowns(
    equity_curve: list[EquityCurvePoint]
) -> DrawdownMetrics:
    """
    Calculate drawdown periods from equity curve.

    Args:
        equity_curve: Time-series of account balances

    Returns:
        DrawdownMetrics with max drawdown and top periods
    """
    drawdowns: list[DrawdownPeriod] = []
    peak_balance = equity_curve[0].balance
    peak_timestamp = equity_curve[0].timestamp
    in_drawdown = False
    trough_balance = peak_balance
    trough_timestamp = peak_timestamp

    for point in equity_curve[1:]:
        if point.balance > peak_balance:
            # New peak - end current drawdown if any
            if in_drawdown:
                dd_amount = peak_balance - trough_balance
                dd_pct = (dd_amount / peak_balance) * 100
                duration = (trough_timestamp - peak_timestamp).days

                drawdowns.append(
                    DrawdownPeriod(
                        peak_timestamp=peak_timestamp,
                        peak_balance=peak_balance,
                        trough_timestamp=trough_timestamp,
                        trough_balance=trough_balance,
                        drawdown_amount=dd_amount,
                        drawdown_pct=dd_pct,
                        duration_days=duration,
                        recovery_timestamp=point.timestamp,
                        recovered=True
                    )
                )
                in_drawdown = False

            peak_balance = point.balance
            peak_timestamp = point.timestamp
            trough_balance = point.balance
            trough_timestamp = point.timestamp
        elif point.balance < trough_balance:
            # New trough
            trough_balance = point.balance
            trough_timestamp = point.timestamp
            in_drawdown = True

    # Handle ongoing drawdown
    current_dd = None
    if in_drawdown:
        dd_amount = peak_balance - trough_balance
        dd_pct = (dd_amount / peak_balance) * 100
        duration = (trough_timestamp - peak_timestamp).days

        current_dd = DrawdownPeriod(
            peak_timestamp=peak_timestamp,
            peak_balance=peak_balance,
            trough_timestamp=trough_timestamp,
            trough_balance=trough_balance,
            drawdown_amount=dd_amount,
            drawdown_pct=dd_pct,
            duration_days=duration,
            recovered=False
        )

    # Sort by drawdown percentage descending
    sorted_dds = sorted(drawdowns, key=lambda d: d.drawdown_pct, reverse=True)

    return DrawdownMetrics(
        max_drawdown=sorted_dds[0] if sorted_dds else current_dd,
        top_drawdowns=sorted_dds[:5],
        current_drawdown=current_dd,
        total_drawdown_periods=len(drawdowns)
    )
```

---

## Entity 4: TradeStatistics

**Purpose**: Aggregate statistics calculated from trade history. Computed on-demand.

### Pydantic Model

```python
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
```

### Calculation Logic

```python
def calculate_trade_statistics(trades: list[Trade]) -> TradeStatistics:
    """
    Calculate aggregate statistics from trade history.

    Args:
        trades: List of completed trades

    Returns:
        TradeStatistics with win rate, profit metrics, streaks, etc.
    """
    if not trades:
        return TradeStatistics(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=Decimal('0.00'),
            total_profit=Decimal('0.00'),
            total_loss=Decimal('0.00'),
            net_profit=Decimal('0.00'),
            average_win=Decimal('0.00'),
            average_loss=Decimal('0.00'),
            largest_win=Decimal('0.00'),
            largest_loss=Decimal('0.00'),
            expectancy=Decimal('0.00'),
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            avg_holding_period_hours=Decimal('0.00'),
            max_holding_period_hours=0,
            min_holding_period_hours=0
        )

    # Filter completed trades only
    completed = [t for t in trades if t.profit_loss is not None]

    # Categorize trades
    winners = [t for t in completed if t.profit_loss > 0]
    losers = [t for t in completed if t.profit_loss < 0]
    breakeven = [t for t in completed if t.profit_loss == 0]

    # Calculate basic metrics
    total_profit = sum(t.profit_loss for t in winners)
    total_loss = sum(abs(t.profit_loss) for t in losers)
    net_profit = total_profit - total_loss

    avg_win = total_profit / len(winners) if winners else Decimal('0')
    avg_loss = total_loss / len(losers) if losers else Decimal('0')

    largest_win = max((t.profit_loss for t in winners), default=Decimal('0'))
    largest_loss = min((t.profit_loss for t in losers), default=Decimal('0'))

    win_rate = (len(winners) / len(completed)) * 100 if completed else Decimal('0')

    # Profit factor
    profit_factor = total_profit / total_loss if total_loss > 0 else None

    # Expectancy
    expectancy = net_profit / len(completed) if completed else Decimal('0')

    # Calculate consecutive streaks
    max_wins_streak = 0
    max_losses_streak = 0
    current_win_streak = 0
    current_loss_streak = 0

    for trade in sorted(completed, key=lambda t: t.exit_timestamp):
        if trade.profit_loss > 0:
            current_win_streak += 1
            current_loss_streak = 0
            max_wins_streak = max(max_wins_streak, current_win_streak)
        elif trade.profit_loss < 0:
            current_loss_streak += 1
            current_win_streak = 0
            max_losses_streak = max(max_losses_streak, current_loss_streak)

    # Holding periods
    holding_periods = [
        t.holding_period_seconds / 3600  # convert to hours
        for t in completed
        if t.holding_period_seconds is not None
    ]

    avg_holding = Decimal(sum(holding_periods) / len(holding_periods)) if holding_periods else Decimal('0')
    max_holding = int(max(holding_periods)) if holding_periods else 0
    min_holding = int(min(holding_periods)) if holding_periods else 0

    return TradeStatistics(
        total_trades=len(completed),
        winning_trades=len(winners),
        losing_trades=len(losers),
        breakeven_trades=len(breakeven),
        win_rate=win_rate,
        total_profit=total_profit,
        total_loss=total_loss,
        net_profit=net_profit,
        average_win=avg_win,
        average_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_consecutive_wins=max_wins_streak,
        max_consecutive_losses=max_losses_streak,
        avg_holding_period_hours=avg_holding,
        max_holding_period_hours=max_holding,
        min_holding_period_hours=min_holding
    )
```

---

## State Transitions

### Trade Lifecycle

```
┌─────────────┐
│   Created   │ ← FillReport imported from Nautilus Trader
└──────┬──────┘
       │
       ▼
┌─────────────┐
│    Open     │ ← entry_timestamp set, exit_timestamp NULL
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Closed    │ ← exit_timestamp set, profit_loss calculated
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Persisted  │ ← Saved to database
└─────────────┘
```

**Note**: In backtest context, all trades are typically closed before persistence. Open trades (if any) are handled by marking to final market price or excluding from statistics.

---

## Validation Rules

### Database Level
1. **Foreign Key Integrity**: trades.backtest_run_id references backtest_runs.id with CASCADE DELETE
2. **Positive Quantities**: quantity > 0
3. **Positive Prices**: entry_price > 0, exit_price > 0 (when not NULL)
4. **Timestamp Ordering**: exit_timestamp >= entry_timestamp (enforced in application layer)

### Application Level (Pydantic)
1. **Order Side**: Must be 'BUY' or 'SELL'
2. **Decimal Precision**: Prices to 8 decimals, percentages to 4 decimals
3. **Non-Negative Costs**: commission_amount >= 0, fees_amount >= 0
4. **Timestamp Format**: ISO 8601 with UTC timezone

### Business Logic
1. **Profit Calculation**: Must account for order side (long vs short)
2. **Holding Period**: Must be non-negative
3. **Equity Curve**: Must start with initial capital
4. **Drawdown**: Cannot exceed 100%

---

## Relationships

```
BacktestRun 1 ──────── N Trade
                       │
                       │ derives
                       ▼
            EquityCurvePoint (computed)
                       │
                       │ derives
                       ▼
            DrawdownMetrics (computed)

Trade N ────────────── 1 TradeStatistics (computed from all trades)
```

**Notes**:
- One BacktestRun can have many Trades
- EquityCurvePoint, DrawdownMetrics, and TradeStatistics are derived entities (not persisted)
- Trades are immutable once created (no updates allowed)

---

## Migration Strategy

### Alembic Migration

```python
"""Add trades table for individual trade tracking

Revision ID: xxx
Created: 2025-01-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

def upgrade():
    # Create trades table
    op.create_table(
        'trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('backtest_run_id', sa.Integer(), nullable=False),
        sa.Column('instrument_id', sa.String(length=50), nullable=False),
        sa.Column('trade_id', sa.String(length=100), nullable=False),
        sa.Column('venue_order_id', sa.String(length=100), nullable=False),
        sa.Column('client_order_id', sa.String(length=100), nullable=True),
        sa.Column('order_side', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.NUMERIC(precision=20, scale=8), nullable=False),
        sa.Column('entry_price', sa.NUMERIC(precision=20, scale=8), nullable=False),
        sa.Column('exit_price', sa.NUMERIC(precision=20, scale=8), nullable=True),
        sa.Column('commission_amount', sa.NUMERIC(precision=20, scale=8), nullable=True),
        sa.Column('commission_currency', sa.String(length=10), nullable=True),
        sa.Column('fees_amount', sa.NUMERIC(precision=20, scale=8), nullable=True),
        sa.Column('profit_loss', sa.NUMERIC(precision=20, scale=8), nullable=True),
        sa.Column('profit_pct', sa.NUMERIC(precision=10, scale=4), nullable=True),
        sa.Column('holding_period_seconds', sa.Integer(), nullable=True),
        sa.Column('entry_timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('exit_timestamp', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.CheckConstraint('quantity > 0', name='positive_quantity'),
        sa.CheckConstraint('entry_price > 0', name='positive_entry_price'),
        sa.CheckConstraint('exit_price IS NULL OR exit_price > 0', name='positive_exit_price'),
        sa.ForeignKeyConstraint(['backtest_run_id'], ['backtest_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create indexes
    op.create_index('idx_trades_backtest_run_id', 'trades', ['backtest_run_id'])
    op.create_index('idx_trades_entry_timestamp', 'trades', ['entry_timestamp'])
    op.create_index('idx_trades_instrument_id', 'trades', ['instrument_id'])
    op.create_index('idx_trades_backtest_time', 'trades', ['backtest_run_id', 'entry_timestamp'])

def downgrade():
    op.drop_index('idx_trades_backtest_time', table_name='trades')
    op.drop_index('idx_trades_instrument_id', table_name='trades')
    op.drop_index('idx_trades_entry_timestamp', table_name='trades')
    op.drop_index('idx_trades_backtest_run_id', table_name='trades')
    op.drop_table('trades')
```

---

## Testing Strategy

### Unit Tests
- Test Trade model validation (Pydantic)
- Test profit_loss calculation for BUY and SELL sides
- Test equity curve generation with various trade sequences
- Test drawdown calculation algorithm
- Test trade statistics calculation

### Integration Tests
- Test trade persistence to database
- Test bulk insert performance (500+ trades)
- Test foreign key cascading on backtest_run deletion
- Test database constraint enforcement

### Example Test

```python
@pytest.mark.asyncio
async def test_trade_profit_calculation_long_position():
    """Test profit calculation for long (BUY) position."""
    trade = TradeCreate(
        backtest_run_id=1,
        instrument_id="AAPL",
        trade_id="trade-123",
        venue_order_id="order-456",
        order_side="BUY",
        quantity=Decimal("100"),
        entry_price=Decimal("150.00"),
        exit_price=Decimal("160.00"),
        commission_amount=Decimal("5.00"),
        entry_timestamp=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        exit_timestamp=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    )

    metrics = calculate_trade_metrics(trade)

    # Expected: (160 - 150) * 100 - 5 = 995
    assert metrics["profit_loss"] == Decimal("995.00")
    # Expected: 995 / (150 * 100) * 100 = 6.63%
    assert abs(metrics["profit_pct"] - Decimal("6.63")) < Decimal("0.01")
    # Expected: 1 hour = 3600 seconds
    assert metrics["holding_period_seconds"] == 3600
```

---

**Next Steps**: Generate API contracts (OpenAPI specs) for trade endpoints in `/contracts/` directory.
