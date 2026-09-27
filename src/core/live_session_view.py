"""Read a session's own view of what it holds, from its engine cache (Story 4.6, FR36).

Owns: reading a session's durable engine cache — its Redis namespace,
``trader-PAPER-<8hex>:`` (Story 2.4) — **load-only**, from outside any node,
and converting what it holds into ``src.models.reconciliation.SessionView`` at
the boundary (AR38). Does not own: the broker's side
(``src/core/live_broker_state.py``), the comparison
(``src/services/reconciliation_service.py``), or the node.

**Why the engine cache, not the trades table.** ``trades`` holds closed round
trips only (``TradeRecorder`` persists ``PositionClosed`` and nothing else), so
a trades-derived position is always flat — exactly the "failure reads as flat"
shape NFR20 forbids. Nothing in PostgreSQL records a session's cash. The engine
cache is the only local record of what the session believes it holds:

- **Positions** — ``positions:<id>`` is the position's fill list; the adapter's
  ``load_position`` replays it (a NETTING close-then-reopen or flip replays to
  the current leg, because ``Position.apply`` resets at FLAT). The session's
  view of an instrument is the net over its open positions **on the configured
  account**: one per strategy under NETTING, plus any ``EXTERNAL`` one
  Nautilus's reconciliation created. Positions held for another account (the
  account changed since the session ran) are left out — symmetric with the
  broker reader's own account filter — and counted in a warning.
- **Cash** — ``general:accountSummary:<account>`` is the IB exec client's
  account summary as last pushed to the session's engine; its
  ``TotalCashValue`` is the session's cash, normalised by the **same** function
  the broker reader uses, so normalisation can never manufacture or hide a
  difference. The key names the account as the exec client knew it — trimmed
  and upper-cased by the node builder — so it is looked up the same way.

**Load-only, measured.** Against a real Redis with ``MONITOR`` capturing, the
adapter's ``keys``/``load_position``/``load`` issue only ``SCAN``, ``LRANGE``,
``GET``, ``MGET``, ``INFO`` and ``CLIENT SETINFO``, and the namespace is
byte-identical afterwards (``tests/integration/core/
test_live_session_view_redis.py``). Constructing the adapter does not
initialise Nautilus's C logging. This module calls those three load methods
and ``close()`` — nothing that writes — and the component suite pins both the
set and the absence of any mutator: a second writer into a running session's
namespace would be the auto-resolution FR35 forbids.

**A missing or unreadable view is a failure, never "flat".** An empty
namespace (the session never started, or Redis was flushed), a position that
cannot be rebuilt (``load_positions()`` would drop it silently — so this module
enumerates the keys itself), a corrupt fill list, a malformed summary, or an
adapter that cannot be opened each raise :class:`SessionViewUnavailableError`.
A namespace with no summary for the account is not a failure: cash is then
*unknown* (``SessionView.cash == ()``).

**NFR26.** The summary key embeds the raw account. No key name, no raw account
and no adapter exception text reaches a record or an exception message.
"""

import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, TypeVar
from uuid import UUID

import msgspec
import structlog
from nautilus_trader.cache.config import CacheConfig
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import PositionId, TraderId
from nautilus_trader.serialization.serializer import MsgSpecSerializer

from src.config import RedisSettings
from src.core.exit_outcome import LiveCheckOutcome

# Private by name, shared on purpose: both sides of the comparison must be
# normalised by one function, and emitted through one never-raising sink.
from src.core.live_broker_state import _cash_from, _emit
from src.core.live_cache import build_cache_config, check_redis_reachable
from src.core.live_trader_id import derive_trader_id
from src.models.broker_state import CashBalance
from src.models.reconciliation import SessionView, ViewPosition

logger = structlog.get_logger(__name__)

T = TypeVar("T")

