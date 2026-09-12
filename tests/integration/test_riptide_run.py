"""Integration tests for the Riptide strategy against a real BacktestEngine.

Runs the strategy end-to-end on a crafted, deterministic daily-bar series that
forces one full round trip: a 15/5-day-low pullback in an uptrend triggers a
buy-limit entry that fills on the next bar's dip, then a green candle flattens
the position at the following open. Compact indicator periods keep the scenario
small while exercising the real order/fill/position wiring.

Runs under ``--forked`` (Nautilus C extensions corrupt state across fork()).
"""

from decimal import Decimal

import pandas as pd
import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.fill_models import GapAwareFillModel
from src.core.strategies.riptide import Riptide, RiptideConfig

# Common prefix: a 25-bar uptrend (100..124), a dip to a 5-day low (118) that is
# still above the SMA-20 (setup bar), then the fill bar (116). Tails below drive
# each distinct exit path from the same entry at the 115.64 limit.
_PREFIX_CLOSES = [100.0 + i for i in range(25)] + [118.0, 116.0]

_EXIT_TAILS = {
    "green": [119.0, 120.0, 121.0, 122.0],  # green candle -> exit next open
    "atr_stop": [100.0, 98.0, 99.0, 100.0],  # crash -> resting ATR stop fires
    "time_stop": [115.0, 114.0, 113.5, 113.0, 113.0],  # red drift -> time stop
}


def _bars_from_closes(bar_type: BarType, closes: list[float]) -> list[Bar]:
    """Build daily bars from a close series (open = prior close)."""
    start = pd.Timestamp("2024-01-01", tz="UTC")
    bars: list[Bar] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        open_ = prev_close if i > 0 else close
        high = max(open_, close) + 0.5
        low = min(open_, close) - 0.5
        ts = int((start + pd.Timedelta(days=i)).value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{open_:.2f}"),
                high=Price.from_str(f"{high:.2f}"),
                low=Price.from_str(f"{low:.2f}"),
                close=Price.from_str(f"{close:.2f}"),
                volume=Quantity.from_int(1_000_000),  # $100M+ dollar volume
                ts_event=ts,
                ts_init=ts,
            )
        )
        prev_close = close
    return bars


def _run(
    bars: list[Bar],
    instrument,
    bar_type: BarType,
    config: RiptideConfig | None = None,
) -> BacktestEngine:
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("RIPTIDE-001"),
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    # Mirror production wiring (backtest_orchestrator): gap-aware fills so stops
    # gapped through overnight fill at the open and MOO-tagged exits fill at the
    # next bar's open.
    fill_model = GapAwareFillModel()
    fill_model.register_clock(engine.kernel.clock)
    fill_model.register_bars(bars)
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(1_000_000, USD)],
        fill_model=fill_model,
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    config = config or RiptideConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        portfolio_value=Decimal("1000000"),
        position_size_pct=Decimal("10.0"),
        sma_trend_period=20,
        pullback_period=5,
        limit_offset_pct=Decimal("2.0"),
        atr_period=5,
        atr_stop_mult=Decimal("2.5"),
        max_hold_days=3,
        adv_period=5,
        min_adv_dollars=Decimal("10000000"),
        roc_period=5,
    )
    engine.add_strategy(Riptide(config=config))
    engine.run()
    return engine


def _scenario(bar_type: BarType, exit_path: str) -> list[Bar]:
    """Common pullback setup + the tail that drives the requested exit path."""
    return _bars_from_closes(bar_type, _PREFIX_CLOSES + _EXIT_TAILS[exit_path])


@pytest.mark.integration
def test_riptide_round_trip_executes_a_trade():
    """The pullback setup fills at the 2%-below limit and closes on the green exit."""
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    engine = _run(_scenario(bar_type, "green"), instrument, bar_type)

    positions = engine.trader.generate_positions_report()
    fills = engine.trader.generate_order_fills_report()

    # At least one fully closed long round trip.
    assert not positions.empty, "expected at least one position"
    assert len(fills) >= 2, "expected an entry fill and an exit fill"

    # Entry filled at the 2%-below limit (118.00 * 0.98 = 115.64) or better.
    buy_fills = fills[fills["side"] == "BUY"]
    assert not buy_fills.empty
    assert float(buy_fills.iloc[0]["avg_px"]) <= 115.64 + 1e-6

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("exit_path", ["green", "atr_stop", "time_stop"])
def test_riptide_exit_paths(exit_path):
    """Each exit mechanism (green candle, ATR stop, time stop) closes the trade."""
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    engine = _run(_scenario(bar_type, exit_path), instrument, bar_type)

    positions = engine.trader.generate_positions_report()
    fills = engine.trader.generate_order_fills_report()

    # Every path yields exactly one closed round trip (entry + exit fills).
    assert len(positions) == 1
    assert len(fills) == 2
    assert {"BUY", "SELL"} == set(fills["side"])

    entry_px = float(fills[fills["side"] == "BUY"].iloc[0]["avg_px"])
    exit_px = float(fills[fills["side"] == "SELL"].iloc[0]["avg_px"])
    if exit_path == "green":
        assert exit_px > entry_px  # exited into strength at a profit
    elif exit_path == "atr_stop":
        assert exit_px < entry_px  # stopped out below entry

    engine.dispose()


