"""Layer 2 enforcement — verify the account the gateway actually reported.

Owns: obtaining the account identifiers the connected gateway named, running
them through the pure Layer 2 decision, and — on a refusal — stopping the node
before any strategy can trade on an unverified account (FR8, FR9, AR14).

Does not own: the *decision*, which is ``evaluate_account_gate`` in
``src/core/live_gate.py`` and stays pure; the node's lifecycle, which is the
runner's (AR38) — this module stops a node but never builds, runs, or disposes
one; and AR39's full startup sequence, which is Epic 2's contract. This module
emits its own ``gate:account`` phase records and nothing more.
"""

import asyncio
from typing import TYPE_CHECKING

import structlog
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS

from src.config import IBKRSettings
from src.core.live_gate import (
    GateDecision,
    GateFlags,
    GateRefusal,
    GateRefusalReason,
    build_refusal,
    evaluate_account_gate,
    mask_account,
    normalize_reported_accounts,
)
from src.core.live_node_builder import GateRefusedError

if TYPE_CHECKING:
    from nautilus_trader.live.node import TradingNode

logger = structlog.get_logger(__name__)

#: AR39's name for this step of the startup sequence. Exported so Epic 2's
#: runner and this story's tests name the same phase, without either inventing a
#: phase enum — the full ordered sequence is the runner's contract, not this
#: module's.
STARTUP_PHASE = "gate:account"

#: Component states a strategy can be in *without ever having been started*
#: (``nautilus_trader/common/component.pyx:1569-1571`` — PRE_INITIALIZED
#: --INITIALIZE--> READY --START--> STARTING). An allowlist rather than a list of
#: "started" states, so a state Nautilus adds later reads as started and refuses
#: instead of silently passing the ordering guard.
NOT_YET_STARTED_STATES: frozenset[str] = frozenset({"PRE_INITIALIZED", "READY"})

#: Upper bound on how long a post-refusal shutdown may take before the refusal
#: is raised anyway. ``kernel.stop_async()`` awaits ``timeout_post_stop`` and
#: then ``timeout_disconnection`` (``system/kernel.py``), both operator-settable,
#: so an unbounded await here could withhold the refusal indefinitely — and the
#: refusal reaching the caller is the whole contract with Story 1.7's exit code.
STOP_TIMEOUT_SECONDS = 30.0


def gateway_reported_accounts(settings: IBKRSettings) -> frozenset[str]:
    """Return the account identifiers the connected gateway named.

    IBKR sends a ``managedAccounts`` message automatically on a successful API
    connection, which the adapter parses into
    ``InteractiveBrokersClient.accounts()``
    (``adapters/interactive_brokers/client/account.py:41-50,198-206``). That set
    is the gateway's own statement of what it manages — the ground truth AR14
    asks for.

    Reached through ``factories.IB_CLIENTS``, the adapter's module-level cache
    keyed on ``(host, port, client_id)``
    (``adapters/interactive_brokers/factories.py:112-124``) — exactly the three
    values ``live_node_builder`` passes as ``ibg_host``/``ibg_port``/
    ``ibg_client_id``. Deliberately **not** ``get_cached_ib_client(...)``, which
    creates *and starts* a client when the key is absent, and deliberately not
    ``node.cache.accounts()``, whose ``AccountId``s are built from the configured
    ``account_id`` (``execution.py:172``) and so echo configuration back rather
    than reporting evidence.

    Returns:
        The reported identifiers, or an empty set when no client is registered
        for this connection. An empty set is a refusal upstream, so a renamed or
        re-keyed adapter cache fails closed rather than raising.
    """
    client = IB_CLIENTS.get((settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id))
    if client is None:
        return frozenset()
    # A *raising* `accounts()` — a renamed method (AttributeError), an adapter
    # lock error — is deliberately NOT swallowed here. It propagates to
    # `verify_connected_account`, which turns any unexpected exception into an
    # ACCOUNT_VERIFICATION_ERROR refusal and stops the node. Swallowing it here
    # would report it as ACCOUNT_NOT_REPORTED, which has a distinct documented
    # meaning ("the gateway named no account") and would send the operator to
    # investigate the gateway rather than the drift.
    return normalize_reported_accounts(frozenset(client.accounts()))


