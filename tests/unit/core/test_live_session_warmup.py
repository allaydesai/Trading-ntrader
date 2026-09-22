"""Story 4.4 — the runner's warm-up watch (D-B, D-C, D-D, D-E).

Unit tier, stub strategies only: ``WarmupWatch`` is duck-typed and never
imports ``nautilus_trader``, so every branch is reachable without an engine.
The engine-level facts it is built on are pinned elsewhere —
``tests/component/core/test_strategy_warmup_engine.py`` (real ``DataEngine``)
and the story's Measured facts F2/F6/F12 — and restated here only where a test
depends on them:

- F2: the IB adapter can finish a history request **without ever calling the
  strategy's callback** (missing contract, empty or timed-out history, a
  same-second duplicate). A strategy whose ``subscribe_bars`` lives in that
  callback then sits silent for the whole run — the watch's deadline exists
  for exactly this.
- F6: a raise on the response path shuts the **whole node** down, so the
  wrapped callback must never let one out.
- F12: in a synchronous engine the callback runs *before* ``request_bars``
  returns its id, so bookkeeping keyed on the returned id would miss it.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.core.exit_outcome import LiveCheckOutcome
from src.core.live_session_warmup import (
    COMPLETED_EVENT,
    DISCARDED_EVENT,
    FAILED_EVENT,
    ON_POLL_INTERVAL_SECONDS,
    SKIPPED_EVENT,
    WARMUP_DEADLINE_MARGIN_SECONDS,
    WARMUP_POLL_SECONDS,
    WarmupFailedError,
    WarmupWatch,
    warmup_deadline_seconds,
)

pytestmark = pytest.mark.unit

BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
DEADLINE = 75.0


class _Timeline:
    """A shared, ordered record of log lines and strategy actions."""

    def __init__(self) -> None:
        self.entries: list[tuple] = []

    def events(self) -> list[str]:
        return [entry[1] for entry in self.entries]


class _Log:
    def __init__(self, timeline: _Timeline) -> None:
        self._timeline = timeline

    def _record(self, level: str, event: str, **fields) -> None:
        self._timeline.entries.append((level, event, fields))

    def info(self, event: str, **fields) -> None:
        self._record("info", event, **fields)

    def warning(self, event: str, **fields) -> None:
        self._record("warning", event, **fields)

    def error(self, event: str, **fields) -> None:
        self._record("error", event, **fields)


class _Indicator:
    def __init__(self, name: str, initialized: bool) -> None:
        self.name = name
        self.initialized = initialized

    def __repr__(self) -> str:
        return self.name


class _Strategy:
    """``request_bars`` with the real parameter order, answering on demand.

    ``answer="sync"`` answers inside the call, before it returns — the
    synchronous-engine shape (F12). ``"later"`` keeps the callback for the test
    to fire. ``"never"`` is F2's silent adapter.
    """

    def __init__(
        self, timeline: _Timeline, *, answer: str = "sync", warm: bool = True, raises=None
    ) -> None:
        self._timeline = timeline
        self._answer = answer
        self._raises = raises
        self.registered_indicators = [_Indicator("SMA(3)", True), _Indicator("SMA(5)", warm)]
        self.requests: list[dict] = []
        self.callbacks: list = []

    def request_bars(
        self,
        bar_type,
        start,
        end=None,
        limit=0,
        client_id=None,
        callback=None,
        update_catalog=False,
        params=None,
    ):
        self.requests.append({"bar_type": bar_type, "start": start, "callback": callback})
        self.callbacks.append(callback)
        if self._answer == "sync" and callback is not None:
            callback("request-1")
        return "request-1"

    def indicators_initialized(self) -> bool:
        return all(indicator.initialized for indicator in self.registered_indicators)

    def on_history_loaded(self, request_id) -> None:
        if self._raises is not None:
            raise self._raises
        self._timeline.entries.append(("strategy", "subscribe_bars", {}))

    def on_start(self) -> None:
        """What both built-ins do: request history with a keyword callback."""
        self.request_bars(
            BAR_TYPE, start="2026-09-17T13:25:00+00:00", callback=self.on_history_loaded
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _watch(timeline: _Timeline, clock: _Clock | None = None) -> WarmupWatch:
    clock = clock or _Clock()

    async def sleeper(seconds: float) -> None:
        clock.now += seconds
        await asyncio.sleep(0)

    return WarmupWatch(log=_Log(timeline), deadline_seconds=DEADLINE, clock=clock, sleeper=sleeper)


def _never_stopped() -> bool:
    return False


def _instrumented(timeline, **strategy_kwargs):
    watch = _watch(timeline)
    strategy = _Strategy(timeline, **strategy_kwargs)
    watch.instrument(strategy, spec_strategy_id="sma_crossover")
    return watch, strategy


class TestWarmupCompletedIsLoggedBeforeTheStrategySubscribes:
    """D-C / AC #4 — a property of the call stack, not of a race."""

    async def test_the_milestone_precedes_the_strategys_own_callback(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        strategy.on_start()
        settled = await watch.settle("sma_crossover", stop_requested=_never_stopped)

        assert settled is True
        assert timeline.events() == [COMPLETED_EVENT, "subscribe_bars"]

    async def test_the_milestone_names_what_was_warmed(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        strategy.on_start()
        await watch.settle("sma_crossover", stop_requested=_never_stopped)

        level, _event, fields = timeline.entries[0]
        assert level == "info"
        assert fields["strategy_id"] == "sma_crossover"
        assert fields["bar_type"] == BAR_TYPE
        assert fields["requested_from"] == "2026-09-17T13:25:00+00:00"
        assert fields["indicators_initialized"] is True
        assert fields["not_initialized"] == []
        assert fields["elapsed_ms"] >= 0

    async def test_cold_indicators_are_reported_loudly_but_still_subscribe(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, warm=False)

        strategy.on_start()
        await watch.settle("sma_crossover", stop_requested=_never_stopped)

        level, event, fields = timeline.entries[0]
        assert (level, event) == ("warning", COMPLETED_EVENT)
        assert fields["indicators_initialized"] is False
        assert fields["not_initialized"] == ["SMA(5)"]
        assert timeline.events()[-1] == "subscribe_bars"

    async def test_an_asynchronous_answer_is_waited_for(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="later")
        strategy.on_start()

        async def answer_soon():
            await asyncio.sleep(0)
            strategy.callbacks[0]("request-1")

        answering = asyncio.ensure_future(answer_soon())
        settled = await watch.settle("sma_crossover", stop_requested=_never_stopped)
        await answering

        assert settled is True
        assert timeline.events() == [COMPLETED_EVENT, "subscribe_bars"]


class TestTheWrapperBindsTheRealSignature:
    def test_a_positional_callback_is_wrapped_too(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        strategy.request_bars(BAR_TYPE, "start", None, 0, None, strategy.on_history_loaded)

        assert timeline.events() == [COMPLETED_EVENT, "subscribe_bars"]

    def test_the_arguments_reach_the_base_method_unchanged(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        strategy.request_bars(BAR_TYPE, start="start", limit=7, callback=strategy.on_history_loaded)

        (request,) = strategy.requests
        assert request["bar_type"] == BAR_TYPE and request["start"] == "start"
        assert request["callback"] is not strategy.on_history_loaded, "the callback was not wrapped"

    def test_a_request_with_no_callback_still_completes(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        strategy.request_bars(BAR_TYPE, start="start")

        assert timeline.events() == [COMPLETED_EVENT]

    def test_instrumenting_twice_does_not_double_wrap(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)
        watch.instrument(strategy, spec_strategy_id="sma_crossover")

        strategy.on_start()

        assert timeline.events().count(COMPLETED_EVENT) == 1

    def test_a_request_that_raises_is_not_left_pending(self):
        """A raise out of ``request_bars`` is ``on_start``'s failure — the
        runner's existing start-failure path owns it, not the deadline."""
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _Strategy(timeline)

        def refusing(*args, **kwargs):
            raise ValueError("start is in the future")

        strategy.request_bars = refusing
        watch.instrument(strategy, spec_strategy_id="sma_crossover")

        with pytest.raises(ValueError):
            strategy.on_start()
        assert watch.pending("sma_crossover") == 0


class TestAStrategyThatRequestsNothingIsSkipped:
    async def test_no_request_logs_skipped_and_settles(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        settled = await watch.settle("sma_crossover", stop_requested=_never_stopped)

        assert settled is True
        assert timeline.events() == [SKIPPED_EVENT]

    async def test_a_strategy_never_instrumented_is_skipped_too(self):
        timeline = _Timeline()
        watch = _watch(timeline)

        settled = await watch.settle("custom", stop_requested=_never_stopped)

        assert settled is True
        assert timeline.events() == [SKIPPED_EVENT]


class TestAWarmupThatNeverAnswersIsBoundedAndLoud:
    """D-D (ruled A) / AC #7 — F2's silent adapter, made loud."""

    async def test_the_deadline_raises_and_logs_failed(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="never")
        strategy.on_start()

        with pytest.raises(WarmupFailedError) as raised:
            await watch.settle("sma_crossover", stop_requested=_never_stopped)

        assert timeline.events() == [FAILED_EVENT]
        level, _event, fields = timeline.entries[0]
        assert level == "error"
        assert fields["reason"] == "no_response"
        assert fields["bar_type"] == BAR_TYPE
        assert fields["waited_seconds"] >= DEADLINE
        assert "sma_crossover" in str(raised.value)

    async def test_the_wait_is_not_shorter_than_the_deadline(self):
        timeline = _Timeline()
        clock = _Clock()
        watch = _watch(timeline, clock)
        strategy = _Strategy(timeline, answer="never")
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()
        started = clock.now

        with pytest.raises(WarmupFailedError):
            await watch.settle("sma_crossover", stop_requested=_never_stopped)

        assert clock.now - started >= DEADLINE

    async def test_a_late_answer_is_discarded_and_never_subscribes(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="never")
        strategy.on_start()
        with pytest.raises(WarmupFailedError):
            await watch.settle("sma_crossover", stop_requested=_never_stopped)

        strategy.callbacks[0]("request-1")

        assert timeline.events() == [FAILED_EVENT, DISCARDED_EVENT]
        assert "subscribe_bars" not in timeline.events()


class TestARaisingHistoryCallbackIsContained:
    """D-E — F6 would otherwise shut the whole node down."""

    async def test_the_raise_never_leaves_the_callback(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, raises=RuntimeError("boom"))

        strategy.on_start()  # the synchronous answer runs the raising callback

        assert timeline.events() == [COMPLETED_EVENT, FAILED_EVENT]
        _level, _event, fields = timeline.entries[1]
        assert fields["reason"] == "callback_raised"
        assert fields["error_type"] == "RuntimeError"

    async def test_settle_then_fails_the_strategy(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, raises=RuntimeError("boom"))
        strategy.on_start()

        with pytest.raises(WarmupFailedError):
            await watch.settle("sma_crossover", stop_requested=_never_stopped)

    def test_keyboard_interrupt_still_propagates(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, raises=KeyboardInterrupt())

        with pytest.raises(KeyboardInterrupt):
            strategy.on_start()


class TestAStopEndsTheWait:
    async def test_a_requested_stop_returns_false_and_logs_no_failure(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="never")
        strategy.on_start()

        settled = await watch.settle("sma_crossover", stop_requested=lambda: True)

        assert settled is False
        assert FAILED_EVENT not in timeline.events()

    async def test_a_response_after_the_stop_does_not_subscribe(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="never")
        strategy.on_start()
        await watch.settle("sma_crossover", stop_requested=lambda: True)

        strategy.callbacks[0]("request-1")

        assert "subscribe_bars" not in timeline.events()


class TestRequestsAfterWarmupPassThrough:
    async def test_a_mid_session_request_is_not_a_warmup(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)
        strategy.on_start()
        await watch.settle("sma_crossover", stop_requested=_never_stopped)
        timeline.entries.clear()

        strategy.request_bars(BAR_TYPE, start="later", callback=strategy.on_history_loaded)

        assert timeline.events() == ["subscribe_bars"]
        assert strategy.requests[-1]["callback"] == strategy.on_history_loaded


class TestTheDeadline:
    def test_the_deadline_is_strictly_after_the_adapters_own_timeout(self):
        """The watch must never abandon a request the adapter is still
        waiting on: the adapter gives up at ``ibkr_request_timeout``."""
        settings = SimpleNamespace(ibkr_request_timeout=60)

        assert warmup_deadline_seconds(settings) == 60 + WARMUP_DEADLINE_MARGIN_SECONDS
        assert WARMUP_DEADLINE_MARGIN_SECONDS > 0

    def test_two_strategies_both_timing_out_still_trade_by_the_open(self):
        """AC #6's broker-double half: a session started at 09:25 ET with the
        default timeout and two strategies whose warm-ups both fail is past
        its startup by 09:27:30 — inside the five minutes NFR3 allows."""
        settings = SimpleNamespace(ibkr_request_timeout=60)

        assert 2 * warmup_deadline_seconds(settings) < 5 * 60


class TestWarmupFailedErrorIsMarked:
    """D-J — the D3 marker protocol, rather than a new ``UNMARKED`` entry."""

    def test_it_carries_both_markers(self):
        assert WarmupFailedError.exit_outcome is LiveCheckOutcome.ERROR
        assert WarmupFailedError.operator_safe_message is True


class _TwoRequestStrategy(_Strategy):
    """A custom-shaped strategy issuing two history requests (code review 2026-09-22)."""

    def on_start(self) -> None:
        self.request_bars(BAR_TYPE, start="first", callback=self.on_history_loaded)
        self.request_bars(BAR_TYPE, start="second", callback=self.on_history_loaded)


class TestNothingLeftArmedAfterTheWait:
    """Code review 2026-09-22 — every pending request is abandoned when the
    wait ends, however it ends, so no late answer subscribes a contained
    strategy."""

    async def test_a_failure_on_one_request_abandons_its_sibling(self):
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _TwoRequestStrategy(timeline, answer="later")
        watch.instrument(strategy, spec_strategy_id="custom")
        strategy.on_start()
        strategy._raises = RuntimeError("boom")
        strategy.callbacks[0]("request-1")  # raises inside -> this request failed

        with pytest.raises(WarmupFailedError):
            await watch.settle("custom", stop_requested=_never_stopped)
        strategy._raises = None
        strategy.callbacks[1]("request-2")  # the sibling answers late

        assert timeline.events()[-1] == DISCARDED_EVENT
        assert "subscribe_bars" not in timeline.events()

    def test_a_faulted_strategy_never_runs_its_callback(self):
        """``on_start`` raised after requesting, so ``settle`` never ran, and
        the runner then faulted the strategy — a late answer is discarded."""
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline, answer="later")
        strategy.on_start()
        strategy.is_faulted = True

        strategy.callbacks[0]("request-1")

        assert timeline.events() == [DISCARDED_EVENT]


class TestRequestsCompareByIdentity:
    async def test_removing_a_failed_call_keeps_its_identical_twin(self):
        """Two identical calls in one clock tick are equal by value; the
        second's base raises, and removing it by value used to drop the first
        — leaving ``settle`` waiting on a request that never existed."""
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _Strategy(timeline, answer="later")
        base = strategy.request_bars
        calls = {"n": 0}

        def second_raises(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("refused")
            return base(*args, **kwargs)

        strategy.request_bars = second_raises
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()
        with pytest.raises(ValueError):
            strategy.on_start()
        strategy.callbacks[0]("request-1")

        assert await watch.settle("sma_crossover", stop_requested=_never_stopped) is True


class TestTheWrapperPassesThroughWhatItCannotBind:
    def test_too_many_positional_arguments_reach_the_real_method_untouched(self):
        """The real ``request_bars`` refuses a ninth positional argument itself;
        the wrapper must not silently drop it (code review 2026-09-22)."""
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)

        with pytest.raises(TypeError):
            strategy.request_bars(BAR_TYPE, "s", None, 0, None, None, False, None, "extra")
        assert watch.pending("sma_crossover") == 0


class TestAStrategyWithNoIndicators:
    def test_the_milestone_is_info_and_says_nothing_is_registered(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)
        strategy.registered_indicators = []

        strategy.on_start()

        level, event, fields = timeline.entries[0]
        assert (level, event) == ("info", COMPLETED_EVENT)
        assert fields["indicators_initialized"] is None


class TestTheWaitIsResponsive:
    async def test_a_stop_is_noticed_within_one_poll_interval(self):
        """AC #7's timing clause, asserted rather than implied by the loop."""
        timeline = _Timeline()
        clock = _Clock()
        watch = _watch(timeline, clock)
        strategy = _Strategy(timeline, answer="never")
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()
        stop_at = clock.now + 2.0

        settled = await watch.settle("sma_crossover", stop_requested=lambda: clock.now >= stop_at)

        assert settled is False
        assert clock.now - stop_at <= WARMUP_POLL_SECONDS

    async def test_the_poll_hook_runs_about_once_a_second(self):
        timeline = _Timeline()
        clock = _Clock()
        polls: list[float] = []

        async def sleeper(seconds: float) -> None:
            clock.now += seconds
            await asyncio.sleep(0)

        watch = WarmupWatch(
            log=_Log(timeline),
            deadline_seconds=DEADLINE,
            on_poll=lambda: polls.append(clock.now),
            clock=clock,
            sleeper=sleeper,
        )
        strategy = _Strategy(timeline, answer="never")
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()
        stop_at = clock.now + 5.0

        await watch.settle("sma_crossover", stop_requested=lambda: clock.now >= stop_at)

        assert 4 <= len(polls) <= 5
        gaps = [later - earlier for earlier, later in zip(polls, polls[1:], strict=False)]
        assert all(gap >= ON_POLL_INTERVAL_SECONDS - 1e-9 for gap in gaps)

    def test_settle_blocking_runs_the_given_loop(self):
        timeline = _Timeline()
        watch, strategy = _instrumented(timeline)
        strategy.on_start()
        loop = asyncio.new_event_loop()
        try:
            assert watch.settle_blocking(loop, "sma_crossover") is True
        finally:
            loop.close()
