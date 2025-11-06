# Feature Specification: PostgreSQL Metadata Storage

**Feature Branch**: `001-postgresql-metadata-storage`
**Created**: 2025-01-24
**Status**: Draft
**Input**: User description: "PostgreSQL Metadata Storage - Backtest Execution Metadata & Results Persistence - Enable users to persist, retrieve, and compare backtesting results across multiple executions"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Automatic Backtest Persistence (Priority: P1)

As a trader, when I run a backtest, the system automatically saves all execution metadata and results to the database so that I never lose results when my session ends.

**Why this priority**: This is the foundation of the entire feature. Without automatic persistence, no other functionality (comparison, reproducibility, history tracking) is possible. It delivers immediate value by preventing data loss.

**Independent Test**: Can be fully tested by running a single backtest and verifying the database contains the execution record with all metadata and performance metrics. Delivers value by eliminating data loss on terminal/system restart.

**Acceptance Scenarios**:

1. **Given** a trader has configured a backtest with strategy parameters, **When** the backtest completes successfully, **Then** the system saves execution metadata (run ID, timestamp, duration, status) and complete performance metrics to the database
2. **Given** a backtest is running, **When** the backtest fails with an error, **Then** the system saves the execution metadata with failure status and error message
3. **Given** a backtest has just completed, **When** the trader views the terminal output, **Then** they see confirmation that results were saved with a unique run identifier
4. **Given** multiple backtests run concurrently, **When** all complete, **Then** each has a separate, correctly saved record without data corruption

---

### User Story 2 - View Backtest History (Priority: P2)

As a trader, I want to list my recent backtests with key performance metrics so that I can quickly review what I've tested recently without re-running anything.

**Why this priority**: Once data is being saved (P1), the most immediate need is to access it. This enables traders to see their testing history and make informed decisions about next steps.

**Independent Test**: Can be fully tested by running multiple backtests, then using a list command to retrieve and display them with key metrics. Delivers value by providing visibility into testing history.

**Acceptance Scenarios**:

1. **Given** a trader has run 5 backtests, **When** they request their backtest history, **Then** they see all 5 runs listed with date/time, strategy name, instrument, total return, Sharpe ratio, and max drawdown
2. **Given** a trader has run 50 backtests, **When** they request recent history without specifying a limit, **Then** they see the 20 most recent backtests sorted by execution time (newest first)
3. **Given** a trader has run backtests for multiple strategies, **When** they filter by strategy name, **Then** they see only backtests for that specific strategy
4. **Given** a trader has both successful and failed backtests, **When** they view history, **Then** each entry shows a status indicator distinguishing success from failure

---

### User Story 3 - Retrieve Complete Backtest Details (Priority: P2)

As a trader, I want to view all details of a specific past backtest using its identifier so that I understand exactly what was tested and how it was configured.

**Why this priority**: Traders need to understand not just summary metrics but the complete context of any test run. This is essential for reproducibility and learning from past experiments.

**Independent Test**: Can be fully tested by retrieving a single backtest record by ID and verifying all configuration parameters, execution context, and performance metrics are displayed. Delivers value by providing complete transparency into past tests.

**Acceptance Scenarios**:

1. **Given** a trader has a backtest run identifier, **When** they request full details, **Then** they see complete strategy configuration (all parameters with values), execution settings (date range, capital, data source), performance metrics, and execution metadata
2. **Given** a backtest failed during execution, **When** the trader retrieves its details, **Then** they see the error message explaining why it failed
3. **Given** a backtest ran for a specific instrument and date range, **When** the trader views details, **Then** they see exactly which instrument was traded and the precise start/end dates used

---

### User Story 4 - Compare Multiple Backtest Runs (Priority: P3)

As a trader, I want to compare performance metrics across multiple backtests side-by-side so that I can identify which parameter combinations work best.

**Why this priority**: Once traders can view individual results, the next logical step is comparison to identify patterns and optimal configurations. This builds on P1 and P2 functionality.

**Independent Test**: Can be fully tested by selecting 3-5 saved backtests and viewing them in a comparison format that highlights metric differences. Delivers value by accelerating parameter optimization decisions.

**Acceptance Scenarios**:

1. **Given** a trader has run 5 backtests with different SMA parameters, **When** they select 3 to compare, **Then** they see all performance metrics displayed side-by-side with configuration differences highlighted
2. **Given** a trader is comparing backtests, **When** viewing the comparison, **Then** they can easily identify which run had the best Sharpe ratio, highest return, and lowest drawdown
3. **Given** a trader wants to compare different strategies, **When** they select backtests from SMA and Mean Reversion strategies, **Then** the system allows the comparison across strategy types

---

### User Story 5 - Re-run Previous Backtest (Priority: P3)

As a trader, I want to re-run a previous backtest with its exact same configuration so that I can validate reproducibility or test with updated data.

**Why this priority**: Reproducibility is critical for research integrity but requires the foundational persistence and retrieval capabilities. This enables validation and data updates.

**Independent Test**: Can be fully tested by loading a saved configuration, re-executing it, and verifying the new run creates a separate record while using identical parameters. Delivers value by enabling reproducibility validation.

**Acceptance Scenarios**:

1. **Given** a trader has a previous backtest identifier, **When** they request to re-run it, **Then** the system loads the complete configuration and executes a new backtest with identical settings
2. **Given** a backtest is being re-run, **When** it completes, **Then** the system creates a new record (not overwriting the original) and indicates this is a reproduction of the previous run
3. **Given** a trader re-runs an old backtest, **When** the original data source is no longer available, **Then** the system provides a clear error explaining the data source issue

---

### User Story 6 - Find Best Performing Runs (Priority: P3)

As a trader, I want to find my top performing backtests based on specific metrics so that I can identify the most promising strategies and parameters.

**Why this priority**: Once comparison capabilities exist, traders need efficient ways to surface the best results from potentially hundreds of tests. This enables data-driven strategy selection.

**Independent Test**: Can be fully tested by running backtests with varying performance, then sorting/filtering by metrics like Sharpe ratio to retrieve top performers. Delivers value by accelerating strategy discovery.

**Acceptance Scenarios**:

1. **Given** a trader has run 30 backtests, **When** they sort by Sharpe ratio descending, **Then** they see the top 20 runs ordered by best Sharpe ratio first
2. **Given** a trader wants to find the best SMA strategy, **When** they filter by strategy type and sort by total return, **Then** they see only SMA runs ranked by return
3. **Given** a trader is reviewing top performers, **When** they see the list, **Then** each entry shows enough context (strategy name, instrument, key parameters) to understand what made it successful

---

### User Story 7 - View Strategy Performance History (Priority: P4)

As a trader, I want to see all backtest runs for a specific strategy over time so that I can track how performance changes with different parameters or market conditions.

**Why this priority**: This provides strategic insight into strategy evolution but requires significant existing data and capabilities. It's valuable for long-term research but not critical for initial usage.

**Independent Test**: Can be fully tested by filtering all saved backtests by a single strategy name and displaying them chronologically. Delivers value by revealing strategy performance patterns over time.

**Acceptance Scenarios**:

1. **Given** a trader has tested an SMA strategy 15 times over 2 months, **When** they view the strategy history, **Then** they see all 15 runs in chronological order showing how metrics varied
2. **Given** a trader is viewing strategy history, **When** reviewing results, **Then** they can identify which parameter values were tested and how they affected performance
3. **Given** a strategy was tested in different market periods, **When** viewing history, **Then** the trader can see date ranges and correlate performance to market conditions

---

### User Story 8 - Avoid Duplicate Testing (Priority: P4)

As a trader, I want to know if I've already tested a specific parameter combination so that I don't waste computation time re-running identical backtests unintentionally.

**Why this priority**: This optimization requires sophisticated configuration matching and is most valuable after significant usage. It prevents wasted effort but isn't critical for initial feature value.

**Independent Test**: Can be fully tested by attempting to run a backtest with parameters matching a previous run and receiving a notification about the existing result. Delivers value by saving computation time.

**Acceptance Scenarios**:

1. **Given** a trader has previously tested SMA with fast=10, slow=50, **When** they configure a backtest with identical parameters for the same instrument and date range, **Then** the system notifies them a matching run exists from a specific date
2. **Given** a duplicate configuration is detected, **When** the trader sees the notification, **Then** they can choose to view the existing results or proceed with a new run anyway
3. **Given** a trader intentionally wants to re-test, **When** a match is found, **Then** the system allows them to continue without blocking the execution

---

### Edge Cases

