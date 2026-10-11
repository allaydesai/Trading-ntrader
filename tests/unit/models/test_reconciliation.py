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
    OpenLeg,
    PositionLine,
    ReconciliationReport,
    SessionHoldings,
    SessionView,
    ViewPosition,
    WorkingOrder,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


def _leg(**overrides) -> OpenLeg:
    fields = {
        "position_id": "NVDA.NASDAQ-SMACrossover-000",
        "instrument_id": "NVDA.NASDAQ",
        "strategy_id": "SMACrossover-000",
        "side": "BUY",
        "quantity": Decimal("10"),
        "avg_px_open": Decimal("120.50"),
        "opened_at": AT,
        "opening_order_id": "O-20260927-150000-0e8f1c2a-000-1",
        "realized_pnl": Decimal("-1.00"),
        "commission_amount": Decimal("1.00"),
        "commission_currency": "USD",
    }
    fields.update(overrides)
    return OpenLeg(**fields)


def _order(**overrides) -> WorkingOrder:
    fields = {
        "client_order_id": "O-20260927-150000-0e8f1c2a-000-2",
        "instrument_id": "NVDA.NASDAQ",
        "side": "SELL",
        "quantity": Decimal("10"),
        "order_type": "LIMIT",
        "status": "ACCEPTED",
    }
    fields.update(overrides)
    return WorkingOrder(**fields)


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

    def test_when_the_cash_was_recorded_defaults_to_unknown(self):
        """Story 4.7, D-C: the account's last reported summary time, or ``None``."""
        assert _view().cash_recorded_at is None
        assert _view(cash_recorded_at=AT).cash_recorded_at == AT

    def test_a_naive_recorded_time_is_refused(self):
        with pytest.raises(ValueError, match="timezone"):
            _view(cash_recorded_at=datetime(2026, 9, 27, 15, 0))


class TestOpenLeg:
    """Story 5.5, R1a: one open position as the seal's null-exit row reads it."""

    def test_a_long_and_a_short_leg_are_representable(self):
        assert _leg().quantity == 10
        assert _leg(side="SELL", quantity=Decimal("-4")).quantity == -4

    @pytest.mark.parametrize(("side", "quantity"), [("BUY", "-1"), ("SELL", "1")])
    def test_the_side_must_agree_with_the_quantitys_sign(self, side, quantity):
        with pytest.raises(ValueError, match="side"):
            _leg(side=side, quantity=Decimal(quantity))

    def test_an_open_leg_is_never_flat(self):
        with pytest.raises(ValueError, match="non-zero"):
            _leg(quantity=Decimal("0"))

    def test_the_side_is_the_backtests_vocabulary(self):
        with pytest.raises(ValueError, match="side"):
            _leg(side="LONG")

    @pytest.mark.parametrize("price", ["0", "-1"])
    def test_the_entry_price_is_positive_as_the_trades_check_requires(self, price):
        with pytest.raises(ValueError, match="avg_px_open"):
            _leg(avg_px_open=Decimal(price))

    @pytest.mark.parametrize(
        "field", ["quantity", "avg_px_open", "realized_pnl", "commission_amount"]
    )
    def test_every_amount_is_a_finite_decimal(self, field):
        with pytest.raises((TypeError, ValueError)):
            _leg(**{field: Decimal("NaN")})
        with pytest.raises(TypeError):
            _leg(**{field: 1.5})

    def test_unknown_pnl_and_commission_are_none_never_zero(self):
        leg = _leg(realized_pnl=None, commission_amount=None, commission_currency=None)
        assert leg.realized_pnl is None
        assert leg.commission_amount is None

    def test_the_commission_currency_is_a_three_letter_code(self):
        with pytest.raises(ValueError, match="currency"):
            _leg(commission_currency="DOLLARS")

    def test_the_open_time_is_timezone_aware(self):
        with pytest.raises(ValueError, match="timezone"):
            _leg(opened_at=datetime(2026, 9, 27, 15, 0))

    @pytest.mark.parametrize(
        "field", ["position_id", "instrument_id", "strategy_id", "opening_order_id"]
    )
    def test_its_identifiers_are_required(self, field):
        with pytest.raises(ValueError, match=field):
            _leg(**{field: ""})


class TestWorkingOrder:
    """Story 5.5, R3b: an order the session's cache still holds working."""

    def test_a_working_order_is_representable(self):
        assert _order().quantity == 10

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_the_remaining_quantity_is_positive(self, quantity):
        with pytest.raises(ValueError, match="quantity"):
            _order(quantity=Decimal(quantity))

    def test_the_side_is_buy_or_sell(self):
        with pytest.raises(ValueError, match="side"):
            _order(side="SHORT")

    @pytest.mark.parametrize("field", ["client_order_id", "instrument_id", "order_type", "status"])
    def test_its_fields_are_required(self, field):
        with pytest.raises(ValueError, match=field):
            _order(**{field: ""})


