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

## Sealed Paper Sessions in These Views (Phase 3, frozen)

A sealed paper session (`ntrader live seal`, Epic 5) is an ordinary `backtest_runs` row with
`run_type = 'paper'`, so every view here shows it unchanged. No query filters on `run_type`, and
nothing in this package or `templates/` reads it. This covers the list and its fragment, the
dashboard, the detail page and every JSON endpoint its charts call. The comparison view is the CLI
`ntrader backtest compare` (`src/cli/commands/compare.py`); there is no web compare route.

- **Frozen for Phase 3 (AR44).** `src/api/**`, `templates/**`, `src/services/backtest_query.py` and
  `src/cli/commands/compare.py` are pinned byte for byte by
  `tests/unit/governance/test_ar44_frozen_views.py`. A changed, added or removed file goes red.
  Only a recorded PO decision may regenerate the manifest (Story 5.6, ruling R2b). `static/js` and
  the CLI's `show`/`history` are not pinned.
- **Proof**: `tests/integration/db/test_sealed_record_in_existing_views.py` and
  `test_sealed_record_in_cli_views.py` seal a session beside its linked backtest in Postgres and read
  both through the real app and commands. `_sealed_record.py` builds the pair. The trades table
  route calls `http://localhost:8000` itself; the test answers that call in-process by patching
  `httpx.AsyncClient` onto an `ASGITransport`.
- **Known presentation limits (accepted, PO ruling R1a):**
  - The config panel renders `session_conditions` as one long "Session Conditions" row, including
    `equity_series.curve`, about one point per closed trade.
  - `ConfigurationSnapshot.cli_command` turns every top-level snapshot key into a flag, so a paper
    record's "CLI Command to Replicate" carries `--session-conditions {…}`. A backtest's carries
    `--strategy-path`/`--config` just as literally.
  - The price chart (`/api/timeseries/run/{run_id}`) reads the default Parquet catalog: FirstRate
    bars, adjusted. These may not cover the session's dates; the endpoint then degrades to no
    candles.
  - The equity chart is the trades-based `/api/equity-curve/{id}`, not the stored
    `equity_series.curve`, which nothing charts.
  - Nothing marks a row as paper in the list. `run_type` makes a "Paper" badge costless; that is
    Growth work.
