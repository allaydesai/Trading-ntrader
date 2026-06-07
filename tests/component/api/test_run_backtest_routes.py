"""Component tests for backtest run page routes."""

import asyncio
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

    from src.models.strategy import SMAParameters

    StrategyRegistry._strategies["sma_crossover"] = StrategyDefinition(
        name="sma_crossover",
        description="Simple Moving Average Crossover",
        strategy_class=mock_strategy_class,
        config_class=mock_config_class,
        param_model=SMAParameters,
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

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_timeout_shows_error_message(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_result = MagicMock()
        mock_load_result.bars = [MagicMock()]
        mock_load_result.instrument = MagicMock()
        mock_load_data.return_value = mock_load_result

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_orchestrator_cls.return_value = mock_orchestrator

        response = client.post(
            "/backtests/run",
            data=self._form_data(timeout_seconds="60"),
        )
        assert response.status_code == 200
        assert "timed out after 60 seconds" in response.text
        assert "Try a shorter date range" in response.text
        mock_orchestrator.dispose.assert_called_once()

    def test_validation_error_preserves_form_data(self, client):
        response = client.post(
            "/backtests/run",
            data=self._form_data(
                symbol="MSFT",
                start_date="2024-12-31",
                end_date="2024-01-01",
                starting_balance="500000",
            ),
        )
        assert response.status_code == 200
        html = response.text
        assert 'value="MSFT"' in html
        assert 'value="500000"' in html

    @patch("src.api.ui.backtests._backtest_lock")
    def test_concurrent_submission_returns_409(self, mock_lock, client):
        """Submitting while a backtest is running returns 409 with message."""
        mock_lock.locked.return_value = True

        response = client.post(
            "/backtests/run",
            data=self._form_data(),
        )
        assert response.status_code == 409
        assert "already in progress" in response.text.lower()

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_post_success_redirects_with_explorer_return(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """Story 3.2 — successful POST appends explorer_return to HX-Redirect URL."""
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "SPY.ARCA"
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
            data=self._form_data(
                symbol="SPY",
                explorer_return="/explorer?catalog=foo&ticker=SPY",
            ),
            follow_redirects=False,
        )

        redirect = response.headers.get("HX-Redirect")
        assert redirect is not None
        assert redirect.startswith(f"/backtests/{run_id}?explorer_return=")
        # URL-encoded value: `/` → `%2F`, `&` → `%26`, `=` → `%3D`.
        assert "%2Fexplorer%3Fcatalog%3Dfoo%26ticker%3DSPY" in redirect

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_post_success_without_explorer_return_unchanged(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """No explorer_return → HX-Redirect has no query string (regression guard)."""
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
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

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_catalog_name_threaded_through_handler(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """catalog_name in form data must be passed to load_backtest_data + from_cli_args."""
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-MINUTE-LAST"
        mock_request.start_date = datetime(2018, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2018, 6, 30, tzinfo=timezone.utc)
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
            data=self._form_data(
                symbol="AAPL",
                start_date="2018-01-01",
                end_date="2018-06-30",
                timeframe="1-MINUTE",
                catalog_name="e2e-test",
            ),
            follow_redirects=False,
        )

        assert response.headers.get("HX-Redirect") == f"/backtests/{run_id}"

        load_kwargs = mock_load_data.call_args.kwargs
        assert load_kwargs["catalog_name"] == "e2e-test"

        req_kwargs = mock_request_cls.from_cli_args.call_args.kwargs
        assert req_kwargs["catalog_name"] == "e2e-test"


class TestErrorSurface:
    """Story 3.3 AC #11 — web UI surfaces data-vs-engine error categorisation
    in inline HTMX error fragments without leaking stack traces or generic
    placeholders.
    """

    def _form_data(self, **overrides) -> dict:
        data = {
            "strategy": "sma_crossover",
            "symbol": "AAPL",
            "start_date": "2018-01-01",
            "end_date": "2018-12-31",
            "data_source": "catalog",
            "timeframe": "1-MINUTE",
            "starting_balance": "1000000",
            "timeout_seconds": "300",
            "catalog_name": "e2e-test",
        }
        data.update(overrides)
        return data

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_missing_ticker_inline_error(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """DataNotFoundError ('Ticker X not found in catalog Y') renders inline."""
        from src.services.exceptions import DataNotFoundError

        mock_request = MagicMock()
        mock_request.instrument_id = "DOES_NOT_EXIST.NASDAQ"
        mock_request.bar_type = "1-MINUTE-LAST"
        mock_request.start_date = datetime(2018, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2018, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_data.side_effect = DataNotFoundError(
            instrument_id="DOES_NOT_EXIST",
            start=datetime(2018, 1, 1, tzinfo=timezone.utc),
            end=datetime(2018, 12, 31, tzinfo=timezone.utc),
            message=(
                "Ticker 'DOES_NOT_EXIST' not found in catalog 'e2e-test'. "
                "Import it via `ntrader data import-csv` or choose a different catalog."
            ),
            context={"missing_from_catalog": "e2e-test"},
        )

        response = client.post(
            "/backtests/run",
            data=self._form_data(symbol="DOES_NOT_EXIST"),
        )

        # Inline error → HTTP 200, not 5xx
        assert response.status_code == 200
        assert "DOES_NOT_EXIST" in response.text
        assert "e2e-test" in response.text
        assert "not found in catalog" in response.text
        # No stack trace leakage
        assert "Traceback" not in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_empty_window_inline_error(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """DataNotFoundError ('No bars... metadata covers...') renders inline."""
        from src.services.exceptions import DataNotFoundError

        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-MINUTE-LAST"
        mock_request.start_date = datetime(2030, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2030, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_data.side_effect = DataNotFoundError(
            instrument_id="AAPL",
            start=datetime(2030, 1, 1, tzinfo=timezone.utc),
            end=datetime(2030, 12, 31, tzinfo=timezone.utc),
            message=(
                "No bars for 'AAPL' in catalog 'e2e-test' between "
                "2030-01-01T00:00:00+00:00 and 2030-12-31T23:59:59+00:00. "
                "Catalog metadata covers 2010-01-04 → 2024-12-31."
            ),
        )

        response = client.post(
            "/backtests/run",
            data=self._form_data(start_date="2030-01-01", end_date="2030-12-31"),
        )

        assert response.status_code == 200
        # Metadata range present (the user-facing AC #8 requirement)
        assert "2010-01-04" in response.text
        assert "2024-12-31" in response.text
        # Requested range present
        assert "2030" in response.text
        assert "Traceback" not in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_unknown_catalog_inline_error(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """UnknownCatalogError 'Unknown catalog X. Available: [...]' renders inline."""
        from src.services.exceptions import UnknownCatalogError

        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NAMED_CATALOG"
        mock_request.bar_type = "1-MINUTE-LAST"
        mock_request.start_date = datetime(2018, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2018, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_data.side_effect = UnknownCatalogError("made-up-name", ["e2e-test", "main"])

        response = client.post(
            "/backtests/run",
            data=self._form_data(catalog_name="made-up-name"),
        )

        assert response.status_code == 200
        assert "Unknown catalog" in response.text
        assert "made-up-name" in response.text
        # Available list surfaces
        assert "e2e-test" in response.text
        assert "Traceback" not in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    def test_strategy_error_inline_error(
        self, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        """ValueError from BacktestOrchestrator.execute renders inline."""
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
        mock_request.bar_type = "1-MINUTE-LAST"
        mock_request.start_date = datetime(2018, 1, 1, tzinfo=timezone.utc)
        mock_request.end_date = datetime(2018, 12, 31, tzinfo=timezone.utc)
        mock_request_cls.from_cli_args.return_value = mock_request

        mock_load_result = MagicMock()
        mock_load_result.bars = [MagicMock()]
        mock_load_result.instrument = MagicMock()
        mock_load_data.return_value = mock_load_result

        mock_orchestrator = MagicMock()
        mock_orchestrator.execute = AsyncMock(
            side_effect=ValueError("Invalid strategy config: fast_period must be > 0")
        )
        mock_orchestrator_cls.return_value = mock_orchestrator

        response = client.post(
            "/backtests/run",
            data=self._form_data(),
        )

        assert response.status_code == 200
        assert "Invalid strategy config" in response.text or "fast_period" in response.text
        # Must NOT be confused for a data-source error (AC #11)
        assert "not found in catalog" not in response.text
        assert "Traceback" not in response.text
        # Generic "backtest failed" placeholder is forbidden
        assert "backtest failed" not in response.text.lower() or (
            "Invalid strategy config" in response.text
        )


class TestBridgePreFill:
    """Story 3.2 — explorer bridge pre-fills for GET /backtests/run."""

    def test_full_bridge_url_prefills_all_fields(self, client):
        response = client.get(
            "/backtests/run?catalog=firstrate-research&ticker=SPY&timeframe=1-DAY"
            "&start=2003-01-02&end=2024-12-31"
            "&explorer_return=%2Fexplorer%3Fcatalog%3Dfirstrate-research"
        )
        assert response.status_code == 200
        html = response.text
        assert 'name="symbol"' in html and 'value="SPY"' in html
        assert 'name="start_date"' in html and 'value="2003-01-02"' in html
        assert 'name="end_date"' in html and 'value="2024-12-31"' in html
        assert '<option value="1-DAY" selected>' in html
        assert 'value="firstrate-research"' in html
        assert 'name="catalog_name"' in html
        # Hidden explorer_return field (URL-decoded once into value attr).
        assert 'name="explorer_return"' in html
        assert "value=&#34;/explorer?catalog=firstrate-research&#34;" in html or (
            'value="/explorer?catalog=firstrate-research"' in html
        )

    def test_invalid_bridge_params_do_not_500(self, client):
        response = client.get(
            "/backtests/run?ticker=<script>alert(1)</script>"
            "&timeframe=DANGEROUS&start=garbage&end=2024-13-99"
        )
        assert response.status_code == 200
        html = response.text
        # XSS: raw <script> must be escaped by Jinja autoescape.
        assert "&lt;script&gt;" in html
        assert "<script>alert(1)" not in html
        # Unknown timeframe silently drops → defaults to 1-DAY.
        assert '<option value="1-DAY" selected>' in html
        # "DANGEROUS" should never be selected.
        assert 'value="DANGEROUS" selected' not in html

    def test_bridge_without_explorer_return(self, client):
        """No explorer_return in URL → the hidden field simply isn't rendered."""
        response = client.get(
            "/backtests/run?catalog=e2e-test&ticker=SPY&timeframe=1-DAY"
            "&start=2024-01-01&end=2024-12-31"
        )
        assert response.status_code == 200
        html = response.text
        # Other bridge params applied, but no explorer_return hidden input.
        assert 'value="SPY"' in html
        assert 'name="explorer_return"' not in html

    def test_unknown_timeframe_falls_back_to_default(self, client):
        response = client.get("/backtests/run?timeframe=99-YEARS")
        assert response.status_code == 200
        assert '<option value="1-DAY" selected>' in response.text


class TestGetRunBacktestFormCatalogPrefill:
    """GET /backtests/run must pre-fill catalog_name from ?catalog= query string."""

    def test_catalog_query_string_is_prefilled(self, client):
        response = client.get("/backtests/run?catalog=e2e-test")
        assert response.status_code == 200
        assert 'value="e2e-test"' in response.text
        assert 'name="catalog_name"' in response.text

    def test_missing_catalog_query_string_has_empty_value(self, client):
        response = client.get("/backtests/run")
        assert response.status_code == 200
        # Catalog field exists (hidden) but empty
        assert 'name="catalog_name"' in response.text


class TestGetStrategyParams:
    """Tests for GET /backtests/run/strategy-params/{strategy_name}."""

    def test_valid_strategy_returns_200(self, client):
        response = client.get("/backtests/run/strategy-params/sma_crossover")
        assert response.status_code == 200

    def test_valid_strategy_contains_param_inputs(self, client):
        response = client.get("/backtests/run/strategy-params/sma_crossover")
        html = response.text
        assert 'name="param_fast_period"' in html
        assert 'name="param_slow_period"' in html

    def test_valid_strategy_has_default_values(self, client):
        response = client.get("/backtests/run/strategy-params/sma_crossover")
        html = response.text
        assert 'value="10"' in html  # fast_period default
        assert 'value="20"' in html  # slow_period default

    def test_unknown_strategy_returns_empty_div(self, client):
        response = client.get("/backtests/run/strategy-params/nonexistent")
        assert response.status_code == 200
        html = response.text.strip()
        assert html == "<div></div>" or html == ""

    def test_param_inputs_have_correct_name_prefix(self, client):
        response = client.get("/backtests/run/strategy-params/sma_crossover")
        html = response.text
        # All parameter inputs should be prefixed with param_
        assert 'name="param_' in html
        # Should not have unprefixed parameter names as input names
        assert 'name="fast_period"' not in html


class TestDefaultCatalogPrefill:
    """The bare run form (no ?catalog=, no explorer bridge) must default to the
    configured DEFAULT_CATALOG_NAME so it routes through the named-catalog loader
    instead of the legacy NAUTILUS_PATH→IBKR path — but only for the catalog
    data source (mock/kraken/ibkr must never be hijacked by a catalog).
    """

    def _form_data(self, **overrides) -> dict:
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

    @patch("src.api.ui.backtests.get_default_catalog", return_value="firstrate-stocks")
    def test_get_bare_form_prefills_default_catalog(self, _mock_default, client):
        response = client.get("/backtests/run")
        assert response.status_code == 200
        # Catalog field is rendered (no longer hidden) with the default value.
        assert 'value="firstrate-stocks"' in response.text

    @patch("src.api.ui.backtests.get_default_catalog", return_value="")
    def test_get_bare_form_no_default_leaves_catalog_empty(self, _mock_default, client):
        response = client.get("/backtests/run")
        assert response.status_code == 200
        assert 'value="firstrate-stocks"' not in response.text

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    @patch("src.api.ui.backtests.get_default_catalog", return_value="firstrate-stocks")
    def test_post_catalog_source_defaults_catalog_name(
        self, _mock_default, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
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
            data=self._form_data(),  # data_source=catalog, no catalog_name
            follow_redirects=False,
        )

        assert response.headers.get("HX-Redirect") == f"/backtests/{run_id}"
        assert mock_load_data.call_args.kwargs["catalog_name"] == "firstrate-stocks"
        assert mock_request_cls.from_cli_args.call_args.kwargs["catalog_name"] == "firstrate-stocks"

    @patch("src.api.ui.backtests.BacktestOrchestrator")
    @patch("src.api.ui.backtests.load_backtest_data")
    @patch("src.api.ui.backtests.BacktestRequest")
    @patch("src.api.ui.backtests.get_default_catalog", return_value="firstrate-stocks")
    def test_post_mock_source_never_uses_catalog(
        self, _mock_default, mock_request_cls, mock_load_data, mock_orchestrator_cls, client
    ):
        run_id = uuid4()
        mock_request = MagicMock()
        mock_request.instrument_id = "AAPL.NASDAQ"
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
            data=self._form_data(data_source="mock"),  # default must NOT leak in
            follow_redirects=False,
        )

        assert response.headers.get("HX-Redirect") == f"/backtests/{run_id}"
        assert mock_load_data.call_args.kwargs["catalog_name"] is None
        assert mock_request_cls.from_cli_args.call_args.kwargs["catalog_name"] is None
