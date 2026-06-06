"""CRUD service for catalog instrument metadata.

Provides both async (web) and sync (CLI) access to catalog_instruments,
delegating to the appropriate repository.
"""

from typing import Dict, List, Optional, Tuple

import structlog

from src.db.models.catalog_instrument import CatalogInstrument
from src.db.repositories.catalog_instrument_repository import (
    CatalogInstrumentRepository,
    SyncCatalogInstrumentRepository,
)

logger = structlog.get_logger(__name__)


class MetadataService:
    """Service for catalog instrument CRUD operations.

    Supports both async (for web API) and sync (for CLI) access patterns
    via the dual repository pattern.

    Attributes:
        _async_repo: Async repository for web operations.
        _sync_repo: Sync repository for CLI operations.
    """

    def __init__(
        self,
        async_repo: Optional[CatalogInstrumentRepository] = None,
        sync_repo: Optional[SyncCatalogInstrumentRepository] = None,
    ) -> None:
        self._async_repo = async_repo
        self._sync_repo = sync_repo

    def _require_async(self) -> CatalogInstrumentRepository:
        if self._async_repo is None:
            raise RuntimeError("MetadataService requires an async repository for async operations")
        return self._async_repo

    def _require_sync(self) -> SyncCatalogInstrumentRepository:
        if self._sync_repo is None:
            raise RuntimeError("MetadataService requires a sync repository for sync operations")
        return self._sync_repo

    # --- Async methods (web) ---

    async def get_instrument(self, catalog_name: str, ticker: str) -> Optional[CatalogInstrument]:
        """Get instrument by catalog name and ticker.

        Args:
            catalog_name: Catalog name.
            ticker: Instrument ticker.

        Returns:
            CatalogInstrument or None.
        """
        repo = self._require_async()
        return await repo.get_by_ticker(catalog_name, ticker)

    async def list_instruments(
        self,
        catalog_name: str,
        asset_class: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CatalogInstrument]:
        """List instruments in a catalog.

        Args:
            catalog_name: Catalog to list from.
            asset_class: Optional asset class filter.
            limit: Max results.
            offset: Pagination offset.

        Returns:
            List of CatalogInstrument instances.
        """
        repo = self._require_async()
        return await repo.list_by_catalog(
            catalog_name, asset_class=asset_class, limit=limit, offset=offset
        )

    async def upsert_instrument(self, instrument: CatalogInstrument) -> CatalogInstrument:
        """Insert or update an instrument.

        Args:
            instrument: CatalogInstrument to upsert.

        Returns:
            The persisted CatalogInstrument.
        """
        repo = self._require_async()
        return await repo.upsert(instrument)

    async def search_instruments(self, query: str, limit: int = 50) -> List[CatalogInstrument]:
        """Search instruments by partial ticker.

        Args:
            query: Search string.
            limit: Max results.

        Returns:
            List of matching CatalogInstrument instances.
        """
        repo = self._require_async()
        return await repo.search(query, limit=limit)

    async def list_catalog_names(self) -> List[str]:
        """Get distinct catalog names from the database.

        Returns:
            Sorted list of catalog name strings.
        """
        repo = self._require_async()
        return await repo.list_catalog_names()

    async def list_instruments_with_search(
        self,
        catalog_name: str,
        search: Optional[str] = None,
        asset_class: Optional[str] = None,
        sort_by: str = "ticker",
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[CatalogInstrument], int]:
        """List instruments with combined filters and prefix search.

        Args:
            catalog_name: Catalog to list from.
            search: Optional prefix search on ticker.
            asset_class: Optional asset class filter.
            sort_by: Sort column name.
            limit: Max results per page.
            offset: Pagination offset.

        Returns:
            Tuple of (instruments, total_count).
        """
        repo = self._require_async()
        return await repo.list_by_catalog_with_search(
            catalog_name,
            search=search,
            asset_class=asset_class,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )

    async def count_asset_classes(self, catalog_name: str) -> Dict[str, int]:
        """Count instruments per asset class in a catalog.

        Args:
            catalog_name: Catalog name.

        Returns:
            Dict mapping asset_class to count.
        """
        repo = self._require_async()
        return await repo.count_asset_classes(catalog_name)

    # --- Sync methods (CLI) ---

    def get_instrument_sync(self, catalog_name: str, ticker: str) -> Optional[CatalogInstrument]:
        """Sync: Get instrument by catalog name and ticker."""
        repo = self._require_sync()
        return repo.get_by_ticker(catalog_name, ticker)

    def list_instruments_sync(
        self,
        catalog_name: str,
        asset_class: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[CatalogInstrument]:
        """Sync: List instruments in a catalog."""
        repo = self._require_sync()
        return repo.list_by_catalog(
            catalog_name, asset_class=asset_class, limit=limit, offset=offset
        )

    def upsert_instrument_sync(self, instrument: CatalogInstrument) -> CatalogInstrument:
        """Sync: Insert or update an instrument."""
        repo = self._require_sync()
        return repo.upsert(instrument)

    def search_instruments_sync(self, query: str, limit: int = 50) -> List[CatalogInstrument]:
        """Sync: Search instruments by partial ticker."""
        repo = self._require_sync()
        return repo.search(query, limit=limit)
