"""TradingNode build/start/stop diagnostic probe (Story 1.3).

Builds a TradingNode against the configured IBKR paper gateway via
``build_trading_node()``, builds its clients, waits for both engines to report
connected, runs it briefly, then stops and disposes it — exercising the
shutdown sequence AC #6 requires (no lingering event loop or socket, and a
fresh process can build a second node afterwards).

``RESULT: ok`` means the node actually reached the gateway. It is not enough to
report success merely because nothing raised: ``kernel.start_async()`` does
**not** raise when a connection fails (``system/kernel.py:1012-1013`` logs a
warning and returns), so a node that never reached the gateway would otherwise
exit 0 and be pasted into a QA log as evidence.

This is a diagnostic, not the runner: it does not own a session's lifecycle.
``live_session_runner.py`` (Epic 2, Story 2.5) does. Do not mistake one for
the other.

``--verify-account`` additionally runs Story 1.4's Layer 2 ``gate:account``
phase in the position AR39 puts it in — after connect, before anything that
looks like trading — and reports what the gateway said the account is. It is
opt-in so that Procedure P1, which this probe is the tool for, keeps exactly the
behaviour it was verified with.

``--read-broker-state`` (Story 4.1, Procedure P15) additionally asks IBKR what
the account actually holds — positions and ``TotalCashValue`` — through
``src.core.live_broker_state.read_broker_state``, and prints it masked. Strictly
a read: the probe adds no strategy, so nothing on this node can submit an order.
Also opt-in, so P1's and P2's documented output is unchanged without it.

``--reconcile`` (Story 4.2, Procedure P17a) additionally runs the session's own
``reconcile`` phase body — ``src.core.live_startup_reconcile.reconcile_at_startup`` —
against this node, after Nautilus's own reconciliation has finished (the trader
has started). Read-only against the broker: no strategy is added, and this
node's cache is in memory, so any broker-ward correction it makes is to a
throwaway cache, never to a session's Redis namespace. Its snapshot is taken
before the node runs, so every broker position the framework imported shows as
``resolution="framework"`` (the empty in-memory cache knew nothing); one it could
not import is corrected by the phase (``resolution="broker"``) or refused.
Requires ``--verify-account``: AR39 puts ``reconcile`` after ``gate:account``.

Usage::

    uv run python scripts/diagnostics/live_node_probe.py [--run-seconds 5] [--verify-account] \
        [--read-broker-state] [--reconcile]

Preconditions:
    - IB Gateway or TWS running on the configured paper port (see .env)
    - .env has TWS_ACCOUNT, IBKR_PORT, IBKR_TRADING_MODE=paper set correctly
      (the gate refuses anything else before a socket is ever opened)

Prints a final ``RESULT: ok|fail ...`` line to stdout for easy parsing. On
failure the full traceback goes to stderr, so the parseable line stays clean
while the diagnostic detail — the whole point of this tool — is preserved.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path

import structlog
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

from src.config import IBKRSettings  # noqa: E402
from src.core.live_account_gate import (  # noqa: E402
    gateway_reported_accounts,
    masked_accounts,
    verify_connected_account,
)
from src.core.live_broker_state import (  # noqa: E402
    BrokerStateUnavailableError,
    read_broker_state,
    render_broker_state,
)
from src.core.live_gate import GateFlags, GateRefusal  # noqa: E402
from src.core.live_node_builder import (  # noqa: E402
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node,
)
from src.core.live_session_node import (  # noqa: E402
    DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
    await_trader_started,
)
from src.core.live_startup_reconcile import (  # noqa: E402
    ReconciliationFailedError,
    capture_local_positions,
    reconcile_at_startup,
)

# A fixed diagnostic identity — not a derivation pattern. The real
# PAPER-<short-session-id> scheme belongs to Epic 2's session (AR10); this
# probe has no session, so it uses one constant value instead of inventing one.
PROBE_TRADER_ID = "PAPER-PROBE0001"

# Polling interval while waiting for the engines to report connected.
_CONNECT_POLL_SECONDS = 0.25


class ProbeError(RuntimeError):
    """The probe reached the gateway path but the node did not come up."""


class AccountGateRefused(RuntimeError):
    """Layer 2 refused the connected account.

    Distinct from ``GateRefusedError`` — which both layers raise — purely so the
    ``RESULT:`` line can tell an operator *which* layer refused. Layer 1 fires
    before a socket is opened; Layer 2 fires after the gateway has named an
    account, and the two want very different next actions.
    """

    def __init__(self, refusal: GateRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


async def _await_connected(node, run_task: asyncio.Task, timeout: float) -> None:
    """Block until both engines report connected, or fail loudly.

    Polls rather than sleeping a fixed interval because ``timeout_connection``
    defaults to 60s while a healthy local Gateway connects in well under a
    second — a fixed sleep would either tear the node down before the outcome
    was known, or make every probe run take a minute.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run_task.done():
            # Surfaces a run_async failure immediately, and re-raises it here
            # rather than letting it emerge during shutdown.
            run_task.result()
            raise ProbeError("node stopped running before it reported connected")
        if node.kernel.data_engine.check_connected() and node.kernel.exec_engine.check_connected():
            return
        await asyncio.sleep(_CONNECT_POLL_SECONDS)

    data_ok = node.kernel.data_engine.check_connected()
    exec_ok = node.kernel.exec_engine.check_connected()
    raise ProbeError(
        f"engines did not connect within {timeout}s "
        f"(data_connected={data_ok} exec_connected={exec_ok}) — "
        "is the Gateway running on the configured paper port?"
    )


