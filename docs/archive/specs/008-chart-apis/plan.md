# Implementation Plan: Chart APIs

**Branch**: `008-chart-apis` | **Date**: 2025-11-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-chart-apis/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement four JSON API endpoints for TradingView Lightweight Charts: OHLCV time series from Parquet files via DataCatalogService, trade markers from PostgreSQL backtest metadata, equity curves with drawdown calculations, and indicator series overlays. APIs follow existing FastAPI patterns with Pydantic validation and return JSON optimized for direct TradingView consumption.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI 0.109+, Pydantic 2.5+, SQLAlchemy 2.0 (async), structlog
**Storage**: PostgreSQL 16+ (backtest metadata), Parquet files (OHLCV market data)
**Testing**: pytest with pytest-asyncio, 80% minimum coverage
**Target Platform**: Linux server (local development macOS)
**Project Type**: Web application (existing backend API)
**Performance Goals**: Time series <500ms (100k candles), Trades/Equity <200ms, Indicators <300ms
**Constraints**: API response <500ms p95, TradingView-compatible JSON format
**Scale/Scope**: Single-user local development, ~4 new endpoints, reuses existing services

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status | Notes |
|-----------|-------------|--------|-------|
| **I. Simplicity (KISS/YAGNI)** | Simplest working solution, no speculative features | ✅ PASS | Reuses existing DataCatalogService and BacktestQueryService |
| **II. TDD (NON-NEGOTIABLE)** | Tests before implementation, 80% coverage | ✅ PASS | Will follow Red-Green-Refactor for each endpoint |
| **III. FastAPI-First** | Pydantic validation, async I/O, dependency injection | ✅ PASS | Follows existing patterns in src/api/dependencies.py |
| **IV. Type Safety** | Type hints, Google docstrings, mypy passes | ✅ PASS | All endpoints will have full type annotations |
| **V. Dependency Discipline** | UV only, pinned versions, no direct pyproject edits | ✅ PASS | No new dependencies required |
| **VI. Fail Fast** | Early validation, structured logging | ✅ PASS | Validates request params, uses structlog |
| **VII. DRY & Modular** | Functions <50 lines, classes <100 lines, files <500 lines | ✅ PASS | 4 small endpoint files, shared models |

**Gate Result**: ✅ PASS - No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/008-chart-apis/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── rest/                    # NEW: JSON API endpoints for charts
│   │   ├── __init__.py
│   │   ├── timeseries.py        # GET /api/timeseries - OHLCV data
│   │   ├── trades.py            # GET /api/trades/{run_id} - trade markers
│   │   ├── equity.py            # GET /api/equity/{run_id} - equity curve
│   │   └── indicators.py        # GET /api/indicators/{run_id} - indicator series
│   ├── models/
│   │   ├── chart_timeseries.py  # NEW: Pydantic models for OHLCV response
│   │   ├── chart_trades.py      # NEW: Pydantic models for trades response
│   │   ├── chart_equity.py      # NEW: Pydantic models for equity response
│   │   └── chart_indicators.py  # NEW: Pydantic models for indicators response
│   ├── dependencies.py          # EXTEND: Add data catalog service dependency
│   ├── ui/                      # EXISTING: HTML endpoints
│   └── web.py                   # EXTEND: Include rest/ routers
├── services/
│   ├── data_catalog.py          # REUSE: query_bars() for OHLCV
│   └── backtest_query.py        # REUSE: get_backtest_by_id() for metadata
└── models/
    └── trade.py                 # REUSE: TradeModel for trade markers

