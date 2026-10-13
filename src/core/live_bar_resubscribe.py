"""Make the IB adapter's bar resubscribe work after a reconnect (Story 5.9).

**This module patches an upstream defect** (PO ruling, Allay 2026-10-12: "fix it
ourselves"), in the ``live_exec_avg_px`` shape: a narrow instance-level patch,
applied where the node builder's factory builds the client.

The defect, read off the installed 1.220.0 wheel and driven for real by
``tests/component/core/test_live_bar_resubscribe.py``: ``_subscribe`` stores a
historical-bars subscription's handle as ``partial(subscribe_historical_bars,
bar_type=, contract=, use_rth=, handle_revised_bars=, start=)``
(``client/market_data.py:147-152, 457-465``). ``subscribe_historical_bars``
takes no ``start`` and requires a ``params`` (``:416-423``), so on every
reconnect ``_resubscribe_all`` (``client/client.py:325-342``) raises
``TypeError`` on that handle, logs it and carries on. Measured live (P23,
2026-10-12): the session stays connected, reconciled and permitted to trade,
and never receives another bar.

The patch is an **instance** attribute on the ``InteractiveBrokersClient``.
``_subscribe`` compares ``subscription_method == self.subscribe_historical_bars``
with both sides looked up at call time, so the stored partial wraps this
wrapper, keeping the keyword shape the adapter reads back (``start`` for the
history filter, ``handle_revised_bars``). A call **with** ``params`` is the
adapter's own initial path and is delegated unchanged. A call **without**
``params`` can only be the stored handle, i.e. a resubscribe: it is delegated
with ``params={"start_ns": None}``, which keeps the adapter's 300-bar window, and
the stored ``start`` keyword still filters the replay. Must be installed before
the first ``subscribe_bars`` (measured: a wrapper installed after leaves the
stored partial pointing at the stock method), so it runs inside
``node.build()``.

**Installed via the exec factory** because the client is shared: data and exec
use one ``(host, port, client_id)`` (``factories.py:112-127``), so the exec
client's ``_client`` is the data client's (story D-A). A data-factory subclass
would break two integration pins for nothing.

**What it records.** One :data:`RESUBSCRIBED_EVENT` per bar subscription whose
history request was re-issued. A resubscribe that raises anyway is logged as
:data:`RESUBSCRIBE_FAILED_EVENT` (bar type and error type, never the error's
text: third-party messages can carry the account, NFR26), noted on the client
for :class:`BarFeedWatch`, and re-raised, so the adapter's own log is
unchanged. PO ruling R2: the session then stops with exit ``1``, as adapter
drift does (Story 5.8), rather than sit up unable to hear a price.

**A refusal IB sends back** (code review P9, Allay 2026-10-13). IB refuses a
history request through its error callback, not by a raise: a pacing ``162``,
a ``504`` not-connected. The adapter retries only ``10189``/``366``/``102`` and
logs any other code as "Unknown subscription error", leaving the subscription
dead (``client/error.py:151-174``). So the shim also wraps the client's
``_handle_subscription_error`` (looked up on the instance by
``process_error``): an error on a request it re-issued, with a code the adapter
does not answer itself (``10182`` ends in another reconnect) and that is not a
warning, is a lost feed. It is noted and logged as :data:`RESUBSCRIBE_FAILED_EVENT`
(``error_type="ib_error"`` and the code), then delegated unchanged. A
subscription the session unsubscribes is removed before IB's ``162`` cancel
answer arrives, so teardown never trips it (P24's log, 2026-10-13).

**How this module should die.** ``TestTheAdapterDefect`` pins the stored
handle's keywords, the method's signature and the swallowing resubscribe. When
an upgrade fixes the adapter, they fail by name, and this module and its call
site in :mod:`src.core.live_node_builder` can be deleted.
"""

from typing import Any, ClassVar

import structlog

from src.core.exit_outcome import LiveCheckOutcome
from src.core.live_broker_state import BrokerStateAdapterError, find_ib_exec_client
from src.core.live_catch_up_gate import CATCH_UP_EVENT, CATCH_UP_ORDER_WITHHELD_EVENT
from src.core.live_runtime_reconcile import RuntimeReconciler

