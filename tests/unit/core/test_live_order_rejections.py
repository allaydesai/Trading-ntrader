"""Unit tests for the rejection tally (Story 3.7, AC #1b, #1c, #2a, #3a).

Unit tier, deliberately: ``src/core/live_order_rejections.py`` is standard
library only — it never imports ``nautilus_trader`` — so every event below is a
hand-built duck-typed object carrying exactly the attributes the tally reads
(``client_order_id``, ``instrument_id``, ``strategy_id``, ``reason``,
``reconciliation``). The real-event proof lives in the component tier
(``tests/component/core/test_live_order_rejections_engine.py``), against a real
``ExecutionEngine`` on a real bus.

The clock is injected (``time_source``), never frozen: ``freezegun`` is banned
near this code path, and an injected clock is what lets ``first_at``/``last_at``
be asserted to the microsecond without one.
"""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from src.core import live_order_rejections as tally_module
from src.core.live_order_rejections import (
    KIND_DENIED,
    KIND_REJECTED,
    TALLY_FAILED_EVENT,
    RejectionSnapshot,
    RejectionTally,
    render_rejection_summary,
)
from src.core.live_strategy_guard import MAX_DETAIL_CHARS

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 22, 14, 0, 0, tzinfo=timezone.utc)

#: The paper account this repo's live transcripts carry, and the one
#: `redact_accounts`'s shape-based token pass must mask on its own.
PAPER_ACCOUNT = "DU4076626"


class _Clock:
    """A monotonically advancing injected clock, one second per reading."""

    def __init__(self, start: datetime = T0) -> None:
        self._at = start
        self.readings: list[datetime] = []

    def __call__(self) -> datetime:
        self.readings.append(self._at)
        taken, self._at = self._at, self._at + timedelta(seconds=1)
        return taken


def _named(name: str, **fields) -> object:
    """Build an event whose ``type(event).__name__`` is exactly ``name``."""
    namespace = {
        "client_order_id": "O-20260922-140000-abcd-000-1",
        "instrument_id": "NVDA.NASDAQ",
        "strategy_id": "SMACrossover-000",
        "reason": "Order rejected - reason: insufficient margin",
        "reconciliation": False,
    }
    namespace.update(fields)
    return type(name, (), namespace)()


def _tally(clock: _Clock | None = None, *, account: str | None = None) -> RejectionTally:
    return RejectionTally(
        structlog.get_logger("test").bind(session_id="s1"),
        time_source=clock or _Clock(),
        account=account,
    )


