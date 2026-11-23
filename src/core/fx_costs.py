"""FX backtest costs: IB spot commission plus the modelled half-spread, in USD (FX M4).

An FX venue's account is single-currency USD (``src/core/backtest_venue.py``). Per fill,
:class:`FxCommissionModel` charges (design section 4, decisions D6 and D7):

- **commission**: ``max(rate_bps / 10,000 x trade value in USD, min_per_order_usd)``, the
  minimum applied **once per order**: Nautilus calls the fee model per fill, so the model keeps
  what it charged each ``client_order_id`` and charges a later partial fill only the top-up;
- **spread**: ``quantity x typical_spread_pips x pip_size / 2`` in the quote currency, converted
  to USD. The FirstRate series is treated as mid (D3), so fills stay at the bar's prices and
  each side pays half the modelled spread as a cost (the equity ``slippage_per_share``
  precedent).

Both components are scaled by the run's cost multiplier and rounded to cents separately, so
the per-pair totals the model records (``config_snapshot.fx_pairs[...]["costs_usd"]``) add up
to the commissions on the fills exactly. Trade value in USD: the quantity for a USD-base pair
(USDJPY), ``quantity x price`` for a USD-quote pair, and for a cross the cache's quote/USD rate,
the accounts manager's own lookup. A cross fill without a rate is charged nothing and recorded:
the account misses the same rate, so ``src/core/fx_results.py`` fails the run.

The terms come from the ``CurrencyPair``'s ``info`` (``build_fx_pair`` copies them from
``pair_specs.yaml``), like the futures models read theirs. **Every value is UNVERIFIED** until
Allay confirms it against the account (tasks.md 4.7; ``docs/agent/fx-backtesting.md``).

Margin needs no model here: the pair's ``margin_init`` is the spec percentage, which Nautilus'
default ``LeveragedMarginModel`` applies to notional; ``src/core/margin_gate.py`` enforces it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

import structlog
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money

from src.core.fx_rates import ACCOUNT_CURRENCY, conversion_pair, is_fx, usd_per_quote

logger = structlog.get_logger(__name__)

#: Cache key (Nautilus' general object store) holding the run's per-pair cost totals as JSON.
FX_COSTS_KEY = "ntrader.fx_costs"
CENT = Decimal("0.01")
ZERO = Decimal(0)
BPS = Decimal(10_000)


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class FxTerms:
    """One pair's cost and margin terms, as the run used them (all UNVERIFIED, see module)."""

    base: str
    quote: str
    pip_size: Decimal
    spread_pips: Decimal
    rate_bps: Decimal
    min_per_order_usd: Decimal
    margin_pct: Decimal
    maint_margin_pct: Decimal
    min_order_usd: Decimal
    info: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def half_spread(self) -> Decimal:
        """Quote-currency cost per base unit per side: half the modelled spread."""
        return self.spread_pips * self.pip_size / 2

    @property
    def margin_fraction(self) -> Decimal:
        """Initial margin as a fraction of notional (3% is 0.03)."""
        return self.margin_pct / 100

    def as_snapshot(self) -> dict[str, Any]:
        """JSON-safe terms for ``config_snapshot.fx_pairs`` (M5 reports against them)."""
        return {
            "base": self.base,
            "quote": self.quote,
            "pip_size": str(self.pip_size),
            "typical_spread_pips": str(self.spread_pips),
            "margin_pct": str(self.margin_pct),
            "maint_margin_pct": str(self.maint_margin_pct),
            "margin_as_of": self.info.get("margin_as_of"),
            "min_order_usd": str(self.min_order_usd),
            "commission": dict(self.info.get("commission", {})),
            "unverified": list(self.info.get("unverified", [])),
        }


def fx_terms(instrument: Instrument) -> FxTerms | None:
    """The pair's FX terms, or ``None`` for anything that is not a spot FX pair.

    Raises:
        ValueError: An FX pair whose ``info`` lacks the spec terms.
    """
    if not is_fx(instrument):
        return None
    info = instrument.info or {}
    try:
        commission = info["commission"]
        return FxTerms(
            base=instrument.base_currency.code,
            quote=instrument.quote_currency.code,
            pip_size=Decimal(str(info["pip_size"])),
            spread_pips=Decimal(str(info["typical_spread_pips"])),
            rate_bps=Decimal(str(commission["rate_bps"])),
            min_per_order_usd=Decimal(str(commission["min_per_order_usd"])),
            margin_pct=Decimal(str(info["margin_pct"])),
            maint_margin_pct=Decimal(str(info["maint_margin_pct"])),
            min_order_usd=Decimal(str(info["min_order_usd"])),
            info=info,
        )
    except KeyError as missing:
        raise ValueError(
            f"FX pair {instrument.id} has no {missing} in its info; build it with "
            "build_fx_pair() so costs and margin come from the pair spec."
        ) from None


def fx_terms_by_instrument(instruments: Iterable[Instrument]) -> dict[InstrumentId, FxTerms]:
    """``{instrument_id: terms}`` for the spot FX pairs among ``instruments``."""
    return {i.id: t for i in instruments if (t := fx_terms(i)) is not None}


@dataclass
class FxCosts:
    """USD charged on one pair so far: commission and modelled spread."""

    commission: Decimal = ZERO
    spread: Decimal = ZERO

    def as_snapshot(self) -> dict[str, str]:
        """``{"commission": "2.20", "spread": "2.50"}``."""
        return {"commission": str(_cents(self.commission)), "spread": str(_cents(self.spread))}


