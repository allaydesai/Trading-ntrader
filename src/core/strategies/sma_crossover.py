"""SMA Crossover strategy implementation using Nautilus Trader."""

from decimal import Decimal

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from src.core.fx_rates import fx_units, usd_per_quote
from src.core.position_sizing import (
    ONE,
    contract_notional,
    crypto_minimum,
    crypto_units,
    instrument_multiplier,
    is_crypto,
    is_fx_pair,
    units_for_notional,
    whole_contracts,
    whole_units,
)
from src.core.strategy_registry import StrategyRegistry, register_strategy
from src.core.strategy_warmup import warmup_lookback
from src.models.strategy import SMAParameters


class SMAConfig(StrategyConfig):
    """
    Configuration for SMA crossover strategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to trade.
    bar_type : BarType
        The bar type for strategy execution.
    fast_period : int, default=10
        Period for fast moving average.
    slow_period : int, default=20
        Period for slow moving average.
    portfolio_value : Decimal, default=1000000
        Starting portfolio value in USD for position sizing calculations.
    position_size_pct : Decimal, default=10.0
        Position size as percentage of portfolio (e.g., 10.0 = 10%).
        Example: With $1M portfolio and 10%, each trade uses $100K notional.
    contracts : int, optional
        Futures only (``backtest run --contracts N``): trade exactly this many whole
        contracts per entry, ignoring the notional budget. Unset sizes by notional.
    units : int | Decimal, optional
        FX and spot crypto (``backtest run --units N``): trade exactly this many base units
        per entry, ignoring the notional budget. Whole units for FX; a decimal multiple of
        the coin's size increment for crypto (``0.25`` BTC). Unset sizes by notional.
    """

    instrument_id: InstrumentId
    bar_type: BarType
    fast_period: int = 10
    slow_period: int = 20
    portfolio_value: Decimal = Decimal("1000000")
    position_size_pct: Decimal = Decimal("10.0")
    contracts: int | None = None
    units: int | Decimal | None = None


def _sized_quantity(
    position_value: Decimal,
    price: Decimal,
    instrument: object | None,
    config: object | None = None,
    cache=None,
) -> tuple[Quantity | None, Decimal, Decimal]:
    """``(quantity, raw units, multiplier)`` for ``position_value`` of exposure at ``price``.

    ``quantity x price x multiplier`` is the USD exposure the strategy logs.

    Shares (multiplier 1) are whole, at least 1; crypto (size precision > 0) is fractional;
    a multiplied contract (futures) floors to whole contracts and is ``None`` below one.
    ``config.contracts`` (futures only) replaces the budget with exactly that many contracts,
    ``config.units`` (FX M5, crypto M4) with exactly that many base units.
    Spot crypto (crypto M4) is sized in coin units floored to its size increment, ``None``
    under one increment or Kraken's minimum order: USD 10,000 of BTC at 78,000 is 0.12820512.
    A spot FX pair (FX M4) is sized in base units worth ``position_value`` USD, floored to its
    size increment and ``None`` below one: USD 100,000 of USDJPY is 100,000 units, not
    ``100,000 / 157``. A cross reads its quote/USD rate from ``cache``.

    Raises:
        ValueError: ``contracts`` for an instrument with no multiplier (not futures),
            ``units`` for one that is not an FX or crypto pair (or, for crypto, not a multiple
            of its size increment), or a cross with no USD rate in ``cache``.
    """
    multiplier = instrument_multiplier(instrument)
    fixed_contracts = getattr(config, "contracts", None)
    if (fixed_units := getattr(config, "units", None)) is not None:
        if is_crypto(instrument):
            units = _fixed_crypto_units(Decimal(str(fixed_units)), instrument)
            usd_min = crypto_minimum(instrument)[1]  # under Kraken's USD minimum at this price
            qty = instrument.make_qty(units) if units * price >= usd_min else None  # type: ignore[union-attr]
            return qty, units, ONE
        if not is_fx_pair(instrument):
            raise ValueError("units applies to FX pairs and spot crypto only")
        if (rate := usd_per_quote(instrument, price, cache)) is None:  # type: ignore[arg-type]
            raise ValueError(f"No USD rate for {instrument.id} yet: USD notional unknown")  # type: ignore[union-attr]
        return Quantity.from_int(fixed_units), Decimal(fixed_units), rate
    if fixed_contracts is not None:
        if multiplier == 1:
            raise ValueError("contracts applies to futures instruments only")
        return Quantity.from_int(fixed_contracts), Decimal(fixed_contracts), multiplier
    if is_fx_pair(instrument):  # third value: USD per quote unit, so qty x price x it is USD
        units = fx_units(position_value, price, instrument, cache)  # type: ignore[arg-type]
        rate = usd_per_quote(instrument, price, cache) or ONE  # type: ignore[arg-type]
        size_prec = instrument.size_precision  # type: ignore[union-attr]
        return (Quantity(units, size_prec) if units else None), units, rate
    if is_crypto(instrument):  # fractional coin units, floored; None under the minimum
        units = crypto_units(position_value, price, instrument)
        return (instrument.make_qty(units) if units else None), units, ONE  # type: ignore[union-attr]
    raw_qty = units_for_notional(position_value, price, multiplier)
    size_prec = getattr(instrument, "size_precision", 0) if instrument else 0
    if multiplier != 1:
        contracts = whole_contracts(raw_qty)
        return (Quantity.from_int(contracts) if contracts else None), raw_qty, multiplier
    if size_prec > 0:
        return Quantity.from_str(f"{float(raw_qty):.{size_prec}f}"), raw_qty, multiplier
    return Quantity.from_int(max(int(raw_qty), 1)), raw_qty, multiplier


