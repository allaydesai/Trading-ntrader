"""What a strategy may start beside, and what its resume looks like (Story 4.5).

Unit tier, duck-typed doubles only: the module reads the node's cache and the
reconciliation result, never an engine. Three behaviours:

- **D-D** — a pre-4.5 engine cache holding the IB adapter's fabricated
  per-position order (``EXTERNAL``, ``client_order_id == instrument id``) is
  refused before the framework's own pass, which would abort the process on it
  after a shrink (Story 4.2, measured 1.4S).
- **D-C (PO ruling B)** — a strategy whose instrument carries a holding no
  strategy owns is refused, contained, and the holding is never touched.
- **D-E** — a strategy that resumes holding its own position says so, once,
  with the broker's quantity beside its own.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from src.core.exit_outcome import LiveCheckOutcome
from src.core.live_session_resume import (
    LEGACY_POSITION_IMPORT,
    RECORD_FAILED_EVENT,
    RESUME_REFUSED_EVENT,
    RESUMED_EVENT,
    SESSION_RESUME_REFUSED_EVENT,
    UNOWNED_POSITION,
    ResumeCheck,
    ResumeRefusedError,
    imported_position_orders,
    refuse_imported_position_orders,
)
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.position_reconciliation import StartupReconciliation

pytestmark = pytest.mark.unit

NVDA = "NVDA.NASDAQ"
AAPL = "AAPL.NASDAQ"
STRATEGY = "SMACrossover-000"
RAW_ACCOUNT = "DU4076626"
STARTED_AT = datetime(2026, 9, 28, 13, 25, tzinfo=UTC)
#: 2026-09-25T14:00:00Z — a position opened by a previous process run.
OPENED_BEFORE = 1_790_344_800_000_000_000
#: 2026-09-28T13:30:00Z — after this run started.
OPENED_AFTER = 1_790_602_200_000_000_000
FORBIDDEN_STEMS = ("close", "kill", "halt", "pause", "finalize")


def _order(strategy_id: str, client_order_id: str, instrument_id: str = NVDA):
    return SimpleNamespace(
        strategy_id=strategy_id, client_order_id=client_order_id, instrument_id=instrument_id
    )


class _Position:
    def __init__(self, instrument_id: str, strategy_id: str, quantity: str, **extra) -> None:
        self.instrument_id = instrument_id
        self.strategy_id = strategy_id
        self._quantity = Decimal(quantity)
        self.id = f"{instrument_id}-{strategy_id}"
        self.is_long = self._quantity > 0
        self.avg_px_open = extra.get("avg_px_open", 217.83)
        self.ts_opened = extra.get("ts_opened", OPENED_BEFORE)

    def signed_decimal_qty(self) -> Decimal:
        return self._quantity


class _Cache:
    def __init__(self, positions=(), orders=(), orders_open=()) -> None:
        self._positions = list(positions)
        self._orders = list(orders)
        self._orders_open = list(orders_open)

    def orders(self):
        return list(self._orders)

    def positions_open(self, instrument_id=None, strategy_id=None):
        return [
            p
            for p in self._positions
            if (strategy_id is None or str(p.strategy_id) == str(strategy_id))
            and (instrument_id is None or str(p.instrument_id) == str(instrument_id))
        ]

    def orders_open(self, instrument_id=None, strategy_id=None):
        return [
            o
            for o in self._orders_open
            if strategy_id is None or str(o.strategy_id) == str(strategy_id)
        ]


def _reconciliation(*held: tuple[str, str, str | None]) -> StartupReconciliation:
    broker = BrokerState(
        account="***626",
        positions=tuple(
            BrokerPosition(
                instrument_id=instrument_id,
                quantity=Decimal(quantity),
                average_price=None if price is None else Decimal(price),
                con_id=4815747,
                symbol=instrument_id.split(".")[0],
            )
            for instrument_id, quantity, price in held
        ),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=STARTED_AT,
    )
    return StartupReconciliation(
        broker=broker,
        framework_resolved=(),
        reconcile_resolved=(),
        open_orders=0,
        synthetic_positions=0,
        elapsed_ms=1.0,
    )


def _spec(bar_type: str = f"{NVDA}-1-MINUTE-LAST-EXTERNAL", strategy_id: str = "sma_crossover"):
    return SimpleNamespace(strategy_id=strategy_id, bar_types=(bar_type,))


def _check(cache: _Cache, reconciliation: StartupReconciliation, log=None) -> ResumeCheck:
    import structlog

    return ResumeCheck(
        cache,
        reconciliation,
        started_at=STARTED_AT,
        log=log if log is not None else structlog.get_logger("test"),
    )


def _events(logs, name):
    return [entry for entry in logs if entry["event"] == name]


class TestImportedPositionOrdersAreRecognisedExactly:
    """D-D's predicate: only the adapter's fabricated shape (Story 4.2, F4)."""

    def test_the_fabricated_order_is_matched(self):
        orders = [_order("EXTERNAL", NVDA)]

        assert imported_position_orders(orders) == (NVDA,)

    @pytest.mark.parametrize(
        "order",
        [
            _order("EXTERNAL", "O-20260928-133000-001-000-1"),
            _order("INTERNAL-DIFF", "O-8f2c7a1e-1465-4c51-bcf1-6e5cd96d7e74"),
            _order(STRATEGY, "O-20260928-133000-PAPER-000-1"),
        ],
        ids=["manual-external", "internal-diff", "strategy-own"],
    )
    def test_nothing_else_is(self, order):
        assert imported_position_orders([order]) == ()

    def test_every_instrument_is_named_once_and_sorted(self):
        orders = [
            _order("EXTERNAL", NVDA),
            _order("EXTERNAL", AAPL, AAPL),
            _order("EXTERNAL", NVDA),
        ]

        assert imported_position_orders(orders) == (AAPL, NVDA)


class TestAPre45NamespaceIsRefusedBeforeTheFrameworkPass:
    def test_a_clean_cache_passes_silently(self):
        with capture_logs() as logs:
            refuse_imported_position_orders(_Cache(orders=[_order(STRATEGY, "O-1")]), _log())

        assert _events(logs, SESSION_RESUME_REFUSED_EVENT) == []

    def test_a_fabricated_order_refuses_naming_the_instrument_and_the_remedy(self):
        cache = _Cache(orders=[_order("EXTERNAL", NVDA)])

        with capture_logs() as logs, pytest.raises(ResumeRefusedError) as caught:
            refuse_imported_position_orders(cache, _log())

        assert caught.value.reason == LEGACY_POSITION_IMPORT
        assert caught.value.instrument_ids == (NVDA,)
        message = str(caught.value)
        assert NVDA in message
        assert "Create a new session" in message
        (record,) = _events(logs, SESSION_RESUME_REFUSED_EVENT)
        assert record["log_level"] == "error"
        assert record["reason"] == LEGACY_POSITION_IMPORT
        assert record["instrument_ids"] == [NVDA]

    def test_the_message_carries_no_account_id(self):
        cache = _Cache(orders=[_order("EXTERNAL", NVDA)])

        with pytest.raises(ResumeRefusedError) as caught:
            refuse_imported_position_orders(cache, _log())

        assert RAW_ACCOUNT not in str(caught.value)


class TestAnUnownedHoldingRefusesOnlyItsInstrumentsStrategy:
    """D-C, PO ruling B: refused, contained, the holding never touched."""

    def test_an_unattributable_holding_on_the_strategys_instrument_refuses(self):
        cache = _Cache(positions=[_Position(NVDA, "INTERNAL-DIFF", "10")])

        with capture_logs() as logs, pytest.raises(ResumeRefusedError) as caught:
            _check(cache, _reconciliation((NVDA, "10", "217.83"))).refuse_unowned(_spec())

        assert caught.value.reason == UNOWNED_POSITION
        assert caught.value.instrument_ids == (NVDA,)
        (record,) = _events(logs, RESUME_REFUSED_EVENT)
        assert record["log_level"] == "error"
        assert record["spec_strategy_id"] == "sma_crossover"
        assert record["instrument_id"] == NVDA
        assert record["unowned_quantity"] == "10"
        assert record["strategy_quantity"] == "0"
        assert record["broker_quantity"] == "10"

    def test_the_message_names_the_instrument_both_quantities_and_the_manual_remedy(self):
        cache = _Cache(positions=[_Position(NVDA, "INTERNAL-DIFF", "10")])

        with pytest.raises(ResumeRefusedError) as caught:
            _check(cache, _reconciliation((NVDA, "10", None))).refuse_unowned(_spec())

        message = str(caught.value)
        assert NVDA in message and "+10" in message
        assert "TWS" in message
        assert "never" in message
        assert not [stem for stem in FORBIDDEN_STEMS if stem in message.lower()]

    def test_a_holding_on_another_instrument_does_not_refuse(self):
        cache = _Cache(positions=[_Position(AAPL, "EXTERNAL", "4")])

        _check(cache, _reconciliation((AAPL, "4", "150"))).refuse_unowned(_spec())

    def test_a_pre_4_5_triple_does_not_refuse(self):
        cache = _Cache(
            positions=[
                _Position(NVDA, STRATEGY, "10"),
                _Position(NVDA, "EXTERNAL", "10"),
                _Position(NVDA, "INTERNAL-DIFF", "-10"),
            ]
        )

        _check(cache, _reconciliation((NVDA, "10", "100"))).refuse_unowned(_spec())

    def test_the_strategys_own_resumed_position_does_not_refuse(self):
        cache = _Cache(positions=[_Position(NVDA, STRATEGY, "10")])

        _check(cache, _reconciliation((NVDA, "10", "100"))).refuse_unowned(_spec())


class TestAResumedPositionIsNamedOnce:
    """D-E — ``strategy.resumed``, before the strategy's history request."""

    def _strategy(self):
        return SimpleNamespace(id=STRATEGY)

    def test_an_own_position_from_a_previous_run_is_named_with_the_brokers_quantity(self):
        cache = _Cache(
            positions=[_Position(NVDA, STRATEGY, "22")],
            orders_open=[_order(STRATEGY, "O-9")],
        )

        with capture_logs() as logs:
            _check(cache, _reconciliation((NVDA, "22", "217.90"))).note_resumed(self._strategy())

        (record,) = _events(logs, RESUMED_EVENT)
        assert record["log_level"] == "info"
        assert record["strategy_id"] == STRATEGY
        assert record["instrument_id"] == NVDA
        assert record["position_id"] == f"{NVDA}-{STRATEGY}"
        assert record["side"] == "LONG"
        assert record["quantity"] == "22"
        assert record["broker_quantity"] == "22"
        assert record["broker_average_price"] == "217.90"
        assert record["avg_px_open"] == "217.83"
        assert record["ts_opened"] == "2026-09-25T14:00:00+00:00"
        assert record["opened_before_this_run"] is True
        assert record["open_orders"] == 1

    def test_a_short_is_named_short(self):
        cache = _Cache(positions=[_Position(NVDA, STRATEGY, "-5", ts_opened=OPENED_AFTER)])

        with capture_logs() as logs:
            _check(cache, _reconciliation((NVDA, "-5", None))).note_resumed(self._strategy())

        (record,) = _events(logs, RESUMED_EVENT)
        assert (record["side"], record["quantity"]) == ("SHORT", "-5")
        assert record["opened_before_this_run"] is False
        assert record["broker_average_price"] is None

    def test_a_flat_strategy_logs_nothing(self):
        cache = _Cache(positions=[_Position(NVDA, "INTERNAL-DIFF", "10")])

        with capture_logs() as logs:
            _check(cache, _reconciliation()).note_resumed(self._strategy())

        assert _events(logs, RESUMED_EVENT) == []

    def test_a_broker_that_does_not_hold_it_reads_zero(self):
        """Never reached in production (Story 4.2 refuses the start first), but
        the record must not invent a broker quantity."""
        cache = _Cache(positions=[_Position(NVDA, STRATEGY, "22")])

        with capture_logs() as logs:
            _check(cache, _reconciliation()).note_resumed(self._strategy())

        assert _events(logs, RESUMED_EVENT)[0]["broker_quantity"] == "0"

    def test_it_never_raises_and_says_it_could_not_write_the_record(self):
        class _Broken(_Cache):
            def positions_open(self, **kwargs):
                raise RuntimeError("cache down")

        with capture_logs() as logs:
            _check(_Broken(), _reconciliation()).note_resumed(self._strategy())

        (record,) = _events(logs, RECORD_FAILED_EVENT)
        assert (record["log_level"], record["error_type"]) == ("warning", "RuntimeError")

    def test_a_raising_logger_is_contained(self):
        class _Raising:
            def __getattr__(self, name):
                def _raise(*args, **kwargs):
                    raise RuntimeError("sink down")

                return _raise

        cache = _Cache(positions=[_Position(NVDA, STRATEGY, "22")])

        _check(cache, _reconciliation((NVDA, "22", None)), log=_Raising()).note_resumed(
            self._strategy()
        )


class TestTheRefusalCarriesTheD3Markers:
    def test_exit_one_with_our_own_text(self):
        assert ResumeRefusedError.exit_outcome is LiveCheckOutcome.ERROR
        assert ResumeRefusedError.operator_safe_message is True


def _log():
    import structlog

    return structlog.get_logger("test")
