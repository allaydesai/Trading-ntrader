"""Pydantic domain models for the FirstRate data import pipeline.

This module defines data models for catalog configuration, validation,
and import tracking used across the FirstRate data import workflow.
"""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

__all__ = [
    "AssetClass",
    "CatalogConfig",
    "ValidationResult",
    "ImportResult",
    "SchemaMismatch",
    "TimeframeSummary",
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
        status: Import outcome ("success" or "failed").
        row_count: Number of rows imported.
        error: Error message if import failed.
        duration: Time taken in seconds.
    """

    ticker: str
    status: str
    row_count: int = Field(default=0, ge=0)
    error: Optional[str] = None
    duration: float = Field(..., ge=0)


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
