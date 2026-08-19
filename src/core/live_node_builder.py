"""TradingNode assembly for IBKR paper trading.

Owns: building a ``TradingNodeConfig`` carrying IB data/exec client configs
behind the Layer 1 safety gate, constructing the node, and registering the
Nautilus LogGuard when node construction is the one that claims the C
logging subsystem.

Also owns the market-data half of that assembly (Story 1.5): the REALTIME
override, the RTH restriction, the instruments the provider must load, and the
market-data line budget check. The policy those rest on lives in
``live_market_data``; this module is where it reaches the client config.

Story 2.4 added the Redis engine cache seam: an optional ``cache`` config
reaches ``TradingNodeConfig``, and ``build_trading_node`` refuses an
unreachable Redis *before* constructing a node — that constructor blocks
forever otherwise. What a cache config contains, and the ``trader_id`` that
names its key namespace, live in ``src/core/live_cache.py`` and
``src/core/live_trader_id.py``.

Does not own: the node's lifecycle (build/run/stop/dispose — the runner does,
AR38), deriving the session's ``trader_id``, indicator warm-up from history
(Epic 4), reading the client's connection status
(``src/core/live_connection_probe.py``, extracted from here by Story 2.4), or
what a connection status *means* — ``src/core/live_connection_monitor.py``
owns the state machine and the trading-permission flag it derives.
"""

import asyncio
from collections.abc import Sequence

import structlog
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.config import CacheConfig, ImportableActorConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import BarType

from src.config import IBKRSettings
from src.core.live_bar_observer import LiveBarObserverConfig
from src.core.live_cache import check_redis_reachable
from src.core.live_gate import GateFlags, GateRefusal, evaluate_gate, mask_account
from src.core.live_market_data import (
    LiveMarketDataError,
    instrument_ids_for,
    resolve_live_bar_types,
    resolve_live_market_data_type,
    resolve_live_use_rth,
    validate_market_data_line_budget,
)
from src.utils.logging import set_nautilus_log_guard

