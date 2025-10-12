"""Tests for IBKR data CLI commands."""

from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner

from src.cli.commands.data import data


def test_data_connect_success():
    """Test data connect command with successful connection."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(
            return_value={
                "connected": True,
                "account_id": "DU123456",
                "server_version": 176,
                "connection_time": "2024-01-15 10:30:00",
            }
        )
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(data, ["connect"])

        assert result.exit_code == 0
        assert "✅ Successfully connected to Interactive Brokers" in result.output
        assert "DU123456" in result.output
        mock_client.connect.assert_called_once()
        mock_client.disconnect.assert_called_once()


def test_data_connect_with_custom_host_port():
    """Test data connect command with custom host and port."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(
            return_value={
                "connected": True,
                "account_id": "DU123456",
                "server_version": 176,
                "connection_time": "2024-01-15 10:30:00",
            }
        )
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            data, ["connect", "--host", "192.168.1.100", "--port", "4002"]
        )

        assert result.exit_code == 0
        # Verify the client was instantiated with correct parameters
        # Note: market_data_type and client_id come from IBKRSettings
        call_args = mock_client_class.call_args
        assert call_args.kwargs["host"] == "192.168.1.100"
        assert call_args.kwargs["port"] == 4002
        # client_id comes from settings (.env or default), just verify it was passed
        assert "client_id" in call_args.kwargs
        assert isinstance(call_args.kwargs["client_id"], int)


def test_data_connect_connection_failure():
    """Test data connect command handles connection failure."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(
            side_effect=ConnectionError("TWS/Gateway not running")
        )
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(data, ["connect"])

        assert result.exit_code == 1
        assert "❌ Connection failed" in result.output
        assert "TWS/Gateway not running" in result.output


def test_data_connect_timeout():
    """Test data connect command handles timeout."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(data, ["connect"])

        assert result.exit_code == 1
        assert "❌ Connection failed" in result.output
        assert "Connection timeout" in result.output


