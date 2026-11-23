"""Pure position sizing logic with no framework dependencies."""

from collections.abc import Mapping
from decimal import ROUND_FLOOR, Decimal
from enum import Enum

ONE = Decimal("1")


def contract_notional(quantity: Decimal, price: Decimal, multiplier: Decimal = ONE) -> Decimal:
    """Dollar exposure of ``quantity`` units: ``quantity x price x multiplier``.

    ``multiplier`` is 1 for shares and the contract's point value for futures (ES 50, CL 1000),
    so one ES contract at 7000 is $350,000 of exposure, not $7,000.
    """
    return quantity * price * multiplier


def units_for_notional(notional: Decimal, price: Decimal, multiplier: Decimal = ONE) -> Decimal:
    """Unrounded units that buy ``notional`` of exposure (``notional / (price x multiplier)``).

    Raises:
        ValueError: If ``price`` is negative or ``multiplier`` is not positive.
        decimal.DivisionByZero: If ``price`` is zero. Deliberately the plain division's own
            error: the live strategy-failure guard tests pin a zero-price bar's failure as
            ``DivisionByZero`` (``tests/component/core/test_session_runner_strategy_failure.py``).
    """
    if price < 0 or multiplier <= 0:
        raise ValueError("price must not be negative and multiplier must be positive")
    return notional / (price * multiplier)


def whole_contracts(units: Decimal) -> int:
    """Floor to whole contracts; 0 means the budget does not cover one contract.

    Unlike the equity whole-share rule there is no minimum of 1: rounding $100k of ES
    exposure up to one $350k contract would more than triple the intended size.
    """
    if units <= 0:
        return 0
    return int(units.to_integral_value(rounding=ROUND_FLOOR))


def instrument_multiplier(instrument: object | None) -> Decimal:
    """The instrument's contract multiplier as a Decimal; 1 when it has none (or is absent)."""
    multiplier = getattr(instrument, "multiplier", None)
    if multiplier is None:
        return ONE
    as_decimal = getattr(multiplier, "as_decimal", None)
    return as_decimal() if callable(as_decimal) else Decimal(str(multiplier))


def is_fx_pair(instrument: object | None) -> bool:
    """A spot FX pair built from ``pair_specs.yaml`` (``info.asset_class == "FX"``).

    Duck-typed so this module stays framework-free; Kraken crypto ``CurrencyPair``s carry no
    such ``info`` and keep the equity path.
    """
    info = getattr(instrument, "info", None) or {}
    return isinstance(info, dict) and info.get("asset_class") == "FX"


def _code(currency: object) -> str:
    return str(getattr(currency, "code", currency))


def quote_to_account_rate(
    instrument: object,
    price: Decimal,
    rates: Mapping[str, Decimal] | None = None,
    account: str = "USD",
) -> Decimal:
    """Account-currency (USD) value of one unit of ``instrument``'s quote currency.

    1 for a USD quote (EURUSD), ``1 / price`` for a USD base (USDJPY: one JPY is 1/157 USD),
    otherwise ``rates[quote]`` (a cross: EURGBP needs the GBP rate).

    Raises:
        ValueError: A USD-base price that is not positive, or a cross with no rate.
    """
    quote = _code(getattr(instrument, "quote_currency"))
    base = _code(getattr(instrument, "base_currency"))
    if quote == account:
        return ONE
    if base == account:
        if price <= 0:
            raise ValueError(f"price must be positive to convert {quote} to {account}")
        return ONE / price
    rate = (rates or {}).get(quote)
    if rate is None:
        raise ValueError(f"no {quote}/{account} rate to size {base}{quote} in {account}")
    return Decimal(str(rate))


def fx_units_for_notional(notional: Decimal, price: Decimal, quote_rate: Decimal) -> Decimal:
    """Unrounded base units worth ``notional`` USD: ``notional / (price x quote_rate)``.

    USDJPY at 157 (quote rate 1/157): USD 100,000 is 100,000 units, not the 636 an equity-style
    ``notional / price`` gives.

    Raises:
        ValueError: ``price`` or ``quote_rate`` is not positive.
    """
    if price <= 0 or quote_rate <= 0:
        raise ValueError("price and quote_rate must be positive")
    return notional / (price * quote_rate)


def fx_units_for_risk(risk: Decimal, stop_distance: Decimal, quote_rate: Decimal) -> Decimal:
    """Unrounded base units that lose ``risk`` USD over ``stop_distance`` (in quote units).

    Returns 0 for a zero stop distance (no sizeable risk), like ``calculate_risk_based_size``.
    Unrounded: to size an order use ``fx_units_for_risk_budget`` (whole units, exact for a USD
    base).

    Raises:
        ValueError: A negative stop distance or a non-positive ``quote_rate``.
    """
    if stop_distance < 0 or quote_rate <= 0:
        raise ValueError("stop_distance must not be negative and quote_rate must be positive")
    if stop_distance == 0:
        return Decimal(0)
    return risk / (stop_distance * quote_rate)


