"""One way to add a backtest venue, picking cost models by instrument type (futures M3).

A venue whose instruments include a futures contract gets the per-contract commission and
dollar margin models from ``src/core/futures_costs.py``; an equity-only venue gets exactly
the ``add_venue`` call it always had (the IBKR per-share fee model, Nautilus' default margin
model). A venue whose fill model is gap-aware also gets next-bar execution (a latency model
and module from ``src/core/fill_models.py``); any other gets none. The orchestrator and the
legacy runner both call ``setup_backtest_venues``, so the choice lives in one place.

A venue listing spot FX pairs (FX M3) is a single-currency USD account (``base_currency=USD``)
charged nothing until M4's FX cost models land (``src/core/fx_costs.py``). Given the run's bars,
``setup_backtest_venues`` also adds the instruments and bars itself, rate sources first, and
registers the ``FxRateFeeder`` (``src/core/fx_rates.py``) before the caller adds strategies.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money

from src.core.fill_models import gap_aware_venue_options
from src.core.futures_costs import venue_cost_models
from src.core.futures_margin_gate import FuturesMarginGate, install_futures_margin_gate
from src.core.fx_costs import FxNoCommissionModel
from src.core.fx_rates import BarsAndInstrument, add_fx_data, is_fx, needs_conversion


def _fx_venue(instruments: list[Instrument]) -> bool:
    """True for a venue of spot FX pairs only; a mix with other instruments is refused."""
    fx = [i for i in instruments if is_fx(i)]
    if fx and len(fx) != len(instruments):
        raise ValueError("A venue cannot list spot FX pairs together with other instruments")
    return bool(fx)


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
    instruments = list(instruments)
    scale = Decimal(str(cost_multiplier))
    fee_model, margin_model = venue_cost_models(instruments, equity_fee_model, scale)
    extra: dict = {"margin_model": margin_model} if margin_model is not None else {}
    if _fx_venue(instruments):  # every balance change in USD; FX costs arrive in M4
        fee_model = FxNoCommissionModel()
        extra["base_currency"] = USD
    extra.update(gap_aware_venue_options(fill_model))  # next-bar execution, gap-aware only
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
    data: Sequence[BarsAndInstrument] | None = None,
    rate_sources: Sequence[BarsAndInstrument] = (),
) -> FuturesMarginGate | None:
    """Add every venue, the futures margin gate once if any instrument is a future, and the data.

    With ``data`` (each traded instrument and its bars) the instruments and bars are added here
    through :func:`src.core.fx_rates.add_fx_data`: conversion-only ``rate_sources`` (GBPUSD for
    an EURGBP run) first, and the ``FxRateFeeder`` registered when a pair settles outside USD.
    Call it before adding strategies. Without ``data`` the caller adds instruments and bars, as
    before, and a pair needing conversion is refused (nothing would feed its rate).

    Raises:
        FxConversionPairError: A pair's rate source is neither traded nor in ``rate_sources``.
        ValueError: An FX pair settling outside USD with no ``data`` to register a feeder for.
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
    if data is None and any(needs_conversion(i) for i in everything):
        raise ValueError("FX pairs settling outside USD need data= so a rate feeder is registered")
    gate = install_futures_margin_gate(engine, everything)
    if data is not None:
        add_fx_data(engine, data, rate_sources)
    return gate
