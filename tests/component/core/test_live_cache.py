"""Component tests for the Redis engine-cache config (Story 2.4, AC #3/#6/#7).

Component tier, not unit: this module imports ``nautilus_trader.config``, which
``pytest.ini:25`` defines the unit tier to exclude. Building a ``CacheConfig``
does not touch the Nautilus C logging subsystem and does not connect to
anything — the autouse fixture below machine-enforces the first half of that.

Anything that constructs a real ``CacheDatabaseAdapter`` (and therefore needs a
live Redis) belongs in ``tests/integration/core/test_live_cache_namespace.py``
instead.
"""

import socket
import time

import pytest
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import CacheConfig, DatabaseConfig
from nautilus_trader.model.identifiers import TraderId

from src.config import RedisSettings
from src.core.live_cache import (
    RedisUnreachableError,
    build_cache_config,
    check_redis_reachable,
)
from src.core.live_trader_id import derive_trader_id


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Copied from ``test_live_node_builder.py``, including its reasoning: the
    assertion is on the *delta*, not the absolute state, because under
    ``-n auto`` this file shares a worker with the rest of the component tier
    and something else in that tier does initialise C logging.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — cache config assembly must not touch "
        "the C logging subsystem, and the non-forked, parallel component tier cannot host "
        "anything that does. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_redis_env(monkeypatch):
    """Keep a developer's own REDIS_* exports out of every assertion."""
    for name in ("REDIS_HOST", "REDIS_PORT", "REDIS_DB"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(host: str = "127.0.0.1", port: int = 6379) -> RedisSettings:
    """RedisSettings built without reading .env or the environment."""
    return RedisSettings(_env_file=None, redis_host=host, redis_port=port)


def _closed_port() -> int:
    """A port nothing is listening on.

    Bound and released, so the number is real and almost certainly still free —
    rather than a hardcoded guess that fails on whichever machine happens to run
    something there.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.component
class TestCacheConfigPointsAtRedis:
    """AC #3: ``CacheConfig(database=DatabaseConfig(...))`` pointed at Redis."""

    def test_the_database_is_a_redis_database_config(self):
        """The type Nautilus branches on in ``kernel.py:302`` must be 'redis'."""
        config = build_cache_config(_settings())

        assert isinstance(config, CacheConfig)
        assert isinstance(config.database, DatabaseConfig)
        assert config.database.type == "redis"

    def test_host_and_port_come_from_the_settings(self):
        """Injected, never fetched — the module never calls get_settings()."""
        config = build_cache_config(_settings(host="redis.example.test", port=6380))

        assert config.database.host == "redis.example.test"
        assert config.database.port == 6380

    def test_no_credentials_are_invented(self):
        """The provisioned redis:7-alpine takes none; sending some would fail."""
        database = build_cache_config(_settings()).database

        assert database.username is None
        assert database.password is None
        assert database.ssl is False


@pytest.mark.component
class TestNamespaceDecidingFieldsArePinned:
    """AC #7: the three fields that decide the key namespace, asserted by name.

    All three are Nautilus defaults today. AC #4 — "a restarted process rejoins
    its own Redis namespace rather than starting empty" — is true *only* because
    of them, and a default that carries a data-integrity property is one upgrade
    away from changing silently. Same treatment ``live_node_builder.py:235-241``
    already gives ``market_data_type`` and ``use_regular_trading_hours``.
    """

    def test_instance_id_is_not_part_of_the_key(self):
        """The single most load-bearing value in this story.

        ``instance_id`` is a fresh UUID4 per process. With this ``True``, every
        restart writes to a brand-new namespace and AC #4 becomes false with
        nothing raising anywhere.
        """
        assert build_cache_config(_settings()).use_instance_id is False

    def test_the_namespace_is_not_flushed_on_start(self):
        """``True`` wipes the namespace on every start (``kernel.py:1245``)."""
        assert build_cache_config(_settings()).flush_on_start is False

    def test_keys_carry_the_trader_prefix(self):
        """Keeps the namespace readable and greppable in redis-cli."""
        assert build_cache_config(_settings()).use_trader_prefix is True

    def test_the_encoding_is_pinned_too(self):
        """Changing this later makes existing Redis state unreadable.

        Not a namespace field, but the same class of silent break: msgpack and
        json are not interchangeable for state already written.
        """
        assert build_cache_config(_settings()).encoding == "msgpack"


@pytest.mark.component
class TestRedisReachabilityPreflight:
    """AC #6: an unreachable Redis fails fast and loudly, never by hanging.

    ``CacheDatabaseAdapter.__init__`` blocks *forever* against an unreachable
    Redis — verified against 1.220.0, and ``DatabaseConfig(timeout=...)`` does
    not bound it. ``NautilusKernel`` constructs that adapter eagerly, so without
    this preflight ``build_trading_node`` would hang with no output at all.
    """

    def test_an_unreachable_redis_raises_rather_than_hanging(self):
        """The exception type, which is what callers branch on."""
        with pytest.raises(RedisUnreachableError):
            check_redis_reachable("127.0.0.1", _closed_port(), timeout=1.0)

    def test_the_failure_returns_within_its_timeout(self):
        """The defect being guarded is an unbounded hang, not a wrong type.

        A test asserting only ``pytest.raises`` passes just as happily against
        an implementation that takes four minutes to get there, which would
        leave the operator-facing symptom exactly as it was.
        """
        port = _closed_port()

        started = time.monotonic()
        with pytest.raises(RedisUnreachableError):
            check_redis_reachable("127.0.0.1", port, timeout=1.0)
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"preflight took {elapsed:.1f}s against a closed port"

    def test_the_message_names_the_host_and_port(self):
        """An operator must be able to act on this without reading source."""
        port = _closed_port()

        with pytest.raises(RedisUnreachableError) as exc_info:
            check_redis_reachable("127.0.0.1", port, timeout=1.0)

        message = str(exc_info.value)
        assert "127.0.0.1" in message
        assert str(port) in message

    def test_an_unresolvable_host_raises_the_same_named_error(self):
        """DNS failure is a different exception underneath; callers see one type."""
        with pytest.raises(RedisUnreachableError):
            check_redis_reachable("redis.invalid.nonexistent.test", 6379, timeout=1.0)

    @pytest.mark.parametrize("bad_timeout", [0, -1.0, float("nan"), float("inf")])
    def test_a_timeout_that_cannot_bound_a_wait_is_refused(self, bad_timeout):
        """A NaN or infinite timeout would reinstate the unbounded hang.

        Mirrors ``live_check_driver._validate_window`` — a NaN comparison is
        always False and an infinite wait never ends, so neither can bound the
        thing this function exists to bound.
        """
        with pytest.raises(ValueError, match="timeout"):
            check_redis_reachable("127.0.0.1", 6379, timeout=bad_timeout)


@pytest.mark.component
class TestDerivedTraderIdSurvivesNautilus:
    """AR23's real dependency: the derived tag must survive ``TraderId``.

    Lives here rather than in ``tests/unit/core/test_live_trader_id.py`` so that
    module can stay importable without Nautilus (AR38).
    """

    def test_nautilus_accepts_the_derived_trader_id(self):
        """A ``trader_id`` with no '-' panics in Rust and aborts the process."""
        from uuid import uuid4

        for _ in range(50):
            derived = derive_trader_id(uuid4())
            assert TraderId(derived).value == derived

    def test_get_tag_returns_the_whole_short_session_id(self):
        """``get_tag()`` splits on the LAST hyphen; client order IDs embed it.

        This is the assertion that catches a ``str(uuid)``-instead-of-``.hex``
        derivation, which produces a *valid* ``TraderId`` with a wrong tag and
        raises nothing (``common/generators.pyx:40, 145-151``).
        """
        from uuid import uuid4

        for _ in range(200):
            session_id = uuid4()
            tag = TraderId(derive_trader_id(session_id)).get_tag()
            assert tag == session_id.hex[:8]
