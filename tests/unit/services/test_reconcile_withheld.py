"""Unit tests for the withheld-period document (Story 5.8, AC #3, D-E/D-H).

Pure: no database, no clock. The document lives in
``runtime_flags["reconcile_withheld"]``; these helpers are the one place its
shape is written, carried, closed and read for a record.
"""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from src.services.reconcile_withheld import (
    ENDED_PROCESS_LOST,
    ENDED_RECONCILED,
    ENDED_SESSION_STOPPED,
    OPEN_AT_SEAL,
    RECONCILE_WITHHELD_KEY,
    WITHHELD_CAUSE,
    WITHHELD_PERIODS_KEPT,
    carried_forward,
    closed_on_stop,
    empty_document,
    with_period,
    withheld_for_record,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 11, 14, 0, tzinfo=UTC)


def _period(document, *, start=T0, end=None, reason="timeout", error_type="E", cycles=3):
    return with_period(
        document,
        withheld_from=start,
        withheld_until=end,
        reason=reason,
        error_type=error_type,
        failed_cycles=cycles,
    )


def _flags(document):
    return {"v": 1, RECONCILE_WITHHELD_KEY: document}


class TestTheVocabulary:
    def test_the_key_the_cause_and_the_bound(self):
        assert RECONCILE_WITHHELD_KEY == "reconcile_withheld"
        assert WITHHELD_CAUSE == "runtime_reconciliation_failing"
        assert WITHHELD_PERIODS_KEPT == 50

    def test_the_ended_by_values_are_distinct(self):
        values = {ENDED_RECONCILED, ENDED_SESSION_STOPPED, ENDED_PROCESS_LOST, OPEN_AT_SEAL}
        assert values == {"reconciled", "session_stopped", "process_lost", "open_at_seal"}

    def test_the_empty_document(self):
        assert empty_document() == {"count": 0, "periods": []}
        assert empty_document() is not empty_document()


class TestWithPeriod:
    def test_a_new_start_is_appended_and_counted(self):
        document = _period(None)

        assert document == {
            "count": 1,
            "periods": [
                {
                    "start": T0.isoformat(),
                    "end": None,
                    "cause": WITHHELD_CAUSE,
                    "reason": "timeout",
                    "error_type": "E",
                    "failed_cycles": 3,
                    "ended_by": None,
                }
            ],
        }

    def test_the_same_start_is_replaced_not_counted_again(self):
        started = _period(None)

        ended = _period(started, end=T0 + timedelta(minutes=2))

        assert ended["count"] == 1
        (period,) = ended["periods"]
        assert period["end"] == (T0 + timedelta(minutes=2)).isoformat()
        assert period["ended_by"] == ENDED_RECONCILED

    def test_a_late_start_write_never_reopens_a_closed_period(self):
        """A retried start after its end must not erase the end (fail closed:
        a period the record says ended stays ended)."""
        ended = _period(_period(None), end=T0 + timedelta(minutes=2))

        replayed = _period(ended)

        assert replayed == ended

    @pytest.mark.parametrize("closure", [ENDED_SESSION_STOPPED, ENDED_PROCESS_LOST])
    def test_an_edges_closure_is_never_overwritten(self, closure):
        """Code review P6: once a stop or a restart closed a period, a write
        carrying an end (only ever a retry; the row's guards refuse any other)
        never relabels it."""
        closed = _period(None)
        closed["periods"][0].update(end=(T0 + timedelta(minutes=9)).isoformat(), ended_by=closure)

        rewritten = _period(closed, end=T0 + timedelta(minutes=2))

        assert rewritten == closed

    def test_a_second_period_is_a_second_entry(self):
        first = _period(_period(None), end=T0 + timedelta(minutes=2))

        both = _period(first, start=T0 + timedelta(minutes=10))

        assert both["count"] == 2
        assert [p["start"] for p in both["periods"]] == [
            T0.isoformat(),
            (T0 + timedelta(minutes=10)).isoformat(),
        ]

    def test_only_the_most_recent_are_kept_and_count_stays_the_total(self):
        document = None
        for n in range(WITHHELD_PERIODS_KEPT + 7):
            start = T0 + timedelta(minutes=10 * n)
            document = _period(document, start=start, end=start + timedelta(minutes=3))

        assert document["count"] == WITHHELD_PERIODS_KEPT + 7
        assert len(document["periods"]) == WITHHELD_PERIODS_KEPT
        assert document["periods"][0]["start"] == (T0 + timedelta(minutes=70)).isoformat()

    def test_the_input_is_never_mutated_or_aliased(self):
        """SQLAlchemy does not track in-place JSONB mutation: every result is a
        new object, so the caller's reassignment is what persists."""
        original = _period(None)
        snapshot = copy.deepcopy(original)

        result = _period(original, end=T0 + timedelta(minutes=1))

        assert original == snapshot
        assert result is not original
        assert result["periods"] is not original["periods"]
        assert result["periods"][0] is not original["periods"][0]

    @pytest.mark.parametrize(
        "malformed",
        ["a string", 7, [1, 2], {"count": "x", "periods": "nope"}, {"periods": [1, "x", {}]}],
    )
    def test_a_malformed_stored_document_is_tolerated_not_raised(self, malformed):
        document = _period(malformed)

        assert document["periods"][-1]["start"] == T0.isoformat()
        assert document["count"] >= 1

    def test_the_document_is_json_native(self):
        json.dumps(_period(_period(None), end=T0 + timedelta(seconds=90)))


