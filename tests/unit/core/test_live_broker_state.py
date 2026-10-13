"""Reading the broker's view of positions and cash (Story 4.1) — unit tier.

The reader is duck-typed with no Nautilus import, so every path is driven here
against stubs shaped like the IB adapter's own members. The stub's
``get_positions`` mirrors the adapter's conflation exactly (F1): it returns
``None`` for an empty answer, a timeout and a lost connection alike — so a
reader that trusted that return value would fail these tests. The component
suite (``tests/component/core/test_live_broker_state_adapter.py``) repeats the
decisive cases against the real adapter code.
"""

import ast
import asyncio
import dataclasses
import gc
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import live_broker_state as reader_module
from src.core.exit_outcome import EXIT_CODES
from src.core.live_broker_state import (
    CASH_TAG,
    DEFAULT_BROKER_STATE_TIMEOUT_SECONDS,
    FAILED_EVENT,
    IB_EXEC_CLIENT_ID,
    IB_UNSET_DECIMAL,
    IB_UNSET_DOUBLE,
    NFR5_BUDGET_SECONDS,
    OPEN_POSITIONS_REQUEST,
    RETRIEVED_EVENT,
    UNRESOLVED_EVENT,
    BrokerStateAdapterError,
    BrokerStateFailure,
    BrokerStateUnavailableError,
    read_broker_state,
    render_broker_state,
)
from src.core.live_check import classify_failure, failure_message
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance

pytestmark = pytest.mark.unit

ACCOUNT = "DU4076626"
MASKED = "***626"


# --------------------------------------------------------------------------- stubs


class RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def _record(self, level: str, event: str, **fields) -> None:
        self.records.append((level, event, fields))

    def info(self, event: str, **fields) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields) -> None:
        self._record("error", event, **fields)

    def events(self, name: str) -> list[tuple[str, dict]]:
        return [(level, fields) for level, event, fields in self.records if event == name]


class _Flag:
    def __init__(self, value: bool) -> None:
        self._value = value

    def is_set(self) -> bool:
        return self._value


class _Request:
    def __init__(self, future: asyncio.Future) -> None:
        self.future = future


class _Registry:
    """The adapter's ``Requests``: the reader may only ``get``."""

    def __init__(self, writes: list[str]) -> None:
        self.by_name: dict[str, _Request] = {}
        self._writes = writes

    def get(self, req_id=None, name=None):
        return self.by_name.get(name)

    def add(self, *args, **kwargs):
        self._writes.append("_requests.add")

    def remove(self, *args, **kwargs):
        self._writes.append("_requests.remove")


class _EClientSpy:
    """The socket-facing ``EClient``: the reader must never call it itself."""

    def __init__(self, writes: list[str]) -> None:
        self._writes = writes

    def reqPositions(self):
        self._writes.append("_eclient.reqPositions")

    def cancelPositions(self):
        self._writes.append("_eclient.cancelPositions")

    def reqAccountSummary(self, *args, **kwargs):
        self._writes.append("_eclient.reqAccountSummary")


def answers(rows):
    def script(ib, future: asyncio.Future) -> None:
        asyncio.get_running_loop().call_soon(future.set_result, list(rows))

    return script


def disconnects(ib, future: asyncio.Future) -> None:
    asyncio.get_running_loop().call_soon(
        future.set_exception, ConnectionError("Socket disconnected.")
    )


def adapter_times_out(ib, future: asyncio.Future) -> None:
    """The adapter's own ``wait_for`` cancels the shared future (probed, Task 1)."""
    asyncio.get_running_loop().call_soon(future.cancel)


def adapter_raises_timeout(ib, future: asyncio.Future) -> None:
    """``_end_request(success=False, exception=TimeoutError())`` on a pending future."""
    asyncio.get_running_loop().call_soon(future.set_exception, TimeoutError())


def never(ib, future: asyncio.Future) -> None:
    """IBKR never answers; the request stays registered, its future pending."""


def ended_without_answer(ib, future: asyncio.Future) -> None:
    """F2's IB-error path, faithfully: ``_end_request(success=False)`` with no
    exception removes the request from the registry and leaves its future
    pending forever."""
    asyncio.get_running_loop().call_later(
        0.01, ib._requests.by_name.pop, OPEN_POSITIONS_REQUEST, None
    )


