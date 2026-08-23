"""Integration proof: the re-arm after node:build survives real node
construction (Story 2.6, Task 4), and a second real signal force-exits
(Task 9).

Integration tier, ``--forked``: constructs a real ``TradingNode``, which
claims the Nautilus C logging subsystem and, per this story's Dev Notes
(Pre-verified finding #3), clobbers whatever signal handlers were armed
before it. This is the one fact a ``TestLiveNode`` double cannot prove,
because the double never constructs a kernel and therefore never clobbers
anything — a component-tier test of this exact claim would pass vacuously.
"""

import asyncio
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.config import IBKRSettings
from src.core.live_node_builder import build_trading_node
from src.core.live_session_signals import SessionStopSignals

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _NullLog:
    def info(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


def _settings() -> IBKRSettings:
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


@pytest.fixture
def snapshot_handlers():
    """Restore every handler in a ``finally`` — a leaked one poisons the rest
    of the worker (Testing standards, this story).
    """
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)}
    yield
    for sig, handler in before.items():
        signal.signal(sig, handler)


def _armed_signals() -> SessionStopSignals:
    return SessionStopSignals(log=_NullLog(), on_stop=lambda name: None)


class TestTheReArmSurvivesRealNodeConstruction:
    """AC #7 — after ``node:build`` returns, the runner's handler owns
    SIGINT/SIGTERM by name, not ``uvloop.Loop.__sighandler`` and not
    ``signal.default_int_handler``.
    """

    def test_sigint_and_sigterm_are_owned_by_our_handler_after_build(self, snapshot_handlers):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        signals = _armed_signals()
        node = None
        try:
            signals.arm(loop)
            node = build_trading_node(_settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
            signals.arm(loop)  # the re-arm under test: node construction just clobbered us

            assert signal.getsignal(signal.SIGINT) == signals._handle
            assert signal.getsignal(signal.SIGTERM) == signals._handle
        finally:
            if node is not None:
                node.dispose()
            signals.restore()
            if not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)

    def test_sigabrt_is_handed_back_to_the_os_not_left_with_nautilus(self, snapshot_handlers):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        signals = _armed_signals()
        node = None
        try:
            signals.arm(loop)
            node = build_trading_node(_settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
            signals.arm(loop)

            assert signal.getsignal(signal.SIGABRT) is signal.SIG_DFL
        finally:
            if node is not None:
                node.dispose()
            signals.restore()
            if not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)

    def test_without_the_post_build_rearm_the_kernel_still_owns_sigint(self, snapshot_handlers):
        """Mutation proof (Task 4's third RED bullet, Task 11's item (a)):
        skip the re-arm and the assertion above goes red — proving it is not
        a test that cannot fail.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        signals = _armed_signals()
        node = None
        try:
            signals.arm(loop)
            node = build_trading_node(_settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
            # No re-arm here — the mutation.

            assert signal.getsignal(signal.SIGINT) != signals._handle, (
                "the kernel's own handler should still own SIGINT without the re-arm — if this "
                "assertion fails, the re-arm test above is not proving what it claims to"
            )
        finally:
            if node is not None:
                node.dispose()
            signals.restore()
            if not loop.is_closed():
                loop.close()
            asyncio.set_event_loop(None)


#: A fresh-interpreter probe body, in the idiom `_run_probe` established in
#: `tests/integration/core/test_epic1_ac_node.py:141-150` — "process state is
#: knowable" there means a *child* process, so a real signal can be sent to it
#: and its exit code observed, which is the only place a force exit is
#: observable at all (Task 9). Prints "READY" once armed, then blocks; the
#: parent sends the signals and reads the return code.
_FORCE_EXIT_PROBE = """
import asyncio
import structlog

from src.config import IBKRSettings
from src.core.live_node_builder import build_trading_node
from src.core.live_session_signals import SessionStopSignals


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


loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
signals = SessionStopSignals(log=structlog.get_logger("probe"), on_stop=lambda name: None)
signals.arm(loop)
node = build_trading_node(settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
signals.arm(loop)
print("READY", flush=True)
loop.run_until_complete(asyncio.sleep(30))
print("SHOULD_NOT_REACH_HERE", flush=True)
"""

#: Task 11 mutation target (b): comment out `arm()`'s `signal.signal` half and
#: this probe's own assertion — "the signal fired while the loop was
#: stopped" — goes red, because `loop.add_signal_handler` alone loses every
#: signal delivered in that window (Pre-verified finding #2).
_SYNCHRONOUS_WINDOW_PROBE = """
import asyncio
import sys
import time
import structlog

from src.config import IBKRSettings
from src.core.live_node_builder import build_trading_node
from src.core.live_session_signals import SessionStopSignals


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


def _on_stop(name):
    print("ON_STOP", name, flush=True)


loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
signals = SessionStopSignals(log=structlog.get_logger("probe"), on_stop=_on_stop)
signals.arm(loop)
node = build_trading_node(settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
signals.arm(loop)
print("READY", flush=True)
# Synchronous, no loop iteration — the exact window Pre-verified finding #2
# measured a signal being lost in, for the unprotected Nautilus-only case.
time.sleep(3)
print("SLEPT", flush=True)
sys.stdout.flush()
"""


def _spawn_probe(body: str = _FORCE_EXIT_PROBE) -> tuple[subprocess.Popen, "queue.Queue[str]"]:
    """Start a probe with a background thread draining its output.

    ``TradingNode`` construction alone prints ~100 lines of banner and config
    (measured). Without continuous draining, ``subprocess.PIPE``'s pipe fills
    (macOS default ~64KiB) and the child blocks on a ``write()`` syscall
    before it ever processes a signal — a classic subprocess deadlock, not a
    behaviour of the code under test. ``stderr=STDOUT`` merges the force-exit
    announcement (written to stderr) into the same drained stream.
    """
    proc = subprocess.Popen(
        [sys.executable, "-c", body],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    lines: "queue.Queue[str]" = queue.Queue()

    def _drain() -> None:
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=_drain, daemon=True).start()
    return proc, lines


def _wait_for_ready(lines: "queue.Queue[str]", *, timeout: float = 10.0) -> None:
    """Block until the probe's own marker line, not a substring match.

    ⚠️ ``TradingNode`` construction logs its own component-readiness lines
    (``Cache: READY``, ``DataEngine: READY``, ...) — a substring check on
    ``"READY"`` matches those too and fires the test's signals while the node
    is still mid-construction, landing in exactly the narrow transient window
    ``NautilusKernel._setup_loop()`` briefly sets ``SIGINT`` to ``SIG_DFL``
    (Pre-verified finding #2). Measured: that false-positive match silently
    swallowed the "first" signal and made the two-signal test time out.
    """
    deadline = time.monotonic() + timeout
    seen: list[str] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"probe never printed READY; last lines: {seen[-5:]!r}")
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        seen.append(line)
        if line.strip() == "READY":
            return


class TestForceExitOnASecondRealSignal:
    """Task 9 — two real SIGINTs against a process running the production
    default ``force_exit`` (``os._exit``), sent with ``proc.send_signal``. The
    subprocess timeout *is* the assertion, per this story's Testing standards
    — there is no ``pytest-timeout`` in this repo.
    """

    def test_two_sigints_force_exit_with_code_one(self):
        proc, lines = _spawn_probe()
        collected: list[str] = []
        try:
            _wait_for_ready(lines)
            proc.send_signal(signal.SIGINT)
            time.sleep(0.3)  # let the first signal's on_stop() run before the second arrives
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=10)
            # Drain whatever the probe managed to say before it died.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    collected.append(lines.get(timeout=0.1))
                except queue.Empty:
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

        output = "".join(collected)
        assert returncode == 1, f"expected FORCE_EXIT_CODE (1), got {returncode}\n{output}"
        # Review fix, 2026-08-22: exit 1 ALONE cannot distinguish `os._exit(1)`
        # from a Python process dying on an unhandled exception — which is
        # exactly what the unguarded force-exit path used to produce on a
        # broken pipe. Assert the announcement too, so a traceback-death can no
        # longer pass as a force exit.
        assert "Force exit:" in output, (
            "the process exited 1 but never announced the force exit — it may have died from an "
            f"unhandled exception rather than reaching os._exit. Output:\n{output}"
        )

    def test_a_single_sigint_does_not_force_exit_within_the_window(self):
        """Non-vacuity for the test above: one signal alone must not trip the
        force-exit path — otherwise the assertion above would pass for the
        wrong reason.
        """
        proc, lines = _spawn_probe()
        try:
            _wait_for_ready(lines)
            proc.send_signal(signal.SIGINT)
            with pytest.raises(subprocess.TimeoutExpired):
                proc.wait(timeout=1.5)
        finally:
            proc.kill()
            proc.wait()


class TestASignalInTheSynchronousWindow:
    """AC #7's other cell: a signal delivered while the loop is **stopped**
    (``node:build``'s own synchronous connect attempt is exactly this
    window). Pre-verified finding #2 measured this signal **lost** for the
    unprotected Nautilus-only case; this proves ``SessionStopSignals`` does
    not lose it. This is also Task 11 mutation target (b)'s red test: drop
    ``arm()``'s ``signal.signal`` half and this test goes red.
    """

    def test_a_signal_sent_while_the_loop_is_stopped_is_observed(self):
        proc, lines = _spawn_probe(_SYNCHRONOUS_WINDOW_PROBE)
        try:
            _wait_for_ready(lines)
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

        assert returncode == 0, f"expected a clean exit, got {returncode}"
        collected = []
        while not lines.empty():
            collected.append(lines.get_nowait())
        assert any("ON_STOP SIGINT" in line for line in collected), (
            f"the signal sent while the loop was stopped was never observed; output: {collected}"
        )
