"""One way to add a backtest venue, picking cost models by instrument type (futures M3).

A venue whose instruments include a futures contract gets the per-contract commission and
dollar margin models from ``src/core/futures_costs.py``; an equity-only venue gets exactly
the ``add_venue`` call it always had (the IBKR per-share fee model, Nautilus' default margin
model). A venue whose fill model is gap-aware also gets next-bar execution (a latency model
and module from ``src/core/fill_models.py``); any other gets none. The orchestrator and the
legacy runner both call ``setup_backtest_venues``, so the choice lives in one place.

A venue listing spot FX pairs (FX M3) is a single-currency USD account (``base_currency=USD``)
charged IB's spot commission plus a modelled half-spread (FX M4, ``src/core/fx_costs.py``), and
the margin gate (``src/core/margin_gate.py``) enforces percentage margin. Given the run's bars,
``setup_backtest_venues`` also adds the instruments and bars itself, rate sources first, and
registers the ``FxRateFeeder`` (``src/core/fx_rates.py``) before the caller adds strategies.

A venue listing spec-built spot crypto pairs (``is_crypto``, crypto M3) gets the account the M1
spike chose (decision C8, ``openspec/changes/archive/2026-11-25-add-crypto-backtesting/design.md``
section 1): a single-currency USD ``MARGIN`` account at leverage 1 (the pairs' ``margin_init``
is 1, so a position locks its full notional), the Kraken Pro spot fee model at the run's tier
(``src/core/crypto_costs.py``) and the margin gate's spot rules (cash, no short). The gate is
what enforces cash: Nautilus runs no risk checks for margin accounts. Crypto and FX venues list
only their own kind; a mix is refused.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money

from src.core.crypto_costs import crypto_snapshot
from src.core.fill_models import GapAwareFillModel, check_bar_alignment, gap_aware_venue_options
from src.core.futures_costs import futures_snapshot, venue_cost_models
from src.core.fx_costs import fx_snapshot
from src.core.fx_rates import BarsAndInstrument, add_fx_data, is_fx, needs_conversion
from src.core.margin_gate import MarginGate, install_margin_gate
from src.core.position_sizing import is_crypto


def _single_currency_venue(instruments: list[Instrument]) -> bool:
    """True for a venue of spot FX or spot crypto pairs only; a mix with others is refused."""
    for kind, member in (("spot FX", is_fx), ("spot crypto", is_crypto)):
        matching = [i for i in instruments if member(i)]
        if matching and len(matching) != len(instruments):
            raise ValueError(f"A venue cannot list {kind} pairs together with other instruments")
        if matching:
            return True
    return False


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
    """Add a USD margin, hedging venue for ``instruments`` with the right cost models.

    Raises:
        ValueError: The venue mixes spot FX or spot crypto pairs with other instruments.
    """
    instruments = list(instruments)
    scale = Decimal(str(cost_multiplier))
    fee_model, margin_model = venue_cost_models(instruments, equity_fee_model, scale, engine.cache)
    extra: dict = {"margin_model": margin_model} if margin_model is not None else {}
    if _single_currency_venue(instruments):  # every balance change (P&L, fees, margin) in USD
        extra["base_currency"] = USD
        # margin_init is the spec's % of notional (FX 3%, spot crypto 100%); Nautilus' margin
        # account default leverage (10) would divide it by ten (3% -> 0.3%, 100% -> 10%)
        extra["default_leverage"] = Decimal(1)
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
) -> MarginGate | None:
    """Add every venue, the margin gate once for futures, FX or crypto, and the data.

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
    gate = install_margin_gate(engine, everything)
    if data is not None:
        if isinstance(fill_model, GapAwareFillModel) and rate_sources:
            # bind_fill_model saw only the traded bars; a rate source's bar at a timestamp the
            # traded pair lacks would release next-bar commands without an open to fill at
            check_bar_alignment([bar for bars, _ in [*data, *rate_sources] for bar in bars])
        add_fx_data(engine, data, rate_sources)
    return gate


def cost_snapshot(cache) -> dict[str, Any]:
    """The ``config_snapshot`` keys recording a run's cost terms: futures, FX, crypto; or ``{}``.

    Read from the run's ``cache``: its instruments, and the FX and crypto cost totals the fee
    models kept there (:func:`src.core.fx_costs.fx_snapshot`,
    :func:`src.core.crypto_costs.crypto_snapshot`).
    """
    instruments = cache.instruments()
    return {
        **futures_snapshot(instruments),
        **fx_snapshot(instruments, cache),
        **crypto_snapshot(instruments, cache),
    }
