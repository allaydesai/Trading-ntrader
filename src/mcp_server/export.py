"""Export runs to the Trading Research vault (S8.1).

The files match what the vault's ``Lab/queue`` runner writes (``System/Runner.md``:
"two run layers, same results format"): ``<slug>.json`` with git state and, per
run, its fields, ``config_snapshot``, all metrics and ``trade_stats``; and
``<slug>.trades.csv`` with every trade. ``trade_stats`` is ported from
``Lab/runner/run_queue.py`` so both layers compute it the same way.

Writes go only to the vault folders listed in ``NTRADER_MCP_EXPORT_FOLDERS``,
resolved through symlinks and checked to stay inside the vault.
"""

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import utc_now
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.runs import parse_run_ids
from src.mcp_server.settings import McpSettings
from src.services.provenance import REPO_ROOT, STRATEGIES_SUBMODULE, git_output

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
TRADE_COLUMNS = (
    "instrument_id",
    "order_side",
    "quantity",
    "entry_price",
    "exit_price",
    "commission_amount",
    "fees_amount",
    "profit_loss",
    "profit_pct",
    "holding_period_seconds",
    "entry_timestamp",
    "exit_timestamp",
)


@dataclass(frozen=True)
class ExportTarget:
    json_path: Path
    csv_path: Path


def export_target(
    settings: McpSettings, folder: str, slug: str, *, overwrite: bool
) -> ExportTarget:
    """Where an export goes, after every check that it may be written there."""
    if settings.vault_path is None:
        raise ToolFailure(
            "vault_not_configured", "No vault is configured.", fix="Set NTRADER_MCP_VAULT_PATH."
        )
    allowed = ", ".join(settings.export_folders)
    if not _SLUG.match(slug):
        raise ToolFailure(
            "invalid_slug",
            f"Slug '{slug}' is not allowed.",
            fix="Use lowercase letters, digits, '.', '_' or '-' (max 81 chars), e.g. rsi2-qqq-01.",
        )
    vault = settings.vault_path.resolve()
    directory = (vault / folder).resolve()
    if folder not in settings.export_folders or not directory.is_relative_to(vault):
        raise ToolFailure(
            "folder_not_allowed", f"Export to '{folder}' is not allowed.", fix=f"Use: {allowed}."
        )
    if not directory.is_dir():
        raise ToolFailure(
            "folder_missing", f"Vault folder '{folder}' does not exist.", fix="Create it first."
        )
    target = ExportTarget(directory / f"{slug}.json", directory / f"{slug}.trades.csv")
    if not overwrite and (target.json_path.exists() or target.csv_path.exists()):
        raise ToolFailure(
            "file_exists",
            f"'{slug}' already exists in '{folder}'.",
            fix="Choose another slug, or pass overwrite=true.",
        )
    return target


def trade_stats(trades: list[dict[str, Any]], run: dict[str, Any]) -> dict[str, Any]:
    """Stats the DB doesn't store: P&L by exit year, exposure, holding time, concentration."""
    closed = [t for t in trades if t.get("exit_timestamp") and t.get("profit_loss") is not None]
    if not closed:
        return {"closed_trades": 0}
    by_year: dict[str, float] = {}
    for t in closed:
        year = str(t["exit_timestamp"].year)
        by_year[year] = by_year.get(year, 0.0) + float(t["profit_loss"])
    pnl = sorted((float(t["profit_loss"]) for t in closed), reverse=True)
    total = sum(pnl)
    hold_days = [
        t["holding_period_seconds"] / 86400 for t in closed if t.get("holding_period_seconds")
    ]
    end = run.get("end_date")
    span = (end - run["start_date"]).total_seconds() / 86400 if end else None
    return {
        "closed_trades": len(closed),
        "pnl_by_exit_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "losing_years": [k for k, v in sorted(by_year.items()) if v < 0],
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 2) if hold_days else None,
        "max_hold_days": round(max(hold_days), 2) if hold_days else None,
        # Time in market ignoring overlap: for single-instrument strategies this is exposure.
        "exposure_pct": round(100 * sum(hold_days) / span, 2) if hold_days and span else None,
        "top5_trades_share_of_pnl_pct": round(100 * sum(pnl[:5]) / total, 1) if total > 0 else None,
        "total_commission": round(sum(float(t["commission_amount"] or 0) for t in closed), 2),
    }


