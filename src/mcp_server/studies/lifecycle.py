"""Open, read, search and decide on studies (S1.1, S1.2, S1.3, S6.4, S8.2)."""

from datetime import datetime, timezone
from typing import Any

from src.db.models.research import ResearchStudy
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.catalogs import timeframe_spec
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.request import BacktestSpec
from src.mcp_server.settings import McpSettings
from src.mcp_server.strategies import resolve_strategy
from src.mcp_server.studies import ledger
from src.mcp_server.studies.gates import FALLBACK_GATE_IDS, load_gates
from src.mcp_server.studies.spec import StudySpec, check_param_space
from src.mcp_server.studies.split import Split, check_split
from src.mcp_server.studies.views import study_view, summary_view
from src.mcp_server.validation import validate

#: Status changes update_study accepts, and the statuses each may leave from.
#: ``active`` resumes a parked study at the status it was parked from.
TRANSITIONS: dict[str, frozenset[str]] = {
    "rejected": frozenset({"exploring", "frozen", "tested", "parked"}),
    "parked": frozenset({"exploring", "frozen", "tested"}),
    "promoted": frozenset({"tested"}),
    "active": frozenset({"parked"}),
}


def load_study(repo: SyncResearchRepository, key: str, *, for_update: bool = False):
    """The study named by slug or id, or a failure saying how to find one."""
    study = repo.find_study(key, for_update=for_update)
    if study is None:
        raise ToolFailure(
            "unknown_study", f"No study '{key}'.", fix="list_studies shows slugs and ids."
        )
    return study


def _check_data(spec: StudySpec, strategy: str, catalog: str) -> list[str]:
    """Every symbol must have bars for the in-sample window; returns the warnings."""
    errors, warnings = [], []
    for symbol in spec.symbols:
        check = validate(
            BacktestSpec(
                strategy=strategy,
                symbol=symbol,
                start=spec.in_sample_start,
                end=spec.in_sample_end,
                timeframe=spec.timeframe,
                catalog=catalog,
                starting_balance=spec.starting_balance,
            ),
            default_catalog=catalog,
        )
        errors += [{**e, "symbol": symbol} for e in check["errors"]]
        warnings += [f"{symbol}: {w}" for w in check["warnings"]]
    if errors:
        raise ToolFailure(
            "data_check_failed",
            "The study's data does not cover its in-sample window; nothing was created.",
            fix="Fix each listed error (catalog_availability shows coverage).",
            details={"errors": errors},
        )
    return warnings


def _pass_criteria(spec: StudySpec, settings: McpSettings) -> tuple[list[str], list[str]]:
    gates = load_gates(settings)
    known = gates.ids or list(FALLBACK_GATE_IDS)
    chosen = spec.pass_criteria or known
    unknown = [g for g in chosen if g not in known]
    if unknown:
        raise ToolFailure(
            "unknown_gate",
            f"Unknown gate id(s): {', '.join(unknown)}.",
            fix=f"Use gate ids from the vault's gates: {', '.join(known)}.",
        )
    warnings = [] if gates.problem is None else [f"Gates not read from the vault: {gates.problem}"]
    return chosen, warnings


def _strategy_of(spec: StudySpec) -> str:
    ref = resolve_strategy(spec.strategy)
    if ref.kind != "strategy":
        raise ToolFailure(
            "invalid_study",
            f"'{ref.name}' is a benchmark; a study researches a strategy.",
            fix="Name a strategy (list_strategies); benchmarks run via submit_benchmark.",
        )
    check_param_space(ref, spec.param_space)
    return ref.name


def _new_study(spec: StudySpec, strategy: str, split: Split, criteria: list[str]):
    return ResearchStudy(
        slug=spec.slug,
        title=spec.title,
        hypothesis=spec.hypothesis,
        strategy_type=strategy,
        symbols=spec.symbols,
        timeframe=spec.timeframe,
        catalog=spec.catalog,
        starting_balance=spec.starting_balance,
        param_space={k: v.model_dump(exclude_none=True) for k, v in spec.param_space.items()},
        is_start=split.is_start,
        is_end=split.is_end,
        oos_start=split.oos_start,
        oos_end=split.oos_end,
        pass_criteria=criteria,
        trial_budget=spec.trial_budget,
        tags=spec.tags,
    )


