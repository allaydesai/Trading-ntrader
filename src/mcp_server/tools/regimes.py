"""Regime calendar tools: build the stored calendar, read its labels (regime attribution phase 1).

Labels are computed only by ``build_regime_calendar`` and only ever read by date
afterwards. Ground-truth output is hindsight: it carries ``hindsight: true`` and
``stamp: "hindsight, not tradeable"``, and no strategy, config, sweep or filter takes it.
"""

from datetime import date
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.analysis import regime_calendar
from src.mcp_server.context import ServerContext
from src.mcp_server.tools import call

TOOL_NAMES = ("build_regime_calendar", "get_regime_labels")


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the regime calendar tools to ``server``."""

    @server.tool()
    async def build_regime_calendar(
        benchmark_symbol: str = "SPY",
        catalog: str = "firstrate-etf",
        definition_overrides: dict[str, dict[str, Any]] | None = None,
        new_revision: bool = False,
        revision_reason: str | None = None,
        revision_note: str | None = None,
    ) -> dict[str, Any]:
        """Create or extend the reference's stored regime calendar from its daily bars.

        Two versions, one per label set: ground_truth (hindsight wave legs at 3/6/12 x
        ATR20, centred ranging and volatility) and detector (sma200, past data only, 272
        sessions of warm-up). Same definition again: verifies the stored bars' hashes,
        appends new sessions, fills ground-truth values that became final; nothing stored
        changes. definition_overrides (keyed 'ground_truth' / 'detector', e.g.
        {"detector": {"sma200": {"window": 150}}}) is a new version, a declared robustness
        check. Changed past bars refuse with reference_bars_changed; new_revision=true with
        revision_reason ('reference_bars_changed' or 'median_recut') relabels them as a new
        input revision. Returns each version's number, build, data range and rows added.
        """
        return await call(
            regime_calendar.build_regime_calendar,
            benchmark_symbol,
            catalog,
            definition_overrides,
            new_revision,
            revision_reason,
            revision_note,
        )

    @server.tool()
    async def get_regime_labels(
        benchmark_symbol: str = "SPY",
        catalog: str = "firstrate-etf",
        label_set: str = "detector",
        start: date | None = None,
        end: date | None = None,
        version: int | None = None,
        as_of_build: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """Stored regime labels by session date, oldest first, optionally as of an earlier
        build; next_start pages on. Without `version` it reads the newest revision of the
        DEFAULT definition, not the highest version number: pass `version` to read an
        override (a declared robustness check). label_set
        'ground_truth' is hindsight: the payload carries hindsight: true and the stamp
        "hindsight, not tradeable"; never use it to trade or filter.
        """
        return await call(
            regime_calendar.get_regime_labels,
            benchmark_symbol,
            catalog,
            label_set,
            start,
            end,
            version,
            as_of_build,
            limit,
        )