class TestClosedOnStop:
    def test_an_open_period_is_closed_at_the_stop_instant(self):
        flags = _flags(_period(None))
        stop = T0 + timedelta(minutes=5)

        closed = closed_on_stop(flags, stop)

        (period,) = closed[RECONCILE_WITHHELD_KEY]["periods"]
        assert (period["end"], period["ended_by"]) == (stop.isoformat(), ENDED_SESSION_STOPPED)
        assert closed["v"] == 1

    def test_a_closed_period_is_never_re_closed(self):
        ended = _flags(_period(_period(None), end=T0 + timedelta(minutes=2)))

        assert closed_on_stop(ended, T0 + timedelta(hours=1)) == ended

    def test_flags_without_a_history_are_returned_unchanged(self):
        flags = {"v": 1, "failed_strategies": [{"spec_strategy_id": "x"}]}

        assert closed_on_stop(flags, T0) is flags
        assert closed_on_stop(None, T0) is None

    def test_other_keys_survive(self):
        flags = {**_flags(_period(None)), "order_rejections": {"rejected": 3}}

        closed = closed_on_stop(flags, T0 + timedelta(minutes=1))

        assert closed["order_rejections"] == {"rejected": 3}

    def test_a_stop_before_the_start_is_an_unknown_end(self):
        """Code review P6: a stop instant before the period began can only be
        a clock step; the end is unknown, never one before its start."""
        closed = closed_on_stop(_flags(_period(None)), T0 - timedelta(seconds=30))

        (period,) = closed[RECONCILE_WITHHELD_KEY]["periods"]
        assert (period["end"], period["ended_by"]) == (None, ENDED_SESSION_STOPPED)

    @pytest.mark.parametrize(
        "stored",
        ["hand-edited", {"count": 1, "periods": "nope"}, 7],
        ids=["str", "periods_not_a_list", "int"],
    )
    def test_a_malformed_history_passes_the_stop_as_stored(self, stored):
        """Code review P6: the edges never normalise what they cannot read —
        the seal records it as stored (D-I)."""
        closed = closed_on_stop({RECONCILE_WITHHELD_KEY: stored}, T0)

        assert closed[RECONCILE_WITHHELD_KEY] == stored

    def test_an_unreadable_entry_is_kept_in_place_and_count_as_stored(self):
        stored = {"count": 9, "periods": ["junk", _period(None)["periods"][0]]}

        closed = closed_on_stop({RECONCILE_WITHHELD_KEY: stored}, T0 + timedelta(minutes=1))

        document = closed[RECONCILE_WITHHELD_KEY]
        assert document["count"] == 9
        assert document["periods"][0] == "junk"
        assert document["periods"][1]["ended_by"] == ENDED_SESSION_STOPPED


