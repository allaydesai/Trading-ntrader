# Feature Specification: Enhanced Price Plot with Trade Markers

**Feature Branch**: `010-enhanced-price-plot`
**Created**: 2025-01-27
**Updated**: 2025-01-30
**Status**: Draft
**Input**: User description: "I want to improve the price plot on backtest details page to show entry and exit positions to help visualize the trades."

**Current Scope**: User Story 1 only (trade markers)
**Deferred**: User Stories 2-4 (indicator overlays, correlation, customization) - may be revisited in future iterations

**Scope Rationale**: After analysis, we determined that indicator overlays add significant complexity with diminishing returns. Trade markers alone provide the 80/20 value - users can see where the strategy traded relative to price movements, which is the core insight needed. Indicator visualization is better served by external tools (TradingView, etc.) that specialize in charting.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Trade Entry/Exit Markers on Price Chart (Priority: P1)

As a quantitative developer analyzing a backtest, I want to see visual markers on the price chart showing where trades were executed, so that I can understand the strategy's entry and exit timing in the context of price movements.

**Why this priority**: This is the core visualization enhancement requested. Without trade markers, users cannot see the relationship between price action and strategy decisions. This is foundational to understanding strategy behavior and identifying potential improvements.

**Independent Test**: Can be fully tested by loading a backtest detail page, verifying that buy/sell markers appear at correct price levels and timestamps, and confirming that marker colors/shapes distinguish entry from exit. Delivers immediate value by showing trade execution context.

**Acceptance Scenarios**:

1. **Given** a backtest with completed trades exists, **When** user views the price chart on the detail page, **Then** buy entries are shown as upward-pointing green triangles at the entry price and timestamp
2. **Given** a backtest with completed trades exists, **When** user views the price chart, **Then** sell exits are shown as downward-pointing red triangles at the exit price and timestamp
3. **Given** a trade marker is displayed on the chart, **When** user hovers over the marker, **Then** a tooltip appears showing trade details: type (entry/exit), price, quantity, timestamp, and P&L (for exits)
4. **Given** a backtest has overlapping trades (multiple entries/exits at similar times), **When** user views the chart, **Then** markers are positioned to avoid overlap and remain individually clickable
5. **Given** user zooms or pans the price chart, **When** the viewport changes, **Then** trade markers remain anchored to their correct price and time coordinates

---

### User Story 2 - Overlay Strategy Indicators on Price Chart (Priority: P2) **[DEFERRED]**

As a quantitative developer, I want to see the technical indicators used by the strategy overlaid on the price chart, so that I can understand what signals drove the trading decisions.

**Status**: DEFERRED - Determined to add significant complexity with diminishing returns. Users can leverage external charting tools for indicator analysis.

**Acceptance Scenarios**: (Kept for reference)

1. SMA lines overlaid on price chart with distinct colors
2. Bollinger Bands displayed as three lines with appropriate styling
3. RSI in separate pane with overbought/oversold threshold lines
4. Tooltips showing indicator values on hover
5. Legend identifying indicators by color and name
6. Visibility toggles for individual indicators

---

### User Story 3 - Correlate Trade Markers with Indicator Signals (Priority: P3) **[DEFERRED]**

**Status**: DEFERRED - Depends on User Story 2 (indicators)

---

### User Story 4 - Customize Chart Display for Focused Analysis (Priority: P3) **[DEFERRED]**

**Status**: DEFERRED - Not needed for MVP with simplified scope

---

### Edge Cases

- What happens when a backtest has hundreds of trades (dense marker placement)?
  - System implements intelligent marker clustering or sampling to prevent visual overload while maintaining representative coverage
- What happens when a backtest has no completed trades?
  - System displays price chart without markers and shows message "No trades executed during backtest period"
- What happens when trade timestamps fall outside the OHLCV data range?
  - System logs a warning and omits markers for trades without corresponding price data
- How does the system perform with years of daily data and many trades?
  - System implements data downsampling for zoomed-out views and loads full-resolution data progressively as user zooms in

## Requirements *(mandatory)*

### Functional Requirements

**Current Scope (User Story 1):**

- **FR-001**: System MUST render buy entry markers as upward-pointing green triangles on the price chart at entry price and timestamp
- **FR-002**: System MUST render sell exit markers as downward-pointing red triangles on the price chart at exit price and timestamp
- **FR-003**: System MUST display tooltips on trade markers showing: type (entry/exit), price, quantity, timestamp, and P&L (for exits)
- **FR-004**: System MUST prevent marker overlap when multiple trades occur at similar times
- **FR-005**: System MUST maintain marker position accuracy during zoom and pan operations
- **FR-006**: System MUST implement intelligent marker clustering when trade density exceeds a threshold to maintain chart readability
- **FR-007**: System MUST implement data downsampling for zoomed-out views to maintain smooth chart performance
- **FR-008**: System MUST display a message when no trades exist for the backtest

**Deferred (User Stories 2-4):**

- **FR-009** [DEFERRED]: System MUST overlay indicator lines on the price chart
- **FR-010** [DEFERRED]: System MUST provide visibility toggle controls for indicators
- **FR-011** [DEFERRED]: System MUST preserve visibility preferences within the browser session

### Key Entities

- **Trade Marker**: Visual representation of a trade entry or exit with timestamp, price, type (buy/sell), quantity, and P&L (for exits)
- **Marker Cluster**: Aggregated representation when multiple trades occur at similar times, expandable on zoom

## Success Criteria *(mandatory)*

### Measurable Outcomes

**Current Scope (User Story 1):**

- **SC-001**: Trade markers appear on the chart within 500ms of page load for typical backtests with up to 1000 trades
- **SC-002**: All trade markers are positioned with 100% accuracy to match trade execution timestamps and prices
- **SC-003**: Chart remains responsive (frame rate above 30fps) during zoom and pan operations even with dense marker placement
- **SC-004**: Tooltip data matches the trade data from the API with 100% accuracy
- **SC-005**: Chart performance remains acceptable (load time under 3 seconds) for 5-year daily backtests

**Deferred (User Stories 2-4):**

- **SC-006** [DEFERRED]: Indicator lines render within 1 second for backtests with up to 100,000 data points
- **SC-007** [DEFERRED]: Users can identify the relationship between indicator signals and trade execution

## Assumptions

- Spec 007 (Backtest Detail View) is implemented, providing the base detail page where the chart is displayed
- Spec 008 (Chart APIs) is implemented, providing /api/trades endpoint with trade data
- Spec 009 (Trade Tracking) is implemented, providing complete trade data including entry/exit prices, quantities, and P&L
- TradingView Lightweight Charts library is already integrated for rendering the base price chart
- Trade data includes all necessary fields: timestamp, price, quantity, side (buy/sell), and P&L for exits
- Browser supports modern JavaScript features required by TradingView Lightweight Charts (ES6+)

## Dependencies

- **Spec 007**: Backtest Detail View provides the page where enhanced chart will be displayed
- **Spec 008**: Chart APIs provide OHLCV data (/api/ohlcv) for the price chart
- **Spec 009**: Trade Tracking provides trade data (/api/trades) with entry/exit details

## Out of Scope

- Indicator overlays on price chart (deferred - use external charting tools)
- Indicator visibility toggles and legends
- Separate panes for oscillator-type indicators (RSI, etc.)
- Real-time indicator calculation
- Custom indicator creation by users
- Drawing tools or annotation capabilities on the chart
- Exporting chart images or data
- Comparing multiple backtests on the same chart
- Advanced charting features like Fibonacci retracements, trend lines, or pattern recognition
- Session-persistent visibility preferences
