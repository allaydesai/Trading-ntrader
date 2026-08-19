"""The Redis namespace actually persists and actually isolates (Story 2.4, AC #4).

Integration tier under ``--forked``: this module constructs a real
``CacheDatabaseAdapter``, which is Rust-backed, opens a real socket to Redis and
initialises Nautilus's C logging. None of that is safe in the parallel,
non-forked component tier.

No ``TradingNode`` is constructed here — that would need a live IB gateway. The
cache adapter is the component AC #4 is actually about, and it stands alone.

⚠️ **``CacheDatabaseAdapter.flush()`` is ``FLUSHDB``** — it clears the *entire*
Redis database, not the trader's namespace (``cache/database.pyx:179-186``).
Calling it here would wipe whatever else a developer keeps in db 0, so this
module never does. Instead every test derives its trader IDs from a fresh
``uuid4()``, so no key written here can ever be read by a later run and no
leftover can make an assertion pass falsely. The handful of bytes left behind
are namespaced under a trader ID that will never recur.
"""

import msgspec
import pytest
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.config import CacheConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.serialization.serializer import MsgSpecSerializer

from src.config import RedisSettings
from src.core.live_cache import (
    RedisUnreachableError,
    build_cache_config,
    check_redis_reachable,
)
from src.core.live_trader_id import derive_trader_id

pytestmark = pytest.mark.integration


def _redis_settings() -> RedisSettings:
    """Settings for the local Redis, ignoring .env so the target is explicit."""
    return RedisSettings(_env_file=None)


@pytest.fixture(scope="module", autouse=True)
def _require_redis():
    """Skip the module cleanly when Redis is not running.

    Load-bearing rather than a courtesy: CI runs ``tests/integration/core``
    (only ``tests/integration/db`` is ``--ignore``d, at ci.yml:168 and :238) and
    CI has no Redis service. Without this skip these tests would fail every
    build.
    """
    settings = _redis_settings()
    try:
        check_redis_reachable(settings.redis_host, settings.redis_port, timeout=2.0)
    except RedisUnreachableError as exc:
        pytest.skip(f"Redis is not reachable, skipping engine-cache namespace tests: {exc}")


def _adapter(trader_id: str, config: CacheConfig) -> CacheDatabaseAdapter:
    """Build a cache adapter the way ``NautilusKernel`` does (kernel.py:303-311).

    ``instance_id`` is deliberately a *fresh* UUID4 on every call — that is what
    a new process run does, and it is precisely why ``use_instance_id`` must
    stay ``False`` for AC #4 to hold.
    """
    return CacheDatabaseAdapter(
        trader_id=TraderId(trader_id),
        instance_id=UUID4(),
        serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True),
        config=config,
    )


class TestARestartRejoinsItsOwnNamespace:
    """AC #4 / FR19 — "rejoins its own Redis namespace rather than starting empty"."""

    def test_a_second_process_run_still_sees_what_the_first_one_wrote(self):
        """The whole story, in one assertion.

        The second adapter stands in for the restarted process: same session,
        same derived ``trader_id``, brand-new ``instance_id``. If it came up
        empty, every restart would lose its orders, positions and — the one
        that actually costs money — its client-order-ID counter
        (``trading/strategy.pyx:353-368``, NFR6/AR23).
        """
        # Arrange — a session identity that cannot collide with any other run
        from uuid import uuid4

        trader_id = derive_trader_id(uuid4())
        config = build_cache_config(_redis_settings())
        key = "story24:rejoin-probe"

        first_run = _adapter(trader_id, config)
        try:
            first_run.add(key, b"written-by-the-first-process-run")
        finally:
            first_run.close()

        # Act — a new process run of the same session
        second_run = _adapter(trader_id, config)
        try:
            rejoined = second_run.keys()
        finally:
            second_run.close()

        # Assert
        assert any(key in entry for entry in rejoined), (
            f"a restarted session came up empty: {rejoined!r}. AC #4 rests on "
            "use_instance_id=False and flush_on_start=False in build_cache_config()."
        )

    def test_the_key_is_namespaced_under_the_derived_trader_id(self):
        """AC #3's "namespaced by trader_id", read straight off the key.

        ``use_trader_prefix=True`` is what produces the ``trader-`` prefix; the
        trader ID that follows it is what makes the namespace per-session.

        Note the write-close-reopen shape, which every test in this module
        uses. ``add()`` does **not** land synchronously — the Rust backing
        pipelines writes, so ``keys()`` called on the same still-open adapter
        immediately after ``add()`` returns ``[]`` (observed, and the reason
        this test originally failed). ``close()`` is what makes the write
        readable. That is not a workaround: it is the actual lifecycle AC #4
        describes, where a process ends before another one starts.
        """
        from uuid import uuid4

        trader_id = derive_trader_id(uuid4())
        config = build_cache_config(_redis_settings())

        writer = _adapter(trader_id, config)
        try:
            writer.add("story24:namespace-probe", b"x")
        finally:
            writer.close()

        reader = _adapter(trader_id, config)
        try:
            keys = reader.keys()
        finally:
            reader.close()

        assert keys, "nothing was written"
        assert all(entry.startswith(f"trader-{trader_id}:") for entry in keys), keys


