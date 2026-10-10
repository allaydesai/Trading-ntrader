"""Seal a stopped session into one comparable run record (Story 5.3, FR40–FR44).

Owns: the seal — the one transaction that turns a ``stopped`` session into an
ordinary ``backtest_runs`` row plus its ``performance_metrics`` row, links the
session's trades to it (AR8), and moves the session to ``sealed`` — and the
seal report (AR29). Every write is **one transaction**. The backtest write is
``BacktestPersistenceService.save_backtest_results``, async and unmodified
(AR12: NaN/Infinity validation and ``DuplicateRecordError`` reused verbatim);
the session half is ``SessionService``, sync and Nautilus-free (AR37, AR38).
``AsyncSession.run_sync`` runs the sync half on the async session's own
connection, so a failure anywhere leaves nothing behind once the caller rolls
back: no orphan run row a list view would show (AR5), no ``sealed`` session
without its record.

Does not own: committing or rolling back (the caller does — ``SessionService``'s
rule), the results computation (``src.core.session_equity``, Story 5.2), the
open-position read (injected: the CLI passes the session's own engine-cache
view, Story 4.6 — PO ruling R2a), or ``--force`` and open trades (Story 5.5).

**Never the broker, never an order.** Sealing reads the database and, through
the injected reader, the session's own Redis view. It calls no order method,
opens no broker connection, and never flattens, closes or modifies a position
(AR31, NFR14) — pinned by ``tests/unit/services/test_session_seal_service.py``.
"""

import copy
import inspect
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import MONEY_MAX
from sqlalchemy import Numeric
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.core.results_extractor import _require_capital  # Story 5.1's rule — its one home
from src.core.session_equity import (
    EQUITY_SERIES_KEY,
    SessionResults,
    SessionTradeRow,
    reconstruct_session_results,
)
from src.core.strategy_registry import StrategyRegistry
from src.db.exceptions import SealRefusedError
from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.models.backtest_result import BacktestResult
from src.models.reconciliation import SessionView, ViewPosition
from src.models.session import SessionStatus
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.session_service import SessionService

#: The largest amount a USD ``Money`` holds — a starting capital past it cannot
#: become the ledger's ``Money`` at all (Story 5.3 code review).
LARGEST_CAPITAL = Decimal(str(MONEY_MAX))
#: The fields a ``BacktestResult`` is built from — the values the persistence
#: service copies into same-named ``performance_metrics`` columns.
_RESULT_FIELDS = frozenset(inspect.signature(BacktestResult.__init__).parameters) - {"self"}

#: FR44/AR6: the one column that tells a sealed session from a backtest.
PAPER_RUN_TYPE = "paper"
#: ``backtest_runs.data_source`` for a sealed session: live IBKR market data,
#: beside a backtest's ``catalog``/``ibkr``/``kraken``/``mock`` (Story 5.4 may
#: refine the wording).
PAPER_DATA_SOURCE = "ibkr_live"
#: The ``config_snapshot`` version every backtest path writes.
SNAPSHOT_VERSION = "1.0"
#: The disposition a clean seal reports. Under PO ruling R2a a seal with any
#: open position is refused, so this is the only one 5.3 can report; Story
#: 5.5's ``--force`` adds the other.
NONE_OPEN_DISPOSITION = "none open at seal"
#: ``backtest_runs.instrument_symbol`` is ``String(50)``; a test pins the two equal.
INSTRUMENT_SYMBOL_MAX = 50
#: Logged by the command **after** its commit — never for a seal that rolls back.
LINKED_BACKTEST_WARNING_EVENT = "session.seal_linked_backtest_warning"

#: The open-position reader the seal is handed: the session's own view of
#: what it holds, by ``session_id``. Raises rather than ever reading as flat.
OpenPositionsReader = Callable[[UUID], SessionView]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SealReport:
    """What a seal did — the one source both renderings read (AR29, D-H)."""

    session_id: UUID
    name: str
    run_id: UUID
    sealed_at: datetime
    closed_trade_count: int
    trades_backfilled: int
    initial_capital: Decimal
    start_date: datetime
    end_date: datetime
    session_conditions: dict[str, Any]
    linked_backtest_run_id: UUID | None = None
    linked_backtest_warning: str | None = None
    open_positions: tuple[ViewPosition, ...] = ()
    open_positions_disposition: str = NONE_OPEN_DISPOSITION
    data_source: str = PAPER_DATA_SOURCE
    run_type: str = PAPER_RUN_TYPE


