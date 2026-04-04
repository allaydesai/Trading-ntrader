# Milestone 2: CSV Data Import & Real Backtest - Design Document

**Generated**: 2025-01-14
**Target Branch**: `001-docs-prd-md`
**Prerequisites**: Milestone 1 Complete ✅

## Executive Summary

Milestone 2 extends the basic backtesting system to support real market data through CSV import and database persistence. This milestone transforms the proof-of-concept from Milestone 1 into a production-ready system capable of handling real historical data.

## Current State Analysis

### Milestone 1 Achievements ✅
- **Core Infrastructure**: Project structure, configuration, and CLI foundation
- **Basic Strategy**: Working SMA crossover strategy with Nautilus Trader
- **Mock Data**: Synthetic data generation for testing
- **Simple Backtest**: End-to-end backtest execution with results display
- **Test Coverage**: 69 passing tests with TDD approach

### Technical Debt from Milestone 1
- No data persistence layer
- Limited to mock data only
- No data validation framework
- Basic result display without storage

## Architecture Overview

```mermaid
graph TB
    subgraph "Data Layer"
        CSV[CSV Files] --> Loader[CSV Loader Service]
        Loader --> Validator[Data Validator]
        Validator --> DB[(PostgreSQL)]
    end

    subgraph "Application Layer"
        CLI[CLI Commands] --> DataService[Data Service]
        DataService --> DB
        DataService --> Cache[In-Memory Cache]
    end

    subgraph "Backtest Layer"
        Runner[Backtest Runner] --> DataService
        Runner --> Engine[Nautilus Engine]
        Engine --> Results[Results Display]
    end
```

## Component Design

### 1. Database Layer

#### Schema Design
```sql
-- Market data table (will become TimescaleDB hypertable in Milestone 6)
CREATE TABLE market_data (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    open DECIMAL(20, 8) NOT NULL,
    high DECIMAL(20, 8) NOT NULL,
    low DECIMAL(20, 8) NOT NULL,
    close DECIMAL(20, 8) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(symbol, timestamp)
);

-- Indexes for performance
CREATE INDEX idx_market_data_symbol_timestamp ON market_data(symbol, timestamp DESC);
CREATE INDEX idx_market_data_timestamp ON market_data(timestamp DESC);
```

#### Migration Strategy
- Use Alembic for version control
- Initial migration creates base tables
- Future migrations add TimescaleDB extension (Milestone 6)

### 2. Data Models (Pydantic)

```python
# src/models/market_data.py
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from decimal import Decimal
from typing import Optional

class MarketDataBase(BaseModel):
    """Base market data model following data-model.md:48-69"""
    symbol: str = Field(..., min_length=1, max_length=20)
    timestamp: datetime
    open: Decimal = Field(..., gt=0, decimal_places=8)
    high: Decimal = Field(..., gt=0, decimal_places=8)
    low: Decimal = Field(..., gt=0, decimal_places=8)
    close: Decimal = Field(..., gt=0, decimal_places=8)
    volume: int = Field(..., ge=0)

    @field_validator('high')
    @classmethod
    def validate_high(cls, v, info):
        if 'low' in info.data and v < info.data['low']:
            raise ValueError('High must be >= Low')
        return v

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }
    }

class MarketDataCreate(MarketDataBase):
    """Model for creating market data records"""
    pass

class MarketData(MarketDataBase):
    """Complete market data model with DB fields"""
    id: int
    created_at: datetime

    model_config = {
        "from_attributes": True  # Enable ORM mode
    }
```

### 3. CSV Loader Service

```python
# src/services/csv_loader.py
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any
from decimal import Decimal
import asyncio
from src.models.market_data import MarketDataCreate

class CSVLoader:
    """Service for loading and validating CSV market data"""

    REQUIRED_COLUMNS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']

    def __init__(self, db_service):
        self.db_service = db_service

    async def load_file(self, file_path: Path, symbol: str) -> Dict[str, Any]:
        """Load CSV file and store to database"""
        # 1. Read CSV with pandas
        df = pd.read_csv(file_path)

        # 2. Validate columns
        self._validate_columns(df)

        # 3. Clean and transform data
        records = self._transform_to_records(df, symbol)

        # 4. Batch insert to database
        result = await self.db_service.bulk_insert_market_data(records)

        return {
            "file": str(file_path),
            "symbol": symbol,
            "records_processed": len(records),
            "records_inserted": result["inserted"],
            "duplicates_skipped": result["skipped"]
        }

    def _validate_columns(self, df: pd.DataFrame):
        """Validate required columns exist"""
        missing = set(self.REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

    def _transform_to_records(self, df: pd.DataFrame, symbol: str) -> List[MarketDataCreate]:
        """Transform DataFrame to Pydantic models"""
        records = []
        for _, row in df.iterrows():
            record = MarketDataCreate(
                symbol=symbol,
                timestamp=pd.to_datetime(row['timestamp']),
                open=Decimal(str(row['open'])),
                high=Decimal(str(row['high'])),
                low=Decimal(str(row['low'])),
                close=Decimal(str(row['close'])),
                volume=int(row['volume'])
            )
            records.append(record)
        return records
```

