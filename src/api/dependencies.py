"""
FastAPI dependencies for web UI.

Provides shared dependencies for database sessions and template rendering.
"""

from typing import Annotated, AsyncGenerator

from fastapi import Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_session as get_db_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Get async database session for request lifecycle.

    Provides a database session that is automatically closed after request completion.

    Yields:
        AsyncSession: Database session for the request

    Example:
        >>> @router.get("/")
        ... async def route(db: Annotated[AsyncSession, Depends(get_db)]):
        ...     # Use db session
        ...     pass
    """
    async with get_db_session() as session:
        yield session


def get_backtest_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> BacktestRepository:
    """
    Get backtest repository instance.

    Args:
        session: Database session from dependency injection

    Returns:
        BacktestRepository instance configured with the session

    Example:
        >>> @router.get("/")
        ... async def route(repo: Annotated[BacktestRepository, Depends(get_backtest_repository)]):
        ...     backtests = await repo.find_recent(limit=10)
    """
    return BacktestRepository(session)


def get_backtest_query_service(
    repository: Annotated[BacktestRepository, Depends(get_backtest_repository)],
) -> BacktestQueryService:
    """
    Get backtest query service instance.

    Args:
        repository: BacktestRepository from dependency injection

    Returns:
        BacktestQueryService instance configured with the repository

    Example:
        >>> @router.get("/")
        ... async def route(service: Annotated[BacktestQueryService, Depends(get_backtest_query_service)]):
        ...     backtests = await service.list_recent_backtests(limit=20)
    """
    return BacktestQueryService(repository)


# Type aliases for cleaner route signatures
DbSession = Annotated[AsyncSession, Depends(get_db)]
BacktestRepo = Annotated[BacktestRepository, Depends(get_backtest_repository)]
BacktestService = Annotated[BacktestQueryService, Depends(get_backtest_query_service)]


def get_templates() -> Jinja2Templates:
    """
    Get Jinja2 templates instance.

    Returns templates configured to look in the templates/ directory at repository root.

    Returns:
        Jinja2Templates instance

    Example:
        >>> templates = get_templates()
        >>> return templates.TemplateResponse("dashboard.html", {"request": request})
    """
    return Jinja2Templates(directory="templates")
