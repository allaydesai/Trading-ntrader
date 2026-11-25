"""Spot crypto reporting figures (crypto M5): coin quantities, USD notional and the costs split.

Pure and framework-free, like :mod:`src.services.fx.returns`. Inputs are a run's
``config_snapshot`` (M3 stores ``crypto_pairs`` there: the terms at the run's Kraken Pro fee
tier and the USD each pair was charged, split maker/taker/spread; M5 adds the orders dropped
under Kraken's minimum), its trade rows (ORM ``Trade`` objects or dicts with the same keys) and
its starting capital. Every renderer (CLI ``show``/``report``/``history``/``compare``, the MCP
run views and export, the dashboard) reads these, so they can't drift apart.

A run without ``crypto_pairs`` is not crypto: :func:`summarise_run` returns ``None`` and callers
leave their output exactly as it was.

Every shipped pair is USD-quoted, so notional is ``quantity x entry`` and ``profit_loss`` is
already USD. The account return on USD equity stays the headline (decision C19); risk metrics
annualise on 365 days (C20). Peak exposure is the largest concurrent notional over equity: spot
is unlevered, so it stays at or under 1 and shows the idle cash. A trade row stores a
position's largest quantity and its open time, not its fill history, so a position that scales
in is counted at its largest size from its open, and a position opened and closed at one
timestamp counts as held then (:func:`src.services.fx.returns.peaks`): exact for single-entry
positions that span bars, an upper bound otherwise (:func:`note` says so). See section 10 of
``openspec/changes/add-crypto-backtesting/design.md``. Runs saved with a fill series
(``crypto_pairs[<id>].position_series``) get exact figures instead
(:mod:`src.services.crypto.exposure`, ``openspec/changes/add-crypto-exact-exposure``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from src.services.crypto import exposure as exact_exposure
from src.services.fx.returns import peaks

CRYPTO = "crypto"
USD = "USD"
VENUE = "KRAKEN"
PERIODS_PER_YEAR = 365
_HUNDRED = Decimal(100)
_PCT = Decimal("0.0001")
_USD = Decimal("0.01")


def note(fee_tier: int | None, exact: bool = False) -> str:
    """The statement every crypto report carries (design section 10).

    ``exact`` when the run saved its fill series: the peak figures are then exact, otherwise the
    M5 upper-bound wording stays (runs saved before the series, no backfill).
    """
    tier = f"fee tier {fee_tier}" if fee_tier is not None else "fee tier per pair"
    peak = (
        "Peak notional and exposure are exact, replayed from every fill: cost-basis notional "
        "over the USD balance after each fill."
        if exact
        else "Peak notional and exposure use each position's largest "
        "size from its open and count a position opened and closed at one timestamp as held "
        "then: an upper bound when a position scales in or round-trips within one bar."
    )
    return (
        f"Spot, unlevered, long-only; Kraken Pro {tier}; spread modelled (half per side). "
        "Commission and spread are charged in USD; the spread and Kraken pair terms are "
        f"UNVERIFIED working defaults. {peak}"
    )


@dataclass(frozen=True)
class PairTerms:
    """One pair's terms as the run used them (``config_snapshot.crypto_pairs[<id>]``)."""

    base: str
    quote: str
    fee_tier: int
    maker_pct: Decimal
    taker_pct: Decimal
    discounted_pair: bool
    spread_bps: Decimal
    size_precision: int
    price_precision: int
    venue: str
    fees_as_of: str | None
    commission_maker_usd: Decimal
    commission_taker_usd: Decimal
    spread_usd: Decimal
    orders_below_minimum: int | None
    raw: Mapping[str, Any]

    @property
    def pair(self) -> str:
        """``BTC/USD``."""
        return f"{self.base}/{self.quote}"

    @property
    def commission_usd(self) -> Decimal:
        return self.commission_maker_usd + self.commission_taker_usd