__all__ = [
    "EMITTED_BAR_FEED_EVENTS",
    "FAILURES_ATTRIBUTE",
    "METHOD_ATTRIBUTE",
    "RESUBSCRIBED_EVENT",
    "RESUBSCRIBE_FAILED_EVENT",
    "BarFeedLostError",
    "ERROR_HANDLER_ATTRIBUTE",
    "BarFeedWatch",
    "install_bar_resubscribe_fix",
    "watched_reconciler",
]

#: A bar subscription was restored after a reconnect.
RESUBSCRIBED_EVENT = "bars.resubscribed"

#: A bar subscription could not be restored after a reconnect; the session stops.
RESUBSCRIBE_FAILED_EVENT = "bars.resubscribe_failed"

#: Every record Story 5.9 emits — a membership-pinned list (CLAUDE.md
#: Anti-Patterns): pinned against the code both ways, and the NFR26 scan is
#: parametrized from it.
EMITTED_BAR_FEED_EVENTS: tuple[str, ...] = (
    RESUBSCRIBED_EVENT,
    RESUBSCRIBE_FAILED_EVENT,
    CATCH_UP_EVENT,
    CATCH_UP_ORDER_WITHHELD_EVENT,
)

#: The adapter method this module replaces, pinned against the wheel's source.
METHOD_ATTRIBUTE = "subscribe_historical_bars"

#: Where a failed resubscribe is noted on the client, as ``(bar_type, error_type)``.
FAILURES_ATTRIBUTE = "_ntrader_bar_feed_failures"

#: The adapter's subscription-error handler, wrapped to notice an IB refusal.
ERROR_HANDLER_ATTRIBUTE = "_handle_subscription_error"

#: The stock method, kept so a second install wraps it once, not twice.
_STOCK_ATTRIBUTE = "_ntrader_stock_subscribe_historical_bars"

#: The stock error handler, kept for the same reason.
_STOCK_ERROR_ATTRIBUTE = "_ntrader_stock_handle_subscription_error"

#: The request ids of the bar subscriptions the shim re-issued.
_REISSUED_ATTRIBUTE = "_ntrader_bar_feed_reissued"

#: Codes the adapter answers itself: retried, or (10182) another reconnect.
_ADAPTER_ANSWERED_CODES = frozenset({10189, 366, 102, 10182})

logger = structlog.get_logger(__name__)


class BarFeedLostError(RuntimeError):
    """A bar subscription could not be restored after a reconnect (PO ruling R2)."""

    exit_outcome: ClassVar[LiveCheckOutcome] = LiveCheckOutcome.ERROR
    operator_safe_message: ClassVar[bool] = True


def install_bar_resubscribe_fix(client: Any) -> None:
    """Replace ``client.subscribe_historical_bars`` with one a resubscribe can call.

    Args:
        client: The shared ``InteractiveBrokersClient``, before any bar
            subscription is made.

    Raises:
        AttributeError: If the client has no ``subscribe_historical_bars`` or
            ``_handle_subscription_error`` — the upstream shape this targets has
            moved. Loud at build time on purpose, the ``live_exec_avg_px``
            precedent.
    """
    stock = getattr(client, _STOCK_ATTRIBUTE, None) or getattr(client, METHOD_ATTRIBUTE)
    failures: list[tuple[str, str]] = getattr(client, FAILURES_ATTRIBUTE, [])
    reissued: set[int] = getattr(client, _REISSUED_ATTRIBUTE, set())

    async def subscribe_historical_bars(
        bar_type: Any,
        contract: Any,
        use_rth: bool,
        handle_revised_bars: bool,
        params: dict | None = None,
        start: int | None = None,
    ) -> None:
        del start  # carried by the stored partial; the adapter's history filter reads it there
        call = {
            "bar_type": bar_type,
            "contract": contract,
            "use_rth": use_rth,
            "handle_revised_bars": handle_revised_bars,
        }
        if params is not None:
            await stock(**call, params=params)
            return
        subscription = client._subscriptions.get(name=str(bar_type))
        if subscription is not None:
            reissued.add(subscription.req_id)
        try:
            await stock(**call, params={"start_ns": None})
        except Exception as exc:
            failures.append((str(bar_type), type(exc).__name__))
            logger.error(
                RESUBSCRIBE_FAILED_EVENT, bar_type=str(bar_type), error_type=type(exc).__name__
            )
            raise
        logger.info(RESUBSCRIBED_EVENT, bar_type=str(bar_type))

    _install_refusal_watch(client, failures, reissued)
    setattr(client, _STOCK_ATTRIBUTE, stock)
    setattr(client, FAILURES_ATTRIBUTE, failures)
    setattr(client, _REISSUED_ATTRIBUTE, reissued)
    setattr(client, METHOD_ATTRIBUTE, subscribe_historical_bars)


