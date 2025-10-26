"""Database integration test fixtures."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.config import get_settings
from src.db.base import Base


@pytest.fixture(scope="function")
async def db_session():
    """
    Create async database session for integration tests.

    Creates a fresh database session for each test, ensuring test isolation.
    Tables are created before the test and dropped after to maintain a clean state.

    Yields:
        AsyncSession: Database session for test use
    """
    settings = get_settings()
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Create async engine
    engine = create_async_engine(async_url, echo=False)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session
    async_session_maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_maker() as session:
        yield session
        await session.rollback()

    # Cleanup: drop tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()
