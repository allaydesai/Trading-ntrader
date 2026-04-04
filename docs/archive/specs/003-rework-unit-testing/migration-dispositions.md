# Migration Dispositions: Complete File Analysis

**Date**: 2025-10-23
**Phase**: Phase 6 - Detailed Analysis Complete
**Status**: Ready for Execution

---

## Executive Summary

**Total Test Files**: 57 files (includes new structure)
**Already Migrated**: 10 files (172 tests in new structure)
**To Process**: 47 files from old structure

### Disposition Breakdown

| Disposition | Files | Percentage | Notes |
|-------------|-------|------------|-------|
| **MIGRATE** | 28    | 60%        | Refactor and move to new structure |
| **REWRITE** | 7     | 15%        | Start fresh with new patterns |
| **DELETE**  | 4     | 8%         | Milestone tests (obsolete) |
| **KEEP**    | 8     | 17%        | Already in integration/, verify only |

---

## Quick Reference: Migration Actions

### Unit Tests (9 files → tests/unit/)
1. ✅ test_config.py - **MIGRATE** - Pure Pydantic validation
2. ✅ test_export_validation.py - **MIGRATE** - Pure validator logic
3. ✅ test_ibkr_config.py - **MIGRATE** - IBKR config validation
4. ⚠️ test_metrics.py - **EXTRACT+MIGRATE** - Extract calculation logic to src/core/metrics.py
5. ⚠️ test_portfolio_analytics.py - **EXTRACT+MIGRATE** - Extract analytics to src/core/analytics.py

### Component Tests (18 files → tests/component/)
6. ✅ test_backtest_commands.py - **MIGRATE** - CLI command logic
7. ✅ test_cli.py - **MIGRATE** - CLI initialization
8. ✅ test_cli_commands.py - **MIGRATE** - run-simple command
9. ✅ test_cli_ibkr_commands.py - **MIGRATE** - IBKR CLI commands
10. ✅ test_config_loader.py - **MIGRATE** - YAML config loading
11. ✅ test_csv_export.py - **MIGRATE** - CSV export logic
12. ✅ test_csv_loader.py - **MIGRATE** - CSV loading/parsing
13. ✅ test_data_commands.py - **MIGRATE** - Data CLI commands
14. ✅ test_data_fetcher.py - **MIGRATE** - IBKR data fetcher
15. ✅ test_data_wrangler.py - **MIGRATE** - Data conversion
16. ✅ test_date_range_adjustment.py - **MIGRATE** - Date logic
17. ✅ test_db_session.py - **MIGRATE** - Session management
18. ✅ test_fee_models.py - **MIGRATE** - Commission models
19. ✅ test_historical_data_fetcher.py - **MIGRATE** - Historical data
20. ✅ test_ibkr_client.py - **MIGRATE** - IBKR client
21. ✅ test_json_export.py - **MIGRATE** - JSON export
22. ✅ test_mock_data.py - **MIGRATE** - Mock data generation
23. ✅ test_report_commands.py - **MIGRATE** - Report CLI
24. ✅ test_rsi_mean_reversion.py - **REWRITE** - RSI strategy
25. ✅ test_sma_momentum.py - **REWRITE** - Momentum strategy
26. ✅ test_strategy_commands.py - **MIGRATE** - Strategy CLI
27. ✅ test_strategy_factory.py - **MIGRATE** - Strategy factory
28. ✅ test_strategy_model.py - **MIGRATE** - Strategy model
29. ✅ test_text_reports.py - **MIGRATE** - Text reports
30. ✅ test_trade_model.py - **MIGRATE** - Trade model

### Integration Tests (12 files → tests/integration/)
31. ✅ test_backtest_runner.py - **REWRITE** - Use TestStubs
32. ✅ test_backtest_runner_yaml.py - **REWRITE** - Use TestStubs
33. ✅ test_csv_import.py - **KEEP** - Already integration
34. ✅ test_data_service.py - **REWRITE** - Simplify with TestStubs
35. ✅ test_database_connection.py - **KEEP** - Already integration
36. ✅ test_ibkr_connection.py - **KEEP** - Already integration
37. ✅ test_ibkr_database_integration.py - **KEEP** - Already integration
38. ✅ test_portfolio_service.py - **REWRITE** - Use TestStubs
39. ✅ test_simple_backtest.py - **REWRITE** - Merge with backtest_runner
40. ✅ test_sma_strategy.py (OLD) - **KEEP+RENAME** - Rename to test_sma_strategy_nautilus.py
41. test_milestone_2.py - **DELETE** - Milestone complete
42. test_milestone_4.py - **DELETE** - Milestone complete
43. test_milestone_4_e2e.py - **DELETE** - Milestone complete
44. test_milestone_5_integration.py - **DELETE** - Milestone complete

