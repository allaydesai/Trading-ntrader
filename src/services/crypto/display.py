"""Text the renderers share for spot crypto runs (crypto M5), so ``show``, ``report``,
``history``, ``compare``, the MCP views and the dashboard word and format the figures from
:mod:`src.services.crypto.returns` the same way.

The account return on USD equity stays the headline (decision C19): these rows sit beside it.
Coin quantities are shown at the pair's full size precision (``0.12820512``), never rounded to
whole units.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from rich.table import Table

from src.services.crypto.returns import (
    PairTerms,
    RunSummary,
    asset_class,
    note,
    quantity_text,
    report_fields,
    summarise_run,
    terms_from_snapshot,
    trade_figures,
)
from src.services.fx.display import signed_pct, tone, usd

TITLE = "Crypto (account return on USD equity is the headline)"


def run_summary(run: Any) -> RunSummary | None:
    """:func:`summarise_run` for a ``BacktestRun``-shaped object; ``None`` if not crypto."""
    return summarise_run(
        getattr(run, "config_snapshot", None),
        getattr(run, "trades", None) or [],
        getattr(run, "initial_capital", None),
    )


def pct(value: Decimal | None) -> str:
    """A 0..1 ratio as ``19.96%``; ``N/A`` when unknown."""
    return "N/A" if value is None else f"{value * 100:.2f}%"


def pair_label(terms: PairTerms) -> str:
    """``BTC/USD (crypto, KRAKEN, fee tier 2)``."""
    return f"{terms.pair} (crypto, {terms.venue}, fee tier {terms.fee_tier})"


def terms_line(terms: PairTerms) -> str:
    """One pair's terms and charged costs, as the run used them."""
    discount = " (discounted pair)" if terms.discounted_pair else ""
    return (
        f"{pair_label(terms)} · maker {terms.maker_pct}% / taker {terms.taker_pct}%{discount} · "
        f"spread {terms.spread_bps} bp (modelled, UNVERIFIED) · size precision "
        f"{terms.size_precision} · charged maker {usd(terms.commission_maker_usd)}, taker "
        f"{usd(terms.commission_taker_usd)}, spread {usd(terms.spread_usd)} · fees as of "
        f"{terms.fees_as_of or 'unknown'}"
    )


def _largest(summary: RunSummary) -> str:
    text = quantity_text(summary.max_quantity, summary.size_precision)
    bases = {t.base for t in summary.pairs.values()}
    return f"{text} {bases.pop()}" if len(bases) == 1 else text


def _dropped(summary: RunSummary) -> str:
    count = summary.orders_below_minimum
    return "not recorded" if count is None else str(count)


def summary_rows(summary: RunSummary) -> list[tuple[str, str]]:
    """``(label, value)`` rows, then one terms row per pair."""
    rows = [
        ("Closed-trade P&L (USD)", usd(summary.net_pnl_usd)),
        ("Closed trades", str(summary.closed_trades)),
        ("Return on underlying", signed_pct(summary.return_on_underlying_pct)),
        ("Largest position", _largest(summary)),
        ("Peak notional (USD)", usd(summary.peak_notional_usd)),
        ("Peak exposure (notional / equity)", pct(summary.peak_exposure)),
        ("Commission, maker (USD)", usd(summary.commission_maker_usd)),
        ("Commission, taker (USD)", usd(summary.commission_taker_usd)),
        ("Modelled spread (USD)", usd(summary.spread_usd)),
        ("Total costs (USD)", usd(summary.costs_usd)),
        ("Orders dropped under minimum", _dropped(summary)),
        ("Annualised on", f"{summary.periods_per_year} days a year"),
        ("Account currency", summary.account_currency),
    ]
    return rows + [(iid, terms_line(t)) for iid, t in summary.pairs.items()]


def symbol_cell(symbol: str, snapshot: Mapping[str, Any] | None) -> str | None:
    """``BTC (crypto)`` for a crypto run; ``None`` otherwise (the caller keeps its own cell)."""
    return f"{symbol} (crypto)" if asset_class(snapshot) else None


