"""The second Phase-3 migration adds ``trading_sessions.runtime_flags`` (Story 2.7, AC #4).

Unit tier, and therefore a test of the migration **artifact** rather than of a
database: the objective gate runs without a live Postgres, so ``alembic
upgrade`` is out of band. It is exercised for real in Task 5's
``downgrade -1 -> upgrade`` round trip against the live database, recorded in
the story's Debug Log, and by ``tests/integration/db/test_migration_schema.py``.

⚠️ **This is the phase's second migration, and that is a deliberate departure.**
``epics.md:367-371`` gave Story 2.2 the phase's *single* migration, and
``deferred-work.md:851-861`` re-pointed the column question at Story 2.8.
Sanctioned by Allay, 2026-08-23, for a reason recorded in full under the story's
AC #4: containment **without** persistence is a regression, not a neutral
omission. Today ``os._exit(1)`` leaves the row ``running`` with a frozen
heartbeat, which Story 2.8 renders ``stale`` — crude, but visible across
processes. After containment the session heartbeats normally and ``note_bar``
keeps advancing ``last_bar_at``, so 2.8 would render ``trading`` for a session
where every strategy is dead.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

#: The head this migration must chain off — Story 2.2's, the phase's first.
STORY_2_2_REVISION = "d08dfbd393f0"


def _migration_text() -> str:
    # Sorted, so a second migration ever touching the column makes the pick
    # deterministic rather than filesystem-order-dependent — and loud, because
    # exactly one migration may *add* it (review fix, 2026-08-23).
    matches = [
        path
        for path in sorted(_VERSIONS_DIR.glob("*.py"))
        if "runtime_flags" in (text := path.read_text(encoding="utf-8")) and "add_column" in text
    ]
    assert len(matches) == 1, f"expected exactly one migration adding runtime_flags: {matches}"
    return matches[0].read_text(encoding="utf-8")


def _all_down_revisions() -> list[str]:
    # The quoted value only — the raw RHS would carry quotes and `None` into
    # the head computation (review fix, 2026-08-23).
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


def test_the_migration_adds_one_nullable_jsonb_column_to_trading_sessions():
    text = _migration_text()

    assert "op.add_column(" in text
    assert "trading_sessions" in text
    assert "JSONB" in text or "postgresql.JSONB" in text
    assert "nullable=True" in text


def test_the_migration_chains_off_story_2_2s_revision():
    """``d08dfbd393f0`` was the single head before this story."""
    text = _migration_text()

    match = re.search(r"^down_revision[^=]*=\s*[\"']([^\"']+)[\"']", text, re.MULTILINE)
    assert match is not None, "the migration declares no down_revision"
    assert match.group(1) == STORY_2_2_REVISION


def test_downgrade_drops_the_column_cleanly():
    text = _migration_text()

    assert 'op.drop_column("trading_sessions", "runtime_flags")' in text


def test_the_chain_still_has_exactly_one_head():
    """Structural equivalent of ``alembic heads`` reporting a single revision.

    A head is a revision no other migration names as its ``down_revision``.
    Asserting on the *count* rather than on a specific id means a future
    migration chained onto this one keeps the test meaningful instead of
    needing an edit. Set membership, not substring-in-joined-string (review
    fix, 2026-08-23): the old form wrongly excluded any revision id that
    happened to be a substring of another.
    """
    revisions = _all_revisions()
    down_revisions = set(_all_down_revisions())
    heads = [revision for revision in revisions if revision not in down_revisions]

    assert len(heads) == 1, f"the migration chain has multiple heads: {sorted(heads)}"


def test_the_orm_model_declares_the_same_column():
    """The migration and the ORM must not drift; Story 2.2's did not."""
    from sqlalchemy.dialects.postgresql import JSONB

    from src.db.models.trading_session import TradingSession

    column = TradingSession.__table__.columns["runtime_flags"]

    assert column.nullable is True
    assert isinstance(column.type, JSONB)


def test_nothing_in_the_api_or_templates_reads_the_table():
    """AR44's zero-UI-change criterion, re-verified rather than assumed.

    The column is free to exist only because no rendering path touches
    ``trading_sessions`` at all. If that stops being true, this test is where a
    future story finds out.
    """
    project_root = Path(__file__).resolve().parents[3]
    hits = []
    for directory in ("src/api", "templates"):
        root = project_root / directory
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".html"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "trading_session" in text or "TradingSession" in text:
                hits.append(str(path.relative_to(project_root)))

    assert hits == []