def create_study(settings: McpSettings, spec: StudySpec) -> dict[str, Any]:
    """Validate and open a study; its out-of-sample window is locked from now on."""
    strategy = _strategy_of(spec)
    split = Split(
        spec.in_sample_start,
        spec.in_sample_end,
        spec.out_of_sample_start,
        spec.out_of_sample_end,
    )
    check_split(split)
    catalog = spec.catalog or settings.resolved_default_catalog()
    if not catalog:
        raise ToolFailure(
            "no_catalog", "No catalog given and the server has no default.", fix="Pass catalog."
        )
    spec = spec.model_copy(
        update={
            "symbols": list(dict.fromkeys(s.strip().upper() for s in spec.symbols)),
            "timeframe": timeframe_spec(spec.timeframe).removesuffix("-LAST"),
            "catalog": catalog,
        }
    )
    warnings = _check_data(spec, strategy, catalog)
    criteria, gate_warnings = _pass_criteria(spec, settings)
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        if repo.find_study(spec.slug) is not None:
            raise ToolFailure(
                "study_exists",
                f"A study '{spec.slug}' already exists.",
                fix="Pick another slug, or continue it with get_study.",
            )
        study = repo.add_study(_new_study(spec, strategy, split, criteria))
        repo.add_event(study, "created", details={"split": split.to_dict()})
        view = study_view(session, repo, study)
    return {"study": view, "warnings": warnings + gate_warnings}


def get_study(store: JobStore, key: str) -> dict[str, Any]:
    """A study with its ledger (settled first), candidates and events."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        return {"study": study_view(session, repo, study)}


def list_studies(store: JobStore, *, limit: int = 50, **filters: Any) -> dict[str, Any]:
    """Studies matching every filter, newest first, with budget and state only."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        found = repo.search_studies(limit=max(1, min(limit, 200)), **filters)
        rows = []
        for study in found:
            ledger.settle(repo, store, study)
            rows.append(summary_view(study, ledger.budget(study, repo.trials(study))))
    return {"studies": rows}


def _resume_status(repo: SyncResearchRepository, study: ResearchStudy) -> str:
    for event in reversed(repo.events(study)):
        if event.kind == "status_changed" and event.details.get("to") == "parked":
            return str(event.details.get("from") or "exploring")
    return "exploring"


def _change_status(repo: SyncResearchRepository, study: ResearchStudy, target: str, reason: str):
    allowed = TRANSITIONS.get(target)
    if allowed is None:
        raise ToolFailure(
            "invalid_status",
            f"Cannot set status '{target}'.",
            fix=f"Use one of: {', '.join(TRANSITIONS)}.",
        )
    if study.status not in allowed:
        raise ToolFailure(
            "invalid_transition",
            f"Study '{study.slug}' is {study.status}; '{target}' needs one of: "
            f"{', '.join(sorted(allowed))}.",
            fix="promoted needs a tested candidate (run_out_of_sample); active resumes a "
            "parked study.",
        )
    new = _resume_status(repo, study) if target == "active" else target
    repo.add_event(study, "status_changed", reason, {"from": study.status, "to": new})
    study.status, study.status_reason = new, reason


def update_study(
    store: JobStore,
    key: str,
    *,
    reason: str,
    status: str | None = None,
    extend_budget_by: int | None = None,
) -> dict[str, Any]:
    """Change a study's status or extend its budget; every change needs a reason."""
    if not reason or not reason.strip():
        raise ToolFailure(
            "reason_required", "Every study change needs a reason.", fix="Pass reason."
        )
    if status is None and not extend_budget_by:
        raise ToolFailure(
            "nothing_to_change", "Give status or extend_budget_by.", fix="Say what to change."
        )
    if extend_budget_by is not None and extend_budget_by <= 0:
        raise ToolFailure(
            "invalid_budget", "extend_budget_by must be positive.", fix="Pass a positive number."
        )
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        if extend_budget_by:
            before = study.trial_budget
            study.trial_budget = before + extend_budget_by
            repo.add_event(
                study, "budget_extended", reason, {"from": before, "to": study.trial_budget}
            )
        if status is not None:
            _change_status(repo, study, status, reason)
        study.updated_at = datetime.now(timezone.utc)
        session.flush()
        return {"study": study_view(session, repo, study)}