def _install_refusal_watch(
    client: Any, failures: list[tuple[str, str]], reissued: set[int]
) -> None:
    """Note an IB error on a re-issued bar request as a lost feed (code review P9)."""
    stock = getattr(client, _STOCK_ERROR_ATTRIBUTE, None) or getattr(
        client, ERROR_HANDLER_ATTRIBUTE
    )
    warnings = frozenset(getattr(client, "WARNING_CODES", ()))

    async def handle_subscription_error(req_id: int, error_code: int, error_string: str) -> None:
        subscription = client._subscriptions.get(req_id=req_id)
        refused = (
            error_code not in _ADAPTER_ANSWERED_CODES
            and error_code not in warnings
            and not 2100 <= error_code < 2200
        )
        if subscription is not None and req_id in reissued and refused:
            bar_type = str(subscription.name)
            failures.append((bar_type, f"IB error {error_code}"))
            logger.error(
                RESUBSCRIBE_FAILED_EVENT,
                bar_type=bar_type,
                error_type="ib_error",
                error_code=error_code,
            )
        await stock(req_id, error_code, error_string)

    setattr(client, _STOCK_ERROR_ATTRIBUTE, stock)
    setattr(client, ERROR_HANDLER_ATTRIBUTE, handle_subscription_error)


class BarFeedWatch:
    """The runtime reconciler, stopped first by a bar feed that could not be restored.

    Wraps the ``reconciler`` the steady-state tick awaits, duck-typed to its
    two members (``on_tick()`` and ``withheld``), so the runner gains no
    statement. A failed resubscribe ends the session on the next tick through
    the same path adapter drift takes (Story 5.8): raised out of the tick,
    ``live start`` exits ``1``. It is checked **before** the reconciler runs, so
    a clean reconnect cycle can never re-grant trading to a session that cannot
    hear a price.
    """

    def __init__(self, reconciler: Any, node: Any) -> None:
        self._reconciler = reconciler
        self._node = node

    @property
    def withheld(self) -> Any:
        """The reconciler's withheld-period ledger (Story 5.8), unchanged."""
        return getattr(self._reconciler, "withheld", None)

    async def on_tick(self) -> None:
        """Raise if a bar subscription was lost; otherwise run the reconciler.

        Raises:
            BarFeedLostError: A resubscribe failed since the session started.
        """
        failure = self._first_failure()
        if failure is not None:
            bar_type, error_type = failure
            raise BarFeedLostError(
                f"The bar feed for {bar_type} could not be restored after a reconnect "
                f"({error_type}). The session stopped rather than stay connected without "
                "prices; restart it."
            )
        await self._reconciler.on_tick()

    def _first_failure(self) -> tuple[str, str] | None:
        try:
            client = find_ib_exec_client(self._node)._client
        except (BrokerStateAdapterError, AttributeError):
            return None  # no IB client: the reconciler names that on its own path
        failures = getattr(client, FAILURES_ATTRIBUTE, None)
        return failures[0] if failures else None


def watched_reconciler(**kwargs: Any) -> BarFeedWatch:
    """Build the session's ``RuntimeReconciler`` from ``kwargs``, watched.

    A factory rather than a wrap at the call site, so the runner, at its size
    ceiling (story F13), changes by one name and gains no line.
    """
    return BarFeedWatch(RuntimeReconciler(**kwargs), kwargs["node"])
