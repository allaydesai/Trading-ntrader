"""add regime calendar tables

Revision ID: a7c3e91d5b20
Revises: 3f56b46c78eb
Create Date: 2026-11-26 00:00:00

Regime attribution phase 1 (openspec change ``add-regime-attribution``, design.md section 1):
``regime_calendar_versions`` (one definition per reference, catalog and label set),
``regime_labels`` (one row per version and reference session) and the append-only
``regime_calendar_builds`` history, with the triggers that make all three append-only.
The trigger SQL is copied here, not imported, so this revision never changes after it
ships; ``tests/unit/db/test_regime_migration.py`` pins it equal to the model's.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e91d5b20"
down_revision: Union[str, Sequence[str], None] = "3f56b46c78eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LABELS_FUNCTION = """
CREATE OR REPLACE FUNCTION regime_labels_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_label_set text;
    v_build integer;
    k text;
    old_v jsonb;
    new_v jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'regime_labels is append-only: DELETE refused';
    END IF;
    SELECT label_set, build_no INTO v_label_set, v_build
      FROM regime_calendar_versions WHERE id = OLD.version_id;
    IF v_label_set = 'detector' THEN
        RAISE EXCEPTION 'regime_labels: a detector row is immutable';
    END IF;
    IF NEW.version_id IS DISTINCT FROM OLD.version_id
       OR NEW.session_date IS DISTINCT FROM OLD.session_date
       OR NEW.bar_hash IS DISTINCT FROM OLD.bar_hash
       OR NEW.inserted_in_build IS DISTINCT FROM OLD.inserted_in_build THEN
        RAISE EXCEPTION 'regime_labels: key, bar_hash and inserted_in_build never change';
    END IF;
    IF (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(NEW.labels) AS x)
         IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(OLD.labels) AS x)
       OR (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(NEW.set_in_build) AS x)
         IS DISTINCT FROM (SELECT array_agg(x ORDER BY x) FROM jsonb_object_keys(OLD.labels) AS x)
    THEN
        RAISE EXCEPTION 'regime_labels: label keys never change';
    END IF;
    FOR k IN SELECT jsonb_object_keys(OLD.labels) LOOP
        old_v := OLD.labels -> k;
        new_v := NEW.labels -> k;
        IF old_v <> jsonb 'null' THEN
            IF new_v IS DISTINCT FROM old_v
               OR (NEW.set_in_build -> k) IS DISTINCT FROM (OLD.set_in_build -> k) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'regime_labels: ground-truth value ' || k || ' is already set';
            END IF;
        ELSIF new_v <> jsonb 'null' THEN
            IF (NEW.set_in_build -> k) IS DISTINCT FROM to_jsonb(v_build) THEN
                RAISE EXCEPTION USING MESSAGE =
                    'regime_labels: filling ' || k || ' must record the current build';
            END IF;
        ELSIF (NEW.set_in_build -> k) IS DISTINCT FROM (OLD.set_in_build -> k) THEN
            RAISE EXCEPTION USING MESSAGE =
                'regime_labels: set_in_build of unset ' || k || ' never changes';
        END IF;
    END LOOP;
    RETURN NEW;
END $$
"""
LABELS_TRIGGER = """
CREATE TRIGGER regime_labels_append_only BEFORE UPDATE OR DELETE ON regime_labels
FOR EACH ROW EXECUTE FUNCTION regime_labels_append_only()
"""
VERSIONS_FUNCTION = """
CREATE OR REPLACE FUNCTION regime_versions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'regime_calendar_versions rows are never deleted';
    END IF;
    IF (NEW.id, NEW.reference, NEW.catalog, NEW.label_set, NEW.version, NEW.definition,
        NEW.definition_hash, NEW.input_revision, NEW.revision_reason, NEW.frozen_stats,
        NEW.data_start, NEW.git_commit, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.reference, OLD.catalog, OLD.label_set, OLD.version, OLD.definition,
        OLD.definition_hash, OLD.input_revision, OLD.revision_reason, OLD.frozen_stats,
        OLD.data_start, OLD.git_commit, OLD.created_at) THEN
        RAISE EXCEPTION 'regime_calendar_versions: only data_end and build_no may change';
    END IF;
    IF NEW.data_end < OLD.data_end THEN
        RAISE EXCEPTION 'regime_calendar_versions: data_end only moves forward';
    END IF;
    IF NEW.build_no IS DISTINCT FROM OLD.build_no + 1 THEN
        RAISE EXCEPTION 'regime_calendar_versions: build_no increments by one';
    END IF;
    RETURN NEW;