def whole_units(units: Decimal, size_increment: Decimal = ONE) -> Decimal:
    """Floor ``units`` to a multiple of ``size_increment``; 0 means no order.

    No minimum of 1 (design D13): a budget below one increment trades nothing.

    Raises:
        ValueError: ``size_increment`` is not positive.
    """
    if size_increment <= 0:
        raise ValueError("size_increment must be positive")
    if units <= 0:
        return Decimal(0)
    steps = (units / size_increment).to_integral_value(rounding=ROUND_FLOOR)
    return steps * size_increment


def fx_units_for_budget(
    notional: Decimal,
    price: Decimal,
    instrument: object,
    rates: Mapping[str, Decimal] | None = None,
) -> Decimal:
    """Whole base units of FX ``instrument`` worth ``notional`` USD at ``price`` (0: no order).

    A USD-base pair (USDJPY) is sized algebraically, ``price x 1/price = 1``: its units are the
    USD notional itself. Dividing by the Decimal reciprocal instead leaves 28-digit noise
    (``1/157 x 157`` is ``0.999...9``) that flooring would turn into one unit fewer, and no
    rounding is applied anywhere else, so a genuine fraction below an increment stays below it.
    """
    increment = getattr(instrument, "size_increment", None)
    step = Decimal(str(increment)) if increment is not None else ONE
    if _code(getattr(instrument, "base_currency")) == "USD":
        if price <= 0:
            raise ValueError("price must be positive to size a USD-base pair")
        return whole_units(notional, step)
    rate = quote_to_account_rate(instrument, price, rates)
    return whole_units(fx_units_for_notional(notional, price, rate), step)


def fx_units_for_risk_budget(
    risk: Decimal,
    stop_distance: Decimal,
    price: Decimal,
    instrument: object,
    rates: Mapping[str, Decimal] | None = None,
) -> Decimal:
    """Whole base units of FX ``instrument`` that lose ``risk`` USD over ``stop_distance``.

    ``stop_distance`` is in quote units (USDJPY: yen). 0 means no order (a zero stop, or a
    budget under one increment). A USD-base pair is sized algebraically, ``risk x price /
    stop_distance``, for the reason ``fx_units_for_budget`` gives: the Decimal reciprocal
    would floor an exact 314,000 to 313,999.

    Raises:
        ValueError: A negative stop distance, a non-positive price, or a cross with no rate.
    """
    increment = getattr(instrument, "size_increment", None)
    step = Decimal(str(increment)) if increment is not None else ONE
    if stop_distance < 0:
        raise ValueError("stop_distance must not be negative")
    if stop_distance == 0:
        return Decimal(0)
    if _code(getattr(instrument, "base_currency")) == "USD":
        if price <= 0:
            raise ValueError("price must be positive to size a USD-base pair")
        return whole_units(risk * price / stop_distance, step)
    rate = quote_to_account_rate(instrument, price, rates)
    return whole_units(fx_units_for_risk(risk, stop_distance, rate), step)


class SizingMethod(str, Enum):
    """Position sizing calculation methods."""

    FIXED = "fixed"  # Fixed position size
    RISK_BASED = "risk_based"  # Based on account risk percentage
    KELLY = "kelly"  # Kelly Criterion
    VOLATILITY = "volatility"  # Based on volatility


