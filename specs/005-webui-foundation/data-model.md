# Data Model: Web UI Foundation

**Date**: 2025-11-15
**Feature**: 005-webui-foundation

## Overview

This feature introduces view models (Pydantic) for web UI rendering. These models transform existing database entities (`BacktestRun`, `PerformanceMetrics`) into UI-friendly representations. No new database tables are required.

## Entities

### 1. DashboardSummary (View Model)

**Purpose**: Aggregate statistics displayed on the home dashboard

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| total_backtests | int | Count of all backtest runs | COUNT(*) on backtest_runs |
| best_sharpe_ratio | Decimal | None | Highest Sharpe ratio achieved | MAX(sharpe_ratio) from metrics |
| best_sharpe_strategy | str | None | Strategy name with best Sharpe | JOIN on best Sharpe |
| worst_max_drawdown | Decimal | None | Worst (most negative) drawdown | MIN(max_drawdown) from metrics |
| worst_drawdown_strategy | str | None | Strategy with worst drawdown | JOIN on worst drawdown |
| recent_backtests | List[RecentBacktestItem] | Last 5 executed backtests | ORDER BY created_at DESC LIMIT 5 |

**Validation Rules**:
- total_backtests >= 0
- best_sharpe_ratio is None if no successful backtests
- worst_max_drawdown is None if no successful backtests

**Example**:
```python
DashboardSummary(
    total_backtests=42,
    best_sharpe_ratio=Decimal("2.15"),
    best_sharpe_strategy="SMA Crossover",
    worst_max_drawdown=Decimal("-0.25"),
    worst_drawdown_strategy="RSI Mean Reversion",
    recent_backtests=[...]
)
```

---

### 2. RecentBacktestItem (View Model)

**Purpose**: Condensed backtest info for dashboard activity feed

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| run_id | UUID | Business identifier | backtest_runs.run_id |
| run_id_short | str | First 8 chars of UUID for display | Computed from run_id |
| strategy_name | str | Strategy display name | backtest_runs.strategy_name |
| instrument_symbol | str | Trading instrument | backtest_runs.instrument_symbol |
| execution_status | str | "success" or "failed" | backtest_runs.execution_status |
| created_at | datetime | When backtest was run | backtest_runs.created_at |
| total_return | Decimal | None | Return percentage (if success) | metrics.total_return |

**Validation Rules**:
- run_id_short = str(run_id)[:8]
- total_return is None if execution_status = "failed"
- execution_status in ["success", "failed"]

**State Transitions**: None (read-only view)

---

### 3. BacktestListItem (View Model)

**Purpose**: Row data for paginated backtest table (FR-009)

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| run_id | UUID | Business identifier (for navigation) | backtest_runs.run_id |
| run_id_short | str | First 8 chars for display | Computed |
| strategy_name | str | Strategy name (max 50 chars display) | backtest_runs.strategy_name |
| instrument_symbol | str | Trading symbol | backtest_runs.instrument_symbol |
| date_range | str | "YYYY-MM-DD to YYYY-MM-DD" | Formatted from start/end dates |
| total_return | Decimal | None | Return percentage | metrics.total_return |
| sharpe_ratio | Decimal | None | Risk-adjusted return | metrics.sharpe_ratio |
| max_drawdown | Decimal | None | Peak-to-trough decline | metrics.max_drawdown |
| execution_status | str | Status indicator | backtest_runs.execution_status |
| created_at | datetime | Execution timestamp | backtest_runs.created_at |

**Validation Rules**:
- strategy_name truncated to 50 chars with "..." if longer
- Metrics are None for failed backtests
- date_range formatted as ISO dates

**Computed Properties**:
- `is_positive_return`: total_return > 0 if not None
- `status_color`: "green" for success, "red" for failed

---

### 4. BacktestListPage (View Model)

**Purpose**: Paginated response for backtest list endpoint

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| backtests | List[BacktestListItem] | Page of backtests | Query result |
| page | int | Current page number (1-indexed) | Request parameter |
| page_size | int | Results per page | Default 20 |
| total_count | int | Total backtests in system | COUNT(*) query |
| total_pages | int | Calculated pages | ceil(total_count / page_size) |
| has_next | bool | More pages available | page < total_pages |
| has_previous | bool | Previous page exists | page > 1 |

**Validation Rules**:
- page >= 1
- page_size in [10, 20, 50] (allowed values)
- total_pages = max(1, ceil(total_count / page_size))

---

### 5. NavigationState (View Model)