@dataclass(frozen=True)
class RunIdentity:
    """The run row's identity columns and base snapshot, from a stored spec."""

    strategy_name: str
    strategy_type: str
    instrument_symbol: str
    config_snapshot: dict[str, Any]


@dataclass(frozen=True)
class _Claimed:
    """Plain values read off the locked row, safe across the async boundary."""

    pk: int
    session_id: UUID
    name: str
    spec: dict[str, Any]
    period_start: datetime
    sealed_at: datetime
    linked_backtest_run_id: UUID | None


class _ReadOnce:
    """The seal's clock, read once — by ``transition()``, after its row lock.

    Read before the lock, a stop that held the row and committed later would
    leave ``sealed_at`` earlier than ``last_stopped_at`` (code review). The one
    instant read here stamps ``sealed_at`` and is the record's ``end_date``.
    """

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self.value: datetime | None = None

    def __call__(self) -> datetime:
        if self.value is None:
            self.value = self._clock()
        return self.value


def require_sealable_capital(capital: Decimal) -> None:
    """The starting capital a record can carry — judged once, up front.

    Story 5.1's rule (a positive, finite, whole-cent ``Decimal``), reached
    through its one home, plus the largest amount a USD ``Money`` holds. The
    capital itself is never changed: it is stored as given (PO ruling R1a).

    Raises:
        SealRefusedError: The capital fails either check.
    """
    try:
        _require_capital(capital)
    except (TypeError, ValueError) as exc:
        raise SealRefusedError(f"The starting capital cannot be sealed: {exc}.") from exc
    if capital > LARGEST_CAPITAL:
        raise SealRefusedError(
            f"The starting capital {capital} is larger than the largest amount a USD record "
            f"can hold ({LARGEST_CAPITAL})."
        )


def _require_storable(result: BacktestResult) -> None:
    """Refuse a metric its ``performance_metrics`` column cannot hold, by name.

    A 1-day session at +6% compounds to a CAGR of ~1.75e9, past
    ``Numeric(15,6)``; Postgres would answer with a ``DataError`` naming no
    column (code review). The bounds are read from the columns themselves. A
    non-finite value is left to the persistence service's own check (AR12).
    Only the result's own fields are checked: ``win_rate`` is a column the
    persistence service computes from the counts, while the result's
    ``win_rate`` is a percentage property of another shape.
    """
    for column in PerformanceMetrics.__table__.columns:
        value = getattr(result, column.name, None) if column.name in _RESULT_FIELDS else None
        if not isinstance(column.type, Numeric) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            continue
        precision, scale = column.type.precision or 0, column.type.scale or 0
        if abs(round(Decimal(str(value)), scale)) >= Decimal(10) ** (precision - scale):
            raise SealRefusedError(
                f"The session's {column.name} is {value}, more than the record's {column.name} "
                f"column can hold (Numeric({precision},{scale}): magnitude below "
                f"1e{precision - scale}); refusing a record Postgres would reject."
            )


def strategy_display_name(strategy_type: str) -> str:
    """The backtest path's ``strategy_name`` for a registry name (story F8)."""
    return strategy_type.replace("_", " ").title()


def period_start(trading_session: Any) -> datetime:
    """The sealed record's ``start_date`` — PO ruling R3a, in one place.

    The session's ``created_at``: nothing durable records its first start
    (``last_started_at`` is overwritten on every claim, ``runtime_flags`` is
    cleared on every run), and creation is never after it. The period may so
    include the gap before the first start — CAGR reads low, never high.
    Story 5.4 owns "the session's first start" and may replace this source.

    Raises:
        SealRefusedError: The row carries no ``created_at``.
    """
    created_at = trading_session.created_at
    if created_at is None:
        raise SealRefusedError(
            f"Session {trading_session.name!r} has no created_at, so its record has no "
            "period start; refusing to guess one."
        )
    return created_at


