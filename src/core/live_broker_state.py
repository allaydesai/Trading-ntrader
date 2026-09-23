"""Read the broker's authoritative view of positions and cash (Story 4.1, FR32).

Owns: asking IBKR — through the IB adapter the node already holds — what the
configured account actually holds, and converting the answer into
``src.models.broker_state`` values at the boundary (AR38). Does not own: what a
difference between that answer and the session's own view *means* (Story 4.2's
startup check, 4.3's runtime alignment, 4.6's on-demand report) — nothing here
compares or resolves anything.

**Why neither Nautilus API is used** (measured against 1.220.0, Story 4.1's
F1-F6):

- **Positions.** The adapter's ``get_positions`` returns ``None`` for a timeout,
  a lost connection *and* an account with zero positions;
  ``generate_position_status_reports`` turns all three into ``[]``; and
  ``reconcile_execution_state`` counts a client whose mass status raised as
  reconciled. Nothing in the framework can tell "flat" from "failed". This
  module issues the same ``reqPositions`` through ``get_positions`` but reads
  the verdict from the adapter's own request **future**, read-only: a list —
  even ``[]`` — is IBKR's ``positionEnd``; a ``ConnectionError`` is a lost
  socket; a cancelled future means some awaiter's ``wait_for`` gave up on it —
  normally the adapter's own 30 s timeout, but any cancelled awaiter does the
  same — so IBKR's answer never arrived; a future still pending at our deadline
  is a request IBKR never answered.
- **Cash.** Nautilus's ``AccountState`` ``total`` is ``NetLiquidation`` — and,
  when maintenance margin exceeds half of it, the adapter substitutes a literal
  ``400000`` (``# TODO: Bug``). IBKR's cash is the ``TotalCashValue`` tag, which
  the exec client keeps, per currency, in its in-memory ``_account_summary``.
  The ``accountSummary:<acct>`` general-cache key holds the same data but is
  Redis-persisted, so across a restart it serves the previous process's values.

**Success is a value, failure an exception.** ``read_broker_state`` returns a
``BrokerState`` — possibly flat — or raises ``BrokerStateUnavailableError``
naming a :class:`BrokerStateFailure`. There is no third shape, so no caller can
read a failure as "flat" (NFR20).

**What it touches.** The adapter calls are ``get_positions`` — which registers
(or joins) the ``OpenPositions`` request exactly as Nautilus's own
reconciliation does — and ``instrument_provider.get_instrument``. The reader
itself never adds, ends, cancels or re-subscribes a request. One deadline
bounds the whole read (NFR5), enforced with ``asyncio.wait`` — never
``wait_for``: cancelling ``get_positions`` would cancel the ``OpenPositions``
future that Nautilus's own reconciliation may be awaiting too. What the reader
started is left to finish on its own when it gives up: an unanswered request
ends on the adapter's 30 s timeout, and a slow resolution may still load an
instrument into the provider afterwards — both exactly what Nautilus's own
callers leave behind.

**Freshness.** Positions are a fresh request. Cash is IBKR's latest
account-summary push received by this process: in full at the exec client's
connect, then as IBKR updates it — seconds old at startup and on a freshly
connected reconcile node, possibly older mid-session (routed to 4.3/4.7).

Duck-typed, with no ``nautilus_trader`` import: the adapter's private members
this depends on are pinned by canaries in
``tests/component/core/test_live_broker_state_adapter.py``, which fail by name
on an upgrade that moves them. A member that has moved is reported as
``BrokerStateAdapterError`` (exit 1), never as a broker outage.
"""

import asyncio
import math
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, ClassVar

import structlog

from src.core.exit_outcome import LiveCheckOutcome
from src.core.live_gate import mask_account
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance, is_currency_code

logger = structlog.get_logger(__name__)

#: One deadline over the whole read. Under NFR5's 30 s so that Story 4.6's
#: on-demand check keeps ~10 s of its own budget for connect/compare/disconnect.
DEFAULT_BROKER_STATE_TIMEOUT_SECONDS = 20.0
#: NFR5's bound on a reconciliation check; no read may be given longer.
NFR5_BUDGET_SECONDS = 30.0

