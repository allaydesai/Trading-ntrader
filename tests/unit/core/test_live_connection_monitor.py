"""Unit tests for the broker connection monitor and its trading-permission flag.

The monitor is a pure state machine: it is polled with an already-taken reading
of the broker connection and decides whether trading is permitted. It imports no
Nautilus, opens no socket, and reads no clock of its own — every test here
injects a fake ``time_source`` so downtime assertions are exact rather than
timing-dependent.

The load-bearing property under test is negative, and it has three parts, each
of which was a real hole closed by code review:

1. No sequence of observations of a *disconnected* source may produce
   ``trading_permitted is True``.
2. No *absence* of observations may leave it True either (staleness).
3. No confirmation taken against a stale reading may grant it (the confirmation
   carries its own reading).
"""

import ast
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from src.core.live_connection_monitor import (
    DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    DEFAULT_RECONNECT_WINDOW_SECONDS,
    ConnectionMonitor,
    ConnectionState,
    ConnectionStatus,
)

SESSION_ID = "paper-a1b2c3d4"

UP = ConnectionStatus(connected=True, detail="socket up, client ready")
DOWN = ConnectionStatus(connected=False, detail="socket down")


class FakeClock:
    """A monotonic clock the test advances explicitly.

    Sleeping to cross the reconnect window would make every halt test both slow
    and flaky; advancing a counter makes the downtime assertions exact.
    """

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _monitor(
    clock: FakeClock,
    *,
    window: float = 60.0,
    max_age: float = DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
) -> ConnectionMonitor:
    return ConnectionMonitor(
        session_id=SESSION_ID,
        reconnect_window_seconds=window,
        max_observation_age_seconds=max_age,
        time_source=clock,
    )


def _connected(clock: FakeClock, *, window: float = 60.0) -> ConnectionMonitor:
    """A monitor that has connected and had its state re-established."""
    monitor = _monitor(clock, window=window)
    monitor.observe(UP)
    monitor.confirm_state_reestablished(UP)
    assert monitor.state is ConnectionState.CONNECTED
    return monitor


