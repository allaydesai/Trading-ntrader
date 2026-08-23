"""Component tests for stopping a session (Story 2.6, Tasks 3, 5, 8, 9).

Component tier: drives a ``TestLiveNode`` double, never a real ``TradingNode``.
Signal *delivery* is not under test here — that is
``tests/unit/core/test_live_session_signals.py`` (the policy) and
``tests/integration/core/test_live_session_signal_ownership.py`` (the real
kernel's clobbering of it). What is under test here is the *wiring*: does the
runner notice a stop at the right boundary, does it tear down in full, does
identity survive repeated cycles, does the force-exit seam get used correctly.

A stop is simulated by calling ``runner._signals._handle(signal.SIGINT, None)``
directly — the same entry point a real ``signal.signal`` callback would use —
rather than sending a real OS signal, so these tests stay ``-n auto`` safe and
need no subprocess.
"""

import asyncio
import signal
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import structlog
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_gate import GateDecision, GateMode
from src.core.live_session_node import BAR_TOPIC
from src.core.live_session_phases import PHASE_SEQUENCE
from src.core.live_session_runner import LiveSessionRunner
from src.core.live_session_signals import FORCE_EXIT_CODE, SessionStopSignals
from src.core.live_trader_id import derive_trader_id
from src.models.session import SessionSpec, StrategySpec
from tests.component.doubles import TestLiveNode

pytestmark = pytest.mark.component

SESSION_ID = UUID("44444444-4444-4444-4444-444444444444")
STARTED_AT = datetime(2026, 8, 19, 14, 30, 0, tzinfo=timezone.utc)
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_session_runner_phases.py:61-78``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the runner's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    for name in (
        "IBKR_RATE_LIMIT",
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IB_MAX_CONNECTION_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings() -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode="paper",
        tws_account="DU4076626",
        ntrader_real_money_account="",
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
    )


def _spec() -> SessionSpec:
    return SessionSpec(
        strategies=(
            StrategySpec(strategy_id="sma_crossover", parameters={}, bar_types=(AAPL_1MIN,)),
        )
    )


async def _permitting_verifier(node, settings, **kwargs) -> GateDecision:
    return GateDecision(permitted=True, mode=GateMode.PAPER)


