"""Unit tests for the ``reconcile`` phase's body (Story 4.2, AC #2, #3, #5, #6).

Duck-typed doubles stand in for the node, its exec engine and its cache; the
broker read is injected. The real ``LiveExecutionEngine`` proofs — that
``reconcile_execution_report`` really moves the cache broker-ward, and the
measured shapes that make D-D necessary — live in
``tests/component/core/test_live_startup_reconcile_engine.py``.

Unit tier despite the ``nautilus_trader`` imports, on the
``test_live_session_controller.py`` precedent: nothing here constructs a node
or touches the C logging subsystem.
"""

import ast
import asyncio
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import AccountId, InstrumentId
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from structlog.testing import capture_logs

from src.core import live_startup_reconcile
from src.core.exit_outcome import EXIT_CODES, EXIT_ERROR, LiveCheckOutcome
from src.core.live_broker_state import BrokerStateFailure, BrokerStateUnavailableError
from src.core.live_check import classify_failure
from src.core.live_startup_reconcile import (
    CASH_CHANGED_EVENT,
    DISCREPANCY_EVENT,
    EMITTED_RECONCILE_EVENTS,
    OK_EVENT,
    SNAPSHOT_FAILED_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
    cached_positions,
    capture_local_state,
    reconcile_at_startup,
    require_broker_ward_reconciliation,
    require_reconciled,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.position_reconciliation import (
    CachedPosition,
    LocalSnapshot,
    StartupReconciliation,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 27, 13, 25, tzinfo=UTC)
RAW_ACCOUNT = "DU4076626"
NVDA_EQUITY = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
AAPL_EQUITY = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
NVDA = str(NVDA_EQUITY.id)
AAPL = str(AAPL_EQUITY.id)
STRATEGY = "SMACrossover-000"


class _Position:
    """The three members ``cached_positions`` reads off a Nautilus ``Position``."""

    def __init__(self, instrument_id: str, strategy_id: str, quantity: str) -> None:
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.strategy_id = strategy_id
        self._quantity = Decimal(quantity)

    def signed_decimal_qty(self) -> Decimal:
        return self._quantity


class _Cache:
    def __init__(
        self,
        positions=(),
        orders_open=(),
        instruments=(NVDA_EQUITY, AAPL_EQUITY),
        *,
        general=None,
        accounts=None,
    ):
        self.positions = list(positions)
        self._orders_open = list(orders_open)
        self._instruments = {instrument.id: instrument for instrument in instruments}
        #: Story 4.7: the general cache and accounts Redis restored at init.
        self.general = dict(general or {})
        self.accounts = dict(accounts or {})

    def positions_open(self):
        return [p for p in self.positions if p.signed_decimal_qty() != 0]

    def orders_open(self):
        return list(self._orders_open)

    def instrument(self, instrument_id):
        return self._instruments.get(instrument_id)

    def get(self, key):
        return self.general.get(key)

    def account(self, account_id):
        return self.accounts.get(account_id)


class _Engine:
    """Records every report; by default applies it the way Nautilus does —
    an ``INTERNAL-DIFF`` position absorbing the difference (Task 1.1)."""

    def __init__(self, cache: _Cache, *, applies: bool = True, returns: bool = True, **settings):
        self.reconciliation = settings.pop("reconciliation", True)
        self.generate_missing_orders = settings.pop("generate_missing_orders", True)
        self.filter_position_reports = settings.pop("filter_position_reports", False)
        self.reconciliation_instrument_ids = settings.pop("reconciliation_instrument_ids", [])
        # Story 4.5 (D-A): the session's engine drops the adapter's fabricated
        # per-position order instead of importing it as `EXTERNAL`.
        self.filter_unclaimed_external_orders = settings.pop(
            "filter_unclaimed_external_orders", True
        )
        self._clients = {
            "INTERACTIVE_BROKERS": SimpleNamespace(
                account_id=AccountId(f"INTERACTIVE_BROKERS-{RAW_ACCOUNT}")
            )
        }
        self._cache, self._applies, self._returns = cache, applies, returns
        self.reports: list = []

    def reconcile_execution_report(self, report) -> bool:
        self.reports.append(report)
        if self._applies:
            instrument_id = str(report.instrument_id)
            net = sum(
                (
                    p.signed_decimal_qty()
                    for p in self._cache.positions_open()
                    if str(p.instrument_id) == instrument_id
                ),
                Decimal(0),
            )
            diff = report.signed_decimal_qty - net
            if diff:
                self._cache.positions.append(_Position(instrument_id, "INTERNAL-DIFF", str(diff)))
        return self._returns


def _node(cache: _Cache | None = None, **engine_kwargs):
    cache = cache if cache is not None else _Cache()
    engine = _Engine(cache, **engine_kwargs)
    return SimpleNamespace(cache=cache, kernel=SimpleNamespace(exec_engine=engine))


def _held(
    instrument_id: str, quantity: str, *, resolved=True, price="101.25", symbol=None
) -> BrokerPosition:
    return BrokerPosition(
        instrument_id=instrument_id,
        quantity=Decimal(quantity),
        average_price=None if price is None else Decimal(price),
        con_id=4815747,
        symbol=instrument_id.split(".")[0] if symbol is None else symbol,
        instrument_resolved=resolved,
    )


def _state(*positions: BrokerPosition) -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(positions),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=AT,
    )


def _reader(state: BrokerState | None = None, *, raises: BaseException | None = None):
    calls: list = []

    async def _read(node, *, log):
        calls.append(node)
        if raises is not None:
            raise raises
        return state if state is not None else _state()

    _read.calls = calls  # type: ignore[attr-defined]
    return _read


def _reconcile(node, reader, *, local_before=None, clock=None):
    """``local_before=None`` is "no snapshot" — the framework-resolved records
    are exercised only where a test supplies one."""
    ticks = iter([10.0, 10.0125]) if clock is None else clock
    return asyncio.run(
        reconcile_at_startup(
            node,
            log=__import__("structlog").get_logger("test"),
            local_before=local_before,
            read_state=reader,
            clock=lambda: next(ticks),
        )
    )


def _events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


def _snapshot(*positions: CachedPosition, cash=(), recorded_at=None) -> LocalSnapshot:
    return LocalSnapshot(positions=positions, cash=cash, cash_recorded_at=recorded_at)


