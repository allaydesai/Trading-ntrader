"""CSV data loading service."""

import tempfile
from pathlib import Path
from typing import Dict, Any, List
from decimal import Decimal
import pandas as pd
from datetime import datetime

from src.models.market_data import MarketDataCreate
from src.db.session import get_session
from src.models.market_data import market_data_table
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert


class CSVLoader:
    """Service for loading and validating CSV market data."""

    REQUIRED_COLUMNS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']

    def __init__(self, session=None):
        """Initialize CSV loader with optional database session."""
        self.session = session

    async def load_file(self, file_path: Path, symbol: str) -> Dict[str, Any]:
        """
        Load CSV file and store to database.

        Args:
            file_path: Path to CSV file
            symbol: Trading symbol (e.g., AAPL)

        Returns:
            Dictionary with import results

        Raises:
            ValueError: If CSV format is invalid
            FileNotFoundError: If file doesn't exist
        """
        if not file_path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        # Read CSV with pandas
        df = pd.read_csv(file_path)

        # Validate columns
        self._validate_columns(df)

        # Clean and transform data
        records = self._transform_to_records(df, symbol)

        # Store to database
        result = await self._bulk_insert_records(records)

        return {
            "file": str(file_path),
            "symbol": symbol,
            "records_processed": len(records),
            "records_inserted": result["inserted"],
            "duplicates_skipped": result["skipped"]
        }

    def _validate_columns(self, df: pd.DataFrame) -> None:
        """
        Validate that all required columns are present.

        Args:
            df: DataFrame to validate

        Raises:
            ValueError: If required columns are missing
        """
        missing = set(self.REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

    def _transform_to_records(self, df: pd.DataFrame, symbol: str) -> List[MarketDataCreate]:
        """
        Transform DataFrame to Pydantic models.

        Args:
            df: Source DataFrame
            symbol: Trading symbol

        Returns:
            List of validated MarketDataCreate objects

        Raises:
            ValueError: If data validation fails
        """
        records = []
        for _, row in df.iterrows():
            try:
                # Parse timestamp and make timezone-aware (UTC)
                timestamp = pd.to_datetime(row['timestamp'])
                if timestamp.tz is None:
                    timestamp = timestamp.tz_localize('UTC')

                record = MarketDataCreate(
                    symbol=symbol,
                    timestamp=timestamp,
                    open=Decimal(str(row['open'])),
                    high=Decimal(str(row['high'])),
                    low=Decimal(str(row['low'])),
                    close=Decimal(str(row['close'])),
                    volume=int(row['volume'])
                )
                records.append(record)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Invalid data in row {len(records) + 1}: {e}")

        return records

    async def _bulk_insert_records(self, records: List[MarketDataCreate]) -> Dict[str, int]:
        """
        Bulk insert records to database with duplicate handling.

        Args:
            records: List of MarketDataCreate objects

        Returns:
            Dictionary with insert statistics
        """
        if self.session:
            # Use provided session
            db_session = self.session
            # Convert records to dictionaries for SQLAlchemy
            data_dicts = []
            for record in records:
                record_dict = record.model_dump()
                # Add created_at timestamp
                record_dict['created_at'] = datetime.utcnow()
                data_dicts.append(record_dict)

            # Use PostgreSQL INSERT ... ON CONFLICT DO NOTHING for duplicate handling
            stmt = pg_insert(market_data_table).values(data_dicts)
            stmt = stmt.on_conflict_do_nothing(constraint='uq_symbol_timestamp')

            result = await db_session.execute(stmt)
            inserted_count = result.rowcount if result.rowcount else 0
            skipped_count = len(data_dicts) - inserted_count

            await db_session.commit()

            return {
                "inserted": inserted_count,
                "skipped": skipped_count
            }
        else:
            # Use get_session context manager
            async with get_session() as db_session:
                # Convert records to dictionaries for SQLAlchemy
                data_dicts = []
                for record in records:
                    record_dict = record.model_dump()
                    # Add created_at timestamp
                    record_dict['created_at'] = datetime.utcnow()
                    data_dicts.append(record_dict)

                # Use PostgreSQL INSERT ... ON CONFLICT DO NOTHING for duplicate handling
                stmt = pg_insert(market_data_table).values(data_dicts)
                stmt = stmt.on_conflict_do_nothing(constraint='uq_symbol_timestamp')

                result = await db_session.execute(stmt)
                inserted_count = result.rowcount if result.rowcount else 0
                skipped_count = len(data_dicts) - inserted_count

                await db_session.commit()

                return {
                    "inserted": inserted_count,
                    "skipped": skipped_count
                }