def git_info() -> dict[str, Any]:
    """Repository state in the runner's ``git`` shape."""
    submodule = REPO_ROOT / STRATEGIES_SUBMODULE
    custom_status = git_output(submodule, "status", "--porcelain") if submodule.is_dir() else None
    return {
        "repo": str(REPO_ROOT),
        "commit": git_output(REPO_ROOT, "rev-parse", "HEAD"),
        "branch": git_output(REPO_ROOT, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git_output(REPO_ROOT, "status", "--porcelain")),
        "custom_commit": git_output(submodule, "rev-parse", "HEAD") if submodule.is_dir() else None,
        "custom_dirty": None if custom_status is None else bool(custom_status),
    }


def load_runs(run_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Runs (all columns, metrics, trade_stats) and their trades, in requested order."""
    with get_sync_session() as session:
        found = SyncBacktestRepository(session).find_by_run_ids([UUID(r) for r in run_ids])
        by_id = {str(r.run_id): r for r in found}
        runs, all_trades = [], []
        for run_id in (r for r in run_ids if r in by_id):
            run = by_id[run_id]
            row = {c.name: getattr(run, c.name) for c in BacktestRun.__table__.columns}
            metrics = run.metrics
            row["metrics"] = (
                {c.name: getattr(metrics, c.name) for c in metrics.__table__.columns}
                if metrics
                else {}
            )
            for key in ("id", "backtest_run_id", "created_at"):
                row["metrics"].pop(key, None)
            stmt = (
                select(Trade).where(Trade.backtest_run_id == run.id).order_by(Trade.entry_timestamp)
            )
            trades = [{c: getattr(t, c) for c in TRADE_COLUMNS} for t in session.scalars(stmt)]
            row["trade_stats"] = trade_stats(trades, row)
            row.pop("id", None)
            runs.append(row)
            all_trades += [{**t, "run_id": run_id} for t in trades]
    return runs, all_trades


def write_export(
    target: ExportTarget,
    *,
    slug: str,
    runs: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    missing: list[str],
    git: dict[str, Any],
) -> dict[str, str | None]:
    """Write the JSON and (when there are trades) the CSV; returns the paths written."""
    started = utc_now()
    if trades:
        with target.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(to_jsonable(trades))
    payload = {
        "job": slug,
        "kind": "export-runs",
        "source": "ntrader-mcp",
        "started": started,
        "finished": utc_now(),
        "status": "ok",
        "git": git,
        "runs": runs,
        "missing": missing,
        "trades_csv": target.csv_path.name if trades else None,
    }
    target.json_path.write_text(json.dumps(to_jsonable(payload), indent=2), encoding="utf-8")
    return {"json": str(target.json_path), "trades_csv": str(target.csv_path) if trades else None}


def export_results(
    settings: McpSettings, run_ids: list[str], slug: str, folder: str, overwrite: bool
) -> dict[str, Any]:
    """Export runs to ``<vault>/<folder>/<slug>.json`` and ``.trades.csv``."""
    target = export_target(settings, folder, slug, overwrite=overwrite)
    ids = parse_run_ids(run_ids, minimum=1, maximum=200)
    runs, trades = load_runs(ids)
    if not runs:
        raise ToolFailure("unknown_run", "None of the run ids exist.", fix="Check the run ids.")
    found = {str(r["run_id"]) for r in runs}
    missing = [r for r in ids if r not in found]
    files = write_export(
        target, slug=slug, runs=runs, trades=trades, missing=missing, git=git_info()
    )
    return {"files": files, "runs": len(runs), "trades": len(trades), "missing": missing}
