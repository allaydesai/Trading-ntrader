"""Fixed-size overrides a run may set: FX and crypto ``units`` and futures ``contracts``.

Shared by ``backtest run --units/--contracts`` and the research MCP's ``units``/``contracts``
arguments, so both accept and refuse the same requests. Whether a strategy takes one is read
from its config class (``SMAConfig.units``), not its name, so a new strategy picks them up
without an edit here.

``units`` is whole base units for FX and a decimal coin size for spot crypto (crypto M4): a
positive multiple of the coin's size increment, at or above Kraken's minimum order.
"""

from __future__ import annotations

import importlib
from decimal import Decimal, InvalidOperation

#: The setting ``--contracts`` / MCP ``contracts`` writes into a strategy's config.
CONTRACTS_KEY = "contracts"
#: The setting ``--units`` / MCP ``units`` writes into a strategy's config.
UNITS_KEY = "units"


def config_declares(config_path: str | None, field_name: str) -> bool:
    """Whether the strategy config class at ``module:Class`` has ``field_name``."""
    if not config_path or ":" not in config_path:
        return False
    module_name, class_name = config_path.split(":", 1)
    config_cls = getattr(importlib.import_module(module_name), class_name, None)
    return field_name in getattr(config_cls, "__struct_fields__", ())


def strategies_declaring(field_name: str) -> str:
    """Comma-separated registered strategies whose config has ``field_name``."""
    from src.core.strategy_registry import StrategyRegistry

    names = sorted(
        name
        for name, definition in StrategyRegistry.get_all().items()
        if config_declares(definition.config_path, field_name)
    )
    return ", ".join(names) or "none"


def is_futures_symbol(symbol: str) -> bool:
    """A root in the futures contract specs (ES, NQ, CL, GC, ZN), dotted or not."""
    from src.services.futures.contract_specs import load_contract_specs

    return symbol.split(".")[0].upper() in load_contract_specs().roots


def check_contracts_instrument(strategy_config: dict, instrument: object) -> None:
    """Refuse a ``contracts`` size (flag or config file) that isn't N >= 1 on a futures contract.

    Raises:
        ValueError: ``contracts`` is not a positive whole number, or the instrument has no
            multiplier (not futures).
    """
    contracts = strategy_config.get(CONTRACTS_KEY)
    if contracts is None:
        return
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 1:
        raise ValueError(f"contracts must be a positive whole number, got {contracts!r}")
    from src.core.position_sizing import instrument_multiplier

    if instrument_multiplier(instrument) == 1:
        raise ValueError(
            f"contracts applies to futures only; {getattr(instrument, 'id', instrument)} "
            "is not a futures contract"
        )


def coin_for_symbol(symbol: str) -> str | None:
    """The coin (``BTC``) when ``symbol`` names one with a coin spec, else ``None``."""
    from src.services.crypto.coin_specs import coin_names

    coin = symbol.strip().upper()
    return coin if coin in coin_names() else None


def crypto_units(units: object, coin: str, increment: Decimal, minimum: Decimal) -> Decimal:
    """``units`` as an exact Decimal coin size: a positive ``increment`` multiple >= ``minimum``.

    Raises:
        ValueError: Not a positive number, off the increment, or under the minimum order.
    """
    from src.core.position_sizing import whole_units

    try:
        if isinstance(units, bool) or not isinstance(units, (int, float, Decimal, str)):
            raise InvalidOperation
        value = Decimal(str(units))
    except InvalidOperation:
        value = Decimal("NaN")
    if not value.is_finite() or value <= 0:
        raise ValueError(f"units must be a positive decimal number of {coin}, got {units!r}")
    if whole_units(value, increment) != value:
        raise ValueError(
            f"units {value:f} is not a multiple of the {coin} size increment {increment:f}"
        )
    if value < minimum:
        raise ValueError(f"units {value:f} is under the Kraken {coin} minimum order {minimum:f}")
    return value


def coin_units(units: object, coin: str) -> Decimal:
    """:func:`crypto_units` at ``coin``'s spec increment and Kraken base minimum."""
    from src.services.crypto.coin_specs import get_coin_spec

    spec = get_coin_spec(coin)
    return crypto_units(units, coin, spec.size_increment, spec.kraken.min_order_base)


def check_units_instrument(strategy_config: dict, instrument: object) -> None:
    """Refuse a ``units`` size (flag or config file) that does not suit the instrument.

    FX takes N >= 1 whole units; spot crypto a decimal size (:func:`crypto_units`).

    Raises:
        ValueError: ``units`` is not a valid size for the pair, or the instrument is neither
            FX nor spot crypto.
    """
    units = strategy_config.get(UNITS_KEY)
    if units is None:
        return
    from src.core.position_sizing import crypto_minimum, is_crypto

    if is_crypto(instrument):
        coin = str(getattr(getattr(instrument, "base_currency"), "code"))
        step = Decimal(str(getattr(instrument, "size_increment")))
        crypto_units(units, coin, step, crypto_minimum(instrument)[0])
        return
    if isinstance(units, bool) or not isinstance(units, int) or units < 1:
        raise ValueError(f"units must be a positive whole number, got {units!r}")
    from src.core.position_sizing import is_fx_pair

    if not is_fx_pair(instrument):
        raise ValueError(
            f"units applies to FX only (or spot crypto); {getattr(instrument, 'id', instrument)} "
            "is not an FX or crypto pair"
        )
