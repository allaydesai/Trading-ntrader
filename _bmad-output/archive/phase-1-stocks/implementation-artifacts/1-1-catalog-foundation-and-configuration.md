# Story 1.1: Catalog Foundation & Configuration

Status: done

## Story

As a system operator,
I want named catalog configuration, a catalog_instruments database table, and domain models for the import pipeline,
So that the system has the foundation to manage multiple data catalogs and track imported instruments.

## Acceptance Criteria

1. **Given** the system has no catalog infrastructure **When** the Alembic migration is run **Then** a `catalog_instruments` table is created with columns: id (BigInteger PK), ticker, nautilus_id, asset_class, catalog_name, exchange, name, sector, industry, ipo_date, date_range_start, date_range_end, bar_count_daily, bar_count_hourly, bar_count_minute, created_at, updated_at **And** indexes exist on ticker (B-tree), (catalog_name, asset_class) composite, and (catalog_name, ticker) unique constraint

2. **Given** the application configuration **When** CatalogSettings is loaded from environment variables **Then** named catalogs are defined with name, path, and format metadata consistent with existing IBKRSettings/KrakenSettings pattern **And** FirstRateSettings includes source path defaults and catalog configuration

3. **Given** the domain model definitions **When** domain models are imported **Then** AssetClass enum, CatalogConfig, ValidationResult, and ImportResult Pydantic models are available in `src/models/catalog.py`

4. **Given** a named catalog is configured in settings **When** CatalogManager resolves a catalog by name **Then** it returns a ParquetDataCatalog instance initialized at the configured path

5. **Given** the catalog_instruments table exists **When** MetadataService is used from the CLI (sync) or web (async) **Then** it provides CRUD operations for catalog_instruments via dual repository pattern (sync for CLI, async for web)

## Tasks / Subtasks

