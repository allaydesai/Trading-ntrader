# Story 1.3: Instrument ID Mapping from Company Profiles

Status: review

## Story

As a system operator,
I want the system to map ETF ticker symbols to Nautilus-qualified instrument IDs using company_profiles.csv,
So that imported data has correct exchange-qualified identifiers for accurate backtesting.

## Acceptance Criteria

1. **Given** a company_profiles.csv file with columns (ticker, name, country, state, exchange, sector, industry, IPO date) **When** the instrument mapper parses the file **Then** all rows are loaded and available for instrument ID resolution **And** the data is stored in the catalog_instruments table with exchange, name, sector, industry, and ipo_date fields populated

2. **Given** a ticker "SPY" with exchange "ARCA" in company_profiles.csv **When** the instrument mapper resolves the Nautilus ID **Then** it returns "SPY.ARCA" as the qualified instrument ID

3. **Given** a ticker that does not exist in company_profiles.csv **When** the instrument mapper attempts resolution **Then** it raises a clear error identifying the unmappable ticker **And** the error does not block processing of other tickers

4. **Given** company_profiles.csv has already been loaded into the database for a catalog **When** the instrument mapper is called again for the same catalog **Then** it reads instrument IDs from the database instead of re-parsing the CSV **And** subsequent lookups use the DB as the authoritative source

## Tasks / Subtasks

