# Backtest Run Command Consolidation

## Development Plan

**Author:** Development Team
**Created:** 2025-01-24
**Status:** In Progress (Phases 1-3 Complete)
**Branch:** `strategy_apolo`
**Last Updated:** 2025-01-24

---

## 1. Problem Statement

The backtest CLI currently has two commands for running backtests:
- `backtest run` - accepts CLI flags for all parameters
- `backtest run-config` - accepts a YAML configuration file

This creates several issues:

1. **Code Duplication** - 155-170 lines of duplicated logic across 733 total lines
2. **Maintenance Overhead** - Two code paths, two test suites, two sets of bugs
3. **User Confusion** - Which command should users choose?
4. **Behavioral Inconsistencies**:
   - `run` has IBKR auto-fetch; `run-config` does not
   - `run` multiplies starting balance by 10x; `run-config` uses direct value
   - `run` auto-detects timeframe; `run-config` requires explicit bar_type

## 2. Current State Analysis

### Code Metrics

**Before Phase 1:**
| Component | Lines of Code | Location |
|-----------|---------------|----------|
| `run_backtest()` | 417 lines | Lines 77-492 |
| `run_config_backtest()` | 316 lines | Lines 494-809 |
| **Total** | **868 lines** | backtest.py |

**After Phase 1:**
| Component | Lines of Code | Location |
|-----------|---------------|----------|
| `backtest.py` | 590 lines | CLI commands (reduced) |
| `_backtest_helpers.py` | 335 lines | Shared helper functions (new) |
| **Total** | **925 lines** | Combined (but deduplicated logic) |

**After Phase 2/3:**
| Component | Lines of Code | Location |
|-----------|---------------|----------|
| `backtest.py` | 639 lines | CLI commands (unified run command) |
| `_backtest_helpers.py` | 616 lines | Shared helpers + resolve/override functions |
| **Total** | **1255 lines** | Combined |

**Note:** Line count increased due to:
- New `resolve_backtest_request()` function (~100 lines)
- New `apply_cli_overrides()` function (~50 lines)
- Internal helper functions `_resolve_config_mode()` and `_resolve_cli_mode()` (~80 lines)
- Enhanced docstrings and validation logic

**Key Improvement:** `run_backtest()` now handles both CLI and config modes through `resolve_backtest_request()`. The `run_config_backtest()` command remains temporarily for backward compatibility but will be deprecated in Phase 5.

### Duplicated Logic

| Section | Approximate LOC | Description |
|---------|-----------------|-------------|
| Data loading & availability | 110-120 | Catalog service init, availability check, bar loading |
| Orchestrator execution | 15 | Setup, execute, dispose pattern |
| Results display | 40-50 | Table creation, metrics formatting |
| **Total Duplicated** | **~165** | 22% of combined code |

### Feature Matrix

| Feature | `run` | `run-config` | Target |
|---------|-------|--------------|--------|
| CLI parameter input | ✅ | ❌ | ✅ |
| YAML config input | ❌ | ✅ | ✅ |
| CLI overrides for config | N/A | ❌ | ✅ |
| Catalog data source | ✅ | ✅ | ✅ |
| Mock data source | ❌ | ✅ | ✅ |
| IBKR auto-fetch | ✅ | ❌ | ✅ |
| Timeframe auto-detect | ✅ | ❌ | ✅ |
| Consistent starting balance | ❌ (10x multiplier) | ✅ (direct) | ✅ (direct) |
| Persistence | ✅ | ✅ | ✅ |

## 3. Proposed Solution

### Unified Command Interface

```bash
# Config-based (recommended for reproducibility)
ntrader backtest run configs/apolo_rsi_amd.yaml

# Config with CLI overrides
ntrader backtest run configs/apolo_rsi_amd.yaml --start 2024-01-01 --end 2024-06-01

# Config with data source override
ntrader backtest run configs/apolo_rsi_amd.yaml --data-source mock

# Pure CLI mode (quick exploration)
ntrader backtest run --symbol AAPL --strategy sma_crossover \
    --start 2024-01-01 --end 2024-12-31
```

### Design Principles

