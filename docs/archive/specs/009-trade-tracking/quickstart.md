# Quickstart: Individual Trade Tracking & Equity Curve Generation

**Feature**: 009-trade-tracking
**For**: Developers implementing trade tracking functionality
**Last Updated**: 2025-01-22

---

## Overview

This quickstart guide provides step-by-step instructions for implementing individual trade tracking in the Trading-NTrader backtesting system. By the end of this guide, you'll have:

- ✅ Database schema for trades table
- ✅ Trade capture from Nautilus Trader FillReports
- ✅ Equity curve generation API endpoint
- ✅ Drawdown and statistics calculation
- ✅ UI integration in backtest details page

**Development Approach**: Test-Driven Development (TDD) - Write tests first, then implement

---

## Prerequisites

Ensure you have the following set up:

```bash
# Check Python version
python --version  # Should be 3.11+

# Check UV is installed
uv --version

# Sync dependencies
cd /Users/allay/dev/Trading-ntrader
uv sync

# Verify database connection
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader_dev -d ntrader_dev -c "SELECT version();"

# Verify Nautilus Trader is available
uv run python -c "from nautilus_trader import __version__; print(__version__)"
```

---

## Step 1: Database Migration (10 minutes)

### 1.1 Create Alembic Migration

```bash
# Navigate to project root
cd /Users/allay/dev/Trading-ntrader

# Generate migration
uv run alembic revision -m "add_trades_table_for_individual_trade_tracking"
```

### 1.2 Edit Migration File

Open the newly created migration file in `src/db/migrations/versions/` and add:

```python
"""add trades table for individual trade tracking

Revision ID: <generated>
Revises: <previous_revision>
Create Date: 2025-01-22
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '<generated>'
down_revision = '<previous_revision>'
branch_labels = None
depends_on = None

def upgrade():
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

### 1.3 Run Migration

```bash
# Apply migration
uv run alembic upgrade head

# Verify table created
PGPASSWORD=ntrader_dev_2025 psql -h localhost -U ntrader_dev -d ntrader_dev -c "\d trades"
```

---

## Step 2: Create Data Models (20 minutes)

### 2.1 Write Tests First (TDD Red Phase)

Create `tests/unit/test_trade_models.py`:

```python
"""Unit tests for trade data models."""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.models.trade import TradeCreate, Trade, calculate_trade_metrics

def test_trade_create_validation():
    """Test TradeCreate model validates required fields."""
    with pytest.raises(ValueError):
        TradeCreate(
            backtest_run_id=1,
            instrument_id="",  # Should fail: empty string
            trade_id="t1",
            venue_order_id="o1",
            order_side="BUY",
            quantity=Decimal("100"),
            entry_price=Decimal("150"),
            entry_timestamp=datetime.now(timezone.utc)
        )

def test_calculate_profit_long_position():
    """Test profit calculation for BUY (long) position."""
    trade = TradeCreate(
        backtest_run_id=1,
        instrument_id="AAPL",
        trade_id="t1",
        venue_order_id="o1",
        order_side="BUY",
        quantity=Decimal("100"),
        entry_price=Decimal("150.00"),
        exit_price=Decimal("160.00"),
        commission_amount=Decimal("5.00"),
        entry_timestamp=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        exit_timestamp=datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc)
    )

    metrics = calculate_trade_metrics(trade)

    # (160 - 150) * 100 - 5 = 995
    assert metrics["profit_loss"] == Decimal("995.00")
    # 995 / (150 * 100) * 100 = 6.63%
    assert abs(metrics["profit_pct"] - Decimal("6.63")) < Decimal("0.01")
    assert metrics["holding_period_seconds"] == 3600

