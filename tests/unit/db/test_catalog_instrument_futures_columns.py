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


class _RecordingOp:
    """Stands in for ``alembic.op`` and records every call."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: self.calls.append((name, args, kwargs))


def _run(direction):
    module = _migration_module()
    recorder = _RecordingOp()
    module.op = recorder
    getattr(module, direction)()
    return recorder.calls


def test_upgrade_adds_every_column_nullable_with_its_type():
    added = {
        args[1].name: args[1]
        for name, args, _ in _run("upgrade")
        if name == "add_column" and args[0] == "catalog_instruments"
    }
    assert set(added) == set(FUTURES_COLUMNS)
    for col_name, col in added.items():
        assert col.nullable, col_name
        assert isinstance(col.type, FUTURES_COLUMNS[col_name]), col_name
        model_type = CatalogInstrument.__table__.columns[col_name].type
        assert repr(col.type) == repr(model_type), col_name


def test_upgrade_adds_the_contract_kind_check():
    checks = [args for name, args, _ in _run("upgrade") if name == "create_check_constraint"]
    assert len(checks) == 1
    constraint, table, condition = checks[0]
    assert (constraint, table) == ("ck_catalog_instruments_contract_kind", "catalog_instruments")
    assert "'continuous'" in condition and "'specific'" in condition


def test_downgrade_drops_every_column_and_the_check():
    calls = _run("downgrade")
    dropped = {args[1] for name, args, _ in calls if name == "drop_column"}
    assert dropped == set(FUTURES_COLUMNS)
    (drop_check,) = [(args, kw) for name, args, kw in calls if name == "drop_constraint"]
    assert drop_check[0][0] == "ck_catalog_instruments_contract_kind"
    assert drop_check[1] == {"type_": "check"}
