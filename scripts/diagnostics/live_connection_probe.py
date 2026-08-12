"""Connection-loss detection diagnostic probe (Story 1.6).

Proves, against a real IBKR paper Gateway, that:

1. a live socket alone does **not** grant trading permission — the monitor sits
   in ``recovering`` until state is explicitly confirmed re-established (NFR10);
2. confirming recovery grants it;
3. a genuine disconnect withdraws it and emits ``connection.lost`` (FR6).

Step 3 is obtained by stopping the node — a real, clean disconnect observed
through the same reader a running session would poll. **Nothing is killed, no
process is signalled, no order is submitted, and no subscription is made.** The
Layer 1 gate runs first (inside ``build_trading_node``), so a non-paper
configuration is refused before any socket is opened.

This is a diagnostic, not the runner: it does not own a session's lifecycle.
``live_session_runner.py`` (Epic 2, Story 2.5) does. It is also not
``live_node_probe.py`` (Story 1.3, Procedure P1), which verifies clean shutdown.

Usage::

    uv run python scripts/diagnostics/live_connection_probe.py [--hold-seconds 3]

Preconditions:
    - IB Gateway or TWS running on the configured paper port (see .env)
    - .env has TWS_ACCOUNT, IBKR_PORT, IBKR_TRADING_MODE=paper set correctly
    - No other process is holding IBKR_LIVE_CLIENT_ID on the Gateway

Prints a final ``RESULT: ok|fail ...`` line to stdout for easy parsing. On
failure the full traceback goes to stderr, so the parseable line stays clean.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
# Self-sufficient rather than requiring `PYTHONPATH=.`: the repo root is
# derived from this file's location, so the probe runs identically from any
# working directory. Must precede the `src.*` imports below, hence the E402s.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS  # noqa: E402

from src.config import IBKRSettings  # noqa: E402
from src.core.live_connection_monitor import (  # noqa: E402
    ConnectionMonitor,
    ConnectionState,
)
from src.core.live_gate import GateFlags  # noqa: E402
from src.core.live_node_builder import (  # noqa: E402
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node,
    read_ibkr_connection_status,
)

# A fixed diagnostic identity — not a derivation pattern. The real
# PAPER-<short-session-id> scheme belongs to Epic 2's session (AR10).
PROBE_TRADER_ID = "PAPER-PROBE0001"
PROBE_SESSION_ID = "probe-connection-0001"

_POLL_SECONDS = 0.25
# How long to wait for the adapter's flags to clear after node.stop(). The IB
# client's own teardown runs as an asyncio task, so the flags clear shortly
# after the node's run task returns rather than synchronously with it.
_DISCONNECT_TIMEOUT_SECONDS = 30.0
# How long to wait for the run task to finish after cancelling it.
_SHUTDOWN_JOIN_SECONDS = 30.0
# Bounds `node.build()`. The adapter reconnects *indefinitely* by default
# (`client.py:135-137`: IB_MAX_CONNECTION_ATTEMPTS unset -> 0 -> indefinite),
# and `client.start()` drives that retry loop synchronously via
# `run_until_complete` because the probe's loop is not running yet — so an
# unreachable Gateway hangs the build forever, before the try/finally, with no
# RESULT line. A diagnostic must fail rather than wait.
_BUILD_CONNECTION_ATTEMPTS = "3"


class ProbeError(RuntimeError):
    """The probe reached the gateway path but the expected state never arrived."""


async def _await_connected(node, run_task: asyncio.Task, timeout: float) -> None:
    """Block until both engines report connected, or fail loudly.

    ``kernel.start_async()`` does not raise when a connection fails
    (``system/kernel.py:1012-1013`` logs a warning and returns), so waiting on
    an observable is the only honest way to know the node reached the Gateway.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run_task.done():
            run_task.result()
            raise ProbeError("node stopped running before it reported connected")
        if node.kernel.data_engine.check_connected() and node.kernel.exec_engine.check_connected():
            return
        await asyncio.sleep(_POLL_SECONDS)

    raise ProbeError(
        f"engines did not connect within {timeout}s — "
        "is the Gateway running on the configured paper port?"
    )


def _ib_socket_flag_is_set(settings: IBKRSettings) -> bool:
    """Whether the adapter still reports a live socket, specifically.

    Local rather than reaching into ``live_node_builder``'s private helper: the
    production reader deliberately collapses socket and readiness into one
    ``ConnectionStatus``, and this probe is the one caller that needs to tell
    them apart. Fail-closed in the same direction — anything unreadable counts
    as disconnected, which is what this poll is waiting for anyway.
    """
    client = IB_CLIENTS.get((settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id))
    if client is None:
        return False
    try:
        return bool(client._is_ib_connected.is_set())
    except Exception:  # noqa: BLE001 - an unreadable flag is not a live socket
        return False


