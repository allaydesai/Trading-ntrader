"""Unit tests for the reconciliation value types (Story 4.6, AC #3/#5, AR38).

``src/models/reconciliation.py`` is the shape a session's own view and the
comparison's verdict take once they have crossed AR38's boundary. Like
``broker_state``, it must be importable from the services layer, so it is held
to the standard library plus that one stdlib-only sibling.
"""

import ast
import dataclasses
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.models import reconciliation as reconciliation_module
from src.models.broker_state import CashBalance
from src.models.reconciliation import (
    CashLine,
    PositionLine,
    ReconciliationReport,
    SessionView,
    ViewPosition,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


def _view(**overrides) -> SessionView:
    fields = {
        "trader_id": "PAPER-0e8f1c2a",
        "positions": (ViewPosition(instrument_id="NVDA.NASDAQ", quantity=Decimal("10")),),
        "cash": (CashBalance(currency="USD", total_cash=Decimal("1000.50")),),
    }
    fields.update(overrides)
    return SessionView(**fields)


def _line(local: str, broker: str, instrument: str = "NVDA.NASDAQ") -> PositionLine:
    return PositionLine(
        instrument_id=instrument,
        local_quantity=Decimal(local),
        broker_quantity=Decimal(broker),
        broker_average_price=None,
    )


def _report(**overrides) -> ReconciliationReport:
    fields = {
        "session_name": "swing-1",
        "trader_id": "PAPER-0e8f1c2a",
        "account": "***626",
        "positions": (_line("10", "10"),),
        "cash": (CashLine(currency="USD", local_cash=Decimal("5"), broker_cash=Decimal("5")),),
        "broker_retrieved_at": AT,
        "elapsed_ms": 12.5,
    }
    fields.update(overrides)
    return ReconciliationReport(**fields)


class TestViewPosition:
    def test_long_and_short_are_representable(self):
        assert ViewPosition("NVDA.NASDAQ", Decimal("3")).quantity == 3
        assert ViewPosition("NVDA.NASDAQ", Decimal("-2")).quantity == -2

    def test_zero_is_refused_because_flat_is_the_absence_of_a_row(self):
        with pytest.raises(ValueError, match="non-zero"):
            ViewPosition("NVDA.NASDAQ", Decimal("0"))

    @pytest.mark.parametrize("bad", [3, 3.0, Decimal("NaN"), Decimal("Infinity")])
    def test_quantity_must_be_a_finite_decimal(self, bad):
        with pytest.raises((TypeError, ValueError)):
            ViewPosition("NVDA.NASDAQ", bad)

    def test_an_instrument_id_is_required(self):
        with pytest.raises(ValueError, match="instrument_id"):
            ViewPosition("", Decimal("1"))


class TestSessionView:
    def test_a_flat_view_with_cash_is_representable(self):
        view = _view(positions=())
        assert view.positions == ()
        assert view.cash_known is True

    def test_no_cash_means_unknown_never_zero(self):
        view = _view(cash=())
        assert view.cash_known is False

    def test_one_row_per_instrument(self):
        row = ViewPosition("NVDA.NASDAQ", Decimal("1"))
        with pytest.raises(ValueError, match="one row per instrument"):
            _view(positions=(row, row))

    def test_one_row_per_currency(self):
        usd = CashBalance("USD", Decimal("1"))
        with pytest.raises(ValueError, match="one row per currency"):
            _view(cash=(usd, usd))

    def test_collections_must_be_tuples_of_the_domain_types(self):
        with pytest.raises(TypeError):
            _view(positions=[ViewPosition("NVDA.NASDAQ", Decimal("1"))])
        with pytest.raises(TypeError):
            _view(cash=({"USD": 1},))

    def test_a_trader_id_is_required(self):
        with pytest.raises(ValueError, match="trader_id"):
            _view(trader_id="")

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _view().positions = ()  # type: ignore[misc]


class TestPositionLine:
    def test_difference_is_broker_minus_session(self):
        assert _line("10", "7").difference == Decimal("-3")
        assert _line("0", "4").difference == Decimal("4")

    def test_it_matches_only_on_exact_equality(self):
        assert _line("10", "10").matches is True
        assert _line("10", "10.0001").matches is False
        assert _line("2", "-2").matches is False

    def test_quantities_must_be_decimals(self):
        with pytest.raises(TypeError):
            PositionLine("X.Y", 1, Decimal("1"), None)  # type: ignore[arg-type]


class TestCashLine:
    def test_difference_is_broker_minus_session(self):
        line = CashLine("USD", local_cash=Decimal("100.00"), broker_cash=Decimal("99.99"))
        assert line.difference == Decimal("-0.01")
        assert line.matches is False

    def test_equal_cash_matches(self):
        line = CashLine("USD", local_cash=Decimal("100.00"), broker_cash=Decimal("100.00"))
        assert line.matches is True
        assert line.difference == 0

    def test_unknown_session_cash_never_matches_and_has_no_difference(self):
        line = CashLine("USD", local_cash=None, broker_cash=Decimal("100.00"))
        assert line.matches is False
        assert line.difference is None

    def test_a_currency_the_broker_did_not_report_never_matches(self):
        line = CashLine("EUR", local_cash=Decimal("1"), broker_cash=None)
        assert line.matches is False
        assert line.difference is None

    def test_a_line_needs_at_least_one_side(self):
        with pytest.raises(ValueError, match="at least one side"):
            CashLine("USD", local_cash=None, broker_cash=None)

    def test_the_currency_is_a_three_letter_code(self):
        with pytest.raises(ValueError, match="currency"):
            CashLine("BASE", local_cash=Decimal("1"), broker_cash=Decimal("1"))


class TestReconciliationReport:
    def test_a_report_whose_every_line_matches_is_clean(self):
        report = _report()
        assert report.is_clean is True
        assert report.discrepancies == ()

    def test_a_one_share_difference_is_not_clean(self):
        """The clean test's twin: it must be able to fail."""
        report = _report(positions=(_line("10", "11"),))
        assert report.is_clean is False
        assert report.discrepancies == (report.positions[0],)

    def test_a_cash_difference_alone_is_not_clean(self):
        cash = (CashLine("USD", local_cash=Decimal("5"), broker_cash=Decimal("5.01")),)
        report = _report(cash=cash)
        assert report.is_clean is False
        assert report.discrepancies == cash

    def test_flat_and_flat_is_clean(self):
        assert _report(positions=()).is_clean is True

    def test_a_raw_account_is_refused(self):
        with pytest.raises(ValueError, match="masked"):
            _report(account="DU4076626")

    def test_a_report_needs_cash_lines(self):
        """The broker always reports cash (``BrokerState`` refuses none), so a
        report with no cash line compared nothing — unrepresentable."""
        with pytest.raises(ValueError, match="cash"):
            _report(cash=())

    def test_timestamps_are_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone"):
            _report(broker_retrieved_at=datetime(2026, 9, 27, 15, 0))

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _report().positions = ()  # type: ignore[misc]


#: The one first-party import allowed: ``broker_state`` is itself stdlib-only
#: (pinned by ``test_broker_state.py``), and ``CashBalance`` is shared, not copied.
ALLOWED_FIRST_PARTY = {"src.models.broker_state"}


def _disallowed_imports(source: str) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
    return [
        name
        for name in imported
        if name.split(".")[0] not in sys.stdlib_module_names and name not in ALLOWED_FIRST_PARTY
    ]


class TestImportPurity:
    """AR38: ``reconciliation_service`` (services) consumes these types."""

    def test_imports_are_standard_library_or_broker_state_only(self):
        source = Path(reconciliation_module.__file__).read_text(encoding="utf-8")

        assert "import" in source, "the scan is not looking at the module"
        assert _disallowed_imports(source) == []

    @pytest.mark.parametrize(
        "planted",
        ["import pydantic", "from src.core import live_gate", "from src.services import x"],
    )
    def test_the_scan_can_fail(self, planted):
        assert _disallowed_imports(f"import decimal\n{planted}\n") != []

    def test_importing_the_module_loads_no_framework(self):
        code = (
            "import sys, src.models.reconciliation;"
            "print(','.join(sorted(m for m in "
            "('sqlalchemy', 'nautilus_trader', 'ibapi', 'src.db', 'src.services', 'src.core') "
            "if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(reconciliation_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""