def _events(captured: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    return [entry for entry in captured if entry["event"] == name]


def _drive_to(state: ConnectionState, clock: FakeClock) -> ConnectionMonitor:
    """Return a monitor genuinely driven into ``state`` through its public API.

    Nothing here pokes private attributes: a state the public API cannot reach
    is a state that cannot occur in production, and the coverage meta-test below
    proves this helper reaches every member of the enum.
    """
    if state is ConnectionState.AWAITING_CONNECTION:
        return _monitor(clock)
    if state is ConnectionState.CONNECTED:
        return _connected(clock)
    if state is ConnectionState.RECOVERING:
        monitor = _monitor(clock)
        monitor.observe(UP)
        return monitor
    if state is ConnectionState.LOST:
        monitor = _connected(clock)
        monitor.observe(DOWN)
        return monitor
    if state is ConnectionState.HALTED:
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(DEFAULT_RECONNECT_WINDOW_SECONDS + 1)
        monitor.observe(DOWN)
        return monitor
    raise AssertionError(f"no driver for {state}")


class TestConstructorValidation:
    """Unusable arguments fail at construction, never in the poll loop."""

    @pytest.mark.unit
    @pytest.mark.parametrize("session_id", ["", "   ", "\n"])
    def test_empty_session_id_is_rejected(self, session_id):
        """An empty id silently defeats the correlation FR6/AR41 want it for."""
        with pytest.raises(ValueError, match="session_id"):
            ConnectionMonitor(session_id=session_id)

    @pytest.mark.unit
    @pytest.mark.parametrize("window", [0.0, -5.0, float("nan"), float("inf")])
    def test_unusable_reconnect_window_is_rejected(self, window):
        """``nan`` is the dangerous one: every ``>`` against it is False.

        A ``nan`` window would silently disable the halt forever rather than
        failing — the same class of input ``_validate_timeouts`` already rejects
        in ``live_node_builder.py``.
        """
        with pytest.raises(ValueError, match="reconnect_window_seconds"):
            ConnectionMonitor(session_id=SESSION_ID, reconnect_window_seconds=window)

    @pytest.mark.unit
    @pytest.mark.parametrize("max_age", [0.0, -1.0, float("nan"), float("inf")])
    def test_unusable_observation_age_is_rejected(self, max_age):
        with pytest.raises(ValueError, match="max_observation_age_seconds"):
            ConnectionMonitor(session_id=SESSION_ID, max_observation_age_seconds=max_age)

    @pytest.mark.unit
    def test_defaults_are_the_nfr_targets(self):
        assert DEFAULT_RECONNECT_WINDOW_SECONDS == 60.0
        assert DEFAULT_MAX_OBSERVATION_AGE_SECONDS == DEFAULT_RECONNECT_WINDOW_SECONDS


class TestInitialState:
    """A monitor starts unpermitted, and startup is not a lost connection."""

    @pytest.mark.unit
    def test_new_monitor_is_awaiting_connection_and_not_permitted(self):
        monitor = _monitor(FakeClock())

        assert monitor.state is ConnectionState.AWAITING_CONNECTION
        assert monitor.trading_permitted is False
        assert monitor.downtime_seconds is None
        assert monitor.observation_age_seconds is None

    @pytest.mark.unit
    def test_disconnected_before_ever_connecting_is_not_a_loss(self):
        """Nothing was lost, so nothing is logged and no downtime clock starts.

        "The gate passed but the broker was never reachable" is Story 1.7's
        exit-code-4 concern; conflating it with a dropped connection would make
        every failed startup emit a spurious ``connection.lost``.
        """
        monitor = _monitor(FakeClock())

        with capture_logs() as captured:
            state = monitor.observe(DOWN)

        assert state is ConnectionState.AWAITING_CONNECTION
        assert monitor.trading_permitted is False
        assert monitor.downtime_seconds is None
        assert _events(captured, "connection.lost") == []

    @pytest.mark.unit
    def test_a_half_up_first_connection_that_drops_is_still_not_a_loss(self):
        """The ``AWAITING_CONNECTION`` guard alone missed this path.

        A first poll catching a half-up socket moves to ``RECOVERING``; the
        second poll then fell straight into the loss branch, emitting
        ``connection.lost`` and — 60s later — ``connection.halted`` for a broker
        that was never once reached.
        """
        clock = FakeClock()
        monitor = _monitor(clock)
        monitor.observe(UP)

        with capture_logs() as captured:
            state = monitor.observe(DOWN)
            clock.advance(120.0)
            monitor.observe(DOWN)

        assert state is ConnectionState.AWAITING_CONNECTION
        assert monitor.downtime_seconds is None
        assert _events(captured, "connection.lost") == []
        assert _events(captured, "connection.halted") == []

    @pytest.mark.unit
    def test_first_connection_logs_no_restoration(self):
        """Restoring implies a prior loss; the first connect restores nothing."""
        monitor = _monitor(FakeClock())

        with capture_logs() as captured:
            monitor.observe(UP)
            monitor.confirm_state_reestablished(UP)

        assert monitor.state is ConnectionState.CONNECTED
        assert monitor.trading_permitted is True
        assert _events(captured, "connection.restored") == []


class TestPermissionInvariant:
    """``trading_permitted`` is true in exactly one state, and is derived."""

    @pytest.mark.unit
    @pytest.mark.parametrize("state", list(ConnectionState))
    def test_permission_is_granted_only_while_connected(self, state):
        monitor = _drive_to(state, FakeClock())

        assert monitor.state is state
        assert monitor.trading_permitted is (state is ConnectionState.CONNECTED)

    @pytest.mark.unit
    def test_driver_covers_every_state(self):
        """Meta-test: the parametrised test above is only exhaustive if this holds.

        A state added to the enum without a driver would otherwise silently skip
        the permission assertion for the one state that mattered.
        """
        reached = {_drive_to(state, FakeClock()).state for state in ConnectionState}

        assert reached == set(ConnectionState)

    @pytest.mark.unit
    def test_permission_has_no_setter(self):
        """Derived, never stored — an assignment must not be able to grant it."""
        monitor = _monitor(FakeClock())

        with pytest.raises(AttributeError):
            monitor.trading_permitted = True  # type: ignore[misc]


class TestPermissionExpiresWhenObservationsStop:
    """A poll loop that dies must fail closed, not leave permission granted."""

    @pytest.mark.unit
    def test_permission_lapses_once_the_last_reading_is_too_old(self):
        """The one failure shape no *observation* can express: silence.

        Before this, a runner whose poll task died (unhandled exception,
        cancellation, loop starvation) left the monitor ``CONNECTED`` and
        reporting ``trading_permitted is True`` indefinitely through a dead
        socket.
        """
        clock = FakeClock()
        monitor = _connected(clock)
        assert monitor.trading_permitted is True

        clock.advance(DEFAULT_MAX_OBSERVATION_AGE_SECONDS + 0.1)

        assert monitor.trading_permitted is False
        assert monitor.observation_is_stale is True
        assert monitor.state is ConnectionState.CONNECTED

    @pytest.mark.unit
    def test_permission_returns_when_polling_resumes(self):
        """Staleness suspends permission; it does not destroy the session."""
        clock = FakeClock()
        monitor = _connected(clock)
        clock.advance(DEFAULT_MAX_OBSERVATION_AGE_SECONDS + 0.1)
        assert monitor.trading_permitted is False

        monitor.observe(UP)

        assert monitor.trading_permitted is True
        assert monitor.observation_is_stale is False

    @pytest.mark.unit
    def test_the_staleness_boundary_is_inclusive(self):
        clock = FakeClock()
        monitor = _connected(clock, window=60.0)

        clock.advance(DEFAULT_MAX_OBSERVATION_AGE_SECONDS)
        assert monitor.trading_permitted is True

        clock.advance(0.1)
        assert monitor.trading_permitted is False

    @pytest.mark.unit
    def test_observation_age_tracks_the_clock(self):
        clock = FakeClock()
        monitor = _connected(clock)

        clock.advance(7.5)

        assert monitor.observation_age_seconds == pytest.approx(7.5)


class TestConnectionLost:
    """AC #1 — a drop withdraws permission and says so once."""

    @pytest.mark.unit
    def test_drop_withdraws_permission_and_logs_with_session_bound(self):
        clock = FakeClock()
        monitor = _connected(clock)

        with capture_logs() as captured:
            state = monitor.observe(DOWN)

        assert state is ConnectionState.LOST
        assert monitor.trading_permitted is False

        lost = _events(captured, "connection.lost")
        assert len(lost) == 1
        assert lost[0]["session_id"] == SESSION_ID
        assert lost[0]["detail"] == DOWN.detail

    @pytest.mark.unit
    def test_repeated_disconnected_polls_log_once(self):
        """The monitor is polled on an interval; per-poll logging would bury the stream."""
        clock = FakeClock()
        monitor = _connected(clock)

        with capture_logs() as captured:
            for _ in range(10):
                monitor.observe(DOWN)

        assert len(_events(captured, "connection.lost")) == 1
        assert monitor.state is ConnectionState.LOST

    @pytest.mark.unit
    def test_loss_from_recovering_does_not_re_log_the_same_outage(self):
        """One outage, one ``connection.lost`` — the anchor is what makes it so."""
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        monitor.observe(UP)
        assert monitor.state is ConnectionState.RECOVERING

        with capture_logs() as captured:
            state = monitor.observe(DOWN)

        assert state is ConnectionState.LOST
        assert _events(captured, "connection.lost") == []


class TestRecovery:
    """AC #2 — permission returns only after state is re-established."""

    @pytest.mark.unit
    def test_reconnect_alone_does_not_restore_permission(self):
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)

        with capture_logs() as captured:
            state = monitor.observe(UP)

        assert state is ConnectionState.RECOVERING
        assert monitor.trading_permitted is False
        assert _events(captured, "connection.restored") == []

    @pytest.mark.unit
    def test_confirmation_restores_permission_and_logs_once(self):
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        monitor.observe(UP)

        with capture_logs() as captured:
            state = monitor.confirm_state_reestablished(UP)

        assert state is ConnectionState.CONNECTED
        assert monitor.trading_permitted is True

        restored = _events(captured, "connection.restored")
        assert len(restored) == 1
        assert restored[0]["session_id"] == SESSION_ID

    @pytest.mark.unit
    def test_confirmation_carries_its_own_reading_and_refuses_a_dead_socket(self):
        """The socket can die between the caller's last poll and its confirmation.

        Before the signature took a status, that window bought a whole poll
        interval of blind trading: the confirmation inspected only the cached
        state and granted permission on a socket that was already gone.
        """
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        monitor.observe(UP)
        assert monitor.state is ConnectionState.RECOVERING

        with capture_logs() as captured:
            state = monitor.confirm_state_reestablished(DOWN)

        assert state is ConnectionState.LOST
        assert monitor.trading_permitted is False
        assert len(_events(captured, "connection.recovery_refused")) == 1

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "state",
        [ConnectionState.LOST, ConnectionState.HALTED, ConnectionState.AWAITING_CONNECTION],
    )
    def test_confirmation_is_refused_while_the_source_is_not_connected(self, state):
        """Permission is never granted from an observation that said 'disconnected'."""
        clock = FakeClock()
        monitor = _drive_to(state, clock)

        with capture_logs() as captured:
            result = monitor.confirm_state_reestablished(DOWN)

        assert result is monitor.state
        assert monitor.trading_permitted is False

        refused = _events(captured, "connection.recovery_refused")
        assert len(refused) == 1
        assert refused[0]["log_level"] == "warning"
        assert refused[0]["session_id"] == SESSION_ID

    @pytest.mark.unit
    def test_confirmation_while_already_connected_is_idempotent(self):
        clock = FakeClock()
        monitor = _connected(clock)

        with capture_logs() as captured:
            state = monitor.confirm_state_reestablished(UP)

        assert state is ConnectionState.CONNECTED
        assert monitor.trading_permitted is True
        assert _events(captured, "connection.restored") == []
        assert _events(captured, "connection.recovery_refused") == []


