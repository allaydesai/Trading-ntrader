"""Unit tests for position sizing logic."""

from decimal import Decimal

import pytest

from src.core.position_sizing import PositionSizingLogic


@pytest.mark.unit
class TestFixedSizeCalculation:
    """Test fixed position size calculation."""

    def test_fixed_size_positive(self):
        """Test that fixed size returns the same value."""
        logic = PositionSizingLogic()
        size = logic.calculate_fixed_size(Decimal("100"))
        assert size == Decimal("100")

    def test_fixed_size_zero(self):
        """Test that zero fixed size is valid."""
        logic = PositionSizingLogic()
        size = logic.calculate_fixed_size(Decimal("0"))
        assert size == Decimal("0")

    def test_fixed_size_negative_raises_error(self):
        """Test that negative fixed size raises ValueError."""
        logic = PositionSizingLogic()
        with pytest.raises(ValueError, match="Fixed units cannot be negative"):
            logic.calculate_fixed_size(Decimal("-10"))


@pytest.mark.unit
class TestRiskBasedSizeCalculation:
    """Test risk-based position sizing."""

    def test_risk_based_size_standard_case(self):
        """Test standard risk-based calculation."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("95"),
        )

        # Risk: 10000 * 0.02 = 200
        # Price risk: |100 - 95| = 5
        # Size: 200 / 5 = 40
        assert size == Decimal("40.00")

    def test_risk_based_size_with_small_stop(self):
        """Test risk-based sizing with tight stop loss."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.01"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
        )

        # Risk: 10000 * 0.01 = 100
        # Price risk: 1
        # Size: 100 / 1 = 100
        assert size == Decimal("100.00")

    def test_risk_based_size_short_position(self):
        """Test risk-based sizing for short position."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("105"),
        )

        # abs(100 - 105) = 5, same calculation
        assert size == Decimal("40.00")

    def test_zero_price_risk_returns_zero(self):
        """Test that zero price risk returns zero size."""
        logic = PositionSizingLogic()

        size = logic.calculate_risk_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            entry_price=Decimal("100"),
            stop_price=Decimal("100"),
        )

        assert size == Decimal("0")

    def test_invalid_account_balance(self):
        """Test that invalid account balance raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Account balance must be positive"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("-100"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_invalid_risk_percent(self):
        """Test that invalid risk percent raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Risk percent must be between 0 and 1"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("1.5"),
                entry_price=Decimal("100"),
                stop_price=Decimal("95"),
            )

    def test_negative_prices_raise_error(self):
        """Test that negative prices raise error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Prices must be positive"):
            logic.calculate_risk_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("0.02"),
                entry_price=Decimal("-100"),
                stop_price=Decimal("95"),
            )


@pytest.mark.unit
class TestKellySizeCalculation:
    """Test Kelly Criterion position sizing."""

    def test_kelly_size_profitable_system(self):
        """Test Kelly sizing with profitable system parameters."""
        logic = PositionSizingLogic()

        size = logic.calculate_kelly_size(
            account_balance=Decimal("10000"),
            win_rate=Decimal("0.6"),
            avg_win=Decimal("150"),
            avg_loss=Decimal("100"),
            kelly_fraction=Decimal("0.25"),
        )

        # Kelly formula: (0.6 * 1.5 - 0.4) / 1.5 = 0.3333
        # Fractional: 0.3333 * 0.25 = 0.0833
        # Size: 10000 * 0.0833 ≈ 833
        assert size > Decimal("800")
        assert size < Decimal("900")

    def test_kelly_size_losing_system_returns_zero(self):
        """Test that losing system returns zero size."""
        logic = PositionSizingLogic()

        size = logic.calculate_kelly_size(
            account_balance=Decimal("10000"),
            win_rate=Decimal("0.3"),  # Low win rate
            avg_win=Decimal("100"),
            avg_loss=Decimal("150"),  # Larger losses
            kelly_fraction=Decimal("0.25"),
        )

        assert size == Decimal("0")

    def test_kelly_invalid_win_rate(self):
        """Test that invalid win rate raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Win rate must be between 0 and 1"):
            logic.calculate_kelly_size(
                account_balance=Decimal("10000"),
                win_rate=Decimal("1.5"),
                avg_win=Decimal("150"),
                avg_loss=Decimal("100"),
            )

    def test_kelly_invalid_kelly_fraction(self):
        """Test that invalid Kelly fraction raises error."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Kelly fraction must be between 0 and 1"):
            logic.calculate_kelly_size(
                account_balance=Decimal("10000"),
                win_rate=Decimal("0.6"),
                avg_win=Decimal("150"),
                avg_loss=Decimal("100"),
                kelly_fraction=Decimal("1.5"),
            )


