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
CL = InstrumentId.from_str("CL.NYMEX")
TERMS = {
    ES: FuturesTerms(Decimal(50), Decimal(27500), Decimal(25000), Decimal("2.25")),
    NQ: FuturesTerms(Decimal(20), Decimal(39600), Decimal(36000), Decimal("2.25")),
    CL: FuturesTerms(Decimal(1000), Decimal(6600), Decimal(6000), Decimal("2.37")),
}
POSITION_ID = PositionId("ES.CME-1")


def _qty(value) -> SimpleNamespace:
    return SimpleNamespace(as_decimal=lambda: Decimal(value))


_IDS = iter(range(1, 10_000))


def _order(instrument=ES, side=OrderSide.BUY, qty=1, status=OrderStatus.INITIALIZED, **kw):
    open_states = {OrderStatus.ACCEPTED, OrderStatus.PENDING_UPDATE, OrderStatus.PENDING_CANCEL}
    return SimpleNamespace(
        instrument_id=instrument,
        side=side,
        quantity=_qty(qty),
        leaves_qty=_qty(qty),
        status=status,
        is_reduce_only=kw.get("reduce_only", False),
        parent_order_id=kw.get("parent"),
        client_order_id=kw.get("client_order_id", f"O-{next(_IDS)}"),
        is_open=status in open_states,
        is_inflight=status in {OrderStatus.SUBMITTED, OrderStatus.PENDING_UPDATE},
        is_closed=status in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED},
        events=list(kw.get("events", [])),
    )


class _Cache:
    def __init__(self, free, inflight=(), position=None, for_position=(), orders=()):
        self._free = free
        self._inflight = list(inflight)
        self._position = position
        self._for_position = list(for_position)
        self._orders = {o.client_order_id: o for o in orders}

    def orders_for_position(self, position_id):
        return self._for_position

    def order(self, client_order_id):
        return self._orders.get(client_order_id)

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
    return SimpleNamespace(
        id=POSITION_ID, instrument_id=ES, is_open=True, is_long=True, quantity=_qty(qty)
    )


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

    def test_separate_submits_share_the_position_credit(self):
        """A working 1-lot sell already claims the 1-lot long: a second sell opens a short."""
        working_close = _order(side=OrderSide.SELL, qty=1, status=OrderStatus.ACCEPTED)
        cache = _Cache(free=25_000, position=_long_es(1), for_position=[working_close])
        reason = _gate(cache).denial_reason([_order(side=OrderSide.SELL, qty=1)], POSITION_ID)
        assert reason is not None and "27500" in reason

    def test_filled_or_cancelled_closes_release_their_claim(self):
        done = _order(side=OrderSide.SELL, qty=1, status=OrderStatus.CANCELED)
        cache = _Cache(free=0, position=_long_es(1), for_position=[done])
        assert _gate(cache).denial_reason([_order(side=OrderSide.SELL)], POSITION_ID) is None

    def test_the_order_being_submitted_does_not_claim_against_itself(self):
        """Strategy.submit_order caches the order with its position before the gate runs."""
        sell = _order(side=OrderSide.SELL, qty=1)
        cache = _Cache(free=0, position=_long_es(1), for_position=[sell])
        assert _gate(cache).denial_reason([sell], POSITION_ID) is None

    def test_bracket_children_are_not_counted(self):
        entry = _order(qty=1)
        child = _order(side=OrderSide.SELL, qty=1, parent="O-1")
        assert _gate(_Cache(free=27_500)).denial_reason([entry, child], None) is None


