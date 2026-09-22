"""Tests for SMA crossover strategy."""

import gc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.config import LoggingConfig
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs

from src.core.fee_models import IBKRCommissionModel
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.services.firstrate.backtest_loader import build_equity

AUDUSD_SIM = TestInstrumentProvider.default_fx_ccy("AUD/USD")
#: `TestDataStubs.bar_5decimal()`'s own bar type.
STUB_BAR_TYPE = BarType.from_str("AUD/USD.SIM-1-MINUTE-BID-EXTERNAL")


def test_sma_config_creation():
    """Test SMA strategy configuration creation."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=10,
        slow_period=20,
        portfolio_value=Decimal("1_000_000"),
        position_size_pct=Decimal("10.0"),
    )

    assert config.instrument_id == instrument.id
    assert config.fast_period == 10
    assert config.slow_period == 20
    assert config.portfolio_value == Decimal("1_000_000")
    assert config.position_size_pct == Decimal("10.0")


@pytest.mark.integration
def test_sma_strategy_initialization():
    """Test SMA strategy initialization."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=10,
        slow_period=20,
        portfolio_value=Decimal("1_000_000"),
        position_size_pct=Decimal("10.0"),
    )

    strategy = SMACrossover(config=config)

    assert strategy.instrument_id == instrument.id
    assert strategy.fast_sma.period == 10
    assert strategy.slow_sma.period == 20
    assert not strategy.fast_sma.initialized
    assert not strategy.slow_sma.initialized


def _started(strategy: SMACrossover) -> SMACrossover:
    """Register ``strategy`` on a real bus behind a real, synchronous data engine.

    Story 4.4: both SMAs are registered indicators now — ``Actor.handle_bar``
    feeds them before calling ``on_bar`` — and ``on_start`` requests history,
    which needs a registered strategy. The (non-live) ``DataEngine`` answers
    that request with an empty history inside ``start()``, exactly as a
    backtest does, so the history callback has subscribed by the time this
    returns.
    """
    clock = TestClock()
    trader_id = TraderId("TESTER-000")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache(database=None)
    cache.add_instrument(AUDUSD_SIM)
    engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock)
    engine.start()
    strategy.register(trader_id, Portfolio(msgbus, cache, clock), msgbus, cache, clock)
    strategy.start()
    return strategy


def _stub_bar_config(**periods) -> SMAConfig:
    """A config on the stub bar's own type: registered indicators only see
    bars of the type they were registered for."""
    return SMAConfig(instrument_id=AUDUSD_SIM.id, bar_type=STUB_BAR_TYPE, **periods)


@pytest.mark.integration
def test_strategy_on_start_subscribes_to_bars_once_history_has_loaded():
    """``subscribe_bars`` runs from the history callback, not ``on_start``."""
    strategy = SMACrossover(config=_stub_bar_config())

    with (
        patch.object(strategy, "subscribe_bars") as mock_subscribe,
        patch.object(strategy, "request_bars", wraps=strategy.request_bars) as mock_request,
    ):
        _started(strategy)

        mock_request.assert_called_once()
        assert mock_request.call_args.kwargs["callback"] == strategy._on_history_loaded
        mock_subscribe.assert_called_once_with(STUB_BAR_TYPE)


@pytest.mark.integration
def test_strategy_on_stop_only_unsubscribes():
    """Stopping the strategy must leave positions alone (Story 3.1, AC #1/#4).

    Replaces the pre-3.1 assertion that ``on_stop()`` flattens positions:
    that behaviour manufactured a fake round trip on every stop and is
    exactly what this story removes. ``unsubscribe_bars`` is the only
    strategy-owned teardown action that should remain.
    """
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    with (
        patch.object(strategy, "close_all_positions") as mock_close,
        patch.object(strategy, "submit_order") as mock_submit,
        patch.object(strategy, "unsubscribe_bars") as mock_unsubscribe,
    ):
        strategy.on_stop()

        mock_unsubscribe.assert_called_once()
        mock_close.assert_not_called()
        mock_submit.assert_not_called()


