"""Unit tests for the catch-up gate (Story 5.9, AC #3; PO rulings 2026-10-12).

A bar the strategy handles more than :data:`CATCH_UP_GRACE_SECONDS` after its
period closed is a catch-up bar: the IB adapter replays an outage's bars in one
burst once the bar feed is resubscribed (F7), each stamped with its own period
end (F8), so only the wall clock at handling can tell. The indicators see every
such bar; no order may act on one (PO ruling R1: "ignore the missed prices for
trading").

Real Nautilus ``Bar`` objects, a duck-typed monitor and a fake strategy clock,
so every boundary is driven exactly. Every "nothing happened" assertion has a
sibling proving the same spy records when something does (Epic 2 retro).
"""

from types import SimpleNamespace

import pytest
import structlog
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from structlog.testing import capture_logs

from src.core.live_catch_up_gate import (
    CATCH_UP_EVENT,
    CATCH_UP_GRACE_SECONDS,
    CATCH_UP_ORDER_WITHHELD_EVENT,
    CatchUpGate,
)
from src.core.live_market_data import DEFAULT_DELAYED_DATA_GRACE_SECONDS
from src.core.live_order_path import ORDER_CREATING_METHODS, SUPPRESSED_EVENT, install_order_path

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/epics.md"
AC_5_9C = (
    "bars that closed before connection.restored and arrive after the resubscribe reach the "
    "indicators, no order acts on them (withheld and named), and the number skipped is "
    "recorded once per reconnect for each strategy and bar type"
)

NS = 1_000_000_000
MINUTE_NS = 60 * NS
BAR_TYPE = BarType.from_str("NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL")
RAW_ACCOUNT = "DU4076626"
# 2026-10-12T18:47:00Z, the P23 transcript's last bar before the freeze.
T0 = 1_791_830_820 * NS


def _bar(minute: int, bar_type: BarType = BAR_TYPE) -> Bar:
    """A one-minute bar opening ``minute`` minutes after ``T0``."""
    ts_event = T0 + minute * MINUTE_NS
    price = Price.from_str("226.70")
    return Bar(
        bar_type,
        price,
        price,
        price,
        price,
        Quantity.from_int(100),
        ts_event,
        ts_event + MINUTE_NS,
    )


def _closes_at(minute: int) -> int:
    return T0 + (minute + 1) * MINUTE_NS


class _Monitor:
    def __init__(self, withheld: bool = False) -> None:
        self.submission_withheld = withheld


class _Clock:
    def __init__(self, now_ns: int) -> None:
        self.now_ns = now_ns

    def timestamp_ns(self) -> int:
        return self.now_ns


def _log():
    return structlog.get_logger("test").bind(session_id="s-1")


def _gate(monitor: _Monitor, clock: _Clock) -> CatchUpGate:
    return CatchUpGate(monitor, clock=clock.timestamp_ns, log=_log())


def _events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


def _no_account(logs) -> None:
    for entry in logs:
        assert RAW_ACCOUNT not in repr(entry), entry


class TestTheGrace:
    def test_it_is_the_delayed_data_grace(self):
        """A duplicated literal needs its own equality pin (CLAUDE.md): the gate
        stays framework-free, so it cannot import ``live_market_data``'s copy,
        and "late" must mean the same to the freshness guard and the gate (R1).
        """
        assert CATCH_UP_GRACE_SECONDS == DEFAULT_DELAYED_DATA_GRACE_SECONDS == 120.0


