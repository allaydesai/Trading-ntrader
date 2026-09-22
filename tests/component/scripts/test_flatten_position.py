"""Component tests for the one tool in this repo that submits an order on purpose.

Two regression mechanisms shaped this suite, and they contradict each other
unless both are pinned at once:

1. **2026-09-01** — a strategy added *before* ``run_async()`` had its
   ``on_start()`` submit an order, because ``TradingNodeKernel.start_async``
   ends with ``self._trader.start()`` (``system/kernel.py:1027``), which
   calls ``on_start()`` on every strategy already added. A dry run filled a
   real order (``venue_order_id=104``, a 22-share SELL) before the connect
   wait, the broker read, the offset check, or the ``--confirm`` branch ever
   ran.
2. **2026-09-11** — the fix for (1) moved construction of the strategy to
   *after* ``run_async()``, so ``on_start`` could never fire early. That hit
   Nautilus 1.220.0's ``Trader.add_strategy()`` refusing to add to an
   already-``RUNNING`` trader (``trading/trader.py:392-397``): it logs and
   returns without adding, so the following ``start_strategy`` call raised
   ``ValueError: Cannot start strategy, ... not found``. Fails safe (no order
   reaches the broker) but ``--confirm`` could never submit anything either.

The fix pinned here: register the strategy before the trader starts (so
mechanism 2 does not apply), give it an inert ``on_start()`` (so mechanism 1
does not apply), and arm it only through its one submit path,
``submit_close``, called directly by ``_run`` after the broker read, the
offset check, and ``--confirm``. So the fake ``_Trader`` mirrors 1.220.0
closely enough that *either* past shape fails this suite: ``add_strategy``
silently no-ops once ``is_running`` is true, and ``start()`` — driven from
the fake node's ``run_async()``, exactly where the real kernel drives it —
calls ``on_start()`` on every strategy already added.
"""

import ast
import asyncio
import importlib.util
from pathlib import Path

import pytest
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

REPO_ROOT = Path(__file__).resolve().parents[3]
NVDA = InstrumentId.from_str("NVDA.NASDAQ")
FLATTEN_SCRIPT = REPO_ROOT / "scripts" / "diagnostics" / "flatten_position.py"


