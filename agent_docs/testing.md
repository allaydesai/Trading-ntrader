# Testing

## Test Pyramid

| Tier | Dir | Runner | What it tests |
|------|-----|--------|---------------|
| **Unit** | `tests/unit/` | `make test-unit` | Pure Python logic, no Nautilus |
| **Component** | `tests/component/` | `make test-component` | Interactions via test doubles |
| **Integration** | `tests/integration/` | `make test-integration` | Real Nautilus engine, DB, IBKR |
| **E2E** | `tests/e2e/` | `make test-e2e` | Full backtest workflow |
| **API** | `tests/api/` | (included in test-all) | FastAPI endpoint tests |
| **UI** | `tests/ui/` | (included in test-all) | Template rendering tests |

## Why `--forked`

Integration tests use `pytest --forked` because Nautilus C/Rust extensions don't survive
`fork()` well. Each test runs in a subprocess to avoid corrupted global state and segfaults.
See `agent_docs/nautilus.md` for details.

## Pytest Markers

Defined in `pytest.ini`:
- `unit` — pure Python, parallelizable (`-n auto`)
- `component` — test doubles, parallelizable
- `integration` — Nautilus + DB, needs `--forked`
- `e2e` — sequential, full workflow
- `slow` — takes >1 second
- `trading` — trading-system specific
- `db` — requires PostgreSQL

## Coverage

Run: `make test-coverage`
Covers `src/core` and `src/strategies`. Report: HTML + terminal.

## TDD Workflow

1. **Red** — write a failing test that describes the expected behavior
2. **Green** — write the minimum code to make it pass
3. **Refactor** — improve code while keeping tests green

Every feature starts with a failing test. Minimum 80% coverage on critical paths.

## Key Fixtures

- `tests/conftest.py` — `project_root`, `test_data_dir`, autouse `cleanup` (gc.collect)
- `tests/component/conftest.py` — `test_engine`, `sma_logic`, `position_sizing_logic`, `risk_manager`
- `tests/integration/conftest.py` — `integration_cleanup` (double gc), `setup_backtest_venue()`
- `tests/fixtures/scenarios.py` — `MarketScenario` dataclass (VOLATILE, TRENDING, RANGING)

## Running Tests

```bash
make test-unit          # Fast, parallel
make test-component     # Test doubles, parallel
make test-integration   # --forked, parallel
make test-e2e           # Sequential
make test-all           # Everything
make test-coverage      # With coverage report
```
