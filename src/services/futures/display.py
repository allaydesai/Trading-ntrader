"""Text the CLI renderers share for futures runs (M4), so ``show``, ``report`` and
``history`` word and format the figures from :mod:`src.services.futures.returns` the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from rich.table import Table

from src.services.futures.returns import (
    FUTURES,
    RATIO_ADJUSTED_NOTE,
    ContractTerms,
    RunSummary,
    asset_class,
    report_fields,
    summarise_run,
    terms_from_snapshot,
    trade_figures,
)

TITLE = "Futures (return on margin is the headline)"


def run_summary(run: Any) -> RunSummary | None:
    """:func:`summarise_run` for a ``BacktestRun``-shaped object; ``None`` for equity."""
    return summarise_run(getattr(run, "config_snapshot", None), getattr(run, "trades", None) or [])


def signed_pct(value: Decimal | None) -> str:
    """``+2.70%`` / ``-1.83%`` / ``0.00%``; ``N/A`` when there is no value."""
    if value is None:
        return "N/A"
    return f"{value:+.2f}%" if value else "0.00%"


def _usd(value: Decimal) -> str:
    return f"${value:,.2f}"


def terms_line(terms: ContractTerms) -> str:
    """One contract's terms as the run used them."""
    return (
        f"x{terms.multiplier} · initial margin {_usd(terms.margin_initial_usd)} · "
        f"maintenance {_usd(terms.margin_maintenance_usd)} · "
        f"{_usd(terms.commission_per_contract_usd)}/contract/side · "
        f"as of {terms.margin_as_of or 'unknown'}"
    )


def summary_rows(summary: RunSummary) -> list[tuple[str, str]]:
    """``(label, value)`` rows, headline first; dollars and points are flagged adjusted."""
    rows = [
        ("Return on margin (headline)", signed_pct(summary.return_on_margin_pct)),
        ("Return on underlying", signed_pct(summary.return_on_underlying_pct)),
        ("Peak margin used", _usd(summary.peak_margin_usd)),
        ("Max contracts", str(summary.max_contracts.normalize())),
        ("Largest notional", _usd(summary.max_notional_usd)),
        ("Closed trades", str(summary.closed_trades)),
        ("Net P&L (ratio-adjusted)", _usd(summary.net_pnl_usd)),
        ("Points per contract (adjusted)", f"{summary.points_per_contract:+.2f}"),
    ]
    rows += [(iid, terms_line(t)) for iid, t in summary.contracts.items()]
    return rows


def history_return_cell(summary: RunSummary) -> str:
    """The history table's Return cell for a futures run: return on margin."""
    return f"RoM {signed_pct(summary.return_on_margin_pct)}"


def symbol_cell(symbol: str, snapshot: Mapping[str, Any] | None) -> str:
    """The symbol, marked ``(fut)`` for a futures run."""
    return f"{symbol} (fut)" if asset_class(snapshot) == FUTURES else symbol


def futures_table(summary: RunSummary) -> Table:
    """A Rich table of :func:`summary_rows`, captioned with the ratio-adjustment note."""
    table = Table(title=TITLE, caption=RATIO_ADJUSTED_NOTE, show_header=False)
    table.add_column("Figure", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    for label, value in summary_rows(summary):
        table.add_row(label, value)
    return table


def summary_text(summary: RunSummary) -> str:
    """Plain-text block for the text report: ``label: value`` lines and the note."""
    lines = [TITLE, *(f"  {label}: {value}" for label, value in summary_rows(summary))]
    return "\n".join([*lines, f"  Note: {RATIO_ADJUSTED_NOTE}"])


def flat_report_fields(summary: RunSummary) -> dict[str, Any]:
    """:func:`report_fields` flattened for the metric/value CSV (``futures_`` prefixed)."""
    fields = report_fields(summary)
    contracts = fields.pop("contracts")
    flat: dict[str, Any] = {"asset_class": fields.pop("asset_class")}
    flat.update({f"futures_{key}": value for key, value in fields.items()})
    for iid, terms in contracts.items():
        flat.update({f"futures_{iid}_{key}": value for key, value in terms.items()})
    return flat


def print_futures(console: Any, run: Any) -> bool:
    """Print a futures run's table (and a blank line); nothing for equity. True if printed."""
    summary = run_summary(run)
    if summary is None:
        return False
    console.print(futures_table(summary))
    console.print()
    return True


# --- Web dashboard (M4, PO ruling Allay 2026-11-22) -------------------------------------


def tone(value: Decimal | None) -> str:
    """Tailwind text colour for a signed figure (the dashboard's green/red/neutral)."""
    if value is None or value == 0:
        return "text-slate-400"
    return "text-green-400" if value > 0 else "text-red-400"


def dashboard_panel(run: Any) -> dict[str, Any] | None:
    """The detail page's futures panel, display-ready; ``None`` for an equity run."""
    summary = run_summary(run)
    if summary is None:
        return None
    headline, underlying, *rows = summary_rows(summary)
    return {
        "return_on_margin": headline[1],
        "return_on_margin_class": tone(summary.return_on_margin_pct),
        "return_on_underlying": underlying[1],
        "return_on_underlying_class": tone(summary.return_on_underlying_pct),
        "rows": rows[: -len(summary.contracts)],
        "terms": rows[-len(summary.contracts) :],
        "note": RATIO_ADJUSTED_NOTE,
    }


def trade_cells(snapshot: Mapping[str, Any] | None, trades: list[Any]) -> list[dict] | None:
    """The trades table's futures cells per trade (``{}`` for an unlisted instrument).

    ``None`` for an equity run, so the table renders its equity columns unchanged.
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
                "contracts": str(fig.contracts.normalize()),
                "notional": _usd(fig.notional_usd),
                "margin": _usd(fig.margin_used_usd),
                "points": "N/A" if fig.points is None else f"{fig.points:+.2f}",
                "points_class": tone(fig.points),
                "underlying": signed_pct(fig.return_on_underlying_pct),
                "underlying_class": tone(fig.return_on_underlying_pct),
                "margin_return": signed_pct(fig.return_on_margin_pct),
                "margin_return_class": tone(fig.return_on_margin_pct),
            }
        )
    return cells


def list_return(run: Any) -> tuple[str, Decimal | None]:
    """``(asset class, return on margin %)`` for the run list."""
    summary = run_summary(run)
    if summary is None:
        return asset_class(getattr(run, "config_snapshot", None)), None
    return FUTURES, summary.return_on_margin_pct