class StubIBClient:
    """``InteractiveBrokersClient`` as the reader sees it.

    ``get_positions`` joins an in-flight ``OpenPositions`` request or registers
    one (before its first ``await``, like the adapter), awaits its future, and
    returns ``None`` for an empty list, a timeout or a lost connection — the
    adapter's own conflation (F1).
    """

    def __init__(
        self,
        script=None,
        *,
        connected=True,
        ready=True,
        send_error=None,
        registers=True,
        register_delay=0.0,
    ) -> None:
        self.writes: list[str] = []
        self._requests = _Registry(self.writes)
        self._eclient = _EClientSpy(self.writes)
        self._is_ib_connected = _Flag(connected)
        self._is_client_ready = _Flag(ready)
        self._loop = asyncio.get_running_loop()
        self._script = answers(()) if script is None else script
        self._send_error = send_error
        self._registers = registers
        self._register_delay = register_delay
        self.get_positions_calls = 0

    async def get_positions(self, account_id):
        self.get_positions_calls += 1
        if not self._registers:
            return None
        if self._register_delay:
            await asyncio.sleep(self._register_delay)
        request = self._requests.get(name=OPEN_POSITIONS_REQUEST)
        if request is None:
            request = _Request(asyncio.get_running_loop().create_future())
            self._requests.by_name[OPEN_POSITIONS_REQUEST] = request
            if self._send_error is not None:
                raise self._send_error
            self._script(self, request.future)
        try:
            rows = await request.future
        except (TimeoutError, ConnectionError):
            return None
        finally:
            if self._requests.by_name.get(OPEN_POSITIONS_REQUEST) is request:
                del self._requests.by_name[OPEN_POSITIONS_REQUEST]
        if not rows:
            return None
        return [row for row in rows if row.account_id == account_id]

    # Write-shaped members: the reader must never call any of them (D-B).
    def _end_request(self, *args, **kwargs):
        self.writes.append("_end_request")

    def subscribe_account_summary(self):
        self.writes.append("subscribe_account_summary")

    def subscribe_positions(self):
        self.writes.append("subscribe_positions")


class _InstrumentId:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value


class StubProvider:
    def __init__(
        self, known: dict[int, str] | None = None, *, delay: float = 0.0, returns_none=()
    ) -> None:
        self.known = {4815747: "NVDA.NASDAQ", 265598: "AAPL.NASDAQ"} if known is None else known
        self.delay = delay
        self.returns_none = set(returns_none)

    async def get_instrument(self, contract):
        if self.delay:
            await asyncio.sleep(self.delay)
        if contract.conId in self.returns_none:
            return None
        if contract.conId not in self.known:
            raise ValueError(f"Instrument not found for contract {contract}")
        return SimpleNamespace(id=_InstrumentId(self.known[contract.conId]))


class _AccountId:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_id(self) -> str:
        return self._value


def _summary(cash=100000.52, currency="USD"):
    tags = {"NetLiquidation": 100400.0, "AccountType": "x"}
    if cash is not None:
        tags[CASH_TAG] = cash
    return {currency: tags, "": {"AccountType": "INDIVIDUAL", "Cushion": 0.9}}


class StubExecClient:
    def __init__(
        self, ib_client, *, summary=None, provider=None, account=ACCOUNT, is_connected=True
    ) -> None:
        self._client = ib_client
        self.account_id = _AccountId(account)
        self._account_summary = _summary() if summary is None else summary
        self.instrument_provider = StubProvider() if provider is None else provider
        self.is_connected = is_connected


class _ClientId:
    """Mimics ``ClientId``: a key whose ``str`` is the id."""

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return self._value

    def __hash__(self) -> int:
        return hash(self._value)


def node_with(exec_client) -> SimpleNamespace:
    clients = {} if exec_client is None else {_ClientId(IB_EXEC_CLIENT_ID): exec_client}
    return SimpleNamespace(kernel=SimpleNamespace(exec_engine=SimpleNamespace(_clients=clients)))


def row(con_id, quantity, avg_cost=180.5, *, account=ACCOUNT, symbol="NVDA", multiplier=""):
    contract = SimpleNamespace(conId=con_id, symbol=symbol, secType="STK", multiplier=multiplier)
    if not isinstance(quantity, Decimal):
        quantity = Decimal(str(quantity))
    return SimpleNamespace(
        account_id=account, contract=contract, quantity=quantity, avg_cost=avg_cost
    )


async def read(node, **kwargs):
    kwargs.setdefault("log", RecordingLog())
    return await read_broker_state(node, **kwargs)


