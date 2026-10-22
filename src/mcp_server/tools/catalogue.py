"""Read tools: strategies, catalogs, coverage and request validation (S1.1, S1.4)."""

from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server import MCPServer

from src.mcp_server import catalogs, strategies, validation
from src.mcp_server.context import ServerContext
from src.mcp_server.tools import build_spec, call

TOOL_NAMES = (
    "list_strategies",
    "describe_strategy",
    "list_catalogs",
    "catalog_availability",
    "validate_config",
)


def register(server: MCPServer, ctx: ServerContext) -> None:
    """Add the catalogue tools to ``server``."""

    @server.tool()
    async def list_strategies() -> dict[str, Any]:
        """List runnable strategies and backtest-only benchmarks (e.g. buy_and_hold)."""
        return await call(lambda: {"strategies": strategies.list_strategies()})

    @server.tool()
    async def describe_strategy(name: str) -> dict[str, Any]:
        """Parameter schema, resolved defaults and an example request for one strategy."""
        return await call(strategies.describe_strategy, name)

    @server.tool()
    async def list_catalogs() -> dict[str, Any]:
        """Named Parquet catalogs with their instrument counts."""
        return await call(lambda: {"catalogs": catalogs.list_catalogs()})

    @server.tool()
    async def catalog_availability(symbol: str, catalog: str) -> dict[str, Any]:
        """Bars available for a symbol in a catalog: span and bar count per timeframe."""
        return await call(catalogs.catalog_availability, symbol, catalog)

    @server.tool()
    async def validate_config(
        strategy: str,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "1-DAY",
        catalog: str | None = None,
        params: dict[str, Any] | None = None,
        starting_balance: Decimal = Decimal("1000000"),
    ) -> dict[str, Any]:
        """Resolve a backtest request and check its data without running it.

        Returns the resolved run (full parameters, config hash), the data coverage,
        and every error with its fix. Submit only when ok is true.
        """

        def run() -> dict[str, Any]:
            spec = build_spec(
                strategy=strategy,
                symbol=symbol,
                start=start,
                end=end,
                timeframe=timeframe,
                catalog=catalog,
                params=params or {},
                starting_balance=starting_balance,
            )
            return validation.validate(
                spec, default_catalog=ctx.settings.resolved_default_catalog()
            )

        return await call(run)
