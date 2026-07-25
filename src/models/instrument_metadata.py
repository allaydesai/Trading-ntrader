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

from collections.abc import Iterable
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

    Only ``RESOLVED`` is backtestable. The other three are distinct kinds of
    "not backtestable" and must not be collapsed: ``UNRESOLVED`` was never
    attempted, ``VENUE_UNRESOLVED`` was attempted and failed (the gate blocker),
    and ``EXCLUDED`` was adjudicated by a human and is a permitted gate exit.
    """

    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    VENUE_UNRESOLVED = "VENUE_UNRESOLVED"
    #: Audited, deliberate exclusion recorded in ``venue_exclusions.csv`` with a
    #: written reason and evidence — a delisted or untradeable instrument that no
    #: authoritative source can qualify. Keeps its bars on disk but is never
    #: backtestable, and is reported separately rather than silently dropped.
    EXCLUDED = "EXCLUDED"


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

    @classmethod
    def from_results(cls, results: Iterable["InstrumentMetadata"]) -> "ResolutionSummary":
        """Aggregate resolved metadata into the three per-import counters (Story 1.6).

        Pure, side-effect-free aggregation over one pass:

        - ``resolved`` / ``venue_unresolved`` are mutually-exclusive *status* counts.
        - ``descriptive_gaps`` is an *orthogonal* dimension: a RESOLVED record with an
          ``NA_SENTINEL`` descriptive field is counted in BOTH ``resolved`` and
          ``descriptive_gaps``. The three counters can therefore sum to MORE than
          ``len(results)`` — that is correct (FR6: three independent metrics), not a bug.

        ``UNRESOLVED`` rows (which should not appear post-resolution) count toward
        neither status bucket, but their descriptive gaps still increment.
        """
        resolved = 0
        venue_unresolved = 0
        descriptive_gaps = 0
        for md in results:
            if md.resolution_status == ResolutionStatus.RESOLVED:
                resolved += 1
            elif md.resolution_status == ResolutionStatus.VENUE_UNRESOLVED:
                venue_unresolved += 1
            if _has_descriptive_gap(md):
                descriptive_gaps += 1
        return cls(
            resolved=resolved,
            descriptive_gaps=descriptive_gaps,
            venue_unresolved=venue_unresolved,
        )


#: The exactly-five descriptive fields that may hold ``NA_SENTINEL``. ``venue`` is
#: excluded (a code or ``None`` — never the sentinel); ``asset_type`` / ``ipo_date``
#: are typed and absent-as-``None``.
_DESCRIPTIVE_FIELDS = ("company_name", "sector", "industry", "country", "currency")


def _has_descriptive_gap(md: "InstrumentMetadata") -> bool:
    """True when any of the five descriptive fields equals ``NA_SENTINEL``."""
    return any(getattr(md, field) == NA_SENTINEL for field in _DESCRIPTIVE_FIELDS)
