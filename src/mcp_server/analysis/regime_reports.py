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
    DRIFT_SCALINGS,
    REPORT_VERSIONS,
    Calendar,
    TradeRecord,
    bucket_name,
    daily_frame,
    families_of,
    labels_in_force,
    profile,
)
from src.core.regimes.bootstrap import CONFIDENCE, MEAN_BLOCK
from src.core.regimes.definition import (
    LABEL_SETS,
    bar_hash,
    build_definition,
    definition_hash,
)
from src.core.regimes.trade_view import correlation
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.models.regime import RegimeCalendarVersion, RegimeReport
from src.db.models.trade import Trade
from src.db.repositories.regime_repository_sync import (
    SyncRegimeReportRepository,
    SyncRegimeRepository,
)
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.regime_calendar import (
    REBUILD_FIX,
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
    "drift_scaling": "side",
    "report_version": 1,
}
DETECTOR_LAG = 1
#: Selectors, not settings: choosing the label set or a snapshot to rerun is no override.
#: ``drift_scaling`` changes no label, bucket or interval, only the drift column, so it is
#: recorded in ``params`` but declares no robustness check.
#: ``report_version`` 2 adds the Q16 stop-rule reading and moves no number either.
_NOT_OVERRIDES = ("label_set", "as_of_build", "drift_scaling", "report_version")
_STOP_RULE = (
    "report_version 2 (design.md Q16, Allay 2026-11-27): verdict over the single "
    "ground-truth families (leg scales, ranging, volatility): dependence if any is "
    "strong_dependence, else insufficient_evidence if any is, else no_dependence. The "
    "cross (scale x volatility, scale picked from the data) is exploratory, a lead for "
    "phase 4, and never counts"
)
_SIDE_DRIFT = (
    "benchmark daily return x the side held into each day (+1 long, -1 short, 0 flat), "
    "mean x252; position size is not scaled"
)
_POSITION_DRIFT = {
    "drift_return": "benchmark daily return x the position weight carried into each day, "
    "mean x252 (drift_scaling='position')",
    "mean_position_weight": "signed quantity carried in from the previous close x the "
    "reference's previous close / the previous equity, mean over the bucket's days (+0.10 = "
    "long 10% of equity; flat days count as 0). The quantity is the stored trade's, its peak "
    "for a position scaled in or out, which overweights its smaller days",
}


@dataclass(frozen=True)
class _Read:
    """The calendar snapshot a report reads."""

    version: RegimeCalendarVersion
    build: int
    calendar: Calendar
    labels_hash: str
    #: Each session's stored OHLC fingerprint: the benchmark closes are checked against it.
    bar_hashes: dict[date, str]


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


def _check_report_version(report_version: int) -> None:
    if report_version not in REPORT_VERSIONS:
        raise ToolFailure(
            "invalid_report_version",
            f"Unknown report_version {report_version}.",
            fix="Use 1 (the default, phase 2) or 2 (adds the Q16 stop-rule reading).",
        )


def _check_drift_scaling(drift_scaling: str) -> None:
    if drift_scaling not in DRIFT_SCALINGS:
        raise ToolFailure(
            "invalid_drift_scaling",
            f"Unknown drift_scaling '{drift_scaling}'.",
            fix="Use 'side' (the default, phase 2) or 'position' (weighted by exposure).",
        )


#: Instruments whose quantity x close is the position's USD notional (an instrument row in
#: ``catalog_instruments`` with one of these classes, multiplier 1 or none, USD or no quote
#: currency). Futures (the multiplier is not applied to a trade's quantity), FX (quoted in
#: another currency, levered) and anything without a row are refused, whatever the catalog
#: is called.
_SCALABLE_CLASSES = ("ETF", "STOCK", "CRYPTO")


