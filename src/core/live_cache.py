"""The Redis-backed Nautilus engine cache for a live session (Story 2.4, AR10).

Owns: translating ``RedisSettings`` into the ``CacheConfig`` a live session's
node runs with, and the reachability preflight that must run before that node
is constructed.

Does not own: the ``trader_id`` that names the key namespace
(``src/core/live_trader_id.py``), node assembly or lifecycle
(``src/core/live_node_builder.py`` and Story 2.5's runner), the session record
(``src/services/session_service.py``), or reconciliation.

**Redis is a disposable cache, and nothing here enforces that.** It holds engine
*cache* state — orders, positions, accounts, instruments — all of which is
rebuildable from IBKR. IBKR is authoritative on any conflict. Flushing this
Redis loses no system of record: closed trades live in PostgreSQL and the
session's own identity lives in ``trading_sessions``. What this module does
*not* do is detect or resolve a conflict between cached state and broker state;
startup reconciliation is Epic 4's (FR35, AR25). Stating the policy is AC #5;
implementing it is not this story.

Two Nautilus facts this module is built around, both executed against the
installed 1.220.0 rather than read from documentation:

1. **An unreachable Redis hangs forever, it does not raise.**
   ``CacheDatabaseAdapter.__init__`` blocks indefinitely, and
   ``DatabaseConfig(timeout=...)`` does not bound it. ``NautilusKernel.__init__``
   constructs that adapter eagerly whenever ``config.cache.database`` is set
   (``system/kernel.py:300-312``), and ``TradingNode(config=...)`` constructs the
   kernel — so without ``check_redis_reachable`` below, starting a session with
   Redis down produces a process that prints nothing and never returns.
   ``RedisCacheDatabase`` is Rust-side and exposes no reachability probe, and
   there is no Python Redis client in this project (AR3: zero new dependencies),
   so the preflight is a stdlib socket. Note that "unreachable" is wider than
   "nothing is listening": an endpoint that completes the TCP handshake and then
   never answers hangs the adapter identically, so ``check_redis_reachable``
   requires a ``+PONG`` and refuses everything else.
2. **Three ``CacheConfig`` fields decide the key namespace, and all three are
   defaults.** ``use_trader_prefix=True``, ``use_instance_id=False``,
   ``flush_on_start=False``. FR19's "a restarted process rejoins its own state"
   is true *only* because of them: ``instance_id`` is a fresh UUID4 per process,
   so ``use_instance_id=True`` would send every restart to a new namespace, and
   ``flush_on_start=True`` would wipe it. They are passed explicitly here for
   the reason ``live_node_builder`` gives for doing the same to
   ``market_data_type``: a data-integrity property that rests on a third-party
   default is one upgrade away from changing silently.
"""

import math
import socket

import structlog
from nautilus_trader.config import CacheConfig, DatabaseConfig

from src.config import RedisSettings

logger = structlog.get_logger(__name__)

#: Long enough to absorb a loaded local machine, short enough that an operator
#: reads it as "failed" rather than "thinking". The failure it bounds is
#: otherwise unbounded, so any finite value is an improvement; this one is
#: chosen to be unambiguous.
DEFAULT_REACHABILITY_TIMEOUT_SECONDS = 2.0


class RedisUnreachableError(Exception):
    """The engine cache's Redis could not be used; no node was constructed.

    Covers "nothing is listening" and "something is listening but it is not a
    Redis that answers" alike — from a caller's point of view both mean the same
    thing, because both hang ``CacheDatabaseAdapter.__init__`` forever.
    """


def _unreachable(host: str, port: int, detail: str) -> RedisUnreachableError:
    """Build the single refusal this module raises, however the check failed.

    One message shape for every failure path, so an operator gets the host, the
    port, what actually went wrong, and the two remedies regardless of which
    branch refused.
    """
    return RedisUnreachableError(
        f"Cannot use the engine cache's Redis at {host}:{port} — {detail}. "
        "A live session cannot start without it: constructing a Nautilus node against "
        "an unusable Redis blocks forever rather than failing. Start the service "
        "('docker compose up -d redis', or 'brew services start redis'), or set "
        "REDIS_HOST / REDIS_PORT to the instance you intend to use."
    )