def _shutdown(node, run_task: asyncio.Task, loop: asyncio.AbstractEventLoop) -> list[str]:
    """Stop, dispose and close, reporting problems rather than raising.

    Runs from a ``finally``, so it must never mask the original failure. Each
    step is independently guarded: a node that failed to connect still has a
    socket and a kernel ``ThreadPoolExecutor`` (``system/kernel.py:265-272``,
    torn down only inside ``dispose()``) whose non-daemon threads would
    otherwise be joined at interpreter exit and hang the process.
    """
    problems: list[str] = []

    try:
        node.stop()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"stop: {type(exc).__name__}: {exc}")

    if not run_task.done():
        run_task.cancel()
    try:
        loop.run_until_complete(run_task)
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001 - already reported via the RESULT line
        problems.append(f"run_task: {type(exc).__name__}: {exc}")

    try:
        node.dispose()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"dispose: {type(exc).__name__}: {exc}")

    if not loop.is_closed():
        loop.close()

    return problems


def _verify_account(node, settings: IBKRSettings, loop: asyncio.AbstractEventLoop) -> str:
    """Run the ``gate:account`` phase in the position AR39 puts it in.

    After ``node:connect``, before anything resembling trading. Driven through
    ``run_until_complete`` because ``verify_connected_account`` is a coroutine —
    on a refusal it awaits ``node.stop_async()``, which is the only way to
    actually bring the node down from inside a running loop.
    """
    print("[probe] verifying connected account (phase=gate:account)...", flush=True)
    # Read once and hand the same set to the gate. Reading again after the gate
    # returned would print a set the gate never judged: the adapter clears
    # `_account_ids` on degrade/disconnect, so a blip between the two calls would
    # put `accounts=` (empty, or changed) next to a `gate_account=paper` verdict
    # reached on different evidence.
    reported = gateway_reported_accounts(settings)
    try:
        decision = loop.run_until_complete(
            verify_connected_account(
                node, settings, cli_flags=GateFlags(), reported_accounts=reported
            )
        )
    except GateRefusedError as exc:
        raise AccountGateRefused(exc.refusal) from exc

    mode = decision.mode.value if decision.mode else "unknown"
    print(f"[probe] gate:account ok mode={mode} accounts={masked_accounts(reported)}", flush=True)
    return mode


def _read_broker_state(node, loop: asyncio.AbstractEventLoop) -> int:
    """Ask IBKR what the account holds (Story 4.1), and print it masked.

    Driven through ``run_until_complete`` on the node's own loop, like
    ``_verify_account``. A failure raises ``BrokerStateUnavailableError`` —
    never a flat reading — and ``main`` turns it into its own RESULT reason.
    """
    print("[probe] reading broker state (positions + cash)...", flush=True)
    t0 = time.monotonic()
    state = loop.run_until_complete(read_broker_state(node))
    for line in render_broker_state(state):
        print(f"[probe] {line}", flush=True)
    print(f"[probe] broker state read in {(time.monotonic() - t0) * 1000:.0f} ms", flush=True)
    return len(state.positions)


