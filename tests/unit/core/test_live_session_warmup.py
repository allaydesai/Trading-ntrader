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
    SEAM_DUPLICATE_EVENT,
    SEAM_GAP_EVENT,
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


class TestALoggingFailureNeverFailsTheWarmup:
    """Code review of PR #35: every other module's ``_emit`` never raises. The
    history callback's own records sat inside the ``try`` whose ``except``
    marks the request ``failed``, so a failing log sink contained a strategy
    whose history had in fact arrived."""

    class _RaisingLog(_Log):
        def info(self, event: str, **fields) -> None:
            raise RuntimeError("sink down")

        def warning(self, event: str, **fields) -> None:
            raise RuntimeError("sink down")

        def error(self, event: str, **fields) -> None:
            raise RuntimeError("sink down")

    def _watch_with_raising_log(self, timeline: _Timeline) -> WarmupWatch:
        clock = _Clock()

        async def sleeper(seconds: float) -> None:
            clock.now += seconds
            await asyncio.sleep(0)

        return WarmupWatch(
            log=self._RaisingLog(timeline), deadline_seconds=DEADLINE, clock=clock, sleeper=sleeper
        )

    async def test_the_milestone_sink_raising_still_completes_and_subscribes(self):
        timeline = _Timeline()
        watch = self._watch_with_raising_log(timeline)
        strategy = _Strategy(timeline)
        watch.instrument(strategy, spec_strategy_id="sma_crossover")

        strategy.on_start()
        settled = await watch.settle("sma_crossover", stop_requested=_never_stopped)

        assert settled is True
        assert timeline.events() == ["subscribe_bars"]

    async def test_the_failure_sink_raising_still_reports_a_raising_callback_as_failed(self):
        """The ``except`` that records ``callback_raised`` must not itself raise
        into the response queue (F6) when the sink is down."""
        timeline = _Timeline()
        watch = self._watch_with_raising_log(timeline)
        strategy = _Strategy(timeline, raises=RuntimeError("boom"))
        watch.instrument(strategy, spec_strategy_id="sma_crossover")

        strategy.on_start()

        with pytest.raises(WarmupFailedError):
            await watch.settle("sma_crossover", stop_requested=_never_stopped)


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

    def test_the_default_deadline_leaves_room_for_two_strategies_before_the_open(self):
        """AC #6's broker-double half, the constant: with the default timeout
        one deadline is 75 s, so two in series end by 09:27:30 from a 09:25
        start — inside the five minutes NFR3 allows. That the runner really
        spends at most one deadline per strategy, in series, is the runner-tier
        test in ``test_session_runner_warmup.py`` (PR #35 code review, P6)."""
        from src.config import IBKRSettings

        # The model's own default, not a literal: a raised default fails here.
        default_timeout = IBKRSettings.model_fields["ibkr_request_timeout"].default
        settings = SimpleNamespace(ibkr_request_timeout=default_timeout)

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


# ---------------------------------------------------------------------------
# Story 4.5 (D-F, PO ruling B) — the history/live seam's own branches. The
# engine-level proof is ``tests/component/core/test_warmup_seam.py``.
# ---------------------------------------------------------------------------


def _bar(ts_event: int, bar_type: str = BAR_TYPE):
    return SimpleNamespace(bar_type=bar_type, ts_event=ts_event)


class _SeamStrategy(_Strategy):
    """``_Strategy`` plus the ``Actor`` bar surface the seam wraps: the
    history batch goes to ``handle_bars`` before the callback (the response
    path's order), live bars to ``handle_bar``, and ``cache.bars`` holds what
    ``DataEngine`` cached while the request was in flight."""

    def __init__(self, timeline, *, history=(), in_flight=(), **kwargs) -> None:
        super().__init__(timeline, **kwargs)
        self.delivered: list[int] = []
        self._history = list(history)
        in_flight = list(in_flight)
        self.cache = SimpleNamespace(
            bars=lambda bar_type: [bar for bar in in_flight if bar.bar_type == bar_type]
        )

    def handle_bars(self, bars) -> None:
        pass

    def handle_bar(self, bar) -> None:
        self.delivered.append(getattr(bar, "ts_event", bar))

    def request_bars(self, bar_type, start, end=None, limit=0, client_id=None, callback=None,
                     update_catalog=False, params=None):  # fmt: skip
        self.handle_bars([bar for bar in self._history if bar.bar_type == bar_type])
        return super().request_bars(bar_type, start, end, limit, client_id, callback)


OTHER = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"


def _seam(timeline, *, watch=None, **kwargs):
    watch = watch or _watch(timeline)
    strategy = _SeamStrategy(timeline, **kwargs)
    watch.instrument(strategy, spec_strategy_id="sma_crossover")
    strategy.on_start()
    return strategy


