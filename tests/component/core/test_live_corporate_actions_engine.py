"""Corporate actions against a real ``LiveExecutionEngine`` (Story 4.7, AC #1, #2).

Component tier on Story 4.2's harness — a real message bus, ``Cache``,
``Portfolio`` and ``LiveExecutionEngine`` behind an IB-shaped NETTING client,
never a ``TradingNode``. Each test is one of Task 1's measured shapes.

**Absorbed (D-A, PO ruling A, 2026-09-28).** A forward split grows the broker's
position in the strategy's direction. At startup Nautilus's own pass imports
the difference into synthetic owners (measured 1.1a: ``EXTERNAL +20``,
``INTERNAL-DIFF −10`` beside the strategy's ``+10``) and the ``reconcile``
phase now accepts that state, naming the change from the pre-run snapshot. At
runtime the cycle corrects it broker-ward through ``reconcile_execution_report``
— for an exact split the framework's own reconciliation price solves to ``0``
(``live/reconciliation.py:62-89``) and falls back to the current average; for an
IBKR-style commission-inclusive average it solves to a small positive price
(measured 1.2). Either way the strategy's own lot is **never** adjusted, closed
or flattened: it keeps its quantity, its price and its single fill.

**Still refused (the PO's named test).** A reverse split — the broker holding
fewer shares than the strategy believes — is refused at startup and stops a
running session, with nothing written and the likely cause named.
"""

from decimal import Decimal

import pytest
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.identifiers import PositionId
from structlog.testing import capture_logs

from src.core.live_startup_reconcile import (
    DISCREPANCY_EVENT,
    OK_EVENT,
    ReconciliationFailedError,
    ReconciliationFailure,
    cached_positions,
)
from src.models.position_reconciliation import LocalSnapshot
from tests.component.core.test_live_runtime_reconcile_engine import _cycles, _reconciler
from tests.component.core.test_live_startup_reconcile_engine import (
    NVDA,
    STRATEGY,
    _Harness,
    _state,
)

pytestmark = pytest.mark.component

STRATEGY_POSITION_ID = PositionId(f"{NVDA.id}-{STRATEGY}")


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


@pytest.fixture
def harness():
    made: list[_Harness] = []

    def _make(broker=None, **engine_config) -> _Harness:
        made.append(_Harness(broker, **engine_config))
        return made[-1]

    yield _make
    for each in made:
        if not each.loop.is_closed():
            each.close()


def _assert_the_strategys_lot_is_untouched(h: _Harness, quantity: int, price: str) -> None:
    """Never adjusted, closed or flattened (PO ruling): the same quantity, the
    same average price, and still only the one fill it was opened with."""
    position = h.cache.position(STRATEGY_POSITION_ID)
    assert position is not None and position.is_open
    assert position.signed_decimal_qty() == Decimal(quantity)
    assert Decimal(str(position.avg_px_open)) == Decimal(price)
    assert position.event_count == 1, "a fill was written to the strategy's own position"


def _discrepancies(logs) -> list[dict]:
    return [entry for entry in logs if entry["event"] == DISCREPANCY_EVENT]


#: ``(sign, IBKR's average after the split)``. IBKR's average is
#: commission-inclusive (4.1 D-G): above the exact half for a long, below it
#: for a short (code review, 2026-09-28: both sides, both prices).
SPLIT_CASES = [(1, "100"), (1, "100.05"), (-1, "100"), (-1, "99.95")]
SPLIT_IDS = ["long-exact", "long-commission", "short-exact", "short-commission"]


class TestAForwardSplitIsAbsorbedAtStartup:
    @pytest.mark.parametrize(("sign", "broker_price"), SPLIT_CASES, ids=SPLIT_IDS)
    def test_a_forward_split_on_a_strategy_position_is_absorbed(self, harness, sign, broker_price):
        """Measured 1.1a: strategy ±10 @ 200 in Redis; overnight a 2:1 split
        leaves the broker at ±20 @ ~100."""
        h = harness({NVDA.id: (20 * sign, float(broker_price))})
        h.seed(NVDA, 10 * sign, px="200.00")
        before = LocalSnapshot(positions=cached_positions(h.cache))
        assert h.native() is True

        with capture_logs() as logs:
            result = h.reconcile(
                _state((NVDA.id, str(20 * sign), broker_price)), local_before=before
            )

        assert h.net(NVDA) == 20 * sign
        assert h.portfolio.net_position(NVDA.id) == 20 * sign
        _assert_the_strategys_lot_is_untouched(h, 10 * sign, "200.0")
        assert result.reconcile_resolved == (), "the framework's pass had already absorbed it"
        (record,) = _discrepancies(logs)
        assert record["log_level"] == "warning"
        assert (record["resolution"], record["scope"], record["kind"]) == (
            "framework",
            "startup",
            "position",
        )
        assert record["instrument_id"] == str(NVDA.id)
        assert record["local_quantity"] == record["strategy_quantity"] == str(10 * sign)
        assert record["broker_quantity"] == str(20 * sign)
        assert [e["event"] for e in logs if e["event"] == OK_EVENT] == [OK_EVENT]

    def test_a_split_after_an_earlier_mid_position_restart_is_absorbed(self, harness):
        """Measured 1.1b: the 1.4A triple from a first restart, then the split.
        The imported ``EXTERNAL`` order grows 10 -> 20 — growth, unlike 1.4S's
        shrink, does not abort the process."""
        h = harness({NVDA.id: (10, 200.0)})
        h.seed(NVDA, 10, px="200.00")
        assert h.native() is True
        h.client.broker[NVDA.id] = (20, 100.0)
        before = LocalSnapshot(positions=cached_positions(h.cache))
        assert h.native() is True

        with capture_logs() as logs:
            h.reconcile(_state((NVDA.id, "20", "100")), local_before=before)

        assert h.net(NVDA) == 20
        _assert_the_strategys_lot_is_untouched(h, 10, "200.0")
        (record,) = _discrepancies(logs)
        assert (record["resolution"], record["local_quantity"], record["broker_quantity"]) == (
            "framework",
            "10",
            "20",
        )


