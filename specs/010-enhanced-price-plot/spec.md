# Feature Specification: Enhanced Price Plot with Trade Markers and Indicators

**Feature Branch**: `010-enhanced-price-plot`
**Created**: 2025-01-27
**Status**: Draft
**Input**: User description: "I want to improve the price plot on backtest details page to show entry and exit positions along with any indicators that are being used in the strategy to help visualize the trades."

**Current Scope**: User Stories 1-2 (P1 priorities)
**Deferred**: User Stories 3-4 (P2-P3 priorities) - kept for reference, to be implemented later

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

### User Story 2 - Overlay Strategy Indicators on Price Chart (Priority: P1)

As a quantitative developer, I want to see the technical indicators used by the strategy overlaid on the price chart, so that I can understand what signals drove the trading decisions and evaluate whether the indicators performed as expected.

**Why this priority**: Indicators are essential for understanding the "why" behind trades. Users need to see how indicators like SMAs, Bollinger Bands, or RSI levels related to price movements and trade execution. This is critical for strategy validation and debugging.

**Independent Test**: Can be fully tested by loading a backtest with a known strategy (e.g., SMA Crossover), verifying that indicator lines appear on the chart with correct values, and confirming that indicator series align with OHLCV timestamps. Delivers value by revealing the strategy's decision-making logic.

**Acceptance Scenarios**:

1. **Given** an SMA Crossover backtest exists, **When** user views the price chart, **Then** both fast SMA and slow SMA lines are overlaid on the price chart with distinct colors and labels
2. **Given** a Bollinger Bands backtest exists, **When** user views the price chart, **Then** upper band, middle band (SMA), and lower band are displayed as separate lines with appropriate styling (e.g., dashed for bands)
3. **Given** an RSI Mean Reversion backtest exists, **When** user views the price chart, **Then** an RSI indicator appears in a separate pane below the price chart with overbought/oversold threshold lines at 70 and 30
4. **Given** indicator lines are displayed on the chart, **When** user hovers over an indicator line, **Then** a tooltip shows the indicator name and value at that timestamp
5. **Given** a strategy uses multiple indicators, **When** user views the chart, **Then** a legend appears identifying each indicator line by color and name
6. **Given** user toggles an indicator visibility control, **When** the toggle is clicked, **Then** the corresponding indicator line is hidden or shown without reloading the page

---

### User Story 3 - Correlate Trade Markers with Indicator Signals (Priority: P2) **[DEFERRED]**

As a quantitative developer, I want to visually correlate trade markers with indicator crossovers or threshold breaches, so that I can validate that the strategy executed trades at the expected signal points.

**Why this priority**: This builds on P1 capabilities to provide integrated analysis. Users benefit from seeing trades and indicators together, but the individual components (markers and indicators) deliver value independently. This priority enhances understanding but is not essential for basic visualization.

**Status**: DEFERRED - To be implemented in a future iteration

**Independent Test**: Can be tested by identifying indicator crossover points (e.g., SMA fast crosses above SMA slow) and verifying that trade markers appear at or near those crossover timestamps. Delivers value by validating strategy logic execution.

**Acceptance Scenarios**:

1. **Given** an SMA Crossover backtest is displayed, **When** user observes a point where fast SMA crosses above slow SMA, **Then** a buy marker appears at or shortly after the crossover point
2. **Given** an RSI Mean Reversion backtest is displayed, **When** user observes RSI crossing below 30 (oversold), **Then** a buy marker appears at or near that timestamp
3. **Given** a Bollinger Reversal backtest is displayed, **When** price touches the lower Bollinger Band, **Then** a buy marker appears indicating a reversal entry
4. **Given** user identifies a missed opportunity (indicator signal without a trade), **When** viewing the chart, **Then** the absence of a trade marker is clearly visible and prompts further analysis

---

### User Story 4 - Customize Chart Display for Focused Analysis (Priority: P3) **[DEFERRED]**

As a quantitative developer, I want to customize which elements are displayed on the chart (price, indicators, trades), so that I can focus on specific aspects of the backtest without visual clutter.

**Why this priority**: Customization improves user experience but is not required for basic functionality. Users can still analyze backtests with all elements visible. This priority addresses power users who want granular control over visualization.

**Status**: DEFERRED - To be implemented in a future iteration

**Independent Test**: Can be tested by toggling visibility controls for different chart elements and verifying that the chart updates accordingly without losing data. Delivers value by enabling focused analysis workflows.

**Acceptance Scenarios**:

1. **Given** the price chart is displayed with all elements, **When** user clicks "Hide Trade Markers" toggle, **Then** all entry/exit markers disappear from the chart while price and indicators remain visible
2. **Given** multiple indicators are displayed, **When** user clicks "Hide All Indicators" toggle, **Then** all indicator lines are hidden, showing only price candles and trade markers
3. **Given** user has hidden certain elements, **When** user clicks "Reset View" button, **Then** all default elements (price, trades, indicators) are restored to visibility
4. **Given** user customizes chart visibility, **When** user navigates to another backtest and returns, **Then** visibility preferences are remembered for the current session

---

### Edge Cases

- What happens when a backtest has hundreds of trades (dense marker placement)?
  - System implements intelligent marker clustering or sampling to prevent visual overload while maintaining representative coverage
- What happens when a strategy uses no indicators (pure price action)?
  - System displays only price chart and trade markers with a message "No indicators configured for this strategy"
- How does the system handle indicators with very different value ranges (e.g., price at $100, RSI at 50)?
  - System places indicators with different scales in separate panes (e.g., RSI below price chart)
