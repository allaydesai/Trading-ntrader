"""Jobs that are not trials: benchmarks beside a study's runs, and reproductions (S2.3, S2.4).

Neither counts against the budget. A benchmark runs on the study's in-sample
window, or on its out-of-sample window once a candidate is frozen; it may be
buy-and-hold or an incumbent strategy, but never the study's own strategy,
which would be an uncounted trial. A reproduction re-runs a stored config with
its stored fill seed and must resolve to the same config hash.
"""

from datetime import date, timezone
from typing import Any

from src.db.models.research import STUDY_STATUSES, ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.run_reads import load_run, run_catalog
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.strategies import resolve_strategy
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.split import Split, require_in_sample, require_out_of_sample
from src.mcp_server.studies.submit import StudyJob, require_status
from src.mcp_server.tools import build_spec
from src.services.provenance import git_provenance

WINDOWS = ("in_sample", "out_of_sample")
FROZEN = frozenset({"frozen", "tested", "promoted"})
OPEN = frozenset({"exploring"}) | FROZEN


def prepare_benchmark(
    study_key: str,
    *,
    strategy: str,
    params: dict[str, Any],
    symbol: str | None,
    window: str,
    start: date | None,
    end: date | None,
) -> tuple[StudyJob, list[str]]:
    """A benchmark or incumbent run beside the study's runs, on one of its windows."""
    if window not in WINDOWS:
        raise ToolFailure(
            "invalid_window", f"Unknown window '{window}'.", fix="Use in_sample or out_of_sample."
        )
    with get_sync_session() as session:
        study = load_study(SyncResearchRepository(session), study_key)
        statuses = OPEN if window == "in_sample" else FROZEN
        require_status(study, statuses, f"{window.replace('_', '-')} benchmarks")
        name = resolve_strategy(strategy).name
        if name == study.strategy_type:
            raise ToolFailure(
                "study_mismatch",
                f"{name} is the study's own strategy: running it is a trial, not a benchmark.",
                fix="Use submit_backtest with study to run it as a counted trial.",
            )
        split = Split.of(study)
        if window == "in_sample":
            start, end = start or split.is_start, end or split.is_end
            require_in_sample(split, start, end)
        else:
            start, end = start or split.oos_start, end or split.oos_end
            require_out_of_sample(split, start, end)
        spec = build_spec(
            strategy=name,
            symbol=(symbol or study.symbols[0]).strip().upper(),
            start=start,
            end=end,
            timeframe=study.timeframe,
            catalog=study.catalog,
            params=params,
            starting_balance=study.starting_balance,
        )
        slug = study.slug
    payload = service.prepare_backtest(spec, default_catalog=spec.catalog or "")
    payload["study"] = {"slug": slug, "role": "benchmark", "window": window}
    job = StudyJob(study=slug, role="benchmark", payload=payload, statuses=statuses)
    return job, payload["warnings"]


def _reproducible_spec(run: Any) -> Any:
    snapshot = run.config_snapshot or {}
    catalog, bar_type = run_catalog(run), snapshot.get("bar_type")
    problem = None
    if run.execution_status != "success":
        problem = "it did not succeed"
    elif not run.config_hash:
        problem = "it predates config hashes"
    elif catalog is None or not bar_type:
        problem = f"it did not read a named catalog ({run.data_source})"
    if problem or catalog is None or not bar_type:
        raise ToolFailure(
            "not_reproducible",
            f"Run {run.run_id} cannot be reproduced: {problem or 'its data source is unknown'}.",
            fix="Submit the same parameters as a new run instead.",
        )
    return build_spec(
        strategy=run.strategy_type,
        symbol=run.instrument_symbol,
        # Stored as timestamptz and read back in the session's zone: the run's days are UTC.
        start=run.start_date.astimezone(timezone.utc).date(),
        end=run.end_date.astimezone(timezone.utc).date(),
        timeframe=bar_type.removesuffix("-LAST"),
        catalog=catalog,
        params=snapshot.get("config") or {},
        starting_balance=run.initial_capital,
        fill_seed=snapshot.get("fill_seed"),
    )


def _require_same_code_for_holdout(run: Any) -> None:
    now = git_provenance()
    if now.git_commit == run.git_commit and now.git_dirty is False and not run.git_dirty:
        return
    raise ToolFailure(
        "holdout_locked",
        f"Run {run.run_id} is an out-of-sample run; reproducing it with different code "
        "would be a second look at the holdout.",
        fix=f"Check out {run.git_commit} with a clean tree, or use run_out_of_sample with "
        "override_reason (it marks the study contaminated).",
    )


def prepare_reproduction(run_id: str) -> tuple[dict[str, Any], StudyJob | None]:
    """The job payload re-running a stored run, and its ledger entry when it is in a study."""
    with get_sync_session() as session:
        run = load_run(session, run_id)
        spec = _reproducible_spec(run)
        trials = SyncResearchRepository(session).trials_of_runs([run.run_id])
        trial = trials[0] if trials else None
        if trial is not None and trial.role == "out_of_sample":
            _require_same_code_for_holdout(run)
        stored_hash, original = run.config_hash, run.run_id
        study = session.get(ResearchStudy, trial.study_pk) if trial is not None else None
        slug = study.slug if study is not None else None
    payload = service.prepare_backtest(spec, default_catalog=spec.catalog or "")
    if payload["resolved"]["config_hash"] != stored_hash:
        raise ToolFailure(
            "config_changed",
            "The stored config no longer resolves to the same run (a parameter model or "
            "default changed since), so this would not be a reproduction.",
            fix="Submit it as a new run and compare.",
            details={"stored": stored_hash, "now": payload["resolved"]["config_hash"]},
        )
    payload["reproduced_from_run_id"] = str(original)
    if slug is None:
        return payload, None
    payload["study"] = {"slug": slug, "role": "reproduction"}
    job = StudyJob(
        study=slug, role="reproduction", payload=payload, statuses=frozenset(STUDY_STATUSES)
    )
    return payload, job
