"""Drive one short-lived node through a broker connectivity check (Story 1.7).

Owns: the sequence ``ntrader live check`` performs — build the node behind the
gate, build its clients under a bounded connection budget, run it, wait for both
engines to report connected, run Layer 2's ``gate:account`` verification, observe
bars for a window, stop, dispose — and the evidence it gathers along the way.

Does not own: the node *plumbing* those steps rest on (``live_check_node`` — the
loop, the bounded build, the connect wait, the teardown); the outcome vocabulary
and exit codes (``live_check`` — pure, which is why that half can be unit-tested
with no Nautilus at all); the gate's rules (``live_gate``, untouched); node
assembly (``live_node_builder``); the account decision (``live_account_gate``); or
the subscription actor (``live_bar_observer``). All of those already exist and are
reused verbatim.

**This is not ``live_session_runner.py``** (Epic 2, Story 2.5, AR38). It owns no
session identity, persists nothing, handles no signals, runs no poll loop past
its observation window, reconnects to nothing, and starts no strategy — Epic 1
has no order path by design. It is the productionised shape of
``scripts/diagnostics/live_{node,bars,connection}_probe.py``, which remain other
stories' evidence and are not modified.

**Two orderings in ``_drive`` are load-bearing and easy to undo by accident.**

1. *The connect deadline starts before ``node.build()``.* The build is where the
   adapter's own connect attempt happens, so a deadline started after it spends
   the build budget and the connect budget in series — an unreachable gateway
   took **115 seconds** to say so when they were additive, measured live.
   ``--connect-timeout`` means "how long the check spends trying to connect", and
   that ordering is what makes it true.
2. *Everything after the node exists runs inside the ``try``.*
   ``TradingNode.__init__`` has already created the kernel's non-daemon
   ``ThreadPoolExecutor`` (``system/kernel.py:269``), so a raise before the
   ``finally`` skips shutdown entirely and hangs the process at interpreter exit.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

import structlog
from nautilus_trader.live.node import TradingNode

from src.config import IBKRSettings
from src.core.live_account_gate import (
    gateway_reported_accounts,
    masked_accounts,
    verify_connected_account,
)
from src.core.live_bar_observer import LiveBarObserver, build_bar_observer_config
from src.core.live_check import (
    CheckEvidence,
    InvalidCheckWindowError,
    LiveCheckError,
    LiveCheckOutcome,
    LiveCheckReport,
    build_report,
    classify_failure,
    failure_message,
    preflight_gate,
    success_message,
)
from src.core.live_check_node import (
    POLL_SECONDS,
    await_connected,
    build_clients,
    current_event_loop,
    find_observer,
    restore_event_loop,
    shutdown,
)
from src.core.live_gate import GateDecision, GateFlags, GateRefusal, GateRefusalReason
from src.core.live_market_data import instrument_ids_for, resolve_live_bar_types
from src.core.live_node_builder import GateRefusedError, build_trading_node

logger = structlog.get_logger(__name__)

#: A fixed diagnostic identity — not a derivation pattern. Epic 2 owns the real
#: ``PAPER-<short-session-id>`` scheme (AR10). It must contain a ``-``: a
#: ``trader_id`` without one **panics in Rust and aborts the process**.
CHECK_TRADER_ID = "PAPER-LIVECHECK"

NodeFactory = Callable[..., TradingNode]
AccountVerifier = Callable[..., Awaitable[GateDecision]]


def _validate_window(name: str, seconds: float, *, allow_zero: bool) -> float:
    """Reject a duration that cannot bound a wait. See ``InvalidCheckWindowError``."""
    if math.isnan(seconds) or math.isinf(seconds):
        raise InvalidCheckWindowError(
            f"{name} is {seconds!r}, which cannot bound a wait: a NaN comparison is always "
            "False so the wait would end before it began, and an infinite one would never "
            "end while holding the live client id. Pass a finite number of seconds."
        )
    if seconds < 0 or (seconds == 0 and not allow_zero):
        raise InvalidCheckWindowError(
            f"{name} is {seconds}, which is not a usable duration. "
            f"Pass a {'non-negative' if allow_zero else 'positive'} number of seconds."
        )
    return seconds


def run_live_check(
    settings: IBKRSettings,
    *,
    bar_types: Sequence[str],
    observe_seconds: float,
    connect_timeout: float,
    cli_flags: GateFlags | None = None,
    require_bars: bool = False,
    node_factory: NodeFactory = build_trading_node,
    account_verifier: AccountVerifier = verify_connected_account,
) -> LiveCheckReport:
    """Run the whole check and report what happened. Never raises for a failure.

    ``settings`` is injected, never fetched — the CLI is the composition root.
    ``connect_timeout`` is deliberately **not** ``IBKR_CONNECTION_TIMEOUT`` (300s):
    a check exists to answer quickly, and that setting is the truth for a
    *session*. ``observe_seconds`` may be ``0``, which still requests the
    subscription — no bar can close in zero seconds. ``require_bars`` turns a
    window that closed with no bar into a failure rather than a reported
    shortfall. ``node_factory`` and ``account_verifier`` are the two broker-facing
    seams, so every branch is reachable in tests without a broker (NFR32).

    Returns:
        A report whose ``exit_code`` is AR28's code for what happened.
    """
    started = time.monotonic()

    refusal = preflight_gate(settings, cli_flags)
    if refusal is not None:
        # Returned before anything is constructed: no loop, no node, no client
        # config, no socket. That is AC #2's "no connection attempt" as a
        # property of the control flow rather than an emergent hope.
        return replace(refusal, elapsed_seconds=time.monotonic() - started)

    evidence = CheckEvidence(bar_types=tuple(bar_types))
    try:
        _validate_window("observe_seconds", observe_seconds, allow_zero=True)
        _validate_window("connect_timeout", connect_timeout, allow_zero=False)
        _drive(
            settings,
            evidence,
            bar_types=bar_types,
            observe_seconds=observe_seconds,
            connect_timeout=connect_timeout,
            cli_flags=GateFlags() if cli_flags is None else cli_flags,
            require_bars=require_bars,
            node_factory=node_factory,
            account_verifier=account_verifier,
        )
    except (Exception, KeyboardInterrupt) as exc:
        # `SystemExit` and `CancelledError` deliberately propagate: neither is a
        # check outcome, and swallowing them would leave the caller with nothing
        # to act on.
        return build_report(
            evidence, classify_failure(exc), failure_message(exc), time.monotonic() - started, exc
        )

    return build_report(
        evidence, LiveCheckOutcome.OK, success_message(evidence), time.monotonic() - started
    )


def _drive(
    settings: IBKRSettings,
    evidence: CheckEvidence,
    *,
    bar_types: Sequence[str],
    observe_seconds: float,
    connect_timeout: float,
    cli_flags: GateFlags,
    require_bars: bool,
    node_factory: NodeFactory,
    account_verifier: AccountVerifier,
) -> None:
    """Drive the node's lifecycle on a loop this function owns end to end.

    ``TradingNode.dispose()`` calls ``loop.stop()`` whenever it finds the loop
    running (``live/node.py:451-458``) — fatal if called from a coroutine still
    executing on that loop, which is what Story 1.3 lost a live run to. Driving
    everything through explicit ``run_until_complete()`` calls means ``stop()``
    and ``dispose()`` always see a loop that is not running.

    The two orderings this rests on are in the module docstring. Read them before
    moving any line in the ``try``.
    """
    resolved = resolve_live_bar_types(bar_types)
    evidence.bar_types = tuple(str(bar_type) for bar_type in resolved)
    evidence.instruments_requested = instrument_ids_for(resolved)
    observer_config = build_bar_observer_config(settings, bar_types)

    previous_loop = current_event_loop()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node: TradingNode | None = None
    run_task: asyncio.Task | None = None
    observer: LiveBarObserver | None = None
    try:
        node = node_factory(
            settings,
            trader_id=CHECK_TRADER_ID,
            bar_types=list(evidence.bar_types),
            bar_observer=observer_config,
            cli_flags=cli_flags,
            loop=loop,
        )
        observer = find_observer(node)
        connect_deadline = time.monotonic() + connect_timeout
        build_clients(node, settings, trader_id=CHECK_TRADER_ID)
        run_task = loop.create_task(node.run_async())
        loop.run_until_complete(
            await_connected(node, run_task, connect_deadline, connect_timeout, settings)
        )
        _verify_account(loop, node, settings, cli_flags, evidence, account_verifier)
        _record_instruments(node, evidence)
        loop.run_until_complete(_observe(run_task, observer, observe_seconds))
    finally:
        # `_record_observations_safely` is guarded for one reason: anything it
        # raised here would skip `shutdown` entirely — leaving the socket open,
        # the loop unclosed and the kernel's non-daemon thread pool to be joined
        # at interpreter exit — and would replace the primary outcome on the way
        # out. That is the exact pair of failures `shutdown` exists to prevent,
        # so nothing unguarded may sit in front of it.
        _record_observations_safely(observer, evidence)
        evidence.shutdown_problems = tuple(shutdown(node, run_task, loop))
        restore_event_loop(previous_loop)

    _assert_observations_are_acceptable(evidence, require_bars)


def _verify_account(
    loop: asyncio.AbstractEventLoop,
    node: TradingNode,
    settings: IBKRSettings,
    cli_flags: GateFlags,
    evidence: CheckEvidence,
    account_verifier: AccountVerifier,
) -> None:
    """Run Layer 2's ``gate:account`` phase and record what it judged.

    The reported accounts are read **once** and handed to the verifier, so the
    set that is judged is the set that is displayed — a second, later read would
    render a set the adapter mutates on disconnect.

    Two fail-closed properties are enforced here rather than assumed, and both
    were fail-open when this was first written:

    1. **A raising account read goes back to the verifier, not to the caller.**
       ``gateway_reported_accounts``' docstring is explicit that a raising
       ``accounts()`` — a renamed adapter method, a lock error — must propagate
       into ``verify_connected_account``, which converts *any* unexpected
       exception into an ``ACCOUNT_VERIFICATION_ERROR`` refusal. Reading it here
       for display took that raise out of the guard and turned a gate refusal
       (exit 3) into a generic error (exit 1) on an adapter-drift path — exactly
       when the distinction matters most. ``reported_accounts=None`` puts the
       read back inside the guard.
    2. **A verifier that *returns* a refusal is a refusal.** The seam is typed
       ``-> GateDecision`` and ``GateDecision`` carries ``permitted``. The default
       implementation raises, but nothing in the type says it must, and a
       returned refusal treated as a pass would let the check subscribe and
       report ``ok`` on an unverified account.
    """
    try:
        reported: frozenset[str] | None = gateway_reported_accounts(settings)
    except Exception:  # noqa: BLE001 - let the verifier's own guard classify it
        reported = None

    evidence.accounts = masked_accounts(reported) if reported is not None else ""
    decision = loop.run_until_complete(
        account_verifier(node, settings, cli_flags=cli_flags, reported_accounts=reported)
    )
    if not decision.permitted:
        raise GateRefusedError(decision.refusal or _UNEXPLAINED_ACCOUNT_REFUSAL)
    evidence.mode = decision.mode


#: Used only when a verifier returns a refusal with no reason — unreachable via
#: ``live_gate``'s own producers, which always populate it. A value rather than an
#: ``assert`` because ``python -O`` strips asserts, and losing this would turn a
#: refusal into an ``AttributeError`` on the safety-critical path.
_UNEXPLAINED_ACCOUNT_REFUSAL = GateRefusal(
    reason=GateRefusalReason.ACCOUNT_VERIFICATION_ERROR,
    message=(
        "Account verification refused the connection but supplied no reason; refusing to "
        "continue against an unexplained refusal."
    ),
)


def _record_instruments(node: TradingNode, evidence: CheckEvidence) -> None:
    """Compare the instruments requested against those the node actually loaded.

    ``InteractiveBrokersInstrumentProvider.load_ids_with_return_async`` *skips* a
    contract that will not qualify (``providers.py:243-265``) — nothing raises
    and nothing reports, so a connected session with zero bars for that
    subscription is indistinguishable from a healthy one. This difference is the
    only visible trace of it, which is why the check reports it.
    """
    loaded = {str(instrument.id) for instrument in node.cache.instruments()}
    evidence.instruments_loaded = tuple(
        name for name in evidence.instruments_requested if name in loaded
    )
    missing = tuple(name for name in evidence.instruments_requested if name not in loaded)
    if missing:
        logger.warning(
            "live_check.instruments",
            requested=list(evidence.instruments_requested),
            missing=list(missing),
            reason="IBKR did not qualify these contracts; their subscriptions cannot deliver",
        )


async def _observe(
    run_task: asyncio.Task,
    observer: LiveBarObserver | None,
    observe_seconds: float,
) -> None:
    """Watch for bars for the requested window.

    Returns when the window elapses; the caller decides whether the count is
    evidence. A run task that finishes early surfaces as itself rather than as
    "no bars", so a dead session is never reported as a quiet market.

    The liveness check runs **once before the loop as well as inside it**. With
    ``--observe-seconds 0`` the loop body never executes, so a node that died the
    instant after both engines reported connected was never inspected at all: the
    check reported ``ok`` and exit **0** — the "connection proven, go ahead"
    signal — with the real failure demoted to a ``shutdown_problems`` line.
    """
    logger.info("live_check.observing", seconds=observe_seconds)
    deadline = time.monotonic() + observe_seconds
    while True:
        _raise_if_node_died(run_task, observer)
        if time.monotonic() >= deadline:
            return
        await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


def _raise_if_node_died(run_task: asyncio.Task, observer: LiveBarObserver | None) -> None:
    """Surface a node that stopped mid-observation as the failure it actually is.

    The delayed-feed case is checked first and by name. ``LiveBarObserver``
    reacts to a delayed feed by calling ``shutdown_system()``, which stops the
    node — so on the one path where the observer has already diagnosed the
    problem, the generic "stopped running" message would bury it and send the
    operator looking for a crash that never happened.
    """
    if not run_task.done():
        return
    if observer is not None and observer.delayed_data_suspected:
        raise LiveCheckError(_DELAYED_DATA_MESSAGE)
    run_task.result()
    raise LiveCheckError("the node stopped running before the observation window elapsed")


_DELAYED_DATA_MESSAGE = (
    "delayed market data suspected — the observer measured bars arriving too late for a "
    "real-time feed and stopped the session (see the live_bars.delayed_data_suspected log "
    "line for the measured delay)"
)


def _record_observations_safely(observer: LiveBarObserver | None, evidence: CheckEvidence) -> None:
    """Copy the observer's counters onto the evidence, never raising.

    Called from the ``finally`` in front of ``shutdown``, so what was seen is
    reported even when the step that followed raised — a failing check that still
    received bars should say so.
    """
    if observer is None:
        return
    try:
        evidence.bars_received = observer.total_received
        evidence.counts_by_bar_type = tuple(sorted(observer.counts_by_bar_type().items()))
        evidence.delayed_data_suspected = observer.delayed_data_suspected
    except Exception as exc:  # noqa: BLE001 - must never pre-empt shutdown
        logger.error("live_check.observations_unreadable", error_type=type(exc).__name__)


def _assert_observations_are_acceptable(evidence: CheckEvidence, require_bars: bool) -> None:
    """Turn an unacceptable observation into a failure, after shutdown is done.

    A *delayed* feed always fails; reporting ``ok`` would certify a session acting
    on prices roughly 15 minutes old. On the common path ``_raise_if_node_died``
    gets there first — the observer's own ``shutdown_system()`` stops the node, so
    the diagnosis surfaces during the window rather than after it — and this stays
    as the backstop for a flag set without the node stopping.

    Zero bars does **not** fail by default: with ``use_rth=True`` no bar closes
    outside regular trading hours, so an operator checking their setup at 07:00
    would otherwise be told their configuration is broken. ``--require-bars`` is
    how a script that knows it is inside RTH makes the distinction assertable.
    """
    if evidence.delayed_data_suspected:
        raise LiveCheckError(_DELAYED_DATA_MESSAGE)
    if require_bars and evidence.bars_received == 0:
        raise LiveCheckError(
            f"no bars were received for {', '.join(evidence.bar_types)} and --require-bars "
            "was set. Either the market is closed (use_rth=True means no bar closes outside "
            "RTH), the account lacks a market-data subscription for this instrument, or the "
            "contract was never qualified"
        )
