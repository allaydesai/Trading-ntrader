# Feature Specification: Chart APIs

**Feature Branch**: `008-chart-apis`
**Created**: 2025-11-19
**Status**: Draft
**Input**: User description: "Phase 4: Chart APIs of NTrader Web UI specification - Implement JSON APIs for time series, trades, indicators, and equity data for TradingView Lightweight Charts"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fetch OHLCV Time Series Data for Price Charts (Priority: P1)

A user viewing a backtest detail page needs to see interactive candlestick charts with price data. The system must provide OHLCV (Open, High, Low, Close, Volume) data in a format suitable for TradingView Lightweight Charts to render candlestick charts with zoom and pan capabilities.

**Why this priority**: This is the foundational data for all chart visualizations. Without OHLCV data, no charts can be rendered. This enables the core visual analysis capability of the web UI.

**Independent Test**: Can be fully tested by requesting time series data for a known symbol and date range, verifying the response contains properly formatted OHLCV candles that TradingView can consume.

**Acceptance Scenarios**:

1. **Given** market data exists for AAPL from 2023-01-01 to 2023-12-31 at 1-minute timeframe, **When** user requests /api/timeseries with symbol=AAPL&start=2023-01-01&end=2023-12-31&timeframe=1_MIN, **Then** system returns JSON with candles array containing timestamp, open, high, low, close, volume for each bar
2. **Given** no market data exists for requested symbol/date range, **When** user requests time series data, **Then** system returns 404 error with actionable message suggesting the CLI command to fetch missing data
3. **Given** user provides invalid date range (end before start), **When** user requests time series data, **Then** system returns 422 validation error with clear message about date range requirements

---

### User Story 2 - Fetch Trade Markers for Chart Overlay (Priority: P1)

A user analyzing a backtest wants to see where trades were executed on the price chart. The system must provide trade entry/exit points with prices and P&L to render buy/sell arrows on the candlestick chart.

**Why this priority**: Trade visualization is essential for understanding strategy behavior. Users need to see where the strategy entered and exited positions to evaluate its decision-making.

**Independent Test**: Can be fully tested by requesting trade data for a known backtest run ID and verifying the response contains trade markers with timestamps, sides (buy/sell), prices, quantities, and P&L values.

**Acceptance Scenarios**:

1. **Given** a completed backtest run with 50 trades exists, **When** user requests /api/trades with run_id={uuid}, **Then** system returns JSON with trades array containing time, side, price, quantity, and pnl for each trade
2. **Given** backtest run ID does not exist, **When** user requests trade data, **Then** system returns 404 error with message "Backtest run {uuid} not found"
3. **Given** backtest has no trades (strategy never entered positions), **When** user requests trade data, **Then** system returns 200 with empty trades array

---

### User Story 3 - Fetch Equity Curve and Drawdown Data (Priority: P1)

A user wants to visualize portfolio performance over time. The system must provide equity curve values and drawdown percentages to render line charts showing portfolio value and risk metrics.

**Why this priority**: Equity curves are the primary metric for evaluating strategy performance. Users need to see how their portfolio grew or shrank over the backtest period, with drawdown visualization for risk assessment.

**Independent Test**: Can be fully tested by requesting equity data for a known backtest run ID and verifying the response contains time series of portfolio values and corresponding drawdown percentages.

**Acceptance Scenarios**:

1. **Given** a completed backtest run exists with equity data, **When** user requests /api/equity with run_id={uuid}, **Then** system returns JSON with equity array (time, value pairs) and drawdown array (time, percentage pairs)
2. **Given** backtest run ID does not exist, **When** user requests equity data, **Then** system returns 404 error with descriptive message
3. **Given** equity curve has maximum drawdown of -12.3%, **When** user requests equity data, **Then** drawdown values accurately reflect the underwater percentage at each point in time

---

### User Story 4 - Fetch Indicator Series for Chart Overlay (Priority: P2)

A user wants to understand what signals drove trading decisions. The system must provide indicator values (such as SMA lines) that can be overlaid on the price chart to show the strategy's technical analysis.

