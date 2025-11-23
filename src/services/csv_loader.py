"""CSV data loading service for Parquet catalog."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import structlog
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from src.services.data_catalog import DataCatalogService

logger = structlog.get_logger(__name__)


class ValidationError(Exception):
    """CSV validation error with row number context."""

    def __init__(self, row_number: int, message: str):
        """
        Initialize validation error.

        Args:
            row_number: Row number (1-indexed)
            message: Error message
        """
        self.row_number = row_number
        self.message = message
        super().__init__(f"Row {row_number}: {message}")


class CSVLoader:
    """
    Service for loading CSV market data directly to Parquet catalog.

    Supports validation, conflict resolution, and Nautilus Bar conversion.
    """

    REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

    def __init__(
        self,
        catalog_service: DataCatalogService | None = None,
        conflict_mode: str = "skip",
    ):
        """
        Initialize CSV loader.

        Args:
            catalog_service: DataCatalogService instance. If None, creates default.
            conflict_mode: Conflict resolution mode:
                - 'skip': Skip existing data (default)
                - 'overwrite': Delete existing data and rewrite
                - 'merge': Merge with existing data

        Example:
            >>> loader = CSVLoader()
            >>> result = await loader.load_file(
            ...     Path("data.csv"),
            ...     "AAPL",
            ...     "NASDAQ",
            ...     "1-MINUTE-LAST"
            ... )
        """
        self.catalog_service = catalog_service or DataCatalogService()
        self.conflict_mode = conflict_mode
        logger.info(
            "csv_loader_initialized",
            conflict_mode=conflict_mode,
        )

    async def load_file(
        self,
        file_path: Path,
        symbol: str,
        venue: str,
        bar_type_spec: str = "1-MINUTE-LAST",
    ) -> Dict[str, Any]:
        """
        Load CSV file and write to Parquet catalog.

        Args:
            file_path: Path to CSV file
            symbol: Trading symbol (e.g., "AAPL")
            venue: Venue/exchange (e.g., "NASDAQ")
            bar_type_spec: Bar type specification (e.g., "1-MINUTE-LAST")

        Returns:
            Dictionary with import results:
                {
                    "file": str,
                    "instrument_id": str,
                    "bar_type_spec": str,
                    "rows_processed": int,
                    "bars_written": int,
                    "conflicts_skipped": int,
                    "validation_errors": List[str],
                    "date_range": str,
                    "file_size_kb": float
                }

        Raises:
            FileNotFoundError: If file doesn't exist
            ValidationError: If CSV format is invalid
        """
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        logger.info("csv_import_started", file=str(file_path), symbol=symbol)

        # Read CSV with pandas
        df = pd.read_csv(file_path)

        # Validate columns
        self._validate_columns(df)

        # Validate and convert to Nautilus Bar objects
        bars, validation_errors = await self._convert_to_bars(df, symbol, venue, bar_type_spec)

        if not bars:
            return {
                "file": str(file_path),
                "instrument_id": f"{symbol}.{venue}",
                "bar_type_spec": bar_type_spec,
                "rows_processed": len(df),
                "bars_written": 0,
                "conflicts_skipped": 0,
                "validation_errors": validation_errors,
                "date_range": "N/A",
                "file_size_kb": 0,
            }

        # Handle conflicts if needed
        bars_to_write, conflicts_skipped = await self._handle_conflicts(
            bars, f"{symbol}.{venue}", bar_type_spec
        )

        # Write bars to catalog
        written_count = 0
        if bars_to_write:
            self.catalog_service.write_bars(
                bars_to_write,
                correlation_id=f"csv-import-{symbol}",
            )
            written_count = len(bars_to_write)

        # Calculate file size
        files = list(
            (
                self.catalog_service.catalog_path
                / "data"
                / "bar"
                / f"{symbol}.{venue}-{bar_type_spec}-EXTERNAL"
            ).glob("*.parquet")
        )
        total_size_kb = sum(f.stat().st_size for f in files) / 1024 if files else 0

        # Get date range
        date_range = "N/A"
        if bars:
            start_ts = min(bar.ts_event for bar in bars)
            end_ts = max(bar.ts_event for bar in bars)
            start_dt = datetime.fromtimestamp(start_ts / 1e9, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_ts / 1e9, tz=timezone.utc)
            date_range = (
                f"{start_dt.strftime('%Y-%m-%d %H:%M')} to {end_dt.strftime('%Y-%m-%d %H:%M')}"
            )

        result = {
            "file": str(file_path),
            "instrument_id": f"{symbol}.{venue}",
            "bar_type_spec": bar_type_spec,
            "rows_processed": len(df),
            "bars_written": written_count,
            "conflicts_skipped": conflicts_skipped,
            "validation_errors": validation_errors,
            "date_range": date_range,
            "file_size_kb": round(total_size_kb, 2),
        }

        logger.info(
            "csv_import_completed",
            bars_written=written_count,
            conflicts_skipped=conflicts_skipped,
            errors=len(validation_errors),
        )

        return result

    def _validate_columns(self, df: pd.DataFrame) -> None:
        """
        Validate that all required columns are present.

        Args:
            df: DataFrame to validate

        Raises:
            ValidationError: If required columns are missing
        """
        missing = set(self.REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValidationError(0, f"Missing required columns: {missing}")

    async def _convert_to_bars(
        self,
        df: pd.DataFrame,
        symbol: str,
        venue: str,
        bar_type_spec: str,
    ) -> tuple[List[Bar], List[str]]:
        """
        Convert CSV DataFrame to Nautilus Bar objects with validation.

        Args:
            df: Source DataFrame
            symbol: Trading symbol
            venue: Venue/exchange
            bar_type_spec: Bar type specification

        Returns:
            Tuple of (bars, validation_errors)
        """
        bars: List[Bar] = []
        validation_errors: List[str] = []

        # Reason: Create bar_type once for all rows
        instrument_id_str = f"{symbol}.{venue}"
        # Reason: CSV data is external, append -EXTERNAL aggregation source
        bar_type = BarType.from_str(f"{instrument_id_str}-{bar_type_spec}-EXTERNAL")

        for row_idx, (_idx, row) in enumerate(df.iterrows()):
            row_num = row_idx + 2  # +2 because: 0-indexed + 1 header row

            try:
                # Reason: Validate and parse timestamp
                timestamp = self._parse_timestamp(row["timestamp"], row_num)

                # Reason: Validate OHLCV data
                open_price, high_price, low_price, close_price, volume = self._validate_ohlcv(
                    row, row_num
                )

                # Reason: Create Nautilus Bar object
                # ts_event and ts_init are the same for historical data
                ts_nanos = int(timestamp.timestamp() * 1e9)

                bar = Bar(
                    bar_type=bar_type,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=volume,
                    ts_event=ts_nanos,
                    ts_init=ts_nanos,
                )

                bars.append(bar)

            except ValidationError as e:
                validation_errors.append(str(e))
                logger.debug("row_validation_failed", row=row_num, error=str(e))
                continue
            except Exception as e:
                error_msg = f"Row {row_num}: Unexpected error - {str(e)}"
                validation_errors.append(error_msg)
                logger.debug("row_conversion_failed", row=row_num, error=str(e))
                continue

        logger.info(
            "conversion_completed",
            total_rows=len(df),
            bars_created=len(bars),
            errors=len(validation_errors),
        )

        return bars, validation_errors

    def _parse_timestamp(self, timestamp_value: Any, row_num: int) -> datetime:
        """
        Parse and validate timestamp.

        Args:
            timestamp_value: Timestamp value from CSV
            row_num: Row number for error reporting

        Returns:
            Timezone-aware datetime (UTC)

        Raises:
            ValidationError: If timestamp is invalid
        """
        try:
            timestamp = pd.to_datetime(timestamp_value)

            # Reason: Ensure timezone-aware (assume UTC if naive)
            if timestamp.tz is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")

            return timestamp

        except Exception as e:
            raise ValidationError(row_num, f"Invalid timestamp format: {timestamp_value} ({e})")

    def _validate_ohlcv(
        self, row: pd.Series, row_num: int
    ) -> tuple[Price, Price, Price, Price, Quantity]:
        """
        Validate OHLCV data constraints.

        Args:
            row: DataFrame row
            row_num: Row number for error reporting

        Returns:
            Tuple of (open, high, low, close, volume) as Nautilus types

        Raises:
            ValidationError: If OHLCV constraints are violated
        """
        try:
            # Reason: Parse values as Decimal for precision
            open_val = Decimal(str(row["open"]))
            high_val = Decimal(str(row["high"]))
            low_val = Decimal(str(row["low"]))
            close_val = Decimal(str(row["close"]))
            volume_val = int(row["volume"])

            # Reason: Validate price constraints
            if open_val <= 0:
                raise ValidationError(row_num, f"open must be > 0, got {open_val}")
            if high_val <= 0:
                raise ValidationError(row_num, f"high must be > 0, got {high_val}")
            if low_val <= 0:
                raise ValidationError(row_num, f"low must be > 0, got {low_val}")
            if close_val <= 0:
                raise ValidationError(row_num, f"close must be > 0, got {close_val}")

            # Reason: Validate OHLC relationships
            if high_val < low_val:
                raise ValidationError(row_num, f"high ({high_val}) must be >= low ({low_val})")
            if high_val < open_val:
                raise ValidationError(row_num, f"high ({high_val}) must be >= open ({open_val})")
            if high_val < close_val:
                raise ValidationError(row_num, f"high ({high_val}) must be >= close ({close_val})")
            if low_val > open_val:
                raise ValidationError(row_num, f"low ({low_val}) must be <= open ({open_val})")
            if low_val > close_val:
                raise ValidationError(row_num, f"low ({low_val}) must be <= close ({close_val})")

            # Reason: Validate volume
            if volume_val < 0:
                raise ValidationError(row_num, f"volume must be >= 0, got {volume_val}")

            # Reason: Determine price precision (number of decimal places)
            # Use max precision from all price fields
            # Handle special Decimal exponent values (n, N, F for NaN, Infinity)
            def get_precision(decimal_val: Decimal) -> int:
                exp = decimal_val.as_tuple().exponent
                return abs(exp) if isinstance(exp, int) else 0

            precisions = [
                get_precision(open_val),
                get_precision(high_val),
                get_precision(low_val),
                get_precision(close_val),
            ]
            price_precision = max(precisions)

            # Reason: Create Nautilus Price and Quantity objects
            open_price = Price(float(open_val), precision=price_precision)
            high_price = Price(float(high_val), precision=price_precision)
            low_price = Price(float(low_val), precision=price_precision)
            close_price = Price(float(close_val), precision=price_precision)
            volume = Quantity(float(volume_val), precision=0)

            return open_price, high_price, low_price, close_price, volume

        except ValidationError:
            raise
        except (ValueError, TypeError, ArithmeticError) as e:
            raise ValidationError(row_num, f"Invalid OHLCV data: {e}")

    async def _handle_conflicts(
        self,
        bars: List[Bar],
        instrument_id: str,
        bar_type_spec: str,
    ) -> tuple[List[Bar], int]:
        """
        Handle conflicts based on conflict_mode.

        Args:
            bars: Bars to write
            instrument_id: Instrument ID
            bar_type_spec: Bar type specification

        Returns:
            Tuple of (bars_to_write, conflicts_skipped)
        """
        if self.conflict_mode == "skip":
            # Reason: Skip if any data exists
            availability = self.catalog_service.get_availability(instrument_id, bar_type_spec)
            if availability:
                logger.info(
                    "skipping_existing_data",
                    instrument_id=instrument_id,
                    existing_range=f"{availability.start_date} to {availability.end_date}",
                )
                return [], len(bars)
            return bars, 0

        elif self.conflict_mode == "overwrite":
            # Reason: Delete existing data range before writing
            if bars:
                start_ts = min(bar.ts_event for bar in bars)
                end_ts = max(bar.ts_event for bar in bars)

                # Delete existing data in this range
                try:
                    self.catalog_service.catalog.delete_data_range(
                        data_cls=Bar,
                        identifier=instrument_id,
                        start=start_ts,
                        end=end_ts,
                    )
                    logger.info(
                        "deleted_existing_data_range",
                        instrument_id=instrument_id,
                        start_ns=start_ts,
                        end_ns=end_ts,
                    )
                except Exception as e:
                    logger.warning(
                        "failed_to_delete_existing_data",
                        error=str(e),
                    )

            return bars, 0

        elif self.conflict_mode == "merge":
            # Reason: Merge with existing data (Nautilus handles duplicates)
            return bars, 0

        else:
            raise ValueError(f"Invalid conflict_mode: {self.conflict_mode}")
