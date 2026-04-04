# Web UI Patterns

## Architecture

- **Backend**: FastAPI + Jinja2 templates (`templates/`)
- **Frontend**: HTMX + Tailwind CSS (dark theme, slate-950 background)
- **Charts**: TradingView Lightweight Charts v5.0.0 (CDN)
- **CSS build**: `./scripts/build-css.sh` (required before first run)
- **Dev server**: `uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000`

## Dependency Injection Chain

`src/api/dependencies.py` implements a 3-level DI pattern:

```
get_db() → AsyncSession → Repository → QueryService
```

Type aliases for route signatures: `DbSession`, `BacktestRepo`, `BacktestService`.

```python
# Route handler example
async def list_backtests(service: BacktestService, request: Request):
    ...
```

## Route Organization

- **REST routes** (`src/api/rest/`): Return Pydantic response models (JSON)
- **UI routes** (`src/api/ui/`): Return `HTMLResponse` via `templates.TemplateResponse()`
- Always include `request` in template context dict
- `NavigationState` context object for all page templates (sets `active_page`, `breadcrumbs`)

## HTMX Patterns

**Fragment templates**: `backtests/list_fragment.html` — used for partial page updates.
- Fragment templates must NOT include `<html>`/`<body>` tags (HTMX replaces content only)
- Target swaps use container IDs (e.g., `#backtest-table`) that must match template `id` attributes
- Sort headers use Jinja2 macros (e.g., `sort_header()`) with `hx-get` + `sort_base_params`

**Filter state preservation**: All pagination, sorting, and filtering via HTMX must reconstruct URL query strings to maintain state. Filter enums must serialize correctly.

**Backtest execution lock**: `_backtest_lock` (asyncio.Lock) prevents concurrent backtest execution per-process. Note: does NOT prevent concurrency across multiple uvicorn workers.

**Console suppression**: `_quiet_console = Console(quiet=True)` in backtests.py suppresses Rich output in web context.

## Template Hierarchy

```
templates/
├── base.html              # Root: HTMX, TradingView lib, chart modules, block structure
├── partials/
│   ├── nav.html           # Navigation bar
│   ├── breadcrumbs.html   # Navigation context (requires NavigationState)
│   ├── metrics_panel.html # Metrics display with formatting
│   ├── trades_table.html  # Individual trade rows
│   └── config_snapshot.html  # JSONB config display
├── backtests/
│   ├── list.html          # Full page: backtest list
│   ├── list_fragment.html # HTMX fragment: paginated/sorted table
│   └── detail.html        # Backtest detail view
└── dashboard.html         # Home page
```

Block structure in `base.html`: `head`, `nav`, `breadcrumbs`, `content`, `footer`.

## Chart Module System

JavaScript modules loaded in strict dependency order (see `base.html`):

1. `charts-core.js` — `CHART_COLORS`, `TIMEFRAME_LABELS`, `createChartWithDefaults()`
2. `charts-price.js` — Candlestick and volume rendering
3. `charts-equity.js` — Equity curve rendering
4. `charts-statistics.js` — Statistical overlays (indicators, bands)
5. `charts.js` — Main orchestrator

`CHART_COLORS` matches Tailwind dark theme. Query string versioning (`?v=2`) for cache busting.

## Presentation Models

`src/api/models/` — transform DB objects for display:

- **`MetricDisplayItem`**: `@computed_field` for `formatted_value` (type-aware: %, $, decimal) and `color_class` (Tailwind CSS based on `is_favorable`)
- **Chart data**: ISO → Unix timestamp conversion for TradingView; `calculate_drawdown()` for equity→drawdown%; volume as integer
- **Filter models**: `ExecutionStatus`, `SortColumn`, `SortOrder` enums; `FilterState` preserves context across pagination
- **Conversion functions**: `to_list_item()`, `to_detail_view()` extract only needed fields from ORM objects

Date filters use `.isoformat()` in URLs but template rendering expects datetime objects.
