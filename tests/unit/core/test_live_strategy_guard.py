"""The containment policy, proved without Nautilus (Story 2.7, Task 2).

Unit tier and it stays that way: :class:`~src.core.live_strategy_guard.StrategyGuard`
is duck-typed against the five things it needs from a strategy — ``id``,
``is_running``, ``degrade``, and the two ``handle_*`` attributes — so the whole
policy is exercised here against a stub, with no C logging, no message bus and
no kernel. What this tier **cannot** prove is that the boundary actually keeps
the *process* alive; that is
``tests/integration/core/test_live_strategy_failure_survives.py``, and it is a
subprocess return code rather than an assertion about a log record.
"""

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.live_strategy_guard import (
    GUARDED_HANDLERS,
    StrategyFailure,
    StrategyGuard,
    StrategyGuardError,
    redact_accounts,
)

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: The measured shape of an IBKR error that embeds an account identifier,
#: quoted verbatim from ``live_check.py:145-160``'s own inline documentation of
#: the same case. AC #8's planted string.
IBKR_ACCOUNT_ERROR = "Error 321: account DU4076626 is not managed"

FIXED_NOW = datetime(2026, 8, 23, 14, 3, 11, 482913, tzinfo=timezone.utc)


def _clock():
    return FIXED_NOW


class RecordingLog:
    """A structlog-shaped double that keeps every record for assertion."""

    def __init__(self):
        self.records: list[tuple[str, str, dict]] = []

    def _capture(self, level):
        def log(event, **fields):
            self.records.append((level, event, fields))

        return log

    def __getattr__(self, level):
        if level.startswith("_"):
            raise AttributeError(level)
        return self._capture(level)

    def events(self, level=None):
        return [e for lvl, e, _ in self.records if level is None or lvl == level]

    def fields_for(self, event):
        return [f for _lvl, e, f in self.records if e == event]


class RaisingLog(RecordingLog):
    """A logger that is itself broken — AC #10's third failure mode."""

    def __getattr__(self, level):
        if level.startswith("_"):
            raise AttributeError(level)

        def log(event, **fields):
            raise RuntimeError(f"the log sink is down: {event}")

        return log


class StubStrategy:
    """The five attributes the guard duck-types against, and nothing else.

    ``handle_bar`` and ``handle_event`` record the call **before** raising, so a
    test can tell "the base handler ran and then blew up" apart from "the
    wrapper short-circuited and never called it" — which is exactly the
    distinction AC #9's indicator clause turns on.
    """

    def __init__(self, *, strategy_id="Stub-000", raises=None, running=True):
        self.id = strategy_id
        self.is_running = running
        self.bar_calls: list[object] = []
        self.event_calls: list[object] = []
        self.degrade_calls = 0
        self.degrade_raises: BaseException | None = None
        self._raises = raises

    def handle_bar(self, bar):
        self.bar_calls.append(bar)
        if self._raises is not None:
            raise self._raises

    def handle_event(self, event):
        self.event_calls.append(event)
        if self._raises is not None:
            raise self._raises

    def degrade(self):
        self.degrade_calls += 1
        if self.degrade_raises is not None:
            raise self.degrade_raises
        self.is_running = False


class UnwrappableStrategy:
    """A stand-in for a bare ``Strategy``: a cdef class with no ``__dict__``.

    Measured (finding #5): ``s.handle_bar = fn`` on a bare ``Strategy`` raises
    ``AttributeError: attribute 'handle_bar' is read-only``. ``__slots__``
    reproduces that refusal in pure Python.
    """

    __slots__ = ()

    id = "Unwrappable-000"
    is_running = True

    def handle_bar(self, bar):
        pass

    def handle_event(self, event):
        pass

    def degrade(self):
        pass


def _guard(log=None):
    return StrategyGuard(log=log if log is not None else RecordingLog(), time_source=_clock)


