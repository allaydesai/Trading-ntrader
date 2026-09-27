"""The session-view reader against a real Redis (Story 4.6, AC #1/#4, D-A/D-I).

Integration tier under ``--forked``: a real ``Cache`` + ``ExecutionEngine``
writes a scratch session's namespace through a real ``CacheDatabaseAdapter``
exactly as a session's node would, and ``read_session_view`` — with its real,
default adapter — reads it back. Two things only a real Redis can prove:

1. **The round trip.** Real fills, persisted and replayed through the real
   msgpack serializer, give the session's true net per instrument — including a
   NETTING close-then-reopen and a flip, which reuse one position id and one
   fill list.
2. **Load-only (D-I, FR35).** A ``MONITOR`` capture over the read sees no
   write verb against the namespace, and the namespace is byte-identical before
   and after. A second writer into a running session's namespace would be an
   auto-resolution; this is the proof there is none.

Every trader id derives from a fresh ``uuid4()`` (``test_live_cache_namespace
.py``'s isolation discipline), cleanup deletes only this test's own keys, and
``flush()`` is **never** called: it is ``FLUSHDB`` on the whole database.
"""

import json
import socket
import threading
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import msgspec
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.engine import ExecEngineConfig, ExecutionEngine
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.orders import MarketOrder
from nautilus_trader.serialization.serializer import MsgSpecSerializer
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from src.config import RedisSettings
from src.core.live_cache import RedisUnreachableError, build_cache_config, check_redis_reachable
from src.core.live_session_view import (
    ACCOUNT_SUMMARY_KEY_PREFIX,
    SessionViewFailure,
    SessionViewUnavailableError,
    read_session_view,
)
from src.core.live_trader_id import derive_trader_id
from src.models.broker_state import CashBalance
from src.models.reconciliation import ViewPosition

pytestmark = pytest.mark.integration

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
ACCOUNT = "DU4076626"
#: The shape the IB exec client stamps on every fill and position: the reader
#: keeps only positions whose `account_id.get_id()` is the configured account.
IB_ACCOUNT = AccountId(f"INTERACTIVE_BROKERS-{ACCOUNT}")

#: Redis verbs that only read. Anything else from the reader is a write.
READ_VERBS = {
    "SCAN",
    "KEYS",
    "GET",
    "MGET",
    "LRANGE",
    "LLEN",
    "HGET",
    "HGETALL",
    "SMEMBERS",
    "ZRANGE",
    "TYPE",
    "EXISTS",
}
#: Connection housekeeping the Rust client issues; none touches data.
HOUSEKEEPING_VERBS = {"CLIENT", "INFO", "PING", "HELLO", "SELECT"}
#: Verbs that destroy data wholesale; seen from *any* client in the window, the
#: test fails, since nothing else should be running them against a dev Redis.
FLUSH_VERBS = {"FLUSHALL", "FLUSHDB"}


def _redis_settings() -> RedisSettings:
    return RedisSettings(_env_file=None)


@pytest.fixture(scope="module", autouse=True)
def _require_redis():
    """Skip cleanly when Redis is not running — CI has no Redis service."""
    settings = _redis_settings()
    try:
        check_redis_reachable(settings.redis_host, settings.redis_port, timeout=2.0)
    except RedisUnreachableError as exc:
        pytest.skip(f"Redis is not reachable, skipping session-view tests: {exc}")


class _Resp:
    """A minimal stdlib RESP client — test code only; the project has no Redis client."""

    def __init__(self, settings: RedisSettings) -> None:
        self.sock = socket.create_connection((settings.redis_host, settings.redis_port), 5)
        self.stream = self.sock.makefile("rb")

    def send(self, *args: str | bytes) -> None:
        parts = [f"*{len(args)}\r\n".encode()]
        for arg in args:
            raw = arg if isinstance(arg, bytes) else arg.encode()
            parts.append(f"${len(raw)}\r\n".encode() + raw + b"\r\n")
        self.sock.sendall(b"".join(parts))

    def cmd(self, *args: str | bytes) -> Any:
        self.send(*args)
        return self.read()

    def read(self) -> Any:
        line = self.stream.readline()
        kind, rest = line[:1], line[1:-2]
        if kind == b"+":
            return rest.decode()
        if kind == b"-":
            raise RuntimeError(rest.decode())
        if kind == b":":
            return int(rest)
        if kind == b"$":
            size = int(rest)
            return None if size < 0 else self.stream.read(size + 2)[:-2]
        if kind == b"*":
            size = int(rest)
            return None if size < 0 else [self.read() for _ in range(size)]
        raise RuntimeError(f"unexpected RESP line {line!r}")

    def close(self) -> None:
        self.sock.close()


