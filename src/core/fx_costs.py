"""FX venue cost models. M3 ships only the placeholder; M4 adds ``FxCommissionModel``.

An FX venue's account is single-currency USD (``src/core/backtest_venue.py``), and the equity
``IBKRCommissionModel`` charges per share in the instrument's quote currency, which is wrong for
FX on both counts (100,000 EUR is not 100,000 shares, and a JPY commission would need a rate).
Until M4 models IB's spot schedule (design D6/D7), FX fills are charged nothing, in USD.
"""

from __future__ import annotations

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.model.orders import Order


class FxNoCommissionModel(FeeModel):
    """Charge every FX fill USD 0.00 (costs are M4's: design section 4)."""

    def get_commission(
        self,
        order: Order,
        fill_qty: Quantity,
        fill_px: Price,
        instrument: Instrument,
    ) -> Money:
        return Money(0, USD)