class FakeClock:
    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SpyRecord:
    """Mirrors ``test_session_runner_phases.SpyRecord`` for this file's needs."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")


async def _never_sleeps(seconds: float) -> None:
    await asyncio.Event().wait()


def _runner(
    node: TestLiveNode, *, record=None, session_id=SESSION_ID, **overrides
) -> LiveSessionRunner:
    def _factory(settings_arg, **kwargs):
        return node

    options = {
        "session_id": session_id,
        "spec": _spec(),
        "record": record if record is not None else SpyRecord(),
        "started_at": STARTED_AT,
        "connect_timeout": 2.0,
        "node_factory": _factory,
        "account_verifier": _permitting_verifier,
        "client_builder": lambda *a, **k: None,
        "sleeper": _never_sleeps,
    }
    options.update(overrides)
    return LiveSessionRunner(_settings(), **options)


def _pairs(logs) -> list[tuple[str, str]]:
    return [
        (entry["phase"], entry["status"])
        for entry in logs
        if entry.get("phase") and entry.get("status")
    ]


def _spying_node(record: SpyRecord, **kwargs) -> TestLiveNode:
    """A double whose teardown steps land on ONE ordered list with the record's.

    ``unsubscribe`` is recorded here too (review fix, 2026-08-22). Before this
    it appended only to ``node.trader.unsubscriptions``, so there was no list
    containing both it and ``dispose`` — and
    ``test_unsubscribe_happens_before_dispose`` could not actually assert the
    ordering in its own name. An ordering test needs one ordered list.
    """
    node = TestLiveNode(**kwargs)
    original_dispose = node.dispose
    original_unsubscribe = node.trader.unsubscribe

    def _dispose():
        record.calls.append("dispose")
        original_dispose()

    def _unsubscribe(topic, handler):
        record.calls.append("unsubscribe")
        original_unsubscribe(topic, handler)

    node.dispose = _dispose  # type: ignore[method-assign]
    node.trader.unsubscribe = _unsubscribe  # type: ignore[method-assign]
    return node


def _stop_after(name: str):
    """Wrap ``LiveSessionRunner``'s phase method ``name`` so that, right after
    it returns, a stop signal is simulated through the real signal-handling
    entry point — exactly as ``signal.signal`` would deliver one.
    """
    original = getattr(LiveSessionRunner, name)
    if asyncio.iscoroutinefunction(original):

        async def _wrapper(self):
            await original(self)
            self._signals._handle(signal.SIGINT, None)

        return _wrapper

    def _wrapper(self):
        original(self)
        self._signals._handle(signal.SIGINT, None)

    return _wrapper


#: The eight phase attributes in ``PHASE_SEQUENCE`` order.
PHASE_ATTRS = (
    "_phase_gate_static",
    "_phase_node_build",
    "_phase_node_connect",
    "_phase_gate_account",
    "_phase_reconcile",
    "_phase_warmup",
    "_phase_subscribe",
    "_phase_trading",
)


class TestAStopAtEachPhaseBoundaryRunsNoLaterPhase:
    """Task 3, AC #8 — mirrors Story 2.5's landmine technique, one case per
    boundary. Stopping after phase ``i`` must run phases ``0..i`` and never
    phases ``i+1..7``.
    """

    @staticmethod
    def _landmine(*args, **kwargs):
        raise AssertionError("a later phase ran after a stop was requested")

    @pytest.mark.parametrize("stop_after_index", range(len(PHASE_ATTRS)))
    def test_no_phase_after_the_stop_boundary_runs(self, monkeypatch, stop_after_index):
        stop_after_attr = PHASE_ATTRS[stop_after_index]
        later_attrs = PHASE_ATTRS[stop_after_index + 1 :]

        monkeypatch.setattr(LiveSessionRunner, stop_after_attr, _stop_after(stop_after_attr))
        for attr in later_attrs:
            monkeypatch.setattr(LiveSessionRunner, attr, self._landmine)

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        with capture_logs() as logs:
            runner.run()  # must not raise — a stop is a success (Task 3 RED #3)

        emitted = {name for name, _ in _pairs(logs)}
        boundary = PHASE_SEQUENCE[stop_after_index]
        for later_phase in PHASE_SEQUENCE[stop_after_index + 1 :]:
            assert later_phase not in emitted, (
                f"{later_phase} ran after a stop following {boundary}"
            )

    def test_the_landmines_are_live(self, monkeypatch):
        """Non-vacuity: without the stop wrapper, the landmine after phase 0
        would trip on a clean run — proving the assertion above could fail.
        """
        for attr in PHASE_ATTRS[1:]:
            monkeypatch.setattr(LiveSessionRunner, attr, self._landmine)
        runner = _runner(TestLiveNode())

        with pytest.raises(AssertionError, match="a later phase ran"):
            runner.run()


class TestTheTeardownStillRunsInFullAfterAStop:
    """Task 3 — ``mark_stopped`` still exactly once, after ``dispose``."""

    def test_dispose_then_mark_stopped_exactly_once(self, monkeypatch):
        record = SpyRecord()
        node = _spying_node(record, run_seconds=0.01)
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        runner = _runner(node, record=record)

        runner.run()

        assert record.calls.count("mark_stopped") == 1
        assert record.calls.index("dispose") < record.calls.index("mark_stopped")

    def test_a_stop_during_node_build_still_tears_down_and_marks_stopped(self, monkeypatch):
        """The earliest boundary: no strategy, no subscription, barely a node."""
        record = SpyRecord()
        node = _spying_node(record, run_seconds=0.01)
        monkeypatch.setattr(
            LiveSessionRunner, "_phase_node_build", _stop_after("_phase_node_build")
        )
        for attr in PHASE_ATTRS[2:]:
            monkeypatch.setattr(
                LiveSessionRunner, attr, TestAStopAtEachPhaseBoundaryRunsNoLaterPhase._landmine
            )
        runner = _runner(node, record=record)

        runner.run()

        assert record.calls == ["dispose", "mark_stopped"]
        assert node.trader.added_strategies == []

    def test_run_returns_normally_no_exception_escapes(self):
        """Task 3 RED #3, stated as its own assertion."""
        monkeypatch_target = "_phase_trading"
        original = getattr(LiveSessionRunner, monkeypatch_target)
        try:
            LiveSessionRunner._phase_trading = _stop_after(monkeypatch_target)
            runner = _runner(TestLiveNode(run_seconds=0.01))
            runner.run()  # no pytest.raises — this IS the assertion
        finally:
            LiveSessionRunner._phase_trading = original


