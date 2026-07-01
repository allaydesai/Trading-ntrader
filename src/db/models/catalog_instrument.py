"""SQLAlchemy ORM model for catalog instrument metadata.

Tracks imported instruments and their metadata across named catalogs,
supporting the FirstRate data import pipeline.
"""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Date, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class CatalogInstrument(Base, TimestampMixin):
    """Catalog instrument metadata record.

    Stores identity and import metadata for instruments in a named catalog.
    Supports the dual async/sync repository pattern for web and CLI access.

    Attributes:
        id: Internal database primary key.
        ticker: Trading symbol (e.g., "SPY").
        nautilus_id: Nautilus Trader instrument ID (e.g., "SPY.ARCA").
        asset_class: Asset class as string (e.g., "ETF").
        catalog_name: Name of the catalog this instrument belongs to.
        exchange: Exchange code (e.g., "ARCA", "XNAS").
        name: Full instrument name.
        sector: Industry sector (nullable).
        industry: Specific industry (nullable).
        ipo_date: IPO date (nullable).
        country: Country of domicile (nullable, e.g. "US").
        state: State/region of domicile (nullable, e.g. "CA").
        date_range_start: Earliest imported data timestamp (nullable).
        date_range_end: Latest imported data timestamp (nullable).
        date_range_end_daily: Latest daily bar timestamp (nullable).
        date_range_end_hourly: Latest hourly bar timestamp (nullable).
        date_range_end_minute: Latest 1-minute bar timestamp (nullable).
        date_range_end_5min: Latest 5-minute bar timestamp (nullable).
        date_range_end_30min: Latest 30-minute bar timestamp (nullable).
        bar_count_daily: Number of daily bars imported.
        bar_count_hourly: Number of hourly bars imported.
        bar_count_minute: Number of 1-minute bars imported.
        bar_count_5min: Number of 5-minute bars imported.
        bar_count_30min: Number of 30-minute bars imported.
        updated_at: Last update timestamp (auto-set on update).
        created_at: Record creation timestamp (via TimestampMixin).
    """

    __tablename__ = "catalog_instruments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    nautilus_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    catalog_name: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ipo_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    date_range_start: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    date_range_end: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    # Per-timeframe latest-bar timestamps. Bar counts are already tracked
    # per timeframe; the end-date must be too, or the idempotent re-run
    # classifier compares every timeframe against the finest timeframe's end
    # (the shared date_range_end after a full import) and re-imports on
    # every run. See ImportService._classify_ticker / _upsert_metadata.
    date_range_end_daily: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    date_range_end_hourly: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    date_range_end_minute: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    date_range_end_5min: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    date_range_end_30min: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    bar_count_daily: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bar_count_hourly: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bar_count_minute: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bar_count_5min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bar_count_30min: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_catalog_instruments_ticker", "ticker"),
        Index(
            "ix_catalog_instruments_catalog_asset",
            "catalog_name",
            "asset_class",
        ),
        UniqueConstraint(
            "catalog_name",
            "ticker",
            name="uq_catalog_instruments_catalog_ticker",
        ),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<CatalogInstrument(ticker={self.ticker}, "
            f"catalog={self.catalog_name}, "
            f"asset_class={self.asset_class})>"
        )