def test_data_connect_shows_troubleshooting_hints():
    """Test data connect shows troubleshooting hints on failure."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(data, ["connect"])

        assert result.exit_code == 1
        assert "Troubleshooting:" in result.output
        assert "TWS or IB Gateway is running" in result.output


def test_data_fetch_success():
    """Test data fetch command with successful fetch."""
    runner = CliRunner()

    with (
        patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class,
        patch("src.cli.commands.data.HistoricalDataFetcher") as mock_fetcher_class,
    ):
        # Mock client
        mock_client = Mock()
        mock_client.connect = AsyncMock(return_value={"connected": True})
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        # Mock fetcher
        mock_fetcher = Mock()
        mock_bar = Mock()
        mock_bar.ts_event = 1704067200000000000  # 2024-01-01
        mock_bar.open.as_double.return_value = 150.50
        mock_bar.high.as_double.return_value = 155.00
        mock_bar.low.as_double.return_value = 149.00
        mock_bar.close.as_double.return_value = 154.00
        mock_bar.volume.as_double.return_value = 1000000

        mock_fetcher.fetch_bars = AsyncMock(return_value=[mock_bar])
        mock_fetcher_class.return_value = mock_fetcher

        result = runner.invoke(
            data,
            [
                "fetch",
                "--instruments",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
            ],
        )

        assert result.exit_code == 0
        assert "✅ Successfully fetched" in result.output
        assert "1 bars" in result.output
        mock_fetcher.fetch_bars.assert_called_once()


def test_data_fetch_multiple_instruments():
    """Test data fetch command with multiple instruments."""
    runner = CliRunner()

    with (
        patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class,
        patch("src.cli.commands.data.HistoricalDataFetcher") as mock_fetcher_class,
    ):
        mock_client = Mock()
        mock_client.connect = AsyncMock(return_value={"connected": True})
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_fetcher = Mock()
        mock_fetcher.fetch_bars = AsyncMock(return_value=[Mock(), Mock()])
        mock_fetcher_class.return_value = mock_fetcher

        result = runner.invoke(
            data,
            [
                "fetch",
                "--instruments",
                "AAPL,MSFT,GOOGL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
            ],
        )

        assert result.exit_code == 0
        # Should be called 3 times (once per instrument)
        assert mock_fetcher.fetch_bars.call_count == 3


def test_data_fetch_with_custom_timeframe():
    """Test data fetch command with custom timeframe."""
    runner = CliRunner()

    with (
        patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class,
        patch("src.cli.commands.data.HistoricalDataFetcher") as mock_fetcher_class,
    ):
        mock_client = Mock()
        mock_client.connect = AsyncMock(return_value={"connected": True})
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_fetcher = Mock()
        mock_fetcher.fetch_bars = AsyncMock(return_value=[])
        mock_fetcher_class.return_value = mock_fetcher

        result = runner.invoke(
            data,
            [
                "fetch",
                "--instruments",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--timeframe",
                "1-MINUTE",
            ],
        )

        assert result.exit_code == 0
        # Verify fetch_bars was called with correct bar spec
        call_args = mock_fetcher.fetch_bars.call_args
        assert "1-MINUTE-LAST" in call_args.kwargs["bar_specifications"]


def test_data_fetch_connection_failure():
    """Test data fetch command handles connection failure."""
    runner = CliRunner()

    with patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class:
        mock_client = Mock()
        mock_client.connect = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        result = runner.invoke(
            data,
            [
                "fetch",
                "--instruments",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
            ],
        )

        assert result.exit_code == 1
        assert "❌" in result.output


def test_data_fetch_invalid_date_format():
    """Test data fetch command handles invalid date format."""
    runner = CliRunner()

    result = runner.invoke(
        data,
        [
            "fetch",
            "--instruments",
            "AAPL",
            "--start",
            "invalid-date",
            "--end",
            "2024-01-31",
        ],
    )

    assert result.exit_code == 2  # Click validation error


def test_data_fetch_missing_required_parameters():
    """Test data fetch command requires instruments, start, and end."""
    runner = CliRunner()

    # Missing instruments
    result = runner.invoke(
        data, ["fetch", "--start", "2024-01-01", "--end", "2024-01-31"]
    )
    assert result.exit_code == 2
    assert "Missing option '--instruments'" in result.output

    # Missing start
    result = runner.invoke(
        data, ["fetch", "--instruments", "AAPL", "--end", "2024-01-31"]
    )
    assert result.exit_code == 2

    # Missing end
    result = runner.invoke(
        data, ["fetch", "--instruments", "AAPL", "--start", "2024-01-01"]
    )
    assert result.exit_code == 2


def test_data_fetch_shows_progress():
    """Test data fetch command shows progress indicators."""
    runner = CliRunner()

    with (
        patch("src.cli.commands.data.IBKRHistoricalClient") as mock_client_class,
        patch("src.cli.commands.data.HistoricalDataFetcher") as mock_fetcher_class,
        patch("src.cli.commands.data.Progress") as mock_progress_class,
    ):
        mock_client = Mock()
        mock_client.connect = AsyncMock(return_value={"connected": True})
        mock_client.disconnect = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_fetcher = Mock()
        mock_fetcher.fetch_bars = AsyncMock(return_value=[Mock()])
        mock_fetcher_class.return_value = mock_fetcher

        mock_progress = Mock()
        mock_progress.add_task.return_value = 1
        mock_progress_class.return_value.__enter__.return_value = mock_progress

        result = runner.invoke(
            data,
            [
                "fetch",
                "--instruments",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
            ],
        )

        assert result.exit_code == 0
        # Verify progress was used
        mock_progress.add_task.assert_called()


def test_data_connect_help():
    """Test data connect command help message."""
    runner = CliRunner()
    result = runner.invoke(data, ["connect", "--help"])

    assert result.exit_code == 0
    assert "Test connection to Interactive Brokers" in result.output
    assert "--host" in result.output
    assert "--port" in result.output


def test_data_fetch_help():
    """Test data fetch command help message."""
    runner = CliRunner()
    result = runner.invoke(data, ["fetch", "--help"])

    assert result.exit_code == 0
    assert "Fetch historical data from Interactive Brokers" in result.output
    assert "--instruments" in result.output
    assert "--start" in result.output
    assert "--end" in result.output
    assert "--timeframe" in result.output