def _load_tool():
    """Import the script by path — ``scripts/`` is not an importable package."""
    spec = importlib.util.spec_from_file_location("flatten_position", FLATTEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flatten = _load_tool()


class _Position:
    def __init__(self, instrument_id: InstrumentId, signed_qty: int) -> None:
        self.instrument_id = instrument_id
        self.signed_qty = signed_qty


class _Fill:
    """Just the fields the tool prints off a fill."""

    last_qty = 22
    last_px = "217.87"
    commission = "1.10 USD"
    trade_id = "TEST-1"
    venue_order_id = "104"


class _FakeInstrument:
    """Only its presence (not ``None``) matters to ``submit_close``."""


class _FakeOrder:
    def __init__(self, instrument_id: InstrumentId, order_side: OrderSide, quantity: int) -> None:
        self.instrument_id = instrument_id
        self.order_side = order_side
        self.quantity = quantity


class _FakeCache:
    def __init__(self, instrument) -> None:
        self._instrument = instrument

    def instrument(self, instrument_id: InstrumentId):
        return self._instrument


class _FakeOrderFactory:
    def market(self, *, instrument_id, order_side, quantity):
        return _FakeOrder(instrument_id, order_side, quantity)


def _patch_submit_seam(monkey, positions: list, *, on_submit=None) -> None:
    """Wire a fake ``cache``/``order_factory`` onto ``_FlattenStrategy`` so
    ``submit_close``'s real body runs unmodified, and a fake ``submit_order``
    that simulates the fill. ``cache``/``order_factory`` are Cython
    ``readonly`` properties on the real ``Strategy`` base and cannot be set
    per-instance, but a ``property`` on the (plain Python) subclass shadows
    them for the life of the patch.
    """
    monkey.setattr(
        flatten._FlattenStrategy,
        "cache",
        property(lambda self: _FakeCache(_FakeInstrument())),
    )
    monkey.setattr(
        flatten._FlattenStrategy,
        "order_factory",
        property(lambda self: _FakeOrderFactory()),
    )

    def _submit_order(self, order) -> None:
        if on_submit is not None:
            on_submit(order)
        self.fills.append(_Fill())
        positions.clear()  # the close went through; the account is flat

    monkey.setattr(flatten._FlattenStrategy, "submit_order", _submit_order)


class _Trader:
    """Mirrors Nautilus 1.220.0 closely enough that either past shape of the
    tool fails this suite:

    * ``add_strategy`` silently no-ops once ``is_running`` is true
      (``trading/trader.py:392-397``) — the regression test for mechanism 2.
    * ``start()`` calls ``on_start()`` on every strategy already added,
      exactly where ``TradingNodeKernel.start_async`` calls it
      (``system/kernel.py:1027``) — the regression test for mechanism 1.
    """

    def __init__(self) -> None:
        self.added: list = []
        self.is_running = False
        self.add_called_while_running: list = []

    def add_strategy(self, strategy) -> None:
        self.add_called_while_running.append(self.is_running)
        if self.is_running:
            return
        self.added.append(strategy)

    def start(self) -> None:
        self.is_running = True
        for strategy in self.added:
            strategy.on_start()


class _Engine:
    def check_connected(self) -> bool:
        return True


class _Cache:
    def __init__(self, positions: list) -> None:
        self._positions = positions

    def positions_open(self) -> list:
        return self._positions


class _Kernel:
    def __init__(self, positions: list) -> None:
        self.data_engine = _Engine()
        self.exec_engine = _Engine()
        self.cache = _Cache(positions)


class _Node:
    """A node that connects instantly and starts its trader exactly where the
    real kernel does: at the top of ``run_async()``, before parking. A
    strategy added before this call is started exactly as it would be for
    real — the same opportunity mechanism 1 exploited.
    """

    def __init__(self, positions: list) -> None:
        self.kernel = _Kernel(positions)
        self.trader = _Trader()
        self.built = False
        self.stopped = False
        self.disposed = False
        self._running = asyncio.Event()

    def build(self) -> None:
        self.built = True

    async def run_async(self) -> None:
        self.trader.start()
        # Parks until `stop()`, like the real thing. Deliberately not a sleep:
        # these tests patch `asyncio.sleep` to be instant, and a sleeping
        # `run_async` would "finish" on its own, which the tool correctly
        # reads as the node having died.
        await self._running.wait()

    def stop(self) -> None:
        self.stopped = True
        self._running.set()

    def dispose(self) -> None:
        self.disposed = True


def _instant_sleep(module):
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        # `run_async` parks on a long sleep; everything else should not block.
        return await real_sleep(0)

    return _sleep


def _run_with(positions: list, side: OrderSide, quantity: int, *, confirm: bool, on_submit=None):
    """Drive the real ``_run`` against a fake node; return (result, node)."""
    node = _Node(positions)

    monkey = pytest.MonkeyPatch()
    try:
        # Reconciliation settle and fill polling are wall-clock waits the fake
        # node has no use for; the arming order is what is under test.
        monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
        _patch_submit_seam(monkey, positions, on_submit=on_submit)
        result = flatten._run(NVDA, side, quantity, confirm=confirm, build=lambda *a, **k: node)
    finally:
        monkey.undo()
    return result, node


class TestDryRunNeverTrades:
    """The property the tool exists to guarantee, and once did not."""

    @pytest.mark.component
    def test_a_dry_run_submits_nothing(self):
        """The 2026-09-01 regression, stated directly.

        Not "no order was submitted" — *nothing was ever handed to
        ``submit_order``*. The strategy is added and started (both mirror
        production now), which is exactly why an inert ``on_start`` matters.
        """
        result, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=False)

        strategy = node.trader.added[0]
        assert strategy.submitted == []
        assert node.trader.is_running is True
        assert "held=+22" in result
        assert "submitted=0" in result

    @pytest.mark.component
    def test_a_confirmed_run_submits_exactly_one_order(self):
        """The anti-tautology twin: the assertion above must fail for a
        reason other than the tool being inert end to end.
        """
        result, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=True)

        strategy = node.trader.added[0]
        assert len(strategy.submitted) == 1
        order = strategy.submitted[0]
        assert order.order_side == OrderSide.SELL
        assert order.quantity == 22
        assert len(strategy.fills) == 1
        assert node.kernel.cache.positions_open() == []
        assert "fills=1" in result

    @pytest.mark.component
    def test_the_strategy_is_added_before_the_trader_starts(self):
        """The precise ordering both regressions violated, one in each
        direction: added too late (mechanism 2) or started too early relative
        to the checks (mechanism 1).
        """
        _, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=True)

        assert node.built is True
        assert node.trader.add_called_while_running == [False]

    @pytest.mark.component
    def test_submission_happens_after_the_offset_check(self):
        """``submit_close`` must be reachable only downstream of the broker
        read — the property that makes the tool's refusals meaningful.
        """
        call_log: list = []
        real_held_net_quantity = flatten._held_net_quantity

        def _spy_held_net_quantity(node, instrument_id):
            call_log.append("held_net_quantity")
            return real_held_net_quantity(node, instrument_id)

        monkey = pytest.MonkeyPatch()
        positions = [_Position(NVDA, 22)]
        node = _Node(positions)
        try:
            monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
            monkey.setattr(flatten, "_held_net_quantity", _spy_held_net_quantity)
            _patch_submit_seam(
                monkey, positions, on_submit=lambda order: call_log.append("submit_order")
            )
            flatten._run(NVDA, OrderSide.SELL, 22, confirm=True, build=lambda *a, **k: node)
        finally:
            monkey.undo()

        # A third `held_net_quantity` call follows, reporting the remaining
        # position after the fill — irrelevant to the property under test.
        assert call_log[:2] == ["held_net_quantity", "submit_order"]