def run_identity(spec: Mapping[str, Any]) -> RunIdentity:
    """The run row's identity, read from the spec **as stored** (D-E).

    Not re-validated through ``SessionSpec.from_stored``: a stopped session
    must always be sealable, and a parameter constraint tightened since the
    session was created must not make its record unwritable.

    Raises:
        SealRefusedError: The spec does not hold exactly one strategy, the
            strategy is no longer registered or has no config class, a bar
            type does not parse, or its symbols exceed the column.
    """
    strategies = list(spec.get("strategies") or ())
    if len(strategies) != 1:
        raise SealRefusedError(
            f"A sealed record carries one strategy; this session's spec holds "
            f"{len(strategies)}, and its trade rows carry no strategy to split them by."
        )
    entry = strategies[0]
    strategy_type = str(entry.get("strategy_id"))
    paths = _strategy_paths(strategy_type)
    bar_types = _parsed_bar_types(entry.get("bar_types") or ())
    symbols = list(dict.fromkeys(bar.instrument_id.symbol.value for bar in bar_types))
    instrument_symbol = ",".join(symbols)
    if len(instrument_symbol) > INSTRUMENT_SYMBOL_MAX:
        raise SealRefusedError(
            f"The session's symbols ({instrument_symbol}) exceed the run record's "
            f"{INSTRUMENT_SYMBOL_MAX}-character instrument column."
        )
    specs = {str(bar.spec) for bar in bar_types}
    return RunIdentity(
        strategy_name=strategy_display_name(strategy_type),
        strategy_type=strategy_type,
        instrument_symbol=instrument_symbol,
        config_snapshot={
            **paths,
            "version": SNAPSHOT_VERSION,
            "config": dict(entry.get("parameters") or {}),
            "bar_type": specs.pop() if len(specs) == 1 else None,
        },
    )


def _strategy_paths(strategy_type: str) -> dict[str, str]:
    try:
        definition = StrategyRegistry.get(strategy_type)
    except KeyError:
        raise SealRefusedError(
            f"The session's strategy {strategy_type!r} is no longer registered, so its "
            "record cannot name the strategy's class; register it again to seal."
        ) from None
    if definition.config_path is None:
        raise SealRefusedError(
            f"The session's strategy {strategy_type!r} registers no config class, so its "
            "record cannot name one."
        )
    return {"strategy_path": definition.strategy_path, "config_path": definition.config_path}


def _parsed_bar_types(raw: Sequence[str]) -> list[BarType]:
    try:
        parsed = [BarType.from_str(str(value)) for value in raw]
    except ValueError as exc:
        raise SealRefusedError(f"A stored bar type does not parse: {exc}") from exc
    if not parsed:
        raise SealRefusedError("The session's spec names no bar type, so no instrument.")
    return parsed


def _claim(
    sync_session: Session, identifier: str, clock: Callable[[], datetime], run_id: UUID
) -> _Claimed:
    """Resolve, move to ``sealed`` through ``transition()`` (AR37), then link.

    The edge is validated under ``FOR UPDATE`` before anything else in the
    seal runs, so ``running``/``sealed``/``created`` refuse first; the seal
    instant is read by ``transition()`` itself, after that lock.
    """
    stamp = _ReadOnce(clock)
    service = SessionService(SyncTradingSessionRepository(sync_session), time_source=stamp)
    row = service.resolve(identifier)
    service.transition(row.session_id, to=SessionStatus.SEALED)
    service.record_sealed_run(row.session_id, sealed_run_id=run_id)
    return _Claimed(
        pk=row.id,
        session_id=row.session_id,
        name=row.name,
        spec=dict(row.spec),
        period_start=period_start(row),
        sealed_at=stamp(),
        linked_backtest_run_id=row.linked_backtest_run_id,
    )


def _refuse_unless_flat(name: str, view: SessionView) -> None:
    """PO ruling R2a: fail closed on anything but a known-flat session."""
    if view.positions:
        held = ", ".join(f"{p.instrument_id} {p.quantity}" for p in view.positions)
        raise SealRefusedError(
            f"Session {name!r} still holds open positions ({held}). A seal never exits or "
            "modifies a position: restart the session so its strategy can exit it, then stop "
            "the session and seal."
        )
    if view.positions_skipped:
        raise SealRefusedError(
            f"Session {name!r}'s engine cache holds {view.positions_skipped} position(s) on "
            "another account than TWS_ACCOUNT, so it is not known to be flat."
        )


