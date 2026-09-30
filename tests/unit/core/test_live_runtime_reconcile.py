"""Unit tests for the running session's reconciliation cycle (Story 4.3, AC #2-#4).

Duck-typed doubles for the node, its exec engine and cache — Story 4.2's, from
``test_live_startup_reconcile.py``, widened with ``orders_inflight()`` — a real
``ConnectionMonitor`` (framework-free) and an injected clock, so every cadence,
debounce and throttle is driven exactly. The real ``LiveExecutionEngine`` proofs
live in ``tests/component/core/test_live_runtime_reconcile_engine.py``.

Every "nothing happened" assertion has a sibling proving the same spy records
when something does (Epic 2 retro: a test that cannot fail).
"""

import asyncio
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import InstrumentId
from structlog.testing import capture_logs

from src.core.exit_outcome import EXIT_CODES, EXIT_ERROR
from src.core.live_broker_state import BrokerStateFailure, BrokerStateUnavailableError
from src.core.live_check import classify_failure
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionState, ConnectionStatus
from src.core.live_runtime_reconcile import (
    CYCLE_FAILED_EVENT,
    DEBOUNCE_SECONDS,
    RUNTIME_OK_LOG_INTERVAL_SECONDS,
    RUNTIME_RECONCILE_EVERY_TICKS,
    SCOPE_RECONNECT,
    SCOPE_RUNTIME,
    RuntimeReconciler,
    grant_after_reconciliation,
)
from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
)
from tests.unit.core.test_live_startup_reconcile import (
    AAPL,
    NVDA,
    NVDA_EQUITY,
    RAW_ACCOUNT,
    STRATEGY,
    _Cache,
    _Engine,
    _held,
    _Position,
    _state,
)

pytestmark = pytest.mark.unit

UP = ConnectionStatus(connected=True, detail="up")
DOWN = ConnectionStatus(connected=False, detail="down")
TICK = 30.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _RuntimeCache(_Cache):
    """Story 4.2's cache double plus the in-flight view the cycle defers on."""

    def __init__(self, *args, inflight=(), **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.inflight = list(inflight)

    def orders_inflight(self):
        return list(self.inflight)


class _Reader:
    """The broker read: a settable state, a call log, and optional side effects."""

    def __init__(self, state=None) -> None:
        self.state = state if state is not None else _state()
        self.raises: BaseException | None = None
        self.during = None
        self.calls: list = []

    async def __call__(self, node, *, log):
        self.calls.append(log)
        if self.during is not None:
            self.during()
        if self.raises is not None:
            raise self.raises
        return self.state


class _Connection:
    def __init__(self) -> None:
        self.status = UP
        self.raises: BaseException | None = None

    def __call__(self, settings):
        if self.raises is not None:
            raise self.raises
        return self.status


class _World:
    """One session's worth of doubles, with the monitor already granted."""

    def __init__(self, positions=(), *, state=None, inflight=(), **engine_kwargs) -> None:
        self.clock = _Clock()
        self.cache = _RuntimeCache(positions, inflight=inflight)
        self.engine = _Engine(self.cache, **engine_kwargs)
        self.node = SimpleNamespace(
            cache=self.cache, kernel=SimpleNamespace(exec_engine=self.engine)
        )
        self.monitor = ConnectionMonitor(session_id="s-1", time_source=self.clock)
        self.monitor.confirm_state_reestablished(UP)
        self.reader = _Reader(state)
        self.connection = _Connection()
        self.reconciler = RuntimeReconciler(
            node=self.node,
            monitor=self.monitor,
            log=_log(),
            connection_reader=self.connection,
            settings=object(),
            read_state=self.reader,
            clock=self.clock,
        )

    def tick(self, times: int = 1) -> None:
        for _ in range(times):
            self.clock.now += TICK
            self.monitor.observe(self.connection.status)
            asyncio.run(self.reconciler.on_tick())

    def cycles(self, times: int = 1) -> None:
        """Advance whole runtime cycles (``RUNTIME_RECONCILE_EVERY_TICKS`` ticks each)."""
        self.tick(times * RUNTIME_RECONCILE_EVERY_TICKS)

    def lose_and_recover(self) -> None:
        self.connection.status = DOWN
        self.clock.now += TICK
        self.monitor.observe(DOWN)
        self.connection.status = UP
        self.clock.now += 5
        self.monitor.observe(UP)
        assert self.monitor.state is ConnectionState.RECOVERING


def _log():
    import structlog

    return structlog.get_logger("test").bind(session_id="s-1")


def _events(logs, name):
    return [e for e in logs if e["event"] == name]


class TestCadence:
    def test_a_connected_session_runs_one_cycle_every_n_ticks(self):
        world = _World()

        world.tick(4 * RUNTIME_RECONCILE_EVERY_TICKS)

        assert len(world.reader.calls) == 4

    def test_nothing_is_read_while_the_connection_is_lost_or_halted(self):
        world = _World()
        world.connection.status = DOWN

        world.tick(6)

        assert world.monitor.state in (ConnectionState.LOST, ConnectionState.HALTED)
        assert world.reader.calls == []

    def test_nothing_is_read_before_the_first_connection(self):
        world = _World()
        world.monitor = ConnectionMonitor(session_id="s-1", time_source=world.clock)
        world.reconciler._monitor = world.monitor

        asyncio.run(world.reconciler.on_tick())

        assert world.reader.calls == []

    def test_recovering_reads_every_tick(self):
        world = _World()
        world.lose_and_recover()
        # An in-flight order holds the grant back, so the state stays RECOVERING.
        world.cache.inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(NVDA))]

        for _ in range(3):
            world.clock.now += TICK
            asyncio.run(world.reconciler.on_tick())

        assert len(world.reader.calls) == 3