def test_calculate_profit_short_position():
    """Test profit calculation for SELL (short) position."""
    trade = TradeCreate(
        backtest_run_id=1,
        instrument_id="AAPL",
        trade_id="t1",
        venue_order_id="o1",
        order_side="SELL",
        quantity=Decimal("100"),
        entry_price=Decimal("160.00"),
        exit_price=Decimal("150.00"),
        commission_amount=Decimal("5.00"),
        entry_timestamp=datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc),
        exit_timestamp=datetime(2025, 1, 1, 11, 0, tzinfo=timezone.utc)
    )

    metrics = calculate_trade_metrics(trade)

    # (160 - 150) * 100 - 5 = 995
    assert metrics["profit_loss"] == Decimal("995.00")
```

Run tests (they should FAIL):

```bash
uv run pytest tests/unit/test_trade_models.py -v
```

### 2.2 Implement Models (TDD Green Phase)

Create `src/models/trade.py`:

```python
"""Pydantic models for trade tracking."""
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from datetime import datetime
from typing import Optional

class TradeBase(BaseModel):
    """Base trade model."""
    instrument_id: str = Field(..., min_length=1, max_length=50)
    trade_id: str
    venue_order_id: str
    client_order_id: Optional[str] = None
    order_side: str = Field(..., pattern="^(BUY|SELL)$")
    quantity: Decimal = Field(..., gt=0)
    entry_price: Decimal = Field(..., gt=0)
    exit_price: Optional[Decimal] = Field(None, gt=0)
    commission_amount: Optional[Decimal] = Field(None, ge=0)
    commission_currency: Optional[str] = Field(None, max_length=10)
    fees_amount: Optional[Decimal] = Field(default=Decimal('0.00'), ge=0)
    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }
    }

class TradeCreate(TradeBase):
    """Model for creating trades."""
    backtest_run_id: int

class Trade(TradeBase):
    """Complete trade with calculated fields."""
    id: int
    backtest_run_id: int
    profit_loss: Optional[Decimal] = None
    profit_pct: Optional[Decimal] = None
    holding_period_seconds: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}

def calculate_trade_metrics(trade: TradeCreate) -> dict:
    """Calculate derived fields."""
    if trade.exit_price is None:
        return {
            "profit_loss": None,
            "profit_pct": None,
            "holding_period_seconds": None
        }

    # Calculate gross P&L
    if trade.order_side == "BUY":
        gross_pnl = (trade.exit_price - trade.entry_price) * trade.quantity
    else:  # SELL (short)
        gross_pnl = (trade.entry_price - trade.exit_price) * trade.quantity

    # Subtract costs
    total_costs = (trade.commission_amount or Decimal('0')) + (trade.fees_amount or Decimal('0'))
    net_pnl = gross_pnl - total_costs

    # Calculate percentage
    entry_value = trade.entry_price * trade.quantity
    pnl_pct = (net_pnl / entry_value * 100).quantize(Decimal('0.01')) if entry_value > 0 else Decimal('0.00')

    # Holding period
    holding_period = None
    if trade.exit_timestamp and trade.entry_timestamp:
        delta = trade.exit_timestamp - trade.entry_timestamp
        holding_period = int(delta.total_seconds())

    return {
        "profit_loss": net_pnl.quantize(Decimal('0.01')),
        "profit_pct": pnl_pct,
        "holding_period_seconds": holding_period
    }
```

Run tests again (should PASS):

```bash
uv run pytest tests/unit/test_trade_models.py -v
```

### 2.3 Create SQLAlchemy Model

Create `src/db/models/trade.py`:

```python
"""SQLAlchemy model for trades."""
from sqlalchemy import Column, Integer, String, NUMERIC, TIMESTAMP, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import relationship
from src.db.base import Base

