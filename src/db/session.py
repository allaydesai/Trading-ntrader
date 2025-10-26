"""Database session management."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings

settings = get_settings()


# Sync engine for migrations
engine = None
SessionLocal = None

# Async engine for application
async_engine = None
AsyncSessionLocal = None


def get_async_engine():
    """Get or create async engine."""
    global async_engine
    if async_engine is None and settings.database_url:
        async_url = settings.database_url.replace(
            "postgresql://", "postgresql+asyncpg://"
        )
        async_engine = create_async_engine(
            async_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_pre_ping=True,  # Verify connection health before use
            pool_recycle=3600,  # Recycle connections after 1 hour
        )
    return async_engine


def get_async_session_maker():
    """Get async session maker."""
    global AsyncSessionLocal
    if AsyncSessionLocal is None:
        engine = get_async_engine()
        if engine:
            AsyncSessionLocal = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
    return AsyncSessionLocal


if settings.database_url:
    # Create sync engine
    engine = create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,  # Verify connection health before use
        pool_recycle=3600,  # Recycle connections after 1 hour
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """Get sync database session."""
    if not SessionLocal:
        raise RuntimeError("Database not configured")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get async database session."""
    session_maker = get_async_session_maker()
    if not session_maker:
        raise RuntimeError("Database not configured")
    async with session_maker() as session:
        yield session


async def test_connection() -> bool:
    """Test database connection."""
    if not settings.database_url:
        return False

    try:
        engine = get_async_engine()
        if engine:
            # Create a fresh connection and immediately close it
            # This avoids connection pool issues in tests
            conn = await engine.connect()
            try:
                await conn.execute(text("SELECT 1"))
                return True
            finally:
                await conn.close()
        return False
    except Exception as e:
        import logging

        logging.error(f"Database connection test failed: {e}")
        return False


async def dispose_all_connections():
    """Dispose all database connections."""
    global async_engine, AsyncSessionLocal
    if async_engine:
        await async_engine.dispose()
        async_engine = None
        AsyncSessionLocal = None
