"""Component tests for backtest run page routes."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.web import app
from src.core.strategy_registry import StrategyDefinition, StrategyRegistry


@pytest.fixture(autouse=True)
def mock_strategy_registry():
    """Register test strategies for all tests in this module."""
    original_strategies = StrategyRegistry._strategies.copy()
    original_aliases = StrategyRegistry._aliases.copy()
    original_discovered = StrategyRegistry._discovered

    StrategyRegistry._strategies.clear()
    StrategyRegistry._aliases.clear()
    StrategyRegistry._discovered = True

    # Create mock strategy classes
    mock_strategy_class = MagicMock()
    mock_strategy_class.__module__ = "src.core.strategies.sma_crossover"
    mock_strategy_class.__name__ = "SMACrossover"

    mock_config_class = MagicMock()
    mock_config_class.__module__ = "src.core.strategies.sma_crossover"
    mock_config_class.__name__ = "SMACrossoverConfig"

    StrategyRegistry._strategies["sma_crossover"] = StrategyDefinition(
        name="sma_crossover",
        description="Simple Moving Average Crossover",
        strategy_class=mock_strategy_class,
        config_class=mock_config_class,
        aliases=["sma"],
    )
    StrategyRegistry._aliases["sma_crossover"] = "sma_crossover"
    StrategyRegistry._aliases["sma"] = "sma_crossover"

    yield

    StrategyRegistry._strategies = original_strategies
    StrategyRegistry._aliases = original_aliases
    StrategyRegistry._discovered = original_discovered


@pytest.fixture
def client() -> TestClient:
    """Get test client."""
    return TestClient(app)


class TestGetRunBacktestForm:
    """Tests for GET /backtests/run."""

    def test_returns_200(self, client):
        response = client.get("/backtests/run")
        assert response.status_code == 200

    def test_contains_strategy_dropdown(self, client):
        response = client.get("/backtests/run")
        html = response.text
        assert "sma_crossover" in html
        assert "Simple Moving Average Crossover" in html

    def test_contains_form_fields(self, client):
        response = client.get("/backtests/run")
        html = response.text
        assert 'name="symbol"' in html
        assert 'name="start_date"' in html
        assert 'name="end_date"' in html
        assert 'name="data_source"' in html
        assert 'name="timeframe"' in html
        assert 'name="starting_balance"' in html
        assert 'name="timeout_seconds"' in html

    def test_contains_submit_button(self, client):
        response = client.get("/backtests/run")
        assert "Run Backtest" in response.text

    def test_contains_data_source_options(self, client):
        response = client.get("/backtests/run")
        html = response.text
        assert "catalog" in html
        assert "ibkr" in html
        assert "kraken" in html
        assert "mock" in html

    def test_contains_timeframe_options(self, client):
        response = client.get("/backtests/run")
        html = response.text
        assert "1-DAY" in html
        assert "1-HOUR" in html
        assert "1-MINUTE" in html


class TestPostRunBacktest:
    """Tests for POST /backtests/run."""

    def _form_data(self, **overrides) -> dict:
        """Return valid form data."""
        data = {
            "strategy": "sma_crossover",
            "symbol": "AAPL",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "data_source": "catalog",
            "timeframe": "1-DAY",
            "starting_balance": "1000000",
            "timeout_seconds": "300",
        }
        data.update(overrides)
        return data

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_successful_submission_redirects(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_result = MagicMock()
        mock_load_result.bars = [MagicMock()]
        mock_load_result.instrument = MagicMock()
        mock_load_data.return_value = mock_load_result

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(return_value=(MagicMock(), run_id))
        mock_orchestrator_cls.return_value = mock_orchestrator

        response = client.post(
            "/backtests/run",
            data=self._form_data(),
            follow_redirects=False,
        )

        assert response.headers.get("HX-Redirect") == f"/backtests/{run_id}"
        mock_orchestrator.dispose.assert_called_once()

    def test_validation_error_rerenders_form(self, client):
        response = client.post(
            "/backtests/run",
            data=self._form_data(start_date="2024-12-31", end_date="2024-01-01"),
        )
        assert response.status_code == 200
        assert "start_date must be before end_date" in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_execution_error_shows_message(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_data.side_effect = ValueError("No data found for AAPL")

        response = client.post(
            "/backtests/run",
            data=self._form_data(),
        )
        assert response.status_code == 200
        assert "No data found for AAPL" in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_orchestrator_disposed_on_error(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-DAY-LAST"
        mock_request.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_result = MagicMock()
        mock_load_result.bars = [MagicMock()]
        mock_load_result.instrument = MagicMock()
        mock_load_data.return_value = mock_load_result

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(side_effect=RuntimeError("Engine error"))
        mock_orchestrator_cls.return_value = mock_orchestrator

        response = client.post(
            "/backtests/run",
            data=self._form_data(),
        )
        assert response.status_code == 200
        mock_orchestrator.dispose.assert_called_once()
