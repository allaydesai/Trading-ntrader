"""Unit tests for CatalogInstrument ORM model structure."""

import pytest

from src.db.models.catalog_instrument import CatalogInstrument


@pytest.mark.unit
class TestCatalogInstrumentModel:
    """Tests for CatalogInstrument SQLAlchemy model definition."""

    def test_tablename(self):
        """Model maps to catalog_instruments table."""
        assert CatalogInstrument.__tablename__ == "catalog_instruments"

    def test_has_all_required_columns(self):
        """Model defines all required columns."""
        columns = {c.name for c in CatalogInstrument.__table__.columns}
        expected = {
            "id",
            "ticker",
            "nautilus_id",
            "asset_class",
            "catalog_name",
            "exchange",
            "name",
            "sector",
            "industry",
            "ipo_date",
            "country",
            "state",
            "date_range_start",
            "date_range_end",
            "bar_count_daily",
            "bar_count_hourly",
            "bar_count_minute",
            "bar_count_5min",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_country_state_columns(self):
        """country and state are nullable String columns."""
        from sqlalchemy import String

        cols = {c.name: c for c in CatalogInstrument.__table__.columns}
        for name in ("country", "state"):
            assert name in cols, f"{name} column missing"
            assert isinstance(cols[name].type, String), f"{name} should be String"
            assert cols[name].nullable, f"{name} should be nullable"

    def test_primary_key_is_id(self):
        """Primary key is the id column."""
        pk_cols = [c.name for c in CatalogInstrument.__table__.primary_key.columns]
        assert pk_cols == ["id"]

    def test_indexes_exist(self):
        """Required indexes are defined."""
        index_names = {idx.name for idx in CatalogInstrument.__table__.indexes}
        assert "ix_catalog_instruments_ticker" in index_names
        assert "ix_catalog_instruments_catalog_asset" in index_names

    def test_unique_constraint_exists(self):
        """Unique constraint on (catalog_name, ticker) is defined."""
        constraints = CatalogInstrument.__table__.constraints
        unique_constraints = [
            c
            for c in constraints
            if hasattr(c, "columns") and c.name == "uq_catalog_instruments_catalog_ticker"
        ]
        assert len(unique_constraints) == 1

    def test_repr(self):
        """__repr__ includes ticker, catalog, and asset_class."""
        instrument = CatalogInstrument(
            ticker="SPY",
            catalog_name="firstrate-etf",
            asset_class="ETF",
        )
        result = repr(instrument)
        assert "SPY" in result
        assert "firstrate-etf" in result
        assert "ETF" in result

    def test_bar_count_defaults(self):
        """Bar count columns default to 0."""
        for col in CatalogInstrument.__table__.columns:
            if col.name.startswith("bar_count_"):
                assert col.server_default is not None, f"{col.name} missing server_default"
                assert str(col.server_default.arg) == "0"
