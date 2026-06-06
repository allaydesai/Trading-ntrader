"""Unit tests for CatalogStockSplit ORM model structure."""

import pytest

from src.db.models.catalog_stock_split import CatalogStockSplit


@pytest.mark.unit
class TestCatalogStockSplitModel:
    """Tests for CatalogStockSplit SQLAlchemy model definition."""

    def test_tablename(self):
        """Model maps to catalog_stock_splits table."""
        assert CatalogStockSplit.__tablename__ == "catalog_stock_splits"

    def test_has_all_required_columns(self):
        """Model defines all required columns."""
        columns = {c.name for c in CatalogStockSplit.__table__.columns}
        expected = {
            "id",
            "catalog_name",
            "ticker",
            "effective_date",
            "ratio",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_primary_key_is_id(self):
        """Primary key is the id column."""
        pk_cols = [c.name for c in CatalogStockSplit.__table__.primary_key.columns]
        assert pk_cols == ["id"]

    def test_ratio_is_numeric_not_float(self):
        """Ratio column stores Numeric, never float (reverse splits < 1)."""
        ratio_col = CatalogStockSplit.__table__.columns["ratio"]
        assert ratio_col.type.__class__.__name__ == "Numeric"
        assert ratio_col.nullable is False

    def test_effective_date_is_date_not_null(self):
        """effective_date column is a Date and not nullable."""
        col = CatalogStockSplit.__table__.columns["effective_date"]
        assert col.type.__class__.__name__ == "Date"
        assert col.nullable is False

    def test_index_exists(self):
        """Composite (catalog_name, ticker) index is defined."""
        index_names = {idx.name for idx in CatalogStockSplit.__table__.indexes}
        assert "ix_catalog_stock_splits_catalog_ticker" in index_names

    def test_unique_constraint_exists(self):
        """Unique constraint on (catalog_name, ticker, effective_date) is defined."""
        constraints = CatalogStockSplit.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns") and c.name == "uq_catalog_stock_splits_catalog_ticker_date"
        ]
        assert len(unique_constraints) == 1
        cols = {c.name for c in unique_constraints[0].columns}
        assert cols == {"catalog_name", "ticker", "effective_date"}

    def test_repr(self):
        """__repr__ includes ticker and catalog."""
        from datetime import date
        from decimal import Decimal

        split = CatalogStockSplit(
            catalog_name="firstrate-stock",
            ticker="AAPL",
            effective_date=date(2020, 8, 31),
            ratio=Decimal("4"),
        )
        result = repr(split)
        assert "AAPL" in result
        assert "firstrate-stock" in result
