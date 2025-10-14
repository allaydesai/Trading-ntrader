"""
Data Catalog Service - Facade for Parquet catalog operations.

This module provides a high-level service layer for interacting with
the Nautilus Trader ParquetDataCatalog, including availability checking,
data queries, and write operations with structured logging.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import structlog
from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from src.models.catalog_metadata import CatalogAvailability
from src.services.exceptions import (
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
)

logger = structlog.get_logger(__name__)


class DataCatalogService:
    """
    Facade service for Parquet catalog operations.

    Provides high-level methods for availability checking, querying,
    and writing market data to the Nautilus ParquetDataCatalog with
    structured logging and error handling.

    Attributes:
        catalog: Nautilus ParquetDataCatalog instance
        availability_cache: In-memory cache of catalog availability
    """

    def __init__(self, catalog_path: str | Path | None = None) -> None:
        """
        Initialize DataCatalogService.

        Args:
            catalog_path: Path to catalog root. If None, uses
                         environment variable NAUTILUS_PATH or
                         defaults to "./data/catalog"

        Example:
            >>> service = DataCatalogService()
            >>> # Or with explicit path:
            >>> service = DataCatalogService("/path/to/catalog")
        """
        # Reason: Determine catalog path from argument, env, or default
        if catalog_path is None:
            catalog_path = os.environ.get("NAUTILUS_PATH", "./data/catalog")

        self.catalog_path = Path(catalog_path)
        self.catalog = ParquetDataCatalog(str(self.catalog_path))

        # Reason: In-memory cache for fast availability checks
        self.availability_cache: Dict[str, CatalogAvailability] = {}

        logger.info(
            "data_catalog_initialized",
            catalog_path=str(self.catalog_path),
        )

        # Reason: Build availability cache on startup
        self._rebuild_availability_cache()

    def _rebuild_availability_cache(self) -> None:
        """
        Rebuild the in-memory availability cache by scanning catalog.

        This method scans the catalog directory structure to build
        metadata about available instruments, bar types, and date ranges.
        Called automatically on service initialization.
        """
        logger.info("rebuilding_availability_cache")

        # Reason: Clear existing cache before rebuild
        self.availability_cache.clear()

        # Reason: Scan catalog directory structure
        # Expected structure: {instrument_id}/{bar_type_spec}/YYYY-MM-DD.parquet
        if not self.catalog_path.exists():
            logger.warning(
                "catalog_path_not_found",
                path=str(self.catalog_path),
            )
            return

        # Reason: Iterate through instrument directories
        for instrument_dir in self.catalog_path.iterdir():
            if not instrument_dir.is_dir():
                continue

            instrument_id = instrument_dir.name

            # Reason: Iterate through bar type directories
            for bar_type_dir in instrument_dir.iterdir():
                if not bar_type_dir.is_dir():
                    continue

                bar_type_spec = bar_type_dir.name

                # Reason: Find all Parquet files and determine date range
                parquet_files = list(bar_type_dir.glob("*.parquet"))
                if not parquet_files:
                    continue

                # Reason: Extract dates from filenames (YYYY-MM-DD.parquet)
                dates = []
                total_rows = 0

                for file in parquet_files:
                    try:
                        # Reason: Parse date from filename
                        date_str = file.stem  # Remove .parquet extension
                        file_date = datetime.strptime(date_str, "%Y-%m-%d")
                        dates.append(file_date)

                        # Reason: Estimate row count from file size
                        # Typical 1-minute bar file: ~390 rows, ~50KB
                        file_size = file.stat().st_size
                        estimated_rows = file_size // 128  # ~128 bytes/row
                        total_rows += estimated_rows

                    except (ValueError, OSError) as e:
                        logger.warning(
                            "failed_to_parse_catalog_file",
                            file=str(file),
                            error=str(e),
                        )
                        continue

                if not dates:
                    continue

                # Reason: Create availability metadata
                cache_key = f"{instrument_id}_{bar_type_spec}"
                availability = CatalogAvailability(
                    instrument_id=instrument_id,
                    bar_type_spec=bar_type_spec,
                    start_date=min(dates),
                    end_date=max(dates),
                    file_count=len(parquet_files),
                    total_rows=total_rows,
                    last_updated=datetime.now(),
                )

                self.availability_cache[cache_key] = availability

                logger.debug(
                    "availability_cached",
                    instrument_id=instrument_id,
                    bar_type_spec=bar_type_spec,
                    start_date=availability.start_date.isoformat(),
                    end_date=availability.end_date.isoformat(),
                    file_count=availability.file_count,
                )

        logger.info(
            "availability_cache_rebuilt",
            total_entries=len(self.availability_cache),
        )

    def get_availability(
        self, instrument_id: str, bar_type_spec: str
    ) -> CatalogAvailability | None:
        """
        Get catalog availability for instrument and bar type.

        Args:
            instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")
            bar_type_spec: Bar type spec (e.g., "1-MINUTE-LAST")

        Returns:
            CatalogAvailability if data exists, None otherwise

        Example:
            >>> service = DataCatalogService()
            >>> avail = service.get_availability(
            ...     "AAPL.NASDAQ", "1-MINUTE-LAST"
            ... )
            >>> if avail:
            ...     print(f"Data from {avail.start_date} to {avail.end_date}")
        """
        cache_key = f"{instrument_id}_{bar_type_spec}"
        availability = self.availability_cache.get(cache_key)

        if availability:
            logger.debug(
                "availability_cache_hit",
                instrument_id=instrument_id,
                bar_type_spec=bar_type_spec,
            )
        else:
            logger.debug(
                "availability_cache_miss",
                instrument_id=instrument_id,
                bar_type_spec=bar_type_spec,
            )

        return availability

    def query_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
    ) -> List[Bar]:
        """
        Query bars from catalog for specified time range.

        Args:
            instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")
            start: Start datetime (UTC)
            end: End datetime (UTC)
            bar_type_spec: Bar type specification (default: "1-MINUTE-LAST")

        Returns:
            List of Bar objects in chronological order

        Raises:
            DataNotFoundError: If requested data not in catalog
            CatalogCorruptionError: If Parquet files are corrupted

        Example:
            >>> from datetime import datetime, timezone
            >>> service = DataCatalogService()
            >>> bars = service.query_bars(
            ...     "AAPL.NASDAQ",
            ...     datetime(2024, 1, 1, tzinfo=timezone.utc),
            ...     datetime(2024, 1, 31, tzinfo=timezone.utc)
            ... )
            >>> print(f"Loaded {len(bars)} bars")
        """
        logger.info(
            "querying_catalog",
            instrument_id=instrument_id,
            start=start.isoformat(),
            end=end.isoformat(),
            bar_type_spec=bar_type_spec,
        )

        try:
            # Reason: Convert datetime to nanoseconds (Nautilus format)
            start_ns = int(start.timestamp() * 1e9)
            end_ns = int(end.timestamp() * 1e9)

            # Reason: Query catalog using Nautilus API
            bars = self.catalog.query(
                data_cls=Bar,
                identifiers=[instrument_id],
                start=start_ns,
                end=end_ns,
            )

            # Reason: Convert generator to list for easier handling
            bars_list = list(bars) if bars else []

            if not bars_list:
                logger.warning(
                    "no_data_found_in_catalog",
                    instrument_id=instrument_id,
                    start=start.isoformat(),
                    end=end.isoformat(),
                )
                raise DataNotFoundError(instrument_id, start, end)

            logger.info(
                "catalog_query_successful",
                instrument_id=instrument_id,
                bar_count=len(bars_list),
                start=start.isoformat(),
                end=end.isoformat(),
            )

            return bars_list

        except DataNotFoundError:
            # Reason: Re-raise DataNotFoundError as-is
            raise

        except Exception as e:
            # Reason: Detect Parquet/Arrow corruption errors
            if "Parquet" in str(e) or "Arrow" in str(e) or "pyarrow" in str(e):
                logger.error(
                    "catalog_corruption_detected",
                    instrument_id=instrument_id,
                    error=str(e),
                )
                file_path = f"{self.catalog_path}/{instrument_id}"
                raise CatalogCorruptionError(file_path, e) from e

            # Reason: Unexpected error, wrap in CatalogError
            logger.error(
                "catalog_query_failed",
                instrument_id=instrument_id,
                error=str(e),
            )
            raise CatalogError(f"Query failed: {e}") from e

    def write_bars(
        self,
        bars: List[Bar],
        correlation_id: str | None = None,
    ) -> None:
        """
        Write bars to catalog with atomic operations.

        Args:
            bars: List of Bar objects to write
            correlation_id: Optional correlation ID for logging

        Raises:
            CatalogError: If write operation fails

        Example:
            >>> bars = [...]  # List of Bar objects
            >>> service = DataCatalogService()
            >>> service.write_bars(bars, correlation_id="backtest-123")
        """
        if not bars:
            logger.warning(
                "write_bars_called_with_empty_list",
                correlation_id=correlation_id,
            )
            return

        # Reason: Extract instrument info from first bar for logging
        first_bar = bars[0]
        instrument_id = str(first_bar.bar_type.instrument_id)

        logger.info(
            "writing_bars_to_catalog",
            instrument_id=instrument_id,
            bar_count=len(bars),
            correlation_id=correlation_id,
        )

        try:
            # Reason: Write to catalog with skip_disjoint_check
            # (allows overlapping time ranges for re-fetches)
            self.catalog.write_data(bars, skip_disjoint_check=True)

            logger.info(
                "bars_written_successfully",
                instrument_id=instrument_id,
                bar_count=len(bars),
                correlation_id=correlation_id,
            )

            # Reason: Rebuild availability cache to reflect new data
            self._rebuild_availability_cache()

        except Exception as e:
            logger.error(
                "catalog_write_failed",
                instrument_id=instrument_id,
                bar_count=len(bars),
                error=str(e),
                correlation_id=correlation_id,
            )
            raise CatalogError(f"Write failed: {e}") from e

    def _is_ibkr_available(self) -> bool:
        """
        Check if IBKR connection is available for data fetching.

        Returns:
            True if IBKR client is connected and ready, False otherwise

        Example:
            >>> service = DataCatalogService()
            >>> if service._is_ibkr_available():
            ...     print("Can fetch from IBKR")
        """
        # Reason: Check if IBKR client has been initialized
        if not hasattr(self, "ibkr_client"):
            logger.debug("ibkr_client_not_initialized")
            return False

        # Reason: Check if IBKR client is connected
        if not hasattr(self.ibkr_client, "is_connected"):
            logger.debug("ibkr_client_missing_is_connected_property")
            return False

        is_connected = self.ibkr_client.is_connected
        logger.debug("ibkr_availability_check", is_connected=is_connected)
        return is_connected

    async def fetch_or_load(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
        correlation_id: str | None = None,
        max_retries: int = 3,
    ) -> List[Bar]:
        """
        Load bars from catalog or fetch from IBKR if missing.

        This method implements the automatic fetch workflow:
        1. Check catalog availability
        2. If data exists and covers range, load from catalog
        3. If data missing and IBKR available, fetch from IBKR
        4. Write fetched data to catalog
        5. Return bars

        Args:
            instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")
            start: Start datetime (UTC)
            end: End datetime (UTC)
            bar_type_spec: Bar type specification (default: "1-MINUTE-LAST")
            correlation_id: Optional correlation ID for logging
            max_retries: Maximum retry attempts for transient failures

        Returns:
            List of Bar objects in chronological order

        Raises:
            IBKRConnectionError: If IBKR unavailable and data not in catalog
            DataNotFoundError: If data cannot be loaded or fetched
            CatalogError: If catalog operations fail

        Example:
            >>> from datetime import datetime, timezone
            >>> service = DataCatalogService()
            >>> # Automatically fetches from IBKR if not in catalog:
            >>> bars = await service.fetch_or_load(
            ...     "AAPL.NASDAQ",
            ...     datetime(2024, 1, 1, tzinfo=timezone.utc),
            ...     datetime(2024, 1, 31, tzinfo=timezone.utc)
            ... )
        """
        logger.info(
            "fetch_or_load_started",
            instrument_id=instrument_id,
            start=start.isoformat(),
            end=end.isoformat(),
            bar_type_spec=bar_type_spec,
            correlation_id=correlation_id,
        )

        # Reason: Check catalog availability first
        availability = self.get_availability(instrument_id, bar_type_spec)

        # Reason: If data fully available in catalog, load it directly
        if availability and availability.covers_range(start, end):
            logger.info(
                "data_found_in_catalog",
                instrument_id=instrument_id,
                correlation_id=correlation_id,
            )
            return self.query_bars(instrument_id, start, end, bar_type_spec)

        # Reason: Data missing or partial, need to fetch from IBKR
        logger.info(
            "data_missing_attempting_ibkr_fetch",
            instrument_id=instrument_id,
            available_range=(
                f"{availability.start_date.isoformat()} to "
                f"{availability.end_date.isoformat()}"
                if availability
                else "None"
            ),
            correlation_id=correlation_id,
        )

        # Reason: Check if IBKR is available
        if not self._is_ibkr_available():
            logger.error(
                "ibkr_unavailable_cannot_fetch",
                instrument_id=instrument_id,
                correlation_id=correlation_id,
            )
            raise IBKRConnectionError(
                "IBKR connection not available. Cannot fetch missing data. "
                "Ensure IBKR Gateway is running with 'docker compose up ibgateway'."
            )

        # Reason: Fetch data from IBKR with retry logic
        bars = await self._fetch_from_ibkr_with_retry(
            instrument_id=instrument_id,
            start=start,
            end=end,
            bar_type_spec=bar_type_spec,
            max_retries=max_retries,
            correlation_id=correlation_id,
        )

        # Reason: Write fetched data to catalog for future use
        logger.info(
            "persisting_fetched_data_to_catalog",
            instrument_id=instrument_id,
            bar_count=len(bars),
            correlation_id=correlation_id,
        )
        self.write_bars(bars, correlation_id=correlation_id)

        logger.info(
            "fetch_or_load_completed",
            instrument_id=instrument_id,
            bar_count=len(bars),
            source="ibkr_fetch",
            correlation_id=correlation_id,
        )

        return bars

    async def _fetch_from_ibkr_with_retry(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str,
        max_retries: int,
        correlation_id: str | None,
    ) -> List[Bar]:
        """
        Fetch data from IBKR with exponential backoff retry logic.

        Args:
            instrument_id: Instrument identifier
            start: Start datetime (UTC)
            end: End datetime (UTC)
            bar_type_spec: Bar type specification
            max_retries: Maximum retry attempts
            correlation_id: Optional correlation ID for logging

        Returns:
            List of Bar objects

        Raises:
            DataNotFoundError: If all retry attempts fail
        """
        import asyncio

        retry_count = 0
        last_error = None

        while retry_count <= max_retries:
            try:
                logger.info(
                    "fetching_from_ibkr",
                    instrument_id=instrument_id,
                    retry_count=retry_count,
                    correlation_id=correlation_id,
                )

                # Reason: Call IBKR client to fetch historical bars
                # NOTE: This assumes ibkr_client has a fetch_bars method
                bars = await self.ibkr_client.fetch_bars(
                    instrument_id=instrument_id,
                    start=start,
                    end=end,
                    bar_type_spec=bar_type_spec,
                )

                logger.info(
                    "ibkr_fetch_successful",
                    instrument_id=instrument_id,
                    bar_count=len(bars),
                    retry_count=retry_count,
                    correlation_id=correlation_id,
                )

                return bars

            except Exception as e:
                last_error = e
                retry_count += 1

                logger.warning(
                    "ibkr_fetch_failed_retrying",
                    instrument_id=instrument_id,
                    retry_count=retry_count,
                    max_retries=max_retries,
                    error=str(e),
                    correlation_id=correlation_id,
                )

                if retry_count <= max_retries:
                    # Reason: Exponential backoff: 2^retry_count seconds
                    backoff_seconds = 2**retry_count
                    logger.info(
                        "waiting_before_retry",
                        backoff_seconds=backoff_seconds,
                        correlation_id=correlation_id,
                    )
                    await asyncio.sleep(backoff_seconds)

        # Reason: All retries exhausted
        logger.error(
            "ibkr_fetch_failed_all_retries_exhausted",
            instrument_id=instrument_id,
            max_retries=max_retries,
            last_error=str(last_error),
            correlation_id=correlation_id,
        )

        raise DataNotFoundError(instrument_id, start, end)