def _snapshot(client: _Resp, prefix: str) -> dict:
    snapshot = {}
    for raw in sorted(client.cmd("KEYS", f"{prefix}*")):
        key = raw.decode()
        kind = client.cmd("TYPE", key)
        if kind == "string":
            snapshot[key] = (kind, client.cmd("GET", key))
        elif kind == "list":
            snapshot[key] = (kind, tuple(client.cmd("LRANGE", key, "0", "-1")))
        elif kind == "set":
            snapshot[key] = (kind, tuple(sorted(client.cmd("SMEMBERS", key))))
        elif kind == "hash":
            snapshot[key] = (kind, tuple(client.cmd("HGETALL", key)))
        elif kind == "zset":
            snapshot[key] = (kind, tuple(client.cmd("ZRANGE", key, "0", "-1", "WITHSCORES")))
        elif kind == "none":
            # Deleted between KEYS and TYPE: the namespace is still changing,
            # which the settle loop and the before/after comparison both see.
            snapshot[key] = (kind, None)
        else:
            raise AssertionError(f"snapshot cannot compare a {kind!r} key: {key}")
    return snapshot


def _monitor_lines(raw: bytes) -> list[tuple[str, str, str]]:
    """``(client_addr, VERB, whole line)`` for each ``MONITOR`` line.

    A line reads ``+<ts> [<db> <addr>] "VERB" "arg" …``.
    """
    parsed = []
    for line in raw.decode(errors="replace").split("\r\n"):
        if "[" not in line or line.count('"') < 2:
            continue
        addr = line.split("[", 1)[1].split("]", 1)[0].split(" ")[-1]
        parsed.append((addr, line.split('"')[1].upper(), line))
    return parsed


class _Session:
    """A scratch session's namespace, written the way a session's node writes it."""

    def __init__(self) -> None:
        self.session_id: UUID = uuid4()
        self.trader_id = derive_trader_id(self.session_id)
        self.prefix = f"trader-{self.trader_id}:"
        self.adapter = CacheDatabaseAdapter(
            trader_id=TraderId(self.trader_id),
            instance_id=UUID4(),
            serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True),
            config=build_cache_config(_redis_settings()),
        )
        clock = TestClock()
        self.cache = Cache(database=self.adapter)
        self.cache.add_instrument(AAPL)
        self.cache.add_instrument(NVDA)
        self.cache.add_account(TestExecStubs.margin_account(IB_ACCOUNT))
        self.engine = ExecutionEngine(
            msgbus=MessageBus(trader_id=TraderId(self.trader_id), clock=clock),
            cache=self.cache,
            clock=clock,
            config=ExecEngineConfig(),
        )
        self.engine.start()  # no client registered: `_determine_oms_type` is NETTING
        self._count = 0

    def fill(self, instrument, side: OrderSide, qty: int, strategy: str = "S-001") -> None:
        self._count += 1
        order = MarketOrder(
            trader_id=TraderId(self.trader_id),
            strategy_id=StrategyId(strategy),
            instrument_id=instrument.id,
            client_order_id=ClientOrderId(f"O-{self._count}"),
            order_side=side,
            quantity=Quantity.from_int(qty),
            init_id=UUID4(),
            ts_init=0,
        )
        self.cache.add_order(order, None)
        self.engine.process(TestEventStubs.order_submitted(order, account_id=IB_ACCOUNT))
        self.engine.process(
            TestEventStubs.order_accepted(
                order, account_id=IB_ACCOUNT, venue_order_id=VenueOrderId(f"V-{self._count}")
            )
        )
        self.engine.process(
            TestEventStubs.order_filled(
                order,
                instrument=instrument,
                account_id=IB_ACCOUNT,
                trade_id=TradeId(f"T-{self._count}"),
                last_px=Price.from_str("100.00"),
            )
        )

    def record_cash(self, account: str, usd: float) -> None:
        body = {"USD": {"TotalCashValue": usd, "NetLiquidation": 400000.0}, "": {"X": "Y"}}
        self.cache.add(f"{ACCOUNT_SUMMARY_KEY_PREFIX}{account}", json.dumps(body).encode())

    def flush_writes(self, *, timeout: float = 5.0) -> None:
        """Wait, bounded, until the namespace stops changing, then close the writer.

        The adapter pipelines its writes, so a fixed sleep flakes on a loaded
        machine: poll until two snapshots 100 ms apart agree.
        """
        client = _Resp(_redis_settings())
        try:
            deadline = time.monotonic() + timeout
            previous = None
            while True:
                current = _snapshot(client, self.prefix)
                if current and current == previous:
                    break
                if time.monotonic() > deadline:
                    raise AssertionError("the scratch namespace never settled")
                previous = current
                time.sleep(0.1)
        finally:
            client.close()
        self.adapter.close()

    def net(self, instrument) -> Decimal:
        return sum(
            (
                p.signed_decimal_qty()
                for p in self.cache.positions_open(instrument_id=instrument.id)
            ),
            Decimal(0),
        )

    def cleanup(self) -> None:
        client = _Resp(_redis_settings())
        try:
            for key in client.cmd("KEYS", f"{self.prefix}*"):
                client.cmd("DEL", key)
        finally:
            client.close()