@pytest.mark.unit
class TestVolatilityBasedSizing:
    """Test volatility-based position sizing."""

    def test_volatility_scaling_up(self):
        """Test that low volatility increases position size."""
        logic = PositionSizingLogic()

        size = logic.calculate_volatility_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            volatility=Decimal("0.01"),  # Low volatility
            target_volatility=Decimal("0.02"),
        )

        # Base: 10000 * 0.02 = 200
        # Scale: 0.02 / 0.01 = 2.0
        # Size: 200 * 2.0 = 400
        assert size == Decimal("400.00")

    def test_volatility_scaling_down(self):
        """Test that high volatility decreases position size."""
        logic = PositionSizingLogic()

        size = logic.calculate_volatility_based_size(
            account_balance=Decimal("10000"),
            risk_percent=Decimal("0.02"),
            volatility=Decimal("0.04"),  # High volatility
            target_volatility=Decimal("0.02"),
        )

        # Base: 200
        # Scale: 0.02 / 0.04 = 0.5
        # Size: 200 * 0.5 = 100
        assert size == Decimal("100.00")

    def test_volatility_invalid_parameters(self):
        """Test that invalid volatility parameters raise errors."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Volatility must be positive"):
            logic.calculate_volatility_based_size(
                account_balance=Decimal("10000"),
                risk_percent=Decimal("0.02"),
                volatility=Decimal("-0.01"),
                target_volatility=Decimal("0.02"),
            )


@pytest.mark.unit
class TestPositionSizeValidation:
    """Test position size validation and limiting."""

    def test_validate_within_limits(self):
        """Test that size within limits passes validation."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("50"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("50")

    def test_validate_caps_at_maximum(self):
        """Test that oversized position is capped at maximum."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("150"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("100")

    def test_validate_raises_to_minimum(self):
        """Test that undersized position is raised to minimum."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("5"),
            max_position_size=Decimal("100"),
            min_position_size=Decimal("10"),
        )

        assert validated == Decimal("10")

    def test_validate_zero_minimum(self):
        """Test validation with zero minimum (default)."""
        logic = PositionSizingLogic()

        validated = logic.validate_position_size(
            position_size=Decimal("50"), max_position_size=Decimal("100")
        )

        assert validated == Decimal("50")

    def test_validate_invalid_limits(self):
        """Test that invalid limits raise ValueError."""
        logic = PositionSizingLogic()

        with pytest.raises(ValueError, match="Max position size must be >= min"):
            logic.validate_position_size(
                position_size=Decimal("50"),
                max_position_size=Decimal("10"),
                min_position_size=Decimal("20"),
            )


