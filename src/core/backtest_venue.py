"""One way to add a backtest venue, picking cost models by instrument type (futures M3).

A venue whose instruments include a futures contract gets the per-contract commission and
dollar margin models from ``src/core/futures_costs.py``; an equity-only venue gets exactly
the ``add_venue`` call it always had (the IBKR per-share fee model, Nautilus' default margin
model). The orchestrator and the legacy runner both call ``setup_backtest_venues``, so the
choice lives in one place.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money

from src.core.futures_costs import venue_cost_models
from src.core.futures_margin_gate import FuturesMarginGate, install_futures_margin_gate


def add_backtest_venue(
    engine: BacktestEngine,
    venue: Venue,
    instruments: Iterable[Instrument],
    *,
    starting_balance: float | Decimal,
    fill_model: FillModel,
    equity_fee_model: FeeModel,
    cost_multiplier: Decimal | float = Decimal("1"),
) -> None:
    """Add a USD margin, hedging venue for ``instruments`` with the right cost models."""
    scale = Decimal(str(cost_multiplier))
    fee_model, margin_model = venue_cost_models(instruments, equity_fee_model, scale)
    extra = {"margin_model": margin_model} if margin_model is not None else {}
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(starting_balance, USD)],
        fill_model=fill_model,
        fee_model=fee_model,
        **extra,
    )


def setup_backtest_venues(
    engine: BacktestEngine,
    venues: Mapping[Venue, list[Instrument]],
    *,
    starting_balance: float | Decimal,
    fill_model: FillModel,
    equity_fee_model: FeeModel,
    cost_multiplier: Decimal | float = Decimal("1"),
) -> FuturesMarginGate | None:
    """Add every venue, then the futures margin gate once if any instrument is a future.

    The instruments themselves are not added here; the caller adds them with their bars.
    """
    for venue, instruments in venues.items():
        add_backtest_venue(
            engine,
            venue,
            instruments,
            starting_balance=starting_balance,
            fill_model=fill_model,
            equity_fee_model=equity_fee_model,
            cost_multiplier=cost_multiplier,
        )
    everything = [instrument for group in venues.values() for instrument in group]
    return install_futures_margin_gate(engine, everything)
