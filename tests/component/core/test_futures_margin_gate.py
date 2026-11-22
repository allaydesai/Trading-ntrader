"""Futures M3: the margin gate's arithmetic, driven through fakes of the kernel parts.

Real Nautilus enums and contract-spec terms; the cache, account and orders are stand-ins
so each rule (list credit, venue-wide pending margin, in-flight states, no size-raising
modifies) can be
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
from src.core.futures_margin_gate import DENIAL_REASON, QTY_INCREASE_REASON, FuturesMarginGate

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


class _Risk:
    """Records what the gate forwards and what it rejects through the risk engine."""

    def __init__(self, on_execute=None):
        self.forwarded: list = []
        self.rejected: list[str] = []
        self._on_execute = on_execute

    def execute(self, command):
        self.forwarded.append(command)
        if self._on_execute is not None:
            self._on_execute()

    def _reject_modify_order(self, order, reason):
        self.rejected.append(reason)


def _modify_command(order, qty):
    """A real ``ModifyOrder`` for ``order``; ``qty=None`` is a price-only modify."""
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.execution.messages import ModifyOrder
    from nautilus_trader.model.identifiers import StrategyId, TraderId
    from nautilus_trader.model.objects import Price, Quantity

    quantity = None if qty is None else Quantity.from_str(str(qty))
    price = Price.from_str("5000.00") if qty is None else None
    ids = (TraderId("T-001"), StrategyId("S-001"), order.instrument_id, order.client_order_id)
    return ModifyOrder(*ids, None, quantity, price, None, UUID4(), 0)


def _working(qty=1, **kw):
    return _order(qty=qty, status=OrderStatus.ACCEPTED, **kw)


class TestModify:
    """A futures modify may lower or re-price an order, never raise its size."""

    @pytest.mark.parametrize("free", [0, 10_000_000])
    def test_a_size_increase_is_rejected_whatever_the_margin(self, free):
        reason = _gate(_Cache(free=free)).modify_denial_reason(_working(1), _qty(2))
        assert reason is not None and reason.startswith(QTY_INCREASE_REASON)
        assert "cancel and resubmit" in reason

    @pytest.mark.parametrize("new_qty", [None, "1", "0.5"])
    def test_price_only_or_reducing_modifies_pass(self, new_qty):
        quantity = None if new_qty is None else _qty(new_qty)
        assert _gate(_Cache(free=0)).modify_denial_reason(_working(1), quantity) is None

    def test_reduce_only_and_child_increases_are_rejected_too(self):
        closer = _working(side=OrderSide.SELL, reduce_only=True)
        child = _working(side=OrderSide.SELL, parent="O-1")
        gate = _gate(_Cache(free=10_000_000))
        assert gate.modify_denial_reason(closer, _qty(5)).startswith(QTY_INCREASE_REASON)
        assert gate.modify_denial_reason(child, _qty(5)).startswith(QTY_INCREASE_REASON)

    def test_a_non_futures_order_is_not_the_gates_business(self):
        equity = _working(instrument=InstrumentId.from_str("AAPL.NASDAQ"))
        assert _gate(_Cache(free=0)).modify_denial_reason(equity, _qty(500)) is None


class TestModifyRouting:
    """Through ``execute``: an increase is rejected and never forwarded; the rest is."""

    def _run(self, qty):
        working = _working(2)
        risk = _Risk()
        gate = _gate(_Cache(free=0, orders=[working]), risk)
        command = _modify_command(working, qty)
        gate.execute(command)
        return gate, risk, command

    def test_an_increase_is_rejected_not_forwarded(self):
        gate, risk, _ = self._run(3)
        assert risk.forwarded == []
        assert len(risk.rejected) == 1 and risk.rejected[0].startswith(QTY_INCREASE_REASON)
        assert gate.denied_count == 1

    @pytest.mark.parametrize("qty", [1, 2, None])
    def test_a_decrease_same_size_or_price_only_modify_is_forwarded_unchanged(self, qty):
        gate, risk, command = self._run(qty)
        assert risk.forwarded == [command]
        assert risk.rejected == [] and gate.denied_count == 0


class TestNoPendingModifyState:
    """With no hold bookkeeping, the round-3 failure modes cannot occur."""

    def test_the_gate_keeps_no_per_order_state(self):
        gate = _gate(_Cache(free=0))
        assert set(vars(gate)) == {"_risk", "_cache", "_msgbus", "_terms", "denied_count"}

    def test_pending_reductions_release_nothing_early(self):
        """3 working lots, modifies to 1 then 2 forwarded: new entries still see only what
        Nautilus has actually released (here nothing yet), never the pending targets."""
        working = _working(3)
        risk = _Risk()
        gate = _gate(_Cache(free=0, orders=[working]), risk)
        gate.execute(_modify_command(working, 1))
        gate.execute(_modify_command(working, 2))
        assert len(risk.forwarded) == 2
        assert gate.denial_reason([_order(qty=1)], None) is not None  # free is still 0

    def test_a_reentrant_submit_during_forwarding_cannot_double_resolve(self):
        """The old IndexError: a strategy submitting from on_order_modify_rejected while the
        modify is being forwarded. With no ledger there is nothing to pop twice."""
        working = _working(2)
        cache = _Cache(free=27_500, orders=[working])
        seen: list = []
        risk = _Risk(on_execute=lambda: seen.append(gate.denial_reason([_order()], None)))
        gate = _gate(cache, risk)
        gate.execute(_modify_command(working, 1))
        assert seen == [None]  # the reentrant check ran and found the margin
        assert len(risk.forwarded) == 1

    def test_an_increase_never_reaches_the_risk_engine_to_be_rejected_there(self):
        working = _working(1)
        risk = _Risk(on_execute=lambda: pytest.fail("an increase must not be forwarded"))
        _gate(_Cache(free=10_000_000, orders=[working]), risk).execute(_modify_command(working, 4))
        assert len(risk.rejected) == 1
