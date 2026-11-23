"""FX M4: the margin gate's FX requirement, driven through stand-ins of the kernel parts.

Real ``CurrencyPair``s and Nautilus ``Cache`` prices; orders and the account are stand-ins so
each case is exact. Engine-level behaviour: ``tests/integration/core/test_fx_margin_engine.py``.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.objects import Price, Quantity

from src.core.margin_gate import DENIAL_REASON, FxRequirement, MarginGate
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

EURUSD = build_fx_pair(get_pair_spec("EURUSD"))
EURGBP = build_fx_pair(get_pair_spec("EURGBP"))
_IDS = iter(range(1, 10_000))


def _order(instrument, qty: int, price: str | None = None):
    return SimpleNamespace(
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        quantity=Quantity.from_int(qty),
        leaves_qty=Quantity.from_int(qty),
        price=Price.from_str(price) if price else None,
        trigger_price=None,
        status=OrderStatus.INITIALIZED,
        is_reduce_only=False,
        parent_order_id=None,
        client_order_id=ClientOrderId(f"O-{next(_IDS)}"),
    )


class _Cache:
    """No bars and no GBP rate yet: only limit orders can be priced (and only USD quotes)."""

    def __init__(self, free: str) -> None:
        self._free = Decimal(free)

    def instrument(self, instrument_id):
        return {EURUSD.id: EURUSD, EURGBP.id: EURGBP}[instrument_id]

    def price(self, instrument_id, price_type):
        return None

    def get_xrate(self, *args):
        return None

    def account_for_venue(self, venue):
        return SimpleNamespace(
            base_currency=USD,
            balance_free=lambda currency: SimpleNamespace(as_decimal=lambda: self._free),
        )

    def orders_inflight(self, venue=None):
        return []

    def position(self, position_id):
        return None


def _gate(free: str = "10000") -> MarginGate:
    terms = {i.id: FxRequirement(Decimal("0.03")) for i in (EURUSD, EURGBP)}
    return MarginGate(risk_engine=None, cache=_Cache(free), msgbus=None, terms=terms)


def test_an_unpriceable_sibling_does_not_waive_a_priced_over_margin_entry():
    # 400,000 EURUSD limit at 1.10000 needs 13,200; the market order has no price yet.
    orders = [_order(EURUSD, 400_000, "1.10000"), _order(EURUSD, 1_000)]
    reason = _gate().denial_reason(orders, None)
    assert reason is not None and reason.startswith(DENIAL_REASON)
    assert "13200.00 USD" in reason


def test_an_unpriceable_order_alone_is_passed_on():
    assert _gate().denial_reason([_order(EURUSD, 400_000)], None) is None  # no bar yet
    assert _gate().denial_reason([_order(EURGBP, 400_000, "0.85000")], None) is None  # no GBP


def test_a_priced_entry_within_margin_passes():
    assert _gate().denial_reason([_order(EURUSD, 300_000, "1.10000")], None) is None  # 9,900
