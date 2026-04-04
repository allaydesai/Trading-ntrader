# Feature Specification: Backtest Detail View & Metrics

**Feature Branch**: `007-backtest-detail-view`
**Created**: 2025-11-15
**Status**: Draft
**Input**: User description: "Phase 3: Detail View & Metrics from NTrader Web UI specification"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - View Complete Backtest Results (Priority: P1)

As a quantitative developer, I want to view all performance metrics for a single backtest run so that I can evaluate the strategy's effectiveness with key risk and return indicators.

**Why this priority**: Core purpose of the detail view - without comprehensive metrics display, users cannot assess backtest performance. This is the foundational capability that all other features build upon.

**Independent Test**: Can be fully tested by navigating to a backtest detail page and verifying all metrics (Sharpe ratio, returns, drawdowns, etc.) are displayed accurately. Delivers immediate value by providing complete performance analysis.

**Acceptance Scenarios**:

1. **Given** a completed backtest exists in the system, **When** user navigates to `/backtests/{run_id}`, **Then** the page displays comprehensive metrics including Total Return, CAGR, Sharpe Ratio, Sortino Ratio, Max Drawdown, Volatility, Total Trades, Win Rate, and Profit Factor

2. **Given** a backtest detail page is displayed, **When** user views the metrics panel, **Then** positive metrics are highlighted in green, negative metrics are highlighted in red, and each metric includes a tooltip explaining its calculation

3. **Given** the CLI has generated backtest results, **When** user views the same backtest in the web UI, **Then** all metric values match exactly between CLI output and web UI display

---

### User Story 2 - Review Trade History (Priority: P2)

As a quantitative developer, I want to view and sort through all individual trades from a backtest so that I can identify patterns, analyze winning/losing trades, and debug strategy behavior.

**Why this priority**: Trade-level analysis is essential for understanding strategy execution and debugging. Builds on P1 metrics to provide granular insights into how performance was achieved.

**Independent Test**: Can be tested independently by loading trade blotter, sorting by different columns, and filtering by trade status. Delivers value by enabling detailed trade analysis without needing other features.

**Acceptance Scenarios**:

1. **Given** a backtest with trades exists, **When** user views the detail page, **Then** a trade blotter table displays all trades with columns: Timestamp, Symbol, Side (Buy/Sell), Quantity, Entry Price, Exit Price, P&L, and Status

2. **Given** the trade blotter is displayed, **When** user clicks on a column header, **Then** the table sorts by that column in ascending order, and clicking again toggles to descending order

3. **Given** a backtest has more than 100 trades, **When** user views the trade blotter, **Then** pagination controls appear allowing navigation through trade pages (default: 100 trades per page)

4. **Given** the trade blotter is displayed, **When** user applies a status filter (open, closed, profitable, losing), **Then** only trades matching that status are shown

---

### User Story 3 - View Backtest Configuration (Priority: P3)

As a quantitative developer, I want to see the exact configuration used for a backtest so that I can understand the parameters and replicate or modify the run.

**Why this priority**: Configuration transparency is important but secondary to viewing results. Users need to see what settings produced the results to reproduce or adjust them.

**Independent Test**: Can be tested by viewing configuration panel and verifying all strategy parameters are displayed. Delivers value by enabling configuration documentation and replication planning.

**Acceptance Scenarios**:

1. **Given** a backtest detail page is displayed, **When** user views the configuration section, **Then** all parameters are shown: instrument symbol, date range (start/end), initial capital, commission model, slippage, and strategy-specific parameters

2. **Given** configuration is displayed, **When** user clicks the "Copy CLI Command" button, **Then** the exact CLI command to replicate this backtest is copied to clipboard

3. **Given** the configuration section is displayed, **When** user clicks the collapse toggle, **Then** the section collapses to save space while keeping a summary visible

---

### User Story 4 - Perform Actions on Backtest (Priority: P4)

As a quantitative developer, I want to perform common actions (export report, delete, re-run) directly from the detail view so that I can efficiently manage backtests without switching to CLI.

**Why this priority**: Convenience features that improve workflow but are not essential for viewing results. CLI fallback exists for all these operations.

**Independent Test**: Can be tested by clicking each action button and verifying the expected behavior. Delivers value by streamlining common operations.

**Acceptance Scenarios**:

1. **Given** a backtest detail page is displayed, **When** user clicks "Export Report", **Then** an HTML report downloads containing all metrics and trade data (same format as CLI-generated reports)

2. **Given** a backtest detail page is displayed, **When** user clicks "Delete" button, **Then** a confirmation dialog appears requiring explicit confirmation before deletion