def masked_accounts(accounts: frozenset[str]) -> str:
    """Render a set of accounts for an operator: masked, and in a stable order.

    A ``frozenset`` has no stable iteration order, so a log line built from one
    would differ run to run. Sorting the *masked* values keeps it deterministic
    without the raw identifiers ever existing in the rendered string (NFR26).
    De-duplicated after masking, because two distinct accounts can share their
    last three characters and ``***567, ***567`` tells the operator nothing.
    """
    return ", ".join(sorted({mask_account(account) for account in accounts}))


async def verify_connected_account(
    node: "TradingNode",
    settings: IBKRSettings,
    *,
    cli_flags: GateFlags | None = None,
    reported_accounts: frozenset[str] | None = None,
) -> GateDecision:
    """Run the ``gate:account`` startup phase against a connected node.

    A coroutine, not a plain function, because a refusal must leave the node
    genuinely down before this returns and only ``stop_async`` can do that from
    inside the running loop. ``TradingNode.stop()`` merely *schedules*
    ``stop_async`` when it finds the loop running (``live/node.py:374-388``), and
    ``TradingNode.dispose()`` busy-waits on a synchronous ``time.sleep`` that
    blocks the very loop the stop task needs (``live/node.py:402-460``) — Story
    1.3's probe hit exactly that and lost a live run to it. Disposal and loop
    ownership stay with the caller (AR38).

    Args:
        node: A built, running, connected node.
        settings: Loaded IBKR settings. Injected, never fetched.
        cli_flags: Operator declarations from the command line. ``None`` means no
            declaration was made, identical to ``GateFlags()``.
        reported_accounts: The gateway's account identifiers, when the caller has
            already read them. ``None`` — the normal case — reads them here,
            *after* the placement guards, so a misplaced call never touches the
            adapter. Supplied only so a caller that must also *display* the
            evidence renders the very set the gate judged, rather than a second,
            later read of a set the adapter mutates on disconnect.

    Returns:
        The permitting :class:`GateDecision`, carrying the established mode.

    Raises:
        GateRefusedError: The gate refused. The node has been stopped and the
            exception is the *same* class the static gate raises through
            ``live_node_builder``, so Story 1.7's exit-code mapping covers both
            layers without a second branch.
    """
    flags = GateFlags() if cli_flags is None else cli_flags
    logger.info("gate.account", phase=STARTUP_PHASE, status="started")

    decision, reported = _decide(node, settings, flags, reported_accounts)

    if decision.permitted:
        logger.info(
            "gate.account",
            phase=STARTUP_PHASE,
            status="ok",
            mode=decision.mode.value if decision.mode else None,
            accounts=masked_accounts(reported),
        )
        return decision

    refusal = decision.refusal or _UNEXPLAINED_REFUSAL
    # `gate.refused` is AR41's enumerated event name for exactly this event.
    # Logged before the shutdown attempt so the record exists even if stopping
    # then hangs or raises.
    logger.error(
        "gate.refused",
        phase=STARTUP_PHASE,
        status="failed",
        reason=refusal.reason.value,
        message=refusal.message,
    )
    await _stop_node(node)
    raise GateRefusedError(refusal)


def _decide(
    node: "TradingNode",
    settings: IBKRSettings,
    flags: GateFlags,
    reported_accounts: frozenset[str] | None,
) -> tuple[GateDecision, frozenset[str]]:
    """Produce the decision, converting *any* unexpected failure into a refusal.

    Every attribute reached here belongs to a third party — ``node.kernel``,
    ``node.trader``, ``IB_CLIENTS``, ``client.accounts()``. Without this guard a
    Nautilus rename would surface as an ``AttributeError`` propagating out of
    ``verify_connected_account`` with the node **still up, still connected, and
    about to have strategies started** — the precise outcome this module exists
    to prevent, and the opposite of what its fail-closed contract promises.
    """
    try:
        # Placement is checked before any account is read: called at the wrong
        # point in the sequence, whatever the gateway says cannot be trusted.
        misplaced = _placement_refusal(node)
        if misplaced is not None:
            return misplaced, frozenset()

        reported = (
            gateway_reported_accounts(settings)
            if reported_accounts is None
            else normalize_reported_accounts(reported_accounts)
        )
        return evaluate_account_gate(settings, flags, reported), reported
    except Exception as exc:  # noqa: BLE001 - an unverifiable account must refuse, not escape
        return (
            build_refusal(
                GateRefusalReason.ACCOUNT_VERIFICATION_ERROR,
                "Account verification could not complete: "
                f"{type(exc).__name__}. The connection is refused because an account that "
                "cannot be verified is indistinguishable from a real-money one.",
            ),
            frozenset(),
        )


