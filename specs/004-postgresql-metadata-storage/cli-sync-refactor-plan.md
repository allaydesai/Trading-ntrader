# CLI Sync Refactor Plan - Fix Async/Sync Boundary Issues

**Created**: 2025-01-24
**Status**: Proposed
**Context**: Fix failing CLI integration tests due to event loop lifecycle conflicts

## Problem Statement

Current CLI commands use `asyncio.run()` internally which creates/closes event loops. When testing:
1. Test setup runs `asyncio.run(setup_test_data())` - creates Loop 1, saves data, closes Loop 1
2. CLI command runs `asyncio.run(_command_async())` - creates Loop 2, tries to use connections from Loop 1
3. AsyncPG connections are bound to their creating event loop → failure when Loop 1 is closed

**Error**: `RuntimeError: unable to perform operation on <TCPTransport closed=True>`

## Solution: Sync-First CLI Architecture (Approach 1)

CLI commands are inherently synchronous (user types command, waits for result). We should:
- Use synchronous SQLAlchemy for CLI commands only
- Keep async SQLAlchemy for API/web endpoints (future)
- Maintain clear separation: CLI = sync, API = async

## Implementation Plan

### Phase 1: Create Sync Database Infrastructure

#### 1.1 Create Sync Session Factory
**File**: `src/db/session_sync.py` (new)

```python
"""Synchronous database session management for CLI commands."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from typing import Generator

from src.config import get_settings

# Global sync engine (lazy initialization)
sync_engine = None
SyncSessionLocal = None


def get_sync_engine():
    """Get or create synchronous database engine."""
    global sync_engine

    if sync_engine is not None:
        return sync_engine

    settings = get_settings()

    if not settings.database_url:
        return None

    # Use synchronous psycopg2 driver
    sync_url = settings.database_url  # postgresql://...

    sync_engine = create_engine(
        sync_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=3600,   # Recycle connections after 1 hour
    )

    return sync_engine


def get_sync_session_maker():
    """Get or create synchronous session maker."""
    global SyncSessionLocal

    if SyncSessionLocal is not None:
        return SyncSessionLocal

    engine = get_sync_engine()
    if engine is None:
        return None

    SyncSessionLocal = sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    return SyncSessionLocal


@contextmanager
def get_sync_session() -> Generator[Session, None, None]:
    """
    Get synchronous database session for CLI commands.

    Yields:
        Session: Synchronous SQLAlchemy session

    Raises:
        RuntimeError: If database is not configured

    Example:
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.get_backtest_by_id(run_id)
    """
    session_maker = get_sync_session_maker()

    if session_maker is None:
        raise RuntimeError("Database not configured. Check DATABASE_URL in .env")

    session = session_maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_sync_connections():
    """Dispose all sync database connections."""
    global sync_engine, SyncSessionLocal

    if sync_engine is not None:
        sync_engine.dispose()
        sync_engine = None

    SyncSessionLocal = None
```

**Tests**: `tests/component/test_db_session_sync.py`
- Test engine creation with correct parameters
- Test session lifecycle (commit, rollback, close)
- Test error handling when DB not configured
- Test connection pooling behavior

---

#### 1.2 Create Sync Repository
**File**: `src/db/repositories/backtest_repository_sync.py` (new)

```python
"""Synchronous repository for backtest database operations (CLI use)."""

from uuid import UUID
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import select, desc, and_

from src.db.models import BacktestRun, PerformanceMetrics


class SyncBacktestRepository:
    """
    Synchronous repository for backtest operations.

    Used by CLI commands that don't need async capabilities.
    For async operations (API endpoints), use BacktestRepository instead.
    """

    def __init__(self, session: Session):
        """
        Initialize repository with database session.

        Args:
            session: Synchronous SQLAlchemy session
        """
        self.session = session

    def get_backtest_by_id(self, run_id: UUID) -> Optional[BacktestRun]:
        """
        Retrieve backtest by run ID.

        Args:
            run_id: Unique backtest identifier

        Returns:
            BacktestRun if found, None otherwise
        """
        stmt = (
            select(BacktestRun)
            .filter(BacktestRun.run_id == run_id)
            .outerjoin(PerformanceMetrics)  # Eager load metrics
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def find_recent(
        self,
        limit: int = 20,
        strategy_name: Optional[str] = None,
        instrument_symbol: Optional[str] = None,
    ) -> List[BacktestRun]:
        """
        Find recent backtests with optional filters.

        Args:
            limit: Maximum number of results
            strategy_name: Filter by strategy name
            instrument_symbol: Filter by instrument

        Returns:
            List of backtest runs, newest first
        """
        stmt = select(BacktestRun).outerjoin(PerformanceMetrics)

        # Apply filters
        if strategy_name:
            stmt = stmt.filter(BacktestRun.strategy_name == strategy_name)

        if instrument_symbol:
            stmt = stmt.filter(BacktestRun.instrument_symbol == instrument_symbol)

        # Order and limit
        stmt = stmt.order_by(desc(BacktestRun.created_at)).limit(limit)

        return list(self.session.execute(stmt).scalars().all())

    # Add other methods as needed:
    # - find_by_strategy_type()
    # - find_top_performers()
    # - etc.
```