class TestTheSnapshotCountsRefusals:
    """AC #3a's arithmetic, in the tier where it is pure."""

    def test_one_rejection_produces_a_snapshot_with_the_last_refusal(self):
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert (snapshot.rejected, snapshot.denied, snapshot.consecutive) == (1, 0, 1)
        assert snapshot.first_at == T0
        assert snapshot.last_at == T0
        assert snapshot.last_kind == KIND_REJECTED
        assert snapshot.last_client_order_id == "O-20260922-140000-abcd-000-1"
        assert snapshot.last_instrument_id == "NVDA.NASDAQ"
        assert snapshot.last_strategy_id == "SMACrossover-000"
        assert snapshot.last_reconciliation is False

    def test_the_last_reason_is_redacted_by_token_shape(self):
        """NFR26/D-I: the COLUMN is redacted. (The transcript stays verbatim —
        that is Story 3.3's contract, proven in the component tier beside this
        one so the two policies are visibly distinct.)
        """
        tally = _tally(_Clock())

        tally.handle_order_event(
            _named("OrderRejected", reason=f"Error 321: account {PAPER_ACCOUNT} is not managed")
        )
        snapshot = tally.pending()

        assert snapshot is not None
        assert PAPER_ACCOUNT not in snapshot.last_reason
        assert "***626" in snapshot.last_reason
        # Not `mask_account(whole_reason)`: the payload survives.
        assert "not managed" in snapshot.last_reason

    def test_the_configured_account_is_redacted_case_insensitively(self):
        """Story 2.7 AC #8's clause, inherited through ``redact_accounts``."""
        # Deliberately NOT token-shaped (`_ACCOUNT_TOKEN` needs one or two
        # uppercase letters then six to ten digits), so only the configured
        # clause can mask it — and deliberately long enough that
        # `mask_account` reveals a tail rather than collapsing to `"***"`.
        tally = _tally(_Clock(), account="paperacct01")

        tally.handle_order_event(_named("OrderRejected", reason="rejected for PAPERACCT01 balance"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert "PAPERACCT01" not in snapshot.last_reason
        assert "***T01" in snapshot.last_reason

    def test_the_last_reason_is_capped_at_max_detail_chars(self):
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected", reason="x" * 1_000))
        snapshot = tally.pending()

        assert snapshot is not None
        assert len(snapshot.last_reason) == MAX_DETAIL_CHARS

    def test_a_denial_counts_under_denied_and_names_its_kind(self):
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderDenied", reason="NOTIONAL_EXCEEDS_FREE_BALANCE"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert (snapshot.rejected, snapshot.denied, snapshot.consecutive) == (0, 1, 1)
        assert snapshot.last_kind == KIND_DENIED

    def test_a_reconciliation_rejection_is_counted_and_marked(self):
        """Story 3.4's in-flight sweep closes an unanswered order as
        ``OrderRejected(reason="UNKNOWN", reconciliation=True)``. It is a
        refusal — the strategy asked and nothing was placed — so it counts, and
        the snapshot says which kind it was so a reader can tell.
        """
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected", reason="UNKNOWN", reconciliation=True))
        snapshot = tally.pending()

        assert snapshot is not None
        assert snapshot.rejected == 1
        assert snapshot.last_reconciliation is True


class TestTheStreakResetsOnlyOnARealAcceptance:
    """D-C: an acceptance is the venue saying "this order is working"."""

    def test_an_acceptance_resets_the_streak_but_not_the_totals(self):
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderAccepted"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert snapshot.consecutive == 0
        assert snapshot.rejected == 2

    def test_a_reconciliation_acceptance_does_not_reset_the_streak(self):
        """A ``reconciliation=True`` acceptance is a startup RESTORE of an
        order accepted before a restart (Story 3.4), not fresh evidence that
        orders are getting through today.
        """
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderAccepted", reconciliation=True))
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert snapshot.consecutive == 2

    def test_a_denial_extends_the_same_streak_as_a_rejection(self):
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderDenied"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert (snapshot.rejected, snapshot.denied, snapshot.consecutive) == (1, 1, 2)

    def test_first_at_is_the_first_refusal_and_never_moves(self):
        clock = _Clock()
        tally = _tally(clock)

        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderAccepted"))
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()

        assert snapshot is not None
        assert snapshot.first_at == T0
        # The clock is read once per REFUSAL; an acceptance reads none, so the
        # second rejection is the clock's second reading, not its third.
        assert snapshot.last_at == T0 + timedelta(seconds=1)
        assert clock.readings == [T0, T0 + timedelta(seconds=1)]

    def test_an_acceptance_alone_never_produces_a_snapshot(self):
        """Nothing was refused, so there is nothing to write — a clean session
        costs zero database round trips (D-E).
        """
        tally = _tally(_Clock())

        tally.handle_order_event(_named("OrderAccepted"))

        assert tally.pending() is None
        assert tally.snapshot is None


class TestDirtyFlagSemantics:
    """AC #3a: at most one write per tick, newest wins, nothing queued."""

    def test_pending_is_none_before_any_event(self):
        assert _tally(_Clock()).pending() is None

    def test_pending_is_none_again_after_mark_written(self):
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()
        assert snapshot is not None

        tally.mark_written(snapshot.version)

        assert tally.pending() is None

    def test_a_further_refusal_dirties_it_again_with_a_newer_version(self):
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        first = tally.pending()
        assert first is not None
        tally.mark_written(first.version)

        tally.handle_order_event(_named("OrderRejected"))
        second = tally.pending()

        assert second is not None
        assert second.version > first.version
        assert second.rejected == 2

    def test_marking_a_stale_version_written_leaves_it_dirty(self):
        """The tick writes version N, a rejection arrives before
        ``mark_written`` lands: the newer snapshot must not be marked clean by
        the older write's acknowledgement.
        """
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        first = tally.pending()
        assert first is not None
        tally.handle_order_event(_named("OrderRejected"))

        tally.mark_written(first.version)

        still_pending = tally.pending()
        assert still_pending is not None
        assert still_pending.rejected == 2

    def test_snapshot_reads_the_latest_without_clearing_the_dirty_flag(self):
        """``snapshot`` is the runner's/CLI's read (D-H); only ``pending()``
        plus ``mark_written`` drive the write.
        """
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))

        assert tally.snapshot is not None
        assert tally.pending() is not None


class TestTheDispatchIsAClosedSet:
    """D-J: three names, and everything else is ignored in silence."""

    def test_the_dispatch_keys_are_exactly_the_three_refusal_related_types(self):
        assert set(_tally(_Clock())._dispatch) == {
            "OrderRejected",
            "OrderDenied",
            "OrderAccepted",
        }

    @pytest.mark.parametrize(
        "event_type",
        [
            "OrderSubmitted",
            "OrderFilled",
            "OrderInitialized",
            "OrderTriggered",
            "OrderModifyRejected",
            "OrderCancelRejected",
            "OrderCanceled",
            "PositionClosed",
        ],
    )
    def test_every_other_event_type_is_ignored_silently(self, event_type):
        tally = _tally(_Clock())

        with capture_logs() as logs:
            tally.handle_order_event(_named(event_type))

        assert tally.pending() is None
        assert logs == []

    def test_a_plain_object_is_ignored_silently(self):
        tally = _tally(_Clock())

        with capture_logs() as logs:
            tally.handle_order_event(object())

        assert tally.pending() is None
        assert logs == []


