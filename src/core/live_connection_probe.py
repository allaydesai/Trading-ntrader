"""Reading the live session's IBKR socket status (extracted from Story 1.6).

Owns: ``read_ibkr_connection_status`` — one polled reading of whether the live
session's IBKR client is actually connected — and the two fail-closed helpers it
reads the adapter's private flags through.

Does not own: what a connection status *means*. ``src/core/live_connection_monitor.py``
owns the state machine and the trading-permission flag it derives. Node assembly
is ``src/core/live_node_builder.py``.

Extracted from ``live_node_builder`` by Story 2.4, unchanged: adding the Redis
cache took that module past this repo's 500-line file limit, and this was the
part that was least about assembling a node. The lookup key is still the exact
``(host, port, client_id)`` triple ``build_trading_node_config`` configures, so
the two must be kept in step by hand — that coupling was the original reason
they shared a module, and it is now a comment rather than an adjacency.
``tests/component/core/test_live_connection_probe.py`` is this module's suite
and was already named for it.
"""

import structlog
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS

from src.config import IBKRSettings
from src.core.live_connection_monitor import ConnectionStatus

logger = structlog.get_logger(__name__)


def _flag_is_set(client: object, name: str) -> bool:
    """Read one of the adapter's private connection flags, fail-closed.

    The whole read — attribute access included — sits inside the guard, because
    the two flags this depends on are private to a third-party adapter and
    ``getattr``'s own default only swallows ``AttributeError``: a cache entry
    exposing ``_is_ib_connected`` as a *property that raises* would otherwise
    propagate straight into the caller's poll loop. If a Nautilus upgrade
    renames or retypes either flag, the correct outcome is "disconnected" —
    trading permission withheld — never an exception.
    ``tests/component/core/test_live_connection_probe.py`` carries a canary that
    fails by name when either flag moves, so the degradation is never silent.
    """
    try:
        flag = getattr(client, name, None)
        if flag is None:
            return False
        return bool(flag.is_set())
    except Exception:  # noqa: BLE001 - a hostile cache entry must not raise here
        return False


def _client_is_unusable(client: object) -> bool:
    """Whether a cached client is a corpse left behind by a previous node.

    ``IB_CLIENTS`` is never purged — grep the whole wheel and there is one
    assignment, a ``get`` and an ``in``, and no deletion anywhere. Worse,
    ``TradingNode.dispose()`` closes the loop without ``cancel_all_tasks()``
    (``live/node.py:449-458``), so a pending ``_stop_async`` may never run and
    both connection flags can be left *set* on a client whose node is gone. A
    later read under the same key would then report a healthy connection for a
    node that no longer exists — fail-open on the one path that must fail
    closed. Absent attributes mean "cannot tell", which is treated as disposed.
    """
    try:
        if bool(getattr(client, "is_disposed", False)):
            return True
        return not bool(getattr(client, "is_running", False))
    except Exception:  # noqa: BLE001 - a hostile cache entry must not raise here
        return True


def read_ibkr_connection_status(settings: IBKRSettings) -> ConnectionStatus:
    """Take one reading of the live session's broker connection.

    Polled, not subscribed. At nautilus-trader 1.220.0 an IBKR socket drop
    publishes no message-bus event and changes no public connection property:
    the adapter's watchdog calls the ``_degrade`` *hook* directly rather than
    the ``degrade()`` FSM transition (``client/client.py:384``), and
    ``is_connected`` — hence ``DataEngine.check_connected()`` — only moves on
    the ``connect()``/``disconnect()`` lifecycle (``live/data_client.py:229,243``).
    Using ``check_connected()`` here would yield a permission flag that is
    permanently ``True`` through a dead socket, which is the blind trading NFR10
    forbids. The adapter's own two flags are the truth.

    Purely a read: it never mutates the client, never starts or stops it, and
    never awaits — so it is safe to call from a synchronous poll and cannot
    influence the connection it is measuring.

    Args:
        settings: Loaded IBKR settings. Injected, never fetched. The lookup key
            is the same ``(host, port, client_id)`` triple
            ``build_trading_node_config`` hands to both client configs, which is
            why this function lives in this module — the key cannot drift from
            the configured one.

    Returns:
        A ``ConnectionStatus``. Fail-closed on every uncertainty: an absent
        client, a missing flag, or an unrecognisable cache entry all report
        ``connected=False`` with a ``detail`` naming what was wrong.
    """
    client_key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
    # IB_CLIENTS (adapters/interactive_brokers/factories.py:42) is the adapter's
    # own process-global cache — the same dict `get_cached_ib_client` populates,
    # so this finds the very client both of our configs are bound to. It also
    # holds the historical data client on `ibkr_client_id`, which is why the key
    # is exact rather than a search (FR5).
    client = IB_CLIENTS.get(client_key)
    if client is None:
        return ConnectionStatus(
            connected=False,
            detail=f"no ib client registered for client_id={settings.ibkr_live_client_id}",
        )

    if _client_is_unusable(client):
        return ConnectionStatus(connected=False, detail="ib client is stopped or disposed")

    if not _flag_is_set(client, "_is_ib_connected"):
        return ConnectionStatus(connected=False, detail="ib socket not connected")

    if not _flag_is_set(client, "_is_client_ready"):
        # Cleared by the adapter's `_degrade()` on connection loss and set again
        # by `_start_async()` once the reconnect handshake completes. Note what
        # it does NOT mean: `_resume_async` waits on this flag and only *then*
        # runs `_resubscribe_all()` (`client/client.py:204,304`), so both flags
        # are set throughout the resubscription window. This is the best signal
        # the adapter exposes, not a guarantee that subscriptions are live —
        # see the IB error-1101 note in deferred-work.md.
        return ConnectionStatus(connected=False, detail="ib client not ready")

    return ConnectionStatus(connected=True, detail="ib socket connected, client ready")