class TestTheReadersOwnRecordsAreQuieted:
    """F9: the reader logs every read at INFO — ~390 lines a day at 60 s."""

    def test_the_reader_gets_a_logger_that_drops_its_records(self):
        world = _World()

        async def _chatty(node, *, log):
            log.info("reconcile.broker_state_retrieved", positions={})
            log.error("reconcile.broker_state_failed")
            return _state()

        world.reconciler._read_state = _chatty
        with capture_logs() as logs:
            world.cycles(3)

        assert not _events(logs, "reconcile.broker_state_retrieved")
        assert not _events(logs, "reconcile.broker_state_failed")
        assert _events(logs, OK_EVENT), "premise: the cycle's own records are captured"

    def test_only_the_two_per_read_records_are_dropped(self):
        """Code review of PR #35: F9 names two records. Story 4.1 D-F's
        ``reconcile.broker_instrument_unresolved`` (WARNING, once per unresolved
        contract) is the only place the contract's symbol and error type are
        named, and at runtime it must still reach the log."""
        world = _World()

        async def _unresolved(node, *, log):
            log.info("reconcile.broker_state_retrieved", positions={})
            log.warning(
                "reconcile.broker_instrument_unresolved", con_id=1, symbol="X", sec_type="BOND"
            )
            return _state()

        world.reconciler._read_state = _unresolved
        with capture_logs() as logs:
            world.cycles(1)

        assert not _events(logs, "reconcile.broker_state_retrieved")
        (record,) = _events(logs, "reconcile.broker_instrument_unresolved")
        assert (record["symbol"], record["sec_type"]) == ("X", "BOND")


class TestDebounce:
    def test_a_discrepancy_seen_once_changes_nothing(self):
        world = _World(state=_state(_held(NVDA, "5")))

        with capture_logs() as logs:
            world.cycles(1)

        assert world.engine.reports == []
        assert not _events(logs, DISCREPANCY_EVENT)

    def test_seen_identically_on_two_cycles_it_resolves_broker_ward(self):
        world = _World(state=_state(_held(NVDA, "5", price="101.25")))

        with capture_logs() as logs:
            world.cycles(2)

        (report,) = world.engine.reports
        assert str(report.instrument_id) == NVDA
        assert report.position_side is PositionSide.LONG
        assert str(report.avg_px_open) == "101.25"
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["resolution"] == "broker"
        assert record["scope"] == SCOPE_RUNTIME
        assert record["log_level"] == "warning"
        assert (record["instrument_id"], record["local_quantity"], record["broker_quantity"]) == (
            NVDA,
            "0",
            "5",
        )

    def test_a_changed_row_restarts_the_debounce(self):
        world = _World(state=_state(_held(NVDA, "5")))
        world.cycles(1)
        world.reader.state = _state(_held(NVDA, "6"))

        world.cycles(1)

        assert world.engine.reports == []

    def test_identical_rows_less_than_the_debounce_apart_do_not_act(self):
        """Reconnect cycles run every 30 s tick; the rule is two identical
        observations **at least** ``DEBOUNCE_SECONDS`` apart (PO ruling 2A)."""
        world = _World(state=_state(_held(NVDA, "5")))
        world.lose_and_recover()

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())
        world.clock.now += DEBOUNCE_SECONDS / 2
        asyncio.run(world.reconciler.on_tick())
        assert world.engine.reports == []

        world.clock.now += DEBOUNCE_SECONDS / 2
        asyncio.run(world.reconciler.on_tick())
        assert len(world.engine.reports) == 1

    def test_a_stale_synthetic_position_is_resolved_with_a_flat_report(self):
        world = _World([_Position(AAPL, "EXTERNAL", "4")], state=_state())

        world.cycles(2)

        (report,) = world.engine.reports
        assert report.position_side is PositionSide.FLAT


