"""Database integration test fixtures."""

import pytest
import asyncio
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import get_settings
from src.db.base import Base


def get_worker_id(request):
    """Get pytest-xdist worker ID or 'master' if running without xdist."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    return worker_id


@pytest.fixture(scope="function")
async def db_session(request):
    """
    Create async database session for integration tests.

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
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        # Set search path for this session
        await session.execute(text(f"SET search_path TO {schema_name}"))
        yield session
        await session.rollback()

    # Cleanup: drop schema and tables
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF NOT EXISTS {schema_name} CASCADE"))

    await engine.dispose()


@pytest.fixture(scope="function")
def test_db_schema(request):
    """
    Provide test database schema context for synchronous CLI tests.

    This fixture creates an isolated schema and provides a session factory
    that can be used in synchronous tests with Click's CliRunner.

    Returns:
        tuple: (schema_name, session_factory_async_func)
    """
    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    # Create engine
    engine = create_async_engine(async_url, echo=False)

    # Setup: create schema and tables
    async def setup():
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
            await conn.execute(text(f"SET search_path TO {schema_name}"))
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(setup())

    # Session factory that uses the test schema
    @asynccontextmanager
    async def get_test_session():
        async_session_maker = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        async with async_session_maker() as session:
            await session.execute(text(f"SET search_path TO {schema_name}"))
            yield session

    yield schema_name, get_test_session

    # Cleanup: dispose engine and drop schema
    async def cleanup():
        # First dispose all connections
        await engine.dispose()

        # Create a new engine just for cleanup
        cleanup_engine = create_async_engine(async_url, echo=False)
        async with cleanup_engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        await cleanup_engine.dispose()

    asyncio.run(cleanup())


@pytest.fixture(scope="function")
def sync_db_session(request):
    """
    Create synchronous database session for CLI integration tests.

    Uses schema isolation for parallel test execution with pytest-xdist.
    Each worker gets its own schema to prevent conflicts.

    Yields:
        Session: Synchronous SQLAlchemy session
    """
    settings = get_settings()
    sync_url = settings.database_url  # Use synchronous psycopg2 driver

    # Get worker ID for schema isolation
    worker_id = get_worker_id(request)
    schema_name = f"test_{worker_id}".replace("-", "_")

    # Create sync engine
    engine = create_engine(sync_url, echo=False)

    # Setup: Create schema and tables
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
        conn.execute(text(f"SET search_path TO {schema_name}"))
        Base.metadata.create_all(conn)

    # Create session factory
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    # Yield session with schema context
    with SessionLocal() as session:
        session.execute(text(f"SET search_path TO {schema_name}"))
        yield session
        session.rollback()

    # Cleanup: Drop schema
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))

    engine.dispose()
