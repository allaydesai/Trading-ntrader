"""Parameter sweeps as counted trials, and the sensitivity map over them (S3.1, S3.2).

A sweep expands a grid (the study's declared space by default, or a
neighbourhood around a parameter set) into in-sample cells, every one an
in-sample trial counted against the budget before anything runs. Cells that
resolve to the same run are submitted once. The sensitivity map reads the
sweep's finished members and scores each cell by its neighbours, so the
recommendation is a plateau centre rather than a lucky spike.
"""

from datetime import datetime, timezone
from typing import Any

from src.core.robustness import grid, sensitivity
from src.db.models.research import ResearchAnalysis, ResearchCandidate, ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.metrics import METRIC_SPECS
from src.mcp_server.request import resolve
from src.mcp_server.studies import ledger
from src.mcp_server.studies.batch import Batch, find_analysis
from src.mcp_server.studies.candidates import TESTABLE, _candidate_spec, current_candidate
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.scorecard import _facts
from src.mcp_server.studies.spec import ParamRange, outside_space
from src.mcp_server.studies.split import Split
from src.mcp_server.studies.submit import require_status
from src.mcp_server.tools import build_spec

#: A sweep is in-sample, so it may run while exploring and still after a freeze: a
#: neighbourhood around a frozen candidate is the evidence G2's neighbourhood check needs.
SWEEP_STATUSES = frozenset({"exploring", "frozen", "tested"})


def _axes(
    study: ResearchStudy, given: dict[str, Any] | None, around: dict[str, Any] | None, pct: float
) -> dict[str, Any]:
    """The grid to run: a given grid, a neighbourhood, or the study's declared space."""
    declared = list(study.param_space)
    if around is not None:
        missing = [n for n in declared if n not in around]
        if not declared or missing:
            raise ToolFailure(
                "invalid_grid",
                "around must give a value for each declared parameter"
                + (f": {', '.join(missing)}." if missing else "; the study declares none."),
                fix="Pass the full parameter set (get_study shows a run's params).",
            )
        return grid.around(around, declared, pct=pct)
    axes = given if given is not None else study.param_space
    if not axes:
        raise ToolFailure(
            "invalid_grid", "No grid: the study declares no parameter space.", fix="Pass grid."
        )
    try:
        return {
            name: ParamRange(**axis).model_dump(exclude_none=True) for name, axis in axes.items()
        }
    except (TypeError, ValueError) as exc:
        raise ToolFailure(
            "invalid_grid", f"grid: {exc}", fix="Each axis is values or min/max/step."
        ) from None


def _spec(study: ResearchStudy, candidate: ResearchCandidate | None, params: dict[str, Any]):
    """An in-sample run of ``params``: the frozen candidate's run with them, once there is one."""
    split = Split.of(study)
    if candidate is not None:
        return _candidate_spec(candidate, split.is_start, split.is_end, changes=params)
    return build_spec(
        strategy=study.strategy_type,
        symbol=study.symbols[0],
        start=split.is_start,
        end=split.is_end,
        timeframe=study.timeframe,
        catalog=study.catalog,
        params=params,
        starting_balance=study.starting_balance,
    )


def _payloads(
    study: ResearchStudy,
    cells: list[dict[str, Any]],
    *,
    candidate: ResearchCandidate | None,
    base: dict[str, Any],
) -> tuple[list[dict], list[dict], list[str]]:
    """One job payload per distinct cell: the first validates data, the rest only resolve.

    A cell changes only its axes: every other parameter comes from ``base`` (the
    ``around`` set) and then the frozen candidate, so the neighbourhood G2 judges
    differs from the candidate's run on the swept axes alone.
    """
    specs = [_spec(study, candidate, {**base, **cell}) for cell in cells]
    first = service.prepare_backtest(specs[0], default_catalog=study.catalog)
    payloads, kept, seen = [], [], set()
    for spec, cell in zip(specs, cells):
        resolved = resolve(spec, default_catalog=study.catalog)
        if resolved.config_hash in seen:
            continue
        seen.add(resolved.config_hash)
        payloads.append(
            {**first, "spec": spec.model_dump(mode="json"), "resolved": resolved.summary()}
        )
        kept.append(cell)
    warnings = list(first["warnings"])
    if len(kept) < len(cells):
        warnings.append(
            f"{len(cells) - len(kept)} cell(s) resolve to the same run as another and were dropped."
        )
    return payloads, kept, warnings


