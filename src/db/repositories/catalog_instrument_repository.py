"""Dual repository for catalog_instruments table.

Provides both async (web) and sync (CLI) access to catalog instrument
metadata, following the project's dual repository pattern.
"""

from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.catalog_instrument import CatalogInstrument


class CatalogInstrumentRepository:
    """Async repository for catalog instrument operations.

    Used by web API endpoints via FastAPI dependency injection.

    Attributes:
        session: Async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, instrument: CatalogInstrument) -> CatalogInstrument:
        """Insert or update a catalog instrument.

        Args:
            instrument: CatalogInstrument model instance.

        Returns:
            The persisted CatalogInstrument with ID assigned.

        Raises:
            DuplicateRecordError: If unique constraint violated unexpectedly.
            DatabaseConnectionError: If database operation fails.
        """
        try:
            existing = await self.get_by_ticker(instrument.catalog_name, instrument.ticker)
            if existing:
                existing.nautilus_id = instrument.nautilus_id
                existing.asset_class = instrument.asset_class
                existing.exchange = instrument.exchange
                existing.name = instrument.name
                existing.sector = instrument.sector
                existing.industry = instrument.industry
                existing.ipo_date = instrument.ipo_date
                existing.country = instrument.country
                existing.state = instrument.state
                existing.date_range_start = instrument.date_range_start
                existing.date_range_end = instrument.date_range_end
                existing.bar_count_daily = instrument.bar_count_daily
                existing.bar_count_hourly = instrument.bar_count_hourly
                existing.bar_count_minute = instrument.bar_count_minute
                existing.bar_count_5min = instrument.bar_count_5min
                existing.bar_count_30min = instrument.bar_count_30min
                await self.session.flush()
                await self.session.refresh(existing)
                return existing

            self.session.add(instrument)
            await self.session.flush()
            await self.session.refresh(instrument)
            return instrument

        except IntegrityError as e:
            raise DuplicateRecordError(
                f"Instrument {instrument.ticker} constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    async def get_by_ticker(self, catalog_name: str, ticker: str) -> Optional[CatalogInstrument]:
        """Find instrument by catalog name and ticker.

        Args:
            catalog_name: Catalog name.
            ticker: Instrument ticker symbol.

        Returns:
            CatalogInstrument or None if not found.
        """
        stmt = select(CatalogInstrument).where(
            and_(
                CatalogInstrument.catalog_name == catalog_name,
                CatalogInstrument.ticker == ticker,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_catalog(
        self,
        catalog_name: str,
        asset_class: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CatalogInstrument]:
        """List instruments in a catalog with optional filtering.

        Args:
            catalog_name: Catalog name to filter by.
            asset_class: Optional asset class filter.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of CatalogInstrument instances.
        """
        conditions = [CatalogInstrument.catalog_name == catalog_name]
        if asset_class:
            conditions.append(CatalogInstrument.asset_class == asset_class)

        stmt = (
            select(CatalogInstrument)
            .where(and_(*conditions))
            .order_by(CatalogInstrument.ticker)
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def search(self, query: str, limit: int = 50) -> List[CatalogInstrument]:
        """Search instruments by partial ticker match.

        Args:
            query: Search string (matched against ticker).
            limit: Maximum results.

        Returns:
            List of matching CatalogInstrument instances.
        """
        escaped = query.replace("%", r"\%").replace("_", r"\_")
        stmt = (
            select(CatalogInstrument)
            .where(CatalogInstrument.ticker.ilike(f"%{escaped}%"))
            .order_by(CatalogInstrument.ticker)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_catalog_names(self) -> List[str]:
        """Get distinct catalog names from the database.

        Returns:
            Sorted list of catalog name strings.
        """
        stmt = (
            select(CatalogInstrument.catalog_name)
            .distinct()
            .order_by(CatalogInstrument.catalog_name)
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]

    async def count_by_catalog(
        self,
        catalog_name: str,
        asset_class: Optional[str] = None,
        search: Optional[str] = None,
    ) -> int:
        """Count instruments in a catalog with optional filters.

        Args:
            catalog_name: Catalog name to filter by.
            asset_class: Optional asset class filter.
            search: Optional prefix search on ticker.

        Returns:
            Total count of matching instruments.
        """
        conditions = [CatalogInstrument.catalog_name == catalog_name]
        if asset_class:
            conditions.append(CatalogInstrument.asset_class == asset_class)
        if search:
            escaped = search.replace("%", r"\%").replace("_", r"\_")
            conditions.append(CatalogInstrument.ticker.ilike(f"{escaped}%"))

        stmt = select(func.count()).select_from(CatalogInstrument).where(and_(*conditions))
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def count_asset_classes(self, catalog_name: str) -> Dict[str, int]:
        """Count instruments per asset class in a catalog.

        Args:
            catalog_name: Catalog name to filter by.

        Returns:
            Dict mapping asset_class string to count.
        """
        stmt = (
            select(CatalogInstrument.asset_class, func.count())
            .where(CatalogInstrument.catalog_name == catalog_name)
            .group_by(CatalogInstrument.asset_class)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    async def list_by_catalog_with_search(
        self,
        catalog_name: str,
        search: Optional[str] = None,
        asset_class: Optional[str] = None,
        sort_by: str = "ticker",
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[CatalogInstrument], int]:
        """List instruments with combined filters, prefix search, and pagination.

        Args:
            catalog_name: Catalog name to filter by.
            search: Optional prefix search on ticker (ILIKE prefix match).
            asset_class: Optional asset class filter.
            sort_by: Sort column name (default: ticker).
            limit: Maximum results per page.
            offset: Pagination offset.

        Returns:
            Tuple of (list of instruments, total count).
        """
        conditions = [CatalogInstrument.catalog_name == catalog_name]
        if asset_class:
            conditions.append(CatalogInstrument.asset_class == asset_class)
        if search:
            escaped = search.replace("%", r"\%").replace("_", r"\_")
            conditions.append(CatalogInstrument.ticker.ilike(f"{escaped}%"))

        where_clause = and_(*conditions)

        count_stmt = select(func.count()).select_from(CatalogInstrument).where(where_clause)
        count_result = await self.session.execute(count_stmt)
        total = count_result.scalar_one()

        _SORTABLE_COLUMNS = {"ticker", "date_range_start", "date_range_end", "bar_count_daily"}
        if sort_by not in _SORTABLE_COLUMNS:
            sort_by = "ticker"
        sort_column = getattr(CatalogInstrument, sort_by)
        data_stmt = (
            select(CatalogInstrument)
            .where(where_clause)
            .order_by(sort_column)
            .limit(limit)
            .offset(offset)
        )
        data_result = await self.session.execute(data_stmt)
        instruments = list(data_result.scalars().all())

        return instruments, total


class SyncCatalogInstrumentRepository:
    """Sync repository for catalog instrument operations.

    Used by CLI commands that don't need async capabilities.

    Attributes:
        session: Synchronous SQLAlchemy session.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, instrument: CatalogInstrument) -> CatalogInstrument:
        """Insert or update a catalog instrument.

        Args:
            instrument: CatalogInstrument model instance.

        Returns:
            The persisted CatalogInstrument with ID assigned.
        """
        try:
            existing = self.get_by_ticker(instrument.catalog_name, instrument.ticker)
            if existing:
                existing.nautilus_id = instrument.nautilus_id
                existing.asset_class = instrument.asset_class
                existing.exchange = instrument.exchange
                existing.name = instrument.name
                existing.sector = instrument.sector
                existing.industry = instrument.industry
                existing.ipo_date = instrument.ipo_date
                existing.country = instrument.country
                existing.state = instrument.state
                existing.date_range_start = instrument.date_range_start
                existing.date_range_end = instrument.date_range_end
                existing.bar_count_daily = instrument.bar_count_daily
                existing.bar_count_hourly = instrument.bar_count_hourly
                existing.bar_count_minute = instrument.bar_count_minute
                existing.bar_count_5min = instrument.bar_count_5min
                existing.bar_count_30min = instrument.bar_count_30min
                self.session.flush()
                self.session.refresh(existing)
                return existing

            self.session.add(instrument)
            self.session.flush()
            self.session.refresh(instrument)
            return instrument

        except IntegrityError as e:
            raise DuplicateRecordError(
                f"Instrument {instrument.ticker} constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def get_by_ticker(self, catalog_name: str, ticker: str) -> Optional[CatalogInstrument]:
        """Find instrument by catalog name and ticker.

        Args:
            catalog_name: Catalog name.
            ticker: Instrument ticker symbol.

        Returns:
            CatalogInstrument or None if not found.
        """
        stmt = select(CatalogInstrument).where(
            and_(
                CatalogInstrument.catalog_name == catalog_name,
                CatalogInstrument.ticker == ticker,
            )
        )
        result = self.session.execute(stmt)
        return result.scalar_one_or_none()

    def list_by_catalog(
        self,
        catalog_name: str,
        asset_class: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CatalogInstrument]:
        """List instruments in a catalog with optional filtering.

        Args:
            catalog_name: Catalog name to filter by.
            asset_class: Optional asset class filter.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            List of CatalogInstrument instances.
        """
        conditions = [CatalogInstrument.catalog_name == catalog_name]
        if asset_class:
            conditions.append(CatalogInstrument.asset_class == asset_class)

        stmt = (
            select(CatalogInstrument)
            .where(and_(*conditions))
            .order_by(CatalogInstrument.ticker)
            .limit(limit)
            .offset(offset)
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def search(self, query: str, limit: int = 50) -> List[CatalogInstrument]:
        """Search instruments by partial ticker match.

        Args:
            query: Search string (matched against ticker).
            limit: Maximum results.

        Returns:
            List of matching CatalogInstrument instances.
        """
        escaped = query.replace("%", r"\%").replace("_", r"\_")
        stmt = (
            select(CatalogInstrument)
            .where(CatalogInstrument.ticker.ilike(f"%{escaped}%"))
            .order_by(CatalogInstrument.ticker)
            .limit(limit)
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())