**Tests**: `tests/unit/db/test_backtest_repository_sync.py`
- Mirror existing async repository tests
- Test all query methods
- Test filtering and sorting
- Test error cases

---

### Phase 2: Refactor CLI Commands

#### 2.1 Update `show` Command
**File**: `src/cli/commands/show.py`

**Before** (async):
```python
@click.command(name="show")
def show_backtest_details(run_id: str):
    asyncio.run(_show_backtest_async(run_id))

async def _show_backtest_async(run_id_str: str):
    async with get_session() as session:
        repository = BacktestRepository(session)
        backtest = await service.get_backtest_by_id(run_id)
```

**After** (sync):
```python
from src.db.session_sync import get_sync_session
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository

@click.command(name="show")
def show_backtest_details(run_id: str):
    """Display complete details of a specific backtest execution."""
    try:
        # Validate UUID format
        try:
            run_id_uuid = UUID(run_id)
        except ValueError:
            console.print(
                f"[red]Error:[/red] Invalid UUID format: {run_id}",
                style="bold red",
            )
            console.print(
                "\n[yellow]Tip:[/yellow] UUIDs should be in format: "
                "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
            )
            return

        # Query database synchronously
        with get_sync_session() as session:
            repository = SyncBacktestRepository(session)
            backtest = repository.get_backtest_by_id(run_id_uuid)

            if backtest is None:
                console.print(
                    f"\n[red]Error:[/red] Backtest with ID {run_id} not found",
                    style="bold red",
                )
                console.print(
                    "\n[yellow]Tip:[/yellow] Use 'ntrader backtest history' "
                    "to see available backtests"
                )
                return

            # Display the backtest details (unchanged)
            _display_backtest(backtest)

    except Exception as e:
        console.print(f"\n[red]Error:[/red] {str(e)}", style="bold red")
        raise


# _display_backtest() remains unchanged - it's synchronous already
```

**Changes Required**:
- Remove `asyncio.run()`
- Remove `async def _show_backtest_async()`
- Replace `get_session()` with `get_sync_session()`
- Replace `BacktestRepository` with `SyncBacktestRepository`
- Remove all `await` keywords
- Keep display logic unchanged

**Files to Update**:
- `src/cli/commands/show.py`
- `src/cli/commands/reproduce.py`
- `src/cli/commands/history.py`
- `src/cli/commands/compare.py`

---

### Phase 3: Update Integration Tests

#### 3.1 Update Test Fixtures
**File**: `tests/integration/db/conftest.py`

**Add sync fixture**:
```python
@pytest.fixture(scope="function")
def sync_db_session(request):
    """
    Provide synchronous database session for CLI integration tests.

    Uses schema isolation for parallel test execution.
    """
    settings = get_settings()
    sync_url = settings.database_url

    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    # Create sync engine
    engine = create_engine(sync_url)

    # Setup: Create schema and tables
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        conn.execute(text(f"SET search_path TO {schema_name}"))
        Base.metadata.create_all(conn)

    # Create session factory
    SessionLocal = sessionmaker(bind=engine)

    # Yield session with schema context
    with SessionLocal() as session:
        session.execute(text(f"SET search_path TO {schema_name}"))
        yield session

    # Cleanup: Drop schema
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))

    engine.dispose()
```

---

#### 3.2 Simplify CLI Tests
**File**: `tests/integration/db/test_cli_show.py`

**Before** (complex async mocking):
```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_show_displays_successful_backtest_details(db_session: AsyncSession):
    # Setup with async
    async def setup_test_data():
        async with get_test_session() as session:
            repository = BacktestRepository(session)
            ...

    run_id = asyncio.run(setup_test_data())

    # Mock get_session
    async def mock_get_session():
        yield db_session

    with patch("src.cli.commands.show.get_session", ...):
        result = runner.invoke(show, [str(run_id)])
```

**After** (simple sync):
```python
@pytest.mark.integration
def test_show_displays_successful_backtest_details(sync_db_session: Session):
    """Test show command displays complete backtest details."""
    # Setup test data (synchronous)
    repository = SyncBacktestRepository(sync_db_session)

    backtest = repository.create_backtest_run(
        run_id=uuid4(),
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.237"),
        config_snapshot={
            "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
            "config_path": "config/strategies/sma_crossover.yaml",
            "version": "1.0",
            "config": {"fast_period": 10, "slow_period": 50, "risk_percent": 2.0},
        },
    )

    repository.create_performance_metrics(
        backtest_run_id=backtest.id,
        total_return=Decimal("0.2547"),
        final_balance=Decimal("125470.00"),
        cagr=Decimal("0.2547"),
        sharpe_ratio=Decimal("1.85"),
        # ... other metrics
    )

    sync_db_session.commit()

    # Test CLI command (no mocking needed - uses same DB)
    runner = CliRunner()
    result = runner.invoke(show, [str(backtest.run_id)])

    # Assertions
    assert result.exit_code == 0, f"Command failed: {result.output}"
    assert "SMA Crossover" in result.output
    assert "AAPL" in result.output
    assert "success" in result.output.lower()
    # ... other assertions
```

