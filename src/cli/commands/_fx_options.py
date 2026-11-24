"""``backtest run``'s FX handling (M5): symbol and bar type, ``--units``, ``--spread-pips``.

- ``EURUSD``/``EUR/USD`` resolve to the catalog ticker, and an FX catalog row loads ``MID`` bars
  (:mod:`src.services.fx.symbols`), in CLI and config mode alike.
- ``--units N`` trades exactly N base units per entry; only for FX pairs (whole units) and
  spot crypto coins (a decimal coin size, crypto M4) and strategies whose config declares
  ``units`` (``SMAConfig.units``), like ``--contracts`` for futures.
- ``--spread-pips X`` replaces the pair spec's ``typical_spread_pips`` for the run (design D7):
  it is part of the run's identity (``BacktestRequest.fx_spread_pips``) and shows in
  ``config_snapshot.fx_pairs[...].typical_spread_pips``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import click

from src.models.backtest_request import BacktestRequest
from src.services.fx.symbols import resolve_fx_symbol
from src.services.sizing_overrides import (
    UNITS_KEY,
    coin_for_symbol,
    coin_units,
    config_declares,
    strategies_declaring,
)


class PositiveDecimal(click.ParamType):
    """A decimal > 0 (``--units 0.25``), parsed exactly: never through a float."""

    name = "decimal"

    def convert(self, value, param, ctx):  # noqa: D102
        if isinstance(value, Decimal):
            return value
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation:
            self.fail(f"{value!r} is not a decimal number", param, ctx)
        if not number.is_finite() or number <= 0:
            self.fail(f"{value!r} is not a positive decimal number", param, ctx)
        return number


def resolve_fx_request(request: BacktestRequest) -> tuple[BacktestRequest, bool]:
    """The request with an FX symbol's catalog ticker and ``MID`` bar type; and whether FX."""
    resolved = resolve_fx_symbol(request.catalog_name, request.symbol, request.bar_type)
    if resolved is None:
        return request, False
    ticker, bar_type = resolved
    instrument_id = request.instrument_id.replace(request.symbol, ticker, 1)
    update = {"symbol": ticker, "instrument_id": instrument_id, "bar_type": bar_type}
    return request.model_copy(update=update), True


def _units_value(request: BacktestRequest, units: Decimal, is_fx: bool) -> int | Decimal:
    """Whole base units for FX (an ``int``, as before crypto M4); an exact coin size for crypto."""
    if is_fx:
        if units != units.to_integral_value():
            raise click.UsageError(f"--units for FX is a whole number of base units, got {units:f}")
        return int(units)
    if (coin := coin_for_symbol(request.symbol)) is None:
        raise click.UsageError(
            f"--units is for FX only, or a spot crypto coin; '{request.symbol}' is neither in "
            f"catalog '{request.catalog_name}' (use e.g. --symbol EURUSD --catalog "
            "firstrate-fx, or --symbol BTC --catalog firstrate-crypto)."
        )
    try:
        return coin_units(units, coin)
    except ValueError as exc:
        raise click.UsageError(f"--units: {exc}") from None


def _apply_units(request: BacktestRequest, units: Decimal, is_fx: bool) -> BacktestRequest:
    if not config_declares(request.config_path, UNITS_KEY):
        supported = strategies_declaring(UNITS_KEY)
        raise click.UsageError(
            f"--units is not supported by strategy '{request.strategy_type}': its config has "
            f"no 'units' setting. Strategies with one: {supported}."
        )
    value = _units_value(request, Decimal(str(units)), is_fx)
    config = {**request.strategy_config, UNITS_KEY: value}
    return request.model_copy(update={"strategy_config": config})


def apply_fx_options(
    request: BacktestRequest,
    units: Decimal | int | None = None,
    spread_pips: float | None = None,
) -> BacktestRequest:
    """Resolve an FX symbol and apply ``--units``/``--spread-pips``.

    Raises:
        click.UsageError: ``--units`` for a symbol that is neither FX nor a crypto coin, a
            fractional FX size, a crypto size off the coin's increment or under its minimum,
            or a strategy whose config has no ``units`` setting; ``--spread-pips`` for a
            non-FX symbol.
    """
    request, is_fx = resolve_fx_request(request)
    if units is not None:
        request = _apply_units(request, units, is_fx)
    if spread_pips is not None:
        if not is_fx:
            raise click.UsageError(
                f"--spread-pips is for FX only; '{request.symbol}' is not an FX pair."
            )
        request = request.model_copy(update={"fx_spread_pips": Decimal(str(spread_pips))})
    return request


def with_spread_pips(instrument, spread_pips: Decimal | None):
    """The FX pair rebuilt with ``typical_spread_pips`` replaced; unchanged when ``None``."""
    from src.core.position_sizing import is_fx_pair

    if spread_pips is None or not is_fx_pair(instrument):
        return instrument
    from src.core.fx_rates import fx_ticker
    from src.services.fx.instrument_builder import build_fx_pair
    from src.services.fx.pair_specs import get_pair_spec

    spec = get_pair_spec(fx_ticker(instrument))
    spec = spec.model_copy(update={"typical_spread_pips": spread_pips})
    return build_fx_pair(
        spec, nautilus_id=str(instrument.id), price_precision=instrument.price_precision
    )
