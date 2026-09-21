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
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import structlog
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_gate import GateDecision, GateMode
from src.core.live_session_node import BAR_TOPIC
from src.core.live_session_phases import PHASE_SEQUENCE
from src.core.live_session_runner import LiveSessionRunner, stop_degraded_strategies
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
        self.failures: list[str] = []
        #: Story 3.7's fourth port method — the teardown flush's target.
        self.rejections: list[dict] = []
        self.rejection_raises: BaseException | None = None

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")

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
        """Story 2.7's third port method. Duck-typed here, but kept complete so
        the double stays an honest ``SessionRecordPort`` rather than one that
        happens to satisfy the calls this file makes today.
        """
        self.calls.append("record_strategy_failure")
        self.failures.append(spec_strategy_id)

    def record_order_rejections(
        self,
        *,
        rejected: int,
        denied: int,
        consecutive: int,
        first_at: datetime,
        last_at: datetime,
        last_kind: str,
        last_client_order_id: str,
        last_instrument_id: str,
        last_strategy_id: str,
        last_reason: str,
        last_reconciliation: bool,
    ) -> None:
        """Story 3.7's fourth port method. Duck-typed here, but kept complete so
        the double stays an honest ``SessionRecordPort`` rather than one that
        happens to satisfy the calls this file makes today.
        """
        self.calls.append("record_order_rejections")
        self.rejections.append(
            {
                "rejected": rejected,
                "denied": denied,
                "consecutive": consecutive,
                "first_at": first_at,
                "last_at": last_at,
                "last_kind": last_kind,
                "last_client_order_id": last_client_order_id,
                "last_instrument_id": last_instrument_id,
                "last_strategy_id": last_strategy_id,
                "last_reason": last_reason,
                "last_reconciliation": last_reconciliation,
            }
        )
        if self.rejection_raises is not None:
            raise self.rejection_raises


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

        topic, handler = node.trader.unsubscriptions[0]
        assert topic == BAR_TOPIC
        subscribed_topic, subscribed_handler = node.trader.subscriptions[0]
        assert (topic, handler) == (subscribed_topic, subscribed_handler)

    def test_every_subscription_the_runner_made_is_cancelled(self, monkeypatch):
        """Review fix, 2026-08-30. Story 3.2 added two more subscriptions in
        ``_phase_subscribe`` (the order observer's bar anchor and its
        ``events.order*`` handler) and cancelled neither, so both kept firing
        through teardown — ``order.submitted`` records could land *after*
        ``session.stopped``, in the very transcript AC #7 asks an operator to
        read.

        This test is written against the whole set rather than index ``[0]``
        on purpose: the assertion it replaces hardcoded
        ``len(unsubscriptions) == 1``, so it stayed green by construction when
        subscriptions two and three arrived. Comparing sets means the next
        subscription added without a matching cancel goes red on its own.
        """
        monkeypatch.setattr(LiveSessionRunner, "_phase_trading", _stop_after("_phase_trading"))
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)

        runner.run()

        assert set(node.trader.unsubscriptions) == set(node.trader.subscriptions), (
            "every subscription the runner made in _phase_subscribe must be cancelled on stop"
        )

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


class StubTrader:
    """Exposes ``strategies()`` as a CALLABLE, matching the real
    ``Trader.strategies()`` method shape (``trading/trader.py:160``) — a list
    ATTRIBUTE here would make this test pass against production code that
    crashes on the real method-vs-property distinction.
    """

    def __init__(self, strategies: list) -> None:
        self._strategies = strategies

    def strategies(self) -> list:
        return self._strategies


