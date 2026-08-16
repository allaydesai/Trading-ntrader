"""Epic-1 acceptance conformance: the two runtime safety controls.

One test per acceptance criterion in Story 1.4 (verify the connected account is
a paper account before trading starts) and Story 1.6 (detect connection loss and
withhold trading permission) of
``_bmad-output/planning-artifacts/prd-epic1-scope.md``. They share a file
because they are the same shape of control: both decide, at run time, whether
the session may proceed, and both must fail closed.

See ``test_epic1_ac_gate.py`` for why this suite sits alongside the tier suites
rather than replacing them.

No broker, no socket, no clock (NFR32/NFR34): the node is a structural double
and the monitor's time source is injected, so every deadline is reached exactly
rather than waited for.
"""

import pytest
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_account_gate import STARTUP_PHASE, verify_connected_account
from src.core.live_connection_monitor import (
    DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    DEFAULT_RECONNECT_WINDOW_SECONDS,
    ConnectionMonitor,
    ConnectionState,
    ConnectionStatus,
)
from src.core.live_gate import GateMode, GateRefusalReason
from src.core.live_node_builder import GateRefusedError
from tests.integration.core.epic1_criteria import criterion

pytestmark = pytest.mark.integration

PAPER_ACCOUNT = "DU4076626"
REAL_ACCOUNT = "U1234567"
SESSION_ID = "PAPER-a1b2c3d4"

CONNECTED = ConnectionStatus(connected=True, detail="ib socket connected, client ready")
DROPPED = ConnectionStatus(connected=False, detail="ib socket not connected")


def _settings(**overrides) -> IBKRSettings:
    fields = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_client_id": 1,
        "ibkr_live_client_id": 10,
        "ibkr_trading_mode": "paper",
        "tws_account": PAPER_ACCOUNT,
        "ntrader_real_money_account": "",
    }
    fields.update(overrides)
    return IBKRSettings(_env_file=None, **fields)


class _ExecEngine:
    """Mirrors ``ExecutionEngine``'s two-part connection surface.

    ``registered_clients`` is modelled as well as ``check_connected()`` because
    the real ``check_connected()`` iterates the registered clients and returns
    True on an empty dict — so a node whose ``build()`` never ran reports
    "connected". A double exposing only a bool could not express that state.
    """

    def __init__(self, *, connected: bool, registered: bool) -> None:
        self._connected = connected
        self.registered_clients = ["INTERACTIVE_BROKERS"] if registered else []

    def check_connected(self) -> bool:
        return True if not self.registered_clients else self._connected


class _Kernel:
    def __init__(self, *, connected: bool, registered: bool) -> None:
        self.exec_engine = _ExecEngine(connected=connected, registered=registered)


class _Trader:
    def __init__(self, states: dict[str, str], *, renamed: bool = False) -> None:
        self._states = states
        self._renamed = renamed
        self.start_calls: list[str] = []

    def strategy_states(self) -> dict[str, str]:
        if self._renamed:
            raise AttributeError("strategy_states was renamed in this Nautilus version")
        return dict(self._states)

    def start_strategy(self, strategy_id: str) -> None:
        """Present only so a test can prove the gate never reaches for it."""
        self.start_calls.append(strategy_id)


class _Node:
    """A structural stand-in for ``TradingNode`` — only what Layer 2 touches."""

    def __init__(
        self,
        *,
        connected: bool = True,
        registered: bool = True,
        strategy_states: dict[str, str] | None = None,
        trader_renamed: bool = False,
    ) -> None:
        self.kernel = _Kernel(connected=connected, registered=registered)
        self.trader = _Trader(strategy_states or {}, renamed=trader_renamed)
        self.stopped = False

    async def stop_async(self) -> None:
        self.stopped = True