class TestUnsubscribeOnStop:
    """Task 5 — the runner's own bar-topic subscription is cancelled, guarded,
    before ``shutdown``.
    """

    def test_unsubscribe_is_called_once_with_the_subscribed_handler(self, monkeypatch):
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert len(node.trader.unsubscriptions) == 1
        topic, handler = node.trader.unsubscriptions[0]
        assert topic == BAR_TOPIC
        subscribed_topic, subscribed_handler = node.trader.subscriptions[0]
        assert (topic, handler) == (subscribed_topic, subscribed_handler)

    def test_unsubscribe_happens_before_dispose(self, monkeypatch):
        """Task 5's load-bearing ordering, on one ordered list.

        Review fix, 2026-08-22: this used to assert only that unsubscribe
        happened *at all* plus `dispose < mark_stopped` (a different fact,
        already covered), so moving `self._unsubscribe()` after `shutdown(...)`
        — inverting the very ordering the name claims — left it green.
        """
        record = SpyRecord()
        node = _spying_node(record, run_seconds=0.01)
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        runner = _runner(node, record=record)

        runner.run()

        assert "unsubscribe" in record.calls, "the runner never cancelled its own subscription"
        assert record.calls.index("unsubscribe") < record.calls.index("dispose"), (
            f"unsubscribe must precede the node teardown; got {record.calls}"
        )
        assert record.calls.index("dispose") < record.calls.index("mark_stopped")

    def test_a_raising_unsubscribe_is_logged_and_does_not_block_teardown(self, monkeypatch):
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        node = TestLiveNode(run_seconds=0.01)
        node.trader.raise_on_unsubscribe = RuntimeError("message bus is gone")
        record = SpyRecord()
        runner = _runner(node, record=record)

        with capture_logs() as logs:
            runner.run()  # must not raise

        assert record.calls == ["mark_stopped"]
        assert node.disposed is True
        failures = [e for e in logs if e["event"] == "session.unsubscribe_failed"]
        assert failures and failures[0]["error_type"] == "RuntimeError"

    def test_no_unsubscribe_attempted_when_subscribe_never_ran(self, monkeypatch):
        """A stop before `subscribe` — nothing to cancel."""
        monkeypatch.setattr(
            LiveSessionRunner, "_phase_node_connect", _stop_after("_phase_node_connect")
        )
        for attr in (
            "_phase_gate_account",
            "_phase_reconcile",
            "_phase_warmup",
            "_phase_subscribe",
            "_phase_trading",
        ):
            monkeypatch.setattr(
                LiveSessionRunner, attr, TestAStopAtEachPhaseBoundaryRunsNoLaterPhase._landmine
            )
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert node.trader.unsubscriptions == []


class TestAStopWhileTheSessionIsServing:
    """AC #1's actual scenario, which had no test at any tier.

    Review fix, 2026-08-22. Every other stop test drives ``_stop_after``, which
    fires once a phase has *returned* — so ``run()`` always raised at
    ``raise_if_requested()`` before reaching ``_serve()``, and the steady-state
    stop the story exists for was proven by prose alone. A repo-wide grep for
    ``request_node_stop`` / ``call_soon_threadsafe`` in ``tests/`` returned
    nothing, and both integration probes pass ``on_stop=lambda name: None``, so
    the ``loop.call_soon_threadsafe(node.stop)`` hand-off was never once
    exercised for effect.
    """

    @staticmethod
    def _signal_once_serving(runner: LiveSessionRunner) -> None:
        """Deliver the signal from inside the running loop, as a real one is."""
        original = LiveSessionRunner._serve

        async def _wrapper(self):
            self._loop.call_later(0.01, self._signals._handle, signal.SIGINT, None)
            await original(self)

        runner._serve = _wrapper.__get__(runner, LiveSessionRunner)

    def test_the_node_is_asked_to_stop_and_run_returns_cleanly(self):
        record = SpyRecord()
        # `run_seconds` is a HANG-GUARD, not the exit path: the node returns
        # after 5s so a broken hand-off fails red instead of blocking CI, while
        # `assert node.stopped` below is what proves the signal actually
        # reached `node.stop()` rather than the clock ending the run.
        node = _spying_node(record, run_seconds=5.0)
        runner = _runner(node, record=record)
        self._signal_once_serving(runner)

        with capture_logs() as logs:
            runner.run()  # must not raise: a stop is a success

        assert node.stopped, "node.stop() was never reached from the signal handler"
        assert runner.stop_signal == "SIGINT"
        assert runner.stopped_by_signal is True
        assert record.calls.index("unsubscribe") < record.calls.index("dispose")
        assert record.calls.count("mark_stopped") == 1

        # AC #1's `session.stopped` record, which no test asserted on.
        stopped = [entry for entry in logs if entry.get("event") == "session.stopped"]
        assert len(stopped) == 1, f"expected exactly one session.stopped, got {stopped}"
        assert stopped[0]["signal"] == "SIGINT"

    def test_a_stop_while_serving_reports_no_shutdown_problems(self):
        """The clean path: D4's new warning must stay silent on a good stop."""
        node = TestLiveNode(run_seconds=5.0)
        runner = _runner(node)
        self._signal_once_serving(runner)

        runner.run()

        assert runner.shutdown_problems == []
        assert runner.record_release_failed is False