@pytest.mark.unit
class TestContractMultiplierHelpers:
    """Futures M3 — multiplier-aware notional and contract counts."""

    def test_notional_applies_the_multiplier(self):
        from src.core.position_sizing import contract_notional

        assert contract_notional(Decimal("1"), Decimal("7000"), Decimal("50")) == Decimal("350000")
        assert contract_notional(Decimal("10"), Decimal("150")) == Decimal("1500")

    def test_units_for_notional_divides_by_contract_value(self):
        from src.core.position_sizing import units_for_notional

        units = units_for_notional(Decimal("700000"), Decimal("7000"), Decimal("50"))
        assert units == Decimal("2")
        assert units_for_notional(Decimal("1500"), Decimal("150")) == Decimal("10")

    @pytest.mark.parametrize("price,multiplier", [("7000", "0"), ("7000", "-1"), ("-1", "1")])
    def test_units_for_notional_rejects_bad_inputs(self, price, multiplier):
        from src.core.position_sizing import units_for_notional

        with pytest.raises(ValueError):
            units_for_notional(Decimal("1000"), Decimal(price), Decimal(multiplier))

    def test_a_zero_price_keeps_the_plain_division_error(self):
        """Live failure-guard tests pin a zero-price bar's failure as DivisionByZero."""
        from decimal import DivisionByZero

        from src.core.position_sizing import units_for_notional

        with pytest.raises(DivisionByZero):
            units_for_notional(Decimal("1000"), Decimal("0"))

    @pytest.mark.parametrize(
        "units,expected", [("0.29", 0), ("1", 1), ("2.99", 2), ("0", 0), ("-3", 0)]
    )
    def test_whole_contracts_floors_without_a_minimum(self, units, expected):
        from src.core.position_sizing import whole_contracts

        assert whole_contracts(Decimal(units)) == expected

    def test_instrument_multiplier_reads_quantity_like_objects(self):
        from types import SimpleNamespace

        from src.core.position_sizing import instrument_multiplier

        quantity_like = SimpleNamespace(as_decimal=lambda: Decimal("50"))
        assert instrument_multiplier(SimpleNamespace(multiplier=quantity_like)) == Decimal("50")
        assert instrument_multiplier(SimpleNamespace(multiplier=Decimal("1000"))) == 1000
        assert instrument_multiplier(SimpleNamespace()) == Decimal("1")
        assert instrument_multiplier(None) == Decimal("1")

    def test_risk_based_size_counts_dollars_per_point(self):
        """Risking $2,000 on a 10-point ES stop ($500 per contract) is 4 contracts."""
        logic = PositionSizingLogic()
        size = logic.calculate_risk_based_size(
            Decimal("100000"), Decimal("0.02"), Decimal("7000"), Decimal("6990"), Decimal("50")
        )
        assert size == Decimal("4.00")

    def test_risk_based_size_default_multiplier_is_unchanged(self):
        logic = PositionSizingLogic()
        size = logic.calculate_risk_based_size(
            Decimal("10000"), Decimal("0.02"), Decimal("100"), Decimal("95")
        )
        assert size == Decimal("40.00")

    def test_risk_based_size_rejects_a_non_positive_multiplier(self):
        logic = PositionSizingLogic()
        with pytest.raises(ValueError, match="Multiplier"):
            logic.calculate_risk_based_size(
                Decimal("10000"), Decimal("0.02"), Decimal("100"), Decimal("95"), Decimal("0")
            )


@pytest.mark.unit
def test_risk_based_futures_size_is_never_rounded_up_before_flooring():
    """3.999 contracts must stay below 4: half-even rounding to 0.01 would make it 4.00."""
    from src.core.position_sizing import whole_contracts

    logic = PositionSizingLogic()
    # risk $1,999.50 / (10 points x $50) = 3.999 contracts
    size = logic.calculate_risk_based_size(
        Decimal("99975"), Decimal("0.02"), Decimal("7000"), Decimal("6990"), Decimal("50")
    )
    assert size == Decimal("3.99")
    assert whole_contracts(size) == 3


def _fx(base: str, quote: str, increment: str = "1"):
    """A duck-typed spot FX pair: what the FX sizing helpers read."""
    from types import SimpleNamespace

    return SimpleNamespace(
        base_currency=SimpleNamespace(code=base),
        quote_currency=SimpleNamespace(code=quote),
        size_increment=Decimal(increment),
        info={"asset_class": "FX"},
    )