async def _await_socket_disconnect(settings: IBKRSettings, timeout: float) -> None:
    """Block until the adapter's *socket* flag clears, not merely readiness.

    The distinction is the whole point of this procedure. ``_stop_async()``
    clears ``_is_client_ready`` first (``client/client.py:266``), while
    ``_is_ib_connected`` clears later and indirectly, via ``eclient.disconnect()``
    → ``connectionClosed`` → ``process_connection_closed``
    (``client/connection.py:241``). Waiting on ``ConnectionStatus.connected``
    alone would return on the readiness flag and certify "readiness cleared"
    while presenting it as evidence of a genuine disconnect.

    Neither flag settles synchronously with ``node.stop()``: the client's
    teardown is scheduled as a task by ``Component.stop()``, so polling here is
    also what drives the loop that lets it run.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _ib_socket_flag_is_set(settings):
            return
        await asyncio.sleep(_POLL_SECONDS)

    raise ProbeError(
        f"the ib socket flag was still set {timeout}s after node.stop() — "
        "the reader is not seeing the adapter's disconnect"
    )


def _build_clients(node) -> None:
    """Build the node's clients under a bounded connection-retry budget.

    ``node.build()`` runs the IB factories, and ``get_cached_ib_client`` calls
    ``client.start()``, which — with the probe's loop not yet running — drives
    the adapter's reconnect loop synchronously through ``run_until_complete``.
    That loop is indefinite unless ``IB_MAX_CONNECTION_ATTEMPTS`` is set, and it
    never consults ``IBKR_CONNECTION_TIMEOUT``. ``setdefault`` so an operator who
    has already chosen a budget keeps it.
    """
    os.environ.setdefault("IB_MAX_CONNECTION_ATTEMPTS", _BUILD_CONNECTION_ATTEMPTS)
    node.build()


def _shutdown(node, run_task: asyncio.Task | None, loop: asyncio.AbstractEventLoop) -> list[str]:
    """Stop, dispose and close, reporting problems rather than raising.

    Runs from a ``finally``, so it must never mask the original failure. Each
    step is guarded independently: a node that failed to connect still holds a
    socket and the kernel's ``ThreadPoolExecutor``, whose non-daemon threads
    would otherwise be joined at interpreter exit and hang the process.
    """
    problems: list[str] = []

    if node is None:
        if not loop.is_closed():
            loop.close()
        return problems

    try:
        # `is_running()` is a method on TradingNode, not a property. The happy
        # path already stopped the node to produce its disconnect, so this only
        # fires when the run was cut short.
        if node.is_running():
            node.stop()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"stop: {type(exc).__name__}: {exc}")

    if run_task is not None:
        cancelled = not run_task.done()
        if cancelled:
            run_task.cancel()
        try:
            # Bounded, like every other wait in this file. `run_async()` sits on
            # an `asyncio.gather` over the engines' queue tasks; a task that
            # swallows `CancelledError` would otherwise hang the probe forever
            # holding the live client id, with no RESULT line ever printed.
            loop.run_until_complete(asyncio.wait_for(run_task, _SHUTDOWN_JOIN_SECONDS))
        except asyncio.CancelledError:
            if not cancelled:
                problems.append("run_task: cancelled without being asked to stop")
        except TimeoutError:
            problems.append(
                f"run_task: still running {_SHUTDOWN_JOIN_SECONDS}s after being cancelled"
            )
        except Exception as exc:  # noqa: BLE001 - already reported via the RESULT line
            problems.append(f"run_task: {type(exc).__name__}: {exc}")

    try:
        node.dispose()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"dispose: {type(exc).__name__}: {exc}")

    if not loop.is_closed():
        loop.close()

    return problems


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeError(message)


def _run(hold_seconds: int) -> str:
    """Drive the node's lifecycle on a loop this function owns end to end.

    ``TradingNode.dispose()`` calls ``loop.stop()`` whenever it finds the loop
    running (``nautilus_trader/live/node.py:451-458``), which is fatal if called
    from inside a coroutine still executing on that loop. Driving everything
    through explicit ``run_until_complete()`` calls keeps ``stop()``/``dispose()``
    on a loop that is not running, exactly as Nautilus expects. (This is Story
    1.3's live finding, reused rather than rediscovered.)
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = IBKRSettings()
    monitor = ConnectionMonitor(session_id=PROBE_SESSION_ID)
    node = None
    run_task: asyncio.Task | None = None

    try:
        print(
            f"[probe] building node host={settings.ibkr_host} port={settings.ibkr_port} "
            f"client_id={settings.ibkr_live_client_id} trader_id={PROBE_TRADER_ID}",
            flush=True,
        )
        # Inside the try: from here on a kernel exists, and with it a non-daemon
        # ThreadPoolExecutor that only `dispose()` tears down. A failure between
        # here and the `finally` used to skip shutdown entirely — leaving the
        # socket open, the loop unclosed and the client id reserved on the
        # Gateway, which is the exact hang `_shutdown` exists to prevent.
        node = build_trading_node(
            settings, trader_id=PROBE_TRADER_ID, cli_flags=GateFlags(), loop=loop
        )

        print("[probe] node built (config + LogGuard); building clients...", flush=True)
        _build_clients(node)

        print("[probe] starting node...", flush=True)
        run_task = loop.create_task(node.run_async())

        print(
            f"[probe] waiting up to {settings.ibkr_connection_timeout}s for engines to connect...",
            flush=True,
        )
        loop.run_until_complete(
            _await_connected(node, run_task, float(settings.ibkr_connection_timeout))
        )

        # 1. A live socket alone must NOT grant permission (NFR10).
        status = read_ibkr_connection_status(settings)
        print(f"[probe] status connected={status.connected} detail={status.detail}", flush=True)
        _expect(status.connected, f"reader did not see a live connection: {status.detail}")

        state = monitor.observe(status)
        print(
            f"[probe] observed -> state={state.value} permitted={monitor.trading_permitted}",
            flush=True,
        )
        _expect(state is ConnectionState.RECOVERING, f"expected recovering, got {state.value}")
        _expect(
            monitor.trading_permitted is False,
            "a live socket alone granted trading permission — NFR10 violated",
        )

        # 2. Only confirming re-established state grants it — and the
        #    confirmation takes its own fresh reading, so it cannot be granted
        #    against a socket that died since the poll above.
        state = monitor.confirm_state_reestablished(read_ibkr_connection_status(settings))
        print(
            f"[probe] confirmed -> state={state.value} permitted={monitor.trading_permitted}",
            flush=True,
        )
        _expect(state is ConnectionState.CONNECTED, f"expected connected, got {state.value}")
        _expect(monitor.trading_permitted is True, "confirmation did not grant permission")

        print(f"[probe] holding the connection for {hold_seconds}s...", flush=True)
        loop.run_until_complete(asyncio.sleep(hold_seconds))

        # 3. A genuine disconnect withdraws it. Stopping the node is a real,
        #    clean disconnect — no process is killed to produce it.
        print("[probe] stopping node to produce a real disconnect...", flush=True)
        node.stop()
        # Deliberately not awaiting `run_task` here. `run_async()` sits on an
        # `asyncio.gather` over the engines' queue tasks, and whether that
        # resolves on its own after a stop is not something this probe should
        # bet on — `_shutdown` cancels it, bounded, from the `finally`. Polling
        # for the disconnect drives the loop meanwhile, which is what lets the
        # IB client's teardown tasks run at all.
        loop.run_until_complete(_await_socket_disconnect(settings, _DISCONNECT_TIMEOUT_SECONDS))

        status = read_ibkr_connection_status(settings)
        print(f"[probe] status connected={status.connected} detail={status.detail}", flush=True)

        state = monitor.observe(status)
        downtime = monitor.downtime_seconds
        print(
            f"[probe] observed -> state={state.value} permitted={monitor.trading_permitted} "
            f"downtime={'n/a' if downtime is None else f'{downtime:.2f}s'}",
            flush=True,
        )
        _expect(state is ConnectionState.LOST, f"expected lost, got {state.value}")
        _expect(
            monitor.trading_permitted is False,
            "trading remained permitted through a disconnect — FR6 violated",
        )
        # The evidence line must certify a *socket* disconnect. Waiting on
        # `ConnectionStatus.connected` alone would return the moment the
        # readiness flag cleared and quietly present that as the same thing.
        _expect(
            "socket" in status.detail,
            f"the disconnect was observed on the readiness flag, not the socket: {status.detail}",
        )
    finally:
        print("[probe] disposing node...", flush=True)
        problems = _shutdown(node, run_task, loop)
        print(f"[probe] disposed loop.is_closed={loop.is_closed()}", flush=True)
        if problems:
            print(f"[probe] shutdown problems: {'; '.join(problems)}", file=sys.stderr, flush=True)

    if problems:
        raise ProbeError(f"unclean shutdown: {'; '.join(problems)}")
    return f"final_state={monitor.state.value} permitted={monitor.trading_permitted}"


def _positive_seconds(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--hold-seconds must be at least 1 (got {value}); asyncio.sleep(<=0) "
            "returns immediately, so the connection would never actually be held"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hold-seconds",
        type=_positive_seconds,
        default=3,
        help="Seconds to hold the permitted connection before dropping it (default: 3, minimum: 1)",
    )
    args = parser.parse_args()

    # Loaded here, not at import time: load_dotenv() mutates os.environ
    # process-wide, which should not be a side effect of importing this module.
    # The path is explicit so the probe behaves identically from any cwd.
    load_dotenv(REPO_ROOT / ".env")

    t0 = time.monotonic()
    try:
        detail = _run(args.hold_seconds)
        print(
            f"RESULT: ok mode=connect-permit-drop {detail} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 0
    except GateRefusedError as e:
        print(
            f"RESULT: fail reason=gate_refused refusal={e.refusal.reason.value} "
            f"elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except LiveNodeConfigError as e:
        print(
            f"RESULT: fail reason=config_error msg={e} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        # Not caught by `except Exception`, so without this arm an operator's
        # Ctrl-C produces no RESULT line at all. Note what it does *not* do:
        # releasing the client id is `_run`'s `finally` -> `_shutdown`, which
        # runs on this path with or without this clause.
        print(
            f"RESULT: fail reason=interrupted elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 130
    except Exception as e:
        traceback.print_exc()
        print(
            f"RESULT: fail reason={type(e).__name__} msg={e!s} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