class TestARaisingHandlerDoesNotPropagate:
    """AC #1 — the boundary catches, and the caller never sees the exception."""

    @pytest.mark.parametrize("handler", GUARDED_HANDLERS)
    def test_the_wrapped_handler_returns_normally_when_the_base_raises(self, handler):
        strategy = StubStrategy(raises=RuntimeError("boom"))
        _guard().wrap(strategy, spec_strategy_id="sma_crossover")

        assert getattr(strategy, handler)(object()) is None

    def test_both_handlers_are_wrapped_because_epic_3_needs_handle_event(self):
        strategy = StubStrategy()
        guard = _guard()
        before = {name: getattr(strategy, name) for name in GUARDED_HANDLERS}

        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        for name in GUARDED_HANDLERS:
            assert getattr(strategy, name) is not before[name], f"{name} was not wrapped"
        assert GUARDED_HANDLERS == ("handle_bar", "handle_event")

    def test_a_clean_handler_is_untouched_and_its_return_value_survives(self):
        strategy = StubStrategy()
        marker = object()
        strategy.handle_bar = lambda bar: marker
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is marker
        # The guard that DID the wrapping (review fix, 2026-08-23: this used to
        # assert on a fresh guard, which is empty by construction).
        assert guard.failures == ()

    def test_a_system_exit_from_a_handler_is_contained_like_any_failure(self):
        """A strategy calling ``sys.exit()`` in ``on_bar`` raises SystemExit —
        a ``BaseException`` that would unwind through ``publish_c`` into the
        same silent ``os._exit(1)`` as a ``RuntimeError`` (review fix,
        2026-08-23: the boundary used to catch ``Exception`` only).
        """
        strategy = StubStrategy(raises=SystemExit(1))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is None
        assert [f.error_type for f in guard.failures] == ["SystemExit"]

    @pytest.mark.parametrize("escapee", [KeyboardInterrupt, asyncio.CancelledError])
    def test_cancellation_and_interrupt_leave_the_boundary_unlatched(self, escapee):
        """The two deliberate escapes: both belong to the loop and the signal
        machinery, not to the strategy, and swallowing either would break
        cancellation for the task the handler runs under.
        """
        strategy = StubStrategy(raises=escapee())
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        with pytest.raises(escapee):
            strategy.handle_bar(object())
        assert guard.failures == ()

    def test_a_strategy_that_refuses_the_assignment_is_a_named_startup_failure(self):
        with pytest.raises(StrategyGuardError) as raised:
            _guard().wrap(UnwrappableStrategy(), spec_strategy_id="sma_crossover")

        assert "handle_bar" in str(raised.value)


class TestTheFailureRecordIsReadAtFailureTime:
    """AC #1 — the record carries the *Nautilus* id, resolved when it raised.

    ``Trader.add_strategy`` rewrites the id when ``order_id_tag`` is ``None``
    (measured: ``SMACrossover-None -> SMACrossover-000``) and the guard is
    applied **before** that call, so an id captured at wrap time is the wrong
    one — mutation #4.
    """

    def test_the_strategy_id_is_the_one_the_strategy_had_when_it_raised(self):
        strategy = StubStrategy(strategy_id="SMACrossover-None", raises=RuntimeError("boom"))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")
        strategy.id = "SMACrossover-000"  # what add_strategy does, after wrapping

        strategy.handle_bar(object())

        (failure,) = guard.failures
        assert failure.strategy_id == "SMACrossover-000"

    def test_the_record_carries_every_field_the_column_and_the_log_need(self):
        strategy = StubStrategy(strategy_id="SMACrossover-000", raises=ValueError("bad size"))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_event(object())

        (failure,) = guard.failures
        assert isinstance(failure, StrategyFailure)
        assert failure.spec_strategy_id == "sma_crossover"
        assert failure.error_type == "ValueError"
        assert failure.handler == "handle_event"
        assert failure.at == FIXED_NOW
        assert "bad size" in failure.detail

    def test_the_log_record_names_the_strategy_the_handler_and_the_traceback(self):
        log = RecordingLog()
        strategy = StubStrategy(strategy_id="SMACrossover-000", raises=RuntimeError("boom"))
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        (fields,) = log.fields_for("strategy.failed")
        assert fields["strategy_id"] == "SMACrossover-000"
        assert fields["spec_strategy_id"] == "sma_crossover"
        assert fields["error_type"] == "RuntimeError"
        assert fields["handler"] == "handle_bar"
        # Mutation #5: an `exc_info=True` implementation renders no string here,
        # so this assertion is what makes the traceback field load-bearing.
        assert "Traceback" in fields["traceback"]
        assert "RuntimeError" in fields["traceback"]
        assert ("error", "strategy.failed") in [(lvl, e) for lvl, e, _ in log.records]

    def test_the_detail_is_one_line_and_capped(self):
        strategy = StubStrategy(raises=RuntimeError("x" * 500 + "\nsecond line"))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        (failure,) = guard.failures
        assert "\n" not in failure.detail
        assert len(failure.detail) <= 200


