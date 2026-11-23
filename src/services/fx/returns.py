"""FX reporting figures (M5): pips, lots, USD notional, leverage and the costs split.

Pure and framework-free, like :mod:`src.services.futures.returns`. Inputs are a run's
``config_snapshot`` (M3/M4 store ``fx_pairs`` there, frozen at run time, with the USD each pair
was charged in ``costs_usd``), its trade rows (ORM ``Trade`` objects or dicts with the same keys)
and its starting capital. Every renderer (CLI ``show``/``report``/``history``/``compare``, the
MCP run views and export, the dashboard) reads these, so they can't drift apart.

A run without ``fx_pairs`` is not FX: :func:`summarise_run` returns ``None`` and callers leave
their output exactly as it was.

The account return on USD equity stays the headline (design D10); the trade rows' ``profit_loss``
is already USD (M3). USD notional needs the quote/USD rate at entry: a USD-quote pair's is 1, a
USD-base pair's notional is its quantity, and a cross's rate is not stored, so a cross has no
notional, margin or leverage figure (``None``). Financing (rollover) is not modelled (D9); every
FX report carries :data:`FINANCING_NOTE`. See ``openspec/changes/add-fx-backtesting/design.md``
section 11.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

FX = "fx"
USD = "USD"
LOT_UNITS = Decimal(100_000)
FINANCING_NOTE = (
    "Financing (overnight rollover/swap) is not modelled: P&L excludes carry. Commission and "
    "the modelled spread are charged in USD; values are UNVERIFIED working defaults."
)
_HUNDRED = Decimal(100)
_PCT = Decimal("0.0001")
_USD = Decimal("0.01")
_PIP = Decimal("0.1")


@dataclass(frozen=True)
class PairTerms:
    """One pair's terms as the run used them (``config_snapshot.fx_pairs[<id>]``)."""

    base: str
    quote: str
    pip_size: Decimal
    margin_pct: Decimal
    typical_spread_pips: Decimal
    commission_usd: Decimal
    spread_usd: Decimal
    margin_as_of: str | None
    raw: Mapping[str, Any]

    @property
    def ticker(self) -> str:
        """``EURUSD`` for ``EUR/USD``."""
        return f"{self.base}{self.quote}"


@dataclass(frozen=True)
class TradeFigures:
    """An FX trade's figures; pips and returns are ``None`` while it is open."""

    instrument_id: str
    units: Decimal
    lots: Decimal
    notional_usd: Decimal | None
    margin_used_usd: Decimal | None
    pips: Decimal | None
    return_on_underlying_pct: Decimal | None
    pnl_usd: Decimal | None


@dataclass(frozen=True)
class RunSummary:
    """An FX run's figures (the account return on USD equity stays the headline)."""

    asset_class: str
    pairs: dict[str, PairTerms]
    closed_trades: int
    max_units: Decimal
    peak_notional_usd: Decimal | None
    peak_margin_usd: Decimal | None
    peak_leverage: Decimal | None
    net_pnl_usd: Decimal
    pips: Decimal
    return_on_underlying_pct: Decimal | None
    return_on_margin_pct: Decimal | None
    commission_usd: Decimal
    spread_usd: Decimal

    @property
    def costs_usd(self) -> Decimal:
        return self.commission_usd + self.spread_usd


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _terms(raw: Mapping[str, Any]) -> PairTerms:
    costs = raw.get("costs_usd") or {}
    return PairTerms(
        base=str(raw["base"]),
        quote=str(raw["quote"]),
        pip_size=Decimal(str(raw["pip_size"])),
        margin_pct=Decimal(str(raw.get("margin_pct") or 0)),
        typical_spread_pips=Decimal(str(raw.get("typical_spread_pips") or 0)),
        commission_usd=Decimal(str(costs.get("commission") or 0)),
        spread_usd=Decimal(str(costs.get("spread") or 0)),
        margin_as_of=raw.get("margin_as_of"),
        raw=raw,
    )


def terms_from_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, PairTerms]:
    """Pair terms by instrument id; ``{}`` for a run that traded no FX (or no snapshot)."""
    raw = (snapshot or {}).get("fx_pairs") or {}
    return {str(iid): _terms(t) for iid, t in raw.items()}


def asset_class(snapshot: Mapping[str, Any] | None) -> str | None:
    """``"fx"`` when the run traded a spot FX pair, else ``None`` (equity or futures)."""
    return FX if terms_from_snapshot(snapshot) else None


def _get(trade: Any, name: str) -> Any:
    return trade.get(name) if isinstance(trade, Mapping) else getattr(trade, name, None)