def _setup(script=None, *, ib_kwargs=None, **exec_kwargs):
    ib = StubIBClient(script, **(ib_kwargs or {}))
    ex = StubExecClient(ib, **exec_kwargs)
    return ib, ex, node_with(ex)


# --------------------------------------------------------------------------- AC #1


class TestTheBrokerIsRead:
    async def test_positions_and_cash_are_read_for_the_configured_account(self):
        ib, _, node = _setup(answers([row(4815747, 22, 180.5)]))
        log = RecordingLog()

        state = await read_broker_state(node, log=log)

        assert state.positions == (
            BrokerPosition(
                instrument_id="NVDA.NASDAQ",
                quantity=Decimal("22"),
                average_price=Decimal("180.5"),
                con_id=4815747,
                symbol="NVDA",
            ),
        )
        assert state.cash == (CashBalance(currency="USD", total_cash=Decimal("100000.52")),)
        assert state.account == MASKED
        assert ib.get_positions_calls == 1

    async def test_rows_for_another_account_on_the_login_are_excluded(self):
        _, _, node = _setup(
            answers([row(4815747, 22), row(265598, 4, 200.0, account="DU9999999", symbol="AAPL")])
        )

        state = await read(node)

        assert [p.instrument_id for p in state.positions] == ["NVDA.NASDAQ"]

    async def test_a_short_position_has_a_negative_quantity(self):
        _, _, node = _setup(answers([row(4815747, -7)]))

        state = await read(node)

        assert state.positions[0].quantity == Decimal("-7")

    async def test_the_last_row_per_contract_wins(self):
        """F4: a streaming update lands in an in-flight request's result, so a
        contract can appear twice. Each ``position`` callback carries the full
        position, so the later row is the truth.
        """
        _, _, node = _setup(answers([row(4815747, 22, 180.5), row(4815747, 23, 181.0)]))

        state = await read(node)

        assert len(state.positions) == 1
        assert state.positions[0].quantity == Decimal("23")
        assert state.positions[0].average_price == Decimal("181.0")

    async def test_a_zero_quantity_row_is_flat_and_is_skipped(self):
        _, _, node = _setup(answers([row(4815747, 22), row(4815747, 0), row(265598, 0, 1.0)]))

        state = await read(node)

        assert state.positions == ()
        assert state.is_flat

    @pytest.mark.parametrize(
        ("avg_cost", "multiplier", "expected"),
        [
            (180.5, "", Decimal("180.5")),
            (1805.0, "100", Decimal("18.05")),
            (180.5, "not-a-number", Decimal("180.5")),
            (0.0, "", None),
            (-1.0, "", None),
            (float("nan"), "", None),
            (float("inf"), "", None),
            (IB_UNSET_DOUBLE, "", None),
            (None, "", None),
        ],
    )
    async def test_the_average_price(self, avg_cost, multiplier, expected):
        _, _, node = _setup(answers([row(4815747, 22, avg_cost, multiplier=multiplier)]))

        state = await read(node)

        assert state.positions[0].average_price == expected

    async def test_output_is_sorted_by_instrument_and_currency(self):
        summary = {
            "USD": {CASH_TAG: 10.0},
            "EUR": {CASH_TAG: 5.0},
            "": {"AccountType": "INDIVIDUAL"},
        }
        _, _, node = _setup(
            answers([row(4815747, 22), row(265598, 4, 200.0, symbol="AAPL")]), summary=summary
        )

        state = await read(node)

        assert [p.instrument_id for p in state.positions] == ["AAPL.NASDAQ", "NVDA.NASDAQ"]
        assert [c.currency for c in state.cash] == ["EUR", "USD"]

    async def test_an_unresolvable_instrument_is_reported_not_dropped(self):
        """D-F: dropping the row would be a partial "flat"."""
        _, _, node = _setup(answers([row(4815747, 22), row(111, 3, 9.5, symbol="ODD")]))
        log = RecordingLog()

        state = await read_broker_state(node, log=log)

        unresolved = [p for p in state.positions if not p.instrument_resolved]
        assert unresolved == [
            BrokerPosition(
                instrument_id="IB-CONID-111",
                quantity=Decimal("3"),
                average_price=Decimal("9.5"),
                con_id=111,
                symbol="ODD",
                instrument_resolved=False,
            )
        ]
        [(level, fields)] = log.events(UNRESOLVED_EVENT)
        assert level == "warning"
        assert fields == {
            "con_id": 111,
            "symbol": "ODD",
            "sec_type": "STK",
            "error_type": "ValueError",
        }
        assert log.events(RETRIEVED_EVENT), "an unresolved row is not a failed read"

    async def test_a_provider_answering_nothing_leaves_one_unresolved_row(self):
        _, _, node = _setup(
            answers([row(4815747, 22), row(111, 3, symbol="ODD")]),
            provider=StubProvider(returns_none={111}),
        )
        log = RecordingLog()

        state = await read_broker_state(node, log=log)

        assert [p.instrument_id for p in state.positions] == ["IB-CONID-111", "NVDA.NASDAQ"]
        [(_, fields)] = log.events(UNRESOLVED_EVENT)
        assert fields["error_type"] == "NoInstrument"

    async def test_tags_without_a_real_currency_are_ignored_not_fatal(self):
        """``""`` carries currency-less tags; ``BASE`` aggregates the others
        and would double-count; a malformed key must not fail the read."""
        summary = _summary()
        summary["BASE"] = {CASH_TAG: 999.0}
        summary["usd"] = {CASH_TAG: 1.0}
        summary["US"] = {CASH_TAG: 2.0}
        _, _, node = _setup(answers(()), summary=summary)

        state = await read(node)

        assert state.cash == (CashBalance(currency="USD", total_cash=Decimal("100000.52")),)

    async def test_the_success_record_names_what_was_read(self):
        _, _, node = _setup(answers([row(4815747, 22)]))
        log = RecordingLog()

        await read_broker_state(node, log=log)

        [(level, fields)] = log.events(RETRIEVED_EVENT)
        assert level == "info"
        assert fields["account"] == MASKED
        assert fields["position_count"] == 1
        assert fields["positions"] == {"NVDA.NASDAQ": "22"}
        assert fields["cash"] == {"USD": "100000.52"}
        assert fields["elapsed_ms"] >= 0
        assert log.events(FAILED_EVENT) == []


