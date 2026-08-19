"""Unit tests for RedisSettings configuration (Story 2.4, AC #1).

``RedisSettings`` points the Nautilus engine cache at the provisioned
``redis:7-alpine`` service (AR11). It carries no credentials, so there is no
``repr``-hardening suite here of the kind ``test_fmp_settings.py`` needs.

The ``redis_db`` refusal tested below is the unusual one and is deliberate: see
``TestRedisSettingsDatabaseIndexRefusal``.
"""

import pytest
from pydantic import ValidationError

from src.config import RedisSettings, Settings, get_settings

#: Every test clears these so a developer's own ``.env`` can never decide an
#: assertion. ``_env_file=None`` stops the file being read; this stops the
#: process environment being read.
REDIS_ENV_VARS = ("REDIS_HOST", "REDIS_PORT", "REDIS_DB")


@pytest.fixture(autouse=True)
def _clear_redis_env(monkeypatch):
    """Isolate every test from the ambient environment and from ``.env``."""
    for name in REDIS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.unit
class TestRedisSettingsDefaults:
    """Defaults must work for a local run with no configuration at all."""

    def test_default_host_is_loopback(self):
        """Host defaults to loopback — the compose service publishes 6379."""
        assert RedisSettings(_env_file=None).redis_host == "127.0.0.1"

    def test_default_port_is_6379(self):
        """Port defaults to Redis's own default, which compose publishes."""
        assert RedisSettings(_env_file=None).redis_port == 6379

    def test_default_database_index_is_zero(self):
        """Database index defaults to 0 — the only value 1.220.0 can honour."""
        assert RedisSettings(_env_file=None).redis_db == 0


@pytest.mark.unit
class TestRedisSettingsEnvOverrides:
    """Every field is reachable from the environment (AR11's "from env")."""

    def test_host_from_env(self, monkeypatch):
        """Host loads from REDIS_HOST — the value compose must set to 'redis'."""
        monkeypatch.setenv("REDIS_HOST", "redis")

        assert RedisSettings(_env_file=None).redis_host == "redis"

    def test_port_from_env(self, monkeypatch):
        """Port loads from REDIS_PORT and coerces to int."""
        monkeypatch.setenv("REDIS_PORT", "6380")

        assert RedisSettings(_env_file=None).redis_port == 6380

    def test_database_index_from_env_accepts_the_honourable_value(self, monkeypatch):
        """REDIS_DB=0 is accepted from env; every other value is refused below."""
        monkeypatch.setenv("REDIS_DB", "0")

        assert RedisSettings(_env_file=None).redis_db == 0


@pytest.mark.unit
class TestRedisSettingsRangeValidation:
    """A port that cannot be connected to must fail at construction, not at connect."""

    def test_port_zero_is_refused(self):
        """Port 0 means "any free port" to bind() and is meaningless to connect()."""
        with pytest.raises(ValidationError, match="redis_port"):
            RedisSettings(_env_file=None, redis_port=0)

    def test_port_above_the_tcp_range_is_refused(self):
        """65536 is not a TCP port."""
        with pytest.raises(ValidationError, match="redis_port"):
            RedisSettings(_env_file=None, redis_port=65536)

    def test_blank_host_is_refused(self):
        """An empty host would silently become "connect to nothing"."""
        with pytest.raises(ValidationError, match="redis_host"):
            RedisSettings(_env_file=None, redis_host="   ")


@pytest.mark.unit
class TestRedisSettingsDatabaseIndexRefusal:
    """A non-zero ``redis_db`` is refused rather than silently ignored.

    ``nautilus_trader.config.DatabaseConfig`` at 1.220.0 has no database-index
    field at all — its full field list is ``type, host, port, username,
    password, ssl, timeout`` — and the config is msgpack-encoded straight into
    the Rust ``RedisCacheDatabase``, so there is no side channel either. An
    operator who sets ``REDIS_DB=1`` to isolate something would therefore get
    exactly zero isolation from it and no indication of that.

    Sessions *are* already isolated, by the ``trader_id`` key namespace — so
    this refusal protects an expectation rather than a live guarantee. It is
    still the right failure mode for a system whose stated purpose is that two
    sessions can never contaminate each other's orders and positions.
    """

    def test_non_zero_database_index_is_refused(self):
        """REDIS_DB=1 raises rather than being dropped on the floor."""
        with pytest.raises(ValidationError, match="redis_db"):
            RedisSettings(_env_file=None, redis_db=1)

    def test_the_refusal_explains_why_and_names_the_version(self):
        """The message must send the reader to the cause, not just to the rule."""
        with pytest.raises(ValidationError) as exc_info:
            RedisSettings(_env_file=None, redis_db=3)

        message = str(exc_info.value)
        assert "1.220.0" in message
        assert "DatabaseConfig" in message
        assert "trader_id" in message

    def test_negative_database_index_is_refused(self):
        """A negative index is not a Redis database under any configuration."""
        with pytest.raises(ValidationError, match="redis_db"):
            RedisSettings(_env_file=None, redis_db=-1)


@pytest.mark.unit
class TestRedisSettingsNesting:
    """``Settings.redis`` mirrors ``ibkr`` / ``kraken`` / ``fmp`` / ``catalog``."""

    def test_settings_redis_is_redis_settings_instance(self):
        """Settings.redis is a RedisSettings."""
        assert isinstance(Settings().redis, RedisSettings)

    def test_settings_redis_reads_host_from_env(self, monkeypatch):
        """The nested default_factory reads the environment, not just defaults."""
        monkeypatch.setenv("REDIS_HOST", "nested-redis-host")

        assert Settings().redis.redis_host == "nested-redis-host"

    def test_get_settings_redis_is_redis_settings_instance(self):
        """get_settings().redis is a RedisSettings."""
        assert isinstance(get_settings().redis, RedisSettings)
