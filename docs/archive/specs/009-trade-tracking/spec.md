# Feature Specification: Individual Trade Tracking & Equity Curve Generation

**Feature Branch**: `009-trade-tracking`
**Created**: 2025-01-22
**Status**: Draft
**Input**: User description: "For backtests we need to begin tracking individual trades and saving them. Use the trades to build equity curve and other useful metrics."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capture Individual Trade Executions (Priority: P1)

As a trader, when I run a backtest, the system automatically captures and persists every individual trade execution (entry and exit) so that I can analyze my trading pattern and calculate precise metrics from actual trade data.

**Why this priority**: This is the foundation for all trade-based analytics. Without capturing individual trades, we cannot build equity curves, calculate per-trade metrics, or perform detailed trade analysis. This delivers immediate value by making trade-level data available for analysis.

**Independent Test**: Can be fully tested by running a single backtest that generates trades, then verifying the database contains all trade records with entry/exit details. Delivers value by enabling trade-level visibility that wasn't previously available.

**Acceptance Scenarios**:

1. **Given** a backtest generates 10 trades, **When** the backtest completes, **Then** the system saves all 10 trades with entry price, exit price, quantity, timestamps, symbol, and profit/loss
2. **Given** a trade enters on one date and exits on another, **When** the trade is saved, **Then** both entry and exit timestamps are accurately recorded
3. **Given** a backtest includes both winning and losing trades, **When** trades are persisted, **Then** each trade shows its realized profit or loss in both dollar amount and percentage
4. **Given** a trade includes commissions and fees, **When** the trade is saved, **Then** the total cost (commissions + fees) is recorded separately from the gross profit/loss
5. **Given** multiple positions exist simultaneously, **When** trades are captured, **Then** each position is tracked independently with unique identifiers

---

### User Story 2 - Generate Equity Curve from Trades (Priority: P1)

As a trader, I want to see how my account balance evolved over time based on actual trade executions so that I can visualize the growth pattern and identify periods of drawdown.

**Why this priority**: The equity curve is one of the most critical visualizations for evaluating strategy performance. It provides immediate, intuitive insight into strategy behavior that aggregate metrics cannot convey. This is essential for understanding drawdown timing and recovery periods.

**Independent Test**: Can be fully tested by running a backtest with 5+ trades, generating the equity curve, and verifying it shows cumulative balance changes at each trade exit. Delivers value by providing visual performance analysis.

**Acceptance Scenarios**:

1. **Given** a backtest has completed with 15 trades, **When** I request the equity curve, **Then** I see a time series showing the account balance after each trade exit, starting from initial capital
2. **Given** an equity curve is displayed, **When** viewing the data points, **Then** each point shows the timestamp, balance, and cumulative return percentage
3. **Given** trades occurred on different days, **When** the equity curve is generated, **Then** the curve correctly reflects the chronological order of trade exits
4. **Given** the initial capital was $100,000, **When** the first trade wins $1,000, **Then** the equity curve shows $101,000 at that timestamp
5. **Given** a losing trade occurs after winning trades, **When** viewing the equity curve, **Then** the curve shows the balance decrease at the appropriate time point

---

### User Story 3 - Calculate Trade-Based Performance Metrics (Priority: P2)

As a trader, I want the system to calculate detailed trade statistics (average win/loss, largest win/loss, consecutive wins/losses, etc.) from my individual trades so that I can understand my trading pattern quality beyond just overall return.

**Why this priority**: Once trades are captured, traders need meaningful statistics derived from that data. These metrics reveal patterns that aren't visible in aggregate returns and are critical for understanding strategy robustness.

**Independent Test**: Can be fully tested by running a backtest with mixed results (wins and losses), then verifying calculated metrics match manual calculations from the trade data. Delivers value by automating complex statistical calculations.

**Acceptance Scenarios**:

1. **Given** a backtest has 10 winning trades and 5 losing trades, **When** calculating trade statistics, **Then** the system shows win rate = 66.67%, average win amount, average loss amount, and profit factor
2. **Given** trades have varying profit amounts, **When** viewing statistics, **Then** the system identifies the largest winning trade and largest losing trade with amounts and dates
3. **Given** a strategy had 4 consecutive wins followed by 2 consecutive losses, **When** calculating metrics, **Then** the system reports the longest winning streak (4) and longest losing streak (2)
4. **Given** each trade has different holding periods, **When** viewing statistics, **Then** the system shows average holding period, longest hold, and shortest hold
5. **Given** trades are spread across multiple months, **When** analyzing monthly performance, **Then** the system breaks down trade statistics by month

---

### User Story 4 - Calculate Drawdown from Equity Curve (Priority: P2)

