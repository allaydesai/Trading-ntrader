"""Unit tests for the pure health derivation (Story 2.8, AC #1-#5, #7, #9).

``src/core/live_session_health.py`` owns the five-value health vocabulary, the
precedence that decides between them, and the pure ``StatusReport`` builder and
renderers. All of it is primitives-only — no SQLAlchemy, no Nautilus, no
``src.services`` import — which is what lets it be tested here with no
database and survive ``TestImportPurity`` (AC #9).
"""

import ast
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core import live_session_health
from src.core.live_session_health import (
    DEFAULT_BAR_FRESH_AFTER_SECONDS,
    SessionHealth,
    StatusReport,
    build_status_report,
    derive_health,
    render_status,
    status_json_payload,
)
from src.models.session import SessionStatus

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def _ar36_violations(text: str) -> list[str]:
    """AR36's five forbidden lifecycle stems, with the trading-domain carve-out.

    ``closed trade``/``closed_trade_count`` are scrubbed before matching
    because AR36 bans those words *as synonyms for* the lifecycle concept,
    not globally (the Story 1.2 precedent). Extracted from the assertion it
    used to be inlined in so a probe can prove it fires — the scrub is a
    ``str.replace`` running ahead of the scan, and nothing proved it was
    narrower than the scan itself.
    """
    scrubbed = text.lower().replace("closed trade", "").replace("closed_trade_count", "")
    hits: list[str] = []
    for forbidden in ("pause", "halt", "kill", "close", "finalize"):
        hits.extend(re.findall(rf"\b{forbidden}\w*\b", scrubbed))
    return hits


def _derive(
    status=SessionStatus.RUNNING,
    *,
    last_heartbeat_at=None,
    last_bar_at=None,
    runtime_flags=None,
    now=NOW,
    heartbeat_stale_after_seconds=90.0,
    bar_fresh_after_seconds=DEFAULT_BAR_FRESH_AFTER_SECONDS,
):
    return derive_health(
        status,
        last_heartbeat_at,
        last_bar_at,
        runtime_flags,
        now=now,
        heartbeat_stale_after_seconds=heartbeat_stale_after_seconds,
        bar_fresh_after_seconds=bar_fresh_after_seconds,
    )


class TestFiveValuesAndPrecedence:
    """AC #2: exactly five values, evaluated in the pinned order."""

    @pytest.mark.parametrize(
        "status", [SessionStatus.CREATED, SessionStatus.STOPPED, SessionStatus.SEALED]
    )
    def test_a_non_running_status_is_always_stopped_health(self, status):
        """Precedence step 1 — covers created/stopped/sealed, whatever else is set."""
        assert (
            _derive(
                status,
                last_heartbeat_at=_ago(1),
                last_bar_at=_ago(1),
                runtime_flags={"all_failed": True},
            )
            is SessionHealth.STOPPED
        )

    def test_a_stale_heartbeat_outranks_a_degraded_flag(self):
        """Precedence step 2 before step 3 — Judgment call #8."""
        health = _derive(
            last_heartbeat_at=_ago(1000),
            runtime_flags={"all_failed": True},
        )
        assert health is SessionHealth.STALE

    def test_a_degraded_flag_outranks_bar_recency(self):
        """Precedence step 3 before step 4 — the false-green trap's shape."""
        health = _derive(
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            runtime_flags={"all_failed": True},
        )
        assert health is SessionHealth.DEGRADED

    def test_fresh_heartbeat_no_flags_recent_bar_is_trading(self):
        assert (
            _derive(last_heartbeat_at=_ago(1), last_bar_at=_ago(1), runtime_flags=None)
            is SessionHealth.TRADING
        )

    def test_fresh_heartbeat_no_flags_no_recent_bar_is_idle(self):
        assert (
            _derive(last_heartbeat_at=_ago(1), last_bar_at=None, runtime_flags=None)
            is SessionHealth.IDLE
        )


