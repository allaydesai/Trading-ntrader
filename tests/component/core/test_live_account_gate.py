"""Component tests for Layer 2 enforcement against a connected node (Story 1.4).

Component tier, not unit: the module under test imports the Nautilus IB
adapter's factory module to read the gateway's reported accounts. Nothing here
constructs a real ``TradingNode`` — the node is a structural double — so no C
logging is initialised and the file is safe for the parallel, non-forked
component suite.

The adapter's ``IB_CLIENTS`` cache is a module-level dict that leaks between
tests sharing a process (the same hazard ``tests/integration/core/
test_live_node_lifecycle.py`` documents). It is snapshotted and restored around
every test here rather than merely cleaned up after the tests that write to it,
so a test that never registers a client provably sees an empty cache.
"""

import asyncio

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.common.enums import ComponentState
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core import live_account_gate
from src.core.live_account_gate import (
    NOT_YET_STARTED_STATES,
    STARTUP_PHASE,
    gateway_reported_accounts,
    verify_connected_account,
)
from src.core.live_gate import GateFlags, GateMode, GateRefusalReason
from src.core.live_node_builder import GateRefusedError

HOST = "127.0.0.1"
PORT = 4002
LIVE_CLIENT_ID = 10
PAPER_ACCOUNT = "DU4076626"
REAL_ACCOUNT = "U1234567"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce this file's tier placement.

    Asserted on the *delta*, not the absolute state: under ``-n auto`` this file
    shares a worker process with component tests that do initialise C logging.
    What this file must never do is *change* the state.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — Layer 2 enforcement must not touch the "
        "C logging subsystem, and the non-forked, parallel component tier cannot host "
        "anything that does. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_ib_client_cache():
    """Give every test a private view of the adapter's module-level client cache."""
    original = dict(IB_CLIENTS)
    IB_CLIENTS.clear()
    yield
    IB_CLIENTS.clear()
    IB_CLIENTS.update(original)


class _FakeIBClient:
    """Stands in for ``InteractiveBrokersClient``; only ``accounts()`` is used."""

    def __init__(self, accounts: set[str], *, explode: bool = False) -> None:
        self._accounts = accounts
        self._explode = explode
        self.account_reads = 0

    def accounts(self) -> set[str]:
        self.account_reads += 1
        if self._explode:
            raise AssertionError("the account list must not be read on this path")
        return set(self._accounts)


class _FakeExecEngine:
    """Mirrors ``ExecutionEngine``'s two-part connection surface.

    ``registered_clients`` is modelled, not just ``check_connected()``, because
    the real ``check_connected()`` iterates the registered clients and returns
    ``True`` on an empty dict — so a node whose ``build()`` never ran reports
    "connected". A double that exposed only a bool could not express that state,
    and the guard against it would be untestable.
    """

    def __init__(self, connected: bool, *, registered: bool = True) -> None:
        self._connected = connected
        self.registered_clients = ["INTERACTIVE_BROKERS"] if registered else []

    def check_connected(self) -> bool:
        # Faithful to `execution/engine.pyx`: vacuously True with no clients.
        return True if not self.registered_clients else self._connected


class _FakeKernel:
    def __init__(self, connected: bool, *, registered: bool = True) -> None:
        self.exec_engine = _FakeExecEngine(connected, registered=registered)


class _FakeTrader:
    def __init__(self, strategy_states: dict[str, str], *, explode: bool = False) -> None:
        self._strategy_states = strategy_states
        self._explode = explode
        self.start_calls: list[str] = []

    def strategy_states(self) -> dict[str, str]:
        if self._explode:
            raise AttributeError("strategy_states was renamed in this Nautilus version")
        return dict(self._strategy_states)

    def start_strategy(self, strategy_id: str) -> None:
        """Present only so a test can prove the gate never calls it (AC #5)."""
        self.start_calls.append(strategy_id)


class _FakeNode:
    """A structural stand-in for ``TradingNode``.

    Kept local to this file rather than promoted to ``tests/component/doubles/``:
    that package holds reusable doubles for Nautilus *domain objects*
    (``test_engine.py``, ``test_order.py``, ``test_position.py``), and Epic 2's
    session runner is the story that will actually need a shared ``TestLiveNode``.
    """

    def __init__(
        self,
        *,
        connected: bool = True,
        registered: bool = True,
        strategy_states: dict[str, str] | None = None,
        trader_explodes: bool = False,
        stop_error: BaseException | None = None,
        stop_hangs: bool = False,
    ) -> None:
        self.kernel = _FakeKernel(connected, registered=registered)
        self.trader = _FakeTrader(strategy_states or {}, explode=trader_explodes)
        self.stop_calls = 0
        self._stop_error = stop_error
        self._stop_hangs = stop_hangs

    async def stop_async(self) -> None:
        self.stop_calls += 1
        if self._stop_hangs:
            await asyncio.sleep(3600)
        if self._stop_error is not None:
            raise self._stop_error


