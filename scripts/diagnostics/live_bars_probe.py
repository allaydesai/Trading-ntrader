"""Real-time RTH bar subscription diagnostic probe (Story 1.5).

Builds a TradingNode against the configured IBKR paper gateway with a
``LiveBarObserver`` attached, connects, waits for real bars to arrive, then
stops and disposes the node.

``RESULT: ok`` means bars **actually arrived**. It is not enough to report
success because the subscription was accepted: ``_subscribe_bars`` logs
``instrument not found`` and returns when the instrument provider did not load
the contract (``adapters/interactive_brokers/data.py:248-254``), and IB refuses
a subscription with a warning rather than an error. Either way nothing raises
and no bar is ever delivered, so a probe that only checked "nothing raised"
would print ``ok`` for a session that saw nothing.

This is read-only: it subscribes to market data. It submits no order, and Epic 1
contains no order path to reach.

This is a diagnostic, not the runner: it does not own a session's lifecycle.
``live_session_runner.py`` (Epic 2, Story 2.5) does. Do not mistake one for the
other.

Usage::

    uv run python scripts/diagnostics/live_bars_probe.py \
        --bar-type AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL --run-seconds 150

Preconditions:
    - IB Gateway or TWS running on the configured paper port (see .env)
    - .env passes the Layer 1 gate (the probe refuses before opening a socket
      otherwise, exactly as live_node_probe.py does)
    - **Run during regular trading hours.** With ``use_rth=True`` no bar closes
      outside RTH, so zero bars outside the session is a precondition failure,
      not an AC failure.

Prints a final ``RESULT: ok|fail ...`` line to stdout for easy parsing. On
failure the full traceback goes to stderr, so the parseable line stays clean.
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

# Put the repo root on the path before importing `src`, so the probe runs from
# any working directory without the caller having to remember `PYTHONPATH=.`.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import IBKRSettings  # noqa: E402
from src.core.live_bar_observer import (  # noqa: E402
    LiveBarObserver,
    build_bar_observer_config,
)
from src.core.live_gate import GateFlags  # noqa: E402
from src.core.live_market_data import LiveMarketDataError  # noqa: E402
from src.core.live_node_builder import (  # noqa: E402
    GateRefusedError,
    LiveNodeConfigError,
    build_trading_node,
)

# A fixed diagnostic identity — not a derivation pattern. The real
# PAPER-<short-session-id> scheme belongs to Epic 2's session (AR10).
PROBE_TRADER_ID = "PAPER-BARPROBE1"

DEFAULT_BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

# Polling interval while waiting for the engines to report connected.
_CONNECT_POLL_SECONDS = 0.25

# How often to report progress while waiting for bars.
_BAR_POLL_SECONDS = 5.0


class ProbeError(RuntimeError):
    """The probe reached the gateway path but the session did not do its job."""


def _find_observer(node) -> LiveBarObserver:
    """Return the LiveBarObserver the kernel instantiated from the node config."""
    for actor in node.trader.actors():
        if isinstance(actor, LiveBarObserver):
            return actor
    raise ProbeError("no LiveBarObserver was registered on the node")


async def _await_connected(node, run_task: asyncio.Task, timeout: float) -> None:
    """Block until both engines report connected, or fail loudly.

    Copied in shape from live_node_probe.py deliberately: polling rather than a
    fixed sleep, and surfacing a dead run_task immediately rather than letting
    its exception emerge during shutdown.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run_task.done():
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