class TestContainmentExtendsToEveryDispatchedBranch:
    """AC #2a. A raise from a msgbus handler re-enters ``MessageBus.publish_c``,
    which has no ``try`` around ``sub.handler(msg)``, and ends at Nautilus's own
    silent ``os._exit(1)`` (``common/component.pyx:2757``). Nothing here may
    ever let that happen.
    """

    @staticmethod
    def _exploding(name: str) -> object:
        class _Exploding:
            def __getattr__(self, item):
                raise RuntimeError("boom")

        _Exploding.__name__ = name
        return _Exploding()

    @pytest.mark.parametrize("event_type", ["OrderRejected", "OrderDenied", "OrderAccepted"])
    def test_a_raising_event_is_contained_and_recorded_once(self, event_type):
        tally = _tally(_Clock())

        with capture_logs() as logs:
            tally.handle_order_event(self._exploding(event_type))  # must not raise

        failed = [entry for entry in logs if entry["event"] == TALLY_FAILED_EVENT]
        assert len(failed) == 1
        assert failed[0]["stage"] == "handle_order_event"
        assert failed[0]["event_type"] == event_type
        assert failed[0]["error_type"] == "RuntimeError"
        assert failed[0]["exc_info"] is True
        assert failed[0]["log_level"] == "error"

    @pytest.mark.parametrize("event_type", ["OrderRejected", "OrderDenied", "OrderAccepted"])
    def test_a_failed_build_leaves_the_counters_untouched(self, event_type):
        """The ``_log_filled`` lesson (Story 3.3 review): commit the counters
        only after the record is built, or a half-applied increment survives a
        failure and every later snapshot carries a phantom refusal.
        """
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        before = tally.pending()
        assert before is not None

        with capture_logs():
            tally.handle_order_event(self._exploding(event_type))

        after = tally.pending()
        assert after is not None
        assert (after.rejected, after.denied, after.consecutive) == (
            before.rejected,
            before.denied,
            before.consecutive,
        )

    def test_the_client_order_id_is_read_through_getattr(self):
        """The diagnostic cannot itself raise on the malformed event that
        brought it here (the ``handle_order_event`` precedent).
        """
        tally = _tally(_Clock())

        with capture_logs() as logs:
            tally.handle_order_event(self._exploding("OrderRejected"))

        failed = [entry for entry in logs if entry["event"] == TALLY_FAILED_EVENT]
        assert "client_order_id" in failed[0]


class TestTheSnapshotCrossesAr38sLineInPrimitivesOnly:
    """AC #3a(vii): nothing Nautilus-shaped may travel to ``src/services``."""

    def test_as_port_kwargs_yields_exactly_the_port_keyword_set(self):
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()
        assert snapshot is not None

        kwargs = snapshot.as_port_kwargs()

        assert set(kwargs) == {
            "rejected",
            "denied",
            "consecutive",
            "first_at",
            "last_at",
            "last_kind",
            "last_client_order_id",
            "last_instrument_id",
            "last_strategy_id",
            "last_reason",
            "last_reconciliation",
        }

    def test_every_port_kwarg_is_a_standard_library_primitive(self):
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()
        assert snapshot is not None

        for name, value in snapshot.as_port_kwargs().items():
            assert type(value) in (int, str, bool, datetime), f"{name} is {type(value)!r}"

    def test_version_is_not_a_port_kwarg(self):
        """``version`` is the dirty-flag token, local to this process — the
        column carries no schema of its own for it.
        """
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        snapshot = tally.pending()
        assert snapshot is not None

        assert "version" not in snapshot.as_port_kwargs()

    def test_the_snapshot_is_frozen(self):
        snapshot = RejectionSnapshot(
            version=1,
            rejected=1,
            denied=0,
            consecutive=1,
            first_at=T0,
            last_at=T0,
            last_kind=KIND_REJECTED,
            last_client_order_id="O-1",
            last_instrument_id="NVDA.NASDAQ",
            last_strategy_id="SMACrossover-000",
            last_reason="insufficient margin",
            last_reconciliation=False,
        )

        with pytest.raises(Exception):
            snapshot.rejected = 2  # type: ignore[misc]


