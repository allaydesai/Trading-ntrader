"""A cost-stress run's multiplier survives the snapshot; ordinary rows do not gain a key."""

import pytest

from src.models.config_snapshot import StrategyConfigSnapshot

pytestmark = pytest.mark.unit

BASE = dict(strategy_path="src.x:Y", config_path="config/x.yaml", config={"a": 1})


def test_an_ordinary_snapshot_dumps_no_cost_multiplier_key():
    assert "cost_multiplier" not in StrategyConfigSnapshot(**BASE).model_dump()


def test_a_stressed_snapshot_keeps_its_multiplier():
    dumped = StrategyConfigSnapshot(**BASE, cost_multiplier=2.0).model_dump()
    assert dumped["cost_multiplier"] == 2.0