def _usd(amount: str, currency: str = "USD") -> CashBalance:
    return CashBalance(currency=currency, total_cash=Decimal(amount))


IB_ACCOUNT_ID = AccountId(f"INTERACTIVE_BROKERS-{RAW_ACCOUNT}")
RECORDED = datetime(2026, 9, 26, 20, 0, 1, 250000, tzinfo=UTC)
RECORDED_NANOS = 1_790_452_801_250_000_000


def _summary(usd: float) -> dict:
    """The general-cache entry as the IB exec client writes it (F7)."""
    body = {"USD": {"TotalCashValue": usd, "NetLiquidation": 400000.0}, "": {"X": "Y"}}
    return {f"accountSummary:{RAW_ACCOUNT}": json.dumps(body).encode()}


def _account(*events) -> SimpleNamespace:
    return SimpleNamespace(events=list(events))


def _account_state(nanos: int, *, reported: bool = True) -> SimpleNamespace:
    return SimpleNamespace(ts_event=nanos, is_reported=reported)


class TestBrokerWardSettingsAreEnforcedOnTheRunningEngine:
    """D-B: the configuration that lets the broker's view win, read off the
    engine the node is actually running — refused before the broker is read."""

    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("reconciliation", False),
            ("generate_missing_orders", False),
            ("filter_position_reports", True),
            ("reconciliation_instrument_ids", [InstrumentId.from_str(NVDA)]),
            ("filter_unclaimed_external_orders", False),
        ],
    )
    def test_each_drifted_setting_is_refused(self, setting, value):
        node = _node(**{setting: value})
        reader = _reader()

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, reader)

        assert caught.value.reason is ReconciliationFailure.FRAMEWORK_RECONCILIATION_DISABLED
        assert setting in str(caught.value)
        assert reader.calls == [], "the broker was read on an engine that cannot defer to it"

    def test_a_missing_setting_is_drift_and_refused(self):
        engine = SimpleNamespace(reconciliation=True, filter_position_reports=False)

        with pytest.raises(ReconciliationFailedError, match="generate_missing_orders"):
            require_broker_ward_reconciliation(engine)

    def test_a_truthy_non_bool_is_not_accepted_as_enabled(self):
        engine = SimpleNamespace(
            reconciliation=1,
            generate_missing_orders=True,
            filter_position_reports=False,
            reconciliation_instrument_ids=[],
        )
        with pytest.raises(ReconciliationFailedError, match="reconciliation"):
            require_broker_ward_reconciliation(engine)

    def test_the_default_engine_settings_pass(self):
        require_broker_ward_reconciliation(_node().kernel.exec_engine)


class TestAMatchingCacheReconcilesClean:
    def test_a_match_returns_the_result_and_logs_reconcile_ok_once(self):
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "10"),
                _Position(NVDA, "EXTERNAL", "10"),
                _Position(NVDA, "INTERNAL-DIFF", "-10"),
            ],
            orders_open=["O-1"],
        )
        node = _node(cache)
        # Measured 1.4A: before the framework's pass the cache held S +10 only.
        before = _snapshot(
            CachedPosition(instrument_id=NVDA, strategy_id=STRATEGY, quantity=Decimal(10))
        )

        with capture_logs() as logs:
            result = _reconcile(node, _reader(_state(_held(NVDA, "10"))), local_before=before)

        assert isinstance(result, StartupReconciliation)
        assert result.framework_resolved == ()
        assert node.kernel.exec_engine.reports == []
        (ok,) = _events(logs, OK_EVENT)
        assert ok["log_level"] == "info"
        assert ok["positions"] == 1
        assert ok["instruments"] == {NVDA: "10"}
        assert ok["open_orders"] == 1
        assert ok["synthetic_positions"] == 2
        assert ok["discrepancies"] == 0
        assert ok["account"] == "***626"
        assert ok["elapsed_ms"] == pytest.approx(12.5)
        assert _events(logs, DISCREPANCY_EVENT) == []

    def test_a_flat_cache_against_a_flat_broker_is_clean(self):
        with capture_logs() as logs:
            result = _reconcile(_node(), _reader())

        assert result.discrepancy_count == 0
        assert len(_events(logs, OK_EVENT)) == 1


