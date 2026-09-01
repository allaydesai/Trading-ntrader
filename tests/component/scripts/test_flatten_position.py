"""Component tests for the one tool in this repo that submits an order on purpose.

Written 2026-09-01, immediately after the tool traded on a **dry run**.

The failure, measured rather than reasoned about: running
``flatten_position.py --instrument NVDA.NASDAQ --side SELL --quantity 22``
*without* ``--confirm`` submitted and filled a 22-share SELL
(``venue_order_id=104``, ``commission=1.10 USD``). The tool added its strategy
to the node before ``run_async()``, and ``TradingNodeKernel.start_async`` ends
with ``self._trader.start()`` (``system/kernel.py:1027``), which starts every
strategy already added. ``on_start`` submitted from there — before the connect
wait, before the broker read, before the offset check, and before the
``--confirm`` branch that was supposed to be the gate.

Two things made it invisible until it fired at a live broker:

* ``live_session_runner.py`` is safe from the same trap by construction — it
  adds no strategy until ``_phase_trading``, long after ``_phase_node_connect``
  started the node — and the tool's comment cited the runner's
  ``start_strategy`` call as proof that ``run_async()`` does not start
  strategies. It does; the runner just has nothing for it to start.
* The tool's refusal paths were hand-verified while a *separate* bug (the
  misnamed exec-client factory, see ``src/core/live_exec_avg_px.py``) was
  causing ``start_async`` to return early at ``kernel.py:1025``, before
  ``_trader.start()``. The trader never started, so ``on_start`` never fired,
  and the dry run looked safe. Fixing the factory removed that accidental
  safety net and the tool traded on the next dry run.

So these tests drive ``_run`` itself against a fake node. A source-scan would
have passed on the broken version — the ``if not confirm`` branch was there and
looked correct — which is exactly why the property is asserted by behaviour:
**on a dry run nothing that can submit an order is ever handed to the trader.**
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId

REPO_ROOT = Path(__file__).resolve().parents[3]
NVDA = InstrumentId.from_str("NVDA.NASDAQ")


def _load_tool():
    """Import the script by path — ``scripts/`` is not an importable package."""
    path = REPO_ROOT / "scripts" / "diagnostics" / "flatten_position.py"
    spec = importlib.util.spec_from_file_location("flatten_position", path)
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


class _Trader:
    """Records what reached the trader, and in which order.

    Starting a strategy also *simulates its fill*, because the real
    ``on_start`` → broker → ``on_order_filled`` round trip is exactly what this
    double stands in for. Without it the tool would block on ``_await_fill``,
    and the confirmed-run tests would pass or fail on a timeout rather than on
    the ordering they exist to check.
    """

    def __init__(self, positions: list) -> None:
        self.added: list = []
        self.started: list = []
        self._positions = positions

    def add_strategy(self, strategy) -> None:
        self.added.append(strategy)

    def start_strategy(self, strategy_id) -> None:
        self.started.append(strategy_id)
        for strategy in self.added:
            if strategy.id == strategy_id:
                strategy.fills.append(_Fill())
        self._positions.clear()  # the close went through; the account is flat


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
    """A node that connects instantly and holds whatever the broker 'reports'.

    ``run_async`` deliberately does **not** start anything: this double models a
    node whose strategies are started only by an explicit ``start_strategy``,
    so a test that passes here is asserting the tool's own ordering rather than
    re-deriving Nautilus's.
    """

    def __init__(self, positions: list) -> None:
        self.kernel = _Kernel(positions)
        self.trader = _Trader(positions)
        self.built = False
        self.stopped = False
        self.disposed = False
        self._running = asyncio.Event()

    def build(self) -> None:
        self.built = True

    async def run_async(self) -> None:
        # Parks until `stop()`, like the real thing. Deliberately not a sleep:
        # these tests patch `asyncio.sleep` to be instant, and a sleeping
        # `run_async` would "finish" on its own, which the tool correctly reads
        # as the node having died.
        await self._running.wait()

    def stop(self) -> None:
        self.stopped = True
        self._running.set()

    def dispose(self) -> None:
        self.disposed = True


def _run_with(positions: list, side: OrderSide, quantity: int, *, confirm: bool):
    """Drive the real ``_run`` against a fake node; return (result, node)."""
    node = _Node(positions)
    captured = {}

    def _build(*args, **kwargs):
        captured["node"] = node
        return node

    monkey = pytest.MonkeyPatch()
    try:
        # Reconciliation settle and fill polling are wall-clock waits the fake
        # node has no use for; the arming order is what is under test.
        monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
        result = flatten._run(NVDA, side, quantity, confirm=confirm, build=_build)
    finally:
        monkey.undo()
    return result, captured["node"]


def _instant_sleep(module):
    real_sleep = asyncio.sleep

    async def _sleep(delay, *args, **kwargs):
        # `run_async` parks on a long sleep; everything else should not block.
        return await real_sleep(0)

    return _sleep


class TestDryRunNeverTrades:
    """The property the tool exists to guarantee, and once did not."""

    @pytest.mark.component
    def test_a_dry_run_hands_the_trader_nothing_that_could_submit(self):
        """The 2026-09-01 regression, stated directly.

        Not "no order was submitted" — *nothing capable of submitting one was
        ever registered*. That is the stronger claim, and it is the one that
        survives Nautilus deciding to start strategies on its own.
        """
        _, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=False)

        assert node.trader.added == []
        assert node.trader.started == []

    @pytest.mark.component
    def test_a_dry_run_still_reports_what_it_found(self):
        result, _ = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=False)

        assert "held=+22" in result
        assert "submitted=0" in result

    @pytest.mark.component
    def test_a_confirmed_run_arms_exactly_one_strategy(self):
        """The anti-tautology twin: the assertions above must fail for a reason
        other than the tool being inert.
        """
        _, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=True)

        assert len(node.trader.added) == 1
        assert node.trader.started == [node.trader.added[0].id]

    @pytest.mark.component
    def test_the_strategy_is_added_only_after_the_node_is_running(self):
        """The precise ordering the defect violated.

        The node must already be built and running when the strategy arrives,
        so that `_trader.start()` — which has already happened by then — cannot
        have started it.
        """
        _, node = _run_with([_Position(NVDA, 22)], OrderSide.SELL, 22, confirm=True)

        assert node.built is True
        assert node.trader.added, "nothing was armed, so the ordering is untested"


class TestRefusals:
    """The offset checks, driven through `_run` rather than through
    `_check_offsets` alone — a refusal that still armed the strategy would be
    no refusal at all, and that distinction is invisible at the unit level.
    """

    @pytest.mark.component
    def test_a_wrong_side_is_refused_without_arming(self):
        with pytest.raises(flatten.FlattenError, match="closed by SELL"):
            _run_with([_Position(NVDA, 22)], OrderSide.BUY, 22, confirm=True)

    @pytest.mark.component
    def test_a_wrong_quantity_is_refused_without_arming(self):
        with pytest.raises(flatten.FlattenError, match="but --quantity 21"):
            _run_with([_Position(NVDA, 22)], OrderSide.SELL, 21, confirm=True)

    @pytest.mark.component
    def test_a_flat_account_is_refused_without_arming(self):
        with pytest.raises(flatten.FlattenError, match="no open position"):
            _run_with([], OrderSide.SELL, 22, confirm=True)

    @pytest.mark.component
    def test_a_refused_run_armed_nothing(self):
        """The refusals above must also be inert, not merely raising."""
        node = _Node([_Position(NVDA, 22)])

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
            with pytest.raises(flatten.FlattenError):
                flatten._run(NVDA, OrderSide.BUY, 22, confirm=True, build=lambda *a, **k: node)
        finally:
            monkey.undo()

        assert node.trader.added == []
        assert node.trader.started == []

    @pytest.mark.component
    def test_positions_in_other_instruments_do_not_count(self):
        """The account carries an unrelated AAPL residual; netting it into NVDA
        would arm a close against the wrong size.
        """
        aapl = _Position(InstrumentId.from_str("AAPL.NASDAQ"), 4)

        with pytest.raises(flatten.FlattenError, match="no open position"):
            _run_with([aapl], OrderSide.SELL, 22, confirm=True)


class TestTeardown:
    @pytest.mark.component
    def test_the_node_is_disposed_even_when_the_run_is_refused(self):
        node = _Node([])

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(flatten.asyncio, "sleep", _instant_sleep(flatten))
            with pytest.raises(flatten.FlattenError):
                flatten._run(NVDA, OrderSide.SELL, 22, confirm=True, build=lambda *a, **k: node)
        finally:
            monkey.undo()

        assert node.disposed is True