#: The IB exec client's ``ClientId`` value (the adapter's ``IB`` constant).
IB_EXEC_CLIENT_ID = "INTERACTIVE_BROKERS"
#: The name ``get_positions`` registers its request under (and joins by).
OPEN_POSITIONS_REQUEST = "OpenPositions"
#: The IB client's two readiness flags (``live_connection_probe``'s truth).
READINESS_FLAGS = ("_is_ib_connected", "_is_client_ready")
#: IBKR's cash tag — not ``NetLiquidation``, not Nautilus's ``AccountBalance``.
CASH_TAG = "TotalCashValue"
#: The identifier a position gets when its contract cannot be resolved.
UNRESOLVED_PREFIX = "IB-CONID-"
#: ibapi's "no value" sentinels (``ibapi/const.py``), pinned by a canary. Its
#: decoder returns ``UNSET_DECIMAL`` for an empty quantity field.
IB_UNSET_DOUBLE = sys.float_info.max
IB_UNSET_DECIMAL = Decimal(2**127 - 1)
POLL_SECONDS = 0.05

RETRIEVED_EVENT = "reconcile.broker_state_retrieved"
FAILED_EVENT = "reconcile.broker_state_failed"
UNRESOLVED_EVENT = "reconcile.broker_instrument_unresolved"


class BrokerStateFailure(StrEnum):
    """Why the broker's view could not be read."""

    NOT_CONNECTED = "not_connected"
    TIMEOUT = "timeout"
    POSITIONS_UNANSWERED = "positions_unanswered"
    CONNECTION_LOST = "connection_lost"
    CASH_UNAVAILABLE = "cash_unavailable"
    ADAPTER_INCOMPATIBLE = "adapter_incompatible"


class BrokerStateUnavailableError(RuntimeError):
    """The broker's positions and cash could not be read. Never "flat".

    Exit 4 (AR28's broker connectivity failure): the broker did not answer, or
    is not connected. Drift in the adapter is the subclass below (exit 1). The
    message is this module's own text only — never broker or exception text.

    Attributes:
        reason: The :class:`BrokerStateFailure`.
        detail: This module's own description of what happened.
        error_type: The type name of the exception that caused it, if any.
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.BROKER_UNREACHABLE
    operator_safe_message: ClassVar[bool] = True

    def __init__(
        self, reason: BrokerStateFailure, detail: str, *, error_type: str | None = None
    ) -> None:
        super().__init__(f"Broker state unavailable ({reason.value}): {detail}")
        self.reason = reason
        self.detail = detail
        self.error_type = error_type


class BrokerStateAdapterError(BrokerStateUnavailableError):
    """The adapter (or the node) no longer looks the way this module reads it.

    Exit 1, not 4: an adapter rename, a missing exec client, or a read on the
    wrong loop is a code problem, and a connectivity exit would send the
    operator to restart a healthy Gateway (the account gate's
    ``ACCOUNT_VERIFICATION_ERROR`` precedent).
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True

    def __init__(self, detail: str, *, error_type: str | None = None) -> None:
        super().__init__(BrokerStateFailure.ADAPTER_INCOMPATIBLE, detail, error_type=error_type)


