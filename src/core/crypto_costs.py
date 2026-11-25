"""Spot crypto backtest costs: Kraken Pro spot commission plus a modelled half-spread (crypto M3).

A crypto venue's account is single-currency USD (``src/core/backtest_venue.py``) and every
shipped pair is USD-quoted, so every charge is USD with no conversion. Per fill,
:class:`CryptoCommissionModel` charges
(``openspec/changes/archive/2026-11-25-add-crypto-backtesting/design.md`` section 4,
decisions C3, C4 and C21):

- **commission**: ``rate x qty x price``, where the rate is the run's Kraken Pro tier's
  **maker** rate when Nautilus marks the fill ``MAKER`` (an order that rested on the book) and
  the **taker** rate otherwise (market, stop and marketable-limit fills). A pair on Kraken's
  discounted list pays ``discounted_pair_bps_off`` less, floored at zero. There is **no
  minimum fee**;
- **spread**: ``qty x price x typical_spread_bps / 2 / 10,000``, half the modelled full spread
  per side (the series is one trade price, so the spread is modelled exactly as FX models it).

Both components are scaled by the run's cost multiplier and rounded to cents once per order and
liquidity side from running totals (:class:`~src.core.fx_costs.OrderCharges`, shared with the FX
model), so split bar fills add no rounding. The totals per pair and liquidity side are kept in
the cache for ``config_snapshot.crypto_pairs[...]["costs_usd"]``. The model also numbers the
fills it charges, in engine order, so :func:`crypto_snapshot` can replay each pair's position
series and save the exact peak exposure (``crypto_peaks``, :mod:`src.services.crypto.exposure`).

The tier is one per run (decision C21): ``info["fee_tier"]`` when the run overrides it
(``--fee-tier``), else the schedule's ``default_tier`` (tier 2). Rates come from the shipped
``coin_specs.yaml`` schedule (VERIFIED, Kraken Pro CA page, 2026-11-24); the spread defaults are
UNVERIFIED (C4). Margin needs no model: the pair's ``margin_init`` is 1 (unlevered), which
Nautilus' leveraged model applies at the venue's leverage of 1; ``src/core/margin_gate.py``
enforces cash and no-short.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.enums import LiquiditySide, OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money

from src.core.fx_costs import OrderCharges
from src.core.fx_rates import ACCOUNT_CURRENCY
from src.core.position_sizing import is_crypto
from src.services.crypto.coin_specs import get_fee_schedule
from src.services.crypto.exposure import PEAKS_KEY, peaks_of, peaks_snapshot, replay

#: Cache key (Nautilus' general object store) holding the run's per-pair cost totals as JSON.
CRYPTO_COSTS_KEY = "ntrader.crypto_costs"
#: Cache key holding, per pair, the orders strategies dropped under Kraken's minimum (M5).
ORDERS_BELOW_MINIMUM_KEY = "ntrader.crypto_orders_below_minimum"
#: Cache keys numbering the fills the fee model charged, in engine order: the count, and per
#: fill (from 0) the client order id. The fee model is asked once per fill, just before
#: Nautilus emits it, so this is the only record of the order of fills across pairs.
FILL_COUNT_KEY = "ntrader.crypto_fill_count"
FILL_ORDER_KEY = "ntrader.crypto_fill.{}"
#: Crypto trades every calendar day: daily returns annualise on 365, not 252 (decision C20).
CRYPTO_PERIODS_PER_YEAR = 365
CENT = Decimal("0.01")
ZERO = Decimal(0)
BPS = Decimal(10_000)


def _cents(amount: Decimal) -> Decimal:
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class CryptoTerms:
    """One pair's cost terms at the run's tier."""

    base: str
    quote: str
    fee_tier: int
    maker_pct: Decimal
    taker_pct: Decimal
    discount_bps: Decimal
    spread_bps: Decimal
    info: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def rate_bps(self, liquidity_side: LiquiditySide) -> Decimal:
        """The commission rate in bps: maker for a ``MAKER`` fill, else taker, less discount."""
        pct = self.maker_pct if liquidity_side == LiquiditySide.MAKER else self.taker_pct
        return max(pct * 100 - self.discount_bps, ZERO)

    def rate(self, liquidity_side: LiquiditySide) -> Decimal:
        """The commission rate as a fraction of trade value (0.60% is 0.006)."""
        return self.rate_bps(liquidity_side) / BPS

    def as_snapshot(self, instrument: Instrument) -> dict[str, Any]:
        """JSON-safe terms for ``config_snapshot.crypto_pairs`` (M5 reports against them)."""
        return {
            "base": self.base,
            "quote": self.quote,
            "fee_tier": self.fee_tier,
            "maker_pct": str(self.maker_pct),
            "taker_pct": str(self.taker_pct),
            "discounted_pair": self.discount_bps > 0,
            "typical_spread_bps": str(self.spread_bps),
            "size_precision": instrument.size_precision,
            "price_precision": instrument.price_precision,
            "fee_schedule": dict(self.info.get("fee_schedule", {})),
            "unverified": list(self.info.get("unverified", [])),
        }


def crypto_terms(instrument: Instrument) -> CryptoTerms | None:
    """The pair's crypto cost terms, or ``None`` for anything that is not a spec-built pair.

    Raises:
        ValueError: A crypto pair whose ``info`` lacks the spec terms.
        CryptoSpecError: Its ``fee_tier`` is not a tier of the shipped schedule.
    """
    if not is_crypto(instrument):
        return None
    info = instrument.info or {}
    schedule = get_fee_schedule()
    try:
        number = int(info.get("fee_tier", info["fee_schedule"]["default_tier"]))
        discounted = bool(info["kraken"]["discounted_pair"])
        spread_bps = Decimal(str(info["typical_spread_bps"]))
    except KeyError as missing:
        raise ValueError(
            f"Crypto pair {instrument.id} has no {missing} in its info; build it with "
            "build_crypto_pair() so costs come from the coin spec."
        ) from None
    tier = schedule.tier(number)
    return CryptoTerms(
        base=instrument.base_currency.code,
        quote=instrument.quote_currency.code,
        fee_tier=number,
        maker_pct=tier.maker_pct,
        taker_pct=tier.taker_pct,
        discount_bps=schedule.discounted_pair_bps_off if discounted else ZERO,
        spread_bps=spread_bps,
        info=info,
    )


def crypto_terms_by_instrument(
    instruments: Iterable[Instrument],
) -> dict[InstrumentId, CryptoTerms]:
    """``{instrument_id: terms}`` for the spot crypto pairs among ``instruments``."""
    return {i.id: t for i in instruments if (t := crypto_terms(i)) is not None}


@dataclass
class CryptoCosts:
    """USD charged on one pair so far: maker and taker commission and modelled spread."""

    maker: Decimal = ZERO
    taker: Decimal = ZERO
    spread: Decimal = ZERO

    def as_snapshot(self) -> dict[str, str]:
        """Commission (total, maker, taker) and spread, as cent strings."""
        return {
            "commission": str(_cents(self.maker + self.taker)),
            "commission_maker": str(_cents(self.maker)),
            "commission_taker": str(_cents(self.taker)),
            "spread": str(_cents(self.spread)),
        }


class CryptoCommissionModel(FeeModel):
    """Kraken Pro spot commission (maker or taker by fill, no minimum) plus half the spread."""

    def __init__(
        self,
        terms: Mapping[InstrumentId, CryptoTerms],
        cache=None,
        scale: Decimal = Decimal("1"),
    ) -> None:
        """``terms`` per pair; ``cache`` keeps the totals; ``scale`` is the cost stress."""
        super().__init__()
        if scale < 0:
            raise ValueError("commission scale cannot be negative")
        self.terms = dict(terms)
        self.scale = scale
        self.costs: dict[str, CryptoCosts] = {str(iid): CryptoCosts() for iid in self.terms}
        self._cache = cache
        self._orders: dict[tuple[str, LiquiditySide], OrderCharges] = {}

    def get_commission(self, order, fill_qty, fill_px, instrument) -> Money:  # noqa: D102
        terms = self.terms.get(instrument.id)
        if terms is None:
            raise ValueError(f"No crypto commission terms for {instrument.id}")
        side = order.liquidity_side
        value = fill_qty.as_decimal() * fill_px.as_decimal()  # USD: every pair is USD-quoted
        key = (str(order.client_order_id), side)
        charges = self._orders.setdefault(key, OrderCharges(terms.rate_bps(side)))
        charges.add(value, value * terms.spread_bps / 2 / BPS)
        commission, spread = charges.top_up(ZERO, self.scale)
        costs = self.costs[str(instrument.id)]
        if side == LiquiditySide.MAKER:
            costs.maker += commission
        else:
            costs.taker += commission
        costs.spread += spread
        self._publish()
        self._number_fill(order)
        return Money(commission + spread, ACCOUNT_CURRENCY)

    def _number_fill(self, order) -> None:
        """Record which order this fill belongs to, under the next fill number."""
        if self._cache is not None:
            # The count lives in the cache, so fee models of several venues share one numbering
            n = int(self._cache.get(FILL_COUNT_KEY) or 0)
            self._cache.add(FILL_ORDER_KEY.format(n), str(order.client_order_id).encode())
            self._cache.add(FILL_COUNT_KEY, str(n + 1).encode())

    def _publish(self) -> None:
        if self._cache is not None:
            totals = {iid: c.as_snapshot() for iid, c in self.costs.items()}
            self._cache.add(CRYPTO_COSTS_KEY, json.dumps(totals).encode())

    def __repr__(self) -> str:
        """Pairs and scale."""
        return f"CryptoCommissionModel({', '.join(map(str, self.terms))}, scale={self.scale})"


def _cached_json(cache, key: str) -> dict[str, Any]:
    raw = cache.get(key) if cache is not None else None
    return json.loads(raw) if raw else {}


class _KeyValueCache(Protocol):
    """The slice of Nautilus' ``Cache`` the drop counter uses (its general object store)."""

    def get(self, key: str) -> bytes | None: ...  # noqa: D102

    def add(self, key: str, value: bytes) -> None: ...  # noqa: D102