class TestTheCacheMovingDuringTheReadSkipsTheCycle:
    def test_a_fill_during_the_read_is_not_acted_on_and_makes_no_progress(self):
        world = _World(state=_state(_held(NVDA, "5")))
        world.cycles(1)

        def _fill():
            world.cache.positions.append(_Position(AAPL, STRATEGY, "1"))

        world.reader.during = _fill
        with capture_logs() as logs:
            world.cycles(1)

        assert world.engine.reports == []
        assert not _events(logs, DISCREPANCY_EVENT)

    def test_without_the_move_the_same_second_cycle_acts(self):
        """The sibling: the only difference from the test above is the move."""
        world = _World(state=_state(_held(NVDA, "5")))

        world.cycles(2)

        assert len(world.engine.reports) == 1


class TestOnlyAnInFlightOrderDefersItsInstrument:
    """PO ruling 2A: an in-flight order defers; an ordinary open one does not."""

    def test_an_in_flight_order_defers_the_instrument_and_is_named(self):
        inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(NVDA))]
        world = _World(state=_state(_held(NVDA, "5")), inflight=inflight)

        with capture_logs() as logs:
            world.cycles(3)

        assert world.engine.reports == []
        (ok,) = _events(logs, OK_EVENT)
        assert ok["deferred_instruments"] == [NVDA]

    def test_a_deferral_on_a_throttled_cycle_is_named_in_the_next_record(self):
        world = _World()
        world.cycles(1)  # the first record goes out; the next is an hour away
        world.cache.inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(NVDA))]
        world.cycles(1)
        world.cache.inflight = []
        per_hour = int(RUNTIME_OK_LOG_INTERVAL_SECONDS / (TICK * RUNTIME_RECONCILE_EVERY_TICKS))

        with capture_logs() as logs:
            world.cycles(per_hour)

        (ok,) = _events(logs, OK_EVENT)
        assert ok["deferred_instruments"] == [NVDA]

    def test_an_open_accepted_order_does_not_defer(self):
        """Measured (Task 1.5): an ACCEPTED order stranded across a restart
        stays open forever; deferring on it would make its instrument
        unverifiable for the rest of the session."""
        world = _World(state=_state(_held(NVDA, "5")))
        world.cache._orders_open = [SimpleNamespace(instrument_id=InstrumentId.from_str(NVDA))]

        world.cycles(2)

        assert len(world.engine.reports) == 1


class TestUnresolvableRows:
    def test_an_instrument_the_cache_does_not_hold_is_logged_once_and_the_session_continues(
        self,
    ):
        world = _World(state=_state(_held("MSFT.NASDAQ", "3")))

        with capture_logs() as logs:
            world.cycles(4)

        assert world.engine.reports == []
        records = _events(logs, DISCREPANCY_EVENT)
        assert [(r["instrument_id"], r["resolution"]) for r in records] == [
            ("MSFT.NASDAQ", "unresolved")
        ]
        assert records[0]["log_level"] == "error"

    def test_an_unresolved_broker_row_holds_back_only_the_rows_it_could_mask(self):
        """An ``IB-CONID-*`` row makes the cache's own row for the same holding
        read "broker 0" — acting on that would stop a session over a lookup
        miss. Every other instrument is still corrected (PO ruling 2026-09-28)."""
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(_held("IB-CONID-4815747", "22", resolved=False), _held(AAPL, "5")),
        )

        with capture_logs() as logs:
            world.cycles(3)

        assert [str(r.instrument_id) for r in world.engine.reports] == [AAPL]
        records = {(r["instrument_id"], r["resolution"]) for r in _events(logs, DISCREPANCY_EVENT)}
        assert records == {(AAPL, "broker"), ("IB-CONID-4815747", "unresolved")}

    def test_an_unresolved_broker_row_is_logged_only_once_confirmed(self):
        world = _World(state=_state(_held("IB-CONID-4815747", "22", resolved=False)))

        with capture_logs() as first:
            world.cycles(1)
        with capture_logs() as second:
            world.cycles(3)

        assert not _events(first, DISCREPANCY_EVENT)
        assert [r["resolution"] for r in _events(second, DISCREPANCY_EVENT)] == ["unresolved"]

    def test_an_instrument_the_cache_does_not_hold_never_withholds_the_grant(self):
        world = _World(state=_state(_held("MSFT.NASDAQ", "3")))
        world.cycles(2)  # confirmed and logged unresolved before the drop
        world.lose_and_recover()

        for _ in range(4):
            world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED

    def test_an_unresolved_broker_row_withholds_the_grant(self):
        """The sibling: a lookup miss may hide a holding, so it fails closed."""
        world = _World(state=_state(_held("IB-CONID-4815747", "22", resolved=False)))
        world.lose_and_recover()

        for _ in range(4):
            world.tick()

        assert world.monitor.state is not ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is True


