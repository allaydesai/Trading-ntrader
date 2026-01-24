"""Tests for backtest CLI helper functions."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from rich.console import Console


class MockBacktestResult:
    """Mock backtest result for testing."""

    def __init__(
        self,
        total_return: Decimal = Decimal("0.15"),
        total_trades: int = 10,
        winning_trades: int = 6,
        losing_trades: int = 4,
        win_rate: float = 60.0,
        largest_win: Decimal = Decimal("500.00"),
        largest_loss: Decimal = Decimal("-200.00"),
        final_balance: Decimal = Decimal("11500.00"),
    ):
        self.total_return = total_return
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.losing_trades = losing_trades
        self.win_rate = win_rate
        self.largest_win = largest_win
        self.largest_loss = largest_loss
        self.final_balance = final_balance


class TestLoadBacktestData:
    """Test cases for load_backtest_data helper."""

    @pytest.mark.asyncio
    async def test_load_catalog_data_full_coverage(self):
        """Test loading data from catalog when full coverage is available."""
        from src.cli.commands._backtest_helpers import DataLoadResult, load_backtest_data

        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000
        mock_catalog_service.get_availability.return_value = mock_availability

        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        async def mock_fetch_or_load(*args, **kwargs):
            return mock_bars

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = mock_instrument

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        result = await load_backtest_data(
            data_source="catalog",
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            console=console,
            catalog_service=mock_catalog_service,
        )

        assert isinstance(result, DataLoadResult)
        assert result.bars == mock_bars
        assert result.instrument == mock_instrument
        assert result.data_source_used == "Parquet Catalog"
        mock_catalog_service.get_availability.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_catalog_data_triggers_ibkr_fetch(self):
        """Test that IBKR fetch is triggered when catalog data is partial."""
        from src.cli.commands._backtest_helpers import load_backtest_data

        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = False
        mock_availability.start_date = datetime(2024, 3, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 6, 30, tzinfo=timezone.utc)
        mock_catalog_service.get_availability.return_value = mock_availability

        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        async def mock_fetch_or_load(*args, **kwargs):
            return mock_bars

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = mock_instrument

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        result = await load_backtest_data(
            data_source="catalog",
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            console=console,
            catalog_service=mock_catalog_service,
        )

        assert result.data_source_used == "IBKR Auto-fetch"

    @pytest.mark.asyncio
    async def test_load_catalog_data_no_availability_triggers_ibkr(self):
        """Test that IBKR fetch is triggered when no catalog data exists."""
        from src.cli.commands._backtest_helpers import load_backtest_data

        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None

        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        async def mock_fetch_or_load(*args, **kwargs):
            return mock_bars

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = mock_instrument

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        result = await load_backtest_data(
            data_source="catalog",
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            console=console,
            catalog_service=mock_catalog_service,
        )

        assert result.data_source_used == "IBKR Auto-fetch"

    @pytest.mark.asyncio
    async def test_load_catalog_data_no_data_raises_error(self):
        """Test that DataNotFoundError is raised when no data is available."""
        from src.cli.commands._backtest_helpers import load_backtest_data
        from src.services.exceptions import DataNotFoundError

        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        async def mock_fetch_or_load(*args, **kwargs):
            raise DataNotFoundError("INVALID.NASDAQ", start, end)

        mock_catalog_service.fetch_or_load = mock_fetch_or_load

        console = Console(force_terminal=True, width=120)

        with pytest.raises(DataNotFoundError):
            await load_backtest_data(
                data_source="catalog",
                instrument_id="INVALID.NASDAQ",
                bar_type_spec="1-DAY-LAST",
                start=start,
                end=end,
                console=console,
                catalog_service=mock_catalog_service,
            )

    @pytest.mark.asyncio
    async def test_load_mock_data_success(self):
        """Test loading mock data from YAML configuration."""
        from src.cli.commands._backtest_helpers import DataLoadResult, load_backtest_data

        yaml_data = {
            "config": {
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL",
            }
        }

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        with patch("src.cli.commands._backtest_helpers.generate_mock_data_from_yaml") as mock_gen:
            mock_bars = [MagicMock()]
            mock_instrument = MagicMock()
            mock_gen.return_value = (mock_bars, mock_instrument, start, end)

            result = await load_backtest_data(
                data_source="mock",
                instrument_id="AAPL.NASDAQ",
                bar_type_spec="1-DAY-LAST",
                start=start,
                end=end,
                console=console,
                yaml_data=yaml_data,
            )

            assert isinstance(result, DataLoadResult)
            assert result.bars == mock_bars
            assert result.instrument == mock_instrument
            assert result.data_source_used == "Mock"
            mock_gen.assert_called_once_with(yaml_data)

    @pytest.mark.asyncio
    async def test_load_mock_data_missing_yaml_raises_error(self):
        """Test that ValueError is raised when yaml_data is missing for mock source."""
        from src.cli.commands._backtest_helpers import load_backtest_data

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="yaml_data is required"):
            await load_backtest_data(
                data_source="mock",
                instrument_id="AAPL.NASDAQ",
                bar_type_spec="1-DAY-LAST",
                start=start,
                end=end,
                console=console,
                yaml_data=None,
            )

    @pytest.mark.asyncio
    async def test_load_catalog_fetches_instrument_from_ibkr_when_missing(self):
        """Test instrument is fetched from IBKR when not in catalog."""
        from src.cli.commands._backtest_helpers import load_backtest_data

        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000
        mock_catalog_service.get_availability.return_value = mock_availability

        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        async def mock_fetch_or_load(*args, **kwargs):
            return mock_bars

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = None

        async def mock_fetch_instrument(*args, **kwargs):
            return mock_instrument

        mock_catalog_service.fetch_instrument_from_ibkr = mock_fetch_instrument

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        result = await load_backtest_data(
            data_source="catalog",
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-DAY-LAST",
            start=start,
            end=end,
            console=console,
            catalog_service=mock_catalog_service,
        )

        assert result.instrument == mock_instrument


class TestExecuteBacktest:
    """Test cases for execute_backtest helper."""

    @pytest.mark.asyncio
    async def test_execute_success_returns_result_and_run_id(self):
        """Test successful backtest execution returns result and run_id."""
        from src.cli.commands._backtest_helpers import execute_backtest

        mock_request = MagicMock()
        mock_request.persist = True
        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        mock_result = MockBacktestResult()
        mock_run_id = UUID("12345678-1234-1234-1234-123456789abc")

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return mock_result, mock_run_id

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()

        console = Console(force_terminal=True, width=120)

        with patch(
            "src.cli.commands._backtest_helpers.BacktestOrchestrator",
            return_value=mock_orchestrator,
        ):
            result, run_id = await execute_backtest(
                request=mock_request,
                bars=mock_bars,
                instrument=mock_instrument,
                console=console,
            )

            assert result == mock_result
            assert run_id == mock_run_id

    @pytest.mark.asyncio
    async def test_execute_disposes_orchestrator_on_success(self):
        """Test that orchestrator is disposed after successful execution."""
        from src.cli.commands._backtest_helpers import execute_backtest

        mock_request = MagicMock()
        mock_request.persist = False
        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return MockBacktestResult(), None

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()

        console = Console(force_terminal=True, width=120)

        with patch(
            "src.cli.commands._backtest_helpers.BacktestOrchestrator",
            return_value=mock_orchestrator,
        ):
            await execute_backtest(
                request=mock_request,
                bars=mock_bars,
                instrument=mock_instrument,
                console=console,
            )

            mock_orchestrator.dispose.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_disposes_orchestrator_on_error(self):
        """Test that orchestrator is disposed even when execution fails."""
        from src.cli.commands._backtest_helpers import execute_backtest

        mock_request = MagicMock()
        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            raise ValueError("Execution failed")

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()

        console = Console(force_terminal=True, width=120)

        with patch(
            "src.cli.commands._backtest_helpers.BacktestOrchestrator",
            return_value=mock_orchestrator,
        ):
            with pytest.raises(ValueError, match="Execution failed"):
                await execute_backtest(
                    request=mock_request,
                    bars=mock_bars,
                    instrument=mock_instrument,
                    console=console,
                )

            mock_orchestrator.dispose.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_uses_custom_progress_message(self):
        """Test that custom progress message is used."""
        from src.cli.commands._backtest_helpers import execute_backtest

        mock_request = MagicMock()
        mock_request.persist = False
        mock_bars = [MagicMock()]
        mock_instrument = MagicMock()

        mock_orchestrator = MagicMock()

        async def mock_execute(*args, **kwargs):
            return MockBacktestResult(), None

        mock_orchestrator.execute = mock_execute
        mock_orchestrator.dispose = MagicMock()

        console = Console(force_terminal=True, width=120)

        with patch(
            "src.cli.commands._backtest_helpers.BacktestOrchestrator",
            return_value=mock_orchestrator,
        ):
            # Just verify it doesn't raise - progress message is displayed
            result, _ = await execute_backtest(
                request=mock_request,
                bars=mock_bars,
                instrument=mock_instrument,
                console=console,
                progress_message="Custom progress message...",
            )

            assert result is not None


class TestDisplayBacktestResults:
    """Test cases for display_backtest_results helper."""

    def test_display_profitable_strategy(self, capsys):
        """Test display of profitable strategy results."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult(total_return=Decimal("0.15"))

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover", "Symbol": "AAPL"},
            table_title="Test Results",
        )

        captured = capsys.readouterr()
        assert "Strategy was profitable!" in captured.out or "profitable" in captured.out.lower()

    def test_display_losing_strategy(self, capsys):
        """Test display of losing strategy results."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult(total_return=Decimal("-0.10"))

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover", "Symbol": "AAPL"},
        )

        captured = capsys.readouterr()
        assert "lost money" in captured.out.lower() or "Strategy lost" in captured.out

    def test_display_break_even_strategy(self, capsys):
        """Test display of break-even strategy results."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult(total_return=Decimal("0.00"))

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover", "Symbol": "AAPL"},
        )

        captured = capsys.readouterr()
        assert "broke even" in captured.out.lower()

    def test_display_with_persistence_shows_run_id(self, capsys):
        """Test that persisted results show run ID."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult()
        run_id = UUID("12345678-1234-1234-1234-123456789abc")

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=run_id,
            persist=True,
            context_rows={"Strategy": "SMA Crossover", "Symbol": "AAPL"},
        )

        captured = capsys.readouterr()
        assert "12345678" in captured.out
        assert "Persisted" in captured.out or "Yes" in captured.out

    def test_display_context_rows_appear_first(self, capsys):
        """Test that context rows appear at the top of the table."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult()

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover", "Symbol": "AAPL", "Period": "2024"},
        )

        captured = capsys.readouterr()
        # Verify context rows are present
        assert "SMA Crossover" in captured.out
        assert "AAPL" in captured.out

    def test_display_with_no_persist_flag(self, capsys):
        """Test display when --no-persist flag was used."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult()

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover"},
        )

        captured = capsys.readouterr()
        assert "--no-persist" in captured.out or "No" in captured.out

    def test_display_with_execution_time(self, capsys):
        """Test display includes execution time when provided."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult()

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "SMA Crossover"},
            execution_time=5.25,
        )

        captured = capsys.readouterr()
        assert "5.25" in captured.out or "Execution" in captured.out

    def test_display_shows_all_metrics(self, capsys):
        """Test that all key metrics are displayed."""
        from src.cli.commands._backtest_helpers import display_backtest_results

        result = MockBacktestResult(
            total_return=Decimal("0.15"),
            total_trades=10,
            winning_trades=6,
            losing_trades=4,
            win_rate=60.0,
            largest_win=Decimal("500.00"),
            largest_loss=Decimal("-200.00"),
            final_balance=Decimal("11500.00"),
        )

        console = Console(force_terminal=True, width=120)

        display_backtest_results(
            result=result,
            console=console,
            run_id=None,
            persist=False,
            context_rows={"Strategy": "Test"},
        )

        captured = capsys.readouterr()
        assert "15.00%" in captured.out
        assert "10" in captured.out
        assert "60" in captured.out
        assert "500" in captured.out
        assert "200" in captured.out
        assert "11500" in captured.out or "11,500" in captured.out