**Purpose**: Current navigation context for template rendering

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| active_page | str | Current page identifier | Route handler |
| breadcrumbs | List[BreadcrumbItem] | Navigation path | Route handler |
| app_version | str | Application version | Settings |

**Navigation Pages**:
- "dashboard" - Home page (/)
- "backtests" - Backtest list (/backtests)
- "data" - Data catalog (/data) [future]
- "docs" - Documentation (/docs) [future]

---

### 6. BreadcrumbItem (View Model)

**Purpose**: Single item in breadcrumb trail (FR-007)

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| label | str | Display text | Static or computed |
| url | str | None | Link URL (None for current) | Route URL |
| is_current | bool | Last item in trail | Position check |

**Example**:
```python
breadcrumbs = [
    BreadcrumbItem(label="Dashboard", url="/", is_current=False),
    BreadcrumbItem(label="Backtests", url="/backtests", is_current=False),
    BreadcrumbItem(label="Detail", url=None, is_current=True)
]
```

---

### 7. EmptyStateMessage (View Model)

**Purpose**: User-friendly message when no data available (FR-015)

**Fields**:
| Field | Type | Description | Source |
|-------|------|-------------|--------|
| title | str | Short message | Hardcoded per context |
| description | str | Helpful explanation | Hardcoded per context |
| action_text | str | None | Suggested action | Context-specific |
| action_command | str | None | CLI command example | Context-specific |

**Instances**:
```python
# No backtests
EmptyStateMessage(
    title="No Backtests Yet",
    description="You haven't run any backtests yet.",
    action_text="Run your first backtest",
    action_command="ntrader backtest run --strategy sma_crossover --symbol AAPL"
)

# Failed query
EmptyStateMessage(
    title="Unable to Load Data",
    description="The database connection is unavailable.",
    action_text="Check database connection",
    action_command="ntrader health check"
)
```

---

## Relationships

```
DashboardSummary
  └── RecentBacktestItem (1:N, embedded list)

BacktestListPage
  └── BacktestListItem (1:N, embedded list)

NavigationState
  └── BreadcrumbItem (1:N, embedded list)
```

## Mapping from Existing Models

### BacktestRun (DB) → BacktestListItem (View)

```python
def to_list_item(run: BacktestRun) -> BacktestListItem:
    return BacktestListItem(
        run_id=run.run_id,
        run_id_short=str(run.run_id)[:8],
        strategy_name=truncate(run.strategy_name, 50),
        instrument_symbol=run.instrument_symbol,
        date_range=f"{run.start_date.date()} to {run.end_date.date()}",
        total_return=run.metrics.total_return if run.metrics else None,
        sharpe_ratio=run.metrics.sharpe_ratio if run.metrics else None,
        max_drawdown=run.metrics.max_drawdown if run.metrics else None,
        execution_status=run.execution_status,
        created_at=run.created_at
    )
```

### BacktestRun (DB) → RecentBacktestItem (View)

```python
def to_recent_item(run: BacktestRun) -> RecentBacktestItem:
    return RecentBacktestItem(
        run_id=run.run_id,
        run_id_short=str(run.run_id)[:8],
        strategy_name=run.strategy_name,
        instrument_symbol=run.instrument_symbol,
        execution_status=run.execution_status,
        created_at=run.created_at,
        total_return=run.metrics.total_return if run.metrics else None
    )
```

---

## Database Queries (No Schema Changes)

### Dashboard Statistics Query

```sql
-- Get aggregate stats
SELECT
    COUNT(*) as total_backtests,
    MAX(pm.sharpe_ratio) as best_sharpe,
    MIN(pm.max_drawdown) as worst_drawdown
FROM backtest_runs br
LEFT JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE br.execution_status = 'success';

-- Get strategy names for best/worst
SELECT br.strategy_name, pm.sharpe_ratio
FROM backtest_runs br
JOIN performance_metrics pm ON br.id = pm.backtest_run_id
WHERE pm.sharpe_ratio = (SELECT MAX(sharpe_ratio) FROM performance_metrics)
LIMIT 1;
```

### Paginated List Query

```sql
-- Already supported by BacktestRepository.find_recent()
-- Uses cursor-based pagination with (created_at, id) index
SELECT * FROM backtest_runs
ORDER BY created_at DESC, id DESC
LIMIT 20 OFFSET (page - 1) * 20;
```

---

## Notes

- All view models are Pydantic BaseModel subclasses
- No database migrations required
- View models enable serialization for potential future JSON API
- Decimal fields use `decimal_places=6` for consistency with DB schema
