# Data Model: Parquet-Only Market Data Storage

**Date**: 2025-01-13
**Branch**: 002-migrate-from-dual
**Status**: Complete

## Overview

This data model defines entities for Parquet-first market data architecture. Following KISS principle, we use Nautilus Trader's native types directly (no custom DTOs) and minimal metadata structures.

---

## Entity 1: Bar (Nautilus Native Type)

**Source**: `nautilus_trader.model.data.Bar`
**Usage**: Primary market data object (OHLCV)

### Description
Nautilus Trader's Bar represents a single OHLCV (Open, High, Low, Close, Volume) candle for a specific time period. We use this type directly without creating custom wrappers.

### Attributes (Read-Only)

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `bar_type` | `BarType` | Bar specification (instrument, timeframe, price type) | Required |
| `open` | `Price` | Opening price | > 0, <= high, >= low |
| `high` | `Price` | Highest price | >= open, >= close, >= low |
| `low` | `Price` | Lowest price | <= open, <= close, <= high |
| `close` | `Price` | Closing price | > 0, <= high, >= low |
| `volume` | `Quantity` | Trading volume | >= 0 |
| `ts_event` | `int` | Event timestamp (nanoseconds since epoch, UTC) | > 0, monotonic increasing |
| `ts_init` | `int` | Initialization timestamp (nanoseconds, UTC) | > 0 |

### Relationships
- Belongs to one `InstrumentId` (embedded in `bar_type`)
- Stored in Parquet catalog partitioned by instrument and bar_type

### State Transitions
None (immutable once created)

### Example
```python
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId

# Bar is created by IBKR adapter or CSV loader, not manually
bar = Bar(
    bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST"),
    open=Price(150.00, precision=2),
    high=Price(150.50, precision=2),
    low=Price(149.80, precision=2),
    close=Price(150.20, precision=2),
    volume=Quantity(10000, precision=0),
    ts_event=1704110400000000000,  # 2024-01-01 10:00:00 UTC in nanoseconds
    ts_init=1704110400000000000
)
```

**Parquet Storage**: Written by `ParquetDataCatalog.write_data([bars])`

---

## Entity 2: InstrumentId (Nautilus Native Type)

**Source**: `nautilus_trader.model.identifiers.InstrumentId`
**Usage**: Unique identifier for tradable instruments

### Description
Represents a unique instrument across all venues. Used as partitioning key in Parquet catalog.

### Attributes

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `symbol` | `Symbol` | Trading symbol | AAPL, MSFT, EUR/USD |
| `venue` | `Venue` | Exchange/venue identifier | NASDAQ, NYSE, IDEALPRO |

### String Representation
Format: `{symbol}.{venue}`
- Example: `AAPL.NASDAQ`, `EURUSD.IDEALPRO`

### Usage in Catalog
```python
from nautilus_trader.model.identifiers import InstrumentId

# Create from string
instrument_id = InstrumentId.from_str("AAPL.NASDAQ")

# Query catalog by instrument_id
bars = catalog.query(
    Bar,
    identifiers=["AAPL.NASDAQ"],  # String representation
    start=start_timestamp,
    end=end_timestamp
)
```

**Parquet Directory Structure**: `./data/catalog/{symbol}.{venue}/{bar_type}/`

---

## Entity 3: BarType (Nautilus Native Type)

**Source**: `nautilus_trader.model.data.BarType`
**Usage**: Specifies bar aggregation and price type

### Description
Defines how market data is aggregated (timeframe, price type). Used for partitioning catalog by aggregation level.

### Attributes

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `instrument_id` | `InstrumentId` | Instrument this bar type applies to | AAPL.NASDAQ |
| `bar_spec` | `BarSpecification` | Aggregation specification | 1-MINUTE, 5-MINUTE, 1-HOUR |

### BarSpecification Components

| Component | Type | Options | Description |
|-----------|------|---------|-------------|
| `step` | `int` | 1, 5, 15, 30, 60 | Number of units |
| `aggregation` | `BarAggregation` | MINUTE, HOUR, DAY | Time unit |
| `price_type` | `PriceType` | LAST, BID, ASK, MID | Price used for OHLC |

