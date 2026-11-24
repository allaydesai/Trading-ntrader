"""Futures backtest costs: per-contract commission and dollar margin (futures M3).

Both models read a contract's terms from the ``FuturesContract`` itself: the M1 builder
(``src/services/futures/instrument_builder.py``) copies the spec's dollar margins and
per-side commission into ``instrument.info``. A futures contract without them is an error,
never a silently free or unmargined one.

Margin is charged in **dollars per contract**, not as Nautilus' fraction of notional.
Nautilus 1.220 lets a venue take a custom ``MarginModel`` (``add_venue(margin_model=...)``),
so the requirement is ``contracts x initial_usd`` (orders) and ``contracts x maintenance_usd``
(positions) at any price, with no drift from a reference price. Nautilus computes and locks
that margin but never refuses an order over it on a margin account; that is the job of
``src/core/futures_margin_gate.py``.

Equity-only venues get exactly the model they had before (the IBKR per-share model, no margin
model), so equity results are unchanged.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nautilus_trader.accounting.margin_models import LeveragedMarginModel, MarginModel
from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import FuturesContract, Instrument
from nautilus_trader.model.objects import Money

from src.core.crypto_costs import CryptoCommissionModel, crypto_terms_by_instrument
from src.core.fx_costs import FxCommissionModel, fx_terms_by_instrument

_TERM_KEYS = ("margin_initial_usd", "margin_maintenance_usd", "commission_per_contract_usd")


@dataclass(frozen=True)
class FuturesTerms:
    """The dollar terms of one futures contract, per contract."""

    multiplier: Decimal
    initial_margin_usd: Decimal
    maintenance_margin_usd: Decimal
    commission_per_contract_usd: Decimal
    margin_as_of: str | None = None

    def as_snapshot(self) -> dict[str, str | None]:
        """JSON-safe copy for the run's ``config_snapshot`` (dashboard reporting, M4)."""
        return {
            "multiplier": str(self.multiplier),
            "margin_initial_usd": str(self.initial_margin_usd),
            "margin_maintenance_usd": str(self.maintenance_margin_usd),
            "commission_per_contract_usd": str(self.commission_per_contract_usd),
            "margin_as_of": self.margin_as_of,
        }


def futures_terms(instrument: Instrument) -> FuturesTerms | None:
    """The contract's dollar terms, or ``None`` for anything that is not a futures contract.

    Raises:
        ValueError: A ``FuturesContract`` whose ``info`` lacks the spec terms.
    """
    if not isinstance(instrument, FuturesContract):
        return None
    info = instrument.info or {}
    missing = [key for key in _TERM_KEYS if key not in info]
    if missing:
        raise ValueError(
            f"Futures contract {instrument.id} has no {', '.join(missing)} in its info; "
            "build it with build_futures_contract() so margin and commission come from "
            "the contract spec."
        )
    return FuturesTerms(
        multiplier=instrument.multiplier.as_decimal(),
        initial_margin_usd=Decimal(str(info["margin_initial_usd"])),
        maintenance_margin_usd=Decimal(str(info["margin_maintenance_usd"])),
        commission_per_contract_usd=Decimal(str(info["commission_per_contract_usd"])),
        margin_as_of=info.get("margin_as_of"),
    )


def terms_by_instrument(instruments: Iterable[Instrument]) -> dict[InstrumentId, FuturesTerms]:
    """``{instrument_id: terms}`` for the futures contracts among ``instruments``."""
    terms = {}
    for instrument in instruments:
        found = futures_terms(instrument)
        if found is not None:
            terms[instrument.id] = found
    return terms


#: How ``trades.profit_pct`` reads for a futures trade (see docs/agent/futures-backtesting.md).
PROFIT_PCT_BASIS = "underlying"


def futures_snapshot(instruments: Iterable[Instrument]) -> dict[str, Any]:
    """``config_snapshot`` keys for a run's futures contracts; ``{}`` when there are none.

    Records each contract's multiplier, dollar margins and commission as the run used them,
    so reporting (M4) can show notional (``qty x price x multiplier``), margin used
    (``qty x margin_initial_usd``) and return on margin without re-reading a spec file that
    may have changed since.
    """
    terms = terms_by_instrument(instruments)
    if not terms:
        return {}
    return {
        "futures_contracts": {str(iid): t.as_snapshot() for iid, t in terms.items()},
        "profit_pct_basis": PROFIT_PCT_BASIS,
    }