1. **Config file as optional positional argument**
   - If first argument is a `.yaml` file, use config mode
   - Otherwise, use CLI mode with required flags

2. **CLI flags override config values**
   - `--start`, `--end` override `backtest.start_date/end_date`
   - `--data-source` override default catalog behavior
   - `--starting-balance` override `backtest.initial_capital`
   - `--symbol` override `config.instrument_id`

3. **Unified data pipeline**
   - Primary: Load from Parquet catalog
   - Fallback: Auto-fetch from IBKR if catalog incomplete
   - Alternative: Generate mock data with `--data-source mock`

4. **Consistent starting balance**
   - Direct value, no hidden multipliers
   - Default: 100,000 (matching current YAML configs)

5. **Persistence by default**
   - Always persist unless `--no-persist` specified
   - Enables viewing results in Web UI

## 4. Architecture

### Command Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     backtest run                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Input Resolution                        │   │
│  │  ┌───────────────┐     ┌───────────────┐           │   │
│  │  │  YAML Config  │ OR  │   CLI Flags   │           │   │
│  │  └───────┬───────┘     └───────┬───────┘           │   │
│  │          │                     │                    │   │
│  │          └──────────┬──────────┘                    │   │
│  │                     ▼                               │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │  apply_cli_overrides(config, cli_args)      │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              BacktestRequest                         │   │
│  │  (unified model regardless of input source)          │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            │                               │
├────────────────────────────┼───────────────────────────────┤
│  Data Loading              │                               │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  load_backtest_data(request, data_source)           │   │
│  │                                                      │   │
│  │  if data_source == "mock":                          │   │
│  │      return generate_mock_data()                    │   │
│  │                                                      │   │
│  │  # Check catalog availability                       │   │
│  │  availability = catalog.get_availability()          │   │
│  │                                                      │   │
│  │  if catalog_has_full_coverage:                      │   │
│  │      return catalog.load_bars()                     │   │
│  │                                                      │   │
│  │  if partial_coverage and ibkr_available:            │   │
│  │      fetch_missing_from_ibkr()                      │   │
│  │      return catalog.load_bars()                     │   │
│  │                                                      │   │
│  │  raise InsufficientDataError()                      │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            │                               │
├────────────────────────────┼───────────────────────────────┤
│  Execution                 │                               │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  execute_backtest(request, bars, instrument)        │   │
│  │                                                      │   │
│  │  orchestrator = BacktestOrchestrator()              │   │
│  │  result, run_id = orchestrator.execute(...)         │   │
│  │  orchestrator.dispose()                             │   │
│  └─────────────────────────┬───────────────────────────┘   │
│                            │                               │
├────────────────────────────┼───────────────────────────────┤
│  Output                    │                               │
│                            ▼                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  display_backtest_results(result, run_id)           │   │
│  │                                                      │   │
│  │  - Format metrics table                             │   │
│  │  - Show persistence info                            │   │
│  │  - Print performance summary                        │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Module Structure

```
src/cli/commands/
├── backtest.py              # CLI command definitions (slimmed down)
└── _backtest_helpers.py     # Extracted helper functions
    ├── DataLoadResult       # Dataclass for data loading results
    ├── apply_cli_overrides()      # ✅ Phase 3: Apply CLI overrides to request
    ├── resolve_backtest_request() # ✅ Phase 2: Resolve inputs to BacktestRequest
    ├── _resolve_config_mode()     # Internal: Handle YAML config mode
    ├── _resolve_cli_mode()        # Internal: Handle CLI flag mode
    ├── load_backtest_data()       # ✅ Phase 1: Unified data loading
    ├── _load_mock_data()          # Internal: Generate mock data
    ├── _load_catalog_data()       # Internal: Load from catalog with IBKR fallback
    ├── execute_backtest()         # ✅ Phase 1: Orchestrator execution wrapper
    └── display_backtest_results() # ✅ Phase 1: Results table formatting
```

## 5. Implementation Phases

### Phase 1: Extract Helper Functions ✅ COMPLETE
**Goal:** Reduce duplication without changing user-facing behavior