# --------------------------------------------------------------------------- AC #2


def _leaves(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from _leaves(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            yield from _leaves(item)
    else:
        yield value


class TestNothingPastTheBoundaryIsAnAdapterType:
    async def test_the_state_holds_only_domain_and_standard_types(self):
        _, _, node = _setup(answers([row(4815747, 22), row(111, 3, 9.5, symbol="ODD")]))

        state = await read(node)

        assert isinstance(state, BrokerState)
        allowed = (str, int, bool, Decimal, datetime, type(None))
        leaves = list(_leaves(state))
        assert leaves, "the walk found nothing — it is not walking the state"
        foreign = [type(leaf).__name__ for leaf in leaves if not isinstance(leaf, allowed)]
        assert foreign == []

    async def test_no_record_ever_carries_the_raw_account(self):
        log = RecordingLog()
        _, _, node = _setup(answers([row(4815747, 22), row(111, 3, symbol="ODD")]))
        await read_broker_state(node, log=log)
        for script in (never, disconnects):
            _, _, failing = _setup(script)
            with pytest.raises(BrokerStateUnavailableError):
                await read_broker_state(failing, log=log, timeout_seconds=0.05)

        assert len(log.records) >= 4, "too little was recorded to check anything"
        for _, event, fields in log.records:
            assert ACCOUNT not in repr(fields), f"{event} leaked the raw account"

    async def test_drift_never_carries_the_adapter_exception_or_its_text(self):
        """NFR26: an adapter exception's text may hold the raw account, so the
        drift error drops the chain (``from None``) and names only the type
        and where it was raised."""

        class LeakyClientId:
            def __str__(self) -> str:
                raise RuntimeError(f"account {ACCOUNT} is not managed")

            def __hash__(self) -> int:
                return 1

        node = SimpleNamespace(
            kernel=SimpleNamespace(exec_engine=SimpleNamespace(_clients={LeakyClientId(): None}))
        )
        log = RecordingLog()

        with pytest.raises(BrokerStateAdapterError) as caught:
            await read_broker_state(node, log=log)

        drift = caught.value
        assert drift.__cause__ is None and drift.__suppress_context__
        assert drift.error_type == "RuntimeError"
        assert "test_live_broker_state.py" in drift.detail, "the raise site is named"
        assert ACCOUNT not in str(drift)
        [(_, fields)] = log.events(FAILED_EVENT)
        assert fields["error_type"] == "RuntimeError"
        assert ACCOUNT not in repr(fields)


# --------------------------------------------------------------------------- AC #3


class TestFlatIsDistinguishableFromFailed:
    async def test_a_flat_account_is_a_successful_empty_state(self):
        """The stub's ``get_positions`` returns ``None`` here — exactly the
        value it returns for a timeout. The reader must still answer "flat".
        """
        _, _, node = _setup(answers(()))

        state = await read(node)

        assert isinstance(state, BrokerState)
        assert state.positions == ()
        assert state.is_flat
        assert state.cash

    async def test_the_same_none_from_a_timeout_raises_instead(self):
        _, _, node = _setup(adapter_times_out)

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read(node)

        assert caught.value.reason is BrokerStateFailure.POSITIONS_UNANSWERED

    async def test_positions_only_on_another_account_is_flat_too(self):
        _, _, node = _setup(answers([row(265598, 4, account="DU9999999", symbol="AAPL")]))

        state = await read(node)

        assert state.is_flat


# --------------------------------------------------------------------------- AC #4


class _Missing:
    """An exec client whose adapter member moved: no ``_client``."""

    def __init__(self) -> None:
        self.account_id = _AccountId(ACCOUNT)


class _RenamedFlagIB(StubIBClient):
    """An upgrade renamed ``_is_ib_connected``."""

    def __init__(self) -> None:
        super().__init__()
        del self._is_ib_connected


def _no_cash_summary():
    return {"USD": {"NetLiquidation": 100400.0}, "": {}}


def _on_other_loop():
    ib, ex, node = _setup(answers(()))
    ib._loop = object()
    return node


def _ib(**ib_kwargs):
    return _setup(ib_kwargs=ib_kwargs)[2]


UNAVAILABLE, DRIFT = BrokerStateUnavailableError, BrokerStateAdapterError
F = BrokerStateFailure

FAILURES = {
    # Refused before anything is requested.
    "socket_down": (lambda: _ib(connected=False), F.NOT_CONNECTED, UNAVAILABLE),
    "client_not_ready": (lambda: _ib(ready=False), F.NOT_CONNECTED, UNAVAILABLE),
    "exec_client_still_connecting": (
        lambda: _setup(answers(()), is_connected=False)[2],
        F.NOT_CONNECTED,
        UNAVAILABLE,
    ),
    # Drift: the node or adapter does not look the way it is read (exit 1).
    "no_exec_client": (lambda: node_with(None), F.ADAPTER_INCOMPATIBLE, DRIFT),
    "readiness_flag_renamed": (
        lambda: node_with(StubExecClient(_RenamedFlagIB())),
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "adapter_member_moved": (lambda: node_with(_Missing()), F.ADAPTER_INCOMPATIBLE, DRIFT),
    "node_shape_moved": (
        lambda: SimpleNamespace(kernel=SimpleNamespace()),
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "wrong_event_loop": (_on_other_loop, F.ADAPTER_INCOMPATIBLE, DRIFT),
    "request_never_registered": (
        lambda: _ib(registers=False),
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "send_raises_unexpectedly": (
        lambda: _ib(send_error=ValueError("bad field")),
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "unset_quantity": (
        lambda: _setup(answers([row(4815747, IB_UNSET_DECIMAL)]))[2],
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "row_without_contract_id": (
        lambda: _setup(answers([row(0, 5), row(0, 7)]))[2],
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    "two_contracts_one_instrument": (
        lambda: _setup(
            answers([row(4815747, 22), row(9, 3)]),
            provider=StubProvider({4815747: "NVDA.NASDAQ", 9: "NVDA.NASDAQ"}),
        )[2],
        F.ADAPTER_INCOMPATIBLE,
        DRIFT,
    ),
    # The broker did not answer (exit 4).
    "our_deadline": (lambda: _setup(never)[2], F.TIMEOUT, UNAVAILABLE),
    "ended_without_answer": (lambda: _setup(ended_without_answer)[2], F.TIMEOUT, UNAVAILABLE),
    "registration_later_than_the_deadline": (
        lambda: _ib(register_delay=5.0),
        F.TIMEOUT,
        UNAVAILABLE,
    ),
    "adapter_timeout_cancels": (
        lambda: _setup(adapter_times_out)[2],
        F.POSITIONS_UNANSWERED,
        UNAVAILABLE,
    ),
    "adapter_timeout_error": (
        lambda: _setup(adapter_raises_timeout)[2],
        F.POSITIONS_UNANSWERED,
        UNAVAILABLE,
    ),
    "connection_lost": (lambda: _setup(disconnects)[2], F.CONNECTION_LOST, UNAVAILABLE),
    "send_on_a_dead_socket": (
        lambda: _ib(send_error=BrokenPipeError()),
        F.CONNECTION_LOST,
        UNAVAILABLE,
    ),
    "no_cash": (
        lambda: _setup(answers(()), summary=_no_cash_summary())[2],
        F.CASH_UNAVAILABLE,
        UNAVAILABLE,
    ),
    "nan_cash": (
        lambda: _setup(answers(()), summary=_summary(cash=float("nan")))[2],
        F.CASH_UNAVAILABLE,
        UNAVAILABLE,
    ),
    "inf_cash": (
        lambda: _setup(answers(()), summary=_summary(cash=float("inf")))[2],
        F.CASH_UNAVAILABLE,
        UNAVAILABLE,
    ),
    "unset_cash": (
        lambda: _setup(answers(()), summary=_summary(cash=IB_UNSET_DOUBLE))[2],
        F.CASH_UNAVAILABLE,
        UNAVAILABLE,
    ),
    "string_cash": (
        lambda: _setup(answers(()), summary=_summary(cash="n/a"))[2],
        F.CASH_UNAVAILABLE,
        UNAVAILABLE,
    ),
}


class TestAFailureIsNeverFlat:
    @pytest.mark.parametrize("case", sorted(FAILURES))
    async def test_each_failure_raises_its_reason_and_logs_it_once(self, case):
        build, reason, error_type = FAILURES[case]
        log = RecordingLog()

        with pytest.raises(error_type) as caught:
            await read_broker_state(build(), log=log, timeout_seconds=0.1)

        assert caught.value.reason is reason
        assert (error_type is DRIFT) == isinstance(caught.value, DRIFT)
        [(level, fields)] = log.events(FAILED_EVENT)
        assert level == "error"
        assert fields["reason"] == reason.value
        assert fields["elapsed_ms"] >= 0
        assert log.events(RETRIEVED_EVENT) == []

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            ("connection_lost", "ConnectionError"),
            ("send_on_a_dead_socket", "BrokenPipeError"),
            ("adapter_timeout_error", "TimeoutError"),
            ("send_raises_unexpectedly", "ValueError"),
        ],
    )
    async def test_the_failure_record_names_the_exception_that_caused_it(self, case, expected):
        build, _, error_type = FAILURES[case]
        log = RecordingLog()

        with pytest.raises(error_type):
            await read_broker_state(build(), log=log, timeout_seconds=0.1)

        [(_, fields)] = log.events(FAILED_EVENT)
        assert fields["error_type"] == expected

    async def test_a_refused_read_issues_no_request(self):
        for build in (FAILURES["socket_down"][0], FAILURES["exec_client_still_connecting"][0]):
            node = build()
            ib = node.kernel.exec_engine._clients[next(iter(node.kernel.exec_engine._clients))]
            with pytest.raises(BrokerStateUnavailableError):
                await read(node)
            assert ib._client.get_positions_calls == 0

    async def test_two_contracts_on_one_instrument_are_named_not_anonymous(self):
        """Not a bare model ``ValueError`` turned into drift: the operator is
        told which instrument and which contracts collided."""
        build = FAILURES["two_contracts_one_instrument"][0]

        with pytest.raises(BrokerStateAdapterError) as caught:
            await read(build())

        assert "NVDA.NASDAQ" in caught.value.detail
        assert "[9, 4815747]" in caught.value.detail
        assert caught.value.error_type is None, "raised by the reader, not a caught exception"

    async def test_a_dead_socket_fails_fast_not_at_the_deadline(self):
        started = time.monotonic()

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read(_ib(send_error=BrokenPipeError()), timeout_seconds=5.0)

        assert caught.value.reason is BrokerStateFailure.CONNECTION_LOST
        assert time.monotonic() - started < 1.0

    async def test_the_adapter_error_is_a_broker_state_error_too(self):
        """A caller catching the base class catches drift as well."""
        assert issubclass(BrokerStateAdapterError, BrokerStateUnavailableError)

    def test_exit_codes_follow_the_marker_protocol(self):
        unreachable = BrokerStateUnavailableError(BrokerStateFailure.TIMEOUT, "no answer")
        drift = BrokerStateAdapterError("a member moved")

        assert EXIT_CODES[classify_failure(unreachable)] == 4
        assert EXIT_CODES[classify_failure(drift)] == 1
        assert drift.reason is BrokerStateFailure.ADAPTER_INCOMPATIBLE
        assert "no answer" in failure_message(unreachable)

    @pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), NFR5_BUDGET_SECONDS + 1])
    async def test_a_timeout_outside_nfr5_is_refused_before_anything_is_requested(self, bad):
        ib, _, node = _setup(answers(()))

        with pytest.raises(ValueError, match="timeout_seconds"):
            await read(node, timeout_seconds=bad)

        assert ib.get_positions_calls == 0

    async def test_the_reader_never_writes_adapter_state(self):
        """D-B: it observes the adapter's request; it never adds, ends,
        cancels or re-subscribes anything, and never talks to the socket."""
        scripts = (answers([row(4815747, 22)]), adapter_times_out, disconnects, never)
        for script in scripts:
            ib, _, node = _setup(script)
            try:
                await read(node, timeout_seconds=0.05)
            except BrokerStateAdapterError:
                raise
            except BrokerStateUnavailableError:
                pass
            assert ib.writes == []

        ib, _, node = _setup(never)
        with pytest.raises(BrokerStateUnavailableError):
            await read(node, timeout_seconds=0.05)
        request = ib._requests.get(name=OPEN_POSITIONS_REQUEST)
        assert request is not None, "the reader removed the adapter's request"
        assert not request.future.done(), "the reader resolved or cancelled the shared future"

    async def test_a_raising_logger_cannot_replace_the_failure(self):
        class Exploding(RecordingLog):
            def _record(self, level, event, **fields):
                raise RuntimeError("log sink down")

        class NoMethods:
            def __getattr__(self, name):
                raise RuntimeError("lazy logger proxy failed to bind")

        for log in (Exploding(), NoMethods()):
            with pytest.raises(BrokerStateUnavailableError):
                await read_broker_state(_setup(never)[2], log=log, timeout_seconds=0.05)
            _, _, node = _setup(answers(()))
            assert (await read_broker_state(node, log=log)).is_flat


# --------------------------------------------------------------------------- AC #5


class TestTheReadIsBounded:
    def test_the_default_deadline_sits_inside_nfr5(self):
        assert DEFAULT_BROKER_STATE_TIMEOUT_SECONDS == 20.0
        assert DEFAULT_BROKER_STATE_TIMEOUT_SECONDS < NFR5_BUDGET_SECONDS == 30.0

    async def test_a_silent_broker_ends_the_read_at_the_deadline(self):
        started = time.monotonic()

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read(_setup(never)[2], timeout_seconds=0.2)

        assert caught.value.reason is BrokerStateFailure.TIMEOUT
        assert time.monotonic() - started < 0.7

    async def test_slow_instrument_resolution_counts_against_the_same_deadline(self):
        _, _, node = _setup(answers([row(4815747, 22)]), provider=StubProvider(delay=5.0))
        started = time.monotonic()

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read(node, timeout_seconds=0.2)

        assert caught.value.reason is BrokerStateFailure.TIMEOUT
        assert time.monotonic() - started < 0.7

    async def test_cash_that_never_arrives_counts_against_the_same_deadline(self):
        _, _, node = _setup(answers(()), summary=_no_cash_summary())
        started = time.monotonic()

        with pytest.raises(BrokerStateUnavailableError) as caught:
            await read(node, timeout_seconds=0.2)

        assert caught.value.reason is BrokerStateFailure.CASH_UNAVAILABLE
        assert time.monotonic() - started < 0.7

    async def test_cash_that_arrives_after_the_positions_is_waited_for(self):
        """F5: the exec client's connect returns after the *first* tag, so
        ``TotalCashValue`` can still be in flight.
        """
        summary = _no_cash_summary()
        _, _, node = _setup(answers(()), summary=summary)
        asyncio.get_running_loop().call_later(0.1, summary["USD"].__setitem__, CASH_TAG, 7.5)

        state = await read(node, timeout_seconds=2.0)

        assert state.cash == (CashBalance(currency="USD", total_cash=Decimal("7.5")),)

    async def test_the_shared_adapter_request_is_never_cancelled(self):
        """D-C: another awaiter of the same ``OpenPositions`` request (Nautilus's
        own reconciliation) must still get the broker's answer after this
        reader has given up.
        """
        ib, _, node = _setup(never)
        with pytest.raises(BrokerStateUnavailableError):
            await read(node, timeout_seconds=0.05)
        request = ib._requests.get(name=OPEN_POSITIONS_REQUEST)
        assert request is not None and not request.future.cancelled()

        joiner = asyncio.ensure_future(ib.get_positions(ACCOUNT))
        await asyncio.sleep(0)
        request.future.set_result([row(4815747, 22)])

        assert [r.quantity for r in await joiner] == [Decimal("22")]

    async def test_an_in_flight_request_is_joined_not_duplicated(self):
        ib, _, node = _setup(never)
        loop = asyncio.get_running_loop()
        existing = _Request(loop.create_future())
        ib._requests.by_name[OPEN_POSITIONS_REQUEST] = existing
        loop.call_later(0.05, existing.future.set_result, [row(4815747, 22)])

        state = await read(node, timeout_seconds=2.0)

        assert [p.quantity for p in state.positions] == [Decimal("22")]

    async def test_a_joined_request_answered_before_the_first_look_is_still_read(self):
        """The join race: the in-flight request is answered and removed from
        the registry in the very next loop step — before the reader's first
        observation — so it must be captured before the task starts."""
        ib, _, node = _setup(never)
        loop = asyncio.get_running_loop()
        existing = _Request(loop.create_future())
        ib._requests.by_name[OPEN_POSITIONS_REQUEST] = existing

        def answer_and_end() -> None:
            existing.future.set_result([row(4815747, 22)])
            ib._requests.by_name.pop(OPEN_POSITIONS_REQUEST, None)

        loop.call_soon(answer_and_end)

        state = await read(node, timeout_seconds=2.0)

        assert [p.quantity for p in state.positions] == [Decimal("22")]

    async def test_a_late_adapter_outcome_is_consumed_quietly(self):
        loop = asyncio.get_running_loop()
        unretrieved: list[dict] = []
        previous = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unretrieved.append(context))
        try:
            ib, _, node = _setup(never)
            with pytest.raises(BrokerStateUnavailableError):
                await read(node, timeout_seconds=0.05)
            ib._requests.get(name=OPEN_POSITIONS_REQUEST).future.set_exception(RuntimeError("late"))
            await asyncio.sleep(0.05)
            gc.collect()
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous)

        assert unretrieved == []

    async def test_a_healthy_read_is_timed(self):
        _, _, node = _setup(answers([row(4815747, 22)]))
        log = RecordingLog()

        await read_broker_state(node, log=log)

        [(_, fields)] = log.events(RETRIEVED_EVENT)
        assert 0 <= fields["elapsed_ms"] < 1000


# --------------------------------------------------------------------------- rendering


class TestRendering:
    def test_the_rendered_lines_name_every_position_and_balance_masked(self):
        state = BrokerState(
            account=MASKED,
            positions=(
                BrokerPosition("AAPL.NASDAQ", Decimal("-4"), None, 265598, "AAPL"),
                BrokerPosition("IB-CONID-111", Decimal("3"), Decimal("9.5"), 111, "ODD", False),
            ),
            cash=(CashBalance("USD", Decimal("100000.52")),),
            retrieved_at=datetime(2026, 9, 22, 20, 0, tzinfo=UTC),
        )

        lines = render_broker_state(state)

        assert lines[0].startswith(f"broker state account={MASKED} positions=2 flat=False")
        assert "position AAPL.NASDAQ qty=-4 avg_price=unknown" in lines
        assert "position IB-CONID-111 qty=+3 avg_price=9.5 (unresolved symbol=ODD)" in lines
        assert "cash USD total_cash=100000.52" in lines

    def test_a_flat_state_says_so(self):
        state = BrokerState(
            account=MASKED,
            positions=(),
            cash=(CashBalance("USD", Decimal("1")),),
            retrieved_at=datetime(2026, 9, 22, 20, 0, tzinfo=UTC),
        )

        assert render_broker_state(state)[0].endswith("positions=0 flat=True")


# --------------------------------------------------------------------------- purity


def _framework_imports(source: str) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return [name for name in imported if name.startswith(("nautilus_trader", "ibapi"))]


class TestTheModuleImportsNoFramework:
    """Duck-typed by design (D-E): unit-testable, and a Nautilus rename is
    caught by the component canaries rather than at import time.
    """

    def test_no_nautilus_or_ibapi_import(self):
        source = Path(reader_module.__file__).read_text(encoding="utf-8")

        assert "import" in source, "the scan is not reading the module"
        assert _framework_imports(source) == []

    @pytest.mark.parametrize(
        "planted",
        [
            "from nautilus_trader.model.identifiers import ClientId",
            "import ibapi.const",
            "def f():\n    import nautilus_trader",
        ],
    )
    def test_the_scan_can_fail(self, planted):
        """Non-vacuity twin: a planted framework import is caught, even nested."""
        assert _framework_imports(f"import asyncio\n{planted}\n") != []
