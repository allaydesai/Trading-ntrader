"""Component tests for reading the IBKR connection status (Story 1.6).

Component tier, not unit: this module imports the Interactive Brokers adapter.
Constructing an ``InteractiveBrokersClient`` does **not** initialise the
Nautilus C logging subsystem — verified empirically, and machine-enforced by
the autouse fixture below — so the parallel, non-forked component suite is safe
for it. Nothing here connects to a broker (NFR32).

The reader is the seam between the framework and the pure state machine in
``src/core/live_connection_monitor.py``: it turns whatever the adapter exposes
into one ``ConnectionStatus``, fail-closed, without ever mutating the client it
is measuring.
"""

import asyncio

import pytest
from nautilus_trader.adapters.interactive_brokers.client.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, is_logging_initialized
from nautilus_trader.model.identifiers import TraderId

from src.config import IBKRSettings
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionState, ConnectionStatus
from src.core.live_node_builder import read_ibkr_connection_status
from tests.component.doubles import (
    TestIBConnection,
    TestIBConnectionWithoutFlags,
    TestIBConnectionWithPlainFlags,
    TestIBConnectionWithRaisingFlags,
)

HOST = "127.0.0.1"
PORT = 4002
LIVE_CLIENT_ID = 10


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Mirrors ``tests/component/core/test_live_node_builder.py``: the assertion is
    on the *delta*, not the absolute state, because under ``-n auto`` this file
    shares a worker process with the rest of the component tier and something
    else in that tier does initialise C logging. What this file must never do is
    *change* the state.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — reading a connection status must "
        "not touch the C logging subsystem, and the non-forked, parallel component "
        "tier cannot host anything that does."
    )


