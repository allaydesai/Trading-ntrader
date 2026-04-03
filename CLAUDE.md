# NTrader — Nautilus Trader Backtesting System

Production-grade algorithmic trading backtester using Nautilus Trader + IBKR data.

## BMAD Context

This project uses the BMAD method for agentic development. Read these before implementing:

- **`_bmad-output/project-context.md`** — implementation rules: tech stack, coding patterns, testing, gotchas (read first)
- **`docs/development-principles.md`** — non-negotiable rules: TDD, coverage, performance targets, security
- **`_bmad/`** — BMAD core config, templates, and agent personas

## Mental Model

Data flows: IBKR/Kraken/CSV → Parquet catalog → BacktestEngine → Results DB → Web UI/Reports

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

## Foundational Rules

- **TDD is non-negotiable** — every feature starts with a failing test (Red-Green-Refactor)
- **UV only** for dependencies — `uv add`, `uv remove`, `uv sync`
- **IBKR/Kraken env vars** — all connection settings via `IBKRSettings`/`KrakenSettings`. Never hardcode
- **Size limits** — files <500 lines, functions <50 lines, classes <100 lines, line length 100 chars
- **context7 MCP** — always use for library documentation lookups
- **Keep README.md in sync** — validate before modifying, update if instructions change

## Critical Gotchas

1. **Nautilus LogGuard** — C logging panics if initialized twice. Store guard via `set_nautilus_log_guard()` in `src/utils/logging.py`. Never let it go out of scope
2. **`--forked` tests** — integration tests need `--forked` because Nautilus C/Rust extensions corrupt state across `fork()`. Already configured in `make test-integration`
3. **Strategies submodule** — `src/core/strategies/custom/` is a git submodule. Update: `git submodule update --remote`
4. **BacktestEngine is single-use** — cannot be reused after a run; create a new instance each time
5. **Alembic migrations** — run `alembic upgrade head` before first use. 4 migrations in `alembic/versions/`

## Anti-Patterns (things that break)

- Never instantiate `LiveLogger` or `Logger` directly — use LogGuard via `set_nautilus_log_guard()`
- Never reuse a `BacktestEngine` instance across runs — create fresh each time
- Never import from `src.core.strategies.custom.*` in core code — custom/ is a git submodule
- Never run integration tests without `--forked` — C extensions corrupt shared state
- Never hardcode IBKR/Kraken connection details — use env var settings classes

## Editing with Auto-Linter

A ruff auto-formatter runs after each file edit. This can silently revert changes (e.g., removing "unused" imports) when dependent edits are split across multiple steps.

- **Make dependent changes in a single edit** — e.g., when moving an import from inline to top-level, remove the inline usage in the same edit that adds the top-level import
- **When removing a function parameter**, update call sites first (extra args still work), then remove the parameter
- **Re-read the file after each edit** if you suspect the linter modified it — never assume your edit landed as written

## Commit Format

```
<type>(<scope>): <subject>
```
Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
Never include AI/claude references in commit messages.

## Decision Heuristics

- **Test tier**: Unit for pure logic · Component for Nautilus with test doubles · Integration for real engine runs (`--forked`) · E2E for full workflows · **UI testing** via `agent-browser` skill
- **New file vs edit**: Prefer editing existing files. Only create new for genuinely new concepts (new strategy, new API route)
- **UI changes**: Always invoke the `web-ui-development` skill before editing templates, routes, or HTMX patterns
- **Stuck on Nautilus error?**: Read `agent_docs/nautilus.md` before trying workarounds

## UI Testing (agent-browser)

Use the `agent-browser` skill for all browser-based UI testing and verification.

- **Always snapshot before interacting** — `agent-browser snapshot -i` to get element refs (@e1, @e2, ...). Never guess selectors
- **Re-snapshot after navigation/re-render** — refs become stale after page changes
- **Wait for async content** — use `agent-browser wait --text "Expected"` before snapshotting dynamic pages
- **Capture evidence** — `agent-browser screenshot result.png` for pass/fail proof
- **Date inputs** — `agent-browser fill @ref` silently fails on `<input type="date">` (exposed as 3 spinbuttons). Use `agent-browser eval` with native value setter + event dispatch instead
- **HTMX form submission** — `agent-browser click @ref` on submit buttons may not trigger HTMX's event chain. Use `agent-browser eval "document.querySelector('button[type=\"submit\"]').click()"` to ensure HTMX intercepts the submit
- **Timeouts for long requests** — set `AGENT_BROWSER_DEFAULT_TIMEOUT=120000` when clicking actions that trigger slow server responses (e.g., backtest execution)
- **Playwright MCP timeouts** — configured with `--timeout-action 60000 --timeout-navigation 120000` to handle long-running backtest requests
- Dev server: `http://127.0.0.1:8000` (FastAPI/HTMX)

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
specs/                 # Feature specs 001-013
```

## Example: Adding a Strategy

```
Good — single file with register_strategy, test first:
  tests/unit/strategies/test_my_strat.py   # Write failing test (TDD)
  src/core/strategies/my_strat.py          # Strategy class + config + register_strategy()

Bad — config in a separate file, no test, missing register_strategy call
```

## Progressive Disclosure

Detailed docs for specific areas — read on demand:

- `_bmad-output/project-context.md` — tech stack, coding patterns, testing rules, anti-patterns, data flow
- `docs/development-principles.md` — TDD enforcement, coverage, performance targets, security, code review
- `agent_docs/architecture.md` — source tree, data flow, DB schema, strategy registry, Docker, web stack
- `agent_docs/nautilus.md` — LogGuard, C extension isolation, engine lifecycle, IBKR client, Parquet catalog
- `agent_docs/testing.md` — test pyramid, markers, fixtures, TDD workflow, coverage
- `agent_docs/conventions.md` — git workflow, UV commands, pre-commit checks, error handling, style

See README.md for full setup and usage instructions.
