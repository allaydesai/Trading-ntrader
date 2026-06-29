"""Pydantic domain models for the FirstRate data import pipeline.

This module defines data models for catalog configuration, validation,
and import tracking used across the FirstRate data import workflow.
"""

from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

#: Allowed values for :attr:`ImportResult.outcome`. ``None`` is the default for
#: legacy call sites (story 1-7 introduced ``outcome``; older callers that pre-
#: date the classifier do not need to set it).
ImportOutcome = Literal["new", "reimported", "skipped"]

__all__ = [
    "AssetClass",
    "CatalogConfig",
    "ValidationResult",
    "ImportResult",
    "SchemaMismatch",
    "TimeframeSummary",
    "TickerDateRange",
    "MetadataEstimate",
    "DryRunReport",
]


class AssetClass(str, Enum):
    """Asset class classification for imported instruments."""

    ETF = "ETF"
    STOCK = "STOCK"
    FUTURES = "FUTURES"
    FX = "FX"
    CRYPTO = "CRYPTO"
    INDEX = "INDEX"
    DELISTED = "DELISTED"


class CatalogConfig(BaseModel):
    """Configuration for a named Parquet data catalog.

    Attributes:
        name: Unique catalog identifier (e.g., "firstrate-etf").
        path: Filesystem path to the Parquet catalog directory.
        format: Data format (default: "parquet").
    """

    name: str = Field(..., min_length=1, description="Unique catalog name")
    path: str = Field(..., min_length=1, description="Path to catalog directory")
    format: str = Field(default="parquet", description="Data format")


class ValidationResult(BaseModel):
    """Result of validating a data file before import.

    Attributes:
        valid: Whether the file passed all validation checks.
        errors: List of validation error messages.
        row_count: Total number of rows in the file.
        invalid_rows: Number of rows that failed validation.
    """

    valid: bool
    errors: list[str] = Field(default_factory=list)
    row_count: int = Field(..., ge=0)
    invalid_rows: int = Field(default=0, ge=0)


class ImportResult(BaseModel):
    """Result of importing a single ticker's data.

    Attributes:
        ticker: Symbol that was imported (e.g., "SPY").
        status: Import status. One of ``"success"``, ``"failed"``, or
            ``"skipped"`` (the last added in story 1-7 for idempotent re-runs).
            Kept as a plain ``str`` rather than a ``Literal`` so existing
            callers that pass other sentinel strings do not break.
        row_count: Number of rows imported (always ``0`` for skipped tickers).
        error: Error message if import failed.
        duration: Time taken in seconds.
        outcome: Classifier decision for this ticker. ``"new"`` for first-time
            imports (or orphans being healed), ``"reimported"`` when an
            existing complete import was overwritten because the source moved
            forward, and ``"skipped"`` when the metadata row already matches
            the source's max date at day granularity. ``None`` for legacy
            callers (e.g., direct tests) and for failed imports where the
            classifier never ran.
    """

    ticker: str
    status: str
    row_count: int = Field(default=0, ge=0)
    error: Optional[str] = None
    duration: float = Field(..., ge=0)
    outcome: Optional[ImportOutcome] = None


class SchemaMismatch(BaseModel):
    """A file that cannot be imported as-is under the expected schema.

    Attributes:
        file_path: Path to the offending file.
        expected_columns: Expected column count (FirstRate 6-column schema).
        detected_columns: Column count found in the file's first non-blank line.
            0 indicates the file was empty, unreadable, undecodable, or had a
            filename pattern the scanner did not recognize; check ``reason`` to
            disambiguate.
        reason: Optional human-readable classification (e.g.,
            ``"unrecognized filename pattern"``, ``"decode error"``,
            ``"empty file"``). ``None`` for plain column-count mismatches.
    """

    file_path: Path
    expected_columns: int = Field(default=6, ge=0)
    detected_columns: int = Field(..., ge=0)
    reason: Optional[str] = None


class TimeframeSummary(BaseModel):
    """Per-timeframe scan aggregate for a dry-run report.

    Attributes:
        ticker_count: Distinct ticker count under this timeframe.
        file_count: Number of source files under this timeframe.
        source_bytes: Sum of ``Path.stat().st_size`` for all source files.
    """

    ticker_count: int = Field(..., ge=0)
    file_count: int = Field(..., ge=0)
    source_bytes: int = Field(..., ge=0)