As a trader, I want the system to calculate maximum drawdown, drawdown duration, and recovery periods from the equity curve so that I understand the risk profile and worst-case scenarios.

**Why this priority**: Drawdown analysis is critical for risk assessment and position sizing. Once we have the equity curve, drawdown calculations provide essential risk metrics that aggregate statistics cannot capture.

**Independent Test**: Can be fully tested by creating an equity curve with a known drawdown pattern, then verifying the system correctly identifies the peak, trough, drawdown percentage, and recovery time. Delivers value by quantifying downside risk.

**Acceptance Scenarios**:

1. **Given** an equity curve peaks at $120,000 and then drops to $100,000 before recovering, **When** calculating drawdown, **Then** the system reports max drawdown = 16.67% ($20,000)
2. **Given** a drawdown period lasted from trade #5 to trade #12, **When** analyzing drawdown, **Then** the system shows the drawdown started on [peak date], bottomed on [trough date], and recovered on [recovery date]
3. **Given** multiple drawdown periods occurred, **When** viewing drawdown statistics, **Then** the system identifies the maximum drawdown and also shows the top 3 largest drawdown periods
4. **Given** the account never fully recovered to a new peak, **When** calculating current drawdown, **Then** the system shows the ongoing drawdown from the last peak
5. **Given** drawdown duration is measured, **When** viewing the metric, **Then** the system shows both calendar days and number of trades between peak and recovery

---

### User Story 5 - View Trades in Backtest Details UI (Priority: P2)

As a trader, when I view a backtest's details page, I want to see the trades table with all individual trades so that I can review my trading activity without leaving the web interface.

**Why this priority**: The UI provides the primary way traders interact with their backtest results. Making trade data visible in the existing backtest details page integrates naturally into the current workflow and eliminates the need to export data for basic analysis.

**Independent Test**: Can be fully tested by running a backtest, navigating to its details page, and verifying the trades section displays all trades with pagination controls. Delivers value by providing in-browser trade visibility.

**Acceptance Scenarios**:

1. **Given** a backtest has 25 trades, **When** I view the backtest details page, **Then** I see a trades section showing the first 20 trades with entry/exit prices, dates, P&L, and pagination controls to view more
2. **Given** a backtest has 500 trades, **When** I navigate the trades table, **Then** the page loads quickly (under 2 seconds) and I can page through trades in groups of 20, 50, or 100
3. **Given** I am viewing the trades table, **When** I click on a column header (e.g., "P&L" or "Entry Date"), **Then** the trades are sorted by that column in ascending/descending order
4. **Given** a backtest generated zero trades, **When** I view the details page, **Then** I see a message "No trades executed" in the trades section without errors
5. **Given** I am viewing the equity curve chart, **When** I click on a point on the curve, **Then** the trades table highlights or scrolls to the corresponding trade
6. **Given** the trades table is displayed, **When** I view trade details, **Then** I see color coding for winning trades (green) and losing trades (red) for quick visual scanning

---

### User Story 6 - Export Trade History (Priority: P3)

As a trader, I want to export my complete trade history to CSV or JSON format so that I can perform custom analysis, share results, or import into other tools.

**Why this priority**: While trade data is stored in the database, traders often need to use external tools for specialized analysis. Export capability enables flexibility without requiring direct database access.

**Independent Test**: Can be fully tested by running a backtest, exporting trades to CSV, and verifying all trade fields are present and correctly formatted. Delivers value by enabling external analysis workflows.

**Acceptance Scenarios**:

1. **Given** a backtest has completed with trades, **When** I export to CSV, **Then** I receive a file with columns for symbol, entry_date, entry_price, exit_date, exit_price, quantity, profit_loss, commission, fees, profit_pct
2. **Given** trades include fractional shares, **When** exporting to CSV, **Then** quantity values preserve decimal precision
3. **Given** I export trades to JSON, **When** opening the file, **Then** the structure includes an array of trade objects with nested details for entry and exit
4. **Given** a backtest has 100 trades, **When** exporting, **Then** all 100 trades are included in chronological order by entry date
5. **Given** trade symbols contain special characters, **When** exporting to CSV, **Then** the output is properly escaped and parseable

---

### User Story 7 - Filter and Query Trades (Priority: P3)

As a trader, I want to filter trades by criteria (symbol, date range, profit/loss threshold, holding period) so that I can analyze specific subsets of my trading history.

**Why this priority**: As trade history grows, traders need to slice data to find patterns. This builds on the foundational trade capture but isn't critical for initial MVP value.

**Independent Test**: Can be fully tested by running a backtest with varied trades, then filtering by win/loss or date range and verifying only matching trades are returned. Delivers value by enabling targeted analysis.

**Acceptance Scenarios**:

1. **Given** a backtest traded 3 different symbols, **When** I filter by symbol "AAPL", **Then** I see only trades for AAPL
2. **Given** trades occurred over 6 months, **When** I filter for date range Q1 2024, **Then** I see only trades entered during that quarter
3. **Given** I want to analyze only losing trades, **When** I filter by profit_loss < 0, **Then** the system shows only trades that lost money
4. **Given** I want to find quick trades, **When** I filter by holding_period < 3 days, **Then** I see only trades held for less than 3 days
5. **Given** multiple filters are applied, **When** querying trades, **Then** the system returns only trades matching all criteria (AND logic)

---

### Edge Cases

- What happens when a backtest completes but generates zero trades (strategy never triggered)? System should save backtest run metadata but have zero trade records, equity curve should be flat line at initial capital, UI should display "No trades executed" message gracefully.
- How does the system handle partial fills or split executions? Each execution should be recorded as a separate trade or aggregated into a single position with weighted average entry/exit prices (to be clarified based on Nautilus Trader behavior).
- What if a trade is still open when the backtest ends? System should record the entry but mark exit as null/pending, exclude from completed trade statistics, optionally close at final market price as unrealized PnL.
- How are overnight positions handled for drawdown calculations? Equity curve should update only at trade exits (realized PnL), not mark-to-market intraday unless specifically configured.
- What if commission/fee data is missing or zero? Trade records should allow null/zero values for fees, profit calculations should handle both gross and net profit scenarios.
- How does the UI handle backtests with thousands of trades (e.g., high-frequency strategies with 5000+ trades)? UI must use pagination to display trades in manageable chunks (default 20 per page), with options for 50 or 100 per page. Initial page load should fetch only the first page of trades, not all trades.
- What if sorting or filtering trades in the UI with large datasets causes performance issues? Backend API should handle sorting and filtering server-side with appropriate database indexes, returning only the requested page of results.
- How does the equity curve chart handle displaying hundreds of data points without becoming cluttered? Chart should intelligently downsample or aggregate points for display while maintaining interactive zoom/pan capabilities for detailed inspection.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST capture and persist every trade execution with entry timestamp, entry price, quantity, symbol, and direction (long/short)
- **FR-002**: System MUST capture exit details for each trade including exit timestamp, exit price, and realized profit/loss
- **FR-003**: System MUST record trade costs separately including commission amounts and fee amounts for each trade
- **FR-004**: System MUST calculate and store profit/loss for each trade in both absolute dollar amount and percentage terms
- **FR-005**: System MUST generate an equity curve showing cumulative account balance after each trade exit
- **FR-006**: System MUST calculate maximum drawdown from the equity curve including peak value, trough value, drawdown amount, and drawdown percentage
- **FR-007**: System MUST calculate drawdown duration in both calendar days and number of trades
- **FR-008**: System MUST calculate trade statistics including total trades, winning trades, losing trades, win rate, average win, average loss, largest win, largest loss
- **FR-009**: System MUST calculate consecutive win/loss streaks showing longest winning streak and longest losing streak
- **FR-010**: System MUST associate all trades with their parent backtest run via foreign key relationship
- **FR-011**: System MUST preserve chronological order of trades based on entry timestamp
- **FR-012**: System MUST support querying trades by backtest run identifier
- **FR-013**: System MUST support exporting trade history to CSV format with all trade fields
- **FR-014**: System MUST support filtering trades by symbol, date range, and profit/loss criteria
- **FR-015**: System MUST handle backtests that generate zero trades without errors
- **FR-016**: System MUST display trades in the backtest details UI page with a dedicated trades section
- **FR-017**: System MUST implement pagination for trade display with configurable page sizes (20, 50, 100 trades per page)
- **FR-018**: System MUST support server-side sorting of trades by any column (entry date, exit date, P&L, holding period, etc.)
- **FR-019**: System MUST visually distinguish winning trades from losing trades in the UI (color coding or icons)
- **FR-020**: System MUST display equity curve chart on the backtest details page with interactive features
- **FR-021**: System MUST handle UI rendering for backtests with 1000+ trades without browser performance degradation

### Key Entities *(include if feature involves data)*

- **Trade**: Represents a single completed trade execution with entry point (timestamp, price, quantity), exit point (timestamp, price), associated costs (commission, fees), realized profit/loss (dollar amount and percentage), holding period, symbol, direction (long/short), and relationship to parent backtest run

- **Equity Curve Point**: Represents account balance at a specific point in time with timestamp, cumulative balance, cumulative return percentage, trade sequence number, and relationship to backtest run

