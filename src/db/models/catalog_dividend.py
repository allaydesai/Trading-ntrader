"""SQLAlchemy ORM model for catalog dividend history.

Stores FirstRate dividend records (ex-date + amount) scoped by catalog
and ticker, supporting display alongside ticker data in the explorer.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin


class CatalogDividend(Base, TimestampMixin):
    """Catalog dividend history record.

    Stores a single dividend payment (ex-date + amount) for a ticker in a
    named catalog. Amount is stored as ``Numeric`` (never float) to preserve
    the exact precision delivered by FirstRate.

    Attributes:
        id: Internal database primary key.
        catalog_name: Name of the catalog this dividend belongs to.
        ticker: Trading symbol (e.g., "AAPL").
        ex_date: Ex-dividend date.
        amount: Dividend amount per share, stored as Decimal/Numeric.
        created_at: Record creation timestamp (via TimestampMixin).
    """

    __tablename__ = "catalog_dividends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    catalog_name: Mapped[str] = mapped_column(String(50), nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "catalog_name",
            "ticker",
            "ex_date",
            name="uq_catalog_dividends_catalog_ticker_date",
        ),
        Index(
            "ix_catalog_dividends_catalog_ticker",
            "catalog_name",
            "ticker",
        ),
    )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"<CatalogDividend(ticker={self.ticker}, "
            f"catalog={self.catalog_name}, "
            f"ex_date={self.ex_date}, amount={self.amount})>"
        )
