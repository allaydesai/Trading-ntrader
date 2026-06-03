# Story 4.1: Dividend & Stock Split Data Parsing

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a system operator,
I want the system to parse FirstRate dividend and stock split history files and store them in the database,
so that supplementary data is available for display alongside ticker data in the explorer (Story 4.2).

## Acceptance Criteria

1. **Dividend parsing → DB.** Given a FirstRate dividend history file for a ticker (`{TICKER}_divs.txt`, comma-delimited `date,amount`), when the dividend parser processes the file, then all dividend records are stored in `catalog_dividends` associated with the correct `catalog_name` + `ticker`, dates parsed as `date` (yyyy-MM-dd), and amounts stored as `Decimal`/`Numeric` (never float) preserving source value precision.

2. **Split parsing → DB.** Given a FirstRate split history file for a ticker (`{TICKER}.txt`, comma-delimited `date,ratio`), when the split parser processes the file, then all split records are stored in `catalog_stock_splits` associated with the correct `catalog_name` + `ticker`, with the ratio stored as `Decimal`/`Numeric` exactly as delivered (e.g. `4`, `7`, `0.5` for reverse splits). **NOTE:** the epic's `"2:1"` string format was a planning-time guess; the real FirstRate data is a single decimal ratio (new shares per old share). The `"4:1"` display string is derived in Story 4.2, **not** stored. See Dev Notes §"Discrepancy: split ratio format".

3. **Parsed alongside import, failure-isolated.** Given dividend/split source directories are provided during an import, when the import runs, then each ticker's dividend and split data is parsed and upserted as part of the same import invocation, **and** any supplementary parse/store failure for a ticker is caught, logged, and does **not** block (or fail) that ticker's bar-data import.

4. **No-file is success.** Given a ticker that has no dividend or split file, when the import processes that ticker, then the import completes successfully with no supplementary records stored for that ticker (and no error/warning that flips the exit code).

5. **Idempotent re-run.** Given an import is re-run for a ticker with existing supplementary data, when the supplementary data is processed, then existing records for `(catalog_name, ticker)` are replaced with the latest parsed set (no duplicate rows accumulate across runs).

6. **No-op when dirs absent.** Given neither `--dividends-dir` nor `--splits-dir` is provided and none is auto-discovered, when the import runs, then bar import behaves exactly as before this story (zero regression) and no supplementary records are written.

## Tasks / Subtasks

- [x] **Task 1 — DB models for the two new tables (AC: 1, 2, 5)**
  - [x] Create `src/db/models/catalog_dividend.py` → `CatalogDividend(Base, TimestampMixin)`, `__tablename__ = "catalog_dividends"`. Columns: `id` BigInteger PK autoincrement; `catalog_name` String(50) not null; `ticker` String(20) not null; `ex_date` Date not null; `amount` Numeric(20, 8) not null. Constraints: `UniqueConstraint("catalog_name", "ticker", "ex_date", name="uq_catalog_dividends_catalog_ticker_date")`; `Index("ix_catalog_dividends_catalog_ticker", "catalog_name", "ticker")`. Mirror the column style of `src/db/models/catalog_instrument.py`.
  - [x] Create `src/db/models/catalog_stock_split.py` → `CatalogStockSplit(Base, TimestampMixin)`, `__tablename__ = "catalog_stock_splits"`. Columns: `id`; `catalog_name` String(50); `ticker` String(20); `effective_date` Date not null; `ratio` Numeric(20, 8) not null. Constraints: `UniqueConstraint("catalog_name", "ticker", "effective_date", name="uq_catalog_stock_splits_catalog_ticker_date")`; `Index("ix_catalog_stock_splits_catalog_ticker", "catalog_name", "ticker")`.
  - [x] Register both in `src/db/models/__init__.py` (`from ... import CatalogDividend` / `CatalogStockSplit`; add to `__all__`).
  - [x] Import both in `alembic/env.py` next to the existing `from src.db.models import CatalogInstrument  # noqa: F401` line so autogenerate detects them.
  - [x] Unit model tests in `tests/unit/models/` (mirror `test_catalog_instrument_model.py`): tablename, columns, unique constraint name, nullability.

