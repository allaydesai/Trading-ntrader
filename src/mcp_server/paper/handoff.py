"""``paper_commands``: the commands that start a paper session of a frozen candidate (S7.1).

Refused until the candidate has a completed out-of-sample run, because that run
is what the session is compared against (``--compare-to``) and where its band
comes from. Everything else that should give Allay pause is a warning: failing
or missing gates, contamination, code that changed since freezing, parameters a
setting would change, a thin band, sessions already linked. Nothing is written.
"""

from typing import Any

from src.config import get_settings
from src.core.benchmarks import BENCHMARKS
from src.core.strategy_factory import StrategyLoader
from src.db.models.research import ResearchCandidate
from src.db.repositories.research_repository import SyncResearchRepository
from src.db.session_sync import get_sync_session
from src.mcp_server.analysis.run_reads import load_run
from src.mcp_server.catalogs import catalog_availability
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.paper import reads
from src.mcp_server.paper.commands import (
    check_name,
    default_name,
    live_bar_type,
    param_value,
    render_commands,
)
from src.mcp_server.paper.specs import model_normaliser
from src.mcp_server.settings import McpSettings
from src.mcp_server.studies import ledger, scorecard
from src.mcp_server.studies.lifecycle import load_study
from src.services.provenance import REPO_ROOT, git_provenance

NOTES = [
    "The research server never runs these: run them in a terminal on the Mac.",
    "live check comes first: it proves the gateway, the paper account and the bar "
    "subscription before anything is created.",
    "get_session(<name>) reads the session against its band; check it weekly.",
]


def _frozen(settings, store: JobStore, key: str, version: int | None) -> dict[str, Any]:
    """The candidate, its newest out-of-sample run and the names already taken."""
    with get_sync_session() as session:
        repo = SyncResearchRepository(session)
        study = load_study(repo, key, for_update=True)
        ledger.settle(repo, store, study)
        candidate: ResearchCandidate = scorecard.candidate_of(repo, study, version)
        runs = reads.oos_runs(session, candidate.id)
        if not runs:
            raise ToolFailure(
                "no_out_of_sample_run",
                f"Version {candidate.version} of '{study.slug}' has no completed out-of-sample "
                "run, which the paper session is compared against.",
                fix="run_out_of_sample(study), wait for the job, then ask again.",
            )
        snapshot = load_run(session, str(runs[-1])).config_snapshot or {}
        return {
            "study": study,
            "candidate": candidate,
            "compare_to": runs[-1],
            "bar_spec": snapshot.get("bar_type") or f"{candidate.timeframe}-LAST",
            "taken": reads.session_names(session),
            "linked": [r.name for r in reads.sessions_linked_to(session, runs)],
        }


def _settings_drift(candidate: ResearchCandidate, rendered: dict[str, str]) -> list[str]:
    """Parameters ``live create`` would store differently from the frozen ones."""
    normalise = model_normaliser(candidate.strategy_type)
    frozen = normalise(candidate.params)
    live = normalise(
        StrategyLoader.build_strategy_params(candidate.strategy_type, rendered, get_settings())
    )
    return [
        f"live create would store {key}={live.get(key)!r}, the frozen value is "
        f"{frozen.get(key)!r} (a setting in .env overrides it)."
        for key in sorted(set(frozen) | set(live))
        if frozen.get(key) != live.get(key)
    ]


def _code_drift(candidate: ResearchCandidate) -> list[str]:
    now = git_provenance()
    found = []
    if candidate.git_dirty:
        found.append("The candidate was frozen from a dirty working tree.")
    if now.git_commit != candidate.git_commit or now.git_dirty:
        found.append(
            f"The session will run the code at HEAD ({(now.git_commit or '?')[:12]}"
            f"{', dirty' if now.git_dirty else ''}), not the frozen "
            f"{(candidate.git_commit or '?')[:12]}: check the strategy did not change."
        )
    if candidate.strategies_commit and now.strategies_commit != candidate.strategies_commit:
        found.append("The custom strategies submodule moved since the candidate was frozen.")
    return found


def _gate_warnings(settings, store: JobStore, key: str, version: int) -> tuple[list[str], dict]:
    card = scorecard.get_scorecard(settings, store, key, version)
    warnings = [w for w in card["warnings"]]
    for status in ("fail", "missing"):
        gates = [g for g in card["summary"][status] if g != "G4"]
        if gates:
            warnings.append(f"Gates {status}: {', '.join(gates)} (get_scorecard).")
    band = card["expectation_band"]
    if band.get("status") != "ok":
        warnings.append(f"Expectation band is {band.get('status')}: {band.get('note', '')}".strip())
    return warnings, band


def _live_bar_type(candidate: ResearchCandidate, bar_spec: str) -> str:
    """The live bar type, refused for a benchmark or an instrument with no venue."""
    if candidate.strategy_type in BENCHMARKS:
        raise ToolFailure(
            "benchmark_not_tradable",
            f"{candidate.strategy_type} is a backtest benchmark, not a strategy.",
            fix="Paper-trade a study's own strategy.",
        )
    instrument = catalog_availability(candidate.symbol, candidate.catalog).get("nautilus_id")
    if not instrument:
        raise ToolFailure(
            "no_venue",
            f"{candidate.symbol} has no resolved venue in '{candidate.catalog}'.",
            fix="Resolve the instrument's venue (catalog_availability says how).",
        )
    return live_bar_type(instrument, bar_spec)


def paper_commands(
    settings: McpSettings, store: JobStore, key: str, version: int | None, name: str | None
) -> dict[str, Any]:
    """The exact commands to start a paper session of the frozen candidate, as text."""
    frozen = _frozen(settings, store, key, version)
    study, candidate, taken = frozen["study"], frozen["candidate"], frozen["taken"]
    bar_type = _live_bar_type(candidate, frozen["bar_spec"])
    session_name = (
        check_name(name, taken=taken)
        if name is not None
        else default_name(study.slug, candidate.version, taken=taken)
    )
    commands, unset = render_commands(
        repo_root=REPO_ROOT,
        name=session_name,
        strategy=candidate.strategy_type,
        bar_type=bar_type,
        params=candidate.params,
        compare_to=str(frozen["compare_to"]),
    )
    rendered = {k: v for k, raw in candidate.params.items() if (v := param_value(raw)) is not None}
    warnings, band = _gate_warnings(settings, store, study.slug, candidate.version)
    warnings += _code_drift(candidate) + _settings_drift(candidate, rendered) + unset
    if frozen["linked"]:
        warnings.append(
            f"Sessions already linked to this candidate: {', '.join(frozen['linked'])}."
        )
    return {
        "study": study.slug,
        "version": candidate.version,
        "compare_to": str(frozen["compare_to"]),
        "session_name": session_name,
        "strategy": candidate.strategy_type,
        "bar_type": bar_type,
        "params": candidate.params,
        "commands": commands,
        "script": "\n".join(c["command"] for c in commands if c["step"] != "status"),
        "expectation_band": band,
        "already_linked": frozen["linked"],
        "warnings": warnings,
        "notes": NOTES,
    }
