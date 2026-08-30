# NTrader — Nautilus Trader Backtesting System

Production-grade algorithmic trading backtester using Nautilus Trader + IBKR data.

## BMAD Context

This project uses the BMAD method for agentic development. Read these before implementing:

- **`_bmad-output/project-context.md`** — implementation rules: tech stack, coding patterns, testing, gotchas (read first)
- **`docs/governance/development-principles.md`** — non-negotiable rules: TDD, coverage, performance targets, security
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
make typecheck          # mypy src/core src/services
make install-hooks      # Install git pre-commit hook (run once per clone)

uv run python -m src.cli.main          # CLI entry point
make web                                # Web UI (http://127.0.0.1:8000)
./scripts/build-css.sh                  # Build Tailwind CSS (required first time)
```

## Foundational Rules

- **TDD is non-negotiable** — every feature starts with a failing test (Red-Green-Refactor)
- **UV only** for dependencies — `uv add`, `uv remove`, `uv sync`
- **IBKR/Kraken env vars** — all connection settings via `IBKRSettings`/`KrakenSettings`. Never hardcode
- **Size limits** — files <500 lines, functions <50 lines, classes <100 lines, line length 100 chars.
  **Measured on executable statements, not raw lines** (decided 2026-08-28, Epic 2 retro D4): a module
  whose bulk is a docstring recording measured framework behaviour is not the problem these caps exist
  for. Line length is the only one ruff enforces today; a unit-tier AST guard for the other three is
  pending, with an explicit allowlist for sanctioned exceptions. **Disclose and record an overage
  rather than silently exceeding it** — and budget a split *before* the edit, not during it, because
  a split that moves a module also moves it out of the hand-maintained guard lists (see Anti-Patterns)
- **context7 MCP** — always use for library documentation lookups
- **Keep README.md in sync** — validate before modifying, update if instructions change

## Critical Gotchas

1. **Nautilus LogGuard** — C logging panics if initialized twice. Store guard via `set_nautilus_log_guard()` in `src/utils/logging.py`. Never let it go out of scope
2. **`--forked` tests** — integration tests need `--forked` because Nautilus C/Rust extensions corrupt state across `fork()`. Already configured in `make test-integration`
3. **Strategies submodule** — `src/core/strategies/custom/` is a git submodule. Update: `git submodule update --remote`
4. **BacktestEngine is single-use** — cannot be reused after a run; create a new instance each time
5. **Alembic migrations** — run `alembic upgrade head` before first use. 16 migrations in `alembic/versions/`, single head (`b7c419e2a3d8`)

## Anti-Patterns (things that break)

- Never instantiate `LiveLogger` or `Logger` directly — use LogGuard via `set_nautilus_log_guard()`
- Never reuse a `BacktestEngine` instance across runs — create fresh each time
- Never import from `src.core.strategies.custom.*` in core code — custom/ is a git submodule
- Never run integration tests without `--forked` — C extensions corrupt shared state
- Never hardcode IBKR/Kraken connection details — use env var settings classes
- Never split a `src/core/live_*.py` or `src/cli/commands/live*.py` module without re-checking the
  **hand-maintained guard lists** in the same commit — `NODE_FACING_MODULES` / `EXEMPT_MODULES`
  (`tests/unit/core/test_live_node_never_exits.py`), `STOP_PATH_MODULES`
  (`tests/unit/core/test_live_stop_path_is_inert.py`), `TestImportPurity.MODULES`
  (`tests/component/core/test_session_runner_phases.py`). Story 2.6's file-size split made
  `live_start.py` silently escape two of them, and nothing asserts the lists are complete, so an
  omission is invisible. `LIVE_MODULE_GLOBS` and `STRATEGY_MODULES` are globbed and need no action
- **Membership-pinned lists** (added 2026-08-29): `FORBIDDEN_ORDER_METHODS` and `LIFECYCLE_HOOKS`
  (`tests/unit/core/test_live_stop_path_is_inert.py`) are asserted as exact sets, because every
  other consumer only *intersects* with them — dropping a name weakened a scan or deleted a
  parametrized probe's own case without anything going red. Change either deliberately, in the
  membership test and the constant together. `ORDER_CREATING_METHODS`
  (`src/core/live_order_path.py`, added 2026-08-30) joins this discipline: pinned as an exact set in
  `tests/component/core/test_live_order_path.py`, and separately asserted a *subset* of
  `FORBIDDEN_ORDER_METHODS` against a duplicated literal there — production code cannot import from
  `tests/`, so the relationship is checked against a copy, not a shared reference

## Editing with Auto-Linter

A ruff auto-formatter runs after each file edit. `F401` (unused import) is configured `unfixable`, so the formatter no longer silently strips imports — but a half-applied edit (import added, usage not yet) leaves an unused import that **hard-blocks the commit**.

**The structural import gate** — unused (F401) / undefined (F821) imports are rejected at three points: the `.githooks/pre-commit` hook (universal — terminal, IDE, and Claude commits alike; run `make install-hooks` once per clone), the `.claude/hooks/bash-guard.sh` commit gate (Claude-issued commits), and CI. A commit will not land until the import is fixed.

- **Escape hatch** — for an intentional unused import (e.g. a re-export in `__init__.py`), append `# noqa: F401  # <reason>`. The reason comment is a required convention (not machine-enforced).
- **Make dependent changes in a single edit** — e.g., when moving an import from inline to top-level, remove the inline usage in the same edit that adds the top-level import
- **When removing a function parameter**, update call sites first (extra args still work), then remove the parameter
- **Re-read the file after each edit** if you suspect the linter modified it — never assume your edit landed as written
- **The commit gate only inspects *staged* files** — `.claude/hooks/bash-guard.sh` runs `ruff format`/`check`/`mypy` before a `git commit`, then reconciles only what the formatter changed among files already in the index. Consequences: partial commits work (stage a subset, commit, repeat — unstaged files are left alone), and `git add <files> && git commit` in one call works too. If the formatter rewrites a fully-staged file, the hook **re-stages it for you**. It blocks only when a *partially* staged file's indexed content is itself unformatted, because re-staging would sweep in the hunks you held back — stash the WIP or stage the file in full

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
- **Stuck on Nautilus error?**: Read `docs/agent/nautilus.md` before trying workarounds

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