- [x] **Task 2 — Alembic migration (AC: 1, 2)**
  - [x] `uv run alembic revision --autogenerate -m "add catalog_dividends and catalog_stock_splits tables"`. → `b3accfdb64bd_...py`
  - [x] **Verify `down_revision = "79f6e07bee8b"`** (current head — the Story 3-6 `data_quality_flag` migration). Confirmed chained automatically.
  - [x] Review the generated `upgrade()`/`downgrade()` — both tables, both unique constraints, both indexes present; `downgrade()` drops them.
  - [x] `uv run alembic upgrade head` then `uv run alembic downgrade -1` then `upgrade head` again — all succeed, migration reversible.

- [x] **Task 3 — Parsers (pure logic, no DB, no Nautilus) (AC: 1, 2, 4)**
  - [x] Create `src/services/firstrate/parsers/supplementary_parser.py` with two pure functions returning typed records — NOT registered with `@register_parser` (these emit DB rows, not `Bar`s).
  - [x] `parse_dividends(file_path: Path) -> list[DividendRow]` — split on `,`; parse date via `strptime("%Y-%m-%d")`; amount via `Decimal`. Blank lines skipped; malformed line logged + skipped (not raised).
  - [x] `parse_splits(file_path: Path) -> list[SplitRow]` — identical shape, `Decimal` ratio. **Split on comma, NOT slash** (verified against real files).
  - [x] Define lightweight `@dataclass(frozen=True)` rows (`DividendRow(ex_date, amount)`, `SplitRow(effective_date, ratio)`).
  - [x] Unit tests `test_supplementary_parser.py`: happy path; reverse-chronological order; reverse split (`ratio < 1`); amount precision (`0.260`/`0.26`); blank lines; malformed skipped; empty → `[]`. **12 tests pass.**

- [x] **Task 4 — Repositories (sync + async, both per project rule) (AC: 1, 2, 5)**
  - [x] Create `src/db/repositories/catalog_dividend_repository.py` and `catalog_stock_split_repository.py`, each with an **async** class and a **Sync** class (mirror the dual classes in `catalog_instrument_repository.py`).
  - [x] **Idempotent replace** method: `replace_for_ticker(catalog_name, ticker, rows)` — delete all existing rows for `(catalog_name, ticker)` then bulk-insert the new set, inside the caller's transaction (flush only; caller commits). Set-replace satisfies AC-5 + drops orphans on shrinking history.
  - [x] Async read method for Story 4.2: `list_by_ticker(catalog_name, ticker)` ordered by date descending.
  - [x] Wrap `IntegrityError` → `DuplicateRecordError` (and `OperationalError` → `DatabaseConnectionError`), matching the existing repo pattern.
  - [x] Component tests for both repos: insert, replace-on-rerun (count constant, content updated), shrinking-history drops orphans, list ordering, catalog scoping, other-ticker untouched, Decimal precision, reverse split < 1. **15 tests pass.**

