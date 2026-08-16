"""Integration tests for TradingNode construction and LogGuard registration.

``--forked`` is mandatory, not a formality, for two independent reasons: the
Nautilus C logging subsystem is process-global and single-init, and the IB
adapter keeps module-level global caches (``IB_CLIENTS``,
``IB_INSTRUMENT_PROVIDERS``, ``GATEWAYS`` in
``nautilus_trader/adapters/interactive_brokers/factories.py``) that would leak
between tests sharing a process. Run via ``make test-integration``.

Both tests declare their process-state precondition explicitly and skip rather
than fail when it does not hold — without that, running this file *unforked*
(``uv run pytest tests/integration/core/test_live_node_lifecycle.py``, the
obvious thing to type when debugging one file) fails the second test with a
bare ``assert None is not None``, because the first test has already claimed
the C logging subsystem for the process.

No automated test here calls ``node.build()`` or ``node.run()`` — ``build()``
runs the factories, and ``get_cached_ib_client`` calls ``client.start()``,
which opens a socket to the gateway. Broker-dependent behaviour is
operator-verified instead (NFR32/NFR33) in
``docs/qa/phase3-live-verification.md``.

Collection-order hazard, RESOLVED 2026-08-11 (see deferred-work.md, "Deferred
from: story-1.3"): running the whole ``tests/integration`` tree in one
``pytest -n auto --forked`` invocation used to crash these two tests with
SIGTRAP if ``tests/integration/api/test_trades_api.py`` landed in the same
xdist worker — that file's ``from src.api.web import app`` ran Nautilus
``init_logging()`` as an import-time side effect in the shared worker process,
before any per-test fork, corrupting the native logging/async state every
later ``--forked`` child inherited. The same mechanism was also crashing 21
``BacktestEngine`` tests elsewhere in the tier, which earlier triage had
misread as an unrelated "pre-existing baseline".

``src/api/web.py`` now claims logging in a FastAPI ``lifespan`` hook instead,
so importing it is inert and these two tests run for real rather than
skipping. ``tests/component/api/test_web_app_logging.py`` guards the
regression.
"""

import subprocess
import sys

import pytest
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig

from src.config import IBKRSettings
from src.core.live_bar_observer import LiveBarObserver, build_bar_observer_config
from src.core.live_node_builder import build_trading_node, build_trading_node_config
from src.utils.logging import get_nautilus_log_guard

TRADER_ID = "PAPER-a1b2c3d4"


def _settings() -> IBKRSettings:
    """Settings that pass the gate; no real gateway is contacted in these tests.

    Every field the module under test reads is passed as an init kwarg.
    ``_env_file=None`` disables only the dotenv *file* — ``os.environ`` remains
    an active pydantic-settings source, so anything left unpinned here could
    still be supplied by the developer's or CI shell.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode="paper",
        ibkr_port=4002,
        ibkr_host="127.0.0.1",
        tws_account="DU4076626",
        ntrader_real_money_account="",
        ibkr_live_client_id=10,
        ibkr_client_id=1,
        ibkr_read_only=True,
        ibkr_connection_timeout=300,
        ibkr_request_timeout=60,
    )


def _assert_factories_registered(node) -> None:
    """AC #1 — the factories are registered under the adapter's own key.

    Worth asserting because Nautilus fails a *missing* registration silently:
    ``node_builder.py:230-232`` logs an error and ``continue``s rather than
    raising, so omitting a line — or transposing the two near-identical
    registration calls — would otherwise surface only during a live run.
    """
    builder = node._builder
    assert builder._data_factories[IB] is InteractiveBrokersLiveDataClientFactory
    assert builder._exec_factories[IB] is InteractiveBrokersLiveExecClientFactory


class TestCoexistenceWithBacktestEngine:
    """AC #4 — a TradingNode built after a BacktestEngine does not double-init."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_node_builds_without_panic_after_a_backtest_engine_claimed_logging(self):
        # Arrange — the engine claims the C logging subsystem first. `async def`
        # (asyncio_mode=auto) gives this test a running event loop: TradingNode
        # resolves asyncio.get_event_loop() internally and expects one to be
        # running, exactly as the Epic 2 runner will provide in production.
        if is_logging_initialized():
            pytest.skip("requires a process where Nautilus logging is not yet initialised")

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                trader_id="BACKTESTER-001",
                logging=LoggingConfig(log_level="ERROR"),
            )
        )
        try:
            assert is_logging_initialized()

            # Act — no panic, no abort
            node = build_trading_node(_settings(), trader_id=TRADER_ID)

            # Assert — NautilusKernel guards its own init (kernel.py:190); when
            # the engine already owns the subsystem the second component's
            # kernel returns None rather than a second guard.
            assert node is not None
            assert node.kernel.get_log_guard() is None

            # And the engine-first path leaves the module-global guard alone —
            # the branch that skips registration when there is nothing to
            # register is otherwise never observed by any test.
            assert get_nautilus_log_guard() is None

            _assert_factories_registered(node)
        finally:
            engine.dispose()