@pytest.mark.integration
def test_on_bar_updates_indicators_when_not_initialized():
    """A bar reaches the indicators but no signal is checked while they are cold."""
    strategy = _started(SMACrossover(config=_stub_bar_config(fast_period=2, slow_period=3)))

    # Mock _check_for_signals to ensure it's not called
    with patch.object(strategy, "_check_for_signals") as mock_check:
        strategy.handle_bar(TestDataStubs.bar_5decimal())

        # Should not check for signals when indicators aren't initialized
        mock_check.assert_not_called()

        # Indicators received the bar exactly once (Story 4.4: fed by
        # `handle_bar` as registered indicators, no longer by `on_bar` too)
        assert strategy.fast_sma.count == 1
        assert strategy.slow_sma.count == 1


@pytest.mark.integration
def test_on_bar_checks_signals_when_indicators_initialized():
    """Test that on_bar checks for signals when indicators are initialized."""
    strategy = _started(SMACrossover(config=_stub_bar_config(fast_period=2, slow_period=2)))

    # Feed bars to initialize indicators
    strategy.handle_bar(TestDataStubs.bar_5decimal())
    strategy.handle_bar(TestDataStubs.bar_5decimal())  # Both initialized now

    # Mock _check_for_signals
    with patch.object(strategy, "_check_for_signals") as mock_check:
        strategy.handle_bar(TestDataStubs.bar_5decimal())

        # Should check for signals when both indicators are initialized and we have previous values
        mock_check.assert_called_once()


@pytest.mark.integration
def test_check_for_signals_bullish_crossover():
    """Test detection of bullish crossover (fast SMA crosses above slow SMA)."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up crossover scenario: fast was below slow, now fast is above slow
    strategy._prev_fast_sma = 1.0950  # Previously below slow
    strategy._prev_slow_sma = 1.0960

    with (
        patch.object(strategy, "_generate_buy_signal") as mock_buy,
        patch.object(strategy, "_generate_sell_signal") as mock_sell,
    ):
        # Current: fast above slow (crossover)
        strategy._check_for_signals(1.0965, 1.0958)

        mock_buy.assert_called_once()
        mock_sell.assert_not_called()


@pytest.mark.integration
def test_check_for_signals_bearish_crossover():
    """Test detection of bearish crossover (fast SMA crosses below slow SMA)."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up crossover scenario: fast was above slow, now fast is below slow
    strategy._prev_fast_sma = 1.0970  # Previously above slow
    strategy._prev_slow_sma = 1.0960

    with (
        patch.object(strategy, "_generate_buy_signal") as mock_buy,
        patch.object(strategy, "_generate_sell_signal") as mock_sell,
    ):
        # Current: fast below slow (crossover)
        strategy._check_for_signals(1.0955, 1.0962)

        mock_sell.assert_called_once()
        mock_buy.assert_not_called()


@pytest.mark.integration
def test_check_for_signals_no_crossover():
    """Test no signals when there's no crossover."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up no crossover scenario: fast remains above slow
    strategy._prev_fast_sma = 1.0970
    strategy._prev_slow_sma = 1.0960

    with (
        patch.object(strategy, "_generate_buy_signal") as mock_buy,
        patch.object(strategy, "_generate_sell_signal") as mock_sell,
    ):
        # Current: fast still above slow (no crossover)
        strategy._check_for_signals(1.0975, 1.0965)

        mock_buy.assert_not_called()
        mock_sell.assert_not_called()


# Note: Signal generation tests are complex to unit test in isolation
# as they require Nautilus Trader infrastructure (order_factory, cache).
# These are better covered by integration tests.


@pytest.mark.integration
def test_on_dispose_does_nothing():
    """Test that on_dispose completes without error."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Should not raise any exception
    strategy.on_dispose()


_NAUTILUS_ID = "AAPL.NASDAQ"
_BAR_TYPE_STR = f"{_NAUTILUS_ID}-1-DAY-LAST-EXTERNAL"


class FlattenOnStop(SMACrossover):
    """The permanent, reproducible "before" variant of ``on_stop`` (Story 3.1,
    AC #2) — restores the two-line body ``SMACrossover.on_stop`` carried
    before this story, so the equivalence proof below does not depend on
    git history.
    """

    def on_stop(self) -> None:
        self.close_all_positions(self.instrument_id)
        self.unsubscribe_bars(self.bar_type)


