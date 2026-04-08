# Story 1.2: ETF CSV Parser with OHLC Validation

Status: ready-for-dev

## Story

As a system operator,
I want the system to parse FirstRate ETF headerless CSV files and validate OHLC data integrity,
So that I can trust the data before it enters the Parquet catalog.

## Acceptance Criteria

1. **Given** a FirstRate ETF CSV file with 6 headerless columns (Datetime, Open, High, Low, Close, Volume) **When** the ETF parser processes the file **Then** it produces a list of Nautilus Bar objects sorted by ts_init ascending **And** all price values maintain source decimal precision (no floating-point rounding artifacts) **And** timestamps are UTC-normalized

2. **Given** an ETF CSV file containing rows where high < low **When** the parser validates the bars **Then** the invalid rows are flagged in the ValidationResult with specific row details **And** the validation reports the count and nature of invalid rows

3. **Given** an ETF CSV file containing rows where volume < 0 **When** the parser validates the bars **Then** the invalid rows are flagged in the ValidationResult

4. **Given** the parser framework **When** a new parser class is created **Then** it extends the base parser ABC from `src/services/firstrate/parsers/base.py` **And** it implements `parse_file(file_path, ticker) -> list[Bar]`, `map_instrument_id(ticker, db_session) -> InstrumentId`, `validate_bars(bars) -> ValidationResult` **And** it is registered via `@register_parser(asset_class=AssetClass.ETF)` decorator

5. **Given** the parser registry **When** a parser is requested for asset class ETF **Then** the registered ETF parser is returned **And** requesting an unregistered asset class raises a clear error

## Tasks / Subtasks

