"""SQLAlchemy ORM model for a named paper-trading session.

Not ``src/db/session.py`` — that name is taken by the SQLAlchemy async engine
module, and ``src/db/session_sync.py`` by its sync twin. ``src/models/session.py``
is Story 2.1's domain model (``SessionSpec``). See the story's Dev Notes,
"the word *session* is a crowded namespace".
"""

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin
from src.models.session import SessionStatus


class TradingSession(Base, TimestampMixin):
    """A named paper-trading session and its frozen specification.

    The system of record for a multi-week forward test: ``spec`` is written once
    at creation and never updated by any repository method (FR14) — the same
    write-once discipline ``BacktestRun.config_snapshot`` already follows.

    Attributes:
        id: Internal database primary key.
        session_id: Business identifier (UUID) for external references.
        name: Operator-chosen unique handle, used everywhere a session ID is.
        status: Lifecycle state (created/running/stopped/sealed). Set only via
            the column default at creation — no code path assigns it directly
            (Story 2.3 owns transitions).
        spec: The frozen ``SessionSpec``, serialised via
            ``SessionSpec.to_stored()``. Write-once: no repository method
            updates this column.
        linked_backtest_run_id: The backtest this session is intended to be
            compared against (FR15), or None.
        sealed_run_id: The ``backtest_runs.run_id`` this session was sealed
            into (Epic 5), or None before sealing. No FK — mirrors
            ``BacktestRun.reproduced_from_run_id``, a bare nullable reference.
        last_started_at: When the session last started running, or None.
        last_stopped_at: When the session last stopped, or None.
        sealed_at: When the session was sealed, or None.
        last_heartbeat_at: Most recent liveness heartbeat, or None.
        last_bar_at: Most recent bar the session observed, or None.
        runtime_flags: Facts about *this* process run that outlive it, or None
            (Story 2.7): the strategies contained during the run, and whether
            all of them were. Versioned (``{"v": 1, ...}``) so a later story can
            add a key without guessing. Written only by
            ``session_service._record_strategy_failure``; cleared on every
            ``-> running`` transition.
        created_at: When the row was created (via ``TimestampMixin``).
    """

    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid4, unique=True, nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    # values_callable is mandatory: sa.Enum(SessionStatus) alone persists the
    # member NAME ("CREATED"), not its value ("created"). architecture.md's
    # naming patterns fix the DB labels as lowercase, and this is the first
    # enum column in the repo that cannot dodge the footgun via name == value
    # (contrast src/models/instrument_metadata.py's ResolutionStatus).
    status: Mapped[SessionStatus] = mapped_column(
        sa.Enum(
            SessionStatus,
            name="session_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SessionStatus.CREATED,
    )

    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)

    linked_backtest_run_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("backtest_runs.run_id"), nullable=True
    )

    sealed_run_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    last_started_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_stopped_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    sealed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_bar_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Story 2.7 (AC #4). NULL means "nothing to report", so every healthy
    # session and every pre-existing row costs nothing — deliberately no
    # server_default, which is also what keeps the migration metadata-only.
    #
    # ⚠️ SQLAlchemy does not track **in-place** mutation of a plain JSONB
    # column: `row.runtime_flags["failed_strategies"].append(...)` silently
    # never persists. Build a new dict and assign it — see
    # `session_service._record_strategy_failure`, the only writer.
    #
    # Cleared to NULL on every `-> running` transition, so a failure from a
    # previous process run is never reported against the current one.
    runtime_flags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_trading_sessions_status", "status"),)

    def __repr__(self) -> str:
        """Return string representation of TradingSession."""
        return (
            f"<TradingSession(session_id={self.session_id}, "
            f"name={self.name!r}, status={self.status})>"
        )
