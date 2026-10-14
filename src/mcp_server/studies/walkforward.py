"""Walk-forward of a frozen candidate's grid, sliced from full-span runs (S5.3).

Allowed only once a candidate is frozen: every cell of the grid runs once over
the whole study span, in-sample and out-of-sample alike, so the holdout is
seen (a later candidate version is contaminated, as after the out-of-sample
run). The analysis stays ``pending`` until every member has settled; the first
read afterwards (``get_analysis``, ``get_scorecard``, ``get_study``) slices
each member's stored daily equity into the windows, picks the best in-sample
cell per window and writes the result once. Members are never trials.
"""

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from src.core.robustness import grid, walk_forward
from src.db.models.research import ResearchAnalysis, ResearchStudy, ResearchTrial
from src.db.repositories.equity_curve_repository import SyncEquityCurveRepository
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.run_reads import _trade_points, load_run
from src.mcp_server.analysis.series import equity_series
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.request import resolve
from src.mcp_server.studies.batch import Batch
from src.mcp_server.studies.candidates import TESTABLE, current_candidate  # noqa: F401
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.split import Split
from src.mcp_server.studies.submit import require_status
from src.mcp_server.studies.sweeps import _axes
from src.mcp_server.tools import build_spec


def _windows(study: ResearchStudy, *, is_years: int, oos_years: int, mode: str) -> list:
    split = Split.of(study)
    try:
        return walk_forward.windows(
            split.is_start, split.oos_end, is_years=is_years, oos_years=oos_years, mode=mode
        )
    except ValueError as exc:
        raise ToolFailure("invalid_request", str(exc), fix="Shorten the windows.") from None


def prepare_walk_forward(
    study_key: str,
    *,
    is_years: int,
    oos_years: int,
    mode: str,
    select_by: str,
    grid_axes: dict[str, Any] | None,
    max_cells: int,
) -> tuple[Batch, list[str]]:
    """One full-span run per grid cell of the frozen candidate's study, as a pending analysis."""
    if select_by not in walk_forward.SELECTORS:
        raise ToolFailure(
            "invalid_request",
            f"select_by must be one of {walk_forward.SELECTORS}.",
            fix="Pick one.",
        )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key)
        require_status(study, TESTABLE, "walk-forward")
        candidate = current_candidate(repo, study)
        found = _windows(study, is_years=is_years, oos_years=oos_years, mode=mode)
        axes = _axes(study, grid_axes, None, 0.0)
        if grid.size(axes) > max_cells:
            raise ToolFailure(
                "sweep_too_large",
                f"The grid has {grid.size(axes)} cells; at most {max_cells} run per walk-forward.",
                fix="Coarsen the steps.",
            )
        payloads, kept = _cell_payloads(study, candidate, grid.expand(axes))
        request = {
            "axes": axes,
            "is_years": is_years,
            "oos_years": oos_years,
            "mode": mode,
            "select_by": select_by,
            "windows": [w.to_dict() for w in found],
            "version": candidate.version,
            "candidate_params": candidate.params,
        }
        batch = Batch(
            study=study.slug,
            role="walk_forward",
            kind="walk_forward",
            payloads=payloads,
            cells=kept,
            statuses=TESTABLE,
            request=request,
            candidate_pk=candidate.id,
        )
        warnings = list(payloads[0]["warnings"]) + [
            "Walk-forward runs every cell over the whole span: the holdout is seen, and a new "
            "candidate version after this is marked contaminated."
        ]
    return batch, warnings


