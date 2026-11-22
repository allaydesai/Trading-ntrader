"""Build Nautilus ``FuturesContract`` instruments from contract specs (futures M1).

A FirstRate continuous ratio-adjusted series is modelled as one never-expiring contract
with id ``{ROOT}.{VENUE}`` (``ES.CME``). Nautilus' matching engine rejects orders outside
``[activation_ns, expiration_ns]`` and closes the instrument at expiration, so the window is
pinned to 1970-01-01 .. 2100-01-01 UTC; ``info["contract_kind"]`` says what it really is.
See ``openspec/changes/add-futures-instrument-model/design.md``.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.enums import AssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from src.services.futures.contract_specs import FuturesContractSpec

CONTINUOUS_ACTIVATION_NS = 0
CONTINUOUS_EXPIRATION_NS = pd.Timestamp("2100-01-01", tz="UTC").value


def continuous_instrument_id(spec: FuturesContractSpec) -> InstrumentId:
    """``{ROOT}.{VENUE}``, e.g. ``ES.CME``."""
    return InstrumentId(Symbol(spec.root), Venue(spec.venue))


def _increment(precision: int) -> Price:
    return Price(Decimal(1).scaleb(-precision), precision)


def _margin_rates(
    spec: FuturesContractSpec, reference_price: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Dollar margins as Nautilus fractions of notional at ``reference_price``."""
    if reference_price is None:
        return None, None
    if reference_price <= 0:
        raise ValueError(f"reference_price must be positive, got {reference_price}")
    notional = reference_price * spec.multiplier
    return spec.margin.initial_usd / notional, spec.margin.maintenance_usd / notional


def _info(spec: FuturesContractSpec) -> dict:
    return {
        "contract_kind": "continuous",
        "adjustment": "ratio",
        "tick_size": str(spec.tick_size),
        "margin_initial_usd": str(spec.margin.initial_usd),
        "margin_maintenance_usd": str(spec.margin.maintenance_usd),
        "margin_as_of": spec.margin.as_of.isoformat(),
        "commission_per_contract_usd": str(spec.commission_per_contract_usd),
        "ib": spec.ib.model_dump(),
    }


def _resolve_id(spec: FuturesContractSpec, nautilus_id: str | None) -> InstrumentId:
    expected = continuous_instrument_id(spec)
    if nautilus_id is None:
        return expected
    given = InstrumentId.from_str(nautilus_id)
    if given != expected:
        raise ValueError(
            f"Futures instrument id '{nautilus_id}' does not match the {spec.root} spec "
            f"(expected '{expected}'). Fix the catalog metadata or the contract spec."
        )
    return given


def build_futures_contract(
    spec: FuturesContractSpec,
    *,
    nautilus_id: str | None = None,
    price_precision: int | None = None,
    reference_price: Decimal | None = None,
) -> FuturesContract:
    """Build the continuous-series ``FuturesContract`` for ``spec``.

    Args:
        spec: The root's contract spec.
        nautilus_id: Catalog id to use; must equal ``{ROOT}.{VENUE}``.
        price_precision: Override (e.g. inferred from bars); defaults to the spec's
            ``continuous_price_precision``. The increment is ``10^-precision`` because
            ratio-adjusted prices are off the exchange tick grid.
        reference_price: When given, dollar margins are converted to Nautilus'
            fraction-of-notional ``margin_init``/``margin_maint``; otherwise left unset.
    """
    precision = spec.continuous_price_precision if price_precision is None else price_precision
    if precision < 0:
        raise ValueError(f"price_precision must be >= 0, got {precision}")
    margin_init, margin_maint = _margin_rates(spec, reference_price)
    instrument_id = _resolve_id(spec, nautilus_id)
    return FuturesContract(
        instrument_id=instrument_id,
        raw_symbol=Symbol(spec.root),
        asset_class=AssetClass[spec.asset_class],
        currency=Currency.from_str(spec.currency),
        price_precision=precision,
        price_increment=_increment(precision),
        multiplier=Quantity(spec.multiplier, 0),
        lot_size=Quantity.from_int(1),
        underlying=spec.underlying,
        activation_ns=CONTINUOUS_ACTIVATION_NS,
        expiration_ns=CONTINUOUS_EXPIRATION_NS,
        ts_event=0,
        ts_init=0,
        margin_init=margin_init,
        margin_maint=margin_maint,
        info=_info(spec),
    )
