# Feature Specification: Multi-Condition Signal Validation

**Feature Branch**: `011-signal-validation`
**Created**: 2025-12-27
**Status**: Draft
**Input**: User description: "Research-based feature for multi-condition trading signal validation with component-based architecture, audit trails, and post-backtest analysis"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Define and Evaluate Multi-Condition Entry Signals (Priority: P1)

As a trading strategy developer, I want to define multiple entry conditions (trend filter, price pattern, Fibonacci level, volume confirmation) that evaluate together as a composite signal, so that I can create sophisticated entry logic while understanding which conditions pass or fail at each bar.

**Why this priority**: This is the core value proposition - without the ability to define and evaluate multiple conditions, the entire feature has no purpose. Strategy developers need this fundamental capability to build any multi-condition strategy.

**Independent Test**: Can be fully tested by configuring a strategy with 4 conditions (trend, pattern, Fibonacci, volume), running a backtest, and verifying that each bar produces a signal evaluation showing pass/fail for each condition and a final composite result.

**Acceptance Scenarios**:

1. **Given** a strategy with 4 configured signal conditions, **When** each bar is processed, **Then** all conditions are evaluated and a combined signal result is produced with pass/fail status for each condition.
2. **Given** a strategy with AND logic for signal combination, **When** 3 of 4 conditions pass, **Then** the final signal is FALSE and the blocking condition is identified.
3. **Given** a strategy with OR logic for signal combination, **When** 1 of 4 conditions passes, **Then** the final signal is TRUE.
4. **Given** a condition with a threshold value, **When** evaluated, **Then** the actual calculated value is captured alongside the pass/fail determination.

---

### User Story 2 - Capture Signal Audit Trail During Backtest (Priority: P2)

As a trading strategy developer, I want every signal evaluation captured and stored during backtesting, so that I can analyze the complete decision-making history after the backtest completes.

**Why this priority**: The audit trail enables debugging and analysis. Without it, users get results but cannot understand why signals fired or failed. This builds on P1's evaluation capability.

**Independent Test**: Can be fully tested by running a backtest and verifying that a complete log of all signal evaluations is available for export to CSV or analysis as a data table after the backtest ends.

**Acceptance Scenarios**:

1. **Given** a completed backtest with 1000 bars evaluated, **When** the audit trail is requested, **Then** 1000 signal evaluation records are available with timestamp, condition states, and final signal.
2. **Given** a signal evaluation record, **When** viewed, **Then** it includes: timestamp, all condition values, all condition pass/fail states, final signal result, signal strength ratio, and blocking condition name (if any).
3. **Given** a completed backtest, **When** exporting the audit trail, **Then** a CSV file is generated with all evaluation data in a tabular format.

---

### User Story 3 - Identify Blocking Conditions (Priority: P2)

As a trading strategy developer, I want to know which condition blocked a signal from triggering, so that I can identify bottlenecks in my strategy logic and tune parameters appropriately.

**Why this priority**: Same priority as audit trail because it's a key analysis insight derived from the audit data. Knowing the blocking condition is critical for parameter tuning.

**Independent Test**: Can be fully tested by running a backtest where signals frequently fail, then querying which condition blocked most often and verifying the blocking analysis statistics.

**Acceptance Scenarios**:

1. **Given** a signal evaluation where the final signal is FALSE, **When** the evaluation is recorded, **Then** the first condition that failed (in evaluation order) is identified as the blocking condition.
2. **Given** a completed backtest, **When** blocking analysis is requested, **Then** a summary shows each condition's blocking rate (how often it was the blocking condition as a percentage of failed signals).
3. **Given** a condition that blocks 80% of potential signals, **When** viewing analysis results, **Then** it is clearly identified as the primary bottleneck.

---

### User Story 4 - Calculate Signal Strength (Priority: P3)

As a trading strategy developer, I want to see how many conditions passed even when the final signal was FALSE, so that I can identify "near miss" situations and understand signal strength over time.

**Why this priority**: Signal strength provides additional insight beyond pass/fail. It helps identify when signals almost triggered, which is valuable for parameter tuning but not essential for basic operation.

**Independent Test**: Can be fully tested by running a backtest and viewing signal strength values for each bar, verifying that strength is calculated as the ratio of passed conditions to total conditions.

**Acceptance Scenarios**:

1. **Given** 4 conditions where 3 pass and 1 fails, **When** signal strength is calculated, **Then** the strength value is 0.75 (75%).
2. **Given** a completed backtest, **When** requesting near-miss analysis, **Then** all evaluations where signal strength was at least 75% but final signal was FALSE are listed.
3. **Given** a time series of signal strength values, **When** visualized, **Then** users can see how close the strategy was to triggering signals over time.

---

### User Story 5 - Analyze Component Trigger Rates (Priority: P3)

As a trading strategy developer, I want to see how often each individual condition passes across the entire backtest, so that I can identify conditions that are too strict or too loose.

**Why this priority**: This is a post-hoc analysis capability that refines understanding. It depends on the audit trail being in place and is used for strategy refinement rather than basic operation.

