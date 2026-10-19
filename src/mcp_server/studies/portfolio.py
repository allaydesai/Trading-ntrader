"""A candidate sleeve inside the held portfolio, before and after (S9.3, S9.5; phase 5a).

The held sleeves are stored runs named with weights; the candidate is one of
this study's runs (its newest out-of-sample run by default). Both portfolios
are daily constant-mixes of the stored curves on the days every sleeve shares,
computed here and stored at once as a completed ``portfolio`` analysis of the
study: no job, no trial, no engine run, no gate. A study that has not frozen
may only bring in-sample runs of its own: its holdout stays unseen.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from src.core.robustness import portfolio as pf
from src.db.models.research import ResearchAnalysis, ResearchStudy, ResearchTrial
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.correlation import DEFINITIONS, align, check_threshold
from src.mcp_server.analysis.series import downsample, drawdown
from src.mcp_server.analysis.sleeves import (
    Sleeve,
    attribute,
    benchmark_closes,
    load_sleeves,
    regime_table,
    sleeve_view,
)
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.paper.reads import oos_runs
from src.mcp_server.runs import parse_run_ids
from src.mcp_server.studies import ledger
from src.mcp_server.studies.candidates import TESTABLE, sees_holdout
from src.mcp_server.studies.lifecycle import load_study

KIND = "portfolio"
REBALANCE = ("daily",)
CURVE_POINTS = 200
#: Statuses in which the study's own holdout runs are already spent evidence.
HOLDOUT_OPEN = TESTABLE | {"promoted"}


class SleeveSpec(BaseModel, extra="forbid"):
    """One held sleeve: a stored run and its weight (any positive scale)."""

    run_id: str
    weight: float = Field(default=1.0, gt=0)


def parse_sleeves(sleeves: list[dict[str, Any]]) -> list[SleeveSpec]:
    if not sleeves:
        raise ToolFailure(
            "too_few_sleeves", "Name at least one held sleeve.", fix="sleeves=[{run_id, weight}]"
        )
    try:
        return [SleeveSpec(**s) for s in sleeves]
    except (ValidationError, TypeError) as exc:
        raise ToolFailure(
            "invalid_weights",
            f"sleeves: {exc.errors()[0]['msg'] if isinstance(exc, ValidationError) else exc}",
            fix="Each sleeve is {run_id: str, weight: positive number}.",
        ) from None


def _candidate_run(
    repo: SyncResearchRepository, study: Any, trials: list[Any], run_id: str | None
) -> tuple[Any, str]:
    """The study's run to judge, and how it was chosen."""
    done = [t for t in trials if t.state == "completed" and t.run_id is not None]
    if run_id is not None:
        (key,) = parse_run_ids([run_id], minimum=1, maximum=1)
        for trial in done:
            if str(trial.run_id) == key:
                return trial, "given"
        raise ToolFailure(
            "not_a_trial",
            f"Run {run_id} is not a finished run of study '{study.slug}'.",
            fix="get_study lists the ledger's runs.",
        )
    current = [t for t in done if t.version == study.current_version]
    for candidate in repo.candidates(study):
        if candidate.version != study.current_version:
            continue
        newest = oos_runs(repo.session, candidate.id)
        if newest:
            return next(t for t in done if t.run_id == newest[-1]), "newest_out_of_sample"
        for trial in current:
            if trial.run_id == candidate.source_run_id:
                return trial, "candidate_source"
    in_sample = [t for t in current if t.role == "in_sample"]
    if in_sample:
        return in_sample[-1], "newest_in_sample"
    raise ToolFailure(
        "no_run",
        f"Version {study.current_version} of '{study.slug}' has no finished run.",
        fix="Run an in-sample trial, or freeze and run_out_of_sample first.",
    )


def _check_holdout(study: Any, trials: list[Any]) -> None:
    """Before a freeze, none of this study's named runs may have seen its holdout."""
    if study.status in HOLDOUT_OPEN:
        return
    for trial in trials:
        if trial.study_pk == study.id and sees_holdout(study, trial):
            raise ToolFailure(
                "holdout_peek",
                f"Run {trial.run_id} saw the holdout of '{study.slug}', which is {study.status}.",
                fix="Freeze a candidate and run_out_of_sample, or use in-sample runs.",
            )


def _weights(
    held: list[SleeveSpec], candidate_id: str, candidate_weight: float | None
) -> tuple[dict[str, float], dict[str, float]]:
    """The held portfolio's weights, and the same scaled to make room for the candidate."""
    try:
        without = pf.check_weights({h.run_id: h.weight for h in held}, [h.run_id for h in held])
    except ValueError as exc:
        raise ToolFailure(
            "invalid_weights", str(exc), fix="Positive weights, one per sleeve."
        ) from None
    share = 1 / (len(held) + 1) if candidate_weight is None else candidate_weight
    if not 0 < share < 1:
        raise ToolFailure(
            "invalid_weights", "candidate_weight is a fraction in (0, 1).", fix="e.g. 0.25"
        )
    with_ = {name: w * (1 - share) for name, w in without.items()}
    return without, {**with_, candidate_id: share}


def _evaluate(frame: pd.DataFrame, weights: dict[str, float], *, initial: float) -> dict:
    found = pf.evaluate(frame[list(weights)], weights, initial=initial)
    equity = found["equity"]
    under = drawdown(equity)
    return {
        "metrics": found["metrics"],
        "contribution": found["contribution"],
        "curve": [
            {
                "time": stamp.isoformat(),
                "equity": round(value, 2),
                "drawdown": round(under[stamp], 6),
            }
            for stamp, value in downsample(equity, CURVE_POINTS).items()
        ],
    }


