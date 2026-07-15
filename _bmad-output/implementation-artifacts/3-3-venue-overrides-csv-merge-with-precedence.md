# Story 3.3: venue_overrides.csv Merge with Precedence

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to supply manual venue corrections in a git-tracked `venue_overrides.csv` (header exactly `ticker,venue`, path via config) that the system loads and merges into `instrument_metadata` during the import-orchestration step,
so that I can resolve venues FMP couldn't — with the override taking precedence and flipping the row to `RESOLVED` — without a web write-path, and idempotently across re-runs.

## Acceptance Criteria

1. **Given** a git-tracked `venue_overrides.csv` with header exactly `ticker,venue` (path resolved from config), **When** an ETF import or a `metadata apply-overrides` refresh runs, **Then** the file is loaded and merged into `instrument_metadata` during the import-orchestration step (in `src/cli/commands/import_data.py` / the `metadata` CLI group), **not** inside `FMPClient`, the FMP provider, or the resolution service.
2. **Given** a ticker present in both FMP data (absent/ambiguous venue → a persisted `VENUE_UNRESOLVED` row) and the override file, **When** the merge runs, **Then** the override venue takes precedence: the row's `venue` is set to the override value and its `resolution_status` becomes `RESOLVED` (so Story 3.1 qualification and Story 3.5 inclusion pick it up). No FMP call is made for that ticker during the merge.
3. **Given** the same override file, **When** the import (or `metadata apply-overrides`) is re-run, **Then** the merge is idempotent — a row already at the override target (`venue == override AND status == RESOLVED`) is left untouched (no write, no `resolved_at` churn, no duplicate row), so re-merging produces exactly the same store state.
4. **Given** a malformed override file (header not exactly `ticker,venue`) **or** an unconfigured metadata DB (`DATABASE_URL` unset), **When** the merge is attempted, **Then** it degrades gracefully with a single clear warning (no traceback): a malformed header skips the merge without aborting an in-flight import (bars already imported are preserved); a missing file is a silent no-op. Rows with a blank ticker, blank venue, or a venue equal to `N/A` are skipped (an unfilled worklist line is not an error).

## Tasks / Subtasks