**Tasks:**
- [x] Create `src/cli/commands/_backtest_helpers.py`
- [x] Extract `load_backtest_data()` - catalog/IBKR/mock data loading
- [x] Extract `execute_backtest()` - orchestrator setup and execution
- [x] Extract `display_backtest_results()` - table formatting and output
- [x] Update both commands to use shared helpers
- [x] Ensure all existing tests pass

**Estimated LOC Change:** -150 lines (deduplication)
**Actual LOC Change:** -278 lines from backtest.py (exceeded goal!)

**Implementation Details (2025-01-24):**
- Created `_backtest_helpers.py` with 335 lines containing:
  - `DataLoadResult` dataclass for unified return type
  - `load_backtest_data()` - handles catalog with IBKR fallback + mock generation
  - `execute_backtest()` - wraps orchestrator with Progress spinner and cleanup
  - `display_backtest_results()` - Rich table formatting with performance summary
- Reduced `backtest.py` from 868 lines to 590 lines
- Created 19 unit tests with 92% coverage for helper functions
- All 41 tests pass (19 helper + 22 component)

### Phase 2: Add Config File Support to `run` ✅ COMPLETE
**Goal:** Enable `backtest run config.yaml` syntax

**Tasks:**
- [x] Add optional `config_file` positional argument to `run_backtest`
- [x] Create `resolve_backtest_request()` that handles both YAML and CLI input
- [x] Implement input mode detection (YAML file vs CLI flags)
- [x] Add validation for mutually exclusive inputs
- [x] Add tests for config file mode

**Implementation Details (2025-01-24):**
- Modified `run_backtest()` command in `backtest.py` to accept optional `config_file` positional argument
- Created `resolve_backtest_request()` function in `_backtest_helpers.py` that:
  - Detects mode based on `config_file` presence
  - Validates required parameters for CLI mode (`--symbol`, `--start`, `--end`)
  - Raises clear error messages guiding users to correct syntax
- Updated `validate_strategy()` callback to allow `None` for config mode (strategy comes from YAML)
- Added `--data-source` option supporting "catalog" and "mock"
- Added `--starting-balance` option for overriding config values
- Created 9 new component tests in `TestRunBacktestConfigMode` class
- All 31 component tests pass

**New CLI Signature (implemented):**
```python
@backtest.command("run")
@click.argument("config_file", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option("--symbol", "-sym", help="Trading symbol (required in CLI mode, override in config mode)")
@click.option("--strategy", "-s", default=None, callback=validate_strategy, help="Strategy (CLI mode only)")
@click.option("--start", "-st", type=click.DateTime(), help="Start date (override)")
@click.option("--end", "-e", type=click.DateTime(), help="End date (override)")
@click.option("--data-source", "-ds", type=click.Choice(["catalog", "mock"]))
@click.option("--starting-balance", "-sb", type=float, help="Initial capital (override)")
@click.option("--persist/--no-persist", default=True)
def run_backtest(config_file, symbol, strategy, start, end, ...):
```

### Phase 3: Implement CLI Overrides ✅ COMPLETE
**Goal:** Allow CLI flags to override YAML config values

**Tasks:**
- [x] Create `apply_cli_overrides(yaml_config, cli_args)` function
- [x] Support overriding: start, end, data_source, starting_balance, symbol
- [x] Add clear precedence rules (CLI > YAML > defaults)
- [x] Add tests for override combinations

**Implementation Details (2025-01-24):**
- Created `apply_cli_overrides()` function in `_backtest_helpers.py`:
  - Accepts a `BacktestRequest` and optional override values
  - Returns the same request if no overrides provided
  - Creates a new request copy with overrides applied using Pydantic's `model_copy()`
  - Handles symbol override by rebuilding `instrument_id` (e.g., "AAPL" → "AAPL.NASDAQ")
  - Ensures timezone-aware dates (converts naive dates to UTC)
- Added 6 unit tests in `TestApplyCliOverrides` class covering:
  - No overrides returns same request
  - Start/end date overrides
  - Symbol override rebuilds instrument_id
  - Starting balance override
  - Multiple overrides applied together
  - Timezone-naive dates converted to UTC
- All 37 unit tests pass

