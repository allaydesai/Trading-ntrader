# Research: Individual Trade Tracking & Equity Curve Generation

**Feature**: 009-trade-tracking
**Research Date**: 2025-01-22
**Purpose**: Document technical decisions, integration patterns, and best practices for implementing trade tracking using Nautilus Trader

---

## Executive Summary

This research establishes the technical foundation for capturing individual trades from Nautilus Trader backtests and persisting them to PostgreSQL. Key findings:

1. **Nautilus Trader provides complete trade data** via `FillReport` objects containing entry/exit prices, quantities, commissions, timestamps, and profit/loss
2. **Best integration point** is post-backtest using `trader.generate_fills_report()` which returns a Pandas DataFrame
3. **Database design** extends existing backtest_runs table with 1:N relationship to trades table
4. **Decimal precision required** for all financial values to avoid rounding errors in profit calculations
5. **Performance optimized** via bulk inserts, database indexes, and server-side pagination

---

## Decision 1: Nautilus Trader Trade Capture Approach

### Decision
Use Nautilus Trader's built-in `trader.generate_fills_report()` method to extract trade data after backtest completion, rather than capturing trades in real-time during backtest execution.

### Rationale
Nautilus Trader provides comprehensive trade reporting APIs that are:
- **Well-tested and maintained** by the Nautilus Trader framework
- **Complete with all required data** including commissions, fees, timestamps, P&L
- **Efficient** - generates complete trade history in single DataFrame
- **Non-invasive** - doesn't require modifying backtest execution logic

From Nautilus Trader documentation:
```python
# Generate fills report returns pandas DataFrame with columns:
# - instrument_id, trade_id, order_id
# - entry_price, exit_price, quantity
# - commission, liquidity_side
# - ts_event (timestamp in nanoseconds)
fills_report = trader.generate_fills_report()
```

### Alternatives Considered

**Alternative 1: Event Listener During Backtest**
- Hook into Nautilus Trader's event system to capture `OrderFilled` events in real-time
- **Rejected**: More complex, requires understanding Nautilus event bus, potential performance impact, and risk of missing events if listener fails

**Alternative 2: Parse Nautilus Trader Log Files**
- Extract trade data from Nautilus Trader's structured logs
- **Rejected**: Fragile (log format may change), incomplete data (logs may not contain all fields), inefficient (text parsing vs. direct API)

**Alternative 3: Custom Position Tracker**
- Build custom logic to track position entries/exits from order fills
- **Rejected**: Reinventing functionality already provided by Nautilus Trader, high risk of calculation errors, substantial development effort

### Implementation Notes
- Call `trader.generate_fills_report()` after `engine.run()` completes
- Convert Pandas DataFrame rows to Pydantic models for validation
- Bulk insert validated trades to PostgreSQL using SQLAlchemy async session

---

## Decision 2: FillReport Data Mapping to Database Schema

### Decision
Map Nautilus Trader `FillReport` fields directly to database columns with the following transformations:
- Store prices/quantities as PostgreSQL `NUMERIC` (Decimal in Python)
- Convert nanosecond timestamps to PostgreSQL `TIMESTAMP WITH TIME ZONE`
- Store instrument_id, trade_id, order_id as TEXT for flexibility
- Calculate and store derived fields: profit_loss, profit_pct, holding_period

### Rationale
Nautilus Trader `FillReport` class provides these fields:
```python
class FillReport:
    account_id: AccountId
    instrument_id: InstrumentId
    venue_order_id: VenueOrderId
    trade_id: TradeId
    order_side: OrderSide  # BUY or SELL
    last_qty: Quantity
    last_px: Price
    commission: Money
    liquidity_side: LiquiditySide  # MAKER or TAKER
    ts_event: int  # Nanoseconds since epoch
    ts_init: int
    client_order_id: Optional[ClientOrderId]
    venue_position_id: Optional[PositionId]
```

**Key Mapping Decisions**:
1. **Use `ts_event` for trade timestamp** (when trade occurred at exchange), not `ts_init` (when object created)
2. **Store `order_side` as ENUM** ('BUY'/'SELL') for type safety
3. **Extract currency from `commission.currency`** and amount from `commission.as_decimal()`
4. **Calculate profit_loss** from price differences and quantity (will implement in analytics service)

