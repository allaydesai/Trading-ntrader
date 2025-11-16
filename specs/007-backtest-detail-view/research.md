# Research: Backtest Detail View & Metrics

**Feature Branch**: `007-backtest-detail-view`
**Date**: 2025-11-15
**Status**: Complete

## Overview

Research findings for implementing the Backtest Detail View feature. This document resolves all technical decisions for displaying comprehensive backtest metrics, trade blotter with sorting/pagination, configuration snapshots, and action buttons.

---

## 1. Trade Blotter Pagination Pattern

### Decision: Server-Side Pagination with HTMX Partial Updates

**Rationale**:
- Consistent with Phase 2 backtest list pagination pattern
- Avoids loading large trade datasets (10,000+ trades) into browser memory
- HTMX enables smooth table updates without full page reload
- Server-side sorting/filtering maintains database efficiency

**Alternatives Considered**:

1. **Client-Side Pagination (JavaScript DataTables)**
   - Rejected: Requires loading all data upfront, poor performance for large datasets
   - Violates KISS principle - unnecessary JavaScript complexity

2. **Infinite Scroll**
   - Rejected: Not suitable for tabular data with sorting requirements
   - Users need to jump to specific page for analysis

**Implementation**:
```html
<!-- Trade blotter with HTMX pagination -->
<div id="trade-blotter">
  <table class="w-full">
    <!-- Table headers with sortable columns -->
  </table>
  <div class="pagination-controls"
       hx-get="/backtests/{run_id}/trades"
       hx-target="#trade-blotter"
       hx-swap="innerHTML">
    <!-- Page navigation -->
  </div>
</div>
```

**Performance Target**: <500ms for 100 trades per page with sorting

---

## 2. Metric Tooltip Implementation

### Decision: Tailwind CSS Tooltips with Static Content

**Rationale**:
- Pure CSS implementation (no JavaScript required)
- Tooltips have fixed explanatory text (not dynamic)
- Accessible via keyboard focus
- Fast rendering without external libraries

**Alternatives Considered**:

1. **JavaScript Tooltip Libraries (Tippy.js, Popper.js)**
   - Rejected: Adds unnecessary dependencies
   - Overkill for static explanatory text

2. **Browser Native `title` Attribute**
   - Rejected: Cannot style, accessibility limitations
   - Appearance varies by browser

**Implementation**:
```html
<div class="relative group">
  <span class="font-semibold">Sharpe Ratio</span>
  <span class="text-green-400">1.67</span>
  <!-- Tooltip on hover -->
  <div class="absolute hidden group-hover:block bg-slate-700 p-2 rounded shadow-lg z-10 w-64 text-sm">
    Risk-adjusted return. Values > 1 are good, > 2 are excellent.
    Calculated as (Return - Risk-Free Rate) / Standard Deviation.
  </div>
</div>
```

**Metric Explanations**:
- **Total Return**: Percentage gain/loss from initial capital
- **CAGR**: Compound Annual Growth Rate - annualized return accounting for time
- **Sharpe Ratio**: Risk-adjusted return (higher is better, >1 is good)
- **Sortino Ratio**: Like Sharpe but only penalizes downside volatility
- **Max Drawdown**: Largest peak-to-trough decline (risk measure)
- **Volatility**: Standard deviation of returns (risk measure)
- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Gross profit / gross loss (>1 is profitable)

---

## 3. Configuration Snapshot Display

### Decision: Collapsible Section with JSON Pretty Print

**Rationale**:
- Configuration stored as JSONB in database (existing schema)
- Pretty-printed JSON shows exact parameters used
- Collapsible saves screen space (expanded by default per spec)
- "Copy CLI Command" generates replication command

**Implementation**:
```html
<details open class="bg-slate-800 rounded-lg p-4">
  <summary class="cursor-pointer font-semibold text-lg">
    Configuration Parameters
  </summary>
  <div class="mt-4">
    <div class="grid grid-cols-2 gap-4">
      <div>Instrument: {{ config.instrument_symbol }}</div>
      <div>Initial Capital: ${{ config.initial_capital }}</div>
      <div>Date Range: {{ config.start_date }} to {{ config.end_date }}</div>
      <!-- Additional parameters -->
    </div>
    <button onclick="copyToClipboard(cliCommand)"
            class="mt-4 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded">
      Copy CLI Command
    </button>
  </div>
</details>
```

**CLI Command Generation**:
```python
def generate_cli_command(config: dict) -> str:
    """Generate CLI command to replicate backtest."""
    return (
        f"ntrader backtest run "
        f"--strategy {config['strategy_name']} "
        f"--instrument {config['instrument_symbol']} "
        f"--start {config['start_date']} "
        f"--end {config['end_date']} "
        f"--capital {config['initial_capital']}"
    )
```

---

## 4. Trade Data Model

### Decision: Use Existing PerformanceMetrics + Add Trade List Query

**Rationale**:
- Performance metrics already stored in database
- Trade history not currently persisted (derived from backtest engine)
- For MVP: Display metrics only, trade blotter shows summary from metrics
- Future enhancement: Persist individual trade records

**Current State Analysis**:
- `PerformanceMetrics` table contains: total_trades, winning_trades, losing_trades, win_rate, profit_factor, avg_win, avg_loss
- Individual trade records NOT persisted in database
- Trade blotter will require either:
  - Option A: Generate dummy/synthetic trade data from metrics (MVP)
  - Option B: Persist trade history during backtest execution (requires DB schema change)