**Override Behavior (implemented):**
```python
def apply_cli_overrides(
    request: BacktestRequest,
    *,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    starting_balance: float | None,
) -> BacktestRequest:
    """Apply CLI argument overrides to a config-based request."""
    updates: dict = {}
    if symbol is not None:
        symbol_upper = symbol.upper()
        instrument_id = f"{symbol_upper}.NASDAQ" if "." not in symbol_upper else symbol_upper
        updates["symbol"] = symbol_upper.split(".")[0]
        updates["instrument_id"] = instrument_id
    if start is not None:
        updates["start_date"] = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start
    if end is not None:
        updates["end_date"] = end.replace(tzinfo=timezone.utc) if end.tzinfo is None else end
    if starting_balance is not None:
        updates["starting_balance"] = Decimal(str(starting_balance))
    return request if not updates else request.model_copy(update=updates)
```

### Phase 4: Unify Data Loading Pipeline
**Goal:** Consistent behavior for catalog + IBKR fallback

**Tasks:**
- [ ] Add IBKR auto-fetch capability to config mode (currently only in CLI mode)
- [ ] Standardize date range handling (clamp vs warn vs fetch)
- [ ] Add clear messaging about data source used
- [ ] Test edge cases: partial catalog, no IBKR connection, mock mode

### Phase 5: Deprecate `run-config`
**Goal:** Transition users to unified command

**Tasks:**
- [ ] Add deprecation warning to `run-config` command
- [ ] Update documentation to recommend `backtest run config.yaml`
- [ ] Update README and help text
- [ ] Keep `run-config` functional for 1-2 releases

**Deprecation Message:**
```
⚠️  DEPRECATED: 'backtest run-config' is deprecated.
    Use 'backtest run configs/your_config.yaml' instead.
    This command will be removed in a future release.
```

### Phase 6: Remove `run-config`
**Goal:** Clean up codebase

**Tasks:**
- [ ] Remove `run_config_backtest()` function
- [ ] Remove associated tests (after migrating useful ones)
- [ ] Update any documentation references
- [ ] Final cleanup pass

## 6. Testing Strategy

### Test Migration Plan

| Current Test File | Action |
|-------------------|--------|
| `test_backtest_run.py` | Keep, expand for config mode |
| `test_backtest_run_config.py` | Migrate unique tests, then remove |

### New Test Cases

```python
# Config file mode ✅ IMPLEMENTED
def test_run_with_yaml_config():
    """Test running backtest with YAML config file."""

def test_run_with_yaml_and_date_override():
    """Test CLI date override takes precedence over YAML."""

def test_run_with_yaml_and_starting_balance_override():
    """Test CLI starting balance override."""

# Input validation ✅ IMPLEMENTED
def test_run_cli_mode_requires_symbol():
    """Test symbol is required in CLI mode."""

def test_run_cli_mode_requires_start():
    """Test start date is required in CLI mode."""

def test_run_cli_mode_requires_end():
    """Test end date is required in CLI mode."""

def test_run_mock_source_requires_config_file():
    """Test mock data source requires config file."""

# Backward compatibility ✅ IMPLEMENTED
def test_run_backward_compatibility_cli_mode():
    """Test existing CLI mode behavior is preserved."""

# Data loading (existing tests cover this)
def test_run_catalog_with_ibkr_fallback():
    """Test IBKR auto-fetch when catalog is incomplete."""

def test_run_mock_data_source():
    """Test mock data generation via --data-source mock."""
```

**Unit Tests Added (Phase 2/3):**
- `TestApplyCliOverrides` - 6 tests for CLI override function
- `TestResolveBacktestRequest` - 11 tests for request resolution logic

### Coverage Requirements

- Maintain existing 80%+ coverage threshold
- All new helper functions must have unit tests
- Integration tests for both input modes

## 7. Rollback Plan

Each phase is independently deployable and reversible:

| Phase | Rollback Action |
|-------|-----------------|
| Phase 1 | Revert helper extraction, inline code back |
| Phase 2 | Remove config_file argument, keep helpers |
| Phase 3 | Remove override logic |
| Phase 4 | Revert to separate data loading paths |
| Phase 5 | Remove deprecation warning |
| Phase 6 | N/A (previous phases must succeed first) |

