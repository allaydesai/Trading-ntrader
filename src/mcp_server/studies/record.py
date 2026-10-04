"""Export a study's record to the vault beside its runs (S8.1).

``<slug>.study.json`` holds the study, its numbered ledger, candidates, every
recorded decision, the current scorecard and one scorecard per frozen version,
so the card a decision was made on survives the next version; the runs
themselves go to the usual ``<slug>.json`` and ``<slug>.trades.csv``.
"""

import json
import os
from typing import Any

from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import export_results, export_target, refuse_existing
from src.mcp_server.jobs.store import JobStore, utc_now
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies import lifecycle, scorecard


def export_study(
    settings: McpSettings,
    store: JobStore,
    key: str,
    *,
    run_ids: list[str] | None,
    slug: str,
    folder: str,
    overwrite: bool,
) -> dict[str, Any]:
    """Write the study record, plus its completed runs unless run ids are given."""
    target = export_target(settings, folder, slug, overwrite=overwrite)
    path = target.json_path.with_name(f"{slug}.study.json")
    refuse_existing([path], slug=slug, folder=folder, overwrite=overwrite)
    study = lifecycle.get_study(store, key)["study"]
    try:
        card: dict[str, Any] = scorecard.get_scorecard(settings, store, key, None)
    except ToolFailure as failure:
        card = {"status": "missing", "reason": failure.message}
    cards = [
        scorecard.get_scorecard(settings, store, key, c["version"]) for c in study["candidates"]
    ]
    ids = run_ids or [r["run_id"] for r in study["ledger"] if r["state"] == "completed"]
    out: dict[str, Any] = (
        export_results(settings, ids, slug, folder, overwrite)
        if ids
        else {"files": {}, "runs": 0, "trades": 0, "missing": []}
    )
    record = {"kind": "study-record", "source": "ntrader-mcp", "exported_at": utc_now()}
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(
            json.dumps(
                {**record, "study": study, "scorecard": card, "scorecards": cards}, indent=2
            ),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return {**out, "files": {**out["files"], "study": str(path)}, "study": study["slug"]}