def _trades_of(sync_session: Session, session_pk: int) -> Sequence[SessionTradeRow]:
    return SyncTradingSessionRepository(sync_session).find_session_trades(session_pk)


def _backfill(sync_session: Session, session_pk: int, run_pk: int, expected: int) -> int:
    linked = SyncTradingSessionRepository(sync_session).backfill_trades_run_id(session_pk, run_pk)
    if linked != expected:
        raise SealRefusedError(
            f"The seal read {expected} trade row(s) but linked {linked}; refusing a record "
            "whose trades do not match the rows its metrics came from."
        )
    return linked


def _reconstruct(
    trades: Sequence[SessionTradeRow], capital: Decimal, start: datetime, end: datetime
) -> SessionResults:
    try:
        results = reconstruct_session_results(
            trades, starting_capital=capital, start=start, end=end
        )
    except (ValueError, ArithmeticError) as exc:
        # ``ArithmeticError`` too: ``decimal.InvalidOperation`` is one, not a
        # ``ValueError`` (code review).
        raise SealRefusedError(f"The session's results cannot be sealed: {exc}") from exc
    _require_storable(results.result)
    return results


async def _write_run(
    db: AsyncSession, claimed: _Claimed, results: SessionResults, **columns: Any
) -> BacktestRun:
    """The verbatim backtest write (AR12), then ``run_type`` — D-D."""
    identity = run_identity(claimed.spec)
    run = await BacktestPersistenceService(BacktestRepository(db)).save_backtest_results(
        strategy_name=identity.strategy_name,
        strategy_type=identity.strategy_type,
        instrument_symbol=identity.instrument_symbol,
        config_snapshot=results.merged_into(identity.config_snapshot),
        backtest_result=results.result,
        data_source=PAPER_DATA_SOURCE,
        start_date=claimed.period_start,
        **columns,
    )
    run.run_type = PAPER_RUN_TYPE
    await db.flush()
    return run


async def _linked_backtest_warning(db: AsyncSession, claimed: _Claimed) -> str | None:
    """PO ruling R4a, refined: name a failed or metric-less link; never refuse.

    Not logged here: the command logs it after its commit, so a seal that
    rolls back leaves no warning about a record that does not exist.
    """
    linked_id = claimed.linked_backtest_run_id
    if linked_id is None:
        return None
    linked = await BacktestRepository(db).find_by_run_id(linked_id)
    if linked is None:
        return f"linked backtest {linked_id} was not found"
    if linked.execution_status != "success":
        return (
            f"linked backtest {linked_id} {linked.execution_status}; it is no successful "
            "baseline to compare this record against"
        )
    if linked.metrics is None:
        return f"linked backtest {linked_id} has no metrics row to compare against"
    return None


