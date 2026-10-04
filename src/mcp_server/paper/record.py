"""Export a paper session's reading to the vault beside the run it is compared against (S8.1).

``<slug>.session.json`` holds ``get_session``'s whole report plus every trade;
``<slug>.session.trades.csv`` the session's trades in the runner's layout; the
compare-to run goes to the usual ``<slug>.json`` and ``<slug>.trades.csv``.
"""

import csv
import json
import os
from pathlib import Path
from typing import Any

from src.mcp_server.export import export_results, export_target, refuse_existing
from src.mcp_server.jobs.store import utc_now
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.paper import reads
from src.mcp_server.paper.sessions import get_session
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


def export_session(
    settings: McpSettings, key: str, *, slug: str, folder: str, overwrite: bool
) -> dict[str, Any]:
    """Write the session's report and trades, and its compare-to run, to the vault."""
    target = export_target(settings, folder, slug, overwrite=overwrite)
    json_path = target.json_path.with_name(f"{slug}.session.json")
    csv_path = target.json_path.with_name(f"{slug}.session.trades.csv")
    refuse_existing([json_path, csv_path], slug=slug, folder=folder, overwrite=overwrite)
    report = get_session(settings, key, recent=0)
    with reads.read_only_session() as db:
        trades = reads.trade_rows(db, reads.resolve_session(db, key).id)
    linked = report["link"]["compare_to"]
    out: dict[str, Any] = (
        export_results(settings, [linked], slug, folder, overwrite)
        if linked
        else {"files": {}, "runs": 0, "trades": 0, "missing": []}
    )
    record = {"kind": "paper-session", "source": "ntrader-mcp", "exported_at": utc_now()}
    payload = json.dumps(to_jsonable({**record, **report, "trades": trades}), indent=2)
    _write(json_path, lambda tmp: tmp.write_text(payload, encoding="utf-8"))
    if trades:
        _write(csv_path, _csv(trades))
    else:
        csv_path.unlink(missing_ok=True)
    files = {**out["files"], "session": str(json_path)}
    files["session_trades_csv"] = str(csv_path) if trades else None
    return {
        **out,
        "files": files,
        "session": report["session"]["name"],
        "session_trades": len(trades),
    }