### Database Schema
```sql
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    backtest_run_id INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,

    -- Nautilus Trader IDs
    instrument_id TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    venue_order_id TEXT NOT NULL,
    client_order_id TEXT,

    -- Trade details
    order_side VARCHAR(10) NOT NULL,  -- 'BUY' or 'SELL'
    quantity NUMERIC(20, 8) NOT NULL,
    entry_price NUMERIC(20, 8) NOT NULL,
    exit_price NUMERIC(20, 8),

    -- Costs
    commission_amount NUMERIC(20, 8),
    commission_currency VARCHAR(10),

    -- Calculated fields
    profit_loss NUMERIC(20, 8),
    profit_pct NUMERIC(10, 4),
    holding_period_seconds INTEGER,

    -- Timestamps (UTC)
    entry_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    exit_timestamp TIMESTAMP WITH TIME ZONE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT positive_quantity CHECK (quantity > 0),
    CONSTRAINT positive_prices CHECK (entry_price > 0 AND (exit_price IS NULL OR exit_price > 0))
);

CREATE INDEX idx_trades_backtest_run_id ON trades(backtest_run_id);
CREATE INDEX idx_trades_entry_timestamp ON trades(entry_timestamp);
CREATE INDEX idx_trades_instrument_id ON trades(instrument_id);
```

### Implementation Notes
- Use SQLAlchemy 2.0 with async support
- Pydantic models validate data before database insertion
- Store all monetary values as Decimal (never float)
- Timestamps stored with timezone information (UTC)

---

## Decision 3: Equity Curve Calculation Algorithm

### Decision
Calculate equity curve by iterating through trades sorted by exit timestamp and computing cumulative account balance after each trade exit. Use Decimal arithmetic throughout to preserve precision.

### Rationale
Equity curve shows account balance evolution over time. Requirements:
- Start with initial capital (from backtest configuration)
- Add/subtract realized P&L at each trade exit
- Maintain chronological order by exit timestamp
- Store as time-series for charting

**Algorithm**:
```python
def calculate_equity_curve(
    trades: List[Trade],
    initial_capital: Decimal
) -> List[EquityCurvePoint]:
    """
    Generate equity curve from closed trades.

    Returns list of (timestamp, balance, cumulative_return_pct) tuples.
    """
    balance = initial_capital
    equity_points = [
        EquityCurvePoint(
            timestamp=start_time,
            balance=balance,
            cumulative_return_pct=Decimal('0.00')
        )
    ]

    # Sort trades by exit timestamp
    sorted_trades = sorted(trades, key=lambda t: t.exit_timestamp)

    for trade in sorted_trades:
        # Add realized P&L to balance
        balance += trade.profit_loss

        cumulative_return = ((balance - initial_capital) / initial_capital) * 100

        equity_points.append(
            EquityCurvePoint(
                timestamp=trade.exit_timestamp,
                balance=balance,
                cumulative_return_pct=cumulative_return
            )
        )

    return equity_points
```

### Alternatives Considered

**Alternative 1: Mark-to-Market Equity Curve**
- Update equity curve on every tick based on unrealized P&L
- **Rejected**: Requires historical price data for all timestamps, computationally expensive, spec indicates equity updates only on trade exits (realized P&L)

**Alternative 2: Store Equity Curve in Separate Table**
- Persist equity curve points to database
- **Rejected**: Can be calculated on-demand from trades table, adds storage overhead, trades table is source of truth

### Implementation Notes
- Generate equity curve on API request (not stored in database)
- Cache results for performance if needed
- Handle edge cases: zero trades (flat line at initial capital), ongoing drawdowns

---

## Decision 4: Drawdown Calculation Method

### Decision
Calculate maximum drawdown using the "running maximum" algorithm that tracks peak-to-trough declines in the equity curve.

### Rationale
Maximum drawdown is critical risk metric showing worst-case loss from peak. Standard algorithm:
1. Track running maximum (peak) balance
2. Calculate current drawdown from peak at each point
3. Record maximum drawdown encountered