def prepare_sweep(
    study_key: str,
    *,
    grid_axes: dict[str, Any] | None,
    around: dict[str, Any] | None,
    around_pct: float,
    over_budget_reason: str | None,
    max_cells: int,
) -> tuple[Batch, list[str]]:
    """A sweep's cells as prepared in-sample jobs, refused before anything is reserved."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key)
        require_status(study, SWEEP_STATUSES, "sweeps")
        axes = _axes(study, grid_axes, around, around_pct)
        count = grid.size(axes)
        if count > max_cells:
            raise ToolFailure(
                "sweep_too_large",
                f"The grid has {count} cells; the server runs at most {max_cells} per sweep.",
                fix="Coarsen the steps or sweep one axis at a time.",
            )
        candidate = current_candidate(repo, study) if study.status in TESTABLE else None
        cells = grid.expand(axes)
        payloads, kept, warnings = _payloads(study, cells, candidate=candidate, base=around or {})
        outside = {w for c in kept for w in outside_space(study.param_space, c)}
        symbol = payloads[0]["spec"]["symbol"]
        slug, request = study.slug, {"axes": axes, "around": around, "symbol": symbol}
    for payload, cell in zip(payloads, kept):
        payload["study"] = {"slug": slug, "role": "in_sample", "cell": cell}
    batch = Batch(
        study=slug,
        role="in_sample",
        kind="sweep",
        payloads=payloads,
        cells=kept,
        statuses=SWEEP_STATUSES,
        request=request,
        reason=over_budget_reason,
    )
    return batch, warnings + sorted(outside)


def _sweep_of(repo: SyncResearchRepository, study: ResearchStudy, key: str | None, session: Any):
    if key is not None:
        analysis = find_analysis(session, key)
        if analysis.study_pk != study.id or analysis.kind != "sweep":
            raise ToolFailure(
                "unknown_analysis",
                f"{key} is not a sweep of '{study.slug}'.",
                fix="get_study lists them.",
            )
        return analysis
    sweeps = repo.analyses(study, kind="sweep")
    if not sweeps:
        raise ToolFailure(
            "no_sweep", f"Study '{study.slug}' has no sweep yet.", fix="submit_sweep first."
        )
    return sweeps[-1]


def run_sensitivity(store: JobStore, study_key: str, *, metric: str, sweep: str | None) -> dict:
    """The sensitivity map of a sweep's finished cells, stored as an analysis of the study."""
    spec = METRIC_SPECS.get(metric)
    if spec is None or spec.unit == "datetime":
        raise ToolFailure(
            "invalid_metric", f"No numeric metric '{metric}'.", fix="e.g. sharpe_ratio."
        )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key, for_update=True)
        ledger.settle(repo, store, study)
        source = _sweep_of(repo, study, sweep, session)
        trials = repo.trials_of_analysis(source)
        cell_of = source.request.get("cells") or {}
        cells = [cell_of[t.job_id] for t in trials]
        values: dict[int, float | None] = {}
        for i, trial in enumerate(trials):
            facts = _facts(session, trial.run_id) if trial.state == "completed" else None
            values[i] = None if facts is None else facts.metrics.get(metric)
        result = sensitivity.sensitivity_map(
            cells, values, list(source.request["axes"]), metric=metric
        )
        for row in result["cells"]:
            trial = trials[row["index"]]
            row["run_id"], row["job_id"] = str(trial.run_id), trial.job_id
        pending = sum(1 for t in trials if t.state == "pending")
        if pending:
            result["note"] = f"{pending} cell(s) are still running: the map is partial."
        stored = repo.add_analysis(
            ResearchAnalysis(
                study_pk=study.id,
                version=source.version,
                kind="sensitivity",
                status="completed",
                request={"sweep": str(source.analysis_id), "metric": metric},
                result=to_jsonable(result),
                completed_at=datetime.now(timezone.utc),
            )
        )
        return to_jsonable(
            {"analysis_id": stored.analysis_id, "sweep": source.analysis_id, **result}
        )
