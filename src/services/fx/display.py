"""Text the renderers share for FX runs (M5), so ``show``, ``report``, ``history``, ``compare``
and the dashboard word and format the figures from :mod:`src.services.fx.returns` the same way.

The account return on USD equity stays the headline (design D10): these rows sit beside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from rich.table import Table

from src.services.fx.returns import (
    FINANCING_NOTE,
    LOT_UNITS,
    PairTerms,
    RunSummary,
    asset_class,
    report_fields,
    summarise_run,
    terms_from_snapshot,
    trade_figures,
)

TITLE = "FX (account return on USD equity is the headline)"


def run_summary(run: Any) -> RunSummary | None:
    """:func:`summarise_run` for a ``BacktestRun``-shaped object; ``None`` if not FX."""
    return summarise_run(
        getattr(run, "config_snapshot", None),
        getattr(run, "trades", None) or [],
        getattr(run, "initial_capital", None),
    )


def signed_pct(value: Decimal | None) -> str:
    """``+0.46%`` / ``-0.33%`` / ``0.00%``; ``N/A`` when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%" if value else "0.00%"


def usd(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}"


def pips(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value:+.1f}"


def leverage(value: Decimal | None) -> str:
    return "N/A" if value is None else f"{value:.2f}x"


def units_lots(units: Decimal) -> str:
    """``100,000 (1.00 lots)``."""
    return f"{units:,.0f} ({units / LOT_UNITS:.2f} lots)"


def terms_line(terms: PairTerms) -> str:
    """One pair's terms and charged costs, as the run used them."""
    commission = terms.raw.get("commission") or {}
    return (
        f"{terms.base}/{terms.quote} · pip {terms.pip_size} · margin {terms.margin_pct}% · "
        f"spread {terms.typical_spread_pips} pips · "
        f"{commission.get('rate_bps', '?')} bp (min ${commission.get('min_per_order_usd', '?')}"
        f"/order) · charged commission {usd(terms.commission_usd)}, "
        f"spread {usd(terms.spread_usd)} · as of {terms.margin_as_of or 'unknown'} (UNVERIFIED)"
    )


def summary_rows(summary: RunSummary) -> list[tuple[str, str]]:
    """``(label, value)`` rows, then one terms row per pair."""
    rows = [
        ("Net P&L (USD)", usd(summary.net_pnl_usd)),
        ("Closed trades", str(summary.closed_trades)),
        ("Pips (sum of closed trades)", pips(summary.pips)),
        ("Return on underlying", signed_pct(summary.return_on_underlying_pct)),
        ("Largest position", units_lots(summary.max_units)),
        ("Peak notional (USD)", usd(summary.peak_notional_usd)),
        ("Peak notional leverage", leverage(summary.peak_leverage)),
        ("Peak margin used", usd(summary.peak_margin_usd)),
        ("Return on margin (secondary)", signed_pct(summary.return_on_margin_pct)),
        ("Commission (USD)", usd(summary.commission_usd)),
        ("Modelled spread (USD)", usd(summary.spread_usd)),
        ("Total costs (USD)", usd(summary.costs_usd)),
    ]
    return rows + [(iid, terms_line(t)) for iid, t in summary.pairs.items()]


def symbol_cell(symbol: str, snapshot: Mapping[str, Any] | None) -> str | None:
    """``EURUSD (fx)`` for an FX run; ``None`` otherwise (the caller keeps its own cell)."""
    return f"{symbol} (fx)" if asset_class(snapshot) else None


