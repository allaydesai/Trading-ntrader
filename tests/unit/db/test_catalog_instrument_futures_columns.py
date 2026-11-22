"""Futures M1: ``catalog_instruments`` carries nullable futures columns.

The model and migration ``3f56b46c78eb`` must agree. ``alembic upgrade``/``downgrade``
against Postgres is checked out of band (see the PR); this pins the artifacts offline.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint, Date, Numeric, String

from src.db.models.catalog_instrument import CatalogInstrument

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "3f56b46c78eb_add_futures_columns_to_catalog_.py"
)

FUTURES_COLUMNS = {
    "multiplier": Numeric,
    "tick_size": Numeric,
    "currency": String,
    "contract_kind": String,
    "expiry": Date,
    "roll_rule": String,
}


@pytest.mark.parametrize("name", sorted(FUTURES_COLUMNS))
def test_model_has_nullable_futures_column(name):
    column = CatalogInstrument.__table__.columns[name]
    assert column.nullable
    assert isinstance(column.type, FUTURES_COLUMNS[name])
    assert column.server_default is None


def test_model_constrains_contract_kind():
    checks = [c for c in CatalogInstrument.__table__.constraints if isinstance(c, CheckConstraint)]
    names = {c.name for c in checks}
    assert "ck_catalog_instruments_contract_kind" in names
    (check,) = [c for c in checks if c.name == "ck_catalog_instruments_contract_kind"]
    assert "'continuous'" in str(check.sqltext) and "'specific'" in str(check.sqltext)


def _migration_module():
    spec = importlib.util.spec_from_file_location("m_3f56b46c78eb", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_chains_off_previous_head():
    module = _migration_module()
    assert module.revision == "3f56b46c78eb"
    assert module.down_revision == "dc41856246ad"


def test_migration_adds_and_drops_every_column():
    text = _MIGRATION.read_text(encoding="utf-8")
    for name in FUTURES_COLUMNS:
        assert f'"{name}"' in text
    assert "op.drop_column(_TABLE, column)" in text
    assert "ck_catalog_instruments_contract_kind" in text
    assert 'type_="check"' in text
