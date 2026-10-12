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
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import InstrumentId
from structlog.testing import capture_logs

from src.core.exit_outcome import EXIT_CODES, EXIT_ERROR
from src.core.live_broker_state import (
    BrokerStateAdapterError,
    BrokerStateFailure,
    BrokerStateUnavailableError,
)
from src.core.live_check import classify_failure
from src.core.live_connection_monitor import ConnectionMonitor, ConnectionState, ConnectionStatus
from src.core.live_order_path import ORDER_CREATING_METHODS, SUPPRESSED_EVENT, install_order_path
from src.core.live_runtime_reconcile import (
    ACTION_SESSION_STOPPED,
    ACTION_TRADING_WITHHELD,
    CYCLE_FAILED_EVENT,
    DEBOUNCE_SECONDS,
    HOLD_BACK_CYCLES,
    HOLD_BACK_EXPIRED_EVENT,
    RUNTIME_OK_LOG_INTERVAL_SECONDS,
    RUNTIME_RECONCILE_EVERY_TICKS,
    SCOPE_RECONNECT,
    SCOPE_RUNTIME,
    WITHHOLD_AFTER_FAILED_CYCLES,
    RuntimeReconciler,
    WithheldPeriod,
    WithheldPeriods,
    grant_after_reconciliation,
)
from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    INEXPRESSIBLE_REMAINDER_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
)
from src.core.live_stranded_orders import StaleOrderWatch
from tests.unit.core.test_live_startup_reconcile import (
    AAPL,
    BOND,
    NVDA,
    NVDA_EQUITY,
    RAW_ACCOUNT,
    STRATEGY,
    _Cache,
    _Engine,
    _held,
    _net,
    _Position,
    _state,
)

pytestmark = pytest.mark.unit

#: Story 5.8's acceptance criteria, as ``prd-epic5-scope.md`` words them.
SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
AC_5_8A = (
    "three consecutive failed cycles withdraw trading permission through RECOVERING (orders "
    "withheld, order.suppressed), log one reconcile.cycle_failed at ERROR naming the streak, "
    "and the next clean cycle re-grants through confirm_state_reestablished as a reconnect does"
)
AC_5_8B = (
    "a BrokerStateAdapterError stops the session with exit 1 on its first occurrence, as "
    "startup treats adapter drift; the hourly throttle does not apply"
)
AC_5_8D = (
    "the three-cycle threshold is a named constant with a test that fails if a streak of two "
    "withholds or a streak of three does not"
)

UP = ConnectionStatus(connected=True, detail="up")
DOWN = ConnectionStatus(connected=False, detail="down")
TICK = 30.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


#: The wall instant ``_Clock`` reads 1000.0 at — Story 5.8's persisted stamps.
WALL_ORIGIN = datetime(2026, 10, 11, 14, 0, tzinfo=UTC)