def fx_table(summary: RunSummary) -> Table:
    """A Rich table of :func:`summary_rows`, captioned with the financing note."""
    table = Table(title=TITLE, caption=FINANCING_NOTE, show_header=False)
    table.add_column("Figure", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    for label, value in summary_rows(summary):
        table.add_row(label, value)
    return table


def summary_text(summary: RunSummary) -> str:
    """Plain-text block for the text report: ``label: value`` lines and the note."""
    lines = [TITLE, *(f"  {label}: {value}" for label, value in summary_rows(summary))]
    return "\n".join([*lines, f"  Note: {FINANCING_NOTE}"])


def flat_report_fields(summary: RunSummary) -> dict[str, Any]:
    """:func:`report_fields` flattened for the metric/value CSV (``fx_`` prefixed)."""
    fields = report_fields(summary)
    pairs = fields.pop("pairs")
    flat: dict[str, Any] = {"asset_class": fields.pop("asset_class")}
    flat.update({f"fx_{key}": value for key, value in fields.items()})
    for iid, terms in pairs.items():
        costs = terms.pop("costs_usd")
        terms.pop("commission")
        terms.pop("unverified")
        flat.update({f"fx_{iid}_{key}": value for key, value in terms.items()})
        flat.update({f"fx_{iid}_{key}_usd": value for key, value in costs.items()})
    return flat


def print_fx(console: Any, run: Any) -> bool:
    """Print an FX run's table (and a blank line); nothing otherwise. True if printed."""
    summary = run_summary(run)
    if summary is None:
        return False
    console.print(fx_table(summary))
    console.print()
    return True


# --- Web dashboard (M5, D12 ruling Allay 2026-11-22) ------------------------------------


def tone(value: Decimal | None) -> str:
    """Tailwind text colour for a signed figure (the dashboard's green/red/neutral)."""
    if value is None or value == 0:
        return "text-slate-400"
    return "text-green-400" if value > 0 else "text-red-400"


#: Rows the panel shows in its own costs box instead of the figures list.
_PANEL_COSTS = ("Commission (USD)", "Modelled spread (USD)")


def dashboard_panel(run: Any) -> dict[str, Any] | None:
    """The detail page's FX panel, display-ready; ``None`` for a non-FX run."""
    summary = run_summary(run)
    if summary is None:
        return None
    rows = summary_rows(summary)
    n_pairs = len(summary.pairs)
    return {
        "net_pnl": usd(summary.net_pnl_usd),
        "net_pnl_class": tone(summary.net_pnl_usd),
        "pips": pips(summary.pips),
        "pips_class": tone(summary.pips),
        "leverage": leverage(summary.peak_leverage),
        "commission": usd(summary.commission_usd),
        "spread": usd(summary.spread_usd),
        "rows": [r for r in rows[1 : len(rows) - n_pairs] if r[0] not in _PANEL_COSTS],
        "terms": rows[-n_pairs:] if n_pairs else [],
        "note": FINANCING_NOTE,
    }


def trade_table_cells(snapshot: Mapping[str, Any] | None, trades: list[Any]) -> dict[str, Any]:
    """The trades table's per-trade cells: ``futures_cells`` (M4) and ``fx_cells`` (M5).

    Each is ``None`` unless the run is of that asset class, so an equity table is unchanged.
    """
    from src.services.futures.display import trade_cells as futures_trade_cells

    return {
        "futures_cells": futures_trade_cells(snapshot, trades),
        "fx_cells": trade_cells(snapshot, trades),
    }


def _price(value: Any, terms: PairTerms) -> str:
    """A price at the pair's quoting precision: a pip and one fractional digit (1.08000)."""
    if value is None:
        return "-"
    places = len(f"{terms.pip_size.normalize():f}".partition(".")[2]) + 1
    return f"{Decimal(str(value)):,.{places}f}"


def trade_cells(snapshot: Mapping[str, Any] | None, trades: list[Any]) -> list[dict] | None:
    """The trades table's FX cells per trade (``{}`` for an unlisted instrument).

    ``None`` for a non-FX run, so the table renders its other columns unchanged.
    """
    terms = terms_from_snapshot(snapshot)
    if not terms:
        return None
    cells: list[dict] = []
    for trade in trades:
        get = trade.get if isinstance(trade, Mapping) else lambda k: getattr(trade, k, None)
        iid = str(get("instrument_id"))
        if iid not in terms:
            cells.append({})
            continue
        fig = trade_figures(trade, terms[iid])
        cells.append(
            {
                "units": f"{fig.units:,.0f}",
                "entry": _price(get("entry_price"), terms[iid]),
                "exit": _price(get("exit_price"), terms[iid]),
                "lots": f"{fig.lots:.2f}",
                "notional": usd(fig.notional_usd),
                "pips": pips(fig.pips),
                "pips_class": tone(fig.pips),
                "underlying": signed_pct(fig.return_on_underlying_pct),
                "underlying_class": tone(fig.return_on_underlying_pct),
                "pnl": usd(fig.pnl_usd),
                "pnl_class": tone(fig.pnl_usd),
            }
        )
    return cells
