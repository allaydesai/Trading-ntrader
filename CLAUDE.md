# Claude Code Runtime Development Guidelines

## Python Backend Development Context

This project follows strict Python Backend Development principles as defined in `.specify/memory/constitution.md`.

## 🔄 Current Project: Nautilus Trader Backtesting System

### Project Overview
Building a production-grade algorithmic trading backtesting system using Nautilus Trader framework with Interactive Brokers data integration.

### Tech Stack
- **Core Framework**: Nautilus Trader (event-driven backtesting engine)
- **Data Source**: Interactive Brokers TWS/Gateway via nautilus_trader[ib]
- **API**: FastAPI with async/await patterns
- **Database**: PostgreSQL with TimescaleDB for time-series data
- **Cache**: Redis for performance optimization
- **Testing**: pytest with 80% minimum coverage (TDD mandatory)

### Key Components
- IBKR data adapter with rate limiting (50 req/sec)
- Trading strategies: SMA crossover, mean reversion, momentum
- Performance analytics with Sharpe ratio, drawdown, win rate
- Report generation: HTML with charts, CSV, JSON formats
- CSV data import as fallback when IBKR unavailable

### Recent Changes (2025-01-13)
- Created feature specification for backtesting system
- Designed data model with 8 core entities
- Generated OpenAPI contracts for REST API
- Planned TDD approach with failing tests first

### Active Development
- Branch: `001-docs-prd-md`
- Phase: Design & Architecture (Phase 1)
- Next: Generate tasks.md for implementation

## Core Development Philosophy

### KISS (Keep It Simple, Stupid)
Simplicity should be a key goal in design. Choose straightforward solutions over complex ones whenever possible. Simple solutions are easier to understand, maintain, and debug.

### YAGNI (You Aren't Gonna Need It)
Avoid building functionality on speculation. Implement features only when they are needed, not when you anticipate they might be useful in the future.

### Design Principles
- **Dependency Inversion**: High-level modules should not depend on low-level modules. Both should depend on abstractions.
- **Open/Closed Principle**: Software entities should be open for extension but closed for modification.
- **Single Responsibility**: Each function, class, and module should have one clear purpose.
- **Fail Fast**: Check for potential errors early and raise exceptions immediately when issues occur.

### Core Development Principles

1. **Test-Driven Development (NON-NEGOTIABLE)**
   - Write tests BEFORE implementation (Red-Green-Refactor)
   - Each feature starts with a failing test
   - Minimum 80% coverage on critical paths
   - Use pytest with descriptive test names

2. **FastAPI Architecture**
   - All APIs built using FastAPI with Pydantic
   - Automatic OpenAPI documentation
   - Async/await for all I/O operations
   - Dependency injection for shared logic

3. **Type Safety & Documentation**
   - All functions require type hints (PEP 484)
   - Google-style docstrings with examples
   - Mypy validation must pass

4. **Dependency Management**
   - Use UV exclusively (`uv add`, `uv remove`, `uv sync`)
   - Never edit pyproject.toml directly
   - Pin production dependencies to specific versions

## 🧱 Code Structure & Modularity

### File and Function Limits
- **Never create a file longer than 500 lines of code**. If approaching this limit, refactor by splitting into modules.
- **Functions should be under 50 lines** with a single, clear responsibility.
- **Classes should be under 100 lines** and represent a single concept or entity.
- **Organize code into clearly separated modules**, grouped by feature or responsibility.
- **Line length should be max 100 characters** (enforced by ruff rule in pyproject.toml)

### Project Structure
```
src/
├── api/          # FastAPI routers and endpoints
├── core/         # Core business logic
├── models/       # Pydantic models and schemas
├── services/     # Business services
├── db/           # Database models and migrations
└── utils/        # Shared utilities

tests/            # Test files mirror src structure
scripts/          # CLI tools and automation
docs/             # Documentation and ADRs
docker/           # Docker configurations
```

### Vertical Slice Architecture
Tests should live next to the code they test for better organization:
```
src/project/
    __init__.py
    main.py
    tests/
        test_main.py
    conftest.py

    # Core modules
    database/
        __init__.py
        connection.py
        models.py
        tests/
            test_connection.py
            test_models.py

    # Feature slices
    features/
        trading/
            __init__.py
            handlers.py
            validators.py
            tests/
                test_handlers.py
                test_validators.py
```

## 🛠️ Development Environment & Workflow

### UV Package Management
This project uses UV for blazing-fast Python package and environment management:

