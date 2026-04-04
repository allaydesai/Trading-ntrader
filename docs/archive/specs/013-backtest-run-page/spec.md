# Feature Specification: Backtest Run Page

**Feature Branch**: `013-backtest-run-page`
**Created**: 2026-03-20
**Status**: Draft
**Input**: User description: "I want to add a frontend page for running backtests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure and Launch a Backtest (Priority: P1)

A trader navigates to a "Run Backtest" page from the main navigation. They select a strategy from a list of available strategies, enter a trading symbol, choose a date range and data source, and submit the form. The system launches the backtest in the background and redirects the trader to the backtest detail page once complete, or shows an error if the backtest fails.

**Why this priority**: This is the core value proposition — enabling users to run backtests from the browser instead of the CLI. Without this, the page has no purpose.

**Independent Test**: Can be fully tested by filling in the form with a valid strategy/symbol/date range and submitting. Delivers the ability to run backtests without the command line.

**Acceptance Scenarios**:

1. **Given** the user is on the Run Backtest page, **When** they select a strategy, enter a symbol, choose dates and data source, and click "Run Backtest", **Then** the backtest executes and the user is redirected to the results detail page.
2. **Given** the user submits the form with valid inputs, **When** the backtest fails during execution (e.g., no data for the symbol/date range), **Then** the user sees a clear error message explaining what went wrong.
3. **Given** the user is on the Run Backtest page, **When** the page loads, **Then** all registered strategies are listed in the strategy dropdown with their descriptions.

---

### User Story 2 - Configure Strategy-Specific Parameters (Priority: P2)

After selecting a strategy, the form dynamically displays that strategy's configurable parameters (e.g., fast period, slow period, position size) with sensible defaults pre-filled. The trader can adjust these before launching the backtest.

**Why this priority**: Strategy parameters are essential for meaningful backtests, but the feature is still usable with defaults alone (P1 works without this).

**Independent Test**: Can be tested by selecting different strategies and verifying that the correct parameter fields appear with appropriate defaults.

**Acceptance Scenarios**:

1. **Given** the user selects a strategy from the dropdown, **When** the strategy has configurable parameters, **Then** the form displays input fields for each parameter with default values pre-filled.
2. **Given** the user has modified strategy parameters, **When** they switch to a different strategy, **Then** the parameter fields update to reflect the newly selected strategy's parameters and defaults.
3. **Given** the user enters an invalid parameter value (e.g., fast period greater than slow period), **When** they submit the form, **Then** validation errors are shown inline next to the invalid fields.

---

### User Story 3 - View Backtest Progress (Priority: P3)

After submitting the form, the user sees a progress indicator while the backtest runs. They can see that the system is working and are not left wondering whether the submission succeeded.

**Why this priority**: Backtests can take several seconds. Without feedback, users may resubmit or navigate away. However, the feature works without this — users would simply wait for the redirect.

**Independent Test**: Can be tested by submitting a backtest and observing that a loading/progress state appears before the redirect to results.

**Acceptance Scenarios**:

1. **Given** the user has submitted a valid backtest configuration, **When** the backtest is processing, **Then** the user sees a visual indicator that the backtest is running (e.g., spinner, progress message).
2. **Given** the backtest is in progress, **When** it completes successfully, **Then** the user is automatically redirected to the backtest detail page.
3. **Given** the backtest is in progress, **When** it fails, **Then** the progress indicator is replaced with an error message.

---

### Edge Cases

- What happens when the user submits a symbol that has no data in the selected data source? The system displays a clear error indicating data is unavailable for that symbol and date range.
- What happens when the user selects a data source that requires credentials (IBKR, Kraken) but credentials are not configured? The system shows an error indicating the data source is not available and suggests using the catalog or mock data source.
- What happens when the user submits a date range where the start date is after the end date? Client-side validation prevents submission and highlights the error.
- What happens if a backtest is already running? The submit button is disabled while a backtest is in progress, and the user sees a "backtest already in progress" message. Only one backtest can run at a time.
- What happens if a backtest exceeds the configured timeout? The system cancels the backtest and displays a timeout error, suggesting the user try a shorter date range, larger timeframe, or increase the timeout.
- What happens if the user navigates away during backtest execution? The backtest continues to run, and results are persisted to the database. The user can find the results on the backtests list page.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST display a "Run Backtest" page accessible from the main navigation bar.
- **FR-002**: System MUST present a form with fields for: strategy selection, trading symbol (free-text with format hint, e.g., "AAPL" or "AAPL.NASDAQ"), start date, end date, data source, initial capital, and execution timeout.
- **FR-003**: System MUST populate the strategy dropdown with all registered strategies and their descriptions.
- **FR-004**: System MUST populate the data source dropdown with available options (catalog, IBKR, Kraken, mock).
- **FR-005**: System MUST provide sensible defaults for all optional fields (initial capital, data source, dates).
- **FR-006**: System MUST dynamically display strategy-specific parameter fields when a strategy is selected, with defaults pre-filled.
- **FR-007**: System MUST validate all form inputs before submission (required fields, date range logic, numeric ranges).
- **FR-008**: System MUST execute the backtest using the submitted configuration and persist results to the database.
- **FR-009**: System MUST redirect the user to the backtest detail page upon successful completion.
- **FR-010**: System MUST display clear, user-friendly error messages when a backtest fails.
- **FR-011**: System MUST show a visual progress indicator while the backtest is executing.
- **FR-012**: System MUST include a timeframe selection field (1-Minute, 5-Minute, 15-Minute, 1-Hour, 4-Hour, 1-Day, 1-Week).
- **FR-013**: System MUST prevent concurrent backtest execution — the submit button is disabled while a backtest is running, with a visible "backtest already in progress" message.
- **FR-014**: System MUST provide a configurable execution timeout field with a sensible default. If the backtest exceeds the timeout, it is cancelled and the user sees a clear timeout error message.

### Key Entities

- **Backtest Configuration**: The set of inputs needed to run a backtest — strategy, symbol, date range, data source, initial capital, timeframe, and strategy-specific parameters.
- **Strategy**: A registered trading algorithm with a name, description, and configurable parameters.
- **Backtest Result**: The outcome of a backtest execution, including performance metrics, trades, and execution status (already exists in the system).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can configure and launch a backtest from the browser in under 60 seconds (from page load to submission).
- **SC-002**: 100% of registered strategies are available for selection on the Run Backtest page.
- **SC-003**: Users receive feedback (progress indicator or error) within 2 seconds of submitting the form.
- **SC-004**: All backtest results launched from the page are visible in the existing backtests list page.
- **SC-005**: Form validation catches invalid inputs before submission, preventing unnecessary backtest failures.

## Clarifications

### Session 2026-03-20

- Q: What happens when a user tries to run a backtest while another is already running? → A: Block — disable submit button and show "backtest already in progress" message. Only one backtest at a time.
- Q: How should the symbol input work — autocomplete, free-text, or split fields? → A: Free-text input with format hint placeholder (e.g., "AAPL or AAPL.NASDAQ").
- Q: Should there be a maximum execution time for backtests? → A: User-configurable timeout field with a sensible default. Backtest is cancelled with a clear error if exceeded.

## Assumptions

- The existing backtest runner and strategy registry are stable and can be invoked from the web layer without modification.
- The existing HTMX/Jinja2 templating pattern is followed for consistency with the current UI.
- Session-based authentication is not required for this page (consistent with the existing UI which has no auth).
- The backtest runs synchronously from the user's perspective (they wait for completion). Asynchronous job queuing is out of scope.
- Strategy parameter definitions (names, types, defaults, validation rules) are available from the strategy registry's param models.
