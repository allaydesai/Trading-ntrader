"""SQLAlchemy ORM model for catalog stock split history.

Stores FirstRate stock split records (effective date + decimal ratio)
scoped by catalog and ticker. The ratio is the raw decimal delivered by
FirstRate (new shares per old share); reverse splits have ratio < 1. Any
"4:1"-style display string is derived in the explorer (Story 4.2), not stored.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class CatalogStockSplit(Base, TimestampMixin):
    """Catalog stock split history record.

    Stores a single split event (effective date + ratio) for a ticker in a
    named catalog. Ratio is stored as ``Numeric`` (never float) exactly as
    delivered (e.g. ``4``, ``7``, ``0.5`` for reverse splits).

    Attributes:
        id: Internal database primary key.
        catalog_name: Name of the catalog this split belongs to.
        ticker: Trading symbol (e.g., "AAPL").
        effective_date: Date the split became effective.
        ratio: New shares per old share, stored as Decimal/Numeric.
        created_at: Record creation timestamp (via TimestampMixin).
    """

    __tablename__ = "catalog_stock_splits"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    catalog_name: Mapped[str] = mapped_column(String(50), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    ratio: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "catalog_name",
            "ticker",
            "effective_date",
            name="uq_catalog_stock_splits_catalog_ticker_date",
        ),
        Index(
            "ix_catalog_stock_splits_catalog_ticker",
            "catalog_name",
            "ticker",
        ),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<CatalogStockSplit(ticker={self.ticker}, "
            f"catalog={self.catalog_name}, "
            f"effective_date={self.effective_date}, ratio={self.ratio})>"
        )
