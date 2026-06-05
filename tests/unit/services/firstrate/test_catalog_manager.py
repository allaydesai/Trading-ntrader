"""Unit tests for CatalogManager service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.services.firstrate.catalog_manager import CatalogManager


@pytest.mark.unit
class TestCatalogManager:
    """Tests for CatalogManager catalog resolution."""

    @pytest.mark.parametrize("bad_path", ["", "."])
    def test_empty_base_path_fails_fast(self, bad_path):
        """Review finding #7: empty CATALOG_BASE_PATH must not scan CWD."""
        with pytest.raises(ValueError, match="CATALOG_BASE_PATH is not configured"):
            CatalogManager(base_path=Path(bad_path))

    def test_resolve_catalog_returns_parquet_data_catalog(self, tmp_path):
        """resolve_catalog returns a ParquetDataCatalog instance."""
        catalog_dir = tmp_path / "test-catalog"
        catalog_dir.mkdir()

        manager = CatalogManager(base_path=tmp_path)

        with patch("src.services.firstrate.catalog_manager.ParquetDataCatalog") as mock_pdc:
            mock_instance = MagicMock()
            mock_pdc.return_value = mock_instance

            result = manager.resolve_catalog("test-catalog")

            assert result is mock_instance
            mock_pdc.assert_called_once_with(path=str(catalog_dir), fs_protocol="file")

    def test_resolve_catalog_caches_instance(self, tmp_path):
        """resolve_catalog returns the same instance on subsequent calls."""
        catalog_dir = tmp_path / "test"
        catalog_dir.mkdir()

        manager = CatalogManager(base_path=tmp_path)

        with patch("src.services.firstrate.catalog_manager.ParquetDataCatalog") as mock_pdc:
            mock_instance = MagicMock()
            mock_pdc.return_value = mock_instance

            result1 = manager.resolve_catalog("test")
            result2 = manager.resolve_catalog("test")

            assert result1 is result2
            assert mock_pdc.call_count == 1

    def test_resolve_catalog_missing_raises_file_not_found(self, tmp_path):
        """resolve_catalog raises FileNotFoundError for missing catalog."""
        manager = CatalogManager(base_path=tmp_path)

        with pytest.raises(FileNotFoundError, match="not found"):
            manager.resolve_catalog("nonexistent")

    def test_list_catalogs_discovers_subdirectories(self, tmp_path):
        """list_catalogs returns names of subdirectories."""
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "not-a-dir.txt").touch()

        manager = CatalogManager(base_path=tmp_path)

        names = manager.list_catalogs()
        assert names == ["alpha", "beta"]

    def test_list_catalogs_empty_when_no_base_path(self, tmp_path):
        """list_catalogs returns empty list when base path doesn't exist."""
        manager = CatalogManager(base_path=tmp_path / "nonexistent")

        assert manager.list_catalogs() == []
