"""Node plumbing for the one-shot connectivity check (Story 1.7).

Owns the mechanics ``live_check_driver`` sits on top of: owning an event loop and
handing it back, bounding ``node.build()`` so an unreachable gateway cannot hang
the command, waiting for both engines to report connected, finding the observer
the kernel instantiated, and tearing everything down without ever masking the
outcome that is already in flight.

Does not own: the check's *sequence* or its evidence (``live_check_driver``), the
outcome vocabulary and exit codes (``live_check`` — pure), or a session's
lifecycle (Epic 2's runner, AR38).

Split out of the driver so both stay inside CLAUDE.md's 500-line file limit
without deleting the explanations — every comment here records something read out
of the nautilus-trader 1.220.0 wheel or paid for by a live run, and the next
person to touch this needs them more than they need brevity.
"""

import asyncio
import os
import time

import structlog
from nautilus_trader.live.node import TradingNode

from src.config import IBKRSettings
from src.core.live_bar_observer import LiveBarObserver
from src.core.live_check import BrokerUnreachableError, LiveCheckError

logger = structlog.get_logger(__name__)

#: How often the connect wait re-reads the engines. Matches the probes.
POLL_SECONDS = 0.25

#: The retry budget installed around ``node.build()``.
#:
#: **One attempt, where the probes use three.** Each failed attempt costs about
#: 20s (a 15s ``managedAccounts`` wait plus the 5s ``_reconnect_delay``) and the
#: retry runs *inside* an uninterruptible ``run_until_complete``, so a budget of
#: three turns "the gateway is not listening" into a 70-second silence. A check
#: exists to report what it found, not to outlast a gateway restart; an operator
#: who wants the retry can re-run it, or set the variable themselves.
BUILD_CONNECTION_ATTEMPTS = "1"

#: How long to wait for the run task after cancelling it. Bounded like every
#: other wait here: ``run_async()`` sits on an ``asyncio.gather`` over the
#: engines' queue tasks, and a task that swallows ``CancelledError`` would
#: otherwise hang the command holding the live client id.
SHUTDOWN_JOIN_SECONDS = 30.0


def current_event_loop() -> asyncio.AbstractEventLoop | None:
    """The thread's current loop, or None when it has none (or cannot say).

    Read before the driver installs its own, so the ``finally`` can put it back.
    """
    try:
        return asyncio.get_event_loop_policy().get_event_loop()
    except Exception:  # noqa: BLE001 - no loop set is a normal state, not an error
        return None


def restore_event_loop(previous: asyncio.AbstractEventLoop | None) -> None:
    """Put the thread's loop back, leaving no closed loop installed.

    :func:`shutdown` closes the loop the driver created, and without this the
    thread would be left with that *closed* loop as its current one — which
    ``build_trading_node(loop=None)`` would then pick up, since ``TradingNode``
    falls back to ``asyncio.get_event_loop()``. Harmless for a one-shot process;
    not harmless for the component suite, which runs the check dozens of times
    per worker, nor for any in-process caller — Epic 2's runner being the obvious
    one.
    """
    asyncio.set_event_loop(previous if previous is not None and not previous.is_closed() else None)


def bounded_connection_attempts() -> str:
    """The retry budget to install, honouring an operator's own valid choice.

    ``os.environ.setdefault`` is *not* enough here, and the difference is a hang.
    The adapter reads ``int(os.getenv("IB_MAX_CONNECTION_ATTEMPTS", 0))`` and then
    ``_indefinite_reconnect = False if _max_connection_attempts else True``
    (``client/client.py:138-139``) — so an exported **``0``**, which is both the
    adapter's own default and the value an operator would naturally set meaning
    "keep trying", means *infinite*, and ``setdefault`` would leave it in place.
    An empty or non-numeric value makes ``int()`` raise deep inside the adapter.
    Only a value that parses to a positive int bounds anything; anything else is
    replaced.
    """
    configured = os.environ.get("IB_MAX_CONNECTION_ATTEMPTS")
    try:
        if configured is not None and int(configured) > 0:
            return configured
    except ValueError:
        pass
    return BUILD_CONNECTION_ATTEMPTS


def build_clients(node: TradingNode, settings: IBKRSettings, *, trader_id: str) -> None:
    """Build the node's clients under a bounded connection-retry budget.

    ``node.build()`` runs the IB factories, and ``get_cached_ib_client`` calls
    ``client.start()``, which — with the loop not yet running — drives the
    adapter's reconnect loop synchronously through ``run_until_complete``.
    """
    os.environ["IB_MAX_CONNECTION_ATTEMPTS"] = bounded_connection_attempts()
    logger.info(
        "live_check.building",
        host=settings.ibkr_host,
        port=settings.ibkr_port,
        client_id=settings.ibkr_live_client_id,
        trader_id=trader_id,
    )
    node.build()


def find_observer(node: TradingNode) -> LiveBarObserver:
    """Return the observer the kernel instantiated from the node config."""
    for actor in node.trader.actors():
        if isinstance(actor, LiveBarObserver):
            return actor
    raise LiveCheckError(
        "no LiveBarObserver was registered on the node, so the check has nothing to "
        "observe bars with"
    )


