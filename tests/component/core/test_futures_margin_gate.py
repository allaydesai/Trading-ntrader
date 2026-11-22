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
from nautilus_trader.model.identifiers import ClientOrderId, InstrumentId, PositionId

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
        client_order_id=kw.get("client_order_id", ClientOrderId(f"O-{next(_IDS)}")),
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


def _gate(cache, risk=None) -> FuturesMarginGate:
    return FuturesMarginGate(risk_engine=risk, cache=cache, msgbus=None, terms=TERMS)


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
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderModifyRejected
    from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, TraderId

    ids = (TraderId("T-001"), StrategyId("S-001"), ES, ClientOrderId("O-1"))
    return OrderModifyRejected(*ids, None, None, "rejected", UUID4(), 0, 0)


def _updated(qty):
    """A real ``OrderUpdated`` carrying ``qty`` (unchanged qty = a price-only update)."""
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderUpdated
    from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, TraderId
    from nautilus_trader.model.objects import Quantity

    ids = (TraderId("T-001"), StrategyId("S-001"), ES, ClientOrderId("O-1"))
    return OrderUpdated(*ids, None, None, Quantity.from_int(qty), None, None, UUID4(), 0, 0)


class _Risk:
    """Records the gate's rejections and, like Nautilus, adds the event to the order."""

    def __init__(self):
        self.rejected: list[str] = []

    def _reject_modify_order(self, order, reason):
        self.rejected.append(reason)
        order.events.append(_rejected())


class TestModifyHolds:
    """Approved quantity raises stay reserved until their own modify is resolved."""

    def _setup(self, free=60_000):
        working = _order(qty=1, status=OrderStatus.ACCEPTED)
        risk = _Risk()
        gate = _gate(_Cache(free=free, orders=[working]), risk)
        return gate, risk, working

    @staticmethod
    def _modify(gate, working, qty, forward=lambda: None):
        gate.handle_modify(working, None if qty is None else _qty(qty), forward)

    @staticmethod
    def _apply(working, qty):
        working.quantity = _qty(qty)
        working.events.append(_updated(qty))

    def test_an_approved_increase_is_held_until_the_exchange_applies_it(self):
        gate, risk, working = self._setup()
        self._modify(gate, working, 2)  # +1 lot held (27,500)
        assert risk.rejected == []
        assert gate.denial_reason([_order(qty=1)], None) is None  # 32,500 free
        assert gate.denial_reason([_order(qty=2)], None) is not None

    def test_the_hold_ends_when_the_update_reaches_its_target(self):
        gate, _, working = self._setup()
        self._modify(gate, working, 2)
        self._apply(working, 2)  # balance_free now reserves the 2 lots itself
        assert gate.denial_reason([_order(qty=2)], None) is None

    def test_the_hold_ends_on_its_own_rejection(self):
        gate, _, working = self._setup()
        self._modify(gate, working, 2)
        working.events.append(_rejected())
        assert gate.denial_reason([_order(qty=2)], None) is None

    def test_a_price_only_update_keeps_the_hold(self):
        """An OrderUpdated that leaves quantity unchanged (e.g. a trailing-stop move)."""
        gate, _, working = self._setup()
        self._modify(gate, working, 2)
        working.events.append(_updated(1))
        assert gate.denial_reason([_order(qty=2)], None) is not None

    def test_a_smaller_second_raise_keeps_the_larger_hold(self):
        """1 -> 3 approved, then 1 -> 2 approved before either resolves: hold stays +2 lots."""
        gate, risk, working = self._setup()
        self._modify(gate, working, 3)  # +2 lots = 55,000
        self._modify(gate, working, 2)  # adds nothing beyond the pending 3
        assert risk.rejected == []
        # 60,000 - 55,000 = 5,000 free: a 1-lot entry (27,500) is still denied.
        assert gate.denial_reason([_order(qty=1)], None) is not None
        self._apply(working, 3)  # the 3 lands; the 2 is still pending but below quantity
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_a_later_larger_raise_is_checked_on_its_own_increase(self):
        gate, risk, working = self._setup()
        self._modify(gate, working, 2)  # +1 held
        self._modify(gate, working, 3)  # 1 -> 3 needs 55,000 of 60,000 (own hold excluded)
        assert risk.rejected == []
        assert gate.denial_reason([_order(qty=1)], None) is not None  # 5,000 free

    def test_an_earlier_modifys_rejection_does_not_release_a_later_hold(self):
        gate, _, working = self._setup()
        self._modify(gate, working, None)  # price-only, sent first
        self._modify(gate, working, 2)  # raise, sent second
        working.events.append(_rejected())  # the exchange resolves the price-only one
        assert gate.denial_reason([_order(qty=2)], None) is not None

    def test_a_denied_later_modify_keeps_the_earlier_hold(self):
        gate, risk, working = self._setup()
        self._modify(gate, working, 2)  # +1 held
        self._modify(gate, working, 4)  # +3 = 82,500 > 60,000: the gate rejects it
        assert len(risk.rejected) == 1
        # The gate's own rejection event must not resolve the pending +1.
        assert gate.denial_reason([_order(qty=2)], None) is not None
        assert gate.denial_reason([_order(qty=1)], None) is None

    def test_a_rejection_raised_while_forwarding_belongs_to_that_modify(self):
        """The risk engine may reject at once (bad price): that is this modify, not the head."""
        gate, _, working = self._setup()
        self._modify(gate, working, 2, forward=lambda: working.events.append(_rejected()))
        assert gate.denial_reason([_order(qty=2)], None) is None

    def test_the_hold_ends_when_the_order_closes(self):
        gate, _, working = self._setup()
        self._modify(gate, working, 2)
        working.is_closed = True
        assert gate.denial_reason([_order(qty=2)], None) is None