### String Representation
Format: `{instrument_id}-{step}-{aggregation}-{price_type}`
- Example: `AAPL.NASDAQ-1-MINUTE-LAST`, `EUR/USD.IDEALPRO-5-MINUTE-MID`

### Usage in Catalog
```python
from nautilus_trader.model.data import BarType

# Parse from string
bar_type = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST")

# Catalog automatically partitions by bar_type
# Directory: ./data/catalog/AAPL.NASDAQ/1-MINUTE-LAST/
```

**Parquet Directory Structure**: `./data/catalog/{instrument_id}/{step}-{aggregation}-{price_type}/`

---

## Entity 4: ParquetDataCatalog (Nautilus Native Type)

**Source**: `nautilus_trader.persistence.catalog.ParquetDataCatalog`
**Usage**: Facade for all Parquet I/O operations

### Description
Manages market data persistence in Parquet format. Handles partitioning, querying, and consolidation automatically. NOT threadsafe - ensure single-threaded access.

### Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | `str | PathLike` | Required | Absolute path to catalog root |
| `fs_protocol` | `str` | 'file' | Filesystem protocol ('file', 's3', 'gcs') |
| `fs_storage_options` | `dict` | None | Storage options (S3 credentials, etc) |
| `max_rows_per_group` | `int` | 5000 | Rows per Parquet row group (performance tuning) |
| `show_query_paths` | `bool` | False | Debug: print globed query paths |

### Key Operations

**1. Initialization**
```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog

# From absolute path
catalog = ParquetDataCatalog("./data/catalog")

# From environment variable NAUTILUS_PATH
catalog = ParquetDataCatalog.from_env()

# From URI (supports S3, GCS)
catalog = ParquetDataCatalog.from_uri(
    "s3://my-bucket/catalog",
    storage_options={"key": "...", "secret": "..."}
)
```

**2. Write Data**
```python
# Write bars (automatic partitioning)
catalog.write_data(bars, skip_disjoint_check=True)

# skip_disjoint_check=True allows overlapping time ranges
# (needed when re-fetching data)
```

**3. Query Data**
```python
# Query by time range
bars = catalog.query(
    data_cls=Bar,
    identifiers=["AAPL.NASDAQ"],
    start=1704110400000000000,  # nanoseconds since epoch
    end=1704196800000000000
)

# Query with SQL filter
bars = catalog.query(
    Bar,
    identifiers=["AAPL.NASDAQ"],
    where="close > 150.0"  # Additional filtering
)
```

**4. Delete Data Range** (data corrections)
```python
# Delete specific date range
catalog.delete_data_range(
    data_cls=Bar,
    identifier="AAPL.NASDAQ",
    start=1704110400000000000,
    end=1704196800000000000
)
```

**5. Consolidate Files** (maintenance)
```python
import pandas as pd

# Consolidate fragmented files into daily partitions
catalog.consolidate_data_by_period(
    data_cls=Bar,
    identifier="AAPL.NASDAQ",
    period=pd.Timedelta(days=1)
)
```

### Thread Safety
⚠️ **NOT threadsafe** - Concurrent writes may corrupt data
- Solution: Single writer process, multiple readers OK
- Backtest engine: Read-only access (safe for concurrent backtests)
- Data fetcher: Write access (serialize fetch operations)

---

## Entity 5: CatalogAvailability (NEW Custom Type)

**Source**: `src/models/catalog_metadata.py` (to be created)
**Usage**: Fast availability checks without scanning Parquet files

### Description
Lightweight index tracking which date ranges exist in catalog for each instrument/bar_type combination. Enables fast availability checks before loading data.

### Attributes

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `instrument_id` | `str` | Instrument identifier | Format: {symbol}.{venue} |
| `bar_type_spec` | `str` | Bar type specification | Format: {step}-{aggregation}-{price_type} |
| `start_date` | `datetime` | Earliest available data | UTC timezone-aware |
| `end_date` | `datetime` | Latest available data | UTC, >= start_date |
| `file_count` | `int` | Number of Parquet files | >= 1 |
| `total_rows` | `int` | Approximate row count | >= 0 |
| `last_updated` | `datetime` | Last write timestamp | UTC, monotonic |