def _settings(
    *,
    host: str = HOST,
    port: int = PORT,
    live_client_id: int = LIVE_CLIENT_ID,
    client_id: int = 1,
    mode: str = "paper",
    account: str = "DU4076626",
    real_money_account: str = "",
) -> IBKRSettings:
    """Build settings with every field the reader touches, passed explicitly.

    Init kwargs outrank the environment in pydantic-settings, so the developer's
    shell cannot become test input. ``_env_file=None`` disables the dotenv
    *file* only — ``os.environ`` stays an active source — so any field the
    reader starts consulting must be added to this signature.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_host=host,
        ibkr_port=port,
        ibkr_live_client_id=live_client_id,
        ibkr_client_id=client_id,
        ibkr_trading_mode=mode,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
    )


@pytest.fixture
def register_client(monkeypatch):
    """Insert a double into the adapter's process-global client cache.

    ``IB_CLIENTS`` (``adapters/interactive_brokers/factories.py:42``) is a
    module-level dict that would otherwise leak between tests in the shared,
    parallel component tier. ``monkeypatch.setitem`` restores it — including
    deleting a key that was absent before — on teardown, which is far harder to
    get wrong than a hand-rolled try/finally.
    """

    def _register(client, *, host: str = HOST, port: int = PORT, client_id: int = LIVE_CLIENT_ID):
        monkeypatch.setitem(IB_CLIENTS, (host, port, client_id), client)
        return client

    return _register


class TestReaderFailsClosed:
    """An unknown connection is a disconnected connection."""

    @pytest.mark.component
    def test_no_client_registered_reports_disconnected(self):
        """The unbuilt-node, wrong-port and moved-cache cases all look like this."""
        status = read_ibkr_connection_status(_settings())

        assert status.connected is False
        assert str(LIVE_CLIENT_ID) in status.detail
        assert "no ib client" in status.detail.lower()

    @pytest.mark.component
    def test_client_without_the_expected_flags_reports_disconnected(self, register_client):
        """A renamed private flag must withhold permission, not raise."""
        register_client(TestIBConnectionWithoutFlags())

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False
        assert status.detail

    @pytest.mark.component
    def test_reader_never_raises_on_a_hostile_cache_entry(self, register_client):
        """Whatever is in the cache, the reader returns a status."""
        register_client(object())

        status = read_ibkr_connection_status(_settings())

        assert isinstance(status, ConnectionStatus)
        assert status.connected is False

    @pytest.mark.component
    def test_flags_refactored_to_plain_booleans_report_disconnected(self, register_client):
        """``getattr`` succeeds, so the ``None`` guard does not fire.

        This is what a Nautilus refactor from ``asyncio.Event`` to a bare
        ``bool`` looks like: ``True.is_set`` raises ``AttributeError`` inside
        the guard. Without a double of this shape the fail-closed ``except`` arm
        had no coverage at all — the whole guard could be deleted and the suite
        would stay green.
        """
        register_client(TestIBConnectionWithPlainFlags())

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False

    @pytest.mark.component
    def test_a_flag_whose_read_raises_reports_disconnected(self, register_client):
        """``getattr``'s default swallows only ``AttributeError``.

        A cache entry exposing ``_is_ib_connected`` as a property that raises
        anything else used to propagate straight out of the reader and into the
        caller's poll loop, contradicting both "never raises" and "fail-closed
        on every uncertainty".
        """
        register_client(TestIBConnectionWithRaisingFlags())

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False


class TestReaderRejectsAStaleClient:
    """``IB_CLIENTS`` is never purged, so a corpse can outlive its node."""

    @pytest.mark.component
    def test_a_disposed_client_reports_disconnected_even_with_both_flags_set(self, register_client):
        """Fail-open on the one path that must fail closed.

        ``TradingNode.dispose()`` closes the loop without ``cancel_all_tasks()``
        (``live/node.py:449-458``), so a pending ``_stop_async`` may never run
        and both flags can be left set on a client whose node is gone.
        """
        register_client(TestIBConnection(is_disposed=True))

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False
        assert "disposed" in status.detail

    @pytest.mark.component
    def test_a_stopped_client_reports_disconnected(self, register_client):
        register_client(TestIBConnection(is_running=False))

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False

    @pytest.mark.component
    def test_a_client_that_cannot_report_its_lifecycle_is_treated_as_unusable(
        self, register_client
    ):
        """Absent lifecycle attributes mean "cannot tell", which fails closed."""
        register_client(object())

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False


class TestReaderReflectsTheAdapterFlags:
    """Both adapter flags must be set for the connection to count as live."""

    @pytest.mark.component
    def test_socket_up_and_client_ready_reports_connected(self, register_client):
        register_client(TestIBConnection())

        status = read_ibkr_connection_status(_settings())

        assert status.connected is True

    @pytest.mark.component
    def test_socket_down_reports_disconnected(self, register_client):
        client = register_client(TestIBConnection())
        client.drop_connection()

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False
        assert "socket" in status.detail.lower()

    @pytest.mark.component
    def test_socket_up_but_client_not_ready_reports_disconnected(self, register_client):
        """The adapter's post-reconnect handshake window is not a live connection.

        ``_is_client_ready`` is cleared by the client's ``_degrade()`` and set
        again only once ``_start_async()`` has completed the reconnect
        handshake. Treating the socket alone as sufficient would grant a live
        reading during the window in which subscriptions have not been restored.
        """
        register_client(TestIBConnection(socket_connected=True, client_ready=False))

        status = read_ibkr_connection_status(_settings())

        assert status.connected is False
        assert "ready" in status.detail.lower()

    @pytest.mark.component
    def test_reader_does_not_mutate_the_client(self, register_client):
        client = register_client(TestIBConnection())

        read_ibkr_connection_status(_settings())
        read_ibkr_connection_status(_settings())

        assert client._is_ib_connected.is_set() is True
        assert client._is_client_ready.is_set() is True


class TestReaderKeyingOnTheLiveClientId:
    """The reader must find *this session's* client, not any IB client."""

    @pytest.mark.component
    def test_a_client_cached_under_another_client_id_is_not_found(self, register_client):
        """The historical data client shares this cache dict (FR5).

        ``ibkr_client_id`` rotates over ``base..base+5`` on connect timeout, so
        several other entries can legitimately be present. Keying loosely would
        report the *historical* client's liveness as the live session's.
        """
        register_client(TestIBConnection(), client_id=1)

        status = read_ibkr_connection_status(_settings(live_client_id=LIVE_CLIENT_ID))

        assert status.connected is False

    @pytest.mark.component
    def test_a_client_cached_under_another_port_is_not_found(self, register_client):
        register_client(TestIBConnection(), port=7497)

        status = read_ibkr_connection_status(_settings(port=PORT))

        assert status.connected is False

    @pytest.mark.component
    def test_the_reader_uses_the_configured_live_client_id(self, register_client):
        """Non-default values, so the test proves the wiring and not the default."""
        register_client(TestIBConnection(), host="10.0.0.7", port=7497, client_id=20)

        status = read_ibkr_connection_status(
            _settings(host="10.0.0.7", port=7497, live_client_id=20, client_id=2)
        )

        assert status.connected is True


