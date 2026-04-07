"""Unit tests for FirstRate and Catalog settings."""

import pytest

from src.config import CatalogSettings, FirstRateSettings, Settings


@pytest.mark.unit
class TestFirstRateSettings:
    """Tests for FirstRateSettings configuration."""

    def test_default_values(self):
        """FirstRateSettings has sensible defaults."""
        settings = FirstRateSettings(_env_file=None)
        assert settings.firstrate_source_path == ""
        assert settings.firstrate_catalog_name == "firstrate-etf"

    def test_env_override(self):
        """FirstRateSettings reads from env vars."""
        settings = FirstRateSettings(
            _env_file=None,
            firstrate_source_path="/data/firstrate/csv",
        )
        assert settings.firstrate_source_path == "/data/firstrate/csv"


@pytest.mark.unit
class TestCatalogSettings:
    """Tests for CatalogSettings configuration."""

    def test_default_values(self):
        """CatalogSettings has sensible defaults."""
        settings = CatalogSettings(_env_file=None)
        assert settings.catalog_base_path == ""
        assert settings.default_catalog_name == ""

    def test_catalog_base_path_env(self):
        """CATALOG_BASE_PATH env var is mapped."""
        settings = CatalogSettings(
            _env_file=None,
            catalog_base_path="/data/catalogs",
        )
        assert settings.catalog_base_path == "/data/catalogs"

    def test_default_catalog_name(self):
        """DEFAULT_CATALOG_NAME env var is mapped."""
        settings = CatalogSettings(
            _env_file=None,
            default_catalog_name="firstrate-etf",
        )
        assert settings.default_catalog_name == "firstrate-etf"

    def test_empty_default_catalog_is_valid(self):
        """Empty default catalog name means no default selected."""
        settings = CatalogSettings(_env_file=None)
        assert settings.default_catalog_name == ""


@pytest.mark.unit
class TestSettingsIntegration:
    """Tests for Settings integration with new catalog settings."""

    def test_settings_has_firstrate(self):
        """Settings has firstrate attribute."""
        settings = Settings(_env_file=None)
        assert hasattr(settings, "firstrate")
        assert isinstance(settings.firstrate, FirstRateSettings)

    def test_settings_has_catalog(self):
        """Settings has catalog attribute."""
        settings = Settings(_env_file=None)
        assert hasattr(settings, "catalog")
        assert isinstance(settings.catalog, CatalogSettings)