class TestSessionHoldings:
    """Story 5.5, R1a: the legs behind the view, pinned to it."""

    def test_legs_that_sum_to_the_view_are_the_holdings(self):
        strategy = _leg()
        external = _leg(
            position_id="NVDA.NASDAQ-EXTERNAL",
            strategy_id="EXTERNAL",
            quantity=Decimal("5"),
        )
        view = _view(positions=(ViewPosition("NVDA.NASDAQ", Decimal("15")),))

        holdings = SessionHoldings(view=view, legs=(strategy, external))

        assert holdings.legs == (strategy, external)
        assert holdings.working_orders == ()

    def test_a_flat_view_has_no_legs(self):
        assert SessionHoldings(view=_view(positions=()), legs=()).legs == ()

    def test_legs_that_do_not_sum_to_the_view_are_refused(self):
        view = _view(positions=(ViewPosition("NVDA.NASDAQ", Decimal("15")),))

        with pytest.raises(ValueError, match="sum"):
            SessionHoldings(view=view, legs=(_leg(),))

    def test_a_listed_instrument_with_no_leg_is_refused(self):
        """The rows must say what remains: a view row nothing would record is refused."""
        with pytest.raises(ValueError, match="sum"):
            SessionHoldings(view=_view(), legs=())

    def test_a_leg_on_an_instrument_the_view_does_not_list_is_refused(self):
        """D-C: legs netting to zero leave no row, so they must leave no leg either."""
        long_leg = _leg()
        short_leg = _leg(
            position_id="NVDA.NASDAQ-EXTERNAL",
            strategy_id="EXTERNAL",
            side="SELL",
            quantity=Decimal("-10"),
        )

        with pytest.raises(ValueError, match="sum"):
            SessionHoldings(view=_view(positions=()), legs=(long_leg, short_leg))

    def test_a_position_counts_once(self):
        view = _view(positions=(ViewPosition("NVDA.NASDAQ", Decimal("20")),))

        with pytest.raises(ValueError, match="once"):
            SessionHoldings(view=view, legs=(_leg(), _leg()))

    def test_an_order_counts_once(self):
        with pytest.raises(ValueError, match="once"):
            SessionHoldings(view=_view(), legs=(_leg(),), working_orders=(_order(), _order()))

    def test_orders_that_could_not_be_read_are_named_and_default_to_none(self):
        """Code review DN1a: an unreadable order is named, never a refusal."""
        assert SessionHoldings(view=_view(), legs=(_leg(),)).unreadable_orders == ()
        holdings = SessionHoldings(view=_view(), legs=(_leg(),), unreadable_orders=("O-1", "O-2"))
        assert holdings.unreadable_orders == ("O-1", "O-2")

    @pytest.mark.parametrize("named", [("",), ("O-1", "O-1"), ["O-1"], (7,)])
    def test_an_unreadable_order_is_named_once_by_a_non_empty_id(self, named):
        with pytest.raises((TypeError, ValueError)):
            SessionHoldings(view=_view(), legs=(_leg(),), unreadable_orders=named)

    def test_collections_must_be_tuples_of_the_domain_types(self):
        with pytest.raises(TypeError):
            SessionHoldings(view=_view(), legs=[_leg()])  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            SessionHoldings(view=_view(), legs=(_leg(),), working_orders=("order",))  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            SessionHoldings(view="view", legs=())  # type: ignore[arg-type]

    def test_it_is_immutable(self):
        holdings = SessionHoldings(view=_view(), legs=(_leg(),))
        with pytest.raises(dataclasses.FrozenInstanceError):
            holdings.legs = ()  # type: ignore[misc]


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
        with pytest.raises(ValueError, match="timezone"):
            _report(local_cash_recorded_at=datetime(2026, 9, 27, 15, 0))

    def test_the_broker_read_time_stays_required(self):
        """Code review: sharing the timezone loop with the optional field must not
        make the required one optional."""
        with pytest.raises(ValueError, match="broker_retrieved_at"):
            _report(broker_retrieved_at=None)

    def test_it_carries_when_the_sessions_cash_was_recorded(self):
        """Story 4.7, D-C: "session cash as of <time>" — ``None`` when unknown."""
        assert _report().local_cash_recorded_at is None
        assert _report(local_cash_recorded_at=AT).local_cash_recorded_at == AT

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
