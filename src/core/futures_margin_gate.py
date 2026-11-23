"""Pre-trade futures margin check for backtests (futures M3), now one case of the margin gate.

FX M4 generalised the gate to any instrument with a per-unit margin requirement
(``src/core/margin_gate.py``: futures dollars per contract, FX a percentage of USD notional).
This module keeps the futures names and reasons exactly as M3 shipped them:
:class:`FuturesMarginGate` takes ``{instrument_id: FuturesTerms}``, and the deny, size-increase
and pending-modify reasons are unchanged. The full rules live in the ``margin_gate`` docstring.
"""

from __future__ import annotations

from collections.abc import Mapping

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument

from src.core.futures_costs import FuturesTerms, terms_by_instrument
from src.core.margin_gate import (
    DENIAL_REASON,
    RISK_ENDPOINT,
    FuturesRequirement,
    MarginGate,
)

QTY_INCREASE_REASON = "FUTURES_QTY_INCREASE_NOT_SUPPORTED"
MODIFY_PENDING_REASON = "FUTURES_MODIFY_PENDING"

__all__ = [
    "DENIAL_REASON",
    "MODIFY_PENDING_REASON",
    "QTY_INCREASE_REASON",
    "RISK_ENDPOINT",
    "FuturesMarginGate",
    "install_futures_margin_gate",
]


class FuturesMarginGate(MarginGate):
    """Refuses futures submits and modifies whose initial margin exceeds free balance."""

    def __init__(self, risk_engine, cache, msgbus, terms: Mapping[InstrumentId, FuturesTerms]):
        """Bind to a kernel's risk engine, cache and bus; ``terms`` are dollars per contract."""
        requirements = {iid: FuturesRequirement(t) for iid, t in terms.items()}
        super().__init__(risk_engine, cache, msgbus, requirements)


def install_futures_margin_gate(
    engine: BacktestEngine, instruments: list[Instrument]
) -> FuturesMarginGate | None:
    """Install the gate when ``instruments`` include a futures contract; else do nothing.

    Futures only; production venues call :func:`src.core.margin_gate.install_margin_gate`,
    which also covers spot FX pairs.
    """
    terms = terms_by_instrument(instruments)
    if not terms:
        return None
    kernel = engine.kernel
    gate = FuturesMarginGate(kernel.risk_engine, kernel.cache, kernel.msgbus, terms)
    gate.install()
    return gate