#: Unreachable via any decision producer — every one of them populates the
#: refusal. Kept as a value rather than an ``assert`` because ``python -O`` strips
#: asserts, and losing that one would turn a refusal into an ``AttributeError``
#: on the safety-critical path. Its reason is ACCOUNT_VERIFICATION_ERROR, not
#: ACCOUNT_NOT_REPORTED: the latter has a documented operator meaning ("the
#: gateway named no account"), and stamping an internal invariant break with it
#: would send the operator to restart a healthy gateway.
_UNEXPLAINED_REFUSAL = GateRefusal(
    reason=GateRefusalReason.ACCOUNT_VERIFICATION_ERROR,
    message=(
        "Account verification refused the connection but supplied no reason; refusing to "
        "continue against an unexplained refusal."
    ),
)


def _placement_refusal(node: "TradingNode") -> GateDecision | None:
    """Refuse if this phase is running at the wrong point in the sequence.

    Returns ``None`` when the placement is correct. A misplacement refuses rather
    than raising: the operator outcome is identical to a failed account check —
    the node goes down and nothing trades — so it belongs on the same path.
    """
    exec_engine = node.kernel.exec_engine
    # `check_connected()` iterates the registered clients and returns True on an
    # empty dict (`execution/engine.pyx`), so on a node whose `build()` has not
    # run it reports "connected" without a socket ever having been opened. The
    # registration check is what makes this guard mean what its name says.
    if not exec_engine.registered_clients or not exec_engine.check_connected():
        return build_refusal(
            GateRefusalReason.NODE_NOT_CONNECTED,
            "Account verification ran before the execution client reported connected, so the "
            "gateway has not yet named an account. The gate:account phase must run after "
            "node:connect.",
        )

    started = sorted(
        str(strategy_id)
        for strategy_id, state in node.trader.strategy_states().items()
        if state not in NOT_YET_STARTED_STATES
    )
    if started:
        return build_refusal(
            GateRefusalReason.STRATEGY_STARTED_BEFORE_ACCOUNT_GATE,
            f"Strategies {', '.join(started)} were already started when account verification "
            "ran. The gate:account phase must run strictly before any strategy starts; "
            "verifying afterwards would bless an account that is already being traded.",
        )

    return None


async def _stop_node(node: "TradingNode") -> None:
    """Stop the node, reporting problems rather than raising.

    Best-effort by design: the refusal is the operator-critical signal, and a
    shutdown error must never replace it with a less useful exception. Three
    things are deliberately swallowed here, each for that reason:

    - **Any ``Exception``** — including the second stop of an already-stopping
      kernel.
    - **A timeout.** ``kernel.stop_async()`` awaits two operator-settable
      timeouts in series, so an unbounded await could withhold the refusal
      indefinitely.
    - **``asyncio.CancelledError``**, which is a ``BaseException`` and so would
      otherwise escape ``except Exception`` and abort the caller *before* the
      ``GateRefusedError`` is raised — leaving Story 1.7's exit-code mapping
      with nothing to map, on the one path where the answer matters most.

    Only the exception *type* is logged, never its message: adapter and broker
    error text routinely embeds the account identifier, and NFR26 admits no
    exception for a string that arrived from a third party.
    """
    try:
        await asyncio.wait_for(node.stop_async(), timeout=STOP_TIMEOUT_SECONDS)
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 - never mask the refusal
        logger.error(
            "gate.refused",
            phase=STARTUP_PHASE,
            status="failed",
            error_type=type(exc).__name__,
            error="node shutdown after refusal did not complete; the node may still be up",
        )