class _Clock:
    """A monotonic-looking time source the test advances by hand."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _monitor(clock: _Clock, **overrides) -> ConnectionMonitor:
    return ConnectionMonitor(session_id=SESSION_ID, time_source=clock, **overrides)


def _connect(monitor: ConnectionMonitor) -> None:
    """Take a monitor through the two-step path to permitted."""
    monitor.observe(CONNECTED)
    monitor.confirm_state_reestablished(CONNECTED)
    assert monitor.trading_permitted


def _events(records, name: str) -> list[dict]:
    return [record for record in records if record.get("event") == name]


# --- Story 1.4: verify the connected account before trading starts -----------


@criterion("1.4a")
async def test_a_paper_prefixed_reported_account_is_required_to_proceed():
    """ "...the reported account ID must carry a paper prefix (DU/DF) for the
    sequence to continue (FR8, AR14)."

    And an account the gateway never named fails closed: an unverifiable account
    is indistinguishable from a real-money one.
    """
    settings = _settings()

    for account in (PAPER_ACCOUNT, "DF1234567"):
        decision = await verify_connected_account(
            _Node(), settings, reported_accounts=frozenset({account})
        )
        assert decision.permitted
        assert decision.mode is GateMode.PAPER

    with pytest.raises(GateRefusedError) as refused:
        await verify_connected_account(
            _Node(), settings, reported_accounts=frozenset({PAPER_ACCOUNT, REAL_ACCOUNT})
        )
    assert refused.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER

    for nothing_reported in (frozenset(), frozenset({""}), frozenset({"   "})):
        with pytest.raises(GateRefusedError) as unverifiable:
            await verify_connected_account(_Node(), settings, reported_accounts=nothing_reported)
        assert unverifiable.value.refusal.reason is GateRefusalReason.ACCOUNT_NOT_REPORTED


@criterion("1.4b")
async def test_a_non_paper_account_stops_the_node_and_starts_no_strategy():
    """ "...the node shuts down immediately, no strategy is started, and the same
    refusal outcome as the static gate is produced (FR9)."

    "The same refusal outcome" is why the exception class is asserted: Story
    1.7's exit-code mapping covers both layers through one branch, and a
    Layer-2-only exception type would silently turn exit 3 into exit 1.
    """
    node = _Node()

    with pytest.raises(GateRefusedError) as refused:
        await verify_connected_account(
            node, _settings(), reported_accounts=frozenset({REAL_ACCOUNT})
        )

    assert node.stopped, "the node was left up after an unverified account"
    assert node.trader.start_calls == [], "a strategy was started behind the refusal"
    assert refused.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER


@criterion("1.4c")
async def test_verification_logs_the_account_masked():
    """ "...the account identifier appears masked to its last 3 characters
    (NFR26)."
    """
    with capture_logs() as permitted_records:
        await verify_connected_account(
            _Node(), _settings(), reported_accounts=frozenset({PAPER_ACCOUNT})
        )

    with capture_logs() as refused_records:
        with pytest.raises(GateRefusedError):
            await verify_connected_account(
                _Node(), _settings(), reported_accounts=frozenset({REAL_ACCOUNT})
            )

    for records in (permitted_records, refused_records):
        rendered = repr(records)
        assert PAPER_ACCOUNT not in rendered and REAL_ACCOUNT not in rendered, (
            f"a full account identifier reached the log stream: {rendered}"
        )

    assert _events(permitted_records, "gate.account")[-1]["accounts"] == "***626"
    assert "***567" in _events(refused_records, "gate.refused")[0]["message"]


@criterion("1.4d")
async def test_verification_runs_after_connect_and_before_any_strategy():
    """ "...it runs after connection and strictly before any strategy is started
    (AR39)."

    Both halves are enforced, not merely documented: called too early it refuses
    because the gateway has not yet named an account, and called too late it
    refuses because verifying afterwards would bless an account already being
    traded.
    """
    too_early = (
        _Node(connected=False),
        # `check_connected()` is vacuously True with no registered clients, so a
        # node whose build() never ran would otherwise pass this guard.
        _Node(connected=True, registered=False),
    )
    for node in too_early:
        with pytest.raises(GateRefusedError) as refused:
            await verify_connected_account(
                node, _settings(), reported_accounts=frozenset({PAPER_ACCOUNT})
            )
        assert refused.value.refusal.reason is GateRefusalReason.NODE_NOT_CONNECTED

    too_late = _Node(strategy_states={"SMA-001": "RUNNING"})
    with pytest.raises(GateRefusedError) as refused:
        await verify_connected_account(
            too_late, _settings(), reported_accounts=frozenset({PAPER_ACCOUNT})
        )
    assert refused.value.refusal.reason is GateRefusalReason.STRATEGY_STARTED_BEFORE_ACCOUNT_GATE

    # A strategy that exists but has not started yet is the legal state.
    with capture_logs() as records:
        decision = await verify_connected_account(
            _Node(strategy_states={"SMA-001": "READY"}),
            _settings(),
            reported_accounts=frozenset({PAPER_ACCOUNT}),
        )
    assert decision.permitted
    statuses = [record["status"] for record in _events(records, "gate.account")]
    assert statuses == ["started", "ok"]
    assert {record["phase"] for record in _events(records, "gate.account")} == {STARTUP_PHASE}


# --- Story 1.6: detect connection loss and withhold trading permission -------


@criterion("1.6a")
def test_a_drop_logs_connection_lost_and_withdraws_permission():
    """ "...a connection.lost event is logged with the session identifier bound
    (FR6, AR41) and the runner's trading-permitted state becomes false."
    """
    clock = _Clock()
    monitor = _monitor(clock)
    _connect(monitor)

    clock.advance(30)
    with capture_logs() as records:
        state = monitor.observe(DROPPED)
        # A second dead poll is the same outage, not a second one.
        monitor.observe(DROPPED)

    assert state is ConnectionState.LOST
    assert monitor.trading_permitted is False

    lost = _events(records, "connection.lost")
    assert len(lost) == 1, f"connection.lost fired {len(lost)} times for one outage"
    assert lost[0]["session_id"] == SESSION_ID
    assert lost[0]["detail"] == DROPPED.detail


@criterion("1.6b")
def test_permission_returns_only_once_state_is_re_established():
    """ "...connection.restored is logged and trading permission is restored only
    after state is re-established, never on reconnect alone (NFR10)."
    """
    clock = _Clock()
    monitor = _monitor(clock)
    _connect(monitor)
    clock.advance(10)
    monitor.observe(DROPPED)

    # The socket comes back. That alone must not re-permit trading: the adapter
    # finishes its handshake before it resubscribes, so there is a window where
    # the connection is up and nothing has been checked against the broker.
    clock.advance(5)
    with capture_logs() as reconnect_records:
        assert monitor.observe(CONNECTED) is ConnectionState.RECOVERING
    assert monitor.trading_permitted is False
    assert _events(reconnect_records, "connection.restored") == []

    clock.advance(5)
    with capture_logs() as confirm_records:
        assert monitor.confirm_state_reestablished(CONNECTED) is ConnectionState.CONNECTED
    assert monitor.trading_permitted is True
    assert len(_events(confirm_records, "connection.restored")) == 1

    # A confirmation is refused outright when the reading it carries is dead —
    # the grant is atomic with a live observation, never a stale one.
    monitor.observe(DROPPED)
    assert monitor.confirm_state_reestablished(DROPPED) is ConnectionState.LOST
    assert monitor.trading_permitted is False


@criterion("1.6c")
def test_a_reconnect_inside_sixty_seconds_reports_its_elapsed_time():
    """ "...it completes within 60 seconds, and the elapsed time is observable in
    the logs (NFR4)."
    """
    assert DEFAULT_RECONNECT_WINDOW_SECONDS == 60.0

    clock = _Clock()
    monitor = _monitor(clock)
    _connect(monitor)

    clock.advance(10)
    monitor.observe(DROPPED)
    clock.advance(20)  # socket back after 20s
    monitor.observe(CONNECTED)
    clock.advance(5)  # state re-established 5s later
    with capture_logs() as records:
        monitor.confirm_state_reestablished(CONNECTED)

    restored = _events(records, "connection.restored")[0]
    assert restored["reconnect_seconds"] == 20.0
    assert restored["downtime_seconds"] == 25.0
    assert restored["within_reconnect_window"] is True
    assert restored["reconnect_window_seconds"] == 60.0
    assert restored["session_id"] == SESSION_ID


@criterion("1.6d")
def test_the_scheduled_gateway_restart_is_an_expected_event():
    """ "...it is handled as an expected event on the normal reconnect path, not
    raised as an error condition (NFR19)."
    """
    clock = _Clock()
    monitor = _monitor(clock)
    _connect(monitor)

    with capture_logs() as records:
        for _cycle in range(3):
            clock.advance(3_600)
            monitor.observe(DROPPED)  # the daily auto-logoff
            clock.advance(25)
            monitor.observe(CONNECTED)
            monitor.confirm_state_reestablished(CONNECTED)
            assert monitor.trading_permitted, "a routine restart cost the session its permission"

    assert monitor.state is ConnectionState.CONNECTED
    assert _events(records, "connection.halted") == []
    assert [record for record in records if record.get("log_level") == "error"] == []
    # It is narrated, not silent: each cycle is one lost/restored pair.
    assert len(_events(records, "connection.lost")) == 3
    assert len(_events(records, "connection.restored")) == 3


@criterion("1.6e")
def test_an_outage_past_the_window_halts_and_never_runs_on_stale_state():
    """ "...it halts trading and reports it, and never proceeds on stale state
    (NFR20)."
    """
    clock = _Clock()
    monitor = _monitor(clock)
    _connect(monitor)
    monitor.observe(DROPPED)

    with capture_logs() as records:
        clock.advance(DEFAULT_RECONNECT_WINDOW_SECONDS + 1)
        assert monitor.observe(DROPPED) is ConnectionState.HALTED
        clock.advance(30)
        monitor.observe(DROPPED)  # still down; the halt is reported once, not per poll

    assert monitor.trading_permitted is False
    halted = _events(records, "connection.halted")
    assert len(halted) == 1
    assert halted[0]["session_id"] == SESSION_ID
    assert halted[0]["downtime_seconds"] > DEFAULT_RECONNECT_WINDOW_SECONDS

    # Stale state is the other half of "never proceeds": a poll loop that dies
    # must not leave permission granted through a socket nobody is watching.
    fresh_clock = _Clock()
    stalled = _monitor(fresh_clock)
    _connect(stalled)
    fresh_clock.advance(DEFAULT_MAX_OBSERVATION_AGE_SECONDS + 1)
    assert stalled.state is ConnectionState.CONNECTED
    assert stalled.observation_is_stale is True
    assert stalled.trading_permitted is False


async def test_the_account_gate_never_leaves_the_node_up_on_an_unexpected_failure():
    """Not an acceptance criterion — the fail-closed backstop the ACs above rest on.

    Every attribute Layer 2 reaches for belongs to a third party. Without this,
    a Nautilus rename surfaces as an ``AttributeError`` propagating out with the
    node still up, still connected, and about to start strategies — the precise
    outcome Story 1.4 exists to prevent.
    """
    node = _Node(trader_renamed=True)

    with pytest.raises(GateRefusedError) as refused:
        await verify_connected_account(
            node, _settings(), reported_accounts=frozenset({PAPER_ACCOUNT})
        )

    assert refused.value.refusal.reason is GateRefusalReason.ACCOUNT_VERIFICATION_ERROR
    assert node.stopped
