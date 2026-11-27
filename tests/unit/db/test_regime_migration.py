"""Regime calendar migration ``a7c3e91d5b20`` agrees with the model (tasks.md 1.9).

The migration copies its trigger SQL so it never changes after it ships; the model's
``after_create`` hooks build the same triggers in scratch schemas. Pinned equal here, so
the tests' schemas enforce exactly what ``alembic upgrade`` enforces.
"""

import importlib.util
from pathlib import Path

import pytest

from src.db.models import regime

pytestmark = pytest.mark.unit

_MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "alembic"
    / "versions"
    / "a7c3e91d5b20_add_regime_calendar_tables.py"
)


_REPORTS_MIGRATION = _MIGRATION.with_name("c81e5a4d9f27_add_regime_reports_table.py")


def _migration(path: Path = _MIGRATION):
    spec = importlib.util.spec_from_file_location(f"m_{path.stem[:12]}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_chain():
    module = _migration()
    assert module.revision == "a7c3e91d5b20"
    assert module.down_revision == "3f56b46c78eb"


def test_trigger_sql_matches_the_model():
    assert _migration().TRIGGER_DDL == regime.TRIGGER_DDL


def test_trigger_sql_survives_ddl_formatting():
    """``DDL`` %-formats its text and ``text()`` binds ``:name``: neither may appear."""
    for _table, statements in regime.TRIGGER_DDL:
        for statement in statements:
            assert "%" not in statement
            assert "::" not in statement


def test_reports_revision_follows_the_calendar_revision():
    module = _migration(_REPORTS_MIGRATION)
    assert module.revision == "c81e5a4d9f27"
    assert module.down_revision == "a7c3e91d5b20"


def test_reports_trigger_sql_matches_the_model():
    assert _migration(_REPORTS_MIGRATION).REPORTS_TRIGGER_DDL == regime.REPORTS_TRIGGER_DDL
    for _table, statements in regime.REPORTS_TRIGGER_DDL:
        for statement in statements:
            assert "%" not in statement and "::" not in statement
