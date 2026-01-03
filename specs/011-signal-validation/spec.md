# Feature Specification: Multi-Condition Signal Validation

**Feature Branch**: `011-signal-validation`
**Created**: 2025-12-27
**Status**: Draft
**Input**: User description: "Research-based feature for multi-condition trading signal validation with component-based architecture, audit trails, and post-backtest analysis"

## User Scenarios & Testing *(mandatory)*

### User Story 0 - Integrate Signal Validation into Existing Strategy (Priority: P0)

As a trading strategy developer, I want to add signal validation to my existing Nautilus Trader strategy with minimal code changes, so that I can start capturing audit trails and analyzing my signal logic without rewriting my strategy from scratch.

**Why this priority**: This is the prerequisite for all other stories - users cannot benefit from signal validation without a clear integration path. The existing strategies (`LarryConnorsRSIMeanRev`, `SMACrossover`, etc.) need a simple migration path.

**Independent Test**: Take an existing strategy (e.g., `LarryConnorsRSIMeanRev`), add the signal validation mixin, run a backtest, and verify that signal evaluations are captured without changing the strategy's trading behavior.

**Acceptance Scenarios**:

1. **Given** an existing Nautilus Trader strategy class, **When** I add `SignalValidationMixin` and define my conditions, **Then** my strategy gains signal evaluation and audit trail capabilities without modifying my core trading logic.
2. **Given** a strategy with the mixin applied, **When** I run a backtest, **Then** the `on_bar` execution time increases by less than 5% (minimal performance overhead).
3. **Given** a strategy configuration, **When** I define signal conditions in the config (not hardcoded), **Then** I can adjust conditions without modifying strategy code.
4. **Given** an existing strategy that calls `self.submit_order()`, **When** I integrate signal validation, **Then** the order submission is automatically linked to the triggering signal evaluation.

**Example Integration**:
```python
from src.core.signals.integration import SignalValidationMixin

class LarryConnorsRSIMeanRev(SignalValidationMixin, Strategy):
    def __init__(self, config):
        super().__init__(config)
        # Define entry signal components
        self.entry_signal = self.create_composite_signal(
            name="entry",
            logic=CombinationLogic.AND,
            components=[
                TrendFilterComponent("trend", self.sma),
                RSIThresholdComponent("rsi_oversold", self.rsi, threshold=10, comparison="<"),
            ]
        )
        # Define exit signal components
        self.exit_signal = self.create_composite_signal(
            name="exit",
            logic=CombinationLogic.OR,
            components=[
                PriceBreakoutComponent("prev_high_break", comparison=">"),
                TimeStopComponent("time_stop", max_bars=5),
            ]
        )

    def on_bar(self, bar: Bar) -> None:
        # ... indicator updates ...

        # Evaluate entry signal (captures audit trail automatically)
        entry_eval = self.evaluate_signal(self.entry_signal, bar)
        if entry_eval.triggered:
            self.submit_order(...)  # Automatically linked to entry_eval
```

---

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

### User Story 6 - Evaluate Exit Signals Separately from Entry Signals (Priority: P1)

As a trading strategy developer, I want to define and evaluate exit signals (stop loss, take profit, time stop) separately from entry signals, so that I can analyze why trades were closed and optimize my exit rules independently.

**Why this priority**: Entry signals are only half the equation. Most strategy tuning involves exit optimization. Without separate exit signal tracking, users cannot analyze why trades were closed profitably or at a loss.

**Independent Test**: Run a backtest with both entry and exit signals defined, verify that the audit trail distinguishes entry evaluations from exit evaluations, and that exit signal blocking analysis is available.

**Acceptance Scenarios**:

1. **Given** a strategy with entry and exit signal generators, **When** viewing the audit trail, **Then** each evaluation is labeled with signal type ("entry" or "exit").
2. **Given** a completed backtest with 50 closed positions, **When** requesting exit analysis, **Then** I can see which exit condition triggered most often (e.g., "time_stop triggered 60%, prev_high_break triggered 40%").
3. **Given** a trade that was exited, **When** viewing the trade details, **Then** I can see the specific exit signal evaluation that caused the close.
4. **Given** multiple exit conditions with OR logic, **When** the first exit condition passes, **Then** the signal evaluates to TRUE and remaining conditions are still captured for analysis.

---

### User Story 7 - Correlate Signals to Executed Trades (Priority: P2)

As a trading strategy developer, I want to see which signal evaluation resulted in each actual trade, so that I can trace from trade outcome back to the decision that created it.

**Why this priority**: Signal evaluations are meaningless in isolation. Users need to connect "this signal triggered" to "this trade was profitable/unprofitable" to learn from outcomes.

