"""Research studies: the record behind the research MCP's honest-research loop.

A **study** is one research question about one idea: its strategy, instruments,
parameter space, a data split whose out-of-sample window is locked when the study
is created, the gates it must pass and a trial budget. Every job the MCP runs for
a study is a **trial** in its ledger; a **candidate** is a parameter set frozen
from exploration, and never changes afterwards; **events** are the append-only
record of every decision (budget extensions, overrides, status changes).

Runs are linked by ``run_id`` (the business UUID), like
``backtest_runs.reproduced_from_run_id``: a trial exists before its run does.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin

#: Study statuses. ``exploring`` → ``frozen`` → ``tested`` → ``promoted`` forward;
#: ``rejected`` and ``parked`` are the recorded ways out.
STUDY_STATUSES = ("exploring", "frozen", "tested", "promoted", "rejected", "parked")
#: What a trial was for. Only ``in_sample`` counts against the budget.
TRIAL_ROLES = ("in_sample", "benchmark", "out_of_sample", "reproduction")
#: ``pending`` until its job ends; then ``completed`` (run saved) or ``void``.
TRIAL_STATES = ("pending", "completed", "void")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _study_fk() -> Mapped[int]:
    return mapped_column(
        BigInteger, ForeignKey("research_studies.id", ondelete="CASCADE"), nullable=False
    )


class ResearchStudy(Base, TimestampMixin):
    """One research question, its locked holdout, its gates and its trial budget."""

    __tablename__ = "research_studies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), unique=True, nullable=False, default=uuid4, index=True
    )
    slug: Mapped[str] = mapped_column(String(81), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    symbols: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    catalog: Mapped[str] = mapped_column(String(100), nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    param_space: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_start: Mapped[date] = mapped_column(Date, nullable=False)
    is_end: Mapped[date] = mapped_column(Date, nullable=False)
    oos_start: Mapped[date] = mapped_column(Date, nullable=False)
    oos_end: Mapped[date] = mapped_column(Date, nullable=False)
    pass_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    trial_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="exploring")
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contaminated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contamination_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("is_start <= is_end", name="chk_study_in_sample"),
        CheckConstraint("oos_start <= oos_end", name="chk_study_out_of_sample"),
        CheckConstraint("is_end < oos_start", name="chk_study_holdout_after"),
        CheckConstraint("trial_budget > 0", name="chk_study_budget"),
        CheckConstraint(_in("status", STUDY_STATUSES), name="chk_study_status"),
        Index("idx_research_studies_status", "status"),
    )


class ResearchCandidate(Base):
    """A parameter set frozen from exploration. Immutable: never updated or deleted."""

    __tablename__ = "research_candidates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), unique=True, nullable=False, default=uuid4
    )
    study_pk: Mapped[int] = _study_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    catalog: Mapped[str] = mapped_column(String(100), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    fill_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    git_commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    git_dirty: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    strategies_commit: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Why beating buy-and-hold on whichever metric it beats is worth it: G1 needs it.
    benchmark_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    frozen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("study_pk", "version", name="uq_candidate_version"),)


class ResearchTrial(Base, TimestampMixin):
    """One job in a study's ledger, recorded when it is submitted."""

    __tablename__ = "research_trials"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_pk: Mapped[int] = _study_fk()
    candidate_pk: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("research_candidates.id", ondelete="CASCADE"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    job_id: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    run_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    start: Mapped[date] = mapped_column(Date, nullable=False)
    end: Mapped[date] = mapped_column(Date, nullable=False)
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("role", TRIAL_ROLES), name="chk_trial_role"),
        CheckConstraint(_in("state", TRIAL_STATES), name="chk_trial_state"),
        Index("idx_research_trials_study", "study_pk", "id"),
        Index("idx_research_trials_run", "run_id"),
    )


class ResearchStudyEvent(Base, TimestampMixin):
    """An append-only record of a decision on a study, with its reason."""

    __tablename__ = "research_study_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    study_pk: Mapped[int] = _study_fk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("idx_research_study_events_study", "study_pk", "id"),)


class ImmutableRecordError(RuntimeError):
    """Raised when code tries to change a frozen candidate or a recorded event."""


@event.listens_for(ResearchCandidate, "before_update")
@event.listens_for(ResearchStudyEvent, "before_update")
def _refuse_update(_mapper: Any, _connection: Any, target: Any) -> None:
    raise ImmutableRecordError(f"{type(target).__name__} rows are immutable once written")