def _fixed_crypto_units(units: Decimal, instrument) -> Decimal:
    """A fixed crypto size (``--units 0.25``), refused unless a positive increment multiple.

    ``make_qty`` would round a stray digit half-even; a fixed size is never silently changed.
    """
    step = Decimal(str(instrument.size_increment))
    if units <= 0 or whole_units(units, step) != units:
        raise ValueError(
            f"units {units:f} for {instrument.id} is not a positive multiple of {step:f}"
        )
    return units


def _unsized(strategy, position_value: Decimal) -> Quantity | None:
    """Log a budget that sized to no order (always ``None``); crypto warns once and counts (C13)."""
    instrument = strategy.cache.instrument(strategy.instrument_id)
    if not is_crypto(instrument):
        strategy.log.warning(f"Position sizing: ${position_value:,.0f} is under one contract")
        return None
    strategy._unsized_orders = getattr(strategy, "_unsized_orders", 0) + 1
    if strategy._unsized_orders == 1:
        strategy.log.warning(
            f"Position sizing: ${position_value:,.2f} is under the {instrument.id} minimum "
            "order; no order (later drops are counted and reported on stop)"
        )
    return None


def _report_unsized(strategy) -> None:
    """The run's count of crypto orders dropped under the minimum, once, on stop."""
    if count := getattr(strategy, "_unsized_orders", 0):
        strategy.log.warning(f"Position sizing: {count} order(s) dropped under the minimum order")


