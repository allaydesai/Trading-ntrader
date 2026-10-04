"""Run provenance: git state of the code and a hash of the run's configuration.

``compute_config_hash`` is the single definition of "the same run": the
orchestrator stamps it on every persisted run and the research MCP uses it to
report a request's hash before running, so the two always agree.
"""

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.models.backtest_request import BacktestRequest
from src.models.run_provenance import RunProvenance

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES_SUBMODULE = Path("src") / "core" / "strategies" / "custom"
_GIT_TIMEOUT_S = 5


def _canonical(value: Any) -> Any:
    """Normalise a value so equal configurations serialise identically."""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def compute_config_hash(request: BacktestRequest) -> str:
    """Return the sha256 hex digest of the fields that define a run's result.

    Execution options (``persist``, ``config_file_path``) are excluded: they do
    not change what the engine computes.
    """
    payload = {
        "strategy_path": request.strategy_path,
        "strategy_config": request.strategy_config,
        "symbol": request.symbol,
        "instrument_id": request.instrument_id,
        "bar_type": request.bar_type,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "starting_balance": request.starting_balance,
        "data_source": request.to_persistence_data_source(),
        "fill_seed": request.fill_seed,
    }
    encoded = json.dumps(_canonical(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def git_output(cwd: Path, *args: str) -> str | None:
    """Stdout of a git command run in ``cwd``; None when git fails or is missing."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=_GIT_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_provenance(repo_root: Path = REPO_ROOT) -> RunProvenance:
    """Read HEAD, dirty state and the custom-strategies submodule HEAD."""
    if git_output(repo_root, "rev-parse", "--show-toplevel") is None:
        return RunProvenance()
    status = git_output(repo_root, "status", "--porcelain")
    submodule = repo_root / STRATEGIES_SUBMODULE
    strategies_commit = git_output(submodule, "rev-parse", "HEAD") if submodule.is_dir() else None
    return RunProvenance(
        git_commit=git_output(repo_root, "rev-parse", "HEAD"),
        git_dirty=None if status is None else bool(status),
        strategies_commit=strategies_commit,
    )


def run_provenance(request: BacktestRequest, repo_root: Path = REPO_ROOT) -> RunProvenance:
    """Git provenance plus the request's config hash."""
    return replace(git_provenance(repo_root), config_hash=compute_config_hash(request))
