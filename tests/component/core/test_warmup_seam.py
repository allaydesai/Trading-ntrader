"""Story 4.5 (D-F, PO ruling B) — the history/live seam, deduplicated and made visible.

Component tier: Story 4.4's real ``DataEngine`` / ``MockMarketDataClient``
harness with a real strategy, instrumented by a real ``WarmupWatch`` in the
runner's window. The seam, deferred here by the PO from Story 4.4's review
(``deferred-work.md:3153-3166``): a strategy joins the bar stream only in its
history callback, so

- a bar the adapter publishes **after** the callback but that the history
  already held (IB publishes bar X on X+1's first update) is fed to the
  registered indicators twice — now **dropped**, with a
  ``warmup.seam_duplicate_dropped`` record;
- a bar published **between** the request and the callback is never seen —
  now named, one ``warmup.seam_gap`` WARNING per missed bar. It is **not**
  replayed (the PO's ruling): a replayed bar could signal on a stale price.

Measured before these tests were written (Task 1.4): ``Cache.add_bars`` keeps
only history *newer* than the latest cached bar, so a live bar cached while the
request was in flight hides the whole history window from ``cache.bar()`` — the
watermark therefore comes from an instance-level ``handle_bars`` wrapper, which
sees the history batch before the callback; live bars are cached by
``DataEngine._handle_bar`` whether or not the strategy has subscribed, so the
gap is readable from the cache inside the callback.
"""

import pytest
import structlog
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

from src.core.live_session_warmup import (
    COMPLETED_EVENT,
    SEAM_DUPLICATE_EVENT,
    SEAM_GAP_EVENT,
    WarmupWatch,
)
from tests.component.core.test_strategy_warmup_engine import (
    FLAT,
    MINUTE,
    NOW,
    SLOW,
    STRATEGIES,
    _bar,
    _Harness,
    _history,
)

pytestmark = pytest.mark.component

#: The history ends one minute before ``NOW`` (``_history``'s shape).
LAST_HISTORY = NOW - MINUTE


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


def _watched(make, *, watch: bool = True) -> _Harness:
    watcher = WarmupWatch(log=structlog.get_logger("test"), deadline_seconds=75.0)
    return _Harness(make(), history=_history(FLAT), watch=watcher if watch else None)


def _counts(harness: _Harness) -> list[int]:
    return [indicator.count for indicator in harness.strategy.registered_indicators]


def _events(logs, name: str) -> list[dict]:
    return [entry for entry in logs if entry["event"] == name]


@pytest.mark.parametrize("make", STRATEGIES)
class TestAHistoryBarRepublishedAfterTheCallbackIsDropped:
    def test_it_reaches_the_indicators_once(self, make):
        harness = _watched(make)
        harness.start()

        with capture_logs() as logs:
            harness.engine.process(_bar(LAST_HISTORY, 100.0))

        assert _counts(harness) == [SLOW, SLOW], "the history's last bar was counted twice"
        assert harness.seen_at_bar == [], "on_bar ran on a duplicate"
        (dropped,) = _events(logs, SEAM_DUPLICATE_EVENT)
        assert dropped["strategy_id"] == "spec"
        assert dropped["ts_event"] == dropped["last_history_ts_event"]

    def test_the_next_live_bar_still_arrives(self, make):
        harness = _watched(make)
        harness.start()
        harness.engine.process(_bar(LAST_HISTORY, 100.0))

        harness.live(1, 100.0)

        assert _counts(harness) == [SLOW + 1, SLOW + 1]
        assert harness.seen_at_bar == [True]

    def test_without_the_watch_it_is_counted_twice(self, make):
        """The anti-tautology twin: the harness, unwatched, double-counts —
        which is the defect, measured (Task 1.4)."""
        harness = _watched(make, watch=False)
        harness.start()

        harness.engine.process(_bar(LAST_HISTORY, 100.0))

        assert _counts(harness) == [SLOW + 1, SLOW + 1]


@pytest.mark.parametrize("make", STRATEGIES)
class TestABarPublishedWhileTheRequestWasInFlightIsNamed:
    def test_one_warning_per_missed_bar_and_no_replay(self, make):
        harness = _watched(make)
        # Published before the strategy has subscribed — the request is in flight.
        harness.engine.process(_bar(NOW, 111.0))
        harness.engine.process(_bar(NOW + MINUTE, 112.0))

        with capture_logs() as logs:
            harness.start()

        gaps = _events(logs, SEAM_GAP_EVENT)
        assert [g["log_level"] for g in gaps] == ["warning", "warning"]
        assert [g["ts_event"] for g in gaps] == [
            "2023-11-14T22:13:20+00:00",
            "2023-11-14T22:14:20+00:00",
        ]
        assert {g["last_history_ts_event"] for g in gaps} == {"2023-11-14T22:12:20+00:00"}
        assert _counts(harness) == [SLOW, SLOW], "a missed bar was replayed"
        assert harness.seen_at_bar == [] and harness.orders == []

    def test_it_is_named_after_warmup_completed_and_before_the_first_bar(self, make):
        harness = _watched(make)
        harness.engine.process(_bar(NOW, 111.0))

        with capture_logs() as logs:
            harness.start()
            harness.live(2, 100.0)

        events = [e["event"] for e in logs if e["event"] in (COMPLETED_EVENT, SEAM_GAP_EVENT)]
        assert events == [COMPLETED_EVENT, SEAM_GAP_EVENT]
        assert harness.seen_at_bar == [True]


@pytest.mark.parametrize("make", STRATEGIES)
def test_a_clean_seam_logs_neither(make):
    harness = _watched(make)

    with capture_logs() as logs:
        harness.start()
        harness.live(1, 100.0)

    assert _events(logs, SEAM_GAP_EVENT) == [] and _events(logs, SEAM_DUPLICATE_EVENT) == []
    assert _counts(harness) == [SLOW + 1, SLOW + 1]


@pytest.mark.parametrize("make", STRATEGIES)
def test_an_empty_history_filters_nothing(make):
    """A backtest-shaped (empty) answer leaves no watermark: every bar passes,
    exactly as before this story."""
    watcher = WarmupWatch(log=structlog.get_logger("test"), deadline_seconds=75.0)
    harness = _Harness(make(), history=[], watch=watcher)
    harness.start()

    with capture_logs() as logs:
        harness.engine.process(_bar(LAST_HISTORY, 100.0))

    assert _events(logs, SEAM_DUPLICATE_EVENT) == []
    assert _counts(harness) == [1, 1]