def _settings(
    *,
    mode: str = "paper",
    port: int = PORT,
    host: str = HOST,
    account: str = PAPER_ACCOUNT,
    real_money_account: str = "",
    live_client_id: int = LIVE_CLIENT_ID,
) -> IBKRSettings:
    """Every field the module under test reads, passed as an init kwarg.

    Init kwargs outrank environment variables in pydantic-settings, and
    ``_env_file=None`` disables the dotenv file — so no field listed here can be
    supplied by the developer's shell. Any field this module starts reading must
    be added, or that isolation silently lapses.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode=mode,
        ibkr_port=port,
        ibkr_host=host,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
        ibkr_live_client_id=live_client_id,
        ibkr_client_id=1,
    )


def _register_gateway(accounts: set[str], *, explode: bool = False, **key) -> _FakeIBClient:
    """Seed the adapter cache under the key Story 1.3's builder configures."""
    client = _FakeIBClient(accounts, explode=explode)
    IB_CLIENTS[
        (key.get("host", HOST), key.get("port", PORT), key.get("client_id", LIVE_CLIENT_ID))
    ] = client
    return client


class TestGatewayReportedAccounts:
    """The account list comes from the adapter's own cache, keyed on the connection."""

    @pytest.mark.component
    def test_accounts_are_read_for_the_configured_connection(self):
        """The key is (host, port, ibkr_live_client_id) — exactly what the builder passes."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT, "DU1234567"})

        # Act
        accounts = gateway_reported_accounts(_settings())

        # Assert
        assert accounts == frozenset({PAPER_ACCOUNT, "DU1234567"})

    @pytest.mark.component
    def test_no_registered_client_yields_an_empty_set_rather_than_raising(self):
        """A renamed or re-keyed adapter cache must fail closed, not explode."""
        # Arrange — cache deliberately left empty

        # Act
        accounts = gateway_reported_accounts(_settings())

        # Assert
        assert accounts == frozenset()

    @pytest.mark.component
    def test_a_different_client_id_is_not_this_session(self):
        """The historical client's socket is a different key and must not be read."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT}, client_id=1)

        # Act
        accounts = gateway_reported_accounts(_settings(live_client_id=LIVE_CLIENT_ID))

        # Assert
        assert accounts == frozenset()


class TestNotYetStartedStates:
    """The ordering guard compares against real Nautilus state names."""

    @pytest.mark.component
    def test_the_allowlist_names_real_component_states(self):
        """A Nautilus rename must break here, not silently refuse every session.

        ``Trader.strategy_states()`` returns ``{k: v.state.name}``, so the
        allowlist is compared against ``ComponentState`` member *names*. If one
        were renamed, `state not in NOT_YET_STARTED_STATES` would be True for
        every strategy and no session could ever start.
        """
        # Arrange
        real_names = {state.name for state in ComponentState}

        # Act & Assert
        assert NOT_YET_STARTED_STATES <= real_names
        assert ComponentState.PRE_INITIALIZED.name in NOT_YET_STARTED_STATES
        assert ComponentState.READY.name in NOT_YET_STARTED_STATES
        assert ComponentState.RUNNING.name not in NOT_YET_STARTED_STATES