#: The general-cache key the IB exec client writes its account summary under,
#: followed by its ``account_id.get_id()`` (``adapters/interactive_brokers/
#: execution.py``). Pinned against the real adapter by a component canary.
ACCOUNT_SUMMARY_KEY_PREFIX = "accountSummary:"
#: The collection the adapter keeps each position's fill list in.
POSITIONS_COLLECTION = "positions"
#: The adapter members this module reads through. ``close()`` releases the
#: handle; nothing else is called. Pinned as an exact set, mutator-free.
LOAD_METHODS = ("keys", "load_position", "load")

READ_EVENT = "reconcile.session_view_read"
FAILED_EVENT = "reconcile.session_view_failed"
OTHER_ACCOUNT_EVENT = "reconcile.session_view_other_account"


class SessionViewFailure(StrEnum):
    """Why a session's own view could not be read."""

    NO_ENGINE_STATE = "no_engine_state"
    UNREADABLE = "unreadable"
    ADAPTER_INCOMPATIBLE = "adapter_incompatible"


class SessionViewUnavailableError(RuntimeError):
    """The session's engine-cache view could not be read. Never "flat".

    Exit 1: a session with nothing to compare, or a cache that cannot be read,
    is not a broker outage — exit 4 would send the operator to restart a
    healthy Gateway. The message is this module's own text only.

    Attributes:
        reason: The :class:`SessionViewFailure`.
        detail: This module's own description of what happened.
        error_type: The type name of the exception that caused it, if any.
    """

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True

    def __init__(
        self, reason: SessionViewFailure, detail: str, *, error_type: str | None = None
    ) -> None:
        super().__init__(f"The session's local view is unavailable ({reason.value}): {detail}")
        self.reason = reason
        self.detail = detail
        self.error_type = error_type


def normalised_account(account: str) -> str:
    """The account as the session's exec client knew it.

    ``live_node_builder._resolve_account`` trims and upper-cases
    ``TWS_ACCOUNT`` before it becomes the exec client's ``account_id`` — the id
    that names the summary key and stamps every position. Duplicated rather
    than imported (the builder's is private and raises on empty), so a test
    pins the two equal.
    """
    return account.strip().upper()


def _open_adapter(trader_id: str, config: CacheConfig) -> CacheDatabaseAdapter:
    """A load-only handle on ``trader_id``'s namespace, built as ``NautilusKernel``
    builds its own (``system/kernel.py:300-312``) — so it reads the keys a
    restarted session would."""
    encoding = config.encoding.lower()
    return CacheDatabaseAdapter(
        trader_id=TraderId(trader_id),
        instance_id=UUID4(),
        serializer=MsgSpecSerializer(
            encoding=msgspec.msgpack if encoding == "msgpack" else msgspec.json,
            timestamps_as_str=True,
            timestamps_as_iso8601=config.timestamps_as_iso8601,
        ),
        config=config,
    )


def _reachable(host: str | None, port: int | None) -> None:
    check_redis_reachable(host, port)


def require_engine_state(
    session_id: UUID,
    redis: RedisSettings,
    *,
    log: Any = None,
    adapter_factory: Callable[[str, CacheConfig], Any] = _open_adapter,
    reachability: Callable[[str | None, int | None], None] = _reachable,
) -> None:
    """Refuse, cheaply and before any IB connection, a session with no engine state.

    The full read (:func:`read_session_view`) still runs after the broker read,
    to keep the skew against a running session small; this only answers "is
    there anything to compare at all?" without costing an IB connection.

    Raises:
        RedisUnreachableError: Redis cannot be reached.
        SessionViewUnavailableError: ``NO_ENGINE_STATE``, or the namespace
            cannot be listed.
    """
    log = logger if log is None else log
    trader_id = derive_trader_id(session_id)
    started = time.monotonic()
    reachability(redis.redis_host, redis.redis_port)
    try:
        _using(
            adapter_factory, trader_id, redis, lambda adapter: _require_state(adapter, trader_id)
        )
    except SessionViewUnavailableError as failure:
        _log_failure(log, failure, trader_id, started)
        raise