class TestStopDegradedStrategies:
    """Story 3.1, AC #6 — component tier for :func:`stop_degraded_strategies`'s
    containment logic against a stub trader. FSM/teardown facts (does
    ``on_stop()`` actually run, does the state end ``STOPPED``) need a real
    ``Trader`` — see the integration counterpart in
    ``test_live_strategy_failure_survives.py``.
    """

    def test_each_degraded_strategy_is_stopped_once_and_the_raiser_is_contained(self):
        # Bare MagicMock attributes are truthy — every stub sets `is_degraded`
        # explicitly (the standing trap this repo has been bitten by before).
        running = MagicMock()
        running.is_degraded = False

        clean_degraded = MagicMock()
        clean_degraded.is_degraded = True

        raising_degraded = MagicMock()
        raising_degraded.is_degraded = True
        raising_degraded.stop.side_effect = RuntimeError("boom")

        trader = StubTrader([running, clean_degraded, raising_degraded])
        log = MagicMock()

        problems = stop_degraded_strategies(trader, log)

        running.stop.assert_not_called()
        clean_degraded.stop.assert_called_once()
        raising_degraded.stop.assert_called_once()
        assert problems == ["strategy_stop: RuntimeError"]

    def test_a_raise_does_not_prevent_the_remaining_degraded_strategies_stopping(self):
        """AC #6's containment clause, which the test above cannot reach.

        Review fix, 2026-08-29. The ordering above puts the raiser **last**,
        so hoisting the ``try/except`` out of the ``for`` loop — loop-level
        containment instead of per-strategy — keeps every assertion green:
        the clean strategy has already been stopped by the time the raise
        happens. Mutation run during review and it survived. Here the raiser
        goes **first**, which is the only arrangement that distinguishes the
        two shapes, and both survivors are checked so a single-survivor
        version cannot pass either.
        """
        raising_first = MagicMock()
        raising_first.is_degraded = True
        raising_first.stop.side_effect = RuntimeError("boom")

        second_raiser = MagicMock()
        second_raiser.is_degraded = True
        second_raiser.stop.side_effect = ValueError("also boom")

        survivor = MagicMock()
        survivor.is_degraded = True

        problems = stop_degraded_strategies(
            StubTrader([raising_first, second_raiser, survivor]), MagicMock()
        )

        raising_first.stop.assert_called_once()
        second_raiser.stop.assert_called_once()
        assert survivor.stop.call_count == 1, "a raise aborted the rest of the teardown"
        # Both failures reported, in order, each carrying only its type (NFR26).
        assert problems == ["strategy_stop: RuntimeError", "strategy_stop: ValueError"]

    def test_no_degraded_strategies_stops_nothing_and_reports_no_problems(self):
        running = MagicMock()
        running.is_degraded = False

        problems = stop_degraded_strategies(StubTrader([running]), MagicMock())

        running.stop.assert_not_called()
        assert problems == []

    def test_the_runner_reaches_the_helper_on_the_stop_path(self):
        """AC #6's **call site**, which nothing else pins (review fix,
        2026-08-29).

        Every other test of this helper — here and in
        ``test_live_strategy_failure_survives.py`` — invokes
        ``stop_degraded_strategies`` directly. None constructs a
        ``LiveSessionRunner``, so during review the entire wiring block in
        ``run()``'s ``finally`` was deleted and **904 tests across the unit,
        component and integration tiers stayed green**. The helper was fully
        tested and called by nothing that any test observed.

        This drives the real runner to a clean stop and asserts the degraded
        strategy was stopped as a consequence. Per the Story 2.7 precedent the
        double is not modified: ``strategies`` is patched on the instance.
        """
        node = TestLiveNode(run_seconds=5.0)

        degraded = MagicMock()
        degraded.is_degraded = True
        running = MagicMock()
        running.is_degraded = False
        node.trader.strategies = lambda: [degraded, running]  # type: ignore[method-assign]

        runner = _runner(node)
        TestAStopWhileTheSessionIsServing._signal_once_serving(runner)

        runner.run()

        assert degraded.stop.call_count == 1, (
            "the runner's teardown never reached stop_degraded_strategies — "
            "AC #6's call site is unwired"
        )
        running.stop.assert_not_called()
        assert runner.shutdown_problems == []

    def test_a_degraded_stop_failure_surfaces_in_the_runners_shutdown_problems(self):
        """The other half of the wiring: the helper's return value must reach
        ``shutdown_problems``, which is what the CLI reports to the operator.

        Distinct prefix asserted deliberately — see the helper's own docstring.
        A bare ``"stop: ..."`` was indistinguishable from ``shutdown()``'s
        node-stop failure, which makes the CLI imply the broker socket may
        still be held when in fact only a strategy's teardown raised.
        """
        node = TestLiveNode(run_seconds=5.0)

        degraded = MagicMock()
        degraded.is_degraded = True
        degraded.stop.side_effect = RuntimeError("boom")
        node.trader.strategies = lambda: [degraded]  # type: ignore[method-assign]

        runner = _runner(node)
        TestAStopWhileTheSessionIsServing._signal_once_serving(runner)

        runner.run()

        assert runner.shutdown_problems == ["strategy_stop: RuntimeError"]

    def test_a_helper_level_explosion_does_not_abort_session_teardown(self):
        """AC #6's "neither aborts session teardown" clause, at the level the
        helper itself fails rather than one strategy inside it.

        Review fix, 2026-08-29 (second pass). The runner wraps the helper call
        in its own ``except BaseException`` so teardown can never abort, and
        that branch had no test: an AC #6 clause-by-clause mutation matrix
        narrowed it to ``except ValueError`` and **nothing went red**.

        Reachable, not hypothetical: ``trader.strategies()`` is evaluated by
        the ``for`` statement itself, *outside* the helper's per-strategy
        ``try``, so anything it raises propagates straight out of the helper.
        ``self._node.trader`` is likewise a property (``live/node.py:134``,
        ``return self.kernel.trader``) that can raise on a half-built node.

        Asserts the whole teardown still completed — not merely that ``run()``
        did not raise — because the point of the guard is that everything
        *after* it still happens.
        """
        node = TestLiveNode(run_seconds=5.0)

        def _explode():
            raise RuntimeError("the trader is gone")

        node.trader.strategies = _explode  # type: ignore[method-assign]

        record = SpyRecord()
        runner = _runner(node, record=record)
        TestAStopWhileTheSessionIsServing._signal_once_serving(runner)

        runner.run()  # must not raise — this IS half the assertion

        assert runner.shutdown_problems == ["stop_degraded_strategies: RuntimeError"], (
            f"the helper's explosion was not contained or not reported: {runner.shutdown_problems}"
        )
        # Only the exception TYPE, never the message (NFR26) — the equality
        # above pins this: "the trader is gone" must not appear.
        assert "mark_stopped" in record.calls, (
            "teardown aborted at the helper — the record was never finished"
        )

    def test_all_degraded_strategies_stop_cleanly_reports_no_problems(self):
        first = MagicMock()
        first.is_degraded = True
        second = MagicMock()
        second.is_degraded = True

        problems = stop_degraded_strategies(StubTrader([first, second]), MagicMock())

        first.stop.assert_called_once()
        second.stop.assert_called_once()
        assert problems == []