- [x] **Task 5 — Supplementary loader service (AC: 3, 4, 5)**
  - [x] Create `src/services/firstrate/supplementary_loader.py` → `SupplementaryDataLoader` taking the two **sync** repositories.
  - [x] `load_for_ticker(ticker, catalog_name, dividends_dir, splits_dir) -> SupplementaryLoadResult` — resolves `{ticker}_divs.txt` / `{ticker}.txt`, parses, calls `replace_for_ticker`. Missing file or `None` dir → no-op (AC-4). Whole op wrapped in `try/except Exception`: logged + result flagged failed, **never propagated** (AC-3).
  - [x] Batch entry `load_for_tickers(...)` iterates and aggregates results (one failure doesn't abort batch).
  - [x] Unit tests with repo doubles: both files; missing div/present split; both missing no-op; None dirs no-op; parser raising isolated; repo raising isolated; batch aggregation; batch isolation. **8 tests pass.**

- [x] **Task 6 — Wire into the import pipeline & CLI (AC: 3, 4, 6)**
  - [x] In `src/cli/commands/import_data.py`: added `--dividends-dir` and `--splits-dir` (`click.Path(exists=True, file_okay=False, path_type=Path)`, both `default=None`).
  - [x] Added `_find_supplementary_dirs(source_path)` best-effort auto-discovery (probes `source_path` + parents for `stock_dividends`/`stock_splits` and `etf_*` variants). Explicit flags override discovery. None found → skip supplementary entirely (AC-6).
  - [x] In `_run_import`, constructs the two sync repos + `SupplementaryDataLoader`, invokes `load_for_tickers(...)` **once**, after profiles and **before** the per-timeframe loop. Ticker list derived from supplementary dirs (`_discover_supplementary_tickers`, skips `_`-prefixed readmes).
  - [x] Supplementary writes share the existing `session` and the single `session.commit()` in `_run_import`.
  - [x] Added `_print_supplementary_summary` (Dividends/Splits tickers+records, non-blocking failures) to `_print_summary` — informational only; `determine_exit_code` unchanged (bar results only).
  - [x] CLI-level tests prove a supplementary failure leaves bar exit code at 0 (AC-3) and absent dirs never construct the loader (AC-6, zero regression). Also discovery + ticker-derivation + flag-wiring tests. **12 new tests pass; 56 existing CLI tests still green.**

- [x] **Task 7 — Manual verification against real subset data (AC: 1–5)**
  - [x] Extended `~/Data/e2e-subset/` with `stock_dividends/` + `stock_splits/` symlinks for the 5 e2e tickers. AMZN/TSLA have **no** dividend file (they pay none) — exercises AC-4 no-op.
  - [x] Ran an import against `e2e-test` with the new flags (bars already present → all 5 skipped; supplementary loaded). Verified AAPL has **exactly 4 splits** (2020-08-31→4, 2014-06-09→7, 2005-02-28→2, 2000-06-21→2) and AAPL dividends match source (2026-02-09→0.26, …). AMZN/TSLA divs=0 (AC-4). Re-ran → counts stable at 14 splits / 156 dividends / AAPL 4 (AC-5), exit 0.
  - [x] **Real-data robustness fix found here:** NVDA_divs.txt repeats `2026-03-11` (FirstRate trailing-dup block) → unique-constraint violation. Added `_dedup_by_date` to the supplementary parser (keep-last, mirrors bar parser `_dedup_by_timestamp`) + 2 tests. This is why parsers were re-touched after Task 3.

### Review Findings

_Code review 2026-06-03 (adversarial: Blind Hunter + Edge Case Hunter + Acceptance Auditor). All 6 ACs functionally satisfied; migration chain, Decimal-never-float, dual repos, set-replace, dedup keep-last, and loader-never-constructed-when-absent all verified truthful. Findings below are robustness/edge concerns._

_Decisions resolved 2026-06-03: (D1) preserve-on-empty-parse → promoted to patch below. (D2) auto-discovery breadth → accepted as best-effort by design (explicit flags are the escape hatch) → dismissed._

**Patch (all fixed 2026-06-03 — lint + mypy clean; 89 affected tests pass incl. 5 new regression cases):**

- [x] [Review][Patch] Present-but-empty/all-malformed file no longer wipes existing history [src/services/firstrate/supplementary_loader.py:_load_dividends/_load_splits]. Per decision D1: when a present file parses to zero rows, the loader logs `supplementary_file_no_valid_rows_preserving_existing` and returns 0 WITHOUT calling `replace_for_ticker`, preserving existing rows. Regression test: `test_present_but_empty_file_preserves_existing`.
- [x] [Review][Patch] Per-ticker SAVEPOINT added [src/services/firstrate/supplementary_loader.py]. Each ticker's writes are wrapped in `self._dividend_repo.session.begin_nested()` so a flush-time error (e.g. PostgreSQL aborted-transaction) rolls back only that ticker and leaves the shared transaction usable for the bar import — DB-level isolation for AC-3.
- [x] [Review][Patch] Split discovery constrained [src/cli/commands/import_data.py:_discover_supplementary_tickers]. Added module-level `_TICKER_STEM_RE = ^[A-Z0-9][A-Z0-9.\-]*$`; the unanchored `*.txt` split glob (and the dividend glob) now only accept ticker-shaped stems, so a stray non-underscore `readme.txt` is no longer treated as a phantom ticker.
- [x] [Review][Patch] Parsers reject non-finite Decimals [src/services/firstrate/parsers/supplementary_parser.py]. New `_parse_finite_decimal(value, *, positive=False)` rejects `nan`/`inf` (which `Decimal()` does NOT raise on) and, for split ratios, values ≤ 0. Regression tests: `test_non_finite_amount_skipped`, `test_non_finite_or_non_positive_ratio_skipped`.
- [x] [Review][Patch] `_read_lines` now opens with `encoding="utf-8"` [src/services/firstrate/parsers/supplementary_parser.py], matching the bar parser (`firstrate_csv_parser.py:124`).
- [x] [Review][Patch] `_dedup_by_date` typed [src/services/firstrate/parsers/supplementary_parser.py]. Now `_dedup_by_date(rows: list[_T], key: Callable[[_T], date]) -> list[_T]` with `last_index: dict[date, int]`.

**Deferred:**

- [x] [Review][Defer] `src/cli/commands/import_data.py` is 718 lines, over the <500-line limit [src/cli/commands/import_data.py] — deferred, pre-existing (was 524 before this story; worsened by +194). New supplementary helpers could be extracted to a `supplementary_discovery` module. Individual functions (<50 lines) and line length (≤100) are within limits.

_Dismissed as noise (5): (1) "flushed but never committed / summary reports loaded" — false: `session.commit()` at L480 and the supplementary summary at L502 is only reached on the success path (rollback branches `return 2` early). (2) Bar-loop failure rolling back already-loaded supplementary rows — accepted by design; idempotent re-run restores them and the summary is not misprinted on that path. (3) 3-field / embedded-comma lines silently dropped — working as specified (log + skip). (4) Case-sensitivity ticker fragmentation — speculative; real FirstRate filenames use consistent uppercase. (5) `_find_supplementary_dirs` probing even when both flags supplied — negligible wasted stat calls, result discarded._

## Dev Notes

### Architecture & patterns (cite source paths)

- **Parser registry is for `Bar` parsers only.** `register_parser`/`get_parser` in `src/services/firstrate/parsers/base.py:40-77` map `AssetClass → BaseParser`, whose `parse_file()` returns `list[Bar]`. Dividend/split parsers emit DB rows, so **do not register them** — call them directly from the loader. [Source: src/services/firstrate/parsers/base.py]
- **company_profiles is the closest analog.** `InstrumentMapper.load_company_profiles(file_path, catalog_name, asset_class)` in `src/services/firstrate/instrument_mapper.py:67-113` reads a CSV and upserts `CatalogInstrument` rows. The supplementary loader mirrors this shape (parse file → upsert rows), and like profiles it is invoked **once** in `_run_import` before the timeframe loop. [Source: src/cli/commands/import_data.py `_run_import` ~lines 278-307]
- **Catalog scoping convention:** rows are scoped by `catalog_name` String(50) + `ticker`, with a composite unique constraint — see `CatalogInstrument` (`uq_catalog_instruments_catalog_ticker`) in `src/db/models/catalog_instrument.py`. Mirror exactly. [Source: src/db/models/catalog_instrument.py:80-92]
- **Dual DB rule (project-context):** Web uses async (`AsyncSession`), CLI uses sync (`Session`). When adding a DB feature, **add both repos**. CLI import (this story) uses the sync repo; Story 4.2 (explorer display) uses the async repo. [Source: _bmad-output/project-context.md "Dual DB pattern"]
- **Decimal, never float** for all financial values (amounts, ratios). Store as `Numeric`, parse via `Decimal`. [Source: _bmad-output/project-context.md "Decimal arithmetic for financial calculations"]
- **Per-ticker failure isolation already exists** for bars: `_import_ticker` wraps each ticker in `try/except` (`src/services/firstrate/import_service.py:284-300`). Supplementary loading sits *outside/before* that loop with its **own** isolation so a bad div/split file can never reach bar import. [Source: src/services/firstrate/import_service.py]
- **Structured logging:** `structlog.get_logger(__name__)`; warn on skipped malformed lines, error on per-ticker supplementary failure. Never bare `except:` — catch `Exception` explicitly. [Source: _bmad-output/project-context.md language rules]

### Real FirstRate data format (verified on disk, do not guess)

Source dirs (full dataset): `~/Data/stock_dividends/` and `~/Data/stock_splits/` (siblings of `~/Data/stock/`, **not** inside it). ETF equivalents: `~/Data/etf/etf_dividends`, `~/Data/etf/etf_splits`.

**Dividends** — `~/Data/stock_dividends/{TICKER}_divs.txt` (note `_divs` suffix), comma-delimited, reverse-chronological, no header:
```
2026-02-09,0.26
2025-08-11,0.260
2024-02-09,0.24
```
Date `yyyy-MM-dd`; amount a plain decimal (note `0.260` vs `0.26` — both valid, parse as `Decimal`).

**Splits** — `~/Data/stock_splits/{TICKER}.txt` (**no** suffix), comma-delimited, reverse-chronological, no header. `AAPL.txt`:
```
2020-08-31,4
2014-06-09,7
2005-02-28,2
2000-06-21,2
```
Ratio = new shares per old share (`4` = 4-for-1). **Reverse splits have ratio < 1** (e.g. `0.5`). One file per ticker; **no file for tickers with no splits/dividends** (AC-4). [Source: `~/Data/stock_splits/_splits_readme.txt`]

### Discrepancy: split ratio format (must read)

The epic AC said split ratios are stored like `"2:1"`, `"1:4"`. **That is wrong for this dataset** — FirstRate ships a single decimal ratio, not a colon string. Store the raw `Decimal` ratio (`4`, `0.5`). Any `"4:1"` / `"1:2"` display string is a *presentation* concern derived in Story 4.2. Storing the numeric ratio also keeps reverse splits (`< 1`) unambiguous. This is the intended interpretation; the story ACs above reflect it.

### Discrepancy: splits readme delimiter

`_splits_readme.txt` documents the format as `{Effective Date}/{Split Ratio}` (slash). The **actual files are comma-delimited** (`2020-08-31,4`). Parse on `,`. Trust the data, not the readme.

### Project structure (new files)

```
src/db/models/catalog_dividend.py            # CatalogDividend
src/db/models/catalog_stock_split.py         # CatalogStockSplit
src/db/repositories/catalog_dividend_repository.py        # async + Sync
src/db/repositories/catalog_stock_split_repository.py     # async + Sync
src/services/firstrate/parsers/supplementary_parser.py    # parse_dividends / parse_splits
src/services/firstrate/supplementary_loader.py            # SupplementaryDataLoader
alembic/versions/<rev>_add_catalog_dividends_and_catalog_stock_splits_tables.py
```
Edited: `src/db/models/__init__.py`, `alembic/env.py`, `src/cli/commands/import_data.py`.
Respect size limits: files <500 lines, functions <50, classes <100, line length 100. [Source: CLAUDE.md Foundational Rules]

### Migration head (verified)

CLAUDE.md says "4 migrations" — **stale**. There are 7; current head is `79f6e07bee8b` (Story 3-6 `data_quality_flag`). The new migration's `down_revision` must be `79f6e07bee8b`. Chain: `7d28f3a711e7 → 9c7d5c448387 → 0937d13d2502 → 34f3c8e99016 → 677ed1cdf56f → 67772db31d8d → 79f6e07bee8b`. [Source: alembic/versions/]

### Manual verification (Task 7)

`~/Data/e2e-subset/` currently has only `1day/1hour/1min/5min/` + `company_profiles.csv` — **no** div/split dirs. Add subset symlink dirs so the e2e import exercises the new path, e.g.:
```bash
mkdir -p ~/Data/e2e-subset/stock_dividends ~/Data/e2e-subset/stock_splits
for T in AAPL AMZN MSFT NVDA TSLA; do
  ln -sf ~/Data/stock_dividends/${T}_divs.txt ~/Data/e2e-subset/stock_dividends/
  [ -f ~/Data/stock_splits/${T}.txt ] && ln -sf ~/Data/stock_splits/${T}.txt ~/Data/e2e-subset/stock_splits/
done
```
Then import with `--dividends-dir ~/Data/e2e-subset/stock_dividends --splits-dir ~/Data/e2e-subset/stock_splits`. Catalog `e2e-test`, env per [[E2E catalog setup]]. The catalog is already TZ-correct post-Story-3-6, so no re-import of bars is required — supplementary load can run against the existing catalog.

### Testing standards

- **Tiers:** unit for parsers + loader (pure / test doubles, no Nautilus); component for repositories (real test DB, mirror `tests/component/db/test_catalog_instrument_repository.py`); unit/CLI for the import-service wiring. No `--forked` needed (no Nautilus C extensions touched). [Source: docs/agent/testing.md, CLAUDE.md test tiers]
- **TDD non-negotiable** — write the failing test first for each task (Red-Green-Refactor). [Source: CLAUDE.md Foundational Rules]
- Run `make test-unit`, `make test-component`, `make lint`, `make typecheck` before marking done. Coverage gate applies to `src/core` + `src/strategies`; new code is under `src/db`, `src/services`, `src/cli` — still keep tests comprehensive.

### Out of scope (defer to Story 4.2)

- Any explorer UI / HTMX `<details>` sections, REST endpoints, or availability flags. This story is **parse + persist only**. Story 4.2 reads these tables via the async repos.
- Company-profile display (already imported into `catalog_instruments` in Epic 1; 4.2 surfaces it).

### Project Structure Notes

- New models live beside existing ones in `src/db/models/` (one class per file, matching `catalog_instrument.py`, `trade.py`). No conflict with unified structure.
- Repositories follow the established dual async/Sync convention in `src/db/repositories/`.
- The CLI gains two optional flags; default behaviour (no flags) is byte-for-byte the prior import path (AC-6), so no regression to Epics 1–3 import flows.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.1] — original ACs (note split-ratio guess corrected here)
- [Source: src/services/firstrate/instrument_mapper.py:67-113] — load_company_profiles analog
- [Source: src/services/firstrate/import_service.py:130,156-300,441-460] — import loop, per-ticker isolation, FirstRate filename convention
- [Source: src/cli/commands/import_data.py:37-55,222-334,471-524] — `_find_profiles_csv`, `_run_import`, Click command
- [Source: src/db/models/catalog_instrument.py:17-101] — table style + constraints to mirror
- [Source: src/db/repositories/catalog_instrument_repository.py:18-376] — async + Sync repo + upsert pattern
- [Source: src/db/exceptions.py] — `DuplicateRecordError`, `RecordNotFoundError`
- [Source: alembic/env.py:1-40] — model registration for autogenerate
- [Source: ~/Data/stock_splits/_splits_readme.txt, ~/Data/stock_dividends/AAPL_divs.txt, ~/Data/stock_splits/AAPL.txt] — real data format
- [Source: _bmad-output/project-context.md] — dual DB, Decimal, logging, anti-patterns

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context) — BMAD dev-story workflow, TDD (Red-Green-Refactor).

