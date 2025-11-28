# Implementation Plan: Enhanced Price Plot with Trade Markers and Indicators

**Branch**: `010-enhanced-price-plot` | **Date**: 2025-01-27 | **Spec**: [specs/010-enhanced-price-plot/spec.md](./spec.md)
**Input**: Feature specification from `/specs/010-enhanced-price-plot/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Enhance the backtest detail page price chart to visualize trade execution points and strategy indicators. The feature adds visual markers for trade entries/exits on the price chart and overlays technical indicators (SMA, Bollinger Bands, RSI) used by the strategy. This enables users to understand the relationship between price movements, indicator signals, and trading decisions, making strategy behavior transparent and actionable for optimization.

**Technical Approach**: Frontend JavaScript using TradingView Lightweight Charts library with REST API endpoints providing trade markers, indicator series, and OHLCV data from existing PostgreSQL database and Parquet catalog.

## Technical Context

**Language/Version**: Python 3.11+ (backend), JavaScript ES6+ (frontend)
**Primary Dependencies**:
- Backend: FastAPI 0.109+, Pydantic 2.5+, SQLAlchemy 2.0 (async), structlog
- Frontend: TradingView Lightweight Charts, HTMX 1.9+, Tailwind CSS
**Storage**: PostgreSQL 16+ (backtest metadata, trades), Parquet files (OHLCV market data via Nautilus catalog)
**Testing**: pytest 7.4+ with async support, Playwright for UI validation
**Target Platform**: Web browser (Chrome 90+, Firefox 88+, Safari 14+)
**Project Type**: Web application (existing FastAPI backend + Jinja2 frontend)
**Performance Goals**:
- Chart renders <1 second for 100k data points
- Trade markers load <500ms for 1000 trades
- Indicator overlays render <1 second for multiple series
- Smooth interactions (30+ FPS) during zoom/pan
**Constraints**:
- Must work with existing TradingView Lightweight Charts integration
- Read-only visualization (no chart drawing tools or annotations)
- Indicators pre-calculated during backtest (no real-time computation)
- Data fetched via existing REST APIs (/api/trades, /api/indicators, /api/timeseries)
**Scale/Scope**:
- Support backtests with up to 100k bars and 1000 trades
- 4 strategy types with different indicator combinations
- Multiple indicator series per chart (2-5 indicators typical)
- Responsive to browser viewport changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (KISS/YAGNI) ✅ PASS
- **Implementation**: Uses existing TradingView Lightweight Charts library (already integrated)
- **Data Sources**: Leverages existing REST APIs (/api/trades, /api/indicators, /api/timeseries)
- **No New Dependencies**: Frontend only adds chart configuration JavaScript (no new npm packages required)
- **Rationale**: Building on proven libraries and existing infrastructure minimizes complexity

### II. Test-Driven Development (NON-NEGOTIABLE) ✅ CONDITIONAL PASS
- **Approach**: Write integration tests using Playwright for UI validation before implementation
- **Backend Tests**: Existing API endpoints already have pytest coverage (specs/008-chart-apis)
- **Frontend Tests**: Will add Playwright tests for chart rendering, marker display, indicator overlays
- **Commitment**: TDD workflow mandatory - tests written before chart JavaScript implementation

### III. FastAPI-First Architecture ✅ PASS
- **Backend**: All new endpoints use existing FastAPI patterns with async/await
- **APIs Already Exist**: /api/trades/{run_id}, /api/indicators/{run_id}, /api/timeseries
- **Models**: Pydantic models already defined (TradesResponse, IndicatorsResponse, TimeseriesResponse)
- **No Backend Changes Required**: Feature is purely frontend visualization using existing APIs

### IV. Type Safety & Documentation ✅ PASS
- **Backend**: Existing APIs have Pydantic models with full type hints
- **Frontend**: Will use JSDoc comments for chart configuration functions
- **OpenAPI Docs**: Existing endpoints already documented via FastAPI automatic generation
- **Rationale**: Type safety maintained through Pydantic validation on API responses

### V. Dependency Discipline ✅ PASS
- **No New Python Dependencies**: Uses existing FastAPI, SQLAlchemy, Pydantic stack
- **TradingView Lightweight Charts**: Already integrated (CDN link in templates)
- **UV Management**: Any future dependencies added via `uv add` only
- **Rationale**: Leveraging existing tech stack, no new package additions required

### VI. Fail Fast & Observable ✅ PASS
- **API Errors**: 404 if backtest not found, 422 for validation errors
- **Logging**: Use existing structlog for any server-side issues
- **Frontend Errors**: Chart rendering failures logged to browser console
- **Validation**: API responses validated against Pydantic models before chart rendering

### VII. DRY & Modular Design ✅ PASS
- **Chart Module**: Reusable chart initialization function for all backtest detail pages
- **Marker Rendering**: Common function for rendering buy/sell markers
- **Indicator Overlay**: Modular functions for each indicator type (SMA, Bollinger, RSI)
- **File Size Limits**: JavaScript chart module <500 lines, functions <50 lines each

**OVERALL GATE STATUS**: ✅ **PASS** - All constitution principles satisfied. Feature aligns with existing architecture and conventions.

## Project Structure

### Documentation (this feature)

```text
specs/010-enhanced-price-plot/
├── spec.md              # Feature specification (User Stories 1-2)
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (TradingView Lightweight Charts patterns)
├── data-model.md        # Phase 1 output (Chart entities and rendering logic)
├── quickstart.md        # Phase 1 output (Developer guide for chart integration)
├── contracts/           # Phase 1 output (API contracts - already exist in specs/008)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