def _result(
    loaded: list[Sleeve],
    weights: tuple[dict[str, float], dict[str, float]],
    *,
    study: ResearchStudy,
    threshold: float,
    benchmark: str,
    min_days: int,
    warnings: list[str],
) -> dict[str, Any]:
    without_w, with_w = weights
    candidate = loaded[-1]
    for sleeve in loaded:
        sleeve.weight = with_w[sleeve.run_id]
    frame, dropped, window = align(loaded, min_days=min_days)
    initial = float(study.starting_balance)
    with_, without = (
        _evaluate(frame, with_w, initial=initial),
        _evaluate(frame, without_w, initial=initial),
    )
    held = pf.portfolio_returns(frame[list(without_w)], without_w)
    pair = pd.DataFrame({"candidate": frame[candidate.run_id], "held": held})
    closes = benchmark_closes(study.catalog, benchmark, frame.index[0], frame.index[-1])
    totals = {"with_candidate": pf.portfolio_returns(frame, with_w), "without_candidate": held}
    regimes = regime_table(frame, totals, closes)
    return {
        "sleeves": [
            sleeve_view(s, days=len(frame), dropped=dropped[s.run_id]) for s in loaded[:-1]
        ],
        "window": window,
        "rebalance": REBALANCE[0],
        "weights": {"with": with_w, "without": without_w},
        "with_candidate": with_,
        "without_candidate": without,
        "delta": pf.delta(without["metrics"], with_["metrics"]),
        "candidate_vs_held": {
            "correlation": pf.correlation(pair)["candidate"]["held"],
            "drawdown_overlap": pf.drawdown_overlap(pair, threshold=threshold)[0],
        },
        "correlation": {"method": "pearson", "matrix": pf.correlation(frame)},
        "regimes": {"benchmark": benchmark, "catalog": study.catalog, **regimes},
        "definitions": {**DEFINITIONS, "basis": pf.BASIS},
        "warnings": warnings + [f"regimes: {note}" for note in regimes["notes"]],
    }


def _store(
    repo: SyncResearchRepository,
    study: ResearchStudy,
    trial: ResearchTrial,
    request: dict,
    result: dict,
) -> ResearchAnalysis:
    return repo.add_analysis(
        ResearchAnalysis(
            study_pk=study.id,
            candidate_pk=trial.candidate_pk,
            version=trial.version,
            kind=KIND,
            status="completed",
            request=to_jsonable(request),
            result=to_jsonable(result),
            completed_at=datetime.now(timezone.utc),
        )
    )


def _held(held: list[SleeveSpec], candidate_id: str) -> list[SleeveSpec]:
    """The held sleeves with canonical run ids; the candidate may not be among them."""
    ids = parse_run_ids([h.run_id for h in held], minimum=0, maximum=1_000_000)
    if candidate_id in ids:
        raise ToolFailure(
            "duplicate_sleeve",
            f"The candidate run {candidate_id} is also listed as a held sleeve.",
            fix="Name the candidate with candidate_run_id only.",
        )
    return [SleeveSpec(run_id=i, weight=h.weight) for i, h in zip(ids, held)]


def _request(
    held: list[SleeveSpec],
    candidate: dict[str, Any],
    *,
    rebalance: str,
    benchmark: str,
    threshold: float,
) -> dict[str, Any]:
    return {
        "sleeves": [h.model_dump() for h in held],
        "candidate_run_id": candidate["run_id"],
        "candidate_weight": candidate["weight"],
        "rebalance": rebalance,
        "benchmark_symbol": benchmark,
        "drawdown_threshold": threshold,
    }


def run_portfolio_backtest(
    store: JobStore,
    study_key: str,
    *,
    sleeves: list[dict[str, Any]],
    candidate_run_id: str | None,
    candidate_weight: float | None,
    rebalance: str,
    benchmark_symbol: str | None,
    drawdown_threshold: float,
    max_sleeves: int,
    min_days: int,
) -> dict[str, Any]:
    """Combine the study's candidate run with the held sleeves; store and return before/after."""
    check_threshold(drawdown_threshold)
    if rebalance not in REBALANCE:
        raise ToolFailure(
            "invalid_request",
            "Only daily (constant-mix) rebalancing exists.",
            fix="rebalance=daily",
        )
    held = parse_sleeves(sleeves)
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key, for_update=True)
        ledger.settle(repo, store, study)
        trial, chosen_by = _candidate_run(repo, study, repo.trials(study), candidate_run_id)
        candidate_id = str(trial.run_id)
        held = _held(held, candidate_id)
        ids = [h.run_id for h in held] + [candidate_id]
        loaded = load_sleeves(session, ids, minimum=2, maximum=max_sleeves)
        shared = attribute(session, loaded)
        _check_holdout(study, repo.trials_of_runs([UUID(s.run_id) for s in loaded]))
        weights = _weights(held, candidate_id, candidate_weight)
        benchmark = (benchmark_symbol or study.symbols[0]).strip().upper()
        result = _result(
            loaded,
            weights,
            study=study,
            threshold=drawdown_threshold,
            benchmark=benchmark,
            min_days=min_days,
            warnings=shared,
        )
        result["candidate"] = {
            "run_id": candidate_id,
            "role": trial.role,
            "version": trial.version,
            "weight": weights[1][candidate_id],
            "chosen_by": chosen_by,
            "warnings": loaded[-1].warnings,
        }
        request = _request(
            held,
            result["candidate"],
            rebalance=rebalance,
            benchmark=benchmark,
            threshold=drawdown_threshold,
        )
        stored = _store(repo, study, trial, request, result)
        head = {"analysis_id": stored.analysis_id, "study": study.slug, "version": trial.version}
        return to_jsonable({**head, "kind": KIND, "status": "completed", **result})