@dataclass(frozen=True)
class TradeFigures:
    """A crypto trade's figures; the underlying return is ``None`` while it is open."""

    instrument_id: str
    quantity: Decimal
    size_precision: int
    notional_usd: Decimal
    return_on_underlying_pct: Decimal | None
    pnl_usd: Decimal | None

    @property
    def margin_used_usd(self) -> Decimal:
        """Unlevered spot: the full notional is cash (what :func:`peaks` reads)."""
        return self.notional_usd


@dataclass(frozen=True)
class RunSummary:
    """A crypto run's figures (the account return on USD equity stays the headline)."""

    asset_class: str
    venue: str
    account_currency: str
    fee_tier: int | None
    periods_per_year: int
    pairs: dict[str, PairTerms]
    closed_trades: int
    max_quantity: Decimal
    peak_notional_usd: Decimal | None
    peak_exposure: Decimal | None
    net_pnl_usd: Decimal
    return_on_underlying_pct: Decimal | None
    commission_maker_usd: Decimal
    commission_taker_usd: Decimal
    spread_usd: Decimal
    orders_below_minimum: int | None
    #: Peak notional/exposure replayed from the saved fill series (else the M5 upper bound)
    exposure_exact: bool = False

    @property
    def commission_usd(self) -> Decimal:
        return self.commission_maker_usd + self.commission_taker_usd

    @property
    def costs_usd(self) -> Decimal:
        return self.commission_usd + self.spread_usd

    @property
    def size_precision(self) -> int:
        """The widest size precision of the run's pairs (8 for every shipped coin)."""
        return max((t.size_precision for t in self.pairs.values()), default=8)


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _terms(raw: Mapping[str, Any]) -> PairTerms:
    costs = raw.get("costs_usd") or {}
    schedule = raw.get("fee_schedule") or {}
    dropped = raw.get("orders_below_minimum")
    return PairTerms(
        base=str(raw["base"]),
        quote=str(raw["quote"]),
        fee_tier=int(raw["fee_tier"]),
        maker_pct=Decimal(str(raw["maker_pct"])),
        taker_pct=Decimal(str(raw["taker_pct"])),
        discounted_pair=bool(raw.get("discounted_pair")),
        spread_bps=Decimal(str(raw.get("typical_spread_bps") or 0)),
        size_precision=int(raw.get("size_precision", 8)),
        price_precision=int(raw.get("price_precision", 2)),
        venue=str(schedule.get("venue") or VENUE),
        fees_as_of=schedule.get("as_of"),
        commission_maker_usd=Decimal(str(costs.get("commission_maker") or 0)),
        commission_taker_usd=Decimal(str(costs.get("commission_taker") or 0)),
        spread_usd=Decimal(str(costs.get("spread") or 0)),
        orders_below_minimum=None if dropped is None else int(dropped),
        raw=raw,
    )


def terms_from_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, PairTerms]:
    """Pair terms by instrument id; ``{}`` for a run that traded no crypto (or no snapshot)."""
    raw = (snapshot or {}).get("crypto_pairs") or {}
    return {str(iid): _terms(t) for iid, t in raw.items()}


def asset_class(snapshot: Mapping[str, Any] | None) -> str | None:
    """``"crypto"`` when the run traded a spot crypto pair, else ``None``."""
    return CRYPTO if terms_from_snapshot(snapshot) else None


def _get(trade: Any, name: str) -> Any:
    return trade.get(name) if isinstance(trade, Mapping) else getattr(trade, name, None)


def trade_figures(trade: Any, terms: PairTerms) -> TradeFigures:
    """Coin quantity, USD notional at entry, side-aware underlying return and USD P&L."""
    quantity = _dec(_get(trade, "quantity")) or Decimal(0)
    entry = _dec(_get(trade, "entry_price")) or Decimal(0)
    exit_ = _dec(_get(trade, "exit_price"))
    rou = None
    # Closed means flat (exit_timestamp is set only when the position reaches zero).
    if exit_ is not None and _get(trade, "exit_timestamp") is not None and entry:
        short = str(_get(trade, "order_side") or "").upper().endswith("SELL")
        rou = ((entry - exit_) if short else (exit_ - entry)) / entry * _HUNDRED
    return TradeFigures(
        instrument_id=str(_get(trade, "instrument_id")),
        quantity=quantity,
        size_precision=terms.size_precision,
        notional_usd=quantity * entry,
        return_on_underlying_pct=rou,
        # Stored realised P&L, also on a still-open (partly closed) position
        pnl_usd=_dec(_get(trade, "profit_loss")),
    )


