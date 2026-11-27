# NTrader — Nautilus Trader Backtesting System

Algorithmic trading backtester and IBKR paper-trading host built on Nautilus Trader.
Data flows: IBKR/Kraken/CSV → Parquet catalog → BacktestEngine → Results DB → Web UI/Reports.
The live/paper path (`src/core/live_*`) is real-time control code: correctness beats speed.

## How we work

- **Superpowers is the base layer.** Its test-driven-development, systematic-debugging,
  requesting-code-review and verification-before-completion skills apply to every change.
- **OpenSpec for bigger changes only:** a change that touches **3+ files or crosses an interface**
  (a port/protocol, DB schema or migration, CLI flag or exit code, MCP tool contract, HTTP route,
  or a record name another module pins). Start with `/openspec-propose`, implement from its
  `tasks.md`, and `/openspec-archive-change` once merged so `openspec/specs/` matches the code.
  For these changes the OpenSpec change folder *is* the design and the plan: don't also write
  `docs/superpowers/specs|plans/` files. Smaller changes go straight to TDD.
- **History is reference, not process:** `_bmad-output/` holds the Phase 1–3 PRDs, architecture,
  epics, story records and retros (many tests and docstrings cite those paths, so leave them in
  place). `_bmad-output/project-context.md` is still the coding-rules digest.
- Non-negotiable rules (TDD, coverage, performance targets, security):
  `docs/governance/development-principles.md`.

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
uv run python -m src.mcp_server        # Research MCP server (stdio; Claude Desktop launches it)
make web                                # Web UI (http://127.0.0.1:8000)
./scripts/build-css.sh                  # Build Tailwind CSS (required first time)
```

## Foundational Rules

- **TDD**: every feature starts with a failing test (Red-Green-Refactor)
- **UV only** for dependencies: `uv add`, `uv remove`, `uv sync`
- **IBKR/Kraken settings** via `IBKRSettings`/`KrakenSettings` env vars; never hardcode
- **Size limits**: files <500, functions <50, classes <100 lines, line length 100. Measured on
  executable statements, not raw lines. Ruff enforces line length;
  `tests/unit/governance/test_size_caps.py` enforces the rest. New code must be under the caps;
  an existing overage is a baseline entry that may only shrink. To exceed a cap deliberately, add
  the entry with a one-line reason in the same commit. Budget a split *before* the edit: a split
  of a live module also moves it out of the hand-maintained guard lists (below)
- **context7 MCP** for library documentation lookups
- **Keep README.md in sync** when instructions change

## Critical Gotchas

1. **Nautilus LogGuard**: C logging panics if initialized twice. Store the guard via
   `set_nautilus_log_guard()` in `src/utils/logging.py`; never instantiate `LiveLogger`/`Logger`
   directly or let the guard go out of scope
2. **`--forked` integration tests**: Nautilus C/Rust extensions corrupt state across `fork()`.
   Already configured in `make test-integration`; never run integration tests without it
3. **Strategies submodule**: `src/core/strategies/custom/` is a git submodule
   (`git submodule update --remote`). Never import `src.core.strategies.custom.*` from core code
4. **BacktestEngine is single-use**: create a fresh instance for every run
5. **Alembic**: run `alembic upgrade head` before first use; single head (`c81e5a4d9f27`)

## Guard lists and pinned constants

Before splitting or adding imports to a `src/core/live_*.py` / `src/cli/commands/live*.py`
module, widening a port, or changing any constant that enumerates what code emits or dispatches,
**read `docs/agent/guard-lists.md`**. Several lists are hand-maintained and nothing asserts they
are complete, so an omission is silent. Two rules to keep in mind always:

- Pin an enumerating constant against **the code** (drive the dispatch map / AST-scan the
  emitters), not against a second hand-written list; that only catches one direction.
- **`FROZEN_VIEWS`** (`tests/unit/governance/test_ar44_frozen_views.py`) pins `src/api/**`,
  `templates/**` and the comparison view byte for byte. Never regenerate the manifest to make it
  pass; only a recorded PO decision may.

## Editing with the auto-linter

A ruff formatter runs after each edit. `F401` is `unfixable`, so a half-applied edit (import
added, usage not yet) leaves an unused import that blocks the commit at the pre-commit hook, the
`.claude/hooks/bash-guard.sh` gate and CI. Make dependent changes in one edit; for an intentional
re-export use `# noqa: F401  # <reason>`. The commit gate only inspects staged files and re-stages
fully staged files it reformats; it blocks a *partially* staged file whose indexed content is
unformatted (stage it in full or set the WIP aside).

## Conventions

- Commits: `<type>(<scope>): <subject>`, types `feat|fix|docs|style|refactor|test|chore`.
  No AI/assistant references in commit messages
- Test tier: unit for pure logic · component for Nautilus with test doubles · integration for
  real engine runs (`--forked`) · e2e for full workflows · browser checks via the
  `e2e-ui-testing` skill
- Prefer editing existing files; create new ones only for genuinely new concepts
- UI work: use the `web-ui-development` skill, and read `PRODUCT.md` + `DESIGN.md` first
- Stuck on a Nautilus error: read `docs/agent/nautilus.md` before trying workarounds

## Docs (read on demand)

- `docs/agent/architecture.md`: source tree, data flow, index of the rest
- `docs/agent/guard-lists.md`: hand-maintained guard lists and membership-pinned constants
- `docs/agent/nautilus.md`, `data-pipeline.md`, `web-ui.md`, `persistence.md`, `testing.md`,
  `conventions.md`: per-area detail
