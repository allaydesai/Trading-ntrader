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

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from src.mcp_server.catalogs import timeframe_spec
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable
from src.mcp_server.strategies import StrategyRef, resolve_params, resolve_strategy
from src.models.backtest_request import DEFAULT_FILL_SEED, BacktestRequest
from src.services.crypto.cost_overrides import CryptoOverrideError, apply_crypto_overrides
from src.services.crypto.symbols import CryptoSymbolError, crypto_coin
from src.services.fx.symbols import fx_ticker, resolve_fx_symbol
from src.services.provenance import compute_config_hash
from src.services.sizing_overrides import (
    CONTRACTS_KEY,
    UNITS_KEY,
    coin_for_symbol,
    coin_units,
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
    units: StrictInt | StrictFloat | None = Field(  # strict: True or "2" is refused, not coerced
        default=None,
        gt=0,
        description=(
            "FX: whole base units per entry (100000 = 1 lot); spot crypto: a decimal coin size "
            "on the coin's increment (0.25 = 0.25 BTC)"
        ),
    )
    contracts: StrictInt | None = Field(
        default=None, gt=0, description="Futures only: whole contracts per entry"
    )
    # Spot crypto only, like backtest run --fee-tier/--spread-bps: range and instrument checks
    # are the CLI's own (``cost_overrides``), so both refuse the same values with one message.
    fee_tier: StrictInt | None = Field(
        default=None, description="Spot crypto only: Kraken Pro spot fee tier 1-17 (default 2)"
    )
    spread_bps: StrictInt | StrictFloat | None = Field(
        default=None,
        description="Spot crypto only: the coin's modelled full spread in bps, finite >= 0",
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
                **({"fee_tier": r.crypto_fee_tier} if r.crypto_fee_tier is not None else {}),
                **({"spread_bps": r.crypto_spread_bps} if r.crypto_spread_bps is not None else {}),
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


def _override(ref: StrategyRef, key: str, value: object, applies: bool, why: str) -> dict:
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


def _units_value(units: int | float, is_fx: bool, coin: str | None) -> int | float | Decimal:
    """FX units stay a whole ``int``; a crypto size becomes an exact increment-multiple Decimal."""
    if is_fx and not isinstance(units, int):
        raise ToolFailure(
            "invalid_params",
            f"units for FX is a whole number of base units, got {units!r}",
            fix="Pass an integer, e.g. 100000.",
        )
    if coin is None:
        return units
    try:
        return coin_units(units, coin)
    except ValueError as exc:
        raise ToolFailure("invalid_params", str(exc), fix="Pass a valid coin size.") from None


def _sizing(ref: StrategyRef, spec: BacktestSpec, symbol: str, catalog: str, is_fx: bool):
    """``units``/``contracts`` as ``backtest run --units/--contracts`` apply them (QA fix c)."""
    sizing: dict[str, object] = {}
    if spec.units is not None:
        why = (
            f"units is for FX only, or a spot crypto coin; '{symbol}' is neither in catalog "
            f"'{catalog}' (e.g. symbol EURUSD, catalog firstrate-fx; or BTC, firstrate-crypto)."
        )
        coin = None if is_fx else coin_for_symbol(symbol)
        units = _units_value(spec.units, is_fx, coin)
        sizing |= _override(ref, UNITS_KEY, units, is_fx or coin is not None, why)
    if spec.contracts is not None:
        why = (
            f"contracts applies to futures only; '{symbol}' is not a futures root "
            "(ES, NQ, CL, GC, ZN in catalog firstrate-futures)."
        )
        futures = not is_fx and is_futures_symbol(symbol)
        sizing |= _override(ref, CONTRACTS_KEY, spec.contracts, futures, why)
    return sizing


def _symbol(given: str) -> str:
    """The catalog ticker: an FX pair's (``EURUSD``), a crypto coin's (``BTC``, crypto M5)."""
    try:
        coin = crypto_coin(given)
    except CryptoSymbolError as exc:
        raise ToolFailure(
            "unknown_crypto_pair",
            str(exc),
            fix="Use BTC, ETH or SOL (any spelling: BTC, BTCUSD, BTC/USD, XBTUSD, "
            "BTC/USD.KRAKEN) with catalog firstrate-crypto.",
        ) from None
    return fx_ticker(given) or coin or given.strip().upper()


def _crypto_costs(request: BacktestRequest, spec: BacktestSpec) -> BacktestRequest:
    """``fee_tier``/``spread_bps`` applied exactly as ``backtest run --fee-tier/--spread-bps``."""
    try:
        return apply_crypto_overrides(request, spec.fee_tier, spec.spread_bps)
    except CryptoOverrideError as exc:
        raise ToolFailure(
            "invalid_params",
            str(exc),
            fix="Drop fee_tier/spread_bps, or use them on a spot crypto symbol (BTC, ETH or SOL "
            "in catalog firstrate-crypto) with fee_tier 1-17 and a spread_bps >= 0.",
        ) from None


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
    symbol = _symbol(spec.symbol)
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
    request = _crypto_costs(request, spec)
    return ResolvedRequest(spec, ref.kind, request, compute_config_hash(request))
