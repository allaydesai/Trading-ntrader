"""FX M1 gate (task 1.7): a USD single-currency account converts non-USD P&L.

Before M2-M6 build on it, this proves in a real ``BacktestEngine`` that:

1. A ``MARGIN`` venue with ``base_currency=USD`` accepts the spot ``CurrencyPair``s and their
   ``MID`` bars.
2. Without an exchange rate, a USDJPY round trip leaves the USD balance unchanged and Nautilus
   logs "Cannot calculate account state" (``accounting/manager.pyx``
   ``_update_balance_single_currency`` returns False). Pinned so M3's guard has a target.
3. With a prototype ``FxRateFeeder`` (an actor writing ``QuoteTick(bid=ask=close)`` straight
   into the cache, no bus publish) the balance moves by ``pnl_jpy / USDJPY``, and an EURGBP
   trade converts through GBPUSD added as a conversion-only instrument.

The feeder here is a prototype; M3 ships the real one in ``src/core/fx_rates.py``.
See ``openspec/changes/add-fx-backtesting/design.md`` sections 1 and 3 (decision D4).

Run with ``--forked`` (C extensions).
"""

from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig, BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import GBP, JPY, USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.trading.strategy import Strategy

from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.integration

IDEALPRO = Venue("IDEALPRO")
START_BALANCE = Decimal("100000")
UNITS = 100_000
# Deep bars so a 100k market order fills entirely at the bar price (no spill to the next tick).
BAR_VOLUME = Quantity.from_int(100_000_000)
HOUR_NS = 3_600_000_000_000
T0 = pd.Timestamp("2025-03-06 15:00", tz="UTC").value


class _FeederConfig(ActorConfig, frozen=True):
    bar_types: tuple[str, ...]


class _PrototypeFxRateFeeder(Actor):
    """Write each MID bar's close into the cache as a bid=ask quote and a mark xrate."""

    def on_start(self) -> None:
        for bar_type in self.config.bar_types:
            self.subscribe_bars(BarType.from_str(bar_type))

    def on_bar(self, bar: Bar) -> None:
        instrument = self.cache.instrument(bar.bar_type.instrument_id)
        one = Quantity.from_int(1)
        tick = QuoteTick(instrument.id, bar.close, bar.close, one, one, bar.ts_event, bar.ts_init)
        self.cache.add_quote_tick(tick)  # cache only: strategies and the exchange never see it
        self.cache.set_mark_xrate(
            instrument.base_currency, instrument.quote_currency, float(bar.close)
        )


class _RoundTripConfig(StrategyConfig, frozen=True):
    bar_type: str
    units: int


class _RoundTrip(Strategy):
    """Buy ``units`` on the first bar, close on the third."""

    def __init__(self, config: _RoundTripConfig) -> None:
        super().__init__(config)
        self._bar = 0

    def on_start(self) -> None:
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar: Bar) -> None:
        self._bar += 1
        instrument_id = bar.bar_type.instrument_id
        if self._bar == 1:
            qty = Quantity.from_int(self.config.units)
            self.submit_order(self.order_factory.market(instrument_id, OrderSide.BUY, qty))
        elif self._bar == 3:
            self.close_all_positions(instrument_id)


def _mid_bars(instrument, closes: list[str]) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-MID-EXTERNAL")
    bars = []
    for i, close in enumerate(closes):
        px = instrument.make_price(Decimal(close))
        ts = T0 + i * HOUR_NS
        bars.append(Bar(bar_type, px, px, px, px, BAR_VOLUME, ts, ts))
    return bars


def _pair(name: str):
    return build_fx_pair(get_pair_spec(name))


def _engine() -> BacktestEngine:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=IDEALPRO,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        base_currency=USD,  # single-currency account: every balance change is in USD
        starting_balances=[Money(START_BALANCE, USD)],
        fill_model=FillModel(),
    )
    return engine


def _run(traded: str, series: dict[str, list[str]], *, feed: bool) -> BacktestEngine:
    """Trade ``traded`` on its bars; feed rates from every series when ``feed``."""
    engine = _engine()
    bar_types = []
    for name, closes in series.items():
        instrument = _pair(name)
        engine.add_instrument(instrument)
        bars = _mid_bars(instrument, closes)
        engine.add_data(bars)
        bar_types.append(str(bars[0].bar_type))
    if feed:  # added before the strategy so it sees each bar first (no one-bar rate lag)
        engine.add_actor(_PrototypeFxRateFeeder(_FeederConfig(bar_types=tuple(bar_types))))
    traded_bar_type = f"{_pair(traded).id}-1-HOUR-MID-EXTERNAL"
    engine.add_strategy(_RoundTrip(_RoundTripConfig(bar_type=traded_bar_type, units=UNITS)))
    engine.run()
    return engine


def _usd_balance(engine: BacktestEngine) -> Decimal:
    account = engine.cache.account_for_venue(IDEALPRO)
    assert account.base_currency == USD
    assert set(account.balances_total()) == {USD}  # never a JPY/GBP balance
    return account.balance_total(USD).as_decimal()


