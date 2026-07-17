"""ETF metadata presentation model with N/A-aware rendering (Story 4.4).

``EtfMetadataPanel`` is a view-layer model (like ``TickerRow`` /
``TickerStatsResponse``) built from the SQLAlchemy ORM ``InstrumentMetadata``
row. It owns the *single* place the three-state N/A rule (ADR-2) is turned into
display strings: the ``display_*`` ``@computed_field`` props each return a
non-empty ``str`` so templates never branch on ``""``/``None`` ad hoc.

Venue is deliberately NOT a descriptive field — it can never be the ``N/A``
sentinel (domain validator), and an unresolved venue is a completeness problem
(Epic 3), so it is rendered distinctly via ``display_venue`` / ``venue_resolved``.
"""

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, computed_field

from src.models.instrument_metadata import NA_SENTINEL, ResolutionStatus

#: Label shown for a ticker whose venue is unresolved (``venue IS NULL``).
#: Distinct from the descriptive ``NA_SENTINEL`` — venue is never the sentinel.
VENUE_UNRESOLVED_LABEL = "Unresolved"


def _na(value: Optional[str]) -> str:
    """Collapse a missing/empty descriptive field to the ``N/A`` sentinel.

    ``None`` (never attempted / DB ``NULL``) and ``""`` both render as
    ``NA_SENTINEL``; an already-``"N/A"`` value is idempotent under ``or``.
    """
    return value if value else NA_SENTINEL


def _normalize_venue(value: Optional[str]) -> Optional[str]:
    """Coerce a blank/whitespace venue to ``None`` (the "unresolved" state).

    The domain contract (ADR-2) is that ``venue`` is a real code or ``None``,
    never ``""``. The nullable ``String`` column has no non-empty check, so a
    stored ``""``/whitespace is normalized here to keep ``display_venue`` and
    ``venue_resolved`` from disagreeing on the resolved/unresolved boundary.
    """
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class EtfMetadataPanel(BaseModel):
    """N/A-aware view model for a single ticker's FMP-resolved metadata.

    Attributes mirror the ``instrument_metadata`` row; the ``display_*`` props
    are the shared rendering contract consumed by ``metadata_panel.html``.
    """

    ticker: str
    metadata_provider: Optional[str] = None
    venue: Optional[str] = None
    currency: Optional[str] = None
    asset_type: Optional[str] = None
    company_name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    ipo_date: Optional[date] = None
    resolution_status: Optional[ResolutionStatus] = None

    @classmethod
    def from_orm_row(cls, row: Any) -> "EtfMetadataPanel":
        """Build the panel from a SQLAlchemy ORM ``InstrumentMetadata`` row."""
        return cls(
            ticker=row.ticker,
            metadata_provider=row.metadata_provider,
            venue=_normalize_venue(row.venue),
            currency=row.currency,
            asset_type=row.asset_type,
            company_name=row.company_name,
            sector=row.sector,
            industry=row.industry,
            country=row.country,
            ipo_date=row.ipo_date,
            resolution_status=row.resolution_status,
        )

    # --- Descriptive fields: value or a clean N/A (shared display contract) ---

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_company_name(self) -> str:
        return _na(self.company_name)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_currency(self) -> str:
        return _na(self.currency)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_asset_type(self) -> str:
        return _na(self.asset_type)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_sector(self) -> str:
        return _na(self.sector)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_industry(self) -> str:
        return _na(self.industry)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_country(self) -> str:
        return _na(self.country)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_ipo_date(self) -> str:
        return self.ipo_date.isoformat() if self.ipo_date else NA_SENTINEL

    # --- Venue: rendered distinctly, never the N/A sentinel (AC3) ---

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_venue(self) -> str:
        return self.venue if self.venue else VENUE_UNRESOLVED_LABEL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def venue_resolved(self) -> bool:
        # Truthiness (not ``is not None``) so this always agrees with
        # ``display_venue`` even if a blank venue slips past normalization.
        return bool(self.venue)
