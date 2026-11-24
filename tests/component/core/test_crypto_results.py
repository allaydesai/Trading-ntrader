"""Crypto M3 (tasks 3.7, 3.8): fractional coin quantities through the results path, and 365.

- ``trades`` rows keep a 0.128 BTC quantity, USD P&L and commission exactly (``Numeric(20, 8)``,
  no migration, decision C11);
- the mark-to-market rebuild and ``_check_against_accounts`` hold with fractional fills whose
  float sum is not exactly zero;
- a crypto run annualises Sharpe, Sortino and volatility on 365 calendar days, every other run on
  252 (decision C20).

Hand figures: the M3 tier-2 taker round trip (0.128 BTC, 78,000 -> 79,000; fees 60.90 + 61.68;
``tests/integration/core/test_crypto_venue_engine.py``).
"""

import math
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core import results_extractor
from src.core.crypto_costs import CRYPTO_PERIODS_PER_YEAR
from src.core.mark_to_market import TRADING_DAYS, Fill, daily_equity, risk_metrics
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair

pytestmark = pytest.mark.unit

BTC = build_crypto_pair(get_coin_spec("BTC"))
DAY_NS = 86_400 * 1_000_000_000
START = 1_600_000_000 * 1_000_000_000 // DAY_NS * DAY_NS  # a UTC midnight


# --- persistence --------------------------------------------------------------------------


def _row(peak_qty: float) -> dict:
    return {
        "position_id": "BTC/USD.KRAKEN-1",
        "instrument_id": "BTC/USD.KRAKEN",
        "opening_order_id": "O-1",
        "closing_order_id": "O-2",
        "entry": "BUY",
        "peak_qty": peak_qty,
        "avg_px_open": 78000.0,
        "avg_px_close": 79000.0,
        "ts_opened": pd.Timestamp("2025-03-06 15:00", tz="UTC"),
        "ts_closed": pd.Timestamp("2025-03-06 15:02", tz="UTC"),
        "duration_ns": 120_000_000_000,
        "realized_pnl": "5.42 USD",  # 128.00 less 122.58 fees
        "commissions": ["122.58 USD"],
    }


@pytest.mark.parametrize("peak_qty", [0.128, 0.12800000000000001])
async def test_a_fractional_trade_persists_its_quantity_pnl_and_commission(peak_qty):
    repository = AsyncMock()
    service = BacktestPersistenceService(repository)
    frame = pd.DataFrame([_row(peak_qty)]).set_index("position_id")

    assert await service.save_trades_from_positions(backtest_run_id=1, positions_report_df=frame)

    (trade,) = repository.bulk_create_trades.call_args.args[0]
    assert trade.quantity == Decimal("0.12800000")
    assert trade.entry_price == Decimal("78000") and trade.exit_price == Decimal("79000")
    assert trade.profit_loss == Decimal("5.42")
    assert (trade.commission_amount, trade.commission_currency) == (Decimal("122.58"), "USD")


# --- mark to market -----------------------------------------------------------------------


def _closes(*prices: float) -> list[tuple[int, float]]:
    return [(START + i * DAY_NS, p) for i, p in enumerate(prices)]


def test_fractional_fills_rebuild_the_usd_equity():
    closes = {"BTC/USD.KRAKEN": _closes(78000, 77000, 79000)}
    fills = [
        Fill(START, "BTC/USD.KRAKEN", 0.128, 78000.0, 60.90),
        Fill(START + 2 * DAY_NS, "BTC/USD.KRAKEN", -0.128, 79000.0, 61.68),
    ]
    equity = daily_equity(10000.0, fills, closes)
    # day 2 marks 0.128 BTC 1,000 lower; day 3 is flat at 10,000 + 128 - 122.58
    assert list(equity) == pytest.approx([9939.10, 9811.10, 10005.42])


def _engine(instrument, balance: float):
    account = SimpleNamespace(
        balance_total=lambda currency: SimpleNamespace(as_double=lambda: balance)
    )
    cache = SimpleNamespace(
        instrument=lambda instrument_id: instrument,
        account_for_venue=lambda venue: account,
    )
    return SimpleNamespace(cache=cache)


def test_the_account_check_holds_for_fractional_fills_netting_to_float_noise():
    # 0.1 + 0.2 - 0.3 is 5.5e-17 in floats: still flat, so the rebuilt balance is checked
    fills = [
        Fill(START, "BTC/USD.KRAKEN", 0.1, 78000.0, 0.0),
        Fill(START, "BTC/USD.KRAKEN", 0.2, 78000.0, 0.0),
        Fill(START + DAY_NS, "BTC/USD.KRAKEN", -0.3, 78000.0, 0.0),
    ]
    equity = pd.Series([10000.0])
    venues = {Venue("KRAKEN")}
    results_extractor._check_against_accounts(_engine(BTC, 10000.0), fills, venues, equity, 1e4)
    with pytest.raises(ValueError, match="rebuilt equity 10000.00 != balance 9000.00"):
        results_extractor._check_against_accounts(_engine(BTC, 9000.0), fills, venues, equity, 1e4)


# --- annualisation (decision C20) -----------------------------------------------------------


def test_risk_metrics_annualise_on_the_given_periods():
    equity = pd.Series([10100.0, 9950.0, 10200.0, 10150.0, 10300.0])
    on_252 = risk_metrics(equity, 10000.0)
    on_365 = risk_metrics(equity, 10000.0, periods_per_year=365)
    assert on_252 == risk_metrics(equity, 10000.0, periods_per_year=TRADING_DAYS)
    scale = math.sqrt(365 / 252)
    assert on_365.sharpe_ratio == pytest.approx(on_252.sharpe_ratio * scale)
    assert on_365.sortino_ratio == pytest.approx(on_252.sortino_ratio * scale)
    assert on_365.volatility == pytest.approx(on_252.volatility * scale)
    assert on_365.max_drawdown == on_252.max_drawdown  # not annualised


@pytest.mark.parametrize(
    ("instrument", "periods"),
    [
        (BTC, CRYPTO_PERIODS_PER_YEAR),
        (TestInstrumentProvider.equity("AAPL", "XNAS"), TRADING_DAYS),
    ],
    ids=["crypto-365", "equity-252"],
)
def test_a_crypto_run_annualises_on_365_and_others_on_252(monkeypatch, instrument, periods):
    iid = instrument.id
    prices = [100.0, 101.0, 99.5, 102.0, 101.5, 103.0]
    bars = [
        SimpleNamespace(ts_event=ts, close=SimpleNamespace(as_double=lambda p=p: p))
        for ts, p in _closes(*prices)
    ]
    fills = [Fill(START, str(iid), 10.0, 100.0, 0.0)]  # held open: no balance check
    monkeypatch.setattr(results_extractor, "_fills", lambda engine: fills)

    marked = results_extractor.mark_to_market(_engine(instrument, 0.0), {iid: bars}, 10000.0)

    assert marked is not None
    assert marked.risk == risk_metrics(marked.equity, 10000.0, periods_per_year=periods)
    assert marked.risk != risk_metrics(marked.equity, 10000.0, periods_per_year=365 + 252 - periods)
