"""``backtest run``'s FX handling (M5): symbol and bar type, ``--units``, ``--spread-pips``.

- ``EURUSD``/``EUR/USD`` resolve to the catalog ticker, and an FX catalog row loads ``MID`` bars
  (:mod:`src.services.fx.symbols`), in CLI and config mode alike.
- ``--units N`` trades exactly N base units per entry; only for FX pairs and strategies whose
  config declares ``units`` (``SMAConfig.units``), like ``--contracts`` for futures.
- ``--spread-pips X`` replaces the pair spec's ``typical_spread_pips`` for the run (design D7):
  it is part of the run's identity (``BacktestRequest.fx_spread_pips``) and shows in
  ``config_snapshot.fx_pairs[...].typical_spread_pips``.
"""

from __future__ import annotations

from decimal import Decimal

import click

from src.cli.commands._strategy_params import _config_declares, _strategies_declaring
from src.models.backtest_request import BacktestRequest
from src.services.fx.symbols import resolve_fx_symbol

#: The setting ``--units`` writes into a strategy's config (``SMAConfig.units``).
UNITS_KEY = "units"


def resolve_fx_request(request: BacktestRequest) -> tuple[BacktestRequest, bool]:
    """The request with an FX symbol's catalog ticker and ``MID`` bar type; and whether FX."""
    resolved = resolve_fx_symbol(request.catalog_name, request.symbol, request.bar_type)
    if resolved is None:
        return request, False
    ticker, bar_type = resolved
    instrument_id = request.instrument_id.replace(request.symbol, ticker, 1)
    update = {"symbol": ticker, "instrument_id": instrument_id, "bar_type": bar_type}
    return request.model_copy(update=update), True


def _apply_units(request: BacktestRequest, units: int, is_fx: bool) -> BacktestRequest:
    if not _config_declares(request.config_path, UNITS_KEY):
        supported = _strategies_declaring(UNITS_KEY)
        raise click.UsageError(
            f"--units is not supported by strategy '{request.strategy_type}': its config has "
            f"no 'units' setting. Strategies with one: {supported}."
        )
    if not is_fx:
        raise click.UsageError(
            f"--units is for FX only; '{request.symbol}' is not an FX pair in catalog "
            f"'{request.catalog_name}' (use e.g. --symbol EURUSD --catalog firstrate-fx)."
        )
    config = {**request.strategy_config, UNITS_KEY: units}
    return request.model_copy(update={"strategy_config": config})


def apply_fx_options(
    request: BacktestRequest, units: int | None = None, spread_pips: float | None = None
) -> BacktestRequest:
    """Resolve an FX symbol and apply ``--units``/``--spread-pips``.

    Raises:
        click.UsageError: ``--units`` or ``--spread-pips`` for a non-FX symbol, or ``--units``
            for a strategy whose config has no ``units`` setting.
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


def check_units_instrument(strategy_config: dict, instrument: object) -> None:
    """Refuse a ``units`` size (flag or config file) that isn't N >= 1 on an FX pair.

    Raises:
        ValueError: ``units`` is not a positive whole number, or the instrument is not FX.
    """
    units = strategy_config.get(UNITS_KEY)
    if units is None:
        return
    if isinstance(units, bool) or not isinstance(units, int) or units < 1:
        raise ValueError(f"units must be a positive whole number, got {units!r}")
    from src.core.position_sizing import is_fx_pair

    if not is_fx_pair(instrument):
        raise ValueError(
            f"units applies to FX only; {getattr(instrument, 'id', instrument)} is not an FX pair"
        )


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
