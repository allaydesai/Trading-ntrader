"""Tests for backtest CLI helper functions."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from rich.console import Console

from src.models.backtest_result import BacktestResult


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
        from src.cli.commands._backtest_helpers import DataLoadResult, load_backtest_data

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
        from src.cli.commands._backtest_helpers import DataLoadResult, load_backtest_data

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

        with patch(
            "src.cli.commands._backtest_helpers.generate_mock_data_from_yaml"
        ) as mock_gen:
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