@register_strategy(
    name="sma_crossover",
    description="Simple Moving Average Crossover (one-sided: trades the side of its first cross)",
    aliases=["sma", "smacrossover"],
)
class SMACrossover(Strategy):
    """
    Simple Moving Average Crossover Strategy.

    Generates buy signals when fast SMA crosses above slow SMA.
    Generates sell signals when fast SMA crosses below slow SMA.

    One-sided in practice: a signal either closes the opposite position or opens
    a new one, never both, and crosses alternate. So the first cross picks the
    side (long if bullish, short if bearish) and every later trade is on that
    side. For a long-only baseline use ``sma_crossover_long_only``.
    """

    def __init__(self, config: SMAConfig) -> None:
        """Initialize the SMA crossover strategy."""
        super().__init__(config)

        # Strategy configuration
        self.instrument_id = config.instrument_id
        self.bar_type = config.bar_type
        # Config files pass YAML floats/strings through uncoerced; sizing needs Decimals.
        self.portfolio_value = Decimal(str(config.portfolio_value))
        self.position_size_pct = Decimal(str(config.position_size_pct))

        # Initialize indicators
        self.fast_sma = SimpleMovingAverage(period=config.fast_period, price_type=PriceType.LAST)
        self.slow_sma = SimpleMovingAverage(period=config.slow_period, price_type=PriceType.LAST)

        # Track previous SMA values for crossover detection
        self._prev_fast_sma: float | None = None
        self._prev_slow_sma: float | None = None

        # Current bar for position sizing calculations
        self._current_bar: Bar | None = None

    def on_start(self) -> None:
        """Register the SMAs and ask for enough history to warm them (Story 4.4).

        ``subscribe_bars`` is deliberately **not** called here: it runs last in
        :meth:`_on_history_loaded`, so the live stream starts only once the
        history has loaded. The seam between the two is guarded outside the
        strategy, by the session's warm-up watch (Story 4.5, D-F): a live bar
        at or before the last history bar is dropped once, and each bar missed
        while the request was in flight is named (``warmup.seam_gap``), not
        replayed — see ``live_session_warmup.py``. Mode-agnostic by
        construction (AR40) — a backtest answers the request
        with nothing, synchronously, so there the callback subscribes before
        this method returns and the strategy behaves exactly as before.
        """
        self.register_indicator_for_bars(self.bar_type, self.fast_sma)
        self.register_indicator_for_bars(self.bar_type, self.slow_sma)
        period = max(self.fast_sma.period, self.slow_sma.period)
        start = self.clock.utc_now() - warmup_lookback(self.bar_type, period)
        self.request_bars(self.bar_type, start=start, callback=self._on_history_loaded)

    def _on_history_loaded(self, request_id: UUID4) -> None:
        """Carry the crossover baseline over from history, then go live.

        History reaches the registered indicators but never ``on_bar``, so
        without this the strategy would be warm and still deaf for one bar
        (and, on daily bars, lose a last-history-to-first-live crossover
        outright). Only the baseline is taken: no signal is ever evaluated on
        a historical bar. Cold indicators (a backtest's empty history) record
        nothing, which leaves the first bars exactly as they were.
        """
        if self.fast_sma.initialized and self.slow_sma.initialized:
            self._prev_fast_sma = self.fast_sma.value
            self._prev_slow_sma = self.slow_sma.value
        self.subscribe_bars(self.bar_type)

    def on_stop(self) -> None:
        """Actions to be performed on strategy stop."""
        self.unsubscribe_bars(self.bar_type)
        _report_unsized(self)

    def on_bar(self, bar: Bar) -> None:
        """
        Handle incoming bar data.

        Parameters
        ----------
        bar : Bar
            The bar to be handled.
        """
        # Store current bar for position sizing
        self._current_bar = bar

        # Both SMAs are registered in `on_start`, so `Actor.handle_bar` has
        # already fed them this bar before calling here — feeding them again
        # would count every bar twice.

        # Wait for both indicators to be initialized
        if not (self.fast_sma.initialized and self.slow_sma.initialized):
            return

        # Get current SMA values
        fast_value = self.fast_sma.value
        slow_value = self.slow_sma.value

        # Check for crossover signals only if we have previous values
        if self._prev_fast_sma is not None and self._prev_slow_sma is not None:
            self._check_for_signals(fast_value, slow_value)

        # Store current values for next iteration
        self._prev_fast_sma = fast_value
        self._prev_slow_sma = slow_value

    def _calculate_position_size(self) -> Quantity | None:
        """
        Calculate position size based on portfolio percentage and current price.

        Returns
        -------
        Quantity | None
            Shares or contracts to trade; ``None`` when the budget does not cover one
            contract of a multiplied instrument (futures), so no order is placed.

        Notes
        -----
        Position size is calculated as:
        units = (portfolio_value * position_size_pct / 100) / (current_price * multiplier)

        The multiplier is 1 for shares, so equities size exactly as before. For futures
        it is the point value (ES 50): $100K at ES 7000 is 0.29 contracts, which floors to
        0 rather than rounding up to one $350K contract.

        Example:
            Portfolio: $1,000,000
            Position %: 10%
            Current Price: $140
            Shares = ($1M * 10% / 100) / $140 = 714 shares (~$100K notional)
        """
        if self._current_bar is None:
            raise ValueError("Cannot calculate position size without current bar data")

        # Calculate position value in USD
        position_value = self.portfolio_value * (self.position_size_pct / Decimal("100"))

        # Get current price (use close price from bar)
        current_price = Decimal(str(self._current_bar.close))

        sizing = (position_value, current_price, self.cache.instrument(self.instrument_id))
        quantity, raw_qty, multiplier = _sized_quantity(*sizing, self.config, self.cache)
        if quantity is None:
            return _unsized(self, position_value)

        notional = contract_notional(Decimal(str(quantity)), current_price, multiplier)
        self.log.info(
            f"Position sizing: Portfolio=${self.portfolio_value:,.0f}, "
            f"Size%={self.position_size_pct}%, "
            f"Price=${current_price:.2f}, "
            f"Qty={quantity} (~${notional:,.0f} notional)"
        )

        return quantity

    def _check_for_signals(self, fast_value: float, slow_value: float) -> None:
        """
        Check for crossover signals and generate orders.

        Parameters
        ----------
        fast_value : float
            Current fast SMA value.
        slow_value : float
            Current slow SMA value.
        """
        # Only check for crossovers if we have previous values
        if self._prev_fast_sma is not None and self._prev_slow_sma is not None:
            # Detect bullish crossover (fast SMA crosses above slow SMA)
            if self._prev_fast_sma <= self._prev_slow_sma and fast_value > slow_value:
                self._generate_buy_signal()

            # Detect bearish crossover (fast SMA crosses below slow SMA)
            elif self._prev_fast_sma >= self._prev_slow_sma and fast_value < slow_value:
                self._generate_sell_signal()

    def _generate_buy_signal(self) -> None:
        """Generate a buy signal."""
        # This strategy's own open positions only (Story 4.5): the cache can
        # also hold positions reconciliation owns (`EXTERNAL`/`INTERNAL-DIFF`)
        # on this instrument, and acting on those trades a holding this
        # strategy never opened (NFR14). Backtests run one strategy per
        # instrument, so there the set is unchanged.
        positions = self.cache.positions_open(instrument_id=self.instrument_id, strategy_id=self.id)

        # Check for open positions
        has_short = any(p.is_short for p in positions)
        has_long = any(p.is_long for p in positions)

        # Close any existing short position first. A signal either closes the
        # opposite side or opens a new position -- never both: closing does
        # not make `has_long` stale (that flag is untouched by a short
        # closing), but submitting a second, independently-sized order right
        # after `close_position()` doubles commission and, against a real
        # broker, produced two fills that left the account net short while
        # this strategy's own bookkeeping reported flat (live-observed
        # 2026-09-11, deferred-work.md "Deferred from: Procedure P10 live
        # run, story-3.4 closeout").
        if has_short:
            for position in positions:
                if position.is_short:
                    self.close_position(position)
                    self.log.info(f"Closed SHORT position: {position.id}")

        # Only open new long position if we don't already have one, and only
        # if we didn't just submit a close above.
        # A futures budget below one contract sizes to None: no order.
        elif not has_long and (quantity := self._calculate_position_size()) is not None:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=quantity,
            )
            self.submit_order(order)

            self.log.info(
                f"Generated BUY signal - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def _generate_sell_signal(self) -> None:
        """Generate a sell signal."""
        # Own positions only -- see `_generate_buy_signal`.
        positions = self.cache.positions_open(instrument_id=self.instrument_id, strategy_id=self.id)

        # Check for open positions
        has_long = any(p.is_long for p in positions)
        has_short = any(p.is_short for p in positions)

        # Close any existing long position first. Mirror of
        # `_generate_buy_signal`'s close/open split -- see its comment.
        if has_long:
            for position in positions:
                if position.is_long:
                    self.close_position(position)
                    self.log.info(f"Closed LONG position: {position.id}")

        # Only open new short position if we don't already have one, and only
        # if we didn't just submit a close above.
        # A futures budget below one contract sizes to None: no order.
        elif not has_short and (quantity := self._calculate_position_size()) is not None:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=quantity,
            )
            self.submit_order(order)

            self.log.info(
                f"Generated SELL signal - Fast SMA: {self.fast_sma.value:.4f}, "
                f"Slow SMA: {self.slow_sma.value:.4f}"
            )

    def on_dispose(self) -> None:
        """Clean up strategy resources."""
        pass  # Nothing additional to clean up


# Register config and parameter model for this strategy
StrategyRegistry.set_config("sma_crossover", SMAConfig)
StrategyRegistry.set_param_model("sma_crossover", SMAParameters)
StrategyRegistry.set_default_config(
    "sma_crossover",
    {
        "instrument_id": "AAPL.NASDAQ",
        "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
        "fast_period": 10,
        "slow_period": 20,
        "portfolio_value": 1000000,
        "position_size_pct": 10.0,
    },
)