def _closed_position(engine: BacktestEngine):
    positions = engine.cache.positions_closed()
    assert len(positions) == 1
    return positions[0]


def test_usd_margin_venue_accepts_the_pairs_and_mid_bars():
    engine = _engine()
    series = {
        "EURUSD": ["1.08431", "1.08440"],
        "GBPUSD": ["1.26512", "1.26520"],
        "USDJPY": ["149.871", "149.880"],
        "EURGBP": ["0.85710", "0.85712"],
    }
    try:
        for name, closes in series.items():
            instrument = _pair(name)
            engine.add_instrument(instrument)
            engine.add_data(_mid_bars(instrument, closes))
        engine.run()
        assert {str(i.id) for i in engine.cache.instruments(venue=IDEALPRO)} == {
            "EUR/USD.IDEALPRO",
            "GBP/USD.IDEALPRO",
            "USD/JPY.IDEALPRO",
            "EUR/GBP.IDEALPRO",
        }
        for name in series:
            bar_type = BarType.from_str(f"{_pair(name).id}-1-HOUR-MID-EXTERNAL")
            assert engine.cache.bar_count(bar_type) == 2
        assert _usd_balance(engine) == START_BALANCE
    finally:
        engine.dispose()


def test_usdjpy_without_a_rate_freezes_the_usd_balance_and_logs(capfd):
    engine = _run("USDJPY", {"USDJPY": ["150.000", "150.000", "151.000", "151.000"]}, feed=False)
    try:
        position = _closed_position(engine)
        assert position.realized_pnl == Money(100_000, JPY)  # the trade itself did happen
        assert engine.cache.get_xrate(IDEALPRO, JPY, USD) is None
        assert _usd_balance(engine) == START_BALANCE  # silently unconverted: M3 must guard
    finally:
        engine.dispose()
    captured = capfd.readouterr()  # Nautilus writes ERROR lines to stderr
    assert "Cannot calculate account state: insufficient data for JPY/USD" in (
        captured.out + captured.err
    )


def test_usdjpy_with_feeder_converts_pnl_at_the_rate():
    engine = _run("USDJPY", {"USDJPY": ["150.000", "150.000", "151.000", "151.000"]}, feed=True)
    try:
        position = _closed_position(engine)
        pnl_jpy = position.realized_pnl
        assert pnl_jpy == Money(100_000, JPY)  # 100,000 USD x (151.000 - 150.000)
        assert engine.cache.get_xrate(IDEALPRO, JPY, USD) == pytest.approx(1 / 151)

        change = _usd_balance(engine) - START_BALANCE
        # 100,000 JPY at the closing fill's rate (USDJPY 151.000), not 100,000 "dollars".
        assert change == Decimal("662.25")
        assert change == (pnl_jpy.as_decimal() / Decimal("151")).quantize(Decimal("0.01"))
    finally:
        engine.dispose()


GBPUSD_CLOSES = ["1.20000", "1.22000", "1.25000", "1.30000"]  # moves every bar
EURGBP_CLOSES = ["0.85000", "0.85000", "0.86000", "0.86000"]  # closes on the third bar


def _eurgbp_usd_change(series: dict[str, list[str]]) -> Decimal:
    engine = _run("EURGBP", series, feed=True)
    try:
        position = _closed_position(engine)
        assert position.realized_pnl == Money(1_000, GBP)  # 100,000 EUR x 0.01000
        assert engine.cache.get_xrate(IDEALPRO, GBP, USD) == pytest.approx(1.30)  # final bar
        assert engine.cache.positions(instrument_id=_pair("GBPUSD").id) == []
        return _usd_balance(engine) - START_BALANCE
    finally:
        engine.dispose()


def test_eurgbp_converts_through_a_conversion_only_gbpusd():
    # GBPUSD (no strategy subscribes) is added before EURGBP, so at each timestamp the feeder
    # has the GBPUSD bar before the strategy acts on EURGBP: the closing fill converts at that
    # bar's rate, 1.25000, not the last cached 1.30000.
    change = _eurgbp_usd_change({"GBPUSD": GBPUSD_CLOSES, "EURGBP": EURGBP_CLOSES})
    assert change == Decimal("1250.00")  # 1,000 GBP x 1.25000


def test_eurgbp_conversion_lags_a_bar_when_the_rate_pair_comes_second():
    # Same bars, traded pair added first: at the closing timestamp the EURGBP bar is
    # dispatched before the GBPUSD bar, so the fill converts at the previous bar's 1.22000.
    # M3 must add conversion-only bars ahead of the traded bars (or accept this lag).
    change = _eurgbp_usd_change({"EURGBP": EURGBP_CLOSES, "GBPUSD": GBPUSD_CLOSES})
    assert change == Decimal("1220.00")  # 1,000 GBP x 1.22000
