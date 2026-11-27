"""Regime profiles of stored runs: run inputs → core attribution → stored report (phase 2).

``run_regime_attribution`` profiles one or more runs against one stored calendar version,
read as of one build (the current one by default), and stores the reading append-only in
``regime_reports``. It computes no labels: the calendar is read by date, and the
reference's closes are read only for the benchmark return. Ground-truth readings carry
``hindsight: true`` and the stamp ``"hindsight, not tradeable"``.

A rerun with the recorded ``calendar_version`` and ``as_of_build`` reads the same label
rows (``labels_hash`` matches) and the same ``data_end``, so it reproduces the report even
after later builds extend the data or fill ground-truth values.

This module is one of the named files permitted to read ground-truth rows
(``tests/unit/governance/test_regime_ground_truth_boundary.py``).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.regimes.attribution import (
    Calendar,
    TradeRecord,
    bucket_name,
    daily_frame,
    families_of,
    labels_in_force,
    profile,
)
from src.core.regimes.bootstrap import CONFIDENCE, MEAN_BLOCK
from src.core.regimes.definition import LABEL_SETS, build_definition, definition_hash
from src.core.regimes.trade_view import correlation
from src.db.models.regime import RegimeCalendarVersion, RegimeReport
from src.db.models.trade import Trade
from src.db.repositories.regime_repository_sync import (
    SyncRegimeReportRepository,
    SyncRegimeRepository,
)
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.regime_calendar import (
    STAMP,
    calendar_version_for,
    load_reference_bars,
    resolve_build,
)
from src.mcp_server.analysis.sleeves import Sleeve, load_sleeves
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.services.provenance import REPO_ROOT, git_output

MAX_RUNS = 10
#: Every parameter and its default; a non-default value is recorded under ``overrides``.
DEFAULTS: dict[str, Any] = {
    "label_set": "detector",
    "benchmark_symbol": "SPY",
    "catalog": "firstrate-etf",
    "lag_days": None,
    "min_days": 60,
    "min_trades": 20,
    "bootstrap_samples": 1000,
    "seed": 0,
    "calendar_version": None,
    "as_of_build": None,
}
DETECTOR_LAG = 1
#: Selectors, not settings: choosing the label set or a snapshot to rerun is no override.
_NOT_OVERRIDES = ("label_set", "as_of_build")


@dataclass(frozen=True)
class _Read:
    """The calendar snapshot a report reads."""

    version: RegimeCalendarVersion
    build: int
    calendar: Calendar
    labels_hash: str


def _resolve_lag(label_set: str, lag_days: int | None) -> int:
    if label_set not in LABEL_SETS:
        raise ToolFailure(
            "invalid_label_set",
            f"Unknown label_set '{label_set}'.",
            fix="Use 'detector' or 'ground_truth'.",
        )
    if label_set == "ground_truth":
        if lag_days is not None:
            raise ToolFailure(
                "lag_not_allowed",
                "Ground-truth labels apply to the same day's return; no lag is taken, not "
                "even lag_days=0, so no one can believe a lag was applied to hindsight.",
                fix="Leave lag_days unset with label_set='ground_truth'.",
            )
        return 0
    if lag_days is None:
        return DETECTOR_LAG
    if not 0 <= lag_days <= 20:
        raise ToolFailure("invalid_lag", "lag_days must be 0-20.", fix="Use 1 (the default).")
    return lag_days


def _check_params(min_days: int, min_trades: int, samples: int, seed: int) -> None:
    if min_days < 1 or min_trades < 0:
        raise ToolFailure(
            "invalid_minimum", "min_days must be ≥ 1 and min_trades ≥ 0.", fix="Use 60 and 20."
        )
    if not 100 <= samples <= 10_000:
        raise ToolFailure(
            "invalid_samples", "bootstrap_samples must be 100-10,000.", fix="Use 1000."
        )
    if seed < 0:
        raise ToolFailure("invalid_seed", "seed must be 0 or more.", fix="Use 0 (the default).")


def labels_hash(rows: list[tuple[date, dict[str, Any]]]) -> str:
    """sha256 of the label rows read: session date and payload, in date order."""
    text = json.dumps(
        [[day.isoformat(), labels] for day, labels in rows], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _read_calendar(
    repo: SyncRegimeRepository,
    key: tuple[str, str, str],
    number: int | None,
    as_of_build: int | None,
) -> _Read:
    version = calendar_version_for(repo, key, number)
    build = resolve_build(version, as_of_build)
    data_end = repo.build_data_end(version.id, build)
    assert data_end is not None, "every build has a history row"
    reader = repo.ground_truth_labels if key[2] == "ground_truth" else repo.detector_labels
    rows = [r for r in reader(version, as_of_build=build) if r.session_date <= data_end]
    calendar = Calendar(key[2], [r.session_date for r in rows], [r.labels for r in rows], data_end)
    digest = labels_hash([(r.session_date, r.labels) for r in rows])
    return _Read(version, build, calendar, digest)


def _trades(session: Session, sleeve: Sleeve) -> list[TradeRecord]:
    stmt = (
        select(Trade.entry_timestamp, Trade.exit_timestamp, Trade.order_side, Trade.profit_loss)
        .where(Trade.backtest_run_id == sleeve.run.id)
        .order_by(Trade.entry_timestamp, Trade.id)
    )
    return [
        TradeRecord(entry, exit_, side, float(pnl or 0))
        for entry, exit_, side, pnl in session.execute(stmt).all()
    ]


def _run_fields(sleeve: Sleeve) -> dict[str, Any]:
    run = sleeve.run
    metrics = run.metrics
    return {
        "run_id": sleeve.run_id,
        "strategy": run.strategy_name,
        "symbol": run.instrument_symbol,
        "start": run.start_date,
        "end": run.end_date,
        "initial_capital": float(run.initial_capital),
        "final_balance": float(metrics.final_balance) if metrics else None,
        "total_trades": metrics.total_trades if metrics else None,
    }


def _closing_note(fields: dict[str, Any], frame: pd.DataFrame, open_trades: int) -> str | None:
    """Why the last equity point and ``final_balance`` differ, when they do."""
    if fields["final_balance"] is None:
        return None
    gap = float(frame["equity"].iloc[-1]) - fields["final_balance"]
    if abs(gap) < 0.005:
        return None
    if not open_trades:
        return (
            f"Last equity point minus final_balance = {gap:,.2f} with no position open at the "
            "end. Bucket P&L reconciles to the last equity point."
        )
    return (
        f"Last equity point minus final_balance = {gap:,.2f}: the unrealised P&L of "
        f"{open_trades} position(s) still open at the end. Bucket P&L reconciles to the last "
        "equity point, which marks them to market."
    )


def _run_profile(
    sleeve: Sleeve,
    inputs: tuple[dict[str, Any], list[TradeRecord]],
    read: _Read,
    closes: pd.Series,
    params: dict,
) -> tuple[dict[str, Any], pd.DataFrame]:
    fields, trades = inputs
    frame = daily_frame(sleeve.equity, fields["initial_capital"])
    result = profile(
        frame,
        read.calendar,
        trades,
        closes,
        lag=params["lag_days"],
        min_days=params["min_days"],
        min_trades=params["min_trades"],
        samples=params["bootstrap_samples"],
        seed=params["seed"],
        initial_capital=fields["initial_capital"],
    )
    notes = [n for n in (_closing_note(fields, frame, sum(t.exit is None for t in trades)),) if n]
    if fields["symbol"].upper() != read.version.reference:
        notes.append(f"{fields['symbol']} is profiled against {read.version.reference}'s labels.")
    if _intraday_futures(sleeve):
        notes.append(
            "Intraday futures bars: run days are UTC dates, so the evening session after "
            "00:00 UTC counts toward the next session's label (an approximation, design.md 2)."
        )
    after = sum(1 for d in frame.index if d > read.calendar.data_end)
    if after:
        notes.append(f"{after} run day(s) after the calendar's data_end are unlabelled.")
    return {**fields, "families": result["families"], "notes": notes}, frame


def _intraday_futures(sleeve: Sleeve) -> bool:
    snapshot = sleeve.run.config_snapshot or {}
    futures = (sleeve.run.data_source or "").endswith("firstrate-futures")
    return futures and "DAY" not in str(snapshot.get("bar_type", "")).upper()


def _correlations(frames: dict[str, pd.DataFrame], read: _Read, params: dict) -> dict:
    """Per family: the runs' correlation overall and inside each bucket (shared days)."""
    returns = {run_id: frame["ret"] for run_id, frame in frames.items()}
    days = sorted(set().union(*(set(f.index) for f in frames.values())))
    out = {}
    for family, buckets in families_of(read.calendar.label_set).items():
        values = labels_in_force(days, read.calendar, family, lag=params["lag_days"])
        named = {d: bucket_name(family, v) for d, v in zip(days, values)}
        out[family] = correlation(returns, named, buckets, params["min_days"])
    return out