**Algorithm**:
```python
def calculate_max_drawdown(
    equity_curve: List[EquityCurvePoint]
) -> DrawdownMetrics:
    """
    Calculate maximum drawdown from equity curve.

    Returns DrawdownMetrics with:
    - max_drawdown_pct
    - max_drawdown_amount
    - peak_timestamp
    - trough_timestamp
    - recovery_timestamp (if recovered)
    """
    peak_balance = equity_curve[0].balance
    peak_timestamp = equity_curve[0].timestamp
    max_dd_pct = Decimal('0.00')
    max_dd_amount = Decimal('0.00')
    trough_timestamp = None

    for point in equity_curve:
        # Update peak if new high
        if point.balance > peak_balance:
            peak_balance = point.balance
            peak_timestamp = point.timestamp

        # Calculate current drawdown from peak
        current_dd_amount = peak_balance - point.balance
        current_dd_pct = (current_dd_amount / peak_balance) * 100 if peak_balance > 0 else Decimal('0.00')

        # Track maximum drawdown
        if current_dd_pct > max_dd_pct:
            max_dd_pct = current_dd_pct
            max_dd_amount = current_dd_amount
            trough_timestamp = point.timestamp

    return DrawdownMetrics(
        max_drawdown_pct=max_dd_pct,
        max_drawdown_amount=max_dd_amount,
        peak_timestamp=peak_timestamp,
        trough_timestamp=trough_timestamp
    )
```

### Implementation Notes
- Calculate drawdown on-demand from equity curve
- Support multiple drawdown periods (top 3 largest drawdowns)
- Calculate recovery time when applicable

---

## Decision 5: API Pagination Strategy

### Decision
Implement server-side pagination with configurable page sizes (20, 50, 100 trades per page) and support for sorting by any column.

