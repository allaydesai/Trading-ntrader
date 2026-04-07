"""Named catalog resolution service for FirstRate data.

Provides lazy-initialized ParquetDataCatalog instances for named catalogs,
discovered from a base directory on the filesystem.
"""

from pathlib import Path

import structlog
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

logger = structlog.get_logger(__name__)


class CatalogManager:
    """Resolves named catalogs to ParquetDataCatalog instances.

    Catalogs are discovered from subdirectories under a base path.
    Uses lazy initialization — catalogs are only created when first
    requested and cached for subsequent access.

    Attributes:
        _base_path: Base directory containing catalog subdirectories.
        _catalogs: Cache of initialized ParquetDataCatalog instances.
    """

    def __init__(self, base_path: Path) -> None:
        """Initialize with base catalog directory.

        Args:
            base_path: Directory containing catalog subdirectories.
        """
        self._base_path = base_path
        self._catalogs: dict[str, ParquetDataCatalog] = {}

    def resolve_catalog(self, name: str) -> ParquetDataCatalog:
        """Resolve a named catalog to a ParquetDataCatalog instance.

        Args:
            name: Catalog name (subdirectory under base_path).

        Returns:
            ParquetDataCatalog initialized at the catalog path.

        Raises:
            FileNotFoundError: If the catalog directory does not exist.
        """
        if name in self._catalogs:
            return self._catalogs[name]

        catalog_path = self._base_path / name
        if not catalog_path.exists():
            available = self.list_catalogs()
            raise FileNotFoundError(
                f"Catalog '{name}' not found at {catalog_path}. Available: {available}"
            )

        logger.info("initializing_catalog", name=name, path=str(catalog_path))
        catalog = ParquetDataCatalog(path=str(catalog_path), fs_protocol="file")
        self._catalogs[name] = catalog
        return catalog

    def list_catalogs(self) -> list[str]:
        """Discover available catalogs from the base directory.

        Returns:
            List of catalog name strings (subdirectory names).
        """
        if not self._base_path.exists():
            return []
        return sorted(d.name for d in self._base_path.iterdir() if d.is_dir())
