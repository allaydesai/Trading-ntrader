"""Build Nautilus ``CurrencyPair`` instruments from FX pair specs (FX M1).

The id is ``{BASE}/{QUOTE}.IDEALPRO`` (``EUR/USD.IDEALPRO``), exactly what the IB adapter's
IB_SIMPLIFIED symbology produces and parses (``RE_CASH``), so a backtest bar type and a live
subscription name the same instrument (decision D1). The price increment is the data grid
(``10^-precision``), not the IB minimum tick, which stays in ``info`` for live orders.
Margins are the spec percentages as Nautilus fractions of notional; fees are zero because
commission comes from the venue fee model (``src/core/fx_costs.py``, M4).
See ``openspec/changes/archive/2026-11-23-add-fx-backtesting/design.md`` section 1.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.services.fx.pair_specs import (
    FxCommissionSpec,
    FxPairSpec,
    decimal_places,
    get_commission_spec,
)


def fx_instrument_id(spec: FxPairSpec) -> InstrumentId:
    """``{BASE}/{QUOTE}.{VENUE}``, e.g. ``EUR/USD.IDEALPRO``."""
    return InstrumentId(Symbol(f"{spec.base}/{spec.quote}"), Venue(spec.venue))


def _resolve_id(spec: FxPairSpec, nautilus_id: str | None) -> InstrumentId:
    expected = fx_instrument_id(spec)
    if nautilus_id is None:
        return expected
    given = InstrumentId.from_str(nautilus_id)
    if given != expected:
        raise ValueError(
            f"FX instrument id '{nautilus_id}' does not match the {spec.pair} spec "
            f"(expected '{expected}'). Fix the catalog metadata or the pair spec."
        )
    return given


def _info(spec: FxPairSpec, commission: FxCommissionSpec) -> dict:
    return {
        "asset_class": "FX",
        "pip_size": str(spec.pip_size),
        "min_tick": str(spec.min_tick),
        "min_order_usd": str(spec.min_order_usd),
        "typical_spread_pips": str(spec.typical_spread_pips),
        "margin_pct": str(spec.margin_pct),
        "maint_margin_pct": str(spec.maint_margin_pct),
        "margin_as_of": spec.margin_as_of.isoformat(),
        "commission": {
            "rate_bps": str(commission.rate_bps),
            "min_per_order_usd": str(commission.min_per_order_usd),
            "as_of": commission.as_of.isoformat(),
        },
        "ib": spec.ib.model_dump(),
        "unverified": list(spec.unverified_fields()),
    }


def build_fx_pair(
    spec: FxPairSpec,
    *,
    nautilus_id: str | None = None,
    price_precision: int | None = None,
    commission: FxCommissionSpec | None = None,
) -> CurrencyPair:
    """Build the spot ``CurrencyPair`` for ``spec``.

    Args:
        spec: The pair's spec.
        nautilus_id: Catalog id to use; must equal ``{BASE}/{QUOTE}.IDEALPRO``.
        price_precision: Override (e.g. inferred from bars; Nautilus rejects a bar whose
            precision differs from the instrument's); defaults to the spec's.
        commission: Commission terms recorded in ``info``; defaults to the shipped schedule.
    """
    precision = spec.price_precision if price_precision is None else price_precision
    if precision < 0:
        raise ValueError(f"price_precision must be >= 0, got {precision}")
    size_precision = decimal_places(spec.size_increment)
    terms = commission if commission is not None else get_commission_spec()
    return CurrencyPair(
        instrument_id=_resolve_id(spec, nautilus_id),
        raw_symbol=Symbol(spec.ib.local_symbol),
        base_currency=Currency.from_str(spec.base),
        quote_currency=Currency.from_str(spec.quote),
        price_precision=precision,
        size_precision=size_precision,
        price_increment=Price(Decimal(1).scaleb(-precision), precision),
        size_increment=Quantity(spec.size_increment, size_precision),
        lot_size=None,
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=spec.margin_pct / Decimal(100),
        margin_maint=spec.maint_margin_pct / Decimal(100),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        ts_event=0,
        ts_init=0,
        info=_info(spec, terms),
    )