class TestHeartbeatStaleness:
    """AC #2 step 2, matching ``session_service._is_heartbeat_stale`` exactly."""

    def test_a_null_heartbeat_on_a_running_session_is_stale(self):
        assert _derive(last_heartbeat_at=None) is SessionHealth.STALE

    def test_age_exactly_at_the_threshold_reads_fresh_not_stale(self):
        """Strictly ``>``, not ``>=`` — exactly-at-threshold is fresh."""
        health = _derive(last_heartbeat_at=_ago(90.0), heartbeat_stale_after_seconds=90.0)
        assert health is not SessionHealth.STALE

    def test_age_one_second_past_the_threshold_is_stale(self):
        health = _derive(last_heartbeat_at=_ago(90.1), heartbeat_stale_after_seconds=90.0)
        assert health is SessionHealth.STALE

    def test_a_future_dated_heartbeat_clamps_to_fresh(self):
        """``_heartbeat_age_seconds``'s clamp: a future timestamp reads as age 0."""
        future = NOW + timedelta(seconds=3600)
        health = _derive(last_heartbeat_at=future)
        assert health is not SessionHealth.STALE


class TestBarRecency:
    """AC #2 step 4/5 — the ``trading``/``idle`` split."""

    def test_null_last_bar_at_is_idle(self):
        assert _derive(last_heartbeat_at=_ago(1), last_bar_at=None) is SessionHealth.IDLE

    def test_bar_age_exactly_at_the_threshold_is_trading(self):
        """Non-strict ``<=`` — exactly 300.0s old still counts as trading."""
        health = _derive(
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(DEFAULT_BAR_FRESH_AFTER_SECONDS),
        )
        assert health is SessionHealth.TRADING

    def test_bar_age_one_second_past_the_threshold_is_idle(self):
        health = _derive(
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(DEFAULT_BAR_FRESH_AFTER_SECONDS + 1),
        )
        assert health is SessionHealth.IDLE


class TestDegradedCoversBothSenses:
    """AC #4: contained strategies (live writer) and connection-lost (dormant reader)."""

    def test_all_failed_true_is_degraded(self):
        assert (
            _derive(last_heartbeat_at=_ago(1), runtime_flags={"all_failed": True})
            is SessionHealth.DEGRADED
        )

    def test_non_empty_failed_strategies_is_degraded_even_when_all_failed_is_false(self):
        flags = {"all_failed": False, "failed_strategies": [{"spec_strategy_id": "sma"}]}
        assert _derive(last_heartbeat_at=_ago(1), runtime_flags=flags) is SessionHealth.DEGRADED

    def test_the_false_green_is_closed(self):
        """Story 2.7's trap: every strategy dead but the heartbeat/bar keep advancing.

        ``note_bar`` subscribes before any strategy, so a session whose entire
        roster failed still shows a fresh heartbeat and a recent bar. Without
        this branch health would read ``trading`` for a session that cannot
        place an order — the single most load-bearing test in this story.
        """
        health = _derive(
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            runtime_flags={"all_failed": True, "failed_strategies": [{"spec_strategy_id": "s1"}]},
        )
        assert health is SessionHealth.DEGRADED
        assert health is not SessionHealth.TRADING

    def test_a_dormant_connection_lost_at_key_reads_degraded(self):
        """Epic 4 supplies the writer; this story only wires the reader.

        No code in this phase ever sets ``connection_lost_at`` — it is
        pre-planned in ``runtime_flags``'s versioned shape
        (``session_service.py:407-410``) so Epic 4 can start writing it with
        zero changes to this module. Pinned here so that day one, the reader
        already works.
        """
        flags = {"connection_lost_at": "2026-08-24T11:00:00+00:00"}
        assert _derive(last_heartbeat_at=_ago(1), runtime_flags=flags) is SessionHealth.DEGRADED

    def test_unknown_keys_and_any_v_value_do_not_crash(self):
        """Render what is understood, ignore the rest — ``v`` is bumped only on meaning changes."""
        flags = {"v": 999, "a_future_key_this_module_has_never_heard_of": {"nested": True}}
        health = _derive(last_heartbeat_at=_ago(1), last_bar_at=_ago(1), runtime_flags=flags)
        assert health is SessionHealth.TRADING

    def test_an_empty_runtime_flags_dict_is_not_degraded(self):
        assert (
            _derive(last_heartbeat_at=_ago(1), last_bar_at=_ago(1), runtime_flags={})
            is SessionHealth.TRADING
        )


