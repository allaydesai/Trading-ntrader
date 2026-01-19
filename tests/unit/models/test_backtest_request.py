"""Tests for BacktestRequest model."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import yaml

from src.models.backtest_request import BacktestRequest


class TestBacktestRequest:
    """Tests for BacktestRequest model."""

    def test_create_backtest_request_with_all_fields(self):
        """Test creating a BacktestRequest with all required fields."""
        request = BacktestRequest(
            strategy_type="sma_crossover",
            strategy_path="src.core.strategies.sma_crossover:SMACrossover",
            config_path="src.core.strategies.sma_crossover:SMAConfig",
            strategy_config={"fast_period": 10, "slow_period": 20},
            symbol="AAPL",
            instrument_id="AAPL.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 1, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            persist=True,
        )

        assert request.strategy_type == "sma_crossover"
        assert request.symbol == "AAPL"
        assert request.persist is True
        assert request.starting_balance == Decimal("1000000")

    def test_create_backtest_request_with_minimal_fields(self):
        """Test creating a BacktestRequest with minimal required fields."""
        request = BacktestRequest(
            strategy_type="sma",
            strategy_path="src.core.strategies:SMA",
            symbol="SPY",
            instrument_id="SPY.ARCA",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 1),
            bar_type="1-HOUR-LAST",
        )

        assert request.strategy_type == "sma"
        assert request.config_path is None
        assert request.persist is True  # Default

    def test_timezone_aware_dates(self):
        """Test that dates are converted to timezone-aware."""
        request = BacktestRequest(
            strategy_type="test",
            strategy_path="test:Test",
            symbol="TEST",
            instrument_id="TEST.SIM",
            start_date=datetime(2024, 1, 1),  # No timezone
            end_date=datetime(2024, 6, 1),  # No timezone
            bar_type="1-DAY-LAST",
        )

        assert request.start_date.tzinfo is not None
        assert request.end_date.tzinfo is not None

    def test_date_range_validation(self):
        """Test that start_date must be before end_date."""
        with pytest.raises(ValueError, match="start_date must be before end_date"):
            BacktestRequest(
                strategy_type="test",
                strategy_path="test:Test",
                symbol="TEST",
                instrument_id="TEST.SIM",
                start_date=datetime(2024, 6, 1, tzinfo=timezone.utc),
                end_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                bar_type="1-DAY-LAST",
            )

    def test_to_config_snapshot(self):
        """Test converting request to config snapshot."""
        request = BacktestRequest(
            strategy_type="sma_crossover",
            strategy_path="src.core.strategies:SMA",
            symbol="AAPL",
            instrument_id="AAPL.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 1, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            strategy_config={"fast_period": 10},
            config_file_path="/path/to/config.yaml",
        )

        snapshot = request.to_config_snapshot()

        assert snapshot["strategy_type"] == "sma_crossover"
        assert snapshot["symbol"] == "AAPL"
        assert snapshot["config_file_path"] == "/path/to/config.yaml"
        assert "start_date" in snapshot
        assert "end_date" in snapshot


class TestBacktestRequestFromYaml:
    """Tests for creating BacktestRequest from YAML config."""

    @pytest.fixture
    def sample_yaml_data(self):
        """Sample YAML configuration data."""
        return {
            "strategy_path": "src.core.strategies.apolo_rsi:ApoloRSI",
            "config_path": "src.core.strategies.apolo_rsi:ApoloRSIConfig",
            "config": {
                "instrument_id": "AMD.NASDAQ",
                "bar_type": "AMD.NASDAQ-1-DAY-LAST-EXTERNAL",
                "trade_size": 100,
                "rsi_period": 2,
            },
            "backtest": {
                "start_date": "2024-01-01",
                "end_date": "2024-06-30",
                "initial_capital": 100000,
            },
        }

    def test_from_yaml_config(self, sample_yaml_data):
        """Test creating BacktestRequest from YAML data."""
        request = BacktestRequest.from_yaml_config(
            yaml_data=sample_yaml_data,
            persist=True,
            config_file_path="/path/to/config.yaml",
        )

        assert request.strategy_type == "apolorsi"  # Extracted from class name
        assert request.symbol == "AMD"
        assert request.instrument_id == "AMD.NASDAQ"
        assert request.bar_type == "1-DAY-LAST"
        assert request.persist is True
        assert request.config_file_path == "/path/to/config.yaml"
        assert request.starting_balance == Decimal("100000")
        assert "trade_size" in request.strategy_config

    def test_from_yaml_config_no_persist(self, sample_yaml_data):
        """Test creating BacktestRequest from YAML with persist=False."""
        request = BacktestRequest.from_yaml_config(
            yaml_data=sample_yaml_data,
            persist=False,
        )

        assert request.persist is False

    def test_from_yaml_config_missing_instrument_id(self, sample_yaml_data):
        """Test error when instrument_id is missing."""
        del sample_yaml_data["config"]["instrument_id"]

        with pytest.raises(ValueError, match="instrument_id"):
            BacktestRequest.from_yaml_config(sample_yaml_data)

    def test_from_yaml_config_missing_bar_type(self, sample_yaml_data):
        """Test error when bar_type is missing."""
        del sample_yaml_data["config"]["bar_type"]

        with pytest.raises(ValueError, match="bar_type"):
            BacktestRequest.from_yaml_config(sample_yaml_data)

    def test_from_yaml_config_missing_dates(self, sample_yaml_data):
        """Test error when dates are missing."""
        del sample_yaml_data["backtest"]["start_date"]

        with pytest.raises(ValueError, match="start_date"):
            BacktestRequest.from_yaml_config(sample_yaml_data)

    def test_from_yaml_file(self, tmp_path, sample_yaml_data):
        """Test loading BacktestRequest from YAML file."""
        config_file = tmp_path / "test_config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(sample_yaml_data, f)

        request = BacktestRequest.from_yaml_file(config_file, persist=True)

        assert request.strategy_type == "apolorsi"
        assert request.symbol == "AMD"

    def test_from_yaml_file_not_found(self, tmp_path):
        """Test error when YAML file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            BacktestRequest.from_yaml_file(tmp_path / "nonexistent.yaml")