class TestAFrameworkRefusalStopsTheSession:
    def test_a_false_return_is_refused_and_raised(self):
        world = _World(state=_state(_held(NVDA, "5")), returns=False)

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.RESOLUTION_REFUSED
        assert caught.value.scope == SCOPE_RUNTIME
        assert [r["resolution"] for r in _events(logs, DISCREPANCY_EVENT)] == ["refused"]

    def test_an_engine_exception_is_the_typed_refusal(self):
        world = _World(state=_state(_held(NVDA, "5")))

        def _boom(report):
            raise RuntimeError("engine")

        world.engine.reconcile_execution_report = _boom
        with pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.RESOLUTION_REFUSED

    def test_a_correction_that_changes_nothing_is_caught_by_the_re_read(self):
        world = _World(state=_state(_held(NVDA, "5")), applies=False)

        with pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.DISCREPANCY_REMAINS

    def test_corrections_that_took_are_logged_before_a_later_refusal_stops_the_cycle(self):
        world = _World(state=_state(_held(AAPL, "3"), _held(NVDA, "5")))
        real = world.engine.reconcile_execution_report

        def _refuse_nvda(report):
            return False if str(report.instrument_id) == NVDA else real(report)

        world.engine.reconcile_execution_report = _refuse_nvda
        with capture_logs() as logs, pytest.raises(ReconciliationFailedError):
            world.cycles(2)

        records = [(r["instrument_id"], r["resolution"]) for r in _events(logs, DISCREPANCY_EVENT)]
        assert records == [(AAPL, "broker"), (NVDA, "refused")]


class TestAContradictedStrategyPositionStopsTheSession:
    """D-D, PO ruling 2A: refuse and stop — nothing written to the cache."""

    def test_it_is_refused_before_anything_is_written(self):
        world = _World([_Position(NVDA, STRATEGY, "22")], state=_state())

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert world.engine.reports == [], "a contradicted strategy row reached the framework"
        assert [p.signed_decimal_qty() for p in world.cache.positions_open()] == [Decimal(22)]
        error = caught.value
        assert error.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert error.scope == SCOPE_RUNTIME
        assert NVDA in str(error) and "+22" in str(error) and "broker +0" in str(error)
        assert "stopped" in str(error)
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert (record["kind"], record["resolution"], record["reason"], record["log_level"]) == (
            "strategy_position",
            "refused",
            "strategy_position_contradicted",
            "error",
        )

    def test_it_maps_to_exit_1(self):
        world = _World([_Position(NVDA, STRATEGY, "22")], state=_state())

        with pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert EXIT_CODES[classify_failure(caught.value)] == EXIT_ERROR

    def test_a_covering_broker_holding_over_mixed_sides_still_stops_the_running_session(self):
        """Story 4.5's relaxation is **startup only** (PO ruling 2026-09-28).
        Since Story 4.7's coverage rule it decides something only for
        strategies on both sides of one instrument (integration merge,
        2026-09-28): A +20 and B −10 against a broker at +15, the net already
        corrected, is covered by the strategies' net yet still stops the
        running session — the exemption is never read here."""
        world = _World(
            [
                _Position(NVDA, STRATEGY, "20"),
                _Position(NVDA, "SMAMomentum-001", "-10"),
                _Position(NVDA, "INTERNAL-DIFF", "5"),
            ],
            state=_state(_held(NVDA, "15")),
        )

        with pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert world.engine.reports == []

    def test_a_covering_holding_the_net_already_absorbed_is_clean_while_running(self):
        """The same excess over one strategy's own lot is Story 4.7's covered
        growth (PO ruling A — runtime included), not a contradiction: this is
        the state its runtime correction leaves, so the cycle is clean and
        nothing is written. (Story 4.5's pre-4.7 pin of a stop here was
        superseded at the integration merge, 2026-09-28.)"""
        world = _World(
            [_Position(NVDA, STRATEGY, "10"), _Position(NVDA, "INTERNAL-DIFF", "5")],
            state=_state(_held(NVDA, "15")),
        )

        with capture_logs() as logs:
            world.cycles(2)

        assert world.engine.reports == []
        assert not _events(logs, DISCREPANCY_EVENT)
        assert _events(logs, OK_EVENT), "premise: the cycle ran and its records are captured"

    def test_seen_once_it_does_not_stop_the_session(self):
        """The strategy's own fill can reach the cache before IB's position does."""
        world = _World([_Position(NVDA, STRATEGY, "22")], state=_state())

        world.cycles(1)
        world.reader.state = _state(_held(NVDA, "22"))
        world.cycles(1)

        assert world.engine.reports == []


