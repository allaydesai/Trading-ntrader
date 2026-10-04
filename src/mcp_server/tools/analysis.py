"""Analysis read tools: trades, equity, regimes, bars, run search (S2.5, S3.3, S4.3, S8.2)."""

from datetime import date
from typing import Any

from mcp.server import MCPServer

from src.mcp_server import search
from src.mcp_server.analysis import bar_export, run_reads
from src.mcp_server.context import ServerContext
from src.mcp_server.tools import call

TOOL_NAMES = (
    "get_trades",
    "get_equity_curve",
    "get_regime_breakdown",
    "export_bars",
    "search_runs",
)


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the analysis tools to ``server``."""
    settings = ctx.settings

    @server.tool()
    async def get_trades(run_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """One page of a run's trades in entry order; next_offset fetches the next page."""
        return await call(
            run_reads.get_trades, run_id, offset, limit, max_limit=settings.max_trades_page
        )

    @server.tool()
    async def get_equity_curve(run_id: str, max_points: int = 500) -> dict[str, Any]:
        """A run's equity and drawdown (fraction), downsampled; the deepest drawdown's peak
        and trough are always kept."""
        points = max(4, min(max_points, settings.max_equity_points))
        return await call(run_reads.get_equity_curve, run_id, points)

    @server.tool()
    async def get_regime_breakdown(
        run_id: str, benchmark_symbol: str | None = None, sub_periods: int = 4
    ) -> dict[str, Any]:
        """A run's results by calendar year, trend regime (benchmark above/below its
        200-day average), volatility tercile (21-day realised) and equal sub-periods.

        Each cell: days, return, max drawdown, trades, win rate, P&L and a losing
        flag. The benchmark defaults to the run's own symbol.
        """
        return await call(run_reads.get_regime_breakdown, run_id, benchmark_symbol, sub_periods)

    @server.tool()
    async def export_bars(
        study: str,
        slug: str,
        folder: str = "Lab/results",
        symbol: str | None = None,
        timeframe: str | None = None,
        start: date | None = None,
        end: date | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Write a study's in-sample bars to <folder>/<slug>.bars.csv in the vault, for
        probing an effect before writing strategy code. Always clamped to the in-sample
        window; clamped says what was cut."""
        return await call(
            bar_export.export_bars,
            settings,
            study,
            slug=slug,
            folder=folder,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            overwrite=overwrite,
        )

    @server.tool()
    async def search_runs(
        strategy: str | None = None,
        symbol: str | None = None,
        study: str | None = None,
        role: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Runs matching every filter, newest first, with headline metrics, study and
        role (in_sample, benchmark, out_of_sample, reproduction; none = unattributed)."""
        return await call(
            search.search_runs,
            strategy=strategy,
            symbol=symbol,
            study=study,
            role=role,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