### Debug Log References

- **NVDA duplicate ex_date (unique-constraint violation)** — manual Task 7 import against `e2e-test` failed with `UniqueViolation` on `uq_catalog_dividends_catalog_ticker_date` for `(e2e-test, NVDA, 2026-03-11)`. Root cause: `NVDA_divs.txt` repeats `2026-03-11` (FirstRate trailing-dup block, same class of defect the bar parser already handles). Fix: added `_dedup_by_date` to `supplementary_parser.py` (keep-last, mirrors `FirstRateCsvParser._dedup_by_timestamp`). Re-ran clean.

### Completion Notes List

- All 6 ACs satisfied; verified against real FirstRate data (Task 7).
- **AC-1/AC-2:** dividends + splits persisted to `catalog_dividends` / `catalog_stock_splits`; amounts/ratios stored as `Numeric(20,8)` via `Decimal` (never float). AAPL splits verified exactly: 2020-08-31→4, 2014-06-09→7, 2005-02-28→2, 2000-06-21→2.
- **AC-2 discrepancy honoured:** ratio stored as a single `Decimal` (`4`, `0.5`-reverse), NOT the epic's `"2:1"` string. `"4:1"` display is deferred to Story 4.2.
- **AC-3:** per-ticker failure isolation in `SupplementaryDataLoader` (never propagates) + supplementary load sits before/outside the bar loop; a supplementary failure leaves the bar exit code untouched (CLI test + manual proof).
- **AC-4:** missing file / `None` dir is a clean no-op — AMZN & TSLA (no dividend files) loaded 0 dividend rows with success.
- **AC-5:** idempotent set-replace (`replace_for_ticker` = delete-then-insert); re-run held counts at 14 splits / 156 dividends / AAPL 4 splits.
- **AC-6:** `--dividends-dir`/`--splits-dir` default None; when absent and nothing auto-discovered, the loader is never constructed (zero regression — proven by CLI test).
- **Deviation (documented):** added `_dedup_by_date` to the parser after Task 3, driven by real-data verification in Task 7 (see Debug Log).
- Migration `b3accfdb64bd` chained to head `79f6e07bee8b`, verified reversible (up → down → up). DB left at `b3accfdb64bd` (head).
- Quality gates: `ruff check` + `ruff format` clean; `mypy` clean on all 7 new source modules; **904 unit + 634 component tests pass**, no regressions.