class TestACorporateActionIsAbsorbedWhileRunning:
    """Story 4.7, D-A (PO ruling A): a split that grows a strategy's position
    mid-session is corrected broker-ward and named — the session keeps running
    and the strategy's own lot is never touched."""

    def test_a_forward_split_is_corrected_named_and_the_session_continues(self):
        world = _World([_Position(NVDA, STRATEGY, "10")], state=_state(_held(NVDA, "20")))

        with capture_logs() as logs:
            world.cycles(2)  # seen twice, DEBOUNCE_SECONDS apart
            world.cycles(1)  # the next cycle is clean

        (report,) = world.engine.reports
        assert report.signed_decimal_qty == Decimal("20")
        own = [p.signed_decimal_qty() for p in world.cache.positions if p.strategy_id == STRATEGY]
        assert own == [Decimal("10")], "the strategy's own lot was adjusted"
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert (record["resolution"], record["scope"], record["kind"]) == (
            "broker",
            SCOPE_RUNTIME,
            "position",
        )
        assert (record["local_quantity"], record["strategy_quantity"]) == ("10", "10")
        assert record["broker_quantity"] == "20"
        assert _events(logs, OK_EVENT), "no clean cycle followed the correction"

    def test_seen_once_a_split_changes_nothing(self):
        """The debounce still applies: a broker position that lags a fill looks
        exactly like a split for one cycle."""
        world = _World([_Position(NVDA, STRATEGY, "10")], state=_state(_held(NVDA, "20")))

        world.cycles(1)

        assert world.engine.reports == []


class TestAnUncoveredStrategyPositionStillStopsTheSession:
    """Story 4.7, PO ruling: a strictly-shrinking, zero or opposite-side broker
    quantity still stops the session, before anything is written, and the stop
    names the likely cause."""

    @pytest.mark.parametrize(
        ("broker", "phrase"),
        [(("5",), "reverse split"), ((), "cash merger"), (("-5",), "opposite side")],
        ids=["shrinking", "zero", "opposite-side"],
    )
    def test_each_shape_stops_the_session_and_names_its_cause(self, broker, phrase):
        held = tuple(_held(NVDA, quantity) for quantity in broker)
        world = _World([_Position(NVDA, STRATEGY, "10")], state=_state(*held))

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert world.engine.reports == [], "an uncovered strategy row reached the framework"
        assert phrase in str(caught.value)
        # Code review: true for every refused shape, not only "the broker holds none".
        assert "no longer covers a strategy's own position" in str(caught.value)
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["resolution"] == "refused" and phrase in record["likely_cause"]
        assert RAW_ACCOUNT not in str(caught.value)