def _bump_orders_below_minimum(cache: _KeyValueCache, instrument_id: object, by: int) -> int:
    counts = _cached_json(cache, ORDERS_BELOW_MINIMUM_KEY)
    iid = str(instrument_id)
    counts[iid] = int(counts.get(iid, 0)) + by
    cache.add(ORDERS_BELOW_MINIMUM_KEY, json.dumps(counts).encode())
    return counts[iid]


def track_orders_below_minimum(cache: _KeyValueCache, instrument_id: object) -> None:
    """Start the pair's drop count at 0: the strategy counts drops, so the run reports one.

    A strategy that never calls this (or :func:`count_order_below_minimum`) leaves the pair
    without a count, which reports as "not recorded" rather than a false 0.

    Args:
        cache: The run's cache (Nautilus ``Cache``).
        instrument_id: The crypto pair the strategy sizes.
    """
    _bump_orders_below_minimum(cache, instrument_id, 0)


def count_order_below_minimum(cache: _KeyValueCache, instrument_id: object) -> int:
    """Count one order a strategy dropped under the pair's minimum.

    Kept in the run's cache (like the cost totals) so :func:`crypto_snapshot` saves it with the
    run (``crypto_pairs[<id>].orders_below_minimum``), not only in the strategy log (M5).

    Args:
        cache: The run's cache (Nautilus ``Cache``).
        instrument_id: The crypto pair whose order was dropped.

    Returns:
        The pair's new total of dropped orders.
    """
    return _bump_orders_below_minimum(cache, instrument_id, 1)


