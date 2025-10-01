"""CSV export service with precision preservation for trading data."""

import csv
import pandas as pd
from typing import Dict, Any, List, Optional, Union, Generator
from decimal import Decimal
from datetime import datetime
import os
from pathlib import Path

from .exceptions import FileWriteError, InvalidDataError, EmptyDataError
from .validators import DataValidator, FileValidator, TradeValidator


class CSVExporter:
    """Export trading data to CSV format with precision preservation."""

    def __init__(
        self,
        decimal_places: int = 5,
        delimiter: str = ',',
        quote_char: str = '"',
        date_format: str = '%Y-%m-%d %H:%M:%S%z'
    ):
        """
        Initialize CSV exporter with configuration options.

        Args:
            decimal_places: Number of decimal places to preserve
            delimiter: CSV delimiter character
            quote_char: Quote character for CSV fields
            date_format: Format string for datetime values
        """
        self.decimal_places = decimal_places
        self.delimiter = delimiter
        self.quote_char = quote_char
        self.date_format = date_format

    def export_metrics(
        self,
        metrics: Dict[str, Any],
        filename: str,
        delimiter: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Export performance metrics to CSV file.

        Args:
            metrics: Performance metrics dictionary
            filename: Output CSV file path
            delimiter: Optional custom delimiter
            metadata: Optional metadata to include

        Returns:
            True if export successful, False otherwise

        Raises:
            InvalidDataError: If filename is invalid
            EmptyDataError: If metrics is None
            FileWriteError: If file writing fails
        """
        # Validate inputs
        FileValidator.validate_filename(filename)
        FileValidator.validate_file_extension(filename, ['.csv'])
        if metrics is None:
            raise EmptyDataError("metrics")

        try:
            if not metrics:
                # Create empty CSV with headers for empty metrics
                df = pd.DataFrame(columns=['metric', 'value'])
            else:
                # Convert metrics to DataFrame
                data = []
                for key, value in metrics.items():
                    processed_value = self._process_value(value)
                    data.append({'metric': key, 'value': processed_value})

                df = pd.DataFrame(data)

            # Add metadata if provided
            if metadata:
                for key, value in metadata.items():
                    df = pd.concat([
                        df,
                        pd.DataFrame([{'metric': f'_metadata_{key}', 'value': self._process_value(value)}])
                    ], ignore_index=True)

            # Export to CSV
            delimiter_to_use = delimiter or self.delimiter
            df.to_csv(
                filename,
                index=False,
                sep=delimiter_to_use,
                quoting=csv.QUOTE_NONNUMERIC,
                quotechar=self.quote_char
            )

            return True

        except OSError as e:
            raise FileWriteError(filename, f"OS error: {str(e)}") from e
        except pd.errors.ParserError as e:
            raise InvalidDataError("data format", str(metrics), "valid CSV data") from e
        except Exception as e:
            raise FileWriteError(filename, f"Unexpected error: {str(e)}") from e

    def export_trades(
        self,
        trades: List[Dict[str, Any]],
        filename: str,
        delimiter: Optional[str] = None
    ) -> bool:
        """
        Export trade data to CSV file.

        Args:
            trades: List of trade dictionaries
            filename: Output CSV file path
            delimiter: Optional custom delimiter

        Returns:
            True if export successful, False otherwise
        """
        try:
            if not trades:
                # Create empty DataFrame with expected columns
                df = pd.DataFrame(columns=[
                    'id', 'entry_time', 'exit_time', 'symbol', 'side',
                    'quantity', 'entry_price', 'exit_price', 'pnl',
                    'commission', 'slippage', 'strategy_name'
                ])
            else:
                # Process trade data
                processed_trades = []
                for trade in trades:
                    processed_trade = {}
                    for key, value in trade.items():
                        processed_trade[key] = self._process_value(value)
                    processed_trades.append(processed_trade)

                df = pd.DataFrame(processed_trades)

            # Export to CSV
            delimiter_to_use = delimiter or self.delimiter
            df.to_csv(
                filename,
                index=False,
                sep=delimiter_to_use,
                quoting=csv.QUOTE_NONNUMERIC,
                quotechar=self.quote_char
            )

            return True

        except Exception as e:
            print(f"Error exporting trades to CSV: {e}")
            return False

    def export_trades_chunked(
        self,
        trade_chunks: Generator[List[Dict[str, Any]], None, None],
        filename: str,
        chunk_size: int = 1000
    ) -> bool:
        """
        Export large trade datasets in chunks for memory efficiency.

        Args:
            trade_chunks: Generator yielding chunks of trade data
            filename: Output CSV file path
            chunk_size: Number of trades per chunk

        Returns:
            True if export successful, False otherwise
        """
        try:
            first_chunk = True

            for chunk in trade_chunks:
                if not chunk:
                    continue

                # Process chunk
                processed_trades = []
                for trade in chunk:
                    processed_trade = {}
                    for key, value in trade.items():
                        processed_trade[key] = self._process_value(value)
                    processed_trades.append(processed_trade)

                df = pd.DataFrame(processed_trades)

                # Write to file (append after first chunk)
                mode = 'w' if first_chunk else 'a'
                header = first_chunk

                df.to_csv(
                    filename,
                    mode=mode,
                    header=header,
                    index=False,
                    sep=self.delimiter,
                    quoting=csv.QUOTE_NONNUMERIC,
                    quotechar=self.quote_char
                )

                first_chunk = False

            return True

        except Exception as e:
            print(f"Error exporting trades in chunks: {e}")
            return False

    def export_equity_curve(
        self,
        equity_curve: pd.Series,
        filename: str
    ) -> bool:
        """
        Export equity curve data to CSV.

        Args:
            equity_curve: Time-indexed equity values
            filename: Output CSV file path

        Returns:
            True if export successful, False otherwise
        """
        try:
            if equity_curve.empty:
                # Create empty DataFrame
                df = pd.DataFrame(columns=['timestamp', 'equity'])
            else:
                # Convert to DataFrame with processed values
                df = pd.DataFrame({
                    'timestamp': equity_curve.index,
                    'equity': [self._process_value(val) for val in equity_curve.values]
                })

            df.to_csv(
                filename,
                index=False,
                sep=self.delimiter,
                quoting=csv.QUOTE_NONNUMERIC,
                quotechar=self.quote_char
            )

            return True

        except Exception as e:
            print(f"Error exporting equity curve to CSV: {e}")
            return False

    def export_dataframe(
        self,
        dataframe: pd.DataFrame,
        filename: str,
        preserve_index: bool = True
    ) -> bool:
        """
        Export arbitrary DataFrame to CSV with value processing.

        Args:
            dataframe: DataFrame to export
            filename: Output CSV file path
            preserve_index: Whether to preserve DataFrame index

        Returns:
            True if export successful, False otherwise
        """
        try:
            if dataframe.empty:
                # Just write the empty DataFrame
                dataframe.to_csv(filename, index=preserve_index)
                return True

            # Process all values in the DataFrame
            processed_df = dataframe.copy()

            for column in processed_df.columns:
                processed_df[column] = processed_df[column].apply(self._process_value)

            # Process index if preserving it
            if preserve_index and not isinstance(processed_df.index, pd.RangeIndex):
                processed_df.index = processed_df.index.map(self._process_value)

            processed_df.to_csv(
                filename,
                index=preserve_index,
                sep=self.delimiter,
                quoting=csv.QUOTE_NONNUMERIC,
                quotechar=self.quote_char
            )

            return True

        except Exception as e:
            print(f"Error exporting DataFrame to CSV: {e}")
            return False

    def export_performance_summary(
        self,
        metrics: Dict[str, Any],
        trades: List[Dict[str, Any]],
        equity_curve: pd.Series,
        base_filename: str
    ) -> Dict[str, bool]:
        """
        Export complete performance data as multiple CSV files.

        Args:
            metrics: Performance metrics
            trades: Trade history
            equity_curve: Equity curve data
            base_filename: Base filename (without extension)

        Returns:
            Dictionary of export results for each file type
        """
        results = {}
        base_path = Path(base_filename)
        base_dir = base_path.parent
        base_name = base_path.stem

        # Export metrics
        metrics_file = base_dir / f"{base_name}_metrics.csv"
        results['metrics'] = self.export_metrics(metrics, str(metrics_file))

        # Export trades
        trades_file = base_dir / f"{base_name}_trades.csv"
        results['trades'] = self.export_trades(trades, str(trades_file))

        # Export equity curve
        equity_file = base_dir / f"{base_name}_equity.csv"
        results['equity'] = self.export_equity_curve(equity_curve, str(equity_file))

        return results

    def _process_value(self, value: Any) -> str:
        """
        Process value for CSV export, preserving precision.

        Args:
            value: Value to process

        Returns:
            String representation suitable for CSV
        """
        if value is None:
            return ''

        if isinstance(value, Decimal):
            # Preserve exact decimal representation including trailing zeros
            return str(value)

        if isinstance(value, datetime):
            # Format datetime consistently
            return value.strftime(self.date_format)

        if isinstance(value, pd.Timestamp):
            # Handle pandas Timestamp
            return value.strftime(self.date_format)

        if isinstance(value, (int, float)):
            # Handle numeric values
            if pd.isna(value) or value != value:  # Check for NaN
                return ''
            if isinstance(value, float):
                # Format float with specified precision
                return f"{value:.{self.decimal_places}f}"
            return str(value)

        if isinstance(value, str):
            return value

        if hasattr(value, '__str__'):
            # For any other object with string representation
            try:
                return str(value)
            except Exception:
                return ''

        # Fallback for unknown types
        return ''

    def validate_file_path(self, filename: str) -> bool:
        """
        Validate that file path is writable.

        Args:
            filename: File path to validate

        Returns:
            True if path is writable, False otherwise
        """
        try:
            # Check if directory exists and is writable
            directory = Path(filename).parent
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)

            # Test write permission
            test_file = directory / f".write_test_{os.getpid()}"
            test_file.write_text("test")
            test_file.unlink()

            return True

        except Exception:
            return False

    def get_export_info(self, filename: str) -> Dict[str, Any]:
        """
        Get information about exported file.

        Args:
            filename: Path to exported file

        Returns:
            Dictionary with file information
        """
        try:
            file_path = Path(filename)
            if not file_path.exists():
                return {'exists': False}

            stat = file_path.stat()

            # Try to get basic CSV info
            try:
                df = pd.read_csv(filename, nrows=0)  # Just headers
                columns = list(df.columns)

                # Get row count
                with open(filename, 'r') as f:
                    row_count = sum(1 for line in f) - 1  # Subtract header

            except Exception:
                columns = []
                row_count = 0

            return {
                'exists': True,
                'size_bytes': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'modified_time': datetime.fromtimestamp(stat.st_mtime),
                'columns': columns,
                'row_count': row_count
            }

        except Exception as e:
            return {'exists': False, 'error': str(e)}