class TestTheSeamDropsWhatTheHistoryHeld:
    def test_a_bar_at_or_before_the_history_end_is_dropped_and_said_so_once(self):
        """D-F (B): the record is once per strategy; every duplicate is dropped."""
        timeline = _Timeline()
        strategy = _seam(timeline, history=[_bar(8), _bar(10)])

        strategy.handle_bar(_bar(9))
        strategy.handle_bar(_bar(10))
        strategy.handle_bar(_bar(11))

        assert strategy.delivered == [11]
        dropped = [e for e in timeline.entries if e[1] == SEAM_DUPLICATE_EVENT]
        assert [(e[0], e[2]["ts_event"]) for e in dropped] == [
            ("info", "1970-01-01T00:00:00+00:00")
        ]

    def test_another_bar_type_is_never_filtered(self):
        strategy = _seam(_Timeline(), history=[_bar(10)])

        strategy.handle_bar(_bar(5, OTHER))

        assert strategy.delivered == [5]

    async def test_a_history_after_the_warmup_settles_moves_no_watermark(self):
        """Only the warm-up is the seam: a later request's history is not."""
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _seam(timeline, watch=watch, history=[_bar(10)])
        await watch.settle("sma_crossover", stop_requested=_never_stopped)

        strategy.handle_bars([_bar(50)])
        strategy.handle_bar(_bar(20))

        assert strategy.delivered == [20]

    async def test_a_warmup_that_never_answers_disarms_when_the_wait_ends(self):
        """Code review 2026-09-28: the seam used to disarm only on a successful
        answer, so after a stalled or stopped warm-up a later history kept
        moving the watermark."""
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _seam(timeline, watch=watch, history=[_bar(10)], answer="never")
        await watch.settle("sma_crossover", stop_requested=lambda: True)

        strategy.handle_bars([_bar(50)])
        strategy.handle_bar(_bar(20))

        assert strategy.delivered == [20]

    def test_a_second_warmup_request_records_its_own_watermark(self):
        """Code review 2026-09-28: the first answer used to disarm the seam, so
        a second bar type's history was never recorded."""
        timeline = _Timeline()
        strategy = _seam(timeline, history=[_bar(10), _bar(30, OTHER)])
        strategy.request_bars(OTHER, "2026-09-17T13:25:00+00:00", callback=lambda _: None)

        strategy.handle_bar(_bar(30, OTHER))
        strategy.handle_bar(_bar(31, OTHER))

        assert strategy.delivered == [31]

    def test_a_bar_it_cannot_read_is_delivered_not_raised(self):
        """Containment: any failure of the seam's own reads delivers the bar
        exactly as before this story — nothing raises into the message bus."""
        strategy = _seam(_Timeline(), history=[_bar(10)])
        unreadable = SimpleNamespace(bar_type=BAR_TYPE)  # no ts_event

        strategy.handle_bar(unreadable)

        assert strategy.delivered == [unreadable]

    def test_a_raising_log_still_drops_the_duplicate(self):
        class _RaisingLog(_Log):
            def info(self, event, **fields):
                raise RuntimeError("sink down")

        timeline = _Timeline()
        watch = WarmupWatch(log=_RaisingLog(timeline), deadline_seconds=DEADLINE)
        strategy = _SeamStrategy(timeline, history=[_bar(10)])
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()

        strategy.handle_bar(_bar(10))

        assert strategy.delivered == []


class TestTheSeamNamesWhatTheStrategyMissed:
    def test_one_warning_per_bar_cached_while_the_request_was_in_flight(self):
        """In ascending order, ``ts_event`` and the history's end as ISO UTC."""
        second = 1_000_000_000
        timeline = _Timeline()
        strategy = _seam(
            timeline,
            history=[_bar(10 * second)],
            in_flight=[_bar(12 * second), _bar(11 * second), _bar(9 * second)],
        )

        gaps = [e for e in timeline.entries if e[1] == SEAM_GAP_EVENT]
        assert [e[0] for e in gaps] == ["warning", "warning"]
        assert [e[2]["ts_event"] for e in gaps] == [
            "1970-01-01T00:00:11+00:00",
            "1970-01-01T00:00:12+00:00",
        ]
        assert {e[2]["last_history_ts_event"] for e in gaps} == {"1970-01-01T00:00:10+00:00"}
        assert timeline.events().index(COMPLETED_EVENT) < timeline.events().index(SEAM_GAP_EVENT)
        assert timeline.events()[-1] == "subscribe_bars", "the strategy still subscribed"
        assert strategy.delivered == [], "a missed bar was replayed"

    def test_a_cache_that_raises_costs_nothing(self):
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _SeamStrategy(timeline, history=[_bar(10)])

        def _raising(bar_type):
            raise RuntimeError("cache down")

        strategy.cache = SimpleNamespace(bars=_raising)
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()

        assert timeline.events() == [COMPLETED_EVENT, "subscribe_bars"]

    def test_an_empty_history_names_nothing(self):
        timeline = _Timeline()
        _seam(timeline, history=[], in_flight=[_bar(12)])

        assert SEAM_GAP_EVENT not in timeline.events()

    def test_only_the_bar_type_whose_history_just_answered_is_reported(self):
        """Code review 2026-09-28: a later answer used to re-report every bar
        type's gap."""
        timeline = _Timeline()
        strategy = _seam(timeline, history=[_bar(10), _bar(30, OTHER)], in_flight=[_bar(12)])
        before = timeline.events().count(SEAM_GAP_EVENT)

        strategy.request_bars(OTHER, "2026-09-17T13:25:00+00:00", callback=lambda _: None)

        assert before == 1
        assert timeline.events().count(SEAM_GAP_EVENT) == 1, "BAR_TYPE's gap was re-reported"

    def test_a_bar_the_strategy_did_receive_is_not_named_missing(self):
        """A strategy that subscribed before its history answered (possible for
        a custom strategy) received the in-flight bar; it is not a gap."""
        timeline = _Timeline()
        watch = _watch(timeline)
        strategy = _SeamStrategy(timeline, history=[_bar(10)], in_flight=[_bar(12)], answer="later")
        watch.instrument(strategy, spec_strategy_id="sma_crossover")
        strategy.on_start()
        strategy.handle_bar(_bar(12))  # delivered while the request was still in flight

        strategy.callbacks[0]("request-1")

        assert SEAM_GAP_EVENT not in timeline.events()
        assert strategy.delivered == [12]