---

## Detailed File Analysis

### ✅ Unit Tests (Pure Python, No Dependencies)

#### test_config.py (14 tests)
- **Disposition**: **MIGRATE** to tests/unit/test_config.py
- **Reasoning**: Pure Pydantic Settings validation
- **Effort**: Low (1 hour)
- **Dependencies**: None
- **Action**: Move file, update imports
- **Tests**: Default values, custom values, env loading, validation

#### test_export_validation.py (27 tests)
- **Disposition**: **MIGRATE** to tests/unit/test_export_validation.py
- **Reasoning**: Pure validator classes (DataValidator, FileValidator, TradeValidator)
- **Effort**: Low (1 hour)
- **Dependencies**: None
- **Action**: Move file, update imports
- **Tests**: Data validation, file validation, trade validation, error handling

#### test_ibkr_config.py (8 tests)
- **Disposition**: **MIGRATE** to tests/unit/test_ibkr_config.py
- **Reasoning**: IBKR configuration Pydantic model validation
- **Effort**: Low (30 min)
- **Dependencies**: None
- **Action**: Move file, update imports
- **Tests**: Config defaults, env loading, validation

### ⚠️ Unit Tests (Require Logic Extraction)

#### test_metrics.py (37 tests)
- **Disposition**: **EXTRACT+MIGRATE**
- **Reasoning**: Tests Nautilus statistics, but has extractable custom logic
- **Effort**: High (4 hours)
- **Dependencies**: Nautilus statistics, numpy
- **Action**:
  1. Extract custom metric calculations to src/core/metrics.py
  2. Create tests/unit/test_metrics.py for extracted logic
  3. Keep Nautilus integration tests in tests/component/test_metrics_nautilus.py
- **Tests**: Sharpe ratio, Sortino, max drawdown, win rate, custom calculations

#### test_portfolio_analytics.py (24 tests)
- **Disposition**: **EXTRACT+MIGRATE**
- **Reasoning**: Portfolio calculations can be pure functions
- **Effort**: High (3 hours)
- **Dependencies**: Performance calculations
- **Action**:
  1. Extract portfolio calculation logic to src/core/portfolio_analytics.py
  2. Create tests/unit/test_portfolio_analytics.py
  3. Keep service integration in tests/component/test_portfolio_analytics_service.py
- **Tests**: Returns, volatility, correlations, portfolio metrics

---

### ✅ Component Tests (Test Doubles, No Framework)

#### test_backtest_commands.py (19 tests)
- **Disposition**: **MIGRATE** to tests/component/test_backtest_commands.py
- **Reasoning**: CLI command logic with mocked BacktestRunner
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI
- **Action**: Move file, update to use TestTradingEngine for mocks
- **Tests**: Command options, parameter validation, error handling

#### test_cli.py (9 tests)
- **Disposition**: **MIGRATE** to tests/component/test_cli.py
- **Reasoning**: CLI initialization and help commands
- **Effort**: Low (1 hour)
- **Dependencies**: Click CLI
- **Action**: Move file, minimal updates needed
- **Tests**: CLI setup, help text, command discovery

#### test_cli_commands.py (14 tests)
- **Disposition**: **MIGRATE** to tests/component/test_cli_commands.py
- **Reasoning**: run-simple CLI command with mocked runner
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI, BacktestRunner
- **Action**: Move file, update mocks to use test doubles
- **Tests**: Command parameters, validation, execution

#### test_cli_ibkr_commands.py (14 tests)
- **Disposition**: **MIGRATE** to tests/component/test_cli_ibkr_commands.py
- **Reasoning**: IBKR CLI commands (connect, fetch) with mocked client
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI, IBKR client
- **Action**: Move file, update to mock IBKR client
- **Tests**: Connect command, fetch command, error handling

#### test_config_loader.py (17 tests)
- **Disposition**: **MIGRATE** to tests/component/test_config_loader.py
- **Reasoning**: YAML configuration loading and validation
- **Effort**: Low (1.5 hours)
- **Dependencies**: YAML parser
- **Action**: Move file, update fixture paths
- **Tests**: YAML loading, validation, error handling, schema

