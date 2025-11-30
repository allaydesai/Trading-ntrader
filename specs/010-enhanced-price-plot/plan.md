# Implementation Plan: Enhanced Price Plot with Trade Markers

**Branch**: `010-enhanced-price-plot` | **Date**: 2025-01-27 | **Updated**: 2025-01-30 | **Spec**: [specs/010-enhanced-price-plot/spec.md](./spec.md)
**Input**: Feature specification from `/specs/010-enhanced-price-plot/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Enhance the backtest detail page price chart to visualize trade execution points. The feature adds visual markers for trade entries (green upward triangles) and exits (red downward triangles) on the price chart with detailed tooltips showing trade type, price, quantity, timestamp, and P&L. This enables users to understand the relationship between price movements and trading decisions.

**Scope Decision**: After analysis, indicator overlays were deferred as they add significant complexity with diminishing returns. Trade markers alone provide the 80/20 value - users can see where the strategy traded relative to price movements, which is the core insight needed. External tools (TradingView, etc.) better serve indicator visualization.

**Technical Approach**: Frontend JavaScript using TradingView Lightweight Charts library with REST API endpoints providing trade markers and OHLCV data from existing PostgreSQL database and Parquet catalog.

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
- Smooth interactions (30+ FPS) during zoom/pan
**Constraints**:
- Must work with existing TradingView Lightweight Charts integration
- Read-only visualization (no chart drawing tools or annotations)
- Data fetched via existing REST APIs (/api/trades, /api/timeseries)
**Scale/Scope**:
- Support backtests with up to 100k bars and 1000 trades
- Responsive to browser viewport changes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### I. Simplicity First (KISS/YAGNI) PASS
- **Implementation**: Uses existing TradingView Lightweight Charts library (already integrated)
- **Data Sources**: Leverages existing REST APIs (/api/trades, /api/timeseries)
- **No New Dependencies**: Frontend only adds chart configuration JavaScript (no new npm packages required)
- **Scope Reduction**: Deferred indicator overlays to keep focus on core value (trade markers)
- **Rationale**: Building on proven libraries and existing infrastructure minimizes complexity

### II. Test-Driven Development (NON-NEGOTIABLE) CONDITIONAL PASS
- **Approach**: Write integration tests using Playwright for UI validation before implementation
- **Backend Tests**: Existing API endpoints already have pytest coverage (specs/008-chart-apis)
- **Frontend Tests**: Will add Playwright tests for chart rendering, marker display
- **Commitment**: TDD workflow mandatory - tests written before chart JavaScript implementation

### III. FastAPI-First Architecture PASS
- **Backend**: All endpoints use existing FastAPI patterns with async/await
- **APIs Already Exist**: /api/trades/{run_id}, /api/timeseries
- **Models**: Pydantic models already defined (TradesResponse, TimeseriesResponse)
- **No Backend Changes Required**: Feature is purely frontend visualization using existing APIs

### IV. Type Safety & Documentation PASS
- **Backend**: Existing APIs have Pydantic models with full type hints
- **Frontend**: Will use JSDoc comments for chart configuration functions
- **OpenAPI Docs**: Existing endpoints already documented via FastAPI automatic generation
- **Rationale**: Type safety maintained through Pydantic validation on API responses

### V. Dependency Discipline PASS
- **No New Python Dependencies**: Uses existing FastAPI, SQLAlchemy, Pydantic stack
- **TradingView Lightweight Charts**: Already integrated (CDN link in templates)
- **UV Management**: Any future dependencies added via `uv add` only
- **Rationale**: Leveraging existing tech stack, no new package additions required

### VI. Fail Fast & Observable PASS
- **API Errors**: 404 if backtest not found, 422 for validation errors
- **Logging**: Use existing structlog for any server-side issues
- **Frontend Errors**: Chart rendering failures logged to browser console
- **Validation**: API responses validated against Pydantic models before chart rendering

### VII. DRY & Modular Design PASS
- **Chart Module**: Reusable chart initialization function for all backtest detail pages
- **Marker Rendering**: Common function for rendering buy/sell markers
- **File Size Limits**: JavaScript chart module <500 lines, functions <50 lines each

**OVERALL GATE STATUS**: **PASS** - All constitution principles satisfied. Feature aligns with existing architecture and conventions.

## Project Structure

### Documentation (this feature)

```text
specs/010-enhanced-price-plot/
├── spec.md              # Feature specification (User Story 1 only)
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (TradingView Lightweight Charts patterns)
├── data-model.md        # Phase 1 output (Chart entities and rendering logic)
├── quickstart.md        # Phase 1 output (Developer guide for chart integration)
└── tasks.md             # Phase 2 output (/speckit.tasks command)
```

### Source Code (repository root)

**Selected Structure**: Web Application (FastAPI backend + Jinja2/HTMX frontend)

```text
src/
├── api/
│   ├── rest/
│   │   ├── timeseries.py        # EXISTS - GET /timeseries
│   │   └── trades.py            # EXISTS - GET /trades/{run_id}
│   ├── ui/
│   │   └── backtests.py         # EXISTS - Backtest detail page template
│   ├── models/
│   │   ├── chart_timeseries.py  # EXISTS - TimeseriesResponse model
│   │   └── chart_trades.py      # EXISTS - TradesResponse model
│   └── web.py                   # EXISTS - FastAPI app with routers
│
├── templates/
│   └── backtests/
│       └── detail.html          # MODIFY - Add chart container and JavaScript
│
└── static/
    └── js/
        └── charts.js            # MODIFY - Add trade marker rendering
                                 #    - renderTradeMarkers()
                                 #    - handleMarkerTooltips()

tests/
├── integration/
│   └── ui/
│       └── test_enhanced_chart.py  # NEW - Playwright UI tests
│                                    #    - test_trade_markers_display()
│                                    #    - test_marker_tooltips()
│                                    #    - test_zoom_pan_persistence()
└── unit/
    └── api/
        └── test_chart_apis.py      # EXISTS - API endpoint tests
```

**Structure Decision**:

This feature follows the existing web application architecture with:
- **Backend**: Existing FastAPI REST APIs provide all necessary data (no modifications needed)
- **Frontend**: JavaScript enhancements added to existing `/static/js/charts.js`
- **Templates**: Existing `detail.html` template already has chart container
- **Tests**: Playwright integration tests for UI validation (TDD approach)

## Complexity Tracking

**No Violations** - Constitution Check passed all gates. Scope simplified to trade markers only.

## Deferred Functionality

The following was explicitly deferred to reduce complexity:

- **Indicator Overlays** (User Story 2): SMA, Bollinger Bands, RSI rendering
- **Indicator Visibility Toggles**: Show/hide controls for individual indicators
- **Separate Indicator Panes**: RSI pane below price chart
- **Indicator Tooltips**: Hover to see indicator values
- **Chart Legend**: Indicator identification by color/name

**Rationale**: Indicator visualization is a well-solved problem by external tools. Trade markers provide the core insight needed for backtest analysis.
