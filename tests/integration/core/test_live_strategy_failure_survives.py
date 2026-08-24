"""The process survives a raising strategy (Story 2.7, AC #1 and AC #3).

Integration tier and ``--forked``, because the probe constructs a real
``LiveDataEngine`` — which claims the Nautilus C logging subsystem, so it must
never be built in the component tier (``test_session_runner_phases.py``'s autouse
``_assert_c_logging_state_is_unchanged`` fixture exists to catch exactly that).

**This is the only test in the suite that can prove the story's central claim.**
Everything else observes a log record or an object's state; those all pass
identically against a design that logs beautifully and then dies. Measured
against the installed ``nautilus-trader 1.220.0``, the uncontained path ends at
``LiveDataEngine._handle_queue_exception``'s ``os._exit(1)``
(``live/data_engine.py:347-365``), which runs no ``except``, no ``finally`` and
no ``atexit`` — so **the return code is the assertion**, and stdout is not:
``os._exit`` discards buffered output, and the uncontained run was measured at
``rc=1`` with **zero bytes**. Every ``print`` below carries ``flush=True`` for
the same reason.

A ``BacktestEngine`` test could not stand in for this. EXECUTED: a
``BacktestEngine`` with a raising ``on_bar`` gives ``engine.run() RAISED
RuntimeError``, ``EXIT_CODE=0``, process alive — there is no ``os._exit`` on the
backtest path at all, so such a test is *structurally incapable* of detecting
the regression and would pass against a design that still crashes in live. Nor
could ``TestLiveNode``: it constructs no ``MessageBus``, no ``DataEngine`` and no
kernel.

⚠️ The probe deliberately uses **``LiveDataEngineConfig()`` with its defaults**,
i.e. ``graceful_shutdown_on_exception=False``. That is not an oversight — it is
what makes the proof mean something. With AC #6's ``True`` the uncontained run
would publish ``ShutdownSystem`` into a bus with no kernel subscribed and exit
``0`` anyway, so the inverted meta-test below would pass vacuously and prove
nothing about the guard. This test isolates the guard from its own backstop.
"""

import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

#: The line the guarded run prints, matched as a **whole line**. Story 2.6's
#: harness was bitten by substring matching: Nautilus logs its own
#: ``Cache: READY`` lines, and a ``"READY"`` substring check fired
#: mid-construction against a process that was nowhere near ready.
SURVIVED = "SURVIVED"

#: The line the meta-test replaces to remove the containment. Asserted to have
#: actually changed the source, so a rename cannot turn the inverted run into a
#: silent no-op that passes for the wrong reason.
GUARD_CALL = "        guard.wrap(strategy, spec_strategy_id=name)"