class TestAStrategyContradictionIsRefusedBeforeAnythingIsWritten:
    """D-D, ruled by the PO 2026-09-27 (option A)."""

    def test_the_p7_fill_0901_shape_is_refused(self):
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")]))

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader())

        error = caught.value
        assert error.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert node.kernel.exec_engine.reports == [], "the cache was written on a refused start"
        (row,) = error.discrepancies
        assert row.instrument_id == NVDA and row.strategy_quantity == Decimal("22")
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["log_level"] == "error"
        assert record["resolution"] == "refused"
        assert record["reason"] == "strategy_position_contradicted"
        assert record["kind"] == "strategy_position"
        assert (record["local_quantity"], record["strategy_quantity"]) == ("22", "22")
        assert record["broker_quantity"] == "0"
        assert _events(logs, OK_EVENT) == []

    def test_a_matching_net_does_not_hide_a_contradicted_strategy(self):
        """Measured 1.4D, after the framework's pass."""
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "22"),
                _Position(NVDA, "EXTERNAL", "10"),
                _Position(NVDA, "INTERNAL-DIFF", "-22"),
            ]
        )

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(_node(cache), _reader(_state(_held(NVDA, "10"))))

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED

    def test_every_disagreeing_instrument_is_logged_when_the_start_is_refused(self):
        """A refused start resolves nothing — so the resolvable row is named
        too, rather than silently left. Only the cause is *refused* (ERROR,
        with the reason, and alone in the exit-1 message); the unrelated row
        is logged ``untouched`` at WARNING, as the runtime twin refuses only
        the contradicted rows (PR #35 code review, P4)."""
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "22"), _Position(AAPL, "EXTERNAL", "4")]
        )
        node = _node(cache)

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader())

        assert [row.instrument_id for row in caught.value.discrepancies] == [NVDA]
        assert AAPL not in str(caught.value)
        records = _events(logs, DISCREPANCY_EVENT)
        assert {(r["instrument_id"], r["resolution"], r["log_level"]) for r in records} == {
            (AAPL, "untouched", "warning"),
            (NVDA, "refused", "error"),
        }
        assert [r["reason"] for r in records if r["instrument_id"] == NVDA] == [
            "strategy_position_contradicted"
        ]
        assert all("reason" not in r for r in records if r["instrument_id"] == AAPL)
        assert node.kernel.exec_engine.reports == []

    def test_the_message_names_the_instrument_both_quantities_and_the_remedy(self):
        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(_node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")])), _reader())

        message = str(caught.value)
        assert NVDA in message and "+22" in message and "broker +0" in message
        assert "new session" in message
        assert RAW_ACCOUNT not in message


class TestABrokerHoldingThatCoversTheStrategyIsNotAContradiction:
    """Story 4.5, PO ruling 2026-09-28 (review Decision 2: A — startup only).

    The broker holding everything the strategies own on the same side, *and
    more*, is not a contradiction: the strategies' position is real, and the
    excess belongs to no strategy — D-C's to refuse, per strategy, contained
    (``live_session_resume``). Every other disagreement still refuses the whole
    session, unchanged."""

    @pytest.mark.parametrize(
        ("own", "excess", "broker"),
        [("10", "5", "15"), ("-10", "-5", "-15")],
        ids=["long", "short"],
    )
    def test_the_covered_excess_passes_the_startup_phase(self, own, excess, broker):
        """After the framework's own pass: the strategy's position plus the
        ``INTERNAL-DIFF`` it imported for the excess. Nothing is written."""
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, own), _Position(NVDA, "INTERNAL-DIFF", excess)]
        )
        node = _node(cache)

        with capture_logs() as logs:
            result = _reconcile(node, _reader(_state(_held(NVDA, broker))))

        assert result.synthetic_positions == 1
        assert node.kernel.exec_engine.reports == []
        assert [e["event"] for e in logs if e["event"] == OK_EVENT] == [OK_EVENT]
        assert all(e.get("resolution") != "refused" for e in _events(logs, DISCREPANCY_EVENT))

    def test_an_uncorrected_covered_excess_is_corrected_broker_ward_then_passes(self):
        """The framework left only the strategy's +10 against a broker at +15:
        the phase corrects the net (``INTERNAL-DIFF +5``) and re-verifies."""
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "10")]))

        result = _reconcile(node, _reader(_state(_held(NVDA, "15"))))

        (report,) = node.kernel.exec_engine.reports
        assert report.signed_decimal_qty == Decimal("15")
        assert result.synthetic_positions == 1

    def test_a_covered_excess_over_mixed_sides_passes_only_by_this_exemption(self):
        """Integration merge of 4.5 and 4.7 (2026-09-28): Story 4.7's coverage
        rule already clears the single-side shapes above, so this is the one
        shape the exemption still decides. A +20 and B −10 net +10 against a
        broker at +15 is contradicted by the mixed-sides equality rule, yet
        passes the phase; D-C then refuses both strategies on the instrument,
        so nothing trades on it. The running cycle still stops on it
        (``test_live_runtime_reconcile.py``)."""
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "20"),
                _Position(NVDA, "SMAMomentum-001", "-10"),
                _Position(NVDA, "INTERNAL-DIFF", "5"),
            ]
        )
        node = _node(cache)

        with capture_logs() as logs:
            result = _reconcile(node, _reader(_state(_held(NVDA, "15"))))

        assert node.kernel.exec_engine.reports == []
        assert result.synthetic_positions == 1
        # D5b (PR #35 code review, PO ruling 2026-09-30): the exempted row is
        # named from this phase, not only by the refusal that follows in `trading`.
        (covered,) = _events(logs, DISCREPANCY_EVENT)
        assert (covered["resolution"], covered["log_level"]) == ("covered", "warning")
        assert (covered["instrument_id"], covered["kind"]) == (NVDA, "strategy_position")
        assert (covered["strategy_quantity"], covered["broker_quantity"]) == ("10", "15")
        assert "reason" not in covered

    @pytest.mark.parametrize(
        ("synthetic", "broker"),
        [("-5", "5"), ("-10", None), ("-15", "-5")],
        ids=["broker-holds-less", "broker-flat", "opposite-side"],
    )
    def test_every_other_disagreement_still_refuses_the_session(self, synthetic, broker):
        """The net matches in each (the framework's correction is in the cache),
        but the broker cannot hold what the strategy believes it holds."""
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "10"), _Position(NVDA, "INTERNAL-DIFF", synthetic)]
        )
        held = () if broker is None else (_held(NVDA, broker),)
        node = _node(cache)

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_state(*held)))

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert node.kernel.exec_engine.reports == [], "the cache was written on a refused start"


class TestACorporateActionIsAbsorbedAtStartup:
    """Story 4.7, D-A (PO ruling A): a split that grows a strategy's position is
    absorbed broker-ward and named, never refused; the strategy's own lot is
    left exactly as it was."""

    def test_a_forward_split_the_framework_left_is_corrected_and_named(self):
        cache = _Cache(positions=[_Position(NVDA, STRATEGY, "10")])
        node = _node(cache)

        with capture_logs() as logs:
            result = _reconcile(node, _reader(_state(_held(NVDA, "20", price="100"))))

        (report,) = node.kernel.exec_engine.reports
        assert report.signed_decimal_qty == Decimal("20")
        (row,) = result.reconcile_resolved
        assert row.kind == "position"
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["log_level"] == "warning"
        assert record["resolution"] == "broker" and record["scope"] == "startup"
        assert record["instrument_id"] == NVDA and record["kind"] == "position"
        assert (record["local_quantity"], record["strategy_quantity"]) == ("10", "10")
        assert record["broker_quantity"] == "20"
        assert "likely_cause" not in record
        assert [p.signed_decimal_qty() for p in cache.positions if p.strategy_id == STRATEGY] == [
            Decimal("10")
        ], "the strategy's own lot was adjusted"
        assert len(_events(logs, OK_EVENT)) == 1

    def test_a_forward_split_the_framework_already_absorbed_is_named_from_the_snapshot(self):
        """Measured 1.1a: Nautilus's own pass left S +10, EXTERNAL +20,
        INTERNAL-DIFF −10 — net right, the strategy covered. The only record of
        the split is the pre-run snapshot's before quantity."""
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "10"),
                _Position(NVDA, "EXTERNAL", "20"),
                _Position(NVDA, "INTERNAL-DIFF", "-10"),
            ]
        )
        node = _node(cache)
        before = _snapshot(
            CachedPosition(instrument_id=NVDA, strategy_id=STRATEGY, quantity=Decimal(10))
        )

        with capture_logs() as logs:
            result = _reconcile(
                node, _reader(_state(_held(NVDA, "20", price="100"))), local_before=before
            )

        assert node.kernel.exec_engine.reports == []
        (row,) = result.framework_resolved
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["resolution"] == "framework"
        assert (record["local_quantity"], record["strategy_quantity"]) == ("10", "10")
        assert record["broker_quantity"] == "20"
        assert row.broker_quantity == Decimal("20")

    def test_without_a_snapshot_the_absorbed_split_has_no_framework_record(self):
        """The pairing AC #2 pins: with no pre-run snapshot there is no "before"
        to name, so nothing claims one — the snapshot's own failure record
        (``reconcile.local_snapshot_failed``) is what says so."""
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "10"),
                _Position(NVDA, "EXTERNAL", "20"),
                _Position(NVDA, "INTERNAL-DIFF", "-10"),
            ]
        )

        with capture_logs() as logs:
            _reconcile(_node(cache), _reader(_state(_held(NVDA, "20"))), local_before=None)

        assert _events(logs, DISCREPANCY_EVENT) == []


