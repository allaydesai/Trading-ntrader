# PR #16 Code Review: Apolo RSI Strategy & Backtest Command Unification

**PR URL**: https://github.com/allaydesai/Trading-ntrader/pull/16
**Branch**: `strategy_apolo` → `main`
**Review Date**: 2026-01-25
**Reviewer**: Claude Code
**Status**: Approved with Required Changes

---

## Executive Summary

This PR introduces a new RSI mean reversion trading strategy and consolidates the backtest CLI architecture. It's a substantial refactoring effort that improves code organization, reduces duplication, and enhances user experience.

| Metric | Value |
|--------|-------|
| Lines Added | 5,418 |
| Lines Deleted | 669 |
| Files Changed | 23 |
| New Tests | 64 total (37 unit + 27 component) |
| Test Coverage | Good |

### Overall Verdict: **Approve with Required Changes**

---

## Table of Contents

1. [Changes Overview](#changes-overview)
2. [Findings Summary](#findings-summary)
3. [Detailed Findings](#detailed-findings)
4. [Phased Resolution Plan](#phased-resolution-plan)
5. [Testing Checklist](#testing-checklist)
6. [Appendix](#appendix)

---

## Changes Overview

### New Features

| Feature | Files | Description |
|---------|-------|-------------|
| Apolo RSI Strategy | `src/core/strategies/apolo_rsi.py` | 2-period RSI mean reversion (long only) |
| Unified `backtest run` | `src/cli/commands/backtest.py` | Supports both CLI args and YAML configs |
| Catalog Data Source | `src/core/backtest_runner.py` | Load OHLCV from Nautilus catalog with IBKR fallback |

### Architecture Improvements

| Component | Lines | Purpose |
|-----------|-------|---------|
| `BacktestOrchestrator` | 478 | Orchestrates backtest workflows with persistence |
| `ResultsExtractor` | 338 | Computes comprehensive metrics from engine results |
| `_backtest_helpers.py` | 616 | Shared CLI utilities for data loading, execution, display |
| `BacktestRequest` | 287 | Unified request model for CLI and YAML modes |

### Removed

| Component | Lines Removed | Reason |
|-----------|---------------|--------|
| `run-config` command | ~370 | Deprecated in favor of unified `run` command |

---

## Findings Summary

### By Severity

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 1 | Must fix before merge |
| Major | 2 | Should fix before merge |
| Minor | 4 | Fix in follow-up |
| Suggestion | 2 | Optional improvements |

### Quick Reference

| ID | Severity | Title | Phase |
|----|----------|-------|-------|
| F-001 | Critical | RSI Parameter Range Mismatch | 1 |
| F-002 | Major | File Size Exceeds 500-Line Limit | 2 |
| F-003 | Major | Missing Test for `_make_json_serializable` | 1 |
| F-004 | Minor | Type Annotation Inconsistency | 3 |
| F-005 | Minor | Fragile Strategy Type Extraction | 3 |
| F-006 | Minor | Hardcoded NASDAQ Venue | 3 |
| F-007 | Minor | Missing Newline at EOF | 1 |
| S-001 | Suggestion | Add Warning for Fallback Instrument | 3 |
| S-002 | Suggestion | Add Integration Test | 2 |

---

## Detailed Findings

### F-001: RSI Parameter Range Mismatch (Critical)

**Location**:
- `src/models/strategy.py:218-222` (ApoloRSIParameters)
- `src/core/strategies/apolo_rsi.py:47-48` (ApoloRSIConfig)

**Issue**: The parameter model and strategy config use different RSI value ranges.

**ApoloRSIParameters (0-100 range)**:
```python
class ApoloRSIParameters(BaseModel):
    buy_threshold: float = Field(default=10.0, ge=0, le=100, ...)
    sell_threshold: float = Field(default=50.0, ge=0, le=100, ...)
```

**ApoloRSIConfig (0-1 range)**:
```python
class ApoloRSIConfig(StrategyConfig):
    # Note: Nautilus Trader's RSI indicator returns values in 0-1 range
    buy_threshold: float = 0.10   # Traditional RSI < 10
    sell_threshold: float = 0.50  # Traditional RSI > 50
```

**Impact**: Users creating strategies via the parameter model will get incorrect RSI thresholds (10.0 instead of 0.10), causing no trades to trigger.

**Resolution**:
```python
# Option A: Update ApoloRSIParameters to use 0-1 range
class ApoloRSIParameters(BaseModel):
    buy_threshold: float = Field(
        default=0.10, ge=0.0, le=1.0,
        description="Buy when RSI < threshold (0.10 = RSI < 10)"
    )
    sell_threshold: float = Field(
        default=0.50, ge=0.0, le=1.0,
        description="Sell when RSI > threshold (0.50 = RSI > 50)"
    )

# Option B: Add conversion in strategy loader (not recommended)
```

---

### F-002: File Size Exceeds 500-Line Limit (Major)

**Location**: `src/cli/commands/_backtest_helpers.py` (616 lines)

**Issue**: Per CLAUDE.md guidelines: "Never create a file longer than 500 lines of code."

**Impact**: Reduced maintainability, harder to test in isolation.

**Resolution**: Split into focused modules:

```
src/cli/commands/
├── _backtest_helpers/
│   ├── __init__.py          # Re-exports all public functions
│   ├── data_loading.py      # DataLoadResult, load_backtest_data, _load_*
│   ├── request_resolver.py  # resolve_backtest_request, apply_cli_overrides
│   └── display.py           # display_backtest_results
```

---

### F-003: Missing Test for `_make_json_serializable` (Major)

**Location**: `src/core/backtest_orchestrator.py:40-56`

**Issue**: This utility function handles recursive Decimal conversion for database storage but has no dedicated test.

**Code**:
```python
def _make_json_serializable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, Decimal):
        return str(obj)
    return obj
```

**Resolution**: Add unit test in `tests/unit/core/test_backtest_orchestrator.py`:
```python
class TestMakeJsonSerializable:
    def test_converts_decimal_to_string(self):
        result = _make_json_serializable(Decimal("100.50"))
        assert result == "100.50"
        assert isinstance(result, str)

    def test_handles_nested_dict_with_decimals(self):
        input_data = {"price": Decimal("150.00"), "nested": {"qty": Decimal("10")}}
        result = _make_json_serializable(input_data)
        assert result == {"price": "150.00", "nested": {"qty": "10"}}

    def test_handles_list_with_decimals(self):
        input_data = [Decimal("1.0"), Decimal("2.0")]
        result = _make_json_serializable(input_data)
        assert result == ["1.0", "2.0"]

    def test_preserves_non_decimal_types(self):
        input_data = {"name": "test", "count": 5, "active": True}
        result = _make_json_serializable(input_data)
        assert result == input_data
```

---

### F-004: Type Annotation Inconsistency (Minor)

**Location**: `src/cli/commands/_backtest_helpers.py`

**Issue**: Mixed usage of `Literal["catalog", "mock"]` and `str | None` for `data_source`.

**Examples**:
- Line 87: `data_source: Literal["catalog", "mock"]`
- Line 868: `data_source: str | None`

**Resolution**: Standardize on `Literal` type:
```python
from typing import Literal

DataSourceType = Literal["catalog", "mock"]

async def load_backtest_data(
    data_source: DataSourceType,
    ...
)

def resolve_backtest_request(
    data_source: DataSourceType | None,
    ...
)
```

---

### F-005: Fragile Strategy Type Extraction (Minor)

**Location**: `src/models/backtest_request.py:111`

**Issue**: Strategy type extraction from path may not match registry keys.

**Code**:
```python
strategy_type = strategy_path.split(":")[-1].lower() if ":" in strategy_path else "unknown"
# "src.core.strategies.apolo_rsi:ApoloRSI" → "apolorsi" (not "apolo_rsi")
```

**Impact**: Could cause registry lookup failures for strategies with underscores.

**Resolution**: Add explicit mapping or use strategy name from registry:
```python
# Option A: Parse module name instead of class name
module_part = strategy_path.split(":")[0].split(".")[-1]  # "apolo_rsi"

# Option B: Add strategy_type field to YAML config (recommended)
# YAML:
#   strategy_type: apolo_rsi
#   strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
```

---

### F-006: Hardcoded NASDAQ Venue (Minor)

**Location**: `src/cli/commands/_backtest_helpers.py:842-843`

**Issue**: Symbols without venue suffix default to NASDAQ.

**Code**:
```python
if "." not in symbol_upper:
    instrument_id = f"{symbol_upper}.NASDAQ"
```

**Impact**: NYSE or ARCA symbols require explicit `.NYSE` or `.ARCA` suffix.

**Resolution**: Add `--venue` option or document requirement:
```python
@click.option("--venue", "-v", default="NASDAQ",
              type=click.Choice(["NASDAQ", "NYSE", "ARCA"]),
              help="Default venue for symbols without suffix")
```

---

### F-007: Missing Newline at EOF (Minor)

**Location**: `configs/apolo_rsi_qqq.yaml`

**Issue**: File ends without newline, which can cause issues with some tools/linters.

**Resolution**: Add newline at end of file.

---

### S-001: Add Warning for Fallback Instrument (Suggestion)

**Location**: `src/cli/commands/_backtest_helpers.py:1250-1256`

**Issue**: When IBKR instrument fetch fails, a fallback test instrument is used silently.

**Current Behavior**:
```python
except Exception as e:
    console.print(f"Failed to fetch instrument from IBKR: {e}", style="red")
    instrument, _ = create_test_instrument(symbol, venue)
    console.print("Using fallback test instrument", style="yellow")
```

**Recommendation**: Make this more prominent or consider failing:
```python
console.print(
    "WARNING: Using synthetic test instrument - backtest results may be inaccurate!",
    style="bold red"
)
```

---

### S-002: Add Integration Test (Suggestion)

**Issue**: No end-to-end integration test for the full config-to-execution flow.

**Recommendation**: Add integration test:
```python
# tests/integration/test_backtest_config_mode.py
class TestBacktestConfigModeIntegration:
    @pytest.mark.integration
    def test_full_config_execution_with_mock_data(self, tmp_path):
        """Test complete flow: YAML config → backtest → results."""
        # Create config file
        config = tmp_path / "test_config.yaml"
        config.write_text("""
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "TEST.SIM"
  bar_type: "TEST.SIM-1-DAY-LAST-EXTERNAL"
  trade_size: 100
  order_id_tag: "TEST"
backtest:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  initial_capital: 100000
""")

        runner = CliRunner()
        result = runner.invoke(run_backtest, [str(config), "--data-source", "mock"])

        assert result.exit_code == 0
        assert "Backtest Results" in result.output
```

---

## Phased Resolution Plan

### Phase 1: Critical & Blocking (Before Merge)

**Estimated Effort**: 1-2 hours

| Task | Finding | Priority | Status |
|------|---------|----------|--------|
| Fix RSI parameter range in `ApoloRSIParameters` | F-001 | P0 | [ ] |
| Add test for `_make_json_serializable` | F-003 | P0 | [ ] |
| Add newline to `apolo_rsi_qqq.yaml` | F-007 | P0 | [ ] |
| Run full test suite | - | P0 | [ ] |

**Commands**:
```bash
# After fixes
uv run ruff format .
uv run ruff check .
uv run pytest tests/unit/ tests/component/ -v
```

---

### Phase 2: Architectural Improvements (Post-Merge Sprint)

**Estimated Effort**: 3-4 hours

| Task | Finding | Priority | Status |
|------|---------|----------|--------|
| Split `_backtest_helpers.py` into submodules | F-002 | P1 | [ ] |
| Add integration test for config mode | S-002 | P1 | [ ] |
| Update imports across codebase | F-002 | P1 | [ ] |

**Implementation**:
```
# New structure
src/cli/commands/_backtest_helpers/
├── __init__.py           # Re-export: DataLoadResult, load_backtest_data, etc.
├── data_loading.py       # ~200 lines
├── request_resolver.py   # ~180 lines
└── display.py            # ~100 lines
```

---

### Phase 3: Code Quality Enhancements (Technical Debt)

**Estimated Effort**: 2-3 hours

| Task | Finding | Priority | Status |
|------|---------|----------|--------|
| Standardize `DataSourceType` annotation | F-004 | P2 | [ ] |
| Fix strategy type extraction from path | F-005 | P2 | [ ] |
| Add `--venue` CLI option | F-006 | P2 | [ ] |
| Improve fallback instrument warning | S-001 | P3 | [ ] |

---

## Testing Checklist

### Before Merge

- [ ] All existing tests pass (`uv run pytest`)
- [ ] Linting passes (`uv run ruff check .`)
- [ ] Type checking passes (`uv run mypy src/`)
- [ ] Manual test: `backtest run --symbol AAPL --start 2024-01-01 --end 2024-01-31`
- [ ] Manual test: `backtest run configs/apolo_rsi_amd.yaml --data-source mock`
- [ ] Manual test: `backtest run configs/apolo_rsi_amd.yaml --start 2024-06-01 --data-source mock`

### After Phase 1

- [ ] RSI thresholds work correctly (verify trades trigger at 0.10/0.50)
- [ ] Decimal serialization test passes
- [ ] Config YAML files pass linting

### After Phase 2

- [ ] All imports still work after module split
- [ ] Integration test passes
- [ ] No import cycles introduced

---

## Appendix

### A. Files Changed

```
.claude/commands/commit.md                      |   1 -
README.md                                        |  40 +-
configs/apolo_rsi_amd.yaml                       |  22 +
configs/apolo_rsi_qqq.yaml                       |  22 +
docs/BACKTEST_RUN_CONSOLIDATION.md               | 590 +++++++
src/cli/commands/_backtest_helpers.py            | 616 +++++++
src/cli/commands/backtest.py                     | 702 ++++----
src/cli/commands/strategy.py                     |   4 +-
src/core/backtest_orchestrator.py                | 478 +++++
src/core/backtest_runner.py                      | 184 +-
src/core/results_extractor.py                    | 338 ++++
src/core/strategies/apolo_rsi.py                 | 150 ++
src/models/__init__.py                           |   2 +
src/models/backtest_request.py                   | 287 +++
src/models/strategy.py                           |  54 +
src/utils/bar_type_utils.py                      |  38 +
src/utils/mock_data.py                           | 168 +-
tests/component/test_apolo_rsi_strategy.py       | 155 ++
tests/component/test_backtest_commands.py        | 697 ++++----
tests/unit/cli/test_backtest_helpers.py          | 1109 ++++++++++++
tests/unit/core/test_results_extractor.py        | 192 ++
tests/unit/models/test_backtest_request.py       | 185 ++
tests/unit/utils/test_bar_type_utils.py          |  53 +
```

### B. New Dependencies

None added.

### C. Breaking Changes

- `backtest run-config` command removed (deprecated in previous commits)
- Users must migrate to `backtest run <config.yaml>` syntax

### D. Related Issues

- Closes backtest command unification initiative
- Part of strategy expansion roadmap

---

## Sign-Off

| Role | Name | Date | Status |
|------|------|------|--------|
| Reviewer | Claude Code | 2026-01-25 | Approved with changes |
| Author | - | - | Pending |
| Maintainer | - | - | Pending |

---

*Document generated by Claude Code review on 2026-01-25*
