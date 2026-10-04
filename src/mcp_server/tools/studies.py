"""Study tools: open, read, search, freeze and decide (S1.1-S1.3, S5.1, S6.3, S6.4, S8.2)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.studies import candidates, lifecycle
from src.mcp_server.studies.spec import build_study_spec
from src.mcp_server.tools import call

TOOL_NAMES = (
    "create_study",
    "get_study",
    "list_studies",
    "update_study",
    "freeze_candidate",
    "new_candidate_version",
)


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the study tools to ``server``."""

    def store() -> JobStore:
        assert ctx.runner is not None, "build_server always creates the runner"
        return ctx.runner.store

    @server.tool()
    async def create_study(
        slug: str,
        title: str,
        hypothesis: str,
        strategy: str,
        symbols: list[str],
        in_sample_start: date,
        in_sample_end: date,
        out_of_sample_start: date,
        out_of_sample_end: date,
        timeframe: str = "1-DAY",
        catalog: str | None = None,
        param_space: dict[str, dict[str, Any]] | None = None,
        pass_criteria: list[str] | None = None,
        trial_budget: int = 40,
        starting_balance: Decimal = Decimal("1000000"),
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Open a study for one idea. The out-of-sample window is locked from now on.

        slug matches the vault Idea (lowercase, e.g. crsi-qqq). param_space maps a
        strategy parameter to {"values": [...]} or {"min": x, "max": y, "step": s};
        each value is checked against the strategy's parameter model. The
        out-of-sample window must come after the in-sample one. pass_criteria are
        gate ids from the vault's System/Gates.md (default: all). Every in-sample
        run counts against trial_budget.
        """

        def run() -> dict[str, Any]:
            spec = build_study_spec(
                slug=slug,
                title=title,
                hypothesis=hypothesis,
                strategy=strategy,
                symbols=symbols,
                in_sample_start=in_sample_start,
                in_sample_end=in_sample_end,
                out_of_sample_start=out_of_sample_start,
                out_of_sample_end=out_of_sample_end,
                timeframe=timeframe,
                catalog=catalog,
                param_space=param_space or {},
                pass_criteria=pass_criteria,
                trial_budget=trial_budget,
                starting_balance=starting_balance,
                tags=tags or [],
            )
            return lifecycle.create_study(ctx.settings, spec)

        return await call(run)

    @server.tool()
    async def get_study(study: str) -> dict[str, Any]:
        """A study by slug or id: split, budget, numbered trial ledger with headline
        metrics, frozen candidates, contamination and its record of decisions."""
        return await call(lifecycle.get_study, store(), study)

    @server.tool()
    async def list_studies(
        status: str | None = None,
        strategy: str | None = None,
        symbol: str | None = None,
        tag: str | None = None,
        text: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Studies matching every filter, newest first; text searches slug, title and
        hypothesis. Rejected and parked studies are included unless status filters."""
        return await call(
            lifecycle.list_studies,
            store(),
            limit=limit,
            status=status,
            strategy=strategy,
            symbol=symbol,
            tag=tag,
            text=text,
            created_from=created_from,
            created_to=created_to,
        )

    _register_decisions(server, ctx, store)


def _register_decisions(
    server: MCPServer, ctx: ServerContext, store: Callable[[], JobStore]
) -> None:
    """Tools that decide on a study: status, budget, freezing and versioning."""

    @server.tool()
    async def update_study(
        study: str,
        reason: str,
        status: str | None = None,
        extend_budget_by: int | None = None,
    ) -> dict[str, Any]:
        """Decide on a study, always with a reason (stored on its record).

        status: rejected or parked (a dead end, still searchable), promoted (needs a
        tested candidate), active (resume a parked study). extend_budget_by raises
        the trial budget.
        """
        return await call(
            lifecycle.update_study,
            store(),
            study,
            reason=reason,
            status=status,
            extend_budget_by=extend_budget_by,
        )

    @server.tool()
    async def freeze_candidate(
        study: str, run_id: str, note: str | None = None, benchmark_rationale: str | None = None
    ) -> dict[str, Any]:
        """Freeze the current version's candidate from one of its completed in-sample runs.

        Records its parameters, code commit and candidate hash; it never changes
        afterwards. Exploration of this version ends: run_out_of_sample tests it
        once, new_candidate_version reopens exploration. ``benchmark_rationale``
        says why beating buy-and-hold on the metric(s) it beats is worth it; G1's
        beats_benchmark_on_one stays missing, never pass, without one.
        """
        return await call(
            candidates.freeze_candidate,
            store(),
            study,
            run_id,
            note,
            benchmark_rationale=benchmark_rationale,
        )

    @server.tool()
    async def new_candidate_version(study: str, reason: str, change: str) -> dict[str, Any]:
        """Start the next candidate version in the same study after a failed check.

        The ledger keeps counting. If the out-of-sample window was already seen, the
        study is marked contaminated: the new version's evidence must then come from
        walk-forward and paper, not the holdout.
        """
        return await call(candidates.new_candidate_version, store(), study, reason, change)