class TestARefusalNamesTheLikelyCause:
    """Story 4.7, PO ruling: reverse splits, cash mergers and symbol changes stay
    refused, and the refusal names the likely cause — in the operator message
    and in the refused record, in our own words (NFR26)."""

    @pytest.mark.parametrize(
        ("broker", "phrase"),
        [
            (("5",), "reverse split"),
            ((), "cash merger"),
            (("-5",), "opposite side"),
        ],
        ids=["shrinking", "zero", "opposite-side"],
    )
    def test_each_uncovered_shape_is_refused_and_named(self, broker, phrase):
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "10")]))
        held = tuple(_held(NVDA, quantity) for quantity in broker)

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader(_state(*held)))

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert node.kernel.exec_engine.reports == [], "the cache was written on a refused start"
        assert phrase in str(caught.value)
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert record["resolution"] == "refused"
        assert phrase in record["likely_cause"]
        assert RAW_ACCOUNT not in str(caught.value)

    def test_a_refused_row_that_is_not_a_strategy_contradiction_carries_no_cause(self):
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "22"), _Position(AAPL, "EXTERNAL", "4")]
        )

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError):
                _reconcile(_node(cache), _reader())

        by_instrument = {r["instrument_id"]: r for r in _events(logs, DISCREPANCY_EVENT)}
        assert "likely_cause" not in by_instrument[AAPL]
        assert "cash merger" in by_instrument[NVDA]["likely_cause"]


class TestEverythingElseResolvesBrokerWard:
    """D-E: through the framework's own public entry point, then re-verified."""

    def test_a_stale_synthetic_position_gets_a_flat_report(self):
        node = _node(_Cache(positions=[_Position(AAPL, "EXTERNAL", "4")]))

        with capture_logs() as logs:
            result = _reconcile(node, _reader())

        (report,) = node.kernel.exec_engine.reports
        assert report.position_side == PositionSide.FLAT
        assert report.quantity.as_decimal() == 0
        assert str(report.instrument_id) == AAPL
        assert report.venue_position_id is None, "a venue position id selects HEDGING"
        assert str(report.account_id) == f"INTERACTIVE_BROKERS-{RAW_ACCOUNT}"
        (row,) = result.reconcile_resolved
        assert row.instrument_id == AAPL
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert (record["resolution"], record["log_level"], record["kind"]) == (
            "broker",
            "warning",
            "position",
        )
        assert _events(logs, OK_EVENT)[0]["discrepancies"] == 1

    def test_a_broker_only_long_is_reported_with_the_brokers_average_price(self):
        """Task 1.2: without ``avg_px_open`` the correcting fill is priced 0."""
        node = _node()

        _reconcile(node, _reader(_state(_held(NVDA, "5"))))

        (report,) = node.kernel.exec_engine.reports
        assert report.position_side == PositionSide.LONG
        assert report.quantity.as_decimal() == Decimal("5")
        assert report.avg_px_open is not None
        assert report.avg_px_open.as_decimal() == Decimal("101.25")

    def test_a_broker_only_short_is_reported_short(self):
        node = _node()

        _reconcile(node, _reader(_state(_held(NVDA, "-3"))))

        (report,) = node.kernel.exec_engine.reports
        assert report.position_side == PositionSide.SHORT
        assert report.signed_decimal_qty == Decimal("-3")

    def test_an_unknown_broker_price_is_passed_as_none(self):
        node = _node()

        _reconcile(node, _reader(_state(_held(NVDA, "5", price=None))))

        (report,) = node.kernel.exec_engine.reports
        assert report.avg_px_open is None

    def test_an_unresolved_broker_row_is_refused_without_writing(self):
        node = _node()

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_state(_held("IB-CONID-265598", "3", resolved=False))))

        assert caught.value.reason is ReconciliationFailure.UNRESOLVABLE_DISCREPANCY
        assert node.kernel.exec_engine.reports == []

    def test_an_instrument_the_cache_does_not_hold_is_refused_without_writing(self):
        node = _node(_Cache(instruments=(NVDA_EQUITY,)))

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_state(_held(AAPL, "5"), _held(NVDA, "1"))))

        assert caught.value.reason is ReconciliationFailure.UNRESOLVABLE_DISCREPANCY
        assert node.kernel.exec_engine.reports == [], "NVDA was written before AAPL was refused"

    def test_a_framework_refusal_is_refused(self):
        node = _node(_Cache(positions=[_Position(AAPL, "EXTERNAL", "4")]), returns=False)

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader())

        assert caught.value.reason is ReconciliationFailure.RESOLUTION_REFUSED

    def test_a_resolution_that_changes_nothing_is_caught_by_the_re_verify(self):
        """F8's shape: an instrument filtered out of reconciliation returns
        ``True`` without reconciling anything."""
        node = _node(_Cache(positions=[_Position(AAPL, "EXTERNAL", "4")]), applies=False)

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader())

        assert caught.value.reason is ReconciliationFailure.DISCREPANCY_REMAINS
        assert [r["resolution"] for r in _events(logs, DISCREPANCY_EVENT)] == ["refused"]
        assert _events(logs, OK_EVENT) == []