@pytest.fixture
def session():
    scratch = _Session()
    yield scratch
    scratch.cleanup()


class TestTheRealRoundTrip:
    def test_open_closed_reopened_and_flipped_positions_read_as_their_true_net(self, session):
        session.fill(NVDA, OrderSide.BUY, 10)
        session.fill(AAPL, OrderSide.BUY, 7)  # open
        session.fill(AAPL, OrderSide.SELL, 7)  # close
        session.fill(AAPL, OrderSide.BUY, 3)  # reopen, same position id
        session.fill(AAPL, OrderSide.SELL, 5)  # flip to short 2
        session.fill(AAPL, OrderSide.BUY, 4, strategy="EXTERNAL")  # a second position
        expected = {"AAPL.NASDAQ": session.net(AAPL), "NVDA.NASDAQ": session.net(NVDA)}
        session.record_cash(ACCOUNT, 100000.52)
        session.record_cash("DU9999999", 5.0)
        session.flush_writes()

        view = read_session_view(session.session_id, ACCOUNT, _redis_settings())

        assert expected == {"AAPL.NASDAQ": Decimal("2"), "NVDA.NASDAQ": Decimal("10")}
        assert view.positions == (
            ViewPosition("AAPL.NASDAQ", Decimal("2")),
            ViewPosition("NVDA.NASDAQ", Decimal("10")),
        )
        assert view.cash == (CashBalance("USD", Decimal("100000.52")),)
        assert view.trader_id == session.trader_id

    def test_a_fully_closed_session_reads_flat_with_its_cash(self, session):
        session.fill(AAPL, OrderSide.BUY, 7)
        session.fill(AAPL, OrderSide.SELL, 7)
        session.record_cash(ACCOUNT, 10.0)
        session.flush_writes()

        view = read_session_view(session.session_id, ACCOUNT, _redis_settings())

        assert view.positions == ()
        assert view.cash == (CashBalance("USD", Decimal("10.0")),)

    def test_a_session_that_never_ran_has_no_engine_state(self):
        with pytest.raises(SessionViewUnavailableError) as raised:
            read_session_view(uuid4(), ACCOUNT, _redis_settings())

        assert raised.value.reason is SessionViewFailure.NO_ENGINE_STATE

    def test_a_position_whose_instrument_is_gone_fails_loudly(self, session):
        """F4 on the real adapter: ``load_positions()`` would return ``{}`` here."""
        session.fill(AAPL, OrderSide.BUY, 7)
        session.flush_writes()
        client = _Resp(_redis_settings())
        try:
            client.cmd("DEL", f"{session.prefix}instruments:AAPL.NASDAQ")
        finally:
            client.close()

        with pytest.raises(SessionViewUnavailableError) as raised:
            read_session_view(session.session_id, ACCOUNT, _redis_settings())

        assert raised.value.reason is SessionViewFailure.UNREADABLE


class TestTheReadIsLoadOnly:
    """D-I / FR35: reading a session's namespace writes nothing to it."""

    def test_monitor_sees_no_write_and_the_namespace_is_byte_identical(self, session):
        session.fill(AAPL, OrderSide.BUY, 7)
        session.record_cash(ACCOUNT, 10.0)
        session.flush_writes()
        observer = _Resp(_redis_settings())
        before = _snapshot(observer, session.prefix)

        monitor = _Resp(_redis_settings())
        assert monitor.cmd("MONITOR") == "OK"
        captured = bytearray()
        stop = threading.Event()

        def pump() -> None:
            monitor.sock.settimeout(0.2)
            while not stop.is_set():
                try:
                    chunk = monitor.sock.recv(65536)
                except OSError:
                    continue
                if not chunk:
                    return
                captured.extend(chunk)

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        try:
            read_session_view(session.session_id, ACCOUNT, _redis_settings())
            time.sleep(0.3)
        finally:
            stop.set()
            thread.join()
            monitor.close()

        after = _snapshot(observer, session.prefix)
        observer.close()
        lines = _monitor_lines(bytes(captured))
        # The reader's connections: every client that touched the namespace, or
        # that opened (the Rust client's `CLIENT SETINFO` handshake) in the window.
        readers = {
            addr
            for addr, verb, line in lines
            if session.trader_id in line or (verb == "CLIENT" and "SETINFO" in line)
        }
        reader_verbs = {verb for addr, verb, _ in lines if addr in readers}
        namespaced_verbs = {verb for _, verb, line in lines if session.trader_id in line}

        assert {"SCAN", "LRANGE", "MGET"} <= namespaced_verbs, f"MONITOR missed: {lines}"
        assert reader_verbs <= READ_VERBS | HOUSEKEEPING_VERBS, (
            f"the reader issued non-read verbs: {reader_verbs - READ_VERBS - HOUSEKEEPING_VERBS}"
        )
        assert not {verb for _, verb, _ in lines} & FLUSH_VERBS
        assert after == before
