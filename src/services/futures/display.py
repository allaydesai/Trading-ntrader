"""Text the CLI renderers share for futures runs (M4), so ``show``, ``report`` and
``history`` word and format the figures from :mod:`src.services.futures.returns` the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from src.services.futures.returns import FUTURES, ContractTerms, RunSummary, asset_class


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
