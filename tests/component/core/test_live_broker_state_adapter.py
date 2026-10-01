"""Reading the broker's view against the REAL IB adapter code (Story 4.1).

Component tier: a real ``InteractiveBrokersClient``,
``InteractiveBrokersExecutionClient``, ``InteractiveBrokersInstrumentProvider``
and ``ExecutionEngine`` — only the socket (``_eclient``) is a stand-in, which
answers ``reqPositions`` by driving the adapter's own ``process_position`` /
``process_position_end`` / ``process_connection_closed``. So ``get_positions``,
``_await_request``, ``_end_request``, ``Requests``, ``_on_account_summary`` and
``get_instrument`` all run for real, and the reader is proven against the very
conflation it exists to defeat (F1) rather than against a stub's imitation of it.

Also the canaries for every private adapter member the duck-typed reader reads:
each fails by name if a Nautilus upgrade moves it. No ``TradingNode``, no
``LiveExecutionEngine``, no C logging (the autouse fixture checks).
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from ibapi import const as ibapi_const
from ibapi.contract import Contract
from nautilus_trader.adapters.interactive_brokers.client.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.client.common import IBPosition
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
    InteractiveBrokersInstrumentProviderConfig,
)
from nautilus_trader.adapters.interactive_brokers.execution import (
    InteractiveBrokersExecutionClient,
)
from nautilus_trader.adapters.interactive_brokers.providers import (
    InteractiveBrokersInstrumentProvider,
)
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus, is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.engine import ExecutionEngine
from nautilus_trader.execution.messages import GeneratePositionStatusReports
from nautilus_trader.model.identifiers import AccountId, TraderId
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.live_broker_state import (
    CASH_TAG,
    IB_EXEC_CLIENT_ID,
    IB_UNSET_DECIMAL,
    IB_UNSET_DOUBLE,
    OPEN_POSITIONS_REQUEST,
    READINESS_FLAGS,
    BrokerStateFailure,
    BrokerStateUnavailableError,
    read_broker_state,
)
from src.models.broker_state import BrokerPosition, CashBalance

pytestmark = pytest.mark.component

ACCOUNT = "DU4076626"
NVDA_CON_ID = 4815747


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()})"
    )


class RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.records.append(("info", event, fields))

    def warning(self, event: str, **fields) -> None:
        self.records.append(("warning", event, fields))

    def error(self, event: str, **fields) -> None:
        self.records.append(("error", event, fields))


class SocketStandIn:
    """The only fake: what IBKR would send back down the socket.

    ``reqPositions`` schedules ``script(client)`` — which calls the adapter's
    own ``process_*`` handlers, exactly as its message reader would — and
    ``reqContractDetails`` answers "no such contract" through the adapter's own
    ``process_contract_details_end``.
    """

    def __init__(self) -> None:
        self.client: InteractiveBrokersClient | None = None
        self.script = None
        self.positions_requests = 0

    def reqPositions(self) -> None:
        self.positions_requests += 1
        if self.script is not None:
            script, client = self.script, self.client
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(script(client)))

    def reqContractDetails(self, reqId, contract) -> None:
        client = self.client
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.ensure_future(client.process_contract_details_end(req_id=reqId))
        )

    def isConnected(self) -> bool:
        return True


def _contract(symbol: str, con_id: int) -> Contract:
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.currency = "USD"
    contract.conId = con_id
    return contract


def _positions(*rows):
    """A script: IBKR streams these ``position`` rows, then ``positionEnd``."""

    async def script(client) -> None:
        for account, symbol, con_id, quantity, avg_cost in rows:
            await client.process_position(
                account_id=account,
                contract=_contract(symbol, con_id),
                position=Decimal(str(quantity)),
                avg_cost=avg_cost,
            )
        await client.process_position_end()

    return script


async def _disconnect(client) -> None:
    client.process_connection_closed()


def _stack(*, connected: bool = True) -> SimpleNamespace:
    """The real adapter objects, wired the way the factory wires them."""
    loop = asyncio.get_running_loop()
    clock = LiveClock()
    msgbus = MessageBus(trader_id=TraderId("PAPER-a1b2c3d4"), clock=clock)
    cache = Cache()
    ib = InteractiveBrokersClient(
        loop=loop,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        host="127.0.0.1",
        port=4002,
        client_id=11,
    )
    socket = SocketStandIn()
    socket.client = ib
    ib._eclient = socket  # type: ignore[assignment]
    if connected:
        ib._is_ib_connected.set()
        ib._is_client_ready.set()
    provider = InteractiveBrokersInstrumentProvider(
        client=ib, clock=clock, config=InteractiveBrokersInstrumentProviderConfig()
    )
    nvda = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
    provider.add(nvda)
    provider.contract_id_to_instrument_id[NVDA_CON_ID] = nvda.id
    cache.add_instrument(nvda)
    exec_client = InteractiveBrokersExecutionClient(
        loop=loop,
        client=ib,
        account_id=AccountId(f"INTERACTIVE_BROKERS-{ACCOUNT}"),
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        instrument_provider=provider,
        config=InteractiveBrokersExecClientConfig(),
    )
    # What the exec client's own `connect()` does once `_connect` completes
    # (`live/execution_client.py:240`); the harness never runs `_connect`.
    exec_client._set_connected(True)
    engine = ExecutionEngine(msgbus=msgbus, cache=cache, clock=clock)
    engine.register_client(exec_client)
    return SimpleNamespace(
        ib=ib,
        socket=socket,
        exec_client=exec_client,
        node=SimpleNamespace(kernel=SimpleNamespace(exec_engine=engine)),
    )


def _push_account_summary(exec_client, *, cash: str = "100000.52") -> None:
    """IBKR's account-summary rows, through the adapter's own handler."""
    for tag, value, currency in [
        ("AccountType", "INDIVIDUAL", ""),
        (CASH_TAG, cash, "USD"),
        ("NetLiquidation", "100400.00", "USD"),
        ("FullAvailableFunds", "90000", "USD"),
        ("FullInitMarginReq", "60000", "USD"),
        ("FullMaintMarginReq", "60000", "USD"),
    ]:
        exec_client._on_account_summary(tag, value, currency)


class TestAgainstTheRealAdapter:
    async def test_positions_and_cash_round_trip_for_the_configured_account(self):
        """AC #1: every row for this account, another account's excluded, an
        unresolvable contract kept by name (D-F), cash from ``TotalCashValue``.
        """
        stack = _stack()
        _push_account_summary(stack.exec_client)
        stack.socket.script = _positions(
            (ACCOUNT, "NVDA", NVDA_CON_ID, 22, 180.5),
            ("DU9999999", "AAPL", 265598, 4, 200.0),
            (ACCOUNT, "ODD", 111, -3, 9.5),
        )
        log = RecordingLog()

        state = await read_broker_state(stack.node, log=log, timeout_seconds=15)

        assert state.account == "***626"
        assert state.positions == (
            BrokerPosition("IB-CONID-111", Decimal("-3"), Decimal("9.5"), 111, "ODD", False),
            BrokerPosition("NVDA.NASDAQ", Decimal("22"), Decimal("180.5"), NVDA_CON_ID, "NVDA"),
        )
        assert state.cash == (CashBalance("USD", Decimal("100000.52")),)
        unresolved = [f for _, e, f in log.records if e == "reconcile.broker_instrument_unresolved"]
        assert unresolved == [
            {"con_id": 111, "symbol": "ODD", "sec_type": "STK", "error_type": "ValueError"}
        ]

    async def test_a_flat_account_is_a_success_although_get_positions_says_none(self):
        """AC #3, against the real conflation: the same ``None`` the adapter
        returns for a timeout, the reader turns into a successful flat state.
        """
        stack = _stack()
        _push_account_summary(stack.exec_client)
        stack.socket.script = _positions()

        assert await stack.ib.get_positions(ACCOUNT) is None, (
            "F1 CANARY: get_positions no longer returns None for an empty account. If "
            "the adapter now tells empty from failed itself, the future observation in "
            "live_broker_state._read_positions may be replaceable by its return value"
        )
        state = await read_broker_state(stack.node, timeout_seconds=15)

        assert state.positions == ()
        assert state.is_flat
        assert state.cash == (CashBalance("USD", Decimal("100000.52")),)

    async def test_a_dropped_socket_is_connection_lost_never_flat(self):
        stack = _stack()
        _push_account_summary(stack.exec_client)
        stack.socket.script = _disconnect

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=15)

        assert caught.value.reason is BrokerStateFailure.CONNECTION_LOST

    async def test_the_adapters_own_timeout_on_a_joined_request_is_unanswered(self):
        """A request someone else started (Nautilus's reconciliation) and the
        adapter times out while the reader is joined to it. The adapter's
        ``wait_for`` **cancels** the shared future (measured at Task 1), so the
        reader must read a cancelled future as "unanswered", never as flat.
        """
        stack = _stack()
        _push_account_summary(stack.exec_client)
        ib = stack.ib
        request = ib._requests.add(
            req_id=ib._next_req_id(), name=OPEN_POSITIONS_REQUEST, handle=lambda: None
        )
        originator = asyncio.ensure_future(ib._await_request(request, 0.1))

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=5)

        assert caught.value.reason is BrokerStateFailure.POSITIONS_UNANSWERED
        assert await originator is None
        assert stack.socket.positions_requests == 0, "the reader joined; it issued nothing"

    async def test_a_socket_that_is_down_issues_nothing(self):
        stack = _stack(connected=False)
        _push_account_summary(stack.exec_client)

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=5)

        assert caught.value.reason is BrokerStateFailure.NOT_CONNECTED
        assert stack.socket.positions_requests == 0

    async def test_cash_never_pushed_is_unavailable_not_zero(self):
        stack = _stack()
        stack.exec_client._on_account_summary("AccountType", "INDIVIDUAL", "")
        stack.socket.script = _positions()

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=0.3)

        assert caught.value.reason is BrokerStateFailure.CASH_UNAVAILABLE

    async def test_giving_up_never_cancels_the_request_nautilus_shares(self):
        """AC #5 / D-C: the reader times out on a silent broker; the real
        ``generate_position_status_reports`` joined to the same request still
        gets IBKR's answer when it finally arrives.
        """
        stack = _stack()
        _push_account_summary(stack.exec_client)

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=0.2)
        assert caught.value.reason is BrokerStateFailure.TIMEOUT

        request = stack.ib._requests.get(name=OPEN_POSITIONS_REQUEST)
        assert request is not None and not request.future.cancelled()
        command = GeneratePositionStatusReports(
            instrument_id=None, start=None, end=None, command_id=UUID4(), ts_init=0
        )
        joiner = asyncio.ensure_future(stack.exec_client.generate_position_status_reports(command))
        await asyncio.sleep(0)
        await _positions((ACCOUNT, "NVDA", NVDA_CON_ID, 22, 180.5))(stack.ib)

        reports = await asyncio.wait_for(joiner, timeout=5)
        assert [(str(r.instrument_id), r.signed_decimal_qty) for r in reports] == [
            ("NVDA.NASDAQ", Decimal("22"))
        ]


class TestAdapterCanaries:
    """Each private member the duck-typed reader reads, pinned by name."""

    def test_the_ib_exec_client_id_is_the_adapters_constant(self):
        assert IB == IB_EXEC_CLIENT_ID

    async def test_the_exec_engine_exposes_its_clients_by_client_id(self):
        stack = _stack()
        engine = stack.node.kernel.exec_engine
        assert {str(key): value for key, value in engine._clients.items()} == {
            IB_EXEC_CLIENT_ID: stack.exec_client
        }

    async def test_the_exec_client_members_the_reader_reads(self):
        stack = _stack()
        client = stack.exec_client
        assert client._client is stack.ib
        assert client.account_id.get_id() == ACCOUNT
        assert isinstance(client.instrument_provider, InteractiveBrokersInstrumentProvider)
        assert client._account_summary == {}
        assert client.is_connected is True
        client._set_connected(False)
        assert client.is_connected is False

    async def test_the_ib_client_members_the_reader_reads(self):
        stack = _stack(connected=False)
        ib = stack.ib
        assert ib._loop is asyncio.get_running_loop()
        for name in READINESS_FLAGS:
            assert getattr(ib, name).is_set() is False, f"the IB client has no {name}"
        task = asyncio.ensure_future(ib.get_positions(ACCOUNT))
        await asyncio.sleep(0)
        request = ib._requests.get(name=OPEN_POSITIONS_REQUEST)
        assert request is not None, f"get_positions no longer registers {OPEN_POSITIONS_REQUEST!r}"
        assert isinstance(request.future, asyncio.Future)
        request.future.set_result([])
        assert await task is None

    def test_a_position_row_still_has_the_fields_the_reader_reads(self):
        assert IBPosition._fields == ("account_id", "contract", "quantity", "avg_cost")

    def test_the_unset_sentinels_are_ibapis(self):
        """The reader refuses ibapi's "no value" markers by value; if ibapi
        changes them, the reader would read a sentinel as data again."""
        assert IB_UNSET_DOUBLE == ibapi_const.UNSET_DOUBLE
        assert IB_UNSET_DECIMAL == ibapi_const.UNSET_DECIMAL

    async def test_an_unconnected_exec_client_is_refused_before_any_request(self):
        stack = _stack()
        _push_account_summary(stack.exec_client)
        stack.exec_client._set_connected(False)

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read_broker_state(stack.node, timeout_seconds=5)

        assert caught.value.reason is BrokerStateFailure.NOT_CONNECTED
        assert stack.socket.positions_requests == 0

    async def test_the_summary_keeps_cash_per_currency_and_nautilus_balance_is_not_cash(self):
        """F5: the reader reads ``TotalCashValue`` from ``_account_summary``
        because Nautilus's own balance is net liquidation — here replaced by
        the adapter's literal 400000 — never cash. Fails by name if fixed.
        """
        stack = _stack()
        generated: list[dict] = []
        stack.exec_client.generate_account_state = lambda **kwargs: generated.append(kwargs)

        _push_account_summary(stack.exec_client)

        assert stack.exec_client._account_summary["USD"][CASH_TAG] == 100000.52
        assert stack.exec_client._account_summary[""] == {"AccountType": "INDIVIDUAL"}
        [account_state] = generated
        [balance] = account_state["balances"]
        assert balance.total.as_decimal() == Decimal("400000.00"), (
            "F5 CANARY: the adapter no longer invents a 400000 total. Its balance is still "
            "net liquidation, not cash — keep reading TotalCashValue"
        )

    async def test_an_unknown_contract_raises_from_get_instrument(self):
        stack = _stack()
        with pytest.raises(ValueError):
            await stack.exec_client.instrument_provider.get_instrument(_contract("ODD", 111))