def _is_short(trade: Any) -> bool:
    return str(_get(trade, "order_side") or "").upper().endswith("SELL")


def _notional_usd(units: Decimal, entry: Decimal, terms: PairTerms) -> Decimal | None:
    if terms.quote == USD:
        return units * entry
    if terms.base == USD:
        return units
    return None  # a cross: its quote/USD rate at entry is not stored


def trade_figures(trade: Any, terms: PairTerms) -> TradeFigures:
    """Units, lots, USD notional and margin, side-aware pips and underlying return."""
    units = _dec(_get(trade, "quantity")) or Decimal(0)
    entry = _dec(_get(trade, "entry_price")) or Decimal(0)
    exit_, pnl = _dec(_get(trade, "exit_price")), _dec(_get(trade, "profit_loss"))
    notional = _notional_usd(units, entry, terms)
    pips = rou = None
    # Closed means flat (exit_timestamp is set only when the position reaches zero).
    if exit_ is not None and _get(trade, "exit_timestamp") is not None:
        move = (entry - exit_) if _is_short(trade) else (exit_ - entry)
        pips = move / terms.pip_size
        rou = move / entry * _HUNDRED if entry else None
    return TradeFigures(
        instrument_id=str(_get(trade, "instrument_id")),
        units=units,
        lots=units / LOT_UNITS,
        notional_usd=notional,
        margin_used_usd=None if notional is None else notional * terms.margin_pct / _HUNDRED,
        pips=pips,
        return_on_underlying_pct=rou,
        pnl_usd=pnl,
    )


