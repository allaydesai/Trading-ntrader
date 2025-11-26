"""Fixtures for API integration tests."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.db.base import Base
from src.db.models.backtest import BacktestRun


def get_worker_id(request):
    """Get pytest-xdist worker ID or 'master' if running without xdist."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    return worker_id


@pytest.fixture(scope="function")
async def db_session(request):
    """
    Create async database session for API integration tests.

    Creates a fresh database session for each test, ensuring test isolation.
    Uses schema-based isolation when running with pytest-xdist to prevent
    conflicts between parallel test workers.

    Yields:
        AsyncSession: Database session for test use
    """
    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    # Create async engine
    engine = create_async_engine(async_url, echo=False)

    # Create schema and tables
    async with engine.begin() as conn:
        # Create schema
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))

        # Set search path to use our test schema
        await conn.execute(text(f"SET search_path TO {schema_name}"))

        # Create all tables in the test schema
        await conn.run_sync(Base.metadata.create_all)

    # Create session with schema search path
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_maker() as session:
        # Set search path for this session
        await session.execute(text(f"SET search_path TO {schema_name}"))
        yield session
        await session.rollback()

    # Cleanup: drop schema and tables
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))

    await engine.dispose()


@pytest.fixture
async def sample_backtest_run(db_session):
    """Create a sample backtest run for testing."""
    backtest = BacktestRun(
        run_id=uuid4(),
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2025, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.5"),
        config_snapshot={"fast_period": 10, "slow_period": 20},
    )
    db_session.add(backtest)
    await db_session.commit()
    await db_session.refresh(backtest)
    return backtest
