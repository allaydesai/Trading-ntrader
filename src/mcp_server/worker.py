"""Worker process: run one backtest job, then exit (``python -m src.mcp_server.worker DIR``).

Started by ``JobRunner`` with stdout and stderr pointed at the job's log, in a
fresh interpreter, so the Nautilus engine, its log guard and its output live
and die with this process. Same code path as ``backtest run --catalog``: the
named-catalog loader (fails fast: no IBKR fetch, no fake instrument), then
``BacktestOrchestrator``, which persists the run with its provenance.

Writes ``progress.json`` as it moves through phases and ``result.json`` when
done. Exit 0 only when the run is verified to be in the database.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import utc_now
from src.mcp_server.request import BacktestSpec, ResolvedRequest, resolve
from src.services.exceptions import DataNotFoundError, UnknownCatalogError

HEADLINE_METRICS = (
    "total_return",
    "cagr",
    "sharpe_ratio",
    "max_drawdown",
    "total_trades",
    "win_rate",
    "final_balance",
)


class _Job:
    """The job directory, as the worker sees it."""

    def __init__(self, job_dir: Path) -> None:
        self.dir = job_dir

    def request(self) -> dict[str, Any]:
        return json.loads((self.dir / "request.json").read_text(encoding="utf-8"))

    def phase(self, name: str) -> None:
        self._write("progress.json", {"phase": name, "at": utc_now()})
        print(f"[ntrader-mcp worker] phase={name}", flush=True)

    def finish(self, result: dict[str, Any]) -> None:
        self._write("result.json", {**result, "finished_at": utc_now()})

    def _write(self, name: str, data: dict[str, Any]) -> None:
        tmp = self.dir / f"{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.dir / name)


async def _load(resolved: ResolvedRequest):
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session
    from src.mcp_server.catalogs import catalog_manager
    from src.services.firstrate.backtest_loader import load_from_catalog
    from src.services.firstrate.metadata_service import MetadataService

    request = resolved.request
    assert request.catalog_name, "the MCP only runs named-catalog requests"
    with get_sync_session() as session:
        return await load_from_catalog(
            catalog_name=request.catalog_name,
            ticker=request.symbol,
            bar_type_spec=request.bar_type,
            start=request.start_date,
            end=request.end_date,
            catalog_manager=catalog_manager(),
            metadata_service=MetadataService(sync_repo=SyncCatalogInstrumentRepository(session)),
        )


async def _execute(resolved: ResolvedRequest, data) -> UUID:
    from src.core.backtest_orchestrator import BacktestOrchestrator

    orchestrator = BacktestOrchestrator()
    try:
        _, run_id = await orchestrator.execute(resolved.request, data.bars, data.instrument)
    finally:
        orchestrator.dispose()
    if run_id is None:
        raise RuntimeError("The orchestrator returned no run id for a persisted request.")
    return run_id


def _verify(run_id: UUID) -> dict[str, Any]:
    """The persisted run's headline; refuses a run the orchestrator failed to save."""
    from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
    from src.db.session_sync import get_sync_session
    from src.mcp_server.jsonable import to_jsonable

    with get_sync_session() as session:
        run = SyncBacktestRepository(session).find_by_run_id(run_id)
        if run is None or run.metrics is None:
            raise ToolFailure(
                "not_persisted",
                f"Run {run_id} finished but was not saved to the database.",
                fix="Check the database connection; the job log has the persistence warning.",
            )
        headline = {name: getattr(run.metrics, name) for name in HEADLINE_METRICS}
        return to_jsonable({"config_hash": run.config_hash, "headline": headline})


def _failure(code: str, exc: Exception, fix: str) -> dict[str, Any]:
    return {"status": "failed", "error": {"code": code, "message": str(exc), "fix": fix}}


async def run_job(job_dir: Path) -> int:
    """Run the job in ``job_dir``; returns the process exit code."""
    job = _Job(job_dir)
    try:
        job.phase("resolving")
        spec = BacktestSpec(**job.request()["spec"])
        resolved = resolve(spec, default_catalog="")
        job.phase("loading_data")
        data = await _load(resolved)
        job.phase("running")
        run_id = await _execute(resolved, data)
        job.phase("verifying")
        verified = _verify(run_id)
        job.phase("done")
        job.finish({"status": "ok", "run_id": str(run_id), **verified})
        return 0
    except ToolFailure as failure:
        job.finish({"status": "failed", **failure.to_dict()})
        return 2
    except DataNotFoundError as exc:
        fix = "Check catalog_availability for this symbol and timeframe."
        job.finish(_failure("data_not_found", exc, fix))
        return 1
    except UnknownCatalogError as exc:
        job.finish(_failure("unknown_catalog", exc, "Use a catalog from list_catalogs."))
        return 1
    except Exception as exc:
        job.finish(_failure("backtest_failed", exc, "Read log_tail in get_job for the cause."))
        return 1


def main(argv: list[str]) -> int:
    """Entry point; the job log receives all output."""
    from src.utils.logging import configure_logging

    configure_logging()
    return asyncio.run(run_job(Path(argv[1])))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
