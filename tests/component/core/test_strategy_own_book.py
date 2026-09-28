"""Story 4.5 (D-B) — the built-in strategies act on their own book only.

Component tier: Story 4.4's harness (a real ``MessageBus``/``Cache``/
``DataEngine`` and a real strategy, order intent recorded instead of
submitted), with positions seeded into the cache beside the strategy's own
the way a restart or a reconciliation correction leaves them.

Before this story ``sma_crossover`` read ``cache.positions(venue,
instrument_id)`` — every strategy's — and ``close_position()``\\ d every
opposite-side one, and ``momentum`` read the portfolio's net. So in the
triple a pre-4.5 restart left (``S +10 / EXTERNAL +10 / INTERNAL-DIFF −10``,
Story 4.2 Task 1.4A) a bearish crossover closed the strategy's 10 **and**
``EXTERNAL``'s 10, and a bullish one "closed" ``INTERNAL-DIFF −10`` — buying
10 more (``deferred-work.md:3353-3361``). A holding no strategy owns was acted
on as if it were the strategy's (NFR14).
"""

from decimal import Decimal

import pytest
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.enums import OmsType, OrderSide
from nautilus_trader.model.identifiers import ClientOrderId, PositionId, StrategyId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from tests.component.core.test_strategy_warmup_engine import (
    AAPL,
    FLAT,
    JUMP,
    STRATEGIES,
    _Harness,
    _history,
    _momentum,
)

pytestmark = pytest.mark.component

#: A bar low enough to pull the fast average below the slow one — a death cross
#: against the flat history's baseline.
DROP = 70.0


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


def _seed(harness: _Harness, owner: StrategyId, qty: int) -> PositionId:
    """An open NETTING position for ``owner``, as the cache holds one after a
    restart (the strategy's own) or a reconciliation (a synthetic owner)."""
    position_id = PositionId(f"{AAPL.id}-{owner}")
    order = TestExecStubs.market_order(
        instrument=AAPL,
        order_side=OrderSide.BUY if qty > 0 else OrderSide.SELL,
        quantity=Quantity.from_int(abs(qty)),
        strategy_id=owner,
        client_order_id=ClientOrderId(f"O-SEED-{owner}"),
    )
    harness.cache.add_order(order, position_id)
    order.apply(TestEventStubs.order_submitted(order))
    order.apply(TestEventStubs.order_accepted(order))
    fill = TestEventStubs.order_filled(
        order, AAPL, position_id=position_id, last_px=Price.from_str("100.00")
    )
    order.apply(fill)
    harness.cache.update_order(order)
    harness.cache.add_position(Position(AAPL, fill), OmsType.NETTING)
    harness.strategy.portfolio.initialize_positions()
    return position_id


class _Recording(_Harness):
    """Records *which* position a ``close_position`` targets, not just that one
    was closed — the triple's hazard is closing someone else's — and the size of
    every order submitted (``sizes``)."""

    def __init__(self, strategy, **kwargs) -> None:
        super().__init__(strategy, **kwargs)
        strategy.close_position = lambda position, *a, **k: self.orders.append(
            f"CLOSE {position.id}"
        )
        self.sizes: list[str] = []
        submit_order = strategy.submit_order

        def recording(order, *args, **kwargs):
            self.sizes.append(f"{order.side_string()} {order.quantity}")
            return submit_order(order, *args, **kwargs)

        strategy.submit_order = recording


def _started(make, *synthetic: tuple[str, int], own: int = 0) -> _Recording:
    harness = _Recording(make(), history=_history(FLAT))
    strategy_id = harness.strategy.id
    if own:
        _seed(harness, strategy_id, own)
    for owner, qty in synthetic:
        _seed(harness, StrategyId(owner), qty)
    harness.start()
    return harness


TRIPLE = (("EXTERNAL", 10), ("INTERNAL-DIFF", -10))


class TestCrossoverActsOnItsOwnPositionOnly:
    def test_a_bearish_cross_beside_the_triple_closes_only_its_own_position(self):
        harness = _started(STRATEGIES[0].values[0], *TRIPLE, own=10)

        harness.live(1, DROP)

        assert harness.orders == [f"CLOSE {AAPL.id}-{harness.strategy.id}"]

    def test_a_bullish_cross_beside_the_triple_submits_nothing_when_already_long(self):
        harness = _started(STRATEGIES[0].values[0], *TRIPLE, own=10)

        harness.live(1, JUMP)

        assert harness.orders == []

    def test_flat_beside_an_unowned_holding_it_reads_its_own_book_as_flat(self):
        """What D-C then governs at the runner: the strategy itself would enter."""
        harness = _started(STRATEGIES[0].values[0], ("INTERNAL-DIFF", 10))

        harness.live(1, JUMP)

        assert harness.orders == ["BUY"]


class TestMomentumActsOnItsOwnPositionOnly:
    def test_flat_beside_an_unowned_holding_a_death_cross_sells_nothing(self):
        """Before: the portfolio's net read the holding as momentum's own long,
        and the long-only exit sold ``trade_size`` of it."""
        harness = _started(STRATEGIES[1].values[0], ("INTERNAL-DIFF", 10))

        harness.live(1, DROP)

        assert harness.orders == []

    def test_flat_beside_an_unowned_holding_a_golden_cross_enters(self):
        harness = _started(STRATEGIES[1].values[0], ("INTERNAL-DIFF", 10))

        harness.live(1, JUMP)

        assert harness.orders == ["BUY"]

    def test_its_own_long_beside_the_triple_exits_once_on_a_death_cross(self):
        harness = _started(STRATEGIES[1].values[0], *TRIPLE, own=10)

        harness.live(1, DROP)

        assert harness.orders == ["SELL"]

    def test_a_resumed_long_of_another_size_is_exited_in_full(self):
        """Code review 2026-09-28: the long-only exit sold ``trade_size`` (10)
        against a resumed +7 (an entry part-filled before the restart) — which
        would have left the account short 3. It sells its own quantity."""
        harness = _started(_momentum, own=7)

        harness.live(1, DROP)

        assert harness.sizes == ["SELL 7"]

    def test_a_flip_covers_its_own_short_then_opens_trade_size(self):
        harness = _started(lambda: _momentum(allow_short=True), own=-7)

        harness.live(1, JUMP)

        assert harness.sizes == ["BUY 17"]

    def test_its_own_long_does_not_re_enter_on_a_golden_cross(self):
        harness = _started(STRATEGIES[1].values[0], own=10)

        harness.live(1, JUMP)

        assert harness.orders == []


def test_the_seeded_triple_nets_to_the_strategys_own_quantity():
    """The fixture's premise, so the tests above cannot pass on an empty cache."""
    harness = _started(STRATEGIES[0].values[0], *TRIPLE, own=10)

    net = sum((p.signed_decimal_qty() for p in harness.cache.positions_open()), Decimal(0))
    assert net == Decimal("10")
    assert len(harness.cache.positions_open()) == 3