class PositionSizingLogic:
    """
    Pure business logic for position sizing calculations.

    This class contains no framework dependencies and provides
    various position sizing methodologies for risk management.
    """

    def calculate_fixed_size(self, fixed_units: Decimal) -> Decimal:
        """
        Calculate fixed position size.

        Args:
            fixed_units: Fixed number of units to trade

        Returns:
            Position size

        Raises:
            ValueError: If units are negative

        Example:
            >>> logic = PositionSizingLogic()
            >>> logic.calculate_fixed_size(Decimal("100"))
            Decimal('100')
        """
        if fixed_units < 0:
            raise ValueError("Fixed units cannot be negative")
        return fixed_units

    def calculate_risk_based_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        multiplier: Decimal = ONE,
    ) -> Decimal:
        """
        Calculate position size based on risk percentage.

        This is the most common position sizing method, where you
        risk a fixed percentage of your account on each trade.

        Args:
            account_balance: Total account balance
            risk_percent: Percentage of account to risk (0.02 = 2%)
            entry_price: Planned entry price
            stop_price: Stop loss price
            multiplier: Dollars per point per unit (1 for shares; ES 50). The risk of one
                unit is ``|entry - stop| x multiplier``; floor futures sizes with
                ``whole_contracts``.

        Returns:
            Position size in units

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_risk_based_size(
            ...     Decimal("10000"), Decimal("0.02"),
            ...     Decimal("100"), Decimal("95")
            ... )
            >>> size
            Decimal('40.00')
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 < risk_percent <= 1):
            raise ValueError("Risk percent must be between 0 and 1")
        if entry_price <= 0 or stop_price <= 0:
            raise ValueError("Prices must be positive")
        if multiplier <= 0:
            raise ValueError("Multiplier must be positive")

        risk_amount = account_balance * risk_percent
        price_risk = abs(entry_price - stop_price) * multiplier

        if price_risk == 0:
            return Decimal("0")

        position_size = risk_amount / price_risk
        # Contracts are floored by the caller (whole_contracts): never round them up first.
        rounding = ROUND_FLOOR if multiplier != ONE else None
        return position_size.quantize(Decimal("0.01"), rounding=rounding)

    def calculate_kelly_size(
        self,
        account_balance: Decimal,
        win_rate: Decimal,
        avg_win: Decimal,
        avg_loss: Decimal,
        kelly_fraction: Decimal = Decimal("0.25"),
    ) -> Decimal:
        """
        Calculate position size using Kelly Criterion.

        Kelly formula: f* = (p * b - q) / b
        where:
        - p = probability of winning
        - q = probability of losing (1 - p)
        - b = ratio of avg win to avg loss

        Args:
            account_balance: Total account balance
            win_rate: Historical win rate (0.6 = 60%)
            avg_win: Average winning trade amount
            avg_loss: Average losing trade amount
            kelly_fraction: Fraction of Kelly to use (default 0.25 for safety)

        Returns:
            Position size as percentage of account

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_kelly_size(
            ...     Decimal("10000"), Decimal("0.6"),
            ...     Decimal("150"), Decimal("100"),
            ...     Decimal("0.25")
            ... )
            >>> size > 0
            True
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 <= win_rate <= 1):
            raise ValueError("Win rate must be between 0 and 1")
        if avg_win <= 0 or avg_loss <= 0:
            raise ValueError("Average win and loss must be positive")
        if not (0 < kelly_fraction <= 1):
            raise ValueError("Kelly fraction must be between 0 and 1")

        # Probability of losing
        lose_rate = Decimal("1") - win_rate

        # Win/loss ratio
        win_loss_ratio = avg_win / avg_loss

        # Kelly percentage: (p * b - q) / b
        kelly_pct = (win_rate * win_loss_ratio - lose_rate) / win_loss_ratio

        # Apply fractional Kelly for safety (typically 0.25 or 0.5)
        adjusted_kelly = kelly_pct * kelly_fraction

        # Ensure non-negative position size
        if adjusted_kelly < 0:
            return Decimal("0")

        return (account_balance * adjusted_kelly).quantize(Decimal("0.01"))

    def calculate_volatility_based_size(
        self,
        account_balance: Decimal,
        risk_percent: Decimal,
        volatility: Decimal,
        target_volatility: Decimal = Decimal("0.02"),
    ) -> Decimal:
        """
        Calculate position size based on volatility targeting.

        Adjusts position size inversely to volatility to maintain
        consistent risk exposure.

        Args:
            account_balance: Total account balance
            risk_percent: Base risk percentage
            volatility: Current instrument volatility (std dev)
            target_volatility: Target volatility level

        Returns:
            Volatility-adjusted position size

        Raises:
            ValueError: If parameters are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> size = logic.calculate_volatility_based_size(
            ...     Decimal("10000"), Decimal("0.02"),
            ...     Decimal("0.03"), Decimal("0.02")
            ... )
            >>> size > 0
            True
        """
        if account_balance <= 0:
            raise ValueError("Account balance must be positive")
        if not (0 < risk_percent <= 1):
            raise ValueError("Risk percent must be between 0 and 1")
        if volatility <= 0:
            raise ValueError("Volatility must be positive")
        if target_volatility <= 0:
            raise ValueError("Target volatility must be positive")

        # Base position size
        base_size = account_balance * risk_percent

        # Volatility scaling factor
        vol_scale = target_volatility / volatility

        # Adjusted position size
        adjusted_size = base_size * vol_scale

        return adjusted_size.quantize(Decimal("0.01"))

    def validate_position_size(
        self,
        position_size: Decimal,
        max_position_size: Decimal,
        min_position_size: Decimal = Decimal("0"),
    ) -> Decimal:
        """
        Validate and cap position size within limits.

        Args:
            position_size: Calculated position size
            max_position_size: Maximum allowed position size
            min_position_size: Minimum allowed position size

        Returns:
            Validated position size within limits

        Raises:
            ValueError: If limits are invalid

        Example:
            >>> logic = PositionSizingLogic()
            >>> logic.validate_position_size(
            ...     Decimal("150"), Decimal("100"), Decimal("10")
            ... )
            Decimal('100')
        """
        if min_position_size < 0:
            raise ValueError("Minimum position size cannot be negative")
        if max_position_size < min_position_size:
            raise ValueError("Max position size must be >= min position size")

        if position_size < min_position_size:
            return min_position_size
        if position_size > max_position_size:
            return max_position_size

        return position_size