@pytest.mark.unit
class TestFxSizingHelpers:
    """FX M4: base-currency units from USD budgets (design section 6, D13)."""

    def test_quote_to_account_rate_for_usd_quote_usd_base_and_a_cross(self):
        from src.core.position_sizing import quote_to_account_rate

        assert quote_to_account_rate(_fx("EUR", "USD"), Decimal("1.1")) == 1
        assert quote_to_account_rate(_fx("USD", "JPY"), Decimal("160")) == Decimal("0.00625")
        rates = {"GBP": Decimal("1.25")}
        assert quote_to_account_rate(_fx("EUR", "GBP"), Decimal("0.85"), rates) == Decimal("1.25")

    def test_a_cross_without_its_rate_raises_naming_it(self):
        from src.core.position_sizing import quote_to_account_rate

        with pytest.raises(ValueError, match="GBP/USD"):
            quote_to_account_rate(_fx("EUR", "GBP"), Decimal("0.85"))

    def test_a_usd_base_needs_a_positive_price(self):
        from src.core.position_sizing import quote_to_account_rate

        with pytest.raises(ValueError, match="positive"):
            quote_to_account_rate(_fx("USD", "JPY"), Decimal("0"))

    def test_usdjpy_notional_is_its_usd_amount_in_units(self):
        """USD 100,000 at 157 is 100,000 USDJPY, not the 636 an equity divide gives."""
        from src.core.position_sizing import fx_units_for_notional, quote_to_account_rate

        price = Decimal("157")
        rate = quote_to_account_rate(_fx("USD", "JPY"), price)
        units = fx_units_for_notional(Decimal("100000"), price, rate)
        assert abs(units - Decimal("100000")) < Decimal("1e-15")  # unrounded: reciprocal noise

    @pytest.mark.parametrize("price", ["144.657", "157.123", "149.999", "101.001"])
    def test_usd_base_units_survive_decimal_division_noise(self, price):
        """1/p x p is 0.999...9 at 28 digits; flooring that must not lose a unit."""
        from src.core.position_sizing import fx_units_for_budget

        units = fx_units_for_budget(Decimal("100000"), Decimal(price), _fx("USD", "JPY"))
        assert units == Decimal("100000")

    def test_a_genuine_fraction_just_below_one_unit_is_not_rounded_up(self):
        from src.core.position_sizing import fx_units_for_budget

        budget = Decimal("0.9999999996")
        assert fx_units_for_budget(budget, Decimal("1"), _fx("EUR", "USD")) == 0
        assert fx_units_for_budget(budget, Decimal("150"), _fx("USD", "JPY")) == 0

    def test_eurusd_and_cross_units(self):
        from src.core.position_sizing import fx_units_for_budget

        # USD 100,000 at 1.08 = 92,592.59 EUR -> 92,592 units
        assert fx_units_for_budget(Decimal("100000"), Decimal("1.08"), _fx("EUR", "USD")) == 92592
        # EURGBP 0.85 at GBP 1.25: one EUR is 1.0625 USD -> 94,117.6 -> 94,117
        rates = {"GBP": Decimal("1.25")}
        units = fx_units_for_budget(Decimal("100000"), Decimal("0.85"), _fx("EUR", "GBP"), rates)
        assert units == 94117

    def test_fx_units_for_risk(self):
        from src.core.position_sizing import fx_units_for_risk

        # risk USD 1,000 over 50 pips of EURUSD (0.0050 USD a unit) = 200,000 units
        assert fx_units_for_risk(Decimal("1000"), Decimal("0.0050"), Decimal("1")) == 200000
        # USDJPY: 50 pips = 0.50 JPY a unit = 0.50 / 150 USD -> 300,000 units
        rate = Decimal(1) / Decimal(150)
        units = fx_units_for_risk(Decimal("1000"), Decimal("0.50"), rate)
        assert abs(units - 300000) < Decimal("1e-15")  # unrounded; callers floor
        assert fx_units_for_risk(Decimal("1000"), Decimal("0"), Decimal("1")) == 0

    @pytest.mark.parametrize(
        "args", [("1", "-0.1", "1"), ("1", "0.1", "0")], ids=["negative stop", "zero rate"]
    )
    def test_fx_units_for_risk_rejects_bad_inputs(self, args):
        from src.core.position_sizing import fx_units_for_risk

        with pytest.raises(ValueError):
            fx_units_for_risk(*(Decimal(a) for a in args))

    @pytest.mark.parametrize("price", ["144.657", "157.123", "149.999", "101.001"])
    def test_usd_base_risk_units_are_exact(self, price):
        """FX M6: USD 1,000 over a 0.5-JPY stop is 2,000 x price units, never one fewer.

        ``fx_units_for_risk`` with the reciprocal rate leaves 28-digit noise below the whole
        number; the budget helper sizes a USD base algebraically, as ``fx_units_for_budget``.
        """
        from src.core.position_sizing import fx_units_for_risk_budget

        units = fx_units_for_risk_budget(
            Decimal("1000"), Decimal("0.5"), Decimal(price), _fx("USD", "JPY")
        )
        assert units == (Decimal(price) * 2000).to_integral_value(rounding="ROUND_FLOOR")

    def test_risk_budget_floors_usd_quote_and_cross(self):
        from src.core.position_sizing import fx_units_for_risk_budget

        # EURUSD: USD 1,000 over 0.0030 = 333,333.3 -> 333,333 units
        eurusd = _fx("EUR", "USD")
        assert fx_units_for_risk_budget(
            Decimal("1000"), Decimal("0.0030"), Decimal("1.08"), eurusd
        ) == Decimal("333333")
        # EURGBP over 0.0040 GBP at GBP 1.25: USD 0.005 a unit -> 200,000
        rates = {"GBP": Decimal("1.25")}
        units = fx_units_for_risk_budget(
            Decimal("1000"), Decimal("0.0040"), Decimal("0.85"), _fx("EUR", "GBP"), rates
        )
        assert units == Decimal("200000")
        # increment 1,000: 333,333 -> 333,000
        lots = _fx("EUR", "USD", "1000")
        assert fx_units_for_risk_budget(
            Decimal("1000"), Decimal("0.0030"), Decimal("1.08"), lots
        ) == Decimal("333000")

    def test_risk_budget_zero_stop_and_tiny_budget_are_no_order(self):
        from src.core.position_sizing import fx_units_for_risk_budget

        usdjpy = _fx("USD", "JPY")
        assert fx_units_for_risk_budget(Decimal("1000"), Decimal("0"), Decimal("150"), usdjpy) == 0
        assert (
            fx_units_for_risk_budget(Decimal("0.001"), Decimal("0.5"), Decimal("150"), usdjpy) == 0
        )

    @pytest.mark.parametrize("pair", [("EUR", "USD"), ("USD", "JPY"), ("EUR", "GBP")])
    @pytest.mark.parametrize("price", ["0", "-1.1"])
    def test_risk_budget_rejects_a_non_positive_price_for_every_pair(self, pair, price):
        from src.core.position_sizing import fx_units_for_risk_budget

        rates = {"GBP": Decimal("1.25")}
        with pytest.raises(ValueError, match="price"):
            fx_units_for_risk_budget(
                Decimal("100"), Decimal("0.01"), Decimal(price), _fx(*pair), rates
            )

    def test_risk_budget_rejects_a_negative_stop_and_a_cross_without_rate(self):
        from src.core.position_sizing import fx_units_for_risk_budget

        with pytest.raises(ValueError):
            fx_units_for_risk_budget(
                Decimal("1000"), Decimal("-0.5"), Decimal("150"), _fx("USD", "JPY")
            )
        with pytest.raises(ValueError, match="GBP/USD"):
            fx_units_for_risk_budget(
                Decimal("1000"), Decimal("0.004"), Decimal("0.85"), _fx("EUR", "GBP")
            )

    @pytest.mark.parametrize(
        "units,increment,expected",
        [("92592.59", "1", "92592"), ("0.4", "1", "0"), ("-5", "1", "0"), ("1234", "1000", "1000")],
    )
    def test_whole_units_floors_to_the_increment_without_a_minimum(
        self, units, increment, expected
    ):
        from src.core.position_sizing import whole_units

        assert whole_units(Decimal(units), Decimal(increment)) == Decimal(expected)

    def test_whole_units_rejects_a_non_positive_increment(self):
        from src.core.position_sizing import whole_units

        with pytest.raises(ValueError, match="size_increment"):
            whole_units(Decimal("10"), Decimal("0"))

    def test_is_fx_pair_reads_the_info_asset_class(self):
        from types import SimpleNamespace

        from src.core.position_sizing import is_fx_pair

        assert is_fx_pair(_fx("EUR", "USD"))
        assert not is_fx_pair(SimpleNamespace(info={"asset_class": "CRYPTO"}))
        assert not is_fx_pair(SimpleNamespace(size_precision=0))
        assert not is_fx_pair(None)