class _WallClock:
    """Aware UTC wall time that advances with the monotonic ``_Clock``, so a
    withheld period's stamps are exact."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    def __call__(self) -> datetime:
        return WALL_ORIGIN + timedelta(seconds=self._clock.now - 1000.0)


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

    def __init__(
        self, positions=(), *, state=None, inflight=(), traded=None, **engine_kwargs
    ) -> None:
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
        self.wall = _WallClock(self.clock)
        self.reconciler = RuntimeReconciler(
            node=self.node,
            monitor=self.monitor,
            log=_log(),
            connection_reader=self.connection,
            settings=object(),
            read_state=self.reader,
            clock=self.clock,
            wall_clock=self.wall,  # Story 5.8: a withheld period's persisted stamps
            # Code review R1: the session's traded instruments; omitted, the default.
            **({} if traded is None else {"traded_instruments": traded}),
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
        miss. Every other instrument is still corrected (PO ruling 2026-09-28).
        The hold-back is by symbol and lasts ``HOLD_BACK_CYCLES`` (PR #35 D1)."""
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(
                _held("IB-CONID-4815747", "22", resolved=False, symbol="NVDA"), _held(AAPL, "5")
            ),
        )

        with capture_logs() as logs:
            world.cycles(HOLD_BACK_CYCLES)

        assert [str(r.instrument_id) for r in world.engine.reports] == [AAPL]
        records = {(r["instrument_id"], r["resolution"]) for r in _events(logs, DISCREPANCY_EVENT)}
        assert records == {(AAPL, "broker"), ("IB-CONID-4815747", "unresolved")}
        assert not _events(logs, HOLD_BACK_EXPIRED_EVENT)

    def test_an_unresolved_row_of_another_symbol_holds_nothing_back(self):
        """D1 (PR #35 code review, PO ruling 2026-09-30): a bond, a warrant or a
        delisted symbol the adapter cannot build is not the holding behind
        NVDA's "broker 0" — so a strategy's +22 sold by hand is refused, not
        masked for the life of the session (NFR14)."""
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(_held("IB-CONID-99", "1000", resolved=False, symbol="T 4.5 02/15/36")),
        )

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert [row.instrument_id for row in caught.value.discrepancies] == [NVDA]
        assert world.engine.reports == []
        refused = [r for r in _events(logs, DISCREPANCY_EVENT) if r["resolution"] == "refused"]
        assert [r["instrument_id"] for r in refused] == [NVDA]

    def test_the_hold_back_ends_after_the_bound_and_the_masked_row_is_acted_on(self):
        """The same-symbol hold-back is bounded: on the cycle after
        ``HOLD_BACK_CYCLES`` the expiry is named once and the held-back row is
        refused — fail closed, rather than masked forever."""
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(_held("IB-CONID-4815747", "22", resolved=False, symbol="NVDA")),
        )

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(HOLD_BACK_CYCLES + 1)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        (expired,) = _events(logs, HOLD_BACK_EXPIRED_EVENT)
        assert expired["log_level"] == "warning"
        assert (expired["instrument_id"], expired["symbol"], expired["cycles"]) == (
            "IB-CONID-4815747",
            "NVDA",
            HOLD_BACK_CYCLES,
        )
        assert expired["scope"] == SCOPE_RUNTIME

    def test_a_cleared_then_returning_unresolved_row_starts_the_bound_over(self):
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(_held("IB-CONID-4815747", "22", resolved=False, symbol="NVDA")),
        )
        world.cycles(HOLD_BACK_CYCLES - 1)
        world.reader.state = _state(_held(NVDA, "22"))  # resolved again: clean
        world.cycles(1)
        world.reader.state = _state(_held("IB-CONID-4815747", "22", resolved=False, symbol="NVDA"))

        with capture_logs() as logs:
            world.cycles(HOLD_BACK_CYCLES)

        assert not _events(logs, HOLD_BACK_EXPIRED_EVENT)
        assert world.engine.reports == []

    @pytest.mark.parametrize("ib_symbol", ["BRK B", "BRK-B", "brk b", "BRK.B"])
    def test_the_symbol_match_survives_the_brokers_spelling(self, ib_symbol):
        """IB writes class shares ``BRK B`` where Nautilus writes ``BRK-B``."""
        brk = "BRK-B.NYSE"
        world = _World(
            [_Position(brk, STRATEGY, "4")],
            state=_state(_held("IB-CONID-7", "4", resolved=False, symbol=ib_symbol)),
        )

        world.cycles(HOLD_BACK_CYCLES)  # held back: no refusal, nothing written

        assert world.engine.reports == []

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

    def test_an_unresolved_broker_row_of_a_held_symbol_withholds_the_grant(self):
        """The sibling: a lookup miss may hide a holding, so it fails closed.
        Story 5.7 (AC #3, D-A) narrows it to the row that *could* — one under a
        symbol a cached position is held under; before 5.7 any unresolved row
        withheld it, on an empty cache too (that case is now AC #2's)."""
        world = _World(
            [_Position(NVDA, STRATEGY, "22")],
            state=_state(
                _held(NVDA, "22"), _held("IB-CONID-4815747", "3", resolved=False, symbol="NVDA")
            ),
        )
        world.lose_and_recover()

        for _ in range(HOLD_BACK_CYCLES + 3):
            world.tick()

        assert world.monitor.state is not ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is True


class TestAnUnresolvedHoldingOfAnUnrelatedSymbolNeverWithholdsTheGrant:
    """Story 5.7, AC #2 (PO ruling D2): an ``IB-CONID-*`` row under a symbol no
    cached position is held under is named once and left out — a recovered
    disconnect is re-granted beside it, and the running session reports clean."""

    def test_a_recovered_disconnect_is_re_granted_beside_it(self):
        world = _World([_Position(AAPL, STRATEGY, "5")], state=_state(_held(AAPL, "5"), BOND))
        world.cycles(2)  # confirmed and named before the loss
        world.lose_and_recover()

        with capture_logs() as logs:
            for _ in range(4):
                world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is False
        assert [e["scope"] for e in _events(logs, OK_EVENT)] == [SCOPE_RECONNECT]

    def test_a_row_first_seen_after_the_loss_is_re_granted_on_the_first_cycle(self):
        """Code review P4: an unrelated row is inert from its first sighting —
        it is never acted on, so it has nothing to debounce. Judging inertness
        from the *debounced* rows withheld the grant for at least one debounce
        window (>= 60 s), and a flickering row could withhold it forever (AC #2)."""
        world = _World(state=_state(BOND))
        world.lose_and_recover()

        world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is False

    def test_a_flickering_unrelated_row_never_withholds_the_grant(self):
        """Each sighting is a first sighting to the debounce; inert regardless."""
        world = _World(state=_state(BOND))
        world.cycles(1)
        world.reader.state = _state()
        world.cycles(1)
        world.reader.state = _state(BOND)
        world.lose_and_recover()

        world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is False

    def test_it_is_named_once_and_never_expires_a_hold_back_it_never_held(self):
        """D-G: D1's counter runs only for rows that could mask a cached position."""
        world = _World(state=_state(BOND))

        with capture_logs() as logs:
            world.cycles(HOLD_BACK_CYCLES + 3)

        records = [(r["instrument_id"], r["resolution"]) for r in _events(logs, DISCREPANCY_EVENT)]
        assert records == [("IB-CONID-99", "unresolved")]
        assert not _events(logs, HOLD_BACK_EXPIRED_EVENT)
        assert _events(logs, OK_EVENT), "the running session reports clean beside it"
        assert world.engine.reports == []