class TestTheLatchIsPerStrategyNotPerBar:
    """AC #9 — one record and one queued write per strategy per process run."""

    def test_a_second_raise_queues_no_second_failure_and_logs_no_second_record(self):
        log = RecordingLog()
        strategy = StubStrategy(raises=RuntimeError("boom"))
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        for _ in range(5):
            strategy.handle_bar(object())

        assert log.events().count("strategy.failed") == 1

    def test_a_raise_in_the_other_handler_is_also_suppressed_by_the_same_latch(self):
        strategy = StubStrategy(raises=RuntimeError("boom"))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())
        strategy.handle_event(object())

        assert len(guard.failures) == 1

    def test_after_latching_the_wrapper_still_calls_through_to_the_base_handler(self):
        """AC #9's indicator clause, and mutation #8.

        ``Actor.handle_bar`` updates registered indicators **before** the
        ``RUNNING`` gate and **outside** its ``try`` (``actor.pyx:3735-3744``),
        so a wrapper that returns early once latched freezes them — measured
        over 8 bars with an ``EMA(3)``: ``ema.count`` 1 rather than 8. A frozen
        indicator is not merely stale: ``sma_crossover`` keeps its own
        ``_prev_fast_sma``, so a resumed strategy could fire a crossover the
        market never justified (NFR14).
        """
        strategy = StubStrategy(raises=RuntimeError("boom"))
        _guard().wrap(strategy, spec_strategy_id="sma_crossover")

        for _ in range(8):
            strategy.handle_bar(object())

        assert len(strategy.bar_calls) == 8

    def test_two_strategies_each_get_their_own_latch(self):
        guard = _guard()
        first = StubStrategy(strategy_id="A-000", raises=RuntimeError("boom"))
        second = StubStrategy(strategy_id="B-001", raises=RuntimeError("boom"))
        guard.wrap(first, spec_strategy_id="sma_crossover")
        guard.wrap(second, spec_strategy_id="momentum")

        first.handle_bar(object())
        second.handle_bar(object())

        assert sorted(f.spec_strategy_id for f in guard.failures) == ["momentum", "sma_crossover"]


class TestTheIsolationVerb:
    """AC #7 — ``degrade()``, guarded by ``is_running``, and latched first."""

    def test_degrade_is_attempted_when_the_strategy_is_running(self):
        log = RecordingLog()
        strategy = StubStrategy(raises=RuntimeError("boom"), running=True)
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        assert strategy.degrade_calls == 1
        assert "strategy.degraded" in log.events("warning")

    def test_degrade_is_not_attempted_when_the_strategy_is_not_running(self):
        """``degrade()`` from a non-``RUNNING`` state is *silently swallowed*
        by Nautilus (measured: ``READY -> degrade() -> still READY``), so
        calling it there would report an isolation that never happened.
        """
        log = RecordingLog()
        guard = _guard(log)
        strategy = StubStrategy(raises=RuntimeError("boom"), running=False)
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        assert strategy.degrade_calls == 0
        assert "strategy.degraded" not in log.events()
        # The failure is still contained, recorded and latched — only the
        # isolation verb is skipped, because it would be a silent no-op.
        assert len(guard.failures) == 1
        assert log.events().count("strategy.failed") == 1

    def test_a_strategy_whose_degrade_is_a_no_op_is_still_latched(self):
        """The latch is set **before** ``degrade()`` is attempted (AC #9), so a
        ``degrade()`` that does nothing cannot produce one contained failure per
        bar for the rest of the session.
        """
        log = RecordingLog()
        strategy = StubStrategy(raises=RuntimeError("boom"))
        strategy.degrade = lambda: None  # legal from Nautilus's point of view; changes nothing
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        for _ in range(4):
            strategy.handle_bar(object())

        assert log.events().count("strategy.failed") == 1