@pytest.mark.unit
class TestIsCrypto:
    """Crypto M1 (task 1.3): the one predicate later milestones key crypto behaviour on."""

    def test_true_only_for_the_crypto_asset_class(self):
        from types import SimpleNamespace

        from src.core.position_sizing import is_crypto

        assert is_crypto(SimpleNamespace(info={"asset_class": "CRYPTO"}))
        assert not is_crypto(_fx("EUR", "USD"))
        assert not is_crypto(SimpleNamespace(info={"asset_class": "crypto"}))
        assert not is_crypto(SimpleNamespace(info={}))
        assert not is_crypto(SimpleNamespace(info=None))
        assert not is_crypto(SimpleNamespace(info="CRYPTO"))
        assert not is_crypto(SimpleNamespace(size_precision=8))
        assert not is_crypto(None)

    def test_fx_predicate_stays_false_for_crypto(self):
        from types import SimpleNamespace

        from src.core.position_sizing import is_crypto, is_fx_pair

        crypto = SimpleNamespace(info={"asset_class": "CRYPTO"})
        assert is_crypto(crypto)
        assert not is_fx_pair(crypto)


def _coin(increment: str = "0.00000001", min_base: str = "0.00005", min_usd: str = "0.5"):
    """A duck-typed spot crypto pair: what the crypto sizing helpers read."""
    from types import SimpleNamespace

    kraken = {"min_order_base": min_base, "min_order_usd": min_usd}
    return SimpleNamespace(
        size_increment=increment, info={"asset_class": "CRYPTO", "kraken": kraken}
    )


