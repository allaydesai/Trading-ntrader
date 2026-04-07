"""Unit tests for catalog domain models."""

import pytest

from src.models.catalog import (
    AssetClass,
    CatalogConfig,
    ImportResult,
    ValidationResult,
)


@pytest.mark.unit
class TestAssetClass:
    """Tests for AssetClass enum."""

    def test_has_all_required_values(self):
        """AssetClass enum has all 7 required values."""
        expected = {"ETF", "STOCK", "FUTURES", "FX", "CRYPTO", "INDEX", "DELISTED"}
        actual = {member.value for member in AssetClass}
        assert actual == expected

    def test_enum_count(self):
        """AssetClass enum has exactly 7 members."""
        assert len(AssetClass) == 7

    def test_string_values(self):
        """AssetClass members have uppercase string values."""
        assert AssetClass.ETF.value == "ETF"
        assert AssetClass.STOCK.value == "STOCK"
        assert AssetClass.FUTURES.value == "FUTURES"
        assert AssetClass.FX.value == "FX"
        assert AssetClass.CRYPTO.value == "CRYPTO"
        assert AssetClass.INDEX.value == "INDEX"
        assert AssetClass.DELISTED.value == "DELISTED"


@pytest.mark.unit
class TestCatalogConfig:
    """Tests for CatalogConfig Pydantic model."""

    def test_create_with_required_fields(self):
        """CatalogConfig can be created with required fields."""
        config = CatalogConfig(name="firstrate-etf", path="/data/catalogs/etf")
        assert config.name == "firstrate-etf"
        assert config.path == "/data/catalogs/etf"
        assert config.format == "parquet"

    def test_create_with_all_fields(self):
        """CatalogConfig can be created with all fields."""
        config = CatalogConfig(
            name="firstrate-etf",
            path="/data/catalogs/etf",
            format="parquet",
        )
        assert config.name == "firstrate-etf"
        assert config.path == "/data/catalogs/etf"
        assert config.format == "parquet"

    def test_default_format(self):
        """CatalogConfig defaults format to 'parquet'."""
        config = CatalogConfig(name="test", path="/tmp")
        assert config.format == "parquet"


@pytest.mark.unit
class TestValidationResult:
    """Tests for ValidationResult Pydantic model."""

    def test_create_valid_result(self):
        """ValidationResult can represent a valid result."""
        result = ValidationResult(
            valid=True,
            errors=[],
            row_count=1000,
            invalid_rows=0,
        )
        assert result.valid is True
        assert result.errors == []
        assert result.row_count == 1000
        assert result.invalid_rows == 0

    def test_create_invalid_result(self):
        """ValidationResult can represent an invalid result."""
        result = ValidationResult(
            valid=False,
            errors=["Missing column: close", "Invalid date format in row 5"],
            row_count=100,
            invalid_rows=3,
        )
        assert result.valid is False
        assert len(result.errors) == 2
        assert result.invalid_rows == 3

    def test_errors_default_empty(self):
        """ValidationResult defaults errors to empty list."""
        result = ValidationResult(valid=True, row_count=100)
        assert result.errors == []
        assert result.invalid_rows == 0


@pytest.mark.unit
class TestImportResult:
    """Tests for ImportResult Pydantic model."""

    def test_create_success_result(self):
        """ImportResult can represent a successful import."""
        result = ImportResult(
            ticker="SPY",
            status="success",
            row_count=5000,
            duration=1.5,
        )
        assert result.ticker == "SPY"
        assert result.status == "success"
        assert result.row_count == 5000
        assert result.error is None
        assert result.duration == 1.5

    def test_create_failed_result(self):
        """ImportResult can represent a failed import."""
        result = ImportResult(
            ticker="INVALID",
            status="failed",
            row_count=0,
            error="File not found",
            duration=0.1,
        )
        assert result.ticker == "INVALID"
        assert result.status == "failed"
        assert result.error == "File not found"

    def test_error_default_none(self):
        """ImportResult defaults error to None."""
        result = ImportResult(
            ticker="SPY",
            status="success",
            row_count=100,
            duration=0.5,
        )
        assert result.error is None