- [ ] Task 1: Base parser ABC and registry (AC: #4, #5)
  - [ ] 1.1 Define `BaseParser` ABC in `src/services/firstrate/parsers/base.py` with abstract methods
  - [ ] 1.2 Implement `@register_parser(asset_class)` decorator and `_PARSER_REGISTRY` dict
  - [ ] 1.3 Implement `get_parser(asset_class) -> BaseParser` lookup function
  - [ ] 1.4 Implement shared `validate_bars(bars) -> ValidationResult` in base class (OHLC + volume checks)
- [ ] Task 2: ETF parser implementation (AC: #1)
  - [ ] 2.1 Create `ETFParser` class in `src/services/firstrate/parsers/etf_parser.py`
  - [ ] 2.2 Implement `parse_file()` — read headerless CSV, produce `list[Bar]` sorted by ts_init
  - [ ] 2.3 Implement `map_instrument_id()` — stub delegating to future InstrumentMapper (Story 1.3)
  - [ ] 2.4 Register via `@register_parser(asset_class=AssetClass.ETF)`
- [ ] Task 3: OHLC validation (AC: #2, #3)
  - [ ] 3.1 Base class `validate_bars()` checks: high >= low, volume >= 0 for each bar
  - [ ] 3.2 Return `ValidationResult` with `valid`, `errors` (row-level detail), `row_count`, `invalid_rows`
- [ ] Task 4: Export and wire up (AC: #4, #5)
  - [ ] 4.1 Update `src/services/firstrate/parsers/__init__.py` to export base, registry, and ETF parser
  - [ ] 4.2 Ensure importing the parsers package auto-registers ETF parser

## Dev Notes

### Architecture Compliance

- **ADR-4:** Strategy pattern with parser registry — each CSV schema is a parser class with common interface; all parsers produce `list[Bar]` with shared validation hooks; registration via `@register_parser` decorator [Source: architecture.md#ADR-4]
- **Parser interface contract from architecture.md:** `parse_file(file_path, ticker) -> list[Bar]`, `map_instrument_id(ticker, db_session) -> InstrumentId`, `validate_bars(bars) -> ValidationResult`
- Parsers are **stateless** — given a file path, produce bars. They never write to Parquet or DB
- All parsers produce `list[Bar]` sorted by `ts_init` ascending — the import service depends on this ordering

### FirstRate ETF CSV Format

**Critical: files are headerless .txt files, comma-delimited.**

**Source:** `/Users/allay/Data/ETF/ETF_PARSER_SPEC.md` — authoritative data spec with real-world data issues.

**Directory Structure (real layout):**
```
ETF/
├── ETF_1day/
│   └── etf_{A-Z}_full_1day_adjsplitdiv_{hash}/
│       └── {TICKER}_full_1day_adjsplitdiv.txt
├── ETF_1min/
│   └── etf_{A-Z}_full_1min_adjsplitdiv_{hash}/
│       └── {TICKER}_full_1min_adjsplitdiv.txt
├── ETF_5min/
│   └── etf_{A-Z}_full_5min_adjsplitdiv_{hash}/
│       └── {TICKER}_full_5min_adjsplitdiv.txt
└── ETF_1hour/
    └── etf_{A-Z}_full_1hour_adjsplitdiv_{hash}/
        └── {TICKER}_full_1hour_adjsplitdiv.txt
```

**Shard discovery:** Each timeframe dir has 26 shard subdirectories (A-Z). The `{hash}` suffix varies per shard — **never hardcode shard names**. Discover by listing dirs matching `etf_*` prefix. Some letters may have no ETFs (not an error).

**Ticker resolution from filename:** Strip the `_full_{timeframe}_adjsplitdiv.txt` suffix to get ticker symbol.

**Two sub-formats:**

| | Intraday (1min, 5min, 1hour) | Daily (1day) |
|---|---|---|
| Columns | 6: Datetime,O,H,L,C,Volume | 6: Date,O,H,L,C,Volume |
| Timestamp | `YYYY-MM-DD HH:MM:SS` | `YYYY-MM-DD` (date only) |
| Volume | integer (some files use float notation like `248.0`) | integer |

**Example rows:**
```
# Intraday (1min/5min/1hour):
2020-09-09 09:00:00,25.1,25.1046,25.08,25.08,10619

# Daily (1day):
2002-05-22,43.8068,44.1444,43.0834,43.2522,23762
```

**Data characteristics:**
- All files use `adjsplitdiv` (split+dividend adjusted) — prices directly comparable across timeframes
- 5,072 tickers across all 4 timeframes
- All price data must maintain source decimal precision through conversion

### Known Data Issues (MUST HANDLE)

These are real issues found in the ETF source data. The parser **must** handle all of them:

**1. Leading blank lines in `ETF_1day` (773 of 5,072 files)**
- 773 daily files begin with 1-6 blank lines before first data row
- Always leading (never mid-file). Most have 1; ~150 have 4-6 leading blanks
- **Only affects `ETF_1day`** — intraday timeframes are clean
- **Action:** Skip blank/empty lines during parsing

**2. Mixed line endings in `ETF_1day`**
- The same 773 files use CRLF (`\r\n`) while remaining files use LF (`\n`)
- Intraday timeframes consistently use LF
- **Action:** Strip trailing `\r` from all lines before parsing

**3. Empty files (4 tickers in 5min and 1hour)**

| Ticker | 5min | 1hour | 1day | 1min |
|--------|------|-------|------|------|
| ARKA | empty | empty | 462 rows | 3,509 rows |
| ARKZ | empty | empty | 464 rows | 4,580 rows |
| CVSE | empty | empty | 432 rows | 916 rows |
| IDAT | empty | empty | 1,052 rows | 4,356 rows |

- **Action:** Skip empty (0-byte) files; log and continue, do not treat as error

**4. Negative prices (2 tickers in `ETF_1day`)**
- Tickers `ARKD` and `IDAT` contain negative OHLC prices and negative volumes
- Likely provider data quality issue; may also appear in intraday files for same tickers
- **Action:** OHLC validation should flag these (high < low check catches many); validation reports them but does not block import of other tickers

### Nautilus Bar Creation Pattern

Follow the established codebase pattern from `src/services/csv_loader.py` and `src/utils/mock_data.py`:

```python
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.identifiers import InstrumentId

# BarType string format: "{INSTRUMENT_ID}-{STEP}-{AGGREGATION}-{PRICE_TYPE}-EXTERNAL"
# Examples:
#   "SPY.ARCA-1-DAY-LAST-EXTERNAL"     (daily)
#   "SPY.ARCA-1-MINUTE-LAST-EXTERNAL"   (1-min)
#   "SPY.ARCA-5-MINUTE-LAST-EXTERNAL"   (5-min)
#   "SPY.ARCA-1-HOUR-LAST-EXTERNAL"     (1-hour)

bar_type = BarType.from_str(f"{instrument_id}-1-DAY-LAST-EXTERNAL")

bar = Bar(
    bar_type=bar_type,
    open=Price.from_str(f"{open_val:.2f}"),    # Use source precision
    high=Price.from_str(f"{high_val:.2f}"),
    low=Price.from_str(f"{low_val:.2f}"),
    close=Price.from_str(f"{close_val:.2f}"),
    volume=Quantity.from_int(int(volume)),       # Or Quantity.from_str() for float volume
    ts_event=int(dt.timestamp() * 1_000_000_000),  # Nanoseconds!
    ts_init=int(dt.timestamp() * 1_000_000_000),
)
```

**Price precision:** Use `Price.from_str()` with the raw string value from CSV to preserve source decimal precision. Do NOT convert to float first — parse the CSV string value directly. Determine precision from the string (count digits after decimal point).

**Timestamps:** `ts_event` and `ts_init` must be **nanoseconds** since Unix epoch. Use `int(datetime.timestamp() * 1_000_000_000)`. ETF data should be UTC-normalized.

**Volume:** Use `Quantity.from_str()` for intraday (volume is float) and `Quantity.from_int()` for daily (volume is integer). Determine precision from the CSV value.

### Parser Registry Pattern

Mirror the `@register_strategy` pattern in the codebase. The registry is a module-level dict mapping `AssetClass` -> parser class:

```python
_PARSER_REGISTRY: dict[AssetClass, type[BaseParser]] = {}

def register_parser(asset_class: AssetClass):
    def decorator(cls: type[BaseParser]) -> type[BaseParser]:
        _PARSER_REGISTRY[asset_class] = cls
        return cls
    return decorator

def get_parser(asset_class: AssetClass) -> BaseParser:
    if asset_class not in _PARSER_REGISTRY:
        raise ValueError(f"No parser registered for {asset_class}")
    return _PARSER_REGISTRY[asset_class]()
```

### BarType and Timeframe Mapping

The parser needs to accept a timeframe parameter to build the correct `BarType` string. Mapping:

| Timeframe | BarType spec |
|---|---|
| daily | `1-DAY-LAST-EXTERNAL` |
| 1-hour | `1-HOUR-LAST-EXTERNAL` |
| 5-min | `5-MINUTE-LAST-EXTERNAL` |
| 1-min | `1-MINUTE-LAST-EXTERNAL` |

The `parse_file()` method needs the `instrument_id` (InstrumentId) and `bar_type` (BarType) to construct Bar objects. Since instrument ID mapping is Story 1.3, for now `map_instrument_id()` can be a stub that creates a basic InstrumentId (e.g., `InstrumentId.from_str(f"{ticker}.ARCA")`).

### File Structure (Required Locations)

```
src/services/firstrate/parsers/
  __init__.py              # MODIFY: export registry, base, ETF parser
  base.py                  # MODIFY: replace stub with ABC + registry + shared validation
  etf_parser.py            # NEW: ETFParser class

tests/unit/services/firstrate/
  __init__.py              # EXISTS (from Story 1.1)
  test_base_parser.py      # NEW: base class + registry tests
  test_etf_parser.py       # NEW: ETF parser + validation tests
```

### Existing Code to Reuse

- **`src/models/catalog.py`** — `AssetClass` enum, `ValidationResult` model (created in Story 1.1)
- **`src/services/firstrate/parsers/base.py`** — currently a stub, replace with real ABC
- **`src/services/firstrate/parsers/__init__.py`** — currently a stub, update exports
- **`src/services/csv_loader.py`** — reference for Bar creation pattern (Price.from_str, Quantity, timestamp conversion)
- **`src/utils/mock_data.py`** — reference for BarType.from_str format

### Anti-Patterns to Avoid

- **Do NOT add headers to CSV reading** — FirstRate files are headerless; use `pd.read_csv(path, header=None, names=[...])` or manual CSV reading
- **Do NOT convert prices through float** — parse CSV string directly to `Price.from_str()` to maintain decimal precision
- **Do NOT hardcode price precision** — detect from CSV source values (count decimal places in the string)
- **Do NOT put parsing + writing in the same class** — parsers only parse and validate; import service (Story 1.4) orchestrates writes
- **Do NOT import from `src.core.*`** — parser code lives in `src/services/firstrate/`
- **Do NOT create a `LiveLogger` or `Logger` directly** — respect LogGuard lifecycle
- **Do NOT write to Parquet or DB from parsers** — they only produce `list[Bar]` and `ValidationResult`
- **Do NOT hardcode shard directory names** — the `{hash}` suffix varies; discover shards by listing dirs matching `etf_*` prefix
- **Do NOT assume all lines are data** — skip blank lines (773 daily files have leading blanks)
- **Do NOT assume LF line endings** — strip `\r` from all lines before parsing (773 daily files use CRLF)
- **Do NOT crash on empty files** — 4 tickers have 0-byte files in 5min/1hour; log and skip gracefully
- **Do NOT assume volume is always integer** — some intraday files use float notation like `248.0`; parse as float then convert

### Testing Strategy

- **TDD is mandatory** — write failing tests first
- **Unit tests** (`@pytest.mark.unit`):
  - Base parser ABC: cannot instantiate directly, subclass must implement all abstract methods
  - Registry: register, retrieve, unregistered raises error, duplicate registration behavior
  - ETF parser `parse_file()`: correct Bar count, price precision preserved, timestamps nanoseconds, sorted by ts_init
  - ETF parser with daily format (date-only `YYYY-MM-DD`) vs intraday format (`YYYY-MM-DD HH:MM:SS`)
  - Validation: valid data passes, high < low flagged, volume < 0 flagged, mixed valid/invalid
  - ValidationResult: correct error messages with row-level detail
  - **Known data issue tests (critical):**
    - Leading blank lines: file with 1-6 blank lines before data (daily format) — parser skips them
    - CRLF line endings: file with `\r\n` endings — parser handles correctly
    - Empty (0-byte) file — parser returns empty list, no crash
    - Negative prices (ARKD/IDAT pattern) — validation flags them in ValidationResult
    - Volume as float notation (`248.0`) — parsed correctly as integer volume
  - Edge cases: single row file, file with all invalid rows, malformed row (wrong column count)
- **Test fixtures:** Create small CSV files in `tmp_path` fixtures — no need for real FirstRate data. Include fixtures for each known data issue
- **No component/integration tests needed** — parsers are pure logic with no external dependencies

### Cross-Story Context

This story creates the parser framework that **all future parsers** (Stories for FX, Futures, Crypto, Index) will extend:
- Story 1.3 (Instrument Mapping) will flesh out `map_instrument_id()` — keep the stub clean
- Story 1.4 (Import Pipeline Core) will call `parse_file()` and `validate_bars()` in the per-ticker loop
- Future phases add new parser files (fx_parser.py, futures_parser.py, etc.) using the same base class and registry

### Previous Story Intelligence (Story 1.1)

**Learnings from Story 1.1 implementation:**
- SQLite BigInteger autoincrement incompatibility: component tests used raw DDL instead of `Base.metadata.create_all` — not relevant for this story (no DB tests)
- Alembic autogenerate picked up extraneous changes: manually cleaned migration — not relevant
- Implementation order was: domain models -> ORM -> migration -> settings -> services — confirms foundation is stable
- All 515 unit + 440 component tests passing — regression baseline established
- Ruff auto-formatter actively enforced — make dependent changes in a single edit

**Review findings applied:**
- Updated_at now has `onupdate` handler (P2 fix)
- SQL LIKE wildcards escaped in search (P1 fix)
- Both fixes landed — no regressions to worry about

**Files from Story 1.1 this story builds on:**
- `src/services/firstrate/parsers/base.py` — stub to replace
- `src/services/firstrate/parsers/__init__.py` — stub to update
- `src/models/catalog.py` — AssetClass enum and ValidationResult model to use

### Project Structure Notes

- All paths align with architecture.md file organization section
- Parser goes in `src/services/firstrate/parsers/etf_parser.py` per architecture specification
- Tests go in `tests/unit/services/firstrate/` — directory exists from Story 1.1
- No conflicts with existing code — parsers have no dependencies on other services

### References

- [Source: /Users/allay/Data/ETF/ETF_PARSER_SPEC.md] **Authoritative ETF data spec** — directory layout, schemas, shard discovery, known data issues (blank lines, CRLF, empty files, negative prices)
- [Source: architecture.md#ADR-4] Strategy pattern with parser registry
- [Source: architecture.md#Implementation Patterns] Parser registry pattern, file organization
- [Source: epics.md#Story 1.2] Acceptance criteria and user story
- [Source: prd.md#Phase 1] ETF schema: 6-column headerless CSV
- [Source: product-brief-distillate.md#CSV Schema Details] Date formats, volume types, filename patterns
- [Source: project-context.md#Testing Rules] TDD mandatory, markers required
- [Source: src/services/csv_loader.py] Bar creation with Price.from_str, Quantity patterns
- [Source: src/utils/mock_data.py] BarType.from_str format reference
- [Source: src/models/catalog.py] AssetClass enum, ValidationResult model

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
