"""TradingNode assembly for IBKR paper trading.

Owns: building a ``TradingNodeConfig`` carrying IB data/exec client configs
behind the Layer 1 safety gate, constructing the node, registering the
Nautilus LogGuard when node construction is the one that claims the C
logging subsystem, and reading the resulting client's connection status
(``read_ibkr_connection_status`` — it lives here because the adapter's cache
key is the exact ``(host, port, client_id)`` triple this module configures).

Does not own: the node's lifecycle (build/run/stop/dispose — the runner does,
AR38), the session's ``trader_id`` (Epic 2 derives it), Redis caching
(Epic 2), REALTIME/RTH market-data settings (Story 1.5), or what a connection
status *means* — ``src/core/live_connection_monitor.py`` owns the state
machine and the trading-permission flag it derives.
"""

import asyncio

import structlog
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    IB_CLIENTS,
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from src.config import IBKRSettings
from src.core.live_connection_monitor import ConnectionStatus
from src.core.live_gate import GateFlags, GateRefusal, evaluate_gate, mask_account
from src.utils.logging import set_nautilus_log_guard

logger = structlog.get_logger(__name__)


class GateRefusedError(Exception):
    """The safety gate refused the connection; no client config was constructed."""

    def __init__(self, refusal: GateRefusal) -> None:
        super().__init__(refusal.message)
        self.refusal = refusal


class LiveNodeConfigError(Exception):
    """Configuration cannot produce a usable node."""


def _validate_trader_id(trader_id: str) -> str:
    """Reject a ``trader_id`` Nautilus would refuse — before it can abort us.

    ``TradingNodeConfig`` eagerly coerces this to a ``TraderId``, and a value
    containing no ``-`` **panics in Rust and aborts the process**
    (``crates/model/src/identifiers/trader_id.rs``). That is not a Python
    exception: ``except BaseException`` does not stop it, and under pytest it
    kills the worker rather than failing a test. An empty value takes a
    different path and raises a bare ``ValueError``, which is neither exception
    this module documents.

    Epic 2 still owns *deriving* the value (AR10); this only validates one it
    is handed, exactly as the ``TWS_ACCOUNT`` check does below.
    """
    resolved = trader_id.strip()
    if not resolved:
        raise LiveNodeConfigError(
            "Cannot build a TradingNode: trader_id is empty. "
            "Expected a value of the form '<NAME>-<ID>', e.g. 'PAPER-a1b2c3d4'."
        )
    if "-" not in resolved:
        raise LiveNodeConfigError(
            f"Cannot build a TradingNode: trader_id {resolved!r} contains no '-'. "
            "Nautilus requires the form '<NAME>-<ID>', e.g. 'PAPER-a1b2c3d4' — "
            "and aborts the process rather than raising if given anything else."
        )
    return resolved


def _resolve_account(settings: IBKRSettings) -> str:
    """Return the account in the casing IBKR itself reports.

    The gate compares ``account.upper().startswith(...)``
    (``src/core/live_gate.py:214``), so a lowercase ``du...`` is legitimately
    permitted. Passing that un-normalised to the exec client would produce
    ``AccountId("InteractiveBrokers-du4076626")`` and mismatch Story 1.4's
    Layer 2 verification against what the gateway reports. Normalise the way
    the gate's own check does.
    """
    account = settings.tws_account.strip().upper()
    if not account:
        raise LiveNodeConfigError(
            "Cannot build an IBKR execution client: TWS_ACCOUNT is not set. "
            "The gate permits an empty account on the paper path, but the "
            "execution client factory requires one."
        )
    return account


def _validate_timeouts(settings: IBKRSettings) -> None:
    """Reject non-positive timeouts, which mean 'expire immediately'.

    Neither field carries a ``ge=`` constraint in ``IBKRSettings``, and both
    are handed to ``asyncio.wait_for`` inside Nautilus — so ``0`` silently
    guarantees the session can never connect, surfacing as an opaque timeout
    rather than a configuration error. This module is their first consumer on
    a live path.
    """
    for name, value in (
        ("IBKR_CONNECTION_TIMEOUT", settings.ibkr_connection_timeout),
        ("IBKR_REQUEST_TIMEOUT", settings.ibkr_request_timeout),
    ):
        if value <= 0:
            raise LiveNodeConfigError(
                f"Cannot build an IBKR client: {name} is {value}, which asyncio "
                "treats as 'expire immediately'. Set a positive number of seconds."
            )


