"""Build Nautilus ``CurrencyPair`` instruments from crypto coin specs (crypto M1).

The id is ``{BASE}/USD.KRAKEN`` (``BTC/USD.KRAKEN``, decision C2), the id
``KrakenPairMapper.to_nautilus_id`` already gives the Kraken data path, so FirstRate-imported
and Kraken-fetched runs name the same instrument. ``raw_symbol`` is the Kraken altname
(``XBTUSD``). The price increment is the data grid (``10^-precision``), not Kraken's tick,
which stays in ``info`` for live orders. Sizes are fractional coin units (1e-8).

Spot is unlevered: margin fractions are 1 (100% of notional, decision C8). Instrument fees are
zero because commission comes from the venue fee model (M3), charged at a run's fee tier; the
tier is a run parameter, so ``info`` records only the schedule's source and date.
See ``openspec/changes/add-crypto-backtesting/design.md`` section 1.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.currencies import Currency
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.services.crypto.coin_specs import CoinSpec, FeeSchedule, get_fee_schedule

CRYPTO_ASSET_CLASS = "CRYPTO"
KRAKEN_VENUE = "KRAKEN"
UNLEVERED = Decimal(1)


def crypto_instrument_id(spec: CoinSpec) -> InstrumentId:
    """``{BASE}/{QUOTE}.KRAKEN``, e.g. ``BTC/USD.KRAKEN``."""
    return InstrumentId(Symbol(spec.kraken.pair), Venue(KRAKEN_VENUE))


def _resolve_id(spec: CoinSpec, nautilus_id: str | None) -> InstrumentId:
    expected = crypto_instrument_id(spec)
    if nautilus_id is None:
        return expected
    given = InstrumentId.from_str(nautilus_id)
    if given != expected:
        raise ValueError(
            f"Crypto instrument id '{nautilus_id}' does not match the {spec.coin} spec "
            f"(expected '{expected}'). Fix the catalog metadata or the coin spec."
        )
    return given


def _info(spec: CoinSpec, schedule: FeeSchedule) -> dict:
    kraken = spec.kraken
    return {
        "asset_class": CRYPTO_ASSET_CLASS,
        "typical_spread_bps": str(spec.typical_spread_bps),
        "kraken": {
            "pair": kraken.pair,
            "rest": kraken.rest,
            "altname": kraken.altname,
            "min_order_base": str(kraken.min_order_base),
            "min_order_usd": str(kraken.min_order_usd),
            "tick_size": str(kraken.tick_size),
            "discounted_pair": kraken.discounted_pair,
        },
        "fee_schedule": {
            "venue": schedule.venue,
            "source": schedule.source,
            "as_of": schedule.as_of.isoformat(),
            "default_tier": schedule.default_tier,
        },
        "unverified": list(spec.unverified_fields()),
    }


def build_crypto_pair(
    spec: CoinSpec,
    *,
    nautilus_id: str | None = None,
    price_precision: int | None = None,
    fee_schedule: FeeSchedule | None = None,
) -> CurrencyPair:
    """Build the spot ``CurrencyPair`` for ``spec``.

    Args:
        spec: The coin's spec.
        nautilus_id: Catalog id to use; must equal ``{BASE}/USD.KRAKEN``.
        price_precision: Override (e.g. inferred from bars; Nautilus rejects a bar whose
            precision differs from the instrument's); defaults to the spec's.
        fee_schedule: Schedule recorded in ``info``; defaults to the shipped one.
    """
    precision = spec.price_precision if price_precision is None else price_precision
    if precision < 0:
        raise ValueError(f"price_precision must be >= 0, got {precision}")
    schedule = fee_schedule if fee_schedule is not None else get_fee_schedule()
    return CurrencyPair(
        instrument_id=_resolve_id(spec, nautilus_id),
        raw_symbol=Symbol(spec.kraken.altname),
        base_currency=Currency.from_str(spec.base),
        quote_currency=Currency.from_str(spec.quote),
        price_precision=precision,
        size_precision=spec.size_precision,
        price_increment=Price(Decimal(1).scaleb(-precision), precision),
        size_increment=Quantity(spec.size_increment, spec.size_precision),
        lot_size=None,
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=UNLEVERED,
        margin_maint=UNLEVERED,
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        ts_event=0,
        ts_init=0,
        info=_info(spec, schedule),
    )