class TestAReverseSplitIsStillRefusedAtStartup:
    def test_a_reverse_split_on_a_strategy_position_still_refuses(self, harness):
        """The PO's named test: a 1:2 reverse split leaves the broker at +5
        against the strategy's +10. The framework's pass makes net agree; the
        phase still refuses, writes nothing, and names the likely cause."""
        h = harness({NVDA.id: (5, 400.0)})
        h.seed(NVDA, 10, px="200.00")
        assert h.native() is True
        after_native = h.open_positions()

        with capture_logs() as logs:
            with pytest.raises(ReconciliationFailedError) as caught:
                h.reconcile(_state((NVDA.id, "5", "400")))

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert "reverse split" in str(caught.value)
        assert h.open_positions() == after_native, "the phase wrote the cache on a refused start"
        _assert_the_strategys_lot_is_untouched(h, 10, "200.0")
        (record,) = _discrepancies(logs)
        assert record["resolution"] == "refused" and "reverse split" in record["likely_cause"]


class TestAForwardSplitIsAbsorbedWhileRunning:
    @pytest.mark.parametrize(
        ("sign", "broker_price", "synthetic_price"),
        [
            (1, "100", "200.0"),
            (1, "100.05", "0.1"),
            (-1, "100", "200.0"),
            (-1, "99.95", "200.0"),
        ],
        ids=SPLIT_IDS,
    )
    def test_a_forward_split_mid_session_is_corrected_and_named(
        self, harness, sign, broker_price, synthetic_price
    ):
        """Measured 1.2, both sides (code review). The framework prices the
        synthetic fill so the *combined* average equals the broker's:
        ``(target·avg − current·avg) / diff``. An exact split solves to 0 and a
        commission-inclusive short to a negative price — both rejected by
        ``calculate_reconciliation_price`` and replaced by the current average
        (``live/execution_engine.py:1526-1544``); a commission-inclusive long
        solves to a small positive price. All four are accepted — none is a
        ``RESOLUTION_REFUSED`` stop."""
        h = harness()
        h.seed(NVDA, 10 * sign, px="200.00")
        reconciler, clock = _reconciler(h, _state((NVDA.id, str(20 * sign), broker_price)))

        with capture_logs() as logs:
            _cycles(h, reconciler, clock, 3)

        assert h.net(NVDA) == 20 * sign
        assert h.portfolio.net_position(NVDA.id) == 20 * sign
        _assert_the_strategys_lot_is_untouched(h, 10 * sign, "200.0")
        synthetic = h.cache.position(PositionId(f"{NVDA.id}-INTERNAL-DIFF"))
        assert synthetic is not None and synthetic.signed_decimal_qty() == Decimal(10 * sign)
        assert Decimal(str(synthetic.avg_px_open)) == Decimal(synthetic_price)
        (record,) = _discrepancies(logs)
        assert (record["resolution"], record["scope"], record["kind"]) == (
            "broker",
            "runtime",
            "position",
        )
        assert record["local_quantity"] == record["strategy_quantity"] == str(10 * sign)
        assert record["broker_quantity"] == str(20 * sign)
        assert [e for e in logs if e["event"] == OK_EVENT], "the cycle after it was not clean"


class TestAReverseSplitStillStopsARunningSession:
    def test_a_reverse_split_mid_session_stops_it_and_writes_nothing(self, harness):
        h = harness()
        h.seed(NVDA, 10, px="200.00")
        reconciler, clock = _reconciler(h, _state((NVDA.id, "5", "400")))

        with pytest.raises(ReconciliationFailedError) as caught:
            _cycles(h, reconciler, clock, 2)

        assert caught.value.reason is ReconciliationFailure.STRATEGY_POSITION_CONTRADICTED
        assert "reverse split" in str(caught.value)
        assert h.open_positions() == {str(STRATEGY): Decimal(10)}
