# Conventions

## Git Workflow

**Branch naming**: `feature/*`, `fix/*`, `docs/*`, `refactor/*`, `test/*`
**Base branch**: `main`

**Commit format**:
```
<type>(<scope>): <subject>

<body>

<footer>
```
Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
Never include "claude code" or AI references in commit messages.

## Dependency Management

UV is the **only** package manager. Never edit `pyproject.toml` dependencies directly.

```bash
uv add <package>           # Production dependency
uv add --dev <package>     # Dev dependency
uv remove <package>        # Remove
uv sync                    # Sync lockfile to environment
```

## Quality Checks

Run before every commit (enforced by `.claude/hooks/`, no `.pre-commit-config.yaml`):
```bash
make format      # ruff format .
make lint        # ruff check . (rules: E, F, I)
make typecheck   # mypy src/core src/strategies
```

Ruff excludes: `tests_archive`, `_bmad`, `.claude`, `.cursor`, `alembic`, `.git`, `.venv`.

## Code Size Limits

Measured on executable statements, not raw lines (Epic 2 retro D4). Enforced by
`tests/unit/governance/test_size_caps.py` (Epic 4 pre-work): new code must be under the caps; an
overage in existing code is a baseline entry that may only shrink, disclosed with a one-line reason
in the same commit that adds it.
- **Files**: <500 lines (hard cap, 3-entry allowlist for pre-existing overages)
- **Functions**: <50 lines (ratchet baseline)
- **Classes**: <100 lines (ratchet baseline)
- **Line length**: 100 chars (enforced by ruff)

## Error Handling

- Domain exceptions: see `src/db/exceptions.py` and `src/services/exceptions.py`
- Use `structlog` for all logging — `structlog.get_logger(__name__)`, key-value pairs only
- Logging config in `src/utils/logging.py`: console (colored) + file (JSON, 10MB rotation)
- Decimal arithmetic for financial calculations (never float)

## Performance Standards

- API response: <200ms for simple queries
- Database queries: <100ms for single entity
- Memory: <500MB typical workload

## Security

- **Never commit secrets** — use `.env` files (`.env`, `.env.dev`, `.env.qa`)
- All IBKR credentials via environment variables (see `src/config.py:IBKRSettings`)
- Validate all input with Pydantic models
- Parameterized queries via SQLAlchemy ORM

