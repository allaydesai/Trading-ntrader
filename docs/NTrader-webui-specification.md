# Product Specification: NTrader Web UI

**Version**: 1.0  
**Date**: November 2025  
**Feature**: Web-Based User Interface for Backtesting System  
**Status**: Planning Phase

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Problem Statement & Solution](#problem-statement--solution)
3. [Objectives & Success Metrics](#objectives--success-metrics)
4. [User Personas & Use Cases](#user-personas--use-cases)
5. [Functional Requirements](#functional-requirements)
6. [Non-Functional Requirements](#non-functional-requirements)
7. [Technical Architecture](#technical-architecture)
8. [User Interface Design](#user-interface-design)
9. [User Workflows](#user-workflows)
10. [API Specifications](#api-specifications)
11. [Implementation Phases](#implementation-phases)
12. [Testing Strategy](#testing-strategy)
13. [Dependencies & Integration Points](#dependencies--integration-points)
14. [Risks & Mitigation](#risks--mitigation)
15. [Future Enhancements](#future-enhancements)

---

## Executive Summary

### Overview
The NTrader Web UI adds a browser-based interface to the existing CLI-driven backtesting system, enabling visual exploration of backtest results, interactive chart analysis, and simplified workflow management without replacing the robust command-line functionality.

### Key Deliverables
- **Server-rendered HTML application** using FastAPI + Jinja2 templates
- **Interactive dashboards** for backtest history, results analysis, and data catalog viewing
- **Real-time charting** with TradingView Lightweight Charts for OHLC, indicators, trades, and equity curves
- **Dynamic filtering and sorting** via HTMX without full page reloads
- **Comparison views** for side-by-side backtest analysis
- **Data catalog explorer** for visualizing available market data coverage

### Design Philosophy
- **CLI-First Mindset**: UI complements rather than replaces existing CLI workflows
- **Minimal JavaScript**: Leverage server-side rendering with HTMX for interactivity
- **Progressive Enhancement**: Core functionality works without JavaScript, enhanced features require it
- **Single-User Focus**: Development tool interface, not multi-tenant SaaS
- **Incremental Adoption**: UI features added progressively as backend capabilities mature

### Success Criteria
✓ All existing CLI functionality remains unchanged and fully operational  
✓ Backtest history browsable with filter/sort/pagination under 200ms response time  
✓ Charts render complete 1-year dataset within 2 seconds  
✓ UI maintained with same TDD standards as backend (80%+ test coverage)  
✓ No external dependencies beyond Python ecosystem (HTMX, Tailwind, Lightweight Charts via CDN)  
✓ Zero data duplication between CLI and UI workflows

---

## Problem Statement & Solution

### Current State
The NTrader backtesting system (Milestones 1-4 complete) provides:
- Robust CLI interface for running backtests
- PostgreSQL persistence of backtest metadata and results
- Parquet storage for market data (single source of truth)
- Three implemented strategies: SMA Crossover, Mean Reversion, Momentum
- Comprehensive performance analytics and reporting (HTML, CSV, JSON)
- 106 passing tests with strong architectural foundations

**Pain Points:**
1. **CLI-Only Interaction**: Exploring backtest history requires memorizing command syntax
2. **Static Reports**: HTML reports are one-way outputs; no interactivity for deeper analysis
3. **No Visual Comparison**: Comparing multiple backtests requires manual work with CSV exports
4. **Data Discovery Gap**: Determining available market data coverage requires database queries
5. **Steep Learning Curve**: New users must learn CLI commands before first successful backtest
6. **Limited Chart Interactivity**: Static PNG equity curves lack zoom, pan, or multi-timeframe views

### Proposed Solution
A lightweight web interface that:
- **Preserves CLI Workflows**: UI calls same backend services as CLI, zero redundancy
- **Enables Visual Exploration**: Browse backtest history with instant filtering and sorting
- **Provides Interactive Charts**: Zoom, pan, and overlay indicators on OHLC data with trade markers
- **Simplifies Comparison**: Select multiple backtests for side-by-side metric comparison
- **Improves Data Discovery**: Visual calendar showing available data coverage by symbol/timeframe
- **Lowers Barrier to Entry**: New users can run first backtest via form submission

### Out of Scope (Current Phase)
- Real-time backtest execution monitoring (show progress bars in CLI)
- Strategy code editing via browser (maintain file-based strategy development)
- Live trading controls (future phase, not backtesting UI scope)
- Multi-user authentication/authorization (single-user development tool)
- Cloud deployment infrastructure (local-first design)
- Mobile-optimized interface (desktop-first for trading analysis)

---

## Objectives & Success Metrics

### Primary Objectives

**PO-1: Complement CLI Without Replacement**
- Maintain 100% backward compatibility with existing CLI commands
- UI features call identical backend service layer as CLI
- No UI-specific business logic; all logic in reusable services
- Success Metric: Zero CLI functionality removed or broken by UI addition

**PO-2: Enable Visual Data Exploration**
- Backtest history browsable via web interface with filtering/sorting
- Interactive charts for price action, indicators, trades, and equity curves
- Data catalog viewer showing coverage gaps and available date ranges
- Success Metric: 90% of backtest result exploration tasks completable via UI

**PO-3: Improve Developer Experience**
- Reduce time-to-first-backtest for new users from 30 minutes to 5 minutes
- Provide visual feedback for common errors (missing data, invalid parameters)
- Enable quick comparison of strategy variations without manual CSV parsing
- Success Metric: 80% reduction in "how do I..." questions from new users

**PO-4: Maintain Code Quality Standards**
- Apply same TDD practices to UI code as backend
- 80%+ test coverage on API routes and HTML rendering logic
- Type-safe Pydantic models for all API request/response schemas
- Success Metric: UI code passes all quality gates (tests, linting, type checking)

### Key Performance Indicators (KPIs)

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| Backtest list page load | < 300ms (20 results) | Browser DevTools Network tab |
| Chart rendering (1 year 1m data) | < 2 seconds | JavaScript performance.now() |
| Filter/sort response time | < 200ms | HTMX request duration |
| Compare view (10 backtests) | < 2 seconds | Server-side timing logs |
| Data catalog query | < 500ms | Database query execution time |
| Full test suite execution | < 5 minutes | CI/CD pipeline duration |
| UI test coverage | > 80% | pytest-cov report |

---

## User Personas & Use Cases

### Persona 1: Quantitative Developer (Primary)
**Name**: Alex, Senior Quant Developer  
**Background**: 5+ years Python experience, comfortable with CLI, builds algorithmic trading strategies  
**Goals**: Validate strategies quickly, compare parameter variations, identify edge cases  
**Pain Points**: CLI is efficient but tedious for exploratory analysis and comparisons  

**Use Cases:**
- **UC-1.1**: Browse all backtests run in past week to identify which strategy variants performed best
- **UC-1.2**: Compare SMA crossover with fast_period [10, 15, 20] side-by-side to find optimal parameter
- **UC-1.3**: Examine specific trades on price chart to understand why strategy entered position
- **UC-1.4**: Check data coverage before starting backtest to avoid errors mid-execution
- **UC-1.5**: Export filtered backtest results to share with team lead for review

### Persona 2: Portfolio Manager (Secondary)
**Name**: Jordan, Portfolio Manager  
**Background**: Finance background, limited Python, evaluates systematic strategies for fund  
**Goals**: Understand strategy performance, risk metrics, and edge cases without writing code  
**Pain Points**: Needs to review backtest results but prefers visual interface over CLI  

**Use Cases:**
- **UC-2.1**: Review latest backtest results submitted by quant team
- **UC-2.2**: Filter backtests by instrument (e.g., only SPY) to assess index strategy performance
- **UC-2.3**: Identify backtests with Sharpe > 1.5 and drawdown < 15% for further review
- **UC-2.4**: Compare equity curves of top 3 strategies to assess risk-adjusted performance
- **UC-2.5**: View trade distribution and win rate statistics visually

### Persona 3: New Developer Onboarding
**Name**: Sam, Junior Developer  
**Background**: Fresh CS graduate, basic Python knowledge, learning quantitative trading  
**Goals**: Understand how backtesting works, run first strategy successfully, build confidence  
**Pain Points**: Overwhelming CLI documentation, frequent syntax errors, unclear error messages  

**Use Cases:**
- **UC-3.1**: Run first SMA backtest via simple web form with visual parameter inputs
- **UC-3.2**: See example backtest results to understand expected output format
- **UC-3.3**: Browse existing backtests to learn from successful configurations
- **UC-3.4**: Visualize what OHLC data looks like before attempting to write strategy
- **UC-3.5**: Access quick links to CLI documentation and strategy examples

---

## Functional Requirements

### FR-1: Dashboard & Navigation

**FR-1.1: Home Dashboard**
- Display summary statistics: total backtests, best Sharpe ratio, worst drawdown
- Show recent activity: last 5 backtests with status indicators
- Provide quick action buttons: "Run New Backtest", "View All Results", "Check Data Coverage"
- Link to documentation and CLI reference
- **Priority**: High | **Complexity**: Low

**FR-1.2: Navigation Menu**
- Persistent navigation across all pages with active page highlighting
- Menu items: Dashboard, Backtests, Data Catalog, Strategies (future), Settings (future)
- Breadcrumb trail for nested pages (e.g., Backtests > Run Details)
- **Priority**: High | **Complexity**: Low

### FR-2: Backtest History & Management

**FR-2.1: Backtest List View**
- Display paginated table of all backtest runs (default: 20 per page, max: 100)
- Columns: Run ID (truncated UUID), Strategy, Instrument, Date Range, Total Return, Sharpe, Max DD, Status, Timestamp
- Support sorting by any column (ascending/descending toggle)
- Clickable rows navigate to backtest detail page
- **Priority**: High | **Complexity**: Medium

**FR-2.2: Advanced Filtering**
- Filter by strategy name (dropdown with all available strategies)
- Filter by instrument symbol (text input with autocomplete)
- Filter by date range (executed between start and end dates)
- Filter by status (success, failure, running)
- Filter by performance metrics (Sharpe > threshold, Return > X%, DD < Y%)
- HTMX partial updates for instant filter application without page reload
- Persist filters in URL query parameters for sharing and bookmarking
- **Priority**: High | **Complexity**: Medium

**FR-2.3: Bulk Actions**
- Select multiple backtests via checkboxes
- Bulk delete (with confirmation prompt)
- Bulk export to CSV (download combined metrics file)
- Bulk comparison view (redirect to comparison page)
- **Priority**: Medium | **Complexity**: Medium

### FR-3: Backtest Detail View

**FR-3.1: Summary Metrics Panel**
- Display all key metrics in organized sections:
  - Return Metrics: Total Return %, CAGR, Annualized Return
  - Risk Metrics: Sharpe Ratio, Sortino Ratio, Max Drawdown, Volatility
  - Trading Metrics: Total Trades, Win Rate, Avg Win/Loss, Profit Factor
  - Benchmark Comparison: Strategy vs SPY performance
- Color-coded indicators (green/red) for positive/negative metrics
- Tooltips explaining each metric calculation
- **Priority**: High | **Complexity**: Low

**FR-3.2: Configuration Snapshot**
- Display immutable strategy configuration used for this backtest
- Show all parameters: instrument, date range, initial capital, commission model, slippage, risk per trade
- Collapsible section to save space (expanded by default)
- Copy button for easy replication via CLI
- **Priority**: Medium | **Complexity**: Low

**FR-3.3: Trade Blotter Table**
- Display all trades with columns: Timestamp, Symbol, Side (Buy/Sell), Quantity, Entry Price, Exit Price (if closed), P&L, Status
- Sortable by any column
- Filter by trade status (open, closed, profitable, losing)
- Export to CSV button
- Pagination for backtests with 1000+ trades
- **Priority**: High | **Complexity**: Medium

**FR-3.4: Interactive Price Chart**
- Render OHLC candlestick chart using TradingView Lightweight Charts
- Overlay strategy indicators (e.g., SMA fast/slow lines)
- Mark trade entry/exit points with arrows (green=buy, red=sell)
- Hover tooltip showing OHLC values and indicator values at cursor position
- Zoom controls (mouse wheel) and pan (drag)
- Time range selector (all data, last year, last 6 months, last 3 months)
- **Priority**: High | **Complexity**: High

**FR-3.5: Equity Curve Chart**
- Line chart of portfolio value over time
- Optional drawdown overlay (inverted area chart below x-axis)
- Benchmark comparison line (SPY buy-and-hold)
- Highlight maximum drawdown period with shaded region
- Synchronized time axis with price chart (zoom/pan linked)
- **Priority**: High | **Complexity**: Medium

**FR-3.6: Action Buttons**
- "Re-run Backtest" - Execute backtest again with same configuration
- "Clone & Modify" - Prefill new backtest form with current config for parameter tweaking
- "Export Report" - Download HTML report (existing functionality)
- "Delete" - Remove backtest from history (with confirmation)
- **Priority**: Medium | **Complexity**: Low

### FR-4: Backtest Comparison View

**FR-4.1: Multi-Backtest Selection**
- Select 2-10 backtests from history page via checkboxes
- "Compare Selected" button triggers comparison view
- URL includes all run IDs for direct link sharing
- **Priority**: Medium | **Complexity**: Low

**FR-4.2: Side-by-Side Metrics Table**
- Display selected backtests in columns with metrics in rows
- Highlight best value in each row (green background)
- Highlight worst value in each row (red background)
- Sortable by any metric to identify best performer
- Percentage difference column showing deviation from best
- **Priority**: Medium | **Complexity**: Medium

**FR-4.3: Overlaid Equity Curves**
- Single chart with multiple equity curves (different colors per backtest)
- Legend with strategy name and final return for each curve
- Toggle visibility of individual curves via legend clicks
- Synchronized tooltips showing all portfolio values at cursor time
- **Priority**: Medium | **Complexity**: High

**FR-4.4: Configuration Diff View**
- Show parameter differences between compared backtests
- Highlight changed parameters in yellow
- Useful for identifying which parameter changes drove performance differences
- **Priority**: Low | **Complexity**: Medium

### FR-5: Data Catalog Viewer

**FR-5.1: Instrument & Timeframe Selector**
- Dropdown to select instrument (all symbols with data in catalog)
- Dropdown to select timeframe (1m, 5m, 1h, 1d)
- Date range picker (start and end dates)
- "Load Data" button triggers chart rendering
- **Priority**: Medium | **Complexity**: Low

**FR-5.2: Data Coverage Calendar**
- Visual heatmap showing data availability by date
- Color intensity indicates data quality (full day vs partial)
- Click on date to filter chart to that specific day
- Identify gaps in historical data coverage at a glance
- **Priority**: Low | **Complexity**: High

**FR-5.3: Raw OHLC Chart**
- Display requested data as candlestick chart
- Volume histogram below price chart
- No indicators or trade markers (raw data visualization only)
- Full zoom and pan controls
- **Priority**: Medium | **Complexity**: Medium

**FR-5.4: Data Statistics Panel**
- Show summary stats: total bars, date range, missing data count
- Display basic statistics: avg volume, price range, volatility estimate
- **Priority**: Low | **Complexity**: Low

### FR-6: New Backtest Form (Future Phase)

**FR-6.1: Strategy Selection**
- Dropdown listing all available strategies from codebase
- Display strategy description and required parameters
- **Priority**: Low (Future) | **Complexity**: Medium

**FR-6.2: Parameter Configuration**
- Dynamic form fields based on selected strategy
- Validation for each parameter (type, range checks)
- Default values pre-filled from strategy configuration
- **Priority**: Low (Future) | **Complexity**: High

**FR-6.3: Execution & Feedback**
- Submit button triggers backtest execution via backend API
- Real-time status updates (polling or SSE)
- Redirect to detail page upon completion
- Error display with actionable suggestions
- **Priority**: Low (Future) | **Complexity**: High

---

## Non-Functional Requirements

### NFR-1: Performance

**NFR-1.1: Page Load Performance**
- Dashboard renders in < 500ms
- Backtest list page (20 results) loads in < 300ms
- Backtest detail page loads in < 1 second (excluding chart data)
- Chart data fetching and rendering completes in < 2 seconds for 1 year of 1-minute data

**NFR-1.2: Scalability**
- Support 10,000+ backtests in database without UI degradation
- Pagination prevents loading excessive data into memory
- Chart rendering handles up to 100,000 candles efficiently (client-side decimation)

**NFR-1.3: Responsiveness**
- Filter/sort operations via HTMX return in < 200ms
- No full page reloads except for navigation to new pages
- Perceived performance: visual feedback (loading spinners) for operations > 300ms

### NFR-2: Reliability

**NFR-2.1: Fault Tolerance**
- Graceful degradation if JavaScript fails (core table views still functional)
- Clear error messages for API failures with retry suggestions
- Automatic reconnection if backend restarts during session

**NFR-2.2: Data Integrity**
- UI never modifies backtest results (read-only after creation except deletion)
- Deletion requires explicit confirmation to prevent accidents
- All state changes logged for audit trail

**NFR-2.3: Availability**
- FastAPI application restarts automatically on crash (systemd/Docker configuration)
- Static assets served with caching headers (1 hour for CSS/JS, 1 day for images)

### NFR-3: Maintainability

**NFR-3.1: Code Quality**
- All Python code fully typed with mypy validation
- Comprehensive docstrings with parameter descriptions and examples
- Adherence to project coding standards (CLAUDE.md constitution)
- Maximum function length: 50 lines
- Maximum template size: 200 lines (split into partials beyond this)

**NFR-3.2: Testing Standards**
- Unit tests for all service layer functions (pytest)
- Integration tests for FastAPI routes using TestClient
- E2E tests for critical user flows (Playwright, optional)
- 80%+ coverage on application logic (excluding templates)

**NFR-3.3: Documentation**
- Inline code comments for complex UI logic
- README with setup instructions for running UI locally
- Developer guide (NTrader_Web_UI_Developer_Guide.md) maintained and updated
- Changelog tracking UI feature additions

**NFR-3.4: Modularity**
- UI code isolated in `src/api/ui/` and `src/api/rest/` directories
- Shared services in `src/services/` used by both CLI and UI
- No UI-specific business logic; all logic in service layer
- Templates organized by feature (backtests/, data/, partials/)

### NFR-4: Usability

**NFR-4.1: Accessibility**
- Semantic HTML with proper heading hierarchy (h1 > h2 > h3)
- ARIA labels for interactive elements
- Keyboard navigation support for all actions
- Color contrast ratio meets WCAG AA standards (4.5:1 minimum)

**NFR-4.2: User Feedback**
- Loading indicators for asynchronous operations
- Success/error toast notifications for actions (delete, export, etc.)
- Disabled state for buttons during processing
- Clear error messages with recovery instructions

**NFR-4.3: Consistency**
- Uniform color scheme (dark mode default, consistent with trading platforms)
- Consistent button styles and interactive element behaviors
- Predictable layouts across all pages (same nav, same footer)

### NFR-5: Security

**NFR-5.1: Input Validation**
- All user inputs validated server-side with Pydantic models
- SQL injection prevention via parameterized queries (SQLAlchemy ORM)
- XSS protection via Jinja2 auto-escaping

**NFR-5.2: Authentication (Future)**
- Single-user mode initially (no auth required)
- Preparation for future basic auth with environment variable credentials
- HTTPS required for any non-localhost deployments (documentation requirement)

**NFR-5.3: Rate Limiting**
- Prevent API abuse with rate limiting on expensive endpoints (100 requests/minute per IP)
- Protect database from unbounded query attacks

---

## Technical Architecture

### Technology Stack

**Backend**
- **FastAPI 0.109+**: ASGI framework for both HTML and JSON APIs
- **Jinja2 3.1+**: Template engine for server-side HTML rendering
- **Pydantic 2.5+**: Request/response validation and serialization
- **SQLAlchemy 2.0+**: ORM for PostgreSQL queries (reuse existing models)
- **Existing Services**: Backtest history service, data catalog service, metric calculators

**Frontend**
- **HTMX 1.9+**: Partial page updates without full reloads
- **Tailwind CSS 3.4+**: Utility-first CSS framework for styling
- **TradingView Lightweight Charts 4.1+**: Performant financial charting library
- **Vanilla JavaScript/TypeScript**: Minimal custom JS for chart initialization (~300 lines)
- **No heavy frameworks**: No React, Vue, Angular (keeps stack simple)

**Infrastructure**
- **PostgreSQL 16+**: Existing database for metadata (no changes required)
- **Parquet Files**: Existing market data storage (read-only access from UI)
- **Uvicorn**: ASGI server for FastAPI application
- **Nginx (Optional)**: Reverse proxy for production deployments

### Project Structure

```
src/
├── api/
│   ├── __init__.py
│   ├── web.py                      # FastAPI app initialization
│   ├── ui/                         # HTML routes (Jinja2)
│   │   ├── __init__.py
│   │   ├── dashboard.py            # GET / (home dashboard)
│   │   ├── backtests.py            # Backtest list, detail, compare
│   │   ├── data_catalog.py         # Data viewer routes
│   │   └── strategies.py           # Future: strategy management
│   └── rest/                       # JSON APIs for charts
│       ├── __init__.py
│       ├── timeseries.py           # GET /api/timeseries
│       ├── indicators.py           # GET /api/indicators
│       ├── trades.py               # GET /api/trades
│       └── equity.py               # GET /api/equity
├── core/                           # Existing business logic (no changes)
├── services/                       # Existing services (reused)
├── db/                             # Existing SQLAlchemy models
└── utils/                          # Existing utilities

templates/
├── base.html                       # Base layout with nav/footer
├── dashboard.html                  # Home dashboard
├── backtests/
│   ├── list.html                   # Backtest history table
│   ├── list_fragment.html          # HTMX partial for table updates
│   ├── detail.html                 # Single backtest detail view
│   └── compare.html                # Side-by-side comparison
├── data/
│   └── catalog.html                # Data catalog viewer
└── partials/
    ├── nav.html                    # Navigation menu
    ├── footer.html                 # Footer links
    ├── metrics_panel.html          # Reusable metrics display
    └── loading_spinner.html        # Loading indicator

static/
├── css/
│   └── tailwind.css                # Compiled Tailwind styles
├── js/
│   └── charts.js                   # Chart initialization logic
└── vendor/                         # Third-party libraries
    ├── htmx.min.js                 # HTMX library
    └── lightweight-charts.*.js     # TradingView charts

tests/
└── ui/                             # UI-specific tests
    ├── test_dashboard.py           # Dashboard route tests
    ├── test_backtests.py           # Backtest views tests
    ├── test_data_catalog.py        # Data catalog tests
    └── test_chart_apis.py          # REST API tests
```

### Data Flow Architecture

```
Browser Request
    ↓
┌───────────────────────────────────────────────┐
│ FastAPI Application (src/api/web.py)         │
│                                               │
│ HTML Routes (ui/)     REST APIs (rest/)       │
│    ↓                      ↓                   │
│ Jinja2 Templates      JSON Serialization      │
│    ↓                      ↓                   │
│ ← HTML Response      ← JSON Response          │
└───────────────────────────────────────────────┘
    ↓                       ↓
Service Layer (src/services/)
    ↓
┌───────────────────┬─────────────────────────┐
│ PostgreSQL        │ Parquet Files           │
│ (Metadata)        │ (Market Data)           │
└───────────────────┴─────────────────────────┘
```

### API Endpoint Map

**HTML Endpoints (Server-Rendered)**
```
GET  /                           → Dashboard summary
GET  /backtests                  → Backtest list (full page)
GET  /backtests/fragment         → Backtest table (HTMX partial)
GET  /backtests/{run_id}         → Backtest detail
GET  /backtests/compare          → Comparison view
GET  /data/catalog               → Data catalog viewer
POST /backtests/{run_id}/rerun   → Trigger backtest re-execution
DELETE /backtests/{run_id}       → Delete backtest
```

**JSON Endpoints (for Charts)**
```
GET  /api/timeseries             → OHLC + volume data
     ?symbol=AAPL&start=2023-01-01&end=2023-12-31&timeframe=1_MIN

GET  /api/indicators             → Indicator series (SMA, RSI, etc.)
     ?run_id={uuid}

GET  /api/trades                 → Trade markers for chart
     ?run_id={uuid}

GET  /api/equity                 → Equity curve + drawdown
     ?run_id={uuid}
```

### Component Interaction Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     Browser (Client)                     │
│                                                          │
│  ┌────────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │   HTML     │  │   HTMX     │  │  Lightweight     │  │
│  │  Pages     │  │  Partials  │  │    Charts        │  │
│  └─────┬──────┘  └─────┬──────┘  └────────┬─────────┘  │
│        │               │                  │             │
└────────┼───────────────┼──────────────────┼─────────────┘
         │               │                  │
         │ HTTP          │ HTTP GET         │ HTTP GET
         │ GET           │ (fragments)      │ (JSON)
         ↓               ↓                  ↓
┌─────────────────────────────────────────────────────────┐
│              FastAPI Application Layer                   │
│                                                          │
│  ┌────────────────────┐    ┌────────────────────────┐   │
│  │   UI Routers       │    │   REST API Routers     │   │
│  │  (dashboard.py,    │    │  (timeseries.py,       │   │
│  │   backtests.py)    │    │   indicators.py)       │   │
│  └─────────┬──────────┘    └──────────┬─────────────┘   │
│            │                          │                  │
│            └──────────┬───────────────┘                  │
│                       │                                  │
│                       ↓                                  │
│            ┌──────────────────────┐                      │
│            │   Service Layer      │                      │
│            │  (services/)         │                      │
│            │  - BacktestService   │                      │
│            │  - MetricsService    │                      │
│            │  - DataCatalogSvc    │                      │
│            └──────────┬───────────┘                      │
└────────────────────────┼──────────────────────────────────┘
                         │
                         ↓
            ┌────────────────────────┐
            │   Data Persistence      │
            │                         │
            │  ┌──────────────────┐   │
            │  │   PostgreSQL     │   │
            │  │   (Metadata)     │   │
            │  └──────────────────┘   │
            │                         │
            │  ┌──────────────────┐   │
            │  │  Parquet Files   │   │
            │  │  (Market Data)   │   │
            │  └──────────────────┘   │
            └────────────────────────┘
```

---

## User Interface Design

### Design Principles

**1. Dark Mode First**
- Default color scheme: Dark background (#020617 slate-950) with light text (#e5e7eb slate-100)
- Matches typical trading platform aesthetics (reduces eye strain)
- Accent colors: Green (#22c55e) for positive metrics, Red (#ef4444) for negative

**2. Information Density**
- Prioritize dense information display for power users
- Use collapsible sections for secondary information
- Tables over cards for tabular data (more efficient space usage)

**3. Minimal Clicks**
- Most common actions accessible within 1-2 clicks from dashboard
- Inline actions (delete, export) directly in list views
- Breadcrumbs for quick navigation upward

**4. Responsive Feedback**
- Immediate visual response to all user actions (button state changes)
- Loading indicators for operations > 300ms
- Toast notifications for background operations

### Color Palette

```css
/* Backgrounds */
--bg-primary: #020617;    /* slate-950 - main background */
--bg-secondary: #0f172a;  /* slate-900 - panels */
--bg-tertiary: #1e293b;   /* slate-800 - elevated sections */

/* Text */
--text-primary: #e5e7eb;   /* slate-100 - main text */
--text-secondary: #94a3b8; /* slate-400 - muted text */
--text-tertiary: #64748b;  /* slate-500 - disabled text */

/* Accents */
--accent-green: #22c55e;   /* Positive metrics, buy trades */
--accent-red: #ef4444;     /* Negative metrics, sell trades */
--accent-blue: #3b82f6;    /* Interactive elements */
--accent-yellow: #eab308;  /* Warnings */

/* Borders */
--border-color: #1e293b;   /* slate-800 */
```

### Typography

```css
/* Font Stack */
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;

/* Sizes (Tailwind scale) */
--text-xs: 0.75rem;    /* 12px - labels, timestamps */
--text-sm: 0.875rem;   /* 14px - table data */
--text-base: 1rem;     /* 16px - body text */
--text-lg: 1.125rem;   /* 18px - section headings */
--text-xl: 1.25rem;    /* 20px - page titles */
--text-2xl: 1.5rem;    /* 24px - dashboard metrics */
```

### Layout Patterns

**Standard Page Layout**
```
┌──────────────────────────────────────────────────────┐
│ Navigation Bar (60px height)                         │
│ Logo | Dashboard | Backtests | Data | Docs           │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ Breadcrumb: Dashboard > Backtests > Detail           │
├──────────────────────────────────────────────────────┤
│                                                       │
│ Page Title (h1)                     [Action Buttons] │
│                                                       │
│ ┌───────────────────────────────────────────────┐   │
│ │                                               │   │
│ │           Main Content Area                   │   │
│ │           (max-width: 1280px, centered)       │   │
│ │                                               │   │
│ └───────────────────────────────────────────────┘   │
│                                                       │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ Footer: Docs | GitHub | Version 1.0                  │
└──────────────────────────────────────────────────────┘
```

### Key UI Components

**1. Backtest List Table**
```
┌─────────────────────────────────────────────────────────────────┐
│ Filters: [Strategy ▼] [Instrument: _____] [Date Range: __ __]  │
├─────┬──────────┬────────┬───────────┬────────┬────────┬─────────┤
│ ☐   │ Strategy │ Symbol │ Date Range│ Return │ Sharpe │ Actions │
├─────┼──────────┼────────┼───────────┼────────┼────────┼─────────┤
│ ☐   │ SMA      │ AAPL   │ 2023      │ +12.3% │  1.45  │ [View]  │
│ ☐   │ Mean Rev │ SPY    │ 2023      │  +8.7% │  1.12  │ [View]  │
│ ...                                                              │
└─────────────────────────────────────────────────────────────────┘
[< Prev]  Page 1 of 10  [Next >]
```

**2. Metrics Panel**
```
┌─────────────────────────────────────────────────────────┐
│ Performance Metrics                                     │
├─────────────────────────────────────────────────────────┤
│ Return Metrics                Risk Metrics              │
│ Total Return    +15.4% ▲      Sharpe Ratio      1.67 ▲  │
│ CAGR            +14.2% ▲      Max Drawdown     -12.3% ▼ │
│ Annualized      +14.8% ▲      Volatility        18.2%   │
│                                                         │
│ Trading Metrics               Profit Metrics            │
│ Total Trades       156        Profit Factor      2.34 ▲ │
│ Win Rate         62.8% ▲      Avg Win          $234.56  │
│ Winning Trades      98        Avg Loss        -$145.23  │
└─────────────────────────────────────────────────────────┘
```

**3. Interactive Chart**
```
┌──────────────────────────────────────────────────────────┐
│ AAPL - 1 Minute Bars                    [1Y][6M][3M][1M]│
│                                                          │
│  $180 ┤                        ╭╮                        │
│       │                     ╭──╯╰─╮                      │
│  $170 ┤    ╭───╮         ╭─╯      ╰──╮    SMA Fast ──   │
│       │ ╭─╯   ╰─╮     ╭─╯            ╰─╮  SMA Slow ──   │
│  $160 ┼─╯       ╰─────╯                ╰──              │
│       │   ▲Buy            ▼Sell                          │
│       │                                                  │
│       └──────────────────────────────────────────────────│
│ Volume                                                   │
│  100k ┤   ││  │││ │││    │││││  │││││                   │
│       └──────────────────────────────────────────────────│
│        Jan    Feb    Mar    Apr    May    Jun    Jul     │
└──────────────────────────────────────────────────────────┘
```

---

## User Workflows

### Workflow 1: Exploring Recent Backtests

**User Goal**: Review all backtests run in the past week

**Steps**:
1. Navigate to Dashboard (/)
2. Click "View All Backtests" link
3. System displays backtest list page (/backtests)
4. User applies date filter: "Last 7 Days" from dropdown
5. HTMX updates table without page reload (< 200ms)
6. User sorts by "Total Return" column (descending)
7. User clicks on best-performing backtest row
8. System navigates to detail view (/backtests/{run_id})
9. User reviews metrics and charts
10. User clicks "Export Report" to download HTML

**Success Criteria**:
- All filters apply instantly via HTMX
- Sorting updates table without full page reload
- Detail page loads with all metrics and charts visible
- Export generates same HTML report as CLI

### Workflow 2: Comparing Strategy Variations

**User Goal**: Determine optimal SMA fast period (10, 15, or 20)

**Steps**:
1. User navigates to /backtests
2. Applies filter: Strategy = "SMA Crossover"
3. Table shows all SMA backtests
4. User selects 3 backtests with fast_period [10, 15, 20] via checkboxes
5. User clicks "Compare Selected" button
6. System redirects to /backtests/compare?run_ids=uuid1,uuid2,uuid3
7. Comparison table displays all 3 backtests side-by-side
8. Best Sharpe (fast_period=15) highlighted in green
9. Equity curves overlaid on single chart with legend
10. User identifies fast_period=15 as optimal based on risk-adjusted return

**Success Criteria**:
- Checkbox selection persists during pagination
- Comparison view loads all data in < 2 seconds
- Equity curves render smoothly with distinct colors
- Metric highlights help identify best performer quickly

### Workflow 3: Checking Data Coverage Before Backtest

**User Goal**: Verify TSLA data available for 2024 before running backtest

**Steps**:
1. User navigates to Dashboard (/)
2. Clicks "Check Data Coverage" quick link
3. System navigates to /data/catalog
4. User selects:
   - Instrument: TSLA (dropdown)
   - Timeframe: 1 Minute (dropdown)
   - Date Range: 2024-01-01 to 2024-12-31 (date pickers)
5. User clicks "Load Data" button
6. System queries Parquet catalog for availability
7. Calendar heatmap displays: Full coverage Jan-Nov, missing December
8. Raw OHLC chart renders Jan-Nov data
9. Statistics panel shows: "11/12 months available, 245 trading days"
10. User notes need to fetch December data before backtest

**Success Criteria**:
- Calendar clearly indicates missing data
- Chart renders even with partial coverage
- Statistics provide actionable information
- User can proceed to data fetch CLI command with confidence

### Workflow 4: Analyzing Specific Trades (Debugging)

**User Goal**: Understand why strategy entered losing trade on specific date

**Steps**:
1. User navigates to backtest detail (/backtests/{run_id})
2. Scrolls to Trade Blotter table
3. Sorts by P&L (ascending) to find worst trades
4. Identifies worst losing trade: -$543.21 on 2023-05-15
5. User notes trade timestamp and price
6. Scrolls to Price Chart section
7. Chart shows full year, user zooms to May 2023 using mouse wheel
8. User hovers over May 15 candle, sees trade marker (red arrow)
9. Indicator overlay shows SMA slow crossed SMA fast (sell signal)
10. User identifies false breakout: price gapped down at open, strategy sold, price recovered
11. User notes need to add volatility filter to strategy to avoid gap trading

**Success Criteria**:
- Trade blotter sorting works correctly
- Chart zoom responds smoothly to mouse wheel
- Trade marker aligns precisely with timestamp
- Indicator values visible in hover tooltip
- User gains actionable insight for strategy improvement

---

## API Specifications

### REST API Endpoints

**GET /api/timeseries**

Retrieve OHLCV time series data for charting.

**Request Parameters**:
```
symbol: str         # Instrument symbol (e.g., "AAPL")
start: date         # Start date (ISO format: 2023-01-01)
end: date           # End date (ISO format: 2023-12-31)
timeframe: str      # Bar size (1_MIN, 5_MIN, 1_HOUR, 1_DAY)
```

**Response** (200 OK):
```json
{
  "symbol": "AAPL",
  "timeframe": "1_MIN",
  "candles": [
    {
      "time": "2023-01-03T09:30:00",
      "open": 125.07,
      "high": 125.18,
      "low": 124.89,
      "close": 125.02,
      "volume": 1234567
    },
    ...
  ]
}
```

**Error Response** (404 Not Found):
```json
{
  "error": "NoDataAvailable",
  "message": "No data found for AAPL in date range 2023-01-01 to 2023-12-31",
  "suggestion": "Run 'ntrader data fetch AAPL --start 2023-01-01 --end 2023-12-31' to download data"
}
```

---

**GET /api/indicators**

Retrieve indicator values for a specific backtest run.

**Request Parameters**:
```
run_id: UUID        # Backtest run identifier
```

**Response** (200 OK):
```json
{
  "run_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
  "indicators": {
    "sma_fast": [
      {"time": "2023-01-03T09:30:00", "value": 124.56},
      {"time": "2023-01-03T09:31:00", "value": 124.58},
      ...
    ],
    "sma_slow": [
      {"time": "2023-01-03T09:30:00", "value": 123.45},
      ...
    ]
  }
}
```

**Error Response** (404 Not Found):
```json
{
  "error": "BacktestNotFound",
  "message": "Backtest run a1b2c3d4-e5f6-7890-1234-567890abcdef not found"
}
```

---

**GET /api/trades**

Retrieve trade markers for a specific backtest run.

**Request Parameters**:
```
run_id: UUID        # Backtest run identifier
```

**Response** (200 OK):
```json
{
  "run_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
  "trades": [
    {
      "time": "2023-01-05T10:15:00",
      "side": "buy",
      "price": 126.45,
      "quantity": 100,
      "pnl": null
    },
    {
      "time": "2023-01-10T14:30:00",
      "side": "sell",
      "price": 128.90,
      "quantity": 100,
      "pnl": 245.00
    },
    ...
  ]
}
```

---

**GET /api/equity**

Retrieve equity curve and drawdown data for a backtest run.

**Request Parameters**:
```
run_id: UUID        # Backtest run identifier
```

**Response** (200 OK):
```json
{
  "run_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef",
  "equity": [
    {"time": "2023-01-03T09:30:00", "value": 100000.00},
    {"time": "2023-01-03T16:00:00", "value": 100245.50},
    {"time": "2023-01-04T16:00:00", "value": 100567.30},
    ...
  ],
  "drawdown": [
    {"time": "2023-01-03T09:30:00", "value": 0.0},
    {"time": "2023-03-15T16:00:00", "value": -0.123},  # -12.3% DD
    ...
  ]
}
```

---

### HTML Routes

All HTML routes return rendered Jinja2 templates with status 200 OK or error pages (404, 500).

**GET /**
- Template: `dashboard.html`
- Context: `{total_backtests, best_sharpe, recent_runs}`

**GET /backtests**
- Template: `backtests/list.html`
- Query Params: `strategy, instrument, status, sort, page`
- Context: `{backtests, pagination, filters, strategies_list}`

**GET /backtests/fragment**
- Template: `backtests/list_fragment.html` (HTMX partial)
- Returns only table HTML for dynamic updates

**GET /backtests/{run_id}**
- Template: `backtests/detail.html`
- Context: `{backtest, metrics, trades, config}`

**GET /backtests/compare**
- Template: `backtests/compare.html`
- Query Params: `run_ids` (comma-separated UUIDs)
- Context: `{backtests, comparison_matrix}`

**POST /backtests/{run_id}/rerun**
- Action: Trigger backtest re-execution via backend service
- Response: Redirect to `/backtests/{new_run_id}` or error page

**DELETE /backtests/{run_id}**
- Action: Delete backtest from database
- Response: HTMX fragment confirming deletion or error message

---

## Implementation Phases

### Phase 1: Foundation (Week 1-2)
**Goal**: Establish basic UI skeleton with core navigation and backtest list

**Deliverables**:
- ✓ FastAPI application structure with Jinja2 templates configured
- ✓ Base HTML template with navigation and Tailwind CSS
- ✓ Dashboard page with summary statistics
- ✓ Backtest list page (read-only table, no filters)
- ✓ Basic routing and static file serving

**Acceptance Criteria**:
- Dashboard loads in < 500ms
- Backtest list displays 20 most recent backtests
- Navigation links work between all pages
- Tailwind CSS applies correctly (dark theme)

---

### Phase 2: Interactive Lists (Week 3)
**Goal**: Add HTMX filtering, sorting, and pagination to backtest list

**Deliverables**:
- ✓ HTMX integration for partial page updates
- ✓ Filter controls: strategy, instrument, date range, status
- ✓ Sortable table headers (click to toggle asc/desc)
- ✓ Pagination controls (previous/next, page selector)
- ✓ URL query parameter persistence for filters

**Acceptance Criteria**:
- Filter/sort operations complete in < 200ms
- No full page reloads during filtering
- Filters persist in URL for bookmarking/sharing
- Table updates smoothly without flicker

---

### Phase 3: Detail View & Metrics (Week 4)
**Goal**: Build backtest detail page with full metrics display

**Deliverables**:
- ✓ Backtest detail route and template
- ✓ Metrics panel with all performance indicators
- ✓ Configuration snapshot display
- ✓ Trade blotter table with sorting
- ✓ Action buttons (delete, export, re-run)

**Acceptance Criteria**:
- Detail page loads in < 1 second
- All metrics displayed correctly and match CLI output
- Trade blotter handles 1000+ trades with pagination
- Export button downloads same HTML report as CLI

---

### Phase 4: Chart APIs (Week 5)
**Goal**: Implement JSON APIs for time series, trades, indicators, equity

**Deliverables**:
- ✓ `/api/timeseries` endpoint with Parquet data loading
- ✓ `/api/trades` endpoint querying PostgreSQL
- ✓ `/api/indicators` endpoint (strategy-specific)
- ✓ `/api/equity` endpoint with drawdown calculation
- ✓ Pydantic models for all request/response schemas
- ✓ Unit tests for all API endpoints (80%+ coverage)

**Acceptance Criteria**:
- APIs return data in < 500ms for 1 year of 1-minute data
- JSON schemas validated with Pydantic
- Error responses include actionable suggestions
- All endpoints have corresponding test cases

---

### Phase 5: Interactive Charts (Week 6-7)
**Goal**: Integrate TradingView Lightweight Charts for visualization

**Deliverables**:
- ✓ `charts.js` module for chart initialization
- ✓ Price chart with OHLC candlesticks + volume
- ✓ Indicator overlays (SMA lines)
- ✓ Trade markers (buy/sell arrows)
- ✓ Equity curve chart with drawdown overlay
- ✓ Synchronized zoom and pan across charts
- ✓ Responsive tooltips with data values

**Acceptance Criteria**:
- Charts render 1 year of 1-minute data in < 2 seconds
- Zoom/pan interactions feel smooth (60 fps)
- Trade markers align precisely with timestamps
- Charts work correctly after HTMX swaps

---

### Phase 6: Comparison View (Week 8)
**Goal**: Enable side-by-side backtest comparison

**Deliverables**:
- ✓ Multi-select checkboxes in backtest list
- ✓ Compare route handling 2-10 backtests
- ✓ Side-by-side metrics table with highlighting
- ✓ Overlaid equity curves on single chart
- ✓ Configuration diff view (optional)

**Acceptance Criteria**:
- Comparison view loads in < 2 seconds for 10 backtests
- Best/worst metrics clearly highlighted
- Equity curves distinguishable with different colors
- URL includes all run IDs for sharing

---

### Phase 7: Data Catalog Viewer (Week 9)
**Goal**: Provide visual exploration of available market data

**Deliverables**:
- ✓ Data catalog route with instrument/timeframe selectors
- ✓ Raw OHLC chart for selected data
- ✓ Statistics panel (bar count, date range, gaps)
- ✓ Calendar heatmap showing data availability (optional)

**Acceptance Criteria**:
- Data loads and renders in < 1 second
- Chart handles missing data gracefully
- Statistics provide actionable information
- Integration with Parquet catalog service

---

### Phase 8: Polish & Testing (Week 10)
**Goal**: Comprehensive testing, documentation, and UX improvements

**Deliverables**:
- ✓ E2E tests with Playwright for critical flows
- ✓ Accessibility audit and fixes (WCAG AA)
- ✓ Loading spinners and error toast notifications
- ✓ README with UI setup instructions
- ✓ Developer guide updates
- ✓ Performance profiling and optimization

**Acceptance Criteria**:
- All KPIs met (page load < 500ms, chart < 2s, etc.)
- 80%+ test coverage on UI code
- No console errors in browser DevTools
- Documentation complete and accurate

---

## Testing Strategy

### Test Categories

**Unit Tests (40% of UI test suite)**
- **Scope**: API routes, service layer functions, Pydantic models
- **Framework**: pytest with FastAPI TestClient
- **Execution Time**: < 10 seconds total
- **Coverage Target**: 90%+ on business logic

**Example**:
```python
from httpx import AsyncClient
import pytest
from src.api.web import app

@pytest.mark.asyncio
async def test_backtest_list_returns_200(client: AsyncClient):
    response = await client.get("/backtests")
    assert response.status_code == 200
    assert "Backtest History" in response.text

@pytest.mark.asyncio
async def test_timeseries_api_validates_date_range(client: AsyncClient):
    response = await client.get(
        "/api/timeseries",
        params={
            "symbol": "AAPL",
            "start": "2023-12-31",  # Invalid: end before start
            "end": "2023-01-01",
            "timeframe": "1_MIN"
        }
    )
    assert response.status_code == 422  # Validation error
    assert "Invalid date range" in response.json()["message"]
```

---

**Integration Tests (30% of UI test suite)**
- **Scope**: Full request/response cycle with real database
- **Framework**: pytest with async fixtures
- **Execution Time**: < 30 seconds total
- **Coverage Target**: Critical user flows

**Example**:
```python
@pytest.mark.asyncio
async def test_backtest_detail_with_real_data(
    client: AsyncClient,
    db_session,
    sample_backtest
):
    # Insert sample backtest into test database
    db_session.add(sample_backtest)
    await db_session.commit()
    
    # Request detail page
    response = await client.get(f"/backtests/{sample_backtest.id}")
    
    # Verify metrics displayed
    assert response.status_code == 200
    assert str(sample_backtest.metrics.sharpe_ratio) in response.text
    assert sample_backtest.instrument_symbol in response.text
```

---

**Component Tests (20% of UI test suite)**
- **Scope**: JavaScript chart initialization, HTMX behaviors
- **Framework**: Playwright or Cypress
- **Execution Time**: < 2 minutes total
- **Coverage Target**: Interactive features

**Example**:
```python
async def test_chart_renders_after_api_call(page):
    # Navigate to backtest detail
    await page.goto(f"http://localhost:8000/backtests/{run_id}")
    
    # Wait for chart container to appear
    chart = page.locator("#run-price-chart")
    await expect(chart).to_be_visible()
    
    # Verify chart canvas rendered (TradingView creates canvas element)
    canvas = chart.locator("canvas")
    await expect(canvas).to_be_visible()
    
    # Verify API was called
    requests = page.context.wait_for_event("request")
    assert any("/api/timeseries" in r.url for r in requests)
```

---

**End-to-End Tests (10% of UI test suite)**
- **Scope**: Complete user workflows across multiple pages
- **Framework**: Playwright with full browser automation
- **Execution Time**: < 5 minutes total
- **Coverage Target**: Critical paths only

**Example**:
```python
async def test_compare_backtests_workflow(page, browser_context):
    # Step 1: Navigate to backtest list
    await page.goto("http://localhost:8000/backtests")
    
    # Step 2: Select 3 backtests via checkboxes
    checkboxes = page.locator('input[type="checkbox"]')
    await checkboxes.nth(0).check()
    await checkboxes.nth(1).check()
    await checkboxes.nth(2).check()
    
    # Step 3: Click compare button
    await page.click('button:has-text("Compare Selected")')
    
    # Step 4: Verify navigation to comparison page
    await expect(page).to_have_url(/.*\/backtests\/compare\?run_ids=.+/)
    
    # Step 5: Verify all 3 backtests displayed
    rows = page.locator('table tbody tr')
    await expect(rows).to_have_count(3)
    
    # Step 6: Verify equity chart rendered
    chart = page.locator("#comparison-equity-chart canvas")
    await expect(chart).to_be_visible()
```

---

### Test Data Strategy

**Fixtures for Consistent Testing**:
```python
@pytest.fixture
async def sample_backtest(db_session):
    """Create a sample backtest with realistic data"""
    backtest = BacktestRun(
        id=uuid.uuid4(),
        strategy_name="SMA Crossover",
        instrument_symbol="AAPL",
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2023, 12, 31),
        metrics=BacktestMetrics(
            total_return=0.154,
            sharpe_ratio=1.67,
            max_drawdown=-0.123,
            win_rate=0.628,
            total_trades=156
        ),
        status="success",
        created_at=datetime.utcnow()
    )
    return backtest
```

**Mock Data for Charts**:
- Store sample OHLCV data in JSON fixtures
- Avoid calling real Parquet files during unit tests
- Use `unittest.mock` to stub external dependencies

---

### Continuous Integration

**GitHub Actions Workflow**:
```yaml
name: UI Tests

on: [push, pull_request]

jobs:
  test-ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install uv
          uv sync
      
      - name: Run UI unit tests
        run: uv run pytest tests/ui/ -m unit --cov
      
      - name: Run UI integration tests
        run: uv run pytest tests/ui/ -m integration
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## Dependencies & Integration Points

### Internal Dependencies

**1. Existing Services (Reused)**
- `BacktestService` (src/services/backtest_service.py)
  - `list_backtests(filters, sort, limit)` → UI calls for list page
  - `get_backtest_by_id(run_id)` → UI calls for detail page
  - `delete_backtest(run_id)` → UI calls for delete action
  - `trigger_backtest_rerun(config)` → UI calls for re-run action

- `DataCatalogService` (src/services/data_catalog_service.py)
  - `get_available_symbols()` → UI calls for dropdowns
  - `check_data_coverage(symbol, timeframe, date_range)` → UI calls for catalog viewer
  - `load_parquet_data(symbol, start, end, timeframe)` → UI calls for chart APIs

- `MetricsService` (src/services/metrics_service.py)
  - `calculate_equity_curve(backtest)` → UI calls for equity chart API
  - `get_indicator_series(backtest)` → UI calls for indicator API

**2. Database Models (No Changes)**
- `BacktestRun` (src/db/models.py)
- `BacktestMetrics` (src/db/models.py)
- `Trade` (src/db/models.py)
- All existing SQLAlchemy models used as-is

**3. Configuration**
- Environment variables remain unchanged
- Same `.env` file used for both CLI and UI
- Database connection pool shared across all requests

### External Dependencies

**New Python Packages**:
```toml
[project.dependencies]
fastapi = ">=0.109.0"
jinja2 = ">=3.1.0"
python-multipart = ">=0.0.6"  # For form data
uvicorn = { version = ">=0.27.0", extras = ["standard"] }
```

**Frontend Assets (CDN)**:
```html
<!-- HTMX -->
<script src="https://unpkg.com/htmx.org@1.9.10"></script>

<!-- Tailwind CSS (compiled locally or CDN) -->
<link href="https://cdn.jsdelivr.net/npm/tailwindcss@3.4.0/dist/tailwind.min.css" rel="stylesheet">

<!-- TradingView Lightweight Charts -->
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
```

**Development Dependencies**:
```toml
[project.optional-dependencies]
dev = [
    "playwright>=1.40.0",  # E2E testing
    "pytest-playwright>=0.4.0",
    "httpx>=0.25.0",  # Async HTTP client for testing
]
```

### Integration Constraints

**1. Backward Compatibility**
- ✓ All CLI commands continue to work identically
- ✓ No changes to existing service layer APIs
- ✓ Database schema remains unchanged
- ✓ Parquet file structure unmodified

**2. Shared State**
- ✓ FastAPI runs in same process as CLI (optional)
- ✓ Database connection pool shared (configure max connections)
- ✓ Environment variables read from same `.env` file

**3. Performance Impact**
- ✓ UI adds negligible overhead to existing functionality
- ✓ Separate web server process recommended for production
- ✓ Static assets cached (1 hour TTL)

---

## Risks & Mitigation

### Risk 1: JavaScript Complexity Creep
**Risk**: Tendency to add more JavaScript features over time, violating "minimal JS" principle

**Probability**: Medium | **Impact**: Medium

**Mitigation**:
- Strict code review process: Reject PRs adding unnecessary JavaScript
- Document decision: "Server-side rendering first, JS only for charts and HTMX"
- Set hard limit: No more than 500 lines of custom JavaScript total
- Use linting rules to prevent importing heavy frameworks

---

### Risk 2: Performance Degradation with Large Datasets
**Risk**: Charts become slow/unresponsive with very large backtests (multi-year 1-minute data)

**Probability**: Medium | **Impact**: High

**Mitigation**:
- Implement server-side decimation: Reduce data points sent to client based on zoom level
- Add pagination to trade blotter (100 trades per page)
- Lazy-load charts: Only fetch data when user scrolls to chart section
- Document limitations: Recommend 1-hour or daily bars for multi-year backtests

---

### Risk 3: HTMX/Tailwind Learning Curve
**Risk**: Developers unfamiliar with HTMX or Tailwind struggle to contribute

**Probability**: Low | **Impact**: Low

**Mitigation**:
- Comprehensive developer guide with examples (already created)
- Link to official HTMX/Tailwind documentation in README
- Pair programming for first few UI features
- Simple patterns only: Avoid advanced HTMX features (SSE, WebSockets)

---

### Risk 4: Test Coverage Slips Below 80%
**Risk**: UI code added without corresponding tests, violating TDD standards

**Probability**: Medium | **Impact**: Medium

**Mitigation**:
- CI/CD pipeline fails if coverage < 80% on UI code
- Pre-commit hooks run tests before allowing commit
- Code review checklist: "Are tests included?"
- Dedicated testing sprint (Phase 8) to catch gaps

---

### Risk 5: Inconsistent Data Between CLI and UI
**Risk**: UI shows different results than CLI for same backtest

**Probability**: Low | **Impact**: High

**Mitigation**:
- **Single source of truth**: UI and CLI call identical service layer functions
- No UI-specific business logic; all calculations in shared services
- Integration tests verify CLI and UI produce same output
- Explicit test: Load backtest via CLI, verify UI displays identical metrics

---

### Risk 6: Security Vulnerabilities (XSS, SQL Injection)
**Risk**: User input not properly sanitized, leading to exploits

**Probability**: Low | **Impact**: High

**Mitigation**:
- Jinja2 auto-escaping enabled by default (prevents XSS)
- Pydantic validation on all API inputs (prevents injection)
- SQLAlchemy parameterized queries (no raw SQL)
- Security audit before production deployment
- Regular dependency updates via `uv sync`

---

## Future Enhancements

### Phase 2 Features (Post-MVP)

**1. Real-Time Backtest Execution Monitoring**
- **Description**: Show progress bars and live updates while backtest runs
- **Technical Approach**: Server-Sent Events (SSE) or WebSockets
- **Value**: Reduces uncertainty during long-running backtests
- **Complexity**: High (requires async job queue)

**2. Strategy Code Viewer/Editor**
- **Description**: Browse strategy code directly in browser
- **Technical Approach**: Monaco Editor (VS Code editor component)
- **Value**: Convenience for quick parameter tweaks
- **Complexity**: High (security considerations for code execution)

**3. Multi-User Authentication**
- **Description**: Support multiple users with separate accounts
- **Technical Approach**: FastAPI Users library + JWT tokens
- **Value**: Team collaboration on backtests
- **Complexity**: Medium (database schema changes required)

**4. Advanced Chart Features**
- **Description**: Technical indicators on demand (RSI, MACD, Bollinger Bands)
- **Technical Approach**: Client-side indicator calculation library
- **Value**: Deeper technical analysis without re-running backtests
- **Complexity**: Medium (requires additional JavaScript)

**5. Export to PDF**
- **Description**: Generate PDF reports from UI
- **Technical Approach**: WeasyPrint or Playwright PDF generation
- **Value**: Professional reports for stakeholders
- **Complexity**: Low (leverage existing HTML reports)

**6. Walk-Forward Analysis UI**
- **Description**: Visualize optimization results across rolling windows
- **Technical Approach**: 3D heatmaps with Plotly
- **Value**: Identify robust parameter ranges
- **Complexity**: High (requires optimization engine implementation)

**7. Mobile-Responsive Design**
- **Description**: Optimize UI for tablet/mobile viewing
- **Technical Approach**: Tailwind responsive classes
- **Value**: Review backtests on-the-go
- **Complexity**: Medium (chart interactions need rethinking)

**8. Notifications & Alerts**
- **Description**: Email/Slack notifications when backtest completes
- **Technical Approach**: Celery tasks + notification service
- **Value**: Async workflows for long backtests
- **Complexity**: Medium (requires background job infrastructure)

---

## Appendix

### Glossary

- **HTMX**: JavaScript library enabling AJAX, CSS transitions, WebSockets, and SSE via HTML attributes
- **Jinja2**: Template engine for Python, used by Flask and FastAPI
- **Lightweight Charts**: Financial charting library by TradingView for performant OHLC rendering
- **SSE**: Server-Sent Events, HTTP-based push notifications from server to client
- **Tailwind CSS**: Utility-first CSS framework for rapid UI development
- **Partial Update**: HTMX technique replacing part of page HTML without full reload
- **TDD**: Test-Driven Development, writing tests before implementation code

### References

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [HTMX Documentation](https://htmx.org/docs/)
- [Tailwind CSS Documentation](https://tailwindcss.com/docs)
- [TradingView Lightweight Charts](https://tradingview.github.io/lightweight-charts/)
- [Jinja2 Template Designer Documentation](https://jinja.palletsprojects.com/)
- [Playwright for Python](https://playwright.dev/python/)
- [NTrader Web UI Developer Guide](./NTrader_Web_UI_Developer_Guide.md)
- [NTrader Product Overview](./PRODUCT_OVERVIEW.md)

### Changelog

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-11-14 | Initial product specification created |

---

**Document Version**: 1.0  
**Last Updated**: November 14, 2025  
**Maintainer**: NTrader Development Team  
**Status**: Planning Phase