@pytest.mark.unit
class TestCryptoSizingHelpers:
    """Crypto M4 (task 4.1, design section 5, decision C13): fractional coin units."""

    STEP = Decimal("0.00000001")

    def test_usd_10k_at_78k_is_0_12820512_btc_not_1(self):
        from src.core.position_sizing import crypto_units_for_notional

        units = crypto_units_for_notional(Decimal("10000"), Decimal("78000"), self.STEP)
        assert units == Decimal("0.12820512")
        assert isinstance(units, Decimal) and units.as_tuple().exponent == -8

    def test_floors_never_rounds_up(self):
        from src.core.position_sizing import crypto_units_for_notional

        # 1 / 3 = 0.333333333... -> 0.33333333, and 0.999999999 floors to 0.99999999
        assert crypto_units_for_notional(Decimal(1), Decimal(3), self.STEP) == Decimal("0.33333333")
        units = crypto_units_for_notional(Decimal("99999.9999"), Decimal("100000"), self.STEP)
        assert units == Decimal("0.99999999")
        assert units * Decimal("100000") <= Decimal("99999.9999")

    def test_no_minimum_of_one_unit(self):
        from src.core.position_sizing import crypto_units_for_notional

        assert crypto_units_for_notional(Decimal("100"), Decimal("78000"), self.STEP) == Decimal(
            "0.00128205"
        )

    def test_below_one_increment_is_zero(self):
        from src.core.position_sizing import crypto_units_for_notional

        assert crypto_units_for_notional(Decimal("0.0001"), Decimal("78000"), self.STEP) == 0

    def test_below_the_base_minimum_is_zero(self):
        from src.core.position_sizing import crypto_units_for_notional

        # USD 3 at 78,000 is 0.00003846 BTC, under Kraken's 0.00005 BTC minimum
        args = (Decimal("3"), Decimal("78000"), self.STEP)
        assert crypto_units_for_notional(*args) == Decimal("0.00003846")
        assert crypto_units_for_notional(*args, min_base=Decimal("0.00005")) == 0
        assert crypto_units_for_notional(
            Decimal("3.9"), Decimal("78000"), self.STEP, min_base=Decimal("0.00005")
        ) == Decimal("0.00005")

    def test_below_the_usd_minimum_is_zero(self):
        from src.core.position_sizing import crypto_units_for_notional

        args = (Decimal("0.49"), Decimal("1"), self.STEP)
        assert crypto_units_for_notional(*args, min_usd=Decimal("0.5")) == 0
        assert crypto_units_for_notional(
            Decimal("0.5"), Decimal("1"), self.STEP, min_usd=Decimal("0.5")
        ) == Decimal("0.5")

    def test_non_positive_price_or_increment_raises(self):
        from src.core.position_sizing import crypto_units_for_notional

        with pytest.raises(ValueError, match="price"):
            crypto_units_for_notional(Decimal(100), Decimal(0), self.STEP)
        with pytest.raises(ValueError, match="size_increment"):
            crypto_units_for_notional(Decimal(100), Decimal(1), Decimal(0))

    def test_risk_units_floor_to_the_increment(self):
        from src.core.position_sizing import crypto_units_for_risk

        # USD 200 at risk over a 1,500 stop: 0.13333333 BTC
        units = crypto_units_for_risk(Decimal("200"), Decimal("1500"), self.STEP)
        assert units == Decimal("0.13333333")
        assert units * Decimal("1500") <= Decimal("200")

    def test_risk_zero_stop_is_no_order_and_negative_raises(self):
        from src.core.position_sizing import crypto_units_for_risk

        assert crypto_units_for_risk(Decimal("200"), Decimal(0), self.STEP) == 0
        with pytest.raises(ValueError, match="stop_distance"):
            crypto_units_for_risk(Decimal("200"), Decimal("-1"), self.STEP)

    def test_risk_minimums(self):
        from src.core.position_sizing import crypto_units_for_risk

        args = (Decimal("0.03"), Decimal("1000"), self.STEP)  # 0.00003 BTC
        assert crypto_units_for_risk(*args) == Decimal("0.00003")
        assert crypto_units_for_risk(*args, min_base=Decimal("0.00005")) == 0
        # a USD minimum needs the entry price to convert
        assert crypto_units_for_risk(*args, price=Decimal("10"), min_usd=Decimal("0.5")) == 0
        with pytest.raises(ValueError, match="price"):
            crypto_units_for_risk(*args, min_usd=Decimal("0.5"))

    def test_instrument_level_helper_reads_increment_and_kraken_minimums(self):
        from src.core.position_sizing import crypto_units

        coin = _coin()
        assert crypto_units(Decimal("10000"), Decimal("78000"), coin) == Decimal("0.12820512")
        assert crypto_units(Decimal("3"), Decimal("78000"), coin) == 0  # under 0.00005 BTC
        # a coin with no Kraken terms still floors to its increment, with no minimum
        bare = _coin()
        bare.info = {"asset_class": "CRYPTO"}
        assert crypto_units(Decimal("3"), Decimal("78000"), bare) == Decimal("0.00003846")

    def test_crypto_minimum(self):
        from src.core.position_sizing import crypto_minimum

        assert crypto_minimum(_coin()) == (Decimal("0.00005"), Decimal("0.5"))
        assert crypto_minimum(_coin(min_base="0", min_usd="0")) == (0, 0)
