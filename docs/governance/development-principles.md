# Development Principles (Non-Negotiable)

These rules govern all development on NTrader. They are not guidelines — they are
requirements that must be followed by all contributors and AI agents.

Migrated from the Python Backend Development Constitution (speckit v1.0.1).

---

## Test-Driven Development

TDD is mandatory for all development. Code written without tests first is technical debt
requiring immediate remediation.

**The cycle:**
1. Write a failing test that defines expected behavior
2. Run test — confirm it fails (Red)
3. Write minimal code to make the test pass (Green)
4. Refactor while keeping tests green
5. Repeat — one test at a time

**Enforcement:**
- No implementation code without a corresponding test file
- Features are considered incomplete without tests
- Code reviews must verify test-first approach
- PRs must reference the tests that were written first

## Coverage Requirements

- Minimum **80% coverage** on critical paths (business logic, strategies, services)
- Focus on behavior coverage, not line coverage
- Coverage check runs via `make test-coverage` (covers `src/core` + `src/strategies`)

## Performance Targets

- API response time: **<200ms** for simple queries, **<1s** for complex
- Database queries: **<100ms** for single entity, **<500ms** for lists
- Memory usage: **<500MB** for typical workload
- Startup time: **<5 seconds** for API services
- Profile before optimizing — use cProfile or py-spy, not guesswork

## Security Requirements

### API Security
- Rate limiting on all public endpoints
- Input validation at all boundaries (Pydantic models)
- SQL injection prevention via parameterized queries (SQLAlchemy ORM)
- CORS configuration for web frontends

### Secret Management
- `.env` files never committed to git
- Production: environment variables or secret management service
- Never log sensitive data (API keys, passwords, tokens)
- IBKR and Kraken credentials exclusively via environment variables

## Code Review Process

1. Self-review before pushing
2. Verify tests were written before implementation
3. Run all tests locally before push (`make test-all`)
4. PR description must reference tests written first
5. Ensure CI/CD pipeline passes
6. Document breaking changes
7. Update relevant documentation

## Code Quality Gates

All code must pass before commit:
- `make format` — ruff formatting
- `make lint` — ruff linting
- `make typecheck` — mypy type checking
- Test coverage must not drop below threshold

## CLI Standards

- All CLI commands via Click framework
- `--help` on all commands
- Progress bars for long operations (rich)
- Structured output options: JSON, CSV, human-readable
- Exit codes: 0 (success), 1 (general error), 2+ (specific errors)

## Monitoring & Observability

- Health check endpoint at `/health`
- Structured logging with structlog (console=colored, file=JSON)
- Correlation IDs for request tracing
- Log levels: DEBUG (dev), INFO (key events), WARNING (recoverable), ERROR (failures)

## Governance

- These principles supersede ad-hoc decisions unless explicitly overridden in project-specific config
- Quarterly review of principles for relevance
- Track significant decisions in architecture docs (`agent_docs/`)

---

*Migrated to BMAD: 2026-04-03 | Original: speckit constitution v1.0.1 (2025-01-13)*