def position_series(cache, instrument_ids: Iterable[object]) -> dict[str, list[list]] | None:
    """Each pair's fills in engine order, as :mod:`src.services.crypto.exposure` replays them.

    Per pair, ``[seq, ts_ns, position_id, position_qty, fill_px, commission_usd]`` per fill:
    the fill number across pairs, its time, its position and that position's signed quantity
    after it, its price and the USD charged (commission plus modelled spread). Built from the
    fill numbers the fee model kept and the orders' fill events. ``None`` when the two don't
    match one for one or a fill has no position (the run then reports the M5 upper bound
    rather than a wrong figure).
    """
    wanted = {str(i) for i in instrument_ids}
    fills_of = {
        str(o.client_order_id): [e for e in o.events if isinstance(e, OrderFilled)]
        for o in cache.orders()
        if str(o.instrument_id) in wanted
    }
    count = int(cache.get(FILL_COUNT_KEY) or 0)
    if count != sum(len(fills) for fills in fills_of.values()):
        return None
    taken: dict[str, int] = {}
    held: dict[str, Decimal] = {}
    series: dict[str, list[list]] = {iid: [] for iid in wanted}
    for seq in range(count):
        coid = (cache.get(FILL_ORDER_KEY.format(seq)) or b"").decode()
        fills, k = fills_of.get(coid, []), taken.get(coid, 0)
        if k >= len(fills) or fills[k].position_id is None:
            return None
        fill, taken[coid] = fills[k], k + 1
        pid = str(fill.position_id)
        sign = 1 if fill.order_side == OrderSide.BUY else -1
        held[pid] = held.get(pid, ZERO) + sign * Decimal(str(fill.last_qty))  # keeps precision
        series[str(fill.instrument_id)].append(
            [
                seq,
                fill.ts_event,
                pid,
                f"{held[pid]:f}",
                str(fill.last_px),
                f"{fill.commission.as_decimal():f}",
            ]
        )
    return series