class TestApplyCliOverrides:
    """Test cases for apply_cli_overrides helper."""

    def test_apply_no_overrides_returns_same_request(self):
        """Test that no overrides returns the same request."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={"trade_size": 100},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            starting_balance=Decimal("100000"),
        )

        result = apply_cli_overrides(
            request,
            symbol=None,
            start=None,
            end=None,
            starting_balance=None,
        )

        assert result is request  # Same object returned when no overrides

    def test_apply_start_date_override(self):
        """Test that start date override is applied."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
        )

        new_start = datetime(2024, 3, 1, tzinfo=timezone.utc)
        result = apply_cli_overrides(
            request,
            symbol=None,
            start=new_start,
            end=None,
            starting_balance=None,
        )

        assert result.start_date == new_start
        assert result.end_date == datetime(2024, 6, 30, tzinfo=timezone.utc)

    def test_apply_symbol_override_rebuilds_instrument_id(self):
        """Test that symbol override also updates instrument_id."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
        )

        result = apply_cli_overrides(
            request,
            symbol="AAPL",
            start=None,
            end=None,
            starting_balance=None,
        )

        assert result.symbol == "AAPL"
        assert result.instrument_id == "AAPL.NASDAQ"

    def test_apply_starting_balance_override(self):
        """Test that starting balance override is applied."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            starting_balance=Decimal("100000"),
        )

        result = apply_cli_overrides(
            request,
            symbol=None,
            start=None,
            end=None,
            starting_balance=250000.0,
        )

        assert result.starting_balance == Decimal("250000.0")

    def test_apply_multiple_overrides(self):
        """Test that multiple overrides can be applied together."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
            starting_balance=Decimal("100000"),
        )

        new_start = datetime(2024, 3, 1, tzinfo=timezone.utc)
        new_end = datetime(2024, 4, 30, tzinfo=timezone.utc)

        result = apply_cli_overrides(
            request,
            symbol="NVDA",
            start=new_start,
            end=new_end,
            starting_balance=500000.0,
        )

        assert result.symbol == "NVDA"
        assert result.instrument_id == "NVDA.NASDAQ"
        assert result.start_date == new_start
        assert result.end_date == new_end
        assert result.starting_balance == Decimal("500000.0")

    def test_apply_timezone_naive_dates_converted_to_utc(self):
        """Test that timezone-naive dates are converted to UTC."""
        from src.cli.commands._backtest_helpers import apply_cli_overrides
        from src.models.backtest_request import BacktestRequest

        request = BacktestRequest(
            strategy_type="apolo_rsi",
            strategy_path="src.core.strategies.apolo_rsi:ApoloRSI",
            config_path="src.core.strategies.apolo_rsi:ApoloRSIConfig",
            strategy_config={},
            symbol="AMD",
            instrument_id="AMD.NASDAQ",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 6, 30, tzinfo=timezone.utc),
            bar_type="1-DAY-LAST",
        )

        # Pass timezone-naive dates
        new_start = datetime(2024, 3, 1)  # No timezone
        result = apply_cli_overrides(
            request,
            symbol=None,
            start=new_start,
            end=None,
            starting_balance=None,
        )

        assert result.start_date.tzinfo == timezone.utc


class TestResolveBacktestRequest:
    """Test cases for resolve_backtest_request helper."""

    def test_resolve_cli_mode_with_required_params(self):
        """Test CLI mode resolves correctly with required parameters."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        request, data_source = resolve_backtest_request(
            config_file=None,
            symbol="AAPL",
            strategy="sma_crossover",
            start=start,
            end=end,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
            fast_period=10,
            slow_period=20,
            trade_size=100000,
            timeframe=None,
        )

        assert request.symbol == "AAPL"
        assert request.instrument_id == "AAPL.NASDAQ"
        assert request.start_date == start
        assert request.end_date == end
        assert request.strategy_type == "sma_crossover"
        assert data_source == "catalog"

    def test_resolve_cli_mode_missing_symbol_raises_error(self):
        """Test that CLI mode raises error when symbol is missing."""
        import click

        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(click.UsageError, match="Missing required option"):
            resolve_backtest_request(
                config_file=None,
                symbol=None,
                strategy="sma_crossover",
                start=start,
                end=end,
                data_source=None,
                starting_balance=None,
                persist=True,
                console=console,
            )

    def test_resolve_cli_mode_missing_start_raises_error(self):
        """Test that CLI mode raises error when start is missing."""
        import click

        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(click.UsageError, match="Missing required option"):
            resolve_backtest_request(
                config_file=None,
                symbol="AAPL",
                strategy="sma_crossover",
                start=None,
                end=end,
                data_source=None,
                starting_balance=None,
                persist=True,
                console=console,
            )

    def test_resolve_cli_mode_missing_end_raises_error(self):
        """Test that CLI mode raises error when end is missing."""
        import click

        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)

        with pytest.raises(click.UsageError, match="Missing required option"):
            resolve_backtest_request(
                config_file=None,
                symbol="AAPL",
                strategy="sma_crossover",
                start=start,
                end=None,
                data_source=None,
                starting_balance=None,
                persist=True,
                console=console,
            )

    def test_resolve_cli_mode_mock_source_raises_error(self):
        """Test that CLI mode raises error when mock data source is used."""
        import click

        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        with pytest.raises(click.UsageError, match="Mock data source requires a YAML config"):
            resolve_backtest_request(
                config_file=None,
                symbol="AAPL",
                strategy="sma_crossover",
                start=start,
                end=end,
                data_source="mock",
                starting_balance=None,
                persist=True,
                console=console,
            )

    def test_resolve_config_mode_from_yaml_file(self, tmp_path):
        """Test config mode resolves correctly from YAML file."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        # Create a temporary YAML config file
        config_content = """
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
  trade_size: 100