class TestFlushPendingTradesInTeardown:
    """Story 3.6, Task 8.2: ``flush_pending()`` runs in the same teardown
    slot reasoning as ``_flush_contained_failures`` — after
    ``_stop_heartbeat`` (the row still reads ``running``, the executor
    cannot race it) and before ``_finish_record`` (the row still ours).
    """

    def test_it_runs_after_stop_heartbeat_and_before_finish_record(self, monkeypatch):
        from src.core import live_session_runner as runner_module

        order: list[str] = []
        original_stop_heartbeat = runner_module.LiveSessionRunner._stop_heartbeat
        original_flush_pending = runner_module.LiveSessionRunner._flush_pending_trades
        original_finish_record = runner_module.LiveSessionRunner._finish_record

        def _spy_stop_heartbeat(self, loop):
            order.append("stop_heartbeat")
            return original_stop_heartbeat(self, loop)

        def _spy_flush_pending(self):
            order.append("flush_pending_trades")
            return original_flush_pending(self)

        def _spy_finish_record(self):
            order.append("finish_record")
            return original_finish_record(self)

        monkeypatch.setattr(runner_module.LiveSessionRunner, "_stop_heartbeat", _spy_stop_heartbeat)
        monkeypatch.setattr(
            runner_module.LiveSessionRunner, "_flush_pending_trades", _spy_flush_pending
        )
        monkeypatch.setattr(runner_module.LiveSessionRunner, "_finish_record", _spy_finish_record)

        node = TestLiveNode(run_seconds=0.01)
        _runner(node).run()

        assert order == ["stop_heartbeat", "flush_pending_trades", "finish_record"], order

    def test_a_pending_trade_is_drained_at_teardown_with_no_further_event(self):
        """A trade queued earlier in the run is retried at teardown even
        though nothing else ever delivers another ``events.position*`` to
        trigger the recorder's own drain-on-next-event path.
        """
        from decimal import Decimal

        from src.core.live_trade_recorder import RecordedTrade
        from src.models.trade import TradeBase

        pending = RecordedTrade(
            trade=TradeBase(
                instrument_id="AAPL.NASDAQ",
                trade_id="AAPL.NASDAQ-SMACrossover-000",
                venue_order_id="O-1",
                client_order_id="O-2",
                order_side="BUY",
                quantity=Decimal("10"),
                entry_price=Decimal("100"),
                exit_price=Decimal("110"),
                commission_amount=None,
                commission_currency=None,
                entry_timestamp=STARTED_AT,
                exit_timestamp=STARTED_AT,
            ),
            profit_loss=Decimal("98.50"),
            profit_pct=Decimal("10"),
            holding_period_seconds=300,
            position_id="AAPL.NASDAQ-SMACrossover-000",
            strategy_id="SMACrossover-000",
            fill_count=1,
            trade_key="AAPL.NASDAQ-SMACrossover-000:O-2",
        )
        attempts: list[str] = []

        def eventually_succeeds(recorded) -> bool:
            attempts.append(recorded.trade_key)
            return True

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, trade_sink=eventually_succeeds)

        def _seed_pending():
            assert runner._trade_recorder is not None
            runner._trade_recorder._pending.append(pending)

        original_subscribe = LiveSessionRunner._phase_subscribe

        def _spy_phase_subscribe(self):
            original_subscribe(self)
            _seed_pending()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(LiveSessionRunner, "_phase_subscribe", _spy_phase_subscribe)
            runner.run()

        assert attempts == ["AAPL.NASDAQ-SMACrossover-000:O-2"]
        assert runner._trade_recorder is not None
        assert runner._trade_recorder._pending == []

    def test_skipped_entirely_once_ownership_is_already_lost(self):
        calls: list[str] = []

        class _RecordingRecorder:
            pending_trade_keys = ()

            def flush_pending(self) -> int:
                calls.append("flush_pending")
                return 0

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner._trade_recorder = _RecordingRecorder()  # type: ignore[assignment]
        runner._ownership_lost = True

        runner._flush_pending_trades()

        assert calls == [], "a reclaim already known must not retry a write this process cannot win"

    def test_leftover_trades_are_named_when_ownership_is_lost(self):
        """Review 2026-09-12: not retried, but never silent — every queued
        trade this process could not persist is named by ``trade_key`` so the
        successor's operator can reconcile the transcript against the table.
        """

        class _LeftoverRecorder:
            pending_trade_keys = ("P-1:O-2", "P-1:O-4")

            def flush_pending(self) -> int:
                raise AssertionError("must not drain once ownership is lost")

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner._trade_recorder = _LeftoverRecorder()  # type: ignore[assignment]
        runner._ownership_lost = True

        with capture_logs() as logs:
            runner._flush_pending_trades()

        pending = [e for e in logs if e["event"] == "session.trades_still_pending"]
        assert len(pending) == 1
        assert pending[0]["pending"] == 2
        assert pending[0]["trade_keys"] == ["P-1:O-2", "P-1:O-4"]
        assert "account_id" not in pending[0]

    def test_a_reclaim_observed_during_the_teardown_drain_does_not_schedule_a_stop(self):
        """Review 2026-09-12: the teardown drain runs after the loop has
        stopped; a reclaim seen there sets the flag (so ``_finish_record``
        skips ``mark_stopped``) but must not queue ``node.stop()`` or log a
        ``session.stopped`` that never happens.
        """
        runner_holder = {}

        class _ReclaimingRecorder:
            pending_trade_keys = ()

            def flush_pending(self) -> int:
                runner_holder["runner"]._note_ownership_lost()
                return 0

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner_holder["runner"] = runner
        runner._trade_recorder = _ReclaimingRecorder()  # type: ignore[assignment]
        runner._node = node

        with capture_logs() as logs:
            runner._flush_pending_trades()

        assert runner.ownership_lost is True
        assert node.stopped is False
        assert [e for e in logs if e["event"] == "session.stopped"] == []
        assert [e for e in logs if e["event"] == "session.reclaimed_by_another_process"] != []

    def test_a_raising_flush_is_contained_and_recorded_in_shutdown_problems(self):
        """``flush_pending()`` is designed never to raise, but this call site
        is guarded anyway — the ``stop_degraded_strategies`` precedent
        (decision D4) — so a defect in that guarantee degrades to a recorded
        problem, never a teardown abort.
        """

        class _ExplodingRecorder:
            def flush_pending(self) -> int:
                raise RuntimeError("flush boom")

        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        runner._trade_recorder = _ExplodingRecorder()  # type: ignore[assignment]

        runner._flush_pending_trades()  # must not raise

        assert runner.shutdown_problems == ["flush_pending_trades: RuntimeError"]


