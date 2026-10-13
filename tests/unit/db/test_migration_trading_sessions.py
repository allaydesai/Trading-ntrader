"""AC #1-#4 guard (Story 2.2): the trading_sessions migration artifact.

The objective gate runs without a live Postgres, so ``alembic upgrade`` is out of
band. This test asserts the migration *artifact* exists and is well-formed,
following the static source-text pattern in ``test_migration_bar_count_30min.py``
verbatim. This tier is deliberate: ``.github/workflows/ci.yml:168,238`` passes
``--ignore=tests/integration/db``, so a test placed in ``tests/integration/db/``
gates nothing on a PR.
"""

from pathlib import Path

import pytest

_VERSIONS_DIR = Path(__file__).resolve().parents[3] / "alembic" / "versions"

_ENUM_LABELS = ("created", "running", "stopped", "sealed")

_TRADING_SESSIONS_COLUMNS = (
    '"id"',
    '"session_id"',
    '"name"',
    '"status"',
    '"spec"',
    '"linked_backtest_run_id"',
    '"sealed_run_id"',
    '"last_started_at"',
    '"last_stopped_at"',
    '"sealed_at"',
    '"last_heartbeat_at"',
    '"last_bar_at"',
    '"created_at"',
)


def _migration_text() -> str:
    """Return the source of the migration file that adds trading_sessions."""
    for py in _VERSIONS_DIR.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        if "trading_sessions" in text and "op.create_table" in text:
            return text
    raise AssertionError("No migration found adding trading_sessions")


@pytest.mark.unit
def test_migration_file_exists_adding_trading_sessions():
    """A versions/*.py migration creates the trading_sessions table."""
    text = _migration_text()
    assert "op.create_table(" in text
    assert '"trading_sessions"' in text


@pytest.mark.unit
def test_migration_chains_off_current_head():
    """down_revision is the single current head, a436f35f525c."""
    text = _migration_text()
    assert 'down_revision: Union[str, Sequence[str], None] = "a436f35f525c"' in text


@pytest.mark.unit
def test_upgrade_creates_every_ac1_column():
    """upgrade() creates trading_sessions with every column AC #1 names."""
    text = _migration_text()
    for column in _TRADING_SESSIONS_COLUMNS:
        assert column in text, f"missing column {column} in trading_sessions"


@pytest.mark.unit
def test_enum_labels_are_lowercase_in_migration_source():
    """AC #2's DB guard: the enum labels are lowercase in the migration source."""
    text = _migration_text()
    for label in _ENUM_LABELS:
        assert f'"{label}"' in text
    # Guard against an UPPERCASE regression slipping through the lowercase check.
    assert "CREATED" not in text
    assert "RUNNING" not in text
    assert "STOPPED" not in text
    assert "SEALED" not in text


def _upgrade_body() -> str:
    """Only the upgrade() half, so a downgrade statement cannot satisfy an upgrade assertion."""
    text = _migration_text()
    return text.split("def downgrade() -> None:")[0]


@pytest.mark.unit
def test_upgrade_widens_backtest_runs_and_trades():
    """AC #3: run_type, trades.session_id, nullable backtest_run_id, and the CHECK.

    Asserts on whole statements, not on loose substrings. ``"nullable=True" in
    text`` was satisfied by any of the nine unrelated nullable columns in
    ``create_table``, so inverting the one ``alter_column`` that AC #3 turns on
    left this test green — and it is the only CI-visible guard for the
    migration (``ci.yml`` ``--ignore``s ``tests/integration/db``).
    """
    upgrade = _upgrade_body()
    assert 'op.add_column(\n        "backtest_runs",' in upgrade
    assert 'sa.Column("run_type", sa.String(length=20), nullable=False, ' in upgrade
    assert 'op.add_column("trades", sa.Column("session_id", sa.BigInteger(), nullable=True))' in (
        upgrade
    )
    assert (
        'op.alter_column("trades", "backtest_run_id", existing_type=sa.BigInteger(), nullable=True)'
        in upgrade
    ), "AC #3's core clause: trades.backtest_run_id must be widened to nullable in upgrade()"
    assert "chk_trades_owner" in upgrade
    assert "backtest_run_id IS NOT NULL OR session_id IS NOT NULL" in upgrade


@pytest.mark.unit
def test_downgrade_drops_table_altered_columns_and_enum():
    """AC #4: downgrade() reverses the table, the two alters, and the enum type."""
    text = _migration_text()
    assert "def downgrade() -> None:" in text
    downgrade_body = text.split("def downgrade() -> None:")[1]
    assert 'op.drop_table("trading_sessions")' in downgrade_body
    assert 'op.drop_column("backtest_runs", "run_type")' in downgrade_body
    assert 'op.drop_column("trades", "session_id")' in downgrade_body
    assert "session_status_enum.drop(" in downgrade_body
    # AC #4 names this clause literally — "restoring trades.backtest_run_id to
    # NOT NULL". Nothing else in the suite covers it, so deleting this one line
    # from the migration used to leave the whole suite green.
    assert (
        'op.alter_column("trades", "backtest_run_id", existing_type=sa.BigInteger(), '
        "nullable=False)" in downgrade_body
    ), "AC #4: downgrade() must restore trades.backtest_run_id to NOT NULL"
    assert 'op.drop_constraint("chk_trades_owner", "trades", type_="check")' in downgrade_body


@pytest.mark.unit
def test_downgrade_guards_against_orphaned_paper_trades():
    """AC #4: downgrade refuses when paper trades exist, naming them."""
    text = _migration_text()
    downgrade_body = text.split("def downgrade() -> None:")[1]
    assert "backtest_run_id IS NULL" in downgrade_body
    assert "raise RuntimeError(" in downgrade_body


@pytest.mark.unit
def test_downgrade_guard_runs_before_any_ddl():
    """The guard must run first, before any destructive DDL call.

    ``op.get_bind()`` is how the guard itself reads the orphan count, so a
    naive search for the first ``"op."`` substring would find that call
    instead — search for the first destructive DDL op specifically.
    """
    text = _migration_text()
    downgrade_body = text.split("def downgrade() -> None:")[1]
    guard_pos = downgrade_body.index("raise RuntimeError(")
    first_ddl_pos = downgrade_body.index("op.drop_constraint(")
    assert guard_pos < first_ddl_pos, "the guard must run before any DDL in downgrade()"


@pytest.mark.unit
def test_migration_never_autogenerated_repairs():
    """Do not 'repair' the pre-existing ORM/migration index drift in this migration."""
    text = _migration_text()
    assert "idx_backtest_runs_instrument" not in text