3. **Given** user confirms deletion, **When** deletion completes, **Then** user is redirected to backtest list page with a success notification

4. **Given** a backtest detail page is displayed, **When** user clicks "Re-run Backtest", **Then** a new backtest execution begins with the same configuration, and user is shown progress feedback

---

### Edge Cases

- What happens when backtest has zero trades (no signals generated)?
  - System displays metrics panel with zero values and empty trade blotter with informative message
- How does system handle very large backtests (10,000+ trades)?
  - Pagination prevents memory issues; trade blotter loads only current page
- What happens when user tries to access non-existent backtest run_id?
  - System returns 404 error page with helpful message and link back to backtest list
- How does system handle backtests with missing or incomplete data?
  - System displays available data with clear indicators for missing fields
- What happens when export fails due to storage issues?
  - System shows error notification with specific reason and retry option

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST display a dedicated detail page for each backtest accessible via unique URL (`/backtests/{run_id}`)
- **FR-002**: System MUST display all return metrics: Total Return %, CAGR, and Annualized Return
- **FR-003**: System MUST display all risk metrics: Sharpe Ratio, Sortino Ratio, Max Drawdown %, and Volatility
- **FR-004**: System MUST display all trading metrics: Total Trades, Win Rate %, Average Win, Average Loss, and Profit Factor
- **FR-005**: System MUST color-code metrics with green for positive/favorable values and red for negative/unfavorable values
- **FR-006**: System MUST provide tooltips for each metric explaining its calculation and interpretation
- **FR-007**: System MUST display immutable backtest configuration including instrument, date range, initial capital, commission model, slippage, and strategy parameters
- **FR-008**: System MUST provide a collapsible configuration section (expanded by default)
- **FR-009**: System MUST provide a "Copy CLI Command" button that copies replication command to clipboard
- **FR-010**: System MUST display a trade blotter table with columns: Timestamp, Symbol, Side, Quantity, Entry Price, Exit Price, P&L, Status
- **FR-011**: System MUST support sorting trade blotter by any column (ascending/descending toggle)
- **FR-012**: System MUST support filtering trade blotter by status: all, open, closed, profitable, losing
- **FR-013**: System MUST implement pagination for trade blotter when exceeding 100 trades per page
- **FR-014**: System MUST provide "Export to CSV" button for trade blotter data
- **FR-015**: System MUST provide "Export Report" button that downloads HTML report matching CLI output format
- **FR-016**: System MUST provide "Delete" button with confirmation dialog before deletion
- **FR-017**: System MUST redirect to backtest list after successful deletion with notification
- **FR-018**: System MUST provide "Re-run Backtest" button that executes backtest with same configuration
- **FR-019**: System MUST handle 404 errors gracefully for non-existent backtest IDs
- **FR-020**: System MUST display breadcrumb navigation (Dashboard > Backtests > Run Details)

### Key Entities

- **Backtest Run**: Represents a single backtest execution with unique run_id, strategy name, configuration parameters, execution timestamp, and status
- **Performance Metrics**: Calculated values derived from backtest results including returns, risk measures, and trading statistics
- **Trade Record**: Individual trade execution with entry/exit details, timestamps, quantities, prices, and profit/loss
- **Backtest Configuration**: Immutable snapshot of parameters used for the backtest run including strategy settings and execution parameters

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Detail page loads and displays all metrics within 1 second for typical backtests
- **SC-002**: All displayed metrics match CLI output values with 100% accuracy
- **SC-003**: Trade blotter handles 1000+ trades with smooth pagination (page load under 500ms)
- **SC-004**: Export Report generates identical HTML output as CLI-generated reports
- **SC-005**: Users can locate and understand any performance metric within 10 seconds of page load
- **SC-006**: Trade sorting operations complete within 200ms for tables with up to 10,000 trades
- **SC-007**: Configuration copy-to-clipboard succeeds on first click 99% of the time
- **SC-008**: Delete operation completes with user feedback within 2 seconds
- **SC-009**: 80% of users can successfully export trade data to CSV on first attempt without documentation

## Assumptions

- User has already completed Phase 2 (Interactive Backtest Lists) so navigation to detail view is available
- Backend services for retrieving backtest data, metrics, and trades already exist from CLI implementation
- PostgreSQL database contains all backtest metadata and results per existing schema
- HTML report generation exists from CLI and can be reused
- Browser supports clipboard API for copy functionality (fallback not required)
- Dark mode color scheme is already established from Phase 1/2 foundation work
