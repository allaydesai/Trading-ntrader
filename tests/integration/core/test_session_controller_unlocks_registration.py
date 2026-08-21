"""The one fact Story 2.5's whole design rests on, proven against a real Trader.

Integration tier and ``--forked``: this builds a **real** ``nautilus_trader``
``Trader`` with a ``MessageBus``, a ``Cache`` and three engines, which claims
the C logging subsystem. The component tier runs ``-n auto`` unforked and
cannot host it.

**Why this cannot live in the component tier as a double.**
``tests/component/doubles/test_live_node.py``'s ``_TestTrader`` deliberately
does not reproduce Nautilus's ``if self.is_running and not self._has_controller:
return`` branch — a double asserting its own behaviour proves nothing. This
file is the mutation proof Story 2.5's Task 9 asks for, encoded as a test that
re-runs every build rather than as a procedure someone has to remember.

The fact: ``Trader.add_strategy`` and ``Trader.add_actor`` **silently return**
on a running trader unless the trader was built with ``has_controller=True``
(``trading/trader.py:331-333``, ``:395-397``). No exception, one ERROR log
line. Story 2.5 registers the bar observer and every strategy *after*
``gate:account`` has passed — so without a controller a session would run a
full trading day having registered nothing at all, and report success.
"""

import asyncio

import pytest
from nautilus_trader.common import Environment
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine
from nautilus_trader.test_kit.stubs.component import TestComponentStubs
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.trading.trader import Trader

from src.core.live_session_controller import build_session_controller_config

pytestmark = pytest.mark.integration


@pytest.fixture
def owned_event_loop():
    """A loop this test owns end to end. ``Trader`` reads one on construction."""
    previous = None
    try:
        previous = asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # noqa: BLE001 - no loop set is a normal state
        previous = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(previous if previous and not previous.is_closed() else None)


def _trader(*, has_controller: bool) -> Trader:
    clock = LiveClock()
    trader_id = TraderId("PAPER-a1b2c3d4")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = TestComponentStubs.cache()
    portfolio = Portfolio(msgbus=msgbus, cache=cache, clock=clock)
    return Trader(
        trader_id=trader_id,
        instance_id=UUID4(),
        msgbus=msgbus,
        cache=cache,
        portfolio=portfolio,
        data_engine=DataEngine(msgbus=msgbus, cache=cache, clock=clock),
        risk_engine=RiskEngine(portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock),
        exec_engine=ExecutionEngine(msgbus=msgbus, cache=cache, clock=clock),
        clock=clock,
        environment=Environment.LIVE,
        has_controller=has_controller,
    )


class TestPostStartRegistrationNeedsAController:
    """AC #8's load-bearing precondition, measured rather than argued."""

    def test_without_a_controller_add_strategy_silently_registers_nothing(self, owned_event_loop):
        """The failure mode: **no exception**, and zero strategies afterwards."""
        trader = _trader(has_controller=False)
        trader.start()
        assert trader.is_running is True

        trader.add_strategy(Strategy())  # returns normally — that is the defect

        assert trader.strategies() == []

    def test_with_a_controller_the_same_call_registers_the_strategy(self, owned_event_loop):
        """The control. Without this the test above would also pass against a
        Nautilus that had stopped accepting strategies at all.
        """
        trader = _trader(has_controller=True)
        trader.start()
        assert trader.is_running is True

        trader.add_strategy(Strategy())

        assert len(trader.strategies()) == 1

    def test_without_a_controller_add_actor_is_refused_the_same_way(self, owned_event_loop):
        """The bar observer goes on at ``subscribe``, after the gate, and takes
        the same path — ``deferred-work.md:354-361`` asked whether the ordering
        guard covered actors, and this is why the answer stopped mattering.
        """
        from nautilus_trader.common.actor import Actor

        trader = _trader(has_controller=False)
        trader.start()

        trader.add_actor(Actor())

        assert trader.actors() == []

    def test_the_runner_always_supplies_a_controller(self):
        """Structural, so the two facts above cannot drift apart from the code
        that depends on them: the runner passes a controller unconditionally,
        never behind a flag or a default the caller can drop.
        """
        import ast
        from pathlib import Path

        from src.core import live_session_runner

        tree = ast.parse(Path(live_session_runner.__file__).read_text(encoding="utf-8"))
        controllers = [
            keyword
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "controller"
        ]

        assert len(controllers) == 1, "the runner must pass `controller=` exactly once"
        value = controllers[0].value
        assert isinstance(value, ast.Call), "the controller must be built, not passed as None"
        assert value.func.id == "build_session_controller_config"

    def test_the_controller_config_the_runner_builds_is_not_none(self):
        assert build_session_controller_config() is not None