### Implementation (Pydantic Model)
```python
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator

class CatalogAvailability(BaseModel):
    """Metadata for catalog data availability."""

    instrument_id: str = Field(..., min_length=1, max_length=50)
    bar_type_spec: str = Field(..., min_length=1, max_length=50)
    start_date: datetime
    end_date: datetime
    file_count: int = Field(..., ge=1)
    total_rows: int = Field(..., ge=0)
    last_updated: datetime

    @field_validator('start_date', 'end_date', 'last_updated')
    @classmethod
    def ensure_utc_timezone(cls, v: datetime) -> datetime:
        """Ensure all timestamps are UTC timezone-aware."""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator('end_date')
    @classmethod
    def validate_date_range(cls, v: datetime, info) -> datetime:
        """Validate end_date >= start_date."""
        if 'start_date' in info.data and v < info.data['start_date']:
            raise ValueError("end_date must be >= start_date")
        return v

    def covers_range(self, start: datetime, end: datetime) -> bool:
        """Check if this availability covers requested range."""
        return self.start_date <= start and self.end_date >= end

    def overlaps_range(self, start: datetime, end: datetime) -> bool:
        """Check if this availability overlaps requested range."""
        return not (self.end_date < start or self.start_date > end)
```

### Usage
```python
# Check availability before loading
availability = catalog_service.get_availability("AAPL.NASDAQ", "1-MINUTE-LAST")

if availability and availability.covers_range(start, end):
    # Data fully available, load from catalog
    bars = catalog.query(Bar, identifiers=["AAPL.NASDAQ"], start=start, end=end)
else:
    # Partial or no data, fetch from IBKR
    bars = await ibkr_fetcher.fetch_bars(...)
    catalog.write_data(bars)
```

### Storage Options

**Option A: In-memory cache** (initial implementation)
- Store as `Dict[str, CatalogAvailability]` keyed by f"{instrument_id}_{bar_type_spec}"
- Rebuild cache on service startup by scanning catalog
- Fast for small catalogs (<100 instruments)

**Option B: SQLite metadata DB** (future optimization)
- Store in `./data/catalog/.metadata.db` (SQLite)
- Index on (instrument_id, bar_type_spec)
- Faster for large catalogs (>100 instruments)

**Decision**: Start with Option A, migrate to Option B if performance becomes issue

---

## Entity 6: FetchRequest (NEW Custom Type)

**Source**: `src/models/fetch_request.py` (to be created)
**Usage**: Track IBKR fetch operation state

### Description
Represents a single request to fetch historical data from IBKR. Tracks retry attempts, progress, and error state.

### Attributes

| Field | Type | Description | Validation |
|-------|------|-------------|------------|
| `request_id` | `UUID` | Unique request identifier | Auto-generated |
| `instrument_id` | `str` | Instrument to fetch | Format: {symbol}.{venue} |
| `bar_type_spec` | `str` | Bar type specification | Format: {step}-{aggregation}-{price_type} |
| `start_date` | `datetime` | Fetch start date | UTC timezone-aware |
| `end_date` | `datetime` | Fetch end date | UTC, >= start_date |
| `status` | `FetchStatus` | Current state | PENDING, IN_PROGRESS, COMPLETED, FAILED |
| `retry_count` | `int` | Number of retry attempts | >= 0, <= max_retries |
| `error_message` | `str | None` | Error details if failed | Optional |
| `created_at` | `datetime` | Request creation time | UTC, monotonic |
| `completed_at` | `datetime | None` | Completion time | UTC, >= created_at |

### State Machine

```
PENDING → IN_PROGRESS → COMPLETED
              ↓
            FAILED → (manual retry) → PENDING
```

### Implementation (Pydantic Model)
```python
from enum import Enum
from uuid import UUID, uuid4
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class FetchStatus(str, Enum):
    """Fetch request status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class FetchRequest(BaseModel):
    """IBKR historical data fetch request."""

    request_id: UUID = Field(default_factory=uuid4)
    instrument_id: str = Field(..., min_length=1)
    bar_type_spec: str = Field(..., min_length=1)
    start_date: datetime
    end_date: datetime
    status: FetchStatus = FetchStatus.PENDING
    retry_count: int = Field(default=0, ge=0, le=5)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    def mark_in_progress(self) -> None:
        """Transition to IN_PROGRESS state."""
        self.status = FetchStatus.IN_PROGRESS

    def mark_completed(self) -> None:
        """Transition to COMPLETED state."""
        self.status = FetchStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        """Transition to FAILED state."""
        self.status = FetchStatus.FAILED
        self.error_message = error
        self.retry_count += 1
```

