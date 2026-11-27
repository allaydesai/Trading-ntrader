"""add regime reports table

Revision ID: c81e5a4d9f27
Revises: a7c3e91d5b20
Create Date: 2026-11-27 00:00:00

Regime attribution phase 2 (openspec change ``add-regime-attribution``, design.md section 8):
``regime_reports``, one append-only row per stored reading (a profile in phase 2), naming
the calendar versions and builds it read. The trigger refuses ``UPDATE`` and ``DELETE``,
and an insert whose ``calendar_version_ids`` is empty or names a (version, build) pair with
no ``regime_calendar_builds`` row. Its SQL is copied here, not imported, so this revision
never changes after it ships; ``tests/unit/db/test_regime_migration.py`` pins it equal to
the model's.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c81e5a4d9f27"
down_revision: Union[str, Sequence[str], None] = "a7c3e91d5b20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REPORTS_FUNCTION = """
CREATE OR REPLACE FUNCTION regime_reports_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    entry jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'regime_reports is append-only: a new reading is a new row';
    END IF;
    IF jsonb_typeof(NEW.calendar_version_ids) IS DISTINCT FROM 'array'
       OR jsonb_array_length(NEW.calendar_version_ids) = 0 THEN
        RAISE EXCEPTION 'regime_reports: calendar_version_ids must be a non-empty list';
    END IF;
    FOR entry IN SELECT jsonb_array_elements(NEW.calendar_version_ids) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM regime_calendar_builds b
             WHERE b.version_id = CAST(entry ->> 'version_id' AS bigint)
               AND b.build_no = CAST(entry ->> 'build_no' AS integer)
        ) THEN
            RAISE EXCEPTION USING MESSAGE =
                'regime_reports: unknown calendar version or build ' || entry;
        END IF;
    END LOOP;
    RETURN NEW;
END $$
"""
REPORTS_TRIGGER = """
CREATE TRIGGER regime_reports_append_only BEFORE INSERT OR UPDATE OR DELETE ON regime_reports
FOR EACH ROW EXECUTE FUNCTION regime_reports_append_only()
"""
REPORTS_TRIGGER_DDL = (("regime_reports", (REPORTS_FUNCTION, REPORTS_TRIGGER)),)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regime_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("run_ids", postgresql.JSONB(), nullable=False),
        sa.Column("calendar_version_ids", postgresql.JSONB(), nullable=False),
        sa.Column("labels_hash", sa.String(length=64), nullable=False),
        sa.Column("label_set", sa.String(length=20), nullable=True),
        sa.Column("hindsight", sa.Boolean(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("report_id", name="regime_reports_report_id_key"),
        sa.CheckConstraint(
            "kind IN ('profile', 'scorecard', 'filter_test')", name="ck_regime_reports_kind"
        ),
        sa.CheckConstraint(
            "label_set IS NULL OR label_set IN ('ground_truth', 'detector')",
            name="ck_regime_reports_label_set",
        ),
        sa.CheckConstraint(
            "label_set IS DISTINCT FROM 'ground_truth' OR hindsight",
            name="ck_regime_reports_hindsight",
        ),
    )
    for _table, statements in REPORTS_TRIGGER_DDL:
        for statement in statements:
            op.execute(statement)


REFUSE_POPULATED = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM regime_reports) THEN
        RAISE EXCEPTION 'regime_reports holds stored reports; a downgrade would delete them. '
            'Export them first and empty the table deliberately if they really must go.';
    END IF;
END $$
"""


def downgrade() -> None:
    """Downgrade schema; refused while any stored report exists (reports are never lost).

    The table is locked against inserts first, so no report can commit between the check
    and the drop. The database evaluates the check itself, so ``alembic downgrade --sql``
    (offline mode) still writes the script.
    """
    op.execute("LOCK TABLE regime_reports IN SHARE ROW EXCLUSIVE MODE")
    op.execute(REFUSE_POPULATED)
    op.drop_table("regime_reports")
    op.execute("DROP FUNCTION IF EXISTS regime_reports_append_only()")