#: A fixed, recent base date for the synthetic series (review fix,
#: 2026-08-29). AC #2 asks for a backtest "over a fixed period"; the first
#: implementation used ``datetime.now()``, so the period moved on every run and
#: the docstring's claim of determinism was false. Recent because epoch-zero
#: timestamps crash the engine here (see below); fixed because a comparison
#: harness whose window drifts cannot be compared against its own recorded
#: numbers by a later story.
_SERIES_START = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


def _make_daily_bars(closes: list[float]) -> list[Bar]:
    """Deterministic daily bars with REALISTIC (non-epoch-zero) timestamps.

    Epoch-zero ``ts_event``/``ts_init`` (i.e. dates in 1970) reproducibly
    crashes ``BacktestEngine.run()`` for an ``Equity`` instrument under
    pytest ``--forked`` in this environment (measured, Story 3.1 Task 1.2
    Debug Log) — an unrelated, pre-existing Nautilus/pytest-fork
    incompatibility, not a defect in this story's change. Real, recent
    timestamps avoid it.

    Genuinely deterministic since the 2026-08-29 review: anchored to the fixed
    :data:`_SERIES_START` rather than ``datetime.now()``, so two runs a day
    apart produce byte-identical bars and a DST boundary cannot shift the
    series under a comparison.
    """
    bar_type = BarType.from_str(_BAR_TYPE_STR)
    start_time = _SERIES_START
    bars = []
    for i, close in enumerate(closes):
        price = Price(close, precision=2)
        ts = int((start_time + timedelta(days=i)).timestamp() * 1_000_000_000)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=price,
                high=Price(close + 1.0, precision=2),
                low=Price(close - 1.0, precision=2),
                close=price,
                volume=Quantity(1_000_000, precision=0),
                ts_event=ts,
                ts_init=ts,
            )
        )
    return bars


def _run_backtest(strategy_cls: type[SMACrossover], bars: list[Bar]):
    """Run ``strategy_cls`` over ``bars`` in a fresh, single-use ``BacktestEngine``.

    Takes pre-built bars (rather than building them internally) so a caller
    comparing two variants over "the same data" uses the literal same ``Bar``
    objects — building bars twice via ``_make_daily_bars`` would give each
    call a different ``datetime.now()``-based timestamp.

    Returns ``(orders, closed_positions)`` read from ``engine.cache`` before
    disposal. Venue/account/fee setup matches the project's own production
    equity path (HEDGING/MARGIN, ``build_equity``'s ``lot_size=1``) rather
    than ``conftest.py``'s crypto-flavoured ``setup_backtest_venue``
    (NETTING/CASH), which is unstable with ``Equity`` instruments in the
    installed Nautilus version independently of the timestamp issue above
    (measured, Task 1.2 Debug Log).
    """
    instrument = build_equity(nautilus_id=_NAUTILUS_ID, ticker="AAPL", bars=bars)

    # `bypass_logging=True` matches the sibling harness in
    # `tests/integration/core/test_live_strategy_failure_survives.py` and is
    # required here, not cosmetic (review fix, 2026-08-29): each equivalence
    # test builds TWO engines in one process, and a default-logging engine has
    # `NautilusKernel.__init__` call `init_logging()` and hold the returned
    # `LogGuard`. Disposing the first engine drops that guard, and initializing
    # C logging a second time is CLAUDE.md Gotcha #1 — the panic this repo
    # keeps a module-level guard store for.
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=Venue("NASDAQ"),
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(1_000_000, USD)],
        fill_model=FillModel(),
        fee_model=IBKRCommissionModel(),
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    strategy = strategy_cls(
        config=SMAConfig(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(_BAR_TYPE_STR),
            fast_period=2,
            slow_period=3,
            portfolio_value=Decimal("1000000"),
            position_size_pct=Decimal("10.0"),
        )
    )
    engine.add_strategy(strategy)
    engine.run()

    orders = list(engine.cache.orders())
    closed_positions = list(engine.cache.positions_closed())

    engine.dispose()
    gc.collect()
    gc.collect()

    return orders, closed_positions


