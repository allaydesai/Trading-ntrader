# Feature Specification: Interactive Backtest Lists

**Feature Branch**: `006-interactive-backtest-lists`
**Created**: 2025-11-15
**Status**: Draft
**Input**: User description: "Phase 2: Interactive Lists from @docs/NTrader-webui-specification.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Filter Backtests by Strategy (Priority: P1)

As a quantitative developer reviewing strategy performance, I want to filter the backtest list by strategy name so that I can quickly find all runs of a specific strategy without scrolling through unrelated results.

**Why this priority**: Filtering by strategy is the most common operation for developers comparing strategy variants. Without it, users must manually scan through potentially hundreds of backtests to find relevant results.

**Independent Test**: Can be fully tested by selecting a strategy from a dropdown and verifying only matching backtests appear in the table, delivering immediate value for strategy comparison workflows.

**Acceptance Scenarios**:

1. **Given** I am on the backtest list page with 50 backtests from 3 different strategies, **When** I select "SMA Crossover" from the strategy filter dropdown, **Then** only backtests using the SMA Crossover strategy appear in the table without a full page reload.

2. **Given** I have filtered by "Mean Reversion" strategy, **When** I click "Clear Filters" or select "All Strategies", **Then** all backtests are displayed again.

3. **Given** I filter by a strategy with no results, **When** the filter is applied, **Then** I see an empty table with a message "No backtests found matching your filters" and a suggestion to adjust filters.

---

### User Story 2 - Sort Backtests by Performance Metrics (Priority: P1)

As a portfolio manager evaluating strategy performance, I want to sort the backtest table by any column (Total Return, Sharpe Ratio, Max Drawdown, etc.) so that I can quickly identify the best or worst performing backtests.

**Why this priority**: Sorting is essential for performance comparison and ranks equally with filtering as a core interactive feature. Without sorting, users cannot efficiently identify top performers.

**Independent Test**: Can be fully tested by clicking column headers and verifying data reorders correctly, delivering immediate value for performance ranking.

**Acceptance Scenarios**:

1. **Given** I am viewing the backtest list table, **When** I click the "Sharpe Ratio" column header, **Then** the table reorders with highest Sharpe values at the top (descending order).

2. **Given** the table is sorted by Sharpe Ratio descending, **When** I click the "Sharpe Ratio" header again, **Then** the sort order toggles to ascending (lowest first).

3. **Given** I have sorted by "Total Return" descending, **When** I click the "Max Drawdown" header, **Then** the table re-sorts by Max Drawdown and the Total Return sort is cleared.

4. **Given** the table is sorted by a column, **When** I look at the column header, **Then** I see a visual indicator (arrow icon) showing the current sort direction.

---

### User Story 3 - Navigate Paginated Results (Priority: P2)

As a user browsing a large backtest history (100+ results), I want to navigate through pages of results so that the page loads quickly and I can access older backtests without overwhelming the browser.

**Why this priority**: Pagination improves page load performance and user experience with large datasets, but basic filtering and sorting provide more immediate value.

**Independent Test**: Can be fully tested by navigating between pages using controls and verifying correct data appears on each page.

**Acceptance Scenarios**:

1. **Given** I have 100 backtests in the database and the default page size is 20, **When** I load the backtest list page, **Then** I see the first 20 backtests and pagination controls showing "Page 1 of 5".

2. **Given** I am on page 1 of 5, **When** I click the "Next" button, **Then** the table updates to show backtests 21-40 without a full page reload.

3. **Given** I am on page 3, **When** I click the "Previous" button, **Then** the table updates to show page 2 content.

4. **Given** pagination controls are displayed, **When** I click on page number "4" directly, **Then** the table jumps to page 4 content.

5. **Given** I am on the last page, **When** I view the pagination controls, **Then** the "Next" button is disabled or hidden.

---

### User Story 4 - Filter by Instrument Symbol (Priority: P2)

As a trader analyzing strategy performance on specific instruments, I want to filter backtests by instrument symbol (e.g., AAPL, SPY) so that I can assess how strategies perform on particular assets.

**Why this priority**: Instrument filtering is important for targeted analysis but less frequently used than strategy filtering.

**Independent Test**: Can be fully tested by entering a symbol and verifying filtered results appear.

**Acceptance Scenarios**:

1. **Given** I am on the backtest list page, **When** I type "AAPL" in the instrument filter text box, **Then** only backtests run on AAPL instrument appear in the table.

2. **Given** I have typed "SP" in the instrument filter, **When** I wait briefly, **Then** I see autocomplete suggestions showing "SPY", "SPXS", and other matching symbols.

3. **Given** I have filtered by "TSLA" instrument, **When** I clear the text box, **Then** all backtests reappear.

---

### User Story 5 - Filter by Date Range (Priority: P2)

As a developer reviewing recent work, I want to filter backtests by the date they were executed so that I can focus on recent runs or analyze historical trends.

**Why this priority**: Date filtering is useful for temporal analysis but less critical than filtering by strategy or sorting by performance.

**Independent Test**: Can be fully tested by selecting date range and verifying only backtests within that period appear.

**Acceptance Scenarios**:

1. **Given** I am on the backtest list page, **When** I set the start date to "2024-01-01" and end date to "2024-01-31", **Then** only backtests executed in January 2024 appear.

2. **Given** I have set a date range filter, **When** I clear the date fields, **Then** all backtests reappear regardless of execution date.

3. **Given** I enter an end date before the start date, **When** I apply the filter, **Then** I see a validation error message and the filter is not applied.

---

### User Story 6 - Persist Filters in URL (Priority: P3)

