"""Futures reporting figures (M4): return on margin, notional, points, from stored rows.

Pure and framework-free. Inputs are a run's ``config_snapshot`` (M3 stores
``futures_contracts`` there, frozen at run time) and its trade rows, as ORM ``Trade`` objects
or dicts with the same keys. Every renderer (CLI ``show``/``report``/``history``, the MCP
run views and export, and the dashboard once its frozen views may change) reads these, so
they can't drift apart.

A run without ``futures_contracts`` is an equity run: :func:`summarise_run` returns ``None``
and callers leave their output exactly as it was.

See ``openspec/changes/add-futures-reporting/design.md`` for the definitions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

FUTURES = "futures"
EQUITY = "equity"
#: FirstRate continuous series are ratio-adjusted: dollars and points of old trades are scaled.
PNL_BASIS = "ratio_adjusted"
RATIO_ADJUSTED_NOTE = (
    "Prices are ratio-adjusted (FirstRate continuous): dollar P&L and points of older trades "
    "are scaled by the adjustment, so compare trades by percentage. Dollar values are kept as "
    "stored."
)
_HUNDRED = Decimal("100")
_PCT = Decimal("0.0001")
_USD = Decimal("0.01")


@dataclass(frozen=True)
class ContractTerms:
    """One contract's terms as the run used them (``config_snapshot.futures_contracts``)."""

    multiplier: Decimal
    margin_initial_usd: Decimal
    margin_maintenance_usd: Decimal
    commission_per_contract_usd: Decimal
    margin_as_of: str | None


@dataclass(frozen=True)
class TradeFigures:
    """A futures trade's figures; returns and points are ``None`` while it is open."""

    instrument_id: str
    contracts: Decimal
    multiplier: Decimal
    notional_usd: Decimal
    margin_used_usd: Decimal
    points: Decimal | None
    return_on_underlying_pct: Decimal | None
    return_on_margin_pct: Decimal | None


@dataclass(frozen=True)
class RunSummary:
    """A futures run's headline figures."""

    asset_class: str
    contracts: dict[str, ContractTerms]
    closed_trades: int
    max_contracts: Decimal
    peak_margin_usd: Decimal
    net_pnl_usd: Decimal
    return_on_margin_pct: Decimal | None
    return_on_underlying_pct: Decimal | None
    points_per_contract: Decimal


def terms_from_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, ContractTerms]:
    """Contract terms by instrument id; ``{}`` for an equity run (or no snapshot)."""
    raw = (snapshot or {}).get("futures_contracts") or {}
    return {
        str(iid): ContractTerms(
            multiplier=Decimal(str(t["multiplier"])),
            margin_initial_usd=Decimal(str(t["margin_initial_usd"])),
            margin_maintenance_usd=Decimal(str(t["margin_maintenance_usd"])),
            commission_per_contract_usd=Decimal(str(t["commission_per_contract_usd"])),
            margin_as_of=t.get("margin_as_of"),
        )
        for iid, t in raw.items()
    }


def asset_class(snapshot: Mapping[str, Any] | None) -> str:
    """``"futures"`` when the run traded a futures contract, else ``"equity"``."""
    return FUTURES if terms_from_snapshot(snapshot) else EQUITY


def _get(trade: Any, name: str) -> Any:
    return trade.get(name) if isinstance(trade, Mapping) else getattr(trade, name, None)


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _is_short(trade: Any) -> bool:
    return str(_get(trade, "order_side") or "").upper().endswith("SELL")


def trade_figures(trade: Any, terms: ContractTerms) -> TradeFigures:
    """Contracts, notional, margin used, adjusted points and returns for one trade."""
    contracts = _dec(_get(trade, "quantity")) or Decimal(0)
    entry = _dec(_get(trade, "entry_price")) or Decimal(0)
    exit_, pnl = _dec(_get(trade, "exit_price")), _dec(_get(trade, "profit_loss"))
    margin = contracts * terms.margin_initial_usd
    points = rou = rom = None
    if exit_ is not None and pnl is not None:
        points = (entry - exit_) if _is_short(trade) else (exit_ - entry)
        rou = points / entry * _HUNDRED if entry else None
        rom = pnl / margin * _HUNDRED if margin else None
    return TradeFigures(
        instrument_id=str(_get(trade, "instrument_id")),
        contracts=contracts,
        multiplier=terms.multiplier,
        notional_usd=contracts * entry * terms.multiplier,
        margin_used_usd=margin,
        points=points,
        return_on_underlying_pct=rou,
        return_on_margin_pct=rom,
    )