def _cell_payloads(
    study: ResearchStudy, candidate: Any, cells: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One full-span job per distinct cell (the first validates data, the rest resolve)."""
    split = Split.of(study)
    payloads: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cell in cells:
        spec = build_spec(
            strategy=study.strategy_type,
            symbol=candidate.symbol,
            start=split.is_start,
            end=split.oos_end,
            timeframe=study.timeframe,
            catalog=study.catalog,
            params=cell,
            starting_balance=study.starting_balance,
            fill_seed=candidate.fill_seed,
        )
        resolved = resolve(spec, default_catalog=study.catalog)
        if resolved.config_hash in seen:
            continue
        seen.add(resolved.config_hash)
        payload = payloads[0] if payloads else service.prepare_backtest(spec, study.catalog)
        payloads.append(
            {
                **payload,
                "spec": spec.model_dump(mode="json"),
                "resolved": resolved.summary(),
                "study": {"slug": study.slug, "role": "walk_forward", "cell": cell},
            }
        )
        kept.append(cell)
    return payloads, kept


def _member_curves(
    session: Session, trials: list[ResearchTrial]
) -> tuple[dict[str, pd.Series], dict[str, float], dict[str, list], list[str]]:
    """Each completed member's daily equity, base and trade entries; the unusable ones."""
    curves: dict[str, pd.Series] = {}
    bases: dict[str, float] = {}
    trades: dict[str, list] = {}
    unusable = []
    for trial in trials:
        if trial.state != "completed" or trial.run_id is None:
            continue
        run = load_run(session, str(trial.run_id))
        points = SyncEquityCurveRepository(session).find_points(run.run_id)
        basis = (run.config_snapshot or {}).get("metrics_basis")
        if not points or basis != "mark_to_market":
            unusable.append(trial.job_id)
            continue
        curves[trial.job_id] = equity_series(points)
        bases[trial.job_id] = float(run.initial_capital)
        trades[trial.job_id] = [
            pd.Timestamp(p.entry).tz_localize("UTC")
            if pd.Timestamp(p.entry).tzinfo is None
            else pd.Timestamp(p.entry).tz_convert("UTC")
            for p in _trade_points(session, run)
        ]
    return curves, bases, trades, unusable


def complete_walk_forward(
    session: Session, repo: SyncResearchRepository, analysis: ResearchAnalysis
) -> None:
    """Evaluate a pending walk-forward whose members have all settled; write the result once."""
    trials = repo.trials_of_analysis(analysis)
    if any(t.state == "pending" for t in trials):
        return
    curves, bases, trades, unusable = _member_curves(session, trials)
    request = analysis.request
    cell_of = request.get("cells") or {}
    missing = [t.job_id for t in trials if t.state == "void"] + unusable
    if not curves:
        repo.complete_analysis(
            analysis,
            result={"missing_cells": missing, "note": "No member run is usable."},
            status="void",
        )
        return
    found = [walk_forward.Window(**{**w, **_dates(w)}) for w in request["windows"]]
    result = walk_forward.evaluate(
        curves, found, select_by=request["select_by"], initial=bases, trades=trades
    )
    result["cells"] = {job_id: cell_of.get(job_id) for job_id in curves}
    result["missing_cells"] = missing
    result["runs"] = {t.job_id: str(t.run_id) for t in trials if t.run_id is not None}
    repo.complete_analysis(analysis, result=to_jsonable(result), status="completed")


def _dates(window: dict[str, Any]) -> dict[str, Any]:
    from datetime import date

    return {
        k: date.fromisoformat(window[k]) for k in ("is_start", "is_end", "oos_start", "oos_end")
    }


def settle_analyses(session: Session, repo: SyncResearchRepository, study: ResearchStudy) -> None:
    """Complete every pending batch analysis whose members have settled (after ledger.settle)."""
    for analysis in repo.analyses(study):
        if analysis.status != "pending":
            continue
        if analysis.kind == "walk_forward":
            complete_walk_forward(session, repo, analysis)
        else:
            _complete_plain_batch(repo, analysis)


def _complete_plain_batch(repo: SyncResearchRepository, analysis: ResearchAnalysis) -> None:
    """A sweep, breadth or cost-stress batch is complete once its members have settled."""
    trials = repo.trials_of_analysis(analysis)
    if not trials or any(t.state == "pending" for t in trials):
        return
    runs = {t.job_id: str(t.run_id) for t in trials if t.run_id is not None}
    missing = [t.job_id for t in trials if t.state == "void"]
    status = "completed" if runs else "void"
    repo.complete_analysis(analysis, result={"runs": runs, "missing_cells": missing}, status=status)


def analysis_view(analysis: ResearchAnalysis, trials: list[ResearchTrial]) -> dict[str, Any]:
    return to_jsonable(
        {
            "analysis_id": analysis.analysis_id,
            "kind": analysis.kind,
            "status": analysis.status,
            "version": analysis.version,
            "request": analysis.request,
            "result": analysis.result,
            "members": [
                {"job_id": t.job_id, "state": t.state, "run_id": t.run_id, "symbol": t.symbol}
                for t in trials
            ],
            "created_at": analysis.created_at,
            "completed_at": analysis.completed_at,
        }
    )


def get_analysis(
    store: JobStore, study_key: str, *, analysis_id: str | None, kind: str | None
) -> dict[str, Any]:
    """One analysis of the study with its request, status, result and member jobs."""
    from src.mcp_server.studies import ledger
    from src.mcp_server.studies.batch import find_analysis

    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key, for_update=True)
        ledger.settle(repo, store, study)
        settle_analyses(session, repo, study)
        if analysis_id is not None:
            analysis = find_analysis(session, analysis_id)
            if analysis.study_pk != study.id:
                raise ToolFailure(
                    "unknown_analysis",
                    f"{analysis_id} is not an analysis of '{study.slug}'.",
                    fix="",
                )
        else:
            found = repo.analyses(study, kind=kind)
            if not found:
                what = f"{kind} analysis" if kind else "analysis"
                raise ToolFailure("no_analysis", f"Study '{study.slug}' has no {what}.", fix="")
            analysis = found[-1]
        return {"analysis": analysis_view(analysis, repo.trials_of_analysis(analysis))}
