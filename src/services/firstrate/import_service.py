"""Import pipeline orchestrator for FirstRate data.

Coordinates the per-ticker import loop: parse CSV -> validate -> write Parquet
-> verify integrity -> upsert metadata. Handles error isolation so that a
single ticker failure does not abort the batch.
"""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import structlog
from nautilus_trader.model.data import Bar, BarType

from src.models.catalog import AssetClass, ImportResult
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.instrument_mapper import InstrumentMapper
from src.services.firstrate.metadata_service import MetadataService
from src.services.firstrate.parsers.base import get_parser
from src.services.firstrate.source_probe import compute_source_last_date

logger = structlog.get_logger(__name__)

# Map timeframe step/aggregation to CatalogInstrument bar_count field
_TIMEFRAME_FIELD_MAP = {
    "DAY": "bar_count_daily",
    "HOUR": "bar_count_hourly",
    "MINUTE": "bar_count_minute",
}

#: Decisions returned by :meth:`ImportService._classify_ticker`.
ClassifierDecision = Literal["new", "reimported", "skipped"]


def _bar_count_field_for_timeframe(timeframe: str) -> str:
    """Return the ``CatalogInstrument.bar_count_*`` attribute for a timeframe.

    Mirrors the ``_TIMEFRAME_FIELD_MAP`` lookup used by ``_upsert_metadata``.
    Unknown aggregations fall back to ``bar_count_daily`` so the classifier
    behaves the same as the existing metadata-upsert code path.
    """
    aggregation = timeframe.split("-")[1] if "-" in timeframe else "DAY"
    return _TIMEFRAME_FIELD_MAP.get(aggregation, "bar_count_daily")


