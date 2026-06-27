"""AC3 guard (Story 2.1): the 11th Alembic migration adds ``bar_count_30min``.

The objective gate runs without a live Postgres, so ``alembic upgrade`` is out of
band. This test asserts the migration *artifact* exists and is well-formed: it adds
``bar_count_30min`` to ``catalog_instruments`` in ``upgrade()`` and drops it cleanly
in ``downgrade()``, mirroring the ``bar_count_5min`` migration (67772db31d8d).
"""

from pathlib import Path

import pytest

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"


def _migration_text_adding_column() -> str:
    """Return the source of the migration file that adds bar_count_30min."""
    for py in _VERSIONS_DIR.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "bar_count_30min" in text and "add_column" in text:
            return text
    raise AssertionError("No migration found adding bar_count_30min")


@pytest.mark.unit
def test_migration_file_exists_adding_bar_count_30min():
    """A versions/*.py migration adds bar_count_30min via add_column."""
    text = _migration_text_adding_column()
    assert "catalog_instruments" in text
    assert "bar_count_30min" in text


@pytest.mark.unit
def test_migration_upgrade_adds_and_downgrade_drops():
    """upgrade() adds the column; downgrade() drops it (clean reversal)."""
    text = _migration_text_adding_column()
    assert "op.add_column(" in text
    assert 'op.drop_column("catalog_instruments", "bar_count_30min")' in text


@pytest.mark.unit
def test_migration_chains_off_current_head():
    """The new migration chains off the prior head, keeping a single linear head.

    The bar_count_30min migration mirrors the bar_count_5min migration's
    column-add pattern, but its down_revision is the *current* head
    (dbec2c1f25a6) so ``alembic upgrade head`` stays single-headed.
    """
    text = _migration_text_adding_column()
    assert "dbec2c1f25a6" in text, "down_revision should chain off the current head"