**Independent Test**: Can be fully tested by running a backtest and viewing a summary table showing each condition's trigger rate (percentage of bars where it passed).

**Acceptance Scenarios**:

1. **Given** a condition that passes on 10 of 100 bars, **When** trigger rate is calculated, **Then** the rate is 10%.
2. **Given** a completed backtest with 4 conditions, **When** trigger rate analysis is requested, **Then** a summary shows each condition's name and trigger rate as a percentage.
3. **Given** a condition with 5% trigger rate vs another with 95%, **When** viewing analysis, **Then** the disparity is clearly visible for tuning decisions.

---

### Edge Cases

- What happens when no conditions are configured in a composite signal?
  - System should raise a configuration error before backtest starts
- How does the system handle a condition that cannot be evaluated (e.g., insufficient data for indicator)?
  - Condition returns FALSE with a reason indicating insufficient data; this is not treated as an error
- What happens when a bar has missing or invalid price data?
  - Evaluation is skipped for that bar with a logged warning; no signal evaluation is recorded
- How does memory consumption grow over very long backtests (millions of bars)?
  - Audit data should be periodically flushed to storage to prevent unbounded memory growth

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST allow defining individual signal conditions with a name, evaluation logic, and threshold parameters.
- **FR-002**: System MUST evaluate each condition and capture: the calculated value, pass/fail determination, and a human-readable reason string.
- **FR-003**: System MUST combine multiple conditions into a composite signal using configurable logic (AND or OR).
- **FR-004**: System MUST calculate signal strength as the ratio of passed conditions to total conditions (0.0 to 1.0).
- **FR-005**: System MUST identify the first failing condition (in evaluation order) as the blocking condition when the final signal is FALSE.
- **FR-006**: System MUST record each signal evaluation with: timestamp, bar data reference, all condition states, final signal result, signal strength, and blocking condition.
- **FR-007**: System MUST provide a mechanism to export the complete audit trail to CSV format after backtest completion.
- **FR-008**: System MUST calculate post-backtest statistics including: trigger rate per condition, blocking rate per condition, and near-miss count.
- **FR-009**: System MUST support at least 10 conditions per composite signal without performance degradation.
- **FR-010**: System MUST manage memory by periodically exporting audit data during long backtests to prevent unbounded growth.
- **FR-011**: System MUST validate that at least one condition is configured before allowing backtest execution.
- **FR-012**: System MUST gracefully handle conditions that cannot evaluate (insufficient data) by returning FALSE with an explanatory reason.

### Key Entities

- **SignalComponent**: Represents a single tradeable condition with name, evaluated value, triggered status, and reason string. Encapsulates evaluation logic for one specific condition (trend filter, RSI threshold, pattern detection, etc.).
- **SignalEvaluation**: Captures the complete state of all conditions at a point in time. Contains timestamp, bar reference, list of component states, final signal result, signal strength ratio, and blocking component name.
- **CompositeSignalGenerator**: Orchestrates evaluation of multiple components. Evaluates each component in sequence, combines results using configured logic, calculates signal strength, and identifies blocking conditions.
- **SignalCollector**: Collects and stores signal evaluations during backtest execution. Provides export functionality and calculates post-backtest statistics. Runs independently from trading strategy logic.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can identify why a specific trade did not trigger at a given point in time within 30 seconds using the audit trail.
- **SC-002**: Users can determine which condition is the primary bottleneck in their strategy within 1 minute of backtest completion.
- **SC-003**: 95% of signal evaluations across all conditions complete without errors during typical backtests.
- **SC-004**: System handles backtests with 100,000+ bars without memory exhaustion or performance issues.
- **SC-005**: Exported audit data accurately reflects all signal evaluations made during the backtest with no data loss.
- **SC-006**: Users can identify all "near miss" signals (75%+ conditions met) in a single query after backtest.
- **SC-007**: Component trigger rates and blocking rates are calculated accurately to within 0.1% precision.

## Assumptions

- Users have basic familiarity with trading concepts (indicators, conditions, signals).
- Backtests are run on historical bar data with OHLCV format.
- Signal evaluation happens once per bar (on bar close by default).
- The evaluation order of conditions may affect which is identified as the "blocking" condition; users accept first-fail ordering.
- AND logic means all conditions must pass; OR logic means at least one must pass.
- Near-miss threshold of 75% signal strength is a reasonable default for identifying close calls.
- CSV export format is sufficient for post-analysis; advanced formats (Parquet, database) can be added later if needed.
- Memory management via periodic export is acceptable even if it introduces minor I/O overhead.

## Out of Scope

- Real-time signal monitoring or alerting (this feature focuses on backtesting analysis)
- Weighted signal strength (all conditions have equal weight in v1)
- Complex boolean logic beyond AND/OR (e.g., (A AND B) OR (C AND D))
- Graphical visualization tools (post-backtest charts and heatmaps are analysis layer, not core feature)
- Machine learning or automated parameter optimization based on blocking analysis