@dataclass(frozen=True)
class _Deadline:
    """One deadline on the loop's own clock — the clock ``asyncio.wait`` uses."""

    loop: asyncio.AbstractEventLoop
    started: float
    budget: float

    def remaining(self) -> float:
        return max(0.0, self.started + self.budget - self.loop.time())

    def elapsed_ms(self) -> float:
        return round((self.loop.time() - self.started) * 1000, 3)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def read_broker_state(
    node: Any,
    *,
    log: Any = None,
    timeout_seconds: float = DEFAULT_BROKER_STATE_TIMEOUT_SECONDS,
    utc_now: Callable[[], datetime] = _utc_now,
) -> BrokerState:
    """Ask IBKR what the node's configured account holds.

    Must run on the node's own loop (``loop.run_until_complete(...)``, the
    ``gate:account`` precedent) — checked. Works on any connected node with an
    IB exec client — the session's (``ibkr_live_client_id``) or a read-only
    reconcile node's (``+ 1``, AR34) — because it finds its IB client through
    the node.

    Args:
        node: A built, running, connected ``TradingNode``.
        log: The caller's (session-bound) logger; the module logger otherwise.
        timeout_seconds: The one deadline over the whole read, in
            ``(0, NFR5_BUDGET_SECONDS]``.
        utc_now: Clock for ``BrokerState.retrieved_at``.

    Returns:
        The broker's view. ``positions == ()`` is a flat account — a success.

    Raises:
        ValueError: ``timeout_seconds`` is outside ``(0, 30]`` — a caller bug,
            refused before anything is requested.
        BrokerStateUnavailableError: The view could not be read, for the
            ``reason`` it carries. ``BrokerStateAdapterError`` (a subclass)
            when the adapter's or node's shape has drifted.
    """
    if not (0 < timeout_seconds <= NFR5_BUDGET_SECONDS):
        raise ValueError(f"timeout_seconds must be in (0, {NFR5_BUDGET_SECONDS:g}]")
    log = logger if log is None else log
    loop = asyncio.get_running_loop()
    deadline = _Deadline(loop, loop.time(), timeout_seconds)
    masked: str | None = None
    try:
        exec_client = find_ib_exec_client(node)
        ib_client, account = exec_client._client, exec_client.account_id.get_id()
        masked = mask_account(account)
        _require_ready(exec_client, ib_client, loop)
        rows = await _read_positions(ib_client, account, deadline)
        positions = await _to_positions(exec_client.instrument_provider, rows, deadline, log)
        cash = await _read_cash(exec_client, deadline)
        state = BrokerState(account=masked, positions=positions, cash=cash, retrieved_at=utc_now())
    except BrokerStateUnavailableError as failure:
        _log_failure(log, failure, masked, deadline.elapsed_ms())
        raise
    except Exception as exc:  # noqa: BLE001 - any other raise is drift, and never "flat"
        # `from None`: the adapter's exception text may carry the raw account
        # (NFR26). Its type and raise site are enough to find what moved.
        drift = BrokerStateAdapterError(
            f"reading broker state raised {type(exc).__name__} at {_raise_site(exc)}",
            error_type=type(exc).__name__,
        )
        _log_failure(log, drift, masked, deadline.elapsed_ms())
        raise drift from None
    _emit(
        log,
        "info",
        RETRIEVED_EVENT,
        account=state.account,
        position_count=len(state.positions),
        positions={p.instrument_id: str(p.quantity) for p in state.positions},
        cash={balance.currency: str(balance.total_cash) for balance in state.cash},
        elapsed_ms=deadline.elapsed_ms(),
    )
    return state


def find_ib_exec_client(node: Any) -> Any:
    """The node's IB exec client, found by its ``ClientId`` value.

    ``exec_engine._clients`` is ``cdef readonly`` — readable from Python;
    ``registered_clients`` would give ids only. None registered is a node that
    was never built (or was built without IB): a code problem, not an outage.
    """
    for client_id, client in node.kernel.exec_engine._clients.items():
        if str(client_id) == IB_EXEC_CLIENT_ID:
            return client
    raise BrokerStateAdapterError(
        f"no execution client {IB_EXEC_CLIENT_ID!r} is registered on this node"
    )


def _raise_site(exc: BaseException) -> str:
    """``file:line function`` where ``exc`` was raised — no message text."""
    tb = exc.__traceback__
    if tb is None:
        return "unknown"
    while tb.tb_next is not None:
        tb = tb.tb_next
    code = tb.tb_frame.f_code
    return f"{os.path.basename(code.co_filename)}:{tb.tb_lineno} {code.co_name}"


