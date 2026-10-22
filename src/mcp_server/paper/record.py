"""Export a paper session's reading to the vault beside the run it is compared against (S8.1).

One reading per day, so the weekly checks accumulate instead of colliding:
``<slug>.<date>.session.json`` holds ``get_session``'s whole report plus every
trade, ``<slug>.<date>.session.trades.csv`` the session's trades in the runner's
layout. The compare-to run goes to the usual ``<slug>.json`` and
``<slug>.trades.csv`` once: it does not change, so a later reading leaves it be.
"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.mcp_server.export import export_results, refuse_existing, vault_directory
from src.mcp_server.jobs.store import utc_now
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.paper import reads
from src.mcp_server.paper.report import session_report
from src.mcp_server.paper.sessions import g4_thresholds
from src.mcp_server.settings import McpSettings


def _write(path: Path, write) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _csv(trades: list[dict[str, Any]]):
    def write(tmp: Path) -> None:
        with tmp.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(trades[0].keys()))
            writer.writeheader()
            writer.writerows(to_jsonable(trades))

    return write


def _run_files(settings: McpSettings, linked, *, slug: str, folder: str, overwrite: bool):
    """Export the compare-to run, unless an earlier reading already filed it."""
    empty: dict[str, Any] = {"files": {}, "runs": 0, "trades": 0, "missing": []}
    if not linked:
        return empty
    directory = vault_directory(settings, folder, slug)
    json_path, csv_path = directory / f"{slug}.json", directory / f"{slug}.trades.csv"
    if json_path.exists() and not overwrite:
        files = {"json": str(json_path), "trades_csv": str(csv_path) if csv_path.exists() else None}
        return {**empty, "files": files, "run_already_filed": True}
    return export_results(settings, [linked], slug, folder, True)


def export_session(
    settings: McpSettings,
    key: str,
    *,
    slug: str,
    folder: str,
    overwrite: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write the session's dated report and trades, and its compare-to run, to the vault."""
    now = now or datetime.now(timezone.utc)
    directory = vault_directory(settings, folder, slug)
    stem = f"{slug}.{now.date().isoformat()}.session"
    json_path, csv_path = directory / f"{stem}.json", directory / f"{stem}.trades.csv"
    refuse_existing([json_path, csv_path], slug=stem, folder=folder, overwrite=overwrite)
    with reads.read_only_session() as db:  # one snapshot: the report and the trades agree
        row = reads.resolve_session(db, key)
        report = to_jsonable(session_report(db, row, g4=g4_thresholds(settings), now=now, recent=0))
        trades = reads.trade_rows(db, row.id)
    record = {"kind": "paper-session", "source": "ntrader-mcp", "exported_at": utc_now()}
    payload = json.dumps(to_jsonable({**record, **report, "trades": trades}), indent=2)
    _write(json_path, lambda tmp: tmp.write_text(payload, encoding="utf-8"))
    if trades:
        _write(csv_path, _csv(trades))
    else:
        csv_path.unlink(missing_ok=True)
    linked = report["link"]["compare_to"]
    out = _run_files(settings, linked, slug=slug, folder=folder, overwrite=overwrite)
    files = {**out["files"], "session": str(json_path)}
    files["session_trades_csv"] = str(csv_path) if trades else None
    return {
        **out,
        "files": files,
        "session": report["session"]["name"],
        "session_trades": len(trades),
    }