### Rationale
Backtests can generate 1000+ trades for high-frequency strategies. Requirements:
- Fast page loads (<300ms for 100 trades)
- Low memory usage (don't load all trades)
- Sortable columns (entry date, P&L, symbol, etc.)
- Standard pagination patterns (limit/offset)

**API Design**:
```python
@router.get("/backtests/{run_id}/trades")
async def list_trades(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("entry_timestamp", regex="^(entry_timestamp|exit_timestamp|profit_loss|symbol)$"),
    sort_order: str = Query("asc", regex="^(asc|desc)$"),
    db: AsyncSession = Depends(get_db)
) -> TradeListResponse:
    """
    Get paginated list of trades for a backtest run.

    Returns:
        TradeListResponse with trades[], total_count, page, page_size, total_pages
    """
    # Build query with filters
    query = select(Trade).where(Trade.backtest_run_id == run_id)

    # Apply sorting
    sort_column = getattr(Trade, sort_by)
    query = query.order_by(sort_column.desc() if sort_order == "desc" else sort_column.asc())

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_count = await db.scalar(count_query)

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.limit(page_size).offset(offset)

    # Execute query
    result = await db.execute(query)
    trades = result.scalars().all()

    return TradeListResponse(
        trades=trades,
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=math.ceil(total_count / page_size)
    )
```

### Alternatives Considered

**Alternative 1: Cursor-Based Pagination**
- Use `id > last_seen_id` instead of offset
- **Rejected**: More complex for bi-directional navigation, sorting by arbitrary columns difficult, offset-based is simpler for small-to-medium datasets

**Alternative 2: Load All Trades to Frontend**
- Send all trades, paginate in browser
- **Rejected**: Violates performance constraints for 1000+ trade datasets, high bandwidth usage, browser memory issues

### Implementation Notes
- Add database indexes on sortable columns
- Use SQLAlchemy `limit()` and `offset()` for efficiency
- Return total count for pagination controls
- Validate sort_by parameter to prevent SQL injection

---

## Decision 6: UI Integration with Existing Backtest Details Page

### Decision
Extend the existing backtest details page (specs/007-backtest-detail-view) by adding two new sections: trades table and equity curve chart. Use HTMX for trades pagination and existing chart integration patterns.

### Rationale
The backtest details page already displays performance metrics. Adding trades naturally extends this view:
- **Trades Table Section**: Paginated table below metrics using HTMX partial updates
- **Equity Curve Chart**: New chart using same pattern as existing OHLCV charts (specs/008-chart-apis)

**HTML Template Structure**:
```html
<!-- Existing sections: Summary, Performance Metrics -->

<!-- NEW: Equity Curve Chart Section -->
<div class="card mb-4">
    <h3>Equity Curve</h3>
    <canvas id="equityCurveChart"></canvas>
</div>

<!-- NEW: Trades Section -->
<div class="card" id="trades-section">
    <h3>Individual Trades</h3>

    <!-- Pagination controls -->
    <div class="flex justify-between items-center mb-4">
        <select hx-get="/backtests/{{ run_id }}/trades"
                hx-target="#trades-table"
                hx-trigger="change"
                name="page_size">
            <option value="20" selected>20 per page</option>
            <option value="50">50 per page</option>
            <option value="100">100 per page</option>
        </select>
    </div>

    <!-- Trades table (HTMX partial) -->
    <div id="trades-table"
         hx-get="/backtests/{{ run_id }}/trades?page=1&page_size=20"
         hx-trigger="load">
        <!-- Table rendered via HTMX -->
    </div>
</div>
```

**HTMX Partial for Trades Table**:
```html
<!-- templates/partials/trades_table.html -->
<table class="w-full">
    <thead>
        <tr>
            <th hx-get="/backtests/{{ run_id }}/trades?sort_by=entry_timestamp&sort_order=asc"
                hx-target="#trades-table">Entry Time</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Quantity</th>
            <th>Entry Price</th>
            <th>Exit Price</th>
            <th hx-get="/backtests/{{ run_id }}/trades?sort_by=profit_loss&sort_order=desc"
                hx-target="#trades-table">P&L</th>
        </tr>
    </thead>
    <tbody>
        {% for trade in trades %}
        <tr class="{{ 'text-green-600' if trade.profit_loss > 0 else 'text-red-600' }}">
            <td>{{ trade.entry_timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
            <td>{{ trade.instrument_id }}</td>
            <td>{{ trade.order_side }}</td>
            <td>{{ trade.quantity }}</td>
            <td>${{ trade.entry_price }}</td>
            <td>${{ trade.exit_price }}</td>
            <td>${{ trade.profit_loss }}</td>
        </tr>
        {% endfor %}
    </tbody>
</table>

<!-- Pagination controls -->
<div class="flex justify-center mt-4">
    {% for page_num in range(1, total_pages + 1) %}
    <button hx-get="/backtests/{{ run_id }}/trades?page={{ page_num }}&page_size={{ page_size }}"
            hx-target="#trades-table"
            class="{{ 'active' if page_num == page else '' }}">
        {{ page_num }}
    </button>
    {% endfor %}
</div>
```

### Implementation Notes
- Follow existing Tailwind CSS patterns from specs/005-webui-foundation
- Use same chart library as OHLCV charts for consistency
- Color code winning trades (green) vs losing trades (red)
- HTMX handles partial page updates without full reload

---

## Decision 7: Performance Optimization Strategies

### Decision
Implement multi-level performance optimizations:
1. **Bulk database inserts** for trades (batch 500+ trades in single transaction)
2. **Database indexes** on frequently queried columns (backtest_run_id, entry_timestamp, instrument_id)
3. **On-demand calculations** for equity curve and metrics (not pre-computed)
4. **Server-side pagination** for API responses
5. **Database connection pooling** (already configured in specs/004-postgresql-metadata-storage)

### Rationale
Performance requirements:
- Bulk insert 500+ trades in <5 seconds
- Equity curve for 1000 trades in <1 second
- API queries return within 300ms
- UI page load within 2 seconds

**Bulk Insert Pattern**:
```python
async def save_trades_bulk(
    backtest_run_id: int,
    fill_reports: List[FillReport],
    db: AsyncSession
) -> None:
    """
    Bulk insert trades from FillReport objects.

    Performance: ~500-1000 trades per second.
    """
    # Convert FillReports to Trade models
    trades = [
        Trade(
            backtest_run_id=backtest_run_id,
            instrument_id=str(report.instrument_id),
            trade_id=str(report.trade_id),
            venue_order_id=str(report.venue_order_id),
            order_side=report.order_side.name,
            quantity=report.last_qty.as_decimal(),
            entry_price=report.last_px.as_decimal(),
            commission_amount=report.commission.as_decimal(),
            commission_currency=str(report.commission.currency),
            entry_timestamp=datetime.fromtimestamp(report.ts_event / 1e9, tz=timezone.utc),
        )
        for report in fill_reports
    ]

    # Bulk insert
    db.add_all(trades)
    await db.commit()
```

**Database Indexes**:
```sql
-- Primary lookup: find all trades for a backtest
CREATE INDEX idx_trades_backtest_run_id ON trades(backtest_run_id);

-- Sorting by entry time (most common)
CREATE INDEX idx_trades_entry_timestamp ON trades(entry_timestamp);

-- Filtering by symbol
CREATE INDEX idx_trades_instrument_id ON trades(instrument_id);

-- Composite index for common query pattern
CREATE INDEX idx_trades_backtest_time ON trades(backtest_run_id, entry_timestamp DESC);
```

### Measurement Strategy
- Use pytest-benchmark for unit test performance tracking
- Log query execution times in development
- Monitor database query performance with EXPLAIN ANALYZE
- Set performance budgets in tests (e.g., equity curve calculation <1 second for 1000 trades)

---

## Research Artifacts

### Nautilus Trader Documentation References
- FillReport API: https://nautilustrader.io/docs/latest/api_reference/execution (FillReport class definition)
- Generate Fills Report: https://nautilustrader.io/docs/latest/api_reference/trading (trader.generate_fills_report() method)
- Position Reports: https://nautilustrader.io/docs/latest/api_reference/execution (Position tracking and P&L calculations)

### Related Specifications
- **specs/004-postgresql-metadata-storage**: Existing database infrastructure, async SQLAlchemy patterns
- **specs/005-webui-foundation**: FastAPI + Jinja2 + HTMX + Tailwind CSS patterns
- **specs/007-backtest-detail-view**: Existing backtest details page structure
- **specs/008-chart-apis**: Chart integration patterns for equity curve visualization

### Technology Stack Validation
All required technologies already in use:
- ✅ Python 3.11+
- ✅ Nautilus Trader (core framework)
- ✅ FastAPI 0.109+
- ✅ SQLAlchemy 2.0 (async)
- ✅ Pydantic 2.5+
- ✅ PostgreSQL 16+
- ✅ Pandas (for report generation)
- ✅ HTMX + Tailwind CSS (UI)

No new dependencies required.

---

## Open Questions & Assumptions

### Resolved
1. ✅ **How to extract trade data from Nautilus Trader?** → Use `trader.generate_fills_report()`
2. ✅ **What precision for financial values?** → PostgreSQL NUMERIC mapped to Python Decimal
3. ✅ **When to update equity curve?** → On trade exits only (realized P&L), not mark-to-market
4. ✅ **How to handle partial fills?** → Each FillReport represents a complete fill, Nautilus handles aggregation
5. ✅ **Database schema design?** → Extend backtest_runs with 1:N trades relationship

### Assumptions (from spec.md)
1. All trades in backtest are eventually closed (or marked at final price for unrealized P&L)
2. Initial implementation focuses on single-instrument backtests (schema supports multi-instrument)
3. Equity curve updates only on trade exits (realized P&L focus)
4. Commission/fee data available from Nautilus Trader (can be zero if not available)
5. Timestamps stored in UTC with nanosecond precision

---

## Next Steps

Phase 1 (Design & Contracts):
1. ✅ Create data-model.md with entity definitions
2. ✅ Generate API contracts (OpenAPI specs) for trade endpoints
3. ✅ Write quickstart.md for developers
4. ✅ Update agent context with new technology stack

**Status**: Research complete. Proceed to Phase 1.