**MVP Decision**: Display metrics-based summary (no individual trade rows)
**Future Enhancement**: Add `trades` table to persist individual trade history

**Trade Summary Display**:
```html
<div class="bg-slate-800 rounded-lg p-4">
  <h3>Trading Summary</h3>
  <div class="grid grid-cols-2 gap-4">
    <div>Total Trades: {{ metrics.total_trades }}</div>
    <div>Winning Trades: {{ metrics.winning_trades }}</div>
    <div>Losing Trades: {{ metrics.losing_trades }}</div>
    <div>Win Rate: {{ metrics.win_rate }}%</div>
    <div>Average Win: ${{ metrics.avg_win }}</div>
    <div>Average Loss: ${{ metrics.avg_loss }}</div>
  </div>
</div>
```

---

## 5. Action Button Implementation

### Decision: HTMX-Based Actions with Confirmation

**Rationale**:
- Delete requires confirmation dialog (spec requirement)
- Re-run triggers async operation (background task)
- Export generates file download
- HTMX handles partial updates for feedback

**Implementation Patterns**:

**Delete with Confirmation**:
```html
<button hx-delete="/backtests/{{ run_id }}"
        hx-confirm="Are you sure you want to delete this backtest?"
        hx-target="body"
        hx-push-url="/backtests"
        class="bg-red-600 hover:bg-red-700">
  Delete
</button>
```

**Export Report**:
```html
<a href="/backtests/{{ run_id }}/export"
   download="backtest_report_{{ run_id }}.html"
   class="bg-green-600 hover:bg-green-700">
  Export Report
</a>
```

**Re-run Backtest**:
```html
<button hx-post="/backtests/{{ run_id }}/rerun"
        hx-indicator="#rerun-spinner"
        hx-disable-elt="this"
        class="bg-blue-600 hover:bg-blue-700">
  <span>Re-run Backtest</span>
  <span id="rerun-spinner" class="htmx-indicator">
    Running...
  </span>
</button>
```

---

## 6. Color-Coding Strategy

### Decision: Tailwind CSS Conditional Classes

**Rationale**:
- Simple conditional logic in templates
- Consistent with dark theme color palette
- Accessible contrast ratios (WCAG AA)

**Color Mapping**:
```python
# In Pydantic model
@computed_field
@property
def return_color(self) -> str:
    """Green for positive, red for negative."""
    if self.total_return >= 0:
        return "text-green-400"
    return "text-red-400"

@computed_field
@property
def sharpe_color(self) -> str:
    """Green if > 1, yellow if 0-1, red if < 0."""
    if self.sharpe_ratio >= 1.0:
        return "text-green-400"
    elif self.sharpe_ratio >= 0:
        return "text-yellow-400"
    return "text-red-400"

@computed_field
@property
def drawdown_color(self) -> str:
    """Always red (negative value is bad)."""
    return "text-red-400"
```

**Template Usage**:
```html
<span class="{{ metric.return_color }}">
  {{ metric.total_return | format_percentage }}
</span>
```

---

## 7. 404 Error Handling

### Decision: Custom Error Page with Navigation

**Rationale**:
- User-friendly error message (spec requirement FR-019)
- Maintains consistent UI (dark theme, navigation)
- Provides helpful next steps

**Implementation**:
```python
@router.get("/backtests/{run_id}")
async def backtest_detail(run_id: UUID, db: AsyncSession = Depends(get_db)):
    """Display backtest detail page."""
    backtest = await get_backtest_by_run_id(db, run_id)
    if not backtest:
        return templates.TemplateResponse(
            "errors/404.html",
            {
                "request": request,
                "message": f"Backtest {run_id} not found",
                "suggestion": "Return to backtest list to find available runs"
            },
            status_code=404
        )
    # ... render detail page
```

---

## 8. Breadcrumb Navigation

### Decision: Reuse Existing Breadcrumb Partial

**Rationale**:
- Breadcrumb partial already exists from Phase 2
- Consistent navigation pattern
- Simple to extend for detail view

**Implementation**:
```html
{% include "partials/breadcrumbs.html" %}

<!-- In template context -->
{
    "breadcrumbs": [
        {"label": "Dashboard", "url": "/"},
        {"label": "Backtests", "url": "/backtests"},
        {"label": "Run Details", "url": None}  # Current page
    ]
}
```

---

## Summary of Decisions

| Area | Decision | Rationale |
|------|----------|-----------|
| Trade Blotter Pagination | Server-side with HTMX | Performance, consistency with Phase 2 |
| Metric Tooltips | Pure CSS (Tailwind) | No JS dependency, accessible |
| Configuration Display | Collapsible JSON | Matches existing schema, space-efficient |
| Trade Data | Summary from metrics (MVP) | No schema change for MVP |
| Action Buttons | HTMX with confirmation | Progressive enhancement |
| Color Coding | Conditional Tailwind classes | Simple, accessible |
| Error Handling | Custom 404 template | User-friendly, consistent UI |
| Breadcrumbs | Reuse existing partial | DRY principle |

---

## Next Steps

1. Generate data-model.md with BacktestDetailView Pydantic models
2. Define HTML route contracts for detail view
3. Create quickstart guide for development setup
4. Proceed to task generation (/speckit.tasks)