class TestATradedInstrumentCountsAsHeldWhileRunning:
    """Code review R1 (PO ruling 2026-10-07): the reconnect grant and D1's
    hold-back count the symbols of the instruments the session's strategies
    trade as held, beside the cached positions' own."""

    LOOKUP_MISS = _held("IB-CONID-4815747", "3", resolved=False, symbol="NVDA")

    def test_a_flat_strategy_beside_a_same_symbol_row_withholds_the_grant(self):
        world = _World(state=_state(self.LOOKUP_MISS), traded=[NVDA])
        world.lose_and_recover()

        for _ in range(HOLD_BACK_CYCLES + 3):
            world.tick()

        assert world.monitor.state is not ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is True

    def test_another_traded_symbol_does_not_withhold_it(self):
        """Non-vacuity: the withholding above is the traded set's doing."""
        world = _World(state=_state(self.LOOKUP_MISS), traded=[AAPL])
        world.lose_and_recover()

        world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED

    def test_a_row_matching_neither_never_withholds_it(self):
        world = _World([_Position(AAPL, STRATEGY, "5")], state=_state(_held(AAPL, "5"), BOND))
        world.reconciler = RuntimeReconciler(
            node=world.node,
            monitor=world.monitor,
            log=_log(),
            connection_reader=world.connection,
            settings=object(),
            read_state=world.reader,
            clock=world.clock,
            traded_instruments=[NVDA, AAPL],
        )
        world.lose_and_recover()

        world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED
        assert world.monitor.submission_withheld is False

    def test_d1s_hold_back_counts_a_traded_symbol(self):
        """D-G feeds the hold-back only rows that may mask; a traded symbol is
        one, so its bound runs (and is named when it ends)."""
        world = _World(state=_state(self.LOOKUP_MISS), traded=[NVDA])

        with capture_logs() as logs:
            world.cycles(HOLD_BACK_CYCLES + 1)

        (expired,) = _events(logs, HOLD_BACK_EXPIRED_EVENT)
        assert expired["symbol"] == "NVDA"