def _reconcile(node, run_task, settings, loop: asyncio.AbstractEventLoop, local_before) -> str:
    """Run the ``reconcile`` phase's body (Story 4.2) once Nautilus's own pass is done.

    Waits for ``trader.is_running`` first — the session's own ``node:connect``
    post-condition — because the framework reconciles inside ``start_async``
    *before* starting the trader, and the phase must compare against its result.
    A refusal raises ``ReconciliationFailedError``; ``main`` gives it its own
    RESULT reason.
    """
    log = structlog.get_logger("live_node_probe")
    timeout = DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS
    print("[probe] waiting for Nautilus's own reconciliation (trader started)...", flush=True)
    loop.run_until_complete(
        await_trader_started(node, run_task, time.monotonic() + timeout, timeout, settings, log)
    )
    print("[probe] reconciling against the broker (phase=reconcile)...", flush=True)
    result = loop.run_until_complete(reconcile_at_startup(node, log=log, local_before=local_before))
    for position in result.broker.positions:
        print(f"[probe] reconciled {position.instrument_id} qty={position.quantity:+}", flush=True)
    print(
        f"[probe] reconcile ok positions={len(result.broker.positions)} "
        f"discrepancies={result.discrepancy_count} open_orders={result.open_orders} "
        f"synthetic_positions={result.synthetic_positions} elapsed_ms={result.elapsed_ms:.0f}",
        flush=True,
    )
    positions, discrepancies = len(result.broker.positions), result.discrepancy_count
    return f" reconcile=ok positions={positions} discrepancies={discrepancies}"