## 8. Success Criteria

### Functional Requirements
- [x] `backtest run config.yaml` works identically to current `run-config`
- [x] `backtest run --symbol X --strategy Y` works identically to current `run`
- [x] CLI overrides correctly modify YAML config values
- [ ] IBKR auto-fetch works in config mode (Phase 4)
- [ ] Results persist and display in Web UI (needs verification)

### Non-Functional Requirements
- [ ] Code reduction: 733 → ~400 lines (-45%) (in progress, will complete with Phase 5/6)
- [x] All existing tests pass (68 tests: 37 unit + 31 component)
- [x] No breaking changes to CLI interface during transition
- [ ] Clear deprecation path for `run-config` (Phase 5)

## 9. Timeline

| Phase | Description | Dependencies | Status |
|-------|-------------|--------------|--------|
| 1 | Extract helpers | None | ✅ Complete |
| 2 | Add config support | Phase 1 | ✅ Complete |
| 3 | CLI overrides | Phase 2 | ✅ Complete |
| 4 | Unify data pipeline | Phase 3 | 🔜 Next |
| 5 | Deprecate run-config | Phase 4 | ⏳ Pending |
| 6 | Remove run-config | Phase 5 + user migration | ⏳ Pending |

## 10. Open Questions

1. **Mock data behavior:** Should `--data-source mock` use YAML dates or generate based on current date?
   - **Proposed:** Use YAML dates if specified, otherwise generate 30 days of recent data

2. **IBKR auto-fetch prompt:** Should we prompt user before fetching from IBKR, or auto-fetch silently?
   - **Proposed:** Auto-fetch with clear console message about what's happening

3. **Starting balance default:** What should the default be?
   - **Proposed:** 100,000 (matching current YAML configs)

4. **Backward compatibility:** How long to keep `run-config` deprecated before removal?
   - **Proposed:** 2 releases or 1 month, whichever is longer

---

## Appendix A: Current Command Signatures

### `backtest run` (current)
```
ntrader backtest run [OPTIONS]

Options:
  -s, --symbol TEXT          Trading symbol (required)
  --strategy TEXT            Strategy name [default: sma_crossover]
  --start DATETIME           Backtest start date (required)
  --end DATETIME             Backtest end date (required)
  --timeframe TEXT           Bar timeframe (auto-detected if not specified)
  --trade-size INTEGER       Number of shares [default: 1000000]
  --fast-period INTEGER      SMA fast period [default: 10]
  --slow-period INTEGER      SMA slow period [default: 20]
  --persist / --no-persist   Persist results [default: persist]
```

### `backtest run-config` (current)
```
ntrader backtest run-config [OPTIONS] CONFIG_FILE

Arguments:
  CONFIG_FILE                Path to YAML config file (required)

Options:
  --data-source [catalog|mock|database]  Data source [default: catalog]
  --start DATETIME           Override start date
  --end DATETIME             Override end date
  --symbol TEXT              Override symbol
  --persist / --no-persist   Persist results [default: persist]
```

### `backtest run` (proposed unified)
```
ntrader backtest run [OPTIONS] [CONFIG_FILE]

Arguments:
  CONFIG_FILE                Path to YAML config file (optional)

Options:
  -s, --symbol TEXT          Trading symbol (required if no config)
  --strategy TEXT            Strategy name (required if no config)
  --start DATETIME           Start date (required if no config, override if config)
  --end DATETIME             End date (required if no config, override if config)
  --timeframe TEXT           Bar timeframe (auto-detected if not specified)
  --starting-balance FLOAT   Initial capital [default: 100000]
  --data-source [catalog|mock]  Data source [default: catalog]
  --persist / --no-persist   Persist results [default: persist]
```

## Appendix B: YAML Config Structure

```yaml
# Strategy definition
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"

# Strategy parameters
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
  trade_size: 100
  rsi_period: 2
  buy_threshold: 0.10
  sell_threshold: 0.50

# Backtest parameters
backtest:
  start_date: "2023-12-29"
  end_date: "2024-12-31"
  initial_capital: 100000
```