class TestReconcileOkVolume:
    """AC #3."""

    def test_a_clean_rth_day_logs_at_most_eight_records_and_accounts_for_every_cycle(self):
        world = _World([_Position(NVDA, STRATEGY, "22")], state=_state(_held(NVDA, "22")))

        with capture_logs() as logs:
            world.cycles(390)

        records = _events(logs, OK_EVENT)
        assert 1 <= len(records) <= 8
        assert all(r["scope"] == SCOPE_RUNTIME and r["log_level"] == "info" for r in records)
        assert records[0]["cycles"] == 1, "the first clean cycle is reported at once"
        assert sum(r["cycles"] for r in records) + world.reconciler.unreported_clean_cycles == 390
        assert records[0]["instruments"] == {NVDA: "22"}

    def test_the_throttle_is_hourly(self):
        world = _World()
        per_hour = int(RUNTIME_OK_LOG_INTERVAL_SECONDS / (TICK * RUNTIME_RECONCILE_EVERY_TICKS))

        with capture_logs() as logs:
            world.cycles(per_hour + 1)

        assert len(_events(logs, OK_EVENT)) == 2

    def test_a_clean_cycle_after_a_non_clean_one_is_reported_at_once(self):
        world = _World(state=_state(_held(NVDA, "5")))
        world.cycles(1)
        with capture_logs() as first:
            world.cycles(1)
        assert _events(first, DISCREPANCY_EVENT)

        with capture_logs() as logs:
            world.cycles(1)

        (record,) = _events(logs, OK_EVENT)
        assert record["cycles"] == 1


class TestAFailedCycleIsContainedAndNotRepeated:
    def test_a_persistent_read_failure_is_logged_once_then_closed_by_the_next_ok(self):
        world = _World()
        world.cycles(1)
        world.reader.raises = BrokerStateUnavailableError(
            BrokerStateFailure.POSITIONS_UNANSWERED, "the positions request was not answered"
        )

        with capture_logs() as failing:
            world.cycles(10)

        (record,) = _events(failing, CYCLE_FAILED_EVENT)
        assert (record["reason"], record["consecutive"], record["log_level"]) == (
            "positions_unanswered",
            1,
            "warning",
        )

        world.reader.raises = None
        with capture_logs() as recovered:
            world.cycles(1)
        assert _events(recovered, OK_EVENT)

    def test_an_unexpected_exception_never_leaves_the_tick(self):
        world = _World()
        world.reader.raises = KeyError("anything")

        world.cycles(2)

        assert world.monitor.state is ConnectionState.CONNECTED

    def test_no_record_carries_the_raw_account_or_exception_text(self):
        world = _World([_Position(NVDA, STRATEGY, "22")], state=_state(_held(AAPL, "3")))
        world.reader.raises = RuntimeError(f"account {RAW_ACCOUNT} leaked")

        with capture_logs() as logs:
            world.cycles(2)
            world.reader.raises = None
            world.cycles(1)
            with pytest.raises(ReconciliationFailedError) as caught:
                world.cycles(1)

        assert logs
        assert all(RAW_ACCOUNT not in repr(record) for record in logs)
        assert RAW_ACCOUNT not in str(caught.value)