**Independent Test**: Run a backtest, select a trade from the trades table, and retrieve the exact signal evaluation that triggered it, including all component states at that moment.

**Acceptance Scenarios**:

1. **Given** a trade with a known entry time, **When** querying the signal audit trail, **Then** I can find the exact `SignalEvaluation` record that triggered the entry order.
2. **Given** a `SignalEvaluation` that resulted in an order, **When** the order fills, **Then** the resulting trade ID is linked to the signal evaluation.
3. **Given** a backtest with 100 trades, **When** exporting signal data, **Then** each trade row includes: entry_signal_id, exit_signal_id (if closed), and the component states at both points.
4. **Given** a signal that triggered but the order was rejected or didn't fill, **When** viewing the evaluation, **Then** it shows `order_id` but `trade_id` is null, indicating the signal fired but no trade resulted.

---

### User Story 8 - Configure Signal Validation via Strategy Config (Priority: P2)

As a trading strategy developer, I want to define my signal conditions in the strategy configuration (YAML/Pydantic), so that I can experiment with different condition parameters without modifying Python code.

**Why this priority**: Rapid iteration requires parameter tuning without code changes. Users should be able to adjust thresholds, add/remove conditions, and change combination logic via configuration.

**Independent Test**: Create two YAML configs with different signal conditions for the same strategy, run both backtests, and verify that each uses the correct conditions as defined in config.

**Acceptance Scenarios**:

1. **Given** a strategy config file with `signal_components` section, **When** the strategy initializes, **Then** components are created from config without hardcoding in Python.
2. **Given** a config that specifies `rsi_threshold: 15` instead of default `10`, **When** running backtest, **Then** the RSI component uses threshold 15.
3. **Given** a config that omits a component (e.g., removes volume confirmation), **When** the signal is evaluated, **Then** only the configured components are checked.
4. **Given** a config with invalid component parameters, **When** the strategy initializes, **Then** a clear validation error is raised before backtest starts.

**Example Config**:
```yaml
strategy:
  type: connors_rsi_mean_rev
  signal_validation:
    entry:
      logic: AND
      components:
        - type: trend_filter
          name: sma_trend
          period: 200
        - type: rsi_threshold
          name: rsi_oversold
          period: 2
          threshold: 10
          comparison: "<"
    exit:
      logic: OR
      components:
        - type: price_breakout
          name: prev_high_break
          comparison: ">"
        - type: time_stop
          name: max_hold
          max_bars: 5
```

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
- **FR-013**: System MUST integrate with Nautilus Trader's `on_bar` event handler without blocking backtest execution or significantly impacting performance (<5% overhead).
- **FR-014**: System MUST support separate entry and exit signal generators, each with independent component sets and combination logic.
- **FR-015**: System MUST link `SignalEvaluation` records to resulting orders and trades when signals trigger trading actions.
- **FR-016**: System MUST support defining signal components via Pydantic configuration models, allowing YAML-based strategy configuration.
- **FR-017**: System MUST provide a `SignalValidationMixin` class that existing Nautilus Trader strategies can inherit to gain signal validation capabilities.

### Key Entities

- **SignalComponent**: Represents a single tradeable condition with name, evaluated value, triggered status, and reason string. Encapsulates evaluation logic for one specific condition (trend filter, RSI threshold, pattern detection, etc.).
- **SignalEvaluation**: Captures the complete state of all conditions at a point in time. Contains timestamp, bar reference, list of component states, final signal result, signal strength ratio, blocking component name, signal type (entry/exit), and optional order_id/trade_id for correlation.
- **CompositeSignalGenerator**: Orchestrates evaluation of multiple components. Evaluates each component in sequence, combines results using configured logic, calculates signal strength, and identifies blocking conditions.
- **SignalCollector**: Collects and stores signal evaluations during backtest execution. Provides export functionality and calculates post-backtest statistics. Runs independently from trading strategy logic.
- **SignalValidationMixin**: A mixin class that existing Nautilus Trader strategies can inherit to gain signal validation capabilities. Provides `create_composite_signal()` and `evaluate_signal()` methods, and wraps `submit_order()` to automatically link orders to triggering signals.

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

---

### User Story 9 - Run Backtest with Signal Validation Enabled (Priority: P1)

As a trading strategy developer, I want to run a backtest with signal validation enabled via the CLI, so that I can capture signal audit data without modifying my backtest workflow.

**Why this priority**: This bridges the gap between defining signals (US0) and analyzing them (US2-5). Without CLI integration, users cannot easily run signal-enabled backtests.

**Independent Test**: Run `ntrader backtest run --strategy crsi --symbol AAPL --enable-signals`, verify that signal evaluations are captured and exported to CSV automatically.