def crypto_table(summary: RunSummary) -> Table:
    """A Rich table of :func:`summary_rows`, captioned with the spot/fee-tier note."""
    table = Table(title=TITLE, caption=note(summary.fee_tier), show_header=False)
    table.add_column("Figure", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    for label, value in summary_rows(summary):
        table.add_row(label, value)
    return table


def summary_text(summary: RunSummary) -> str:
    """Plain-text block for the text report: ``label: value`` lines and the note."""
    lines = [TITLE, *(f"  {label}: {value}" for label, value in summary_rows(summary))]
    return "\n".join([*lines, f"  Note: {note(summary.fee_tier)}"])


def flat_report_fields(summary: RunSummary) -> dict[str, Any]:
    """:func:`report_fields` flattened for the metric/value CSV (``crypto_`` prefixed)."""
    fields = report_fields(summary)
    pairs = fields.pop("pairs")
    flat: dict[str, Any] = {"asset_class": fields.pop("asset_class")}
    flat.update({f"crypto_{key}": value for key, value in fields.items()})
    for iid, terms in pairs.items():
        costs = terms.pop("costs_usd")
        terms.pop("unverified")
        flat.update({f"crypto_{iid}_{key}": value for key, value in terms.items()})
        flat.update({f"crypto_{iid}_{key}_usd": value for key, value in costs.items()})
    return flat


def print_crypto(console: Any, run: Any) -> bool:
    """Print a crypto run's table (and a blank line); nothing otherwise. True if printed."""
    summary = run_summary(run)
    if summary is None:
        return False
    console.print(crypto_table(summary))
    console.print()
    return True


# --- Web dashboard (crypto M5, protected-file sign-off Allay 2026-11-24) -------------------

#: Rows the panel shows in its own costs box instead of the figures list.
_PANEL_COSTS = ("Commission, maker (USD)", "Commission, taker (USD)", "Modelled spread (USD)")


def dashboard_panel(run: Any) -> dict[str, Any] | None:
    """The detail page's crypto panel, display-ready; ``None`` for a non-crypto run."""
    summary = run_summary(run)
    if summary is None:
        return None
    rows = summary_rows(summary)
    n_pairs = len(summary.pairs)
    return {
        "net_pnl": usd(summary.net_pnl_usd),
        "net_pnl_class": tone(summary.net_pnl_usd),
        "fee_tier": summary.fee_tier,
        "maker": usd(summary.commission_maker_usd),
        "taker": usd(summary.commission_taker_usd),
        "spread": usd(summary.spread_usd),
        "rows": [r for r in rows[1 : len(rows) - n_pairs] if r[0] not in _PANEL_COSTS],
        "pairs": rows[-n_pairs:] if n_pairs else [],
        "note": note(summary.fee_tier),
    }


def _price(value: Any, terms: PairTerms) -> str:
    """A price at the pair's price precision (``78,000.0000``)."""
    if value is None:
        return "-"
    return f"{Decimal(str(value)):,.{terms.price_precision}f}"


def trade_cells(snapshot: Mapping[str, Any] | None, trades: list[Any]) -> list[dict] | None:
    """The trades table's crypto cells per trade (``{}`` for an unlisted instrument).

    ``None`` for a non-crypto run, so the table renders its other columns unchanged.
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
                "quantity": quantity_text(fig.quantity, fig.size_precision),
                "entry": _price(get("entry_price"), terms[iid]),
                "exit": _price(get("exit_price"), terms[iid]),
                "notional": usd(fig.notional_usd),
                "underlying": signed_pct(fig.return_on_underlying_pct),
                "underlying_class": tone(fig.return_on_underlying_pct),
                "pnl": usd(fig.pnl_usd),
                "pnl_class": tone(fig.pnl_usd),
            }
        )
    return cells
