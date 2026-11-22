"""A futures run's contract terms survive the snapshot; equity rows gain no key (M3).

``BacktestPersistenceService`` stores ``StrategyConfigSnapshot(**snapshot).model_dump()``,
so a key the model does not declare is silently dropped.
"""

import pytest

from src.models.config_snapshot import StrategyConfigSnapshot

pytestmark = pytest.mark.unit

BASE = dict(strategy_path="src.x:Y", config_path="config/x.yaml", config={"a": 1})
ES = {
    "ES.CME": {
        "multiplier": "50",
        "margin_initial_usd": "27500",
        "margin_maintenance_usd": "25000",
        "commission_per_contract_usd": "2.25",
        "margin_as_of": "2026-11-21",
    }
}


def test_an_equity_snapshot_dumps_no_futures_keys():
    dumped = StrategyConfigSnapshot(**BASE).model_dump()
    assert "futures_contracts" not in dumped
    assert "profit_pct_basis" not in dumped


def test_a_futures_snapshot_keeps_its_contract_terms():
    dumped = StrategyConfigSnapshot(
        **BASE, futures_contracts=ES, profit_pct_basis="underlying"
    ).model_dump()
    assert dumped["futures_contracts"] == ES
    assert dumped["profit_pct_basis"] == "underlying"