def _require_ready(exec_client: Any, ib_client: Any, loop: asyncio.AbstractEventLoop) -> None:
    """Refuse before issuing anything: wrong loop, unfinished connect, dead socket.

    A *missing* member is drift (exit 1); a present flag that is unset is a
    real "not connected" (exit 4). ``exec_engine.check_connected()`` stays
    ``True`` through a dead socket, so the IB client's own flags are the truth.
    """
    if ib_client._loop is not loop:
        raise BrokerStateAdapterError("read_broker_state must run on the node's own event loop")
    if not exec_client.is_connected:
        raise BrokerStateUnavailableError(
            BrokerStateFailure.NOT_CONNECTED,
            "the IB execution client has not finished connecting, so nothing was requested",
        )
    for name in READINESS_FLAGS:
        flag = getattr(ib_client, name, None)
        if flag is None or not callable(getattr(flag, "is_set", None)):
            raise BrokerStateAdapterError(f"the IB client has no readiness flag {name}")
        if not flag.is_set():
            raise BrokerStateUnavailableError(
                BrokerStateFailure.NOT_CONNECTED,
                f"the IB client is not connected ({name} is not set), so nothing was requested",
            )


def _consume_outcome(future: asyncio.Future) -> None:
    """Mark a detached task's outcome retrieved; the reader has its answer."""
    if not future.cancelled():
        future.exception()


async def _read_positions(ib_client: Any, account: str, deadline: _Deadline) -> list[Any]:
    """Issue (or join) ``OpenPositions`` and classify IBKR's answer from its future."""
    # Captured *before* the task runs: when joining an in-flight request, its
    # answer could otherwise land and be removed before the first observation.
    request = ib_client._requests.get(name=OPEN_POSITIONS_REQUEST)
    task = asyncio.ensure_future(ib_client.get_positions(account))
    task.add_done_callback(_consume_outcome)
    if request is None:
        request = await _observe_request(ib_client, task, deadline)
    # `asyncio.wait` never cancels what it waits on (D-C); `wait_for` would.
    # The task is watched too: `get_positions` raising after it registered
    # (a send on a dead socket) must not cost the whole deadline.
    await asyncio.wait(
        {request.future, task},
        timeout=deadline.remaining(),
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not request.future.done() and task.done():
        raise _task_failure(task)
    return _answered_rows(request.future, account, deadline)


async def _observe_request(ib_client: Any, task: asyncio.Future, deadline: _Deadline) -> Any:
    """Find the request ``get_positions`` registers (synchronously, before its
    first ``await`` — so normally on the first look after one yield)."""
    pause = 0.0
    while True:
        await asyncio.sleep(pause)
        request = ib_client._requests.get(name=OPEN_POSITIONS_REQUEST)
        if request is not None:
            return request
        if task.done():
            error = None if task.cancelled() else task.exception()
            raise BrokerStateAdapterError(
                "the positions request was never registered, so an empty answer could not be "
                "told from a failed one",
                error_type=None if error is None else type(error).__name__,
            )
        if deadline.remaining() <= 0:
            raise _timed_out("the positions request could not be observed", deadline)
        pause = POLL_SECONDS


def _task_failure(task: asyncio.Future) -> BrokerStateUnavailableError:
    """``get_positions`` ended without its request's future resolving."""
    error = None if task.cancelled() else task.exception()
    if isinstance(error, OSError):  # ConnectionError included
        return BrokerStateUnavailableError(
            BrokerStateFailure.CONNECTION_LOST,
            "sending the positions request to IBKR failed",
            error_type=type(error).__name__,
        )
    return BrokerStateAdapterError(
        "the positions request ended without IBKR's answer",
        error_type=None if error is None else type(error).__name__,
    )


def _answered_rows(future: asyncio.Future, account: str, deadline: _Deadline) -> list[Any]:
    if not future.done():
        raise _timed_out("IBKR did not answer the positions request", deadline)
    if future.cancelled():
        raise BrokerStateUnavailableError(
            BrokerStateFailure.POSITIONS_UNANSWERED,
            "the positions request was cancelled before IBKR answered (the adapter's own "
            "timeout, or another awaiter giving up)",
        )
    error = future.exception()
    if isinstance(error, ConnectionError):
        raise BrokerStateUnavailableError(
            BrokerStateFailure.CONNECTION_LOST,
            "the connection to IBKR was lost while the positions request was in flight",
            error_type=type(error).__name__,
        )
    if isinstance(error, TimeoutError):
        raise BrokerStateUnavailableError(
            BrokerStateFailure.POSITIONS_UNANSWERED,
            "the adapter timed the positions request out",
            error_type=type(error).__name__,
        )
    if error is not None:
        raise BrokerStateAdapterError(
            "the positions request failed", error_type=type(error).__name__
        )
    rows = future.result()
    if not isinstance(rows, list):
        raise BrokerStateAdapterError(f"the positions answer is a {type(rows).__name__}")
    # `reqPositions` answers for every account on the login (F10).
    return [row for row in rows if row.account_id == account]


def _timed_out(what: str, deadline: _Deadline) -> BrokerStateUnavailableError:
    return BrokerStateUnavailableError(
        BrokerStateFailure.TIMEOUT, f"{what} within {deadline.budget:g} s"
    )


async def _to_positions(
    provider: Any, rows: Sequence[Any], deadline: _Deadline, log: Any
) -> tuple[BrokerPosition, ...]:
    """Normalise IBKR's rows (D-G) and resolve each contract's instrument id (D-F)."""
    latest: dict[int, Any] = {}
    for row in rows:
        if not row.contract.conId:
            # The decoder's default for an empty field: last-wins would merge
            # distinct positions under it and under-report the account.
            raise BrokerStateAdapterError("a position row carries no contract id")
        # F4: a streaming update can be appended to an in-flight request, so a
        # contract may appear twice. Each row is the full position: last wins.
        latest[row.contract.conId] = row
    held = [row for row in latest.values() if _quantity(row.quantity) != 0]
    resolved = await _resolve(provider, [row.contract for row in held], deadline)
    positions = [
        _to_position(row, instrument_id, error_type, log)
        for row, (instrument_id, error_type) in zip(held, resolved, strict=True)
    ]
    _refuse_shared_instrument_ids(positions)
    return tuple(sorted(positions, key=lambda position: position.instrument_id))


def _refuse_shared_instrument_ids(positions: Sequence[BrokerPosition]) -> None:
    by_instrument: dict[str, list[int]] = {}
    for position in positions:
        by_instrument.setdefault(position.instrument_id, []).append(position.con_id)
    shared = {iid: sorted(ids) for iid, ids in by_instrument.items() if len(ids) > 1}
    if shared:
        raise BrokerStateAdapterError(
            f"distinct contracts resolve to one instrument, so positions cannot be told apart: "
            f"{shared}"
        )


async def _resolve(
    provider: Any, contracts: Sequence[Any], deadline: _Deadline
) -> list[tuple[str | None, str | None]]:
    """``get_instrument`` per contract — the resolution Nautilus's own
    reconciliation uses, so ids match the cache. No-cancel, like positions."""
    tasks = [asyncio.ensure_future(provider.get_instrument(contract)) for contract in contracts]
    for task in tasks:
        task.add_done_callback(_consume_outcome)
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=deadline.remaining())
        if pending:
            raise _timed_out("instrument resolution did not finish", deadline)
    return [_instrument_id(task) for task in tasks]