def _account_balances(cache, instruments: list[Instrument]) -> tuple[Decimal, Decimal] | None:
    """The pairs' venue accounts' starting and final USD balance; ``None`` without an account."""
    accounts = {}
    for instrument in instruments:
        account = cache.account_for_venue(instrument.id.venue)
        if account is None:
            return None
        accounts[str(account.id)] = account
    start = sum(
        (a.starting_balances()[ACCOUNT_CURRENCY].as_decimal() for a in accounts.values()), ZERO
    )
    final = sum((a.balance_total(ACCOUNT_CURRENCY).as_decimal() for a in accounts.values()), ZERO)
    return start, final


def exact_peaks(
    cache, instruments: list[Instrument], series: dict[str, list[list]]
) -> dict[str, Any] | None:
    """The run's exact peaks as saved (:func:`src.services.crypto.exposure.peaks_snapshot`).

    The series is replayed from the accounts' starting balance. ``None`` (the run reports the
    upper bound) when the replay misses the final balance by more than a cent per fill:
    Nautilus rounds each fill's P&L to the cent from floats and the replay rounds exact
    decimals, so a fill can land a cent apart; more means the series misses something. Without
    an account (a bare cache) there is nothing to check and no capital, so no exposure.
    """
    balances = _account_balances(cache, instruments)
    start = None if balances is None else balances[0]
    samples = replay(series, start)
    if balances is not None:
        replayed = samples[-1].balance if samples else start
        if replayed is None or abs(replayed - balances[1]) > CENT * max(len(samples), 1):
            return None
    return peaks_snapshot(*peaks_of(samples))


def crypto_snapshot(instruments: Iterable[Instrument], cache=None) -> dict[str, Any]:
    """``config_snapshot`` keys for a run's crypto pairs; ``{}`` when there are none.

    The account currency, the run's fee tier (``None`` if pairs ran at different tiers), 365
    periods a year, and per pair the terms the run used, the USD it was charged and, when its
    strategy counts them (:func:`track_orders_below_minimum`), the orders dropped under the
    minimum. A pair no strategy counted has no ``orders_below_minimum`` ("not recorded").
    With the run's cache the fills (:func:`position_series`) are replayed and only the result is
    saved, as ``crypto_peaks`` (:func:`exact_peaks`): the reported peak notional and exposure
    are then exact. The series itself is not saved.
    """
    instruments = list(instruments)
    terms = crypto_terms_by_instrument(instruments)
    if not terms:
        return {}
    charged = _cached_json(cache, CRYPTO_COSTS_KEY)
    dropped = _cached_json(cache, ORDERS_BELOW_MINIMUM_KEY)
    series = position_series(cache, terms) if cache is not None else None
    crypto = [i for i in instruments if i.id in terms]
    exact = exact_peaks(cache, crypto, series) if series is not None else None
    pairs = {}
    for instrument in instruments:
        if instrument.id in terms:
            entry = terms[instrument.id].as_snapshot(instrument)
            entry["costs_usd"] = charged.get(str(instrument.id), CryptoCosts().as_snapshot())
            if str(instrument.id) in dropped:
                entry["orders_below_minimum"] = int(dropped[str(instrument.id)])
            pairs[str(instrument.id)] = entry
    tiers = {t.fee_tier for t in terms.values()}
    snapshot = {
        "account_currency": ACCOUNT_CURRENCY.code,
        "fee_tier": tiers.pop() if len(tiers) == 1 else None,
        "periods_per_year": CRYPTO_PERIODS_PER_YEAR,
        "crypto_pairs": pairs,
    }
    if exact is not None:
        snapshot[PEAKS_KEY] = exact
    return snapshot