- **Drawdown Period**: Represents a peak-to-trough decline with peak timestamp, peak balance, trough timestamp, trough balance, drawdown amount, drawdown percentage, duration in days, recovery timestamp (if recovered), and relationship to backtest run

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Traders can view the complete trade history for any backtest run showing all trade entries, exits, and profit/loss
- **SC-002**: System generates equity curves for 100% of successful backtest runs with trade data
- **SC-003**: Maximum drawdown calculations match manual calculations from trade data with <0.01% error margin
- **SC-004**: Trade statistics (win rate, average win/loss, profit factor) are calculated and displayed for all completed backtests
- **SC-005**: Traders can export trade history to CSV with all 100+ trades completed in under 2 seconds
- **SC-006**: Equity curve visualization loads and displays within 1 second for backtests with up to 500 trades
- **SC-007**: Trade filtering queries return results within 500ms for datasets up to 1000 trades
- **SC-008**: 90% of traders report that trade-level visibility improves their ability to evaluate strategy quality (user feedback)
- **SC-009**: Backtest details page with trades section loads within 2 seconds for backtests with up to 500 trades
- **SC-010**: Traders can navigate between pages of trades (using pagination) with page transitions completing in under 500ms
- **SC-011**: Sorting trades by any column completes within 1 second for datasets up to 1000 trades
- **SC-012**: UI remains responsive (no freezing or lag) when displaying backtests with 1000+ trades using pagination

## Assumptions

1. **Trade completion**: We assume all trades in a backtest are eventually closed (no open positions carried beyond backtest period) OR we handle open trades by excluding them from statistics or marking to market at final price
2. **Single instrument backtests**: Initial implementation assumes one instrument per backtest run (multi-instrument support can be added later based on database schema that already includes symbol field)
3. **Realized PnL focus**: Equity curve updates only on trade exits (realized PnL), not on mark-to-market intraday movements
4. **Commission structure**: We assume commission and fee data is available from Nautilus Trader execution reports (if not available, fields can be zero/null)
5. **Chronological integrity**: We assume Nautilus Trader provides trades in chronological order by entry time or we sort them after retrieval
6. **Database storage**: Trade data will be stored in PostgreSQL using the existing backtest_runs infrastructure with a new trades table
7. **Time zones**: All timestamps are stored in UTC (consistent with existing BacktestRun model using TIMESTAMP(timezone=True))
8. **Decimal precision**: We assume trade prices and quantities require Decimal precision (not float) to avoid rounding errors in financial calculations
9. **UI integration**: We assume the existing backtest details page (specs/007-backtest-detail-view) provides a suitable location to add trades section and equity curve chart
10. **Pagination defaults**: Initial implementation uses 20 trades per page as default, with options for 50 and 100 trades per page available to users
11. **Browser compatibility**: UI features assume modern browser support for interactive charts and HTMX-based pagination (consistent with existing web UI in specs/005-webui-foundation)

## Non-Functional Requirements

### Performance
- Trade insertion should handle bulk inserts of 500+ trades in under 5 seconds
- Equity curve generation should process 1000 trades in under 1 second
- Database queries for trade history should use indexes on backtest_run_id and entry_timestamp
- API endpoints for paginated trade queries should return results within 300ms for page sizes up to 100 trades
- Backtest details page should load initial view (first page of trades + equity curve) within 2 seconds
- Server-side sorting and filtering operations should complete within 500ms for datasets up to 1000 trades

### Data Quality
- All monetary values (prices, PnL, commissions) must use Decimal type with appropriate precision
- Timestamps must preserve millisecond precision for accurate ordering
- Foreign key constraints must maintain referential integrity between trades and backtest runs

### Scalability
- Trade storage should support backtests generating 1000+ trades without performance degradation
- Equity curve calculations should remain efficient as trade count grows (use efficient algorithms, not N^2 operations)

## Dependencies

- Existing PostgreSQL database infrastructure (specs/004-postgresql-metadata-storage)
- Existing BacktestRun model and database schema
- Nautilus Trader framework providing trade execution data
- SQLAlchemy 2.0 async ORM for database operations
- Existing backtest persistence service (src/services/backtest_persistence.py)
- Existing backtest details UI page (specs/007-backtest-detail-view) for trade display integration
- Existing web UI foundation with HTMX and Tailwind CSS (specs/005-webui-foundation)
- Chart APIs for equity curve visualization (specs/008-chart-apis)

## Out of Scope

- Real-time trade tracking during backtest execution (trades are saved after backtest completes)
- Intraday equity curve updates (equity curve updates only at trade exits, not tick-by-tick)
- Portfolio-level tracking across multiple backtests or instruments (focus is single backtest run)
- Advanced trade pattern recognition or machine learning on trade data
- Trade-level annotations or manual categorization (trades are system-generated only)
- Integration with external trading platforms or live trading systems
