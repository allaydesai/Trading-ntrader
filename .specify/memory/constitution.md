# Python Backend Development Constitution

## Core Principles

### I. Simplicity First (KISS/YAGNI)
Every implementation must start with the simplest working solution. Code must be written in the fewest lines possible without sacrificing readability. Features are built only when needed, never on speculation. Files must never exceed 500 lines - split into modules when approaching this limit. Avoid clever code in favor of obvious code that any developer can understand immediately.

### II. Test-Driven Development (NON-NEGOTIABLE)
Tests are written BEFORE implementation following strict Red-Green-Refactor cycle. Each feature begins with a failing test that defines expected behavior. Implementation contains only minimal code to make tests pass. Use pytest with descriptive test names explaining what is being tested. Maintain minimum 80% coverage on critical paths with focus on behavior, not lines.

### III. FastAPI-First Architecture
All APIs built using FastAPI with Pydantic for validation. Every endpoint must have automatic OpenAPI documentation. Use dependency injection for shared logic (database sessions, authentication). Implement async/await for all I/O operations. Structure apps with routers for logical separation of concerns.

### IV. Type Safety & Documentation
All functions require type hints (PEP 484) with mypy validation passing before commit. Public APIs must have Google-style docstrings with examples. Complex logic requires inline comments with `# Reason:` prefix. README.md mandatory for each module with setup instructions and examples. Architecture Decision Records (ADRs) document significant design choices.

### V. Dependency Discipline
Use UV exclusively for package management - never edit pyproject.toml directly. Commands: `uv add package`, `uv add --dev package`, `uv remove package`, `uv sync`. Select only actively maintained libraries (commits within 6 months, clear documentation). Pin all production dependencies to specific versions. Separate dev, test, and production dependencies clearly.

### VI. Fail Fast & Observable
Check for potential errors early and raise exceptions immediately when issues occur. Validate inputs at system boundaries. Use structured logging with structlog for all operations. Include correlation IDs for request tracing. Log levels: DEBUG (dev only), INFO (key events), WARNING (recoverable), ERROR (failures), CRITICAL (system issues).

### VII. DRY & Modular Design
Extract repeated business logic to private methods or utility functions. Create reusable components for common operations. Use decorators for cross-cutting concerns. Each function, class, and module should have one clear purpose. Functions under 50 lines, classes under 100 lines.

## Development Environment

### Project Structure
```
project/
├── src/
│   ├── api/          # FastAPI routers and endpoints
│   ├── core/         # Core business logic
│   ├── models/       # Pydantic models and schemas
│   ├── services/     # Business services
│   ├── db/           # Database models and migrations
│   └── utils/        # Shared utilities
├── tests/            # Test files mirror src structure
├── scripts/          # CLI tools and automation
├── docs/             # Documentation and ADRs
└── docker/           # Docker configurations
```

### Environment Standards
Python 3.11+ specified in `.python-version` file. Virtual environments mandatory via `uv venv` or `python -m venv`. Single command setup: `make setup` or `uv sync`. Environment variables in `.env` files (never committed). Use python-dotenv for local development. Docker containerization for deployable services.

### Development Workflow
No code can be written without a failing test first. Every coding session begins with writing tests. Features are considered incomplete without tests. The development cycle is: Write Test → Run Test (Red) → Write Code → Run Test (Green) → Refactor → Run Test (Green). This workflow is mandatory for all development.

### Code Quality Gates
Pre-commit hooks required: black (formatting), ruff (linting), mypy (type checking). Test coverage check - blocks commit if coverage drops below 80%. All code must pass: `black .`, `ruff check .`, `mypy .`. No implementation code without corresponding test file. Security scanning with bandit before commit. No commented-out code in commits. Use ripgrep (`rg`) for searching, never grep/find.

## Testing Strategy

### Test Organization
Test file naming: `test_<module>.py`. Test function naming: `test_<function>_<scenario>_<expected_result>`. Tests placed next to code they test or in mirrored test directory. Use descriptive names that explain what is being tested.

### Testing Requirements
Unit tests for all business logic functions. Integration tests for API endpoints using FastAPI TestClient. Edge cases and error conditions must be tested. Use pytest fixtures for shared setup. Mock external dependencies (databases, APIs) in unit tests. Test data generated with Faker for realism. One test per test function - avoid testing multiple behaviors.

### Test-Driven Development Process
1. Write the test first - Define expected behavior before implementation
2. Watch it fail - Ensure the test actually tests something
3. Write minimal code - Just enough to make the test pass
4. Refactor - Improve code while keeping tests green
5. Repeat - One test at a time

### TDD Enforcement
Tests must exist before implementation code. Git hooks prevent commits without tests. Code reviews must verify test-first approach. Features without tests are considered incomplete. Production code without tests is technical debt requiring immediate action.

## API Development Standards

### FastAPI Implementation
```python
from fastapi import APIRouter, Depends, HTTPException
from typing import List

router = APIRouter(prefix="/api/v1/users", tags=["users"])

async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session

@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
) -> UserResponse:
    """Create a new user with validation."""
    pass
```

### Error Handling
Custom exception classes for domain-specific errors. Consistent error response format using Pydantic models. Never use bare `except:` - always catch specific exceptions. Log errors with context: request ID, user ID, operation. Implement global exception handlers in FastAPI. Provide meaningful error messages to clients.

### Data Validation
All input/output through Pydantic models. Use Field validators for complex validation logic. Implement request/response models separately. Version API endpoints when breaking changes required. Support both JSON and form data where appropriate.

