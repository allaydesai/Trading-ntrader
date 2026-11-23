"""FX M3 (task 3.5): trade rows store ``profit_loss`` in USD (decision D11).

Nautilus reports a USDJPY position's realised P&L in JPY (``"100000.00 JPY"``). The writer
parses the currency, converts non-USD P&L at the position's exit rate (``usd_rates``, from
``src/core/fx_results.exit_rates``) and refuses a non-USD P&L it has no rate for, instead of
storing 100,000 "dollars". USD rows are parsed exactly as before.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from src.db.exceptions import ValidationError
from src.services.backtest_persistence import BacktestPersistenceService, usd_profit_loss

pytestmark = pytest.mark.unit

RATE_151 = Decimal(str(1 / 151))  # what get_xrate returned at the USDJPY 151.000 exit


def _row(position_id: str, instrument: str, pnl) -> dict:
    return {
        "position_id": position_id,
        "instrument_id": instrument,
        "opening_order_id": "O-1",
        "closing_order_id": "O-2",
        "entry": "BUY",
        "peak_qty": 100_000,
        "avg_px_open": 150.0,
        "avg_px_close": 151.0,
        "ts_opened": pd.Timestamp("2025-03-06 15:00", tz="UTC"),
        "ts_closed": pd.Timestamp("2025-03-06 17:00", tz="UTC"),
        "duration_ns": 7_200_000_000_000,
        "realized_pnl": pnl,
        "commissions": ["0.00 USD"],
    }


async def _save(frame_rows: list[dict], usd_rates=None) -> list:
    repository = AsyncMock()
    service = BacktestPersistenceService(repository)
    frame = pd.DataFrame(frame_rows).set_index("position_id")
    await service.save_trades_from_positions(
        backtest_run_id=1, positions_report_df=frame, usd_rates=usd_rates
    )
    return repository.bulk_create_trades.call_args.args[0]


def test_usd_pnl_is_parsed_as_before():
    assert usd_profit_loss("-134.66 USD", None) == Decimal("-134.66")
    assert usd_profit_loss("100.00 USD", Decimal("2")) == Decimal("100.00")  # rate unused


def test_no_realized_pnl_is_zero():
    assert usd_profit_loss(None, None) == Decimal("0.00")
    assert usd_profit_loss(float("nan"), None) == Decimal("0.00")


def test_a_jpy_pnl_converts_at_the_exit_rate_to_cents():
    assert usd_profit_loss("100000.00 JPY", RATE_151) == Decimal("662.25")
    assert usd_profit_loss("-100000.00 JPY", RATE_151) == Decimal("-662.25")


def test_a_gbp_pnl_converts_at_the_gbpusd_rate():
    assert usd_profit_loss("1000.00 GBP", Decimal("1.25")) == Decimal("1250.00")


def test_a_non_usd_pnl_without_a_rate_raises_naming_the_currency():
    with pytest.raises(ValueError, match="JPY"):
        usd_profit_loss("100000.00 JPY", None)


async def test_a_usdjpy_trade_row_stores_usd_profit_loss():
    saved = await _save(
        [_row("P-1", "USD/JPY.IDEALPRO", "100000.00 JPY")], usd_rates={"P-1": RATE_151}
    )
    assert saved[0].profit_loss == Decimal("662.25")


async def test_a_usdjpy_trade_row_without_a_rate_fails_the_write():
    with pytest.raises(ValidationError, match="JPY"):
        await _save([_row("P-1", "USD/JPY.IDEALPRO", "100000.00 JPY")])


async def test_usd_rows_are_unchanged_beside_an_fx_row():
    saved = await _save(
        [
            _row("P-1", "USD/JPY.IDEALPRO", "100000.00 JPY"),
            _row("P-2", "EUR/USD.IDEALPRO", "90.00 USD"),
        ],
        usd_rates={"P-1": RATE_151},
    )
    assert [t.profit_loss for t in saved] == [Decimal("662.25"), Decimal("90.00")]
