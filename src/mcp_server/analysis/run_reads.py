"""Read one stored run's trades, equity curve and regime breakdown (S3.3, S4.3).

All three read what the run persisted; nothing is re-run. A run's trades are
loaded only when asked for: ``BacktestRun.trades`` is a select-in relationship,
so the run row is read with it switched off.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, noload

from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.bars import daily_closes
from src.mcp_server.analysis.regimes import WARMUP_CALENDAR_DAYS, TradePoint, breakdown
from src.mcp_server.analysis.series import downsample, drawdown, equity_series
from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import TRADE_COLUMNS
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.runs import parse_run_ids

CATALOG_PREFIX = "catalog:"


def load_run(session: Session, run_id: str) -> BacktestRun:
    """The run row (without its trades), or a failure saying where run ids come from."""
    (canonical,) = parse_run_ids([run_id], minimum=1, maximum=1)
    stmt = (
        select(BacktestRun)
        .options(noload(BacktestRun.trades))
        .where(BacktestRun.run_id == UUID(canonical))
    )
    run = session.scalars(stmt).first()
    if run is None:
        raise ToolFailure("unknown_run", f"No run {canonical}.", fix="Use a run_id from get_job.")
    return run


def run_catalog(run: BacktestRun) -> str | None:
    """The named catalog a run read its bars from, when it read one."""
    source = run.data_source or ""
    return source.removeprefix(CATALOG_PREFIX) if source.startswith(CATALOG_PREFIX) else None


def get_trades(run_id: str, offset: int, limit: int, *, max_limit: int) -> dict[str, Any]:
    """One page of a run's trades, in entry order."""
    limit = max(1, min(limit, max_limit))
    offset = max(0, offset)
    with get_sync_session() as session:
        run = load_run(session, run_id)
        total = session.scalar(
            select(func.count()).select_from(Trade).where(Trade.backtest_run_id == run.id)
        )
        stmt = (
            select(Trade)
            .where(Trade.backtest_run_id == run.id)
            .order_by(Trade.entry_timestamp, Trade.id)
            .offset(offset)
            .limit(limit)
        )
        trades = [{c: getattr(t, c) for c in TRADE_COLUMNS} for t in session.scalars(stmt)]
    end = offset + len(trades)
    return to_jsonable(
        {
            "run_id": run.run_id,
            "total": total,
            "offset": offset,
            "trades": trades,
            "next_offset": end if end < (total or 0) else None,
        }
    )


def _equity(session: Session, run: BacktestRun):
    points = SyncEquityCurveRepository(session).find_points(run.run_id)
    if not points:
        raise ToolFailure(
            "no_equity_curve",
            f"Run {run.run_id} has no stored equity curve (it predates them).",
            fix="reproduce_run makes a new run of the same config with its curve stored.",
        )
    return equity_series(points)


def get_equity_curve(run_id: str, max_points: int) -> dict[str, Any]:
    """The equity curve and its drawdown, at most ``max_points`` points."""
    with get_sync_session() as session:
        run = load_run(session, run_id)
        equity = _equity(session, run)
    dd = drawdown(equity)
    small = downsample(equity, max_points)
    small_dd = dd.loc[small.index].to_numpy()
    points = [
        {"time": t.isoformat(), "equity": round(float(v), 2), "drawdown": round(float(d), 6)}
        for t, v, d in zip(pd.DatetimeIndex(small.index), small.to_numpy(), small_dd)
    ]
    trough = pd.DatetimeIndex(dd.index)[int(dd.to_numpy().argmin())]
    return to_jsonable(
        {
            "run_id": run.run_id,
            "points": points,
            "total_points": len(equity),
            "returned_points": len(points),
            "start_equity": float(equity.iloc[0]),
            "final_equity": float(equity.iloc[-1]),
            "max_drawdown": round(float(dd.min()), 6),
            "max_drawdown_at": trough.isoformat(),
            "units": {"equity": "currency", "drawdown": "fraction"},
        }
    )


def _trade_points(session: Session, run: BacktestRun) -> list[TradePoint]:
    stmt = select(Trade.entry_timestamp, Trade.exit_timestamp, Trade.profit_loss).where(
        Trade.backtest_run_id == run.id
    )
    return [
        TradePoint(entry=entry, closed=exit_ is not None, pnl=float(pnl or 0))
        for entry, exit_, pnl in session.execute(stmt).all()
    ]


def get_regime_breakdown(run_id: str, benchmark_symbol: str | None, periods: int) -> dict[str, Any]:
    """The run's results by year, trend regime, volatility tercile and sub-period."""
    if not 2 <= periods <= 12:
        raise ToolFailure("invalid_periods", "sub_periods must be 2-12.", fix="Use e.g. 4.")
    with get_sync_session() as session:
        run = load_run(session, run_id)
        equity = _equity(session, run)
        trades = _trade_points(session, run)
    catalog = run_catalog(run)
    if catalog is None:
        raise ToolFailure(
            "no_catalog",
            f"Run {run.run_id} did not read a named catalog ({run.data_source}).",
            fix="Regimes need the benchmark's bars from a named catalog.",
        )
    benchmark = (benchmark_symbol or run.instrument_symbol).strip().upper()
    closes = daily_closes(
        catalog, benchmark, run.start_date - timedelta(days=WARMUP_CALENDAR_DAYS), run.end_date
    )
    if closes.empty:
        raise ToolFailure(
            "no_benchmark_bars",
            f"No daily bars for {benchmark} in '{catalog}' for this window.",
            fix="Pass benchmark_symbol with daily bars (catalog_availability).",
        )
    result = breakdown(equity, trades, closes, periods=periods)
    return to_jsonable(
        {
            "run_id": run.run_id,
            "symbol": run.instrument_symbol,
            "benchmark": benchmark,
            "catalog": catalog,
            "start": run.start_date,
            "end": run.end_date,
            **result,
            "definitions": {
                "trend": "benchmark close above/below its 200-day average, as of the prior close",
                "volatility": "benchmark 21-day realised volatility (annualised), terciles over "
                "the run's days, as of the prior close",
                "return": "compounded daily return of the run's equity on the cell's days",
                "trades": "trades entered in the cell; win_rate and pnl over closed ones",
            },
        }
    )
