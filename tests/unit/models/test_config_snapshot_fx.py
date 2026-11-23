"""An FX run's account currency and pair terms survive the snapshot; other rows gain no key.

``BacktestPersistenceService`` stores ``StrategyConfigSnapshot(**snapshot).model_dump()``,
so a key the model does not declare is silently dropped (the futures lesson, task 4.5).
M4 fills ``fx_pairs``; M3 declares it (task 3.7).
"""

import pytest

from src.models.config_snapshot import StrategyConfigSnapshot

pytestmark = pytest.mark.unit

BASE = dict(strategy_path="src.x:Y", config_path="config/x.yaml", config={"a": 1})
USDJPY = {
    "USD/JPY.IDEALPRO": {
        "base": "USD",
        "quote": "JPY",
        "pip_size": "0.01",
        "margin_pct": "3.0",
        "commission": {"rate_bps": "0.20", "min_per_order_usd": "2.00"},
        "typical_spread_pips": "0.5",
        "as_of": "2026-11-22",
        "conversion_pair": "USDJPY",
    }
}


def test_an_equity_or_futures_snapshot_dumps_no_fx_keys():
    dumped = StrategyConfigSnapshot(**BASE).model_dump()
    assert "fx_pairs" not in dumped
    assert "account_currency" not in dumped


def test_an_fx_snapshot_keeps_its_account_currency_and_pair_terms():
    dumped = StrategyConfigSnapshot(**BASE, fx_pairs=USDJPY, account_currency="USD").model_dump()
    assert dumped["fx_pairs"] == USDJPY
    assert dumped["account_currency"] == "USD"