#### test_csv_export.py (15 tests)
- **Disposition**: **MIGRATE** to tests/component/test_csv_export.py
- **Reasoning**: CSV export with pandas, file I/O
- **Effort**: Low (1.5 hours)
- **Dependencies**: Pandas, file I/O
- **Action**: Move file, update test data fixtures
- **Tests**: Export formats, headers, data types, error handling

#### test_csv_loader.py (17 tests)
- **Disposition**: **MIGRATE** to tests/component/test_csv_loader.py
- **Reasoning**: CSV loading and bar conversion
- **Effort**: Medium (2 hours)
- **Dependencies**: CSV parser, Nautilus bar conversion
- **Action**: Move file, may extract parsing logic
- **Tests**: CSV parsing, bar conversion, validation, error handling

#### test_data_commands.py (15 tests)
- **Disposition**: **MIGRATE** to tests/component/test_data_commands.py
- **Reasoning**: Data CLI commands with mocked service
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI, DataService
- **Action**: Move file, update service mocks
- **Tests**: Import command, list command, check command, errors

#### test_data_fetcher.py (5 tests)
- **Disposition**: **MIGRATE** to tests/component/test_data_fetcher.py
- **Reasoning**: IBKR data fetcher with mocked client
- **Effort**: Low (1 hour)
- **Dependencies**: IBKR client
- **Action**: Move file, update IBKR client mocks
- **Tests**: Historical data fetching, error handling

#### test_data_wrangler.py (19 tests)
- **Disposition**: **MIGRATE** to tests/component/test_data_wrangler.py
- **Reasoning**: Data conversion to Nautilus format
- **Effort**: Medium (2 hours)
- **Dependencies**: Nautilus bar creation, pandas
- **Action**: Move file, update test data
- **Tests**: Bar conversion, validation, aggregation, error handling

#### test_date_range_adjustment.py (6 tests)
- **Disposition**: **MIGRATE** to tests/component/test_date_range_adjustment.py
- **Reasoning**: Date range calculation logic
- **Effort**: Low (1 hour)
- **Dependencies**: DataService (can be mocked)
- **Action**: Move file, update to test pure date logic
- **Tests**: Date range calculation, boundary conditions

#### test_db_session.py (17 tests)
- **Disposition**: **MIGRATE** to tests/component/test_db_session.py
- **Reasoning**: Database session management (can use SQLite in-memory)
- **Effort**: Medium (2 hours)
- **Dependencies**: SQLAlchemy async
- **Action**: Move file, use in-memory database for tests
- **Tests**: Session lifecycle, connection pooling, error handling

#### test_fee_models.py (15 tests)
- **Disposition**: **MIGRATE** to tests/component/test_fee_models.py
- **Reasoning**: IBKR commission model with Nautilus integration
- **Effort**: Medium (2 hours)
- **Dependencies**: Nautilus FeeModel
- **Action**: Move file, may need test data updates
- **Tests**: Commission calculation, tiered pricing, integration

#### test_historical_data_fetcher.py (20 tests)
- **Disposition**: **MIGRATE** to tests/component/test_historical_data_fetcher.py
- **Reasoning**: Historical data fetching with rate limiting
- **Effort**: Medium (2.5 hours)
- **Dependencies**: IBKR client, rate limiter, catalog
- **Action**: Move file, mock IBKR client and catalog
- **Tests**: Data fetching, rate limiting, retries, catalog integration

#### test_ibkr_client.py (20 tests)
- **Disposition**: **MIGRATE** to tests/component/test_ibkr_client.py
- **Reasoning**: IBKR client connection and rate limiting
- **Effort**: Medium (2.5 hours)
- **Dependencies**: ib_async, rate limiter
- **Action**: Move file, mock ib_async library
- **Tests**: Connection, rate limiting, error handling, retries

#### test_json_export.py (12 tests)
- **Disposition**: **MIGRATE** to tests/component/test_json_export.py
- **Reasoning**: JSON export with custom serialization
- **Effort**: Low (1 hour)
- **Dependencies**: JSON serialization
- **Action**: Move file, update test data
- **Tests**: Export formats, serialization, Decimal/datetime handling