class TestAFractionalRemainderIsNamedOnceWhileRunning:
    """Story 5.7, AC #4: judged at whole shares, named once at WARNING, and
    carried by every ``reconcile.ok`` while it stands — never a silent ok."""

    def _world(self, broker: str = "22.5", *positions) -> _World:
        return _World(
            list(positions) or [_Position(NVDA, STRATEGY, "22")],
            state=_state(_held(NVDA, broker)),
        )

    def test_it_is_clean_named_once_and_carried_by_reconcile_ok(self):
        world = self._world()

        with capture_logs() as logs:
            world.cycles(5)

        assert world.engine.reports == []
        assert _events(logs, DISCREPANCY_EVENT) == []
        (named,) = _events(logs, INEXPRESSIBLE_REMAINDER_EVENT)
        assert (named["scope"], named["log_level"]) == (SCOPE_RUNTIME, "warning")
        assert (named["broker_quantity"], named["expressible_quantity"], named["remainder"]) == (
            "22.5",
            "22",
            "0.5",
        )
        (ok,) = _events(logs, OK_EVENT)
        assert ok["inexpressible_remainders"] == {NVDA: "0.5"}
        assert ok["instruments"] == {NVDA: "22.5"}, "D-D: the broker is reported exact"

    def test_a_clean_session_with_nothing_inexpressible_says_so(self):
        world = self._world("22")

        with capture_logs() as logs:
            world.cycles(2)

        assert _events(logs, OK_EVENT)[0]["inexpressible_remainders"] == {}
        assert _events(logs, INEXPRESSIBLE_REMAINDER_EVENT) == []

    def test_a_changed_remainder_is_named_again(self):
        world = self._world()
        world.cycles(2)
        world.reader.state = _state(_held(NVDA, "22.7"))

        with capture_logs() as logs:
            world.cycles(3)

        assert [r["remainder"] for r in _events(logs, INEXPRESSIBLE_REMAINDER_EVENT)] == ["0.7"]

    def test_an_unchanged_remainder_is_not_renamed_when_the_strategy_trades(self):
        """Code review P2 (P22b's Fail criterion): a strategy buying one share
        on a DRIP instrument moves the broker 22.5 -> 23.5 and leaves the
        remainder at 0.5 — the same standing fact, so it is not named again.
        Keying "already named" on the whole record (both quantities) did."""
        world = self._world()
        world.cycles(2)
        world.cache.positions = [_Position(NVDA, STRATEGY, "23")]
        world.reader.state = _state(_held(NVDA, "23.5"))

        with capture_logs() as logs:
            world.cycles(3)

        assert _events(logs, INEXPRESSIBLE_REMAINDER_EVENT) == []
        assert _events(logs, DISCREPANCY_EVENT) == []

    def test_a_remainder_whose_value_changes_is_named_again(self):
        world = self._world()
        world.cycles(2)
        world.reader.state = _state(_held(NVDA, "22.75"))

        with capture_logs() as logs:
            world.cycles(3)

        assert [r["remainder"] for r in _events(logs, INEXPRESSIBLE_REMAINDER_EVENT)] == ["0.75"]

    def test_a_cleared_then_returning_remainder_is_named_again(self):
        world = self._world()
        world.cycles(1)
        world.reader.state = _state(_held(NVDA, "22"))
        world.cycles(1)
        world.reader.state = _state(_held(NVDA, "22.5"))

        with capture_logs() as logs:
            world.cycles(2)

        assert len(_events(logs, INEXPRESSIBLE_REMAINDER_EVENT)) == 1

    def test_a_remainder_first_seen_on_a_reconnect_cycle_is_named_and_grants(self):
        world = self._world("22")
        world.cycles(1)
        world.reader.state = _state(_held(NVDA, "22.5"))
        world.lose_and_recover()

        with capture_logs() as logs:
            world.tick()

        assert world.monitor.state is ConnectionState.CONNECTED
        (named,) = _events(logs, INEXPRESSIBLE_REMAINDER_EVENT)
        assert named["scope"] == SCOPE_RECONNECT
        (ok,) = _events(logs, OK_EVENT)
        assert ok["inexpressible_remainders"] == {NVDA: "0.5"}

    def test_the_frameworks_over_import_is_corrected_down_and_verifies_clean(self):
        """The re-verify judges at the same precision, or it would read the
        corrected ``22`` against the exact ``22.5`` and stop the session."""
        world = self._world(
            "22.5", _Position(NVDA, STRATEGY, "22"), _Position(NVDA, "INTERNAL-DIFF", "1")
        )

        with capture_logs() as logs:
            world.cycles(2)

        (report,) = world.engine.reports
        assert report.signed_decimal_qty == Decimal(22)
        assert _net(world.cache, NVDA) == Decimal(22)
        resolutions = [r["resolution"] for r in _events(logs, DISCREPANCY_EVENT)]
        assert resolutions == ["broker"]

    def test_truncation_never_rounds_a_shortfall_into_cover_and_names_it_first(self):
        world = self._world("23.5", _Position(NVDA, STRATEGY, "24"))

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            world.cycles(2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert caught.value.discrepancies[0].broker_quantity == Decimal(23)
        assert len(_events(logs, INEXPRESSIBLE_REMAINDER_EVENT)) == 1
        assert world.engine.reports == []


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
    def test_a_persistent_read_failure_warns_once_then_errors_once_at_the_threshold(self):
        """Changed deliberately by Story 5.8 (AC #1; was
        ``test_a_persistent_read_failure_is_logged_once_then_closed_by_the_next_ok``).
        A streak used to be one WARNING and nothing else for as long as it
        lasted (PR #35 D3). Now its third consecutive failure withholds trading
        and says so once, at ERROR; the hourly throttle still governs every
        other record. Recovery is a reconnect cycle's ``reconcile.ok`` and a
        re-grant."""
        world = _World()
        world.cycles(1)
        world.reader.raises = BrokerStateUnavailableError(
            BrokerStateFailure.POSITIONS_UNANSWERED, "the positions request was not answered"
        )

        with capture_logs() as failing:
            world.cycles(10)

        records = _events(failing, CYCLE_FAILED_EVENT)
        assert [(r["consecutive"], r["log_level"]) for r in records] == [
            (1, "warning"),
            (3, "error"),
        ]
        assert all(r["reason"] == "positions_unanswered" for r in records)
        assert world.monitor.trading_permitted is False

        world.reader.raises = None
        with capture_logs() as recovered:
            world.tick()
        assert _events(recovered, OK_EVENT)[0]["scope"] == SCOPE_RECONNECT
        assert world.monitor.state is ConnectionState.CONNECTED

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


def _unanswered() -> BrokerStateUnavailableError:
    return BrokerStateUnavailableError(
        BrokerStateFailure.POSITIONS_UNANSWERED, "the positions request was not answered"
    )


def _drift() -> BrokerStateAdapterError:
    return BrokerStateAdapterError(
        "distinct contracts resolve to one instrument, so positions cannot be told apart: "
        "{'NVDA.NASDAQ': [4815747, 4815748]}"
    )


def _withheld(world: "_World", streak: int = 3) -> None:
    """A granted session, then ``streak`` failed runtime cycles."""
    world.cycles(1)
    world.reader.raises = _unanswered()
    world.cycles(streak)


class _SpyStrategy(SimpleNamespace):
    """Every order-creating method a spy, so the order path can wrap it."""

    def __init__(self) -> None:
        super().__init__(id="SMACrossover-000", calls=[])
        for name in ORDER_CREATING_METHODS:
            setattr(self, name, lambda *args, _name=name, **kwargs: self.calls.append(_name))


class TestAPersistentFailureWithholdsTrading:
    """Story 5.8, AC #1 and #4 (PO ruling D3: "withhold after 3")."""

    @pytest.mark.acceptance_criterion("5.8d", AC_5_8D, SOURCE)
    def test_the_threshold_is_a_named_constant_of_three(self):
        assert WITHHOLD_AFTER_FAILED_CYCLES == 3

    @pytest.mark.acceptance_criterion("5.8d", AC_5_8D, SOURCE)
    @pytest.mark.parametrize(
        "streak", [2, WITHHOLD_AFTER_FAILED_CYCLES - 1], ids=["literal", "constant"]
    )
    def test_a_streak_of_two_does_not_withhold(self, streak):
        """AC #4, one side: fails if two failed cycles ever withhold — against
        the literal and the constant both (code review P8)."""
        world = _World()

        _withheld(world, streak=streak)

        assert world.monitor.state is ConnectionState.CONNECTED
        assert world.monitor.trading_permitted is True
        assert world.reconciler.withheld.pending() == ()

    @pytest.mark.acceptance_criterion("5.8d", AC_5_8D, SOURCE)
    @pytest.mark.parametrize(
        "streak", [3, WITHHOLD_AFTER_FAILED_CYCLES], ids=["literal", "constant"]
    )
    def test_a_streak_of_three_withholds(self, streak):
        """AC #4, the other side: fails if three failed cycles do not."""
        world = _World()

        _withheld(world, streak=streak)

        assert world.monitor.state is ConnectionState.RECOVERING
        assert world.monitor.trading_permitted is False
        assert world.monitor.submission_withheld is True

    @pytest.mark.acceptance_criterion("5.8a", AC_5_8A, SOURCE)
    def test_the_third_failure_logs_one_error_naming_the_streak(self):
        world = _World()
        world.cycles(1)
        world.reader.raises = _unanswered()

        with capture_logs() as logs:
            world.cycles(3)

        records = _events(logs, CYCLE_FAILED_EVENT)
        assert [(r["consecutive"], r["log_level"]) for r in records] == [
            (1, "warning"),
            (3, "error"),
        ]
        error = records[1]
        assert error["action"] == ACTION_TRADING_WITHHELD
        assert (error["scope"], error["reason"], error["error_type"]) == (
            SCOPE_RUNTIME,
            "positions_unanswered",
            "BrokerStateUnavailableError",
        )

    def test_the_error_ignores_the_hourly_throttle_with_the_clock_held_still(self):
        """Hazard: today's throttle returns early for any record after the
        first inside the hour. The threshold record must not."""
        world = _World()
        world.reader.raises = _unanswered()

        with capture_logs() as logs:
            for _ in range(3):
                asyncio.run(world.reconciler._run(SCOPE_RUNTIME))

        levels = [r["log_level"] for r in _events(logs, CYCLE_FAILED_EVENT)]
        assert levels == ["warning", "error"]
        assert world.monitor.state is ConnectionState.RECOVERING

    @pytest.mark.acceptance_criterion("5.8a", AC_5_8A, SOURCE)
    def test_a_withheld_session_suppresses_every_order(self):
        """AC #1's "orders withheld, ``order.suppressed``", through the real
        order path wrapper reading the reconciler's own monitor."""
        world = _World()
        strategy = _SpyStrategy()
        install_order_path(strategy, world.monitor, _log())
        _withheld(world, streak=3)

        with capture_logs() as logs:
            strategy.submit_order(SimpleNamespace(client_order_id="O-1"))

        assert strategy.calls == []
        assert _events(logs, SUPPRESSED_EVENT)

    def test_two_failures_leave_orders_flowing(self):
        """Sibling: the same spy records when nothing is withheld."""
        world = _World()
        strategy = _SpyStrategy()
        install_order_path(strategy, world.monitor, _log())
        _withheld(world, streak=2)

        with capture_logs() as logs:
            strategy.submit_order(SimpleNamespace(client_order_id="O-1"))

        assert strategy.calls == ["submit_order"]
        assert _events(logs, SUPPRESSED_EVENT) == []

    @pytest.mark.acceptance_criterion("5.8a", AC_5_8A, SOURCE)
    def test_the_next_clean_cycle_regrants_exactly_as_a_reconnect_does(self):
        world = _World()
        _withheld(world)
        calls = []
        original = world.monitor.confirm_state_reestablished
        world.monitor.confirm_state_reestablished = lambda s: calls.append(s) or original(s)
        world.reader.raises = None

        with capture_logs() as logs:
            world.tick()  # RECOVERING: a reconnect cycle runs on the very next tick

        assert world.monitor.state is ConnectionState.CONNECTED
        assert calls == [UP]
        names = [e["event"] for e in logs if e["event"] in (OK_EVENT, "connection.restored")]
        assert names == [OK_EVENT, "connection.restored"]
        assert _events(logs, OK_EVENT)[0]["scope"] == SCOPE_RECONNECT

    def test_a_failing_reconnect_cycle_keeps_it_withheld(self):
        world = _World()
        _withheld(world)

        world.tick(4)

        assert world.monitor.trading_permitted is False

    def test_an_in_flight_order_still_withholds_the_regrant(self):
        inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(AAPL))]
        world = _World()
        _withheld(world)
        world.cache.inflight = inflight
        world.reader.raises = None

        world.tick()

        assert world.monitor.state is ConnectionState.RECOVERING

    def test_a_new_streak_withholds_again_at_its_own_third_failure(self):
        world = _World()
        _withheld(world)
        world.reader.raises = None
        world.tick()
        assert world.monitor.state is ConnectionState.CONNECTED

        world.reader.raises = _unanswered()
        world.cycles(2)
        assert world.monitor.state is ConnectionState.CONNECTED
        world.cycles(1)
        assert world.monitor.state is ConnectionState.RECOVERING

    def test_a_streak_after_a_real_disconnect_withdraws_nothing_and_says_nothing_more(self):
        """D-J, as amended by code review P5: already ``RECOVERING`` after a
        loss, the withhold is the outage's, not this rule's. The third failure
        withdraws nothing, so it opens no period and logs no ERROR — an
        ``action=trading_withheld`` that withheld nothing would mislead every
        grep for it; the streak's WARNING throttle covers it."""
        world = _World()
        world.lose_and_recover()
        world.reader.raises = _unanswered()

        with capture_logs() as logs:
            for _ in range(3):
                world.clock.now += TICK
                asyncio.run(world.reconciler.on_tick())

        records = _events(logs, CYCLE_FAILED_EVENT)
        assert [(r["consecutive"], r["log_level"]) for r in records] == [(1, "warning")]
        assert not [r for r in records if "action" in r]
        assert world.reconciler.withheld.pending() == ()

    def test_no_withheld_record_carries_the_raw_account_or_exception_text(self):
        world = _World()
        world.reader.raises = RuntimeError(f"account {RAW_ACCOUNT} leaked")

        with capture_logs() as logs:
            world.cycles(3)

        assert _events(logs, CYCLE_FAILED_EVENT)
        assert all(RAW_ACCOUNT not in repr(record) for record in logs)
        (period,) = world.reconciler.withheld.pending()
        assert RAW_ACCOUNT not in repr(period)
        assert (period.reason, period.error_type) == (None, "RuntimeError")

    def test_only_the_readers_own_reason_is_logged_or_persisted(self):
        """Code review P4: a third-party exception's free-text ``.reason``
        (``URLError``'s, say) never reaches a record or the session row —
        only a ``BrokerStateFailure`` value does."""

        class _Reasoned(Exception):
            reason = f"connection to {RAW_ACCOUNT}@10.0.0.7 refused"

        world = _World()
        world.reader.raises = _Reasoned()

        with capture_logs() as logs:
            world.cycles(3)

        records = _events(logs, CYCLE_FAILED_EVENT)
        assert records and all(r["reason"] is None for r in records)
        assert all(RAW_ACCOUNT not in repr(record) for record in logs)
        (period,) = world.reconciler.withheld.pending()
        assert (period.reason, period.error_type) == (None, "_Reasoned")


