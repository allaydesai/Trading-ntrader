"""Unit tests for CatalogDividend ORM model structure."""

import pytest

from src.db.models.catalog_dividend import CatalogDividend


@pytest.mark.unit
class TestCatalogDividendModel:
    """Tests for CatalogDividend SQLAlchemy model definition."""

    def test_tablename(self):
        """Model maps to catalog_dividends table."""
        assert CatalogDividend.__tablename__ == "catalog_dividends"

    def test_has_all_required_columns(self):
        """Model defines all required columns."""
        columns = {c.name for c in CatalogDividend.__table__.columns}
        expected = {
            "id",
            "catalog_name",
            "ticker",
            "ex_date",
            "amount",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_primary_key_is_id(self):
        """Primary key is the id column."""
        pk_cols = [c.name for c in CatalogDividend.__table__.primary_key.columns]
        assert pk_cols == ["id"]

    def test_amount_is_numeric_not_float(self):
        """Amount column stores Numeric, never float, preserving precision."""
        amount_col = CatalogDividend.__table__.columns["amount"]
        assert amount_col.type.__class__.__name__ == "Numeric"
        assert amount_col.nullable is False

    def test_ex_date_is_date_not_null(self):
        """ex_date column is a Date and not nullable."""
        ex_date_col = CatalogDividend.__table__.columns["ex_date"]
        assert ex_date_col.type.__class__.__name__ == "Date"
        assert ex_date_col.nullable is False

    def test_index_exists(self):
        """Composite (catalog_name, ticker) index is defined."""
        index_names = {idx.name for idx in CatalogDividend.__table__.indexes}
        assert "ix_catalog_dividends_catalog_ticker" in index_names

    def test_unique_constraint_exists(self):
        """Unique constraint on (catalog_name, ticker, ex_date) is defined."""
        constraints = CatalogDividend.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns") and c.name == "uq_catalog_dividends_catalog_ticker_date"
        ]
        assert len(unique_constraints) == 1
        cols = {c.name for c in unique_constraints[0].columns}
        assert cols == {"catalog_name", "ticker", "ex_date"}

    def test_repr(self):
        """__repr__ includes ticker, catalog, and ex_date."""
        from datetime import date
        from decimal import Decimal

        div = CatalogDividend(
            catalog_name="firstrate-stock",
            ticker="AAPL",
            ex_date=date(2026, 2, 9),
            amount=Decimal("0.26"),
        )
        result = repr(div)
        assert "AAPL" in result
        assert "firstrate-stock" in result