class Trade(Base):
    """Trade database model."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    backtest_run_id = Column(Integer, ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    instrument_id = Column(String(50), nullable=False, index=True)
    trade_id = Column(String(100), nullable=False)
    venue_order_id = Column(String(100), nullable=False)
    client_order_id = Column(String(100))
    order_side = Column(String(10), nullable=False)
    quantity = Column(NUMERIC(20, 8), nullable=False)
    entry_price = Column(NUMERIC(20, 8), nullable=False)
    exit_price = Column(NUMERIC(20, 8))
    commission_amount = Column(NUMERIC(20, 8))
    commission_currency = Column(String(10))
    fees_amount = Column(NUMERIC(20, 8), default=0)
    profit_loss = Column(NUMERIC(20, 8))
    profit_pct = Column(NUMERIC(10, 4))
    holding_period_seconds = Column(Integer)
    entry_timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    exit_timestamp = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default="CURRENT_TIMESTAMP")

    backtest_run = relationship("BacktestRun", back_populates="trades")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("entry_price > 0", name="positive_entry_price"),
        CheckConstraint("exit_price IS NULL OR exit_price > 0", name="positive_exit_price"),
        Index("idx_trades_backtest_time", "backtest_run_id", "entry_timestamp"),
    )
```

Update `src/db/models/backtest_run.py` to add relationship:

```python
# Add to BacktestRun class
from sqlalchemy.orm import relationship

class BacktestRun(Base):
    # ... existing columns ...

    # Add relationship
    trades = relationship("Trade", back_populates="backtest_run", cascade="all, delete-orphan")
```

---

## Step 3: Implement Trade Capture Service (30 minutes)

### 3.1 Write Integration Test First

Create `tests/integration/test_trade_persistence.py`:

```python
"""Integration tests for trade persistence."""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from src.services.backtest_persistence import save_trades_from_fills
from src.db.session import get_async_session

@pytest.mark.asyncio
async def test_save_trades_from_nautilus_fills(async_session, sample_backtest_run):
    """Test saving trades from Nautilus Trader FillReports."""
    from nautilus_trader.model.identifiers import AccountId, InstrumentId, TradeId, VenueOrderId
    from nautilus_trader.model.enums import OrderSide, LiquiditySide
    from nautilus_trader.model.objects import Quantity, Price, Money
    from nautilus_trader.execution.reports import FillReport
    from nautilus_trader.model.currencies import USD

    # Create sample FillReport (mimicking Nautilus Trader output)
    fill_report = FillReport(
        account_id=AccountId("SIM-001"),
        instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
        venue_order_id=VenueOrderId("order-123"),
        trade_id=TradeId("trade-456"),
        order_side=OrderSide.BUY,
        last_qty=Quantity.from_int(100),
        last_px=Price.from_str("150.00"),
        commission=Money(5.00, USD),
        liquidity_side=LiquiditySide.TAKER,
        report_id=UUID4(),
        ts_event=1706000000000000000,  # Nanoseconds
        ts_init=1706000000000000000
    )

    # Call service to save trades
    await save_trades_from_fills(
        backtest_run_id=sample_backtest_run.id,
        fill_reports=[fill_report],
        db=async_session
    )

    # Verify trade was saved
    from sqlalchemy import select
    from src.db.models.trade import Trade

    result = await async_session.execute(
        select(Trade).where(Trade.backtest_run_id == sample_backtest_run.id)
    )
    trades = result.scalars().all()

    assert len(trades) == 1
    assert trades[0].instrument_id == "AAPL.NASDAQ"
    assert trades[0].quantity == Decimal("100.00000000")
    assert trades[0].entry_price == Decimal("150.00000000")
```

### 3.2 Implement Service

Add to `src/services/backtest_persistence.py`:

```python
"""Trade persistence service."""
from typing import List
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from nautilus_trader.execution.reports import FillReport
from src.db.models.trade import Trade as TradeModel
from src.models.trade import TradeCreate, calculate_trade_metrics

async def save_trades_from_fills(
    backtest_run_id: int,
    fill_reports: List[FillReport],
    db: AsyncSession
) -> None:
    """
    Save trades from Nautilus Trader FillReports.

    Args:
        backtest_run_id: ID of the backtest run
        fill_reports: List of FillReport objects from Nautilus Trader
        db: Async database session
    """
    trades = []

    for report in fill_reports:
        # Convert FillReport to TradeCreate
        trade_data = TradeCreate(
            backtest_run_id=backtest_run_id,
            instrument_id=str(report.instrument_id),
            trade_id=str(report.trade_id),
            venue_order_id=str(report.venue_order_id),
            client_order_id=str(report.client_order_id) if report.client_order_id else None,
            order_side=report.order_side.name,
            quantity=report.last_qty.as_decimal(),
            entry_price=report.last_px.as_decimal(),
            exit_price=None,  # Will be set when position closes
            commission_amount=report.commission.as_decimal(),
            commission_currency=str(report.commission.currency),
            entry_timestamp=datetime.fromtimestamp(report.ts_event / 1e9, tz=timezone.utc),
            exit_timestamp=None
        )

        # Calculate metrics
        metrics = calculate_trade_metrics(trade_data)

        # Create database model
        trade_model = TradeModel(
            backtest_run_id=trade_data.backtest_run_id,
            instrument_id=trade_data.instrument_id,
            trade_id=trade_data.trade_id,
            venue_order_id=trade_data.venue_order_id,
            client_order_id=trade_data.client_order_id,
            order_side=trade_data.order_side,
            quantity=trade_data.quantity,
            entry_price=trade_data.entry_price,
            exit_price=trade_data.exit_price,
            commission_amount=trade_data.commission_amount,
            commission_currency=trade_data.commission_currency,
            fees_amount=trade_data.fees_amount,
            profit_loss=metrics["profit_loss"],
            profit_pct=metrics["profit_pct"],
            holding_period_seconds=metrics["holding_period_seconds"],
            entry_timestamp=trade_data.entry_timestamp,
            exit_timestamp=trade_data.exit_timestamp
        )

        trades.append(trade_model)

    # Bulk insert
    db.add_all(trades)
    await db.commit()
```

---

## Step 4: Create API Endpoints (30 minutes)

### 4.1 Write API Test First

Create `tests/integration/test_trades_api.py`:

```python
"""API endpoint tests for trades."""
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_list_trades_paginated(async_client: AsyncClient, sample_trades):
    """Test GET /backtests/{id}/trades with pagination."""
    response = await async_client.get(
        "/api/v1/backtests/1/trades?page=1&page_size=20&sort_by=entry_timestamp&sort_order=asc"
    )

    assert response.status_code == 200
    data = response.json()
    assert "trades" in data
    assert "total_count" in data
    assert "page" in data
    assert data["page"] == 1
    assert data["page_size"] == 20

@pytest.mark.asyncio
async def test_get_equity_curve(async_client: AsyncClient, sample_trades):
    """Test GET /backtests/{id}/equity-curve."""
    response = await async_client.get("/api/v1/backtests/1/equity-curve")

    assert response.status_code == 200
    data = response.json()
    assert "points" in data
    assert "initial_capital" in data
    assert "final_balance" in data
    assert len(data["points"]) > 0
```

### 4.2 Implement Router

Create `src/api/routers/trades.py`:

```python
"""API endpoints for trade tracking."""
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from src.db.session import get_async_session
from src.db.models.trade import Trade as TradeModel
from src.models.trade import Trade, TradeListResponse
from src.services.trade_analytics import generate_equity_curve, EquityCurveResponse

router = APIRouter(prefix="/backtests/{backtest_run_id}", tags=["trades"])

@router.get("/trades", response_model=TradeListResponse)
async def list_trades(
    backtest_run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("entry_timestamp", regex="^(entry_timestamp|exit_timestamp|profit_loss|instrument_id)$"),
    sort_order: str = Query("asc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_async_session)
):
    """Get paginated list of trades."""
    # Build query
    query = select(TradeModel).where(TradeModel.backtest_run_id == backtest_run_id)

    # Apply sorting
    sort_column = getattr(TradeModel, sort_by)
    query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_count = await db.scalar(count_query)

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)

    # Execute
    result = await db.execute(query)
    trades = result.scalars().all()

    return TradeListResponse(
        trades=trades,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=(total_count + page_size - 1) // page_size
    )

@router.get("/equity-curve", response_model=EquityCurveResponse)
async def get_equity_curve(
    backtest_run_id: int,
    db: AsyncSession = Depends(get_async_session)
):
    """Generate equity curve for backtest."""
    # Get all trades
    query = select(TradeModel).where(TradeModel.backtest_run_id == backtest_run_id)
    result = await db.execute(query)
    trades = result.scalars().all()

    # TODO: Get initial capital from backtest config
    initial_capital = Decimal("100000.00")

    return generate_equity_curve(trades, initial_capital)
```

Register router in `src/api/main.py`:

```python
from src.api.routers import trades

app.include_router(trades.router, prefix="/api/v1")
```

---

## Step 5: Implement Analytics Service (30 minutes)

Create `src/services/trade_analytics.py`:

```python
"""Trade analytics calculations."""
from decimal import Decimal
from typing import List
from datetime import datetime
from src.models.trade import Trade
from pydantic import BaseModel

class EquityCurvePoint(BaseModel):
    """Point on equity curve."""
    timestamp: datetime
    balance: Decimal
    cumulative_return_pct: Decimal
    trade_number: int

class EquityCurveResponse(BaseModel):
    """Equity curve response."""
    points: List[EquityCurvePoint]
    initial_capital: Decimal
    final_balance: Decimal
    total_return_pct: Decimal

def generate_equity_curve(
    trades: List[Trade],
    initial_capital: Decimal
) -> EquityCurveResponse:
    """Generate equity curve from trades."""
    balance = initial_capital
    points = [
        EquityCurvePoint(
            timestamp=trades[0].entry_timestamp if trades else datetime.now(),
            balance=balance,
            cumulative_return_pct=Decimal('0.00'),
            trade_number=0
        )
    ]

    # Sort by exit timestamp
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

## Step 6: Integration with Backtest Execution (15 minutes)

Update backtest execution workflow to capture trades:

```python
# In src/services/backtest_runner.py (or equivalent)

async def run_backtest_with_trade_capture(config: BacktestConfig):
    """Run backtest and capture trades."""
    # ... existing backtest setup ...

    # Run backtest
    engine.run()

    # Capture trades using Nautilus Trader's generate_fills_report()
    fills_df = engine.trader.generate_fills_report()

    # Convert DataFrame to FillReport objects
    # (Implementation depends on Nautilus Trader version)
    fill_reports = convert_dataframe_to_fill_reports(fills_df)

    # Save trades to database
    async with get_async_session() as db:
        await save_trades_from_fills(
            backtest_run_id=run.id,
            fill_reports=fill_reports,
            db=db
        )

    return run
```

---

## Step 7: UI Integration (20 minutes)

### 7.1 Add Trades Section to Backtest Details Page

Edit `src/templates/backtest_detail.html`:

```html
<!-- Existing sections: Summary, Performance Metrics -->

<!-- NEW: Equity Curve Chart -->
<div class="card mb-4">
    <h3 class="text-xl font-bold mb-4">Equity Curve</h3>
    <canvas id="equityCurveChart" width="800" height="400"></canvas>
</div>

<script>
async function loadEquityCurve() {
    const response = await fetch('/api/v1/backtests/{{ backtest_run.id }}/equity-curve');
    const data = await response.json();

    const ctx = document.getElementById('equityCurveChart').getContext('2d');
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.points.map(p => new Date(p.timestamp).toLocaleString()),
            datasets: [{
                label: 'Account Balance',
                data: data.points.map(p => parseFloat(p.balance)),
                borderColor: 'rgb(75, 192, 192)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            scales: {
                y: {
                    beginAtZero: false,
                    title: { display: true, text: 'Balance ($)' }
                }
            }
        }
    });
}
loadEquityCurve();
</script>

<!-- NEW: Trades Table -->
<div class="card mt-6">
    <h3 class="text-xl font-bold mb-4">Individual Trades</h3>

    <div id="trades-table"
         hx-get="/api/v1/backtests/{{ backtest_run.id }}/trades?page=1&page_size=20"
         hx-trigger="load"
         hx-swap="innerHTML">
        <!-- Table loaded via HTMX -->
    </div>
</div>
```

### 7.2 Create HTMX Partial Template

Create `src/templates/partials/trades_table.html`:

```html
<table class="w-full">
    <thead>
        <tr class="bg-gray-100">
            <th class="p-2">Entry Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Entry Price</th>
            <th>Exit Price</th>
            <th>P&L</th>
        </tr>
    </thead>
    <tbody>
        {% for trade in trades %}
        <tr class="border-b {{ 'text-green-600' if trade.profit_loss > 0 else 'text-red-600' }}">
            <td class="p-2">{{ trade.entry_timestamp.strftime('%Y-%m-%d %H:%M') }}</td>
            <td>{{ trade.instrument_id }}</td>
            <td>{{ trade.order_side }}</td>
            <td>{{ trade.quantity }}</td>
            <td>${{ "%.2f"|format(trade.entry_price) }}</td>
            <td>${{ "%.2f"|format(trade.exit_price) if trade.exit_price else '-' }}</td>
            <td>${{ "%.2f"|format(trade.profit_loss) if trade.profit_loss else '-' }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>

<!-- Pagination -->
<div class="flex justify-center mt-4 space-x-2">
    {% for page_num in range(1, total_pages + 1) %}
    <button hx-get="/api/v1/backtests/{{ backtest_run_id }}/trades?page={{ page_num }}&page_size={{ page_size }}"
            hx-target="#trades-table"
            class="px-3 py-1 rounded {{ 'bg-blue-500 text-white' if page_num == page else 'bg-gray-200' }}">
        {{ page_num }}
    </button>
    {% endfor %}
</div>
```

---

## Step 8: Testing & Validation (20 minutes)

### 8.1 Run All Tests

```bash
# Unit tests
uv run pytest tests/unit/test_trade_models.py -v
uv run pytest tests/unit/test_trade_analytics.py -v

# Integration tests
uv run pytest tests/integration/test_trade_persistence.py -v
uv run pytest tests/integration/test_trades_api.py -v

# Check coverage
uv run pytest --cov=src --cov-report=html
```

### 8.2 Manual Testing

```bash
# Start development server
uv run uvicorn src.main:app --reload

# Run a test backtest
uv run python scripts/run_backtest.py --symbol AAPL --start 2024-01-01 --end 2024-12-31

# View backtest details in browser
open http://localhost:8000/backtests/1
```

### 8.3 Code Quality Checks

```bash
# Format code
uv run ruff format .

# Lint
uv run ruff check .

# Type checking
uv run mypy src/
```

---

## Troubleshooting

### Issue: Migration fails with "relation already exists"

**Solution**: Drop and recreate the table, or use `alembic downgrade -1` then `alembic upgrade head`

### Issue: FillReport conversion fails

**Solution**: Check Nautilus Trader version compatibility. Use `generate_fills_report()` which returns a Pandas DataFrame

### Issue: Decimal precision errors in profit calculations

**Solution**: Ensure all calculations use `Decimal` type, not `float`. Use `.quantize()` for rounding.

### Issue: HTMX pagination not working

**Solution**: Verify HTMX is loaded in base template. Check browser console for errors.

---

## Next Steps

After completing this quickstart:

1. ✅ Review `tasks.md` for implementation task breakdown
2. ✅ Implement drawdown metrics endpoint
3. ✅ Implement trade statistics endpoint
4. ✅ Add CSV export functionality
5. ✅ Implement trade filtering (by symbol, date range, P&L)

**Reference Documents**:
- `spec.md` - Full feature specification
- `research.md` - Technical decisions and Nautilus Trader integration
- `data-model.md` - Complete entity definitions
- `contracts/trades-api.yaml` - OpenAPI specification

**Questions?** Refer to project CLAUDE.md for development guidelines and TDD practices.