class TestWithholding:
    @pytest.mark.acceptance_criterion("5.9c", AC_5_9C, SOURCE)
    def test_a_live_bar_does_not_withhold(self):
        clock = _Clock(_closes_at(0) + 5 * NS)
        gate = _gate(_Monitor(), clock)

        gate.note_bar(_bar(0))

        assert gate.submission_withheld is False

    @pytest.mark.acceptance_criterion("5.9c", AC_5_9C, SOURCE)
    def test_a_catch_up_bar_withholds_and_names_itself(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            withheld = gate.submission_withheld

        assert withheld is True
        [record] = _events(logs, CATCH_UP_ORDER_WITHHELD_EVENT)
        assert record["bar_type"] == str(BAR_TYPE)
        assert record["ts_event"] == "2026-10-12T18:47:00+00:00"
        assert record["log_level"] == "warning"
        _no_account(logs)

    def test_the_grace_itself_is_still_live(self):
        clock = _Clock(_closes_at(0) + int(CATCH_UP_GRACE_SECONDS * NS))
        gate = _gate(_Monitor(), clock)

        gate.note_bar(_bar(0))

        assert gate.submission_withheld is False

    def test_one_nanosecond_past_the_grace_is_catch_up(self):
        clock = _Clock(_closes_at(0) + int(CATCH_UP_GRACE_SECONDS * NS) + 1)
        gate = _gate(_Monitor(), clock)

        gate.note_bar(_bar(0))

        assert gate.submission_withheld is True

    def test_the_monitor_still_withholds_on_a_live_bar(self):
        clock = _Clock(_closes_at(0) + 5 * NS)
        gate = _gate(_Monitor(withheld=True), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            withheld = gate.submission_withheld

        assert withheld is True
        assert _events(logs, CATCH_UP_ORDER_WITHHELD_EVENT) == []

    def test_the_monitor_is_named_as_the_cause_when_both_withhold(self):
        """Only the gate's own reason is the gate's record: when the monitor
        already withholds, ``order.suppressed`` alone says why, as today."""
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(withheld=True), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            withheld = gate.submission_withheld

        assert withheld is True
        assert _events(logs, CATCH_UP_ORDER_WITHHELD_EVENT) == []

    def test_no_bar_yet_defers_to_the_monitor(self):
        gate = _gate(_Monitor(), _Clock(T0))

        assert gate.submission_withheld is False

    def test_a_live_bar_after_a_burst_lifts_the_withhold(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(), clock)
        gate.note_bar(_bar(0))
        assert gate.submission_withheld is True

        clock.now_ns = _closes_at(5) + 5 * NS
        gate.note_bar(_bar(5))

        assert gate.submission_withheld is False

    def test_a_malformed_bar_never_raises(self):
        """``note_bar`` runs inside the strategy's ``handle_bar``: a raise there
        would be a contained strategy failure caused by us."""
        gate = _gate(_Monitor(), _Clock(T0))

        gate.note_bar(SimpleNamespace())

        assert gate.submission_withheld is False

    def test_a_malformed_bar_after_a_catch_up_bar_is_judged_on_its_own(self):
        """Code review P1 (2026-10-13): each bar is judged by itself. A bar the
        gate cannot read must not inherit the previous bar's verdict, which would
        withhold silently after a catch-up bar and permit silently after a live one."""
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(), clock)
        gate.note_bar(_bar(0))
        assert gate.submission_withheld is True

        gate.note_bar(SimpleNamespace())

        assert gate.submission_withheld is False


class TestTheBurstRecord:
    @pytest.mark.acceptance_criterion("5.9c", AC_5_9C, SOURCE)
    def test_one_record_per_burst_when_the_first_live_bar_follows(self):
        """The P23 outage, replayed at 18:56:10: the 18:48..18:53 bars closed
        more than the grace before, so they are the burst; the 18:54 bar (closed
        70 s before) is as fresh as the freshness guard accepts (R1), so it is
        the live bar that ends the burst and gets the record."""
        clock = _Clock(_closes_at(8) + 10 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            for minute in range(1, 7):
                gate.note_bar(_bar(minute))
            assert _events(logs, CATCH_UP_EVENT) == []

            gate.note_bar(_bar(7))
            gate.note_bar(_bar(8))

        [record] = _events(logs, CATCH_UP_EVENT)
        assert record["count"] == 6
        assert record["bar_type"] == str(BAR_TYPE)
        assert record["first_ts_event"] == "2026-10-12T18:48:00+00:00"
        assert record["last_ts_event"] == "2026-10-12T18:53:00+00:00"
        assert gate.submission_withheld is False
        _no_account(logs)

    def test_a_second_burst_gets_its_own_record(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            clock.now_ns = _closes_at(5) + 5 * NS
            gate.note_bar(_bar(5))
            clock.now_ns = _closes_at(10) + 300 * NS
            gate.note_bar(_bar(10))
            gate.note_bar(_bar(11))
            clock.now_ns = _closes_at(16) + 5 * NS
            gate.note_bar(_bar(16))

        assert [entry["count"] for entry in _events(logs, CATCH_UP_EVENT)] == [1, 2]

    def test_live_bars_alone_emit_nothing(self):
        clock = _Clock(_closes_at(0) + 5 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            for minute in range(3):
                clock.now_ns = _closes_at(minute) + 5 * NS
                gate.note_bar(_bar(minute))

        assert _events(logs, CATCH_UP_EVENT) == []

    def test_an_rth_open_republish_is_withheld_but_never_counted(self):
        """The adapter re-delivers the previous session's last bar after every
        RTH gap (``live_bar_observer``'s "Delayed-feed detection", fact (2)). It
        is hours past its close, so it is withheld, but it is not a burst: a
        multi-day session must not log a phantom catch-up at every open."""
        clock = _Clock(_closes_at(0) + 5 * NS)
        gate = _gate(_Monitor(), clock)
        gate.note_bar(_bar(0))

        with capture_logs() as logs:
            clock.now_ns = _closes_at(0) + 17 * 3600 * NS
            gate.note_bar(_bar(0))
            withheld = gate.submission_withheld
            clock.now_ns = _closes_at(1020) + 5 * NS
            gate.note_bar(_bar(1020))

        assert withheld is True
        assert _events(logs, CATCH_UP_EVENT) == []

    def test_bar_types_are_counted_apart(self):
        other = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
        clock = _Clock(_closes_at(2) + 300 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            gate.note_bar(_bar(0, other))
            gate.note_bar(_bar(1))
            clock.now_ns = _closes_at(9) + 5 * NS
            gate.note_bar(_bar(9))
            gate.note_bar(_bar(9, other))

        counts = {entry["bar_type"]: entry["count"] for entry in _events(logs, CATCH_UP_EVENT)}
        assert counts == {str(BAR_TYPE): 2, str(other): 1}


class _SpyStrategy(SimpleNamespace):
    """Every order-creating method a spy, plus the ``handle_bar`` and
    ``clock`` a registered strategy has, so the order path can wrap it."""

    def __init__(self, clock: _Clock) -> None:
        super().__init__(id="SMACrossover-000", calls=[], handled=[], clock=clock)
        for name in ORDER_CREATING_METHODS:
            setattr(self, name, lambda *args, _name=name, **kwargs: self.calls.append(_name))
        self.handle_bar = self.handled.append


class TestThroughTheOrderPath:
    """``install_order_path`` installs the gate on the strategy's own
    ``handle_bar`` (D-E as amended at dev): the gate judges the exact bar the
    strategy reacts to, and the runner gains no statement."""

    @pytest.mark.acceptance_criterion("5.9c", AC_5_9C, SOURCE)
    def test_an_order_on_a_catch_up_bar_is_suppressed_and_the_bar_still_handled(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        strategy = _SpyStrategy(clock)
        install_order_path(strategy, _Monitor(), _log())

        with capture_logs() as logs:
            strategy.handle_bar(_bar(0))
            strategy.submit_order(SimpleNamespace(client_order_id="O-1"))

        assert strategy.handled == [_bar(0)]
        assert strategy.calls == []
        assert len(_events(logs, CATCH_UP_ORDER_WITHHELD_EVENT)) == 1
        assert _events(logs, SUPPRESSED_EVENT)[0]["method"] == "submit_order"
        _no_account(logs)

    def test_an_order_on_a_live_bar_is_submitted(self):
        clock = _Clock(_closes_at(0) + 5 * NS)
        strategy = _SpyStrategy(clock)
        install_order_path(strategy, _Monitor(), _log())

        strategy.handle_bar(_bar(0))
        strategy.submit_order(SimpleNamespace(client_order_id="O-1"))

        assert strategy.handled == [_bar(0)]
        assert strategy.calls == ["submit_order"]

    def test_every_order_creating_method_is_judged_by_the_gate(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        strategy = _SpyStrategy(clock)
        install_order_path(strategy, _Monitor(), _log())
        strategy.handle_bar(_bar(0))

        for name in sorted(ORDER_CREATING_METHODS):
            getattr(strategy, name)(SimpleNamespace(client_order_id="O-1"))

        assert strategy.calls == []

    def test_installing_twice_notes_each_bar_once(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        strategy = _SpyStrategy(clock)
        install_order_path(strategy, _Monitor(), _log())
        install_order_path(strategy, _Monitor(), _log())

        strategy.handle_bar(_bar(0))

        assert strategy.handled == [_bar(0)]

    def test_both_records_name_the_strategy_as_it_is_registered(self):
        """Code review P10 (DN3, Allay 2026-10-13): the gate is per strategy, so
        its records carry ``strategy_id``. Read per record, never at install:
        ``Trader.add_strategy`` rewrites the id after ``install_order_path``."""
        clock = _Clock(_closes_at(0) + 300 * NS)
        strategy = _SpyStrategy(clock)
        install_order_path(strategy, _Monitor(), _log())
        strategy.id = "SMACrossover-007"

        with capture_logs() as logs:
            strategy.handle_bar(_bar(0))
            strategy.submit_order(SimpleNamespace(client_order_id="O-1"))
            clock.now_ns = _closes_at(5) + 5 * NS
            strategy.handle_bar(_bar(5))

        [withheld] = _events(logs, CATCH_UP_ORDER_WITHHELD_EVENT)
        [burst] = _events(logs, CATCH_UP_EVENT)
        assert withheld["strategy_id"] == burst["strategy_id"] == "SMACrossover-007"

    def test_a_gate_without_a_strategy_writes_no_strategy_id(self):
        clock = _Clock(_closes_at(0) + 300 * NS)
        gate = _gate(_Monitor(), clock)

        with capture_logs() as logs:
            gate.note_bar(_bar(0))
            assert gate.submission_withheld is True

        [record] = _events(logs, CATCH_UP_ORDER_WITHHELD_EVENT)
        assert "strategy_id" not in record

    def test_a_strategy_without_handle_bar_is_judged_by_the_monitor_alone(self):
        strategy = _SpyStrategy(_Clock(T0))
        del strategy.handle_bar
        monitor = _Monitor()
        install_order_path(strategy, monitor, _log())

        strategy.submit_order(SimpleNamespace(client_order_id="O-1"))
        monitor.submission_withheld = True
        strategy.submit_order(SimpleNamespace(client_order_id="O-2"))

        assert strategy.calls == ["submit_order"]