**Why this priority**: While not essential for basic chart rendering, indicator overlays help users understand the "why" behind trades. This is valuable for strategy debugging and optimization but secondary to core price/trade/equity data.

**Independent Test**: Can be fully tested by requesting indicator data for a known backtest run ID and verifying the response contains named indicator series with time-value pairs that align with OHLCV timestamps.

**Acceptance Scenarios**:

1. **Given** a completed SMA Crossover backtest exists, **When** user requests /api/indicators with run_id={uuid}, **Then** system returns JSON with indicator names (sma_fast, sma_slow) and their corresponding time-value series
2. **Given** backtest strategy has no indicators, **When** user requests indicator data, **Then** system returns 200 with empty indicators object
3. **Given** backtest run ID does not exist, **When** user requests indicator data, **Then** system returns 404 error

---

### Edge Cases

- What happens when chart data request spans multiple years of 1-minute data (hundreds of thousands of candles)?
- How does system handle gaps in market data (weekends, holidays, missing bars)?
- What happens when user requests timeframe not available in data catalog (e.g., 5-minute when only 1-minute exists)?
- How does system respond when database connection is temporarily unavailable?
- What happens when concurrent requests for same large dataset are made?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide GET /api/timeseries endpoint that returns OHLCV candle data for specified symbol, date range, and timeframe
- **FR-002**: System MUST provide GET /api/trades endpoint that returns trade markers for a specified backtest run ID
- **FR-003**: System MUST provide GET /api/equity endpoint that returns equity curve and drawdown series for a specified backtest run ID
- **FR-004**: System MUST provide GET /api/indicators endpoint that returns indicator series for a specified backtest run ID
- **FR-005**: System MUST validate all request parameters using type checking and constraint validation
- **FR-006**: System MUST return 404 errors with actionable suggestions when requested data is not found
- **FR-007**: System MUST return 422 errors for invalid request parameters with clear validation messages
- **FR-008**: System MUST format all timestamps consistently for TradingView Lightweight Charts compatibility
- **FR-009**: System MUST handle large datasets (up to 100,000 candles) without timing out
- **FR-010**: System MUST return data in JSON format suitable for direct consumption by TradingView Lightweight Charts
- **FR-011**: System MUST load OHLCV data from existing Parquet files (single source of truth for market data)
- **FR-012**: System MUST load backtest metadata from existing PostgreSQL database
- **FR-013**: System MUST reuse existing service layer functions used by CLI (no duplicate business logic)

### Key Entities

- **Candle**: Represents a single OHLCV bar with timestamp, open, high, low, close, volume
- **Trade Marker**: Represents a trade execution with timestamp, side (buy/sell), price, quantity, P&L
- **Equity Point**: Represents portfolio value at a point in time with timestamp and value
- **Drawdown Point**: Represents underwater percentage at a point in time with timestamp and percentage
- **Indicator Point**: Represents an indicator value at a point in time with timestamp and value

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Time series API returns 1 year of 1-minute data (approximately 100,000 candles) in under 500 milliseconds
- **SC-002**: Trade markers API returns data for backtests with up to 1,000 trades in under 200 milliseconds
- **SC-003**: Equity curve API returns data in under 200 milliseconds for any backtest
- **SC-004**: Indicator series API returns data in under 300 milliseconds for backtests with multiple indicators
- **SC-005**: All API responses are consumable by TradingView Lightweight Charts without client-side transformation
- **SC-006**: API test coverage reaches 80% or higher
- **SC-007**: All endpoints return appropriate error codes (404, 422) with actionable messages
- **SC-008**: API validates 100% of required request parameters before processing
- **SC-009**: Zero data discrepancies between API responses and CLI-generated reports for same backtest

## Assumptions

- Market data is stored in Parquet files and accessible via existing DataCatalogService
- Backtest metadata and results are stored in PostgreSQL and accessible via existing BacktestService
- Existing service layer functions can be reused without modification
- TradingView Lightweight Charts expects timestamps in ISO 8601 format or Unix timestamps
- API will be consumed by same-origin browser requests (no CORS configuration needed for initial implementation)
- Single-user local development context (no rate limiting required for initial implementation)