```bash
# Install UV (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Sync dependencies
uv sync

# Add a package ***NEVER UPDATE A DEPENDENCY DIRECTLY IN PYPROJECT.toml***
# ALWAYS USE UV ADD
uv add requests

# Add development dependency
uv add --dev pytest ruff mypy

# Remove a package
uv remove requests

# Run commands in the environment
uv run python script.py
uv run pytest
uv run ruff check .
```

### Development Workflow

1. **Before Writing Any Code**:
   - Write failing test first
   - Run test to ensure it fails (Red phase)
   - Only then write implementation

2. **Code Quality Gates**:
   - Run `ruff format .` for formatting
   - Run `ruff check .` for linting
   - Run `mypy .` for type checking
   - Run `pytest` with coverage check

3. **Performance Standards**:
   - API response time <200ms for simple queries
   - Database queries <100ms for single entity
   - Memory usage <500MB typical workload

## 📋 Style & Conventions

### Python Style Guide
- **Follow PEP8** with these specific choices:
  - Line length: 100 characters (set by Ruff in pyproject.toml)
  - Use double quotes for strings
  - Use trailing commas in multi-line structures
- **Always use type hints** for function signatures and class attributes
- **Format with `ruff format`**
- **Use `pydantic` v2** for data validation and settings management

### Naming Conventions
- **Variables and functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private attributes/methods**: `_leading_underscore`
- **Type aliases**: `PascalCase`
- **Enum values**: `UPPER_SNAKE_CASE`

### Docstring Standards
Use Google-style docstrings for all public functions, classes, and modules:

```python
def calculate_profit(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: int = 1
) -> Decimal:
    """
    Calculate profit from a trade.

    Args:
        entry_price: Price at which position was opened
        exit_price: Price at which position was closed
        quantity: Number of shares/units traded

    Returns:
        Profit or loss amount

    Raises:
        ValueError: If prices are negative or quantity is zero

    Example:
        >>> calculate_profit(Decimal("100"), Decimal("110"), 10)
        Decimal('100.00')
    """
```

### Key Technologies

- **Language**: Python 3.11+
- **Framework**: FastAPI 0.109+
- **ORM**: SQLAlchemy 2.0+ (async)
- **Validation**: Pydantic 2.5+
- **Testing**: pytest 7.4+ with pytest-asyncio
- **Package Manager**: UV (exclusive)
- **Logging**: structlog with correlation IDs
- **Database**: PostgreSQL 16+ with Alembic migrations

## 🧪 Testing Strategy

### Test-Driven Development (TDD)
1. **Write the test first** - Define expected behavior before implementation
2. **Watch it fail** - Ensure the test actually tests something
3. **Write minimal code** - Just enough to make the test pass
4. **Refactor** - Improve code while keeping tests green
5. **Repeat** - One test at a time

### Testing Best Practices

```python
# Always use pytest fixtures for setup
import pytest
from datetime import datetime
from decimal import Decimal

@pytest.fixture
def sample_trade():
    """Provide a sample trade for testing."""
    return Trade(
        id=123,
        symbol="AAPL",
        entry_price=Decimal("150.00"),
        quantity=100,
        created_at=datetime.now()
    )

# Use descriptive test names
def test_trade_calculates_profit_when_price_increases(sample_trade):
    """Test that trades calculate profit correctly when exit price is higher."""
    exit_price = Decimal("160.00")
    profit = sample_trade.calculate_profit(exit_price)
    assert profit == Decimal("1000.00")

# Test edge cases and error conditions
def test_trade_calculation_fails_with_negative_price(sample_trade):
    """Test that negative exit prices are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        sample_trade.calculate_profit(Decimal("-10.00"))
    assert "Price cannot be negative" in str(exc_info.value)
```

### Test Organization
- Unit tests: Test individual functions/methods in isolation
- Integration tests: Test component interactions
- End-to-end tests: Test complete trading workflows
- Keep test files next to the code they test
- Use `conftest.py` for shared fixtures
- Aim for 80%+ code coverage, but focus on critical paths

## 🚨 Error Handling

### Exception Best Practices

```python
# Create custom exceptions for your trading domain
class TradingError(Exception):
    """Base exception for trading-related errors."""
    pass

class InsufficientFundsError(TradingError):
    """Raised when account has insufficient funds."""
    def __init__(self, required: Decimal, available: Decimal):
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient funds: required {required}, available {available}"
        )

# Use specific exception handling
try:
    execute_trade(trade_request)
except InsufficientFundsError as e:
    logger.warning(f"Trade failed: {e}")
    return TradeResult(success=False, reason="insufficient_funds")
except TradingError as e:
    logger.error(f"Trading error: {e}")
    return TradeResult(success=False, reason="trading_error")

# Use context managers for resource management
from contextlib import contextmanager

@contextmanager
def trading_session():
    """Provide a transactional scope for trading operations."""
    session = get_trading_session()
    transaction = session.begin_transaction()
    try:
        yield session
        transaction.commit()
    except Exception:
        transaction.rollback()
        raise
    finally:
        session.close()
```

