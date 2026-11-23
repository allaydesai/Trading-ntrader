"""FX M4: the margin gate's FX requirement, driven through stand-ins of the kernel parts.

Real ``CurrencyPair``s and Nautilus ``Cache`` prices; orders and the account are stand-ins so
each case is exact. Engine-level behaviour: ``tests/integration/core/test_fx_margin_engine.py``.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    ContingencyType,
    OrderSide,
    OrderStatus,
    OrderType,
    TriggerType,
)
from nautilus_trader.model.identifiers import ClientOrderId, ExecAlgorithmId
from nautilus_trader.model.objects import Price, Quantity

from src.core.margin_gate import DENIAL_REASON, FxRequirement, MarginGate
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

EURUSD = build_fx_pair(get_pair_spec("EURUSD"))
EURGBP = build_fx_pair(get_pair_spec("EURGBP"))
_IDS = iter(range(1, 10_000))


def _order(instrument, qty: int, price: str | None = None, leaves: int | None = None, **kw):
    return SimpleNamespace(
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        quantity=Quantity.from_int(qty),
        leaves_qty=Quantity.from_int(qty if leaves is None else leaves),
        price=Price.from_str(price) if price else None,
        trigger_price=None,
        status=kw.get("status", OrderStatus.INITIALIZED),
        events=list(kw.get("events", [])),
        order_type=kw.get("order_type", OrderType.LIMIT),
        contingency_type=kw.get("contingency_type", ContingencyType.NO_CONTINGENCY),
        emulation_trigger=kw.get("emulation_trigger", TriggerType.NO_TRIGGER),
        exec_algorithm_id=kw.get("exec_algorithm_id"),
        is_active_local=kw.get("active_local", False),
        is_reduce_only=kw.get("reduce_only", False),
        parent_order_id=None,
        client_order_id=ClientOrderId(f"O-{next(_IDS)}"),
    )


class _Cache:
    """No bars and no GBP rate yet: only limit orders can be priced (and only USD quotes)."""

    def __init__(self, free: str, inflight=(), open_orders=()) -> None:
        self._free = Decimal(free)
        self._inflight = list(inflight)
        self._open = list(open_orders)

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
        return self._inflight

    def orders_open(self, venue=None):
        return self._open

    def position(self, position_id):
        return None


def _gate(free: str = "10000", inflight=(), open_orders=()) -> MarginGate:
    terms = {i.id: FxRequirement(Decimal("0.03")) for i in (EURUSD, EURGBP)}
    cache = _Cache(free, inflight, open_orders)
    return MarginGate(risk_engine=None, cache=cache, msgbus=None, terms=terms)


def _event(kind: str):
    """A real order event: "pending" (OrderPendingUpdate), "updated" or "rejected"."""
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import OrderModifyRejected, OrderPendingUpdate, OrderUpdated
    from nautilus_trader.model.identifiers import StrategyId, TraderId

    ids = (TraderId("T-001"), StrategyId("S-001"), EURUSD.id, ClientOrderId("A"))
    if kind == "pending":
        return OrderPendingUpdate(*ids, None, None, UUID4(), 0, 0)
    if kind == "updated":
        qty = Quantity.from_int(100_000)
        return OrderUpdated(*ids, None, None, qty, Price.from_str("1.12000"), None, UUID4(), 0, 0)
    return OrderModifyRejected(*ids, None, None, "rejected", UUID4(), 0, 0)


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


class TestReprice:
    def test_a_partly_filled_order_is_margined_on_its_full_quantity_as_nautilus_does(self):
        # 300,000 at 1.10 with 200,000 filled: Nautilus 1.220 margins order.quantity, so
        # 1.10 -> 1.12 adds 300,000 x 0.02 x 3% = 180 USD (not 60 on the 100,000 left).
        order = _order(EURUSD, 300_000, "1.10000", leaves=100_000, status=OrderStatus.ACCEPTED)
        reason = _gate(free="100").modify_denial_reason(order, None, Price.from_str("1.12000"))
        assert reason is not None and reason.startswith(DENIAL_REASON)
        assert "by 180.00 USD" in reason

    def test_a_margin_raising_reprice_waits_for_another_pending_modify(self):
        # Two accepted limits could each pass against the same free 100 USD while both
        # increases are still pending; the second waits until the first is applied.
        other = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.PENDING_UPDATE)
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.PENDING_UPDATE)
        gate = _gate(free="100", inflight=[other, order])
        reason = gate.modify_denial_reason(order, None, Price.from_str("1.12000"))  # +60
        assert reason is not None and reason.startswith("FX_MODIFY_PENDING")

    def test_a_margin_raising_reprice_alone_within_free_passes(self):
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.PENDING_UPDATE)
        gate = _gate(free="100", inflight=[order])  # its own pending state does not count
        assert gate.modify_denial_reason(order, None, Price.from_str("1.12000")) is None

    def test_a_lowering_reprice_passes_whatever_is_pending(self):
        other = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.PENDING_UPDATE)
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED)
        gate = _gate(free="0", inflight=[other])
        assert gate.modify_denial_reason(order, None, Price.from_str("1.05000")) is None


class TestPendingReadFromEvents:
    """Whether another order's modify is unresolved comes from its events, not its status.

    Nautilus 1.220 puts an order back to its previous status when a later modify is rejected,
    even while an earlier modify of it is still unapplied (CodeRabbit, PR #63).
    """

    def test_a_reset_status_after_a_rejected_later_modify_still_counts_as_pending(self):
        # A: reprice to 1.12 queued (+60), reprice to 1.14 rejected -> status back to ACCEPTED,
        # and no longer in flight. B's +60 must still wait: A's +60 is unreserved.
        events = [_event("pending"), _event("pending"), _event("rejected")]
        other = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED, events=events)
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED)
        gate = _gate(free="100", open_orders=[other, order])
        reason = gate.modify_denial_reason(order, None, Price.from_str("1.12000"))
        assert reason is not None and reason.startswith("FX_MODIFY_PENDING")

    def test_a_resolved_modify_on_another_order_does_not_block(self):
        events = [_event("pending"), _event("updated")]
        other = _order(EURUSD, 100_000, "1.12000", status=OrderStatus.ACCEPTED, events=events)
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED)
        gate = _gate(free="100", open_orders=[other, order])
        assert gate.modify_denial_reason(order, None, Price.from_str("1.12000")) is None


#: Order kinds Nautilus 1.220 sends ``OrderUpdated`` to without a modify asking: trailing
#: stops (matching engine and emulator trail them), MARKET_TO_LIMIT (first fill reprices it),
#: reduce-only (a fill past the position is clipped), contingent OUO siblings (quantity
#: synced on a linked fill), emulated and exec-algorithm orders (local updates).
UNSOLICITED_KINDS = {
    "trailing stop market": {"order_type": OrderType.TRAILING_STOP_MARKET},
    "trailing stop limit": {"order_type": OrderType.TRAILING_STOP_LIMIT},
    "market to limit": {"order_type": OrderType.MARKET_TO_LIMIT},
    "reduce only": {"reduce_only": True},
    "contingent OUO": {"contingency_type": ContingencyType.OUO},
    "emulated": {"emulation_trigger": TriggerType.BID_ASK},
    "exec algorithm": {"exec_algorithm_id": ExecAlgorithmId("TWAP")},
}


class TestUnsolicitedUpdatesNeverAnswerAModify:
    """An ``OrderUpdated`` closes a pending modify only where it can only be the answer.

    Rejections always answer a modify (Nautilus emits ``OrderModifyRejected`` only in reply
    to one). On a plain limit or stop the only backtest source of ``OrderUpdated`` is the venue
    applying a modify, so it answers one. On the kinds above it may be unsolicited, so it never
    clears a pending modify: when in doubt the modify stays pending (a reprice or resize waits;
    margin is never overcommitted). CodeRabbit, PR #63.
    """

    @pytest.mark.parametrize("kind", list(UNSOLICITED_KINDS))
    def test_an_unsolicited_update_mid_modify_keeps_another_reprice_waiting(self, kind):
        # A (USD 100 free): a +60 reprice queued, then an update that may be A trailing on its
        # own. B's +60 must still wait, or both increases pass against the same 100 USD.
        events = [_event("pending"), _event("updated")]
        other = _order(
            EURUSD,
            100_000,
            "1.10000",
            status=OrderStatus.ACCEPTED,
            events=events,
            **UNSOLICITED_KINDS[kind],
        )
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED)
        gate = _gate(free="100", open_orders=[other, order])
        reason = gate.modify_denial_reason(order, None, Price.from_str("1.12000"))
        assert reason is not None and reason.startswith("FX_MODIFY_PENDING")

    @pytest.mark.parametrize("kind", list(UNSOLICITED_KINDS))
    def test_an_unsolicited_update_does_not_free_the_orders_own_next_resize(self, kind):
        # The shared quantity rule: an earlier modify, an update that may be unsolicited, then
        # this modify's own pending event. The earlier one is still outstanding.
        events = [_event("pending"), _event("updated"), _event("pending")]
        order = _order(
            EURUSD,
            100_000,
            "1.10000",
            status=OrderStatus.PENDING_UPDATE,
            events=events,
            **UNSOLICITED_KINDS[kind],
        )
        reason = _gate().modify_denial_reason(order, Quantity.from_int(50_000))
        assert reason is not None and reason.startswith("FX_MODIFY_PENDING")

    def test_a_rejection_still_answers_a_modify_on_any_kind(self):
        events = [_event("pending"), _event("rejected")]
        other = _order(
            EURUSD,
            100_000,
            "1.10000",
            status=OrderStatus.ACCEPTED,
            events=events,
            order_type=OrderType.TRAILING_STOP_MARKET,
        )
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.ACCEPTED)
        gate = _gate(free="100", open_orders=[other, order])
        assert gate.modify_denial_reason(order, None, Price.from_str("1.12000")) is None

    def test_on_a_plain_order_the_update_is_the_answer(self):
        events = [_event("pending"), _event("updated"), _event("pending")]
        order = _order(EURUSD, 100_000, "1.10000", status=OrderStatus.PENDING_UPDATE, events=events)
        assert _gate().modify_denial_reason(order, Quantity.from_int(50_000)) is None


def test_a_local_orders_modify_has_no_pending_event_so_none_is_popped_for_it():
    # Strategy.modify_order applies no OrderPendingUpdate to an active-local (emulated, not yet
    # released) order, so the last pending event is an earlier modify's and must still count.
    events = [_event("pending")]
    order = _order(
        EURUSD,
        100_000,
        "1.10000",
        status=OrderStatus.EMULATED,
        events=events,
        emulation_trigger=TriggerType.BID_ASK,
        active_local=True,
    )
    reason = _gate().modify_denial_reason(order, Quantity.from_int(50_000))
    assert reason is not None and reason.startswith("FX_MODIFY_PENDING")


class TestNewEntriesSeePendingReprices:
    """A new FX entry counts every still-pending reprice of a margined entry as committed.

    The gate keeps no per-order state and Nautilus keeps the requested price only in the
    simulated exchange's private queue, so the size of a pending increase is unknown: it is
    taken as committed in full, and the new entry waits (``FX_MODIFY_PENDING``) until the
    reprice is applied or rejected (CodeRabbit, PR #63).
    """

    def test_a_new_order_waits_for_a_pending_reprice_then_passes_once_it_is_rejected(self):
        # USD 100 free; A's +60 reprice is pending; a new 1,000 EURUSD limit needs 33 USD.
        new = _order(EURUSD, 1_000, "1.10000")
        pending = _order(
            EURUSD,
            100_000,
            "1.10000",
            status=OrderStatus.PENDING_UPDATE,
            events=[_event("pending")],
        )
        reason = _gate(free="100", inflight=[pending], open_orders=[pending]).denial_reason(
            [new], None
        )
        assert reason is not None and reason.startswith("FX_MODIFY_PENDING")

        rejected = _order(
            EURUSD,
            100_000,
            "1.10000",
            status=OrderStatus.ACCEPTED,
            events=[_event("pending"), _event("rejected")],
        )
        assert _gate(free="100", open_orders=[rejected]).denial_reason([new], None) is None

    def test_a_pending_modify_on_an_exit_order_does_not_hold_new_entries(self):
        # Reduce-only and child (bracket) orders are not margined as entries, so a modify of
        # them is never a margin-raising reprice the gate approved.
        new = _order(EURUSD, 1_000, "1.10000")
        exit_order = _order(
            EURUSD,
            100_000,
            "1.05000",
            status=OrderStatus.PENDING_UPDATE,
            events=[_event("pending")],
            reduce_only=True,
        )
        gate = _gate(free="100", inflight=[exit_order], open_orders=[exit_order])
        assert gate.denial_reason([new], None) is None