## Database Management

### SQLAlchemy Standards
Use SQLAlchemy 2.0+ with async support. Alembic for all schema migrations. Never modify migrations after deployment. Use database transactions for multi-step operations. Connection pooling configured for production loads. Implement soft deletes where appropriate.

### Data Processing
Pandas for data manipulation and analysis. Polars for performance-critical operations. NumPy for numerical computations. Always use vectorized operations over loops. Profile memory usage for large datasets. Use chunking for processing large files.

### Database Best Practices
Indexes on foreign keys and commonly queried fields. Use database-level constraints for data integrity. Implement optimistic locking for concurrent updates. Regular VACUUM and ANALYZE for PostgreSQL. Monitor slow queries and optimize.

## CLI Applications

### Click Framework Standards
```python
import click

@click.command()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose output')
@click.option('--format', type=click.Choice(['json', 'csv', 'text']), default='text')
@click.argument('input_file', type=click.Path(exists=True))
def process(verbose, format, input_file):
    """Process the input file with specified output format."""
    pass
```

### CLI Requirements
Provide `--help` for all commands. Use progress bars for long operations (rich or tqdm). Structured output options: JSON, CSV, human-readable. Exit codes: 0 (success), 1 (general error), 2+ (specific errors). Support for pipe operations and stdin/stdout. Configuration via files and environment variables.

## Documentation Requirements

### Code Documentation
```python
def calculate_metrics(data: pd.DataFrame, window: int = 7) -> Dict[str, float]:
    """Calculate rolling metrics for the dataset.
    
    Args:
        data: DataFrame with 'value' and 'timestamp' columns
        window: Rolling window size in days
        
    Returns:
        Dictionary containing mean, std, min, max metrics
        
    Raises:
        ValueError: If data is empty or window is invalid
        
    Example:
        >>> df = pd.DataFrame({'value': [1, 2, 3], 'timestamp': [...]})
        >>> metrics = calculate_metrics(df, window=7)
        >>> print(metrics['mean'])
        2.0
    """
    pass
```

### Project Documentation
README.md with project overview, setup instructions, usage examples. CHANGELOG.md following Keep a Changelog format. API documentation auto-generated from FastAPI. Architecture diagrams for complex systems (Mermaid or draw.io). Document decisions in `docs/architecture/decisions/`. Maintain technical debt log in `docs/technical-debt.md`.

## Security Requirements

### API Security
Rate limiting on all endpoints using slowapi. Input validation at all boundaries. SQL injection prevention via parameterized queries. JWT tokens for authentication with refresh tokens. API keys for service-to-service communication. CORS configuration for web frontends.

### Secret Management
Development: `.env` files with direnv (never committed). Production: Environment variables or secret management service. Rotate secrets every 90 days. Never log sensitive data. Use bcrypt or argon2 for password hashing. Implement proper session management.

## Performance Standards

### Optimization Rules
Profile before optimizing using cProfile or py-spy. Async/await for all I/O operations in FastAPI. Database query optimization: use select_related/prefetch_related. Cache expensive computations with functools.lru_cache. Batch operations over individual transactions. Use Redis for caching when appropriate.

### Performance Targets
API response time: <200ms for simple queries, <1s for complex. Database queries: <100ms for single entity, <500ms for lists. Memory usage: <500MB for typical workload. CPU usage: <70% under normal load. Startup time: <5 seconds for API services.

## Deployment & Operations

### Containerization
Multi-stage Docker builds for smaller images. Non-root user for security. Health check endpoints required. Graceful shutdown handling. Resource limits defined. Log to stdout/stderr for container orchestration.

### Configuration Management
Environment-specific configs: `.env.local`, `.env.test`, `.env.prod`. Pydantic Settings for configuration validation. Never hardcode credentials or URLs. Feature flags for gradual rollouts. Support for hot-reloading in development.

### Monitoring & Observability
Structured logging with correlation IDs. Health check endpoint at `/health`. Metrics endpoint at `/metrics` (Prometheus format). Distributed tracing with OpenTelemetry when needed. Alert on error rates, latency, and resource usage.

## Git Workflow

### Branch Strategy
`main` - Stable, deployable code. `develop` - Integration branch for features. `feature/<name>` - New features. `fix/<issue>` - Bug fixes. `docs/<topic>` - Documentation updates. `refactor/<area>` - Code refactoring.

### Commit Standards
Use conventional commit format: `<type>(<scope>): <subject>`. Types: feat, fix, docs, style, refactor, test, chore. Keep subject line under 50 characters. Never include "claude code" or AI references in messages. Add detailed description in body if needed.

### Code Review Process
Self-review before pushing. Verify tests were written before implementation. Run all tests locally before push. PR description must reference the tests that were written first. Ensure CI/CD pipeline passes. Document breaking changes. Update relevant documentation. Squash commits for clean history.

## Governance

Constitution supersedes all project practices unless explicitly overridden in project-specific `.agent-os/product/` configurations. Test-Driven Development is mandatory - any code written without tests first is considered technical debt and must be refactored immediately. Violations require written justification in code comments or commit messages. Use `best-practices.md` and `tech-stack.md` for runtime development guidance. Weekly self-review of code quality metrics including TDD compliance. Monthly dependency updates and security scans. Quarterly review of constitution relevance. Track decisions in ADRs. Maintain lessons learned documentation.

**Version**: 1.0.1 | **Ratified**: 2025-01-13 | **Last Amended**: 2025-01-13
**Amendment Notes**: Updated all dependent templates and commands for Python Backend consistency