- [x] Task 1: Alembic migration for catalog_instruments table (AC: #1)
  - [x] 1.1 Create migration `005_add_catalog_instruments.py` in `alembic/versions/`
  - [x] 1.2 Define all columns with correct types (BigInteger PK, String, DateTime with timezone, Integer bar counts)
  - [x] 1.3 Add B-tree index on `ticker`
  - [x] 1.4 Add composite index on `(catalog_name, asset_class)`
  - [x] 1.5 Add unique constraint on `(catalog_name, ticker)`
  - [x] 1.6 Implement `downgrade()` to drop table and indexes
- [x] Task 2: SQLAlchemy ORM model for catalog_instruments (AC: #1, #5)
  - [x] 2.1 Create `src/db/models/catalog_instrument.py` inheriting `Base` + `TimestampMixin`
  - [x] 2.2 Define all mapped columns with type annotations (`Mapped[<type>]`)
  - [x] 2.3 Add `__table_args__` for composite index and unique constraint
  - [x] 2.4 Export from `src/db/models/__init__.py`
- [x] Task 3: Pydantic domain models (AC: #3)
  - [x] 3.1 Create `src/models/catalog.py` with AssetClass enum (ETF, STOCK, FUTURES, FX, CRYPTO, INDEX, DELISTED)
  - [x] 3.2 Add CatalogConfig model (name, path, format)
  - [x] 3.3 Add ValidationResult model (valid: bool, errors: list, row_count, invalid_rows)
  - [x] 3.4 Add ImportResult model (ticker, status, row_count, error, duration)
  - [x] 3.5 Export from `src/models/__init__.py`
- [x] Task 4: Pydantic settings for named catalogs (AC: #2)
  - [x] 4.1 Add `FirstRateSettings` to `src/config.py` with source_path, default catalog name
  - [x] 4.2 Add `CatalogSettings` with named catalog definitions (dict of name -> CatalogConfig)
  - [x] 4.3 Add `FIRSTRATE_CATALOG_PATH` env var support
  - [x] 4.4 Embed in main `Settings` class following IBKRSettings/KrakenSettings pattern
- [x] Task 5: CatalogManager service (AC: #4)
  - [x] 5.1 Create `src/services/firstrate/__init__.py` (and parsers/ subdirectory stubs)
  - [x] 5.2 Create `src/services/firstrate/catalog_manager.py` with `resolve_catalog(name) -> ParquetDataCatalog`
  - [x] 5.3 Use lazy initialization pattern (consistent with DataCatalogService)
- [x] Task 6: Dual repository for catalog_instruments (AC: #5)
  - [x] 6.1 Create `src/db/repositories/catalog_instrument_repository.py` with async methods (get_by_ticker, list_by_catalog, upsert, search)
  - [x] 6.2 Create sync counterpart for CLI usage
  - [x] 6.3 Follow existing `DatabaseRepository` patterns with `selectinload` where applicable
- [x] Task 7: MetadataService (AC: #5)
  - [x] 7.1 Create `src/services/firstrate/metadata_service.py` with CRUD operations
  - [x] 7.2 Async methods for web (using async repository)
  - [x] 7.3 Sync methods for CLI (using sync repository)

## Dev Notes

### Architecture Compliance

All decisions for this story are defined in the Architecture Decision Document:
- **ADR-1:** Catalog metadata stored in PostgreSQL (`catalog_instruments` table) for fast paginated search, sorting, and dual async/sync access [Source: architecture.md#ADR-1]
- **ADR-2:** Named catalogs defined via Pydantic settings (CatalogSettings), consistent with IBKRSettings/KrakenSettings pattern [Source: architecture.md#ADR-2]
- **ADR-3:** Single `catalog_instruments` table combining instrument identity with import metadata [Source: architecture.md#ADR-3]

### File Structure (Required Locations)

All FirstRate code lives under `src/services/firstrate/` per architecture.md:

```
src/services/firstrate/
  __init__.py
  catalog_manager.py       # Named catalog resolution
  metadata_service.py      # catalog_instruments CRUD (async + sync)
  parsers/
    __init__.py             # Stub for Story 1.2
    base.py                 # Stub for Story 1.2
src/db/models/catalog_instrument.py    # SQLAlchemy ORM model
src/db/repositories/catalog_instrument_repository.py  # Dual repo
src/models/catalog.py                  # Domain models + AssetClass enum
alembic/versions/005_add_catalog_instruments.py
```

Tests mirror source structure:
```
tests/unit/models/test_catalog.py           # Domain model tests
tests/unit/services/firstrate/test_catalog_manager.py
tests/unit/services/firstrate/test_metadata_service.py
tests/component/db/test_catalog_instrument_repository.py
```

### Existing Patterns to Follow

**SQLAlchemy model pattern** (from `src/db/models/backtest.py`):
- Inherit `Base` + `TimestampMixin`
- Use `Mapped[<type>]` for all columns
- `id: Mapped[int] = mapped_column(BigInteger, primary_key=True)`
- Define constraints in `__table_args__`

**Pydantic settings pattern** (from `src/config.py`):
- Nested `BaseSettings` classes with `model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False, "extra": "ignore"}`
- Embed as attribute in main `Settings` class
- Use `Field()` with descriptions for all config values

**Alembic migration pattern** (from existing migrations):
- Auto-generated revision IDs (alphanumeric hash, not sequential number)
- Module docstring describing the migration
- `upgrade()` creates table + indexes, `downgrade()` drops them
- Use `op.create_table()`, `op.create_index()`, `op.drop_table()`, `op.drop_index()`
- **Generate via**: `alembic revision --autogenerate -m "add catalog instruments table"` (do NOT manually pick a revision ID)

**Dual repository pattern** (from `src/services/database_repository.py`):
- Async methods using `async with get_session() as session:`
- Sync counterpart for CLI usage
- Custom domain exceptions from `src/db/exceptions.py` wrapping SQLAlchemy errors

**DataCatalogService lazy init pattern** (from `src/services/data_catalog.py`):
- Lazy-initialized catalog instances stored as instance attributes
- Availability caching pattern
- structlog for logging: `structlog.get_logger(__name__)`

### Technical Requirements

**Database column types for catalog_instruments:**

| Column | Type | Notes |
|---|---|---|
| id | BigInteger | PK, auto-increment |
| ticker | String(20) | B-tree indexed |
| nautilus_id | String(50) | e.g., "SPY.ARCA" |
| asset_class | String(20) | enum value as string |
| catalog_name | String(50) | part of unique constraint |
| exchange | String(20) | e.g., "ARCA", "XNAS" |
| name | String(200) | company/instrument name |
| sector | String(100) | nullable |
| industry | String(100) | nullable |
| ipo_date | Date | nullable |
| date_range_start | DateTime(timezone=True) | nullable until import |
| date_range_end | DateTime(timezone=True) | nullable until import |
| bar_count_daily | Integer | default 0 |
| bar_count_hourly | Integer | default 0 |
| bar_count_minute | Integer | default 0 |
| created_at | DateTime(timezone=True) | via TimestampMixin |
| updated_at | DateTime(timezone=True) | via TimestampMixin |

**Indexes:**
- `ix_catalog_instruments_ticker` — B-tree on `ticker` (for search)
- `ix_catalog_instruments_catalog_asset` — composite on `(catalog_name, asset_class)` (for filtered listing)
- `uq_catalog_instruments_catalog_ticker` — unique on `(catalog_name, ticker)` (prevent duplicates)

**CatalogSettings env var mapping:**
- `FIRSTRATE_CATALOG_PATH` — path to FirstRate Parquet catalog directory
- `FIRSTRATE_SOURCE_PATH` — default source directory for CSV imports (optional)
- Named catalogs as a dict in settings (small number, rarely changed)

**Nautilus ParquetDataCatalog initialization:**
```python
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
catalog = ParquetDataCatalog(path=str(catalog_path), fs_protocol="file")
```

**AssetClass enum values:** ETF, STOCK, FUTURES, FX, CRYPTO, INDEX, DELISTED (7 total, matching PRD phases)

### Anti-Patterns to Avoid

- **Do NOT put FirstRate code in `src/core/`** — core is framework-agnostic; all FirstRate code goes under `src/services/firstrate/`
- **Do NOT create a single god-service** — separate CatalogManager (Parquet resolution) from MetadataService (DB CRUD)
- **Do NOT hardcode catalog paths** — all paths via `CatalogSettings` / env vars
- **Do NOT manually assign Alembic revision IDs** — use `alembic revision --autogenerate`
- **Do NOT skip the sync repository** — CLI commands need sync DB access; the dual pattern is required
- **Do NOT use `float` for any price-related domain model fields** — use `Decimal` where applicable (bar counts are integers, so `int` is fine for those)
- **Do NOT create `updated_at` column manually** — `TimestampMixin` provides `created_at` only. Check if `updated_at` needs to be added to the model directly (existing pattern uses `TimestampMixin` for `created_at` and adds `updated_at` as `server_onupdate`)
- **Do NOT import from `src.services.firstrate.*` in `src/core/`** — service layer depends on core, never the reverse

### Testing Strategy

- **TDD is mandatory** — write failing tests first for every component
- **Unit tests** for: domain models (AssetClass, CatalogConfig, ValidationResult, ImportResult), CatalogManager resolution logic, MetadataService business logic
- **Component tests** for: repository CRUD against in-memory SQLite (`sqlite+aiosqlite:///:memory:`), Alembic migration up/down
- **Markers required**: `@pytest.mark.unit` or `@pytest.mark.component` on every test
- Use `pytest-asyncio` (auto mode) for async repository tests
- Composable fixtures for test data

### Cross-Story Context

This is the **foundation story** — Stories 1.2 through 1.7 all depend on artifacts created here:
- Story 1.2 (ETF Parser) needs: AssetClass enum, ValidationResult model, `src/services/firstrate/parsers/` directory
- Story 1.3 (Instrument Mapping) needs: catalog_instruments table, MetadataService, CatalogConfig
- Story 1.4 (Import Pipeline Core) needs: CatalogManager, MetadataService, ImportResult model
- Story 1.5 (CLI Command) needs: CatalogSettings for `--catalog` flag resolution
- Story 1.7 (Idempotent Import) needs: catalog_instruments metadata for completeness detection

**Ensure all stubs and interfaces are cleanly extensible** — downstream stories should not need to refactor foundation code.

### Project Structure Notes

- All new paths align with architecture.md file organization section
- No conflicts with existing `src/services/data_catalog.py` — FirstRate catalog is a parallel, independent service under `src/services/firstrate/`
- Existing `src/models/catalog_metadata.py` contains `CatalogAvailability` and `FetchRequest` for IBKR/Kraken — new `src/models/catalog.py` is for FirstRate-specific domain models (no naming collision)

### References

- [Source: architecture.md#ADR-1] Catalog metadata in PostgreSQL
- [Source: architecture.md#ADR-2] Named catalogs via Pydantic settings
- [Source: architecture.md#ADR-3] Single catalog_instruments table design
- [Source: architecture.md#Implementation Patterns] File organization, database naming, parser registry pattern
- [Source: epics.md#Story 1.1] Acceptance criteria and user story
- [Source: prd.md#Technical Requirements] Architecture considerations, implementation considerations
- [Source: project-context.md#Critical Implementation Rules] Dual DB pattern, Pydantic settings, SQLAlchemy model inheritance
- [Source: src/db/models/backtest.py] ORM model pattern with Base + TimestampMixin
- [Source: src/config.py] IBKRSettings/KrakenSettings nested settings pattern
- [Source: src/services/data_catalog.py] DataCatalogService lazy init + availability caching pattern

## Dev Agent Record

### Agent Model Used

Claude Opus 4.6 (1M context)

### Debug Log References

- SQLite BigInteger autoincrement incompatibility: solved by using raw DDL in component test fixtures instead of `Base.metadata.create_all` (which also avoids JSONB incompatibility with other tables)
- Alembic autogenerate picked up extraneous changes from other tables: manually cleaned migration to only include catalog_instruments operations

### Completion Notes List

- All 7 tasks completed with TDD (red-green-refactor) approach
- 44 new tests added: 12 domain model, 7 ORM model, 7 settings, 4 CatalogManager, 11 repository, 10 MetadataService (minus 7 ORM = 44 new across all files)
- Full regression suite passes: 515 unit tests, 440 component tests
- Migration verified: upgrade and downgrade both work correctly
- All ruff lint checks pass on new files
- Implementation order: domain models → ORM model → migration → settings → CatalogManager → repositories → MetadataService

### File List

New files:
- src/models/catalog.py
- src/db/models/catalog_instrument.py
- src/services/firstrate/__init__.py
- src/services/firstrate/catalog_manager.py
- src/services/firstrate/metadata_service.py
- src/services/firstrate/parsers/__init__.py
- src/services/firstrate/parsers/base.py
- src/db/repositories/catalog_instrument_repository.py
- alembic/versions/677ed1cdf56f_add_catalog_instruments_table.py
- tests/unit/models/test_catalog.py
- tests/unit/models/test_catalog_instrument_model.py
- tests/unit/test_catalog_settings.py
- tests/unit/services/firstrate/__init__.py
- tests/unit/services/firstrate/test_catalog_manager.py
- tests/unit/services/firstrate/test_metadata_service.py
- tests/component/db/test_catalog_instrument_repository.py

Modified files:
- src/models/__init__.py
- src/db/models/__init__.py
- src/config.py
- alembic/env.py

### Review Findings

- [x] [Review][Decision] D1: CatalogSettings simplified — replaced `catalogs` dict with `catalog_base_path` + `default_catalog_name`; CatalogManager now discovers catalogs from filesystem [src/config.py, src/services/firstrate/catalog_manager.py] — RESOLVED: simplified
- [x] [Review][Decision] D2: Missing delete operation — AC5 specifies "CRUD" but Delete not needed by downstream stories — RESOLVED: accepted as-is
- [x] [Review][Decision] D3: ORM/migration nullability mismatch — made `nautilus_id`, `exchange`, `name` nullable in ORM to match migration for partial imports — RESOLVED: fixed
- [x] [Review][Patch] P1: SQL LIKE wildcards escaped in search() [src/db/repositories/catalog_instrument_repository.py] — FIXED
- [x] [Review][Patch] P2: `updated_at` column now has `onupdate=lambda: datetime.now(timezone.utc)` [src/db/models/catalog_instrument.py] — FIXED
- [x] [Review][Patch] P3: `ipo_date` type annotation corrected to `Mapped[Optional[date]]` [src/db/models/catalog_instrument.py] — FIXED
- [x] [Review][Defer] W1: Async upsert TOCTOU race (SELECT then UPDATE without FOR UPDATE lock) — unique constraint catches collisions; acceptable for sequential import pipeline [src/db/repositories/catalog_instrument_repository.py:44] — deferred, acceptable risk
- [x] [Review][Defer] W2: `CatalogConfig.name` field redundant with dict key — no validation they match [src/models/catalog.py:32] — deferred, design smell
- [x] [Review][Defer] W3: CatalogManager is general-purpose but lives under vendor-specific `src/services/firstrate/` [src/services/firstrate/catalog_manager.py] — deferred, can relocate if more catalogs added

### Change Log

- 2026-04-06: Code review completed — 3 decision-needed, 3 patch, 3 deferred, 5 dismissed
- 2026-04-05: Story 1.1 implemented — catalog foundation with domain models, ORM model, migration, settings, CatalogManager, dual repository, and MetadataService