class TestReviewFindings:
    """Code review 2026-09-27 (see the story's Review Findings)."""

    def test_an_unrepresentable_broker_quantity_is_refused_before_any_write(self):
        """0.5 of a precision-0 equity would be rounded, and the correction miss
        the broker; the NVDA row must not be written first either."""
        node = _node()

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_state(_held(AAPL, "0.5"), _held(NVDA, "1"))))

        assert caught.value.reason is ReconciliationFailure.UNRESOLVABLE_DISCREPANCY
        assert [row.instrument_id for row in caught.value.discrepancies] == [AAPL]
        assert node.kernel.exec_engine.reports == []

    def test_an_unresolvable_refusal_carries_and_logs_only_the_unresolvable_rows(self):
        node = _node(_Cache(instruments=(NVDA_EQUITY,)))

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader(_state(_held(AAPL, "5"), _held(NVDA, "1"))))

        assert [row.instrument_id for row in caught.value.discrepancies] == [AAPL]
        assert [r["instrument_id"] for r in _events(logs, DISCREPANCY_EVENT)] == [AAPL]

    def test_an_unresolved_broker_row_beside_a_strategy_position_is_not_a_contradiction(self):
        """The broker's NVDA arrived as ``IB-CONID-*``: the cache's NVDA row reads
        "broker 0". Refusing it as contradicted would tell the operator to
        abandon a session over a lookup miss."""
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")]))

        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_state(_held("IB-CONID-4815747", "22", resolved=False))))

        assert caught.value.reason is ReconciliationFailure.UNRESOLVABLE_DISCREPANCY
        assert "new session" not in str(caught.value)
        assert node.kernel.exec_engine.reports == []

    def test_a_framework_refusal_mid_loop_names_what_was_already_corrected(self):
        cache = _Cache(
            positions=[_Position(AAPL, "EXTERNAL", "4"), _Position(NVDA, "EXTERNAL", "3")]
        )
        node = _node(cache)
        engine = node.kernel.exec_engine
        apply = engine.reconcile_execution_report
        engine.reconcile_execution_report = lambda report: (
            apply(report) and str(report.instrument_id) == AAPL
        )

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader())

        error = caught.value
        assert error.reason is ReconciliationFailure.RESOLUTION_REFUSED
        assert [row.instrument_id for row in error.discrepancies] == [NVDA]
        assert f"already corrected broker-ward before it: {AAPL}" in str(error)
        # D-F: a row the framework took before refusing a later one is a
        # discrepancy that was resolved — logged, never only named in an error
        # message (code review of PR #35).
        assert [
            (r["instrument_id"], r["resolution"]) for r in _events(logs, DISCREPANCY_EVENT)
        ] == [
            (AAPL, "broker"),
            (NVDA, "refused"),
        ]

    def test_an_engine_exception_is_the_typed_refusal_not_an_escape(self):
        node = _node(_Cache(positions=[_Position(AAPL, "EXTERNAL", "4")]))

        def _boom(report):
            raise RuntimeError("engine fault")

        node.kernel.exec_engine.reconcile_execution_report = _boom

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                _reconcile(node, _reader())

        assert caught.value.reason is ReconciliationFailure.RESOLUTION_REFUSED
        assert "raised RuntimeError" in str(caught.value)
        assert [r["resolution"] for r in _events(logs, DISCREPANCY_EVENT)] == ["refused"]

    def test_the_operator_message_has_no_doubled_period(self):
        with pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(_node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")])), _reader())

        assert ".." not in str(caught.value)

    def test_a_none_instrument_filter_is_no_filter(self):
        require_broker_ward_reconciliation(
            _node(reconciliation_instrument_ids=None).kernel.exec_engine
        )

    def test_elapsed_covers_a_reader_that_takes_real_time(self):
        """Task 4.1: the real monotonic clock, a reader sleeping under its deadline."""
        state = _state()

        async def _slow(node, *, log):
            await asyncio.sleep(0.05)
            return state

        with capture_logs() as logs:
            asyncio.run(
                reconcile_at_startup(
                    _node(),
                    log=__import__("structlog").get_logger("test"),
                    local_before=None,
                    read_state=_slow,
                )
            )

        (ok,) = _events(logs, OK_EVENT)
        assert 50 <= ok["elapsed_ms"] < 5000


class TestWhatTheFrameworkFixedIsNotSilent:
    """D-F: the pre-reconciliation snapshot names what Nautilus's own pass
    corrected inside ``node:connect``."""

    def test_a_disagreement_the_framework_already_corrected_is_logged(self):
        cache = _Cache(positions=[_Position(AAPL, "EXTERNAL", "5")])
        before = _snapshot()

        with capture_logs() as logs:
            result = _reconcile(
                _node(cache), _reader(_state(_held(AAPL, "5"))), local_before=before
            )

        (row,) = result.framework_resolved
        assert (row.local_quantity, row.broker_quantity) == (Decimal("0"), Decimal("5"))
        (record,) = _events(logs, DISCREPANCY_EVENT)
        assert (record["resolution"], record["log_level"]) == ("framework", "warning")
        assert result.reconcile_resolved == ()

    def test_no_snapshot_means_no_framework_records_and_no_failure(self):
        cache = _Cache(positions=[_Position(AAPL, "EXTERNAL", "5")])

        with capture_logs() as logs:
            result = _reconcile(_node(cache), _reader(_state(_held(AAPL, "5"))), local_before=None)

        assert result.framework_resolved == ()
        assert _events(logs, DISCREPANCY_EVENT) == []

    def test_a_disagreement_still_present_is_not_counted_as_the_frameworks(self):
        before = _snapshot(
            CachedPosition(instrument_id=AAPL, strategy_id="EXTERNAL", quantity=Decimal(4))
        )
        cache = _Cache(positions=[_Position(AAPL, "EXTERNAL", "4")])

        with capture_logs() as logs:
            result = _reconcile(_node(cache), _reader(), local_before=before)

        assert result.framework_resolved == ()
        assert [r["resolution"] for r in _events(logs, DISCREPANCY_EVENT)] == ["broker"]


class TestTheSnapshot:
    def test_it_converts_open_positions_to_domain_values(self):
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "22"), _Position(AAPL, "EXTERNAL", "0")]
        )

        assert cached_positions(cache) == (
            CachedPosition(instrument_id=NVDA, strategy_id=STRATEGY, quantity=Decimal("22")),
        )

    def test_capture_returns_the_snapshot(self):
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "1")]))

        snapshot = capture_local_state(node, _structlog())

        (position,) = snapshot.positions
        assert position.quantity == Decimal("1")
        assert snapshot.cash == () and snapshot.cash_recorded_at is None

    def test_a_failed_capture_is_contained_and_logged(self):
        node = SimpleNamespace(cache=SimpleNamespace())

        with capture_logs() as logs:
            assert capture_local_state(node, _structlog()) is None

        (record,) = _events(logs, SNAPSHOT_FAILED_EVENT)
        assert record["error_type"] == "AttributeError"
        assert record["log_level"] == "warning"
        assert record["part"] == "positions"


