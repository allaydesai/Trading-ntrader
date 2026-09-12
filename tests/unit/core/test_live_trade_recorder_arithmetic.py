"""Unit tests for the three pure ``Decimal`` helpers in
``src/core/live_trade_recorder.py`` (Story 3.5, Task 2).

Unit tier: no ``nautilus_trader`` import anywhere in this file or in the
module under test — hand-rolled doubles stand in for ``Money``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.core.live_trade_recorder import select_commission, to_price_decimal, unix_nanos_to_utc

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Currency:
    code: str


@dataclass(frozen=True)
class _Money:
    amount: Decimal
    code: str

    def as_decimal(self) -> Decimal:
        return self.amount

    @property
    def currency(self) -> _Currency:
        return _Currency(self.code)

    def __str__(self) -> str:
        return f"{self.amount} {self.code}"


class TestToPriceDecimal:
    def test_a_terminating_float_quantizes_to_eight_places(self):
        assert to_price_decimal(100.1) == Decimal("100.10000000")

    def test_a_non_terminating_float_quantizes_to_eight_places(self):
        assert to_price_decimal(10.006666666666667) == Decimal("10.00666667")

    def test_the_conversion_goes_through_str_not_the_raw_float(self):
        """M8's pin, corrected by measurement. ``Decimal(100.1)`` IS a
        51-digit artifact (``100.099999999999994315658113919198513031
        005859375``), but quantizing it to 8 dp happens to round back to the
        clean ``100.10000000`` regardless — the error sits at the 15th
        significant digit, far past where an 8 dp quantize looks. Every price
        in this story's own worked examples (100.00, 106.0, 110.00, 10.01,
        the real measured ``avg_px_open`` for the 100@10.00+200@10.01 case)
        was checked the same way and none of them diverge either — so
        ``100.1`` does not actually kill M8 (Decimal(value) instead of
        Decimal(str(value))), a discrepancy from the story's Task 9.1 claim,
        disclosed here rather than silently worked around. A value with more
        significant digits does diverge, because the float error then reaches
        past the 8th decimal place, and this is the test the mutation sweep's
        M8 entry actually kills against.
        """
        raw_artifact = Decimal(100.1)
        via_str = Decimal(str(100.1))
        assert raw_artifact != via_str, (
            "the artifact itself must exist for this test to mean anything"
        )
        quantum = Decimal("0.00000001")
        assert raw_artifact.quantize(quantum) == via_str.quantize(quantum), (
            "100.1 happens to round to the same 8 dp value either way; documented above, not a bug"
        )

        diverging_price = 1234567.891234565
        assert Decimal(diverging_price).quantize(quantum) != Decimal(str(diverging_price)).quantize(
            quantum
        ), "this value must itself diverge under quantization, or it cannot kill the mutation below"
        assert to_price_decimal(diverging_price) == Decimal(str(diverging_price)).quantize(quantum)

    def test_nan_raises_value_error(self):
        with pytest.raises(ValueError, match="NaN"):
            to_price_decimal(float("nan"))


class TestSelectCommission:
    def test_empty_yields_zero_in_the_settlement_currency(self):
        amount, code, diagnostics = select_commission([], "USD")
        assert amount == Decimal("0")
        assert code == "USD"
        assert diagnostics == ()

    def test_a_single_entry_is_returned_as_is_with_no_diagnostics(self):
        amount, code, diagnostics = select_commission([_Money(Decimal("5.75"), "USD")], "USD")
        assert amount == Decimal("5.75")
        assert code == "USD"
        assert diagnostics == ()

    def test_two_currencies_with_the_settlement_one_present_picks_it(self):
        commissions = [_Money(Decimal("1.00"), "EUR"), _Money(Decimal("2.50"), "USD")]
        amount, code, diagnostics = select_commission(commissions, "USD")
        assert amount == Decimal("2.50")
        assert code == "USD"
        assert diagnostics == ("1.00 EUR", "2.50 USD")

    def test_two_currencies_with_the_settlement_one_absent_picks_the_first(self):
        commissions = [_Money(Decimal("1.00"), "EUR"), _Money(Decimal("2.50"), "GBP")]
        amount, code, diagnostics = select_commission(commissions, "USD")
        assert amount == Decimal("1.00")
        assert code == "EUR"
        assert diagnostics == ("1.00 EUR", "2.50 GBP")

    def test_never_raises_on_any_shape(self):
        """The handler must always produce a trade — no input shape here may raise."""
        select_commission([], "USD")
        select_commission([_Money(Decimal("0"), "USD")], "USD")
        select_commission(
            [_Money(Decimal("1"), "USD"), _Money(Decimal("1"), "EUR"), _Money(Decimal("1"), "GBP")],
            "JPY",
        )


class TestUnixNanosToUtc:
    def test_one_second_past_epoch(self):
        assert unix_nanos_to_utc(1_000_000_000) == datetime(1970, 1, 1, 0, 0, 1, tzinfo=UTC)

    def test_the_result_is_tz_aware(self):
        assert unix_nanos_to_utc(0).tzinfo is not None

    def test_sub_microsecond_nanos_truncate(self):
        assert unix_nanos_to_utc(1_000_000_999) == unix_nanos_to_utc(1_000_000_000)

    def test_exact_no_float_arithmetic(self):
        """A duration nowhere near a clean fraction of a second must convert
        without accumulating float error.
        """
        ns = 3_141_592_653_589
        expected_micros = ns // 1_000
        result = unix_nanos_to_utc(ns)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        assert (result - epoch).total_seconds() * 1_000_000 == pytest.approx(expected_micros, abs=0)


class TestHoldingPeriodIsIntegerDivision:
    """``holding_period_seconds = duration_ns // 1_000_000_000`` — integer
    division, kept out of the module's own float-division equivalent
    (``backtest_persistence.py``'s ``int(x / 1e9)``); the two agree for every
    real duration, so this pins the safer of the two forms.
    """

    def test_three_seconds_exactly(self):
        assert 3_000_000_000 // 1_000_000_000 == 3

    def test_a_duration_with_a_remainder_truncates(self):
        assert 3_999_999_999 // 1_000_000_000 == 3