def _unscalable(session: Session, ticker: str, catalog: str) -> str | None:
    """Why ``ticker`` in ``catalog`` cannot be position-scaled, or ``None`` when it can."""
    row = session.scalars(
        select(CatalogInstrument).where(
            CatalogInstrument.ticker == ticker, CatalogInstrument.catalog_name == catalog
        )
    ).one_or_none()
    if row is None:
        return f"no instrument row for {ticker} in '{catalog}'"
    multiplier = 1 if row.multiplier is None else float(row.multiplier)
    if row.asset_class.upper() not in _SCALABLE_CLASSES or multiplier != 1:
        return f"{ticker} in '{catalog}' is {row.asset_class} (multiplier {multiplier:g})"
    if (row.currency or "USD").upper() != "USD":
        return f"{ticker} in '{catalog}' is quoted in {row.currency}"
    return None


def _check_scalable(session: Session, sleeve: Sleeve, reference: str, catalog: str) -> None:
    """Position-scaled drift marks the carried quantity at the reference's close, so the
    reference must be the instrument traded, an unlevered, USD-quoted one, in the calendar's
    catalog and in the run's own."""
    symbol = sleeve.run.instrument_symbol.upper()
    source = sleeve.run.data_source or ""
    reasons = [] if symbol == reference else [f"the run trades {symbol}, not {reference}"]
    if not source.startswith("catalog:"):  # its own instrument record cannot be checked
        reasons.append(f"its data source '{source}' has no instrument record to verify")
    catalogs = [catalog] + ([source.split(":", 1)[1]] if source.startswith("catalog:") else [])
    reasons += [r for c in dict.fromkeys(catalogs) if (r := _unscalable(session, reference, c))]
    if reasons:
        raise ToolFailure(
            "drift_scaling_unavailable",
            f"Run {sleeve.run_id} cannot be position-scaled against {reference} in "
            f"'{catalog}': {'; '.join(reasons)}. Quantity x close must be its USD notional.",
            fix="Profile it against its own symbol's calendar (an ETF, stock or USD crypto "
            "catalog), or leave drift_scaling unset.",
        )


def _check_sized(sleeve: Sleeve, trades: list[TradeRecord]) -> None:
    unsized = sum(1 for t in trades if not t.quantity > 0)
    if unsized:
        raise ToolFailure(
            "drift_scaling_unavailable",
            f"Run {sleeve.run_id} has {unsized} trade(s) with no quantity, so its position "
            "weight is not known.",
            fix="Leave drift_scaling unset (side drift needs no quantity).",
        )


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
    return _Read(version, build, calendar, digest, {r.session_date: r.bar_hash for r in rows})


def _benchmark_closes(bars: pd.DataFrame, read: _Read) -> pd.Series:
    """The reference's closes up to the build's ``data_end``, refused unless every session
    matches the calendar's stored bar fingerprint, so a rerun as of a recorded build can
    never read a changed historical close while its ``labels_hash`` still matches."""
    inside = bars[bars.index <= read.calendar.data_end]
    start = min(read.bar_hashes) if read.bar_hashes else read.calendar.data_end
    inside = inside[inside.index >= start]
    found = {
        day: bar_hash(*row)
        for day, row in zip(
            inside.index, inside[["open", "high", "low", "close"]].itertuples(False)
        )
    }
    differing = sorted(
        day
        for day in set(found) | set(read.bar_hashes)
        if found.get(day) != read.bar_hashes.get(day)
    )
    if differing:
        raise ToolFailure(
            "reference_bars_changed",
            f"{read.version.reference}'s bars in '{read.version.catalog}' no longer match "
            f"calendar v{read.version.version} ({len(differing)} session(s), first "
            f"{differing[0]}); the benchmark return would not reproduce.",
            fix=REBUILD_FIX,
            details={"first_date": differing[0], "sessions": len(differing)},
        )
    return inside["close"]