class TestHealthIsDerivedNeverStored:
    """AC #2's closing clause: two calls straddling a threshold, zero writes."""

    def test_two_calls_with_a_stepped_clock_flip_the_answer_with_no_intervening_write(self):
        heartbeat = _ago(0)
        first = derive_health(
            SessionStatus.RUNNING,
            heartbeat,
            None,
            None,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )
        later = NOW + timedelta(seconds=200)
        second = derive_health(
            SessionStatus.RUNNING,
            heartbeat,
            None,
            None,
            now=later,
            heartbeat_stale_after_seconds=90.0,
        )

        assert first is SessionHealth.IDLE
        assert second is SessionHealth.STALE


class TestDriftPin:
    """The ``trading``/``idle`` threshold is pinned equal to the no-bars watchdog's.

    Judgment call #2: no number exists anywhere for this split, and reusing
    the watchdog's 300s beats inventing a second constant for the same
    question. A future correction to one fixes both.
    """

    def test_bar_fresh_threshold_matches_the_no_bars_watchdog(self):
        from src.core.live_session_steady_state import DEFAULT_NO_BARS_AFTER_SECONDS

        assert DEFAULT_BAR_FRESH_AFTER_SECONDS == DEFAULT_NO_BARS_AFTER_SECONDS


class TestBuildStatusReport:
    """The primitives-only report the CLI renders (AC #1, #5, #8)."""

    def _report(self, **overrides) -> StatusReport:
        fields = dict(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )
        fields.update(overrides)
        return build_status_report(**fields)

    def test_last_activity_is_the_greatest_non_null_timestamp(self):
        report = self._report(
            last_heartbeat_at=_ago(5),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
        )
        assert report.last_activity_at == _ago(1)

    def test_last_activity_is_null_for_a_never_started_session(self):
        report = self._report(
            status=SessionStatus.CREATED,
            last_heartbeat_at=None,
            last_bar_at=None,
            last_started_at=None,
            last_stopped_at=None,
            sealed_at=None,
        )
        assert report.last_activity_at is None

    def test_the_report_carries_the_derived_health(self):
        report = self._report(runtime_flags={"all_failed": True})
        assert report.health == SessionHealth.DEGRADED.value

    def test_the_report_status_is_the_bare_enum_value(self):
        report = self._report(status=SessionStatus.RUNNING)
        assert report.status == "running"

    def test_failed_strategies_and_all_failed_are_carried_for_rendering(self):
        flags = {
            "all_failed": True,
            "failed_strategies": [
                {
                    "strategy_id": "SMACrossover-000",
                    "spec_strategy_id": "sma_crossover",
                    "error_type": "DivisionByZero",
                    "handler": "handle_bar",
                    "at": "2026-08-23T14:03:11+00:00",
                    "detail": "[<class 'decimal.DivisionByZero'>]",
                }
            ],
        }
        report = self._report(runtime_flags=flags)
        assert report.all_failed is True
        assert len(report.failed_strategies) == 1
        assert report.failed_strategies[0]["spec_strategy_id"] == "sma_crossover"

    def test_a_stopped_sessions_failures_still_render(self):
        """AC #5: ``runtime_flags`` is cleared only on ``-> running``, so a
        stopped session's failures remain readable."""
        flags = {"all_failed": False, "failed_strategies": [{"spec_strategy_id": "sma"}]}
        report = self._report(status=SessionStatus.STOPPED, runtime_flags=flags)
        assert report.health == SessionHealth.STOPPED.value
        assert len(report.failed_strategies) == 1

    def test_trader_id_is_carried_when_given(self):
        report = self._report(trader_id="PAPER-11111111")
        assert report.trader_id == "PAPER-11111111"