def _structlog():
    return __import__("structlog").get_logger("t")


class TestTheSnapshotCapturesThePreviousRunsCash:
    """Story 4.7, D-B: the "before" for ``reconcile.cash_changed`` — existing
    Redis state only (PO note: no column, no migration), read before the exec
    client's first push overwrites it (measured 1.3)."""

    def test_the_restored_summary_and_its_last_reported_time_are_captured(self):
        later = RECORDED_NANOS + 60_000_000_000
        cache = _Cache(
            general=_summary(100000.52),
            accounts={
                IB_ACCOUNT_ID: _account(
                    _account_state(RECORDED_NANOS - 1),
                    _account_state(RECORDED_NANOS),
                    _account_state(later, reported=False),  # the portfolio's own recalculation
                )
            },
        )

        snapshot = capture_local_state(_node(cache), _structlog())

        assert snapshot.cash == (_usd("100000.52"),)
        assert snapshot.cash_recorded_at == RECORDED

    def test_no_restored_summary_is_cash_unknown_not_zero(self):
        cache = _Cache(accounts={IB_ACCOUNT_ID: _account(_account_state(RECORDED_NANOS))})

        snapshot = capture_local_state(_node(cache), _structlog())

        assert snapshot.cash == () and snapshot.cash_recorded_at is None

    @pytest.mark.parametrize(
        "account",
        [
            None,
            _account(),
            _account(_account_state(0)),
            _account(_account_state(5, reported=False)),
        ],
        ids=["no-account", "no-events", "zero-ts", "none-reported"],
    )
    def test_an_unknown_recording_time_is_none_never_the_epoch(self, account):
        accounts = {} if account is None else {IB_ACCOUNT_ID: account}
        cache = _Cache(general=_summary(1.0), accounts=accounts)

        snapshot = capture_local_state(_node(cache), _structlog())

        assert snapshot.cash == (_usd("1.0"),) and snapshot.cash_recorded_at is None

    @pytest.mark.parametrize(
        "raw", [b"{not json", b'["a", "list"]', b'{"USD": "not tags"}'], ids=str
    )
    def test_an_unreadable_summary_is_contained_and_keeps_the_positions(self, raw):
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "1")],
            general={f"accountSummary:{RAW_ACCOUNT}": raw},
        )

        with capture_logs() as logs:
            snapshot = capture_local_state(_node(cache), _structlog())

        assert len(snapshot.positions) == 1 and snapshot.cash == ()
        (record,) = _events(logs, SNAPSHOT_FAILED_EVENT)
        assert record["part"] == "cash" and record["log_level"] == "warning"
        assert RAW_ACCOUNT not in str(record)

    def test_it_only_reads(self):
        """AC #1: the capture writes nothing — every cache member it touches is
        a read (``positions_open``, ``get``, ``account``)."""
        touched: list[str] = []
        real = _Cache(general=_summary(1.0))

        class _Spy:
            def __getattr__(self, name):
                touched.append(name)
                return getattr(real, name)

        node = _node()
        node.cache = _Spy()

        capture_local_state(node, _structlog())

        assert set(touched) == {"positions_open", "get", "account"}


class _FlakyCache(_Cache):
    """``positions_open`` raises while ``broken`` — the snapshot failing, then the
    phase reading a healthy cache."""

    broken = True

    def positions_open(self):
        if self.broken:
            raise RuntimeError("cache read failed")
        return super().positions_open()


class TestAFailedSnapshotIsNamedNotSilent:
    """AC #2's pairing, in one test (code review, 2026-09-28): a capture that fails
    leaves no "before", so a split the framework absorbed gets no ``framework``
    record — and ``reconcile.local_snapshot_failed`` is what says so."""

    def test_the_snapshot_failure_record_stands_in_for_the_missing_before(self):
        cache = _FlakyCache(
            positions=[
                _Position(NVDA, STRATEGY, "10"),
                _Position(NVDA, "EXTERNAL", "20"),
                _Position(NVDA, "INTERNAL-DIFF", "-10"),
            ]
        )
        node = _node(cache)

        with capture_logs() as logs:
            before = capture_local_state(node, _structlog())
            cache.broken = False
            _reconcile(node, _reader(_state(_held(NVDA, "20"))), local_before=before)

        assert before is None
        assert [r["part"] for r in _events(logs, SNAPSHOT_FAILED_EVENT)] == ["positions"]
        assert _events(logs, DISCREPANCY_EVENT) == []
        assert len(_events(logs, OK_EVENT)) == 1


class TestNoRecordCarriesTheRawAccount:
    """AC #2 / NFR26 with a raw account whose tail differs from the mask (code
    review): the summary key and ``AccountId`` embed ``DU7654321``; the broker
    state's mask is ``***626``. Neither the id nor its tail may reach a record."""

    def test_capture_and_phase_log_neither_the_account_nor_its_tail(self):
        raw = "DU7654321"
        body = {"USD": {"TotalCashValue": 5.0}}
        cache = _Cache(
            positions=[_Position(AAPL, "EXTERNAL", "4")],
            general={f"accountSummary:{raw}": json.dumps(body).encode()},
        )
        node = _node(cache)
        node.kernel.exec_engine._clients["INTERACTIVE_BROKERS"] = SimpleNamespace(
            account_id=AccountId(f"INTERACTIVE_BROKERS-{raw}")
        )

        with capture_logs() as logs:
            before = capture_local_state(node, _structlog())
            _reconcile(node, _reader(_cash_state(_usd("6"))), local_before=before)

        assert before is not None and before.cash == (_usd("5.0"),)
        assert {e["event"] for e in logs} >= set(EMITTED_RECONCILE_EVENTS)
        assert raw not in str(logs) and raw[2:] not in str(logs)