class TestPendingMargin:
    def test_submitted_orders_on_other_instruments_of_the_venue_are_reserved(self):
        """An NQ order in flight on CME holds 39,600 (at NQ's rate, not ES's 27,500).

        60,000 free: ES 27,500 + NQ 39,600 = 67,100 does not fit, but would if the pending
        NQ order were charged at ES's rate (60,000 - 27,500 = 32,500 >= 27,500).
        """
        pending_nq = _order(instrument=NQ, status=OrderStatus.SUBMITTED)
        gate = _gate(_Cache(free=60_000, inflight=[pending_nq]))
        assert gate.denial_reason([_order(qty=1)], None) is not None

    def test_pending_cancel_and_update_are_already_reserved(self):
        """Those states are in flight but their margin is already locked in balance_free."""
        cancelling = _order(status=OrderStatus.PENDING_CANCEL)
        updating = _order(status=OrderStatus.PENDING_UPDATE)
        gate = _gate(_Cache(free=32_500, inflight=[cancelling, updating]))
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_other_venues_are_separate(self):
        """A margined CL order in flight on NYMEX does not spend CME's free balance."""
        pending_cl = _order(instrument=CL, status=OrderStatus.SUBMITTED)
        cache = _Cache(free=27_500, inflight=[pending_cl])
        assert _gate(cache).denial_reason([_order()], None) is None


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

    def test_reduce_only_orders_are_never_rejected(self):
        closer = _order(side=OrderSide.SELL, qty=1, reduce_only=True, status=OrderStatus.ACCEPTED)
        assert _gate(_Cache(free=0)).modify_denial_reason(closer, _qty(5)) is None

    def test_a_non_reduce_only_child_increase_is_checked(self):
        """A child sell grown from 1 to 5 could open a 4-lot short: it needs the margin."""
        child = _order(side=OrderSide.SELL, qty=1, parent="O-1", status=OrderStatus.ACCEPTED)
        reason = _gate(_Cache(free=27_500)).modify_denial_reason(child, _qty(5))
        assert reason is not None and "110000" in reason


def _rejected():
    from nautilus_trader.model.events import OrderModifyRejected

    return OrderModifyRejected.__new__(OrderModifyRejected)


def _updated():
    from nautilus_trader.model.events import OrderUpdated

    return OrderUpdated.__new__(OrderUpdated)


class TestApprovedIncreases:
    def _approve(self, free=60_000, working=None):
        working = working or _order(qty=1, status=OrderStatus.ACCEPTED)
        cache = _Cache(free=free, orders=[working])
        gate = _gate(cache)
        assert gate.modify_denial_reason(working, _qty(2)) is None  # approved: +27,500
        return gate, cache, working

    def test_an_approved_increase_is_held_until_the_exchange_applies_it(self):
        gate, _, _ = self._approve()
        # 60,000 free less the unapplied +1 lot (27,500) leaves 32,500: one more lot fits,
        # two do not.
        assert gate.denial_reason([_order(qty=1)], None) is None
        assert gate.denial_reason([_order(qty=2)], None) is not None

    def test_the_hold_ends_once_applied(self):
        gate, _, working = self._approve()
        working.quantity = _qty(2)  # the exchange applied it; balance_free now reserves it
        working.events.append(_updated())
        assert gate.denial_reason([_order(qty=2)], None) is None

    def test_the_hold_ends_on_its_own_rejection(self):
        gate, _, working = self._approve()
        working.events.append(_rejected())
        assert gate.denial_reason([_order(qty=2)], None) is None

    def test_an_earlier_modifys_rejection_does_not_release_a_later_hold(self):
        """A price-only modify sent first is resolved first; the increase is still pending."""
        working = _order(qty=1, status=OrderStatus.ACCEPTED)
        gate = _gate(_Cache(free=60_000, orders=[working]))
        assert gate.modify_denial_reason(working, None) is None  # price-only, modify #1
        assert gate.modify_denial_reason(working, _qty(2)) is None  # increase, modify #2
        working.events.append(_rejected())  # resolves modify #1 only
        assert gate.denial_reason([_order(qty=2)], None) is not None

    def test_a_denied_later_modify_keeps_the_earlier_hold(self):
        gate, _, working = self._approve()  # +1 lot held (27,500)
        assert gate.modify_denial_reason(working, _qty(4)) is not None  # +3 = 82,500 > 60,000
        # The +1 hold must survive: 60,000 - 27,500 = 32,500 < 2 lots (55,000).
        assert gate.denial_reason([_order(qty=2)], None) is not None
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_an_approved_later_modify_replaces_rather_than_stacks_the_hold(self):
        gate, _, working = self._approve()  # 1 -> 2 held
        assert gate.modify_denial_reason(working, _qty(3)) is None  # 1 -> 3: +2 = 55,000
        # Hold is now 55,000, not 27,500 + 55,000: 5,000 free, so no lot fits.
        assert gate.denial_reason([_order(qty=1)], None) is not None
        working.events.extend([_updated(), _updated()])  # both resolved
        working.quantity = _qty(3)
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_the_hold_ends_when_the_order_closes(self):
        gate, _, working = self._approve()
        working.is_closed = True
        assert gate.denial_reason([_order(qty=2)], None) is None
