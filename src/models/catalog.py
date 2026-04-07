"""Pydantic domain models for the FirstRate data import pipeline.

This module defines data models for catalog configuration, validation,
and import tracking used across the FirstRate data import workflow.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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