def _params(
    run_ids: list[str], label_set: str, lag: int, given: dict[str, Any], read: _Read
) -> tuple[dict, dict]:
    params = {**given, "label_set": label_set, "lag_days": lag, "run_ids": run_ids}
    overrides = {k: v for k, v in given.items() if v != DEFAULTS[k] and k not in _NOT_OVERRIDES}
    # Pinning the default definition's own version (a rerun) changes nothing; pinning any
    # other version reads a different definition, a declared robustness check.
    if read.version.definition_hash == definition_hash(build_definition(label_set)):
        overrides.pop("calendar_version", None)
    if label_set == "detector" and given["lag_days"] == DETECTOR_LAG:
        overrides.pop("lag_days", None)
    return params, overrides


def _calendar_stamp(read: _Read) -> dict[str, Any]:
    version = read.version
    return {
        "version_id": version.id,
        "version": version.version,
        "build_no": read.build,
        "label_set": version.label_set,
        "input_revision": version.input_revision,
        "definition_hash": version.definition_hash,
        "data_end": read.calendar.data_end,
        "frozen_stats": version.frozen_stats,
    }


def run_regime_attribution(
    run_ids: list[str],
    label_set: str = "detector",
    benchmark_symbol: str = "SPY",
    catalog: str = "firstrate-etf",
    lag_days: int | None = None,
    min_days: int = 60,
    min_trades: int = 20,
    bootstrap_samples: int = 1000,
    seed: int = 0,
    calendar_version: int | None = None,
    as_of_build: int | None = None,
) -> dict[str, Any]:
    """Profile runs against one stored calendar version; store and return the report."""
    lag = _resolve_lag(label_set, lag_days)
    _check_params(min_days, min_trades, bootstrap_samples, seed)
    reference = benchmark_symbol.strip().upper()
    given = {
        "label_set": label_set, "benchmark_symbol": reference, "catalog": catalog,
        "lag_days": lag_days, "min_days": min_days, "min_trades": min_trades,
        "bootstrap_samples": bootstrap_samples, "seed": seed,
        "calendar_version": calendar_version, "as_of_build": as_of_build,
    }  # fmt: skip
    with get_sync_session() as session:
        sleeves = load_sleeves(session, run_ids, minimum=1, maximum=MAX_RUNS)
        inputs = {s.run_id: (_run_fields(s), _trades(session, s)) for s in sleeves}
        read = _read_calendar(
            SyncRegimeRepository(session), (reference, catalog, label_set),
            calendar_version, as_of_build,
        )  # fmt: skip
    closes = load_reference_bars(catalog, reference)["close"]
    closes = closes[closes.index <= read.calendar.data_end]
    params, overrides = _params([s.run_id for s in sleeves], label_set, lag, given, read)
    runs, frames = [], {}
    for sleeve in sleeves:
        run, frames[sleeve.run_id] = _run_profile(
            sleeve, inputs[sleeve.run_id], read, closes, params
        )
        runs.append(run)
    payload = _payload(read, params, overrides, runs)
    if len(frames) > 1:
        payload["correlation"] = _correlations(frames, read, params)
    return _store(payload, read, label_set)