Hooks in `.claude/hooks/` auto-enforce formatting, file protection, and pre-commit checks (Claude-issued actions only). The tracked `.githooks/pre-commit` hook gates the structural import check (F401/F821) for **all** commits — terminal, IDE, and Claude — and is installed via `make install-hooks` (once per clone; sets `core.hooksPath`).

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
```

## Example: Adding a Strategy

```
Good — single file with register_strategy, test first:
  tests/unit/strategies/test_my_strat.py   # Write failing test (TDD)
  src/core/strategies/my_strat.py          # Strategy class + config + register_strategy()

Bad — config in a separate file, no test, missing register_strategy call
```

## Design Context

Read before any UI/design work (templates, CSS, charts, new pages):

- **`PRODUCT.md`** — register (product), users, brand personality ("modern quant workbench"), anti-references, strategic design principles
- **`DESIGN.md`** — visual system: color tokens, typography, elevation doctrine (flat/tonal), component specs, do's and don'ts

## Progressive Disclosure

Detailed docs for specific areas — read on demand by topic:

- `_bmad-output/project-context.md` — tech stack, coding patterns, testing rules, anti-patterns
- `docs/governance/development-principles.md` — TDD enforcement, coverage, performance targets, security
- `docs/agent/architecture.md` — source tree overview, data flow map, progressive disclosure index
- `docs/agent/nautilus.md` — LogGuard, C extension isolation, engine lifecycle, strategy config
- `docs/agent/data-pipeline.md` — DataCatalogService, IBKR/Kraken clients, Parquet catalog, symbol resolution
- `docs/agent/web-ui.md` — HTMX patterns, templates, DI chain, charts, presentation models
- `docs/agent/persistence.md` — DB models, async/sync repositories, results extraction, exceptions
- `docs/agent/testing.md` — test pyramid, markers, fixtures, TDD workflow, coverage
- `docs/agent/conventions.md` — git workflow, UV commands, quality checks, error handling, style

See README.md for full setup and usage instructions.