def _validate_timeout(timeout: float) -> float:
    """Reject a timeout that cannot bound a wait.

    Mirrors ``live_check_driver._validate_window``: a ``nan`` comparison is
    always ``False`` and an infinite wait never ends, so neither can bound the
    hang this module exists to bound — and passing either to
    ``socket.create_connection`` reinstates exactly the failure being guarded
    against. ``bool`` is excluded explicitly because ``True`` is an ``int``.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(f"timeout must be a finite positive number of seconds, got {timeout!r}.")
    if math.isnan(timeout) or math.isinf(timeout) or timeout <= 0:
        raise ValueError(
            f"timeout is {timeout!r}, which cannot bound a wait: a NaN comparison is always "
            "False and an infinite one never ends, so either would restore the unbounded hang "
            "this check exists to prevent. Pass a positive number of seconds."
        )
    return float(timeout)


def build_cache_config(settings: RedisSettings) -> CacheConfig:
    """Build the ``CacheConfig`` a live session's node runs with (AC #3, AC #7).

    The returned config carries **no** ``trader_id``: Nautilus takes that from
    ``TradingNodeConfig.trader_id`` and hands it to the cache database itself
    (``system/kernel.py:303-311``), which is what makes the Redis namespace
    per-session. Deriving that value is ``src/core/live_trader_id.py``'s job.

    Args:
        settings: Redis connection settings. Injected, never fetched — this
            module never calls ``get_settings()``. Typed to ``RedisSettings``
            rather than ``Settings`` for the same reason ``live_node_builder``
            takes ``IBKRSettings``: the narrowest parameter that does the job.

    Returns:
        A ``CacheConfig`` whose ``database`` points at Redis and whose three
        namespace-deciding fields are pinned explicitly. Building it performs
        no I/O and does not validate that Redis is reachable — call
        ``check_redis_reachable`` for that, before constructing a node.
    """
    return CacheConfig(
        database=DatabaseConfig(
            type="redis",
            host=settings.redis_host,
            port=settings.redis_port,
            # The provisioned redis:7-alpine takes no credentials and no TLS.
            # Named explicitly so that adding either later is a visible edit
            # here rather than an invisible inherited default.
            username=None,
            password=None,
            ssl=False,
        ),
        # See the module docstring, point 2. These three are the whole of
        # "a restarted process rejoins its own namespace" — do not remove them
        # on the grounds that they match the current defaults. That is the point.
        use_trader_prefix=True,
        use_instance_id=False,
        flush_on_start=False,
        # Not a namespace field, but the same class of silent break: msgpack and
        # json are not interchangeable for state already written to Redis.
        encoding="msgpack",
    )


def check_redis_reachable(
    host: str | None,
    port: int | None,
    *,
    timeout: float = DEFAULT_REACHABILITY_TIMEOUT_SECONDS,
) -> None:
    """Fail fast if the engine cache's Redis cannot be reached (AC #6).

    Must be called **before** any ``TradingNode`` is constructed with a
    Redis-backed cache — see the module docstring, point 1. This is the same
    shape as the bound Story 1.7 had to put around the CLI's connect, and as
    the Epic 1 retrospective's Action Item #10 asks for around ``node.build()``.

    Uses ``socket`` from the standard library, not a Redis client: AR3 forbids
    new dependencies and none is installed. A TCP connection is opened, an
    inline ``PING`` is sent, and the reply is read. **Only a confirmed ``+PONG``
    is accepted.** Anything else refuses: nothing listening, a listener that
    accepts and then stays silent, or a listener that answers something else.

    That last part is a reversal, and the reason is measured rather than
    argued. This originally accepted a non-``+PONG`` reply with a warning, on
    the grounds that something was listening and that refusing over an unusual
    proxy answer would be worse than the failure being prevented. It is not:
    pointed at a listener that accepts and never speaks, the preflight passed
    and ``CacheDatabaseAdapter.__init__`` was still blocked, silently, when it
    was killed at 40 seconds — the exact hang AC #6 exists to bound. An
    HTTP-speaking listener behaved the same way. A completed TCP handshake
    proves nothing about whether the Rust client can proceed.

    The one thing given up: a password-protected Redis answers ``-NOAUTH`` and
    *would* have failed fast inside the adapter with ``NOAUTH: Authentication
    required``. It is now refused here instead, with a less specific message.
    That is the accepted cost of refusing every endpoint that hangs.

    Takes host and port rather than a settings object so that callers can pass
    the values the node will *actually* connect to — ``cache.database.host`` and
    ``cache.database.port`` — rather than a settings object that might have been
    built separately from the ``CacheConfig``. A preflight that checks a
    different Redis than the node then uses is worse than none at all.

    Args:
        host: Redis host. ``None`` means Nautilus's own "typical default",
            which for its Redis backing is loopback.
        port: Redis port. ``None`` means Redis's default, 6379.
        timeout: Seconds to allow for the connect, the send, and the reply —
            **each**, not the sum. It does not bound the whole call: CPython's
            ``socket.create_connection`` resolves the name outside the timeout
            and then applies it per resolved address, so a multi-address host
            whose SYNs are dropped costs (addresses x ``timeout``), and a wedged
            resolver blocks in ``getaddrinfo`` for the OS resolver's own budget
            first. Every path still terminates, which is what the hang this
            function prevents actually requires; do not size a caller's own
            deadline off this argument alone.

    Raises:
        RedisUnreachableError: Nothing is listening, the host does not resolve
            or cannot be encoded, the connection could not be completed, or the
            endpoint did not answer ``+PONG``.
        ValueError: ``timeout`` cannot bound a wait, or ``host`` is blank.
    """
    bounded = _validate_timeout(timeout)
    # `DatabaseConfig.host`/`port` are Optional and documented as "if None then
    # should use the typical default", so the fallback mirrors what the Rust
    # client would do rather than refusing a config Nautilus would accept.
    resolved_host = (host or "127.0.0.1").strip()
    resolved_port = 6379 if port is None else port
    if not resolved_host:
        raise ValueError("host is blank; pass a hostname or IP address, or None for loopback.")
    host = resolved_host
    port = resolved_port

    try:
        with socket.create_connection((host, port), timeout=bounded) as connection:
            connection.settimeout(bounded)
            try:
                # Inline command form: Redis accepts a bare `PING\r\n` on a
                # fresh connection and answers `+PONG\r\n`.
                connection.sendall(b"PING\r\n")
                reply = connection.recv(64)
            except OSError as exc:
                # `socket.timeout` is an `OSError`, so this is the branch a
                # wedged endpoint arrives on. It must raise: see the docstring.
                raise _unreachable(
                    host, port, f"it accepted the connection but never answered PING ({exc})"
                ) from exc
    # `socket.gaierror` is itself an `OSError` and needs no separate entry.
    # `UnicodeError` is *not* — `create_connection` runs the host through the
    # `idna` codec, which raises it for a DNS label over 63 characters or
    # non-encodable text, and `redis_host` is a free-form env string.
    except (OSError, UnicodeError) as exc:
        raise _unreachable(host, port, str(exc)) from exc

    if not reply.startswith(b"+PONG"):
        answered = reply[:32].decode("utf-8", errors="replace") if reply else "nothing"
        raise _unreachable(
            host,
            port,
            f"it answered {answered!r} rather than '+PONG', so it is not a usable Redis",
        )

    logger.debug("redis.reachable", host=host, port=port)