def _peaks(
    rows: list[tuple[Any, TradeFigures]], capital: Decimal | None
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """Peak concurrent notional and margin (USD), and peak notional / equity.

    Equity is the starting capital plus the P&L of trades closed so far. At one timestamp,
    exits come before entries (a close-and-reverse is not counted twice), every exit there is
    applied before leverage is sampled, and a trade that opens and closes on that timestamp
    holds its notional. A position with no USD value (a cross) makes all three unknown
    (``None``): a peak that leaves out a position would understate the exposure.
    """
    if any(fig.notional_usd is None for _, fig in rows):
        return None, None, None
    events: dict[tuple[Any, int], list[Decimal]] = {}
    for trade, fig in rows:
        notional, margin = fig.notional_usd or Decimal(0), fig.margin_used_usd or Decimal(0)
        entry_ts = _get(trade, "entry_timestamp")
        _add(events, (entry_ts, 1), notional, margin, Decimal(0))
        if (exit_ts := _get(trade, "exit_timestamp")) is not None:
            kind = 2 if exit_ts == entry_ts else 0
            _add(events, (exit_ts, kind), -notional, -margin, fig.pnl_usd or Decimal(0))
    notional = margin = peak_notional = peak_margin = Decimal(0)
    equity, leverage = capital, None
    for key in sorted(events, key=lambda k: (k[0], k[1])):
        d_notional, d_margin, pnl = events[key]
        notional, margin = notional + d_notional, margin + d_margin
        peak_notional, peak_margin = max(peak_notional, notional), max(peak_margin, margin)
        if equity is not None:
            equity += pnl
            if equity > 0 and (leverage is None or notional / equity > leverage):
                leverage = notional / equity
    return peak_notional, peak_margin, leverage


def _add(events: dict, key: tuple[Any, int], *deltas: Decimal) -> None:
    """Sum same-timestamp, same-kind events, so leverage is sampled once per group."""
    totals = events.setdefault(key, [Decimal(0)] * len(deltas))
    for i, delta in enumerate(deltas):
        totals[i] += delta


def _compounded(returns_pct: list[Decimal]) -> Decimal | None:
    if not returns_pct:
        return None
    growth = Decimal(1)
    for r in returns_pct:
        growth *= 1 + r / _HUNDRED
    return (growth - 1) * _HUNDRED


def summarise_run(
    snapshot: Mapping[str, Any] | None, trades: Iterable[Any], capital: Any = None
) -> RunSummary | None:
    """The run's FX summary, or ``None`` when it traded no FX pair.

    Trades on an instrument the snapshot doesn't list are skipped (they have no terms).
    ``capital`` (the starting balance) anchors the leverage; without it leverage is ``None``.
    """
    terms = terms_from_snapshot(snapshot)
    if not terms:
        return None
    rows = [
        (t, trade_figures(t, terms[iid]))
        for t in trades
        if (iid := str(_get(t, "instrument_id"))) in terms
    ]
    closed = sorted(
        (r for r in rows if r[1].pips is not None), key=lambda r: _get(r[0], "exit_timestamp")
    )
    net = sum((f.pnl_usd or Decimal(0) for _, f in closed), Decimal(0))
    peak_notional, peak_margin, leverage = _peaks(rows, _dec(capital))
    return RunSummary(
        asset_class=FX,
        pairs=terms,
        closed_trades=len(closed),
        max_units=max((f.units for _, f in rows), default=Decimal(0)),
        peak_notional_usd=peak_notional,
        peak_margin_usd=peak_margin,
        peak_leverage=leverage,
        net_pnl_usd=net,
        pips=sum((f.pips for _, f in closed if f.pips), Decimal(0)),
        return_on_underlying_pct=_compounded(
            [
                f.return_on_underlying_pct
                for _, f in closed
                if f.return_on_underlying_pct is not None
            ]
        ),
        return_on_margin_pct=net / peak_margin * _HUNDRED if closed and peak_margin else None,
        commission_usd=sum((t.commission_usd for t in terms.values()), Decimal(0)),
        spread_usd=sum((t.spread_usd for t in terms.values()), Decimal(0)),
    )


def plain(value: Decimal) -> str:
    """``200000`` / ``1234.5``: no exponent (``Decimal.normalize`` gives ``2E+5``)."""
    return f"{value.normalize():f}"


def _fmt(value: Decimal | None, step: Decimal) -> str | None:
    return None if value is None else str(value.quantize(step))


def _pair_fields(t: PairTerms) -> dict[str, Any]:
    return {
        "base": t.base,
        "quote": t.quote,
        "pip_size": str(t.pip_size),
        "margin_pct": str(t.margin_pct),
        "typical_spread_pips": str(t.typical_spread_pips),
        "margin_as_of": t.margin_as_of,
        "conversion_pair": t.raw.get("conversion_pair"),
        "commission": dict(t.raw.get("commission") or {}),
        "unverified": list(t.raw.get("unverified") or []),
        "costs_usd": {
            "commission": _fmt(t.commission_usd, _USD),
            "spread": _fmt(t.spread_usd, _USD),
        },
    }


def report_fields(summary: RunSummary) -> dict[str, Any]:
    """JSON/CSV-safe run fields (decimals as strings, percentages to 4 places)."""
    return {
        "asset_class": summary.asset_class,
        "account_currency": USD,
        "net_pnl_usd": _fmt(summary.net_pnl_usd, _USD),
        "closed_trades": summary.closed_trades,
        "pips": _fmt(summary.pips, _PIP),
        "return_on_underlying_pct": _fmt(summary.return_on_underlying_pct, _PCT),
        "return_on_margin_pct": _fmt(summary.return_on_margin_pct, _PCT),
        "max_units": plain(summary.max_units),
        "max_lots": _fmt(summary.max_units / LOT_UNITS, _USD),
        "peak_notional_usd": _fmt(summary.peak_notional_usd, _USD),
        "peak_margin_usd": _fmt(summary.peak_margin_usd, _USD),
        "peak_leverage": _fmt(summary.peak_leverage, _USD),
        "commission_usd": _fmt(summary.commission_usd, _USD),
        "spread_usd": _fmt(summary.spread_usd, _USD),
        "costs_usd": _fmt(summary.costs_usd, _USD),
        "financing_modelled": False,
        "note": FINANCING_NOTE,
        "pairs": {iid: _pair_fields(t) for iid, t in summary.pairs.items()},
    }


#: The per-trade columns :func:`trade_fields` adds to a trade export, in order.
TRADE_COLUMNS = (
    "units",
    "lots",
    "notional_usd",
    "margin_used_usd",
    "pips",
    "return_on_underlying_pct",
    "pnl_usd",
)


def trade_fields(fig: TradeFigures) -> dict[str, str | None]:
    """A trade's FX columns (:data:`TRADE_COLUMNS`), JSON/CSV-safe."""
    return {
        "units": plain(fig.units),
        "lots": _fmt(fig.lots, _USD),
        "notional_usd": _fmt(fig.notional_usd, _USD),
        "margin_used_usd": _fmt(fig.margin_used_usd, _USD),
        "pips": _fmt(fig.pips, _PIP),
        "return_on_underlying_pct": _fmt(fig.return_on_underlying_pct, _PCT),
        "pnl_usd": _fmt(fig.pnl_usd, _USD),
    }


def trade_rows(snapshot: Mapping[str, Any] | None, trades: Iterable[Any]) -> list[dict]:
    """Per-trade FX columns in trade order; ``{}`` for a trade with no listed terms."""
    terms = terms_from_snapshot(snapshot)
    return [
        trade_fields(trade_figures(t, terms[iid]))
        if (iid := str(_get(t, "instrument_id"))) in terms
        else {}
        for t in trades
    ]
