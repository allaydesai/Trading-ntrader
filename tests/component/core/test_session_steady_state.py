"""Component tests for the session's steady-state tick (Story 2.5, AC #5/#7).

Component tier: ``live_session_steady_state`` imports the IB adapter
transitively through ``live_connection_probe``. Nothing here constructs a node.

Every wait is driven by an **injected sleeper**, and every timestamp by an
**injected clock** — there is no ``freezegun``, no ``time-machine`` and no
``pytest-timeout`` in this repo, and the house idiom is a callable seam. A test
sleeper that advances the clock and returns immediately makes 780 ticks cost
milliseconds instead of 6.5 hours.
"""

import asyncio
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.config import IBKRSettings
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionStatus
from src.core.live_session_record import SessionReclaimedError
from src.core.live_session_steady_state import (
    DEFAULT_NO_BARS_AFTER_SECONDS,
    NO_BARS_EVENT,
    SessionSteadyState,
    StartupHeartbeat,
    join_heartbeat,
    release_record,
)

pytestmark = pytest.mark.component

STARTED_AT = datetime(2026, 8, 19, 14, 30, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SpyRecord:
    def __init__(self) -> None:
        self.activity: list[tuple[datetime, datetime | None]] = []
        self.raises: BaseException | None = None
        self.stopped = 0
        #: Story 2.7's third port method. `(spec_strategy_id, all_failed)` per
        #: call, plus the thread each ran on — AC #10 requires the write to
        #: leave the event-loop thread, and a name is how that is asserted.
        self.failures: list[tuple[str, bool]] = []
        self.failure_threads: list[str] = []
        self.failure_raises: BaseException | None = None

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.activity.append((at, bar_seen_at))
        if self.raises is not None:
            raise self.raises

    def mark_stopped(self) -> None:
        self.stopped += 1

    def record_strategy_failure(
        self,
        *,
        strategy_id: str,
        spec_strategy_id: str,
        error_type: str,
        handler: str,
        at: datetime,
        detail: str | None = None,
        all_failed: bool = False,
    ) -> None:
        self.failures.append((spec_strategy_id, all_failed))
        self.failure_threads.append(threading.current_thread().name)
        if self.failure_raises is not None:
            raise self.failure_raises


class CapturingLog:
    """A bound-logger stand-in that keeps every record, level included."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def _record(self, level):
        def _emit(event, **fields):
            self.records.append((level, event, fields))

        return _emit

    def __getattr__(self, level):
        return self._record(level)

    def events(self, name: str) -> list[dict]:
        return [fields for _, event, fields in self.records if event == name]

    def level_of(self, name: str) -> str | None:
        for level, event, _ in self.records:
            if event == name:
                return level
        return None


def _settings() -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode="paper",
        tws_account="DU4076626",
    )


class _StopTicking(Exception):
    """Ends the loop at a clean boundary, from inside the sleeper.

    Cancelling the task instead is racy: a tick is only half done at the point
    ``ticks`` increments, so a cancel timed off that counter tears the last
    tick's write out from under it and the test measures one fewer than it
    asked for. Raising from the *sleeper* means every tick that started has
    finished before the loop ends.
    """


def _steady_state(clock: FakeClock, record: SpyRecord, log: CapturingLog, **overrides):
    """Build a steady state whose sleeper advances the clock and returns."""
    ticks: list[float] = []
    budget = overrides.pop("max_ticks", None)

    async def _sleeper(seconds: float) -> None:
        if budget is not None and len(ticks) >= budget:
            raise _StopTicking
        ticks.append(seconds)
        clock.advance(seconds)

    options = {
        "record": record,
        "settings": _settings(),
        "monitor": ConnectionMonitor(session_id="a-session"),
        "log": log,
        "time_source": clock,
        "sleeper": _sleeper,
        "connection_reader": lambda settings: ConnectionStatus(True, "ok"),
    }
    options.update(overrides)
    state = SessionSteadyState(**options)
    state.sleeps = ticks  # type: ignore[attr-defined]
    return state


async def _run_ticks(state: SessionSteadyState, count: int) -> None:
    """Run the loop for exactly ``count`` complete ticks."""
    state._sleeper = _budgeted(state, count)  # type: ignore[attr-defined]
    try:
        await state.run()
    except _StopTicking:
        pass


def _budgeted(state: SessionSteadyState, count: int):
    """Wrap the state's own sleeper so it ends the loop after ``count`` sleeps."""
    inner = state._sleeper  # type: ignore[attr-defined]
    seen = {"n": 0}

    async def _sleeper(seconds: float) -> None:
        if seen["n"] >= count:
            raise _StopTicking
        seen["n"] += 1
        await inner(seconds)

    return _sleeper


#: Attributes this *test file* hangs on the object under test, which are not
#: part of its state and must not be mistaken for a backlog.
_TEST_OWNED_ATTRIBUTES = frozenset({"sleeps"})


def _growing(state) -> dict[str, int]:
    """Any container the object holds that has grown past a handful of items."""
    return {
        name: len(value)
        for name, value in vars(state).items()
        if name not in _TEST_OWNED_ATTRIBUTES
        and isinstance(value, (list, dict, set, tuple))
        and len(value) > 8
    }


class TestTheCadence:
    """AC #5: ``last_heartbeat_at`` roughly every 30 seconds."""

    async def test_the_interval_defaults_to_thirty_seconds(self):
        """The literal is asserted **here**, deliberately.

        Asserting that the default equals the constant it is read from cannot
        fail. This is the one place the number 30 is written down on the test
        side, and the runner's own source contains no literal ``30`` at all.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 3)

        assert state.sleeps == [30.0, 30.0, 30.0]

    async def test_one_write_per_interval_and_no_more(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 4)

        assert len(record.activity) == 4
        assert [at for at, _ in record.activity] == [
            STARTED_AT + timedelta(seconds=30 * n) for n in range(1, 5)
        ]

    async def test_the_first_write_happens_after_one_interval_not_immediately(self):
        """``transition(to=RUNNING)`` already stamped ``last_heartbeat_at``."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 1)

        assert record.activity[0][0] == STARTED_AT + timedelta(seconds=30)

    async def test_a_shorter_injected_interval_is_honoured(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, interval_seconds=5.0)

        await _run_ticks(state, 3)

        assert state.sleeps == [5.0, 5.0, 5.0]


class TestTheBarPath:
    """AC #5's ``last_bar_at``, observed per bar and persisted on the tick."""

    async def test_a_bar_seen_since_the_last_tick_is_carried_on_the_next_write(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)
        state.note_bar(object())
        observed = clock.now

        await _run_ticks(state, 1)

        assert record.activity[0][1] == observed

    async def test_no_bar_means_bar_seen_at_is_none(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 2)

        assert [seen for _, seen in record.activity] == [None, None]

    async def test_the_bar_instant_is_consumed_not_repeated(self):
        """Otherwise a single bar would keep refreshing ``last_bar_at`` forever."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)
        state.note_bar(object())

        await _run_ticks(state, 2)

        assert record.activity[0][1] is not None
        assert record.activity[1][1] is None

    def test_note_bar_records_the_time_the_bar_was_seen(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)
        clock.advance(17)

        state.note_bar(object())

        assert state.take_bar_seen() == STARTED_AT + timedelta(seconds=17)

    def test_note_bar_keeps_only_the_latest_instant_no_matter_how_many_bars(self):
        """NFR2: the per-bar path must be O(1) in space as well as time."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        for _ in range(500):
            clock.advance(1)
            state.note_bar(object())

        assert state.bars_seen == 500
        assert state.take_bar_seen() == STARTED_AT + timedelta(seconds=500)
        assert state.take_bar_seen() is None

    def test_note_bar_makes_no_database_call(self):
        """A DB round trip per bar, on the event-loop thread, is precisely the
        accumulating backlog NFR2 forbids (*Judgment call #3*).
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        for _ in range(100):
            state.note_bar(object())

        assert record.activity == []


class TestAr42SurvivalAndItsOneException:
    """A DB hiccup never kills a trading session — except loss of ownership."""

    async def test_a_raising_write_is_logged_and_the_loop_survives(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        record.raises = RuntimeError("connection pool exhausted")
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 3)

        assert state.ticks >= 3
        failures = log.events("session.heartbeat_write_failed")
        assert len(failures) >= 3
        assert log.level_of("session.heartbeat_write_failed") == "error"

    async def test_the_failure_record_carries_only_the_error_type(self):
        """NFR26 — never ``str(exc)`` for anything a third party wrote."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        record.raises = RuntimeError("account DU4076626 is not managed")
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 1)

        failure = log.events("session.heartbeat_write_failed")[0]
        assert failure["error_type"] == "RuntimeError"
        assert "DU4076626" not in repr(failure)

    async def test_a_reclaim_refusal_ends_the_loop_and_propagates(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        record.raises = SessionReclaimedError("another process owns this session")
        state = _steady_state(clock, record, log)

        with pytest.raises(SessionReclaimedError):
            await state.run()

        assert log.events("session.heartbeat_write_failed") == []

    async def test_a_failing_connection_read_is_also_survivable(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()

        def _hostile(settings):
            raise AttributeError("_is_ib_connected moved")

        state = _steady_state(clock, record, log, connection_reader=_hostile)

        await _run_ticks(state, 2)

        assert len(record.activity) == 2
        assert log.events("session.connection_read_failed")[0]["error_type"] == "AttributeError"


class TestTheConnectionMonitorIsFed:
    """Closes ``deferred-work.md:550-555`` — nothing polled the monitor."""

    async def test_every_tick_observes_the_connection(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        reads: list[object] = []

        def _reader(settings):
            reads.append(settings)
            return ConnectionStatus(True, "ib socket connected, client ready")

        monitor = ConnectionMonitor(session_id="a-session")
        state = _steady_state(clock, record, log, monitor=monitor, connection_reader=_reader)

        await _run_ticks(state, 3)

        assert len(reads) == 3

    async def test_a_lost_socket_moves_the_monitor(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        monitor = ConnectionMonitor(session_id="a-session")
        state = _steady_state(
            clock,
            record,
            log,
            monitor=monitor,
            connection_reader=lambda s: ConnectionStatus(False, "ib socket not connected"),
        )

        await _run_ticks(state, 2)

        assert monitor.trading_permitted is False

    def test_confirm_state_reestablished_is_never_called_in_this_story(self):
        """*Judgment call #6*: ``reconcile`` is a no-op placeholder, so there is
        no genuine reconciliation for it to follow. ``deferred-work.md:542-548``
        warns verbatim that calling it after merely observing a live socket
        *"would satisfy the type signature while defeating the design"*.
        """
        import ast
        from pathlib import Path

        from src.core import live_session_runner, live_session_steady_state

        for module in (live_session_steady_state, live_session_runner):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            assert "confirm_state_reestablished" not in called, module.__name__


class TestTheFirstBarWatchdog:
    """Epic 1 retro Action Item #9 — visibility only, and exactly once."""

    async def test_a_session_with_no_bars_warns_once_after_the_window(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, no_bars_after_seconds=60.0)

        await _run_ticks(state, 6)

        warnings = log.events(NO_BARS_EVENT)
        assert len(warnings) == 1
        assert log.level_of(NO_BARS_EVENT) == "warning"

    async def test_it_does_not_fire_before_the_window_elapses(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, no_bars_after_seconds=300.0)

        await _run_ticks(state, 3)

        assert log.events(NO_BARS_EVENT) == []

    async def test_a_session_that_has_seen_a_bar_never_warns(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, no_bars_after_seconds=30.0)
        state.note_bar(object())

        await _run_ticks(state, 6)

        assert log.events(NO_BARS_EVENT) == []

    async def test_the_warning_changes_no_state_and_stops_nothing(self):
        """It is *visibility*, not a control. The broker-authoritative fix is
        Epic 4's; a watchdog that stopped a session on a quiet market would be
        strictly worse than the blind spot it replaces.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, no_bars_after_seconds=30.0)

        await _run_ticks(state, 6)

        assert record.stopped == 0
        assert len(record.activity) == 6

    def test_the_default_window_is_a_stated_number(self):
        assert DEFAULT_NO_BARS_AFTER_SECONDS == 300.0


class TestSixAndAHalfHoursWithoutWaitingForThem:
    """AC #7 — 6.5 hours unattended, with no accumulating backlog (NFR2).

    ⚠️ **These are proxies.** They prove the *shape* is right: O(1) per bar,
    one bounded write per interval, nothing that grows. They cannot prove a
    real RTH day, and the AC is closed by Procedure P6 in
    ``docs/qa/phase3-live-verification.md``, not by this file. That is the Epic
    1 precedent for an acceptance criterion a test cannot fully close.
    """

    RTH_MINUTES = 390  # 09:30–16:00 ET, one 1-minute bar each
    RTH_SECONDS = 6.5 * 3600

    def _healthy_bars(self, count: int):
        """``count`` **healthy** synthesized bars.

        ⚠️ Deliberately not ``_delayed_bar``'s shape (a 900s lag): that exists
        to trip the delayed-feed guard, which would shut the node down mid-test
        and prove the opposite of what this asserts. ``ts_event`` is the bar's
        *open* and ``ts_init`` is one minute later — its close.
        """
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.model.objects import Price, Quantity

        minute_ns = 60_000_000_000
        base = 1_755_000_000_000_000_000
        for index in range(count):
            open_ns = base + index * minute_ns
            yield Bar(
                bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"),
                open=Price.from_str("100.00"),
                high=Price.from_str("101.00"),
                low=Price.from_str("99.00"),
                close=Price.from_str("100.50"),
                volume=Quantity.from_int(1_000),
                ts_event=open_ns,
                ts_init=open_ns + minute_ns,
            )

    def test_a_full_rth_day_of_bars_makes_no_database_call_at_all(self):
        """The persist half happens on the tick, never on the bar."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        for bar in self._healthy_bars(self.RTH_MINUTES):
            clock.advance(60)
            state.note_bar(bar)

        assert state.bars_seen == self.RTH_MINUTES
        assert record.activity == []

    def test_the_observed_timestamp_is_overwritten_not_appended(self):
        """The whole of the per-bar state is one instant and one counter, so
        the runner's memory is flat no matter how long the session runs.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        for bar in self._healthy_bars(self.RTH_MINUTES):
            clock.advance(60)
            state.note_bar(bar)

        # Exactly one instant survives — the last one.
        assert state.take_bar_seen() == STARTED_AT + timedelta(minutes=self.RTH_MINUTES)
        assert state.take_bar_seen() is None

    def test_no_attribute_on_the_steady_state_grows_with_the_bar_count(self):
        """A structural no-backlog assertion: after a full day of bars, every
        container the object holds is still empty or scalar.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        for bar in self._healthy_bars(self.RTH_MINUTES):
            clock.advance(60)
            state.note_bar(bar)

        assert _growing(state) == {}, f"these grew with the bar count: {_growing(state)}"

    async def test_six_and_a_half_hours_produces_exactly_seven_hundred_and_eighty_writes(self):
        """23400s / 30s. The literal is the point: it is what an operator would
        count in the ``trading_sessions`` row after a day.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, int(self.RTH_SECONDS // 30))

        assert len(record.activity) == 780
        assert clock.now == STARTED_AT + timedelta(seconds=self.RTH_SECONDS)

    async def test_doubling_the_interval_halves_the_count(self):
        """Mutation proof, permanent rather than manual: the count above is
        driven by the interval and not by the tick budget.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, interval_seconds=60.0)

        await _run_ticks(state, int(self.RTH_SECONDS // 60))

        assert len(record.activity) == 390
        assert clock.now == STARTED_AT + timedelta(seconds=self.RTH_SECONDS)

    async def test_a_full_day_of_ticks_accumulates_no_state(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 780)

        assert _growing(state) == {}, f"these grew with the tick count: {_growing(state)}"

    async def test_a_full_day_with_bars_writes_the_bar_instant_on_every_tick(self):
        """The realistic shape: bars arriving between ticks, one write each."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        bars = self._healthy_bars(self.RTH_MINUTES)

        async def _tick_and_feed(seconds: float) -> None:
            clock.advance(seconds)
            state.note_bar(next(bars, None))

        state = _steady_state(clock, record, log, sleeper=_tick_and_feed)

        await _run_ticks(state, 300)

        assert len(record.activity) == 300
        assert all(seen is not None for _, seen in record.activity)


class TestStartupHeartbeat:
    """Review fix (2026-08-21): the startup phases outlast the 90s staleness
    threshold, so a writer must keep the row fresh before the steady loop
    exists. Driven with a **real thread and a real (tiny) interval** — here the
    ``Event.wait`` cadence is the thing under test, so the seams are the clock
    and the record, not the wait.
    """

    def _wait_for(self, condition, *, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.005)
        return condition()

    def _writer(self, record, log, *, interval_seconds: float = 0.005, clock=None):
        return StartupHeartbeat(
            record=record,
            log=log,
            time_source=clock if clock is not None else FakeClock(),
            interval_seconds=interval_seconds,
        )

    def test_it_writes_repeatedly_with_the_injected_clocks_instant(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        writer = self._writer(record, log, clock=clock)

        writer.start()
        assert self._wait_for(lambda: len(record.activity) >= 2)
        writer.stop()

        assert all(at == clock.now for at, _ in record.activity)
        assert all(seen is None for _, seen in record.activity)

    def test_an_ordinary_write_failure_is_logged_and_the_writer_survives(self):
        """AR42, same as the steady loop: a DB hiccup must not kill a start."""
        record, log = SpyRecord(), CapturingLog()
        record.raises = RuntimeError("connection pool exhausted")
        writer = self._writer(record, log)

        writer.start()
        assert self._wait_for(lambda: len(record.activity) >= 2)
        assert writer.running
        writer.stop()

        failures = log.events("session.heartbeat_write_failed")
        assert failures and failures[0]["error_type"] == "RuntimeError"
        assert writer.reclaim is None

    def test_a_reclaim_ends_the_writer_and_is_kept_for_the_runner(self):
        record, log = SpyRecord(), CapturingLog()
        record.raises = SessionReclaimedError("session 'alpha' was reclaimed")
        writer = self._writer(record, log)

        writer.start()
        assert self._wait_for(lambda: writer.reclaim is not None)
        assert self._wait_for(lambda: not writer.running)

        assert isinstance(writer.reclaim, SessionReclaimedError)
        assert len(record.activity) == 1, "the writer must stop at the first refusal"
        assert log.level_of("session.reclaimed_by_another_process") == "error"

    def test_stop_is_idempotent_and_prevents_further_writes(self):
        record, log = SpyRecord(), CapturingLog()
        writer = self._writer(record, log)

        writer.start()
        self._wait_for(lambda: len(record.activity) >= 1)
        writer.stop()
        writer.stop()
        written = len(record.activity)
        time.sleep(0.05)

        assert len(record.activity) == written
        assert not writer.running

    def test_stopping_before_the_first_interval_elapses_writes_nothing(self):
        """The claim already stamped the heartbeat; an immediate write would be
        a redundant round trip — the writer sleeps first, like the steady loop.
        """
        record, log = SpyRecord(), CapturingLog()
        writer = self._writer(record, log, interval_seconds=30.0)

        writer.start()
        writer.stop()

        assert record.activity == []
        assert not writer.running


class TestTheWriteStaysOffTheKernelsExecutor:
    """Decision D2 — the record write runs on this object's OWN thread pool.

    Review fix, 2026-08-23. The write used to go through ``asyncio.to_thread``,
    which resolves to the loop's **default** executor — the one
    ``TradingNode.__init__`` replaces with the kernel's and ``dispose()`` joins
    with ``wait=True``. That made AC #9's bound inert end to end: cancelling the
    task completes it instantly (``CancelledError`` is not caught), so
    ``join_heartbeat`` returned in 0.00s and its timeout branch never ran, and
    the teardown then blocked **59.8s** inside ``dispose()`` on the very write
    the bound existed to escape. Measured before and after.
    """

    async def test_the_write_does_not_run_on_the_loops_default_executor(self):
        """The load-bearing fact, asserted directly rather than by timing.

        Mutation that must fail this: put ``asyncio.to_thread`` back.
        """
        default_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kernel-default")
        asyncio.get_running_loop().set_default_executor(default_pool)
        seen: list[str] = []

        class _ThreadNamingRecord(SpyRecord):
            def record_activity(self, *, at, bar_seen_at=None):
                seen.append(threading.current_thread().name)
                super().record_activity(at=at, bar_seen_at=bar_seen_at)

        clock, log = FakeClock(), CapturingLog()
        state = _steady_state(clock, _ThreadNamingRecord(), log)
        try:
            await _run_ticks(state, 1)
        finally:
            state.release_executor()
            default_pool.shutdown(wait=False)

        assert seen, "the tick never wrote"
        assert not any(name.startswith("kernel-default") for name in seen), (
            f"the record write ran on the loop's default executor ({seen}) — `dispose()` joins "
            "that pool with wait=True, which is what made AC #9's bound inert"
        )
        assert all("session-heartbeat-write" in name for name in seen), seen

    async def test_release_executor_does_not_wait_for_an_in_flight_write(self):
        """`wait=False` is the whole point: a wedged write must not hold the
        teardown open. Bounded generously (2s) against a write that blocks for
        far longer, so the assertion is about *not waiting*, not about speed.
        """
        entered, release = threading.Event(), threading.Event()

        class _WedgedRecord(SpyRecord):
            def record_activity(self, *, at, bar_seen_at=None):
                entered.set()
                release.wait(30)  # uninterruptible by Task.cancel()

        clock, log = FakeClock(), CapturingLog()
        state = _steady_state(clock, _WedgedRecord(), log)
        task = asyncio.create_task(state.run())
        try:
            await asyncio.to_thread(entered.wait, 5)
            assert entered.is_set(), "the write never started"

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            started = time.monotonic()
            state.release_executor()
            elapsed = time.monotonic() - started
        finally:
            release.set()

        assert elapsed < 2.0, (
            f"release_executor() waited {elapsed:.1f}s for an in-flight write — it must not, or a "
            "wedged Postgres holds the node teardown and the `-> stopped` transition behind it"
        )


class TestJoinHeartbeat:
    """Review fix (2026-08-21): ``gather(return_exceptions=True)`` swallows an
    in-flight reclaim — the join must report it, or the final release touches a
    row another process owns.
    """

    def _completed_task(self, loop, exc: BaseException | None):
        async def _outcome():
            if exc is not None:
                raise exc

        task = loop.create_task(_outcome())
        loop.run_until_complete(asyncio.sleep(0.01))
        return task

    def test_a_reclaim_that_completed_the_task_is_reported(self):
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        try:
            task = self._completed_task(loop, SessionReclaimedError("taken"))
            assert join_heartbeat(task, loop, log) is True
        finally:
            loop.close()

    def test_an_ordinary_failure_is_not_reported_as_a_reclaim(self):
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        try:
            task = self._completed_task(loop, RuntimeError("db went away"))
            assert join_heartbeat(task, loop, log) is False
        finally:
            loop.close()

    def test_a_pending_task_is_cancelled_and_joined_without_a_reclaim(self):
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        try:

            async def _waits_forever():
                await asyncio.Event().wait()

            task = loop.create_task(_waits_forever())
            loop.run_until_complete(asyncio.sleep(0.01))
            assert join_heartbeat(task, loop, log) is False
            assert task.cancelled()
        finally:
            loop.close()

    def test_no_task_and_a_closed_loop_are_both_no_ops(self):
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        loop.close()

        assert join_heartbeat(None, loop, log) is False
        assert join_heartbeat(None, asyncio.new_event_loop(), log) is False

    def test_a_task_that_cannot_be_cancelled_returns_within_the_bound(self):
        """Story 2.6, AC #9: the measured shape is ``asyncio.to_thread`` stuck
        inside a socket read, which ``Task.cancel()`` cannot interrupt at all.
        Driven with an injected, deterministic stand-in — not wall-clock luck.
        """
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        try:

            async def _immune_to_cancellation():
                while True:
                    try:
                        await asyncio.sleep(1000)
                    except asyncio.CancelledError:
                        continue  # a blocking thread join would ignore this too

            task = loop.create_task(_immune_to_cancellation())
            loop.run_until_complete(asyncio.sleep(0.01))

            started = time.monotonic()
            result = join_heartbeat(task, loop, log, timeout=0.05)
            elapsed = time.monotonic() - started

            assert result is False
            assert elapsed < 2.0, f"the join took {elapsed:.2f}s against a 0.05s bound"
            assert log.level_of("session.heartbeat_join_timeout") == "error"
        finally:
            # The task is, by construction, immune to cancellation — do not
            # await it here, or this cleanup hangs exactly as the bug this
            # test exists to catch would. Closing the loop with it still
            # pending is enough; nothing keeps a reference to it afterwards.
            with contextlib.suppress(RuntimeError):
                loop.close()

    def test_a_cleanly_cancelled_task_logs_nothing(self):
        log = CapturingLog()
        loop = asyncio.new_event_loop()
        try:

            async def _waits_forever():
                await asyncio.Event().wait()

            task = loop.create_task(_waits_forever())
            loop.run_until_complete(asyncio.sleep(0.01))

            assert join_heartbeat(task, loop, log) is False
            assert log.records == []
        finally:
            loop.close()


class TestReleaseRecord:
    """The stop-path policy in one place: skip when dispossessed, survive a
    refusal, survive a failure, write exactly once when clean.
    """

    def test_ownership_lost_skips_the_write_entirely_and_logs_reclaimed(self):
        record, log = SpyRecord(), CapturingLog()

        failed = release_record(record, log, trader_id="PAPER-c697f850", ownership_lost=True)

        assert record.stopped == 0
        assert log.level_of("session.reclaimed_by_another_process") == "error"
        assert failed is False, "a reclaim is not the AC #9 failure the caller warns about"

    def test_a_stop_refused_by_the_ownership_guard_is_logged_not_raised(self):
        """The reclaim happened inside the detection window — the guard on the
        write is the first this process hears of it (review fix, 2026-08-21).
        """
        log = CapturingLog()

        class _Refusing(SpyRecord):
            def mark_stopped(self) -> None:
                super().mark_stopped()
                raise SessionReclaimedError("the row belongs to someone else")

        failed = release_record(_Refusing(), log, trader_id="PAPER-c697f850", ownership_lost=False)

        assert log.level_of("session.reclaimed_by_another_process") == "error"
        assert failed is False, "a reclaim discovered on write is not the AC #9 failure either"

    def test_an_ordinary_failure_is_logged_as_mark_stopped_failed(self):
        log = CapturingLog()

        class _Failing(SpyRecord):
            def mark_stopped(self) -> None:
                raise RuntimeError("db went away")

        failed = release_record(_Failing(), log, trader_id="PAPER-c697f850", ownership_lost=False)

        events = log.events("session.mark_stopped_failed")
        assert events and events[0]["error_type"] == "RuntimeError"
        assert failed is True, "AC #9: this is exactly the shape the CLI must warn about"

    def test_the_clean_path_writes_exactly_once(self):
        record, log = SpyRecord(), CapturingLog()

        failed = release_record(record, log, trader_id="PAPER-c697f850", ownership_lost=False)

        assert record.stopped == 1
        assert log.records == []
        assert failed is False


class _FakeGuard:
    """A ``StrategyGuard``-shaped double: the tick only needs two members."""

    def __init__(self, pending=(), all_failed=False) -> None:
        self._pending = list(pending)
        self.all_failed = all_failed
        self.drains = 0

    def drain_pending(self):
        self.drains += 1
        drained = tuple(self._pending)
        self._pending.clear()
        return drained

    def requeue(self, failures):
        self._pending[:0] = failures

    def queue(self, *failures):
        self._pending.extend(failures)


def _failure(spec_strategy_id="sma_crossover", **overrides):
    from src.core.live_strategy_guard import StrategyFailure

    fields = {
        "strategy_id": "SMACrossover-000",
        "spec_strategy_id": spec_strategy_id,
        "error_type": "DivisionByZero",
        "handler": "handle_bar",
        "at": STARTED_AT,
        "detail": "[<class 'decimal.DivisionByZero'>]",
    }
    fields.update(overrides)
    return StrategyFailure(**fields)


class TestTheTickDrainsTheStrategyGuard:
    """Story 2.7, AC #10 — the write happens *here*, not in the bar handler.

    ``handle_*`` runs inline on the event-loop thread inside
    ``MessageBus.publish_c``. A Postgres round trip there stalls the loop and
    delays the bar for every later-subscribed strategy — partially recreating
    the AC #2 starvation the guard exists to fix — and this repo has already
    measured that class of write blocking the node for **59.81s** (decision D2).
    So the guard queues and this tick drains.
    """

    async def test_a_queued_failure_is_written_on_the_tick(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        guard = _FakeGuard([_failure()])
        state = _steady_state(clock, record, log, guard=guard)

        await _run_ticks(state, 1)

        assert record.failures == [("sma_crossover", False)]

    async def test_the_write_runs_on_the_objects_own_executor_never_the_loop(self):
        """⚠️ Never ``asyncio.to_thread``, whose default executor is the
        kernel's — the one ``TradingNode.dispose()`` joins with ``wait=True``.
        Asserted by **thread name**, so a mutation back to ``to_thread`` fails
        rather than merely being slower.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log, guard=_FakeGuard([_failure()]))

        await _run_ticks(state, 1)

        assert record.failure_threads
        assert all(name.startswith("session-heartbeat-write") for name in record.failure_threads), (
            record.failure_threads
        )

    async def test_an_empty_queue_costs_no_write_at_all(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        guard = _FakeGuard()
        state = _steady_state(clock, record, log, guard=guard)

        await _run_ticks(state, 3)

        assert record.failures == []
        assert guard.drains == 3

    async def test_two_queued_failures_are_both_written(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        guard = _FakeGuard([_failure(), _failure("momentum")], all_failed=True)
        state = _steady_state(clock, record, log, guard=guard)

        await _run_ticks(state, 1)

        assert record.failures == [("sma_crossover", True), ("momentum", True)]

    async def test_a_failure_queued_after_the_first_tick_is_written_on_the_next(self):
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        guard = _FakeGuard()
        state = _steady_state(clock, record, log, guard=guard)
        guard.queue(_failure())

        await _run_ticks(state, 2)

        assert record.failures == [("sma_crossover", False)]

    async def test_a_write_failure_is_logged_and_the_session_continues(self):
        """AR42: a database hiccup must never kill a trading session."""
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        record.failure_raises = RuntimeError("postgres is down")
        state = _steady_state(clock, record, log, guard=_FakeGuard([_failure()]))

        await _run_ticks(state, 2)

        assert ("error", "session.strategy_record_failed") in [
            (level, event) for level, event, _ in log.records
        ]
        assert state.ticks == 2

    async def test_a_failed_write_is_retried_on_the_next_tick_and_lands(self):
        """Review fix, 2026-08-23: a drained failure whose write hit a
        transient DB error used to be dropped forever — blinding AC #4's
        cross-process visibility for the rest of a multi-week run over a
        one-tick hiccup. The guard latches per strategy, so the retry backlog
        is bounded by the session's strategy count, not the unbounded state
        NFR2 forbids. Tick 1: the write raises and the failure is re-queued.
        Tick 2: Postgres is back, and the record lands.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        attempts = {"n": 0}
        original = record.record_strategy_failure

        def flaky_once(**kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("postgres is down")
            original(**kwargs)

        record.record_strategy_failure = flaky_once
        guard = _FakeGuard([_failure()])
        state = _steady_state(clock, record, log, guard=guard)

        await _run_ticks(state, 2)

        assert attempts["n"] == 2
        assert record.failures == [("sma_crossover", False)]
        assert guard.drains == 2

    async def test_a_reclaim_on_the_failure_write_ends_the_loop(self):
        """The one exception AR42 does not survive, on this path as on the
        heartbeat's: two processes on one broker account is NFR6's catastrophe.

        It reaches a boundary *here* — the tick — rather than propagating out of
        a wrapped ``handle_*``, where the measured outcome is ``rc=1`` with zero
        bytes of output (*Judgment call #10*).
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        record.failure_raises = SessionReclaimedError("taken by another process")
        state = _steady_state(clock, record, log, guard=_FakeGuard([_failure()]))

        with pytest.raises(SessionReclaimedError):
            await _run_ticks(state, 3)

    async def test_a_session_with_no_guard_ticks_exactly_as_before(self):
        """The guard is optional, so Story 2.5's and 2.6's construction sites
        and every existing test keep working unmodified.
        """
        clock, record, log = FakeClock(), SpyRecord(), CapturingLog()
        state = _steady_state(clock, record, log)

        await _run_ticks(state, 2)

        assert state.ticks == 2
        assert record.failures == []