@pytest.mark.integration
class TestOnStopEquivalenceAcrossVariants:
    """Story 3.1, AC #2 — the edit behaves identically in both engines; the
    difference is confined to how a position still open at the end of the
    data is recorded (AR40).

    Non-vacuity is built in, not asserted separately: today (before Story
    3.1's edit), ``SMACrossover.on_stop`` still flattens, so it behaves
    identically to ``FlattenOnStop`` and
    ``test_open_at_end_flatten_submits_exactly_one_extra_order`` fails RED —
    both variants submit the SAME number of orders, not one extra. It turns
    GREEN only once AC #1 lands and the two variants actually diverge.
    """

    #: Flat 100 x4, then a clean bullish crossover (130, then 140 with no
    #: further crossover) — the position is open when the data ends.
    OPEN_AT_END_CLOSES = [100.0, 100.0, 100.0, 100.0, 130.0, 140.0]

    #: No crossover ever fires — the strategy is flat throughout.
    FLAT_AT_END_CLOSES = [100.0, 100.0, 100.0, 100.0]

    def test_open_at_end_flatten_submits_exactly_one_extra_order(self):
        bars = _make_daily_bars(self.OPEN_AT_END_CLOSES)
        clean_orders, clean_closed = _run_backtest(SMACrossover, bars)
        flatten_orders, flatten_closed = _run_backtest(FlattenOnStop, bars)

        # The load-bearing observable is the ORDER record: close_all_positions
        # submits exactly one extra MarketOrder per open position, whether or
        # not it fills at end-of-data (trading/strategy.pyx:1305-1362).
        assert len(flatten_orders) == len(clean_orders) + 1, (
            f"clean={len(clean_orders)} flatten={len(flatten_orders)}"
        )

        # Every order the two variants share is identical. Compared field by
        # field rather than by side/quantity alone (review fix, 2026-08-29):
        # AC #2 says "no other metric changes", and the first version compared
        # two of an order's attributes, so a variant that filled at a different
        # price or paid different commission would have passed.
        assert len(clean_orders) == 1, (
            f"the scenario no longer produces the single expected entry order: {len(clean_orders)}"
        )
        for clean_order, flatten_order in zip(clean_orders, flatten_orders, strict=False):
            assert clean_order.side == flatten_order.side
            assert clean_order.quantity == flatten_order.quantity
            assert clean_order.status == flatten_order.status
            assert clean_order.avg_px == flatten_order.avg_px

        # Measured (Task 1.2 Debug Log): the end-of-data flatten DOES fill in
        # this harness, so the flatten variant closes one more trade than the
        # clean variant, and its exit is the final bar.
        #
        # The clean variant closes NOTHING here — the position is open when the
        # data ends, which is the whole point of the scenario. Asserted
        # explicitly (review fix, 2026-08-29) because the original
        # `zip(clean_closed, flatten_closed)` loop below ran **zero
        # iterations** and proved nothing; measured during review as
        # `clean_closed=0, flatten_closed=1`. Stating the emptiness is the
        # honest form of "all earlier trades are identical" for this scenario:
        # there are none, and if that ever changes the assertion fails loudly
        # instead of the comparison silently evaporating.
        assert clean_closed == [], (
            "the clean variant closed a trade — the open-at-end scenario no longer holds a "
            f"position through the final bar: {clean_closed}"
        )
        assert len(flatten_closed) == len(clean_closed) + 1, (
            f"clean={len(clean_closed)} flatten={len(flatten_closed)}"
        )
        extra_trade = flatten_closed[-1]
        assert extra_trade.ts_closed == bars[-1].ts_event

    def test_flat_at_end_variants_are_identical(self):
        bars = _make_daily_bars(self.FLAT_AT_END_CLOSES)
        clean_orders, clean_closed = _run_backtest(SMACrossover, bars)
        flatten_orders, flatten_closed = _run_backtest(FlattenOnStop, bars)

        # close_all_positions submits nothing when flat -- it logs and returns.
        assert len(clean_orders) == 0
        assert len(flatten_orders) == 0
        assert len(clean_closed) == 0
        assert len(flatten_closed) == 0


