"""The broker's view of positions and cash, as domain values (Story 4.1, AC #2).

These types are what crosses AR38's boundary: everything the IB adapter hands
back is converted into them by ``src/core/live_broker_state.py``, and a
services-layer consumer (Story 4.6's comparison) must be able to import them
without importing Nautilus. So the module is held to the standard library, and
its invariants make the shapes a failed read would produce unrepresentable.
"""

import ast
import dataclasses
import subprocess
import sys
from datetime import UTC, datetime, tzinfo
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.live_gate import mask_account
from src.models import broker_state as broker_state_module
from src.models.broker_state import (
    MASK_PREFIX,
    BrokerPosition,
    BrokerState,
    CashBalance,
    is_currency_code,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)


def _position(**overrides) -> BrokerPosition:
    fields = {
        "instrument_id": "NVDA.NASDAQ",
        "quantity": Decimal("22"),
        "average_price": Decimal("180.5"),
        "con_id": 4815747,
        "symbol": "NVDA",
    }
    fields.update(overrides)
    return BrokerPosition(**fields)


def _cash(**overrides) -> CashBalance:
    fields = {"currency": "USD", "total_cash": Decimal("100000.52")}
    fields.update(overrides)
    return CashBalance(**fields)


def _state(**overrides) -> BrokerState:
    fields = {
        "account": "***626",
        "positions": (_position(),),
        "cash": (_cash(),),
        "retrieved_at": AT,
    }
    fields.update(overrides)
    return BrokerState(**fields)


class TestBrokerPosition:
    def test_a_long_and_a_short_position_are_both_representable(self):
        assert _position(quantity=Decimal("22")).quantity == Decimal("22")
        assert _position(quantity=Decimal("-7")).quantity == Decimal("-7")

    def test_a_zero_quantity_is_refused_because_flat_is_the_absence_of_a_row(self):
        with pytest.raises(ValueError, match="quantity"):
            _position(quantity=Decimal("0"))

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity"), 22, 22.0])
    def test_a_quantity_must_be_a_finite_decimal(self, bad):
        with pytest.raises((ValueError, TypeError)):
            _position(quantity=bad)

    def test_an_unknown_average_price_is_none_never_zero(self):
        assert _position(average_price=None).average_price is None
        with pytest.raises(ValueError, match="average_price"):
            _position(average_price=Decimal("0"))

    @pytest.mark.parametrize("bad", [Decimal("-1"), Decimal("NaN"), 180.5])
    def test_a_known_average_price_is_a_positive_finite_decimal(self, bad):
        with pytest.raises((ValueError, TypeError)):
            _position(average_price=bad)

    def test_an_instrument_id_is_required(self):
        with pytest.raises(ValueError, match="instrument_id"):
            _position(instrument_id="")

    def test_resolved_is_the_default_and_an_unresolved_row_says_so(self):
        assert _position().instrument_resolved is True
        unresolved = _position(instrument_id="IB-CONID-4815747", instrument_resolved=False)
        assert unresolved.instrument_resolved is False

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _position().quantity = Decimal("1")  # type: ignore[misc]


class TestCashBalance:
    def test_cash_may_be_negative_or_zero_because_a_margin_account_can_owe(self):
        assert _cash(total_cash=Decimal("-250.10")).total_cash == Decimal("-250.10")
        assert _cash(total_cash=Decimal("0")).total_cash == Decimal("0")

    @pytest.mark.parametrize("bad", ["", "usd", "US D", "BASE", "US", "ÜSD"])
    def test_a_currency_is_a_three_letter_upper_case_code(self, bad):
        """``BASE`` is IBKR's aggregate of every other currency: accepting it
        would double-count cash for any consumer that sums the rows."""
        with pytest.raises(ValueError, match="currency"):
            _cash(currency=bad)

    @pytest.mark.parametrize(
        ("code", "expected"),
        [("USD", True), ("EUR", True), ("BASE", False), ("usd", False), ("", False)],
    )
    def test_is_currency_code(self, code, expected):
        assert is_currency_code(code) is expected

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("-Infinity"), 100.0])
    def test_cash_is_a_finite_decimal(self, bad):
        with pytest.raises((ValueError, TypeError)):
            _cash(total_cash=bad)


