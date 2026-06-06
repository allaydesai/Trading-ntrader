"""Dual repository for the catalog_dividends table.

Provides both async (web) and sync (CLI) access to catalog dividend
history, following the project's dual repository pattern. The write path
uses set-replace semantics (``replace_for_ticker``) so a re-run produces no
duplicate rows and leaves no orphans when a ticker's history shrinks (AC-5).
"""

from typing import List, Sequence

from sqlalchemy import and_, delete, literal, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.catalog_dividend import CatalogDividend


class CatalogDividendRepository:
    """Async repository for catalog dividend operations.

    Used by web API endpoints (Story 4.2) via FastAPI dependency injection.

    Attributes:
        session: Async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_ticker(
        self,
        catalog_name: str,
        ticker: str,
        rows: Sequence[CatalogDividend],
    ) -> None:
        """Replace all dividend rows for a ``(catalog_name, ticker)`` pair.

        Deletes existing rows then bulk-inserts the new set, inside the
        caller's transaction (flush only — the caller commits). Set-replace
        avoids duplicate rows on re-run and orphans when history shrinks (AC-5).

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.
            rows: New dividend rows (``catalog_name``/``ticker`` are set here).

        Raises:
            DuplicateRecordError: If the unique constraint is violated.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            await self.session.execute(
                delete(CatalogDividend).where(
                    and_(
                        CatalogDividend.catalog_name == catalog_name,
                        CatalogDividend.ticker == ticker,
                    )
                )
            )
            for row in rows:
                row.catalog_name = catalog_name
                row.ticker = ticker
                self.session.add(row)
            await self.session.flush()
        except IntegrityError as e:
            raise DuplicateRecordError(
                f"Dividend rows for {ticker} in '{catalog_name}' constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    async def list_by_ticker(self, catalog_name: str, ticker: str) -> List[CatalogDividend]:
        """List dividend rows for a ticker ordered by ex-date descending.

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.

        Returns:
            List of CatalogDividend ordered by ``ex_date`` descending.
        """
        stmt = (
            select(CatalogDividend)
            .where(
                and_(
                    CatalogDividend.catalog_name == catalog_name,
                    CatalogDividend.ticker == ticker,
                )
            )
            .order_by(CatalogDividend.ex_date.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def has_for_ticker(self, catalog_name: str, ticker: str) -> bool:
        """Return True if any dividend row exists for ``(catalog_name, ticker)``.

        Uses a ``SELECT 1 … LIMIT 1`` existence probe — does not load full rows.

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.

        Returns:
            True if at least one dividend row exists, else False.
        """
        stmt = (
            select(literal(1))
            .where(
                and_(
                    CatalogDividend.catalog_name == catalog_name,
                    CatalogDividend.ticker == ticker,
                )
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None


class SyncCatalogDividendRepository:
    """Sync repository for catalog dividend operations.

    Used by the CLI import pipeline (this story).

    Attributes:
        session: Synchronous SQLAlchemy session.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_for_ticker(
        self,
        catalog_name: str,
        ticker: str,
        rows: Sequence[CatalogDividend],
    ) -> None:
        """Replace all dividend rows for a ``(catalog_name, ticker)`` pair.

        Deletes existing rows then bulk-inserts the new set, inside the
        caller's transaction (flush only — the caller commits).

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.
            rows: New dividend rows (``catalog_name``/``ticker`` are set here).

        Raises:
            DuplicateRecordError: If the unique constraint is violated.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            self.session.execute(
                delete(CatalogDividend).where(
                    and_(
                        CatalogDividend.catalog_name == catalog_name,
                        CatalogDividend.ticker == ticker,
                    )
                )
            )
            for row in rows:
                row.catalog_name = catalog_name
                row.ticker = ticker
                self.session.add(row)
            self.session.flush()
        except IntegrityError as e:
            raise DuplicateRecordError(
                f"Dividend rows for {ticker} in '{catalog_name}' constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def list_by_ticker(self, catalog_name: str, ticker: str) -> List[CatalogDividend]:
        """List dividend rows for a ticker ordered by ex-date descending.

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.

        Returns:
            List of CatalogDividend ordered by ``ex_date`` descending.
        """
        stmt = (
            select(CatalogDividend)
            .where(
                and_(
                    CatalogDividend.catalog_name == catalog_name,
                    CatalogDividend.ticker == ticker,
                )
            )
            .order_by(CatalogDividend.ex_date.desc())
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())
