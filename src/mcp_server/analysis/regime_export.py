"""File a stored regime report in the vault: ``<slug>.regime.json`` and ``<slug>.regime.md``.

The files are written from the stored ``regime_reports`` row, never recomputed, so they
carry the versions, builds and ``labels_hash`` the reading used. A ground-truth report's
files carry ``hindsight: true`` and the stamp ``"hindsight, not tradeable"`` at the top.
Writes go only to the folders in ``NTRADER_MCP_EXPORT_FOLDERS`` (``vault_directory``).
"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from src.db.repositories.regime_repository_sync import SyncRegimeReportRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import refuse_existing, vault_directory
from src.mcp_server.settings import McpSettings

_DAILY = (
    ("bucket", "Bucket"), ("days", "Days"), ("time_share", "Time"), ("pnl", "P&L"),
    ("pnl_share", "P&L share"), ("ann_return", "Ann. ret"), ("ann_volatility", "Ann. vol"),
    ("sharpe", "Sharpe"), ("worst_day", "Worst day"), ("ci_low", "90% CI low"),
    ("ci_high", "90% CI high"), ("exposure", "Exposure"), ("benchmark_ann_return", "Bench"),
    ("drift_return", "Drift"), ("trades_entered", "Trades in"), ("insufficient", "Insufficient"),
)  # fmt: skip
_DIFFERENCES = (
    ("pair", "Difference"), ("point", "Mean daily"), ("low", "90% CI low"),
    ("high", "90% CI high"), ("excludes_zero", "Excludes 0"),
)  # fmt: skip
_TRADES = (
    ("bucket", "Bucket"), ("trades", "Trades"), ("win_rate", "Win rate"),
    ("avg_win", "Avg win"), ("avg_loss", "Avg loss"), ("expectancy", "Expectancy"),
    ("profit_factor", "PF"),
)  # fmt: skip


def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.6g}" if abs(value) < 1000 else f"{value:,.2f}"
    return str(value)


def _table(rows: list[dict[str, Any]], columns: tuple[tuple[str, str], ...]) -> list[str]:
    lines = ["| " + " | ".join(title for _, title in columns) + " |"]
    lines.append("|" + "---|" * len(columns))
    lines += ["| " + " | ".join(_cell(r.get(k)) for k, _ in columns) + " |" for r in rows]
    return lines


def _family_lines(name: str, family: dict[str, Any]) -> list[str]:
    outcome = family["outcome"]
    title = f"#### {name}" + (f" (scale: {family['scale']})" if "scale" in family else "")
    lines = [title, "", f"Outcome: **{outcome['outcome']}** ({outcome['sufficient']} sufficient)"]
    lines += ["", *_table([*family["rows"], family["unlabelled"], family["total"]], _DAILY), ""]
    differences = [{**d, "pair": f"{d['a']} − {d['b']}"} for d in family["differences"]]
    lines += ["Differences:", "", *_table(differences, _DIFFERENCES), ""]
    view = family["trades"]
    lines += [f"Trades (closed {view['closed_trades']}, open {view['open_trades']}):", ""]
    lines += [*_table([*view["rows"], view["unlabelled"]], _TRADES), ""]
    if "alignment" in family:
        aligned = [{**r, "bucket": r["group"]} for r in family["alignment"]]
        lines += ["Alignment:", "", *_table(aligned, _TRADES), ""]
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    """The stored report as a vault note."""
    lines = []
    if report.get("hindsight"):
        lines += [f"> **{report['stamp']}** (hindsight: true)", ""]
    lines += [
        f"# Regime profile {report['report_id']}",
        "",
        f"- label set: `{report['label_set']}`, lag_days {report['lag_days']}",
        f"- reference: {report['reference']} ({report['catalog']})",
        *[
            f"- calendar version {c['version']} (id {c['version_id']}), build {c['build_no']}, "
            f"data_end {c['data_end']}, definition `{c['definition_hash'][:12]}`"
            for c in report["calendar"]
        ],
        f"- labels_hash `{report['labels_hash']}`",
        f"- overrides: {json.dumps(report['overrides']) if report['overrides'] else 'none'}",
        "",
    ]
    for run in report["runs"]:
        lines += [f"## {run['strategy']} {run['symbol']} `{run['run_id']}`", ""]
        lines += [f"- {note}" for note in run["notes"]] + [""]
        for name, family in run["families"].items():
            lines += _family_lines(name, family)
    return "\n".join(lines) + "\n"


def _stored(report_id: str) -> dict[str, Any]:
    try:
        key = UUID(report_id)
    except ValueError:
        raise ToolFailure(
            "invalid_report", f"'{report_id}' is not a report id.", fix="Use run_regime_"
            "attribution's report_id."
        ) from None  # fmt: skip
    with get_sync_session() as session:
        row = SyncRegimeReportRepository(session).find_report(key)
        if row is None:
            raise ToolFailure(
                "unknown_report", f"No regime report {report_id}.", fix="Run "
                "run_regime_attribution first."
            )  # fmt: skip
        return {
            "report_id": str(row.report_id),
            **row.result,
            "kind": row.kind,
            "calendar_version_ids": row.calendar_version_ids,
            "git_commit": row.git_commit,
            "created_at": row.created_at.isoformat(),
        }


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def export_regime_report(
    settings: McpSettings, report_id: str, *, slug: str, folder: str, overwrite: bool
) -> dict[str, Any]:
    """Write a stored report to ``<vault>/<folder>/<slug>.regime.{json,md}``."""
    directory = vault_directory(settings, folder, slug)
    paths = [directory / f"{slug}.regime.json", directory / f"{slug}.regime.md"]
    refuse_existing(paths, slug=slug, folder=folder, overwrite=overwrite)
    report = _stored(report_id)
    _write(paths[0], json.dumps(report, indent=2))
    _write(paths[1], render_markdown(report))
    return {
        "files": {"json": str(paths[0]), "markdown": str(paths[1])},
        "report_id": report["report_id"],
        "hindsight": report["hindsight"],
    }
