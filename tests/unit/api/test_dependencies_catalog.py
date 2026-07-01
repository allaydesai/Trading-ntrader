"""Unit tests for the catalog-aware DataCatalog dependency factory.

These pin the behaviour that ``get_data_catalog_service`` must resolve the
selected ``catalog`` query param to ``CATALOG_BASE_PATH/<catalog>`` instead of
always reading a single fixed ``NAUTILUS_PATH`` catalog (the bug that made every
non-default catalog — e.g. the ETF catalog — render "No data" charts).
"""

from pathlib import Path

import pytest

from src.api.dependencies import get_data_catalog_service


@pytest.mark.unit
class TestGetDataCatalogService:
    """Path resolution for the DataCatalog dependency factory."""

    def test_resolves_catalog_name_under_base_path(self, monkeypatch):
        """A catalog name resolves to <CATALOG_BASE_PATH>/<catalog>."""
        monkeypatch.setenv("CATALOG_BASE_PATH", "./data/catalogs")

        service = get_data_catalog_service(catalog="firstrate-etf")

        assert service.catalog_path == Path("data/catalogs/firstrate-etf")

    def test_different_catalog_yields_different_path(self, monkeypatch):
        """The resolved path tracks the requested catalog, not a fixed one."""
        monkeypatch.setenv("CATALOG_BASE_PATH", "./data/catalogs")

        etf = get_data_catalog_service(catalog="firstrate-etf")
        stocks = get_data_catalog_service(catalog="firstrate-stocks")

        assert etf.catalog_path == Path("data/catalogs/firstrate-etf")
        assert stocks.catalog_path == Path("data/catalogs/firstrate-stocks")

    def test_empty_catalog_falls_back_to_nautilus_path(self, monkeypatch):
        """No catalog (e.g. /timeseries) preserves the NAUTILUS_PATH fallback."""
        monkeypatch.setenv("CATALOG_BASE_PATH", "./data/catalogs")
        monkeypatch.setenv("NAUTILUS_PATH", "./data/catalog")

        service = get_data_catalog_service(catalog="")

        assert service.catalog_path == Path("data/catalog")

    def test_catalog_set_but_no_base_path_falls_back(self, monkeypatch):
        """A catalog name with no configured base path degrades to the fallback."""
        monkeypatch.setenv("CATALOG_BASE_PATH", "")
        monkeypatch.setenv("NAUTILUS_PATH", "./data/catalog")

        service = get_data_catalog_service(catalog="firstrate-etf")

        assert service.catalog_path == Path("data/catalog")
