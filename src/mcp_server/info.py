"""``server_info``: what this server is, what it can reach and what it can do."""

import importlib.metadata
import os
from dataclasses import asdict
from typing import Any

from sqlalchemy import text

from src.db.session_sync import get_sync_engine
from src.mcp_server.catalogs import catalog_manager
from src.mcp_server.context import ServerContext
from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies.gates import load_gates
from src.services.provenance import git_provenance


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _database() -> dict[str, Any]:
    engine = get_sync_engine()
    if engine is None:
        return {"ok": False, "error": "DATABASE_URL is not configured"}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # report, never crash the info tool
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True}


def _catalogs() -> dict[str, Any]:
    try:
        return {"ok": True, "names": catalog_manager().list_catalogs()}
    except ToolFailure as failure:
        return {"ok": False, "error": failure.message}


def _vault(ctx: ServerContext) -> dict[str, Any]:
    vault = ctx.settings.vault_path
    if vault is None:
        return {"configured": False, "fix": "Set NTRADER_MCP_VAULT_PATH to enable export."}
    folders = {
        folder: (vault / folder).is_dir() and os.access(vault / folder, os.W_OK)
        for folder in ctx.settings.export_folders
    }
    return {"configured": True, "path": str(vault), "writable_folders": folders}


def server_info(ctx: ServerContext, capabilities: list[str]) -> dict[str, Any]:
    """Versions, git state, health, job queue, limits and the registered tools."""
    s = ctx.settings
    return {
        "server": "ntrader-research",
        "phase": 3,
        "versions": {p: _version(p) for p in ("nautilus_trader", "mcp", "pydantic")},
        "git": {k: v for k, v in asdict(git_provenance()).items() if k != "config_hash"},
        "database": _database(),
        "catalogs": _catalogs(),
        "default_catalog": s.resolved_default_catalog() or None,
        "jobs": ctx.runner.queue_state() if ctx.runner else {"running": None, "queued": 0},
        "limits": {
            "workers": 1,
            "job_timeout_s": s.job_timeout_s,
            "max_compare": s.max_compare,
            "log_tail_lines": s.log_tail_lines,
        },
        "vault": _vault(ctx),
        "gates": load_gates(ctx.settings).status(),
        "capabilities": sorted(capabilities),
    }
