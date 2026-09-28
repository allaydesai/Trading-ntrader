"""Unit tests for the on-demand reconciliation comparison (Story 4.6, AC #3/#5).

``src/services/reconciliation_service.py`` compares two domain values — the
broker's ``BrokerState`` (Story 4.1) and the session's ``SessionView`` — and
renders the verdict. Pure: no Nautilus, no SQL, no I/O. Exactness is the whole
contract (FR36: "no averaging, no close enough"), so every number here is a
``Decimal`` and every comparison is ``==``.
"""

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.reconciliation import SessionView, ViewPosition
from src.services import reconciliation_service
from src.services.reconciliation_service import compare, render_report

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)
RAW_ACCOUNT = "DU4076626"


def _bp(instrument: str, qty: str, *, price: str | None = "100", resolved: bool = True, symbol="X"):
    return BrokerPosition(
        instrument_id=instrument,
        quantity=Decimal(qty),
        average_price=None if price is None else Decimal(price),
        con_id=abs(hash(instrument)) % 10_000 + 1,
        symbol=symbol,
        instrument_resolved=resolved,
    )


def _broker(*positions, cash=(("USD", "1000.00"),)) -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(sorted(positions, key=lambda p: p.instrument_id)),
        cash=tuple(CashBalance(c, Decimal(v)) for c, v in cash),
        retrieved_at=AT,
    )


def _local(*positions, cash=(("USD", "1000.00"),), recorded_at=None) -> SessionView:
    return SessionView(
        trader_id="PAPER-0e8f1c2a",
        positions=tuple(ViewPosition(i, Decimal(q)) for i, q in positions),
        cash=tuple(CashBalance(c, Decimal(v)) for c, v in cash),
        cash_recorded_at=recorded_at,
    )


RECORDED = datetime(2026, 9, 26, 20, 0, 1, 250000, tzinfo=UTC)


def _compare(broker: BrokerState, local: SessionView):
    return compare(broker, local, session_name="swing-1", elapsed_ms=42.0)


class TestPositions:
    def test_matching_positions_are_clean(self):
        report = _compare(_broker(_bp("NVDA.NASDAQ", "10")), _local(("NVDA.NASDAQ", "10")))

        assert report.is_clean is True
        [line] = report.positions
        assert (line.instrument_id, line.local_quantity, line.broker_quantity) == (
            "NVDA.NASDAQ",
            Decimal("10"),
            Decimal("10"),
        )

    def test_flat_and_flat_is_clean(self):
        report = _compare(_broker(), _local())

        assert report.positions == ()
        assert report.is_clean is True

    def test_a_position_only_the_broker_holds_is_a_discrepancy(self):
        report = _compare(_broker(_bp("AAPL.NASDAQ", "4")), _local())

        [line] = report.discrepancies
        assert (line.instrument_id, line.local_quantity, line.broker_quantity) == (
            "AAPL.NASDAQ",
            Decimal("0"),
            Decimal("4"),
        )
        assert line.difference == Decimal("4")

    def test_a_position_only_the_session_holds_is_a_discrepancy(self):
        """The p7-fill-0901 phantom: the cache held what the broker did not."""
        report = _compare(_broker(), _local(("AAPL.NASDAQ", "4")))

        [line] = report.discrepancies
        assert (line.local_quantity, line.broker_quantity, line.difference) == (
            Decimal("4"),
            Decimal("0"),
            Decimal("-4"),
        )

    def test_a_size_mismatch_is_a_discrepancy(self):
        report = _compare(_broker(_bp("NVDA.NASDAQ", "7")), _local(("NVDA.NASDAQ", "10")))

        [line] = report.discrepancies
        assert line.difference == Decimal("-3")

    def test_a_sign_flip_is_a_discrepancy(self):
        report = _compare(_broker(_bp("NVDA.NASDAQ", "-5")), _local(("NVDA.NASDAQ", "5")))

        [line] = report.discrepancies
        assert line.difference == Decimal("-10")

    def test_a_ten_thousandth_of_a_share_is_a_discrepancy(self):
        """No tolerance: the smallest representable difference is reported."""
        report = _compare(_broker(_bp("BTC.PAXOS", "1.0001")), _local(("BTC.PAXOS", "1")))

        [line] = report.discrepancies
        assert line.difference == Decimal("0.0001")

    def test_an_unresolved_broker_row_is_reported_by_symbol_never_dropped(self):
        row = _bp("IB-CONID-265598", "5", resolved=False, symbol="AAPL")
        report = _compare(_broker(row), _local())

        [line] = report.discrepancies
        assert line.instrument_id == "IB-CONID-265598"
        assert line.broker_resolved is False
        assert line.broker_symbol == "AAPL"

    def test_average_price_is_carried_but_never_compared(self):
        """4.1 D-G: IBKR's avgCost is commission-inclusive; quantities decide."""
        report = _compare(
            _broker(_bp("NVDA.NASDAQ", "10", price="123.456")), _local(("NVDA.NASDAQ", "10"))
        )

        assert report.is_clean is True
        assert report.positions[0].broker_average_price == Decimal("123.456")

    def test_lines_are_sorted_by_instrument(self):
        report = _compare(
            _broker(_bp("ZZZ.NYSE", "1"), _bp("AAA.NYSE", "1")),
            _local(("MMM.NYSE", "1")),
        )

        assert [line.instrument_id for line in report.positions] == [
            "AAA.NYSE",
            "MMM.NYSE",
            "ZZZ.NYSE",
        ]


