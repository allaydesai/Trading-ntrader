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
from src.mcp_server.strategies import StrategyRef, resolve_params, resolve_strategy
from src.models.backtest_request import DEFAULT_FILL_SEED, BacktestRequest
from src.services.fx.symbols import fx_ticker, resolve_fx_symbol
from src.services.provenance import compute_config_hash
from src.services.sizing_overrides import (
    CONTRACTS_KEY,
    UNITS_KEY,
    config_declares,
    is_futures_symbol,
    strategies_declaring,
)


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
    cost_multiplier: float = Field(
        default=1.0, ge=0, description="Commission scale (cost stress); 1 is the broker's schedule"
    )
    units: int | None = Field(
        default=None, gt=0, description="FX only: base units per entry (100000 = 1 lot)"
    )
    contracts: int | None = Field(
        default=None, gt=0, description="Futures only: whole contracts per entry"
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
                **({"cost_multiplier": r.cost_multiplier} if r.cost_multiplier != 1 else {}),
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


def _override(ref: StrategyRef, key: str, value: int, applies: bool, why: str) -> dict:
    """``{key: value}`` when the strategy's config takes ``key`` and the symbol suits it."""
    if not config_declares(ref.config_path, key):
        raise ToolFailure(
            "invalid_params",
            f"{key} is not supported by strategy '{ref.name}': its config has no '{key}' setting.",
            fix=f"Strategies with one: {strategies_declaring(key)}.",
        )
    if not applies:
        raise ToolFailure("invalid_params", why, fix=f"Drop {key}, or choose a matching symbol.")
    return {key: value}


def _sizing(ref: StrategyRef, spec: BacktestSpec, symbol: str, catalog: str, is_fx: bool):
    """``units``/``contracts`` as ``backtest run --units/--contracts`` apply them (QA fix c)."""
    sizing: dict[str, int] = {}
    if spec.units is not None:
        why = (
            f"units is for FX only; '{symbol}' is not an FX pair in catalog '{catalog}' "
            "(e.g. symbol EURUSD, catalog firstrate-fx)."
        )
        sizing |= _override(ref, UNITS_KEY, spec.units, is_fx, why)
    if spec.contracts is not None:
        why = (
            f"contracts applies to futures only; '{symbol}' is not a futures root "
            "(ES, NQ, CL, GC, ZN in catalog firstrate-futures)."
        )
        futures = not is_fx and is_futures_symbol(symbol)
        sizing |= _override(ref, CONTRACTS_KEY, spec.contracts, futures, why)
    return sizing


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
    symbol = fx_ticker(spec.symbol) or spec.symbol.strip().upper()
    bar_type = timeframe_spec(spec.timeframe)
    # FX (M5): an FX catalog row has MID bars, not the -LAST default
    if fx := resolve_fx_symbol(catalog, symbol, bar_type):
        symbol, bar_type = fx
    params = {**params, **_sizing(ref, spec, symbol, catalog, fx is not None)}
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
            bar_type=bar_type,
            persist=True,
            starting_balance=spec.starting_balance,
            data_source="catalog",
            catalog_name=catalog,
            fill_seed=DEFAULT_FILL_SEED if spec.fill_seed is None else spec.fill_seed,
            cost_multiplier=spec.cost_multiplier,
        )
    except ValidationError as exc:
        raise ToolFailure(
            "invalid_request", str(exc.errors()[0]["msg"]), fix="Correct the request fields."
        ) from None
    return ResolvedRequest(spec, ref.kind, request, compute_config_hash(request))
