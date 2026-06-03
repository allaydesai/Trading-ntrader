"""Dual repository for the catalog_stock_splits table.

Provides both async (web) and sync (CLI) access to catalog stock split
history, following the project's dual repository pattern. The write path
uses set-replace semantics (``replace_for_ticker``) so a re-run produces no
duplicate rows and leaves no orphans when a ticker's history shrinks (AC-5).
"""

from typing import List, Sequence

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.catalog_stock_split import CatalogStockSplit


class CatalogStockSplitRepository:
    """Async repository for catalog stock split operations.

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
        rows: Sequence[CatalogStockSplit],
    ) -> None:
        """Replace all split rows for a ``(catalog_name, ticker)`` pair.

        Deletes existing rows then bulk-inserts the new set, inside the
        caller's transaction (flush only — the caller commits).

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.
            rows: New split rows (``catalog_name``/``ticker`` are set here).

        Raises:
            DuplicateRecordError: If the unique constraint is violated.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            await self.session.execute(
                delete(CatalogStockSplit).where(
                    and_(
                        CatalogStockSplit.catalog_name == catalog_name,
                        CatalogStockSplit.ticker == ticker,
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
                f"Split rows for {ticker} in '{catalog_name}' constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    async def list_by_ticker(self, catalog_name: str, ticker: str) -> List[CatalogStockSplit]:
        """List split rows for a ticker ordered by effective-date descending.

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.

        Returns:
            List of CatalogStockSplit ordered by ``effective_date`` descending.
        """
        stmt = (
            select(CatalogStockSplit)
            .where(
                and_(
                    CatalogStockSplit.catalog_name == catalog_name,
                    CatalogStockSplit.ticker == ticker,
                )
            )
            .order_by(CatalogStockSplit.effective_date.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class SyncCatalogStockSplitRepository:
    """Sync repository for catalog stock split operations.

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
        rows: Sequence[CatalogStockSplit],
    ) -> None:
        """Replace all split rows for a ``(catalog_name, ticker)`` pair.

        Deletes existing rows then bulk-inserts the new set, inside the
        caller's transaction (flush only — the caller commits).

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.
            rows: New split rows (``catalog_name``/``ticker`` are set here).

        Raises:
            DuplicateRecordError: If the unique constraint is violated.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            self.session.execute(
                delete(CatalogStockSplit).where(
                    and_(
                        CatalogStockSplit.catalog_name == catalog_name,
                        CatalogStockSplit.ticker == ticker,
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
                f"Split rows for {ticker} in '{catalog_name}' constraint violation"
            ) from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def list_by_ticker(self, catalog_name: str, ticker: str) -> List[CatalogStockSplit]:
        """List split rows for a ticker ordered by effective-date descending.

        Args:
            catalog_name: Catalog scope.
            ticker: Trading symbol.

        Returns:
            List of CatalogStockSplit ordered by ``effective_date`` descending.
        """
        stmt = (
            select(CatalogStockSplit)
            .where(
                and_(
                    CatalogStockSplit.catalog_name == catalog_name,
                    CatalogStockSplit.ticker == ticker,
                )
            )
            .order_by(CatalogStockSplit.effective_date.desc())
        )
        result = self.session.execute(stmt)
        return list(result.scalars().all())