def endpoint(settings: IBKRSettings) -> str:
    """Where the check looked, for a message an operator can act on."""
    return f"{settings.ibkr_host}:{settings.ibkr_port} (client_id={settings.ibkr_live_client_id})"


async def await_connected(
    node: TradingNode,
    run_task: asyncio.Task,
    deadline: float,
    timeout: float,
    settings: IBKRSettings,
) -> None:
    """Block until both engines report connected, or call the broker unreachable.

    Polled rather than awaited on an exception, because **an unreachable gateway
    does not raise**: on exhausting its attempt budget the IB client logs
    ``"Max connection attempts reached, connection failed"``, calls ``_stop()``
    and *breaks out of its own loop* (``client/client.py:184-189``), and
    ``kernel.start_async()`` likewise logs and returns rather than raising
    (``system/kernel.py:1012-1013``). There is nothing to catch, so the only
    honest signal is the observable.

    ``check_connected()`` is the right observable *here* and the wrong one in
    ``read_ibkr_connection_status``: it is a lifecycle flag that answers "did the
    node finish connecting?", which is precisely this question, and it does not
    track liveness — which is why Story 1.6's reader exists. Do not swap one for
    the other in either direction.

    Args:
        deadline: A ``time.monotonic()`` instant, set by the caller *before*
            ``node.build()`` so the build's own connect attempt is spent from the
            same budget. It may already be in the past on entry — the loop then
            falls straight through to the refusal, which is correct: that time
            was spent trying to connect.
        timeout: The budget that produced ``deadline``, for the message only.
    """
    while time.monotonic() < deadline:
        if run_task.done():
            run_task.result()
            raise BrokerUnreachableError(
                f"the node stopped running before it reported connected to "
                f"{endpoint(settings)} — the gateway refused or dropped the connection"
            )
        if node.kernel.data_engine.check_connected() and node.kernel.exec_engine.check_connected():
            logger.info("live_check.connected", endpoint=endpoint(settings))
            return
        await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    raise BrokerUnreachableError(
        f"both engines did not report connected within {timeout:g}s at {endpoint(settings)} — "
        "is IB Gateway or TWS running on the configured paper port?"
    )


def shutdown(
    node: TradingNode | None,
    run_task: asyncio.Task | None,
    loop: asyncio.AbstractEventLoop,
) -> list[str]:
    """Stop, dispose and close, reporting problems rather than raising.

    Runs from a ``finally``, so it must never mask the original failure: a
    shutdown error replacing a gate refusal would leave the exit-code mapping
    with nothing to map, on the one path where the answer matters most. Each step
    is guarded independently, because a node that failed still holds a socket and
    the kernel's non-daemon ``ThreadPoolExecutor``, whose threads would otherwise
    be joined at interpreter exit and hang the process.

    Every guard here catches ``BaseException``, not ``Exception``. The reason is
    ``KeyboardInterrupt`` and the window is real: ``TradingNode.dispose()``
    busy-waits on ``time.sleep(0.1)`` for up to ``timeout_disconnection`` seconds
    (``live/node.py:409-424``). A Ctrl-C landing in that sleep while a
    ``GateRefusedError`` is already in flight would escape the ``finally``,
    replace the refusal, and turn **exit 3 into exit 1** — losing the one signal
    FR11 exists to provide, and skipping ``loop.close()`` on the way out.

    Only exception *types* are reported, never their messages: adapter and broker
    error text routinely embeds the account identifier (NFR26).
    """
    problems: list[str] = []
    if node is None:
        close_loop(loop)
        return problems

    try:
        if node.is_running():
            node.stop()
    except BaseException as exc:  # noqa: BLE001 - shutdown must never mask the outcome
        problems.append(f"stop: {type(exc).__name__}")

    problems.extend(join_run_task(run_task, loop))

    try:
        node.dispose()
    except BaseException as exc:  # noqa: BLE001 - shutdown must never mask the outcome
        problems.append(f"dispose: {type(exc).__name__}")

    close_loop(loop)
    return problems


def join_run_task(run_task: asyncio.Task | None, loop: asyncio.AbstractEventLoop) -> list[str]:
    """Cancel and join the run task, bounded.

    Bounded like every other wait here: ``run_async()`` sits on an
    ``asyncio.gather`` over the engines' queue tasks, and a task that swallows
    ``CancelledError`` would otherwise hang the command forever holding the live
    client id, with no report ever printed.
    """
    if run_task is None:
        return []

    cancelled = not run_task.done()
    if cancelled:
        run_task.cancel()
    try:
        loop.run_until_complete(asyncio.wait_for(run_task, SHUTDOWN_JOIN_SECONDS))
    except asyncio.CancelledError:
        if not cancelled:
            return ["run_task: cancelled without being asked to stop"]
    except TimeoutError:
        return [f"run_task: still running {SHUTDOWN_JOIN_SECONDS}s after being cancelled"]
    except BaseException as exc:  # noqa: BLE001 - already reported via the outcome
        return [f"run_task: {type(exc).__name__}"]
    return []


def close_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Close the loop, tolerating one Nautilus already closed for us."""
    try:
        if not loop.is_closed():
            loop.close()
    except BaseException:  # noqa: BLE001 - a failed close must not mask the outcome
        pass