class TestStatusJsonPayload:
    """AC #7: exactly the seven pinned keys, set equality not membership."""

    EXPECTED_KEYS = frozenset(
        {
            "session_id",
            "name",
            "status",
            "closed_trade_count",
            "open_positions",
            "last_activity_at",
            "health",
        }
    )

    def _report(self, **overrides) -> StatusReport:
        fields = dict(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=3,
            open_positions=1,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
            trader_id="PAPER-11111111",
        )
        fields.update(overrides)
        return build_status_report(**fields)

    def test_the_key_set_is_exactly_the_seven_pinned_keys(self):
        payload = status_json_payload(self._report())
        assert set(payload) == self.EXPECTED_KEYS

    def test_trader_id_and_failed_strategies_never_leak_into_json(self):
        """AR29's key set is exact — ``trader_id`` is human-output only."""
        payload = status_json_payload(self._report())
        assert "trader_id" not in payload
        assert "failed_strategies" not in payload
        assert "all_failed" not in payload

    def test_timestamps_are_iso8601_with_a_utc_offset(self):
        payload = status_json_payload(self._report())
        assert payload["last_activity_at"].endswith("+00:00")

    def test_a_never_started_session_renders_null_last_activity(self):
        report = self._report(
            status=SessionStatus.CREATED,
            last_heartbeat_at=None,
            last_bar_at=None,
            last_started_at=None,
            last_stopped_at=None,
            sealed_at=None,
        )
        payload = status_json_payload(report)
        assert payload["last_activity_at"] is None

    def test_money_free_this_story_counts_are_plain_ints(self):
        payload = status_json_payload(self._report(closed_trade_count=5, open_positions=2))
        assert payload["closed_trade_count"] == 5
        assert payload["open_positions"] == 2
        assert isinstance(payload["closed_trade_count"], int)


class TestRenderStatus:
    """AC #1, #5: the human-readable text — vocabulary, and never-started rendering."""

    def _report(self, **overrides) -> StatusReport:
        fields = dict(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )
        fields.update(overrides)
        return build_status_report(**fields)

    def test_a_never_started_session_renders_never_for_last_activity(self):
        report = self._report(
            status=SessionStatus.CREATED,
            last_heartbeat_at=None,
            last_bar_at=None,
            last_started_at=None,
            last_stopped_at=None,
            sealed_at=None,
        )
        assert "never" in render_status(report)

    def test_failed_strategies_render_every_named_field(self):
        flags = {
            "all_failed": False,
            "failed_strategies": [
                {
                    "strategy_id": "SMACrossover-000",
                    "spec_strategy_id": "sma_crossover",
                    "error_type": "DivisionByZero",
                    "handler": "handle_bar",
                    "at": "2026-08-23T14:03:11+00:00",
                    "detail": "already redacted line",
                }
            ],
        }
        text = render_status(self._report(runtime_flags=flags))

        assert "sma_crossover" in text
        assert "SMACrossover-000" in text
        assert "DivisionByZero" in text
        assert "handle_bar" in text
        assert "already redacted line" in text

    def test_all_failed_gets_its_own_line(self):
        flags = {"all_failed": True, "failed_strategies": [{"spec_strategy_id": "s1"}]}
        text = render_status(self._report(runtime_flags=flags))

        assert "no longer trade" in text or "contained" in text

    def test_the_wording_respects_ar36s_vocabulary(self):
        """Forbidden lifecycle synonyms, scoped so the legitimate ``closed
        trade`` domain term is not a false hit (Story 1.2 precedent)."""
        flags = {"all_failed": True, "failed_strategies": [{"spec_strategy_id": "sma"}]}
        text = render_status(self._report(runtime_flags=flags, closed_trade_count=4))

        hits = _ar36_violations(text)
        assert not hits, f"AR36 forbidden vocabulary {hits} in: {text}"

    def test_the_ar36_scan_can_actually_fail(self):
        """Non-vacuity probe. The scan scrubs the carved-out domain term before
        matching, and nothing proved the scrub was narrower than the scan."""
        assert _ar36_violations("the session was halted") == ["halted"]
        assert _ar36_violations("please close the session") == ["close"]
        assert _ar36_violations("closed trades: 4, open positions: 0") == []

    def test_trader_id_renders_when_given(self):
        text = render_status(self._report(trader_id="PAPER-11111111"))
        assert "PAPER-11111111" in text