### Structured Logging Strategy

```python
import structlog
from functools import wraps

# Configure structured logging
logger = structlog.get_logger(__name__)

# Log function entry/exit for debugging
def log_execution(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug(f"Entering {func.__name__}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"Exiting {func.__name__} successfully")
            return result
        except Exception as e:
            logger.exception(f"Error in {func.__name__}: {e}")
            raise
    return wrapper

# Log with context for trading operations
logger.info(
    "trade_executed",
    symbol=trade.symbol,
    quantity=trade.quantity,
    price=trade.entry_price,
    user_id=user.id,
    execution_time=execution_time
)
```

### Common Commands

```bash
# Package management (UV only)
uv add <package>
uv add --dev <package>
uv remove <package>
uv sync

# Testing
uv run pytest
uv run pytest --cov=src --cov-report=html
uv run pytest tests/test_module.py -v

# Code quality
uv run ruff format .
uv run ruff check .
uv run ruff check --fix .
uv run mypy .

# Database
alembic upgrade head
alembic revision --autogenerate -m "description"

# Running the app
uvicorn src.main:app --reload
```

## 🔧 Configuration Management

### Environment Variables and Settings

```python
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings with validation."""
    app_name: str = "Trading-NTrader"
    debug: bool = False
    database_url: str
    redis_url: str = "redis://localhost:6379"
    api_key: str
    max_connections: int = 100
    
    # Trading specific settings
    max_position_size: int = 10000
    risk_limit_percent: float = 2.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

# Usage
settings = get_settings()
```

## 🔍 Data Models and Validation

### Example Pydantic Models (v2)

```python
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Optional, List
from decimal import Decimal
from enum import Enum

class TradeType(str, Enum):
    BUY = "buy"
    SELL = "sell"

class TradeBase(BaseModel):
    """Base trade model with common fields."""
    symbol: str = Field(..., min_length=1, max_length=10)
    quantity: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=0, decimal_places=2)
    trade_type: TradeType
    notes: Optional[str] = None

    @field_validator('price')
    @classmethod
    def validate_price(cls, v):
        if v > Decimal('100000'):
            raise ValueError('Price cannot exceed 100,000')
        return v

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        },
        "use_enum_values": True
    }

class TradeCreate(TradeBase):
    """Model for creating new trades."""
    pass

class Trade(TradeBase):
    """Complete trade model with database fields."""
    id: int
    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    model_config = {
        "from_attributes": True  # Enable ORM mode
    }
```

## 🚀 Performance Considerations

### Optimization Guidelines
- Profile before optimizing - use `cProfile` or `py-spy`
- Use `lru_cache` for expensive computations
- Prefer generators for large datasets
- Use `asyncio` for I/O-bound operations
- Consider `multiprocessing` for CPU-bound tasks
- Cache database queries appropriately

### Example Optimization

```python
from functools import lru_cache
import asyncio
from typing import AsyncIterator

@lru_cache(maxsize=1000)
def calculate_technical_indicator(prices: tuple, period: int) -> Decimal:
    """Cache results of expensive technical calculations."""
    # Convert tuple back to list for processing
    price_list = list(prices)
    return moving_average(price_list, period)

async def process_market_data() -> AsyncIterator[dict]:
    """Process large market dataset without loading all into memory."""
    async with aiofiles.open('market_data.json', mode='r') as f:
        async for line in f:
            data = json.loads(line)
            # Process and yield each market tick
            yield process_tick(data)
```

## 🛡️ Security Best Practices

### Security Guidelines
- Never commit secrets - use environment variables
- Validate all user input with Pydantic
- Use parameterized queries for database operations
- Implement rate limiting for APIs
- Keep dependencies updated with `uv`
- Use HTTPS for all external communications
- Implement proper authentication and authorization

### Example Security Implementation

```python
from passlib.context import CryptContext
import secrets

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_urlsafe(length)
```

## 🔍 Debugging Tools

### Debugging Commands

```bash
# Interactive debugging with ipdb
uv add --dev ipdb
# Add breakpoint: import ipdb; ipdb.set_trace()

# Memory profiling
uv add --dev memory-profiler
uv run python -m memory_profiler script.py

# Line profiling
uv add --dev line-profiler
# Add @profile decorator to functions

# Debug with rich traceback
uv add --dev rich
# In code: from rich.traceback import install; install()
```