def _instrument_id(task: asyncio.Future) -> tuple[str | None, str | None]:
    """A resolved id, or ``None`` and why — never a raise (D-F keeps the row)."""
    if task.cancelled():
        return None, "CancelledError"
    error = task.exception()
    if error is not None:
        return None, type(error).__name__
    instrument_id = getattr(task.result(), "id", None)
    if instrument_id is None:
        return None, "NoInstrument"
    return str(instrument_id), None


def _to_position(
    row: Any, instrument_id: str | None, error_type: str | None, log: Any
) -> BrokerPosition:
    contract = row.contract
    if instrument_id is None:
        _emit(
            log,
            "warning",
            UNRESOLVED_EVENT,
            con_id=contract.conId,
            symbol=contract.symbol,
            sec_type=contract.secType,
            error_type=error_type,
        )
    return BrokerPosition(
        instrument_id=instrument_id or f"{UNRESOLVED_PREFIX}{contract.conId}",
        quantity=_quantity(row.quantity),
        average_price=_average_price(row.avg_cost, contract.multiplier),
        con_id=int(contract.conId),
        symbol=str(contract.symbol),
        instrument_resolved=instrument_id is not None,
    )


def _quantity(value: Any) -> Decimal:
    """IBKR's quantity arrives as ``Decimal``; anything else goes via ``str``,
    never ``Decimal(float)``'s binary noise. ibapi's unset sentinel (an empty
    field) is refused: it is not a 1.7e38-share position."""
    quantity = value if isinstance(value, Decimal) else Decimal(str(value))
    if quantity == IB_UNSET_DECIMAL or not quantity.is_finite():
        raise BrokerStateAdapterError("a position row carries no usable quantity")
    return quantity


