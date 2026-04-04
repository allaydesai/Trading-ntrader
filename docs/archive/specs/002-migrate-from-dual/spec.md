# Feature Specification: Parquet-Only Market Data Storage

**Feature Branch**: `002-migrate-from-dual`
**Created**: 2025-01-13
**Status**: Draft
**Input**: User description: "Migrate from dual storage (PostgreSQL + Parquet) to Parquet-only architecture for market data, deprecating PostgreSQL for market data while preserving it for future metadata use"

## Executive Summary

This specification defines the migration from dual storage (PostgreSQL + Parquet) to a single source of truth architecture using Parquet files for all market data. PostgreSQL will be deprecated for market data storage and eventually repurposed for metadata tracking only (future phase).

**Core Principle**: Market data lives in Parquet ONLY. No duplication, no synchronization, no complexity.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run Backtest with Cached Data (Priority: P1)

A trader wants to run a backtest using previously fetched market data without waiting for new data downloads or dealing with connection setup.

**Why this priority**: This is the most common scenario and delivers immediate value by eliminating the need for dual storage synchronization. It represents the core functionality that 80% of users will experience most frequently.

**Independent Test**: Can be fully tested by loading existing Parquet files and running a backtest, which delivers instant backtest execution without any external dependencies.

**Acceptance Scenarios**:

1. **Given** AAPL 1-minute bars exist in Parquet catalog for Jan 2024, **When** user runs backtest for AAPL Jan 2024, **Then** system loads data from Parquet and starts backtest immediately
2. **Given** multiple instruments (AAPL, MSFT, GOOGL) exist in catalog, **When** user runs portfolio backtest, **Then** all data loads from Parquet without any IBKR connection required
3. **Given** 1 year of 1-minute bars exist in catalog, **When** data is loaded, **Then** system successfully retrieves all bars and proceeds with backtest

---

### User Story 2 - Automatic Data Fetching on Missing Data (Priority: P1)

A trader wants to run a backtest for a new instrument or date range that isn't yet in the local catalog, and have the system automatically fetch and persist the data for future use.

**Why this priority**: This is the second most critical workflow that makes the system self-sufficient. Without this, users would need manual data fetching steps, breaking the seamless experience.

**Independent Test**: Can be tested by requesting data not in catalog while IBKR is connected, verifying automatic fetch, persist, and backtest execution in one flow.

**Acceptance Scenarios**:

1. **Given** TSLA data is NOT in Parquet catalog and IBKR is connected, **When** user runs backtest for TSLA, **Then** system automatically fetches data from IBKR, saves to Parquet, and proceeds with backtest
2. **Given** data fetch is in progress, **When** user is waiting, **Then** system displays progress indicator showing "Fetching TSLA 1-minute bars from IBKR: 45% complete"
3. **Given** data is successfully fetched and persisted, **When** same backtest runs again later, **Then** system uses cached Parquet data without re-fetching

---

### User Story 3 - Clear Error Messaging for Unavailable Data (Priority: P2)

A trader wants to run a backtest but data is not in catalog and IBKR connection is unavailable, and needs clear guidance on how to resolve the situation.

**Why this priority**: Error handling is critical for user trust but comes after core happy paths. Good error messages prevent support tickets and user frustration.

**Independent Test**: Can be tested by simulating missing data with IBKR disconnected, verifying error message clarity and actionable recovery steps.

**Acceptance Scenarios**:

1. **Given** NVDA data is NOT in catalog and IBKR is NOT connected, **When** user runs backtest for NVDA, **Then** system fails with message "Data not found for NVDA. Please connect to IBKR Gateway or import CSV data."
2. **Given** IBKR connection fails mid-fetch, **When** error occurs, **Then** system displays "IBKR connection lost. Retry backtest after reconnecting, or use: docker compose up ibgateway"
3. **Given** IBKR rate limit is exceeded, **When** system is throttled, **Then** user sees "IBKR rate limit reached. Retrying in 30 seconds... (attempt 2/5)" with option to cancel

---

### User Story 4 - Import CSV Data Directly to Parquet (Priority: P2)

A trader has historical market data in CSV format and wants to import it directly into the Parquet catalog for use in backtests, without involving PostgreSQL.

**Why this priority**: Provides data import flexibility for users with external data sources, but is secondary to the core backtest workflows.

**Independent Test**: Can be tested by importing a CSV file and verifying Parquet file creation with correct structure, then running a backtest with that data.

**Acceptance Scenarios**:

1. **Given** user has AAPL_2023.csv file, **When** user runs import command, **Then** data is validated and written directly to Parquet catalog at ./data/catalog/AAPL.NASDAQ/1_MIN/2023-*.parquet
2. **Given** CSV contains invalid data (missing timestamps, negative prices), **When** import is attempted, **Then** validation fails with clear error message indicating specific row and issue
3. **Given** CSV data overlaps with existing Parquet data, **When** import is attempted, **Then** system prompts user to either skip, overwrite, or merge the data

---

### User Story 5 - Verify Data Availability Before Backtest (Priority: P3)

A trader wants to check which date ranges are available in the catalog for specific instruments before deciding which backtests to run.

**Why this priority**: Nice-to-have feature for power users who want to plan their backtests, but not essential for basic functionality.

**Independent Test**: Can be tested by running a data check command that scans catalog and displays availability report without loading full datasets.

**Acceptance Scenarios**:

1. **Given** catalog contains AAPL data for Jan-Mar 2024, **When** user runs data check for AAPL, **Then** system displays "Available: 2024-01-01 to 2024-03-31 (1-minute bars, 65 trading days)"
2. **Given** user checks multiple instruments, **When** report is generated, **Then** system shows gaps: "MSFT: Jan 1-15 ✓, Jan 16-31 ✗, Feb 1-28 ✓"
3. **Given** data check completes, **When** user sees gaps, **Then** message suggests: "Run backtest to auto-fetch missing data, or use: import-csv command"

---

### Edge Cases

- **What happens when Parquet file is corrupted?** System detects corruption, marks file as invalid, attempts re-fetch from IBKR if connection available, logs error for investigation
- **How does system handle partial data fetches?** If IBKR fetch fails mid-stream, system does not corrupt existing catalog; incomplete data is discarded, error logged, user prompted to retry
- **What happens with concurrent backtest runs accessing same data?** Parquet files are read-only during backtest; multiple concurrent reads are safe; writes from new fetches use atomic file operations to prevent conflicts
- **How does system handle clock changes or timezone issues?** All data is stored and indexed in UTC; timezone conversions only happen at display layer; explicit validation ensures no timestamp ambiguity
- **What happens during migration if PostgreSQL and Parquet data differ?** Migration phase preserves PostgreSQL data; validation script flags inconsistencies; manual reconciliation required before complete PostgreSQL deprecation
- **How does system handle disk space exhaustion?** Pre-checks available space before fetch operations; fails gracefully with clear message if insufficient space; provides estimate of required space for requested data range

## Requirements *(mandatory)*

### Functional Requirements

#### Data Flow & Storage

- **FR-001**: System MUST check Parquet catalog for requested data before attempting any external fetch operation
- **FR-002**: System MUST automatically fetch data from IBKR when requested data is not present in Parquet catalog and IBKR connection is available
- **FR-003**: System MUST persist all fetched IBKR data to Parquet catalog immediately after successful retrieval
- **FR-004**: System MUST organize Parquet files by instrument ID (symbol + exchange), bar type, and daily partitions
- **FR-005**: System MUST store all Parquet files in ./data/catalog/ directory with structured hierarchy
- **FR-006**: System MUST maintain catalog metadata for quick availability lookups without scanning all files

#### Data Catalog Service

- **FR-008**: System MUST verify complete data availability for a requested time period without loading the full dataset
- **FR-009**: System MUST return availability status including start date, end date, and gap identification
- **FR-010**: System MUST load bars from Parquet and return data in Nautilus Trader Bar format
- **FR-011**: System MUST support partial data loading for large datasets to prevent memory exhaustion
- **FR-012**: System MUST validate data integrity during load operations and detect corrupted files
- **FR-013**: System MUST organize Parquet data by date for efficient range queries

#### Backtest Integration

- **FR-014**: System MUST use Parquet catalog as the sole primary data source for all backtests
- **FR-015**: System MUST NOT query PostgreSQL for market data during backtest execution
- **FR-016**: System MUST cache frequently accessed data in memory during backtest execution to minimize disk I/O
- **FR-017**: System MUST provide progress indicators during data fetch operations showing percentage complete and estimated time remaining
- **FR-018**: System MUST clearly communicate whether data is being loaded from cache (Parquet) or fetched from IBKR

#### CSV Import

- **FR-019**: System MUST write imported CSV data directly to Parquet catalog, bypassing PostgreSQL entirely
- **FR-020**: System MUST validate CSV data using same rules as current implementation (timestamp format, price ranges, volume validation)
- **FR-021**: System MUST support same CSV file formats as current implementation
- **FR-022**: System MUST handle CSV import conflicts (overlapping data) by prompting user to skip, overwrite, or merge

