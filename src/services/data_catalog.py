"""
Data Catalog Service - Facade for Parquet catalog operations.

This module provides a high-level service layer for interacting with
the Nautilus Trader ParquetDataCatalog, including availability checking,
data queries, and write operations with structured logging.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import structlog
from dotenv import load_dotenv
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

# Load environment variables from .env file
load_dotenv()

from src.models.catalog_metadata import CatalogAvailability  # noqa: E402
from src.services.exceptions import (  # noqa: E402
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
)
from src.services.ibkr_client import IBKRHistoricalClient  # noqa: E402

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

    def __init__(
        self,
        catalog_path: str | Path | None = None,
        ibkr_client: IBKRHistoricalClient | None = None,
    ) -> None:
        """
        Initialize DataCatalogService.

        Args:
            catalog_path: Path to catalog root. If None, uses
                         environment variable NAUTILUS_PATH or
                         defaults to "./data/catalog"
            ibkr_client: Optional IBKR client for data fetching. If None,
                        creates client lazily when first needed (avoids
                        unnecessary connection attempts during backtests).

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

        # Reason: Store provided IBKR client or None for lazy initialization
        # This avoids creating connections during backtests when data is already in catalog
        self._ibkr_client = ibkr_client
        self._ibkr_client_initialized = ibkr_client is not None

        logger.info(
            "data_catalog_initialized",
            catalog_path=str(self.catalog_path),
        )

        # Reason: Build availability cache on startup
        self._rebuild_availability_cache()

    @property
    def ibkr_client(self) -> IBKRHistoricalClient:
        """
        Lazy-initialized IBKR client property.

        Creates the IBKR client on first access using environment variables.
        This avoids unnecessary connection attempts during backtests when
        data is already available in the catalog.

        Returns:
            IBKRHistoricalClient instance

        Note:
            Connection settings are read from environment variables:
            - IBKR_HOST (default: 127.0.0.1)
            - IBKR_PORT (default: 7497, but typically set to 4002 for Gateway paper)
            - IBKR_CLIENT_ID (default: 10)

            The .env file should be loaded before this service is initialized.
        """
        if not self._ibkr_client_initialized:
            # Reason: Create IBKR client with env settings on first access
            # Strip whitespace and handle inline comments
            ibkr_host = os.environ.get("IBKR_HOST", "127.0.0.1").split("#")[0].strip()
            ibkr_port_str = os.environ.get("IBKR_PORT", "7497").split("#")[0].strip()
            ibkr_client_id_str = os.environ.get("IBKR_CLIENT_ID", "10").split("#")[0].strip()

            ibkr_port = int(ibkr_port_str)
            ibkr_client_id = int(ibkr_client_id_str)

            self._ibkr_client = IBKRHistoricalClient(
                host=ibkr_host,
                port=ibkr_port,
                client_id=ibkr_client_id,
            )
            self._ibkr_client_initialized = True

            logger.info(
                "ibkr_client_lazy_initialized",
                host=ibkr_host,
                port=ibkr_port,
                client_id=ibkr_client_id,
            )

        # At this point, _ibkr_client is guaranteed to be non-None
        assert self._ibkr_client is not None
        return self._ibkr_client

    def _rebuild_availability_cache(self) -> None:
        """
        Rebuild the in-memory availability cache by scanning catalog.

        This method scans the Nautilus Parquet catalog directory structure to build
        metadata about available instruments, bar types, and date ranges.
        Called automatically on service initialization.

        Nautilus catalog structure:
        {catalog_path}/data/bar/{instrument_id}-{bar_type_spec}-EXTERNAL/TIMESTAMP_TIMESTAMP.parquet
        """
        logger.info("rebuilding_availability_cache")

        # Reason: Clear existing cache before rebuild
        self.availability_cache.clear()

        # Reason: Nautilus stores bar data in catalog_path/data/bar/
        bar_data_path = self.catalog_path / "data" / "bar"
        if not bar_data_path.exists():
            logger.warning(
                "bar_data_path_not_found",
                path=str(bar_data_path),
            )
            return

        # Reason: Iterate through bar type directories
        # Directory format: {instrument_id}-{bar_type_spec}-EXTERNAL
        for bar_type_dir in bar_data_path.iterdir():
            if not bar_type_dir.is_dir():
                continue

            # Reason: Parse directory name to extract instrument_id and bar_type_spec
            # Example: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
            dir_name = bar_type_dir.name
            if not dir_name.endswith("-EXTERNAL"):
                logger.debug("skipping_non_external_dir", dir_name=dir_name)
                continue

            # Reason: Remove "-EXTERNAL" suffix
            name_without_external = dir_name[:-9]  # Remove "-EXTERNAL"

            # Reason: Split into instrument_id and bar_type_spec
            # Format: {instrument_id}-{bar_type_spec}
            # instrument_id contains ".", bar_type_spec contains hyphens
            # Example: "AAPL.NASDAQ-1-MINUTE-LAST" -> "AAPL.NASDAQ" + "1-MINUTE-LAST"
            parts = name_without_external.split("-")
            if len(parts) < 4:  # Need at least SYMBOL.VENUE-N-PERIOD-PRICE
                logger.warning(
                    "invalid_bar_type_dir_format",
                    dir_name=dir_name,
                )
                continue

            # Reason: Find the split point between instrument_id and bar_type_spec
            # instrument_id is everything up to the first part that looks like a number
            split_idx = 0
            for i, part in enumerate(parts):
                if part.isdigit() or (i > 0 and "." in parts[i - 1]):
                    # Found the bar type spec start (number like "1" in "1-MINUTE-LAST")
                    split_idx = i
                    break

            if split_idx == 0:
                logger.warning(
                    "could_not_parse_bar_type_dir",
                    dir_name=dir_name,
                )
                continue

            instrument_id = "-".join(parts[:split_idx])
            bar_type_spec = "-".join(parts[split_idx:])

            # Reason: Find all Parquet files
            parquet_files = list(bar_type_dir.glob("*.parquet"))
            if not parquet_files:
                continue

            # Reason: Extract timestamps from filenames
            # Format: YYYY-MM-DDTHH-MM-SS-NNNNNNNNNZ_YYYY-MM-DDTHH-MM-SS-NNNNNNNNNZ.parquet
            start_timestamps = []
            end_timestamps = []
            total_rows = 0

            for file in parquet_files:
                try:
                    # Reason: Parse timestamp range from filename
                    filename = file.stem  # Remove .parquet extension
                    start_str, end_str = filename.split("_")

                    # Reason: Parse ISO8601-like timestamp from Nautilus format
                    # Convert "2023-12-29T20-01-00-000000000Z" or
                    # "2023-12-29T23-59-59-999999999Z" to datetime
                    # Remove nanoseconds (any 9-digit number) and Z suffix using regex
                    start_clean = re.sub(r"-\d{9}Z$", "", start_str)
                    end_clean = re.sub(r"-\d{9}Z$", "", end_str)

                    # Replace hyphens in time part with colons
                    # Format is "YYYY-MM-DDTHH-MM-SS"
                    # We need to keep first two hyphens (date), replace next two (time)
                    parts_start = start_clean.split("T")
                    if len(parts_start) == 2:
                        date_part = parts_start[0]
                        time_part = parts_start[1].replace("-", ":")
                        start_datetime = datetime.strptime(
                            f"{date_part}T{time_part}", "%Y-%m-%dT%H:%M:%S"
                        ).replace(tzinfo=timezone.utc)
                    else:
                        # Fallback to date only
                        start_datetime = datetime.strptime(parts_start[0], "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )

                    parts_end = end_clean.split("T")
                    if len(parts_end) == 2:
                        date_part = parts_end[0]
                        time_part = parts_end[1].replace("-", ":")
                        end_datetime = datetime.strptime(
                            f"{date_part}T{time_part}", "%Y-%m-%dT%H:%M:%S"
                        ).replace(tzinfo=timezone.utc)
                    else:
                        # Fallback to date only
                        end_datetime = datetime.strptime(parts_end[0], "%Y-%m-%d").replace(
                            tzinfo=timezone.utc
                        )

                    start_timestamps.append(start_datetime)
                    end_timestamps.append(end_datetime)

                    # Reason: Estimate row count from file size
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

            if not start_timestamps or not end_timestamps:
                continue

            # Reason: Create availability metadata with full timestamp range
            cache_key = f"{instrument_id}_{bar_type_spec}"
            availability = CatalogAvailability(
                instrument_id=instrument_id,
                bar_type_spec=bar_type_spec,
                start_date=min(start_timestamps),
                end_date=max(end_timestamps),
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

    def _quarantine_corrupted_file(self, file_path: Path) -> None:
        """
        Move corrupted Parquet file to quarantine directory.

        Args:
            file_path: Path to corrupted Parquet file

        Raises:
            CatalogError: If quarantine operation fails

        Example:
            >>> service = DataCatalogService()
            >>> corrupted = Path("data/catalog/AAPL.NASDAQ/1-MIN/2024-01-01.parquet")
            >>> service._quarantine_corrupted_file(corrupted)
        """
        # Reason: Create .corrupt directory if it doesn't exist
        quarantine_dir = self.catalog_path / ".corrupt"
        quarantine_dir.mkdir(exist_ok=True)

        try:
            # Reason: Preserve directory structure in quarantine
            relative_path = file_path.relative_to(self.catalog_path)
            quarantine_path = quarantine_dir / relative_path
            quarantine_path.parent.mkdir(parents=True, exist_ok=True)

            # Reason: Move file to quarantine (not copy, to avoid data duplication)
            import shutil

            shutil.move(str(file_path), str(quarantine_path))

            logger.warning(
                "file_quarantined",
                original_path=str(file_path),
                quarantine_path=str(quarantine_path),
            )

        except Exception as e:
            logger.error(
                "quarantine_failed",
                file_path=str(file_path),
                error=str(e),
            )
            raise CatalogError(f"Failed to quarantine corrupted file {file_path}: {e}") from e

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

            # Reason: Construct bar type string for catalog query
            # Format: {instrument_id}-{bar_type_spec}-EXTERNAL
            bar_type_str = f"{instrument_id}-{bar_type_spec}-EXTERNAL"
            bar_type = BarType.from_str(bar_type_str)

            # Reason: Query catalog using Nautilus bars() API with bar_type filter
            bars = self.catalog.bars(
                instrument_ids=[instrument_id],
                bar_type=bar_type,
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

                # Reason: Attempt to quarantine corrupted files
                instrument_dir = self.catalog_path / instrument_id
                if instrument_dir.exists():
                    bar_type_dir = instrument_dir / bar_type_spec.replace("-", "_")
                    if bar_type_dir.exists():
                        # Reason: Find and quarantine corrupted Parquet files
                        for file in bar_type_dir.glob("*.parquet"):
                            try:
                                self._quarantine_corrupted_file(file)
                            except CatalogError as qe:
                                logger.error(
                                    "quarantine_error_continuing",
                                    file=str(file),
                                    error=str(qe),
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

    def load_instrument(self, instrument_id: str) -> object | None:
        """
        Load instrument definition from the Parquet catalog.

        Args:
            instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")

        Returns:
            Nautilus Instrument object if found, None otherwise

        Example:
            >>> service = DataCatalogService()
            >>> instrument = service.load_instrument("AAPL.NASDAQ")
        """
        try:
            # Reason: Query all instruments from catalog
            instruments = self.catalog.instruments()

            # Reason: Find the matching instrument by ID
            for instrument in instruments:
                if str(instrument.id) == instrument_id:
                    logger.info(
                        "instrument_loaded_from_catalog",
                        instrument_id=instrument_id,
                    )
                    return instrument

            logger.debug(
                "instrument_not_found_in_catalog",
                instrument_id=instrument_id,
            )
            return None

        except Exception as e:
            logger.warning(
                "failed_to_load_instrument",
                instrument_id=instrument_id,
                error=str(e),
            )
            return None

    async def fetch_instrument_from_ibkr(self, instrument_id: str) -> object | None:
        """
        Fetch instrument definition from IBKR and save to catalog.

        This is a one-time operation to backfill instruments for existing catalog data.

        Args:
            instrument_id: Instrument identifier (e.g., "AAPL.NASDAQ")

        Returns:
            Nautilus Instrument object if found, None otherwise

        Raises:
            IBKRConnectionError: If IBKR is not available
        """
        # Reason: Check if IBKR is available
        if not await self._is_ibkr_available():
            raise IBKRConnectionError("IBKR connection not available. Cannot fetch instrument.")

        try:
            # Reason: Request instrument from IBKR
            logger.info(
                "fetching_instrument_from_ibkr",
                instrument_id=instrument_id,
            )

            # Reason: Create InstrumentId object from string
            from nautilus_trader.model.identifiers import InstrumentId

            nautilus_instrument_id = InstrumentId.from_str(instrument_id)

            instruments = await self.ibkr_client.client.request_instruments(
                instrument_ids=[nautilus_instrument_id],
            )

            if not instruments:
                logger.warning(
                    "instrument_not_found_in_ibkr",
                    instrument_id=instrument_id,
                )
                return None

            instrument = instruments[0]

            # Reason: Save instrument to catalog for future use
            logger.info(
                "persisting_instrument_to_catalog",
                instrument_id=instrument_id,
            )
            self.catalog.write_data([instrument])

            logger.info(
                "instrument_fetch_successful",
                instrument_id=instrument_id,
            )

            return instrument

        except Exception as e:
            logger.error(
                "instrument_fetch_failed",
                instrument_id=instrument_id,
                error=str(e),
            )
            raise

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

    async def _is_ibkr_available(self) -> bool:
        """
        Check if IBKR connection is available for data fetching.

        Attempts to connect if not already connected.

        Returns:
            True if IBKR client is connected and ready, False otherwise

        Example:
            >>> service = DataCatalogService()
            >>> if await service._is_ibkr_available():
            ...     print("Can fetch from IBKR")
        """
        # Reason: Check if IBKR client has been initialized
        if not hasattr(self, "ibkr_client"):
            logger.debug("ibkr_client_not_initialized")
            return False

        # Reason: Check if IBKR client is connected, connect if needed
        if not self.ibkr_client.is_connected:
            logger.info("ibkr_not_connected_attempting_connection")
            try:
                await self.ibkr_client.connect(timeout=10)
                logger.info("ibkr_connection_successful")
            except Exception as e:
                logger.error("ibkr_connection_failed", error=str(e))
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
                f"{availability.start_date.isoformat()} to {availability.end_date.isoformat()}"
                if availability
                else "None"
            ),
            correlation_id=correlation_id,
        )

        # Reason: Check if IBKR is available
        if not await self._is_ibkr_available():
            logger.error(
                "ibkr_unavailable_cannot_fetch",
                instrument_id=instrument_id,
                correlation_id=correlation_id,
            )
            raise IBKRConnectionError(
                "IBKR connection not available. Cannot fetch missing data. "
                "Ensure IBKR Gateway is running with 'docker compose up ibgateway'."
            )

        # Reason: Fetch data and instrument from IBKR with retry logic
        bars, instrument = await self._fetch_from_ibkr_with_retry(
            instrument_id=instrument_id,
            start=start,
            end=end,
            bar_type_spec=bar_type_spec,
            max_retries=max_retries,
            correlation_id=correlation_id,
        )

        # Reason: Write fetched instrument to catalog first (required for bars)
        if instrument is not None:
            logger.info(
                "persisting_instrument_to_catalog",
                instrument_id=instrument_id,
                correlation_id=correlation_id,
            )
            self.catalog.write_data([instrument])
        else:
            logger.warning(
                "instrument_not_available_skipping_persistence",
                instrument_id=instrument_id,
                correlation_id=correlation_id,
            )

        # Reason: Write fetched bars to catalog for future use
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
    ) -> tuple[List[Bar], object | None]:
        """
        Fetch data and instrument from IBKR with exponential backoff retry logic.

        Args:
            instrument_id: Instrument identifier
            start: Start datetime (UTC)
            end: datetime (UTC)
            bar_type_spec: Bar type specification
            max_retries: Maximum retry attempts
            correlation_id: Optional correlation ID for logging

        Returns:
            Tuple of (bars, instrument) where bars is a list of Bar objects
            and instrument is the Nautilus Instrument object (or None if unavailable)

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

                # Reason: Call IBKR client to fetch historical bars and instrument
                # fetch_bars now returns (bars, instrument) tuple
                bars, instrument = await self.ibkr_client.fetch_bars(
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

                return bars, instrument

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

    def scan_catalog(self) -> Dict[str, List[CatalogAvailability]]:
        """
        Scan catalog and return all available instruments with metadata.

        Returns:
            Dictionary mapping instrument_id to list of CatalogAvailability objects
            (one per bar type)

        Example:
            >>> service = DataCatalogService()
            >>> catalog_data = service.scan_catalog()
            >>> for instrument_id, availabilities in catalog_data.items():
            ...     print(f"{instrument_id}: {len(availabilities)} bar types")
        """
        logger.info("scanning_catalog", catalog_path=str(self.catalog_path))

        # Reason: Group by instrument_id
        result: Dict[str, List[CatalogAvailability]] = {}

        for key, availability in self.availability_cache.items():
            instrument_id = availability.instrument_id
            if instrument_id not in result:
                result[instrument_id] = []
            result[instrument_id].append(availability)

        logger.info(
            "catalog_scan_complete",
            instrument_count=len(result),
            total_entries=len(self.availability_cache),
        )

        return result

    def detect_gaps(
        self,
        instrument_id: str,
        bar_type_spec: str,
        start_date: datetime,
        end_date: datetime,
    ) -> List[Dict[str, datetime]]:
        """
        Detect gaps in available data for given date range.

        Args:
            instrument_id: Instrument to check
            bar_type_spec: Bar type specification
            start_date: Start of requested range
            end_date: End of requested range

        Returns:
            List of gap dictionaries with 'start' and 'end' keys

        Example:
            >>> gaps = service.detect_gaps(
            ...     "AAPL.NASDAQ", "1-MINUTE-LAST",
            ...     datetime(2024, 1, 1), datetime(2024, 1, 31)
            ... )
            >>> for gap in gaps:
            ...     print(f"Gap: {gap['start']} to {gap['end']}")
        """
        # Reason: Ensure dates are timezone-aware for comparison
        import pytz

        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=pytz.UTC)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=pytz.UTC)

        availability = self.get_availability(instrument_id, bar_type_spec)

        if not availability:
            # Reason: No data available = entire range is a gap
            return [{"start": start_date, "end": end_date}]

        gaps = []

        # Reason: Gap at the beginning
        if availability.start_date > start_date:
            gaps.append(
                {
                    "start": start_date,
                    "end": min(availability.start_date, end_date),
                }
            )

        # Reason: Gap at the end
        if availability.end_date < end_date:
            gaps.append(
                {
                    "start": max(availability.end_date, start_date),
                    "end": end_date,
                }
            )

        logger.debug(
            "gap_detection_complete",
            instrument_id=instrument_id,
            bar_type_spec=bar_type_spec,
            gap_count=len(gaps),
        )

        return gaps