class TestTheReconnectReEstablishesStateBeforeTheGrant:
    """AC #4 — permission returns only through ``confirm_state_reestablished``,
    and only after a clean cycle (PO: "only after the state check passes")."""

    def test_a_clean_reconnect_cycle_logs_ok_then_the_monitor_restores(self):
        world = _World()
        world.lose_and_recover()

        with capture_logs() as logs:
            world.clock.now += TICK
            asyncio.run(world.reconciler.on_tick())

        assert world.monitor.state is ConnectionState.CONNECTED
        names = [e["event"] for e in logs if e["event"] in (OK_EVENT, "connection.restored")]
        assert names == [OK_EVENT, "connection.restored"]
        assert _events(logs, OK_EVENT)[0]["scope"] == SCOPE_RECONNECT

    def test_the_grant_goes_through_confirm_state_reestablished_only(self):
        world = _World()
        world.lose_and_recover()
        calls = []
        original = world.monitor.confirm_state_reestablished
        world.monitor.confirm_state_reestablished = lambda s: calls.append(s) or original(s)

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())

        assert calls == [UP]

    def test_an_in_flight_order_withholds_the_grant(self):
        inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(AAPL))]
        world = _World(inflight=inflight)
        world.lose_and_recover()

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())

        assert world.monitor.state is ConnectionState.RECOVERING
        assert world.monitor.submission_withheld is True

    def test_a_discrepancy_withholds_the_grant_until_resolved_and_verified(self):
        """Driven through real ticks (observe, then the cycle), so the monitor's
        own halt clock runs as it does in a session: the debounce outlasts the
        60 s window, the halt is reported, and the grant still follows the
        correction and a clean cycle — never precedes them."""
        world = _World(state=_state(_held(NVDA, "5")))
        world.lose_and_recover()

        states = []
        with capture_logs() as logs:
            for _ in range(8):
                world.tick()
                states.append((len(world.engine.reports), world.monitor.state))
                if world.monitor.state is ConnectionState.CONNECTED:
                    break

        assert world.monitor.state is ConnectionState.CONNECTED
        assert len(world.engine.reports) == 1
        assert all(
            reports == 1 for reports, state in states if state is ConnectionState.CONNECTED
        ), "permission returned before the discrepancy was corrected"
        names = [
            (e["event"], e.get("resolution"))
            for e in logs
            if e["event"] in (DISCREPANCY_EVENT, OK_EVENT, "connection.restored")
        ]
        assert names == [
            (DISCREPANCY_EVENT, "broker"),
            (OK_EVENT, None),
            ("connection.restored", None),
        ]

    def test_a_failed_read_withholds_the_grant(self):
        world = _World()
        world.lose_and_recover()
        world.reader.raises = BrokerStateUnavailableError(
            BrokerStateFailure.NOT_CONNECTED, "not connected"
        )

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())

        assert world.monitor.state is ConnectionState.RECOVERING

    def test_a_reading_that_drops_at_confirm_time_is_not_granted(self):
        world = _World()
        world.lose_and_recover()
        world.connection.status = DOWN

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())

        assert world.monitor.state is not ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is True

    def test_a_refused_grant_does_not_repeat_the_ok_every_tick(self):
        world = _World()
        world.lose_and_recover()
        world.connection.status = DOWN  # every confirm-time reading drops

        with capture_logs() as logs:
            for _ in range(3):
                world.clock.now += TICK
                asyncio.run(world.reconciler.on_tick())

        assert len(_events(logs, OK_EVENT)) == 1

    def test_a_row_seen_before_the_loss_must_be_seen_twice_after_it(self):
        world = _World(state=_state(_held(NVDA, "5")))
        world.cycles(1)  # first sighting, still debouncing
        world.lose_and_recover()

        world.clock.now += DEBOUNCE_SECONDS
        asyncio.run(world.reconciler.on_tick())

        assert world.engine.reports == [], "a pre-loss sighting confirmed a post-loss row"

    def test_past_the_window_the_halt_fires_and_a_clean_cycle_still_restores(self):
        world = _World()
        world.reader.raises = BrokerStateUnavailableError(
            BrokerStateFailure.NOT_CONNECTED, "not connected"
        )
        world.lose_and_recover()
        with capture_logs() as logs:
            for _ in range(4):
                world.tick()
            world.reader.raises = None
            world.tick()

        assert _events(logs, "connection.halted")
        assert world.monitor.state is ConnectionState.CONNECTED


class TestGrantAfterReconciliation:
    """The startup grant, called by the runner at the end of ``reconcile``."""

    def test_a_connected_reading_grants(self):
        monitor = ConnectionMonitor(session_id="s-1")
        monitor.observe(UP)

        state = grant_after_reconciliation(monitor, lambda settings: UP, object(), _log())

        assert state is ConnectionState.CONNECTED
        assert monitor.trading_permitted is True

    def test_a_disconnected_reading_does_not_grant_and_does_not_raise(self):
        monitor = ConnectionMonitor(session_id="s-1")

        state = grant_after_reconciliation(monitor, lambda settings: DOWN, object(), _log())

        assert state is not ConnectionState.CONNECTED
        assert monitor.submission_withheld is True

    def test_a_reader_that_raises_is_contained(self):
        monitor = ConnectionMonitor(session_id="s-1")

        def _raises(settings):
            raise RuntimeError("probe")

        with capture_logs() as logs:
            state = grant_after_reconciliation(monitor, _raises, object(), _log())

        assert state is None
        assert monitor.submission_withheld is True
        assert _events(logs, "session.connection_read_failed")


def test_the_unit_doubles_are_what_the_cycle_reads():
    """Guard for this file's own premise: the stub engine records reports."""
    world = _World(state=_state(_held(NVDA, "5")))
    world.engine.reconcile_execution_report(
        SimpleNamespace(instrument_id=NVDA_EQUITY.id, signed_decimal_qty=Decimal(5))
    )
    assert len(world.engine.reports) == 1
