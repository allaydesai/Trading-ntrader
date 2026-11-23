"""FX M4: the margin gate's FX requirement, driven through stand-ins of the kernel parts.

Real ``CurrencyPair``s, ``ModifyOrder`` commands and order events; orders, the account and the
risk engine are stand-ins so each case is exact. Engine-level behaviour:
``tests/integration/core/test_fx_margin_engine.py``.

Margin arithmetic used throughout: EURUSD at 3%, 100,000 units at 1.10000 hold 3,300 USD and
each 0.01 of price on them is 30 USD (1.10 -> 1.12 adds 60).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import ModifyOrder
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    ContingencyType,
    OrderSide,
    OrderStatus,
    OrderType,
    TriggerType,
)
from nautilus_trader.model.events import OrderModifyRejected, OrderUpdated
from nautilus_trader.model.identifiers import (
    ClientOrderId,
    ExecAlgorithmId,
    StrategyId,
    TraderId,
)
from nautilus_trader.model.objects import Price, Quantity

from src.core.margin_gate import DENIAL_REASON, FxRequirement, MarginGate
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

EURUSD = build_fx_pair(get_pair_spec("EURUSD"))
EURGBP = build_fx_pair(get_pair_spec("EURGBP"))
_IDS = iter(range(1, 10_000))
PENDING = "FX_MODIFY_PENDING"
UNPRICEABLE = "FX_MARGIN_UNPRICEABLE"


def _order(instrument, qty: int, price: str | None = None, leaves: int | None = None, **kw):
    return SimpleNamespace(
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        quantity=Quantity.from_int(qty),
        leaves_qty=Quantity.from_int(qty if leaves is None else leaves),
        price=Price.from_str(price) if price else None,
        trigger_price=Price.from_str(kw["trigger"]) if kw.get("trigger") else None,
        status=kw.get("status", OrderStatus.INITIALIZED),
        events=list(kw.get("events", [])),
        order_type=kw.get("order_type", OrderType.LIMIT),
        contingency_type=kw.get("contingency_type", ContingencyType.NO_CONTINGENCY),
        emulation_trigger=kw.get("emulation_trigger", TriggerType.NO_TRIGGER),
        exec_algorithm_id=kw.get("exec_algorithm_id"),
        is_active_local=False,
        is_reduce_only=kw.get("reduce_only", False),
        is_closed=False,
        parent_order_id=kw.get("parent"),
        client_order_id=ClientOrderId(f"O-{next(_IDS)}"),
    )


def _working(qty: int = 100_000, price: str | None = "1.10000", **kw):
    return _order(EURUSD, qty, price, status=OrderStatus.ACCEPTED, **kw)


class _Cache:
    """No bars and no GBP rate yet: only limit orders can be priced (and only USD quotes)."""

    def __init__(self, free: str, inflight=(), open_orders=()) -> None:
        self.free = Decimal(free)
        self._inflight = list(inflight)
        self._orders = {o.client_order_id: o for o in [*inflight, *open_orders]}

    def add(self, *orders):
        self._orders.update({o.client_order_id: o for o in orders})

    def order(self, client_order_id):
        return self._orders.get(client_order_id)

    def instrument(self, instrument_id):
        return {EURUSD.id: EURUSD, EURGBP.id: EURGBP}[instrument_id]

    def price(self, instrument_id, price_type):
        return None

    def get_xrate(self, *args):
        return None

    def account_for_venue(self, venue):
        return SimpleNamespace(
            base_currency=USD,
            balance_free=lambda currency: SimpleNamespace(as_decimal=lambda: self.free),
        )

    def orders_inflight(self, venue=None):
        return self._inflight

    def position(self, position_id):
        return None


class _Risk:
    """Records forwarded commands; a rejection lands on the order as Nautilus' does."""

    def __init__(self, answer=None) -> None:
        self.forwarded: list = []
        self.rejected: list[str] = []
        self.answer = answer  # called with each forwarded command (a sync risk rejection)

    def execute(self, command):
        self.forwarded.append(command)
        if self.answer is not None:
            self.answer(command)

    def _reject_modify_order(self, order, reason):
        self.rejected.append(reason)
        order.events.append(_rejected(order))


def _gate(free: str = "10000", inflight=(), open_orders=(), risk=None) -> MarginGate:
    terms = {i.id: FxRequirement(Decimal("0.03")) for i in (EURUSD, EURGBP)}
    cache = _Cache(free, inflight, open_orders)
    return MarginGate(risk_engine=risk or _Risk(), cache=cache, msgbus=None, terms=terms)


def _ids(order):
    return (TraderId("T-001"), StrategyId("S-001"), order.instrument_id, order.client_order_id)


