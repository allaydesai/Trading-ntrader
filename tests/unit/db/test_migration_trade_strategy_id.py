"""MCP phase 5b's first migration adds ``trades.strategy_id`` — which strategy
owned a trade, so a run holding more than one strategy can be attributed per
sleeve.

Unit tier, and therefore a test of the migration **artifact** rather than of a
database (the ``test_migration_owner_epoch.py`` precedent).
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

#: The head this migration must chain off — MCP phase 5a's portfolio analysis kind.
PHASE_5A_REVISION = "72a945ca06f5"


def _migration_text() -> str:
    matches = [
        path
        for path in sorted(_VERSIONS_DIR.glob("*.py"))
        if "strategy_id" in (text := path.read_text(encoding="utf-8"))
        and "add_column" in text
        and '"trades"' in text
    ]
    assert len(matches) == 1, f"expected exactly one migration adding trades.strategy_id: {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_the_migration_adds_a_nullable_strategy_id_to_trades():
    """Nullable: rows written before it have no recorded owner, and none is invented."""
    text = _migration_text()

    assert "op.add_column(" in text
    assert "sa.String(length=100)" in text
    assert "nullable=True" in text


def test_the_migration_chains_off_phase_5as_revision():
    text = _migration_text()

    match = re.search(r"^down_revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    assert match is not None, "the migration declares no down_revision"
    assert match.group(1) == PHASE_5A_REVISION


def test_downgrade_drops_the_column():
    assert 'op.drop_column("trades", "strategy_id")' in _migration_text()


def test_the_orm_declares_the_same_column():
    """The migration and the ORM must not drift."""
    from sqlalchemy import String

    from src.db.models.trade import Trade

    column = Trade.__table__.columns["strategy_id"]

    assert column.nullable is True
    assert isinstance(column.type, String)
    assert column.type.length == 100