def _refuse_an_order(runner: LiveSessionRunner, *, count: int = 1) -> None:
    """Dirty the runner's own tally through the handler the bus would call.

    Deliberately *through* ``handle_order_event`` rather than by reaching into
    the tally's counters: the object under test in the teardown flush is the
    one the runner wired, and a test that set its private state would pass
    against a runner that never subscribed it.
    """
    assert runner._rejection_tally is not None
    for index in range(count):
        event = type(
            "OrderRejected",
            (),
            {
                "client_order_id": f"O-{index}",
                "instrument_id": "NVDA.NASDAQ",
                "strategy_id": "SMACrossover-000",
                "reason": "Order rejected - reason: insufficient margin",
                "reconciliation": False,
            },
        )()
        runner._rejection_tally.handle_order_event(event)


def _refuse_after_subscribe(runner: LiveSessionRunner, *, count: int = 1) -> None:
    """Refuse ``count`` orders the instant the runner's tally exists.

    Wraps ``_phase_subscribe`` — the phase that constructs and subscribes the
    tally — so the refusals land inside the run, after the wiring and before
    the teardown, without the test ever building a tally of its own.
    """
    original = runner._phase_subscribe

    def _subscribe_then_refuse() -> None:
        original()
        _refuse_an_order(runner, count=count)

    runner._phase_subscribe = _subscribe_then_refuse  # type: ignore[method-assign]