@pytest.mark.integration
class TestReversalSubmitsExactlyOneOrder:
    """A reversal signal must submit ONE order, not two.

    Found live 2026-09-11 during Story 3.4's Procedure P10 (deferred-work.md,
    "Deferred from: Procedure P10 live run, story-3.4 closeout"): a real SELL
    crossover against a real broker submitted TWO fills — one from
    ``close_position()``, a second from the "open new short" branch right
    after it — because ``_generate_buy_signal``/``_generate_sell_signal``
    compute ``has_long``/``has_short`` once from ``self.cache.positions(...)``
    and never re-check it after ``close_position()`` runs. The broker ended up
    genuinely short while the strategy's own ``PositionClosed`` event reported
    ``side=FLAT``.

    ``REVERSAL_CLOSES`` is the minimal series that exercises this with
    ``fast_period=2, slow_period=3`` (the same tuning used live): flat for 3
    bars to initialize both SMAs, then up to fire a bullish crossover (BUY),
    then down to fire a bearish crossover (SELL) while the BUY's position is
    still open — the exact reversal shape. Bar-by-bar SMA arithmetic that
    produces the two crossovers is recorded next to the list itself.
    """

    #: bar0-2: flat 100/100/100 -- both SMAs initialize, no signal (no
    #: previous values yet). bar3: 130 -- fast(2)=115, slow(3)=110, prev
    #: (100, 100) had fast<=slow -- BULLISH crossover, BUY. bar4: 140 -- no
    #: crossover (stays bullish). bar5: 100 -- fast(2)=120, slow(3)=123.33,
    #: prev (135, 123.33) had fast>=slow -- BEARISH crossover, SELL, while
    #: the BUY's LONG position from bar3 is still open.
    REVERSAL_CLOSES = [100.0, 100.0, 100.0, 130.0, 140.0, 100.0]

    def test_reversal_submits_exactly_one_order(self):
        bars = _make_daily_bars(self.REVERSAL_CLOSES)
        orders, _ = _run_backtest(SMACrossover, bars)

        buys = [o for o in orders if o.side.name == "BUY"]
        sells = [o for o in orders if o.side.name == "SELL"]
        assert len(buys) == 1, f"expected exactly one BUY (the entry), got {len(buys)}: {buys}"
        assert len(sells) == 1, (
            f"expected exactly one SELL (the reversal's close), got {len(sells)}: {sells} -- "
            "a second SELL here is the live-observed bug: has_long/has_short read once, "
            "before close_position(), then trusted again to decide whether to also open a "
            "new position"
        )
        assert len(orders) == 2, f"expected exactly 2 orders total (one entry, one exit): {orders}"

    #: Mirror of REVERSAL_CLOSES for the BUY side: bar0-2 flat at 100 to
    #: initialize both SMAs. bar3: 70 -- fast(2)=85, slow(3)=90, prev (100,
    #: 100) had fast>=slow -- BEARISH crossover, SELL (opens a short from
    #: flat, one order). bar4: 60 -- no crossover (stays bearish). bar5: 100
    #: -- fast(2)=80, slow(3)=76.67, prev (65, 76.67) had fast<slow --
    #: BULLISH crossover, BUY, while the SELL's SHORT position from bar3 is
    #: still open -- the mirror-image reversal.
    REVERSAL_CLOSES_BUY_SIDE = [100.0, 100.0, 100.0, 70.0, 60.0, 100.0]

    def test_reversal_submits_exactly_one_order_buy_side(self):
        bars = _make_daily_bars(self.REVERSAL_CLOSES_BUY_SIDE)
        orders, _ = _run_backtest(SMACrossover, bars)

        buys = [o for o in orders if o.side.name == "BUY"]
        sells = [o for o in orders if o.side.name == "SELL"]
        assert len(sells) == 1, f"expected exactly one SELL (the entry), got {len(sells)}: {sells}"
        assert len(buys) == 1, (
            f"expected exactly one BUY (the reversal's close), got {len(buys)}: {buys} -- "
            "the mirror of the SELL-side bug: has_long checked once, before close_position(), "
            "then trusted again to decide whether to also open a new long"
        )
        assert len(orders) == 2, f"expected exactly 2 orders total (one entry, one exit): {orders}"