**Benefits**:
- ✅ No async/await complexity
- ✅ No event loop juggling
- ✅ No mocking of `get_session()`
- ✅ Direct, straightforward testing
- ✅ Tests actually use the same database connection

---

## Implementation Checklist

### Phase 1: Sync Infrastructure (2-3 hours)
- [ ] Create `src/db/session_sync.py`
  - [ ] Implement `get_sync_engine()`
  - [ ] Implement `get_sync_session_maker()`
  - [ ] Implement `get_sync_session()` context manager
  - [ ] Add `dispose_sync_connections()`
- [ ] Create tests `tests/component/test_db_session_sync.py`
  - [ ] Test engine creation
  - [ ] Test session lifecycle
  - [ ] Test error handling
  - [ ] Test connection pooling
- [ ] Create `src/db/repositories/backtest_repository_sync.py`
  - [ ] Implement `get_backtest_by_id()`
  - [ ] Implement `find_recent()`
  - [ ] Implement `find_by_strategy_type()`
  - [ ] Add other query methods as needed
- [ ] Create tests `tests/unit/db/test_backtest_repository_sync.py`
  - [ ] Mirror async repository test coverage
  - [ ] Test all query methods
  - [ ] Test filtering and sorting

### Phase 2: Refactor CLI Commands (3-4 hours)
- [ ] Refactor `src/cli/commands/show.py`
  - [ ] Remove `asyncio.run()`
  - [ ] Replace async session with sync
  - [ ] Update imports
  - [ ] Test manually
- [ ] Refactor `src/cli/commands/reproduce.py`
  - [ ] Same pattern as show.py
- [ ] Refactor `src/cli/commands/history.py`
  - [ ] Same pattern as show.py
- [ ] Refactor `src/cli/commands/compare.py`
  - [ ] Same pattern as show.py

### Phase 3: Update Tests (2-3 hours)
- [ ] Update `tests/integration/db/conftest.py`
  - [ ] Add `sync_db_session` fixture with schema isolation
- [ ] Update `tests/integration/db/test_cli_show.py`
  - [ ] Convert all tests to sync pattern
  - [ ] Remove async fixtures
  - [ ] Remove mocking
  - [ ] Simplify test setup
- [ ] Update `tests/integration/db/test_cli_reproduce.py`
  - [ ] Same pattern as test_cli_show.py
- [ ] Update `tests/integration/db/test_cli_history.py`
  - [ ] Same pattern as test_cli_show.py
- [ ] Update `tests/integration/db/test_cli_compare.py`
  - [ ] Same pattern as test_cli_show.py

### Phase 4: Validation (1 hour)
- [ ] Run all tests sequentially: `uv run pytest -n0`
- [ ] Run tests in parallel: `uv run pytest -n auto`
- [ ] Verify CLI commands work manually
- [ ] Run linting: `uv run ruff check .`
- [ ] Run type checking: `uv run mypy .`

---

## Migration Strategy

### Step 1: Build Alongside Existing (Non-Breaking)
- Create sync infrastructure in new files
- Don't modify existing async code
- No breaking changes

### Step 2: Migrate CLI Commands One at a Time
- Start with `show.py` (simplest)
- Test thoroughly before moving to next
- Can rollback individual commands if issues arise

### Step 3: Update Tests After Each Command
- Update tests immediately after refactoring each command
- Verify tests pass before moving to next command

### Step 4: Clean Up
- Once all CLI commands migrated and tested
- Remove unused async helper functions
- Update documentation

---

## Rollback Plan

If issues arise:
1. Sync infrastructure is isolated - can be removed without affecting async code
2. Each CLI command can be reverted independently
3. Tests can use old fixtures while some commands use new pattern
4. No database schema changes required

---

## Future Considerations

### When API Endpoints Are Added
- API will use async `BacktestRepository` (already exists)
- CLI will use sync `SyncBacktestRepository` (new)
- Both repositories operate on same database schema
- Clear separation of concerns

### Code Duplication
- Sync and async repositories will have similar code
- This is acceptable because:
  - CLI and API have different performance characteristics
  - Async overhead not needed for CLI
  - Simpler testing for sync code
  - Clear separation makes codebase easier to understand

---

## Estimated Effort

- **Phase 1 (Infrastructure)**: 2-3 hours
- **Phase 2 (CLI Refactor)**: 3-4 hours
- **Phase 3 (Test Updates)**: 2-3 hours
- **Phase 4 (Validation)**: 1 hour
- **Total**: 8-11 hours

---

## Success Criteria

- [ ] All CLI commands work without async/event loop issues
- [ ] All integration tests pass with simplified sync pattern
- [ ] Tests run successfully in parallel (`pytest -n auto`)
- [ ] No event loop-related errors in test output
- [ ] Code is simpler and easier to understand
- [ ] No breaking changes to user-facing CLI interface