#### test_mock_data.py (6 tests)
- **Disposition**: **MIGRATE** to tests/component/test_mock_data.py
- **Reasoning**: Mock market data generation
- **Effort**: Low (1 hour)
- **Dependencies**: Nautilus bar creation
- **Action**: Move file, update bar creation
- **Tests**: Mock data generation, randomization, validation

#### test_report_commands.py (21 tests)
- **Disposition**: **MIGRATE** to tests/component/test_report_commands.py
- **Reasoning**: Report CLI commands with mocked generators
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI, report generators
- **Action**: Move file, mock report generators
- **Tests**: Generate command, list command, format options, errors

#### test_strategy_commands.py (18 tests)
- **Disposition**: **MIGRATE** to tests/component/test_strategy_commands.py
- **Reasoning**: Strategy CLI commands with mocked factory
- **Effort**: Medium (2 hours)
- **Dependencies**: Click CLI, strategy factory
- **Action**: Move file, mock strategy factory
- **Tests**: List command, create command, validate command, errors

#### test_strategy_factory.py (17 tests)
- **Disposition**: **MIGRATE** to tests/component/test_strategy_factory.py
- **Reasoning**: Strategy factory pattern
- **Effort**: Low (1.5 hours)
- **Dependencies**: Strategy classes
- **Action**: Move file, update strategy references
- **Tests**: Strategy creation, registration, validation, errors

#### test_strategy_model.py (16 tests)
- **Disposition**: **MIGRATE** to tests/component/test_strategy_model.py
- **Reasoning**: Strategy configuration model with in-memory DB
- **Effort**: Medium (2 hours)
- **Dependencies**: Database, Pydantic
- **Action**: Move file, use in-memory database
- **Tests**: Model validation, persistence, queries, errors

#### test_text_reports.py (17 tests)
- **Disposition**: **MIGRATE** to tests/component/test_text_reports.py
- **Reasoning**: Text report generation and formatting
- **Effort**: Low (1.5 hours)
- **Dependencies**: Report formatters
- **Action**: Move file, update test data
- **Tests**: Report formatting, templates, data presentation

#### test_trade_model.py (26 tests)
- **Disposition**: **MIGRATE** to tests/component/test_trade_model.py
- **Reasoning**: Trade model with in-memory database
- **Effort**: Medium (2.5 hours)
- **Dependencies**: Database, Pydantic
- **Action**: Move file, use in-memory database
- **Tests**: Model validation, persistence, queries, relationships

### ⚠️ Component Tests (Require Rewrite)

#### test_rsi_mean_reversion.py (10 tests)
- **Disposition**: **REWRITE** to tests/component/test_rsi_strategy.py
- **Reasoning**: Tightly coupled to old Nautilus patterns
- **Effort**: Medium (3 hours)
- **Dependencies**: Nautilus strategy
- **Action**: Rewrite using new test doubles and patterns
- **Tests**: RSI calculation, entry/exit signals, position sizing

#### test_sma_momentum.py (10 tests)
- **Disposition**: **REWRITE** to tests/component/test_momentum_strategy.py
- **Reasoning**: Tightly coupled to old patterns
- **Effort**: Medium (3 hours)
- **Dependencies**: Nautilus strategy
- **Action**: Rewrite using test doubles
- **Tests**: Momentum signals, trend following, position management

---

### ✅ Integration Tests (Keep or Rewrite with TestStubs)

#### test_backtest_runner.py (14 tests)
- **Disposition**: **REWRITE** in tests/integration/test_backtest_runner.py
- **Reasoning**: Update to use Nautilus TestStubs
- **Effort**: High (4 hours)
- **Dependencies**: BacktestRunner, database, mock data
- **Action**: Rewrite with TestInstrumentProvider, TestDataStubs
- **Tests**: Backtest execution, scenarios, performance validation

#### test_backtest_runner_yaml.py (9 tests)
- **Disposition**: **REWRITE** in tests/integration/test_backtest_runner_yaml.py
- **Reasoning**: Update to use TestStubs and new config pattern
- **Effort**: Medium (3 hours)
- **Dependencies**: ConfigLoader, BacktestRunner
- **Action**: Rewrite with YAML fixtures and TestStubs
- **Tests**: YAML-driven backtests, config validation

#### test_csv_import.py (2 tests)
- **Disposition**: **KEEP** in tests/integration/test_csv_import.py
- **Reasoning**: Already integration test, minimal updates
- **Effort**: Low (1 hour)
- **Dependencies**: Database, CSV loader
- **Action**: Add --forked marker, verify cleanup
- **Tests**: CSV import to database, error handling