As a team member sharing analysis results, I want my current filter and sort settings to be reflected in the page URL so that I can bookmark specific views or share filtered lists with colleagues.

**Why this priority**: URL persistence enables collaboration and bookmarking but is a convenience feature after core functionality works.

**Independent Test**: Can be fully tested by applying filters, copying URL, and verifying a new browser session loads the same filtered view.

**Acceptance Scenarios**:

1. **Given** I filter by strategy "SMA Crossover" and sort by Sharpe descending, **When** I look at the browser URL, **Then** the URL contains query parameters like `?strategy=SMA+Crossover&sort=sharpe&order=desc`.

2. **Given** a colleague shares a URL with filter parameters, **When** I open that URL in my browser, **Then** I see the backtest list with the same filters and sort order already applied.

3. **Given** I have applied filters via the UI, **When** I refresh the page, **Then** my filter and sort settings are preserved.

4. **Given** I clear all filters, **When** I look at the URL, **Then** the query parameters are removed or reset to default values.

---

### User Story 7 - Filter by Backtest Status (Priority: P3)

As a developer troubleshooting failed backtests, I want to filter by backtest status (success, failure, running) so that I can quickly find problematic runs.

**Why this priority**: Status filtering is useful for debugging but used less frequently than performance-based filtering.

**Independent Test**: Can be fully tested by selecting a status and verifying only matching backtests appear.

**Acceptance Scenarios**:

1. **Given** I have backtests with various statuses (success, failure), **When** I select "Failure" from the status filter, **Then** only failed backtests appear.

2. **Given** I filter by "Success" status, **When** I also apply strategy filter "SMA Crossover", **Then** I see only successful SMA Crossover backtests (combined filtering).

---

### Edge Cases

- What happens when filtering returns zero results? Display a clear message with suggestions to adjust filters.
- How does the system handle pagination when filters reduce results to fewer pages? Reset to page 1 and update pagination controls accordingly.
- What happens when a user directly edits URL parameters with invalid values? Ignore invalid parameters and apply only valid ones, showing the user what was applied.
- How does sorting interact with filtering? Sorting applies to the filtered result set, not the entire database.
- What happens during slow network conditions? Show a loading indicator during filter operations and maintain previous state until new data arrives.
- What if the autocomplete service for instruments is unavailable? Allow manual text entry without autocomplete suggestions.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a dropdown control to filter backtests by strategy name, populated with all distinct strategy names in the database.
- **FR-002**: System MUST provide a text input with autocomplete to filter backtests by instrument symbol.
- **FR-003**: System MUST provide date picker controls to filter backtests by execution date range (start and end dates).
- **FR-004**: System MUST provide a dropdown to filter backtests by status (success, failure, running).
- **FR-005**: System MUST allow sorting by clicking any column header in the backtest table.
- **FR-006**: System MUST toggle sort direction (ascending/descending) when clicking the same column header repeatedly.
- **FR-007**: System MUST display a visual indicator (arrow or icon) showing current sort column and direction.
- **FR-008**: System MUST paginate results with configurable page size (default: 20 results per page).
- **FR-009**: System MUST provide pagination controls including Previous/Next buttons and direct page number selection.
- **FR-010**: System MUST update the table content without full page reload when filters, sorts, or pagination change.
- **FR-011**: System MUST persist all filter and sort settings in URL query parameters.
- **FR-012**: System MUST restore filter and sort settings from URL query parameters when page loads.
- **FR-013**: System MUST support combining multiple filters simultaneously (e.g., strategy AND instrument AND date range).
- **FR-014**: System MUST provide a "Clear Filters" action to reset all filters to default state.
- **FR-015**: System MUST display a loading indicator when filter operations are in progress.
- **FR-016**: System MUST show a user-friendly message when no backtests match the applied filters.
- **FR-017**: System MUST validate date range inputs (end date must be after start date).
- **FR-018**: System MUST reset pagination to page 1 when filters change.

### Key Entities

- **Filter State**: Current values for all filter controls (strategy, instrument, date range, status), representing user's current view preferences.
- **Sort State**: Current sort column and direction (ascending/descending), determining the order of displayed results.
- **Pagination State**: Current page number and page size, controlling which subset of results is displayed.
- **Backtest List Item**: Summary information for each backtest displayed in the table (run ID, strategy, instrument, date range, performance metrics, status, timestamp).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can filter and sort the backtest list to find specific results in under 5 seconds (including response time and user interaction).
- **SC-002**: Filter and sort operations complete without full page reload and return results in under 200 milliseconds.
- **SC-003**: Backtest list page with 20 results loads in under 300 milliseconds.
- **SC-004**: System supports browsing 10,000+ backtests via pagination without performance degradation.
- **SC-005**: 90% of users successfully apply at least one filter on first attempt without external help.
- **SC-006**: Shared URLs with filter parameters correctly restore the exact filtered view for recipients.
- **SC-007**: Users can combine 3 or more filters simultaneously to narrow results precisely.
- **SC-008**: Zero data loss or inconsistency between filtered views and actual database contents.

## Assumptions

- The existing backtest list page (Phase 1) is complete with basic table rendering and routing infrastructure.
- PostgreSQL database contains backtest metadata that can be efficiently queried with filters.
- Frontend framework supports partial page updates (HTMX integration is assumed based on the Web UI specification).
- All distinct strategy names and instrument symbols are queryable from the database for populating filter dropdowns.
- The system is single-user focused (no multi-user permission concerns for filtering).
- Browser supports JavaScript for enhanced interactivity (autocomplete, partial updates).
- Network latency is typical for local development or nearby server deployment (sub-100ms round trip).
