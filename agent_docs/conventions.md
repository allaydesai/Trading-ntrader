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

## Pre-Commit Checklist

Run before every commit:
```bash
make format      # ruff format .
make lint        # ruff check .
make typecheck   # mypy src/core src/strategies
```

## Code Size Limits

- **Files**: <500 lines
- **Functions**: <50 lines
- **Classes**: <100 lines
- **Line length**: 100 chars (enforced by ruff)

## Error Handling

- Domain exceptions inherit from a base `TradingError` (see `src/db/exceptions.py`)
- Use `structlog` for all logging — structured key-value pairs, not format strings
- Logging config in `src/utils/logging.py`: console (colored) + file (JSON, 10MB rotation)

## Performance Standards

- API response: <200ms for simple queries
- Database queries: <100ms for single entity
- Memory: <500MB typical workload

## Security

- **Never commit secrets** — use `.env` files (`.env`, `.env.dev`, `.env.qa`)
- All IBKR credentials via environment variables (see `src/config.py:IBKRSettings`)
- Validate all input with Pydantic models
- Parameterized queries via SQLAlchemy ORM

