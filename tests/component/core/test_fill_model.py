"""The backtest fill model is seeded, so a stored run can be reproduced (MCP spec S2.3)."""

import random
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.backtest.models import LatencyModel

from src.core.backtest_orchestrator import BacktestOrchestrator, _run_record_fields
from src.core.fill_model import make_fill_model
from src.core.fill_models import (
    GapAwareFillModel,
    NextBarExecutionModule,
    gap_aware_venue_options,
)
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
    make.assert_called_once_with(7, kind="default")


def test_multi_instrument_setup_seeds_from_the_request():
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.make_fill_model", side_effect=RuntimeError("stop")) as make,
        pytest.raises(RuntimeError, match="stop"),
    ):
        orchestrator._setup_engine_multi(_request(fill_seed=7), instruments_data=[])
    make.assert_called_once_with(7, kind="default")


def test_the_seed_is_recorded_in_the_config_snapshot():
    assert _run_record_fields(_request(fill_seed=7))["config_snapshot"]["fill_seed"] == 7


def test_the_gap_aware_model_shares_the_seeded_probabilities():
    model = make_fill_model(DEFAULT_FILL_SEED, kind="gap_aware")
    assert isinstance(model, GapAwareFillModel)
    assert model.prob_fill_on_limit == 0.95
    assert model.prob_fill_on_stop == 0.95
    assert model.prob_slippage == 0.01


def test_the_gap_aware_model_is_seeded_like_the_default():
    make_fill_model(7, kind="gap_aware")
    gap_aware = [random.random() for _ in range(5)]
    assert gap_aware == _draws(7)


def test_an_unknown_fill_model_is_an_error():
    with pytest.raises(ValueError, match="optimistic"):
        make_fill_model(7, kind="optimistic")


def test_single_instrument_setup_uses_the_requested_model():
    orchestrator = BacktestOrchestrator()
    with (
        patch(f"{MODULE}.make_fill_model", side_effect=RuntimeError("stop")) as make,
        pytest.raises(RuntimeError, match="stop"),
    ):
        orchestrator._setup_engine(
            _request(fill_model="gap_aware"), bars=[], instrument=MagicMock()
        )
    make.assert_called_once_with(DEFAULT_FILL_SEED, kind="gap_aware")


def test_a_gap_aware_run_records_its_model_and_a_default_run_does_not():
    gap_aware = _run_record_fields(_request(fill_model="gap_aware"))["config_snapshot"]
    assert gap_aware["fill_model"] == "gap_aware"
    assert "fill_model" not in _run_record_fields(_request())["config_snapshot"]


def test_a_default_model_adds_nothing_to_the_venue():
    assert gap_aware_venue_options(make_fill_model(7)) == {}


def test_a_gap_aware_model_gets_next_bar_execution_at_the_venue():
    options = gap_aware_venue_options(make_fill_model(7, kind="gap_aware"))
    assert isinstance(options["latency_model"], LatencyModel)
    assert options["latency_model"].base_latency_nanos == 1
    assert [type(m) for m in options["modules"]] == [NextBarExecutionModule]