def _finite_decimal(value: Any) -> Decimal | None:
    """A reported number, or ``None`` when absent, non-finite, or ibapi's
    ``UNSET_DOUBLE`` sentinel (``sys.float_info.max``)."""
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or abs(value) >= IB_UNSET_DOUBLE):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _average_price(avg_cost: Any, multiplier: Any) -> Decimal | None:
    cost = _finite_decimal(avg_cost)
    if cost is None or cost <= 0:
        return None
    try:
        factor = Decimal(str(multiplier).strip())
    except InvalidOperation:
        factor = Decimal(1)
    if not factor.is_finite() or factor <= 0:
        factor = Decimal(1)
    return cost / factor


async def _read_cash(exec_client: Any, deadline: _Deadline) -> tuple[CashBalance, ...]:
    """Wait (bounded) for ``TotalCashValue``: the exec client's connect returns
    after the account summary's *first* tag (F5), so cash can still be in flight.

    Returns as soon as one currency has it. The adapter subscribes ``AllTags``
    without ``$LEDGER``, so only the base currency ever arrives (F7) and that
    is the whole answer; a wider subscription would need a completeness signal
    (routed in ``deferred-work.md``).
    """
    while True:
        cash = _cash_from(exec_client._account_summary)
        if cash:
            return cash
        if deadline.remaining() <= 0:
            raise BrokerStateUnavailableError(
                BrokerStateFailure.CASH_UNAVAILABLE,
                f"IBKR has not reported {CASH_TAG} for this account within "
                f"{deadline.budget:g} s; cash is unknown, not zero",
            )
        await asyncio.sleep(min(POLL_SECONDS, deadline.remaining()))


def _cash_from(summary: Mapping[str, Mapping[str, Any]]) -> tuple[CashBalance, ...]:
    """Cash per real currency. Tags without one (``""``) and pseudo-currencies
    such as ``BASE`` are skipped, never allowed to fail the whole read."""
    balances = []
    for currency, tags in list(summary.items()):
        if not is_currency_code(currency):
            continue
        amount = _finite_decimal(tags.get(CASH_TAG))
        if amount is not None:
            balances.append(CashBalance(currency=currency, total_cash=amount))
    return tuple(sorted(balances, key=lambda balance: balance.currency))


def _emit(log: Any, level: str, event: str, **fields: Any) -> None:
    """Log without ever raising — not even resolving ``log.<level>``: a failing
    sink must not replace the typed failure the caller is owed, nor turn a
    good read into an exception."""
    try:
        getattr(log, level)(event, **fields)
    except Exception:  # noqa: BLE001 - diagnostics must not change the outcome
        pass


def _log_failure(
    log: Any, failure: BrokerStateUnavailableError, masked: str | None, elapsed_ms: float
) -> None:
    fields: dict[str, Any] = {"reason": failure.reason.value, "detail": failure.detail}
    if masked:
        fields["account"] = masked
    if failure.error_type:
        fields["error_type"] = failure.error_type
    _emit(log, "error", FAILED_EVENT, elapsed_ms=elapsed_ms, **fields)


def render_broker_state(state: BrokerState) -> list[str]:
    """Operator-readable lines (the probe's output); the account is masked."""
    lines = [
        f"broker state account={state.account} positions={len(state.positions)} "
        f"flat={state.is_flat}"
    ]
    lines.extend(_render_position(position) for position in state.positions)
    lines.extend(f"cash {b.currency} total_cash={b.total_cash}" for b in state.cash)
    return lines


def _render_position(position: BrokerPosition) -> str:
    price = "unknown" if position.average_price is None else str(position.average_price)
    line = f"position {position.instrument_id} qty={position.quantity:+} avg_price={price}"
    if not position.instrument_resolved:
        line += f" (unresolved symbol={position.symbol})"
    return line