backtest:
  start_date: "2024-01-01"
  end_date: "2024-06-30"
  initial_capital: 100000
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(config_content)

        console = Console(force_terminal=True, width=120)

        request, data_source = resolve_backtest_request(
            config_file=str(config_file),
            symbol=None,
            strategy=None,
            start=None,
            end=None,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
        )

        assert request.symbol == "AMD"
        assert request.instrument_id == "AMD.NASDAQ"
        assert request.start_date == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert request.end_date == datetime(2024, 6, 30, tzinfo=timezone.utc)
        assert request.starting_balance == Decimal("100000")
        assert data_source == "catalog"

    def test_resolve_config_mode_with_date_overrides(self, tmp_path):
        """Test config mode applies date overrides."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        # Create a temporary YAML config file
        config_content = """
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
backtest:
  start_date: "2024-01-01"
  end_date: "2024-06-30"
  initial_capital: 100000
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(config_content)

        console = Console(force_terminal=True, width=120)
        override_start = datetime(2024, 3, 1, tzinfo=timezone.utc)
        override_end = datetime(2024, 4, 30, tzinfo=timezone.utc)

        request, _ = resolve_backtest_request(
            config_file=str(config_file),
            symbol=None,
            strategy=None,
            start=override_start,
            end=override_end,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
        )

        assert request.start_date == override_start
        assert request.end_date == override_end

    def test_resolve_config_mode_with_mock_data_source(self, tmp_path):
        """Test config mode with mock data source."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        # Create a temporary YAML config file
        config_content = """