**Acceptance Scenarios**:

1. **Given** a strategy with SignalValidationMixin, **When** I run `backtest run --enable-signals`, **Then** the backtest executes normally AND signal evaluations are captured.
2. **Given** a completed backtest with signals enabled, **When** the backtest finishes, **Then** a signal audit CSV is automatically exported to the output directory.
3. **Given** a backtest run with `--signal-export-path ./signals.csv`, **When** the backtest completes, **Then** the audit trail is written to the specified path.
4. **Given** a backtest with signals enabled, **When** viewing CLI results, **Then** a summary shows: total evaluations, trigger rate, and primary blocking condition.

**CLI Integration**:
```bash
# Enable signal validation for backtest
ntrader backtest run --strategy crsi --symbol AAPL \
    --start 2024-01-01 --end 2024-06-01 \
    --enable-signals \
    --signal-export-path ./output/signals.csv

# Output includes signal summary:
# 🎯 Backtest Results
# ...
# 📊 Signal Analysis
#    Total Evaluations: 1,234
#    Entry Signal Trigger Rate: 8.2%
#    Primary Blocker: rsi_oversold (blocked 72% of failed signals)
#    Near-Misses (≥75% strength): 45
#    Audit Trail: ./output/signals.csv
```

---

### User Story 10 - View Signal Summary in WebUI (Priority: P2)

As a trading strategy developer, I want to see signal analysis summary on the backtest detail page in the WebUI, so that I can quickly understand signal behavior without downloading CSV files.

**Why this priority**: The WebUI already shows backtest details (007-backtest-detail-view). Adding a signal summary tab provides immediate visibility without requiring separate tools.

**Independent Test**: Run a backtest with signals enabled, navigate to backtest detail page, verify signal statistics are displayed in a dedicated tab.

**Acceptance Scenarios**:

1. **Given** a backtest with signal data, **When** viewing the detail page, **Then** a "Signals" tab appears in the navigation.
2. **Given** the Signals tab is selected, **When** the page loads, **Then** I see: trigger rates per component, blocking rates, and near-miss count.
3. **Given** the Signals tab, **When** I click "Export CSV", **Then** the full audit trail downloads as a CSV file.
4. **Given** a backtest without signal data, **When** viewing the detail page, **Then** the Signals tab shows "No signal data available" with instructions.

**WebUI Integration** (minimal):
- Add "Signals" tab to existing backtest detail page
- Display signal statistics summary (reuse SignalStatistics from US5)
- Add CSV download button for full audit trail
- No complex visualizations in v1 (charts are out of scope)

---

### User Story 11 - Validate Signal Configuration Before Backtest (Priority: P2)

As a trading strategy developer, I want the system to validate my signal configuration before running a backtest, so that I catch configuration errors early rather than mid-execution.

**Why this priority**: Edge cases like missing conditions or invalid thresholds should fail fast with clear error messages.

**Independent Test**: Create a config with zero conditions, attempt to run backtest, verify clear error message before execution starts.

**Acceptance Scenarios**:

1. **Given** a signal config with zero components, **When** the backtest starts, **Then** a clear error is raised: "Signal 'entry' requires at least one component".
2. **Given** a component with invalid threshold (e.g., RSI threshold > 100), **When** config is loaded, **Then** Pydantic validation fails with specific field error.
3. **Given** a component referencing an indicator not available in warmup period, **When** the first bar is processed, **Then** the component returns FALSE with reason "Insufficient data: RSI requires 14 bars".

---

## Requirements *(mandatory)* - Integration Additions

### Additional Functional Requirements

- **FR-018**: System MUST provide CLI flags `--enable-signals` and `--signal-export-path` for the `backtest run` command.
- **FR-019**: System MUST automatically export signal audit trail to CSV when backtest completes with signals enabled.
- **FR-020**: System MUST display signal summary (total evaluations, trigger rate, primary blocker) in CLI output when signals are enabled.
- **FR-021**: System MUST persist signal statistics alongside backtest run in the database for WebUI display.
- **FR-022**: System MUST validate signal configuration before backtest execution, failing fast with clear error messages.
- **FR-023**: System MUST integrate with existing WebUI backtest detail page to display signal statistics tab.

---

## Out of Scope

- Real-time signal monitoring or alerting (this feature focuses on backtesting analysis)
- Weighted signal strength (all conditions have equal weight in v1)
- Complex boolean logic beyond AND/OR (e.g., (A AND B) OR (C AND D))
- Graphical visualization tools (post-backtest charts and heatmaps are analysis layer, not core feature)
- Machine learning or automated parameter optimization based on blocking analysis
- Signal comparison across multiple backtest runs (can be added in future iteration)