- [x] Task 1: Company profiles CSV parser (AC: #1)
  - [x] 1.1 Create `InstrumentMapper` class in `src/services/firstrate/instrument_mapper.py`
  - [x] 1.2 Implement `load_company_profiles(file_path: Path, catalog_name: str, asset_class: str) -> int` — parse CSV, bulk upsert into catalog_instruments, return row count
  - [x] 1.3 Handle the 8-column headerless CSV: ticker, name, country, state, exchange, sector, industry, IPO date
  - [x] 1.4 Parse IPO date from string to `date` object (handle missing/empty values)
  - [x] 1.5 Build `nautilus_id` as `f"{ticker}.{exchange}"` during load
- [x] Task 2: Instrument ID resolution (AC: #2, #3, #4)
  - [x] 2.1 Implement `resolve_instrument_id(ticker: str, catalog_name: str) -> InstrumentId` — look up from DB
  - [x] 2.2 Raise `InstrumentMappingError` (new custom exception) when ticker not found in DB
  - [x] 2.3 Implement `is_loaded(catalog_name: str) -> bool` — check if profiles already exist for this catalog
- [x] Task 3: Wire into ETF parser (AC: #2)
  - [x] 3.1 ETFParser.map_instrument_id() stub kept as-is per Option A — Story 1.4 import pipeline will use InstrumentMapper.resolve_instrument_id() directly
  - [x] 3.2 Keep the method signature compatible — Story 1.4 import pipeline will orchestrate the mapper
- [x] Task 4: Tests (all ACs)
  - [x] 4.1 Unit tests for CSV parsing (valid file, missing fields, empty IPO date, duplicate tickers)
  - [x] 4.2 Unit tests for resolve_instrument_id (found, not found raises error)
  - [x] 4.3 Unit tests for is_loaded (empty catalog, populated catalog)
  - [x] 4.4 Component tests with in-memory SQLite for DB round-trip (load → resolve → verify)

## Dev Notes

### Architecture Compliance

- **ADR-6:** Instrument ID mapping via PostgreSQL — company_profiles.csv loaded into `catalog_instruments` table on first import; subsequent imports look up instrument IDs from DB. [Source: architecture.md#ADR-6]
- **ADR-3:** Single `catalog_instruments` table combines instrument identity with import metadata. The mapper populates identity fields (ticker, nautilus_id, exchange, name, sector, industry, ipo_date); the import pipeline (Story 1.4) later updates metadata fields (date_range, bar_counts). [Source: architecture.md#ADR-3]
- **File location:** `src/services/firstrate/instrument_mapper.py` per architecture file organization. [Source: architecture.md#File Organization]
- Tests: `tests/unit/services/firstrate/test_instrument_mapper.py` and `tests/component/services/firstrate/test_instrument_mapper.py`

### Company Profiles CSV Format

**Source:** FirstRate Data `company_profiles.csv` — 7,664 entries covering stocks and ETFs.

**Format:** 8 columns, headerless CSV (same pattern as all FirstRate files):
```
ticker,name,country,state,exchange,sector,industry,ipo_date
```

**Example rows (inferred from data spec):**
```
SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,Financial Services,Asset Management,1993-01-22
QQQ,Invesco QQQ Trust,US,NY,NASDAQ,Financial Services,Asset Management,1999-03-10
AAPL,Apple Inc.,US,CA,NASDAQ,Technology,Consumer Electronics,1980-12-12
```

**Known data characteristics:**
- Exchange values are venue codes directly usable as Nautilus venue qualifiers (ARCA, NASDAQ, NYSE, BATS, etc.)
- Some tickers may appear in both stock and ETF datasets — unique constraint is (catalog_name, ticker)
- IPO date may be empty/missing for some tickers — handle as None
- Country/state are metadata only — not needed for instrument ID mapping but store for future explorer display

### Nautilus InstrumentId Format

Nautilus uses `InstrumentId.from_str("{TICKER}.{VENUE}")`:
- `SPY.ARCA` — ETF on NYSE Arca
- `AAPL.NASDAQ` — Stock on NASDAQ
- `QQQ.NASDAQ` — ETF on NASDAQ

The instrument ID is critical — it becomes the Parquet directory name (`{INSTRUMENT_ID}/{BAR_TYPE}/*.parquet`) and must match across import, catalog reads, chart API, and backtest engine. A wrong venue cascades silently through every downstream component.

### DB Integration Pattern

Use the existing `SyncCatalogInstrumentRepository` (CLI context) for DB operations:

```python
from src.db.repositories.catalog_instrument_repository import SyncCatalogInstrumentRepository
from src.db.models.catalog_instrument import CatalogInstrument

# Create instrument record
instrument = CatalogInstrument(
    ticker="SPY",
    nautilus_id="SPY.ARCA",
    asset_class="ETF",
    catalog_name="firstrate-etf",
    exchange="ARCA",
    name="SPDR S&P 500 ETF Trust",
    sector="Financial Services",
    industry="Asset Management",
    ipo_date=date(1993, 1, 22),
)
repo.upsert(instrument)
```

The `upsert()` method already handles insert-or-update by (catalog_name, ticker) unique constraint. Use it directly — don't reinvent upsert logic.

### InstrumentMapper Design

The mapper is a **service class** (not a parser) that:
1. Parses company_profiles.csv into CatalogInstrument records
2. Bulk-upserts them into the DB via `SyncCatalogInstrumentRepository`
3. Resolves ticker → InstrumentId by reading from DB

```python
class InstrumentMapper:
    def __init__(self, repo: SyncCatalogInstrumentRepository):
        self._repo = repo

    def load_company_profiles(
        self, file_path: Path, catalog_name: str, asset_class: str
    ) -> int:
        """Parse CSV and upsert all rows. Returns count loaded."""

    def resolve_instrument_id(
        self, ticker: str, catalog_name: str
    ) -> InstrumentId:
        """Look up ticker in DB, return InstrumentId. Raises on miss."""

    def is_loaded(self, catalog_name: str) -> bool:
        """Check if any instruments exist for this catalog."""
```

Constructor takes `SyncCatalogInstrumentRepository` — follows the project's dependency injection pattern. The mapper does NOT own a session; the caller provides the repo.

### ETFParser Integration

The current `ETFParser.map_instrument_id()` is a stub returning `InstrumentId.from_str(f"{ticker}.ARCA")`. Two options:

**Option A (recommended):** Keep `map_instrument_id` as-is for now. Story 1.4's import pipeline will use `InstrumentMapper.resolve_instrument_id()` directly before calling `parse_file()`. The parser receives the already-resolved `InstrumentId` as a parameter.

**Option B:** Update `map_instrument_id` to accept an `InstrumentMapper` and delegate. This changes the base class signature.

**Choose Option A** — the import pipeline (Story 1.4) orchestrates: resolve ID via mapper → pass to parser. The parser doesn't need to know about the mapper. The stub remains as a standalone fallback.

### Custom Exception

Add `InstrumentMappingError` to `src/db/exceptions.py` (where project custom exceptions live):

```python
class InstrumentMappingError(Exception):
    """Raised when a ticker cannot be mapped to a Nautilus InstrumentId."""
```

### File Structure (Required Locations)

```
src/services/firstrate/
  instrument_mapper.py             # NEW: InstrumentMapper class
src/db/
  exceptions.py                    # MODIFY: add InstrumentMappingError

tests/unit/services/firstrate/
  test_instrument_mapper.py        # NEW: CSV parsing + resolution logic
tests/component/services/firstrate/
  test_instrument_mapper.py        # NEW: DB round-trip tests
```

### Existing Code to Reuse

- **`src/db/repositories/catalog_instrument_repository.py`** — `SyncCatalogInstrumentRepository` with `upsert()`, `get_by_ticker()`, `list_by_catalog()` already implemented (Story 1.1)
- **`src/db/models/catalog_instrument.py`** — ORM model with all needed columns: ticker, nautilus_id, exchange, name, sector, industry, ipo_date, asset_class, catalog_name
- **`src/db/exceptions.py`** — existing custom exceptions (`DatabaseConnectionError`, `DuplicateRecordError`); add `InstrumentMappingError` here
- **`src/models/catalog.py`** — `AssetClass` enum for asset class values
- **`src/services/firstrate/metadata_service.py`** — higher-level CRUD service wrapping the repository; mapper can use repo directly for lower-level control

### Anti-Patterns to Avoid

- **Do NOT parse CSV with pandas** — company_profiles.csv is small (7,664 rows); use stdlib `csv` module for simplicity and zero extra dependencies
- **Do NOT cache instrument IDs in memory** — the DB is the authoritative source; in-memory caching adds staleness risk for negligible performance gain on CLI operations
- **Do NOT create a new DB model or table** — `catalog_instruments` already has all needed columns
- **Do NOT modify the BaseParser ABC signature** — keep `map_instrument_id` as-is; the import pipeline (Story 1.4) orchestrates mapper + parser independently
- **Do NOT hardcode exchange values** — read them from the CSV; different ETFs trade on different venues (ARCA, NASDAQ, BATS, etc.)
- **Do NOT skip the `is_loaded` check** — prevents redundant re-parsing of the CSV on every import run
- **Do NOT treat country/state as required fields** — they may be empty; store as-is for future use but don't fail on missing values

### Testing Strategy

- **TDD is mandatory** — write failing tests first
- **Unit tests** (`@pytest.mark.unit`):
  - CSV parsing: valid 8-column file, missing/empty IPO date, empty exchange field, blank lines
  - `nautilus_id` construction: `"{ticker}.{exchange}"` format verified
  - `resolve_instrument_id`: mock repo returns instrument → correct InstrumentId; mock repo returns None → raises `InstrumentMappingError`
  - `is_loaded`: mock repo returns empty list → False; mock repo returns instruments → True
  - Edge cases: duplicate tickers in CSV (last wins or first wins — document behavior), ticker with special characters
- **Component tests** (`@pytest.mark.component`):
  - In-memory SQLite round-trip: load CSV → verify DB records → resolve by ticker → verify InstrumentId
  - Re-load same catalog → verify upsert updates existing records (not duplicates)
  - Load then resolve unknown ticker → InstrumentMappingError
- **Test fixtures:** Create small CSV files in `tmp_path`; use in-memory SQLite (`sqlite+aiosqlite:///:memory:` for async, `sqlite:///:memory:` for sync) following Story 1.1 patterns

### Cross-Story Context

- **Story 1.2** created `ETFParser.map_instrument_id()` stub returning `{ticker}.ARCA` — this story provides the real mapping via DB lookup
- **Story 1.4** (Import Pipeline Core) will orchestrate: `InstrumentMapper.resolve_instrument_id(ticker)` → pass `InstrumentId` to `ETFParser.parse_file()`. The mapper must be ready as a standalone service
- **Story 1.5** (CLI Import Command) will call `InstrumentMapper.load_company_profiles()` as a prerequisite step before the per-ticker import loop
- **Future phases** will add asset-class-specific mapping logic (futures → CME, FX → SIM, etc.) — keep the mapper design extensible but don't over-engineer

### Previous Story Intelligence (Story 1.2)

**Learnings from Story 1.2 implementation:**
- Ruff auto-formatter actively removes "unused" imports — make dependent changes in a single edit
- Nautilus Bar constructor enforces OHLC correctness — validate on raw data before construction
- `calendar.timegm()` used for timestamp conversion (integer arithmetic, no float precision loss)
- `RawBarData` dataclass introduced as intermediate CSV representation — consider similar pattern if CSV parsing needs pre-validation
- 36 unit tests added; 553 total unit tests passing — regression baseline

**Review findings from Story 1.2:**
- F8 (deferred): `_row_to_bar` silently swallows exceptions — Story 1.4 may need parse result with skipped-row counts
- Validation operates on pre-construction strings (`RawBarData`), not `Bar` objects

**Files from Story 1.2 this story relates to:**
- `src/services/firstrate/parsers/etf_parser.py:66-76` — `map_instrument_id()` stub to eventually be replaced by mapper usage in Story 1.4
- `src/services/firstrate/parsers/base.py:111-125` — `map_instrument_id` abstract method signature (don't change)

### Project Structure Notes

- `instrument_mapper.py` goes in `src/services/firstrate/` alongside `metadata_service.py` and `catalog_manager.py` — per architecture.md file organization
- Tests mirror source tree: `tests/unit/services/firstrate/` (exists from Story 1.1) and `tests/component/services/firstrate/` (may need `__init__.py`)
- No new DB models or migrations needed — `catalog_instruments` table already has all required columns

### References

- [Source: architecture.md#ADR-6] Instrument mapping via PostgreSQL — company_profiles.csv → DB → lookup
- [Source: architecture.md#ADR-3] Single catalog_instruments table design
- [Source: architecture.md#File Organization] instrument_mapper.py location
- [Source: epics.md#Story 1.3] Acceptance criteria and user story
- [Source: prd.md#FR5] Ticker to Nautilus instrument ID mapping requirement
- [Source: prd.md#FR28] Parse company_profiles.csv requirement
- [Source: product-brief-distillate.md#Nautilus Instrument ID Mapping] Exchange column mapping strategy
- [Source: product-brief-distillate.md#Supplementary Data] company_profiles.csv has 7,664 entries with 8 columns
- [Source: src/db/models/catalog_instrument.py] ORM model with exchange, name, sector, industry, ipo_date columns
- [Source: src/db/repositories/catalog_instrument_repository.py] SyncCatalogInstrumentRepository with upsert/get_by_ticker
- [Source: src/services/firstrate/parsers/etf_parser.py:66-76] map_instrument_id stub

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- SQLite component tests: `Base.metadata.create_all()` fails with JSONB columns from other tables — used manual DDL for `catalog_instruments` only
- `CatalogInstrument` constructor doesn't apply `default=0` for `bar_count_*` columns (SQLAlchemy insert-time default only) — explicitly set `bar_count_daily=0, bar_count_hourly=0, bar_count_minute=0` in mapper

### Completion Notes List

- Created `InstrumentMapper` service class with `load_company_profiles()`, `resolve_instrument_id()`, and `is_loaded()` methods
- Added `InstrumentMappingError` custom exception to `src/db/exceptions.py`
- CSV parsing uses stdlib `csv` module, handles blank lines, empty IPO dates, and builds `nautilus_id` as `{ticker}.{exchange}`
- Constructor takes `SyncCatalogInstrumentRepository` following DI pattern — mapper does not own a session
- ETF parser `map_instrument_id()` stub kept as-is per Option A — Story 1.4 pipeline will orchestrate mapper + parser independently
- 12 unit tests + 6 component tests = 18 new tests; 1013 total tests passing (0 regressions)

### Change Log

- 2026-04-08: Story 1.3 implemented — InstrumentMapper, InstrumentMappingError, 18 tests

### File List

- `src/services/firstrate/instrument_mapper.py` — NEW: InstrumentMapper class
- `src/db/exceptions.py` — MODIFIED: added InstrumentMappingError
- `tests/unit/services/firstrate/test_instrument_mapper.py` — NEW: 12 unit tests
- `tests/component/services/firstrate/__init__.py` — NEW: package init
- `tests/component/services/firstrate/test_instrument_mapper.py` — NEW: 6 component tests
