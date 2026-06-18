"""Pydantic domain models for provider-agnostic instrument metadata.

Defines the canonical ``NA_SENTINEL`` constant, the ``ResolutionStatus`` and
``AssetType`` enums, the ``InstrumentMetadata`` domain model (with three-state
``N/A`` enforcement, ADR-2), and the ``ResolutionSummary`` aggregate used by the
per-import resolution report (defined here, populated in Story 1.6).

This is the domain layer. The SQLAlchemy ORM counterpart lives in
``src/db/models/instrument_metadata.py`` and shares the class name
``InstrumentMetadata`` — never cross-import them into the same module without
aliasing.
"""

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator

#: Canonical sentinel for a descriptive field the provider returned empty.
#: Defined exactly once here; the whole Phase 2 codebase imports it. A field
#: is one of: a resolved value, this sentinel (descriptive fields only), or
#: DB ``NULL`` (not yet attempted). ``venue`` never holds this sentinel.
NA_SENTINEL = "N/A"

__all__ = [
    "NA_SENTINEL",
    "ResolutionStatus",
    "AssetType",
    "InstrumentMetadata",
    "ResolutionSummary",
]


class ResolutionStatus(str, Enum):
    """Row-level resolution state for a metadata record.

    Members deliberately use ``name == value`` so the string stored by the
    SQLAlchemy ``Enum`` column matches the value, avoiding the
    ``values_callable`` footgun.
    """

    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    VENUE_UNRESOLVED = "VENUE_UNRESOLVED"


class AssetType(str, Enum):
    """FMP-derived asset classification (from ``isEtf`` / ``isFund``).

    Distinct from ``src.models.catalog.AssetClass`` (ETF/STOCK/FUTURES/…) —
    do not conflate or merge the two.
    """

    ETF = "ETF"
    EQUITY = "EQUITY"
    FUND = "FUND"


class InstrumentMetadata(BaseModel):
    """Provider-agnostic resolved metadata for a single instrument.

    Encodes the three-state ``N/A`` rule (ADR-2): a descriptive field is a
    value or ``NA_SENTINEL``; ``venue`` is a real code or ``None`` (never the
    sentinel); an unattempted field is ``None`` (DB ``NULL``).

    Attributes:
        ticker: Trading symbol (primary key, e.g. "SPY").
        metadata_provider: Source provider identifier (e.g. "FMP").
        venue: Nautilus venue code, or ``None`` — never ``NA_SENTINEL``.
        currency: Trading currency (e.g. "USD"), ``NA_SENTINEL``, or ``None``.
        asset_type: FMP-derived classification, or ``None``.
        company_name: Full instrument name, ``NA_SENTINEL``, or ``None``.
        sector: Industry sector, ``NA_SENTINEL``, or ``None``.
        industry: Specific industry, ``NA_SENTINEL``, or ``None``.
        country: Country of domicile, ``NA_SENTINEL``, or ``None``.
        ipo_date: IPO date, or ``None``.
        resolution_status: Row-level resolution state (default UNRESOLVED).
        resolved_at: Timestamp resolution completed, or ``None``.
    """

    ticker: str
    metadata_provider: str
    venue: Optional[str] = None
    currency: Optional[str] = None
    asset_type: Optional[AssetType] = None
    company_name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    ipo_date: Optional[date] = None
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    resolved_at: Optional[datetime] = None

    @field_validator("venue")
    @classmethod
    def venue_never_na_sentinel(cls, v: Optional[str]) -> Optional[str]:
        """Reject the NA sentinel for venue — it is a real code or ``None``."""
        if v == NA_SENTINEL:
            raise ValueError(f"venue must be a real code or None, never {NA_SENTINEL!r}")
        return v


class ResolutionSummary(BaseModel):
    """Per-import resolution counters.

    Defined here in Story 1.2 with zeroed defaults; the counting logic that
    populates it lives in Story 1.6.

    Attributes:
        resolved: Count of fully resolved instruments.
        descriptive_gaps: Count of instruments with at least one descriptive
            field returned empty (``NA_SENTINEL``).
        venue_unresolved: Count of instruments whose venue could not be
            resolved.
    """

    resolved: int = 0
    descriptive_gaps: int = 0
    venue_unresolved: int = 0