def _trades(session: Session, sleeve: Sleeve) -> list[TradeRecord]:
    stmt = (
        select(
            Trade.entry_timestamp, Trade.exit_timestamp, Trade.order_side, Trade.profit_loss,
            Trade.quantity,
        )
        .where(Trade.backtest_run_id == sleeve.run.id)
        .order_by(Trade.entry_timestamp, Trade.id)
    )  # fmt: skip
    return [
        TradeRecord(entry, exit_, side, float(pnl or 0), abs(float(qty or 0)))
        for entry, exit_, side, pnl, qty in session.execute(stmt).all()
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
        drift_scaling=params.get("drift_scaling", DEFAULTS["drift_scaling"]),
        report_version=params.get("report_version", DEFAULTS["report_version"]),
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
    extra = {"stop_rule": result["stop_rule"]} if "stop_rule" in result else {}
    return {**fields, "families": result["families"], **extra, "notes": notes}, frame


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
    drift_scaling: str = "side",
    report_version: int = 1,
) -> dict[str, Any]:
    """Profile runs against one stored calendar version; store and return the report."""
    lag = _resolve_lag(label_set, lag_days)
    _check_params(min_days, min_trades, bootstrap_samples, seed)
    _check_drift_scaling(drift_scaling)
    _check_report_version(report_version)
    reference = benchmark_symbol.strip().upper()
    given = {
        "label_set": label_set, "benchmark_symbol": reference, "catalog": catalog,
        "lag_days": lag_days, "min_days": min_days, "min_trades": min_trades,
        "bootstrap_samples": bootstrap_samples, "seed": seed,
        "calendar_version": calendar_version, "as_of_build": as_of_build,
    }  # fmt: skip
    if drift_scaling != DEFAULTS["drift_scaling"]:  # absent by default: phase 2 reproduces
        given["drift_scaling"] = drift_scaling
    if report_version != DEFAULTS["report_version"]:  # absent by default, as above
        given["report_version"] = report_version
    with get_sync_session() as session:
        sleeves = load_sleeves(session, run_ids, minimum=1, maximum=MAX_RUNS)
        if drift_scaling == "position":  # refuse before any calendar or catalog read
            for sleeve in sleeves:
                _check_scalable(session, sleeve, reference, catalog)
        inputs = {s.run_id: (_run_fields(s), _trades(session, s)) for s in sleeves}
        if drift_scaling == "position":
            for sleeve in sleeves:
                _check_sized(sleeve, inputs[sleeve.run_id][1])
        read = _read_calendar(
            SyncRegimeRepository(session), (reference, catalog, label_set),
            calendar_version, as_of_build,
        )  # fmt: skip
    closes = _benchmark_closes(load_reference_bars(catalog, reference), read)
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
    return (
        payload
        | {
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
            "definitions": _definitions(read, params),
        }
        | _scaling(params)
        | _version(params)
    )


def _definitions(read: _Read, params: dict) -> dict[str, str]:
    definitions = {
        "pnl": "E(d) - E(d-1), the first day from initial capital; rows incl. unlabelled "
        "sum to the last equity point minus initial capital",
        "exposure": "share of the bucket's days with a position carried in from the "
        "previous close, or opened and closed within the day",
        "benchmark": f"{read.version.reference} close-to-close return from catalog "
        f"'{read.version.catalog}' on the bucket's days, annualised x252 (no dividends "
        "unless the catalog's bars fold them in)",
        "drift_return": _SIDE_DRIFT,
        "trades": "closed trades by the label in force on their entry day; open apart",
    }
    definitions |= _POSITION_DRIFT if _scaling(params) else {}
    return definitions | ({"stop_rule": _STOP_RULE} if _version(params) else {})


def _version(params: dict) -> dict[str, Any]:
    """The top-level marker of a version 2 report; nothing for the default."""
    version = params.get("report_version", DEFAULTS["report_version"])
    return {} if version == DEFAULTS["report_version"] else {"report_version": version}


def _scaling(params: dict) -> dict[str, Any]:
    """The top-level marker of a position-scaled report; nothing for the default."""
    scaling = params.get("drift_scaling", DEFAULTS["drift_scaling"])
    return {} if scaling == DEFAULTS["drift_scaling"] else {"drift_scaling": scaling}


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
