"""Resolve a chat-level backtest spec into the ``BacktestRequest`` NTrader runs.

``resolve`` is the one path from a tool's input to a run: ``validate_config``
reports what it produces, ``submit_backtest`` stores its spec, and the worker
re-resolves the same spec, so all three always agree on the run and its hash.
Every run targets a **named catalog**: that loader fails fast on missing data,
never fetching from IBKR or inventing a test instrument.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.mcp_server.catalogs import timeframe_spec
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.strategies import resolve_params, resolve_strategy
from src.models.backtest_request import DEFAULT_FILL_SEED, BacktestRequest
from src.services.provenance import compute_config_hash


class BacktestSpec(BaseModel):
    """What a researcher asks for: strategy, instrument, window, timeframe, catalog, params."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(min_length=1, description="Strategy or benchmark name (list_strategies)")
    symbol: str = Field(min_length=1, description="Ticker, e.g. QQQ")
    start: date = Field(description="First day of the window (inclusive)")
    end: date = Field(description="Last day of the window (inclusive)")
    timeframe: str = Field(default="1-DAY", description="1-DAY, 1-HOUR, 30-MINUTE, 5-MINUTE, ...")
    catalog: str | None = Field(default=None, description="Named catalog; server default if unset")
    params: dict[str, Any] = Field(default_factory=dict, description="Strategy parameters")
    starting_balance: Decimal = Field(default=Decimal("1000000"), gt=0)
    fill_seed: int | None = Field(
        default=None, ge=0, description="Fill-model seed; NTrader's default when unset"
    )


@dataclass(frozen=True)
class ResolvedRequest:
    """A spec resolved to the exact request the engine will run."""

    spec: BacktestSpec
    kind: str
    request: BacktestRequest
    config_hash: str

    def summary(self) -> dict[str, Any]:
        """The resolved run, as plain JSON."""
        r = self.request
        return to_jsonable(
            {
                "strategy": r.strategy_type,
                "kind": self.kind,
                "symbol": r.symbol,
                "start": self.spec.start,
                "end": self.spec.end,
                "bar_type": r.bar_type,
                "catalog": r.catalog_name,
                "params": r.strategy_config,
                "starting_balance": r.starting_balance,
                "config_hash": self.config_hash,
            }
        )


def _window(spec: BacktestSpec) -> tuple[datetime, datetime]:
    if spec.end < spec.start:
        raise ToolFailure(
            "invalid_window",
            f"End {spec.end} is before start {spec.start}.",
            fix="Swap the dates or pick a later end.",
        )
    # End is inclusive: end-of-day UTC, matching the web UI and the CLI.
    return (
        datetime.combine(spec.start, datetime.min.time(), tzinfo=timezone.utc),
        datetime.combine(spec.end, datetime.max.time(), tzinfo=timezone.utc),
    )


def resolve(spec: BacktestSpec, *, default_catalog: str) -> ResolvedRequest:
    """Resolve ``spec``; raises ``ToolFailure`` naming the fix when it cannot run."""
    ref = resolve_strategy(spec.strategy)
    params = resolve_params(ref, spec.params)
    catalog = spec.catalog or default_catalog
    if not catalog:
        raise ToolFailure(
            "no_catalog",
            "No catalog given and the server has no default catalog.",
            fix="Pass catalog (list_catalogs) or set NTRADER_MCP_DEFAULT_CATALOG.",
        )
    start, end = _window(spec)
    symbol = spec.symbol.strip().upper()
    try:
        request = BacktestRequest(
            strategy_type=ref.name,
            strategy_path=ref.strategy_path,
            config_path=ref.config_path,
            strategy_config=params,
            symbol=symbol,
            # Placeholder the named-catalog loader replaces with the DB nautilus_id,
            # exactly as BacktestRequest.from_cli_args does for named catalogs.
            instrument_id=f"{symbol}.NAMED_CATALOG",
            start_date=start,
            end_date=end,
            bar_type=timeframe_spec(spec.timeframe),
            persist=True,
            starting_balance=spec.starting_balance,
            data_source="catalog",
            catalog_name=catalog,
            fill_seed=DEFAULT_FILL_SEED if spec.fill_seed is None else spec.fill_seed,
        )
    except ValidationError as exc:
        raise ToolFailure(
            "invalid_request", str(exc.errors()[0]["msg"]), fix="Correct the request fields."
        ) from None
    return ResolvedRequest(spec, ref.kind, request, compute_config_hash(request))
