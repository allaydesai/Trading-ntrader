"""The phase's third migration adds ``owner_epoch`` and the trade-key index
(Story 3.6, retrospective D1, AC #7).

Unit tier, and therefore a test of the migration **artifact** rather than of a
database: the objective gate runs without a live Postgres. Exercised for real
against a scratch schema by ``tests/integration/db/test_migration_schema.py``.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

#: The head this migration must chain off — Story 2.7's, the phase's second.
STORY_2_7_REVISION = "b7c419e2a3d8"


def _migration_text() -> str:
    matches = [
        path
        for path in sorted(_VERSIONS_DIR.glob("*.py"))
        if "owner_epoch" in (text := path.read_text(encoding="utf-8")) and "add_column" in text
    ]
    assert len(matches) == 1, f"expected exactly one migration adding owner_epoch: {matches}"
    return matches[0].read_text(encoding="utf-8")


def _all_down_revisions() -> list[str]:
    found = []
    for path in _VERSIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^down_revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if match:
            found.append(match.group(1))
    return found


def _all_revisions() -> list[str]:
    found = []
    for path in _VERSIONS_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
        if match:
            found.append(match.group(1))
    return found


def test_the_migration_adds_a_not_null_owner_epoch_with_a_zero_default():
    text = _migration_text()

    assert "op.add_column(" in text
    assert "trading_sessions" in text
    assert "owner_epoch" in text
    assert "nullable=False" in text
    assert 'server_default="0"' in text


def test_the_migration_creates_the_unique_trade_key_index():
    text = _migration_text()

    assert "op.create_index(" in text
    assert "uq_trades_session_trade_key" in text
    assert '"trades"' in text
    assert '["session_id", "trade_id", "client_order_id"]' in text
    assert "unique=True" in text


def test_the_migration_chains_off_story_2_7s_revision():
    text = _migration_text()

    match = re.search(r"^down_revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    assert match is not None, "the migration declares no down_revision"
    assert match.group(1) == STORY_2_7_REVISION


def test_downgrade_drops_the_index_then_the_column():
    text = _migration_text()

    index_pos = text.index('op.drop_index("uq_trades_session_trade_key"')
    column_pos = text.index('op.drop_column("trading_sessions", "owner_epoch")')
    assert index_pos < column_pos, "the index must be dropped before the column"


def test_the_chain_still_has_exactly_one_head():
    """Structural equivalent of ``alembic heads`` reporting a single revision."""
    revisions = _all_revisions()
    down_revisions = set(_all_down_revisions())
    heads = [revision for revision in revisions if revision not in down_revisions]

    assert len(heads) == 1, f"the migration chain has multiple heads: {sorted(heads)}"


def test_the_orm_declares_the_same_owner_epoch_column():
    """The migration and the ORM must not drift."""
    from src.db.models.trading_session import TradingSession

    column = TradingSession.__table__.columns["owner_epoch"]

    assert column.nullable is False


def test_the_orm_declares_the_same_trade_key_index():
    from src.db.models.trade import Trade

    indexes = {index.name: index for index in Trade.__table__.indexes}
    index = indexes["uq_trades_session_trade_key"]

    assert index.unique is True
    assert [col.name for col in index.columns] == ["session_id", "trade_id", "client_order_id"]