class TestTwoSessionsNeverShareState:
    """AC #4's second half — "two sessions can never contaminate each other"."""

    def test_a_different_session_sees_none_of_another_sessions_keys(self):
        """The isolation the whole story exists to deliver.

        Two sessions, one Redis, one IBKR paper account. If these namespaces
        overlapped, one session's engine would load the other's orders and
        positions on start.
        """
        # Arrange
        from uuid import uuid4

        config = build_cache_config(_redis_settings())
        session_a = derive_trader_id(uuid4())
        session_b = derive_trader_id(uuid4())
        assert session_a != session_b

        writer = _adapter(session_a, config)
        try:
            writer.add("story24:isolation-probe", b"belongs-to-session-a")
        finally:
            writer.close()

        # Act
        other_session = _adapter(session_b, config)
        try:
            visible = other_session.keys()
        finally:
            other_session.close()

        # Assert
        assert visible == [], (
            f"session {session_b} can see session {session_a}'s cache: {visible!r}"
        )


class TestTheNamespaceGuaranteeIsLoadBearing:
    """Mutation coverage for AC #7, as a test rather than a manual procedure.

    Epic 1 retro Action Item #3: for any newly-introduced load-bearing guard,
    deliberately break it and confirm the failure is observed. Doing it here
    means the proof re-runs on every build instead of living in a story's
    completion notes.
    """

    def test_turning_on_use_instance_id_breaks_the_rejoin(self):
        """Proves the rejoin test above measures what it claims to measure.

        With ``use_instance_id=True`` the key carries the per-process instance
        ID, so a restart writes to a brand-new namespace and comes up empty —
        with nothing raising anywhere. If this test ever fails, the rejoin
        assertion has stopped depending on the field it is supposed to pin.
        """
        # Arrange — the mutated config, built by hand rather than via
        # build_cache_config() so production code is never altered
        from uuid import uuid4

        pinned = build_cache_config(_redis_settings())
        mutated = CacheConfig(
            database=pinned.database,
            use_trader_prefix=pinned.use_trader_prefix,
            use_instance_id=True,
            flush_on_start=pinned.flush_on_start,
            encoding=pinned.encoding,
        )
        trader_id = derive_trader_id(uuid4())
        key = "story24:mutation-probe"

        first_run = _adapter(trader_id, mutated)
        try:
            first_run.add(key, b"written-under-a-per-process-namespace")
        finally:
            first_run.close()

        # Act — a "restart" under the mutated config
        second_run = _adapter(trader_id, mutated)
        try:
            rejoined = second_run.keys()
        finally:
            second_run.close()

        # Assert — the mutation must be observable
        assert not any(key in entry for entry in rejoined), (
            "use_instance_id=True still rejoined the previous run's namespace, so the "
            "rejoin test is not actually pinned to that field"
        )