def _modify(gate, order, price=None, trigger=None, qty=None):
    """Send a real ``ModifyOrder`` for ``order`` through the gate."""
    gate._cache.add(order)
    command = ModifyOrder(
        *_ids(order),
        None,
        None if qty is None else Quantity.from_int(qty),
        None if price is None else Price.from_str(price),
        None if trigger is None else Price.from_str(trigger),
        UUID4(),
        0,
    )
    gate.execute(command)


def _rejected(order):
    return OrderModifyRejected(*_ids(order), None, None, "rejected", UUID4(), 0, 0)


def _apply(order, price=None, trigger=None, qty=None):
    """An ``OrderUpdated`` applied as Nautilus does: event appended, order fields set."""
    if price is not None:
        order.price = Price.from_str(price)
    if trigger is not None:
        order.trigger_price = Price.from_str(trigger)
    if qty is not None:
        order.quantity = Quantity.from_int(qty)
    order.events.append(
        OrderUpdated(
            *_ids(order),
            None,
            None,
            order.quantity,
            order.price,
            order.trigger_price,
            UUID4(),
            0,
            0,
        )
    )


def _entry_reason(gate, qty: int):
    return gate.denial_reason([_order(EURUSD, qty, "1.10000")], None)


def _reprice_reason(gate, order, price):
    return gate.modify_denial_reason(order, None, Price.from_str(price))


class TestPricing:
    def test_an_unpriceable_sibling_does_not_waive_a_priced_over_margin_entry(self):
        # 400,000 EURUSD limit at 1.10000 needs 13,200; the market order has no price yet.
        orders = [_order(EURUSD, 400_000, "1.10000"), _order(EURUSD, 1_000)]
        reason = _gate().denial_reason(orders, None)
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "13200.00 USD" in reason

    def test_an_unpriceable_market_order_is_passed_on(self):
        # It fills at once: with no rate M3's guard fails the run; with no market the venue
        # rejects it. Either way it never fills unchecked later.
        assert _gate().denial_reason([_order(EURUSD, 400_000)], None) is None

    @pytest.mark.parametrize("kind", ["limit", "stop"])
    def test_a_resting_entry_without_a_usd_rate_is_refused(self, kind):
        # EURGBP before its first GBPUSD bar: it could fill later, unmargined.
        kw = {"trigger": "0.85000"} if kind == "stop" else {}
        order = _order(EURGBP, 400_000, "0.85000" if kind == "limit" else None, **kw)
        reason = _gate().denial_reason([order], None)
        assert reason is not None and reason.startswith(UNPRICEABLE)
        assert "EUR/GBP.IDEALPRO" in reason and "400000 unit(s)" in reason

    def test_an_unpriceable_resting_sibling_refuses_the_whole_list(self):
        orders = [_order(EURUSD, 1_000, "1.10000"), _order(EURGBP, 1_000, "0.85000")]
        reason = _gate().denial_reason(orders, None)
        assert reason is not None and reason.startswith(UNPRICEABLE)

    def test_an_unpriceable_resting_exit_is_not_margined(self):
        closer = _order(EURGBP, 400_000, "0.85000", reduce_only=True)
        assert _gate().denial_reason([closer], None) is None

    def test_a_priced_entry_within_margin_passes(self):
        assert _gate().denial_reason([_order(EURUSD, 300_000, "1.10000")], None) is None


class TestReprice:
    def test_a_partly_filled_order_is_margined_on_its_full_quantity_as_nautilus_does(self):
        # 300,000 at 1.10 with 200,000 filled: Nautilus 1.220 margins order.quantity, so
        # 1.10 -> 1.12 adds 300,000 x 0.02 x 3% = 180 USD (not 60 on the 100,000 left).
        order = _order(EURUSD, 300_000, "1.10000", leaves=100_000, status=OrderStatus.ACCEPTED)
        reason = _gate(free="100").modify_denial_reason(order, None, Price.from_str("1.12000"))
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "by 180.00 USD" in reason

    def test_a_margin_raising_reprice_within_free_passes(self):
        assert _reprice_reason(_gate(free="100"), _working(), "1.12000") is None

    def test_a_lowering_reprice_passes_whatever_is_free(self):
        assert _reprice_reason(_gate(free="0"), _working(), "1.05000") is None

    def test_a_decrease_that_also_reprices_is_checked_on_the_new_size(self):
        # 100,000 at 1.10 (3,300) -> 99,000 at 1.20 (3,564): +264 against 100 free.
        order = _working()
        reason = _gate(free="100").modify_denial_reason(
            order, Quantity.from_int(99_000), Price.from_str("1.20000")
        )
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "by 264.00 USD" in reason