### 4. Data Service Layer

```python
# src/services/data_service.py
from datetime import datetime
from typing import List, Optional
import pandas as pd
from nautilus_trader.model.data import BarType, Bar
from src.db.session import get_session

class DataService:
    """Service for fetching and converting market data"""

    def __init__(self):
        self._cache = {}  # Simple in-memory cache

    async def get_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime
    ) -> List[Bar]:
        """Fetch bars from database and convert to Nautilus format"""
        # 1. Check cache
        cache_key = f"{symbol}_{start}_{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 2. Query database
        async with get_session() as session:
            query = """
                SELECT * FROM market_data
                WHERE symbol = :symbol
                AND timestamp >= :start
                AND timestamp <= :end
                ORDER BY timestamp ASC
            """
            result = await session.execute(
                query,
                {"symbol": symbol, "start": start, "end": end}
            )
            rows = result.fetchall()

        # 3. Convert to Nautilus Bars
        bars = self._convert_to_nautilus_bars(rows)

        # 4. Cache results
        self._cache[cache_key] = bars

        return bars

    def _convert_to_nautilus_bars(self, rows) -> List[Bar]:
        """Convert database rows to Nautilus Bar objects"""
        # Implementation details for Nautilus conversion
        pass
```

### 5. CLI Commands Structure

```python
# src/cli/commands/data.py
import click
from pathlib import Path
from rich.progress import Progress, SpinnerColumn, TextColumn
from src.services.csv_loader import CSVLoader

@click.group()
def data():
    """Data management commands"""
    pass

@data.command()
@click.option('--file', '-f', type=click.Path(exists=True), required=True)
@click.option('--symbol', '-s', required=True, help='Trading symbol (e.g., AAPL)')
def import_csv(file, symbol):
    """Import CSV market data"""
    file_path = Path(file)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task(f"Importing {file_path.name}...", total=None)

        # Run async import
        result = asyncio.run(import_csv_async(file_path, symbol))

        progress.update(task, completed=True)

    # Display results
    console.print(f"✅ Imported {result['records_inserted']} records")
    if result['duplicates_skipped'] > 0:
        console.print(f"⚠️  Skipped {result['duplicates_skipped']} duplicates")

@data.command()
@click.option('--symbol', '-s', required=True)
def list(symbol):
    """List available data for a symbol"""
    # Query and display data ranges
    pass
```

## Implementation Tasks (Refined)

### Phase 1: Database Setup (T013-T016)
1. **T013**: Add Database Dependencies ✅
   - SQLAlchemy, psycopg2-binary, alembic
   - pytest-asyncio for async testing

2. **T014**: Database Configuration
   - Update `src/config.py` with database settings
   - Create `.env.example` template
   - Add connection pooling configuration

3. **T015**: Market Data Models
   - Pydantic models with validation
   - SQLAlchemy ORM models
   - Field validators for OHLCV integrity

4. **T016**: Database Migrations
   - Initialize Alembic
   - Create initial migration
   - Add helper scripts for migration management

### Phase 2: Data Import (T017-T019)
5. **T017**: CSV Import Tests (TDD)
   - Write failing tests first
   - Test validation scenarios
   - Test duplicate handling

6. **T018**: CSV Loader Implementation
   - Parse and validate CSV
   - Transform to Pydantic models
   - Bulk insert with conflict handling

7. **T019**: Data Import CLI Command
   - Click command structure
   - Progress bar with Rich
   - Error handling and reporting

### Phase 3: Data Service (T020-T021)
8. **T020**: Data Service Layer
   - Fetch from database
   - Convert to Nautilus format
   - Implement caching strategy

9. **T021**: Backtest Runner Update
   - Support database data source
   - Date range selection
   - Real commission modeling