def _compounded(returns_pct: list[Decimal]) -> Decimal | None:
    if not returns_pct:
        return None
    growth = Decimal(1)
    for r in returns_pct:
        growth *= 1 + r / _HUNDRED
    return (growth - 1) * _HUNDRED


def _run_tier(snapshot: Mapping[str, Any], terms: dict[str, PairTerms]) -> int | None:
    if (tier := snapshot.get("fee_tier")) is not None:
        return int(tier)
    tiers = {t.fee_tier for t in terms.values()}
    return tiers.pop() if len(tiers) == 1 else None


def _dropped(terms: dict[str, PairTerms]) -> int | None:
    """Orders dropped under the minimum over all pairs; ``None`` if no pair recorded it."""
    counts = [t.orders_below_minimum for t in terms.values() if t.orders_below_minimum is not None]
    return sum(counts) if counts else None


def summarise_run(
    snapshot: Mapping[str, Any] | None, trades: Iterable[Any], capital: Any = None
) -> RunSummary | None:
    """The run's crypto summary, or ``None`` when it traded no crypto pair.

    Trades on an instrument the snapshot doesn't list are skipped (they have no terms).
    ``capital`` (the starting balance) anchors the exposure; without it exposure is ``None``.
    """
    terms = terms_from_snapshot(snapshot)
    if not terms:
        return None
    snapshot = snapshot or {}
    rows = [
        (t, trade_figures(t, terms[iid]))
        for t in trades
        if (iid := str(_get(t, "instrument_id"))) in terms
    ]
    closed = [
        f
        for t, f in sorted(
            rows, key=lambda r: _get(r[0], "exit_timestamp") or _get(r[0], "entry_timestamp")
        )
        if f.return_on_underlying_pct is not None
    ]
    series = {iid: t.raw.get("position_series") for iid, t in terms.items()}
    exact = exact_exposure.has_series(series)
    if exact:
        peak_notional, exposure = exact_exposure.peaks(
            {iid: s or [] for iid, s in series.items()}, _dec(capital)
        )
    else:
        peak_notional, _, exposure = peaks(rows, _dec(capital)) if rows else (None, None, None)
    return RunSummary(
        asset_class=CRYPTO,
        venue=next(iter(terms.values())).venue,
        account_currency=str(snapshot.get("account_currency") or USD),
        fee_tier=_run_tier(snapshot, terms),
        periods_per_year=int(snapshot.get("periods_per_year") or PERIODS_PER_YEAR),
        pairs=terms,
        closed_trades=len(closed),
        max_quantity=max((f.quantity for _, f in rows), default=Decimal(0)),
        peak_notional_usd=peak_notional,
        peak_exposure=exposure,
        net_pnl_usd=sum((f.pnl_usd or Decimal(0) for f in closed), Decimal(0)),
        return_on_underlying_pct=_compounded(
            [f.return_on_underlying_pct for f in closed if f.return_on_underlying_pct is not None]
        ),
        commission_maker_usd=sum((t.commission_maker_usd for t in terms.values()), Decimal(0)),
        commission_taker_usd=sum((t.commission_taker_usd for t in terms.values()), Decimal(0)),
        spread_usd=sum((t.spread_usd for t in terms.values()), Decimal(0)),
        orders_below_minimum=_dropped(terms),
        exposure_exact=exact,
    )


def quantity_text(quantity: Decimal, size_precision: int) -> str:
    """A coin quantity at full size precision, trailing zeros trimmed: ``0.12820512``, ``0.25``.

    Never rounded to whole units; a value finer than the precision is rounded half-even to it.
    """
    step = Decimal(1).scaleb(-size_precision)
    value = quantity.quantize(step, rounding=ROUND_HALF_EVEN).normalize()
    return f"{value:,f}"