#### test_data_service.py (26 tests)
- **Disposition**: **REWRITE** in tests/integration/test_data_service.py
- **Reasoning**: Simplify with TestStubs, reduce complexity
- **Effort**: High (4 hours)
- **Dependencies**: Database, DataService, Nautilus conversion
- **Action**: Rewrite with TestStubs and in-memory database
- **Tests**: Data retrieval, caching, format conversion

#### test_database_connection.py (2 tests)
- **Disposition**: **KEEP** in tests/integration/test_database_connection.py
- **Reasoning**: Already integration test
- **Effort**: Low (30 min)
- **Dependencies**: PostgreSQL database
- **Action**: Add --forked marker, verify cleanup
- **Tests**: Database connection, configuration

#### test_ibkr_connection.py (6 tests)
- **Disposition**: **KEEP** in tests/integration/test_ibkr_connection.py
- **Reasoning**: Already integration test
- **Effort**: Low (1 hour)
- **Dependencies**: IBKR gateway/TWS
- **Action**: Add --forked marker, verify cleanup
- **Tests**: IBKR connection lifecycle, disconnection

#### test_ibkr_database_integration.py (5 tests)
- **Disposition**: **KEEP** in tests/integration/test_ibkr_database_integration.py
- **Reasoning**: Already integration test
- **Effort**: Low (1 hour)
- **Dependencies**: IBKR client, database, DataService
- **Action**: Add --forked marker, verify cleanup
- **Tests**: IBKR data storage, end-to-end flow

#### test_portfolio_service.py (18 tests)
- **Disposition**: **REWRITE** in tests/integration/test_portfolio_service.py
- **Reasoning**: Simplify with TestStubs and extracted analytics
- **Effort**: High (3 hours)
- **Dependencies**: Database, BacktestRunner
- **Action**: Rewrite with TestStubs and new patterns
- **Tests**: Portfolio management, performance tracking

#### test_simple_backtest.py (6 tests)
- **Disposition**: **REWRITE** (merge into test_backtest_runner.py)
- **Reasoning**: Duplicate of backtest_runner tests
- **Effort**: Low (1 hour)
- **Dependencies**: BacktestRunner, mock data
- **Action**: Merge useful tests into test_backtest_runner.py, delete file
- **Tests**: Simple backtest scenarios (merge into main backtest tests)

#### test_sma_strategy.py (OLD) (10 tests)
- **Disposition**: **KEEP+RENAME** to test_sma_strategy_nautilus.py
- **Reasoning**: Tests full Nautilus SMACrossover strategy
- **Effort**: Low (1 hour)
- **Dependencies**: Nautilus strategy
- **Action**: Rename to distinguish from component test, add TestStubs
- **Tests**: Nautilus SMA strategy integration (distinct from component test)

---

### ❌ DELETE (Milestone Tests - Obsolete)

#### test_milestone_2.py (4 tests)
- **Disposition**: **DELETE**
- **Reasoning**: Milestone 2 complete, functionality covered by other tests
- **Effort**: None
- **Action**: Verify coverage, then delete
- **Coverage**: CSV import (test_csv_import.py), backtest (test_backtest_runner.py)

#### test_milestone_4.py (13 tests)
- **Disposition**: **DELETE**
- **Reasoning**: Milestone 4 complete, strategies tested elsewhere
- **Effort**: None
- **Action**: Verify coverage, then delete
- **Coverage**: Strategies (component tests), backtest runner (integration tests)

#### test_milestone_4_e2e.py (4 tests)
- **Disposition**: **DELETE**
- **Reasoning**: Milestone 4 E2E complete, covered by integration tests
- **Effort**: None
- **Action**: Verify coverage, then delete
- **Coverage**: Full stack tests in integration/

#### test_milestone_5_integration.py (9 tests)
- **Disposition**: **DELETE**
- **Reasoning**: Milestone 5 complete, IBKR integration covered
- **Effort**: None
- **Action**: Verify coverage, then delete
- **Coverage**: IBKR tests (test_ibkr_*.py), integration (test_ibkr_database_integration.py)

---

## Migration Execution Plan