- What happens when a backtest run is interrupted mid-execution (system crash, manual termination)?
- How does the system handle attempting to save metrics when the database connection is lost?
- What happens if two backtests complete at exactly the same timestamp?
- How does the system handle retrieving details for a backtest ID that doesn't exist?
- What happens when a trader tries to compare backtests that use completely different instruments or date ranges?
- How does the system handle querying when there are 0 saved backtests (empty database)?
- What happens if stored configuration references a strategy that no longer exists in the codebase?
- How does the system handle parameter values that are extremely large (e.g., JSON config > 100KB)?
- What happens when a trader filters by a date range that returns 10,000+ backtests?
- How does the system handle metric values that are NaN, Infinity, or undefined?

## Requirements *(mandatory)*

### Functional Requirements

#### Backtest Execution Tracking

- **FR-001**: System MUST capture and persist metadata for every backtest execution, including both success and failure cases
- **FR-002**: System MUST assign a unique, immutable identifier to each backtest run
- **FR-003**: System MUST record the complete strategy configuration as it was at execution time, stored as a snapshot independent of external files
- **FR-004**: System MUST record the instrument traded, date range tested (start and end dates), and initial capital amount
- **FR-005**: System MUST record the data source used (CSV file, IBKR connection, or mock data)
- **FR-006**: System MUST timestamp when each backtest started and calculate execution duration
- **FR-007**: For failed backtests, system MUST capture and store the complete error message and stack trace

#### Performance Metrics Storage

- **FR-008**: System MUST store standard performance metrics for each successful backtest: total return, CAGR, Sharpe ratio, Sortino ratio, maximum drawdown
- **FR-009**: System MUST store trading activity metrics: total trades executed, count of winning trades, count of losing trades, win rate percentage
- **FR-010**: System MUST store risk/reward metrics: profit factor, average winning trade amount, average losing trade amount
- **FR-011**: Metrics MUST be stored as pre-computed values that can be retrieved without recalculation
- **FR-012**: Each set of performance metrics MUST be uniquely associated with exactly one backtest run

#### Query and Retrieval

- **FR-013**: Users MUST be able to list recent backtests with a configurable limit (default 20, maximum 1000)
- **FR-014**: Users MUST be able to retrieve complete details of any backtest using its unique identifier
- **FR-015**: Users MUST be able to filter backtests by strategy name, strategy type, instrument symbol, execution date range, and status (success/failure)
- **FR-016**: Users MUST be able to sort backtests by execution date, total return, Sharpe ratio, or maximum drawdown
- **FR-017**: Users MUST be able to search for backtests matching specific configuration parameter criteria
- **FR-018**: System MUST provide retrieval performance under 100ms for individual backtest lookups by ID
- **FR-019**: System MUST provide retrieval performance under 2 seconds for filtered list queries returning up to 1000 results

#### Comparison Capabilities

- **FR-020**: Users MUST be able to select between 2 and 10 backtests for side-by-side comparison
- **FR-021**: Comparison view MUST display all performance metrics across selected runs in a tabular format
- **FR-022**: Comparison view MUST highlight configuration parameter differences between selected runs
- **FR-023**: Users MUST be able to identify the best performing run for any specific metric within the comparison set
- **FR-024**: System MUST support comparing runs across different strategies, instruments, or time periods without restriction

#### Reproducibility

- **FR-025**: Users MUST be able to extract the complete configuration snapshot from any past backtest
- **FR-026**: Users MUST be able to re-execute a backtest with configuration loaded from a previous run via a single command
- **FR-027**: Configuration snapshots MUST be preserved independently of any external files or code changes
- **FR-028**: Re-running a backtest MUST create a new backtest record without modifying or overwriting the original record
- **FR-029**: System MUST clearly indicate in the new record's metadata that it is a reproduction of a specific previous run (store original run ID reference)

#### Data Integrity

- **FR-030**: Backtest records MUST be immutable once created - the system MUST NOT allow updates to historical execution data
- **FR-031**: System MUST maintain referential integrity between backtest runs and their associated performance metrics
- **FR-032**: When a backtest run is deleted, the system MUST also delete all associated performance metrics (cascade delete)
- **FR-033**: System MUST validate all metrics before storage to ensure values are within reasonable ranges (e.g., returns between -100% and +10000%, Sharpe ratio between -10 and +10)
- **FR-034**: System MUST handle concurrent backtest executions without data corruption

