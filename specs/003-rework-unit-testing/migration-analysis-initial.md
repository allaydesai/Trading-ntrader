# Initial Migration Analysis

**Date**: 2025-01-23
**Phase**: Phase 6 - File Analysis
**Purpose**: Document initial analysis of test files for migration decisions

---

## Analysis Summary

Analyzed sample of test files to establish migration patterns and identify obvious categorizations.

---

## Key Findings

### ✅ Clear Unit Test Candidates (MIGRATE to tests/unit/)

#### test_config.py (14 tests)
- **Analysis**: Pure Pydantic Settings validation, no external dependencies
- **Disposition**: **MIGRATE** to tests/unit/
- **Reasoning**: Tests configuration model validation with no Nautilus or database deps
- **Effort**: Low - straightforward move with import updates
- **Value**: High - critical configuration testing

#### test_export_validation.py (27 tests)
- **Analysis**: Pure validation logic for data/file/trade validators
- **Disposition**: **MIGRATE** to tests/unit/
- **Reasoning**: Validator classes are pure Python with no framework dependencies
- **Effort**: Low - clean validation logic
- **Value**: High - data integrity validation

#### test_ibkr_config.py (8 tests)
- **Analysis**: IBKR configuration settings validation
- **Disposition**: **MIGRATE** to tests/unit/
- **Reasoning**: Pydantic settings validation, no IBKR client dependencies
- **Effort**: Low
- **Value**: Medium

---

### ⚙️ Component Test Candidates (MIGRATE to tests/component/)

#### test_metrics.py (37 tests)
- **Analysis**: Nautilus statistics calculations (Sharpe, Sortino, etc.)
- **Disposition**: **MIGRATE** to tests/component/
- **Reasoning**: Tests Nautilus framework analytics with test data
- **Effort**: Medium - may need test data fixtures
- **Value**: High - performance metrics are critical
- **Note**: Could extract pure calculation logic to src/core/ for unit tests

#### test_portfolio_analytics.py (24 tests)
- **Analysis**: Portfolio performance calculations
- **Disposition**: **MIGRATE** to tests/component/ (or extract logic to unit)
- **Reasoning**: Analytics calculations, may have extractable logic
- **Effort**: Medium - assess extraction potential
- **Value**: High

#### test_csv_export.py (15 tests)
- **Analysis**: CSV export functionality
- **Disposition**: **MIGRATE** to tests/component/
- **Reasoning**: File I/O operations with pandas, component-level
- **Effort**: Low-Medium
- **Value**: Medium

#### test_json_export.py (12 tests)
- **Analysis**: JSON export and serialization
- **Disposition**: **MIGRATE** to tests/component/
- **Reasoning**: JSON serialization with Decimal/datetime handling
- **Effort**: Low
- **Value**: Medium

---

### 🔗 Integration Test Candidates (KEEP in tests/integration/)

#### test_sma_strategy.py (10 tests)
- **Analysis**: Tests full Nautilus SMACrossover strategy class
- **Disposition**: **MIGRATE** to tests/integration/
- **Reasoning**: Tests complete Nautilus strategy, NOT a duplicate of component test
- **Note**: Component test covers pure logic, this covers Nautilus integration
- **Effort**: Low - already in integration mindset
- **Value**: High

#### test_data_service.py (26 tests)
- **Analysis**: DataService with database integration
- **Disposition**: **KEEP** in tests/integration/
- **Reasoning**: Tests database queries, caching, Nautilus conversion
- **Effort**: Low - already categorized correctly
- **Value**: High

#### test_backtest_runner.py (14 tests)
- **Analysis**: BacktestRunner with database and mock data
- **Disposition**: **KEEP/REFACTOR** in tests/integration/
- **Reasoning**: Full backtest execution flow
- **Effort**: Medium - may need TestStubs update
- **Value**: High

---

### ❌ Deletion Candidates

#### test_milestone_*.py files (4 files)
- **Analysis**: Milestone validation tests (M2, M4, M5)
- **Disposition**: **DELETE** (after verifying coverage)
- **Reasoning**: Milestone tests are temporary validation, not ongoing tests
- **Note**: Ensure underlying functionality is covered by other tests
- **Effort**: Low - just delete after validation
- **Value**: Low - milestones are complete

#### Potential duplicates to investigate:
- test_simple_backtest.py vs test_backtest_runner.py
- test_data_fetcher.py vs test_historical_data_fetcher.py

---

## Extraction Opportunities

### Logic to Extract to src/core/

1. **test_portfolio_analytics.py**
   - Extract portfolio calculation logic to src/core/portfolio_analytics.py
   - Create unit tests for pure calculations
   - Keep component tests for service integration

2. **test_metrics.py**
   - Extract custom metric calculations (if any) to src/core/metrics.py
   - Use Nautilus built-in metrics where possible
   - Unit test custom logic

3. **test_csv_loader.py / test_csv_export.py**
   - Potentially extract validation/parsing logic
   - Keep I/O operations in component tests

---

## Migration Patterns Identified

### Pattern 1: Pure Validation/Config (→ Unit)
- Pydantic model validation
- Configuration settings
- Data validators
- **Examples**: test_config.py, test_export_validation.py, test_ibkr_config.py

### Pattern 2: Framework Analytics (→ Component)
- Nautilus statistics calculations
- Portfolio analytics
- Export/import operations
- **Examples**: test_metrics.py, test_portfolio_analytics.py, test_csv_export.py

### Pattern 3: Full Stack (→ Integration)
- Database operations
- Backtest execution
- Strategy lifecycle
- Data service integration
- **Examples**: test_data_service.py, test_backtest_runner.py, test_sma_strategy.py

### Pattern 4: CLI Commands (→ Component)
- Click CLI command tests
- Mock external dependencies
- Test command logic, not I/O
- **Examples**: test_cli.py, test_backtest_commands.py, test_data_commands.py

---

## Next Steps

1. **Continue file-by-file analysis** for remaining 30+ files
2. **Mark dispositions** in migration-plan.md
3. **Identify extraction candidates** for src/core/
4. **Create extraction tasks** for tightly coupled logic
5. **Begin migration** with low-effort, high-value files first

---

## Priority Order for Migration

### Quick Wins (Low Effort, High Value)
1. test_config.py → tests/unit/
2. test_ibkr_config.py → tests/unit/
3. test_export_validation.py → tests/unit/
4. test_json_export.py → tests/component/

### Medium Effort, High Value
1. test_metrics.py → tests/component/ (assess extraction)
2. test_portfolio_analytics.py → tests/component/ (assess extraction)
3. test_csv_export.py → tests/component/
4. test_sma_strategy.py → tests/integration/

### Requires Careful Analysis
1. test_milestone_*.py → DELETE (verify coverage first)
2. test_backtest_runner.py → tests/integration/ (may need refactoring)
3. test_data_service.py → tests/integration/ (already correct)

---

## Migration Metrics (Initial)

**Files Analyzed**: 12 / 42 (29%)
**Clear Decisions**: 8 files
- Unit: 3 files
- Component: 4 files
- Integration: 3 files (keep/migrate)
- Delete: 4 files (milestones, pending verification)

**Remaining**: 30 files
**Extraction Opportunities**: 2-3 files

---

**Last Updated**: 2025-01-23
**Next**: Complete analysis of remaining files and update migration-plan.md
