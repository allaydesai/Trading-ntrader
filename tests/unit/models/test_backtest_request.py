"""Tests for BacktestRequest model."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

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


class TestBacktestRequestCatalogName:
    """Tests for the catalog_name field and persistence helper (Story 3.1)."""

    def _base_kwargs(self) -> dict:
        return {
            "strategy_type": "sma_crossover",
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "symbol": "AAPL",
            "instrument_id": "AAPL.NASDAQ",
            "start_date": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "end_date": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "bar_type": "1-DAY-LAST",
        }

    def test_catalog_name_defaults_to_none(self):
        """catalog_name should default to None when not provided."""
        request = BacktestRequest(**self._base_kwargs())
        assert request.catalog_name is None

    def test_catalog_name_can_be_set(self):
        """catalog_name should accept a string value."""
        request = BacktestRequest(catalog_name="e2e-test", **self._base_kwargs())
        assert request.catalog_name == "e2e-test"

    def test_from_cli_args_propagates_catalog_name(self):
        """from_cli_args should propagate catalog_name to the built request."""
        request = BacktestRequest.from_cli_args(
            strategy="sma_crossover",
            symbol="AAPL",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 6, 1, tzinfo=timezone.utc),
            bar_type_spec="1-MINUTE-LAST",
            catalog_name="e2e-test",
        )
        assert request.catalog_name == "e2e-test"
        assert request.symbol == "AAPL"

    def test_from_cli_args_normalizes_midnight_end_to_end_of_day(self):
        """Review #4: a date-only (midnight) end becomes inclusive end-of-day.

        Without this the CLI's `--end 2024-12-31` (parsed to midnight) silently
        excluded every bar on the final trading day, diverging from the web UI.
        """
        request = BacktestRequest.from_cli_args(
            strategy="sma_crossover",
            symbol="AAPL",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_type_spec="1-DAY-LAST",
        )
        assert request.end_date == datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)

    def test_from_cli_args_preserves_explicit_end_of_day(self):
        """A non-midnight end (web end-of-day / explicit time) is left unchanged."""
        explicit = datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
        request = BacktestRequest.from_cli_args(
            strategy="sma_crossover",
            symbol="AAPL",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=explicit,
            bar_type_spec="1-DAY-LAST",
        )
        assert request.end_date == explicit

    def test_from_cli_args_skips_instrument_resolution_when_catalog_name_set(self):
        """When catalog_name is set, _resolve_instrument_id must not be called.

        The DB-authoritative nautilus_id will be overridden at load time —
        the request starts with a bare {symbol}.PLACEHOLDER shape that the
        loader overwrites.
        """
        with patch("src.models.backtest_request._resolve_instrument_id") as mock_resolve:
            BacktestRequest.from_cli_args(
                strategy="sma_crossover",
                symbol="AAPL",
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, tzinfo=timezone.utc),
                bar_type_spec="1-DAY-LAST",
                catalog_name="e2e-test",
            )
            mock_resolve.assert_not_called()

    def test_to_config_snapshot_includes_catalog_name(self):
        """Config snapshot should include catalog_name for reproducibility."""
        request = BacktestRequest(catalog_name="e2e-test", **self._base_kwargs())
        snapshot = request.to_config_snapshot()
        assert snapshot["catalog_name"] == "e2e-test"

    def test_to_persistence_data_source_with_catalog_name(self):
        """Named-catalog runs persist as 'catalog:<name>'."""
        request = BacktestRequest(catalog_name="e2e-test", **self._base_kwargs())
        assert request.to_persistence_data_source() == "catalog:e2e-test"

    def test_to_persistence_data_source_without_catalog_name(self):
        """Non-named runs persist as the underlying data_source."""
        request = BacktestRequest(**self._base_kwargs())
        assert request.to_persistence_data_source() == "catalog"

    def test_to_persistence_data_source_with_kraken(self):
        """Kraken runs persist as 'kraken', unaffected by catalog_name default."""
        kwargs = self._base_kwargs()
        kwargs["data_source"] = "kraken"
        kwargs["instrument_id"] = "BTC/USD.KRAKEN"
        request = BacktestRequest(**kwargs)
        assert request.to_persistence_data_source() == "kraken"

    def test_data_source_allow_list_unchanged(self):
        """The data_source validator allow-list must stay {catalog,ibkr,kraken,mock}."""
        from pydantic import ValidationError

        kwargs = self._base_kwargs()
        kwargs["data_source"] = "firstrate"
        with pytest.raises(ValidationError, match="Invalid data_source"):
            BacktestRequest(**kwargs)