### Phase 4: Integration (T022-T024)
10. **T022**: Backtest Command
    - New command for real data
    - Parameter validation
    - Results display enhancement

11. **T023**: Sample Data Creation
    - Generate realistic AAPL CSV
    - Include edge cases
    - Documentation

12. **T024**: Integration Tests
    - End-to-end workflow
    - Performance benchmarks
    - Error scenarios

## Testing Strategy

### Unit Tests
```python
# tests/test_csv_loader.py
def test_csv_loader_validates_columns():
    """Test that loader rejects CSV with missing columns"""

def test_csv_loader_handles_duplicates():
    """Test duplicate timestamp handling"""

def test_csv_loader_validates_ohlc_integrity():
    """Test that high >= low validation works"""
```

### Integration Tests
```python
# tests/test_milestone_2.py
def test_full_csv_import_workflow():
    """Test complete import and backtest cycle"""
    # 1. Import sample CSV
    # 2. Verify database storage
    # 3. Run backtest on imported data
    # 4. Verify realistic results
```

## Performance Considerations

### Database Optimization
- Batch inserts (1000 records per transaction)
- Connection pooling (max 10 connections)
- Prepared statements for queries
- Index on (symbol, timestamp) for fast lookups

### Memory Management
- Stream CSV reading for large files
- Paginated database queries
- LRU cache with size limits
- Clear cache between backtests

### Expected Performance
- CSV Import: ~10,000 records/second
- Data Query: <100ms for 1 year of daily data
- Backtest Init: <500ms with database data
- Memory Usage: <200MB for typical dataset

## Risk Mitigation

### Data Integrity
- **Risk**: Corrupted CSV data
- **Mitigation**: Validation at multiple levels, transaction rollback on errors

### Performance Degradation
- **Risk**: Slow queries with large datasets
- **Mitigation**: Indexes, query optimization, caching

### Database Connection Issues
- **Risk**: Connection pool exhaustion
- **Mitigation**: Connection limits, timeout configuration, retry logic

## Success Criteria

### Functional Requirements
- ✅ Import CSV files with OHLCV data
- ✅ Store data in PostgreSQL database
- ✅ Run backtests on real historical data
- ✅ Handle duplicate data gracefully
- ✅ Display import progress and results

### Performance Requirements
- ✅ Import 10,000 records in <2 seconds
- ✅ Query 1 year of data in <100ms
- ✅ Memory usage <200MB
- ✅ Support concurrent imports

### Quality Requirements
- ✅ 80% test coverage minimum
- ✅ All tests passing
- ✅ Type hints on all functions
- ✅ Comprehensive error handling

## Migration Path from Milestone 1

1. **Preserve Existing Functionality**
   - Keep mock data generator
   - Maintain simple backtest command
   - Add new commands alongside

2. **Incremental Enhancement**
   - Add database layer without breaking existing code
   - New data source option in backtest runner
   - Gradual migration to database-backed tests

3. **Backward Compatibility**
   - `run-simple` command continues to work
   - Mock data remains available
   - Tests continue passing

## Next Steps

1. **Immediate Actions**:
   - Set up PostgreSQL database
   - Configure environment variables
   - Initialize Alembic migrations

2. **Development Sequence**:
   - Follow TDD: Write tests first
   - Implement in task order (T013-T024)
   - Run tests continuously

3. **Validation**:
   - Run existing Milestone 1 tests
   - Execute new integration tests
   - Performance benchmarking

## Appendix A: Sample CSV Format

```csv
timestamp,open,high,low,close,volume
2024-01-02 09:30:00,150.25,151.00,150.00,150.75,1000000
2024-01-02 09:31:00,150.75,151.25,150.50,151.00,850000
2024-01-02 09:32:00,151.00,151.50,150.75,151.25,920000
```

## Appendix B: Environment Variables

```bash
# .env.example
DATABASE_URL=postgresql://user:password@localhost:5432/ntrader
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
DATABASE_POOL_TIMEOUT=30
LOG_LEVEL=INFO
```

## Appendix C: Command Examples

```bash
# Import CSV data
ntrader data import --file data/AAPL_2024.csv --symbol AAPL

# List available data
ntrader data list --symbol AAPL

# Run backtest with real data
ntrader backtest run --strategy sma --symbol AAPL \
    --start 2024-01-01 --end 2024-12-31

# Run simple backtest (still works)
ntrader run-simple
```

---

**Document Status**: Ready for Implementation
**Review Date**: 2025-01-14
**Next Review**: After T024 completion