def _peak_margin(rows: list[tuple[Any, TradeFigures]]) -> Decimal:
    """Peak concurrent initial margin; exits sort before entries at the same timestamp."""
    events = []
    for trade, fig in rows:
        events.append((_get(trade, "entry_timestamp"), 1, fig.margin_used_usd))
        if (exit_ts := _get(trade, "exit_timestamp")) is not None:
            events.append((exit_ts, 0, -fig.margin_used_usd))
    level = peak = Decimal(0)
    for _, _, delta in sorted(events, key=lambda e: (e[0], e[1])):
        level += delta
        peak = max(peak, level)
    return peak


def _compounded(returns_pct: list[Decimal]) -> Decimal | None:
    if not returns_pct:
        return None
    growth = Decimal(1)
    for r in returns_pct:
        growth *= 1 + r / _HUNDRED
    return (growth - 1) * _HUNDRED


def summarise_run(snapshot: Mapping[str, Any] | None, trades: Iterable[Any]) -> RunSummary | None:
    """The run's futures summary, or ``None`` for an equity run.

    Trades on an instrument the snapshot doesn't list are skipped (they have no terms).
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
        (r for r in rows if r[1].points is not None),
        key=lambda r: _get(r[0], "exit_timestamp"),
    )
    net = sum((_dec(_get(t, "profit_loss")) or Decimal(0) for t, _ in closed), Decimal(0))
    peak = _peak_margin(rows)
    return RunSummary(
        asset_class=FUTURES,
        contracts=terms,
        closed_trades=len(closed),
        max_contracts=max((f.contracts for _, f in rows), default=Decimal(0)),
        peak_margin_usd=peak,
        net_pnl_usd=net,
        return_on_margin_pct=net / peak * _HUNDRED if closed and peak else None,
        return_on_underlying_pct=_compounded(
            [
                f.return_on_underlying_pct
                for _, f in closed
                if f.return_on_underlying_pct is not None
            ]
        ),
        points_per_contract=sum((f.points for _, f in closed if f.points), Decimal(0)),
    )


def _fmt(value: Decimal | None, step: Decimal) -> str | None:
    return None if value is None else str(value.quantize(step))


def report_fields(summary: RunSummary) -> dict[str, Any]:
    """JSON/CSV-safe run fields (decimals as strings, percentages to 4 places)."""
    return {
        "asset_class": summary.asset_class,
        "return_on_margin_pct": _fmt(summary.return_on_margin_pct, _PCT),
        "return_on_underlying_pct": _fmt(summary.return_on_underlying_pct, _PCT),
        "net_pnl_usd": _fmt(summary.net_pnl_usd, _USD),
        "peak_margin_usd": _fmt(summary.peak_margin_usd, _USD),
        "max_contracts": str(summary.max_contracts.normalize()),
        "closed_trades": summary.closed_trades,
        "points_per_contract_adjusted": _fmt(summary.points_per_contract, _USD),
        "pnl_basis": PNL_BASIS,
        "contracts": {
            iid: {
                "multiplier": str(t.multiplier),
                "margin_initial_usd": str(t.margin_initial_usd),
                "margin_maintenance_usd": str(t.margin_maintenance_usd),
                "commission_per_contract_usd": str(t.commission_per_contract_usd),
                "margin_as_of": t.margin_as_of,
            }
            for iid, t in summary.contracts.items()
        },
    }


#: The per-trade columns :func:`trade_fields` adds to a trade export, in order.
TRADE_COLUMNS = (
    "contracts",
    "multiplier",
    "notional_usd",
    "margin_used_usd",
    "points_adjusted",
    "return_on_underlying_pct",
    "return_on_margin_pct",
)


def trade_fields(fig: TradeFigures) -> dict[str, str | None]:
    """A trade's futures columns (:data:`TRADE_COLUMNS`), JSON/CSV-safe."""
    return {
        "contracts": str(fig.contracts.normalize()),
        "multiplier": str(fig.multiplier),
        "notional_usd": _fmt(fig.notional_usd, _USD),
        "margin_used_usd": _fmt(fig.margin_used_usd, _USD),
        "points_adjusted": _fmt(fig.points, _USD),
        "return_on_underlying_pct": _fmt(fig.return_on_underlying_pct, _PCT),
        "return_on_margin_pct": _fmt(fig.return_on_margin_pct, _PCT),
    }


def trade_rows(snapshot: Mapping[str, Any] | None, trades: Iterable[Any]) -> list[dict]:
    """Per-trade futures columns in trade order; ``{}`` for a trade with no listed terms."""
    terms = terms_from_snapshot(snapshot)
    return [
        trade_fields(trade_figures(t, terms[iid]))
        if (iid := str(_get(t, "instrument_id"))) in terms
        else {}
        for t in trades
    ]