class TestTheTeardownFlushesTheRejectionTally:
    """Story 3.7, Task 3.2 — the end-of-run window, closed the same way
    ``_flush_contained_failures`` closes it for a contained strategy.

    The steady-state tick is the routine path, but a refusal that arrives in
    the last interval before a stop would otherwise never reach the row: the
    ``-> stopped`` transition makes that permanent, because the service
    refuses writes against a non-``running`` row by design. So the runner's
    ``finally`` writes once more, **before** ``mark_stopped``, while the row
    is still ours.
    """

    def test_a_refusal_after_the_last_tick_reaches_the_record_before_mark_stopped(self):
        """The heartbeat never ticks in these tests (``_never_sleeps``), so the
        only route this summary has to the record is the teardown flush.
        """
        record = SpyRecord()
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, record=record)
        _refuse_after_subscribe(runner, count=2)

        runner.run()

        (written,) = record.rejections
        assert written["rejected"] == 2
        assert written["consecutive"] == 2
        assert written["last_instrument_id"] == "NVDA.NASDAQ"
        # Ordering is the point: the write lands while the row still reads
        # `running` — after `mark_stopped` the service would refuse it.
        flush_index = record.calls.index("record_order_rejections")
        assert flush_index < record.calls.index("mark_stopped")

    def test_a_clean_run_writes_nothing_at_teardown(self):
        """The anti-tautology twin: no refusal, no round trip."""
        record = SpyRecord()
        runner = _runner(TestLiveNode(run_seconds=0.01), record=record)

        runner.run()

        assert record.rejections == []
        assert "record_order_rejections" not in record.calls

    def test_the_flush_is_skipped_when_ownership_is_already_lost(self):
        """The remaining facts belong in the successor's log, not its row —
        the ``_flush_contained_failures`` short-circuit, verbatim.
        """
        record = SpyRecord()
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, record=record)
        _refuse_after_subscribe(runner)
        runner._ownership_lost = True

        runner.run()

        assert record.rejections == []

    def test_a_raising_flush_does_not_replace_the_runs_outcome(self):
        """AR42 on the flush path: the teardown behind it — ``mark_stopped``
        above all — must still run.
        """
        record = SpyRecord()
        record.rejection_raises = RuntimeError("postgres is down")
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, record=record)
        _refuse_after_subscribe(runner)

        with capture_logs() as logs:
            runner.run()  # must not raise

        assert record.calls[-1] == "mark_stopped"
        assert [e for e in logs if e["event"] == "session.rejection_record_failed"] != []

    def test_a_reclaim_during_the_flush_marks_ownership_lost(self):
        """A reclaim is not a hiccup: it stops the flush and is recorded, the
        way the contained-failure flush handles the same refusal.
        """
        from src.core.live_session_record import SessionReclaimedError

        record = SpyRecord()
        record.rejection_raises = SessionReclaimedError("taken by another process")
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, record=record)
        _refuse_after_subscribe(runner)

        runner.run()  # must not raise

        assert runner.ownership_lost is True

    def test_the_runner_exposes_the_final_snapshot_for_the_cli(self):
        """Decision D-H's in-process half: ``live start`` reads this after
        ``run()`` returns, so an operator watching the stop is told what was
        refused without going to the database.
        """
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node)
        _refuse_after_subscribe(runner, count=3)

        runner.run()

        snapshot = runner.order_rejections
        assert snapshot is not None
        assert snapshot.rejected == 3
        assert snapshot.consecutive == 3