- [x] **Task 1: Config — override file path setting** (AC: #1) — *test FIRST (TDD)*
  - [x] Add `firstrate_venue_overrides_path: str = Field(default="venue_overrides.csv", description="Path to the git-tracked venue overrides CSV (header: ticker,venue)")` to `FirstRateSettings` in `src/config.py`. Repo-root-relative default so the file is discoverable without `.env` config. Keep `model_config` (`extra="ignore"`) unchanged. [Source: src/config.py:147-163]
  - [x] Add a unit assertion (in the existing config test module if present, else a tiny new one) that `FirstRateSettings().firstrate_venue_overrides_path == "venue_overrides.csv"`.

- [x] **Task 2: Pure loader + merge core** (AC: #1, #2, #3, #4) — *tests FIRST (TDD Red→Green)*
  - [x] Create `src/services/metadata/venue_overrides.py`:
    - `class VenueOverrideError(Exception)` — raised on a malformed header (the one hard-fail; everything else degrades to a skipped row).
    - `load_venue_overrides(path: Path) -> dict[str, str]` — **pure parse** (the only I/O is reading the file; no DB, no clock, no network). Behavior:
      - Missing file ⇒ return `{}` (overrides are optional; a bare-header or absent file must never break an import). [AC4]
      - Read with `encoding="utf-8-sig"` (strip BOM) and `newline=""`; use `csv.reader`.
      - Validate the header row: each cell `.strip().lower()` must equal exactly `["ticker", "venue"]`, else raise `VenueOverrideError` with a clear message. [AC1, AC4]
      - Per data row: `ticker = row[0].strip().upper()`, `venue = row[1].strip().upper()`. Skip a row when `ticker` is blank, `venue` is blank, or `venue == NA_SENTINEL` (import `NA_SENTINEL` from `src.models.instrument_metadata`) — a `debug`/`warning` log, not an error. Ragged/short rows are skipped defensively. [AC4]
      - Duplicate ticker ⇒ **last wins** (deterministic); optionally `warning`-log the shadowed value.
      - Return `{ticker: venue}`.
    - `class VenueOverrideMergeResult(BaseModel)` — `applied: int = 0`, `unchanged: int = 0`, `unmatched: list[str] = []`, with a `total` property (`applied + unchanged + len(unmatched)`). `unmatched` holds override tickers with no `instrument_metadata` row (surfaced, not fabricated).
    - `merge_venue_overrides(overrides: dict[str, str], repository: SyncInstrumentMetadataRepository, resolved_at: datetime) -> VenueOverrideMergeResult` — iterate `sorted(overrides.items())` (deterministic), call `repository.apply_venue_override(ticker, venue, resolved_at)` (returns `"applied" | "unchanged" | "unmatched"`), and tally into the result. **No clock inside** — `resolved_at` is passed by the caller (mirrors how `InstrumentMetadataService.resolve` stamps `resolved_at` at the service layer, not the repo). [Source: src/services/metadata/instrument_metadata_service.py:34-36,59-62]
  - [x] Keep every function `< 50` lines, the file `< 500`, fully return-type-annotated (F821 gate). No import from `firstrate` (keep `metadata` decoupled, mirroring `_parse_ipo_date`'s "kept local to avoid coupling metadata→firstrate" note). [Source: src/services/metadata/providers/fmp_provider.py:34-40]

- [x] **Task 3: Repository — `apply_venue_override`** (AC: #2, #3, #4) — *test FIRST (component TDD)*
  - [x] Add `apply_venue_override(self, ticker: str, venue: str, resolved_at: datetime) -> str` to `SyncInstrumentMetadataRepository` (`src/db/repositories/instrument_metadata_repository_sync.py`). Logic:
    - `row = self.get_by_ticker(ticker)`; if `None` ⇒ return `"unmatched"` (do NOT fabricate a half-empty row — overrides correct existing rows; a truly-absent ticker is surfaced by the merge result). [AC2, AC4]
    - **Idempotency guard:** if `row.venue == venue and row.resolution_status == ResolutionStatus.RESOLVED` ⇒ return `"unchanged"` (no mutation, no `resolved_at` write). [AC3]
    - Else set `row.venue = venue`, `row.resolution_status = ResolutionStatus.RESOLVED`, `row.resolved_at = resolved_at`, `self.session.flush()`, return `"applied"`. [AC2]
    - Wrap the query/flush in `try/except OperationalError` → `raise DatabaseConnectionError(...)` (mirror `upsert`/`list_by_status`). [Source: src/db/repositories/instrument_metadata_repository_sync.py:68-71,107-110]
  - [x] Return type is a plain `str` (values `"applied"/"unchanged"/"unmatched"`); annotate fully. Function `< 50` lines. Do **not** touch the async repo (CLI/import sync path only — no web consumer in this story).

- [x] **Task 4: CLI — wire the merge into the import orchestration** (AC: #1, #2, #3) — *test FIRST (TDD)*
  - [x] In `src/cli/commands/import_data.py`, add a thin helper `_apply_venue_overrides(session: Session, settings) -> None` that: resolves the path from `settings.firstrate.firstrate_venue_overrides_path`; `overrides = load_venue_overrides(Path(path))`; if `overrides` is empty ⇒ return silently (header-only/absent file); else `result = merge_venue_overrides(overrides, SyncInstrumentMetadataRepository(session), datetime.now(timezone.utc))` and print a single Rich summary line (`"Venue overrides: {applied} applied, {unchanged} unchanged, {len(unmatched)} unmatched"`, plus a yellow line listing unmatched tickers if any). Catch `VenueOverrideError` → print one yellow warning and return (skip the merge; bars already imported). [AC1, AC4]
  - [x] Call `_apply_venue_overrides(session, settings)` in `_run_import` **after** the per-timeframe import loop and **before** `session.commit()` (so the override upserts land in the same transaction as the import, and take precedence over the FMP-resolved rows written during the loop). Do not gate on asset class — the default header-only file makes it a no-op for non-ETF runs. [Source: src/cli/commands/import_data.py:344-361]
  - [x] Keep the command body within size limits; the merge/format logic lives in `venue_overrides.py`, the CLI helper stays thin.

- [x] **Task 5: CLI — `metadata apply-overrides` refresh subcommand** (AC: #1, #2, #3, #4) — *test FIRST (CliRunner TDD)*
  - [x] Add `@metadata.command("apply-overrides")` to `src/cli/commands/metadata.py` — the "metadata refresh" path (AC1) so the operator can fill the CSV from the Story 3.2 `metadata unresolved` worklist and apply it **without re-importing bars** (also the seam Story 3.5 relies on for "later receives a venue → becomes backtestable"). It:
    1. Resolves the path from `get_settings().firstrate.firstrate_venue_overrides_path`.
    2. `overrides = load_venue_overrides(Path(path))` inside a `try/except VenueOverrideError` → yellow warning + return (AC4).
    3. Opens `get_sync_session()` inside a `try/except (RuntimeError, DatabaseConnectionError, SQLAlchemyError)` → yellow "Metadata DB not available" warning + return (mirror `unresolved`, AC4).
    4. On empty `overrides`: print a clear "no overrides to apply" line and return (exit 0). Else `merge_venue_overrides(...)`, `session.commit()`, and render a summary (applied/unchanged/unmatched, with the unmatched tickers listed). [AC1, AC2, AC3]
  - [x] Reuse the module `console`; `escape()` any DB/CSV-sourced token printed via Rich markup (mirror the Story 3.2 escaping fix). Keep functions `< 50` lines. [Source: src/cli/commands/metadata.py:44-67]

- [x] **Task 6: Git-tracked override file** (AC: #1)
  - [x] Create `venue_overrides.csv` at the repo root containing exactly the header line `ticker,venue` (no data rows) so the file is git-tracked, present by default, and a no-op until the operator fills it. Add a short leading comment? **No** — the header must be the first row exactly (`ticker,venue`); keep the file header-only.

- [x] **Task 7: Tests** (AC: all)
  - [x] **Loader/merge unit (`tests/unit/services/metadata/test_venue_overrides.py`, `@pytest.mark.unit`):**
    - `load_venue_overrides`: valid file → normalized dict (ticker upper, venue upper, whitespace trimmed); BOM header tolerated; missing file → `{}`; malformed header → `VenueOverrideError`; blank-ticker / blank-venue / `N/A`-venue rows skipped; duplicate ticker last-wins; ragged short row skipped. Use `tmp_path`.
    - `merge_venue_overrides`: with a **fake repository** (records calls, returns scripted `"applied"/"unchanged"/"unmatched"`) assert the tally and that `sorted` order is used and `resolved_at` is threaded through (no clock inside). `VenueOverrideMergeResult.total` correctness.
  - [x] **Repo component (`tests/component/db/test_instrument_metadata_repository.py`):** seed a `VENUE_UNRESOLVED` row via `_make_metadata`; `apply_venue_override("SPY", "ARCA", ts)` → returns `"applied"`, row now `venue == "ARCA"`, `resolution_status == RESOLVED`, `resolved_at == ts`. Second identical call → `"unchanged"` and `resolved_at` **unchanged** (idempotency). Absent ticker → `"unmatched"`, no row created.
  - [x] **CLI unit (`tests/unit/cli/commands/test_metadata.py`):** `apply-overrides` — patch `get_settings`, `load_venue_overrides`, `get_sync_session`, and `merge_venue_overrides` (or the repo): populated → exit 0, summary output with counts; empty overrides → "no overrides" line, no merge; malformed file (`load_venue_overrides` raises `VenueOverrideError`) → exit 0, warning, no traceback; DB unconfigured (`get_sync_session` raises `RuntimeError`) → exit 0, warning.
  - [x] **Import-wiring unit:** a focused test of `_apply_venue_overrides` (patch `load_venue_overrides` + a fake session/repo) covering empty-file no-op, non-empty summary, and `VenueOverrideError` skip. Prefer this over exercising the full `import` command (keeps the test unit-tier, no Nautilus/catalog).

- [x] **Task 8: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 gate (new imports: `NA_SENTINEL`, `datetime`/`timezone`, `SyncInstrumentMetadataRepository`, `load_venue_overrides`/`merge_venue_overrides`).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate new functions and the `Literal`/`str` return of `apply_venue_override`.
  - [x] `make test-unit` green (loader/merge/CLI/import-wiring), `make test-component` green (repo round-trip). No integration/e2e (no engine, no C extensions).
  - [x] Size limits: files `< 500`, functions `< 50`, classes `< 100`, lines `≤ 100`.
  - [x] Smoke: `uv run python -m src.cli.main metadata apply-overrides --help` renders; `... metadata apply-overrides` runs against the header-only file → "no overrides to apply" (or a DB-unconfigured warning) with no traceback.

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Acceptance Auditor: **PASS** — all 4 ACs satisfied, every "do NOT do here" scope boundary honored. 6 patches applied and re-verified (1889 unit+component pass, ruff+mypy clean); 3 dismissed as documented design.

- [x] [Review][Patch] Malformed-header warning not escaped → Rich markup crash aborts the import (Blind Medium + Auditor). The `VenueOverrideError` message embeds `{header!r}`, whose list repr **always** contains `[...]`; `console.print` with markup enabled would raise `MarkupError`, and the exception propagated out of `_apply_venue_overrides` (called before `session.commit()`) → full-run rollback + traceback. Fixed: `escape(str(exc))` on the import-path warning (mirrors the `metadata.py` twin). Added a bracketed-message regression test. [src/cli/commands/import_data.py:_apply_venue_overrides]
- [x] [Review][Patch] Unreadable/undecodable override file escapes as a non-`VenueOverrideError` (Edge High + Blind Low) — a non-UTF-8/permission-denied file raised `UnicodeDecodeError`/`OSError`, which neither caller caught → CLI traceback and, in the import path, a swallowed exception that rolled back the whole run. Fixed: `load_venue_overrides` wraps the open/parse in `try/except (OSError, UnicodeDecodeError, csv.Error)` → `raise VenueOverrideError(...)`, so both callers degrade uniformly. Added a non-UTF-8 test. [src/services/metadata/venue_overrides.py:load_venue_overrides]
- [x] [Review][Patch] Empty (0-byte) file reported as malformed header, not a no-op (Edge Low) — `next(reader, None)` returned `None` → raised. Fixed: `header is None` now returns `{}` (matches the docstring's "absent/empty/header-only → no-op"). Added an empty-file test. [src/services/metadata/venue_overrides.py:load_venue_overrides]
- [x] [Review][Patch] Venue longer than the `String(20)` column unguarded (Edge Medium) — Postgres would `DataError` (uncaught → full rollback), SQLite silently truncates (env divergence). Fixed: skip a normalized venue longer than `_MAX_VENUE_LEN` (20) at load, alongside the blank/`N/A` skips. Added an oversize-venue test. [src/services/metadata/venue_overrides.py:_merge_row]
- [x] [Review][Patch] Import-path summary omitted the unmatched-tickers line (Auditor minor) — the CLI `apply-overrides` path listed them; the import path did not. Fixed: `_apply_venue_overrides` now prints the yellow unmatched list (escaped) for parity. Added an unmatched-line test. [src/cli/commands/import_data.py:_apply_venue_overrides]
- [x] [Review][Patch] Shadowed-duplicate log recorded the winning value, not the dropped one (Blind Low, cosmetic) — `kept=venue` logged the new value; the discarded earlier venue was never recorded. Fixed: `kept=venue, dropped=overrides[ticker]`. [src/services/metadata/venue_overrides.py:_merge_row]
- [x] [Review][Dismiss] No closed-set validation of the override venue (Edge Medium) — **deliberate, documented** ("No venue validation against a closed set"): overrides exist precisely to supply venues the conservative FMP map can't (ARCA/BATS/IEX…); an allowlist would defeat the purpose and re-introduce guessing. Only empty/`N/A`/oversize are rejected. No change.
- [x] [Review][Dismiss] DB error mid-merge in the import path is uncaught (Edge Low) — already handled safely by `_run_import`'s existing broad `except Exception` (clean "Fatal error" message + `session.rollback()`, no raw traceback); a dead DB genuinely fails the import. No change.
- [x] [Review][Dismiss] A benign extra header column rejects the whole file (Edge Low) — by AC1 design ("header exactly `ticker,venue`"). Strict header is intentional. No change.

## Dev Notes

### The core idea (why this story exists)
Story 3.1 made the FMP-resolved venue authoritative; the conservative venue map (ADR-6) deliberately routes ambiguous labels (notably `AMEX`) to `VENUE_UNRESOLVED` rather than guess. Story 3.2 surfaced those rows as a worklist. Story 3.3 closes the loop: the operator supplies the *correct* venue in a git-tracked `venue_overrides.csv`, and the system merges it into `instrument_metadata` with **precedence** — the override wins over absent/ambiguous provider data and flips the row to `RESOLVED`. This is the only manual write-path into the metadata store (no web write-path — preserves the Phase-1 read-only-explorer stance). Story 3.4 then gates completion on 0 `VENUE_UNRESOLVED`; this story is how the operator drives that count to zero without guessing. [Source: epics.md Epic 3 intro + Story 3.3; architecture.md "no web write-path for venue overrides"]

### Precedence & idempotency — the exact semantics
- **Precedence (AC2):** the override is operator-asserted truth. The merge sets `venue` and forces `resolution_status = RESOLVED` regardless of what FMP wrote. Descriptive fields (`company_name`, `sector`, …) are **left untouched** — a `RESOLVED` row with `N/A` descriptive gaps is valid (descriptive gaps are an orthogonal dimension in `ResolutionSummary`, not a venue-coverage failure). So even a fully-degraded "unknown to FMP" row (`asset_type=None`, all descriptive `N/A`) becomes backtestable once its venue is asserted. [Source: src/models/instrument_metadata.py:105-152 (three independent metrics)]
- **Idempotency (AC3):** achieved by a **skip-when-already-at-target** guard in `apply_venue_override` — a row already `(venue == override, status == RESOLVED)` is not written at all, so `resolved_at` does not churn and a second run is a pure no-op. This is why the clock is passed *in* (caller stamps `resolved_at` once per merge) rather than stamped per-row inside the repo: unchanged rows never touch it.

### No venue validation against a closed set (deliberate)
The whole point of overrides is to supply venues the conservative FMP map could **not** (e.g. `ARCA`, `BATS`, `IEX`, `NYSE-American`). Validating the override venue against a known set would defeat the purpose and re-introduce the "guessing" the map avoids. So the loader normalizes case/whitespace and rejects only the empty string and the `N/A` sentinel (a venue is a real code or nothing — the domain model's `venue_never_na_sentinel` invariant, enforced here at the CSV boundary since the merge writes the ORM directly). [Source: src/models/instrument_metadata.py:96-102; src/services/metadata/venue_map.py:1-14]

### The seam (data + control flow)
```
venue_overrides.csv (git-tracked, header ticker,venue, path via FirstRateSettings)
        │  load_venue_overrides(path)  ← pure parse → {TICKER: VENUE}, skips blanks/N-A, header-validated
        ▼
merge_venue_overrides(overrides, repo, resolved_at)   ← orchestration step (CLI), NOT FMPClient
        │  per ticker: repo.apply_venue_override(ticker, venue, resolved_at)
        │     absent row → "unmatched"   (surfaced, not fabricated)
        │     already (venue, RESOLVED) → "unchanged"   (idempotent no-write)
        │     else set venue + RESOLVED + resolved_at → "applied"
        ▼
instrument_metadata rows flip VENUE_UNRESOLVED → RESOLVED  (committed in the import txn / refresh txn)
```
Two entry points, one core: the ETF `import` command (merge after the timeframe loop, before commit) and `metadata apply-overrides` (refresh without re-importing bars). Both call the same `merge_venue_overrides`. [Source: src/cli/commands/import_data.py:344-361; src/cli/commands/metadata.py]

### Where the merge lives (AC1 — "not inside FMPClient")
The merge is import-orchestration, not provider logic. It lives in the CLI layer (`import_data.py` `_run_import`, and the `metadata` group), reading the store the FMP resolution already populated during the import loop. `FMPClient`, `FMPMetadataProvider`, and `InstrumentMetadataService` are **not** touched — the override is a post-resolution correction over the persisted rows, so it must run after the provider has written its (possibly `VENUE_UNRESOLVED`) rows and take precedence over them. [Source: epics.md Story 3.3 AC1]

### Scope boundaries (do NOT do here)
- **No coverage count / completeness gate** — that is Story 3.4 (`metadata coverage` + the 0-unresolved gate). This story flips rows to `RESOLVED`; it does not compute a pass/fail gate.
- **No exclude/flag/keep-bars** — Story 3.5. (This merge is the mechanism by which a 3.5-excluded ticker later "receives a venue" and transitions to backtestable, but the exclusion/flag logic itself is 3.5.)
- **No new DB columns / migrations** — the merge writes existing `instrument_metadata` columns (`venue`, `resolution_status`, `resolved_at`).
- **No web/API endpoint, no async repo** — CLI sync path only (preserves the read-only-explorer stance).
- **No changes to the FMP provider, venue map, or resolution service** — the override is a store correction layered on top of their persisted output.
- **No row fabrication** — an override for a ticker with no metadata row is reported `unmatched`, never invented (a metadata row needs a provider + descriptive context the CSV can't supply).

### Previous-story intelligence
- **Story 3.1 (done):** `VENUE_UNRESOLVED` ⇔ `venue is None`; the import pipeline is the single writer of `catalog_instruments`; `instrument_metadata` is the source of truth for resolved metadata. The override merge writes `instrument_metadata` (venue + status), and Story 3.1's qualification then reads the now-`RESOLVED` venue to compute `nautilus_id`.
- **Story 3.2 (done):** `list_by_status(VENUE_UNRESOLVED)` produces the worklist whose `ticker` column keys this CSV; the CLI DB-unconfigured/graceful-degradation idiom (`try/except (RuntimeError, DatabaseConnectionError, SQLAlchemyError)` → one yellow line) and Rich `escape()` of DB/CSV tokens are established there — mirror both. [Source: 3-2 story; src/cli/commands/metadata.py:39-48]
- **Import wiring (Story 2.4/2.7):** the ETF import already opens a sync session, wires the metadata resolver, runs the timeframe loop, and commits once at the end — the override merge slots in right before that single commit so it is atomic with the import. [Source: src/cli/commands/import_data.py:284-361]

### Testing standards
- **Unit tier** for the pure loader, the merge core (fake repo), the CLI commands (CliRunner with settings/session/loader patched — no DB, no Nautilus), and the `_apply_venue_overrides` import helper. **Component tier** for the real-repo `apply_venue_override` round-trip over the in-memory SQLite fixture (applied → unchanged idempotency → unmatched). No integration/e2e. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — Red first for `load_venue_overrides`, `merge_venue_overrides`, `apply_venue_override`, and both CLI paths. [Source: development-principles.md]
- Build ORM rows via the `_make_metadata` kwargs idiom already in the component test; keep markers consistent with siblings. [Source: tests/component/db/test_instrument_metadata_repository.py:48-70]

### Git intelligence
Scope precedent: Epic-3 stories committed as `feat(epic3): story 3-N — …`. Subject here: `feat(epic3): story 3-3 — venue_overrides.csv merge with precedence`. No AI/claude references. Stage then commit in **separate** Bash calls (the bash-guard commit gate rejects a `git add && git commit` one-liner). [Source: CLAUDE.md Commit Format + Editing with Auto-Linter]

### Project Structure Notes
- **New:** `src/services/metadata/venue_overrides.py` (`VenueOverrideError`, `load_venue_overrides`, `VenueOverrideMergeResult`, `merge_venue_overrides`); `venue_overrides.csv` (repo root, header-only, git-tracked).
- **Modified:** `src/config.py` (`firstrate_venue_overrides_path`); `src/db/repositories/instrument_metadata_repository_sync.py` (`apply_venue_override`); `src/cli/commands/import_data.py` (`_apply_venue_overrides` + wiring before commit); `src/cli/commands/metadata.py` (`apply-overrides` subcommand).
- **New tests:** `tests/unit/services/metadata/test_venue_overrides.py`.
- **Modified tests:** `tests/unit/cli/commands/test_metadata.py` (apply-overrides), `tests/component/db/test_instrument_metadata_repository.py` (apply_venue_override), config test (path default), import-wiring unit test (co-locate with existing import tests).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.3] — story statement + 3 acceptance criteria; FR22, FR23
- [Source: src/config.py:147-163] — `FirstRateSettings` (where the override path setting belongs)
- [Source: src/db/repositories/instrument_metadata_repository_sync.py:32-110] — `upsert`/`get_by_ticker`/`list_by_status` idioms to mirror for `apply_venue_override`
- [Source: src/services/metadata/instrument_metadata_service.py:34-62] — service-layer `resolved_at` stamping (clock lives above the repo)
- [Source: src/models/instrument_metadata.py:36-102] — `ResolutionStatus`, `NA_SENTINEL`, `venue_never_na_sentinel` invariant
- [Source: src/cli/commands/import_data.py:284-361] — import orchestration seam (wire the merge before the single commit)
- [Source: src/cli/commands/metadata.py:26-67] — `metadata` group + graceful-degradation + Rich-escape idiom to extend with `apply-overrides`
- [Source: tests/component/db/test_instrument_metadata_repository.py:36-70] — `sync_session` fixture + `_make_metadata` idiom

### Open questions (non-blocking — proceed with the documented default)
1. **Unmatched-override handling.** Default: report override tickers with no metadata row as `unmatched` (surfaced in the summary), never fabricate a row. Fabricating a minimal row would need a provider + descriptive context the CSV can't supply and would pollute `descriptive_gaps`. Flag if the team wants unmatched overrides to seed placeholder rows.
2. **Override-file location.** Default: repo-root `venue_overrides.csv` (path via `FirstRateSettings`). A per-catalog override file (e.g. `<catalog>/venue_overrides.csv`) could scope corrections per import, but the metadata store is global (keyed by ticker, not catalog), so a single global file matches the store's grain. Flag if per-catalog scoping is wanted.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/services/metadata/test_venue_overrides.py tests/unit/cli/commands/test_metadata.py tests/unit/cli/commands/test_import_data.py tests/component/db/test_instrument_metadata_repository.py` — 98 passed.
- `uv run pytest tests/unit tests/component` — 1885 passed, 16 skipped (pre-existing report-command skips), no regressions.
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found in 348 source files.

### Completion Notes List

- **AC1** — `load_venue_overrides` (pure CSV parse, header validated exactly `ticker,venue`, path from `FirstRateSettings.firstrate_venue_overrides_path`) + `merge_venue_overrides` run in the import-orchestration step: `import_data._apply_venue_overrides` (after the timeframe loop, before the single commit) and `metadata apply-overrides` (refresh without re-importing). Neither touches `FMPClient`/the provider/the resolution service.
- **AC2** — `SyncInstrumentMetadataRepository.apply_venue_override` sets `venue` + forces `resolution_status = RESOLVED` on the existing row (precedence over absent/ambiguous FMP data); no provider call. Descriptive fields untouched (RESOLVED-with-gaps is valid).
- **AC3** — Idempotent via a skip-when-already-at-target guard: a row already `(venue == override, RESOLVED)` returns `"unchanged"` with no write and no `resolved_at` churn. Clock is passed in by the caller (stamped once per merge), so re-runs are pure no-ops.
- **AC4** — Malformed header raises `VenueOverrideError` → single yellow warning, merge skipped, in-flight import preserved. Missing file → silent `{}`. Blank-ticker / blank-venue / `N/A`-venue rows skipped. DB-unconfigured (`RuntimeError`/`DatabaseConnectionError`/`SQLAlchemyError`) → one yellow "Metadata DB not available" line, no traceback (mirrors Story 3.2).
- **Design** — override venue is operator-asserted truth (no closed-set validation — that would re-introduce guessing); unmatched overrides are surfaced, never fabricated into half-empty rows. `venue_overrides.csv` created header-only at repo root (git-tracked, default no-op).
- **Scope respected** — no coverage/gate (3.4), no exclude/flag (3.5), no new columns/migrations, no async/web path, no changes to provider/venue-map/resolution service.

### Change Log

| Date | Change |
|---|---|
| 2026-07-13 | Story 3.3 drafted (SM). Status → ready-for-dev. |
| 2026-07-13 | Implemented pure loader + merge core, `apply_venue_override` repo method, config path, import-orchestration wiring + `metadata apply-overrides` refresh subcommand, git-tracked `venue_overrides.csv` (TDD). Gates green (ruff, mypy, 1885 pass). Status → review. |
| 2026-07-13 | Code review (3 adversarial layers; Auditor PASS on all 4 ACs). Applied 6 patches (import-path `escape()` markup-crash fix, file-read error → `VenueOverrideError` uniform degradation, empty-file no-op, oversize-venue skip, import-path unmatched line, shadowed-dup log); dismissed 3 as documented design. 1889 unit+component pass, ruff+mypy clean. Status → done. |

### File List

- `src/config.py` (modified — `firstrate_venue_overrides_path`)
- `src/services/metadata/venue_overrides.py` (new — `VenueOverrideError`, `load_venue_overrides`, `VenueOverrideMergeResult`, `merge_venue_overrides`)
- `src/db/repositories/instrument_metadata_repository_sync.py` (modified — `apply_venue_override`, `datetime` import)
- `src/cli/commands/import_data.py` (modified — `_apply_venue_overrides` + wiring before commit, `Settings` type import)
- `src/cli/commands/metadata.py` (modified — `apply-overrides` subcommand + `_render_override_summary`)
- `venue_overrides.csv` (new — repo-root, header-only, git-tracked)
- `tests/unit/services/metadata/test_venue_overrides.py` (new — loader + merge)
- `tests/unit/cli/commands/test_metadata.py` (modified — `apply-overrides` CLI)
- `tests/unit/cli/commands/test_import_data.py` (modified — `_apply_venue_overrides` wiring)
- `tests/component/db/test_instrument_metadata_repository.py` (modified — `apply_venue_override` round-trip)