class TestCash:
    def test_equal_cash_is_clean(self):
        report = _compare(_broker(), _local())

        [line] = report.cash
        assert line.matches is True

    @pytest.mark.parametrize(
        ("local", "broker", "difference"),
        [("1000.00", "999.99", "-0.01"), ("1000.00", "1000.01", "0.01"), ("0", "-50", "-50")],
    )
    def test_any_cash_difference_is_a_discrepancy_to_the_cent(self, local, broker, difference):
        report = _compare(_broker(cash=(("USD", broker),)), _local(cash=(("USD", local),)))

        [line] = report.discrepancies
        assert line.difference == Decimal(difference)

    def test_unknown_session_cash_is_a_discrepancy_never_zero(self):
        report = _compare(_broker(), _local(cash=()))

        [line] = report.discrepancies
        assert line.local_cash is None
        assert line.broker_cash == Decimal("1000.00")

    def test_a_currency_on_one_side_only_is_a_discrepancy(self):
        report = _compare(_broker(cash=(("USD", "1"),)), _local(cash=(("USD", "1"), ("EUR", "2"))))

        [line] = report.discrepancies
        assert (line.currency, line.local_cash, line.broker_cash) == ("EUR", Decimal("2"), None)


class TestTheReportCarriesItsContext:
    def test_session_trader_account_and_timing(self):
        report = _compare(_broker(), _local())

        assert report.session_name == "swing-1"
        assert report.trader_id == "PAPER-0e8f1c2a"
        assert report.account == "***626"
        assert report.broker_retrieved_at == AT
        assert report.elapsed_ms == 42.0


class TestRender:
    def test_a_clean_report_says_so_in_words(self):
        lines = render_report(
            _compare(_broker(_bp("NVDA.NASDAQ", "10")), _local(("NVDA.NASDAQ", "10")))
        )

        result = lines[-1]
        assert result.startswith("RESULT: clean")
        assert "match IBKR exactly" in result
        assert "positions=1" in result
        assert any("position NVDA.NASDAQ session=+10 broker=+10" in line for line in lines)

    def test_a_flat_clean_report_reads_positions_zero(self):
        lines = render_report(_compare(_broker(), _local()))

        assert lines[-1].startswith("RESULT: clean")
        assert "positions=0" in lines[-1]

    def test_a_position_discrepancy_names_instrument_expected_actual_and_difference(self):
        lines = render_report(_compare(_broker(_bp("AAPL.NASDAQ", "4", price="150.5")), _local()))

        [line] = [line for line in lines if line.startswith("position AAPL.NASDAQ")]
        assert "session=0" in line
        assert "broker=+4" in line
        assert "difference=+4" in line
        assert "avg_price=150.5" in line
        assert "DISCREPANCY" in line
        assert lines[-1].startswith("RESULT: discrepancy")

    def test_a_cash_discrepancy_names_both_sides_and_the_difference(self):
        lines = render_report(
            _compare(_broker(cash=(("USD", "990.10"),)), _local(cash=(("USD", "1000.52"),)))
        )

        [line] = [line for line in lines if line.startswith("cash USD")]
        assert "session=1000.52" in line
        assert "broker=990.10" in line
        assert "difference=-10.42" in line
        assert "DISCREPANCY" in line

    def test_unknown_session_cash_reads_unknown_not_zero(self):
        lines = render_report(_compare(_broker(), _local(cash=())))

        [line] = [line for line in lines if line.startswith("cash USD")]
        assert "session=unknown" in line
        assert "session=0" not in line

    def test_an_unresolved_row_shows_its_symbol(self):
        row = _bp("IB-CONID-265598", "5", resolved=False, symbol="AAPL")
        lines = render_report(_compare(_broker(row), _local()))

        assert any("unresolved symbol=AAPL" in line for line in lines)

    def test_the_result_line_says_nothing_was_changed(self):
        """FR35: the command reports; it never auto-resolves."""
        lines = render_report(_compare(_broker(_bp("AAPL.NASDAQ", "4")), _local()))

        assert "nothing was changed" in lines[-1]

    def test_the_account_is_masked_in_every_line(self):
        lines = render_report(_compare(_broker(_bp("AAPL.NASDAQ", "4")), _local()))

        assert any("account=***626" in line for line in lines)
        assert not any(RAW_ACCOUNT in line for line in lines)