# Dotted paths the Nautilus kernel resolves when it instantiates the observer.
# Kept as constants so the component tier can assert they still import, rather
# than discovering a typo when a node is being built against a live gateway.
BAR_OBSERVER_ACTOR_PATH = "src.core.live_bar_observer:LiveBarObserver"
BAR_OBSERVER_CONFIG_PATH = "src.core.live_bar_observer:LiveBarObserverConfig"

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
    bar_types: Sequence[str] = (),
    bar_observer: LiveBarObserverConfig | None = None,
    cli_flags: GateFlags | None = None,
    cache: CacheConfig | None = None,
) -> TradingNodeConfig:
    """Assemble a TradingNodeConfig for IBKR paper trading.

    Runs the Layer 1 safety gate before constructing any client config —
    ordering is the contract (AC #2, FR9), not merely the outcome: a refusal
    raises before ``InteractiveBrokersDataClientConfig`` or
    ``InteractiveBrokersExecClientConfig`` is ever instantiated. Every later
    check keeps that property: nothing that can fail runs after a client config
    exists, so a bad configuration never reaches connecting code.

    Args:
        settings: Loaded IBKR settings. Injected, never fetched — this module
            never calls ``get_settings()``.
        trader_id: Required, no default. Epic 2 owns deriving it from the
            session; this function does not invent one.
        bar_types: Bar types the session will subscribe to. Their instruments
            become the provider's ``load_ids`` — without which the adapter
            silently never delivers a bar — and their count is checked against
            the account's market-data line budget (Story 1.5, NFR16/NFR30).
            Empty is legal: a node with no subscriptions still builds.
        bar_observer: Observer configuration. When given, it is carried onto the
            node declaratively as an ``ImportableActorConfig`` so the kernel owns
            the actor's lifetime.
        cli_flags: Operator declarations from the command line.
        cache: Engine-cache configuration from ``live_cache.build_cache_config``.
            ``None`` (the default) leaves the node's cache in memory, which is
            what ``ntrader live check`` wants — a broker diagnostic must not
            require Redis. Building a config never contacts Redis either way;
            only ``build_trading_node`` does.

    Returns:
        A configured ``TradingNodeConfig`` with one IB data client and one IB
        exec client, keyed by the adapter's own client key.

    Raises:
        GateRefusedError: The gate refused the connection.
        LiveNodeConfigError: The configuration cannot produce a usable node —
            a malformed ``trader_id``, an empty ``TWS_ACCOUNT``, or a
            non-positive timeout.
        LiveMarketDataError: The market-data configuration cannot be honoured —
            a non-REALTIME type, RTH disabled, an unusable bar type, or more
            subscriptions than the account has market-data lines.
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

    # Market data resolves before any client config too. Both of these refuse
    # rather than degrade: a live session that quietly ran on delayed or
    # extended-hours bars would report prices it never actually traded on.
    market_data_type = resolve_live_market_data_type(settings)
    use_regular_trading_hours = resolve_live_use_rth(settings)

    resolved_bar_types = _reconcile_bar_types(bar_types, bar_observer)
    validate_market_data_line_budget(resolved_bar_types, budget=settings.ibkr_market_data_lines)

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
        # Both passed explicitly even though the adapter's own defaults happen to
        # agree today. A data-integrity property that depends on a third-party
        # default is one upgrade away from changing silently, and these two are
        # the difference between a session trading live RTH bars and one trading
        # 15-minute-old extended-hours bars while reporting them as live.
        market_data_type=market_data_type,
        use_regular_trading_hours=use_regular_trading_hours,
        # Contracts the session will subscribe to. A missing entry is not an
        # error at the adapter — `_subscribe_bars` logs "instrument not found"
        # and returns (data.py:248-254), so the subscription is silently dead.
        instrument_provider=InteractiveBrokersInstrumentProviderConfig(
            load_ids=frozenset(instrument_ids_for(resolved_bar_types)),
        ),
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
        bar_types=[str(bar_type) for bar_type in resolved_bar_types],
    )

    return TradingNodeConfig(
        trader_id=resolved_trader_id,
        data_clients={IB: data_client_config},
        exec_clients={IB: exec_client_config},
        actors=_actor_configs(bar_observer),
        # Nautilus reads the Redis key namespace from `trader_id` above, not
        # from `cache` — `CacheConfig` carries no trader id (kernel.py:303-311).
        # That is what makes the namespace per-session (AR10).
        cache=cache,
    )


def _reconcile_bar_types(
    bar_types: Sequence[str],
    bar_observer: LiveBarObserverConfig | None,
) -> tuple[BarType, ...]:
    """Return the one subscription set the node and the observer both act on.

    The observer is what actually calls ``subscribe_bars``; ``bar_types`` is what
    sizes the line-budget check and fills the instrument provider's
    ``load_ids``. Letting the two disagree produces the worst failure this module
    can produce: a session that connects, reports healthy, and never receives a
    bar, because ``_subscribe_bars`` logs ``instrument not found`` and returns
    for a contract nobody loaded (``data.py:248-254``). It would also let a
    caller slip past the market-data line budget entirely by naming its
    subscriptions only on the observer.

    So: an observer with no ``bar_types`` argument supplies the set; both
    supplied must name the same set; and a disagreement is refused rather than
    silently resolved in either direction.
    """
    if bar_observer is None:
        return resolve_live_bar_types(bar_types)

    observer_bar_types = resolve_live_bar_types(bar_observer.bar_types)
    if not bar_types:
        return observer_bar_types

    resolved = resolve_live_bar_types(bar_types)
    if {str(bar_type) for bar_type in resolved} != {
        str(bar_type) for bar_type in observer_bar_types
    }:
        raise LiveMarketDataError(
            "The bar types the node loads instruments for and the bar types the observer "
            "subscribes to must be the same set. "
            f"bar_types={sorted(str(bar_type) for bar_type in resolved)}, "
            f"observer={sorted(str(bar_type) for bar_type in observer_bar_types)}. "
            "A subscription whose instrument was never loaded is dropped by the IBKR adapter "
            "with a log line and no error, so the session would run and never see a bar."
        )
    return resolved


def _actor_configs(bar_observer: LiveBarObserverConfig | None) -> list[ImportableActorConfig]:
    """Carry the observer onto the node declaratively, or carry nothing.

    Declarative rather than ``node.trader.add_actor()``: the kernel then owns the
    actor's lifetime alongside every other component, and the whole node config
    stays serialisable. The dotted paths are resolved by Nautilus at build time,
    which is why the component tier asserts they still import.
    """
    if bar_observer is None:
        return []
    return [
        ImportableActorConfig(
            actor_path=BAR_OBSERVER_ACTOR_PATH,
            config_path=BAR_OBSERVER_CONFIG_PATH,
            config=bar_observer.dict(),
        )
    ]


def build_trading_node(
    settings: IBKRSettings,
    *,
    trader_id: str,
    bar_types: Sequence[str] = (),
    bar_observer: LiveBarObserverConfig | None = None,
    cli_flags: GateFlags | None = None,
    cache: CacheConfig | None = None,
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
        bar_types: Bar types the session subscribes to; see
            ``build_trading_node_config``.
        bar_observer: Observer configuration, attached declaratively.
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
        LiveMarketDataError: The market-data configuration cannot be honoured.
    """
    config = build_trading_node_config(
        settings,
        trader_id=trader_id,
        bar_types=bar_types,
        bar_observer=bar_observer,
        cli_flags=cli_flags,
        cache=cache,
    )

    # Strictly before TradingNode(...), because that constructor is the thing
    # that hangs: NautilusKernel builds a CacheDatabaseAdapter eagerly whenever
    # cache.database is set (kernel.py:300-312), and that adapter blocks forever
    # against an unreachable Redis — DatabaseConfig(timeout=) does not bound it.
    # After the gate, so a refused connection still reports as a gate refusal.
    if cache is not None and cache.database is not None:
        check_redis_reachable(cache.database.host, cache.database.port)

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