@dataclass
class _OrderCharges:
    """One order's running totals, so partial fills add up to the order's exact charge.

    The fee model is called per fill; IB charges per order. Each fill adds its USD value and
    its unrounded USD half-spread, then pays the order's cents-rounded totals so far less what
    earlier fills paid: ``max(bps, minimum)`` once, and the spread without per-fill rounding.
    """

    rate_bps: Decimal
    value_usd: Decimal = ZERO
    spread_usd: Decimal = ZERO
    commission_paid: Decimal = ZERO
    spread_paid: Decimal = ZERO

    def add(self, value_usd: Decimal, spread_usd: Decimal) -> None:
        """Record one fill's USD trade value and unrounded USD half-spread."""
        self.value_usd += value_usd
        self.spread_usd += spread_usd

    def top_up(self, minimum_usd: Decimal, scale: Decimal) -> tuple[Decimal, Decimal]:
        """``(commission, spread)`` this fill owes: the order's totals so far less paid."""
        commission_due = _cents(max(self.value_usd * self.rate_bps / BPS, minimum_usd) * scale)
        spread_due = _cents(self.spread_usd * scale)
        owed = (commission_due - self.commission_paid, spread_due - self.spread_paid)
        self.commission_paid, self.spread_paid = commission_due, spread_due
        return owed


class FxCommissionModel(FeeModel):
    """IB spot FX commission (bps, USD minimum per order) plus half the spread, in USD."""

    def __init__(
        self,
        terms: Mapping[InstrumentId, FxTerms],
        cache=None,
        scale: Decimal = Decimal("1"),
    ) -> None:
        """``terms`` per pair; ``cache`` for cross rates and the totals; ``scale`` is stress."""
        super().__init__()
        if scale < 0:
            raise ValueError("commission scale cannot be negative")
        self.terms = dict(terms)
        self.scale = scale
        self.costs: dict[str, FxCosts] = {str(iid): FxCosts() for iid in self.terms}
        self.misses: list[str] = []
        self._cache = cache
        self._orders: dict[str, _OrderCharges] = {}
        self._odd_lots: set[str] = set()

    def get_commission(self, order, fill_qty, fill_px, instrument) -> Money:  # noqa: D102
        terms = self.terms.get(instrument.id)
        if terms is None:
            raise ValueError(f"No FX commission terms for {instrument.id}")
        qty, px = fill_qty.as_decimal(), fill_px.as_decimal()
        rate = usd_per_quote(instrument, px, self._cache, order.side)
        if rate is None:
            self.misses.append(f"{instrument.id} {order.client_order_id}: no {terms.quote}/USD")
            return Money(0, ACCOUNT_CURRENCY)
        self._warn_odd_lot(instrument, terms, order.quantity.as_decimal() * px * rate)
        charges = self._orders.setdefault(str(order.client_order_id), _OrderCharges(terms.rate_bps))
        charges.add(qty * px * rate, qty * terms.half_spread * rate)
        commission, spread = charges.top_up(terms.min_per_order_usd, self.scale)
        costs = self.costs[str(instrument.id)]
        costs.commission += commission
        costs.spread += spread
        self._publish()
        return Money(commission + spread, ACCOUNT_CURRENCY)

    def _warn_odd_lot(self, instrument, terms: FxTerms, order_value_usd: Decimal) -> None:
        """Once per pair: an order below the IDEALPRO minimum would be an odd lot live."""
        iid = str(instrument.id)
        if order_value_usd < terms.min_order_usd and iid not in self._odd_lots:
            self._odd_lots.add(iid)
            logger.warning(
                "fx_odd_lot",
                instrument_id=iid,
                order_value_usd=str(_cents(order_value_usd)),
                idealpro_minimum_usd=str(terms.min_order_usd),
            )

    def _publish(self) -> None:
        if self._cache is not None:
            totals = {iid: c.as_snapshot() for iid, c in self.costs.items()}
            self._cache.add(FX_COSTS_KEY, json.dumps(totals).encode())

    def __repr__(self) -> str:
        """Pairs and scale."""
        return f"FxCommissionModel({', '.join(map(str, self.terms))}, scale={self.scale})"


def fx_snapshot(instruments: Iterable[Instrument], cache=None) -> dict[str, Any]:
    """``config_snapshot`` keys for a run's FX pairs; ``{}`` when there are none.

    Per pair the terms the run used, its USD conversion pair (None for a USD quote) and the
    USD it was charged (``costs_usd``: commission and modelled spread, from ``cache``).
    """
    instruments = list(instruments)
    terms = fx_terms_by_instrument(instruments)
    if not terms:
        return {}
    raw = cache.get(FX_COSTS_KEY) if cache is not None else None
    charged = json.loads(raw) if raw else {}
    pairs = {}
    for instrument in instruments:
        if instrument.id not in terms:
            continue
        entry = terms[instrument.id].as_snapshot()
        entry["conversion_pair"] = conversion_pair(instrument)
        entry["costs_usd"] = charged.get(str(instrument.id), FxCosts().as_snapshot())
        pairs[str(instrument.id)] = entry
    return {"account_currency": ACCOUNT_CURRENCY.code, "fx_pairs": pairs}
