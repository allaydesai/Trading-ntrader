"""Robustness runs of a candidate: breadth on untuned symbols and cost stress (S4.4, G2).

Both are batches recorded as analyses of the study, never counted against the
budget and never freezable. Breadth runs the chosen parameters (the frozen
candidate's once there is one) on symbols the study did not tune on; cost
stress runs the frozen candidate with every commission term scaled, so the
scorecard can ask whether it is still profitable at twice the costs.
"""

from decimal import Decimal
from typing import Any

from src.db.models.research import ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs import service
from src.mcp_server.request import resolve
from src.mcp_server.strategies import resolve_params, resolve_strategy
from src.mcp_server.studies.batch import Batch
from src.mcp_server.studies.candidates import TESTABLE, _candidate_spec, current_candidate
from src.mcp_server.studies.lifecycle import load_study
from src.mcp_server.studies.split import Split
from src.mcp_server.studies.submit import require_status
from src.mcp_server.tools import build_spec
from src.services.provenance import compute_candidate_hash

BREADTH_STATUSES = frozenset({"exploring", "frozen", "tested"})
WINDOWS = ("in_sample", "out_of_sample")
DEFAULT_COST_MULTIPLIER = 2.0


def _window(study: ResearchStudy, window: str) -> tuple[Any, Any]:
    split = Split.of(study)
    if window == "in_sample":
        return split.is_start, split.is_end
    if window == "out_of_sample":
        if study.status not in TESTABLE:
            raise ToolFailure(
                "holdout_locked",
                "The out-of-sample window is locked until a candidate is frozen.",
                fix="Use window='in_sample', or freeze_candidate first.",
            )
        return split.oos_start, split.oos_end
    raise ToolFailure("invalid_window", f"window must be one of {WINDOWS}.", fix="Pick one.")


def _breadth_params(
    repo: SyncResearchRepository, study: ResearchStudy, params: dict[str, Any] | None
) -> tuple[dict[str, Any], int | None, int | None]:
    """The parameters to run and the candidate they come from (when frozen and not given)."""
    if params is None and study.status in TESTABLE:
        candidate = current_candidate(repo, study)
        return dict(candidate.params), candidate.id, candidate.version
    if params is None:
        raise ToolFailure(
            "params_required",
            "No frozen candidate to take parameters from: pass params.",
            fix="Pass the parameter set to test, or freeze_candidate first.",
        )
    return resolve_params(resolve_strategy(study.strategy_type), params), None, None


def prepare_breadth(
    study_key: str, *, symbols: list[str], window: str, params: dict[str, Any] | None
) -> tuple[Batch, list[str]]:
    """The same parameters on symbols the study did not tune on, as one uncounted batch."""
    wanted = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    if not wanted:
        raise ToolFailure("invalid_request", "symbols is empty.", fix="Name the symbols.")
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key)
        require_status(study, BREADTH_STATUSES, "breadth runs")
        tuned = [s for s in wanted if s in study.symbols]
        if tuned:
            raise ToolFailure(
                "study_mismatch",
                f"{', '.join(tuned)} is a study symbol: the parameters were tuned on it.",
                fix="Breadth is for untuned instruments; submit_backtest runs study symbols.",
            )
        start, end = _window(study, window)
        full_params, candidate_pk, version = _breadth_params(repo, study, params)
        payloads, warnings = [], []
        for symbol in wanted:
            spec = build_spec(
                strategy=study.strategy_type,
                symbol=symbol,
                start=start,
                end=end,
                timeframe=study.timeframe,
                catalog=study.catalog,
                params=full_params,
                starting_balance=study.starting_balance,
            )
            payload = service.prepare_backtest(spec, default_catalog=study.catalog)
            payload["study"] = {"slug": study.slug, "role": "breadth", "symbol": symbol}
            payloads.append(payload)
            warnings += [f"{symbol}: {w}" for w in payload["warnings"]]
        request = {"symbols": wanted, "window": window, "params": full_params, "version": version}
        batch = Batch(
            study=study.slug,
            role="breadth",
            kind="breadth",
            payloads=payloads,
            cells=[{"symbol": s} for s in wanted],
            statuses=BREADTH_STATUSES,
            request=request,
            candidate_pk=candidate_pk,
        )
    return batch, warnings


def prepare_cost_stress(
    study_key: str, *, multiplier: float | None, gate_multiplier: Any
) -> tuple[Batch, list[str]]:
    """The frozen candidate on the in-sample window with every commission term scaled."""
    scale = float(
        multiplier if multiplier is not None else (gate_multiplier or DEFAULT_COST_MULTIPLIER)
    )
    if scale <= 1:
        raise ToolFailure(
            "invalid_request", "multiplier must be above 1.", fix="e.g. 2 for twice the costs."
        )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, study_key)
        require_status(study, TESTABLE, "cost stress")
        candidate = current_candidate(repo, study)
        split = Split.of(study)
        plain = _candidate_spec(candidate, split.is_start, split.is_end)
        if compute_candidate_hash(resolve(plain, default_catalog="").request) != (
            candidate.candidate_hash
        ):
            raise ToolFailure(
                "candidate_drift",
                "The frozen parameters no longer resolve to the frozen candidate.",
                fix="Freeze a new version with new_candidate_version.",
            )
        spec = plain.model_copy(update={"cost_multiplier": scale})
        payload = service.prepare_backtest(spec, default_catalog=spec.catalog or "")
        payload["study"] = {
            "slug": study.slug,
            "role": "cost_stress",
            "version": candidate.version,
            "cost_multiplier": scale,
        }
        batch = Batch(
            study=study.slug,
            role="cost_stress",
            kind="cost_stress",
            payloads=[payload],
            cells=[{"cost_multiplier": scale}],
            statuses=TESTABLE,
            request={"multiplier": scale, "window": "in_sample", "version": candidate.version},
            candidate_pk=candidate.id,
        )
        warnings = list(payload["warnings"])
    return batch, warnings


def cost_multiplier_of(gates: Any) -> Decimal | float | None:
    """G2's ``cost_stress_multiplier`` threshold, when the vault names one."""
    g2 = (getattr(gates, "thresholds", None) or {}).get("G2") or {}
    value = g2.get("cost_stress_multiplier")
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None
