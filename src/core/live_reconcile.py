"""The on-demand reconciliation check behind ``ntrader live reconcile`` (Story 4.6, FR36).

Owns: the check's sequence, its one wall-clock budget (NFR5), the read-only
node it runs on, and the ``reconcile.ok`` / ``reconcile.discrepancy`` records
of what it found. Does not own: reading the broker
(``src/core/live_broker_state.py``), reading the session's view
(``src/core/live_session_view.py``), the comparison — injected as ``compare``,
a port, because this module may not import ``src.services`` (AR38; the Story
3.5/3.6 ``sink`` precedent) — the session record, or the exit (the CLI).

**The sequence (D-F), each step before the next:**

1. ``gate:static`` — Layer 1, before any socket at all, so a refusal is always
   exit 3 whatever else is down (FR11).
2. The session-state precheck — Redis reachable, and the session's namespace
   not empty — before any IB socket: an unreachable Redis would hang the view
   read forever, and a session with nothing to compare should not cost an IB
   connection to discover.
3. A node on ``ibkr_live_client_id + 1`` (FR5, AR34) — so a running session on
   ``ibkr_live_client_id`` is never evicted — built, run, connected.
4. ``gate:account`` — Layer 2, on the account the Gateway actually reports.
5. The broker's positions and cash (Story 4.1's reader).
6. The session's view, read **immediately after** the broker, to keep the skew
   against a running session to the milliseconds a Redis read takes.
7. Compare; then teardown in ``finally``, on every path.

**Read-only is structural, not a flag.** The node carries no strategy, no
controller, no bar observer and an in-memory cache, so nothing on it can create
an order or write the session's Redis. ``ibkr_read_only`` is never read (AR43).
The builder's own Nautilus startup reconciliation still runs on this node — it
cannot be switched off without a builder change — and writes only to that
in-memory cache; the broker read joins its ``OpenPositions`` request by design.

**One budget, bounded where it can be and visible where it cannot.**
``RECONCILE_BUDGET_SECONDS`` (NFR5's 30 s) runs from the start. The connect
deadline sits inside it; the broker read gets what is left after a
``teardown_reserve``, capped at the reader's own default — and if nothing is
left, the check raises its own typed timeout rather than handing the reader a
non-positive timeout (which it refuses with an unmarked ``ValueError``). Two
steps are synchronous and cannot be interrupted from here: the adapter's own
connect attempt inside ``node.build()`` (about 20 s against a Gateway that is
down) and ``shutdown``'s teardown. So the whole check is timed once more after
teardown, and a run that overran is recorded as ``reconcile.budget_exceeded``
rather than passing silently.

**Success is a value, failure an exception.** A completed check returns a
``ReconciliationReport`` — clean (exit 0) or not (exit 5,
``LiveCheckOutcome.DISCREPANCY``). A check that could not complete raises its
typed failure, and the CLI maps it through ``classify_failure``.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog

from src.config import IBKRSettings, RedisSettings
from src.core.exit_outcome import EXIT_CODES, LiveCheckOutcome
from src.core.live_account_gate import gateway_reported_accounts, verify_connected_account
from src.core.live_broker_state import (
    DEFAULT_BROKER_STATE_TIMEOUT_SECONDS,
    NFR5_BUDGET_SECONDS,
    BrokerStateFailure,
    BrokerStateUnavailableError,
    _emit,
    read_broker_state,
)
from src.core.live_check import preflight_gate
from src.core.live_check_node import (
    await_connected,
    build_clients,
    current_event_loop,
    restore_event_loop,
    shutdown,
)
from src.core.live_gate import GateDecision
from src.core.live_node_builder import GateRefusedError, build_trading_node
from src.core.live_session_node import UNEXPLAINED_ACCOUNT_REFUSAL, refusal_from_report
from src.core.live_session_view import read_session_view, require_engine_state
from src.models.broker_state import BrokerState
from src.models.reconciliation import CashLine, PositionLine, ReconciliationReport, SessionView

logger = structlog.get_logger(__name__)

#: The reconcile node's identity. Carries a hyphen (Nautilus aborts without
#: one) and keeps the ``PAPER`` safety signal every trader id this system uses
#: carries; distinct from every session's ``PAPER-<8hex>``.
RECONCILE_TRADER_ID = "PAPER-RECONCILE"
#: NFR5: a reconciliation check completes in < 30 s.
RECONCILE_BUDGET_SECONDS = NFR5_BUDGET_SECONDS
#: How long the node may take to report connected. Deliberately not
#: ``IBKR_CONNECTION_TIMEOUT`` (300 s): a check exists to answer quickly.
CONNECT_TIMEOUT_SECONDS = 10.0
#: Held back from the broker read for the view read and teardown.
TEARDOWN_RESERVE_SECONDS = 5.0

OK_EVENT = "reconcile.ok"
DISCREPANCY_EVENT = "reconcile.discrepancy"
SHUTDOWN_EVENT = "reconcile.shutdown_problems"
BUDGET_EXCEEDED_EVENT = "reconcile.budget_exceeded"
#: Tells this story's records from Story 4.2's startup and 4.3's runtime ones,
#: which share AR41's ``reconcile.ok`` / ``reconcile.discrepancy`` names.
TRIGGER = "on_demand"

Compare = Callable[..., ReconciliationReport]
NodeFactory = Callable[..., Any]
AccountVerifier = Callable[..., Awaitable[GateDecision]]
BrokerReader = Callable[..., Awaitable[BrokerState]]
ViewReader = Callable[..., SessionView]
StateCheck = Callable[..., None]


@dataclass(frozen=True)
class ReconcileTarget:
    """The session whose view is checked — resolved by the CLI, plain values only."""

    name: str
    session_id: UUID
    status: str


@dataclass(frozen=True)
class ReconcileBudget:
    """D-F's one wall-clock budget, in seconds from the check's start.

    Every field is finite and positive; ``broker_read`` is within what the
    reader accepts (NFR5's 30 s); and ``connect + teardown_reserve < total``,
    so a node that connects inside its deadline always leaves the read some
    time. A budget that breaks any of these is a caller bug, refused here
    rather than silently disabling the bound (``min(20, nan)`` is ``20``).
    """

    total: float = RECONCILE_BUDGET_SECONDS
    connect: float = CONNECT_TIMEOUT_SECONDS
    teardown_reserve: float = TEARDOWN_RESERVE_SECONDS
    broker_read: float = DEFAULT_BROKER_STATE_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        for name in ("total", "connect", "teardown_reserve", "broker_read"):
            value = getattr(self, name)
            if not (isinstance(value, int | float) and math.isfinite(value) and value > 0):
                raise ValueError(f"budget {name} must be a finite positive number, got {value!r}")
        if self.broker_read > NFR5_BUDGET_SECONDS:
            raise ValueError(f"budget broker_read must be at most {NFR5_BUDGET_SECONDS:g} s")
        if self.connect + self.teardown_reserve >= self.total:
            raise ValueError("budget connect + teardown_reserve must be less than total")

    def read_timeout(self, elapsed: float) -> float:
        """What the broker read may spend: the reader's default, or what is left."""
        return min(self.broker_read, self.total - self.teardown_reserve - elapsed)


@dataclass(frozen=True)
class _Seams:
    compare: Compare
    node_factory: NodeFactory
    account_verifier: AccountVerifier
    broker_reader: BrokerReader
    view_reader: ViewReader


def reconcile_settings(settings: IBKRSettings) -> IBKRSettings:
    """``settings`` on ``ibkr_live_client_id + 1`` — the reconcile reservation (AR34).

    A copy: the caller's object is never mutated. Moving the one field moves
    the data client, the exec client, the account gate's ``IB_CLIENTS`` lookup
    and every log line together; ``validate_client_ids_distinct`` has already
    checked ``+ 1`` against the historical client on the original.
    """
    return settings.model_copy(update={"ibkr_live_client_id": settings.ibkr_live_client_id + 1})


def outcome_for(report: ReconciliationReport) -> LiveCheckOutcome:
    """``OK`` only when every line matches; otherwise ``DISCREPANCY``."""
    return LiveCheckOutcome.OK if report.is_clean else LiveCheckOutcome.DISCREPANCY


def exit_code_for(report: ReconciliationReport) -> int:
    return EXIT_CODES[outcome_for(report)]


def run_reconcile(
    settings: IBKRSettings,
    redis: RedisSettings,
    target: ReconcileTarget,
    *,
    compare: Compare,
    node_factory: NodeFactory = build_trading_node,
    account_verifier: AccountVerifier = verify_connected_account,
    broker_reader: BrokerReader = read_broker_state,
    view_reader: ViewReader = read_session_view,
    state_check: StateCheck = require_engine_state,
    budget: ReconcileBudget | None = None,
    log: Any = None,
) -> ReconciliationReport:
    """Run the whole check and return what it found.

    Args:
        settings: Loaded IBKR settings — the session's; the node runs on a
            ``+ 1`` copy. Injected, never fetched: the CLI is the composition
            root.
        redis: Where the session's engine cache lives.
        target: The session, already resolved.
        compare: ``reconciliation_service.compare`` — the port.
        node_factory, account_verifier, broker_reader, view_reader,
            state_check: The broker- and Redis-facing seams (NFR32).
        budget: The wall-clock budget; NFR5's by default.
        log: The caller's logger; the module logger otherwise.

    Returns:
        The report. ``is_clean`` decides exit 0 or 5.

    Raises:
        GateRefusedError, RedisUnreachableError, SessionViewUnavailableError,
        BrokerUnreachableError, BrokerStateUnavailableError: the check could
            not complete; nothing was compared.
    """
    log = logger if log is None else log
    budget = ReconcileBudget() if budget is None else budget
    started = time.monotonic()
    try:
        refusal = preflight_gate(settings, None)
        if refusal is not None:
            # Before anything is constructed or connected: no socket at all.
            raise GateRefusedError(refusal_from_report(refusal))
        state_check(target.session_id, redis, log=log)
        seams = _Seams(compare, node_factory, account_verifier, broker_reader, view_reader)
        report = _check_on_node(settings, redis, target, budget, started, seams, log)
    finally:
        _note_overrun(log, budget, started)
    _record_verdict(log, report, target)
    return report


def _check_on_node(
    settings: IBKRSettings,
    redis: RedisSettings,
    target: ReconcileTarget,
    budget: ReconcileBudget,
    started: float,
    seams: _Seams,
    log: Any,
) -> ReconciliationReport:
    """Steps 3-7 on a loop this function owns end to end (the ``live check`` shape)."""
    node_settings = reconcile_settings(settings)
    previous = current_event_loop()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node = run_task = None
    try:
        node = seams.node_factory(
            node_settings, trader_id=RECONCILE_TRADER_ID, cli_flags=None, loop=loop
        )
        # Set before `build()`, whose own connect attempt spends the same
        # budget, and never later than the budget allows.
        connect_deadline = min(
            time.monotonic() + budget.connect, started + budget.total - budget.teardown_reserve
        )
        build_clients(node, node_settings, trader_id=RECONCILE_TRADER_ID)
        run_task = loop.create_task(node.run_async())
        loop.run_until_complete(
            await_connected(node, run_task, connect_deadline, budget.connect, node_settings)
        )
        _verify_account(loop, node, node_settings, seams.account_verifier)
        broker = _read_broker(loop, node, budget, started, seams.broker_reader, log)
        view = seams.view_reader(target.session_id, settings.tws_account, redis, log=log)
        return seams.compare(
            broker, view, session_name=target.name, elapsed_ms=_elapsed_ms(started)
        )
    finally:
        problems = shutdown(node, run_task, loop)
        restore_event_loop(previous)
        if problems:
            _emit(log, "warning", SHUTDOWN_EVENT, problems=problems)


def _verify_account(
    loop: asyncio.AbstractEventLoop,
    node: Any,
    node_settings: IBKRSettings,
    verifier: AccountVerifier,
) -> None:
    """Layer 2 on the ``+ 1`` client, exactly as ``live check`` runs it.

    A raising account read goes back to the verifier (``reported_accounts=None``),
    whose own guard turns it into a refusal; a *returned* refusal is a refusal.
    """
    try:
        reported: frozenset[str] | None = gateway_reported_accounts(node_settings)
    except Exception:  # noqa: BLE001 - let the verifier's own guard classify it
        reported = None
    decision = loop.run_until_complete(
        verifier(node, node_settings, cli_flags=None, reported_accounts=reported)
    )
    if not decision.permitted:
        raise GateRefusedError(decision.refusal or UNEXPLAINED_ACCOUNT_REFUSAL)


def _read_broker(
    loop: asyncio.AbstractEventLoop,
    node: Any,
    budget: ReconcileBudget,
    started: float,
    reader: BrokerReader,
    log: Any,
) -> BrokerState:
    timeout = budget.read_timeout(time.monotonic() - started)
    if timeout <= 0:
        raise BrokerStateUnavailableError(
            BrokerStateFailure.TIMEOUT,
            f"the check's {budget.total:g} s budget was spent before the broker could be asked, "
            "so nothing was requested",
        )
    return loop.run_until_complete(reader(node, log=log, timeout_seconds=timeout))


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _note_overrun(log: Any, budget: ReconcileBudget, started: float) -> None:
    """Time the whole check once more — teardown included — and say if it overran."""
    total_ms = _elapsed_ms(started)
    if total_ms > budget.total * 1000:
        _emit(
            log,
            "warning",
            BUDGET_EXCEEDED_EVENT,
            total_ms=total_ms,
            budget_ms=budget.total * 1000,
        )


def _record_verdict(log: Any, report: ReconciliationReport, target: ReconcileTarget) -> None:
    """AR41's milestones: one ``reconcile.ok``, or one ``reconcile.discrepancy``
    per discrepant line — each naming its instrument or currency and both sides."""
    common = {
        "trigger": TRIGGER,
        "session_id": str(target.session_id),
        "trader_id": report.trader_id,
        "account": report.account,
        "elapsed_ms": report.elapsed_ms,
    }
    if report.is_clean:
        _emit(
            log,
            "info",
            OK_EVENT,
            position_count=len(report.positions),
            currencies=[line.currency for line in report.cash],
            **common,
        )
        return
    for line in report.discrepancies:
        _emit(log, "warning", DISCREPANCY_EVENT, **_discrepancy_fields(line, report), **common)


def _discrepancy_fields(
    line: PositionLine | CashLine, report: ReconciliationReport
) -> dict[str, Any]:
    if isinstance(line, PositionLine):
        return {
            "kind": "position",
            "instrument_id": line.instrument_id,
            "local_quantity": str(line.local_quantity),
            "broker_quantity": str(line.broker_quantity),
            "difference": str(line.difference),
        }
    recorded = report.local_cash_recorded_at
    return {
        "kind": "cash",
        "currency": line.currency,
        "local_cash": "unknown" if line.local_cash is None else str(line.local_cash),
        "broker_cash": "none" if line.broker_cash is None else str(line.broker_cash),
        "difference": None if line.difference is None else str(line.difference),
        # Story 4.7, D-C: "session cash as of" — since when the difference can be.
        "local_cash_recorded_at": None if recorded is None else recorded.isoformat(),
    }