class TestTheGuardGuardsItself:
    """AC #10 — nothing propagates out of a wrapped handler, ever.

    Measured, three modes of a real ``LiveDataEngine`` in a fresh interpreter:
    no guard -> ``rc=1`` with 0 bytes; guard -> ``rc=0``; **guard whose
    ``except`` block itself raises -> ``rc=1`` with 0 bytes**. This class is the
    unit-tier half of mutation #9.
    """

    def test_a_raising_degrade_does_not_propagate(self):
        strategy = StubStrategy(raises=RuntimeError("boom"))
        strategy.degrade_raises = RuntimeError("degrade is broken too")
        _guard().wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is None

    def test_a_raising_logger_does_not_propagate(self):
        strategy = StubStrategy(raises=RuntimeError("boom"))
        _guard(RaisingLog()).wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is None

    def test_a_strategy_whose_id_raises_does_not_propagate(self):
        """The id is read *inside* the guard's own ``try`` — a property that
        raises is one more thing the boundary must absorb, not re-raise.
        """

        class ExplodingId(StubStrategy):
            @property
            def id(self):
                raise RuntimeError("the id is broken")

            @id.setter
            def id(self, value):
                pass

        strategy = ExplodingId(raises=RuntimeError("boom"))
        _guard().wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is None

    def test_a_guard_failure_is_reported_as_the_ar42_event(self):
        log = RecordingLog()
        strategy = StubStrategy(raises=RuntimeError("boom"))
        strategy.degrade_raises = RuntimeError("degrade is broken too")
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        assert "session.strategy_record_failed" in log.events("error")

    def test_a_reclaim_shaped_exception_from_the_base_handler_is_absorbed_too(self):
        """AC #10's named case. ``SessionReclaimedError`` is the one exception
        every *other* caller in this codebase re-raises; here it must not, and
        that asymmetry is Judgment call #10.
        """
        from src.core.live_session_record import SessionReclaimedError

        strategy = StubStrategy(raises=SessionReclaimedError("taken"))
        _guard().wrap(strategy, spec_strategy_id="sma_crossover")

        assert strategy.handle_bar(object()) is None


class TestThePendingQueue:
    """AC #10 — the guard queues; it never writes on the calling thread."""

    def test_drain_returns_each_failure_exactly_once_and_empties_the_queue(self):
        guard = _guard()
        first = StubStrategy(strategy_id="A-000", raises=RuntimeError("boom"))
        second = StubStrategy(strategy_id="B-001", raises=RuntimeError("boom"))
        guard.wrap(first, spec_strategy_id="sma_crossover")
        guard.wrap(second, spec_strategy_id="momentum")
        first.handle_bar(object())
        second.handle_bar(object())

        drained = guard.drain_pending()

        assert len(drained) == 2
        assert guard.drain_pending() == ()
        # `failures` is the whole run's history, for the CLI's report; it is
        # *not* drained.
        assert len(guard.failures) == 2

    def test_requeued_failures_come_back_oldest_first_on_the_next_drain(self):
        """The steady-state tick puts a failure back when its DB write failed
        for a reason other than a reclaim (review fix, 2026-08-23), so the
        record is retried rather than lost for the rest of a multi-week run.
        Front of the queue, so a failure contained between the failed write and
        the retry does not jump the order.
        """
        guard = _guard()
        first = StubStrategy(strategy_id="A-000", raises=RuntimeError("boom"))
        second = StubStrategy(strategy_id="B-001", raises=RuntimeError("boom"))
        guard.wrap(first, spec_strategy_id="sma_crossover")
        guard.wrap(second, spec_strategy_id="momentum")
        first.handle_bar(object())
        drained = guard.drain_pending()
        second.handle_bar(object())

        guard.requeue(drained)
        redrained = guard.drain_pending()

        assert [f.spec_strategy_id for f in redrained] == ["sma_crossover", "momentum"]
        assert guard.drain_pending() == ()

    def test_the_guard_module_calls_no_record_port_method_at_all(self):
        """Mutation-proof for "no I/O on the calling thread", asserted
        structurally rather than by watching a double.

        A double can only prove *this* test's path made no call. The AST scan
        proves the module has no route to the database at all, which is the
        property AC #10 actually needs — the write belongs to
        ``SessionSteadyState``'s tick, on that object's own executor.
        """
        source = (PROJECT_ROOT / "src/core/live_strategy_guard.py").read_text(encoding="utf-8")
        called = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        forbidden = {"record_strategy_failure", "record_activity", "mark_stopped"}

        assert not called & forbidden
        # Non-vacuity: the scan does find the calls the guard genuinely makes.
        assert "append" in called


