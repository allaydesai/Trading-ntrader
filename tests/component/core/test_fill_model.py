"""The backtest fill model is seeded, so a stored run can be reproduced (MCP spec S2.3)."""

import random
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.core.backtest_orchestrator import BacktestOrchestrator, _run_record_fields
from src.core.fill_model import make_fill_model
from src.models.backtest_request import DEFAULT_FILL_SEED, BacktestRequest

pytestmark = pytest.mark.component

MODULE = "src.core.backtest_orchestrator"


def _request(**overrides) -> BacktestRequest:
    fields = dict(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        config_path="src.core.strategies.sma_crossover:SMAConfig",
        symbol="QQQ",
        instrument_id="QQQ.NAMED_CATALOG",
        start_date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2020, 12, 31, tzinfo=timezone.utc),
        bar_type="1-DAY-LAST",
    )
    fields.update(overrides)
    return BacktestRequest(**fields)


def _draws(seed: int) -> list[float]:
    make_fill_model(seed)
    return [random.random() for _ in range(5)]


def test_the_same_seed_gives_the_same_random_sequence():
    assert _draws(7) == _draws(7)
    assert _draws(7) != _draws(8)


def test_the_fill_probabilities_are_unchanged():
    model = make_fill_model(DEFAULT_FILL_SEED)
    assert model.prob_fill_on_limit == 0.95
    assert model.prob_fill_on_stop == 0.95
    assert model.prob_slippage == 0.01


def test_a_request_is_seeded_by_default():
    assert _request().fill_seed == DEFAULT_FILL_SEED


def test_single_instrument_setup_seeds_from_the_request():
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.make_fill_model", side_effect=RuntimeError("stop")) as make,
        pytest.raises(RuntimeError, match="stop"),
    ):
        orchestrator._setup_engine(_request(fill_seed=7), bars=[], instrument=MagicMock())
    make.assert_called_once_with(7)


def test_multi_instrument_setup_seeds_from_the_request():
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.make_fill_model", side_effect=RuntimeError("stop")) as make,
        pytest.raises(RuntimeError, match="stop"),
    ):
        orchestrator._setup_engine_multi(_request(fill_seed=7), instruments_data=[])
    make.assert_called_once_with(7)


def test_the_seed_is_recorded_in_the_config_snapshot():
    assert _run_record_fields(_request(fill_seed=7))["config_snapshot"]["fill_seed"] == 7
