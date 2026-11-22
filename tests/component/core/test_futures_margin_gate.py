"""Futures M3: the margin gate's arithmetic, driven through fakes of the kernel parts.

Real Nautilus enums and contract-spec terms; the cache, account and orders are stand-ins
so each rule (list credit, venue-wide pending margin, in-flight states, modifies) can be
set up exactly. The engine-level behaviour is in
``tests/integration/core/test_futures_margin_engine.py``.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import OrderSide, OrderStatus
from nautilus_trader.model.identifiers import InstrumentId, PositionId

from src.core.futures_costs import FuturesTerms
from src.core.futures_margin_gate import DENIAL_REASON, FuturesMarginGate

pytestmark = pytest.mark.component

ES = InstrumentId.from_str("ES.CME")
NQ = InstrumentId.from_str("NQ.CME")
TERMS = {
    ES: FuturesTerms(Decimal(50), Decimal(27500), Decimal(25000), Decimal("2.25")),
    NQ: FuturesTerms(Decimal(20), Decimal(39600), Decimal(36000), Decimal("2.25")),
}
POSITION_ID = PositionId("ES.CME-1")


def _qty(value) -> SimpleNamespace:
    return SimpleNamespace(as_decimal=lambda: Decimal(value))


def _order(instrument=ES, side=OrderSide.BUY, qty=1, status=OrderStatus.INITIALIZED, **kw):
    return SimpleNamespace(
        instrument_id=instrument,
        side=side,
        quantity=_qty(qty),
        leaves_qty=_qty(qty),
        status=status,
        is_reduce_only=kw.get("reduce_only", False),
        parent_order_id=kw.get("parent"),
        client_order_id=kw.get("client_order_id"),
    )


class _Cache:
    def __init__(self, free, inflight=(), position=None):
        self._free = free
        self._inflight = list(inflight)
        self._position = position

    def account_for_venue(self, venue):
        return SimpleNamespace(
            balance_free=lambda currency: SimpleNamespace(as_decimal=lambda: Decimal(self._free))
        )

    def instrument(self, instrument_id):
        return SimpleNamespace(quote_currency=USD)

    def orders_inflight(self, venue=None):
        return [o for o in self._inflight if venue is None or o.instrument_id.venue == venue]

    def position(self, position_id):
        return self._position


def _gate(cache) -> FuturesMarginGate:
    return FuturesMarginGate(risk_engine=None, cache=cache, msgbus=None, terms=TERMS)


def _long_es(qty=1):
    return SimpleNamespace(instrument_id=ES, is_open=True, is_long=True, quantity=_qty(qty))


class TestSubmit:
    def test_an_entry_over_free_margin_is_denied(self):
        reason = _gate(_Cache(free=50_000)).denial_reason([_order(qty=2)], None)
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "55000" in reason

    def test_an_entry_within_free_margin_passes(self):
        assert _gate(_Cache(free=50_000)).denial_reason([_order(qty=1)], None) is None

    def test_reduce_only_never_needs_margin(self):
        order = _order(side=OrderSide.SELL, qty=5, reduce_only=True)
        assert _gate(_Cache(free=0)).denial_reason([order], None) is None

    def test_closing_the_targeted_position_needs_no_margin(self):
        gate = _gate(_Cache(free=0, position=_long_es(2)))
        assert gate.denial_reason([_order(side=OrderSide.SELL, qty=2)], POSITION_ID) is None

    def test_reversing_past_the_targeted_position_is_margined_on_the_excess(self):
        gate = _gate(_Cache(free=27_499, position=_long_es(1)))
        reason = gate.denial_reason([_order(side=OrderSide.SELL, qty=2)], POSITION_ID)
        assert reason is not None and "27500" in reason

    def test_a_list_spends_the_position_credit_once(self):
        """Two 1-lot sells against a 1-lot long: the second opens a short and needs margin."""
        gate = _gate(_Cache(free=25_000, position=_long_es(1)))
        sells = [_order(side=OrderSide.SELL, qty=1), _order(side=OrderSide.SELL, qty=1)]
        reason = gate.denial_reason(sells, POSITION_ID)
        assert reason is not None and "27500" in reason

    def test_bracket_children_are_not_counted(self):
        entry = _order(qty=1)
        child = _order(side=OrderSide.SELL, qty=1, parent="O-1")
        assert _gate(_Cache(free=27_500)).denial_reason([entry, child], None) is None


class TestPendingMargin:
    def test_submitted_orders_on_other_instruments_of_the_venue_are_reserved(self):
        """An NQ order in flight on CME holds 39,600 the ES check must not spend again."""
        pending_nq = _order(instrument=NQ, status=OrderStatus.SUBMITTED)
        gate = _gate(_Cache(free=39_600, inflight=[pending_nq]))
        assert gate.denial_reason([_order(qty=1)], None) is not None

    def test_pending_cancel_and_update_are_already_reserved(self):
        """Those states are in flight but their margin is already locked in balance_free."""
        cancelling = _order(status=OrderStatus.PENDING_CANCEL)
        updating = _order(status=OrderStatus.PENDING_UPDATE)
        gate = _gate(_Cache(free=32_500, inflight=[cancelling, updating]))
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_other_venues_are_separate(self):
        pending_cl = _order(
            instrument=InstrumentId.from_str("CL.NYMEX"), status=OrderStatus.SUBMITTED
        )
        assert (
            _gate(_Cache(free=27_500, inflight=[pending_cl])).denial_reason([_order()], None)
            is None
        )


class TestModify:
    def test_raising_a_working_order_past_free_margin_is_rejected(self):
        working = _order(qty=1, status=OrderStatus.ACCEPTED)
        reason = _gate(_Cache(free=22_500)).modify_denial_reason(working, _qty(3))
        assert reason is not None and "55000" in reason

    def test_raising_within_free_margin_passes(self):
        working = _order(qty=1, status=OrderStatus.ACCEPTED)
        assert _gate(_Cache(free=27_500)).modify_denial_reason(working, _qty(2)) is None

    @pytest.mark.parametrize("new_qty", [None, 1, 0.5])
    def test_price_only_or_reducing_modifies_pass(self, new_qty):
        working = _order(qty=1, status=OrderStatus.ACCEPTED)
        quantity = None if new_qty is None else _qty(str(new_qty))
        assert _gate(_Cache(free=0)).modify_denial_reason(working, quantity) is None

    def test_reduce_only_and_child_orders_are_never_rejected(self):
        stop = _order(side=OrderSide.SELL, qty=1, parent="O-1")
        closer = _order(side=OrderSide.SELL, qty=1, reduce_only=True)
        gate = _gate(_Cache(free=0))
        assert gate.modify_denial_reason(stop, _qty(5)) is None
        assert gate.modify_denial_reason(closer, _qty(5)) is None