class FuturesCommissionModel(FeeModel):
    """A flat per-contract, per-side commission (IB fixed-rate style), scaled by ``scale``.

    Instruments without a rate go to ``fallback`` (the equity model), so a venue that also
    lists a non-futures instrument still charges it the way it did before.
    """

    def __init__(
        self,
        rates: Mapping[InstrumentId, Decimal],
        fallback: FeeModel | None = None,
        scale: Decimal = Decimal("1"),
    ) -> None:
        """Rates are USD per contract per side; ``scale`` is the cost-stress multiplier."""
        super().__init__()
        if any(rate < 0 for rate in rates.values()):
            raise ValueError("commission per contract cannot be negative")
        if scale < 0:
            raise ValueError("commission scale cannot be negative")
        self.rates = dict(rates)
        self.fallback = fallback
        self.scale = scale

    def get_commission(self, order, fill_qty, fill_px, instrument) -> Money:  # noqa: D102
        rate = self.rates.get(instrument.id)
        if rate is None:
            if self.fallback is None:
                raise ValueError(f"No per-contract commission for {instrument.id}")
            return self.fallback.get_commission(order, fill_qty, fill_px, instrument)
        return Money(fill_qty.as_decimal() * rate * self.scale, instrument.quote_currency)

    def __repr__(self) -> str:
        """Rates per instrument."""
        rates = ", ".join(f"{iid}={rate}" for iid, rate in self.rates.items())
        return f"FuturesCommissionModel({rates}, scale={self.scale})"


class FuturesMarginModel(MarginModel):
    """Dollar margin per contract from the spec; other instruments keep Nautilus' default.

    Overrides the ``cpdef`` methods of Nautilus' ``MarginModel``; the margin account calls
    them positionally in this order.
    """

    def __init__(self, terms: Mapping[InstrumentId, FuturesTerms]) -> None:
        """``terms`` maps each futures instrument to its dollar margins."""
        self.terms = dict(terms)
        self._fallback = LeveragedMarginModel()

    def calculate_margin_init(  # noqa: D102
        self, instrument, quantity, price, leverage, use_quote_for_inverse=False
    ) -> Money:
        terms = self.terms.get(instrument.id)
        if terms is None:
            return self._fallback.calculate_margin_init(
                instrument, quantity, price, leverage, use_quote_for_inverse
            )
        return Money(quantity.as_decimal() * terms.initial_margin_usd, instrument.quote_currency)

    def calculate_margin_maint(  # noqa: D102
        self, instrument, side, quantity, price, leverage, use_quote_for_inverse=False
    ) -> Money:
        terms = self.terms.get(instrument.id)
        if terms is None:
            return self._fallback.calculate_margin_maint(
                instrument, side, quantity, price, leverage, use_quote_for_inverse
            )
        return Money(
            quantity.as_decimal() * terms.maintenance_margin_usd, instrument.quote_currency
        )


def venue_cost_models(
    instruments: Iterable[Instrument],
    equity_fee_model: FeeModel,
    cost_multiplier: Decimal = Decimal("1"),
    cache=None,
) -> tuple[FeeModel, MarginModel | None]:
    """The fee and margin model for one venue listing ``instruments``.

    With no futures contract or spot FX pair among them this returns
    ``(equity_fee_model, None)``: the venue is set up exactly as before. Futures get a
    per-contract commission model (falling back to the equity model) and a dollar-per-contract
    margin model. An FX venue (FX M4; spot pairs only, ``backtest_venue`` refuses a mix) gets
    ``FxCommissionModel`` (``cache`` gives it cross rates and keeps its cost totals) and
    Nautilus' default margin model, which applies the pair's percentage ``margin_init``. A spot
    crypto venue (crypto M3; crypto pairs only, ``backtest_venue`` refuses a mix) gets
    ``CryptoCommissionModel`` and Nautilus' default margin model (``margin_init`` 1).
    """
    instruments = list(instruments)
    crypto = crypto_terms_by_instrument(instruments)
    if crypto:
        return CryptoCommissionModel(crypto, cache=cache, scale=cost_multiplier), None
    fx = fx_terms_by_instrument(instruments)
    if fx:
        return FxCommissionModel(fx, cache=cache, scale=cost_multiplier), None
    terms = terms_by_instrument(instruments)
    if not terms:
        return equity_fee_model, None
    rates = {iid: t.commission_per_contract_usd for iid, t in terms.items()}
    fee_model = FuturesCommissionModel(rates, fallback=equity_fee_model, scale=cost_multiplier)
    return fee_model, FuturesMarginModel(terms)