@pytest.mark.integration
def test_riptide_accepts_float_config_params():
    """Config values may arrive as int/float (YAML path); Decimal maths must hold.

    Nautilus StrategyConfig does not coerce Decimal-typed fields, so the loader
    delivers plain int/float. Regression for a float/Decimal TypeError that only
    surfaced through the CLI, not the Decimal-typed fixtures.
    """
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("RIPTIDE-002"),
            logging=LoggingConfig(bypass_logging=True),
        )
    )
    engine.add_venue(
        venue=instrument.id.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(1_000_000, USD)],
    )
    engine.add_instrument(instrument)
    engine.add_data(_bars_from_closes(bar_type, _PREFIX_CLOSES + _EXIT_TAILS["green"]))

    # Numeric literals as the YAML/config loader delivers them: int and float.
    config = RiptideConfig(
        instrument_id=instrument.id,
        bar_type=bar_type,
        portfolio_value=1000000,  # int
        position_size_pct=10.0,  # float
        sma_trend_period=20,
        pullback_period=5,
        limit_offset_pct=2.0,  # float
        atr_period=5,
        atr_stop_mult=2.5,  # float
        max_hold_days=3,
        adv_period=5,
        min_adv_dollars=10000000,  # int
        roc_period=5,
    )
    engine.add_strategy(Riptide(config=config))
    engine.run()  # would raise float/Decimal TypeError before the coercion fix

    assert len(engine.trader.generate_positions_report()) == 1
    engine.dispose()


def _bars_from_ohlc(bar_type: BarType, rows: list[tuple]) -> list[Bar]:
    """Build daily bars from explicit (open, high, low, close) rows.

    Unlike ``_bars_from_closes`` (open = prior close, so gapless), this builder
    can express overnight gaps — required to pin down fill prices when price
    gaps through a limit, a stop trigger, or an exit's next open.
    """
    start = pd.Timestamp("2024-01-01", tz="UTC")
    bars: list[Bar] = []
    for i, (open_, high, low, close) in enumerate(rows):
        ts = int((start + pd.Timedelta(days=i)).value)
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{open_:.2f}"),
                high=Price.from_str(f"{high:.2f}"),
                low=Price.from_str(f"{low:.2f}"),
                close=Price.from_str(f"{close:.2f}"),
                volume=Quantity.from_int(1_000_000),  # $100M+ dollar volume
                ts_event=ts,
                ts_init=ts,
            )
        )
    return bars


# Gap-scenario prefix: a 25-bar flat-OHLC uptrend, then the setup bar — close
# 118.00 is a 5-day low above the SMA-20, so a buy limit rests at 115.64.
_GAP_PREFIX = [(100.0 + i,) * 4 for i in range(25)] + [(119.0, 119.5, 117.5, 118.0)]
# Fill bar: dips to 115.00 intrabar, so the limit fills at exactly 115.64.
_GAP_FILL_BAR = (118.0, 118.5, 115.0, 116.0)


@pytest.mark.integration
def test_riptide_stop_gapped_through_fills_at_open():
    """A bar opening far below the ATR stop trigger fills the stop at the open.

    The engine default fills a triggered stop-market at its trigger price even
    when the bar gaps through it overnight — overstating stop protection.
    GapAwareFillModel must fill at the (worse) open instead.
    """
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    # Entry at 115.64 puts the 2.5*ATR(5) stop trigger well above 100; the next
    # bar gaps down to open at 90.00, far through it.
    rows = _GAP_PREFIX + [
        _GAP_FILL_BAR,
        (90.0, 92.0, 88.0, 91.0),
        (91.0, 93.0, 90.0, 92.0),
        (92.0, 94.0, 91.0, 93.0),
    ]
    engine = _run(_bars_from_ohlc(bar_type, rows), instrument, bar_type)

    fills = engine.trader.generate_order_fills_report()
    assert {"BUY", "SELL"} == set(fills["side"])
    assert float(fills[fills["side"] == "BUY"].iloc[0]["avg_px"]) == pytest.approx(115.64)
    # Stop fill at the gap open, not at the ~110 trigger.
    assert float(fills[fills["side"] == "SELL"].iloc[0]["avg_px"]) == pytest.approx(90.00)

    engine.dispose()


@pytest.mark.integration
def test_riptide_green_exit_fills_at_next_bar_open():
    """The green-candle exit fills at the next bar's open (MOO), not the signal close.

    Green candle closes at 119.00; the next bar gaps up to open at 125.00. Per
    the spec (sell at market open on day T+1) the exit must fill at 125.00.
    """
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    rows = _GAP_PREFIX + [
        _GAP_FILL_BAR,
        (116.0, 119.5, 115.8, 119.0),  # green candle: close 119 > prev close 116
        (125.0, 126.0, 124.0, 125.5),  # next day gaps up to open 125
        (125.0, 126.0, 124.0, 125.0),
    ]
    engine = _run(_bars_from_ohlc(bar_type, rows), instrument, bar_type)

    fills = engine.trader.generate_order_fills_report()
    assert {"BUY", "SELL"} == set(fills["side"])
    assert float(fills[fills["side"] == "BUY"].iloc[0]["avg_px"]) == pytest.approx(115.64)
    assert float(fills[fills["side"] == "SELL"].iloc[0]["avg_px"]) == pytest.approx(125.00)

    engine.dispose()


@pytest.mark.integration
def test_riptide_completes_and_preserves_capital_shape():
    """Engine runs to completion and the account remains well-formed."""
    instrument = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")

    engine = _run(_scenario(bar_type, "green"), instrument, bar_type)

    account = engine.trader.generate_account_report(Venue("NASDAQ"))
    assert not account.empty

    engine.dispose()