**Selected Structure**: Web Application (FastAPI backend + Jinja2/HTMX frontend)

```text
src/
├── api/
│   ├── rest/
│   │   ├── indicators.py        # ✅ EXISTS - GET /indicators/{run_id}
│   │   ├── timeseries.py        # ✅ EXISTS - GET /timeseries
│   │   └── trades.py            # ✅ EXISTS - GET /trades/{run_id}
│   ├── ui/
│   │   └── backtests.py         # ✅ EXISTS - Backtest detail page template
│   ├── models/
│   │   ├── chart_indicators.py  # ✅ EXISTS - IndicatorsResponse model
│   │   ├── chart_timeseries.py  # ✅ EXISTS - TimeseriesResponse model
│   │   └── chart_trades.py      # ✅ EXISTS - TradesResponse model
│   └── web.py                   # ✅ EXISTS - FastAPI app with routers
│
├── templates/
│   └── backtests/
│       └── detail.html          # 🔨 MODIFY - Add chart container and JavaScript
│
└── static/                      # 🔨 NEW DIRECTORY
    └── js/
        └── chart-enhanced.js    # 🔨 NEW - Chart rendering logic
                                 #    - initializeChart()
                                 #    - renderTradeMarkers()
                                 #    - overlayIndicators()
                                 #    - handleMarkerTooltips()

tests/
├── integration/
│   └── ui/
│       └── test_enhanced_chart.py  # 🔨 NEW - Playwright UI tests
│                                    #    - test_trade_markers_display()
│                                    #    - test_indicator_overlay()
│                                    #    - test_marker_tooltips()
│                                    #    - test_zoom_pan_persistence()
└── unit/
    └── api/
        └── test_chart_apis.py      # ✅ EXISTS - API endpoint tests
```

**Structure Decision**:

This feature follows the existing web application architecture with:
- **Backend**: Existing FastAPI REST APIs provide all necessary data (no modifications needed)
- **Frontend**: JavaScript module added to `/src/static/js/` for chart enhancements
- **Templates**: Modify existing `detail.html` template to include chart container and load new JS module
- **Tests**: Playwright integration tests for UI validation (TDD approach)

## Complexity Tracking

**No Violations** - Constitution Check passed all gates. No complexity tracking required for this feature.