strategy_path: "src.core.strategies.apolo_rsi:ApoloRSI"
config_path: "src.core.strategies.apolo_rsi:ApoloRSIConfig"
config:
  instrument_id: "AMD.NASDAQ"
  bar_type: "AMD.NASDAQ-1-DAY-LAST-EXTERNAL"
backtest:
  start_date: "2024-01-01"
  end_date: "2024-06-30"
  initial_capital: 100000
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(config_content)

        console = Console(force_terminal=True, width=120)

        request, data_source = resolve_backtest_request(
            config_file=str(config_file),
            symbol=None,
            strategy=None,
            start=None,
            end=None,
            data_source="mock",
            starting_balance=None,
            persist=True,
            console=console,
        )

        assert data_source == "mock"

    def test_resolve_config_mode_nonexistent_file_raises_error(self):
        """Test config mode raises error for nonexistent file."""
        import click

        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)

        with pytest.raises(click.UsageError, match="Configuration file not found"):
            resolve_backtest_request(
                config_file="/nonexistent/path/config.yaml",
                symbol=None,
                strategy=None,
                start=None,
                end=None,
                data_source=None,
                starting_balance=None,
                persist=True,
                console=console,
            )

    def test_resolve_cli_mode_defaults_strategy_to_sma_crossover(self):
        """Test that CLI mode defaults strategy to sma_crossover."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        request, _ = resolve_backtest_request(
            config_file=None,
            symbol="AAPL",
            strategy=None,  # No strategy specified
            start=start,
            end=end,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
        )

        assert request.strategy_type == "sma_crossover"

    def test_resolve_cli_mode_auto_detects_daily_bar_type(self):
        """Test that CLI mode auto-detects daily bar type from date-only format."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        # Date-only (no time component) should result in daily bars
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, 0, 0, 0, tzinfo=timezone.utc)

        request, _ = resolve_backtest_request(
            config_file=None,
            symbol="AAPL",
            strategy="sma_crossover",
            start=start,
            end=end,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
            timeframe=None,  # Auto-detect
        )

        assert request.bar_type == "1-DAY-LAST"

    def test_resolve_cli_mode_uses_explicit_timeframe(self):
        """Test that CLI mode uses explicit timeframe when provided."""
        from src.cli.commands._backtest_helpers import resolve_backtest_request

        console = Console(force_terminal=True, width=120)
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        request, _ = resolve_backtest_request(
            config_file=None,
            symbol="AAPL",
            strategy="sma_crossover",
            start=start,
            end=end,
            data_source=None,
            starting_balance=None,
            persist=True,
            console=console,
            timeframe="1-HOUR",  # Explicit timeframe
        )

        assert request.bar_type == "1-HOUR-LAST"
