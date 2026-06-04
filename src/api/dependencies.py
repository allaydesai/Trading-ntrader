"""
FastAPI dependencies for web UI and REST APIs.

Provides shared dependencies for database sessions, template rendering,
and service instances.
"""

import os
from typing import Annotated, AsyncGenerator

from fastapi import Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import CatalogSettings
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.catalog_dividend_repository import CatalogDividendRepository
from src.db.repositories.catalog_instrument_repository import CatalogInstrumentRepository
from src.db.repositories.catalog_stock_split_repository import CatalogStockSplitRepository
from src.db.session import get_session as get_db_session
from src.services.backtest_query import BacktestQueryService
from src.services.data_catalog import DataCatalogService
from src.services.firstrate.metadata_service import MetadataService


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


def get_metadata_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MetadataService:
    """Get MetadataService with async repository.

    Args:
        session: Database session from dependency injection.

    Returns:
        MetadataService configured with async CatalogInstrumentRepository.
    """
    repo = CatalogInstrumentRepository(session)
    return MetadataService(async_repo=repo)


async def get_catalog_list(
    service: Annotated[MetadataService, Depends(get_metadata_service)],
) -> list[str]:
    """Get list of available catalog names from the database.

    Args:
        service: MetadataService dependency.

    Returns:
        Sorted list of catalog names that have imported instruments.
    """
    return await service.list_catalog_names()


def get_default_catalog() -> str:
    """Get default catalog name from settings.

    Returns:
        Default catalog name (may be empty string).
    """
    settings = CatalogSettings()
    return settings.default_catalog_name


# Type aliases for explorer dependencies
Metadata = Annotated[MetadataService, Depends(get_metadata_service)]
CatalogList = Annotated[list[str], Depends(get_catalog_list)]
DefaultCatalog = Annotated[str, Depends(get_default_catalog)]


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
        ... async def route(
        ...     service: Annotated[BacktestQueryService, Depends(get_backtest_query_service)]
        ... ):
        ...     backtests = await service.list_recent_backtests(limit=20)
    """
    return BacktestQueryService(repository)


def get_dividend_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CatalogDividendRepository:
    """Get async catalog dividend repository instance.

    Args:
        session: Database session from dependency injection.

    Returns:
        CatalogDividendRepository configured with the session.
    """
    return CatalogDividendRepository(session)


def get_stock_split_repository(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> CatalogStockSplitRepository:
    """Get async catalog stock split repository instance.

    Args:
        session: Database session from dependency injection.

    Returns:
        CatalogStockSplitRepository configured with the session.
    """
    return CatalogStockSplitRepository(session)


# Type aliases for cleaner route signatures
DbSession = Annotated[AsyncSession, Depends(get_db)]
BacktestRepo = Annotated[BacktestRepository, Depends(get_backtest_repository)]
BacktestService = Annotated[BacktestQueryService, Depends(get_backtest_query_service)]
DividendRepo = Annotated[CatalogDividendRepository, Depends(get_dividend_repository)]
SplitRepo = Annotated[CatalogStockSplitRepository, Depends(get_stock_split_repository)]


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


def get_data_catalog_service() -> DataCatalogService:
    """
    Get DataCatalogService instance for Parquet catalog operations.

    Creates a DataCatalogService using the NAUTILUS_PATH environment variable,
    or defaults to "./data/catalog" if not set.

    Returns:
        DataCatalogService instance

    Example:
        >>> @router.get("/")
        ... def route(catalog: DataCatalog):
        ...     bars = catalog.query_bars("AAPL.NASDAQ", start, end)
    """
    catalog_path = os.environ.get("NAUTILUS_PATH", "./data/catalog")
    return DataCatalogService(catalog_path=catalog_path)


# Type alias for DataCatalogService dependency
DataCatalog = Annotated[DataCatalogService, Depends(get_data_catalog_service)]