def _run(
    run_seconds: int, *, verify_account: bool, read_state: bool = False, reconcile: bool = False
) -> str:
    """Drive the node's lifecycle on a loop this function owns end to end.

    ``TradingNode.dispose()`` calls ``loop.stop()`` synchronously whenever it
    finds the loop still running (``nautilus_trader/live/node.py:451-458``) —
    correct when ``dispose()`` runs after ``node.run()`` has already returned
    control, but fatal if called from inside a coroutine still executing on
    that same loop (as it would be under a bare ``asyncio.run(...)`` wrapper):
    the loop stops mid-flight and ``asyncio.run()``'s own cleanup then raises
    ``RuntimeError: Event loop stopped before Future completed.`` — a probe
    bug, not a ``build_trading_node()`` one, confirmed by a live run against
    the paper Gateway on 2026-08-05 (see docs/qa/phase3-live-verification.md).
    Driving everything through explicit ``loop.run_until_complete()`` calls
    instead means ``node.stop()`` and ``node.dispose()`` always see a loop
    that is not running, exactly as Nautilus expects.

    Returns a short status suffix for the RESULT line.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = IBKRSettings()
    print(
        f"[probe] building node host={settings.ibkr_host} port={settings.ibkr_port} "
        f"client_id={settings.ibkr_live_client_id} trader_id={PROBE_TRADER_ID}",
        flush=True,
    )
    # The loop is passed explicitly: TradingNode otherwise falls back to
    # asyncio.get_event_loop(), and binding the node to a loop this function
    # does not own is how stop()/dispose() end up driving the wrong one.
    node = build_trading_node(settings, trader_id=PROBE_TRADER_ID, cli_flags=GateFlags(), loop=loop)

    print("[probe] node built (config + LogGuard); building clients...", flush=True)
    node.build()

    # Before the node runs: the framework reconciles inside `run_async`.
    local_before = (
        capture_local_positions(node, structlog.get_logger("live_node_probe"))
        if reconcile
        else None
    )
    print("[probe] starting node...", flush=True)
    run_task = loop.create_task(node.run_async())
    gate_account: str | None = None
    broker_positions: int | None = None
    reconciled = ""

    try:
        print(
            f"[probe] waiting up to {settings.ibkr_connection_timeout}s for engines to connect...",
            flush=True,
        )
        loop.run_until_complete(
            _await_connected(node, run_task, float(settings.ibkr_connection_timeout))
        )
        print("[probe] connected (data + exec)", flush=True)

        gate_account = _verify_account(node, settings, loop) if verify_account else None
        broker_positions = _read_broker_state(node, loop) if read_state else None
        if reconcile:
            reconciled = _reconcile(node, run_task, settings, loop, local_before)

        print(f"[probe] running for {run_seconds}s...", flush=True)
        loop.run_until_complete(asyncio.sleep(run_seconds))
    finally:
        print("[probe] stopping and disposing node...", flush=True)
        problems = _shutdown(node, run_task, loop)
        print(
            f"[probe] disposed loop.is_running={loop.is_running()} "
            f"loop.is_closed={loop.is_closed()}",
            flush=True,
        )
        if problems:
            print(f"[probe] shutdown problems: {'; '.join(problems)}", file=sys.stderr, flush=True)

    if problems:
        raise ProbeError(f"unclean shutdown: {'; '.join(problems)}")

    detail = f"loop_closed={loop.is_closed()}"
    # Only appended when --verify-account was passed, so P1's documented RESULT
    # line is byte-for-byte what it was before this flag existed.
    if gate_account is not None:
        detail += f" gate_account={gate_account}"
    # Likewise only with --read-broker-state, so P1/P2's lines are unchanged.
    if broker_positions is not None:
        detail += f" broker_state=ok positions={broker_positions}"
    # And only with --reconcile (Story 4.2), for the same reason.
    return detail + reconciled


def _positive_seconds(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--run-seconds must be at least 1 (got {value}); asyncio.sleep(<=0) "
            "returns immediately, so the node would never actually run"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-seconds",
        type=_positive_seconds,
        default=5,
        help="How long to leave the node running once connected (default: 5, minimum: 1)",
    )
    parser.add_argument(
        "--verify-account",
        action="store_true",
        help=(
            "Run the Layer 2 gate:account phase after connecting (Procedure P2). "
            "Off by default so Procedure P1's behaviour is unchanged."
        ),
    )
    parser.add_argument(
        "--read-broker-state",
        action="store_true",
        help=(
            "After connecting (and after --verify-account, if given), read the positions and "
            "cash IBKR reports for the account (Procedure P15). Read-only; off by default."
        ),
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "After connecting, run the session's reconcile phase body against this node "
            "(Procedure P17a). No strategy, in-memory cache; off by default."
        ),
    )
    args = parser.parse_args()
    if args.reconcile and not args.verify_account:
        parser.error(
            "--reconcile requires --verify-account (AR39: reconcile runs after gate:account)"
        )

    # Loaded here, not at import time: load_dotenv() mutates os.environ
    # process-wide, which should not be a side effect of importing this module.
    # The path is explicit so the probe behaves identically from any cwd —
    # pydantic-settings resolves `env_file=".env"` relative to the cwd.
    load_dotenv(REPO_ROOT / ".env")

    t0 = time.monotonic()
    try:
        detail = _run(
            args.run_seconds,
            verify_account=args.verify_account,
            read_state=args.read_broker_state,
            reconcile=args.reconcile,
        )
        print(
            f"RESULT: ok mode=build-connect-run-stop {detail} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 0
    except AccountGateRefused as e:
        # A sibling of the handler below, not a subclass of it — `_verify_account`
        # converts every Layer 2 `GateRefusedError` into this type precisely so
        # the two are distinguishable here. Keeping them separate is what tells
        # the operator whether a socket was ever opened; the handler *order* is
        # not what does that, and must not be relied on for it.
        print(
            f"RESULT: fail reason=account_gate_refused refusal={e.refusal.reason.value} "
            f"elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except BrokerStateUnavailableError as e:
        # Its own reason, so a failed read can never be mistaken for a flat
        # account (NFR20): the node connected, the broker's view did not arrive.
        # The detail is the reader's own text (masked, no broker text) and, for
        # adapter drift, names the exception type and where it was raised.
        print(f"[probe] broker state failed: {e.detail}", flush=True)
        print(
            f"RESULT: fail reason=broker_state_unavailable failure={e.reason.value} "
            f"elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except ReconciliationFailedError as e:
        # Story 4.2: the cache could not be proven to match the broker. Our own
        # text, naming instruments and quantities only.
        print(f"[probe] reconcile refused: {e}", flush=True)
        print(
            f"RESULT: fail reason=reconcile_refused failure={e.reason.value} "
            f"elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
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
        # Not caught by `except Exception`. Without this an operator's Ctrl-C
        # produces no RESULT line at all and leaves the client id reserved on
        # the Gateway — the stale-id condition the QA doc warns about.
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
