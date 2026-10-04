"""Read persisted runs: detail with units, and side-by-side comparison (S3.3)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.core.benchmarks import BENCHMARKS
from src.db.models.backtest import BacktestRun
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.metrics import METRIC_SPECS


def parse_run_ids(run_ids: list[str], *, minimum: int, maximum: int) -> list[str]:
    """Validate and de-duplicate run ids, keeping their order.

    Ids come back in canonical form, the form rows are keyed by, so an
    upper-case or un-hyphenated spelling still matches its run.
    """
    canonical = []
    for run_id in run_ids:
        try:
            canonical.append(str(UUID(run_id.strip())))
        except ValueError:
            raise ToolFailure(
                "invalid_run_id",
                f"'{run_id}' is not a run id.",
                fix="Use the UUID get_job returns.",
            ) from None
    unique = list(dict.fromkeys(canonical))
    if len(unique) < minimum:
        raise ToolFailure("too_few_runs", f"Give at least {minimum} run ids.", fix="Add runs.")
    if len(unique) > maximum:
        raise ToolFailure(
            "too_many_runs", f"At most {maximum} runs at once.", fix="Compare fewer runs."
        )
    return unique


MARK_TO_MARKET = "mark_to_market"


def _metrics_basis(run: Any) -> str:
    """What the run's risk metrics were computed from; runs before the field are realised."""
    return (run.config_snapshot or {}).get("metrics_basis") or "realised"


def _metric_values(run: Any) -> dict[str, Any]:
    if run.metrics is None:
        return {}
    return {name: getattr(run.metrics, name) for name in METRIC_SPECS}


def run_view(run: Any) -> dict[str, Any]:
    """One run: identity, window, provenance, parameters and metrics with units."""
    warnings = []
    if run.git_dirty:
        warnings.append("Run was made from uncommitted code (git_dirty); treat as provisional.")
    if run.execution_status != "success":
        warnings.append(f"Run failed: {run.error_message}")
    elif _metrics_basis(run) != MARK_TO_MARKET:
        warnings.append(
            "Risk metrics are on the realised basis (run predates mark-to-market): drawdown, "
            "Sharpe, Sortino and volatility ignore moves inside open trades. Re-run to compare."
        )
    snapshot = run.config_snapshot or {}
    metrics = {
        name: {"value": value, "unit": METRIC_SPECS[name].unit}
        for name, value in _metric_values(run).items()
    }
    return to_jsonable(
        {
            "run_id": run.run_id,
            "kind": "benchmark" if run.strategy_type in BENCHMARKS else "strategy",
            "strategy": run.strategy_type,
            "symbol": run.instrument_symbol,
            "start": run.start_date,
            "end": run.end_date,
            "bar_type": snapshot.get("bar_type"),
            "initial_capital": run.initial_capital,
            "data_source": run.data_source,
            "status": run.execution_status,
            "duration_s": run.execution_duration_seconds,
            "created_at": run.created_at,
            "run_type": run.run_type,
            "reproduced_from_run_id": run.reproduced_from_run_id,
            "metrics_basis": _metrics_basis(run),
            "provenance": {
                "git_commit": run.git_commit,
                "git_dirty": run.git_dirty,
                "strategies_commit": run.strategies_commit,
                "config_hash": run.config_hash,
            },
            "params": snapshot.get("config", {}),
            "metrics": metrics,
            "warnings": warnings,
        }
    )


def _best(runs: list[Any]) -> dict[str, Any]:
    best: dict[str, Any] = {}
    for name, spec in METRIC_SPECS.items():
        if spec.direction is None:
            continue
        values = {
            str(r.run_id): float(v) for r in runs if (v := _metric_values(r).get(name)) is not None
        }
        # A metric some run lacks has no winner: a lone value would be marked best by default.
        if len(values) < len(runs):
            continue
        pick = max if spec.direction == "higher" else min
        top = pick(values.values())
        best[name] = {"value": top, "run_ids": [k for k, v in values.items() if v == top]}
    return best


def compare_view(runs: list[Any], *, requested: list[str]) -> dict[str, Any]:
    """Rows in requested order, the parameters that differ, and the best run per metric."""
    by_id = {str(r.run_id): r for r in runs}
    ordered = [by_id[r] for r in requested if r in by_id]
    params = [(r.config_snapshot or {}).get("config", {}) for r in ordered]
    keys = sorted({k for p in params for k in p})
    differing = [k for k in keys if len({repr(p.get(k)) for p in params}) > 1]
    rows = []
    for run, run_params in zip(ordered, params):
        view = run_view(run)
        view["params"] = to_jsonable({k: run_params.get(k) for k in differing})
        view["metrics"] = {k: v["value"] for k, v in view["metrics"].items()}
        rows.append(view)
    warnings = []
    if len({(r.instrument_symbol, r.start_date, r.end_date) for r in ordered}) > 1:
        warnings.append("Runs cover different symbols or windows; compare with care.")
    if len({_metrics_basis(r) for r in ordered}) > 1:
        warnings.append(
            "Runs compute risk metrics on different bases (see metrics_basis); drawdown, "
            "Sharpe, Sortino and volatility are not comparable across them."
        )
    return {
        "rows": rows,
        "differing_params": differing,
        "best": _best(ordered),
        "missing": [r for r in requested if r not in by_id],
        "units": {name: spec.unit for name, spec in METRIC_SPECS.items()},
        "warnings": warnings,
    }


def get_run(run_id: str) -> dict[str, Any]:
    """A run by id, or a failure saying where to find run ids."""
    (run_id,) = parse_run_ids([run_id], minimum=1, maximum=1)
    with get_sync_session() as session:
        run = SyncBacktestRepository(session).find_by_run_id(UUID(run_id))
        if run is None:
            raise ToolFailure(
                "unknown_run",
                f"No run {run_id}.",
                fix="Use a run_id from get_job, or `backtest history`.",
            )
        return run_view(run)


def compare_runs(run_ids: list[str], *, maximum: int) -> dict[str, Any]:
    """Compare 2..maximum runs side by side."""
    ids = parse_run_ids(run_ids, minimum=2, maximum=maximum)
    with get_sync_session() as session:
        runs = SyncBacktestRepository(session).find_by_run_ids([UUID(r) for r in ids])
        return compare_view(runs, requested=ids)


def find_saved_run(config_hash: str, since: datetime) -> str | None:
    """The newest successful run with ``config_hash`` created at or after ``since``.

    How a job whose worker was killed after its commit, but before it could
    write its result, is recognised as having saved its run.
    """
    stmt = (
        select(BacktestRun.run_id)
        .where(
            BacktestRun.config_hash == config_hash,
            BacktestRun.created_at >= since,
            BacktestRun.execution_status == "success",
        )
        .order_by(BacktestRun.created_at.desc())
        .limit(1)
    )
    with get_sync_session() as session:
        run_id = session.scalars(stmt).first()
    return str(run_id) if run_id else None