def _payload(read: _Read, params: dict, overrides: dict, runs: list[dict]) -> dict[str, Any]:
    hindsight = read.calendar.label_set == "ground_truth"
    payload: dict[str, Any] = {"hindsight": hindsight}
    if hindsight:
        payload["stamp"] = STAMP
    return payload | {
        "label_set": read.calendar.label_set,
        "lag_days": params["lag_days"],
        "reference": read.version.reference,
        "catalog": read.version.catalog,
        "calendar": [_calendar_stamp(read)],
        "labels_hash": read.labels_hash,
        "params": params,
        "overrides": overrides,
        "declared_robustness_check": bool(overrides),
        "interval": {
            "method": "stationary bootstrap of the whole daily timeline, circular wrap",
            "mean_block_days": MEAN_BLOCK,
            "confidence": CONFIDENCE,
            "samples": params["bootstrap_samples"],
            "seed": params["seed"],
        },
        "runs": runs,
        "definitions": {
            "pnl": "E(d) - E(d-1), the first day from initial capital; rows incl. unlabelled "
            "sum to the last equity point minus initial capital",
            "exposure": "share of the bucket's days with a position carried in from the "
            "previous close, or opened and closed within the day",
            "benchmark": f"{read.version.reference} close-to-close return from catalog "
            f"'{read.version.catalog}' on the bucket's days, annualised x252 (no dividends "
            "unless the catalog's bars fold them in)",
            "drift_return": "benchmark daily return x the side held into each day (+1 long, "
            "-1 short, 0 flat), mean x252; position size is not scaled",
            "trades": "closed trades by the label in force on their entry day; open apart",
        },
    }


def _store(payload: dict[str, Any], read: _Read, label_set: str) -> dict[str, Any]:
    report_id = uuid4()
    body = to_jsonable(payload)
    row = RegimeReport(
        report_id=report_id,
        kind="profile",
        run_ids=body["params"]["run_ids"],
        calendar_version_ids=[{"version_id": read.version.id, "build_no": read.build}],
        labels_hash=read.labels_hash,
        label_set=label_set,
        hindsight=payload["hindsight"],
        params={**body["params"], "overrides": body["overrides"]},
        result=body,
        git_commit=git_output(REPO_ROOT, "rev-parse", "HEAD"),
    )
    with get_sync_session() as session:
        SyncRegimeReportRepository(session).add_report(row)
    return {"report_id": str(report_id), **body}
