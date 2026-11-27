"""Regime tools: build the stored calendar, read its labels (phase 1), profile runs (phase 2).

Labels are computed only by ``build_regime_calendar`` and only ever read by date
afterwards. Ground-truth output is hindsight: it carries ``hindsight: true`` and
``stamp: "hindsight, not tradeable"``, and no strategy, config, sweep or filter takes it.
"""

from datetime import date
from typing import Any

from mcp.server import MCPServer

from src.mcp_server.analysis import regime_calendar, regime_reports
from src.mcp_server.context import ServerContext
from src.mcp_server.tools import call

TOOL_NAMES = ("build_regime_calendar", "get_regime_labels", "run_regime_attribution")


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

    @server.tool()
    async def run_regime_attribution(
        run_ids: list[str],
        label_set: str = "detector",
        benchmark_symbol: str = "SPY",
        catalog: str = "firstrate-etf",
        lag_days: int | None = None,
        min_days: int = 60,
        min_trades: int = 20,
        bootstrap_samples: int = 1000,
        seed: int = 0,
        calendar_version: int | None = None,
        as_of_build: int | None = None,
        drift_scaling: str = "side",
    ) -> dict[str, Any]:
        """Profile 1-10 stored runs by regime against the stored calendar; stores the report.

        Daily view per label family (ground_truth: leg_minor/intermediate/major up vs down,
        ranging, volatility, and the widest-gap scale x volatility as 'cross'; detector:
        sma200): days, time share, P&L and share, annualised return and volatility, Sharpe,
        worst day, a 90% stationary-bootstrap interval on the mean daily return (mean block
        10 days, seeded), exposure, the reference's return and the drift it explains, trades
        entered and an insufficient flag (< min_days days or < min_trades trades). Each
        family has an outcome: strong_dependence, no_dependence or insufficient_evidence.
        Plus a trade view (closed trades by entry-day label), with/counter-trend alignment
        and, for 2+ runs, correlation overall and per bucket. Every family's rows sum to
        the run's P&L. lag_days: detector default 1; ground_truth takes none (refused).
        Runs must have a mark-to-market curve (curve_missing otherwise). Ground-truth
        output is hindsight: hindsight: true and stamp "hindsight, not tradeable". The
        report records calendar version, build_no and labels_hash; pass calendar_version
        and as_of_build to rerun it as of that snapshot. export_results(regime_report=...)
        files it in the vault. drift_scaling='position' weights the drift by the position
        actually carried in (quantity x previous close / previous equity; adds
        mean_position_weight) instead of +1/-1/0 by side; it needs the run to trade the
        reference itself, unlevered (drift_scaling_unavailable otherwise).
        """
        return await call(
            regime_reports.run_regime_attribution,
            run_ids,
            label_set,
            benchmark_symbol,
            catalog,
            lag_days,
            min_days,
            min_trades,
            bootstrap_samples,
            seed,
            calendar_version,
            as_of_build,
            drift_scaling,
        )