class TestVerifyConnectedAccountPermits:
    """A clean paper gateway proceeds, untouched."""

    @pytest.mark.component
    async def test_paper_accounts_permit_and_leave_the_node_running(self):
        """The permitted path returns the decision and does not stop the node."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode()

        # Act
        decision = await verify_connected_account(node, _settings(), cli_flags=GateFlags())

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER
        assert node.stop_calls == 0

    @pytest.mark.component
    async def test_added_but_unstarted_strategies_do_not_trip_the_ordering_guard(self):
        """AR39's normal position: strategies registered, none started yet."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode(
            strategy_states={"SMACross-000": "READY", "Momentum-001": "PRE_INITIALIZED"}
        )

        # Act
        decision = await verify_connected_account(node, _settings())

        # Assert
        assert decision.permitted is True
        assert node.stop_calls == 0

    @pytest.mark.component
    async def test_cli_flags_default_to_no_declaration(self):
        """Omitting cli_flags is the same as passing an empty GateFlags()."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode()

        # Act
        decision = await verify_connected_account(node, _settings())

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER


class TestVerifyConnectedAccountRefuses:
    """Every refusal stops the node and raises the static gate's own exception."""

    @pytest.mark.component
    async def test_non_paper_reported_account_stops_the_node_and_raises(self):
        """A real account on the paper gateway: the case Layer 1 cannot see."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT, REAL_ACCOUNT})
        node = _FakeNode()

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert — raising is not enough; the node must actually be down
        assert exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER
        assert node.stop_calls == 1
        assert PAPER_ACCOUNT not in str(exc.value)
        assert REAL_ACCOUNT not in str(exc.value)

    @pytest.mark.component
    async def test_layer_one_refusal_reaches_the_caller_unchanged(self):
        """Layer 2's seam is also the enforcement point for a Layer 1 refusal."""
        # Arrange — a non-paper port; the reported account is spotless
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode()

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings(port=7496))

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.NON_PAPER_PORT
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_unconnected_node_refuses_without_reading_any_account(self):
        """Called before the connection exists, there is no evidence to judge."""
        # Arrange
        client = _register_gateway({PAPER_ACCOUNT}, explode=True)
        node = _FakeNode(connected=False)

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.NODE_NOT_CONNECTED
        assert client.account_reads == 0
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_gateway_reporting_nothing_refuses(self):
        """No registered client at all — fail closed, never a KeyError."""
        # Arrange — cache deliberately left empty
        node = _FakeNode()

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.ACCOUNT_NOT_REPORTED
        assert node.stop_calls == 1

    @pytest.mark.component
    @pytest.mark.parametrize(
        "state",
        ["RUNNING", "STARTING", "STOPPING", "DEGRADED", "SOME_FUTURE_STATE"],
        ids=["running", "starting", "stopping", "degraded", "unknown-state-fails-closed"],
    )
    async def test_a_started_strategy_refuses_before_the_account_is_even_read(self, state):
        """AR39's ordering is enforced, not merely documented."""
        # Arrange
        client = _register_gateway({PAPER_ACCOUNT}, explode=True)
        node = _FakeNode(strategy_states={"SMACross-000": state})

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.STRATEGY_STARTED_BEFORE_ACCOUNT_GATE
        assert client.account_reads == 0
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_an_unbuilt_node_is_not_connected_despite_check_connected_saying_so(self):
        """`check_connected()` is vacuously True with zero registered clients."""
        # Arrange — models a node returned by build_trading_node() before build()
        node = _FakeNode(registered=False)
        assert node.kernel.exec_engine.check_connected() is True

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert — the registration check, not check_connected(), catches this
        assert exc.value.refusal.reason is GateRefusalReason.NODE_NOT_CONNECTED
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_a_raising_adapter_refuses_rather_than_escaping(self):
        """Adapter drift must not propagate past the gate with the node still up."""
        # Arrange — `accounts()` raises the way a renamed method would
        _register_gateway({PAPER_ACCOUNT}, explode=True)
        node = _FakeNode()

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert — a refusal, not an AssertionError leaking out
        assert exc.value.refusal.reason is GateRefusalReason.ACCOUNT_VERIFICATION_ERROR
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_a_raising_trader_refuses_rather_than_escaping(self):
        """The placement guard reaches Nautilus internals too, and must fail closed."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode(trader_explodes=True)

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.ACCOUNT_VERIFICATION_ERROR
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_a_cancelled_shutdown_still_delivers_the_refusal(self):
        """`CancelledError` is a BaseException — it must not abort before the raise."""
        # Arrange
        _register_gateway({REAL_ACCOUNT})
        node = _FakeNode(stop_error=asyncio.CancelledError())

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert — Story 1.7's exit-code mapping still has something to map
        assert exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_a_hanging_shutdown_does_not_withhold_the_refusal(self, monkeypatch):
        """An unbounded stop would leave the operator with no verdict at all."""
        # Arrange — a stop that never returns, against a deliberately tiny bound
        monkeypatch.setattr(live_account_gate, "STOP_TIMEOUT_SECONDS", 0.05)
        _register_gateway({REAL_ACCOUNT})
        node = _FakeNode(stop_hangs=True)

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER
        assert node.stop_calls == 1

    @pytest.mark.component
    async def test_no_strategy_is_started_on_any_path(self):
        """AC #5's 'no strategy is started' clause, asserted rather than assumed."""
        # Arrange
        _register_gateway({PAPER_ACCOUNT, REAL_ACCOUNT})
        node = _FakeNode(strategy_states={"SMACross-000": "READY"})

        # Act
        with pytest.raises(GateRefusedError):
            await verify_connected_account(node, _settings())

        # Assert
        assert node.trader.start_calls == []
        assert node.trader.strategy_states() == {"SMACross-000": "READY"}

    @pytest.mark.component
    async def test_a_failing_shutdown_does_not_mask_the_refusal(self):
        """The refusal is the operator-critical signal; shutdown is best-effort."""
        # Arrange
        _register_gateway({REAL_ACCOUNT})
        node = _FakeNode(stop_error=RuntimeError("kernel already stopping"))

        # Act
        with capture_logs() as logs:
            with pytest.raises(GateRefusedError) as exc:
                await verify_connected_account(node, _settings())

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER
        assert node.stop_calls == 1
        # The one signal telling an operator the node may still be up. Pinned
        # here because every assertion above still holds if `_stop_node`'s error
        # branch is replaced with `pass`.
        shutdown_records = [entry for entry in logs if "error_type" in entry]
        assert [entry["error_type"] for entry in shutdown_records] == ["RuntimeError"]
        # Only the exception TYPE, never its message: adapter and broker error
        # text routinely embeds the account identifier (NFR26).
        assert "kernel already stopping" not in repr(logs)


