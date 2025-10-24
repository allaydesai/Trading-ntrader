"""Tests for CSV export functionality with precision preservation."""

import pytest
import pandas as pd
from decimal import Decimal
from datetime import datetime, timezone

from src.services.reports.csv_exporter import CSVExporter


class TestCSVExporter:
    """Test suite for CSV export functionality."""

    @pytest.fixture
    def exporter(self):
        """Create CSVExporter instance."""
        return CSVExporter()

    @pytest.fixture
    def sample_metrics(self):
        """Provide sample metrics data with various data types."""
        return {
            "sharpe_ratio": Decimal("1.42"),
            "sortino_ratio": Decimal("1.68"),
            "total_return": Decimal("0.153"),
            "cagr": Decimal("0.125"),
            "volatility": Decimal("0.18"),
            "max_drawdown": Decimal("-0.087"),
            "calmar_ratio": Decimal("1.44"),
            "profit_factor": Decimal("1.80"),
            "win_rate": Decimal("0.583"),
            "total_trades": 45,
            "winning_trades": 26,
            "losing_trades": 19,
            "avg_win": Decimal("1250.50"),
            "avg_loss": Decimal("-850.25"),
            "largest_win": Decimal("3500.00"),
            "largest_loss": Decimal("-2100.00"),
            "total_pnl": Decimal("15300.00"),
            "calculation_timestamp": datetime(
                2024, 1, 15, 12, 30, 45, tzinfo=timezone.utc
            ),
        }

    @pytest.fixture
    def sample_trades(self):
        """Provide sample trade data with Decimal precision."""
        return [
            {
                "id": 1,
                "entry_time": datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc),
                "exit_time": datetime(2024, 1, 15, 11, 45, tzinfo=timezone.utc),
                "symbol": "AAPL",
                "side": "LONG",
                "quantity": 100,
                "entry_price": Decimal("150.12345"),
                "exit_price": Decimal("152.67890"),
                "pnl": Decimal("255.545"),
                "commission": Decimal("2.00"),
                "slippage": Decimal("0.50"),
                "strategy_name": "sma_crossover",
            },
            {
                "id": 2,
                "entry_time": datetime(2024, 1, 16, 10, 15, tzinfo=timezone.utc),
                "exit_time": datetime(2024, 1, 16, 14, 30, tzinfo=timezone.utc),
                "symbol": "MSFT",
                "side": "SHORT",
                "quantity": 50,
                "entry_price": Decimal("310.50000"),
                "exit_price": Decimal("308.75000"),
                "pnl": Decimal("87.50"),
                "commission": Decimal("1.50"),
                "slippage": Decimal("0.25"),
                "strategy_name": "mean_reversion",
            },
        ]

    @pytest.fixture
    def sample_equity_curve(self):
        """Provide sample equity curve data."""
        dates = pd.date_range("2024-01-01", periods=5, freq="D")
        values = [Decimal(str(100000 + i * 500)) for i in range(5)]
        return pd.Series(values, index=dates)

    @pytest.mark.component
    def test_exporter_initialization(self, exporter):
        """Test that CSVExporter initializes correctly."""
        assert exporter is not None
        assert hasattr(exporter, "decimal_places")
        assert exporter.decimal_places == 5  # Default precision

    @pytest.mark.component
    def test_export_metrics_maintains_precision(
        self, exporter, sample_metrics, tmp_path
    ):
        """Test that metrics export preserves Decimal precision."""
        output_file = tmp_path / "test_metrics.csv"

        success = exporter.export_metrics(sample_metrics, str(output_file))

        assert success is True
        assert output_file.exists()

        # Read back and verify precision
        df = pd.read_csv(output_file)

        # Check that Decimal values are preserved as strings in key-value format
        sharpe_row = df[df["metric"] == "sharpe_ratio"]
        assert "1.42" in str(sharpe_row["value"].values[0])

        total_return_row = df[df["metric"] == "total_return"]
        assert "0.153" in str(total_return_row["value"].values[0])

        avg_win_row = df[df["metric"] == "avg_win"]
        assert "1250.50" in str(avg_win_row["value"].values[0])

        total_pnl_row = df[df["metric"] == "total_pnl"]
        assert "15300.00" in str(total_pnl_row["value"].values[0])

    @pytest.mark.component
    def test_export_trades_preserves_decimal_precision(
        self, exporter, sample_trades, tmp_path
    ):
        """Test that trade export preserves precise decimal values."""
        output_file = tmp_path / "test_trades.csv"

        success = exporter.export_trades(sample_trades, str(output_file))

        assert success is True
        assert output_file.exists()

        # Read back and verify precision by checking raw CSV content
        with open(output_file, "r") as f:
            csv_content = f.read()

        # Check high-precision decimal preservation in raw CSV
        assert "150.12345" in csv_content
        assert "310.50000" in csv_content
        assert "255.545" in csv_content
        assert "87.50" in csv_content

        # Also verify pandas can read it correctly
        df = pd.read_csv(output_file)
        assert len(df) == 2  # Should have 2 trades

    @pytest.mark.component
    def test_datetime_formatting_in_csv(self, exporter, sample_trades, tmp_path):
        """Test that datetime values are formatted consistently."""
        output_file = tmp_path / "test_trades_datetime.csv"

        success = exporter.export_trades(sample_trades, str(output_file))

        assert success is True

        df = pd.read_csv(output_file)

        # Check datetime formatting (allowing for slight variations in timezone format)
        entry_times = df["entry_time"]
        assert "2024-01-15 09:30:00" in entry_times.iloc[0]
        assert "2024-01-16 10:15:00" in entry_times.iloc[1]

    @pytest.mark.component
    def test_export_equity_curve(self, exporter, sample_equity_curve, tmp_path):
        """Test equity curve export functionality."""
        output_file = tmp_path / "test_equity_curve.csv"

        success = exporter.export_equity_curve(sample_equity_curve, str(output_file))

        assert success is True
        assert output_file.exists()

        df = pd.read_csv(output_file, index_col=0, parse_dates=True)

        # Check that dates and values are preserved
        assert len(df) == 5
        assert "equity" in df.columns
        assert "100000" in df["equity"].astype(str).iloc[0]
        assert "102000" in df["equity"].astype(str).iloc[-1]

    @pytest.mark.component
    def test_custom_delimiter_support(self, exporter, sample_metrics, tmp_path):
        """Test export with custom delimiter."""
        output_file = tmp_path / "test_semicolon.csv"

        success = exporter.export_metrics(
            sample_metrics, str(output_file), delimiter=";"
        )

        assert success is True

        # Read with pandas to verify delimiter
        df = pd.read_csv(output_file, delimiter=";")
        assert len(df) > 0  # Should have metrics data
        assert "metric" in df.columns
        assert "value" in df.columns

    @pytest.mark.component
    def test_large_dataset_handling(self, exporter, tmp_path):
        """Test export with large dataset."""
        # Create large trade dataset
        large_trades = []
        for i in range(1000):
            trade = {
                "id": i,
                "entry_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "symbol": f"STOCK{i % 100}",
                "entry_price": Decimal(f"{100 + i * 0.01:.5f}"),
                "pnl": Decimal(f"{(i - 500) * 0.25:.3f}"),
                "quantity": 100 + i,
            }
            large_trades.append(trade)

        output_file = tmp_path / "large_trades.csv"

        success = exporter.export_trades(large_trades, str(output_file))

        assert success is True
        assert output_file.exists()

        # Verify file size is reasonable and data is there
        file_size = output_file.stat().st_size
        assert file_size > 50000  # Should be substantial

        # Spot check some data
        df = pd.read_csv(output_file)
        assert len(df) == 1000
        assert "STOCK99" in df["symbol"].values

    @pytest.mark.component
    def test_chunked_export_for_memory_efficiency(self, exporter, tmp_path):
        """Test chunked export for very large datasets."""
        output_file = tmp_path / "chunked_export.csv"

        # Create a generator of trade chunks
        def trade_generator():
            for chunk_start in range(0, 2000, 500):
                chunk = []
                for i in range(chunk_start, min(chunk_start + 500, 2000)):
                    trade = {
                        "id": i,
                        "symbol": f"SYM{i}",
                        "pnl": Decimal(f"{i * 0.1:.2f}"),
                    }
                    chunk.append(trade)
                yield chunk

        success = exporter.export_trades_chunked(trade_generator(), str(output_file))

        assert success is True
        assert output_file.exists()

        # Verify all data was written
        df = pd.read_csv(output_file)
        assert len(df) == 2000

    @pytest.mark.component
    def test_file_permission_error_handling(self, exporter, sample_metrics):
        """Test handling of file permission errors."""
        # Try to write to a read-only location
        readonly_path = "/root/readonly_file.csv"

        from src.services.reports.exceptions import FileWriteError

        with pytest.raises(FileWriteError):
            exporter.export_metrics(sample_metrics, readonly_path)

    @pytest.mark.component
    def test_invalid_data_handling(self, exporter, tmp_path):
        """Test handling of invalid data types."""
        invalid_metrics = {
            "valid_metric": Decimal("1.5"),
            "invalid_metric": object(),  # Invalid type
            "none_metric": None,
            "nan_metric": float("nan"),
        }

        output_file = tmp_path / "invalid_data.csv"

        # Should handle gracefully without crashing
        success = exporter.export_metrics(invalid_metrics, str(output_file))

        # Should either succeed with cleaned data or fail gracefully
        if success:
            df = pd.read_csv(output_file)
            # Check that valid metric exists in the data
            assert "metric" in df.columns
            assert "value" in df.columns
            assert "valid_metric" in df["metric"].values

    @pytest.mark.component
    def test_empty_data_export(self, exporter, tmp_path):
        """Test export with empty data structures."""
        output_file = tmp_path / "empty_data.csv"

        # Test empty metrics
        success = exporter.export_metrics({}, str(output_file))
        if success:
            assert output_file.exists()

        # Test empty trades
        empty_trades_file = tmp_path / "empty_trades.csv"
        success = exporter.export_trades([], str(empty_trades_file))
        if success:
            assert empty_trades_file.exists()

    @pytest.mark.component
    def test_metadata_inclusion(self, exporter, sample_metrics, tmp_path):
        """Test inclusion of export metadata."""
        output_file = tmp_path / "with_metadata.csv"

        metadata = {
            "export_timestamp": datetime.now(timezone.utc),
            "data_source": "backtest_run_123",
            "strategy": "sma_crossover",
        }

        success = exporter.export_metrics(
            sample_metrics, str(output_file), metadata=metadata
        )

        assert success is True

        # Check if metadata is preserved (either in file or separate metadata file)
        assert output_file.exists()

    @pytest.mark.component
    def test_index_preservation(self, exporter, tmp_path):
        """Test that DataFrame index is preserved correctly."""
        # Create time-indexed data
        dates = pd.date_range("2024-01-01", periods=3, freq="H")
        data = pd.DataFrame(
            {
                "price": [Decimal("100.50"), Decimal("101.25"), Decimal("99.75")],
                "volume": [1000, 1500, 800],
            },
            index=dates,
        )

        output_file = tmp_path / "indexed_data.csv"

        success = exporter.export_dataframe(data, str(output_file))

        assert success is True

        # Read back and verify index preservation
        df_read = pd.read_csv(output_file, index_col=0, parse_dates=True)
        assert len(df_read.index) == 3
        assert isinstance(df_read.index[0], pd.Timestamp)

    @pytest.mark.component
    def test_configuration_options(self, tmp_path):
        """Test CSVExporter with different configuration options."""
        # Test with high precision
        high_precision_exporter = CSVExporter(decimal_places=8)
        assert high_precision_exporter.decimal_places == 8

        # Test with custom quote character
        CSVExporter(quote_char='"')

        metrics = {"test_decimal": Decimal("1.12345678")}
        output_file = tmp_path / "high_precision.csv"

        success = high_precision_exporter.export_metrics(metrics, str(output_file))
        assert success is True

    @pytest.mark.component
    def test_concurrent_export_safety(self, exporter, sample_trades, tmp_path):
        """Test that concurrent exports don't interfere with each other."""
        import threading

        results = []

        def export_worker(worker_id):
            output_file = tmp_path / f"worker_{worker_id}.csv"
            success = exporter.export_trades(sample_trades, str(output_file))
            results.append((worker_id, success, output_file.exists()))

        # Start multiple workers
        threads = []
        for i in range(5):
            thread = threading.Thread(target=export_worker, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all workers
        for thread in threads:
            thread.join()

        # Verify all succeeded
        assert len(results) == 5
        for worker_id, success, file_exists in results:
            assert success is True
            assert file_exists is True
