"""Read paper sessions, their trades and their link to a study: plain SELECTs only.

The research server never writes a session row. Reads that serve only the paper
tools open their transaction ``READ ONLY``, so a write slipped in by mistake is
refused by Postgres itself. Timestamps come back in the database server's zone
and are turned into UTC here, once. The stored spec is read as raw JSON: the
domain model's loader resolves strategies through live-trading code.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from src.db.models.research import ResearchCandidate, ResearchStudy, ResearchTrial
from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import TRADE_COLUMNS
from src.mcp_server.paper.band import ClosedTrade, closed_trade


@contextmanager
def read_only_session() -> Iterator[Session]:
    """A database session whose transaction Postgres refuses to write in."""
    with get_sync_session() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session


def to_utc(value: datetime | None) -> datetime | None:
    """A stored timestamp in UTC (the database returns its own zone)."""
    return None if value is None else value.astimezone(timezone.utc)


def _owner(*, run_pk: int | None, session_pk: int | None):
    if (run_pk is None) == (session_pk is None):
        raise ValueError("pass exactly one of run_pk and session_pk")
    return Trade.backtest_run_id == run_pk if run_pk is not None else Trade.session_id == session_pk


def closed_trades(
    session: Session, *, run_pk: int | None = None, session_pk: int | None = None
) -> list[ClosedTrade]:
    """A run's or a session's closed trades, in exit order."""
    stmt = (
        select(
            Trade.entry_timestamp,
            Trade.exit_timestamp,
            Trade.quantity,
            Trade.entry_price,
            Trade.profit_loss,
            Trade.commission_amount,
        )
        .where(_owner(run_pk=run_pk, session_pk=session_pk))
        .order_by(Trade.exit_timestamp, Trade.id)
    )
    found = []
    for entry, exit_, quantity, price, pnl, commission in session.execute(stmt):
        trade = closed_trade(
            entry_at=to_utc(entry) or entry,
            exit_at=to_utc(exit_),
            quantity=quantity,
            entry_price=price,
            profit_loss=pnl,
            commission=commission,
        )
        if trade is not None:
            found.append(trade)
    return found


def trade_rows(session: Session, session_pk: int, limit: int | None = None) -> list[dict[str, Any]]:
    """A session's trades in the export layout, newest first when limited."""
    stmt = select(Trade).where(Trade.session_id == session_pk)
    order = Trade.entry_timestamp.desc() if limit else Trade.entry_timestamp
    rows = session.scalars(stmt.order_by(order, Trade.id).limit(limit))
    return [{c: getattr(t, c) for c in TRADE_COLUMNS} for t in rows]


def resolve_session(session: Session, key: str) -> TradingSession:
    """The session named by name or session id, or a failure saying where to find one."""
    try:
        stmt = select(TradingSession).where(TradingSession.session_id == UUID(key.strip()))
    except ValueError:
        stmt = select(TradingSession).where(TradingSession.name == key)
    row = session.scalars(stmt).first()
    if row is None:
        raise ToolFailure("unknown_session", f"No paper session {key!r}.", fix="list_sessions.")
    return row


def sessions(session: Session, *, status: str | None = None) -> list[TradingSession]:
    """Every session, newest first, optionally of one status."""
    stmt = select(TradingSession).order_by(TradingSession.created_at.desc(), TradingSession.id)
    if status is not None:
        stmt = stmt.where(TradingSession.status == status)
    return list(session.scalars(stmt))


def session_names(session: Session) -> set[str]:
    return set(session.scalars(select(TradingSession.name)))


def closed_counts(session: Session, pks: list[int]) -> dict[int, int]:
    """Closed trades per session primary key."""
    if not pks:
        return {}
    stmt = (
        select(Trade.session_id, func.count())
        .where(Trade.session_id.in_(pks), Trade.exit_timestamp.is_not(None))
        .group_by(Trade.session_id)
    )
    return {pk: count for pk, count in session.execute(stmt).all()}


def sessions_linked_to(session: Session, run_ids: list[UUID]) -> list[TradingSession]:
    """Sessions compared against any of the runs, newest first."""
    if not run_ids:
        return []
    stmt = select(TradingSession).where(TradingSession.linked_backtest_run_id.in_(run_ids))
    return list(session.scalars(stmt.order_by(TradingSession.created_at.desc())))


@dataclass(frozen=True)
class Link:
    """The study and frozen candidate a session's compare-to run was the holdout test of."""

    study: ResearchStudy
    candidate: ResearchCandidate
    #: The newest completed out-of-sample run of that candidate is the linked run.
    is_latest_out_of_sample: bool

    @property
    def candidate_is_current(self) -> bool:
        return self.candidate.version == self.study.current_version


def oos_runs(session: Session, candidate_pk: int) -> list[UUID]:
    """A candidate's completed out-of-sample runs, oldest first."""
    stmt = select(ResearchTrial.run_id).where(
        ResearchTrial.candidate_pk == candidate_pk,
        ResearchTrial.role == "out_of_sample",
        ResearchTrial.state == "completed",
        ResearchTrial.run_id.is_not(None),
    )
    return [r for r in session.scalars(stmt.order_by(ResearchTrial.id)) if r is not None]


def link_of(session: Session, run_id: UUID | None) -> Link | None:
    """The candidate whose out-of-sample run this is, whichever attempt it was."""
    if run_id is None:
        return None
    stmt = select(ResearchTrial).where(
        ResearchTrial.run_id == run_id,
        ResearchTrial.role == "out_of_sample",
        ResearchTrial.state == "completed",
    )
    trial = session.scalars(stmt).first()
    if trial is None or trial.candidate_pk is None:
        return None
    candidate = session.get(ResearchCandidate, trial.candidate_pk)
    study = session.get(ResearchStudy, trial.study_pk)
    if candidate is None or study is None:
        return None
    runs = oos_runs(session, candidate.id)
    return Link(study, candidate, is_latest_out_of_sample=bool(runs) and runs[-1] == run_id)
