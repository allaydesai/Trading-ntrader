"""Study tools: open, read, search and decide on studies (S1.1-S1.3, S6.4, S8.2)."""

from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.context import ServerContext
from src.mcp_server.jobs.store import JobStore
from src.mcp_server.studies import lifecycle
from src.mcp_server.studies.spec import build_study_spec
from src.mcp_server.tools import call

TOOL_NAMES = ("create_study", "get_study", "list_studies", "update_study")


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