class TestAllStrategiesFailed:
    """AC #9's last clause — the session runs on, and says so."""

    def test_all_failed_is_false_until_every_expected_strategy_has_failed(self):
        guard = _guard()
        guard.expect(2)
        first = StubStrategy(strategy_id="A-000", raises=RuntimeError("boom"))
        guard.wrap(first, spec_strategy_id="sma_crossover")

        first.handle_bar(object())

        assert guard.all_failed is False

    def test_all_failed_flips_when_the_last_live_strategy_fails(self):
        log = RecordingLog()
        guard = _guard(log)
        guard.expect(2)
        first = StubStrategy(strategy_id="A-000", raises=RuntimeError("boom"))
        second = StubStrategy(strategy_id="B-001", raises=RuntimeError("boom"))
        guard.wrap(first, spec_strategy_id="sma_crossover")
        guard.wrap(second, spec_strategy_id="momentum")

        first.handle_bar(object())
        second.handle_bar(object())

        assert guard.all_failed is True
        assert "session.all_strategies_failed" in log.events("error")

    def test_all_failed_is_false_before_expect_is_ever_called(self):
        guard = _guard()
        strategy = StubStrategy(raises=RuntimeError("boom"))
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        assert guard.all_failed is False

    def test_a_start_failure_counts_towards_all_failed(self):
        guard = _guard()
        guard.expect(1)

        guard.record_start_failure(spec_strategy_id="sma_crossover", exc=RuntimeError("no start"))

        assert guard.all_failed is True


class TestTheStartPathRecord:
    """AC #5 — a spec that never started is recorded exactly like a runtime one."""

    def test_a_start_failure_is_queued_and_logged(self):
        log = RecordingLog()
        guard = _guard(log)

        guard.record_start_failure(spec_strategy_id="sma_crossover", exc=ValueError("no cache"))

        (failure,) = guard.failures
        assert failure.spec_strategy_id == "sma_crossover"
        assert failure.error_type == "ValueError"
        assert failure.handler == "start"
        assert failure.strategy_id == ""
        assert guard.drain_pending() == (failure,)
        (fields,) = log.fields_for("strategy.start_failed")
        assert fields["spec_strategy_id"] == "sma_crossover"
        assert "Traceback" in fields["traceback"] or "ValueError" in fields["traceback"]

    def test_a_start_failure_latches_the_spec_so_a_later_raise_is_not_double_counted(self):
        guard = _guard()

        guard.record_start_failure(spec_strategy_id="sma_crossover", exc=ValueError("no cache"))
        guard.record_start_failure(spec_strategy_id="sma_crossover", exc=ValueError("again"))

        assert len(guard.failures) == 1

    def test_a_raising_logger_does_not_propagate_out_of_the_start_path_either(self):
        guard = _guard(RaisingLog())

        guard.record_start_failure(spec_strategy_id="sma_crossover", exc=ValueError("no cache"))