tests/
├── api/
│   └── rest/                    # NEW: Tests for JSON API endpoints
│       ├── test_timeseries.py
│       ├── test_trades.py
│       ├── test_equity.py
│       └── test_indicators.py
└── conftest.py                  # EXTEND: Add fixtures for chart API tests
```

**Structure Decision**: Extending existing single-project structure per Developer Guide. New JSON API endpoints go in `src/api/rest/` (separate from HTML UI in `src/api/ui/`). Response models in `src/api/models/chart_*.py`. Reuses existing DataCatalogService and BacktestQueryService.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. The design:
- Reuses existing services (DataCatalogService, BacktestQueryService)
- Adds minimal new code (4 endpoint files, 4 model files)
- Follows established patterns from Developer Guide
- No new dependencies required

---

## Post-Design Constitution Re-Check

| Principle | Status | Verification |
|-----------|--------|--------------|
| **I. Simplicity** | ✅ PASS | 4 endpoints, reuses existing services, ~50 lines per file |
| **II. TDD** | ✅ READY | Test structure defined in tests/api/rest/ |
| **III. FastAPI-First** | ✅ PASS | Follows patterns in src/api/ui/backtests.py |
| **IV. Type Safety** | ✅ PASS | All models have full type hints |
| **V. Dependencies** | ✅ PASS | No new dependencies needed |
| **VI. Fail Fast** | ✅ PASS | Pydantic validation + HTTPException pattern |
| **VII. Modular** | ✅ PASS | Each endpoint <100 lines estimated |

**Final Gate Result**: ✅ PASS - Ready for task generation

---

## Phase 5: Interactive Charts Implementation

**Date**: 2025-11-19 | **Depends On**: Phase 4 Chart APIs (completed)

### Summary

Implement TradingView Lightweight Charts integration for the backtest detail page, consuming the Chart APIs (Phase 4) to display interactive price charts with indicators, trade markers, and equity curves.

### Technical Context

**Frontend**: TradingView Lightweight Charts 4.1.0 via CDN
**Integration**: JavaScript consuming FastAPI JSON endpoints
**Theme**: Dark mode (slate-950 background, slate-100 text)
**Deliverables**: `static/js/charts.js`, template updates

### Implementation Tasks

#### Task 1: Add TradingView Lightweight Charts Library
**File**: `templates/base.html`

- Add Lightweight Charts script from CDN before `charts.js`
- Add custom `charts.js` script reference
- Ensure proper loading order with `defer`

#### Task 2: Create Chart Container Elements
**File**: `templates/backtests/detail.html`

Add chart containers with data attributes:
- **Price Chart**: `div#run-price-chart` with `data-chart="run-price"`, `data-run-id`, `data-symbol`, `data-start`, `data-end`
- **Equity Chart**: `div#run-equity-chart` with `data-chart="run-equity"`, `data-run-id`
- Place between Trading Summary and Configuration Snapshot
- Only show for successful backtests (`view.is_successful`)

#### Task 3: Implement charts.js Module
**File**: `static/js/charts.js`

##### 3a. Core Bootstrap
```javascript
document.addEventListener("DOMContentLoaded", initCharts);
document.body.addEventListener("htmx:afterSwap", handleHtmxSwap);
```

##### 3b. initRunPriceChart(el)
- Fetch `/api/timeseries?symbol=...&start=...&end=...&timeframe=1_DAY`
- Fetch `/api/trades/{run_id}` and `/api/indicators/{run_id}` in parallel
- Create candlestick chart with dark theme colors
- Add SMA indicator line series (if available)
- Add trade markers (green arrows=buy, red arrows=sell)
- Configure zoom/pan controls

##### 3c. initEquityChart(el)
- Fetch `/api/equity/{run_id}`
- Create line chart for equity curve
- Add area series for drawdown (negative percentages)
- Style with dark theme colors

##### 3d. Helper Functions
- `createChartWithDefaults(el)` - common chart config
- Error handling for failed API calls
- Loading state management

#### Task 4: Dark Theme Chart Styling

Configure Lightweight Charts to match Tailwind dark theme:
- Background: `#020617` (slate-950)
- Grid lines: `#1e293b` (slate-800)
- Text: `#e5e7eb` (slate-100)
- Bullish candles: `#22c55e` (green-500)
- Bearish candles: `#ef4444` (red-500)
- SMA fast: `#3b82f6` (blue-500)
- SMA slow: `#f59e0b` (amber-500)

#### Task 5: Loading States & Error Handling

- Show loading spinner while fetching chart data
- Display user-friendly error messages if API calls fail
- Handle empty data gracefully (no trades, no indicators)

#### Task 6: Write Tests
**File**: `tests/ui/test_charts.py`

- Test that detail page includes chart containers
- Test that chart data attributes are correct
- Integration tests for chart API calls

### Project Structure Updates

```text
templates/
├── base.html                    # MODIFIED: Add Lightweight Charts CDN + charts.js
└── backtests/
    └── detail.html              # MODIFIED: Add chart container divs

static/
└── js/
    └── charts.js                # NEW: Chart initialization module

tests/
└── ui/
    └── test_charts.py           # NEW: Chart integration tests
```

### Acceptance Criteria

- [ ] Charts render 1 year of 1-minute data in < 2 seconds
- [ ] Zoom/pan interactions feel smooth (60 fps)
- [ ] Trade markers align precisely with timestamps
- [ ] Charts work correctly after HTMX swaps
- [ ] Dark theme colors match rest of UI
- [ ] Graceful handling of missing indicators/trades

### API Endpoints Consumed

| Endpoint | Purpose |
|----------|---------|
| `GET /api/timeseries` | OHLCV candlestick data |
| `GET /api/trades/{run_id}` | Trade markers (buy/sell) |
| `GET /api/indicators/{run_id}` | SMA indicator overlays |
| `GET /api/equity/{run_id}` | Equity curve + drawdown |

### Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Simplicity** | ✅ PASS | Single JS file, follows Developer Guide patterns |
| **II. TDD** | ✅ PASS | Tests for template structure and API integration |
| **III. Minimal JS** | ✅ PASS | ~300 lines, no frameworks |
| **IV. Reuse** | ✅ PASS | Consumes existing Phase 4 APIs |

**Gate Result**: ✅ PASS - Ready for implementation