class TestAdapterDriftStopsTheSession:
    """Story 5.8, AC #2 (PO ruling D3: "drift stops")."""

    def test_no_drift_record_carries_the_raw_account(self):
        """Code review P2 — Task 2.1's "either path": the drift record's
        ``detail`` is the reader's own text (contracts and raise sites, never
        the account), from the read and from ``correct()`` alike."""
        read = _World()
        read.reader.raises = BrokerStateAdapterError(
            "reading broker state raised KeyError at execution.py:841 _on_account_summary",
            error_type="KeyError",
        )
        lookup = _World(state=_state(_held(NVDA, "5")))
        lookup.engine._clients = {}  # `correct()`'s find_ib_exec_client drifts

        with capture_logs() as logs:
            for world, cycles in ((read, 1), (lookup, 3)):
                with pytest.raises(BrokerStateAdapterError) as caught:
                    world.cycles(cycles)
                assert RAW_ACCOUNT not in str(caught.value)

        stops = [r for r in _events(logs, CYCLE_FAILED_EVENT) if r["action"] == "session_stopped"]
        assert len(stops) == 2
        assert stops[0]["error_type"] == "KeyError"
        assert all(RAW_ACCOUNT not in repr(record) for record in logs)

    @pytest.mark.acceptance_criterion("5.8b", AC_5_8B, SOURCE)
    def test_drift_on_the_first_cycle_stops_with_exit_1(self):
        world = _World()
        world.reader.raises = _drift()

        with capture_logs() as logs, pytest.raises(BrokerStateAdapterError) as caught:
            world.cycles(1)

        assert caught.value is world.reader.raises
        assert EXIT_CODES[classify_failure(caught.value)] == EXIT_ERROR == 1
        (record,) = _events(logs, CYCLE_FAILED_EVENT)
        assert record["log_level"] == "error"
        assert record["action"] == ACTION_SESSION_STOPPED
        assert (record["reason"], record["consecutive"], record["scope"]) == (
            "adapter_incompatible",
            1,
            SCOPE_RUNTIME,
        )
        assert "distinct contracts" in record["detail"]
        assert world.monitor.state is ConnectionState.CONNECTED

    @pytest.mark.acceptance_criterion("5.8b", AC_5_8B, SOURCE)
    def test_drift_inside_the_hourly_throttle_window_is_still_logged_and_stops(self):
        world = _World()
        world.reader.raises = _unanswered()
        world.cycles(2)  # a streak of two: one WARNING, the second throttled

        world.reader.raises = _drift()
        with capture_logs() as logs, pytest.raises(BrokerStateAdapterError):
            world.cycles(1)

        (record,) = _events(logs, CYCLE_FAILED_EVENT)
        assert (record["log_level"], record["consecutive"], record["action"]) == (
            "error",
            3,
            ACTION_SESSION_STOPPED,
        )
        assert world.reconciler.withheld.pending() == ()

    def test_drift_in_a_reconnect_cycle_stops_too(self):
        world = _World()
        world.lose_and_recover()
        world.reader.raises = _drift()

        with capture_logs() as logs, pytest.raises(BrokerStateAdapterError):
            world.clock.now += TICK
            asyncio.run(world.reconciler.on_tick())

        (record,) = _events(logs, CYCLE_FAILED_EVENT)
        assert (record["scope"], record["action"]) == (SCOPE_RECONNECT, ACTION_SESSION_STOPPED)

    def test_drift_while_withheld_stops_the_session(self):
        world = _World()
        _withheld(world)
        world.reader.raises = _drift()

        with pytest.raises(BrokerStateAdapterError):
            world.tick(2)

    def test_drift_from_the_correction_path_stops(self):
        """``correct`` finds the IB exec client itself; a node without one is
        the same drift (``find_ib_exec_client``)."""
        world = _World(state=_state(_held(NVDA, "5")))
        world.engine._clients = {}

        with pytest.raises(BrokerStateAdapterError):
            world.cycles(3)  # first sighting, then confirmed a debounce later

    def test_a_plain_unavailable_error_never_stops(self):
        """Sibling: the subclass stops, its base does not."""
        world = _World()
        world.reader.raises = _unanswered()

        world.cycles(5)

        assert world.monitor.trading_permitted is False


