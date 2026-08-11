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

Usage::

    uv run python scripts/diagnostics/live_node_probe.py [--run-seconds 5] [--verify-account]

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

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]

from src.config import IBKRSettings  # noqa: E402
from src.core.live_account_gate import (  # noqa: E402
    gateway_reported_accounts,
    masked_accounts,
    verify_connected_account,
)
from src.core.live_gate import GateFlags, GateRefusal  # noqa: E402
from src.core.live_node_builder import (  # noqa: E402
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node,
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


def _run(run_seconds: int, *, verify_account: bool) -> str:
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

    print("[probe] starting node...", flush=True)
    run_task = loop.create_task(node.run_async())
    gate_account: str | None = None

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
    return detail


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
    args = parser.parse_args()

    # Loaded here, not at import time: load_dotenv() mutates os.environ
    # process-wide, which should not be a side effect of importing this module.
    # The path is explicit so the probe behaves identically from any cwd —
    # pydantic-settings resolves `env_file=".env"` relative to the cwd.
    load_dotenv(REPO_ROOT / ".env")

    t0 = time.monotonic()
    try:
        detail = _run(args.run_seconds, verify_account=args.verify_account)
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