### Phase 1: Quick Wins (Low Effort, High Value) - 6 files, ~8 hours
1. test_config.py → tests/unit/ (1h)
2. test_export_validation.py → tests/unit/ (1h)
3. test_ibkr_config.py → tests/unit/ (0.5h)
4. test_json_export.py → tests/component/ (1h)
5. test_cli.py → tests/component/ (1h)
6. test_mock_data.py → tests/component/ (1h)
7. DELETE milestone tests (verify coverage) (2.5h)

### Phase 2: Component Tests (Medium Effort) - 15 files, ~28 hours
8. test_config_loader.py → tests/component/ (1.5h)
9. test_csv_export.py → tests/component/ (1.5h)
10. test_strategy_factory.py → tests/component/ (1.5h)
11. test_text_reports.py → tests/component/ (1.5h)
12. test_backtest_commands.py → tests/component/ (2h)
13. test_cli_commands.py → tests/component/ (2h)
14. test_cli_ibkr_commands.py → tests/component/ (2h)
15. test_data_commands.py → tests/component/ (2h)
16. test_report_commands.py → tests/component/ (2h)
17. test_strategy_commands.py → tests/component/ (2h)
18. test_csv_loader.py → tests/component/ (2h)
19. test_data_wrangler.py → tests/component/ (2h)
20. test_db_session.py → tests/component/ (2h)
21. test_fee_models.py → tests/component/ (2h)
22. test_strategy_model.py → tests/component/ (2h)

### Phase 3: Component Tests (Higher Complexity) - 8 files, ~18 hours
23. test_data_fetcher.py → tests/component/ (1h)
24. test_date_range_adjustment.py → tests/component/ (1h)
25. test_historical_data_fetcher.py → tests/component/ (2.5h)
26. test_ibkr_client.py → tests/component/ (2.5h)
27. test_trade_model.py → tests/component/ (2.5h)
28. test_rsi_mean_reversion.py → REWRITE tests/component/test_rsi_strategy.py (3h)
29. test_sma_momentum.py → REWRITE tests/component/test_momentum_strategy.py (3h)
30. test_simple_backtest.py → Merge into test_backtest_runner.py (1h)

### Phase 4: Extract and Unit Test - 2 files, ~7 hours
31. test_metrics.py → EXTRACT to src/core/metrics.py + unit tests (4h)
32. test_portfolio_analytics.py → EXTRACT to src/core/analytics.py + unit tests (3h)

### Phase 5: Integration Tests - 8 files, ~17 hours
33. test_csv_import.py → KEEP, add markers (1h)
34. test_database_connection.py → KEEP, add markers (0.5h)
35. test_ibkr_connection.py → KEEP, add markers (1h)
36. test_ibkr_database_integration.py → KEEP, add markers (1h)
37. test_sma_strategy.py → RENAME to test_sma_strategy_nautilus.py (1h)
38. test_backtest_runner.py → REWRITE with TestStubs (4h)
39. test_backtest_runner_yaml.py → REWRITE with TestStubs (3h)
40. test_data_service.py → REWRITE simplified (4h)
41. test_portfolio_service.py → REWRITE with TestStubs (3h)

---

## Estimated Total Effort

| Phase | Files | Hours |
|-------|-------|-------|
| Phase 1: Quick Wins | 7 | 8 |
| Phase 2: Component (Easy) | 15 | 28 |
| Phase 3: Component (Hard) | 8 | 18 |
| Phase 4: Extract Logic | 2 | 7 |
| Phase 5: Integration | 9 | 17 |
| **TOTAL** | **41** | **78 hours** |

**Estimated Calendar Time**: 10-15 days (working 5-8 hours/day)

---

## Success Criteria

### Before Migration
- Tests: 763 total
- Files: 52 files
- Structure: Mixed (tests/ root + some tests/integration/)

### After Migration (Target)
- Tests: ≥763 tests (minus ~30 deleted milestone tests = ~733 tests)
- Files: 51 files (reorganized into pyramid)
- Unit Tests: 120+ tests (84 existing + 36 from extraction)
- Component Tests: 300+ tests (61 existing + migrations)
- Integration Tests: 80+ tests (27 existing + rewrites)
- Test Distribution: ~50% unit, ~40% component, ~10% integration
- Execution Time: 50% faster than baseline
- All tests passing with proper markers

---

**Status**: Ready for execution
**Next Step**: Begin Phase 1 (Quick Wins)
**Document Version**: 1.0
**Last Updated**: 2025-10-23
