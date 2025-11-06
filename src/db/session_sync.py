"""Synchronous database session management for CLI commands.

This module provides synchronous database access using SQLAlchemy's sync engine
and psycopg2 driver. It's designed specifically for CLI commands that don't need
async capabilities.

For async operations (future API endpoints), use src.db.session instead.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings

# Global sync engine and session maker (lazy initialization)
sync_engine = None
SyncSessionLocal = None


def get_sync_engine():
    """
    Get or create synchronous database engine.

    Uses psycopg2 driver for synchronous PostgreSQL access.
    Engine is created once and reused for the application lifetime.

    Returns:
        Engine: Synchronous SQLAlchemy engine, or None if database not configured

    Example:
        >>> engine = get_sync_engine()
        >>> if engine:
        ...     with engine.connect() as conn:
        ...         result = conn.execute(text("SELECT 1"))
    """
    global sync_engine

    if sync_engine is not None:
        return sync_engine

    settings = get_settings()

    if not settings.database_url:
        return None

    # Use synchronous psycopg2 driver (no +asyncpg suffix)
    # database_url is already postgresql://... format
    sync_url = settings.database_url

    sync_engine = create_engine(
        sync_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=3600,  # Recycle connections after 1 hour
    )

    return sync_engine


def get_sync_session_maker():
    """
    Get or create synchronous session maker.

    Session maker is created once and reused. Each call to the session maker
    returns a new session instance.

    Returns:
        sessionmaker: Synchronous session factory, or None if database not configured

    Example:
        >>> SessionLocal = get_sync_session_maker()
        >>> if SessionLocal:
        ...     with SessionLocal() as session:
        ...         result = session.execute(text("SELECT 1"))
    """
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

    Provides a transactional context manager that automatically:
    - Creates a new session
    - Commits on success
    - Rolls back on exceptions
    - Closes the session

    Yields:
        Session: Synchronous SQLAlchemy session

    Raises:
        RuntimeError: If database is not configured (DATABASE_URL not set)

    Example:
        >>> with get_sync_session() as session:
        ...     repository = SyncBacktestRepository(session)
        ...     backtest = repository.get_backtest_by_id(run_id)
        ...     print(backtest.strategy_name)
    """
    session_maker = get_sync_session_maker()

    if session_maker is None:
        raise RuntimeError("Database not configured. Check DATABASE_URL in .env file")

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
    """
    Dispose all sync database connections.

    Closes all connections in the pool and resets the global engine.
    Useful for testing and shutdown scenarios.

    Example:
        >>> dispose_sync_connections()
        >>> # All connections closed, engine reset to None
    """
    global sync_engine, SyncSessionLocal

    if sync_engine is not None:
        sync_engine.dispose()
        sync_engine = None

    SyncSessionLocal = None