class TestCarriedForward:
    """The ``-> running`` edge (PO ruling Q1): only the history survives."""

    def test_no_history_carries_nothing(self):
        assert carried_forward(None, last_heartbeat_at=None) is None
        assert carried_forward({"v": 1, "all_failed": True}, last_heartbeat_at=None) is None

    def test_the_history_alone_is_carried(self):
        document = _period(_period(None), end=T0 + timedelta(minutes=2))
        flags = {
            "v": 1,
            "all_failed": True,
            "failed_strategies": [{"spec_strategy_id": "x"}],
            "order_rejections": {"rejected": 3},
            RECONCILE_WITHHELD_KEY: document,
        }

        carried = carried_forward(flags, last_heartbeat_at=T0 + timedelta(hours=1))

        assert carried == document
        assert carried is not document

    def test_a_dead_processes_open_period_closes_at_its_last_heartbeat(self):
        heartbeat = T0 + timedelta(minutes=4)

        carried = carried_forward(_flags(_period(None)), last_heartbeat_at=heartbeat)

        (period,) = carried["periods"]
        assert (period["end"], period["ended_by"]) == (heartbeat.isoformat(), ENDED_PROCESS_LOST)

    @pytest.mark.parametrize(
        "heartbeat", [None, T0 - timedelta(seconds=5)], ids=["none", "before_start"]
    )
    def test_an_end_is_never_invented(self, heartbeat):
        """No heartbeat, or one before the period began (written earlier in
        the same tick): the end is unknown and recorded as ``null``."""
        carried = carried_forward(_flags(_period(None)), last_heartbeat_at=heartbeat)

        (period,) = carried["periods"]
        assert (period["end"], period["ended_by"]) == (None, ENDED_PROCESS_LOST)

    def test_each_open_period_is_judged_against_its_own_start(self):
        """Code review P6: one open period starting after the heartbeat does
        not null the end of another that started before it."""
        early = _period(None)
        both = _period(early, start=T0 + timedelta(minutes=10))
        heartbeat = T0 + timedelta(minutes=5)

        carried = carried_forward(_flags(both), last_heartbeat_at=heartbeat)

        assert [(p["end"], p["ended_by"]) for p in carried["periods"]] == [
            (heartbeat.isoformat(), ENDED_PROCESS_LOST),
            (None, ENDED_PROCESS_LOST),
        ]

    def test_a_malformed_history_is_carried_as_stored(self):
        assert carried_forward({RECONCILE_WITHHELD_KEY: "hand-edited"}, last_heartbeat_at=T0) == (
            "hand-edited"
        )

    def test_a_closed_period_with_an_unknown_end_stays_closed(self):
        """One definition of "open" everywhere (code review P6): ``ended_by``
        unset. A ``process_lost`` period with a ``null`` end is closed."""
        lost = carried_forward(_flags(_period(None)), last_heartbeat_at=None)

        stopped = closed_on_stop(_flags(lost), T0 + timedelta(hours=1))

        (period,) = stopped[RECONCILE_WITHHELD_KEY]["periods"]
        assert (period["end"], period["ended_by"]) == (None, ENDED_PROCESS_LOST)


class TestWithheldForRecord:
    """The seal's read (D-I): as stored, never judged — except that an
    open-at-seal period says so explicitly (PO condition)."""

    def test_absent_is_an_explicit_empty_document(self):
        assert withheld_for_record(None) == empty_document()
        assert withheld_for_record({"v": 1}) == empty_document()
        assert withheld_for_record("not a mapping") == empty_document()

    def test_the_stored_document_is_recorded_as_stored(self):
        document = _period(_period(None), end=T0 + timedelta(minutes=2))

        recorded = withheld_for_record(_flags(document))

        assert recorded == document
        assert recorded is not document

    def test_an_open_period_is_recorded_open_at_seal(self):
        recorded = withheld_for_record(_flags(_period(None)))

        (period,) = recorded["periods"]
        assert (period["end"], period["ended_by"]) == (None, OPEN_AT_SEAL)

    def test_a_process_lost_period_with_no_end_keeps_its_own_ending(self):
        flags = _flags(carried_forward(_flags(_period(None)), last_heartbeat_at=None))

        (period,) = withheld_for_record(flags)["periods"]

        assert period["ended_by"] == ENDED_PROCESS_LOST

    def test_a_malformed_document_is_recorded_not_refused(self):
        flags = {RECONCILE_WITHHELD_KEY: "hand-edited"}

        assert withheld_for_record(flags) == "hand-edited"