async def _await_bars(observer, run_task: asyncio.Task, run_seconds: int) -> None:
    """Run for the requested window, reporting bar counts as they arrive.

    Returns as soon as the window elapses; the caller decides whether the count
    is evidence. Returns early if the node dies, so a failure is reported as
    itself rather than as "no bars".
    """
    deadline = time.monotonic() + run_seconds
    while time.monotonic() < deadline:
        if run_task.done():
            run_task.result()
            if observer.delayed_data_suspected:
                # Checked before the generic message: the guard's own shutdown
                # stops the node, so this branch is how a real downgrade
                # surfaces — reporting it as "stopped running" would bury it.
                raise ProbeError(
                    "delayed market data suspected — the observer measured bars arriving too "
                    "late for a real-time feed and stopped the session (see the "
                    "live_bars.delayed_data_suspected log line for the measured delay)"
                )
            raise ProbeError("node stopped running before the observation window elapsed")
        remaining = max(0.0, deadline - time.monotonic())
        await asyncio.sleep(min(_BAR_POLL_SECONDS, remaining))
        print(
            f"[probe] bars so far: {observer.total_received} ({observer.counts_by_bar_type()})",
            flush=True,
        )


def _shutdown(
    node,
    run_task: asyncio.Task | None,
    loop: asyncio.AbstractEventLoop,
) -> list[str]:
    """Stop, dispose and close, reporting problems rather than raising.

    Runs from a ``finally``, so it must never mask the original failure. A node
    that failed still holds a socket and the kernel's non-daemon
    ``ThreadPoolExecutor``, which would otherwise hang the process at exit.

    ``run_task`` is optional because the node can raise between construction and
    ``create_task`` — in which case there is nothing to cancel, but there is very
    much still a kernel to dispose.

    Guards on ``BaseException``, not ``Exception``: a second Ctrl-C landing
    inside ``run_until_complete`` would otherwise skip ``dispose()`` and
    ``loop.close()`` on exactly the interrupt path ``main()`` handles cleanly.
    """
    problems: list[str] = []

    try:
        node.stop()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"stop: {type(exc).__name__}: {exc}")

    if run_task is not None:
        if not run_task.done():
            run_task.cancel()
        try:
            loop.run_until_complete(run_task)
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001 - already reported via RESULT
            problems.append(f"run_task: {type(exc).__name__}: {exc}")

    try:
        node.dispose()
    except Exception as exc:  # noqa: BLE001 - shutdown is best-effort by design
        problems.append(f"dispose: {type(exc).__name__}: {exc}")

    if not loop.is_closed():
        loop.close()

    return problems


def _load_settings(overrides: dict[str, object]) -> IBKRSettings:
    """Load settings, applying any connection overrides the operator passed.

    The overrides exist so the probe can run in a checkout that has no ``.env``
    (a git worktree, for instance — ``.env`` is untracked). They are **not** a
    way around the safety gate: ``evaluate_gate`` still runs on the resulting
    configuration inside ``build_trading_node_config``, so a non-paper port or a
    non-paper account prefix is refused exactly as it would be from ``.env``.
    Neither ``--real-money`` nor ``NTRADER_REAL_MONEY_ACCOUNT`` is reachable from
    this script at all.
    """
    return IBKRSettings(**overrides)  # type: ignore[arg-type]


