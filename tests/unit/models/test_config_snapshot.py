"""Unit tests for StrategyConfigSnapshot Pydantic model."""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.models.config_snapshot import StrategyConfigSnapshot
from src.services.backtest_persistence import BacktestPersistenceService


class TestStrategyConfigSnapshot:
    """Test suite for StrategyConfigSnapshot Pydantic validation model."""

    def test_valid_config_snapshot(self):
        """Test StrategyConfigSnapshot with valid data."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.sma_crossover.SMAStrategyConfig",
            config_path="config/strategies/sma_crossover.yaml",
            version="1.0",
            config={"fast_period": 10, "slow_period": 50, "risk_percent": 2.0},
        )

        assert snapshot.strategy_path == "src.strategies.sma_crossover.SMAStrategyConfig"
        assert snapshot.config_path == "config/strategies/sma_crossover.yaml"
        assert snapshot.version == "1.0"
        assert snapshot.config["fast_period"] == 10
        assert snapshot.config["slow_period"] == 50

    def test_missing_required_fields(self):
        """Test StrategyConfigSnapshot raises error for missing required fields."""
        with pytest.raises(ValidationError) as exc_info:
            StrategyConfigSnapshot(
                strategy_path="src.strategies.test.TestStrategy",
                # Missing config_path
                version="1.0",
                config={},
            )

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("config_path",) for error in errors)

    def test_empty_config_allowed(self):
        """Test StrategyConfigSnapshot allows empty config dict."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.test.TestStrategy",
            config_path="config/test.yaml",
            version="1.0",
            config={},
        )

        assert snapshot.config == {}

    def test_nested_config_structure(self):
        """Test StrategyConfigSnapshot with nested configuration."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.complex.ComplexStrategy",
            config_path="config/complex.yaml",
            version="1.0",
            config={
                "risk_management": {"max_position_size": 1000, "stop_loss_pct": 0.05},
                "indicators": {"sma_period": 20, "rsi_period": 14},
            },
        )

        assert "risk_management" in snapshot.config
        assert snapshot.config["risk_management"]["max_position_size"] == 1000

    def test_serialization_to_dict(self):
        """Test StrategyConfigSnapshot serializes to dictionary."""
        snapshot = StrategyConfigSnapshot(
            strategy_path="src.strategies.sma_crossover.SMAStrategyConfig",
            config_path="config/strategies/sma_crossover.yaml",
            version="1.0",
            config={"fast_period": 10, "slow_period": 50},
        )

        snapshot_dict = snapshot.model_dump()

        assert isinstance(snapshot_dict, dict)
        assert snapshot_dict["strategy_path"] == "src.strategies.sma_crossover.SMAStrategyConfig"
        assert "config" in snapshot_dict


#: The two snapshot shapes the backtest paths pass to ``BacktestPersistenceService``
#: (``backtest_orchestrator.py:522-537``, ``backtest_runner.py:112-125``) — both
#: carry an ``equity_curve`` the validator has always stripped (Story 5.2 F3).
ORCHESTRATOR_SNAPSHOT = {
    "strategy_path": "src.core.strategies.sma_crossover.SMACrossover",
    "config_path": "config/x.yaml",
    "version": "1.0",
    "config": {"fast_period": 10, "nested": {"a": 1.5}},
    "bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL",
    "equity_curve": [{"time": 1, "value": 2.0}],
    "config_file_path": "/tmp/x.yaml",
}
RUNNER_SNAPSHOT = {
    "strategy_path": "src.core.strategies.sma_crossover",
    "config_path": "runtime_config",
    "version": "1.0",
    "config": {"fast_period": 10},
    "equity_curve": [{"time": 1, "value": 2.0}],
}
#: ``json.dumps(validated)`` of each, captured from the code at ``cea1daf`` —
#: before ``session_conditions`` existed (Story 5.2, Task 1.4). Key order included.
BEFORE_SESSION_CONDITIONS = {
    "orchestrator": (
        '{"strategy_path": "src.core.strategies.sma_crossover.SMACrossover", '
        '"config_path": "config/x.yaml", "version": "1.0", '
        '"config": {"fast_period": 10, "nested": {"a": 1.5}}, '
        '"bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"}'
    ),
    "runner": (
        '{"strategy_path": "src.core.strategies.sma_crossover", '
        '"config_path": "runtime_config", "version": "1.0", '
        '"config": {"fast_period": 10}, "bar_type": null}'
    ),
}


class TestSessionConditions:
    """Story 5.2, PO ruling R2a: one optional ``session_conditions`` mapping,
    dropped when empty, so every backtest row stays byte-identical."""

    @staticmethod
    def _validated(snapshot: dict) -> dict:
        return BacktestPersistenceService(MagicMock())._validate_config_snapshot(snapshot)

    @pytest.mark.parametrize(
        ("shape", "snapshot"),
        [("orchestrator", ORCHESTRATOR_SNAPSHOT), ("runner", RUNNER_SNAPSHOT)],
    )
    def test_a_backtest_snapshot_validates_byte_identical_to_before(self, shape, snapshot):
        assert json.dumps(self._validated(dict(snapshot))) == BEFORE_SESSION_CONDITIONS[shape]

    def test_a_sealed_sessions_conditions_survive_validation_intact(self):
        conditions = {"equity_series": {"granularity": "closed_trade", "curve": [{"time": 1}]}}
        validated = self._validated({**RUNNER_SNAPSHOT, "session_conditions": conditions})
        assert validated["session_conditions"] == conditions
        assert "equity_curve" not in validated

    @pytest.mark.parametrize("empty", [None, {}])
    def test_empty_session_conditions_are_dropped(self, empty):
        snapshot = StrategyConfigSnapshot(
            strategy_path="a", config_path="b", session_conditions=empty
        )
        assert "session_conditions" not in snapshot.model_dump()
        assert "session_conditions" not in json.loads(snapshot.model_dump_json())
