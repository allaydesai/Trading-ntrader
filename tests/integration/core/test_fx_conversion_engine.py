"""FX M3 (task 3.3): USD conversion through the production venue wiring, in a real engine.

``setup_backtest_venues`` (``src/core/backtest_venue.py``) gives an FX venue a USD
single-currency account, adds the run's bars (rate sources first) and registers the
``FxRateFeeder`` before any strategy; the orchestrator, extractor and persistence then report
in USD. Pinned here:

- USDJPY and EURGBP round trips move the USD balance by exactly the hand-computed amount,
  with mark-to-market equity (not the realised fallback) and USD trade statistics.
- A fill the cache had no rate for fails the run; a cross without its rate source fails
  before the engine starts. The equity venue's ``add_venue`` call is unchanged.
- When a fill converts. A fill converts at the latest rate-source close the feeder has
  handled when the fill happens:
  * a market order submitted from ``on_bar`` (default fill model) fills at bar N's close and
    converts at bar N's rate (the feeder handled bar N before the strategy);
  * an order the exchange matches while processing bar N (a resting limit or stop, and every
    order released by gap-aware next-bar execution) converts at bar N-1's rate for USDJPY,
    because the exchange sees a bar before any actor does;
  * for a cross the rate source (GBPUSD) is added before the traded pair, so its bar N is
    handled before the exchange matches EURGBP's bar N: a resting EURGBP order converts at
    bar N's GBPUSD rate, while gap-aware releases (which happen at the start of the first
    data point of bar N, the GBPUSD bar) still convert at bar N-1's.

Run with ``--forked`` (C extensions).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import GBP, JPY, USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

from src.core.backtest_orchestrator import BacktestOrchestrator
from src.core.backtest_venue import setup_backtest_venues
from src.core.fee_models import IBKRCommissionModel
from src.core.fill_model import make_fill_model
from src.core.fill_models import MOO_EXIT_TAG, bind_fill_model
from src.core.fx_rates import FxConversionPairError, FxRateFeeder
from src.core.fx_results import FxRateMissingError, exit_rates
from src.core.results_extractor import ResultsExtractor, mark_to_market
from src.models.backtest_request import BacktestRequest
from src.services.backtest_persistence import usd_profit_loss
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.integration

IDEALPRO = Venue("IDEALPRO")
START = Decimal("100000")
UNITS = 100_000
BAR_VOLUME = Quantity.from_int(100_000_000)
HOUR_NS = 3_600_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


def _bars(instrument, ohlc: list) -> list[Bar]:
    """One 1-hour MID bar per entry: a close string, or an ``(open, high, low, close)`` tuple."""
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-MID-EXTERNAL")
    bars = []
    for i, values in enumerate(ohlc):
        o, h, low, c = (values,) * 4 if isinstance(values, str) else values
        px = [instrument.make_price(Decimal(v)) for v in (o, h, low, c)]
        ts = T0 + i * HOUR_NS
        bars.append(Bar(bar_type, *px, BAR_VOLUME, ts, ts))
    return bars


class _TradeConfig(StrategyConfig, frozen=True):
    bar_type: str
    exit_bar: int = 3
    exit_kind: str = "market"  # market | limit | moo
    limit_px: str | None = None


class _Trade(Strategy):
    """Buy 100k on bar 1; exit on ``exit_bar`` (market, MOO-tagged market) or rest a limit."""

    def __init__(self, config: _TradeConfig) -> None:
        super().__init__(config)
        self._bar = 0
        self.quotes_seen = 0

    def on_start(self) -> None:
        bar_type = BarType.from_str(self.config.bar_type)
        self.subscribe_bars(bar_type)
        self.subscribe_quote_ticks(bar_type.instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quotes_seen += 1

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        iid = bar.bar_type.instrument_id
        qty = Quantity.from_int(UNITS)
        if self._bar == 1:
            self.submit_order(self.order_factory.market(iid, OrderSide.BUY, qty))
            return
        open_ids = [p.id for p in self.cache.positions_open(instrument_id=iid)]
        if not open_ids:
            return
        if self.config.exit_kind == "limit" and self._bar == 2:  # HEDGING: close that position
            price = self.cache.instrument(iid).make_price(Decimal(self.config.limit_px))
            order = self.order_factory.limit(iid, OrderSide.SELL, qty, price)
            self.submit_order(order, position_id=open_ids[0])
        elif self._bar == self.config.exit_bar and self.config.exit_kind != "limit":
            tags = [MOO_EXIT_TAG] if self.config.exit_kind == "moo" else None
            order = self.order_factory.market(iid, OrderSide.SELL, qty, tags=tags)
            self.submit_order(order, position_id=open_ids[0])


def _engine() -> BacktestEngine:
    return BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))


def _run(traded, data, rate_sources=(), fill_model=None, **trade) -> tuple[BacktestEngine, _Trade]:
    """Set the venue up as production does, trade ``traded``, run."""
    engine = _engine()
    fill_model = fill_model or FillModel()
    bind_fill_model(fill_model, engine, data[[i for _, i in data].index(traded)][0])
    setup_backtest_venues(
        engine,
        {IDEALPRO: [i for _, i in data]},
        starting_balance=START,
        fill_model=fill_model,
        equity_fee_model=IBKRCommissionModel(),
        data=data,
        rate_sources=rate_sources,
    )
    strategy = _Trade(_TradeConfig(bar_type=f"{traded.id}-1-HOUR-MID-EXTERNAL", **trade))
    engine.add_strategy(strategy)
    engine.run()
    return engine, strategy


def _usd_change(engine: BacktestEngine) -> Decimal:
    account = engine.cache.account_for_venue(IDEALPRO)
    assert account.base_currency == USD
    assert set(account.balances_total()) == {USD}
    return account.balance_total(USD).as_decimal() - START


def _closed(engine: BacktestEngine):
    (position,) = engine.cache.positions_closed()
    return position


USDJPY = _pair("USDJPY")
EURGBP = _pair("EURGBP")
GBPUSD = _pair("GBPUSD")
JPY_BARS = ["150.000", "150.500", "151.000", "151.000"]
GBPUSD_BARS = ["1.20000", "1.22000", "1.25000", "1.30000"]  # moves every bar
EURGBP_BARS = ["0.85000", "0.85000", "0.86000", "0.86000"]


# -- the account and the venue call --------------------------------------------------------------


def test_only_fx_venues_get_a_usd_base_currency_and_the_equity_call_is_unchanged():
    engine = _engine()
    aapl = TestInstrumentProvider.equity("AAPL", "XNAS")
    fee = IBKRCommissionModel()
    fill = FillModel()
    calls: list[dict] = []

    class _Spy:  # the Cython engine's methods cannot be patched; record add_venue calls
        def __getattr__(self, name):
            return getattr(engine, name)

        def add_venue(self, **kwargs):
            calls.append(kwargs)
            return engine.add_venue(**kwargs)

    setup_backtest_venues(
        _Spy(),  # type: ignore[arg-type]
        {aapl.id.venue: [aapl], IDEALPRO: [USDJPY]},
        starting_balance=START,
        fill_model=fill,
        equity_fee_model=fee,
        data=[(_bars(aapl, ["190.00"]), aapl), (_bars(USDJPY, JPY_BARS), USDJPY)],
    )
    equity_call, fx_call = calls
    assert set(equity_call) == {
        "venue",
        "oms_type",
        "account_type",
        "starting_balances",
        "fill_model",
        "fee_model",
    }  # exactly the pre-FX call: no base_currency
    assert equity_call["fee_model"] is fee
    assert fx_call["base_currency"] == USD
    assert fx_call["fee_model"] is not fee  # FX costs are M4's; the per-share model is wrong
    engine.dispose()


def test_a_venue_mixing_fx_and_equities_is_refused():
    engine = _engine()
    aapl = TestInstrumentProvider.equity("AAPL", "IDEALPRO")
    with pytest.raises(ValueError, match="spot FX pairs together"):
        setup_backtest_venues(
            engine,
            {IDEALPRO: [aapl, USDJPY]},
            starting_balance=START,
            fill_model=FillModel(),
            equity_fee_model=IBKRCommissionModel(),
            data=[],
        )
    engine.dispose()


# -- exact USD results ---------------------------------------------------------------------------


def test_usdjpy_round_trip_converts_at_the_fill_bars_rate():
    engine, strategy = _run(USDJPY, [(_bars(USDJPY, JPY_BARS), USDJPY)])
    try:
        position = _closed(engine)
        assert position.realized_pnl == Money(100_000, JPY)  # 100k x (151.000 - 150.000)
        assert _usd_change(engine) == Decimal("662.25")  # 100,000 JPY / 151.000
        assert strategy.quotes_seen == 0  # the feeder's quotes stay in the cache
        assert [type(a) for a in engine.trader.actors()] == [FxRateFeeder]
    finally:
        engine.dispose()


def test_eurgbp_round_trip_converts_through_gbpusd_at_the_fill_bars_rate():
    engine, _ = _run(
        EURGBP,
        [(_bars(EURGBP, EURGBP_BARS), EURGBP)],
        rate_sources=[(_bars(GBPUSD, GBPUSD_BARS), GBPUSD)],
    )
    try:
        assert _closed(engine).realized_pnl == Money(1_000, GBP)
        assert _usd_change(engine) == Decimal("1250.00")  # 1,000 GBP x 1.25000, not 1.22000
        assert engine.cache.positions(instrument_id=GBPUSD.id) == []  # conversion-only
    finally:
        engine.dispose()


def test_a_traded_rate_source_listed_after_the_cross_is_still_added_first():
    # Both pairs traded, cross listed first: add_fx_data reorders, so no one-bar lag (1,220).
    engine, _ = _run(
        EURGBP,
        [(_bars(EURGBP, EURGBP_BARS), EURGBP), (_bars(GBPUSD, GBPUSD_BARS), GBPUSD)],
    )
    try:
        assert _usd_change(engine) == Decimal("1250.00")
    finally:
        engine.dispose()


# -- failures ------------------------------------------------------------------------------------


def test_a_cross_without_its_rate_source_fails_before_the_engine_starts():
    with pytest.raises(FxConversionPairError, match="GBPUSD"):
        _run(EURGBP, [(_bars(EURGBP, EURGBP_BARS), EURGBP)])


def test_fx_pairs_needing_conversion_without_data_are_refused():
    engine = _engine()
    with pytest.raises(ValueError, match="rate feeder"):
        setup_backtest_venues(
            engine,
            {IDEALPRO: [USDJPY]},
            starting_balance=START,
            fill_model=FillModel(),
            equity_fee_model=IBKRCommissionModel(),
        )
    engine.dispose()


def test_a_fill_without_a_rate_fails_the_run():
    # GBPUSD bars start an hour late, so the bar-1 EURGBP entry has no GBP/USD rate: Nautilus
    # leaves the USD balance unchanged for it, and the extractor must refuse the run.
    late_gbpusd = _bars(GBPUSD, GBPUSD_BARS)[1:]
    engine, _ = _run(
        EURGBP, [(_bars(EURGBP, EURGBP_BARS), EURGBP)], rate_sources=[(late_gbpusd, GBPUSD)]
    )
    try:
        extractor = ResultsExtractor(engine, IDEALPRO, starting_balance=float(START))
        with pytest.raises(FxRateMissingError, match="GBP/USD"):
            extractor.extract_results()
        with pytest.raises(FxRateMissingError):
            exit_rates(engine)
    finally:
        engine.dispose()


# -- when a fill converts (the lag rules, see the module docstring) ------------------------------


def test_a_resting_usdjpy_limit_converts_at_the_previous_bars_rate():
    # The limit at 151.000 fills while the exchange matches bar 3; the feeder still holds
    # bar 2's 150.500, so 100,000 JPY converts at 1/150.5 (664.45), not 1/151 (662.25).
    engine, _ = _run(
        USDJPY, [(_bars(USDJPY, JPY_BARS), USDJPY)], exit_kind="limit", limit_px="151.000"
    )
    try:
        assert _closed(engine).realized_pnl == Money(100_000, JPY)
        assert _usd_change(engine) == Decimal("664.45")
    finally:
        engine.dispose()


def test_a_resting_eurgbp_limit_converts_at_the_fill_bars_gbpusd_rate():
    # GBPUSD's bar 3 is handled before the exchange matches EURGBP's bar 3: 1.25, no lag.
    engine, _ = _run(
        EURGBP,
        [(_bars(EURGBP, EURGBP_BARS), EURGBP)],
        rate_sources=[(_bars(GBPUSD, GBPUSD_BARS), GBPUSD)],
        exit_kind="limit",
        limit_px="0.86000",
    )
    try:
        assert _closed(engine).realized_pnl == Money(1_000, GBP)
        assert _usd_change(engine) == Decimal("1250.00")
    finally:
        engine.dispose()


GAP_BARS = [
    "150.000",
    "150.500",
    ("151.000", "152.000", "151.000", "152.000"),  # opens 151.000, closes 152.000
    "152.000",
]


def test_a_gap_aware_moo_exit_converts_at_the_previous_bars_rate():
    # The MOO exit signalled on bar 2 is released at the start of bar 3 and fills at its
    # open (151.000), before the feeder sees bar 3: 100,000 JPY at 1/150.5 = 664.45.
    engine, _ = _run(
        USDJPY,
        [(_bars(USDJPY, GAP_BARS), USDJPY)],
        fill_model=make_fill_model(1, kind="gap_aware"),
        exit_bar=2,
        exit_kind="moo",
    )
    try:
        position = _closed(engine)
        assert position.avg_px_close == pytest.approx(151.0)
        assert position.realized_pnl == Money(100_000, JPY)
        assert _usd_change(engine) == Decimal("664.45")
    finally:
        engine.dispose()


def test_a_gap_aware_cross_release_converts_at_the_previous_bars_rate():
    # Released at the start of the GBPUSD bar 3 (the first data point at that time), before
    # the feeder handles it: GBPUSD bar 2's 1.22, unlike the resting limit above.
    eurgbp = [
        "0.85000",
        "0.85000",
        ("0.86000", "0.86000", "0.86000", "0.86000"),
        "0.86000",
    ]
    engine, _ = _run(
        EURGBP,
        [(_bars(EURGBP, eurgbp), EURGBP)],
        rate_sources=[(_bars(GBPUSD, GBPUSD_BARS), GBPUSD)],
        fill_model=make_fill_model(1, kind="gap_aware"),
        exit_bar=2,
        exit_kind="moo",
    )
    try:
        assert _closed(engine).realized_pnl == Money(1_000, GBP)
        assert _usd_change(engine) == Decimal("1220.00")
    finally:
        engine.dispose()


# -- the orchestrator end to end -----------------------------------------------------------------


def _request(symbol: str) -> BacktestRequest:
    return BacktestRequest(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        symbol=symbol,
        instrument_id=f"{symbol}.IDEALPRO",
        start_date=pd.Timestamp(T0, tz="UTC").to_pydatetime(),
        end_date=pd.Timestamp(T0 + 3 * HOUR_NS, tz="UTC").to_pydatetime(),
        bar_type="1-HOUR-MID",
        starting_balance=START,
        persist=False,
    )


async def _orchestrate(instrument, bars, rate_sources=()):
    orchestrator = BacktestOrchestrator()
    strategy = _Trade(_TradeConfig(bar_type=str(bars[0].bar_type)))
    with patch.object(orchestrator, "_create_strategy", return_value=strategy):
        with patch("src.core.backtest_orchestrator.make_fill_model", return_value=FillModel()):
            result, _ = await orchestrator.execute(
                _request(str(instrument.id.symbol)), bars, instrument, rate_sources=rate_sources
            )
    return orchestrator, result


async def test_the_orchestrator_reports_a_usdjpy_run_in_usd_marked_to_market():
    orchestrator, result = await _orchestrate(USDJPY, _bars(USDJPY, JPY_BARS))
    try:
        engine = orchestrator.engine
        assert result.final_balance == pytest.approx(100_662.25)
        assert result.largest_win == pytest.approx(100_000 / 151)  # USD, not 100,000 JPY
        assert result.total_pnl == pytest.approx(662.25)
        assert result.winning_trades == 1
        marked = mark_to_market(engine, {USDJPY.id: _bars(USDJPY, JPY_BARS)}, float(START))
        assert marked is not None  # mark-to-market, not the realised fallback
        assert float(marked.equity.iloc[-1]) == pytest.approx(100_662.25, abs=0.01)
        assert result.max_drawdown == marked.risk.max_drawdown
        # the trade row's profit_loss, from the positions report and the exit rates
        report = engine.trader.generate_positions_report()
        rates = exit_rates(engine)
        (position_id,) = report.index
        assert usd_profit_loss(report.loc[position_id, "realized_pnl"], rates[position_id]) == (
            Decimal("662.25")
        )
    finally:
        orchestrator.dispose()


async def test_the_orchestrator_feeds_an_eurgbp_run_from_its_rate_sources():
    orchestrator, result = await _orchestrate(
        EURGBP, _bars(EURGBP, EURGBP_BARS), rate_sources=[(_bars(GBPUSD, GBPUSD_BARS), GBPUSD)]
    )
    try:
        assert result.final_balance == pytest.approx(101_250.00)
        assert result.total_pnl == pytest.approx(1_250.00)
        bars = {EURGBP.id: _bars(EURGBP, EURGBP_BARS)}
        assert mark_to_market(orchestrator.engine, bars, float(START)) is not None
    finally:
        orchestrator.dispose()


async def test_the_orchestrator_refuses_a_cross_without_rate_sources():
    with pytest.raises(FxConversionPairError, match="GBPUSD"):
        await _orchestrate(EURGBP, _bars(EURGBP, EURGBP_BARS))