class TestPendingIncreaseIsHeldBack:
    """A passed reprice's increase is unreserved until the venue applies it; the gate holds it.

    CodeRabbit, PR #63: two reprices or a reprice and a new entry must not both pass against
    the same free balance, and an applied or answered modify must stop counting.
    """

    def test_new_entries_see_free_less_the_pending_increase(self):
        gate, a = _gate(free="100"), _working()
        _modify(gate, a, price="1.12000")  # +60 passed on
        assert _entry_reason(gate, 1_000) is None  # 33 <= 40
        reason = _entry_reason(gate, 2_000)  # 66 > 40
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "free=40.00 USD" in reason

    def test_another_orders_reprice_cannot_spend_the_same_free(self):
        gate, a, b = _gate(free="100"), _working(), _working()
        _modify(gate, a, price="1.12000")
        _modify(gate, b, price="1.12000")
        assert len(gate._risk.forwarded) == 1
        assert gate._risk.rejected[0].startswith(DENIAL_REASON)
        assert "free=40.00 USD" in gate._risk.rejected[0]

    def test_an_orders_next_reprice_replaces_its_pending_one(self):
        # Only one requested state is live at a time: 1.12 pending, 1.13 (+90) fits the 100,
        # 1.14 (+120) does not.
        gate, a = _gate(free="100"), _working()
        _modify(gate, a, price="1.12000")
        _modify(gate, a, price="1.13000")
        _modify(gate, a, price="1.14000")
        assert len(gate._risk.forwarded) == 2
        assert len(gate._risk.rejected) == 1 and "by 120.00 USD" in gate._risk.rejected[0]
        assert _entry_reason(gate, 1_000) is not None  # 33 > 100 - 90

    def test_once_applied_the_increase_counts_once_in_the_account(self):
        gate, a = _gate(free="100"), _working()
        _modify(gate, a, price="1.12000")
        _apply(a, price="1.12000")
        gate._cache.free = Decimal("40")  # Nautilus now reserves the 60
        assert _entry_reason(gate, 1_000) is None  # 33 <= 40, not 40 - 60
        assert len(gate._pending) == 0
        # a later reprice is checked against the 40 left (sequential, CodeRabbit)
        assert _reprice_reason(gate, _working(), "1.12000") is not None

    @pytest.mark.parametrize("release", ["rejected", "cancelled", "filled", "expired"])
    def test_a_rejection_or_the_order_closing_releases_it(self, release):
        gate, a = _gate(free="100"), _working()
        _modify(gate, a, price="1.12000")
        if release == "rejected":
            a.events.append(_rejected(a))
        else:
            a.is_closed = True
        assert _entry_reason(gate, 3_000) is None  # 99 <= 100
        assert len(gate._pending) == 0


#: Order kinds Nautilus 1.220 sends ``OrderUpdated`` to without a modify asking: trailing
#: stops (matching engine and emulator trail them), MARKET_TO_LIMIT (first fill reprices it),
#: contingent OUO siblings (quantity synced on a linked fill), emulated and exec-algorithm
#: orders (local updates). All parentless, so each is a margined entry.
UNSOLICITED_KINDS = {
    "trailing stop limit": {"order_type": OrderType.TRAILING_STOP_LIMIT},
    "market to limit": {"order_type": OrderType.MARKET_TO_LIMIT},
    "contingent OUO": {"contingency_type": ContingencyType.OUO},
    "emulated": {"emulation_trigger": TriggerType.BID_ASK},
    "exec algorithm": {"exec_algorithm_id": ExecAlgorithmId("TWAP")},
}