class TestIdentityAcrossStopStartCycles:
    """Task 8 (component half) — three cycles against doubles."""

    def test_three_cycles_leave_identity_unchanged_and_call_mark_stopped_three_times(
        self, monkeypatch
    ):
        # Wrapped ONCE, before any cycle: `_stop_after` reads the class's
        # current `_phase_trading` to find "the original" — wrapping inside
        # the loop would re-wrap the *already-wrapped* method on cycles 2 and
        # 3, firing the stop handler multiple times per run and forcing a
        # second-signal exit instead of a graceful one.
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        record = SpyRecord()
        trader_ids = []
        session_ids = []

        for _ in range(3):
            node = TestLiveNode(run_seconds=0.01)
            runner = _runner(node, record=record)
            trader_ids.append(runner._trader_id)
            session_ids.append(runner._session_id)

            runner.run()

        assert len(set(trader_ids)) == 1
        assert len(set(session_ids)) == 1
        assert record.calls.count("mark_stopped") == 3
        # Review fix, 2026-08-22: the two `len(set(...)) == 1` assertions above
        # cannot fail on their own — every cycle is built by `_runner(...)`,
        # which passes the same literal `SESSION_ID`, and `_trader_id` is a
        # pure function of it. Pin the identity to a value derived
        # independently of the runner, so a runner that started deriving the
        # trader id from something else (a uuid4 per run, the node, the clock)
        # would be caught.
        assert trader_ids[0] == derive_trader_id(SESSION_ID)
        assert session_ids[0] == SESSION_ID

    def test_a_different_session_yields_a_different_trader_id(self):
        """The other half of the identity claim, which nothing asserted.

        "Same session ⇒ same trader id" is only half a guarantee; without this
        a `derive_trader_id` that returned a constant would satisfy every
        assertion above.
        """
        other = UUID("55555555-5555-5555-5555-555555555555")
        mine = _runner(TestLiveNode(run_seconds=0.01))
        theirs = _runner(TestLiveNode(run_seconds=0.01), session_id=other)

        assert mine._trader_id != theirs._trader_id


class TestForceExitSeam:
    """Task 9 (component half) — the second signal calls ``force_exit`` with
    ``FORCE_EXIT_CODE``, and nothing further is attempted through the record.
    """

    def test_a_second_signal_calls_force_exit_and_attempts_no_further_record_write(
        self, monkeypatch
    ):
        force_exit_calls: list[int] = []

        def _force_exit(code: int) -> None:
            force_exit_calls.append(code)
            # A real force_exit is `NoReturn` (`os._exit`); this double
            # returns so the test can inspect state afterwards instead of
            # killing the pytest worker.

        # Review fix, 2026-08-22: the record is wired into a REAL runner now.
        # It used to be constructed and passed to nothing, so
        # `assert record.calls == []` was true for every possible production
        # behaviour — including one that wrote to the record on the force-exit
        # path, which is the exact claim the assertion exists to make.
        record = SpyRecord()
        node = _spying_node(record, run_seconds=0.01)
        runner = _runner(node, record=record)
        signals = SessionStopSignals(
            log=structlog.get_logger("test"),
            on_stop=runner._request_node_stop,
            force_exit=_force_exit,
        )
        runner._signals = signals

        # Fire the stop-signal handler directly, twice, the way two real
        # signals would — the second must not be a graceful stop.
        signals._handle(signal.SIGINT, None)
        signals._handle(signal.SIGINT, None)

        assert force_exit_calls == [FORCE_EXIT_CODE]
        # AC #6: no best-effort record write on the force-exit path. The record
        # is genuinely reachable here — the runner holds it and the first
        # signal's `on_stop` ran against it — so a `mark_stopped()` added to
        # `_default_force_exit` or to the second-signal branch would fail this.
        assert record.calls == [], f"the force-exit path touched the record: {record.calls}"