def _dividend_before() -> LocalSnapshot:
    return _snapshot(cash=(_usd("100000.00"),), recorded_at=RECORDED)


def _cash_state(*balances: CashBalance) -> BrokerState:
    return BrokerState(account="***626", positions=(), cash=balances, retrieved_at=AT)


class TestACashChangeIsNamedAtStartup:
    """Story 4.7, D-B (PO ruling A): one INFO ``reconcile.cash_changed`` per
    currency that moved while the session was not running. Informational only:
    it never refuses a start, never blocks the phase, and touches nothing."""

    def test_a_dividend_is_one_record_with_before_after_difference_and_when(self):
        with capture_logs() as logs:
            result = _reconcile(
                _node(), _reader(_cash_state(_usd("100123.45"))), local_before=_dividend_before()
            )

        assert isinstance(result, StartupReconciliation)
        (record,) = _events(logs, CASH_CHANGED_EVENT)
        assert record == {
            "event": CASH_CHANGED_EVENT,
            "log_level": "info",
            "scope": "startup",
            "currency": "USD",
            "before": "100000.00",
            "after": "100123.45",
            "difference": "123.45",
            "recorded_at": RECORDED.isoformat(),
        }
        names = [entry["event"] for entry in logs]
        assert names.index(CASH_CHANGED_EVENT) < names.index(OK_EVENT)

    def test_unchanged_cash_has_no_record(self):
        with capture_logs() as logs:
            _reconcile(
                _node(), _reader(_cash_state(_usd("100000"))), local_before=_dividend_before()
            )

        assert _events(logs, CASH_CHANGED_EVENT) == []

    @pytest.mark.parametrize("before", [None, _snapshot()], ids=["no-snapshot", "no-recorded-cash"])
    def test_nothing_recorded_means_nothing_to_name(self, before):
        with capture_logs() as logs:
            _reconcile(_node(), _reader(_cash_state(_usd("5"))), local_before=before)

        assert _events(logs, CASH_CHANGED_EVENT) == []

    def test_a_currency_on_one_side_only_is_named_with_that_side_unknown(self):
        before = _snapshot(cash=(_usd("5", "EUR"), _usd("100000")))

        with capture_logs() as logs:
            _reconcile(_node(), _reader(_cash_state(_usd("100000"))), local_before=before)

        (record,) = _events(logs, CASH_CHANGED_EVENT)
        assert (record["currency"], record["before"], record["after"]) == ("EUR", "5", None)
        assert record["difference"] is None and record["recorded_at"] is None

    def test_it_is_named_even_when_the_start_is_then_refused(self):
        """Visible rather than silent: the broker was read, so what moved is named
        — and the refusal is the position check's, never the cash record's."""
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")]))

        with capture_logs() as logs, pytest.raises(ReconciliationFailedError) as caught:
            _reconcile(node, _reader(_cash_state(_usd("1"))), local_before=_dividend_before())

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert len(_events(logs, CASH_CHANGED_EVENT)) == 1

    def test_a_failure_to_compare_cash_never_blocks_the_phase(self, monkeypatch):
        def _raise(before, broker):
            raise RuntimeError("boom")

        monkeypatch.setattr(live_startup_reconcile, "cash_changes", _raise)

        with capture_logs() as logs:
            result = _reconcile(
                _node(), _reader(_cash_state(_usd("1"))), local_before=_dividend_before()
            )

        assert isinstance(result, StartupReconciliation)
        assert _events(logs, CASH_CHANGED_EVENT) == []
        assert len(_events(logs, OK_EVENT)) == 1

    def test_a_failing_log_sink_never_blocks_the_phase(self):
        class _Exploding:
            def __getattr__(self, name):
                def _raise(*args, **kwargs):
                    raise RuntimeError("sink down")

                return _raise

        ticks = iter([10.0, 10.0125])
        result = asyncio.run(
            reconcile_at_startup(
                _node(),
                log=_Exploding(),
                local_before=_dividend_before(),
                read_state=_reader(_cash_state(_usd("1"))),
                clock=lambda: next(ticks),
            )
        )

        assert isinstance(result, StartupReconciliation)

    def test_the_record_carries_no_raw_account(self):
        with capture_logs() as logs:
            _reconcile(_node(), _reader(_cash_state(_usd("2"))), local_before=_dividend_before())

        assert RAW_ACCOUNT not in str(_events(logs, CASH_CHANGED_EVENT))

    def test_the_module_never_touches_trading_permission(self):
        """PO note (2): the record never touches trading permission — nothing in
        the startup module reads or grants it (Story 4.3's module does)."""
        source = Path(live_startup_reconcile.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        # Code review: `getattr(x, "trading_permitted")` is a string, not an attribute.
        literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        forbidden = {"trading_permitted", "confirm_state_reestablished", "observe"}

        assert not names & forbidden
        assert not literals & forbidden


def _emitted_event_names(source: str) -> set[str]:
    """Every record name the module passes to ``_emit`` — read from the code
    itself, so a new emitter cannot join without being seen (CLAUDE.md,
    "an exact-set pin against hand-written constants is one-directional")."""
    constants = {
        target.id: node.value.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_emit":
            keyword = {k.arg: k.value for k in node.keywords}.get("event")
            event = node.args[2] if len(node.args) > 2 else keyword
            assert event is not None, f"an _emit call names no event: {ast.dump(node)}"
            names.add(constants[event.id] if isinstance(event, ast.Name) else event.value)
    return names


def _reconcile_names_in_source(source: str) -> set[str]:
    """Every ``reconcile.*`` record name written anywhere in the module, however
    it is emitted — ``_emit``, a direct ``log.<level>(...)`` or a constant (code
    review, 2026-09-28: the ``_emit`` scan alone would miss a direct call)."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"reconcile\.[a-z_]+", node.value)
    }


def _drive_every_path() -> set[str]:
    """``reconcile_at_startup`` through each of its outcomes; the record names."""
    cases = [
        (_node(), _cash_state(_usd("1")), _dividend_before()),  # cash moved, clean
        (
            _node(_Cache(positions=[_Position(AAPL, "EXTERNAL", "4")])),
            _state(),
            None,
        ),  # a correction
        (_node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")])), _state(), None),  # refused
    ]
    names: set[str] = set()
    for node, state, before in cases:
        with capture_logs() as logs:
            try:
                _reconcile(node, _reader(state), local_before=before)
            except ReconciliationFailedError:
                pass
        names.update(entry["event"] for entry in logs)
    return names


class TestEveryEmittedReconcileRecordIsPinned:
    """PO note (2): ``reconcile.cash_changed`` joins the startup phase's emitted
    names, pinned against the code rather than a second hand-written list — the
    ``TestEveryDispatchedRecordNameIsPinned`` precedent, both directions.
    ``reconcile.local_snapshot_failed`` is the diagnostic outside it."""

    def test_every_name_the_module_emits_is_in_the_constant_or_is_the_diagnostic(self):
        source = Path(live_startup_reconcile.__file__).read_text(encoding="utf-8")

        emitted = _emitted_event_names(source) - {SNAPSHOT_FAILED_EVENT}

        assert emitted == set(EMITTED_RECONCILE_EVENTS)

    def test_every_name_in_the_constant_is_really_emitted_by_the_phase(self):
        assert _drive_every_path() == set(EMITTED_RECONCILE_EVENTS)

    def test_no_reconcile_record_name_is_written_outside_the_constant(self):
        """However a record is logged, its name is a literal in this module."""
        source = Path(live_startup_reconcile.__file__).read_text(encoding="utf-8")

        names = _reconcile_names_in_source(source) - {SNAPSHOT_FAILED_EVENT}

        assert names == set(EMITTED_RECONCILE_EVENTS)

    def test_the_source_scan_can_fail(self):
        planted = (
            'X = "reconcile.planted"\n_emit(log, "info", X)\n_emit(log, "info", "y.z")\n'
            '_emit(log, "info", event="reconcile.keyword")\n'
        )
        assert _emitted_event_names(planted) == {"reconcile.planted", "y.z", "reconcile.keyword"}

    def test_the_literal_scan_sees_a_direct_log_call(self):
        planted = 'log.warning("reconcile.direct", x=1)\n"""a docstring naming reconcile.ok"""\n'
        assert _reconcile_names_in_source(planted) == {"reconcile.direct"}

    def test_the_names_are_in_the_sanctioned_namespace(self):
        assert all(name.startswith("reconcile.") for name in EMITTED_RECONCILE_EVENTS)
        assert CASH_CHANGED_EVENT == "reconcile.cash_changed"

    @pytest.mark.parametrize("name", sorted(EMITTED_RECONCILE_EVENTS))
    def test_no_record_carries_a_raw_account(self, name):
        """NFR26, parametrized from the constant: a dropped name would exempt
        its record from this scan, which is why the constant is pinned."""
        node = _node(_Cache(general=_summary(1.0), positions=[_Position(AAPL, "EXTERNAL", "4")]))
        with capture_logs() as logs:
            _reconcile(node, _reader(_cash_state(_usd("2"))), local_before=_dividend_before())

        records = _events(logs, name)
        assert records, f"{name} was not emitted by the scenario"
        assert RAW_ACCOUNT not in str(records)


class TestABrokerReadFailurePropagatesUnchanged:
    def test_the_readers_own_failure_is_not_rewrapped_or_read_as_flat(self):
        failure = BrokerStateUnavailableError(BrokerStateFailure.TIMEOUT, "IBKR did not answer")
        node = _node(_Cache(positions=[_Position(NVDA, STRATEGY, "22")]))

        with capture_logs() as logs:
            with pytest.raises(BrokerStateUnavailableError) as caught:
                _reconcile(node, _reader(raises=failure))

        assert caught.value is failure
        assert _events(logs, OK_EVENT) == [] and _events(logs, DISCREPANCY_EVENT) == []
        assert node.kernel.exec_engine.reports == []


class TestTheFailureType:
    def test_it_carries_the_d3_markers_for_exit_1(self):
        error = ReconciliationFailedError(ReconciliationFailure.DISCREPANCY_REMAINS, "detail")

        assert ReconciliationFailedError.exit_outcome is LiveCheckOutcome.ERROR
        assert ReconciliationFailedError.operator_safe_message is True
        assert EXIT_CODES[classify_failure(error)] == EXIT_ERROR

    @pytest.mark.parametrize(
        ("positions", "engine_kwargs", "state"),
        [
            pytest.param([(NVDA, STRATEGY, "22")], {}, _state(), id="refused-contradicted"),
            pytest.param([(AAPL, "EXTERNAL", "4")], {}, _state(), id="corrected-clean"),
            pytest.param(
                [(AAPL, "EXTERNAL", "4")], {"returns": False}, _state(), id="framework-refused"
            ),
            pytest.param([(AAPL, "EXTERNAL", "4")], {"applies": False}, _state(), id="remains"),
            pytest.param([], {}, _state(_held(NVDA, "5")), id="hydrated-clean"),
        ],
    )
    def test_no_record_or_message_carries_the_raw_account(self, positions, engine_kwargs, state):
        """NFR26 on every path — the correcting ones build a report carrying the
        raw ``AccountId``, which must never reach a record or a message."""
        cache = _Cache(positions=[_Position(*position) for position in positions])
        node = _node(cache, **engine_kwargs)

        with capture_logs() as logs:
            try:
                _reconcile(node, _reader(state))
            except ReconciliationFailedError as error:
                assert RAW_ACCOUNT not in str(error)

        assert logs, "non-vacuity: the path logged nothing to scan"
        for entry in logs:
            assert RAW_ACCOUNT not in repr(entry), entry


class TestRequireReconciled:
    """D-I: the latch ``_phase_trading`` opens with."""

    def test_no_result_is_refused(self):
        with pytest.raises(ReconciliationFailedError) as caught:
            require_reconciled(None)

        assert caught.value.reason is ReconciliationFailure.NOT_RECONCILED

    def test_a_result_is_returned(self):
        result = StartupReconciliation(
            broker=_state(),
            framework_resolved=(),
            reconcile_resolved=(),
            open_orders=0,
            synthetic_positions=0,
            elapsed_ms=0.0,
        )
        assert require_reconciled(result) is result