class TestEveryDegradationNamesItsCause:
    """AC #4, #5: `_is_degraded` has three independent senses, and the renderer
    must explain whichever one fired — not only `failed_strategies`."""

    def _report(self, **overrides) -> StatusReport:
        fields = dict(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )
        fields.update(overrides)
        return build_status_report(**fields)

    def test_a_connection_loss_renders_a_cause_line(self):
        """Sense (b), the dormant reader. Epic 4 supplies the writer; this pins
        that the rendering is already here, so that writer lights the whole
        path up with no change to this story's code."""
        report = self._report(
            runtime_flags={"v": 1, "connection_lost_at": "2026-08-24T11:00:00+00:00"}
        )
        text = render_status(report)

        assert report.health == SessionHealth.DEGRADED
        assert "2026-08-24T11:00:00+00:00" in text
        assert "connection" in text.lower()

    def test_all_failed_alone_renders_a_cause_line(self):
        """`all_failed` with an empty entries list: degraded by the derivation,
        but the renderer nested its line inside the entries block, so it said
        nothing at all."""
        report = self._report(runtime_flags={"v": 1, "all_failed": True, "failed_strategies": []})
        text = render_status(report)

        assert report.health == SessionHealth.DEGRADED
        assert "no longer trade" in text

    def test_no_degraded_report_ever_renders_without_a_cause(self):
        """The invariant behind the three cases above, asserted directly."""
        for flags in (
            {"v": 1, "all_failed": True},
            {"v": 1, "all_failed": True, "failed_strategies": []},
            {"v": 1, "failed_strategies": [{"spec_strategy_id": "s1"}]},
            {"v": 1, "connection_lost_at": "2026-08-24T11:00:00+00:00"},
        ):
            report = self._report(runtime_flags=flags)
            body = render_status(report).splitlines()[6:]

            assert report.health == SessionHealth.DEGRADED, flags
            assert body, f"degraded with no explanation for {flags}"

    def test_a_degraded_session_with_no_nameable_cause_says_so(self):
        """The fallback's own reachable case, not a hypothetical.

        ``_is_degraded`` reads ``failed_strategies`` for *truthiness*, but the
        builder keeps only entries that are mappings. A document whose list
        holds nothing usable is therefore degraded with every named branch
        empty — and without the fallback it renders a bare ``health:
        degraded`` and nothing else.
        """
        report = self._report(runtime_flags={"v": 1, "failed_strategies": ["junk"]})
        text = render_status(report)

        assert report.health == SessionHealth.DEGRADED
        assert report.failed_strategies == ()
        assert "Impaired" in text
        assert "name no cause" in text

    def test_a_healthy_report_renders_no_degradation_lines(self):
        """The converse — the fallback must not fire on a healthy session."""
        text = render_status(self._report())

        assert "Impaired" not in text
        assert "no longer trade" not in text


class TestMalformedRuntimeFlagsAreToleratedNotFatal:
    """`runtime_flags` is unconstrained JSONB with no server-side schema, and
    the renderer runs *outside* the CLI's exit-code guard — so a shape the one
    writer never produces must degrade gracefully, not raise past AR28."""

    def _build(self, flags):
        return build_status_report(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=flags,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )

    @pytest.mark.parametrize(
        "flags",
        [
            {"v": 1, "failed_strategies": ["sma_crossover"]},
            {"v": 1, "failed_strategies": "sma_crossover"},
            {"v": 1, "failed_strategies": 3},
            {"v": 1, "failed_strategies": {"sma": {"error_type": "X"}}},
            ["boom"],
            "boom",
            42,
        ],
    )
    def test_neither_deriving_nor_rendering_raises(self, flags):
        report = self._build(flags)

        assert render_status(report)
        assert status_json_payload(report)["status"] == "running"

    def test_non_mapping_entries_are_dropped_and_the_good_ones_kept(self):
        flags = {
            "v": 1,
            "failed_strategies": [{"spec_strategy_id": "keeper"}, "junk", 7, None],
        }
        report = self._build(flags)

        assert len(report.failed_strategies) == 1
        assert "keeper" in render_status(report)