class TestReconnectTiming:
    """AC #3 — the elapsed downtime is observable in the logs."""

    @pytest.mark.unit
    def test_reconnect_and_downtime_are_reported_separately(self):
        """NFR4 measures the *reconnect*, not the reconciliation that follows.

        Reporting one number for both would make every ``connection.restored``
        indict the broker connection for however long re-establishing state
        took — and NFR5 allows reconciliation 30 seconds.
        """
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(12.5)
        monitor.observe(UP)
        clock.advance(4.0)

        with capture_logs() as captured:
            monitor.confirm_state_reestablished(UP)

        restored = _events(captured, "connection.restored")[0]
        assert restored["reconnect_seconds"] == pytest.approx(12.5)
        assert restored["downtime_seconds"] == pytest.approx(16.5)
        assert restored["within_reconnect_window"] is True
        assert restored["reconnect_window_seconds"] == 60.0
        assert restored["log_level"] == "info"

    @pytest.mark.unit
    def test_slow_reconciliation_does_not_indict_the_reconnect(self):
        """Socket back in 2s, state re-established 40s later: the reconnect was fast.

        Reporting one number for both would label a 42-second recovery as a
        42-second *reconnect* and fail NFR4 on a connection that met it in two.
        """
        clock = FakeClock()
        monitor = _connected(clock, window=60.0)
        monitor.observe(DOWN)
        clock.advance(2.0)
        monitor.observe(UP)
        clock.advance(40.0)

        with capture_logs() as captured:
            monitor.confirm_state_reestablished(UP)

        restored = _events(captured, "connection.restored")[0]
        assert restored["reconnect_seconds"] == pytest.approx(2.0)
        assert restored["downtime_seconds"] == pytest.approx(42.0)
        assert restored["within_reconnect_window"] is True

    @pytest.mark.unit
    def test_unavailability_past_the_window_halts_even_after_a_fast_reconnect(self):
        """The deliberate consequence of anchoring the halt on unavailability.

        A 2-second reconnect followed by reconciliation that never finishes is
        still a session that has been unable to trade for longer than the
        window, and NFR20 says such a session halts and reports rather than
        waiting quietly. The halt is therefore measured against *unavailability*
        (loss → permission restored), while ``within_reconnect_window`` on
        ``connection.restored`` reports NFR4's narrower quantity. The two
        deliberately answer different questions; this test pins that apart.
        """
        clock = FakeClock()
        monitor = _connected(clock, window=60.0)
        monitor.observe(DOWN)
        clock.advance(2.0)
        monitor.observe(UP)
        clock.advance(70.0)

        with capture_logs() as captured:
            state = monitor.confirm_state_reestablished(UP)

        assert state is ConnectionState.HALTED
        assert monitor.trading_permitted is False

        halted = _events(captured, "connection.halted")[0]
        assert halted["downtime_seconds"] == pytest.approx(72.0)
        assert halted["reconnect_seconds"] == pytest.approx(2.0)

    @pytest.mark.unit
    def test_a_slow_reconnect_is_reported_not_suppressed(self):
        """Past the window the event still fires — flagged, so it is visible."""
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(75.0)
        monitor.observe(UP)

        with capture_logs() as captured:
            monitor.confirm_state_reestablished(UP)

        restored = _events(captured, "connection.restored")[0]
        assert restored["reconnect_seconds"] == pytest.approx(75.0)
        assert restored["within_reconnect_window"] is False

    @pytest.mark.unit
    def test_downtime_is_readable_while_still_disconnected(self):
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(20.0)

        assert monitor.downtime_seconds == pytest.approx(20.0)
        assert monitor.reconnect_seconds is None

    @pytest.mark.unit
    def test_downtime_keeps_running_through_recovering(self):
        """Documented behaviour: unavailability, not disconnection."""
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(5.0)
        monitor.observe(UP)
        clock.advance(6.0)

        assert monitor.state is ConnectionState.RECOVERING
        assert monitor.downtime_seconds == pytest.approx(11.0)
        assert monitor.reconnect_seconds == pytest.approx(5.0)

    @pytest.mark.unit
    def test_downtime_clears_once_restored(self):
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(5.0)
        monitor.observe(UP)
        monitor.confirm_state_reestablished(UP)

        assert monitor.downtime_seconds is None
        assert monitor.reconnect_seconds is None

    @pytest.mark.unit
    def test_a_backward_clock_step_cannot_defeat_the_halt(self):
        """``time_source`` is injected and cannot be assumed monotonic.

        An NTP step or a host suspend can hand back a smaller number. A backward
        step may shorten a report; it must never make an overrun outage look
        like it is inside its window, nor a stale reading look fresh.
        """
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(-3600.0)

        assert monitor.downtime_seconds == pytest.approx(0.0)
        assert monitor.observation_age_seconds == pytest.approx(0.0)

        clock.advance(3600.0 + 61.0)
        assert monitor.observe(DOWN) is ConnectionState.HALTED