def build_trading_node_config(
    settings: IBKRSettings,
    *,
    trader_id: str,
    cli_flags: GateFlags | None = None,
) -> TradingNodeConfig:
    """Assemble a TradingNodeConfig for IBKR paper trading.

    Runs the Layer 1 safety gate before constructing any client config —
    ordering is the contract (AC #2, FR9), not merely the outcome: a refusal
    raises before ``InteractiveBrokersDataClientConfig`` or
    ``InteractiveBrokersExecClientConfig`` is ever instantiated.

    Args:
        settings: Loaded IBKR settings. Injected, never fetched — this module
            never calls ``get_settings()``.
        trader_id: Required, no default. Epic 2 owns deriving it from the
            session; this function does not invent one.
        cli_flags: Operator declarations from the command line.

    Returns:
        A configured ``TradingNodeConfig`` with one IB data client and one IB
        exec client, keyed by the adapter's own client key.

    Raises:
        GateRefusedError: The gate refused the connection.
        LiveNodeConfigError: The configuration cannot produce a usable node —
            a malformed ``trader_id``, an empty ``TWS_ACCOUNT``, or a
            non-positive timeout.
    """
    decision = evaluate_gate(settings, GateFlags() if cli_flags is None else cli_flags)
    if not decision.permitted:
        if decision.refusal is None:
            # Unreachable via evaluate_gate, whose only refusal producer always
            # populates this. Checked rather than asserted because `python -O`
            # strips asserts, and losing this one would turn a gate refusal
            # into an AttributeError on the safety-critical path.
            raise LiveNodeConfigError(
                "Safety gate refused the connection but supplied no refusal reason; "
                "refusing to build a node against an unexplained refusal."
            )
        raise GateRefusedError(decision.refusal)

    # Everything that can make the configuration unusable is settled before any
    # client config exists, so a bad input never reaches a third-party factory
    # (and, for trader_id, never reaches code that would abort the process).
    resolved_trader_id = _validate_trader_id(trader_id)
    account = _resolve_account(settings)
    _validate_timeouts(settings)

    # Declares the intent to trade on this in-process view only. Nothing
    # enforces against this flag elsewhere: the gate above is the load-bearing
    # control (NFR27, AR43) — see "The ibkr_read_only truth" in the story.
    trading_settings = settings.model_copy(update={"ibkr_read_only": False})

    # Both clients deliberately share one id. `get_cached_ib_client` is keyed on
    # (host, port, client_id) (adapters/interactive_brokers/factories.py:114), so
    # they share ONE socket rather than colliding on a second API connection.
    # This is not the collision FR5 forbids — that concerns the historical data
    # client on `ibkr_client_id`, a different socket entirely.
    data_client_config = InteractiveBrokersDataClientConfig(
        ibg_host=trading_settings.ibkr_host,
        ibg_port=trading_settings.ibkr_port,
        ibg_client_id=trading_settings.ibkr_live_client_id,
        connection_timeout=trading_settings.ibkr_connection_timeout,
        request_timeout=trading_settings.ibkr_request_timeout,
    )
    exec_client_config = InteractiveBrokersExecClientConfig(
        ibg_host=trading_settings.ibkr_host,
        ibg_port=trading_settings.ibkr_port,
        ibg_client_id=trading_settings.ibkr_live_client_id,
        connection_timeout=trading_settings.ibkr_connection_timeout,
        account_id=account,
    )

    logger.debug(
        "live_node.configured",
        client_id=trading_settings.ibkr_live_client_id,
        account=mask_account(account),
        trader_id=resolved_trader_id,
    )

    return TradingNodeConfig(
        trader_id=resolved_trader_id,
        data_clients={IB: data_client_config},
        exec_clients={IB: exec_client_config},
    )


def build_trading_node(
    settings: IBKRSettings,
    *,
    trader_id: str,
    cli_flags: GateFlags | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> TradingNode:
    """Build an unbuilt, unstarted TradingNode configured for IBKR paper trading.

    The gate runs inside ``build_trading_node_config``, so a refusal raises
    before a node exists. Registers the Nautilus LogGuard as early as the
    framework allows — immediately after construction, before returning (AC #4,
    AR20; ``NautilusKernel.__init__`` builds its own components before it
    returns, so no caller can register earlier than this). Returns the node
    unbuilt and not running: the runner owns ``build()``/``run()``/``stop()``
    (AR38).

    Args:
        settings: Loaded IBKR settings. Injected, never fetched.
        trader_id: Required, no default. Epic 2 owns deriving it.
        cli_flags: Operator declarations from the command line.
        loop: The event loop to bind the node to. Passed straight through to
            ``TradingNode``, which otherwise falls back to
            ``asyncio.get_event_loop()`` — quietly manufacturing an orphan loop
            nobody will ever run when called from synchronous code, so that a
            later ``node.stop()`` drives the wrong loop. Ownership of the loop
            stays with the caller (AR38); this only makes the binding explicit.

    Raises:
        GateRefusedError: The gate refused the connection.
        LiveNodeConfigError: The configuration cannot produce a usable node.
    """
    config = build_trading_node_config(settings, trader_id=trader_id, cli_flags=cli_flags)
    node = TradingNode(config=config, loop=loop)

    # When a BacktestEngine already claimed the C logging subsystem in this
    # process, NautilusKernel returns None here rather than a second guard
    # (kernel.py:190) — storing None would be meaningless.
    guard = node.kernel.get_log_guard()
    if guard is not None:
        set_nautilus_log_guard(guard)

    node.add_data_client_factory(IB, InteractiveBrokersLiveDataClientFactory)
    node.add_exec_client_factory(IB, InteractiveBrokersLiveExecClientFactory)

    return node


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