class TestUnsolicitedUpdateKinds:
    """On kinds Nautilus also updates unsolicited, only the matching update answers a modify.

    The old counter could never clear on these, so one applied modify on a parentless trailing
    entry locked every new FX entry on the venue until that order closed (CodeRabbit, PR #63).
    """

    @pytest.mark.parametrize("kind", list(UNSOLICITED_KINDS))
    def test_an_applied_modify_frees_new_entries_and_the_orders_next_resize(self, kind):
        gate, a = _gate(free="100"), _working(**UNSOLICITED_KINDS[kind])
        _modify(gate, a, price="1.12000")
        _apply(a, price="1.12000")
        gate._cache.free = Decimal("40")
        assert _entry_reason(gate, 1_000) is None
        _modify(gate, a, qty=50_000)
        assert gate._risk.rejected == [] and len(gate._risk.forwarded) == 2

    @pytest.mark.parametrize("kind", list(UNSOLICITED_KINDS))
    def test_an_update_that_does_not_carry_the_request_leaves_it_pending(self, kind):
        gate, a = _gate(free="100"), _working(**UNSOLICITED_KINDS[kind])
        _modify(gate, a, price="1.12000")
        _apply(a, qty=90_000)  # e.g. an OUO sync: not the answer
        b = _working()
        _modify(gate, b, price="1.12000")  # A's 90,000 x 0.02 x 3% = 54 still held: 46 left
        assert gate._risk.rejected and "free=46.00 USD" in gate._risk.rejected[0]
        _modify(gate, a, qty=80_000)
        assert gate._risk.rejected[-1].startswith(PENDING)

    def test_a_trailing_stop_entry_trailing_mid_modify_keeps_the_increase_held(self):
        # Trigger 1.10 -> 1.12 requested; it trails to 1.09 first, so the pending increase is
        # now 1.09 -> 1.12 = 90 against what Nautilus reserves; the matching update clears it.
        gate = _gate(free="100")
        a = _working(price=None, trigger="1.10000", order_type=OrderType.TRAILING_STOP_MARKET)
        _modify(gate, a, trigger="1.12000")
        _apply(a, trigger="1.09000")
        gate._cache.free = Decimal("130")  # 30 released by the trail
        assert "free=40.00 USD" in _entry_reason(gate, 2_000)
        _apply(a, trigger="1.12000")
        gate._cache.free = Decimal("40")
        assert _entry_reason(gate, 1_000) is None and len(gate._pending) == 0

    def test_a_rejection_answers_a_modify_on_any_kind(self):
        gate = _gate(free="100")
        a = _working(order_type=OrderType.TRAILING_STOP_LIMIT)
        _modify(gate, a, price="1.12000")
        a.events.append(_rejected(a))
        assert _reprice_reason(gate, _working(), "1.13000") is None  # +90 <= 100


class TestExitsHoldNothing:
    """Reduce-only and contingent child orders are not margined as entries (``denial_reason``),
    so a reprice of them holds nothing back, and their modifies still settle."""

    @pytest.mark.parametrize("kw", [{"reduce_only": True}, {"parent": ClientOrderId("P-1")}])
    def test_a_pending_exit_reprice_holds_no_margin(self, kw):
        gate = _gate(free="100")
        exit_order = _working(**kw, contingency_type=ContingencyType.OTO)
        _modify(gate, exit_order, price="1.20000")
        assert gate._risk.forwarded and gate._risk.rejected == []
        assert _entry_reason(gate, 3_000) is None  # 99 <= 100

    @pytest.mark.parametrize("kw", [{"reduce_only": True}, {"parent": ClientOrderId("P-1")}])
    def test_an_exits_applied_modify_frees_its_next_resize(self, kw):
        gate = _gate(free="0")
        exit_order = _working(**kw, contingency_type=ContingencyType.OUO)
        _modify(gate, exit_order, price="1.20000")
        _modify(gate, exit_order, qty=50_000)
        assert gate._risk.rejected[-1].startswith(PENDING)
        _apply(exit_order, price="1.20000")
        _modify(gate, exit_order, qty=50_000)
        assert len(gate._risk.forwarded) == 2


class TestOnlyRealAnswersSettle:
    def test_the_gates_own_rejection_does_not_settle_the_earlier_modify(self):
        gate, a = _gate(free="100"), _working()
        _modify(gate, a, price="1.12000")  # passed, +60 pending
        _modify(gate, a, price="1.20000")  # +300: the gate rejects it (event on the order)
        assert len(gate._risk.rejected) == 1
        assert "free=40.00 USD" in _entry_reason(gate, 2_000)  # the +60 still held

    def test_a_risk_engine_rejection_of_this_modify_is_not_recorded_or_counted(self):
        risk = _Risk()
        gate, a = _gate(free="100", risk=risk), _working()
        _modify(gate, a, price="1.12000")  # passed, pending
        risk.answer = lambda command: a.events.append(_rejected(a))
        _modify(gate, a, price="1.11000")  # the risk engine refuses it there and then
        assert "free=40.00 USD" in _entry_reason(gate, 2_000)  # 1.12 still pending

    def test_a_reentrant_check_during_that_rejection_still_sees_the_earlier_modify(self):
        risk = _Risk()
        gate, a = _gate(free="100", risk=risk), _working()
        _modify(gate, a, price="1.12000")
        seen: list = []

        def reject_then_reenter(command):
            a.events.append(_rejected(a))
            seen.append(_entry_reason(gate, 2_000))  # a strategy submitting from the callback

        risk.answer = reject_then_reenter
        _modify(gate, a, price="1.11000")
        assert seen[0] is not None and "free=40.00 USD" in seen[0]
        assert "free=40.00 USD" in _entry_reason(gate, 2_000)

    def test_a_second_quantity_modify_waits_for_the_first_then_may_go(self):
        gate, a = _gate(), _working()
        _modify(gate, a, qty=60_000)
        _modify(gate, a, qty=50_000)
        assert gate._risk.rejected[-1].startswith(PENDING)
        _apply(a, qty=60_000)
        _modify(gate, a, qty=50_000)
        assert len(gate._risk.forwarded) == 2