class TestOnStartIsInert:
    """The regression surface for mechanism 1, pinned directly on the hook."""

    @pytest.mark.component
    def test_on_start_submits_nothing(self):
        """Drive ``on_start()`` directly — no node, no ``_run`` — so this
        fails immediately if anyone ever adds order-submitting logic back to
        the hook, regardless of what ``_run`` does around it.
        """
        calls: list = []
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(
                flatten._FlattenStrategy, "submit_order", lambda self, order: calls.append(order)
            )
            strategy = flatten._FlattenStrategy(NVDA)
            strategy.on_start()
        finally:
            monkey.undo()

        assert calls == []


class TestNoLifecycleHookCanReachSubmitOrder:
    """AST-level pin, same shape as
    ``tests/unit/core/test_live_stop_path_is_inert.py``: a source scan would
    have caught neither past regression by reading the code alone (both
    looked correct), so this asserts the *structural* property instead —
    exactly one method may call ``submit_order``, and no lifecycle hook may
    reach it.
    """

    @pytest.mark.component
    def test_no_lifecycle_hook_can_reach_submit_order(self):
        tree = ast.parse(FLATTEN_SCRIPT.read_text())
        strategy_class = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "_FlattenStrategy"
        )
        methods = {
            node.name: node for node in strategy_class.body if isinstance(node, ast.FunctionDef)
        }

        def _calls(func_node: ast.FunctionDef, name: str) -> bool:
            return any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == name
                for n in ast.walk(func_node)
            )

        submit_order_callers = [
            name for name, node in methods.items() if _calls(node, "submit_order")
        ]
        assert submit_order_callers == ["submit_close"], submit_order_callers

        lifecycle_hooks = [name for name in methods if name.startswith("on_")]
        hooks_reaching_submit_close = [
            name for name in lifecycle_hooks if _calls(methods[name], "submit_close")
        ]
        assert hooks_reaching_submit_close == []


class TestRefusals:
    """The offset checks, driven through `_run` rather than through
    `_check_offsets` alone — a refusal that still called `submit_close` would
    be no refusal at all, and that distinction is invisible at the unit
    level.
    """

    @pytest.mark.component
    @pytest.mark.parametrize(
        ("positions", "side", "quantity", "match"),
        [
            pytest.param(
                [_Position(NVDA, 22)], OrderSide.BUY, 22, "closed by SELL", id="wrong_side"
            ),
            pytest.param(
                [_Position(NVDA, 22)],
                OrderSide.SELL,
                21,
                "but --quantity 21",
                id="wrong_quantity",
            ),
            pytest.param([], OrderSide.SELL, 22, "no open position", id="flat_account"),
            pytest.param(
                [_Position(InstrumentId.from_str("AAPL.NASDAQ"), 4)],
                OrderSide.SELL,
                22,
                "no open position",
                id="other_instrument",
            ),
        ],
    )
    def test_a_refused_run_submits_nothing(self, positions, side, quantity, match):
        positions = list(positions)
        node = _Node(positions)
        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
            _patch_submit_seam(monkey, positions)
            with pytest.raises(flatten.FlattenError, match=match):
                flatten._run(NVDA, side, quantity, confirm=True, build=lambda *a, **k: node)
        finally:
            monkey.undo()

        assert node.trader.added[0].submitted == []


class TestTeardown:
    @pytest.mark.component
    def test_the_node_is_disposed_even_when_the_run_is_refused(self):
        positions: list = []
        node = _Node(positions)

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
            _patch_submit_seam(monkey, positions)
            with pytest.raises(flatten.FlattenError):
                flatten._run(NVDA, OrderSide.SELL, 22, confirm=True, build=lambda *a, **k: node)
        finally:
            monkey.undo()

        assert node.disposed is True