_PROBE = """
import asyncio
import sys
from datetime import datetime, timezone

import structlog
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.live.data_engine import LiveDataEngine
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.live_session_node import materialise_strategy
from src.core.live_strategy_guard import StrategyGuard
from src.models.session import SessionSpec, StrategySpec

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
BAR_TYPE = BarType.from_str(AAPL_1MIN)
# Four clean closes then a zero: a real `sma_crossover` at fast=2/slow=3
# crosses down into `_generate_sell_signal` and divides by the zero close at
# `sma_crossover.py:150`. A real failure, not a monkeypatched `raise`.
CLOSES = ["90.00", "95.00", "100.00", "105.00", "0.00"]

SEEN = {"sma_crossover": 0, "momentum": 0}


def make_bar(index, close):
    price = Price.from_str(close)
    return Bar(
        bar_type=BAR_TYPE,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=index * 60_000_000_000,
        ts_init=index * 60_000_000_000,
    )


def spec():
    return SessionSpec(
        strategies=(
            StrategySpec(
                strategy_id="sma_crossover",
                parameters={"fast_period": 2, "slow_period": 3},
                bar_types=(AAPL_1MIN,),
            ),
            StrategySpec(strategy_id="momentum", parameters={}, bar_types=(AAPL_1MIN,)),
        )
    )


async def wait_for(condition, what, deadline_seconds=30.0):
    # Deadline-polled, never a bare sleep (review fix, 2026-08-23): on a loaded
    # CI worker a fixed sleep loses the race against the engine's queue task,
    # and this probe's AssertionError is indistinguishable from the regression
    # it exists to detect.
    now = asyncio.get_running_loop().time
    deadline = now() + deadline_seconds
    while not condition():
        assert now() < deadline, f"timed out waiting for {what}"
        await asyncio.sleep(0.01)


async def main():
    loop = asyncio.get_running_loop()
    clock = LiveClock()
    trader_id = TraderId("TESTER-000")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache(database=None)
    # Without the instrument, `SMAMomentum.on_start` finds `cache.instrument(...)`
    # is None, calls `self.stop()` and returns BEFORE `subscribe_bars` -- the
    # survivor would never subscribe and the sibling assertion would be untestable.
    cache.add_instrument(TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ"))
    portfolio = Portfolio(msgbus, cache, clock)

    # DEFAULTS on purpose: graceful_shutdown_on_exception=False, so this proves
    # the per-strategy guard rather than AC #6's engine-level backstop.
    engine = LiveDataEngine(
        loop=loop, msgbus=msgbus, cache=cache, clock=clock, config=LiveDataEngineConfig()
    )
    assert engine.graceful_shutdown_on_exception is False, "the probe lost its whole point"

    guard = StrategyGuard(
        log=structlog.get_logger("probe"), time_source=lambda: datetime.now(timezone.utc)
    )
    strategies = []
    for name, strategy_spec in zip(["sma_crossover", "momentum"], spec().strategies):
        strategy = materialise_strategy(strategy_spec)
        strategy.register(trader_id, portfolio, msgbus, cache, clock)

        def count(strategy=strategy, name=name):
            base = strategy.on_bar

            def on_bar(bar):
                SEEN[name] += 1
                return base(bar)

            return on_bar

        strategy.on_bar = count()
        guard.wrap(strategy, spec_strategy_id=name)
        strategies.append(strategy)

    engine.start()
    for strategy in strategies:
        strategy.start()
    await wait_for(lambda: all(s.is_running for s in strategies), "every strategy to start")

    for index, close in enumerate(CLOSES):
        engine.process(make_bar(index, close))
        # `momentum` is registered last, so its counter advancing proves the
        # whole synchronous dispatch of this bar completed.
        await wait_for(
            lambda index=index: SEEN["momentum"] >= index + 1, f"bar {index} to dispatch"
        )

    print("bars", SEEN["sma_crossover"], SEEN["momentum"], flush=True)
    print("states", strategies[0].state.name, strategies[1].state.name, flush=True)
    print("contained", [f.error_type for f in guard.failures], flush=True)
    print("queue_alive", not engine.get_data_queue_task().done(), flush=True)

    assert SEEN["momentum"] == len(CLOSES), "the sibling was starved of a bar"
    assert strategies[0].state.name == "DEGRADED", "the raiser was not isolated"
    assert strategies[1].state.name == "RUNNING", "the sibling was disturbed"
    assert [f.error_type for f in guard.failures] == ["DivisionByZero"]
    assert not engine.get_data_queue_task().done(), "the data queue task died"

    engine.stop()
    await asyncio.sleep(0.1)
    print("SURVIVED", flush=True)


asyncio.run(main())
sys.exit(0)
"""