class TestVerifyConnectedAccountRealMoney:
    """The crossing is enforced against what the gateway reports."""

    @pytest.mark.component
    async def test_authorized_account_confirmed_by_the_gateway_permits(self):
        # Arrange
        _register_gateway({REAL_ACCOUNT})
        node = _FakeNode()
        settings = _settings(account=REAL_ACCOUNT, real_money_account=REAL_ACCOUNT)

        # Act
        decision = await verify_connected_account(
            node, settings, cli_flags=GateFlags(real_money=True)
        )

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        assert node.stop_calls == 0

    @pytest.mark.component
    async def test_paper_prefixed_authorization_is_stopped(self):
        """`--real-money` against a demo account never reaches trading."""
        # Arrange
        _register_gateway({"DU1234567"})
        node = _FakeNode()
        settings = _settings(account="DU1234567", real_money_account="DU1234567")

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, settings, cli_flags=GateFlags(real_money=True))

        # Assert
        assert exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_IS_PAPER
        assert node.stop_calls == 1


class TestAccountGatePhaseLogging:
    """AR39's phase fields and NFR26's masking, on every path."""

    @pytest.mark.component
    async def test_permit_logs_started_then_ok(self):
        # Arrange
        _register_gateway({PAPER_ACCOUNT})
        node = _FakeNode()

        # Act
        with capture_logs() as logs:
            await verify_connected_account(node, _settings())

        # Assert
        statuses = [entry.get("status") for entry in logs if entry.get("phase") == STARTUP_PHASE]
        assert statuses == ["started", "ok"]

    @pytest.mark.component
    async def test_refusal_logs_started_then_failed_with_the_reason(self):
        # Arrange
        _register_gateway({REAL_ACCOUNT})
        node = _FakeNode()

        # Act
        with capture_logs() as logs:
            with pytest.raises(GateRefusedError):
                await verify_connected_account(node, _settings())

        # Assert
        phase_logs = [entry for entry in logs if entry.get("phase") == STARTUP_PHASE]
        assert [entry.get("status") for entry in phase_logs] == ["started", "failed"]
        assert phase_logs[-1].get("reason") == GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER.value
        # AR41 enumerates `gate.refused` as this event's name.
        assert phase_logs[-1].get("event") == "gate.refused"

    @pytest.mark.component
    async def test_the_permit_record_names_the_accounts_the_gate_actually_judged(self):
        """Masked, de-duplicated, and normalised — not the raw adapter payload.

        IBKR's comma-separated `managedAccounts` yields a blank member from a
        trailing comma; masking that raw set would print `accounts=, ***626`,
        which is neither what the gate judged nor what Procedure P2's pass
        criterion expects to read.
        """
        # Arrange
        _register_gateway({PAPER_ACCOUNT, "", "  "})
        node = _FakeNode()

        # Act
        with capture_logs() as logs:
            await verify_connected_account(node, _settings())

        # Assert
        ok_record = next(entry for entry in logs if entry.get("status") == "ok")
        assert ok_record["accounts"] == "***626"

    @pytest.mark.component
    async def test_distinct_accounts_sharing_a_masked_suffix_are_not_collapsed_silently(self):
        """Two accounts, one mask — the operator must still see two entries."""
        # Arrange — both mask to ***567
        _register_gateway({PAPER_ACCOUNT, "U1234567", "X7654567"})
        node = _FakeNode()

        # Act
        with pytest.raises(GateRefusedError) as exc:
            await verify_connected_account(node, _settings())

        # Assert — de-duplicated to one entry rather than printing "***567, ***567"
        assert exc.value.refusal.message.count("***567") == 1

    @pytest.mark.component
    @pytest.mark.parametrize(
        "reported",
        [{PAPER_ACCOUNT}, {PAPER_ACCOUNT, REAL_ACCOUNT}],
        ids=["permit", "refusal"],
    )
    async def test_no_log_record_ever_carries_a_full_account(self, reported):
        """NFR26 — masked to the last three characters, everywhere."""
        # Arrange
        _register_gateway(reported)
        node = _FakeNode()

        # Act
        with capture_logs() as logs:
            try:
                await verify_connected_account(node, _settings())
            except GateRefusedError:
                pass

        # Assert
        rendered = repr(logs)
        for account in reported:
            assert account not in rendered, f"{account!r} leaked into a log record"
        assert "***" in rendered
