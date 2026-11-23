"""FX M3 (task 3.5): the trade writer stores USD ``profit_loss`` for FX positions.

The service against a mocked repository: a USDJPY row stores its ``usd_pnls`` amount, a
missing rate fails the write, and USD rows beside it are untouched. The scalar parsing rules
are unit-tested in ``tests/unit/services/test_backtest_persistence_fx.py``.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from src.db.exceptions import ValidationError
from src.services.backtest_persistence import BacktestPersistenceService

pytestmark = pytest.mark.component

USD_662 = Decimal(str(100_000 / 151))  # 100,000 JPY at the USDJPY 151.000 exit


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


async def _save(frame_rows: list[dict], usd_pnls=None) -> list:
    repository = AsyncMock()
    service = BacktestPersistenceService(repository)
    frame = pd.DataFrame(frame_rows).set_index("position_id")
    await service.save_trades_from_positions(
        backtest_run_id=1, positions_report_df=frame, usd_pnls=usd_pnls
    )
    return repository.bulk_create_trades.call_args.args[0]


async def test_a_usdjpy_trade_row_stores_usd_profit_loss():
    saved = await _save(
        [_row("P-1", "USD/JPY.IDEALPRO", "100000.00 JPY")], usd_pnls={"P-1": USD_662}
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
        usd_pnls={"P-1": USD_662},
    )
    assert [t.profit_loss for t in saved] == [Decimal("662.25"), Decimal("90.00")]