class TestLogGuardRegistrationOnNodeFirst:
    """AC #4 — building the node first registers its own guard."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_node_first_registers_its_own_log_guard(self):
        # Arrange — this test is only meaningful in a process where nothing has
        # claimed C logging yet. Stated and checked rather than assumed: unforked,
        # the coexistence test above runs first and would otherwise make this
        # fail on a bare `assert None is not None`.
        if is_logging_initialized():
            pytest.skip("requires a process where Nautilus logging is not yet initialised")
        assert get_nautilus_log_guard() is None

        # Act
        node = build_trading_node(_settings(), trader_id=TRADER_ID)

        # Assert — the node created the subsystem, so it owns a real guard, and
        # set_nautilus_log_guard registered that same object
        guard = node.kernel.get_log_guard()
        assert guard is not None
        assert get_nautilus_log_guard() is guard

        _assert_factories_registered(node)


#: Builds a node, disposes it, then builds a *second* one — all in one fresh
#: interpreter. Run as a child process on purpose: the claim is about what a
#: process is left holding after shutdown, and by the time this module executes,
#: the pytest worker has already built nodes for the tests above. Only a new
#: interpreter can answer it.
#:
#: The loop is created and owned explicitly rather than via ``asyncio.run()``,
#: mirroring ``live_check_driver._drive``. That is not stylistic:
#: ``TradingNode.dispose()`` calls ``loop.stop()`` whenever it finds the loop
#: running (``live/node.py:451-458``), so disposing from *inside* a coroutine
#: kills the loop under ``asyncio.run()``'s own cleanup and the child dies with
#: ``RuntimeError: Event loop stopped before Future completed``. Verified.
_SECOND_NODE_PROBE = """
import asyncio

from src.config import IBKRSettings
from src.core.live_node_builder import build_trading_node


def settings():
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode="paper",
        ibkr_port=4002,
        ibkr_host="127.0.0.1",
        tws_account="DU4076626",
        ntrader_real_money_account="",
        ibkr_live_client_id=10,
        ibkr_client_id=1,
        ibkr_read_only=True,
        ibkr_connection_timeout=300,
        ibkr_request_timeout=60,
    )


def build_and_dispose(trader_id):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        node = build_trading_node(settings(), trader_id=trader_id, loop=loop)
        node.dispose()
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    return loop


first = build_and_dispose("PAPER-first001")
second = build_and_dispose("PAPER-second02")

assert first is not second, "the second build reused the first loop"
assert first.is_closed(), "the first node's event loop was left open"
assert second.is_closed(), "the second node's event loop was left open"
assert not first.is_running() and not second.is_running()

print("SECOND_NODE_BUILT")
"""


class TestShutdownLeavesNothingBehind:
    """AC #6 — clean shutdown, and a second node is buildable in a fresh process.

    ``BacktestEngine`` is single-use: it cannot be reused after a run. This test
    pins the corresponding claim for the live path — that the constraint is not
    inherited, so a process can build a node, shut it down, and build another.

    Nothing here contacts a broker: ``build_trading_node`` returns the node
    *unbuilt*, and it is ``node.build()`` — not construction — that runs the IB
    factories and opens a socket (NFR32/NFR33).
    """

    @pytest.mark.integration
    def test_a_second_node_is_buildable_after_the_first_shuts_down(self):
        result = subprocess.run(
            [sys.executable, "-c", _SECOND_NODE_PROBE],
            capture_output=True,
            text=True,
            # A leaked non-daemon ThreadPoolExecutor or an unclosed loop hangs at
            # interpreter exit rather than raising, so the timeout *is* the
            # assertion for "the process exits".
            timeout=180,
        )

        assert result.returncode == 0, (
            f"the probe process did not exit cleanly (rc={result.returncode})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "SECOND_NODE_BUILT" in result.stdout, (
            "a second TradingNode could not be built after the first was disposed\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    @pytest.mark.integration
    def test_the_probe_can_actually_fail(self):
        """Meta-test: a probe that stopped proving anything must not pass silently.

        Mirrors ``tests/component/api/test_web_app_logging.py``. Without this, a
        probe whose assertions were deleted would keep this AC green forever.
        """
        broken = _SECOND_NODE_PROBE.replace(
            'assert first.is_closed(), "the first node\'s event loop was left open"',
            'assert not first.is_closed(), "inverted on purpose"',
        )
        assert broken != _SECOND_NODE_PROBE, "the meta-test no longer patches the probe"

        result = subprocess.run(
            [sys.executable, "-c", broken],
            capture_output=True,
            text=True,
            timeout=180,
        )

        assert result.returncode != 0
        assert "SECOND_NODE_BUILT" not in result.stdout


class TestBarObserverReachesTheNode:
    """Story 1.5 AC #3 — the declarative actor wiring survives kernel construction.

    ``ImportableActorConfig`` is resolved by the kernel, not by the builder, so a
    typo'd dotted path or a config field the actor cannot accept only surfaces
    when a node is actually constructed. That is this test's whole reason to
    exist at the integration tier; nothing here contacts a broker.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_a_configured_observer_is_instantiated_and_registered_on_the_trader(self):
        # Arrange
        if is_logging_initialized():
            pytest.skip("requires a process where Nautilus logging is not yet initialised")

        settings = _settings()
        bar_types = ["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]
        observer_config = build_bar_observer_config(settings, bar_types)

        # Act
        node = build_trading_node(
            settings,
            trader_id=TRADER_ID,
            bar_types=bar_types,
            bar_observer=observer_config,
        )

        # Assert — the kernel resolved the dotted paths and built the actor
        actors = node.trader.actors()
        assert len(actors) == 1
        observer = actors[0]
        assert isinstance(observer, LiveBarObserver)
        assert [str(bar_type) for bar_type in observer.bar_types] == bar_types

        # The observer subscribes to exactly the instruments the data client was
        # told to load. Asserted against the config rather than the node —
        # ``TradingNode`` exposes no accessor for the config it was built from,
        # and reaching into a private attribute would test Nautilus internals
        # rather than this wiring.
        config = build_trading_node_config(
            settings,
            trader_id=TRADER_ID,
            bar_types=bar_types,
            bar_observer=observer_config,
        )
        load_ids = config.data_clients[IB].instrument_provider.load_ids
        assert set(load_ids) == {str(bar_type.instrument_id) for bar_type in observer.bar_types}

        _assert_factories_registered(node)