- What happens when indicator data is missing for certain timestamps?
  - System renders available indicator data and shows gaps clearly; tooltip indicates "No data available" for missing points
- How does the system perform when rendering years of daily data with complex indicators?
  - System implements data downsampling for zoomed-out views and loads full-resolution data progressively as user zooms in

## Requirements *(mandatory)*

### Functional Requirements

**Current Scope (User Stories 1-2):**

- **FR-001**: System MUST render buy entry markers as upward-pointing green triangles on the price chart at entry price and timestamp
- **FR-002**: System MUST render sell exit markers as downward-pointing red triangles on the price chart at exit price and timestamp
- **FR-003**: System MUST display tooltips on trade markers showing: type, price, quantity, timestamp, and P&L (for exits)
- **FR-004**: System MUST prevent marker overlap when multiple trades occur at similar times
- **FR-005**: System MUST maintain marker position accuracy during zoom and pan operations
- **FR-006**: System MUST overlay indicator lines on the price chart using data from the indicators API endpoint
- **FR-007**: System MUST render SMA indicators as solid lines with distinct colors (e.g., fast SMA blue, slow SMA orange)
- **FR-008**: System MUST render Bollinger Bands as three lines: upper, middle (SMA), and lower, with dashed styling for bands
- **FR-009**: System MUST render RSI indicator in a separate pane below the price chart with horizontal lines at 70 (overbought) and 30 (oversold)
- **FR-010**: System MUST display indicator tooltips showing indicator name and value on hover
- **FR-011**: System MUST provide a chart legend identifying each indicator by color and name
- **FR-012**: System MUST provide basic visibility toggle controls for each individual indicator *(Note: Advanced controls for trade markers, hide all, and reset deferred to User Story 4)*
- **FR-013**: System MUST update chart display immediately when indicator visibility toggles are clicked

**General Requirements (Support User Stories 1-2):**

- **FR-015**: System MUST display a message when no indicators are configured for the strategy
- **FR-016**: System MUST handle missing indicator data gracefully by showing gaps in the line and providing explanatory tooltips
- **FR-017**: System MUST implement intelligent marker clustering when trade density exceeds a threshold to maintain chart readability
- **FR-018**: System MUST place indicators with different value ranges (e.g., RSI 0-100, price in dollars) in separate panes
- **FR-019**: System MUST implement data downsampling for zoomed-out views to maintain smooth chart performance
- **FR-020**: System MUST load full-resolution data progressively as user zooms in

**Deferred (User Stories 3-4):**

- **FR-014** [DEFERRED]: System MUST preserve visibility preferences within the current browser session

### Key Entities

- **Trade Marker**: Visual representation of a trade entry or exit with timestamp, price, type (buy/sell), quantity, and P&L (for exits)
- **Indicator Series**: Time series data for a technical indicator (e.g., SMA, RSI, Bollinger Bands) with name, values, and display properties (color, style, pane)
- **Chart Pane**: A distinct visual area within the chart for displaying data (e.g., main pane for price/indicators, separate pane for RSI)
- **Visibility Settings**: User preferences for which chart elements are displayed (trade markers, specific indicators)

## Success Criteria *(mandatory)*

### Measurable Outcomes

**Current Scope (User Stories 1-2):**

- **SC-001**: Trade markers appear on the chart within 500ms of page load for typical backtests with up to 1000 trades
- **SC-002**: All trade markers are positioned with 100% accuracy to match trade execution timestamps and prices
- **SC-003**: Indicator lines render within 1 second for backtests with up to 100,000 data points
- **SC-004**: Indicator values in tooltips match the raw data values from the indicators API with 100% accuracy
- **SC-006**: Chart remains responsive (frame rate above 30fps) during zoom and pan operations even with dense marker placement
- **SC-007**: Visibility toggle operations complete within 100ms and update the chart without flicker
- **SC-009**: Chart performance remains acceptable (load time under 3 seconds) for 5-year daily backtests with multiple indicators

**Deferred (User Stories 3-4):**

- **SC-005** [DEFERRED]: Users can identify the relationship between indicator signals and trade execution within 30 seconds of viewing the chart
- **SC-008** [DEFERRED]: 90% of users can successfully correlate trade entries with indicator signals without external documentation

## Assumptions

- Spec 008 (Chart APIs) has been implemented, providing /api/indicators endpoint with indicator time series data
- Spec 007 (Backtest Detail View) is implemented, providing the base detail page where the chart is displayed
- TradingView Lightweight Charts library is already integrated for rendering the base price chart
- Trade data is available from the existing /api/trades endpoint (spec 008)
- The system can determine which indicators were used by a strategy from backtest metadata or strategy configuration
- Indicator data is stored and retrievable for the full duration of the backtest
- Browser supports modern JavaScript features required by TradingView Lightweight Charts (ES6+)
- Performance optimization techniques (downsampling, progressive loading) are acceptable trade-offs for maintaining chart responsiveness

## Dependencies

- **Spec 007**: Backtest Detail View provides the page where enhanced chart will be displayed
- **Spec 008**: Chart APIs provide trade data (/api/trades) and indicator data (/api/indicators)
- Strategy implementations (SMA Crossover, RSI Mean Reversion, Bollinger Reversal, SMA Momentum) define which indicators are used

## Out of Scope

- Real-time indicator calculation (indicators are pre-calculated during backtest execution)
- Custom indicator creation by users (only strategy-defined indicators are displayed)
- Drawing tools or annotation capabilities on the chart
- Exporting chart images or data (separate from existing export functionality)
- Comparing multiple backtests on the same chart
- Advanced charting features like Fibonacci retracements, trend lines, or pattern recognition
