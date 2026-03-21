# NTrader — Nautilus Trader Backtesting System

Production-grade algorithmic trading backtester using Nautilus Trader + IBKR data.
Follows Python Backend Development principles in `.specify/memory/constitution.md`.

## Mental Model
Data flows: IBKR → Parquet catalog → BacktestEngine → Results DB → Web UI/Reports

## Stack
Python 3.11+ · Nautilus Trader · FastAPI · SQLAlchemy · PostgreSQL/TimescaleDB · HTMX/Tailwind

## Foundational Rules

- **TDD is non-negotiable** — every feature starts with a failing test (Red-Green-Refactor)
- **UV only** for dependencies — `uv add`, `uv remove`, `uv sync`
- **IBKR env vars** — all connection settings via environment variables through `IBKRSettings`. Never hardcode
- **Size limits** — files <500 lines, functions <50 lines, classes <100 lines, line length 100 chars
- **context7 MCP** — always use for library documentation lookups
- **Keep README.md in sync** — validate before modifying, update if instructions change

## Commands

```bash
make test-unit          # Unit tests (parallel, no Nautilus)
make test-component     # Component tests (test doubles)
make test-integration   # Integration tests (--forked for C extensions)
make test-e2e           # End-to-end (sequential)
make test-all           # All tests
make test-coverage      # Coverage report (src/core + src/strategies)

make format             # ruff format .
make lint               # ruff check .
make typecheck          # mypy src/core src/strategies

uv run python -m src.cli.main          # CLI entry point
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000  # Web UI
./scripts/build-css.sh                  # Build Tailwind CSS (required first time)
```

## Critical Gotchas

1. **IMPORTANT: Nautilus LogGuard** — C logging panics if initialized twice. Store guard via `set_nautilus_log_guard()` in `src/utils/logging.py`. Never let it go out of scope
2. **`--forked` tests** — integration tests need `--forked` because Nautilus C/Rust extensions corrupt state across `fork()`. Already configured in `make test-integration`
3. **Strategies submodule** — `src/core/strategies/custom/` is a git submodule. Update: `git submodule update --remote`
4. **BacktestEngine is single-use** — cannot be reused after a run; create a new instance each time
5. **Alembic migrations** — run `alembic upgrade head` before first use. 4 migrations in `alembic/versions/`

## Anti-Patterns (things that break)

- Never instantiate `LiveLogger` or `Logger` directly — use LogGuard via `set_nautilus_log_guard()`
- Never reuse a `BacktestEngine` instance across runs — create fresh each time
- Never import from `src.core.strategies.custom.*` in core code — custom/ is a git submodule
- Never run integration tests without `--forked` — C extensions corrupt shared state
- Never hardcode IBKR connection details — use `IBKRSettings` env vars

## Decision Heuristics

- **Test tier**: Unit for pure logic · Component for Nautilus with test doubles · Integration for real engine runs (`--forked`) · E2E for full workflows
- **New file vs edit**: Prefer editing existing files. Only create new for genuinely new concepts (new strategy, new API route)
- **Stuck on Nautilus error?**: Read `agent_docs/nautilus.md` before trying workarounds

## Hooks

Hooks in `.claude/hooks/` auto-enforce formatting, file protection, and pre-commit checks.

## Project Layout

```
src/
├── config.py          # Settings + IBKRSettings (Pydantic, env vars)
├── api/               # FastAPI REST + Jinja2/HTMX UI routes
├── cli/               # Click CLI commands
├── core/              # Strategy registry, backtest runner, analytics
│   └── strategies/    # Built-in + custom/ (git submodule)
├── db/                # SQLAlchemy models + async repositories
├── models/            # Domain Pydantic models
├── services/          # IBKR client, data catalog, persistence, reports
└── utils/             # Logging (LogGuard), config loader, helpers
tests/                 # unit/ component/ integration/ e2e/ api/ ui/
specs/                 # Feature specs 001-010
```

## Example: Adding a Strategy

```
Good — single file with register_strategy, test first:
  tests/unit/strategies/test_my_strat.py   # Write failing test (TDD)
  src/core/strategies/my_strat.py          # Strategy class + config + register_strategy()

Bad — config in a separate file, no test, missing register_strategy call
```

## Commit Format

```
<type>(<scope>): <subject>
```
Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
Never include AI/claude references in commit messages.

## Progressive Disclosure

Detailed docs for specific areas — read on demand:

- `agent_docs/architecture.md` — source tree, data flow, DB schema, strategy registry, Docker, web stack
- `agent_docs/nautilus.md` — LogGuard, C extension isolation, engine lifecycle, IBKR client, Parquet catalog
- `agent_docs/testing.md` — test pyramid, markers, fixtures, TDD workflow, coverage
- `agent_docs/conventions.md` — git workflow, UV commands, pre-commit checks, error handling, style

See README.md for full setup and usage instructions.

## Active Technologies
- Python 3.11+ + python-kraken-sdk, nautilus-trader, FastAPI, Pydantic (012-kraken-crypto-support)
- Parquet (via Nautilus ParquetDataCatalog), PostgreSQL/TimescaleDB (existing backtest results) (012-kraken-crypto-support)

## Recent Changes
- 012-kraken-crypto-support: Added Python 3.11+ + python-kraken-sdk, nautilus-trader, FastAPI, Pydantic