def read_session_view(
    session_id: UUID,
    account: str,
    redis: RedisSettings,
    *,
    log: Any = None,
    adapter_factory: Callable[[str, CacheConfig], Any] = _open_adapter,
    reachability: Callable[[str | None, int | None], None] = _reachable,
) -> SessionView:
    """Read what ``session_id``'s engine cache says the account holds.

    Args:
        session_id: The session's UUID business key; its namespace is
            ``derive_trader_id(session_id)``.
        account: The configured account, **raw** (``TWS_ACCOUNT``) — normalised
            here the way the node builder does, used only to pick the summary
            key and filter positions, never logged.
        redis: Where the engine cache lives.
        log: The caller's logger; the module logger otherwise.
        adapter_factory: Builds the load-only adapter (test seam).
        reachability: The Redis preflight (test seam).

    Returns:
        The session's view: net open positions per instrument on the account,
        and its last-recorded cash (``()`` when unknown).

    Raises:
        RedisUnreachableError: Redis cannot be reached — refused before the
            adapter is built, because its constructor blocks forever.
        SessionViewUnavailableError: The view is missing or unreadable.
    """
    log = logger if log is None else log
    trader_id = derive_trader_id(session_id)
    started = time.monotonic()
    reachability(redis.redis_host, redis.redis_port)
    target = normalised_account(account)
    try:
        view, skipped = _using(
            adapter_factory, trader_id, redis, lambda adapter: _read(adapter, trader_id, target)
        )
    except SessionViewUnavailableError as failure:
        _log_failure(log, failure, trader_id, started)
        raise
    if skipped:
        _emit(log, "warning", OTHER_ACCOUNT_EVENT, trader_id=trader_id, positions_skipped=skipped)
    _emit(
        log,
        "info",
        READ_EVENT,
        trader_id=trader_id,
        position_count=len(view.positions),
        positions={p.instrument_id: str(p.quantity) for p in view.positions},
        currencies=[balance.currency for balance in view.cash],
        cash={balance.currency: str(balance.total_cash) for balance in view.cash},
        elapsed_ms=_elapsed_ms(started),
    )
    return view


def _using(
    adapter_factory: Callable[[str, CacheConfig], Any],
    trader_id: str,
    redis: RedisSettings,
    read: Callable[[Any], T],
) -> T:
    """Open the adapter (a failure to open is "unreadable"), read, always close."""
    adapter = _call(
        lambda: adapter_factory(trader_id, build_cache_config(redis)), "opening the engine cache"
    )
    try:
        return read(adapter)
    finally:
        _close_quietly(adapter)


def _require_state(adapter: Any, trader_id: str) -> None:
    keys = _member(adapter, "keys")
    if not _call(lambda: keys("*"), "listing the namespace"):
        raise SessionViewUnavailableError(
            SessionViewFailure.NO_ENGINE_STATE,
            f"the engine cache holds nothing for {trader_id} — the session has never run, "
            "or its Redis was flushed — so there is no local view to compare",
        )


def _read(adapter: Any, trader_id: str, account: str) -> tuple[SessionView, int]:
    keys, load_position, load = (_member(adapter, name) for name in LOAD_METHODS)
    _require_state(adapter, trader_id)
    # SCAN may return a key more than once; a position counts once.
    position_keys = _call(
        lambda: sorted(set(keys(f"{POSITIONS_COLLECTION}:*"))), "listing positions"
    )
    positions, skipped = _net_positions(load_position, position_keys, account)
    general = _call(load, "reading the general cache")
    if not isinstance(general, Mapping):
        raise SessionViewUnavailableError(
            SessionViewFailure.UNREADABLE, "the general cache has an unexpected shape"
        )
    summary = general.get(f"{ACCOUNT_SUMMARY_KEY_PREFIX}{account}")
    return SessionView(trader_id=trader_id, positions=positions, cash=_cash(summary)), skipped