class TickerDateRange(BaseModel):
    """Observed first/last data dates for a single ticker (Story 2.3).

    Derived cheaply by the dry-run scanner from the first and last non-blank
    data lines of a ticker's source files — never a full parse, no Nautilus, no
    DB. Dates are stored as ISO ``YYYY-MM-DD`` strings (lexical order ==
    chronological order); the time-of-day portion of intraday rows is dropped.

    Attributes:
        earliest: Earliest observed data date across the ticker's files.
        latest: Latest observed data date across the ticker's files.
    """

    earliest: str = Field(..., min_length=1)
    latest: str = Field(..., min_length=1)


class MetadataEstimate(BaseModel):
    """FMP-aware, offline metadata-resolution estimate for a dry-run (Story 2.3).

    Classifies the distinct scanned tickers using ONLY locally-cached metadata
    state (no network / no provider call): a ticker is "cached" iff the
    instrument-metadata store already holds a ``RESOLVED`` record for it
    (``ResolutionStatus.RESOLVED``); everything else "needs resolution". The
    lookup arrives via an injected read-only reader so the dry-run scanner stays
    DB-free and Nautilus-free.

    Attributes:
        total_tickers: Distinct tickers considered (== ``distinct_ticker_count``).
        cached_count: Tickers already RESOLVED in the metadata store.
        needs_resolution_count: Tickers that would require FMP resolution.
    """

    total_tickers: int = Field(..., ge=0)
    cached_count: int = Field(..., ge=0)
    needs_resolution_count: int = Field(..., ge=0)


class DryRunReport(BaseModel):
    """Result of a ``--dry-run`` directory scan.

    Dry-run scans are read-only smoke tests: no Parquet files, no DB rows,
    no Nautilus imports. See ``src/services/firstrate/dry_run.py``.

    Attributes:
        asset_class: Asset class the scan was run against.
        source_path: The input directory as a string (stringified ``Path``).
        catalog: Optional target catalog name, surfaced only in the report
            header for operator confirmation (the dry-run never writes).
        timeframes: Per-timeframe aggregates keyed by Nautilus timeframe spec
            (e.g., ``"1-DAY-LAST"``).
        total_file_count: Total countable ``.txt`` files across all timeframes
            (files with unrecognized filename patterns are excluded and appear
            in ``schema_mismatches`` instead).
        total_source_bytes: Sum of source CSV bytes across all countable files.
        estimated_parquet_bytes: Projected Parquet output size (see
            ``estimate_parquet_bytes``).
        distinct_ticker_count: Number of distinct tickers across all timeframes.
            Unlike summing ``TimeframeSummary.ticker_count`` (which double-counts
            tickers appearing in multiple timeframes), this is the true
            set-cardinality surfaced on the TOTAL row.
        unreadable_count: Files the scanner encountered but could not ``stat()``
            (permission denied, I/O errors). Not included in the other totals.
        schema_mismatches: Files whose sampled first line did not parse as the
            expected 6-column FirstRate schema, plus files with unrecognized
            filenames or encoding errors. Empty if the scan is clean.
        ticker_date_ranges: Per-ticker earliest/latest observed data dates
            (Story 2.3). Empty unless date-range derivation was requested (the
            ETF dry-run path); keyed by ticker symbol.
        metadata_estimate: FMP-aware, offline metadata-resolution estimate
            (Story 2.3). ``None`` unless a cache reader was injected (the ETF
            dry-run path, or when the DB is unavailable and the estimate is
            gracefully omitted).
    """

    asset_class: AssetClass
    source_path: str
    catalog: Optional[str] = None
    timeframes: dict[str, TimeframeSummary]
    total_file_count: int = Field(..., ge=0)
    total_source_bytes: int = Field(..., ge=0)
    estimated_parquet_bytes: int = Field(..., ge=0)
    distinct_ticker_count: int = Field(default=0, ge=0)
    unreadable_count: int = Field(default=0, ge=0)
    schema_mismatches: list[SchemaMismatch] = Field(default_factory=list)
    ticker_date_ranges: dict[str, TickerDateRange] = Field(default_factory=dict)
    metadata_estimate: Optional[MetadataEstimate] = None