class TestRedactAccounts:
    """AC #8 — account tokens are replaced *in place*, and the payload survives."""

    def test_the_account_token_is_masked_and_the_rest_of_the_message_survives(self):
        out = redact_accounts(IBKR_ACCOUNT_ERROR)

        assert "DU4076626" not in out
        assert "***626" in out
        # Mutation #6: `redact_accounts = mask_account` returns '***ged', which
        # passes the first assertion and fails these two. Without them the test
        # cannot fail against an implementation that destroys the payload.
        assert "is not managed" in out
        assert out.startswith("Error 321: account ")

    def test_a_traceback_keeps_its_frames_and_its_exception_type(self):
        traceback_text = (
            'Traceback (most recent call last):\n  File "src/core/strategies/sma_crossover.py", '
            "line 150, in _calculate_position_size\n    raw_qty = position_value / current_price\n"
            "decimal.DivisionByZero: [<class 'decimal.DivisionByZero'>]\n"
            f"context: {IBKR_ACCOUNT_ERROR}"
        )

        out = redact_accounts(traceback_text)

        assert "DU4076626" not in out
        assert "sma_crossover.py" in out
        assert "DivisionByZero" in out

    @pytest.mark.parametrize("account", ["DU4076626", "U1234567", "DF12345678", "DU1234567890"])
    def test_every_ibkr_account_shape_is_matched(self, account):
        assert account not in redact_accounts(f"account {account} is not managed")

    @pytest.mark.parametrize(
        "text",
        [
            "line 150, in _calculate_position_size",
            "ZeroDivisionError: division by zero",
            "bar_type=AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        ],
    )
    def test_ordinary_traceback_text_is_left_alone(self, text):
        assert redact_accounts(text) == text

    def test_an_empty_string_survives(self):
        assert redact_accounts("") == ""

    def test_every_occurrence_is_redacted_not_only_the_first(self):
        out = redact_accounts(f"{IBKR_ACCOUNT_ERROR}; also DU4076626 again")

        assert "DU4076626" not in out
        assert out.count("***626") == 2

    def test_the_configured_account_is_redacted_even_when_the_shape_misses(self):
        """AC #8's pinned clause, "plus the configured TWS_ACCOUNT when set"
        (review fix, 2026-08-23). The planted value is lowercase — trivially
        produced by a strategy that lowercases its message before raising — so
        the shape-based token cannot match it and only the configured-value
        pass can.
        """
        out = redact_accounts(
            "account du4076626 is not managed; also DU4076626", account="DU4076626"
        )

        assert "du4076626" not in out
        assert "DU4076626" not in out
        assert "is not managed" in out

    def test_no_configured_account_changes_nothing(self):
        assert redact_accounts(IBKR_ACCOUNT_ERROR, account=None) == redact_accounts(
            IBKR_ACCOUNT_ERROR
        )
        assert redact_accounts("plain text", account="") == "plain text"

    def test_a_guard_built_with_an_account_redacts_it_from_the_detail(self):
        strategy = StubStrategy(raises=RuntimeError("account du4076626 is not managed"))
        guard = StrategyGuard(log=RecordingLog(), time_source=_clock, account="DU4076626")
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        (failure,) = guard.failures
        assert "du4076626" not in failure.detail
        assert "is not managed" in failure.detail

    def test_the_detail_written_to_the_column_is_redacted(self):
        strategy = StubStrategy(raises=RuntimeError(IBKR_ACCOUNT_ERROR))
        guard = _guard()
        guard.wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        (failure,) = guard.failures
        assert "DU4076626" not in failure.detail
        assert "is not managed" in failure.detail

    def test_the_logged_traceback_is_redacted(self):
        log = RecordingLog()
        strategy = StubStrategy(raises=RuntimeError(IBKR_ACCOUNT_ERROR))
        _guard(log).wrap(strategy, spec_strategy_id="sma_crossover")

        strategy.handle_bar(object())

        (fields,) = log.fields_for("strategy.failed")
        assert "DU4076626" not in fields["traceback"]
        assert "***626" in fields["traceback"]
        assert "is not managed" in fields["traceback"]