def _fmt(value: Decimal | None, step: Decimal) -> str | None:
    return None if value is None else str(value.quantize(step))


def _pair_fields(t: PairTerms) -> dict[str, Any]:
    return {
        "base": t.base,
        "quote": t.quote,
        "venue": t.venue,
        "fee_tier": t.fee_tier,
        "maker_pct": str(t.maker_pct),
        "taker_pct": str(t.taker_pct),
        "discounted_pair": t.discounted_pair,
        "typical_spread_bps": str(t.spread_bps),
        "size_precision": t.size_precision,
        "fees_as_of": t.fees_as_of,
        "orders_below_minimum": t.orders_below_minimum,
        "unverified": list(t.raw.get("unverified") or []),
        "costs_usd": {
            "commission": _fmt(t.commission_usd, _USD),
            "commission_maker": _fmt(t.commission_maker_usd, _USD),
            "commission_taker": _fmt(t.commission_taker_usd, _USD),
            "spread": _fmt(t.spread_usd, _USD),
        },
    }


def report_fields(summary: RunSummary) -> dict[str, Any]:
    """JSON/CSV-safe run fields (decimals as strings, percentages to 4 places)."""
    return {
        "asset_class": summary.asset_class,
        "venue": summary.venue,
        "account_currency": summary.account_currency,
        "fee_tier": summary.fee_tier,
        "periods_per_year": summary.periods_per_year,
        "spot": True,
        "leveraged": False,
        "long_only": True,
        # Closed trades only: open positions and their entry costs are not in it
        "closed_trade_pnl_usd": _fmt(summary.net_pnl_usd, _USD),
        "closed_trades": summary.closed_trades,
        "return_on_underlying_pct": _fmt(summary.return_on_underlying_pct, _PCT),
        "max_quantity": quantity_text(summary.max_quantity, summary.size_precision),
        "peak_notional_usd": _fmt(summary.peak_notional_usd, _USD),
        "peak_exposure_pct": _fmt(
            None if summary.peak_exposure is None else summary.peak_exposure * _HUNDRED, _PCT
        ),
        "peak_exposure_basis": "exact" if summary.exposure_exact else "upper_bound",
        "commission_usd": _fmt(summary.commission_usd, _USD),
        "commission_maker_usd": _fmt(summary.commission_maker_usd, _USD),
        "commission_taker_usd": _fmt(summary.commission_taker_usd, _USD),
        "spread_usd": _fmt(summary.spread_usd, _USD),
        "costs_usd": _fmt(summary.costs_usd, _USD),
        "orders_below_minimum": summary.orders_below_minimum,
        "note": note(summary.fee_tier, summary.exposure_exact),
        "pairs": {iid: _pair_fields(t) for iid, t in summary.pairs.items()},
    }


#: The per-trade columns :func:`trade_fields` adds to a trade export, in order.
TRADE_COLUMNS = ("coin_quantity", "notional_usd", "return_on_underlying_pct", "pnl_usd")


def trade_fields(fig: TradeFigures) -> dict[str, str | None]:
    """A trade's crypto columns (:data:`TRADE_COLUMNS`), JSON/CSV-safe."""
    return {
        "coin_quantity": quantity_text(fig.quantity, fig.size_precision),
        "notional_usd": _fmt(fig.notional_usd, _USD),
        "return_on_underlying_pct": _fmt(fig.return_on_underlying_pct, _PCT),
        "pnl_usd": _fmt(fig.pnl_usd, _USD),
    }


def trade_rows(snapshot: Mapping[str, Any] | None, trades: Iterable[Any]) -> list[dict]:
    """Per-trade crypto columns in trade order; ``{}`` for a trade with no listed terms."""
    terms = terms_from_snapshot(snapshot)
    return [
        trade_fields(trade_figures(t, terms[iid]))
        if (iid := str(_get(t, "instrument_id"))) in terms
        else {}
        for t in trades
    ]