class TestTheWithheldPeriodLedger:
    """Story 5.8 (AC #3, D-E/D-F): what the steady state persists."""

    def test_a_withdrawal_opens_one_period_stamped_by_the_wall_clock(self):
        world = _World()
        world.cycles(1)
        world.reader.raises = _unanswered()
        world.cycles(2)
        world.cycles(1)
        at_withdrawal = world.wall()

        (period,) = world.reconciler.withheld.pending()

        assert period == WithheldPeriod(
            started_at=at_withdrawal,
            reason="positions_unanswered",
            error_type="BrokerStateUnavailableError",
            failed_cycles=3,
        )
        assert period.ended_at is None
        assert period.started_at.tzinfo is not None

    def test_the_regrant_closes_it(self):
        world = _World()
        _withheld(world)
        world.reader.raises = None

        world.tick()
        at_grant = world.wall()

        (period,) = world.reconciler.withheld.pending()
        assert period.ended_at == at_grant
        assert period.ended_at > period.started_at

    def test_a_refused_grant_does_not_close_it(self):
        world = _World()
        _withheld(world)
        world.reader.raises = None
        world.connection.status = DOWN  # the grant's own reading drops

        world.clock.now += TICK
        asyncio.run(world.reconciler.on_tick())

        assert world.monitor.state is not ConnectionState.CONNECTED
        (period,) = world.reconciler.withheld.pending()
        assert period.ended_at is None

    def test_a_written_start_is_pending_again_once_it_ends(self):
        world = _World()
        _withheld(world)
        ledger = world.reconciler.withheld
        (start,) = ledger.pending()
        ledger.mark_written(start)
        assert ledger.pending() == ()

        world.reader.raises = None
        world.tick()

        (end,) = ledger.pending()
        assert (end.started_at, end.ended_at is not None) == (start.started_at, True)

    def test_marking_a_superseded_version_written_keeps_the_newer_one(self):
        world = _World()
        _withheld(world)
        ledger = world.reconciler.withheld
        (start,) = ledger.pending()
        world.reader.raises = None
        world.tick()

        ledger.mark_written(start)  # the start's write finished after the end landed

        (end,) = ledger.pending()
        assert end.ended_at is not None

    def test_two_unwritten_periods_are_both_pending_in_order(self):
        world = _World()
        _withheld(world)
        world.reader.raises = None
        world.tick()
        world.reader.raises = _unanswered()
        world.cycles(3)

        first, second = world.reconciler.withheld.pending()

        assert first.ended_at is not None and second.ended_at is None
        assert first.started_at < second.started_at

    def test_the_port_kwargs_are_stdlib_values_only(self):
        """AR38: nothing but primitives crosses the record port."""
        period = WithheldPeriod(
            started_at=WALL_ORIGIN,
            reason="timeout",
            error_type="BrokerStateUnavailableError",
            failed_cycles=3,
            ended_at=WALL_ORIGIN + timedelta(minutes=2),
        )

        assert period.as_port_kwargs() == {
            "withheld_from": WALL_ORIGIN,
            "withheld_until": WALL_ORIGIN + timedelta(minutes=2),
            "reason": "timeout",
            "error_type": "BrokerStateUnavailableError",
            "failed_cycles": 3,
        }

    def test_the_default_wall_clock_is_aware_utc(self):
        ledger = WithheldPeriods()

        period = ledger.start(reason=None, error_type="KeyError", failed_cycles=3)

        assert period.started_at.tzinfo is not None
        assert period.started_at.utcoffset() == timedelta(0)

    def test_ending_with_nothing_open_changes_nothing(self):
        ledger = WithheldPeriods()

        assert ledger.end() is None
        assert ledger.pending() == ()

    def test_a_wall_clock_stepped_back_never_ends_a_period_before_it_began(self):
        """Code review P7: an NTP step between the withdrawal and the grant."""
        stamps = iter([WALL_ORIGIN, WALL_ORIGIN - timedelta(seconds=40)])
        ledger = WithheldPeriods(lambda: next(stamps))
        ledger.start(reason=None, error_type="TimeoutError", failed_cycles=3)

        ended = ledger.end()

        assert ended is not None and ended.ended_at == ended.started_at == WALL_ORIGIN


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