async def seal_session(
    db: AsyncSession,
    identifier: str,
    *,
    starting_capital: Decimal,
    read_open_positions: OpenPositionsReader,
    clock: Callable[[], datetime] = _utc_now,
    new_run_id: Callable[[], UUID] = uuid4,
) -> SealReport:
    """Seal one stopped session, in the caller's transaction (FR40, FR42, AR8).

    Args:
        db: The async session. Never committed or rolled back here.
        identifier: The session's name or ``session_id``.
        starting_capital: The operator's stated starting balance, stored as
            given (PO ruling R1a) once :func:`require_sealable_capital` passes.
        read_open_positions: The session's own view of what it holds.
        clock: When the seal happens — read once, after the row lock, for
            ``sealed_at`` and ``end_date`` alike.
        new_run_id: The new run's ``run_id`` (a test seam).

    Raises:
        InvalidSessionTransition: The session is not ``stopped``.
        RecordNotFoundError: No session matches ``identifier``.
        SealRefusedError: The capital cannot be carried, the session is not
            known to be flat, a row, the spec or a metric cannot be sealed
            honestly, or the backfill missed a row.
        SessionViewUnavailableError: The reader could not read the view —
            never read as flat (the reader's own).
        RedisUnreachableError: The reader could not reach Redis (its own).
        ValidationError: A metric is NaN/Infinity (the backtest path's own).
        DuplicateRecordError: ``run_id`` exists (the backtest path's own).
    """
    require_sealable_capital(starting_capital)
    started, run_id = time.monotonic(), new_run_id()
    claimed = await db.run_sync(_claim, identifier, clock, run_id)
    view = read_open_positions(claimed.session_id)
    _refuse_unless_flat(claimed.name, view)
    trades = await db.run_sync(_trades_of, claimed.pk)
    results = _reconstruct(trades, starting_capital, claimed.period_start, claimed.sealed_at)
    warning = await _linked_backtest_warning(db, claimed)
    run = await _write_run(
        db,
        claimed,
        results,
        run_id=run_id,
        end_date=claimed.sealed_at,
        initial_capital=starting_capital,
        execution_duration_seconds=Decimal(f"{time.monotonic() - started:.3f}"),
    )
    backfilled = await db.run_sync(_backfill, claimed.pk, run.id, len(trades))
    conditions = copy.deepcopy(run.config_snapshot["session_conditions"])
    return SealReport(
        session_id=claimed.session_id,
        name=claimed.name,
        run_id=run_id,
        sealed_at=claimed.sealed_at,
        closed_trade_count=conditions[EQUITY_SERIES_KEY]["closed_trades"],
        trades_backfilled=backfilled,
        initial_capital=run.initial_capital,  # as stored, never as typed
        start_date=claimed.period_start,
        end_date=claimed.sealed_at,
        session_conditions=conditions,
        linked_backtest_run_id=claimed.linked_backtest_run_id,
        linked_backtest_warning=warning,
        open_positions=view.positions,
    )


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(UTC).isoformat()


def _summarised(conditions: Mapping[str, Any]) -> dict[str, Any]:
    """The stored document, its point list replaced by a count (D-H)."""
    summary = copy.deepcopy(dict(conditions))
    series = summary.get(EQUITY_SERIES_KEY)
    if isinstance(series, dict) and "curve" in series:
        series["curve_points"] = len(series.pop("curve"))
    return summary


def seal_json_payload(report: SealReport) -> dict[str, Any]:
    """AR29: plain values, snake_case, ISO-8601 UTC, Decimal money as strings."""
    linked = report.linked_backtest_run_id
    return {
        "session_id": str(report.session_id),
        "name": report.name,
        "run_id": str(report.run_id),
        "run_type": report.run_type,
        "sealed_at": _iso(report.sealed_at),
        "closed_trade_count": report.closed_trade_count,
        "trades_backfilled": report.trades_backfilled,
        "open_positions": [
            {"instrument_id": p.instrument_id, "quantity": str(p.quantity)}
            for p in report.open_positions
        ],
        "open_positions_disposition": report.open_positions_disposition,
        "linked_backtest_run_id": None if linked is None else str(linked),
        "linked_backtest_warning": report.linked_backtest_warning,
        "execution_conditions": {
            "initial_capital": str(report.initial_capital),
            "start_date": _iso(report.start_date),
            "end_date": _iso(report.end_date),
            "data_source": report.data_source,
            "session_conditions": _summarised(report.session_conditions),
        },
    }


def render_seal_report(report: SealReport) -> list[str]:
    """The human form, rendered **from** :func:`seal_json_payload` — one line
    per key, nested keys indented — so both forms carry identical fields."""
    return _render(seal_json_payload(report), depth=0)


def _render(payload: Mapping[str, Any], *, depth: int) -> list[str]:
    indent = "  " * depth
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, Mapping):
            lines.append(f"{indent}{key}:")
            lines.extend(_render(value, depth=depth + 1))
        elif isinstance(value, list) and value and isinstance(value[0], Mapping):
            lines.append(f"{indent}{key}:")
            for item in value:
                pairs = ", ".join(f"{k}={v}" for k, v in item.items())
                lines.append(f"{indent}  - {pairs}")
        elif isinstance(value, list):
            lines.append(f"{indent}{key}: {', '.join(map(str, value)) if value else 'none'}")
        else:
            lines.append(f"{indent}{key}: {'none' if value is None else value}")
    return lines