def _member(adapter: Any, name: str) -> Callable[..., Any]:
    member = getattr(adapter, name, None)
    if not callable(member):
        raise SessionViewUnavailableError(
            SessionViewFailure.ADAPTER_INCOMPATIBLE,
            f"the cache adapter has no callable {name!r}",
        )
    return member


def _call(read: Callable[[], T], what: str) -> T:
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - any failure to read is "unreadable", never "flat"
        raise SessionViewUnavailableError(
            SessionViewFailure.UNREADABLE,
            f"{what} failed",
            error_type=type(exc).__name__,
        ) from None


def _position_id(key: Any) -> str:
    _, found, position_id = str(key).partition(f":{POSITIONS_COLLECTION}:")
    if not isinstance(key, str) or not found or not position_id:
        raise SessionViewUnavailableError(
            SessionViewFailure.UNREADABLE, "a position key has an unexpected shape"
        )
    return position_id


def _held(position: Any) -> tuple[str, str, Decimal] | None:
    """``(account, instrument_id, signed quantity)`` of an open position; ``None`` if closed."""
    if not position.is_open:
        return None
    return (
        str(position.account_id.get_id()),
        str(position.instrument_id),
        Decimal(position.signed_decimal_qty()),
    )


def _net_positions(
    load_position: Callable[[PositionId], Any], position_keys: list[str], account: str
) -> tuple[tuple[ViewPosition, ...], int]:
    """Rebuild every position key — each one, or fail — and net the open ones
    held on ``account``. Returns the rows and how many other-account ones were
    left out."""
    net: dict[str, Decimal] = defaultdict(Decimal)
    skipped = 0
    for key in position_keys:
        position_id = _position_id(key)
        position = _call(
            lambda: load_position(PositionId(position_id)), f"rebuilding position {position_id}"
        )
        if position is None:
            # F4: `load_positions()` would drop this silently — a partial "flat".
            raise SessionViewUnavailableError(
                SessionViewFailure.UNREADABLE,
                f"position {position_id} is in the engine cache but could not be rebuilt "
                "(its instrument is missing), so the view would under-report what is held",
            )
        held = _call(lambda: _held(position), f"reading position {position_id}")
        if held is None:
            continue
        position_account, instrument_id, quantity = held
        if position_account != account:
            skipped += 1
            continue
        net[instrument_id] += quantity
    rows = tuple(
        ViewPosition(instrument_id=instrument_id, quantity=quantity)
        for instrument_id, quantity in sorted(net.items())
        if quantity != 0
    )
    return rows, skipped


def _cash(summary: Any) -> tuple[CashBalance, ...]:
    """The session's last-recorded ``TotalCashValue`` per currency; ``()`` if none."""
    if summary is None:
        return ()
    try:
        decoded = json.loads(summary)
    except (ValueError, TypeError) as exc:
        raise SessionViewUnavailableError(
            SessionViewFailure.UNREADABLE,
            "the recorded account summary is not valid JSON",
            error_type=type(exc).__name__,
        ) from None
    if not isinstance(decoded, Mapping) or not all(
        isinstance(tags, Mapping) for tags in decoded.values()
    ):
        raise SessionViewUnavailableError(
            SessionViewFailure.UNREADABLE, "the recorded account summary has an unexpected shape"
        )
    return _cash_from(decoded)


def _close_quietly(adapter: Any) -> None:
    try:
        adapter.close()
    except Exception:  # noqa: BLE001 - closing a read handle must not change the outcome
        pass


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


def _log_failure(
    log: Any, failure: SessionViewUnavailableError, trader_id: str, started: float
) -> None:
    fields: dict[str, Any] = {"reason": failure.reason.value, "detail": failure.detail}
    if failure.error_type:
        fields["error_type"] = failure.error_type
    _emit(
        log, "error", FAILED_EVENT, trader_id=trader_id, elapsed_ms=_elapsed_ms(started), **fields
    )
