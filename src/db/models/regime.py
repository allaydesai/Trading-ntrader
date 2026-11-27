"""Regime calendar: versions, per-session labels and the build history (design.md section 1).

A **version** is one (reference, catalog, label set) definition with its hash and input
revision. Its **labels** are one row per reference session. Every build that appends rows
or fills ground-truth values is a **build** row. All three are append-only, enforced in
the database by the triggers below (the migration ``a7c3e91d5b20`` creates the same ones;
``after_create`` hooks create them wherever ``Base.metadata.create_all`` builds a scratch
schema, so every test sees the real rules):

- ``regime_labels_append_only``: a detector row never changes; a ground-truth row may
  only turn a ``null`` value into a value, recording the version's current build in
  ``set_in_build``. ``DELETE`` raises.
- ``regime_versions_immutable``: only ``data_end`` (forward), ``build_no`` (+1) and
  ``updated_at`` may change. ``DELETE`` raises.
- ``regime_calendar_builds_append_only``: no ``UPDATE`` or ``DELETE``.

The SQL avoids ``%`` (``DDL`` formats it) and ``::`` casts, so it runs unchanged
through ``DDL``, ``op.execute`` and every driver the tests use.
"""

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin

LABEL_SETS = ("ground_truth", "detector")
#: Why a same-definition version got a new input revision.
REVISION_REASONS = ("reference_bars_changed", "median_recut")

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
#: (table, statements) in creation order; each statement is executed on its own.
TRIGGER_DDL: tuple[tuple[str, tuple[str, str]], ...] = (
    ("regime_calendar_versions", (VERSIONS_FUNCTION, VERSIONS_TRIGGER)),
    ("regime_labels", (LABELS_FUNCTION, LABELS_TRIGGER)),
    ("regime_calendar_builds", (BUILDS_FUNCTION, BUILDS_TRIGGER)),
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _version_fk() -> Mapped[int]:
    return mapped_column(
        BigInteger,
        ForeignKey("regime_calendar_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class RegimeCalendarVersion(Base, TimestampMixin):
    """One calendar definition for one reference in one catalog."""

    __tablename__ = "regime_calendar_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reference: Mapped[str] = mapped_column(String(20), nullable=False)
    catalog: Mapped[str] = mapped_column(String(100), nullable=False)
    label_set: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    frozen_stats: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    build_no: Mapped[int] = mapped_column(Integer, nullable=False)
    data_start: Mapped[date] = mapped_column(Date, nullable=False)
    data_end: Mapped[date] = mapped_column(Date, nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("reference", "catalog", "label_set", "version", name="uq_regime_version"),
        UniqueConstraint(
            "reference",
            "catalog",
            "label_set",
            "definition_hash",
            "input_revision",
            name="uq_regime_version_identity",
        ),
        CheckConstraint(_in("label_set", LABEL_SETS), name="ck_regime_versions_label_set"),
        CheckConstraint(
            "(input_revision = 1) = (revision_reason IS NULL)",
            name="ck_regime_versions_revision_reason",
        ),
        CheckConstraint(
            "version >= 1 AND input_revision >= 1 AND build_no >= 1",
            name="ck_regime_versions_positive",
        ),  # fmt: skip
        CheckConstraint("data_end >= data_start", name="ck_regime_versions_range"),
    )


class RegimeLabel(Base):
    """One reference session's labels in one version."""

    __tablename__ = "regime_labels"

    version_id: Mapped[int] = _version_fk()
    session_date: Mapped[date] = mapped_column(Date, primary_key=True)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    inserted_in_build: Mapped[int] = mapped_column(Integer, nullable=False)
    set_in_build: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    bar_hash: Mapped[str] = mapped_column(String(32), nullable=False)


class RegimeCalendarBuild(Base):
    """One build of a version: the history behind its ``build_no`` and ``data_end``."""

    __tablename__ = "regime_calendar_builds"

    version_id: Mapped[int] = _version_fk()
    build_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_end: Mapped[date] = mapped_column(Date, nullable=False)
    git_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    built_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )


for _table_name, _statements in TRIGGER_DDL:
    for _statement in _statements:
        event.listen(Base.metadata.tables[_table_name], "after_create", DDL(_statement))