class _SpyWatch:
    """Story 4.8's stale-order watch, as the reconciler sees it: one call per cycle."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, frozenset[str]]] = []
        self.raises: BaseException | None = None

    def forget(self) -> None:
        pass

    async def check(self, node, log, now, *, skip, scope) -> None:
        self.calls.append((scope, frozenset(skip)))
        if self.raises is not None:
            raise self.raises


def _watched(world: _World) -> _SpyWatch:
    spy = _SpyWatch()
    world.reconciler._stale_orders = spy
    return spy


class TestStaleOrdersAreCheckedOnRuntimeCyclesOnly:
    """Story 4.8, D-A: once per runtime cycle, never on a reconnect cycle."""

    def test_each_runtime_cycle_checks_once(self):
        world = _World()
        spy = _watched(world)

        world.cycles(3)

        assert spy.calls == [(SCOPE_RUNTIME, frozenset())] * 3

    def test_a_reconnect_cycle_never_checks(self):
        world = _World()
        spy = _watched(world)
        world.lose_and_recover()
        world.cache.inflight = [SimpleNamespace(instrument_id=InstrumentId.from_str(NVDA))]

        for _ in range(4):
            world.clock.now += TICK
            asyncio.run(world.reconciler.on_tick())

        assert len(world.reader.calls) == 4, "premise: the reconnect cycles ran"
        assert spy.calls == []

    def test_a_cycle_whose_cache_moved_does_not_check(self):
        world = _World()
        spy = _watched(world)
        world.reader.during = lambda: world.cache.positions.append(_Position(NVDA, STRATEGY, "1"))

        world.cycles(1)

        assert spy.calls == []

    def test_instruments_with_a_row_still_standing_or_deferred_are_skipped(self):
        """AC #5: the row's instrument (debouncing, then corrected this cycle)
        and an instrument deferred for an in-flight order."""
        world = _World(
            state=_state(_held(NVDA, "5")),
            inflight=[SimpleNamespace(instrument_id=InstrumentId.from_str(AAPL))],
        )
        spy = _watched(world)

        world.cycles(2)

        assert spy.calls == [(SCOPE_RUNTIME, frozenset({NVDA, AAPL}))] * 2

    def test_an_instrument_whose_row_is_inert_is_skipped_too(self):
        """Story 5.7, D-H — the Story 4.8 code review's deferred gap ("AC #5's
        skip set omits the unresolvable rows", owner: whoever next touches
        ``_act``'s return value): an instrument the cache does not hold is
        logged ``unresolved`` and inert from its second cycle, and still skipped."""
        world = _World(state=_state(_held("MSFT.NASDAQ", "3"), BOND))
        spy = _watched(world)

        world.cycles(3)

        assert spy.calls == [(SCOPE_RUNTIME, frozenset({"MSFT.NASDAQ", "IB-CONID-99"}))] * 3

    def test_a_check_that_raises_never_leaves_the_tick_and_positions_were_handled(self):
        world = _World(state=_state(_held(NVDA, "5")))
        spy = _watched(world)
        spy.raises = RuntimeError("boom")

        with capture_logs() as logs:
            world.cycles(2)

        assert len(spy.calls) == 2, "premise: the check ran and raised"
        assert len(world.engine.reports) == 1, "the position half still corrected"
        assert _events(logs, CYCLE_FAILED_EVENT)

    def test_a_connection_loss_makes_the_watch_forget_its_sightings(self):
        """The position debounce restarts on a loss; so must the order one —
        an absence seen before it must be seen twice again after it."""
        world = _World()
        forgotten = []
        world.reconciler._stale_orders.forget = lambda: forgotten.append(True)

        world.lose_and_recover()
        for _ in range(3):
            world.clock.now += TICK
            asyncio.run(world.reconciler.on_tick())

        assert forgotten == [True], "once per loss, not once per reconnect cycle"

    def test_the_reconciler_builds_its_watch_from_the_injected_read(self):
        calls = []

        async def _read(node):
            calls.append(node)
            raise AssertionError("never asked: no candidate")

        reconciler = RuntimeReconciler(
            node=SimpleNamespace(),
            monitor=ConnectionMonitor(session_id="s-1"),
            log=_log(),
            connection_reader=lambda settings: UP,
            settings=object(),
            read_open_orders=_read,
        )

        assert isinstance(reconciler._stale_orders, StaleOrderWatch)
        assert reconciler._stale_orders._read is _read


def test_the_unit_doubles_are_what_the_cycle_reads():
    """Guard for this file's own premise: the stub engine records reports."""
    world = _World(state=_state(_held(NVDA, "5")))
    world.engine.reconcile_execution_report(
        SimpleNamespace(instrument_id=NVDA_EQUITY.id, signed_decimal_qty=Decimal(5))
    )
    assert len(world.engine.reports) == 1