class TestSessionCashAsOf:
    """Story 4.7, D-C (PO ruling A): a cash difference says since when — the
    time the broker last reported the session's cash — so a dividend on a
    stopped session reads as "moved since <time>" (Story 4.6's routed debt)."""

    def test_the_report_carries_when_the_sessions_cash_was_recorded(self):
        report = _compare(_broker(), _local(recorded_at=RECORDED))

        assert report.local_cash_recorded_at == RECORDED

    def test_a_cash_discrepancy_says_session_cash_as_of_when(self):
        lines = render_report(
            _compare(
                _broker(cash=(("USD", "1123.45"),)),
                _local(cash=(("USD", "1000.00"),), recorded_at=RECORDED),
            )
        )

        [note] = [line for line in lines if line.startswith("note:")]
        assert f"session cash as of {RECORDED.isoformat()}" in note

    def test_an_unknown_time_is_said_so_never_invented(self):
        lines = render_report(
            _compare(_broker(cash=(("USD", "1123.45"),)), _local(cash=(("USD", "1000.00"),)))
        )

        [note] = [line for line in lines if line.startswith("note:")]
        assert "session cash as of an unknown time" in note

    def test_unknown_session_cash_is_not_described_as_something_received(self):
        """Code review: with no recorded cash at all the engine received nothing,
        so the note must not say it is "the last TotalCashValue ... received"."""
        lines = render_report(_compare(_broker(), _local(cash=())))

        [note] = [line for line in lines if line.startswith("note:")]
        assert "never recorded" in note and "unknown" in note
        assert "the last TotalCashValue" not in note

    def test_a_clean_report_has_no_note(self):
        lines = render_report(_compare(_broker(), _local(recorded_at=RECORDED)))

        assert not [line for line in lines if line.startswith("note:")]


#: Constructs a comparison could use to hide a difference. FR36: none of them.
_TOLERANCE_CALLS = {"isclose", "round", "quantize", "approx"}
_TOLERANCE_WORDS = ("tolerance", "epsilon", "close_enough", "threshold")


def _tolerance_constructs(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _TOLERANCE_CALLS:
                found.append(name)
        if isinstance(node, ast.Name | ast.arg) and any(
            word in (node.id if isinstance(node, ast.Name) else node.arg).lower()
            for word in _TOLERANCE_WORDS
        ):
            found.append(node.id if isinstance(node, ast.Name) else node.arg)
    return found


class TestNoTolerance:
    """AC #3: no averaging, no "close enough" — structurally."""

    @pytest.mark.parametrize(
        "relative_path",
        [
            "src/services/reconciliation_service.py",
            "src/models/reconciliation.py",
            # The netting of the session's positions happens here, not in the service.
            "src/core/live_session_view.py",
            "src/core/live_reconcile.py",
            "src/cli/commands/live_reconcile.py",
        ],
    )
    def test_no_tolerance_construct_in_any_new_module(self, relative_path):
        root = Path(reconciliation_service.__file__).resolve().parents[2]
        source = (root / relative_path).read_text(encoding="utf-8")

        assert "def " in source, "the scan is not looking at a module"
        assert _tolerance_constructs(source) == []

    @pytest.mark.parametrize(
        "planted",
        [
            "math.isclose(a, b)",
            "round(a, 2)",
            "a.quantize(b)",
            "TOLERANCE = 1",
            "def f(epsilon): pass",
        ],
    )
    def test_the_scan_can_fail(self, planted):
        assert _tolerance_constructs(planted) != []


def _imported_modules(source: str) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


class TestLayering:
    """AR38/D-H: the service imports no Nautilus, no SQL, no live-path module."""

    FORBIDDEN = ("nautilus_trader", "ibapi", "sqlalchemy", "src.db", "src.core.live_")

    def test_the_service_imports_nothing_forbidden(self):
        imported = _imported_modules(
            Path(reconciliation_service.__file__).read_text(encoding="utf-8")
        )

        assert imported, "the scan is not looking at the module"
        assert [name for name in imported if name.startswith(self.FORBIDDEN)] == []

    def test_the_scan_can_fail(self):
        planted = "from nautilus_trader.model import Position\nimport sqlalchemy\n"
        assert [n for n in _imported_modules(planted) if n.startswith(self.FORBIDDEN)] != []