#### Error Handling

- **FR-023**: System MUST fail backtest with clear error message when data is unavailable from both Parquet and IBKR
- **FR-024**: System MUST provide specific recovery actions in all error messages (e.g., "Run: docker compose up ibgateway")
- **FR-025**: System MUST implement automatic retry with exponential backoff for transient IBKR failures
- **FR-026**: System MUST respect IBKR rate limits (50 requests/second) during data fetch operations
- **FR-027**: System MUST allow users to cancel long-running fetch operations without data corruption
- **FR-028**: System MUST log all error conditions with sufficient context for debugging

#### PostgreSQL Deprecation

- **FR-029**: System MUST mark PostgreSQL market_data table as deprecated with clear comments
- **FR-030**: System MUST preserve existing PostgreSQL market data during transition period for validation purposes
- **FR-031**: System MUST provide migration script to export PostgreSQL data to Parquet format for users with existing data
- **FR-032**: Existing features accessing PostgreSQL MUST be migrated to Parquet approach or deprecated if not applicable

#### User Notifications & Feedback

- **FR-033**: System MUST use consistent terminology ("Parquet catalog" not "cache") in all user-facing messages
- **FR-034**: System MUST provide actionable error resolution steps in every error message
- **FR-035**: System MUST include progress indicators for operations exceeding 5 seconds
- **FR-036**: System MUST maintain consistent message formatting across all CLI commands

#### Command Line Interface

- **FR-037**: System MUST provide data availability check command without fetching data
- **FR-038**: System MUST display date ranges available in catalog with gap identification
- **FR-039**: System MUST provide clear help text explaining new Parquet-first workflow

### Key Entities

- **Parquet Catalog**: Central storage repository for all market data; organized hierarchically by instrument, bar type, and date partitions; serves as single source of truth; stores columnar data optimized for analytical queries

- **Instrument ID**: Unique identifier combining symbol and exchange (e.g., AAPL.NASDAQ); used for organizing Parquet files; immutable once created; determines directory structure in catalog

- **Bar Type**: Market data aggregation granularity (1_MIN, 5_MIN, 1_HOUR, 1_DAY); determines partitioning strategy; affects storage layout and query performance; configured per instrument

- **Data Availability Record**: Metadata tracking which date ranges exist in catalog for each instrument/bar-type combination; enables fast availability checks without file scanning; updated on each data fetch or import

- **Fetch Request**: User-initiated or automatic request for data not in catalog; triggers IBKR connection; includes instrument, bar type, date range, and retry policy

- **Catalog Metadata**: Lightweight index of available data including start/end dates, file locations, record counts, and last update timestamps; stored separately from data files for fast querying

- **Data Partition**: Daily file representing one day of market data for specific instrument and bar type; atomic unit of storage; enables efficient range queries

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All backtests retrieve market data from Parquet catalog as primary source without querying PostgreSQL
- **SC-002**: Data fetch operations automatically trigger when requested data is absent from catalog, with 100% success rate when IBKR is available
- **SC-003**: Fetched data persists to Parquet catalog for future use with zero manual intervention required
- **SC-004**: Users receive clear failure messages with actionable recovery steps when data is unavailable from both sources
- **SC-005**: IBKR rate limits never exceeded (50 requests/second) during automatic fetch operations
- **SC-006**: Users can complete data import from CSV to Parquet catalog successfully
- **SC-007**: Error messages consistently provide specific resolution steps with 100% of error scenarios documented
- **SC-008**: Concurrent backtest runs execute without data conflicts or race conditions
- **SC-009**: Zero data loss occurs during partial fetch failures or system interruptions
- **SC-010**: Users can identify data gaps and plan data fetching without running full backtests

### User Experience Outcomes

- **UX-001**: Data fetching process is transparent with clear progress indicators for all operations exceeding 5 seconds
- **UX-002**: Terminology is consistent across all user-facing messages using "Parquet catalog" uniformly
- **UX-003**: Recovery procedures for all error scenarios are documented and easily accessible

## Assumptions