### Usage
```python
# Create fetch request
request = FetchRequest(
    instrument_id="AAPL.NASDAQ",
    bar_type_spec="1-MINUTE-LAST",
    start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
    end_date=datetime(2024, 12, 31, tzinfo=timezone.utc)
)

# Track progress
request.mark_in_progress()
try:
    bars = await ibkr_client.fetch_bars(...)
    catalog.write_data(bars)
    request.mark_completed()
except Exception as e:
    request.mark_failed(str(e))
    if request.retry_count < 5:
        # Retry with exponential backoff
        await asyncio.sleep(2 ** request.retry_count)
```

**Storage**: In-memory during fetch, optional persistence to SQLite for debugging

---

## Catalog Directory Structure

### Physical Layout
```
data/catalog/
├── AAPL.NASDAQ/
│   ├── 1-MINUTE-LAST/
│   │   ├── 2024-01-02.parquet     # ~50KB compressed, ~390 bars
│   │   ├── 2024-01-03.parquet
│   │   └── 2024-01-04.parquet
│   ├── 5-MINUTE-LAST/
│   │   ├── 2024-01-02.parquet     # ~12KB compressed, ~78 bars
│   │   └── 2024-01-03.parquet
│   └── 1-HOUR-LAST/
│       └── 2024-01.parquet         # Monthly for hourly bars (~700KB, ~21 days * 6.5 hours)
├── MSFT.NASDAQ/
│   └── 1-MINUTE-LAST/
│       ├── 2024-01-02.parquet
│       └── 2024-01-03.parquet
└── EURUSD.IDEALPRO/
    └── 1-MINUTE-MID/
        ├── 2024-01-02.parquet      # Forex: 24-hour data (~1440 bars/day)
        └── 2024-01-03.parquet
```

### Partitioning Rules (Managed by Nautilus)
1. **Level 1**: Instrument ID (`{symbol}.{venue}`)
2. **Level 2**: Bar type specification (`{step}-{aggregation}-{price_type}`)
3. **Level 3**: Date-based files (`YYYY-MM-DD.parquet` for minute/5-minute, `YYYY-MM.parquet` for hourly/daily)

---

## Summary: Entities and Relationships

```
┌─────────────────────────────────────────────────────────────┐
│                    ParquetDataCatalog                       │
│  (Manages all persistence, partitioning, queries)           │
└────────────────────────────┬────────────────────────────────┘
                             │
                             │ writes/reads
                             │
                    ┌────────▼────────┐
                    │      Bar        │
                    │  (OHLCV data)   │
                    └────────┬────────┘
                             │
                ┌────────────┼────────────┐
                │                         │
         ┌──────▼──────┐         ┌───────▼──────┐
         │ InstrumentId│         │   BarType    │
         │ (AAPL.NASD) │         │ (1-MIN-LAST) │
         └─────────────┘         └──────────────┘
                                         │
                                         │ metadata
                                         │
                          ┌──────────────▼──────────────┐
                          │   CatalogAvailability       │
                          │ (Fast availability checks)  │
                          └─────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     FetchRequest                            │
│          (Track IBKR fetch operations)                      │
│  PENDING → IN_PROGRESS → COMPLETED                          │
│                ↓                                             │
│             FAILED (retry)                                   │
└─────────────────────────────────────────────────────────────┘
```

**Key Principles**:
- Use Nautilus native types (Bar, InstrumentId, BarType, ParquetDataCatalog) directly
- Minimal custom types (CatalogAvailability, FetchRequest) for metadata only
- No DTOs, no mapping layers (KISS principle)
- Immutable data objects (Bar is read-only once created)
- All timestamps in UTC (no timezone conversions)

---

**Ready for**: Contract specifications, quickstart scenarios, implementation tasks