def _run(bar_types: list[str], run_seconds: int, overrides: dict[str, object]) -> str:
    """Drive the node's lifecycle on a loop this function owns end to end.

    ``TradingNode.dispose()`` calls ``loop.stop()`` whenever it finds the loop
    running (``nautilus_trader/live/node.py:451-458``) — fatal if called from
    inside a coroutine still executing on that loop, as a bare
    ``asyncio.run(...)`` wrapper guarantees. Story 1.3's first live run of P1 hit
    exactly that. Driving everything through explicit
    ``loop.run_until_complete()`` calls means ``stop()`` and ``dispose()`` always
    see a loop that is not running.

    Returns a short status suffix for the RESULT line.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    settings = _load_settings(overrides)
    observer_config = build_bar_observer_config(settings, bar_types)

    print(
        f"[probe] building node host={settings.ibkr_host} port={settings.ibkr_port} "
        f"client_id={settings.ibkr_live_client_id} trader_id={PROBE_TRADER_ID} "
        f"bar_types={bar_types} lines_budget={settings.ibkr_market_data_lines}",
        flush=True,
    )
    node = build_trading_node(
        settings,
        trader_id=PROBE_TRADER_ID,
        bar_types=bar_types,
        bar_observer=observer_config,
        cli_flags=GateFlags(),
        loop=loop,
    )
    # Everything from here on runs inside the try: `TradingNode.__init__` has
    # already created the kernel's non-daemon ThreadPoolExecutor
    # (system/kernel.py:269), so any raise between construction and the finally —
    # `_find_observer`, `node.build()`, `create_task` — would otherwise skip
    # shutdown entirely and hang the process at interpreter exit, which is the
    # exact failure `_shutdown` exists to prevent.
    run_task: asyncio.Task | None = None
    observer: LiveBarObserver | None = None
    try:
        observer = _find_observer(node)

        print("[probe] node built (config + observer + LogGuard); building clients...", flush=True)
        node.build()

        print("[probe] starting node...", flush=True)
        run_task = loop.create_task(node.run_async())

        print(
            f"[probe] waiting up to {settings.ibkr_connection_timeout}s for engines to connect...",
            flush=True,
        )
        loop.run_until_complete(
            _await_connected(node, run_task, float(settings.ibkr_connection_timeout))
        )
        print("[probe] connected (data + exec)", flush=True)

        print(f"[probe] observing bars for {run_seconds}s...", flush=True)
        loop.run_until_complete(_await_bars(observer, run_task, run_seconds))
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

    if observer is None:  # pragma: no cover - _find_observer raises first
        raise ProbeError("no LiveBarObserver was registered on the node")

    if observer.delayed_data_suspected:
        raise ProbeError(
            "delayed market data suspected — the observer measured a bar lag beyond its "
            "threshold and shut the session down (see the live_bars.delayed_data_suspected log)"
        )

    if observer.total_received == 0:
        raise ProbeError(
            f"no bars received in {run_seconds}s for {bar_types}. Either the market is closed "
            "(use_rth=True means no bar closes outside RTH), the account lacks a market-data "
            "subscription for this instrument, or the contract was not loaded"
        )

    return (
        f"bars={observer.total_received} counts={observer.counts_by_bar_type()} "
        f"republished={observer.total_republished}"
    )


def _positive_seconds(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--run-seconds must be at least 1 (got {value}); the window has to be long "
            "enough for at least one bar to close, so a 1-minute bar type needs >60"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bar-type",
        action="append",
        dest="bar_types",
        help=f"Bar type to subscribe to; repeatable (default: {DEFAULT_BAR_TYPE})",
    )
    parser.add_argument(
        "--run-seconds",
        type=_positive_seconds,
        default=150,
        help="How long to observe once connected (default: 150 — over two 1-minute bars)",
    )
    parser.add_argument("--host", help="Override IBKR_HOST (for a checkout with no .env)")
    parser.add_argument("--port", type=int, help="Override IBKR_PORT (the gate still applies)")
    parser.add_argument("--account", help="Override TWS_ACCOUNT (the gate still applies)")
    args = parser.parse_args()
    bar_types = args.bar_types or [DEFAULT_BAR_TYPE]

    overrides: dict[str, object] = {}
    if args.host is not None:
        overrides["ibkr_host"] = args.host
    if args.port is not None:
        overrides["ibkr_port"] = args.port
    if args.account is not None:
        overrides["tws_account"] = args.account

    # Loaded here, not at import time: load_dotenv() mutates os.environ
    # process-wide, which should not be a side effect of importing this module.
    load_dotenv(REPO_ROOT / ".env")

    t0 = time.monotonic()
    try:
        detail = _run(bar_types, args.run_seconds, overrides)
        print(
            f"RESULT: ok mode=subscribe-observe-stop {detail} elapsed={time.monotonic() - t0:.2f}",
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
    except (LiveNodeConfigError, LiveMarketDataError) as e:
        print(
            f"RESULT: fail reason=config_error msg={e} elapsed={time.monotonic() - t0:.2f}",
            flush=True,
        )
        return 1
    except KeyboardInterrupt:
        # Not caught by `except Exception`. Without this an operator's Ctrl-C
        # produces no RESULT line and leaves the client id reserved on the
        # Gateway — the stale-id condition the QA doc warns about.
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