class TestTimestampsAreNormalisedToUtc:
    """AR29 promises ISO-8601 **UTC**; `.isoformat()` alone promises nothing.

    The shipped tests fed in datetimes that were aware-UTC by construction, so
    they were made true by their fixtures rather than by the code.
    """

    def _build(self, **overrides):
        fields = dict(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status=SessionStatus.RUNNING,
            last_heartbeat_at=_ago(1),
            last_bar_at=None,
            last_started_at=None,
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )
        fields.update(overrides)
        return build_status_report(**fields)

    def test_an_aware_non_utc_timestamp_is_converted_not_echoed(self):
        eastern = timezone(timedelta(hours=-4))
        moment = NOW.astimezone(eastern)
        report = self._build(last_heartbeat_at=moment)

        rendered = status_json_payload(report)["last_activity_at"]
        assert rendered.endswith("+00:00"), rendered
        assert report.last_activity_at == moment  # same instant, different offset

    def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing(self):
        """A naive value would make `now - at` raise `TypeError` and `max()`
        raise across a mixed list — turning a healthy session into an exit-1
        'live status failed' with no cause named."""
        naive = NOW.replace(tzinfo=None)
        report = self._build(last_heartbeat_at=naive, last_started_at=NOW - timedelta(seconds=5))

        assert status_json_payload(report)["last_activity_at"].endswith("+00:00")
        assert report.heartbeat_age_seconds == 0.0

    def test_a_mixed_naive_and_aware_row_still_reports(self):
        report = self._build(
            last_heartbeat_at=NOW.replace(tzinfo=None) - timedelta(seconds=30),
            last_bar_at=NOW - timedelta(seconds=10),
            last_started_at=NOW.replace(tzinfo=None) - timedelta(seconds=600),
        )

        assert report.health == SessionHealth.TRADING
        assert status_json_payload(report)["last_activity_at"].endswith("+00:00")


class TestAStrStatusIsNotMisreadAsStopped:
    """`SessionStatus` is a `StrEnum`, so `"running"` compares equal to the
    member but is a different object. An identity test reported
    `health: stopped` beside `state: running` — the hazard
    `session_service._as_status` exists to close."""

    def test_a_str_status_derives_the_same_health_as_the_member(self):
        common = dict(
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            runtime_flags=None,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )

        assert derive_health("running", **common) is SessionHealth.TRADING
        assert derive_health(SessionStatus.RUNNING, **common) is SessionHealth.TRADING

    def test_the_report_never_contradicts_itself(self):
        report = build_status_report(
            session_id="11111111-1111-1111-1111-111111111111",
            name="alpha-session",
            status="running",
            last_heartbeat_at=_ago(1),
            last_bar_at=_ago(1),
            last_started_at=_ago(500),
            last_stopped_at=None,
            sealed_at=None,
            runtime_flags=None,
            closed_trade_count=0,
            open_positions=0,
            now=NOW,
            heartbeat_stale_after_seconds=90.0,
        )

        assert report.status == "running"
        assert report.health != SessionHealth.STOPPED


class TestModulePurity:
    """AC #9: the module stays framework-free — no SQLAlchemy, no Nautilus, no src.services."""

    def test_module_imports_no_forbidden_dependency(self):
        source = Path(live_session_health.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        forbidden = ("nautilus_trader", "ibapi", "sqlalchemy", "src.db", "src.services")
        offenders = [name for name in imported if name.startswith(forbidden)]

        assert offenders == [], f"live_session_health must stay framework-free: {offenders}"
