"""Synchronous repository for the instrument_metadata cache table (CLI use).

Used by CLI commands that don't need async capabilities. For async operations
(API endpoints), use ``instrument_metadata_repository.py`` instead.

Operates on the SQLAlchemy ORM ``InstrumentMetadata``
(``src.db.models.instrument_metadata``) — not the Pydantic domain model of the
same name. ORM↔domain mapping is the Story 1.5 service's responsibility.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.models.instrument_metadata import InstrumentMetadata


class SyncInstrumentMetadataRepository:
    """Sync repository for instrument metadata operations.

    Attributes:
        session: Synchronous SQLAlchemy session.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, metadata: InstrumentMetadata) -> InstrumentMetadata:
        """Insert or update a metadata record, keyed by ticker.

        Args:
            metadata: InstrumentMetadata ORM instance.

        Returns:
            The persisted InstrumentMetadata.

        Raises:
            DuplicateRecordError: If a unique constraint is violated unexpectedly.
            DatabaseConnectionError: If the database operation fails.
        """
        try:
            existing = self.get_by_ticker(metadata.ticker)
            if existing:
                existing.metadata_provider = metadata.metadata_provider
                existing.venue = metadata.venue
                existing.currency = metadata.currency
                existing.asset_type = metadata.asset_type
                existing.company_name = metadata.company_name
                existing.sector = metadata.sector
                existing.industry = metadata.industry
                existing.country = metadata.country
                existing.ipo_date = metadata.ipo_date
                existing.resolution_status = metadata.resolution_status
                existing.resolved_at = metadata.resolved_at
                self.session.flush()
                self.session.refresh(existing)
                return existing

            self.session.add(metadata)
            self.session.flush()
            self.session.refresh(metadata)
            return metadata

        except IntegrityError as e:
            raise DuplicateRecordError(f"Metadata {metadata.ticker} constraint violation") from e
        except OperationalError as e:
            raise DatabaseConnectionError(f"Database connection failed: {e}") from e

    def get_by_ticker(self, ticker: str) -> Optional[InstrumentMetadata]:
        """Find metadata by ticker (primary key).

        Args:
            ticker: Instrument ticker symbol.

        Returns:
            InstrumentMetadata or None if not found.
        """
        stmt = select(InstrumentMetadata).where(InstrumentMetadata.ticker == ticker)
        result = self.session.execute(stmt)
        return result.scalar_one_or_none()