class TestAdapterFlagCanary:
    """Guard the private-attribute dependency this reader rests on."""

    @pytest.mark.component
    def test_the_real_client_still_exposes_both_connection_flags(self):
        """Fail loudly, and by name, if a Nautilus upgrade moves either flag.

        Without this the reader would degrade to a permanent, silent
        ``connected=False``: safe, but undiagnosable — the session would simply
        never be permitted to trade and nothing would say why.
        """
        loop = asyncio.new_event_loop()
        try:
            clock = LiveClock()
            client = InteractiveBrokersClient(
                loop=loop,
                msgbus=MessageBus(trader_id=TraderId("PAPER-a1b2c3d4"), clock=clock),
                cache=Cache(),
                clock=clock,
                host=HOST,
                port=PORT,
                client_id=LIVE_CLIENT_ID,
            )
        finally:
            loop.close()

        assert hasattr(client, "_is_ib_connected"), (
            "InteractiveBrokersClient no longer exposes `_is_ib_connected` — "
            "read_ibkr_connection_status() reads it as the socket-liveness flag"
        )
        assert hasattr(client, "_is_client_ready"), (
            "InteractiveBrokersClient no longer exposes `_is_client_ready` — "
            "read_ibkr_connection_status() reads it as the handshake-complete flag"
        )
        assert client._is_ib_connected.is_set() is False
        assert client._is_client_ready.is_set() is False


class TestReaderDrivesTheMonitor:
    """The two halves compose: a real reading moves the real state machine."""

    @pytest.mark.component
    def test_a_dropped_connection_withdraws_trading_permission(self, register_client):
        client = register_client(TestIBConnection())
        monitor = ConnectionMonitor(session_id="paper-a1b2c3d4")
        settings = _settings()

        monitor.observe(read_ibkr_connection_status(settings))
        monitor.confirm_state_reestablished(read_ibkr_connection_status(settings))
        assert monitor.trading_permitted is True

        client.drop_connection()
        state = monitor.observe(read_ibkr_connection_status(settings))

        assert state is ConnectionState.LOST
        assert monitor.trading_permitted is False

    @pytest.mark.component
    def test_a_socket_that_dies_before_confirmation_is_not_confirmed(self, register_client):
        """The reader and the mandatory status argument close the window together.

        The confirmation takes its own reading, so a socket that dropped between
        the caller's last poll and its call to ``confirm_state_reestablished``
        refuses instead of buying a whole poll interval of blind trading.
        """
        client = register_client(TestIBConnection())
        monitor = ConnectionMonitor(session_id="paper-a1b2c3d4")
        settings = _settings()

        monitor.observe(read_ibkr_connection_status(settings))
        monitor.confirm_state_reestablished(read_ibkr_connection_status(settings))
        monitor.observe(ConnectionStatus(connected=False, detail="socket down"))
        monitor.observe(read_ibkr_connection_status(settings))
        assert monitor.state is ConnectionState.RECOVERING

        client.drop_connection()
        state = monitor.confirm_state_reestablished(read_ibkr_connection_status(settings))

        assert state is ConnectionState.LOST
        assert monitor.trading_permitted is False
