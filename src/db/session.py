"""Database session management."""

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

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

if settings.database_url:
    # Convert to async URL if needed
    async_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")

    # Create sync engine
    engine = create_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create async engine
    async_engine = create_async_engine(
        async_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
    )
    AsyncSessionLocal = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )


def get_db() -> Session:
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
    if not AsyncSessionLocal:
        raise RuntimeError("Database not configured")
    async with AsyncSessionLocal() as session:
        yield session


async def test_connection() -> bool:
    """Test database connection."""
    if not settings.database_url:
        return False

    try:
        if async_engine:
            async with async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        return False
    except Exception:
        return False