END $$
"""
VERSIONS_TRIGGER = """
CREATE TRIGGER regime_versions_immutable BEFORE UPDATE OR DELETE ON regime_calendar_versions
FOR EACH ROW EXECUTE FUNCTION regime_versions_immutable()
"""
BUILDS_FUNCTION = """
CREATE OR REPLACE FUNCTION regime_calendar_builds_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'regime_calendar_builds is append-only';
END $$
"""
BUILDS_TRIGGER = """
CREATE TRIGGER regime_calendar_builds_append_only BEFORE UPDATE OR DELETE
ON regime_calendar_builds
FOR EACH ROW EXECUTE FUNCTION regime_calendar_builds_append_only()
"""
TRIGGER_DDL = (
    ("regime_calendar_versions", (VERSIONS_FUNCTION, VERSIONS_TRIGGER)),
    ("regime_labels", (LABELS_FUNCTION, LABELS_TRIGGER)),
    ("regime_calendar_builds", (BUILDS_FUNCTION, BUILDS_TRIGGER)),
)
_FUNCTIONS = (
    "regime_calendar_builds_append_only",
    "regime_labels_append_only",
    "regime_versions_immutable",
)


def _version_fk() -> sa.Column:
    return sa.Column(
        "version_id",
        sa.BigInteger(),
        sa.ForeignKey("regime_calendar_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "regime_calendar_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("reference", sa.String(length=20), nullable=False),
        sa.Column("catalog", sa.String(length=100), nullable=False),
        sa.Column("label_set", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("revision_reason", sa.Text(), nullable=True),
        sa.Column("frozen_stats", postgresql.JSONB(), nullable=False),
        sa.Column("build_no", sa.Integer(), nullable=False),
        sa.Column("data_start", sa.Date(), nullable=False),
        sa.Column("data_end", sa.Date(), nullable=False),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "reference", "catalog", "label_set", "version", name="uq_regime_version"
        ),
        sa.UniqueConstraint(
            "reference",
            "catalog",
            "label_set",
            "definition_hash",
            "input_revision",
            name="uq_regime_version_identity",
        ),
        sa.CheckConstraint(
            "label_set IN ('ground_truth', 'detector')", name="ck_regime_versions_label_set"
        ),
        sa.CheckConstraint(
            "(input_revision = 1) = (revision_reason IS NULL)",
            name="ck_regime_versions_revision_reason",
        ),
        sa.CheckConstraint(
            "version >= 1 AND input_revision >= 1 AND build_no >= 1",
            name="ck_regime_versions_positive",
        ),
        sa.CheckConstraint("data_end >= data_start", name="ck_regime_versions_range"),
    )
    op.create_table(
        "regime_labels",
        _version_fk(),
        sa.Column("session_date", sa.Date(), primary_key=True),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.Column("inserted_in_build", sa.Integer(), nullable=False),
        sa.Column("set_in_build", postgresql.JSONB(), nullable=False),
        sa.Column("bar_hash", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "regime_calendar_builds",
        _version_fk(),
        sa.Column("build_no", sa.Integer(), primary_key=True),
        sa.Column("data_end", sa.Date(), nullable=False),
        sa.Column("git_commit", sa.String(length=40), nullable=True),
        sa.Column(
            "built_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for _table, statements in TRIGGER_DDL:
        for statement in statements:
            op.execute(statement)


def downgrade() -> None:
    """Downgrade schema (drops every regime label: only for a dev database)."""
    op.drop_table("regime_calendar_builds")
    op.drop_table("regime_labels")
    op.drop_table("regime_calendar_versions")
    for name in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