class ImportService:
    """Orchestrates per-ticker import from CSV to Parquet catalog.

    Pure orchestrator — delegates parsing, writing, mapping, and metadata
    to injected dependencies. Owns the import loop and error isolation.

    Args:
        catalog_manager: Resolves named catalogs to ParquetDataCatalog.
        metadata_service: CRUD for catalog instrument metadata.
        instrument_mapper: Resolves tickers to Nautilus InstrumentIds.
    """

    def __init__(
        self,
        catalog_manager: CatalogManager,
        metadata_service: MetadataService,
        instrument_mapper: InstrumentMapper,
    ) -> None:
        self._catalog_manager = catalog_manager
        self._metadata_service = metadata_service
        self._instrument_mapper = instrument_mapper

    def import_directory(
        self,
        source_dir: Path,
        catalog_name: str,
        asset_class: AssetClass,
        timeframe: str = "1-DAY-LAST",
    ) -> list[ImportResult]:
        """Import all ticker CSV files from a source directory.

        Args:
            source_dir: Root directory containing alphabetical subdirectories.
            catalog_name: Target catalog name for Parquet output.
            asset_class: Asset class for parser selection and metadata.
            timeframe: Bar timeframe spec (default: "1-DAY-LAST").

        Returns:
            List of ImportResult for each discovered ticker.

        Raises:
            FileNotFoundError: If source_dir does not exist.
            ValueError: If instrument mapper profiles are not loaded.
        """
        if not source_dir.exists():
            raise FileNotFoundError(f"Source directory does not exist: {source_dir}")

        if not self._instrument_mapper.is_loaded(catalog_name):
            raise ValueError(
                f"Instrument mapper not loaded for catalog '{catalog_name}'. "
                "Load company profiles before importing."
            )

        tickers = self._discover_tickers(source_dir)
        logger.info(
            "import_directory_start",
            source_dir=str(source_dir),
            catalog_name=catalog_name,
            asset_class=asset_class.value,
            ticker_count=len(tickers),
        )

        results: list[ImportResult] = []
        for ticker, file_path in tickers:
            result = self._import_ticker(
                ticker=ticker,
                file_path=file_path,
                catalog_name=catalog_name,
                asset_class=asset_class,
                timeframe=timeframe,
            )
            results.append(result)

        success = sum(1 for r in results if r.status == "success")
        failed = sum(1 for r in results if r.status == "failed")
        skipped = sum(1 for r in results if r.outcome == "skipped")
        reimported = sum(1 for r in results if r.outcome == "reimported")
        new = sum(1 for r in results if r.outcome == "new")
        logger.info(
            "import_directory_complete",
            total=len(results),
            success=success,
            failed=failed,
            skipped=skipped,
            reimported=reimported,
            new=new,
        )
        return results

    def _import_ticker(
        self,
        ticker: str,
        file_path: Path,
        catalog_name: str,
        asset_class: AssetClass,
        timeframe: str = "1-DAY-LAST",
    ) -> ImportResult:
        """Execute the full import loop for a single ticker.

        Story 1-7 adds an idempotent re-run short-circuit: ``_classify_ticker``
        decides whether this ticker is ``"skipped"``, ``"reimported"``, or
        ``"new"``. Skipped tickers exit immediately with no parser, no
        ``catalog.write_data``, and no metadata upsert. Reimported / new
        tickers go through the full import path and have their result tagged
        with an ``outcome`` field for the CLI summary.

        Args:
            ticker: Trading symbol (e.g., "SPY").
            file_path: Path to the ticker's CSV file.
            catalog_name: Target catalog name.
            asset_class: Asset class for parser selection.
            timeframe: Bar timeframe spec.

        Returns:
            ImportResult with status, row_count, duration, and outcome.
        """
        start = time.perf_counter()
        try:
            # 0. Classify (story 1-7): metadata is the source of truth.
            decision = self._classify_ticker(
                ticker=ticker,
                file_path=file_path,
                catalog_name=catalog_name,
                timeframe=timeframe,
            )
            if decision == "skipped":
                logger.info(
                    "ticker_import_skipped",
                    ticker=ticker,
                    catalog=catalog_name,
                    timeframe=timeframe,
                    reason="already complete",
                )
                return ImportResult(
                    ticker=ticker,
                    status="skipped",
                    row_count=0,
                    outcome="skipped",
                    duration=time.perf_counter() - start,
                )

            # 1. Resolve instrument ID
            instrument_id = self._instrument_mapper.resolve_instrument_id(ticker, catalog_name)

            # 2. Build BarType
            bar_type = BarType.from_str(f"{instrument_id}-{timeframe}-EXTERNAL")

            # 3. Parse CSV
            parser = get_parser(asset_class)
            bars = parser.parse_file(file_path, instrument_id, bar_type)

            if not bars:
                logger.warning("no_bars_parsed", ticker=ticker)
                return ImportResult(
                    ticker=ticker,
                    status="failed",
                    row_count=0,
                    error="No bars parsed from file",
                    duration=time.perf_counter() - start,
                )

            # 4. Write to catalog
            catalog = self._catalog_manager.resolve_catalog(catalog_name)
            catalog.write_data(bars)

            # 5. Verify row count
            bars_read_back = catalog.bars(bar_types=[str(bar_type)])
            if not self._verify_row_count(bars, bars_read_back):
                return ImportResult(
                    ticker=ticker,
                    status="failed",
                    row_count=len(bars),
                    error=(
                        f"Row count mismatch: wrote {len(bars)}, read back {len(bars_read_back)}"
                    ),
                    duration=time.perf_counter() - start,
                )

            # 6. Verify sample points
            if not self._verify_sample_points(bars, bars_read_back, ticker=ticker):
                return ImportResult(
                    ticker=ticker,
                    status="failed",
                    row_count=len(bars),
                    error="Sample point validation failed",
                    duration=time.perf_counter() - start,
                )

            # 7. Upsert metadata (gatekeeper — only after verification)
            if not self._upsert_metadata(
                ticker=ticker,
                catalog_name=catalog_name,
                bars=bars,
                timeframe=timeframe,
            ):
                return ImportResult(
                    ticker=ticker,
                    status="failed",
                    row_count=len(bars),
                    error="Metadata upsert failed — no instrument record found",
                    duration=time.perf_counter() - start,
                )

            logger.info(
                "ticker_import_success",
                ticker=ticker,
                row_count=len(bars),
                outcome=decision,
            )
            return ImportResult(
                ticker=ticker,
                status="success",
                row_count=len(bars),
                outcome=decision,
                duration=time.perf_counter() - start,
            )

        except Exception as e:
            logger.error(
                "ticker_import_failed",
                ticker=ticker,
                asset_class=asset_class.value
                if isinstance(asset_class, AssetClass)
                else str(asset_class),
                error=str(e),
                exc_info=True,
            )
            return ImportResult(
                ticker=ticker,
                status="failed",
                row_count=0,
                error=str(e),
                duration=time.perf_counter() - start,
            )

    # ------------------------------------------------------------------
    # Idempotent re-run classifier (story 1-7)
    # ------------------------------------------------------------------
    #
    # ADR-5 in _bmad-output/planning-artifacts/architecture.md:212-216 declares
    # that the ``catalog_instruments`` row (not the Parquet file on disk) is
    # the gatekeeper for "what was successfully imported". The classifier
    # must decide skip / reimport / new *solely* from metadata plus a cheap
    # source-CSV last-date probe — never by scanning the filesystem for
    # orphan Parquet files. See that ADR before touching this method.

    def _classify_ticker(
        self,
        ticker: str,
        file_path: Path,
        catalog_name: str,
        timeframe: str,
    ) -> ClassifierDecision:
        """Classify a ticker as ``"skipped"``, ``"reimported"``, or ``"new"``.

        Looks up the existing :class:`CatalogInstrument` row and compares its
        ``date_range_end`` / ``bar_count_<timeframe>`` fields against the
        source CSV's max date (via :func:`compute_source_last_date`). Day-
        granularity comparison avoids hour/minute drift when daily bars are
        stored with an implicit midnight UTC.

        Decisions:

        - Metadata row missing (should not happen post-``load_company_profiles``
          but safe default): ``"new"``.
        - Metadata row present but ``date_range_end`` is ``None`` OR the
          ``bar_count_<tf>`` field is 0: ``"new"`` (orphan or never imported
          *this* timeframe).
        - Source probe returns ``None`` (empty / malformed source): ``"new"``
          so the real import path fails loudly with "No bars parsed from file"
          instead of silently skipping.
        - Source day < metadata day: ``"reimported"`` with a structured
          warning — stale metadata should never mask a source regression.
        - Source day == metadata day: ``"skipped"``.
        - Source day > metadata day: ``"reimported"``.

        Args:
            ticker: Trading symbol.
            file_path: Source CSV path.
            catalog_name: Catalog name to scope the metadata lookup.
            timeframe: Bar timeframe spec (e.g., ``"1-DAY-LAST"``). Used to
                pick the right ``bar_count_*`` field.

        Returns:
            One of ``"skipped"``, ``"reimported"``, or ``"new"``.
        """
        existing = self._metadata_service.get_instrument_sync(catalog_name, ticker)
        source_last = compute_source_last_date(file_path)

        if existing is None:
            self._log_classification(ticker, "new", None, source_last, timeframe)
            return "new"

        metadata_end = existing.date_range_end
        bar_count_field = _bar_count_field_for_timeframe(timeframe)
        bar_count = getattr(existing, bar_count_field, 0) or 0

        # Orphan state or never-imported-this-timeframe.
        if metadata_end is None or bar_count <= 0:
            self._log_classification(ticker, "new", metadata_end, source_last, timeframe)
            return "new"

        # Empty / malformed source — let the real import path fail loudly.
        if source_last is None:
            self._log_classification(ticker, "new", metadata_end, source_last, timeframe)
            return "new"

        source_day = source_last.date()
        metadata_day = metadata_end.date()

        if source_day == metadata_day:
            self._log_classification(ticker, "skipped", metadata_end, source_last, timeframe)
            return "skipped"

        if source_day < metadata_day:
            logger.warning(
                "source_date_behind_metadata",
                ticker=ticker,
                metadata_date_end=metadata_end.isoformat(),
                source_last_date=source_last.isoformat(),
                timeframe=timeframe,
            )
            self._log_classification(ticker, "reimported", metadata_end, source_last, timeframe)
            return "reimported"

        self._log_classification(ticker, "reimported", metadata_end, source_last, timeframe)
        return "reimported"

    @staticmethod
    def _log_classification(
        ticker: str,
        decision: ClassifierDecision,
        metadata_end: datetime | None,
        source_last: datetime | None,
        timeframe: str,
    ) -> None:
        """Emit one structured line per classification decision."""
        logger.debug(
            "ticker_classification",
            ticker=ticker,
            decision=decision,
            metadata_date_end=metadata_end.isoformat() if metadata_end is not None else None,
            source_last_date=source_last.isoformat() if source_last is not None else None,
            timeframe=timeframe,
        )

    def _discover_tickers(self, source_dir: Path) -> list[tuple[str, Path]]:
        """Traverse alphabetical subdirectories and discover ticker files.

        Args:
            source_dir: Root directory with single-letter subdirectories.

        Returns:
            Sorted list of (ticker, file_path) tuples from .txt files.
        """
        tickers: list[tuple[str, Path]] = []
        for subdir in sorted(source_dir.iterdir()):
            if not subdir.is_dir():
                continue
            for file in sorted(subdir.iterdir()):
                if file.suffix == ".txt" and file.is_file():
                    tickers.append((self._extract_ticker(file), file))
        return tickers

    @staticmethod
    def _extract_ticker(file: Path) -> str:
        """Extract the ticker symbol from a FirstRate filename.

        FirstRate filenames follow ``{TICKER}_full_{timeframe}_adjsplitdiv.txt``.
        For other filename patterns, fall back to the stem.

        Args:
            file: Path to the ticker data file.

        Returns:
            The ticker symbol (e.g., "SPY" from "SPY_full_1day_adjsplitdiv.txt").
        """
        stem = file.stem
        if "_full_" in stem:
            return stem.split("_full_", 1)[0]
        return stem

    def _verify_row_count(self, bars_written: list[Bar], bars_read_back: list[Bar]) -> bool:
        """Verify source and catalog row counts match.

        Args:
            bars_written: Bars that were written to catalog.
            bars_read_back: Bars read back from catalog.

        Returns:
            True if counts match exactly.
        """
        match = len(bars_written) == len(bars_read_back)
        if not match:
            logger.warning(
                "row_count_mismatch",
                written=len(bars_written),
                read_back=len(bars_read_back),
            )
        return match

    def _verify_sample_points(
        self,
        source_bars: list[Bar],
        catalog_bars: list[Bar],
        sample_size: int = 10,
        ticker: str = "",
    ) -> bool:
        """Compare first N and last N bars between source and catalog.

        Args:
            source_bars: Original bars from parser.
            catalog_bars: Bars read back from Parquet catalog.
            sample_size: Number of bars to check from each end.
            ticker: Ticker symbol for log context.

        Returns:
            True if all sampled bars match exactly.
        """
        n = min(sample_size, len(source_bars))
        head_src = source_bars[:n]
        head_cat = catalog_bars[:n]
        tail_src = source_bars[-n:]
        tail_cat = catalog_bars[-n:]

        for i, (s, c) in enumerate(zip(head_src, head_cat)):
            if not self._bars_match(s, c):
                logger.warning(
                    "sample_point_mismatch",
                    ticker=ticker,
                    position=f"head[{i}]",
                    source_ts=s.ts_init,
                    catalog_ts=c.ts_init,
                )
                return False

        for i, (s, c) in enumerate(zip(tail_src, tail_cat)):
            if not self._bars_match(s, c):
                logger.warning(
                    "sample_point_mismatch",
                    ticker=ticker,
                    position=f"tail[{i}]",
                    source_ts=s.ts_init,
                    catalog_ts=c.ts_init,
                )
                return False

        return True

    @staticmethod
    def _bars_match(a: Bar, b: Bar) -> bool:
        """Check if two bars have identical OHLCV and timestamp values."""
        return (
            a.ts_init == b.ts_init
            and a.open == b.open
            and a.high == b.high
            and a.low == b.low
            and a.close == b.close
            and a.volume == b.volume
        )

    def _upsert_metadata(
        self,
        ticker: str,
        catalog_name: str,
        bars: list[Bar],
        timeframe: str = "1-DAY-LAST",
    ) -> bool:
        """Upsert catalog instrument metadata after verified import.

        Args:
            ticker: Trading symbol.
            catalog_name: Catalog name.
            bars: Successfully imported bars.
            timeframe: Timeframe spec to determine which bar_count field.

        Returns:
            True if metadata was upserted, False if no instrument record found.
        """
        existing = self._metadata_service.get_instrument_sync(catalog_name, ticker)
        if existing is None:
            logger.error(
                "metadata_upsert_skipped",
                ticker=ticker,
                reason="No instrument record found — company profiles not loaded?",
            )
            return False

        # Convert nanosecond timestamps to datetime
        existing.date_range_start = datetime.fromtimestamp(
            bars[0].ts_init / 1_000_000_000, tz=timezone.utc
        )
        existing.date_range_end = datetime.fromtimestamp(
            bars[-1].ts_init / 1_000_000_000, tz=timezone.utc
        )

        # Determine bar count field from timeframe
        aggregation = timeframe.split("-")[1] if "-" in timeframe else "DAY"
        field_name = _TIMEFRAME_FIELD_MAP.get(aggregation, "bar_count_daily")
        setattr(existing, field_name, len(bars))

        self._metadata_service.upsert_instrument_sync(existing)
        logger.debug(
            "metadata_upserted",
            ticker=ticker,
            bar_count=len(bars),
            field=field_name,
        )
        return True