class TestScheduledGatewayRestart:
    """AC #4 — the daily restart is an expected event, not an error."""

    @pytest.mark.unit
    def test_a_full_cycle_raises_nothing_and_logs_at_expected_levels(self):
        clock = FakeClock()
        monitor = _connected(clock)

        with capture_logs() as captured:
            monitor.observe(DOWN)
            clock.advance(30.0)
            monitor.observe(UP)
            monitor.confirm_state_reestablished(UP)

        assert _events(captured, "connection.lost")[0]["log_level"] == "warning"
        assert _events(captured, "connection.restored")[0]["log_level"] == "info"
        assert _events(captured, "connection.halted") == []

    @pytest.mark.unit
    def test_repeated_restart_cycles_return_the_monitor_to_the_same_state(self):
        """Three cycles, because a one-shot recovery would pass a single-cycle test."""
        clock = FakeClock()
        monitor = _connected(clock)

        for _ in range(3):
            monitor.observe(DOWN)
            clock.advance(15.0)
            monitor.observe(UP)
            monitor.confirm_state_reestablished(UP)

            assert monitor.state is ConnectionState.CONNECTED
            assert monitor.trading_permitted is True
            assert monitor.downtime_seconds is None


class TestHalt:
    """AC #5 — an outage past the window halts trading and reports it."""

    @pytest.mark.unit
    def test_outage_past_the_window_halts_and_reports_once(self):
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(60.1)

        with capture_logs() as captured:
            state = monitor.observe(DOWN)
            monitor.observe(DOWN)
            monitor.observe(DOWN)

        assert state is ConnectionState.HALTED
        assert monitor.state is ConnectionState.HALTED
        assert monitor.trading_permitted is False

        halted = _events(captured, "connection.halted")
        assert len(halted) == 1
        assert halted[0]["log_level"] == "error"
        assert halted[0]["session_id"] == SESSION_ID
        assert halted[0]["downtime_seconds"] == pytest.approx(60.1)

    @pytest.mark.unit
    def test_the_window_boundary_does_not_halt(self):
        """Exactly at the window is still inside it; NFR4 says reconnect < 60s."""
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(60.0)

        with capture_logs() as captured:
            state = monitor.observe(DOWN)

        assert state is ConnectionState.LOST
        assert _events(captured, "connection.halted") == []

    @pytest.mark.unit
    def test_a_flapping_connection_cannot_reset_the_halt_clock(self):
        """The defect this anchor exists to close.

        Resetting the clock on every entry to ``LOST`` meant a gateway that came
        up for a single poll every 59 seconds restarted the halt window on every
        cycle — so ``connection.halted`` never fired no matter how many hours
        the link was unusable. Exactly the failure mode NFR20 is written for.
        """
        clock = FakeClock()
        monitor = _connected(clock)

        with capture_logs() as captured:
            for _ in range(10):
                monitor.observe(DOWN)
                clock.advance(50.0)
                monitor.observe(UP)
                clock.advance(1.0)

        assert monitor.trading_permitted is False
        halted = _events(captured, "connection.halted")
        assert len(halted) == 1, "a flapping link must accumulate one continuous outage"
        assert len(_events(captured, "connection.lost")) == 1

    @pytest.mark.unit
    def test_an_unconfirmed_recovery_still_halts(self):
        """A socket that came back but was never confirmed is still an outage.

        With the deadline evaluated only in the ``LOST`` branch, this sat in
        ``RECOVERING`` forever: no halt, no further log line, and
        ``downtime_seconds`` growing without bound.
        """
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        clock.advance(5.0)
        monitor.observe(UP)
        assert monitor.state is ConnectionState.RECOVERING

        clock.advance(120.0)

        with capture_logs() as captured:
            state = monitor.observe(UP)

        assert state is ConnectionState.HALTED
        assert monitor.trading_permitted is False
        assert len(_events(captured, "connection.halted")) == 1

    @pytest.mark.unit
    def test_a_halt_is_escapable_only_through_the_same_two_step_path(self):
        clock = FakeClock()
        monitor = _drive_to(ConnectionState.HALTED, clock)

        assert monitor.observe(UP) is ConnectionState.RECOVERING
        assert monitor.trading_permitted is False

        assert monitor.confirm_state_reestablished(UP) is ConnectionState.CONNECTED
        assert monitor.trading_permitted is True

    @pytest.mark.unit
    def test_a_second_outage_after_a_halt_halts_again(self):
        """The halt latch resets on a successful confirmation, not before."""
        clock = FakeClock()
        monitor = _drive_to(ConnectionState.HALTED, clock)
        monitor.observe(UP)
        monitor.confirm_state_reestablished(UP)

        monitor.observe(DOWN)
        clock.advance(61.0)

        with capture_logs() as captured:
            state = monitor.observe(DOWN)

        assert state is ConnectionState.HALTED
        assert len(_events(captured, "connection.halted")) == 1

    @pytest.mark.unit
    def test_no_disconnected_observation_ever_permits_trading(self):
        """The story's negative invariant, exercised over a long hostile sequence."""
        clock = FakeClock()
        monitor = _connected(clock)

        for step in range(40):
            monitor.observe(DOWN)
            clock.advance(7.0)
            assert monitor.trading_permitted is False, f"permitted at step {step}"
            monitor.confirm_state_reestablished(DOWN)
            assert monitor.trading_permitted is False, f"permitted after confirm at step {step}"

    @pytest.mark.unit
    def test_a_confirmation_cannot_outrun_the_halt_deadline(self):
        """A confirmation arriving after the window halts rather than granting."""
        clock = FakeClock()
        monitor = _connected(clock)
        monitor.observe(DOWN)
        monitor.observe(UP)
        clock.advance(61.0)

        state = monitor.confirm_state_reestablished(UP)

        assert state is ConnectionState.HALTED
        assert monitor.trading_permitted is False


class TestModulePurity:
    """The monitor stays framework-free — that is what keeps it unit-tier."""

    @pytest.mark.unit
    def test_module_imports_no_framework_or_io_library(self):
        from src.core import live_connection_monitor

        source = Path(live_connection_monitor.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = ("nautilus_trader", "sqlalchemy", "src.db", "src.services", "src.api")
        offenders = [name for name in imported if name.startswith(forbidden)]

        assert offenders == [], f"monitor must stay framework-free, found: {offenders}"