1. **IBKR Connection**: Users have access to Interactive Brokers Gateway/TWS with appropriate market data subscriptions for fetching new data
2. **Disk Space**: Users have sufficient disk space for storing Parquet files
3. **Data Latency**: Users accept that newly fetched data requires time to download and persist (not instantaneous)
4. **UTC Timezone**: All timestamp data is stored in UTC timezone to avoid ambiguity
5. **Nautilus Trader Compatibility**: Parquet data format can be converted to Nautilus Trader Bar format without data loss
6. **Atomic File Operations**: Filesystem supports atomic writes for preventing partial file corruption during concurrent access
7. **Migration Approach**: Existing features and tests will be migrated to new Parquet approach or deprecated if not applicable
8. **Read-Only Access**: Backtest operations perform read-only access to Parquet files; only fetch/import operations write data
9. **Single Machine Deployment**: Catalog is designed for single-machine deployment; distributed storage is out of scope for this phase

## Migration Strategy

### Phase 1: Parallel Operation (Current State)

- Both PostgreSQL and Parquet storage systems remain operational
- No breaking changes to existing functionality
- Validation of new Parquet-based approach runs in parallel
- Duration: Complete (current state)

### Phase 2: Parquet-First Implementation (This Specification)

1. Create unified data catalog service abstraction
2. Refactor backtest runner to use Parquet catalog exclusively
3. Migrate CSV import to direct Parquet writing
4. Migrate existing features and tests to Parquet approach or deprecate if not applicable
5. Deprecate PostgreSQL market_data table with clear warnings
6. Update all data-related CLI commands
7. Update documentation and user guides

**Expected Duration**: 2-3 days

**Migration Principle**: Existing features are migrated to the new Parquet approach or deprecated. No backward compatibility layers are maintained.

### Phase 3: PostgreSQL Cleanup (Future Scope)

- Remove deprecated PostgreSQL market data tables
- Eliminate redundant data access code
- Implement metadata tracking in PostgreSQL for data inventory
- Expected Duration: 1 day (future phase)

## Out of Scope

The following enhancements are explicitly excluded from this specification and deferred to future releases:

### PostgreSQL Metadata Management
- Data inventory tracking (catalog statistics, access patterns)
- Quality metrics storage (data completeness scores, validation results)
- Symbol metadata caching (instrument details, contract specifications)

### Advanced Data Exploration
- DuckDB integration for SQL queries on Parquet files
- Jupyter notebook integration for interactive analysis
- Real-time data visualization dashboards
- Advanced analytics pipelines (feature engineering, ML data prep)

### Cloud Storage Integration
- S3/GCS backup for Parquet files
- Distributed catalog management across multiple machines
- Multi-region data replication
- Cloud-native deployment options (AWS Lambda, Kubernetes)

### Performance Optimizations
- Query result caching layer beyond in-memory backtest cache
- Parallel data loading across multiple instruments
- Incremental data updates (append-only optimization)
- Data compression strategies

## Risk Mitigation

1. **Data Preservation**: PostgreSQL data remains intact during entire transition; no destructive operations until validation complete
2. **Migration Script**: Provide script to export PostgreSQL data to Parquet format for users with existing data
3. **Extensive Testing**: Comprehensive unit and integration tests before deployment
4. **User Communication**: Clear changelog and migration guide published before release
5. **Monitoring**: Enhanced logging during transition period to quickly identify issues

## Testing Requirements

### Unit Testing Coverage

- Data availability checking logic (mocked filesystem)
- Parquet read/write operations (in-memory test files)
- IBKR fetch triggering conditions (mocked connection states)
- Error handling pathways (simulated failure scenarios)
- CSV validation and import logic
- Data migration from PostgreSQL to Parquet format

### Integration Testing Scenarios

1. Backtest with missing data successfully triggers fetch from IBKR
2. Backtest with existing data skips fetch and loads from Parquet
3. CSV import creates correctly structured Parquet files
4. Migrated features work correctly with Parquet catalog
5. Concurrent backtests handle data access without conflicts
6. Partial fetch failure does not corrupt catalog
7. Rate limit handling prevents IBKR throttling
8. Data gap detection correctly identifies missing ranges
9. PostgreSQL to Parquet migration script handles all data correctly

## Documentation Requirements

1. **Architecture Diagrams**: Update system architecture to show Parquet-first data flow
2. **Data Flow Documentation**: Revise sequence diagrams showing new fetch-persist-load lifecycle
3. **Migration Guide**: Step-by-step instructions for users transitioning from dual storage
4. **Troubleshooting Procedures**: Common error scenarios and resolution steps
5. **CLI Command Reference**: Updated help text and examples for all data commands
6. **Developer Guide**: Code-level documentation for data catalog service API
7. **Release Notes**: Clear changelog explaining breaking changes and migration path