### Key Entities

- **Backtest Run**: Represents a single execution of a backtest. Contains unique identifier, execution timestamp, duration, status (success/failure), error details (if failed), reference to original run if reproduction, complete strategy configuration snapshot, strategy name, strategy type, instrument symbol, date range (start/end), initial capital, and data source identifier.

- **Performance Metrics**: Represents the results of a successful backtest execution. Contains reference to associated backtest run, return metrics (total return, CAGR, annualized return), risk metrics (Sharpe ratio, Sortino ratio, max drawdown, volatility), trading metrics (total trades, winning trades, losing trades, win rate), and profit metrics (profit factor, average win amount, average loss amount).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of completed backtests (both successful and failed) are automatically saved to the database within 1 week of feature deployment
- **SC-002**: Traders can retrieve complete details of any past backtest in under 100 milliseconds
- **SC-003**: Traders can list their 20 most recent backtests in under 200 milliseconds
- **SC-004**: Traders can compare up to 10 backtests side-by-side and view results in under 2 seconds
- **SC-005**: System handles at least 10,000 stored backtest runs without performance degradation on queries
- **SC-006**: Zero data loss incidents occur - all completed backtests are successfully persisted with complete metadata
- **SC-007**: Traders can reproduce any past backtest result by loading its stored configuration and re-executing
- **SC-008**: All three existing strategies (SMA Crossover, Mean Reversion, Momentum) work correctly with automatic persistence enabled
- **SC-009**: Traders spend 50% less time re-running backtests to remember what they tested (measured by backtest execution frequency for review purposes)
- **SC-010**: Traders can find relevant past backtests using filters (strategy, instrument, date range) in under 1 second

## Scope

### In Scope

- Automatic persistence of backtest execution metadata and performance metrics
- Data storage design for backtest runs and metrics
- CLI commands for listing, retrieving, filtering, and sorting backtests
- CLI command for comparing multiple backtests side-by-side
- CLI command for re-running a backtest using stored configuration
- Integration with existing Nautilus Trader backtest execution flow
- Support for all three existing strategies (SMA, Mean Reversion, Momentum)
- Storage of complete strategy configuration snapshots
- Query performance optimization for fast retrieval
- Safe handling of concurrent backtest executions
- Error handling and validation of metrics before storage

### Out of Scope (Preliminary Phase)

- Individual trade-level storage (only aggregate metrics stored)
- Equity curve and portfolio value snapshots over time
- Parameter sweep metadata and optimization algorithm tracking
- Multi-strategy portfolio performance tracking
- Multi-user support, authentication, or access control
- Advanced analytics (statistical analysis, correlation, regression testing)
- Integration with report file metadata (reports remain separate)
- Real-time updates during backtest execution
- Market data versioning or change tracking
- Strategy code versioning or storage
- Web-based user interface (CLI only for preliminary phase)
- Automatic cleanup or archival of old backtest data
- Export capabilities to external file formats

## Assumptions

- System operates in single-user mode with no concurrent users modifying the same data
- Database is already installed, running locally, and accessible
- Existing CLI infrastructure is operational and can be extended with new commands
- Backtest execution flow is functional and stable
- Historical market data storage remains unchanged; this feature only stores metadata and results
- Required runtime environment and dependencies are available
- Backtest execution times are reasonable (under 10 minutes) so saves don't block user workflow
- Strategy configuration can be stored in a structured format without loss of information
- Metrics calculated by the backtesting engine are accurate and don't require validation beyond range checks
- Traders understand that reproductions assume strategy code hasn't changed since the original run
- Storage capacity is not a constraint (adequate disk space available for estimated 10-50 MB for 10,000 runs)

## Dependencies

### Technical Dependencies

- Database must be running and accessible from the application
- Existing CLI command infrastructure must be operational
- Backtest execution engine must be functional and return complete performance metrics
- Data validation framework must be available
- Data access layer must be configured
- Schema migration capabilities must be available

### Feature Dependencies

- Backtest execution flow must be stable and not disrupted by this feature
- Existing strategy implementations (SMA, Mean Reversion, Momentum) must continue to work unchanged
- Market data retrieval and storage must remain functional

### Milestone Dependencies

- This feature builds on completed Milestones 1-4 from the project roadmap
- No breaking changes to existing CLI commands or workflows are permitted