def _run(probe: str, timeout: float = 240.0) -> subprocess.CompletedProcess:
    """Run the probe in a fresh interpreter.

    ``subprocess.run(capture_output=True)`` rather than a hand-rolled ``Popen``
    read loop: it drains both pipes concurrently through ``communicate()``, so
    Story 2.6's ``subprocess.PIPE`` deadlock — ``TradingNode``'s ~100 banner
    lines filling the buffer while the parent waits — cannot occur here. The
    ``timeout`` is itself an assertion; there is no ``pytest-timeout`` in this
    repo.
    """
    return subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestARaisingStrategyDoesNotEndTheProcess:
    """AC #1 — proven by return code, not by a log assertion."""

    def test_the_guarded_process_exits_zero_and_says_it_survived(self):
        result = _run(_PROBE)

        assert result.returncode == 0, (
            f"the process died (rc={result.returncode}) — a raising strategy still reaches "
            "LiveDataEngine._handle_queue_exception's os._exit(1)\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert SURVIVED in result.stdout.splitlines(), (
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_the_sibling_received_every_bar_including_the_one_that_raised(self):
        """AC #2, at the only tier where a real ``LiveDataEngine`` is involved."""
        result = _run(_PROBE)

        assert result.returncode == 0, result.stderr
        bars = [line for line in result.stdout.splitlines() if line.startswith("bars ")]
        assert bars, f"stdout:\n{result.stdout}"
        assert bars[-1] == "bars 5 5"

    def test_the_raiser_is_degraded_and_the_queue_task_is_still_alive(self):
        """The queue task surviving is why ``run()`` never returns and the
        session keeps serving — measured in finding #4.
        """
        result = _run(_PROBE)

        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert "states DEGRADED RUNNING" in lines
        assert "queue_alive True" in lines
        assert "contained ['DivisionByZero']" in lines


class TestTheProbeCanActuallyFail:
    """The permanent mutation proof — mutations #1 and #3.

    Without this, a probe whose guard call was deleted would keep AC #1 green
    forever. This is the inverted run that a ``TestLiveNode`` double can never
    give, because the double has no message bus, no engine and no kernel: it is
    the *only* evidence in the repo that the guard is what keeps the process
    alive rather than something else in the arrangement.
    """

    def test_without_the_guard_the_same_probe_dies_silently(self):
        broken = _PROBE.replace(GUARD_CALL, "        pass  # guard removed")
        assert broken != _PROBE, (
            "the meta-test no longer patches the probe — GUARD_CALL has drifted from the "
            "probe's source and this test is now proving nothing"
        )

        result = _run(broken)

        assert result.returncode == 1, (
            f"expected rc=1 from the uncontained run, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert SURVIVED not in result.stdout.splitlines()

    def test_the_uncontained_death_is_silent_which_is_why_rc_is_the_assertion(self):
        """``os._exit`` skips ``atexit``, every ``finally`` and the async Rust
        log flush. The explanatory ERROR line survived **4 of 20** measured runs,
        so no test may depend on it — stated here rather than asserted as a
        percentage.
        """
        broken = _PROBE.replace(GUARD_CALL, "        pass  # guard removed")

        result = _run(broken)

        assert result.returncode == 1
        # The traceback Python would normally print is *not* there: the process
        # was terminated below the interpreter, not by an unhandled exception.
        assert "Traceback (most recent call last)" not in result.stderr


class TestADegradedStrategyIsSkippedAtTeardown:
    """AC #7's disclosed consequence, pinned: a ``DEGRADED`` strategy is
    skipped at session teardown and its ``on_stop()`` never runs — Story 3.1
    must revisit this once it removes ``sma_crossover.on_stop()``'s flatten.

    Today the skip is strictly **safer**: ``on_stop()`` still calls
    ``close_all_positions()``, and skipping it is what stops an unrelated
    ``on_bar`` bug from manufacturing an exit (NFR14, AR43). After Story 3.1
    the same skip inverts into a leak — no ``unsubscribe_bars``, no
    strategy-owned cleanup — and this test is the tripwire that says so. The
    mechanism is ``Trader.stop_strategy()`` / ``Trader._stop()`` guarding on
    ``is_running``, which means ``state == RUNNING`` **exactly**
    (``common/component.pyx:1757-1767``), and ``degrade()`` leaves the
    strategy at ``DEGRADED`` where ``is_running`` is ``False``.

    A real ``Trader`` (via ``BacktestEngine``, this tier's cheapest source of
    one — fine here because the fact under pin is ``Trader``'s FSM guard, not
    the live queue's ``os._exit``), never the ``TestLiveNode`` double: pinning
    the double's teardown would pin our own stub.
    """

    def test_trader_teardown_never_reaches_a_degraded_strategys_on_stop(self):
        from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
        from nautilus_trader.config import LoggingConfig, StrategyConfig
        from nautilus_trader.trading.strategy import Strategy

        stopped = []

        class Recording(Strategy):
            def on_stop(self):
                stopped.append(str(self.id))

        engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
        strategy = Recording(StrategyConfig())
        engine.add_strategy(strategy)
        engine.trader.start()
        assert strategy.is_running

        strategy.degrade()
        assert strategy.state.name == "DEGRADED"
        assert strategy.is_running is False

        engine.trader.stop_strategy(strategy.id)
        engine.trader.stop()

        assert stopped == [], "teardown reached a DEGRADED strategy's on_stop()"
        assert strategy.state.name == "DEGRADED"