## 🔄 Git Workflow

### Branch Strategy
- `main` - Production-ready code
- `develop` - Integration branch for features
- `feature/*` - New features
- `fix/*` - Bug fixes
- `docs/*` - Documentation updates
- `refactor/*` - Code refactoring
- `test/*` - Test additions or fixes

### Commit Message Format
Never include "claude code" or "written by claude code" in commit messages:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: feat, fix, docs, style, refactor, test, chore

Example:
```
feat(trading): add position size validation

- Implement max position size checks
- Add risk limit validation
- Update trade creation endpoint

Closes #123
```

## 🔍 Search Command Requirements

**CRITICAL**: Always use `rg` (ripgrep) instead of traditional `grep` and `find` commands:

```bash
# ❌ Don't use grep
grep -r "pattern" .

# ✅ Use rg instead
rg "pattern"

# ❌ Don't use find with name
find . -name "*.py"

# ✅ Use rg with file filtering
rg --files -g "*.py"
```

### Search and Development Tools

- Use `rg` (ripgrep) for searching, never grep/find
- Functions must be <50 lines
- Classes must be <100 lines  
- Files must be <500 lines
- Line length must be max 100 characters

### Recent Tech Stack Updates

- FastAPI with async/await patterns
- SQLAlchemy 2.0 with async sessions
- Pydantic v2 for validation
- UV for package management
- structlog for structured logging
- pytest with fixtures for testing

## ⚠️ Important Notes

- **NEVER ASSUME OR GUESS** - When in doubt, ask for clarification
- **Always verify file paths and module names** before use
- **Keep CLAUDE.md updated** when adding new patterns or dependencies
- **Test your code** - No feature is complete without tests
- **Document your decisions** - Future developers (including yourself) will thank you
- **Never include AI references in commit messages**

## 📚 Useful Resources

### Essential Tools
- UV Documentation: https://github.com/astral-sh/uv
- Ruff: https://github.com/astral-sh/ruff
- Pytest: https://docs.pytest.org/
- Pydantic: https://docs.pydantic.dev/
- FastAPI: https://fastapi.tiangolo.com/

### Python Best Practices
- PEP 8: https://pep8.org/
- PEP 484 (Type Hints): https://www.python.org/dev/peps/pep-0484/
- The Hitchhiker's Guide to Python: https://docs.python-guide.org/

---
*This document is a living guide. Update it as the project evolves and new patterns emerge.*
*Generated from Python Backend Development Constitution v1.0.1*
- Always use context7 mcp for library documentation which contains examples on how best to use the library.
- run formatting and linting check before every commit.
- when testing the application follow instructions from README. keep it upto date if any of the instructions change, validate before making changes.
- Always use env variables for IBKR API connectivity settings, do not override it unless explicitly mentioned. When developing make sure within code we are always using the env variables for IBKR connection establishment as opposed to hardcoded values or other sources.

## Active Technologies
- Python 3.11+ + SQLAlchemy 2.0 (async), Alembic, Pydantic 2.5+, asyncpg, PostgreSQL 16+ (004-postgresql-metadata-storage)
- PostgreSQL 16+ with async connection pooling (004-postgresql-metadata-storage)
- Python 3.11+ + FastAPI, Jinja2, HTMX, Tailwind CSS (005-webui-foundation)
- PostgreSQL (existing backtest metadata via SQLAlchemy 2.0 async) (005-webui-foundation)
- Python 3.11+ + FastAPI 0.109+, Jinja2, HTMX, Tailwind CSS, SQLAlchemy 2.0 (async), Pydantic 2.5+ (006-interactive-backtest-lists)
- PostgreSQL 16+ (existing backtest metadata via SQLAlchemy 2.0 async) (006-interactive-backtest-lists)
- Python 3.11+ (matches existing codebase) + FastAPI 0.109+, Jinja2 3.1+, Pydantic 2.5+, SQLAlchemy 2.0+ (async), HTMX 1.9+, Tailwind CSS (007-backtest-detail-view)
- PostgreSQL 16+ (existing backtest_runs and performance_metrics tables) (007-backtest-detail-view)
- Python 3.11+ + FastAPI 0.109+, Pydantic 2.5+, SQLAlchemy 2.0 (async), structlog (008-chart-apis)
- PostgreSQL 16+ (backtest metadata), Parquet files (OHLCV market data) (008-chart-apis)

## Recent Changes
- 004-postgresql-metadata-storage: Added Python 3.11+ + SQLAlchemy 2.0 (async), Alembic, Pydantic 2.5+, asyncpg, PostgreSQL 16+