class TestRenderRejectionSummary:
    """D-H's pure renderer, and AR36's vocabulary over every line of it."""

    #: AR36's forbidden stems, word-matched. ``closed`` matches ``\bclose\w*\b``.
    FORBIDDEN_STEMS = ("halt", "kill", "paus", "close", "finaliz")

    def _snapshot(self) -> RejectionSnapshot:
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        tally.handle_order_event(_named("OrderDenied"))
        snapshot = tally.pending()
        assert snapshot is not None
        return snapshot

    def test_the_summary_names_both_counters_the_streak_and_the_last_refusal(self):
        lines = render_rejection_summary(self._snapshot())

        text = "\n".join(lines)
        assert "1" in text  # rejected
        assert "NVDA.NASDAQ" in text
        assert "O-20260922-140000-abcd-000-1" in text
        assert T0.isoformat() in text

    def test_no_line_uses_an_ar36_forbidden_stem(self):
        lines = render_rejection_summary(self._snapshot())

        for line in lines:
            lowered = line.lower()
            for stem in self.FORBIDDEN_STEMS:
                assert stem not in lowered, f"AR36 vocabulary violation in {line!r}: {stem}"

    def test_no_line_carries_an_unmasked_account_shaped_token(self):
        tally = _tally(_Clock())
        tally.handle_order_event(
            _named("OrderRejected", reason=f"account {PAPER_ACCOUNT} is not managed")
        )
        snapshot = tally.pending()
        assert snapshot is not None

        text = "\n".join(render_rejection_summary(snapshot))

        assert PAPER_ACCOUNT not in text

    def test_rendering_none_produces_nothing(self):
        assert render_rejection_summary(None) == []


class TestModulePurity:
    """The tally runs inline inside ``MessageBus.publish_c`` and builds the
    record that crosses into ``src/services`` — standard library only, plus the
    one framework-free sibling it borrows redaction from.
    """

    FORBIDDEN = ("sqlalchemy", "nautilus_trader", "ibapi", "src.db", "src.services")

    def test_top_level_imports_never_reach_a_framework(self):
        tree = ast.parse(Path(tally_module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [name for name in imported if name.startswith(self.FORBIDDEN)]
        assert offenders == [], f"the tally must stay framework-free, found: {offenders}"

    def test_no_record_name_is_emitted_but_the_one_diagnostic(self):
        """NFR26/AR41: this module emits exactly one record, and it is a
        diagnostic — deliberately outside ``EMITTED_ORDER_EVENTS``, on the
        ``order.observer_failed`` precedent.
        """
        source = Path(tally_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        emitted = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"debug", "info", "warning", "error"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert emitted == set()  # every call site names the constant, not a literal
        assert TALLY_FAILED_EVENT == "order.rejection_tally_failed"

    def test_no_record_field_is_named_account(self):
        """NFR26's anti-field scan, the ``TestNoRecordEverCarriesAnAccountId``
        shape: an account id must never travel as its own field.
        """
        tally = _tally(_Clock())

        with capture_logs() as logs:
            tally.handle_order_event(
                TestContainmentExtendsToEveryDispatchedBranch._exploding("OrderRejected")
            )

        for entry in logs:
            assert "account" not in entry
            assert "account_id" not in entry


class TestMarkWrittenIsMonotonic:
    """A late acknowledgement must not un-acknowledge a newer write.

    ⚠️ Added after Task 11's mutation sweep, which is the only reason this
    class exists: mutation **M2** (``mark_written`` assigning the version
    unconditionally instead of only when it advances) **survived** the
    original suite. ``test_marking_a_stale_version_written_leaves_it_dirty``
    looks like it covers this and does not — there the newer snapshot is
    dirty either way, so both the guarded and the unguarded form return it.

    The direction that actually needs the guard is the other one: two writes
    acknowledged out of order must leave the tally *clean*, or the tick pays
    a redundant round trip for a summary the row already holds — small, but
    exactly the unbounded-work shape NFR2 is about, and invisible without
    this pin.
    """

    def test_an_out_of_order_acknowledgement_does_not_re_dirty_the_tally(self):
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        first = tally.pending()
        assert first is not None
        tally.handle_order_event(_named("OrderRejected"))
        second = tally.pending()
        assert second is not None

        tally.mark_written(second.version)
        tally.mark_written(first.version)  # the older write's ack, arriving late

        assert tally.pending() is None

    def test_the_newer_snapshot_is_still_offered_before_it_is_acknowledged(self):
        """The control, so the assertion above is not satisfied by a
        ``mark_written`` that simply latches clean forever.
        """
        tally = _tally(_Clock())
        tally.handle_order_event(_named("OrderRejected"))
        first = tally.pending()
        assert first is not None
        tally.mark_written(first.version)
        tally.handle_order_event(_named("OrderRejected"))

        assert tally.pending() is not None