class TestBrokerState:
    def test_a_flat_account_is_a_successful_value_distinct_from_any_failure(self):
        """AC #3's half that lives in the type: flat is ``positions == ()``,
        a real value with cash — never ``None`` and never an empty stand-in
        a failed read could also produce (a failure raises; see the reader).
        """
        state = _state(positions=())

        assert state.is_flat is True
        assert state.positions == ()
        assert state.cash == (_cash(),)

    def test_a_state_holding_a_position_is_not_flat(self):
        assert _state().is_flat is False

    def test_a_raw_account_id_is_refused(self):
        """NFR26: only the masked form may ever be held, so no rendering of a
        ``BrokerState`` — a log line, a report, a ``repr`` — can leak it.
        """
        for raw in ("DU4076626", "", "***DU4076626", "***6266"):
            with pytest.raises(ValueError, match="account"):
                _state(account=raw)

    @pytest.mark.parametrize("raw", ["DU4076626", "DU1", "ABCD", "  DU4076626\n"])
    def test_every_real_mask_is_accepted(self, raw):
        """The equality pin for the duplicated ``MASK_PREFIX`` literal:
        whatever ``live_gate.mask_account`` produces must be accepted, so a
        change to the mask format fails here rather than failing every read.
        """
        masked = mask_account(raw)

        assert masked.startswith(MASK_PREFIX)
        assert _state(account=masked).account == masked

    def test_success_without_cash_is_unrepresentable(self):
        """A read that could not get cash is ``CASH_UNAVAILABLE``, never a
        state whose cash is silently absent (or zero)."""
        with pytest.raises(ValueError, match="cash"):
            _state(cash=())

    def test_collections_must_be_tuples(self):
        with pytest.raises(TypeError, match="positions"):
            _state(positions=[_position()])
        with pytest.raises(TypeError, match="cash"):
            _state(cash=[_cash()])

    def test_members_must_be_the_domain_types(self):
        with pytest.raises(TypeError, match="positions"):
            _state(positions=(object(),))
        with pytest.raises(TypeError, match="cash"):
            _state(cash=(object(),))

    def test_one_row_per_instrument_and_per_currency(self):
        with pytest.raises(ValueError, match="instrument"):
            _state(positions=(_position(), _position(quantity=Decimal("5"))))
        with pytest.raises(ValueError, match="currency"):
            _state(cash=(_cash(), _cash(total_cash=Decimal("1"))))

    def test_retrieved_at_is_timezone_aware(self):
        class NoOffset(tzinfo):
            def utcoffset(self, dt):
                return None

        with pytest.raises(ValueError, match="retrieved_at"):
            _state(retrieved_at=datetime(2026, 9, 22, 20, 0))
        with pytest.raises(ValueError, match="retrieved_at"):
            _state(retrieved_at=datetime(2026, 9, 22, 20, 0, tzinfo=NoOffset()))

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _state().positions = ()  # type: ignore[misc]


def _non_stdlib_imports(source: str) -> list[str]:
    """Every imported module whose root is not in the standard library."""
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
    return [name for name in imported if name.split(".")[0] not in sys.stdlib_module_names]


class TestImportPurity:
    """AR38: a services-layer consumer imports these types, so importing them
    must not drag in Nautilus, IBKR's API, SQLAlchemy, or either layer that
    owns I/O. Held to *stdlib only* — not merely "none of a forbidden list".
    """

    def test_imports_are_standard_library_only(self):
        source = Path(broker_state_module.__file__).read_text(encoding="utf-8")

        assert "import" in source, "the scan is not looking at the module"
        offenders = _non_stdlib_imports(source)
        assert offenders == [], f"broker_state must stay stdlib-only, found: {offenders}"

    @pytest.mark.parametrize(
        "planted",
        ["import pydantic", "from structlog import get_logger", "from src.core import live_gate"],
    )
    def test_the_scan_can_fail(self, planted):
        """Non-vacuity twin: a planted non-stdlib import is caught."""
        assert _non_stdlib_imports(f"import decimal\n{planted}\n") != []

    def test_importing_the_module_loads_no_framework(self):
        code = (
            "import sys, src.models.broker_state;"
            "print(','.join(sorted(m for m in "
            "('sqlalchemy', 'nautilus_trader', 'ibapi', 'src.db', 'src.services') "
            "if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(broker_state_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""
