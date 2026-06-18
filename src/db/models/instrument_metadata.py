"""SQLAlchemy ORM model for provider-agnostic instrument metadata cache.

Persists resolved metadata keyed by ticker alone (decoupled from any catalog),
readable by both the web (async) and CLI (sync) execution paths. The Pydantic
domain counterpart shares the class name ``InstrumentMetadata`` and lives in
``src/models/instrument_metadata.py`` — never cross-import them unaliased.
"""

from datetime import date, datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Date, Index, String
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin
from src.models.instrument_metadata import ResolutionStatus


class InstrumentMetadata(Base, TimestampMixin):
    """Provider-agnostic resolved metadata cache record.

    Keyed by ``ticker`` (PK) — decoupled from any named catalog. Stores the
    three-state ``N/A`` model (ADR-2): descriptive fields hold a value, the
    ``"N/A"`` sentinel, or ``NULL``; ``venue`` is a real code or ``NULL``
    (never the sentinel). The ``resolution_status`` enum makes the venue
    completeness gate a trivial indexed query.

    Attributes:
        ticker: Trading symbol (primary key, e.g. "SPY").
        metadata_provider: Source provider identifier (e.g. "FMP").
        venue: Nautilus venue code, or NULL — never "N/A".
        currency: Trading currency (nullable).
        asset_type: FMP-derived classification stored as the enum value (nullable).
        company_name: Full instrument name (nullable).
        sector: Industry sector (nullable).
        industry: Specific industry (nullable).
        country: Country of domicile (nullable).
        ipo_date: IPO date (nullable).
        resolution_status: Row-level resolution state (not null).
        resolved_at: Timestamp resolution completed (nullable).
        updated_at: Last update timestamp (auto-set on update).
        created_at: Record creation timestamp (via TimestampMixin).
    """

    __tablename__ = "instrument_metadata"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    metadata_provider: Mapped[str] = mapped_column(String(20), nullable=False)
    venue: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    asset_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ipo_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    resolution_status: Mapped[ResolutionStatus] = mapped_column(
        sa.Enum(ResolutionStatus, name="resolution_status", create_type=False),
        nullable=False,
        default=ResolutionStatus.UNRESOLVED,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (Index("ix_instrument_metadata_resolution_status", "resolution_status"),)

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<InstrumentMetadata(ticker={self.ticker}, "
            f"provider={self.metadata_provider}, "
            f"status={self.resolution_status})>"
        )