### Change Log

| Date | Change |
|------|--------|
| 2026-06-03 | Story 4-1 implemented: dividend/split DB models + migration, dual repos (set-replace + list), supplementary parsers (with real-data dedup), loader with per-ticker isolation, CLI `--dividends-dir`/`--splits-dir` wiring + auto-discovery + summary. 49 new tests. Verified against `e2e-test` catalog. Status → review. |

### File List

**New — source:**
- `src/db/models/catalog_dividend.py`
- `src/db/models/catalog_stock_split.py`
- `src/db/repositories/catalog_dividend_repository.py`
- `src/db/repositories/catalog_stock_split_repository.py`
- `src/services/firstrate/parsers/supplementary_parser.py`
- `src/services/firstrate/supplementary_loader.py`
- `alembic/versions/b3accfdb64bd_add_catalog_dividends_and_catalog_stock_.py`

**New — tests:**
- `tests/unit/models/test_catalog_dividend_model.py`
- `tests/unit/models/test_catalog_stock_split_model.py`
- `tests/unit/services/firstrate/test_supplementary_parser.py`
- `tests/unit/services/firstrate/test_supplementary_loader.py`
- `tests/component/db/test_catalog_dividend_repository.py`
- `tests/component/db/test_catalog_stock_split_repository.py`
- `tests/unit/cli/commands/test_import_supplementary.py`

**Modified:**
- `src/db/models/__init__.py` (register both models)
- `alembic/env.py` (import both models for autogenerate)
- `src/cli/commands/import_data.py` (flags, discovery, loader wiring, summary)
- `tests/component/cli/commands/test_import_data.py` (mock signatures for new `_run_import` params)

**Off-repo (manual verification only):** `~/Data/e2e-subset/stock_dividends/` + `stock_splits/` symlink dirs.
