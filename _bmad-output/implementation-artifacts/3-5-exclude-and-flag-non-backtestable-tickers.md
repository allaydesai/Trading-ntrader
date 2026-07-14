# Story 3.5: Exclude & Flag Non-Backtestable Tickers

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want tickers without a resolved venue excluded from the backtestable universe and flagged non-backtestable, while their imported bars are kept,
so that an unresolved-venue ticker never silently enters a backtest and its data is never lost or orphaned.

## Acceptance Criteria

1. **Given** a ticker with `resolution_status = VENUE_UNRESOLVED`, **When** the backtestable universe is enumerated, **Then** the ticker is **excluded** from it and **flagged** non-backtestable — the enumeration is a live derivation over `instrument_metadata.resolution_status` (backtestable ⟺ `RESOLVED`), so no venue is guessed to admit it and a stale/provisional `catalog_instruments.nautilus_id` never sneaks it back in.
2. **Given** that same excluded ticker, **When** the catalog is inspected, **Then** its imported Parquet bars are still present (excluded ≠ dropped): this story never deletes a partition, and the `catalog_instruments` row (with its `bar_count_*`) persists, so `metadata backtestable` reports the retained bars as evidence. The Story-3.1 identity-nulling is **intentionally retained** — nulling `nautilus_id` is the exclusion mechanism the backtest path honors (`backtest_loader` gates on `nautilus_id`); the bars stay physically on disk and become reachable again when the venue resolves. (See Review Findings: keeping a provisional id to make bars reachable was tried and reverted because it re-admits guessed-venue tickers to backtests — worse than the benign unreachable-by-id state.)
3. **Given** that ticker later receives a venue (via the Story 3.3 `venue_overrides.csv` merge, which flips its row to `RESOLVED`), **When** the backtestable universe is re-enumerated, **Then** it transitions to backtestable **without re-importing its bars** — a consequence of the enumeration being derived live from `resolution_status` (the override alone moves it), and its bar counts / date ranges are unchanged.
4. **Given** the metadata DB is unconfigured (`DATABASE_URL` unset) or unreachable, **When** the `metadata backtestable` report runs, **Then** it degrades gracefully with a single clear warning (no traceback), mirroring the existing `metadata unresolved` / `metadata coverage` DB-unconfigured handling.

## Tasks / Subtasks

- [x] **Task 1: Pure backtestable classifier + flagged-ticker record** (AC: #1, #2) — *write the test in `tests/unit/services/metadata/test_backtestable.py` FIRST (TDD Red→Green)*
  - [x] Add `src/services/metadata/backtestable.py` — domain-facing, pure, beside `coverage_report.py` / `unresolved_report.py` (no Click, no session, no clock):
    - `is_backtestable(status: ResolutionStatus) -> bool` = `status == ResolutionStatus.RESOLVED`. This is the ONLY backtestable predicate: `VENUE_UNRESOLVED` (the gate blocker) and `UNRESOLVED` (never attempted) are both non-backtestable. Keying the universe on the *venue verdict* — not on `catalog_instruments.nautilus_id` — is what makes AC1 robust (a kept provisional id can't re-admit an unresolved ticker) and AC3 free (an override flip to `RESOLVED` moves the ticker with no re-qualify, no re-import). [Source: src/models/instrument_metadata.py:36-46; epics.md Story 3.5 AC1/AC3; src/services/metadata/coverage_report.py:60-63 (`is_complete` keys on the same enum)]
    - `total_bars(instrument: CatalogInstrument) -> int` = sum of the five `bar_count_*` fields (`daily + hourly + minute + 5min + 30min`). Used to *evidence* that an excluded ticker's bars are retained. [Source: src/db/models/catalog_instrument.py:92-106]
    - A frozen dataclass `NonBacktestableTicker` with `ticker: str` and `bars_retained: int`, plus a `bars_kept: bool` property = `bars_retained > 0`. This is the flag record the CLI renders (a ticker with no `catalog_instruments` row / no bars reports `bars_retained == 0` — fresh unresolved, nothing to lose). [Source: src/services/metadata/coverage_report.py:14-41 (frozen-dataclass idiom)]
  - [x] Import `from src.models.instrument_metadata import ResolutionStatus` and `from src.db.models.catalog_instrument import CatalogInstrument`. Keep the module `< 100` lines, fully return-type-annotated (F821 gate). No new settings, no I/O.

- [x] **Task 2: ~~Orphan-safety fix in `sync_qualification`~~ — TRIED & REVERTED (AC: #2)**
  - [x] **This task was implemented, then reverted during code review — see Review Findings.** The plan was to keep the provisional `nautilus_id` when `total_bars(instrument) > 0` (venue unresolved but bars on disk) so the bars stay reachable, per the 3.1 deferred-work note. Code review (Edge Case Hunter, confirmed against `backtest_loader.py:136`) found that `backtest_loader.load_from_catalog` — and `api/rest/explorer.py`, `api/stats_service.py`, `api/chart_bars.py` — gate backtestability **solely on `catalog_instruments.nautilus_id`**, never on `resolution_status`. Keeping a provisional id therefore lets an unresolved-venue ticker **run a backtest under a guessed venue**, violating AC1 ("never silently enter a backtest") and ADR-6 — strictly worse than the orphan it fixes.
  - [x] **Resolution:** `sync_qualification` is left at its Story 3.1 behavior (null `nautilus_id`/`exchange` on `VENUE_UNRESOLVED`). Nulling is the exclusion mechanism the real backtest path honors; the Parquet is never deleted, so AC2 ("bars present, not dropped") holds — bars are physically retained, just unreachable-by-id until the venue resolves. The true "keep bars reachable AND excluded" fix needs a consumer-honored non-backtestable signal threaded through the loader/explorer/stats/chart — re-deferred to Epic 5 (backtest integration). [Source: deferred-work.md updated 2026-07-13; src/services/firstrate/backtest_loader.py:136-147]

- [x] **Task 3: CLI — `metadata backtestable` report** (AC: #1, #2, #4) — *tests FIRST (TDD, CliRunner)*
  - [x] Add `@metadata.command("backtestable")` with `--catalog` (`click.option`, default `None` → resolved to `get_settings().firstrate.firstrate_catalog_name`) to `src/cli/commands/metadata.py`. Docstring: enumerate the backtestable universe (RESOLVED-venue tickers) and flag the non-backtestable ones (VENUE_UNRESOLVED), confirming their bars are retained (excluded ≠ dropped). [Source: src/cli/commands/metadata.py:82-112 (`apply-overrides` catalog/settings idiom); src/cli/commands/metadata.py:126-153 (`coverage` option idiom)]
  - [x] Command body:
    1. `catalog_name = catalog or get_settings().firstrate.firstrate_catalog_name`.
    2. Open a sync session inside a `try/except (RuntimeError, DatabaseConnectionError, SQLAlchemyError)` that prints a single yellow `Metadata DB not available — {escape(exc)}` line and returns (exit 0) — mirror `coverage`/`unresolved` (the query runs inside the `with`, since the lazy session surfaces failures at execute time). (AC4)
    3. `meta_repo = SyncInstrumentMetadataRepository(session)`; `cat_repo = SyncCatalogInstrumentRepository(session)`.
    4. `backtestable_count = meta_repo.count_by_status().get(ResolutionStatus.RESOLVED, 0)` (indexed grouped count — the backtestable universe size). `unresolved_rows = meta_repo.list_by_status(ResolutionStatus.VENUE_UNRESOLVED)` (the flagged set, ordered by ticker).
    5. Build `flagged: list[NonBacktestableTicker]`: for each unresolved row, `inst = cat_repo.get_by_ticker(catalog_name, row.ticker)`; `bars = total_bars(inst) if inst is not None else 0`; append `NonBacktestableTicker(row.ticker, bars)`.
  - [x] Delegate rendering to a pure `_render_backtestable(catalog_name: str, backtestable_count: int, flagged: list[NonBacktestableTicker]) -> None` (no exit logic, `capsys`-testable):
    - When `flagged` is empty: a green `✓ All venue-resolved — backtestable universe = {backtestable_count} ticker(s), 0 non-backtestable.` line.
    - Otherwise a Rich `Table` (title `Non-backtestable tickers (venue unresolved)`, columns **Ticker**, **Bars retained**) — one escaped row per flagged ticker showing `bars_retained`; then a summary: `Backtestable: {backtestable_count} · Non-backtestable: {len(flagged)} (bars retained, not dropped)`, and an explicit `Bars are kept on disk — resolve each venue in venue_overrides.csv (Story 3.3) to make it backtestable.` hint. Escape all DB-sourced strings (`escape(row.ticker)`) as `_render_unresolved` does. [Source: src/cli/commands/metadata.py:64-79]
  - [x] Add imports: `SyncCatalogInstrumentRepository`, `NonBacktestableTicker`, `total_bars` (mind the F401/F821 gate). Keep every function `< 50` lines, the file `< 500`. No `sys.exit` here (report-only, always exit 0). No network/provider call. [Source: CLAUDE.md Foundational Rules]

- [x] **Task 4: Unit tests** (AC: #1, #2, #4)
  - [x] **Classifier (`tests/unit/services/metadata/test_backtestable.py`, `@pytest.mark.unit`):**
    - `is_backtestable(ResolutionStatus.RESOLVED) is True`; `is_backtestable(ResolutionStatus.VENUE_UNRESOLVED) is False`; `is_backtestable(ResolutionStatus.UNRESOLVED) is False`.
    - `total_bars` on a `CatalogInstrument` with e.g. `bar_count_daily=2, bar_count_minute=3` (others 0) → `5`; all-zero → `0`.
    - `NonBacktestableTicker("ZZZ", 10).bars_kept is True`; `NonBacktestableTicker("QQQ", 0).bars_kept is False`; dataclass is frozen (immutability).
  - [x] **CLI (`tests/unit/cli/commands/test_metadata.py`, `CliRunner`, `@pytest.mark.unit`):** patch `get_sync_session`, `SyncInstrumentMetadataRepository`, and `SyncCatalogInstrumentRepository` (via a `_patch_both_repos` helper). Stub `count_by_status.return_value = {RESOLVED: 3, VENUE_UNRESOLVED: 1}`, `list_by_status.return_value = [_degraded_row("ZZZ")]`, and `cat_repo.get_by_ticker.return_value` = a `CatalogInstrument` with `bar_count_minute=100`.
    - **Flagged case:** `metadata backtestable` → exit 0, output contains `ZZZ`, `100`, `Non-backtestable (venue unresolved): 1`, `bars retained`, and the venue_overrides hint.
    - **All-clear:** `list_by_status.return_value = []`, only RESOLVED counts → exit 0, output contains `0 non-backtestable` and the green all-clear.
    - **Never-attempted surfaced:** counts `{RESOLVED: 2, UNRESOLVED: 3}`, no flagged → exit 0, output does NOT claim `All venue-resolved`, and shows `Not yet attempted (UNRESOLVED): 3` (transparency — Review Finding).
    - **Empty DB:** `count_by_status.return_value = {}` → exit 0, output contains `No metadata rows yet` (not a green success banner).
    - **Ticker with no catalog row:** `cat_repo.get_by_ticker.return_value = None` → exit 0, flagged ticker rendered with `0` bars retained (no crash). (AC1 — still flagged/excluded even when it has no bars.)
    - **DB unconfigured:** `get_sync_session().__enter__` raises `RuntimeError(...)` → exit 0, `result.exception is None`, output contains `Metadata DB not available` (AC4).
    - **`--catalog` override:** `["backtestable", "--catalog", "custom-cat"]` → asserts `cat_repo.get_by_ticker` called with `"custom-cat"`.
  - [x] **Render helper (`TestRenderBacktestable`, `capsys`, no CliRunner):** direct `_render_backtestable("firstrate-etf", 3, 0, [NonBacktestableTicker("ZZZ", 100)])` asserts the ticker, bar count, `Non-backtestable (venue unresolved): 1`; the all-clear, never-attempted, and empty cases assert their respective banners.

- [x] **Task 5: Component test — AC3 transition** (AC: #3)
  - [x] In `tests/component/db/test_instrument_metadata_repository.py` add `test_venue_override_transitions_ticker_to_backtestable` (AC3): seed one `RESOLVED` row + one `VENUE_UNRESOLVED` row (via `_make_metadata`/`upsert`). Assert `list_by_status(RESOLVED)` = the resolved ticker only and the unresolved ticker is `is_backtestable(...) is False`. Then `apply_venue_override(unresolved_ticker, "ARCA", <ts>)`; assert it now appears in `list_by_status(RESOLVED)` and `is_backtestable` of its status is `True` — the transition happens with **no** bar/import operation (pure metadata flip). [Source: tests/component/db/test_instrument_metadata_repository.py (`apply_venue_override` + `list_by_status` precedents from Stories 3.2/3.3)]

- [x] **Task 6: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new `backtestable` module imports; `SyncCatalogInstrumentRepository`, `NonBacktestableTicker`, `total_bars` in the CLI; `total_bars` in the mapper).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate the new function/dataclass/helper.
  - [x] `make test-unit` green (classifier + mapper orphan guard + CLI/render deltas, no regressions). `make test-component` green for the new mapper + transition tests.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.
  - [x] Smoke: covered by `CliRunner` tests (help/registration + report output + DB-unconfigured warning); the harness permission mode blocks the live `python -m src.cli.main` subprocess, so the identical command paths are exercised via `CliRunner.invoke`.

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter). One High finding reverted a whole task; two Med transparency findings patched; scope findings documented.

- [x] [Review][Revert] **Keep-bars `sync_qualification` fix re-admits guessed-venue tickers to backtests (Edge Case, High).** The implemented "keep the provisional `nautilus_id` when bars are present" change left a `VENUE_UNRESOLVED` ticker with a live `nautilus_id`. `backtest_loader.load_from_catalog` (verified at `backtest_loader.py:136-147`) and the explorer/stats/chart API paths gate backtestability **only** on `nautilus_id`, never on `resolution_status` — so the "excluded" ticker would load bars and run a backtest under its guessed/provisional venue, violating AC1 and ADR-6. **Reverted** the entire Task-2 mapper change (source + unit + component tests); `sync_qualification` is back to Story 3.1 behavior (null the id — the correct exclusion). AC2 still holds (Parquet never deleted; retained bars reported). The "reachable AND excluded" reconciliation is re-deferred to Epic 5 with a consumer-honored flag. Updated `deferred-work.md`.
- [x] [Review][Patch] **`UNRESOLVED` (never-attempted) rows reported as neither backtestable nor flagged → misleading green "0 non-backtestable" (Edge Case, Med).** The report only counted `RESOLVED` and listed `VENUE_UNRESOLVED`; a store with `UNRESOLVED` rows and no `VENUE_UNRESOLVED` printed the green "✓ All venue-resolved" banner while a third non-backtestable bucket was silently omitted. Fixed: the command now reads the never-attempted count and `_render_backtestable` surfaces `Not yet attempted (UNRESOLVED): N`, withholds the green all-clear unless both buckets are empty, and prints "No metadata rows yet" on an empty store (also addresses the empty-DB green-banner finding). Added render + CLI tests.
- [x] [Review][Patch/Doc] **Catalog-scope mismatch: global metadata flag set vs single-catalog bar evidence (Blind + Edge Case, Low).** `instrument_metadata` is catalog-global (keyed by ticker) but bar evidence is read from one `catalog_name`; a flagged ticker whose bars live in another catalog reads "0 bars retained". Clarified by labelling the bars column `Bars retained ({catalog})` so the operator sees the scope; the multi-catalog sweep remains Open Question #2 (documented default).
- [x] [Review][Ack] **N+1 `get_by_ticker` per flagged ticker (Blind, Low).** O(flagged) lookups. Acceptable — the `VENUE_UNRESOLVED` set is the small, shrinking worklist (the whole point of Epic 3 is to drive it to 0); a batched fetch is a premature optimization for a report. No change.
- [x] [Review][Defer] **Re-resolving to a *different* venue than the bars were written under orphans the partition (Edge Case, High-conditional).** A cross-run ticker's bars sit under partition `SPY.<provisional>`; resolving to a different venue builds a `bar_type` under `SPY.<new>` → loader finds no bars. Pre-existing partition-path coupling, same root as the reverted item; the AC3 "no re-import" promise holds only when the resolved venue matches the write-time venue. Re-deferred to Epic 5 (backtest integration) alongside the consumer-honored flag. Recorded in `deferred-work.md`.

## Dev Notes

### The core idea (why this story exists)
Epic 3 gates ETF-universe completion on 100% venue coverage. Story 3.1 qualified resolved tickers; 3.2 gave the worklist; 3.3 let the operator fix venues; 3.4 counts + gates. Story 3.5 is the **enforcement of the exclude/flag/keep-bars contract**: an unresolved-venue ticker is kept **out** of the backtestable universe and flagged, but its bars are **never** dropped or orphaned — and the moment its venue is resolved it flows back in with no re-import. This is the "no unresolved ticker silently enters a backtest, no data lost" guarantee. [Source: epics.md Story 3.5; epics.md:142 "Unresolved tickers keep their bars but are flagged non-backtestable"]

### Design decision — the backtestable universe is a live derivation over `resolution_status`
Backtestable ⟺ `resolution_status == RESOLVED`. The universe is **derived** from the current metadata verdict, not stored as a boolean and not read off `catalog_instruments.nautilus_id`. Three payoffs:
- **AC1 robustness** — the enumeration/report keys on the venue verdict, and the real backtest path independently excludes the ticker because Story 3.1 nulls its `nautilus_id` (which `backtest_loader` gates on). Both agree: a `VENUE_UNRESOLVED` ticker is out.
- **AC3 for free** — the Story 3.3 override flips the row to `RESOLVED`; the next enumeration derives it as backtestable. No re-qualify pass, no re-import, no second write path to keep in sync.
- **No migration** — `resolution_status` + its index (`ix_instrument_metadata_resolution_status`) already exist; `list_by_status(RESOLVED)` **is** the enumeration and `list_by_status(VENUE_UNRESOLVED)` **is** the flagged set. [Source: src/db/models/instrument_metadata.py:71; src/db/repositories/instrument_metadata_repository_sync.py:87-135]

### AC2 (keep bars) — nulling the identity is correct; keeping it was tried & reverted
Story 3.1 nulls `nautilus_id` on a `VENUE_UNRESOLVED` ticker. The 3.1 deferred-work note asked 3.5 to instead **keep** the provisional id when the row still holds bars, so those bars stay reachable (avoid the cross-run "orphan"). That was implemented, then **reverted during code review**: `backtest_loader.load_from_catalog` (and `api/rest/explorer.py`, `api/stats_service.py`, `api/chart_bars.py`) decide backtestability **solely from `catalog_instruments.nautilus_id`** — they never read `resolution_status`. So a kept provisional id lets an unresolved-venue ticker **run a backtest under a guessed venue** (AC1 + ADR-6 violation), strictly worse than the benign "unreachable-by-id" orphan. Final decision: leave `sync_qualification` at its 3.1 behavior. AC2 is satisfied because the Parquet is never deleted and `catalog_instruments.bar_count_*` is untouched — `metadata backtestable` reports the retained bars — the ticker is just correctly unreachable-by-id (i.e. excluded) until its venue resolves. The real "reachable AND excluded" fix needs a consumer-honored non-backtestable signal (loader/explorer/stats/chart) — Epic 5 scope. [Source: deferred-work.md updated 2026-07-13; src/services/firstrate/backtest_loader.py:136-147]

### The seam (data + control flow)
```
instrument_metadata.resolution_status            catalog_instruments (bars + identity)
        │  list_by_status(RESOLVED)          ← backtestable universe (AC1)
        │  list_by_status(VENUE_UNRESOLVED)  ← flagged non-backtestable set
        │                                         │  get_by_ticker → total_bars (evidence bars kept, AC2)
        ▼                                         ▼
  metadata backtestable [--catalog]  (CLI, this story)  → Rich table + summary (report-only, exit 0)

  sync_qualification (import path, unchanged from 3.1): VENUE_UNRESOLVED ⇒ nautilus_id NULL (exclusion the loader honors)
  apply_venue_override (Story 3.3): VENUE_UNRESOLVED → RESOLVED ⇒ next enumeration = backtestable (AC3, no re-import)
```
Read-only over the local cache. No network, no provider call, no write. [Source: architecture.md two-table boundary; backtest_loader.py:136-147]

### Scope boundaries (do NOT do here)
- **No new DB column / migration / stored `backtestable` flag** — derivation over the existing `resolution_status` + index. Do not add a `non_backtestable` column to `catalog_instruments`.
- **No venue inference/guessing** — a `VENUE_UNRESOLVED` ticker stays excluded; never fabricate a venue to admit it (ADR-6).
- **No parquet deletion / no re-import** — this story keeps bars; it never drops a partition and never re-imports on transition.
- **No override load/merge** — that is Story 3.3 (`apply-overrides`, done). This story reads the post-merge state and proves the transition.
- **No async/web/explorer surface** — CLI sync path only. The explorer's ETF non-backtestable rendering is Epic 4 (4.4 venue-state distinct from N/A); do not touch UI here.
- **No changes to the FMP provider, venue map, resolution service, coverage gate (3.4), or the qualified-id math (3.1's `qualified_instrument_id`).** This story adds the classifier, the report, and the orphan-safety branch only.

### Design decision — classifier in `services/metadata`, report in the CLI
`is_backtestable` / `total_bars` / `NonBacktestableTicker` (pure, no I/O) live in `src/services/metadata/backtestable.py` beside `coverage_report.py`, unit-testable without Click/DB. The CLI owns only session wiring + rendering. Matches the 3.2/3.4 precedent (pure logic → unit tier; CLI thin). [Source: CLAUDE.md Decision Heuristics; src/services/metadata/coverage_report.py; src/services/metadata/unresolved_report.py]

### Previous-story intelligence
- **Story 3.1 (done)** built `sync_qualification` (nulls the id when unresolved) and deferred a keep-bars reconciliation to this story; that was attempted (Task 2) and **reverted** — nulling is the correct exclusion the loader honors, so the reconciliation is re-deferred to Epic 5. [Source: deferred-work.md updated 2026-07-13; Review Findings]
- **Story 3.3 (done)** `apply_venue_override` flips a row to `RESOLVED` (venue set) idempotently and touches only `instrument_metadata` — so AC3's transition needs no code beyond re-enumerating. [Source: src/db/repositories/instrument_metadata_repository_sync.py:137-171]
- **Story 3.4 (done)** added `count_by_status` (indexed grouped count) — reused here for the backtestable (`RESOLVED`) count — and the `metadata` group's graceful-degradation idiom (`(RuntimeError, DatabaseConnectionError, SQLAlchemyError)` → yellow warning). [Source: src/db/repositories/instrument_metadata_repository_sync.py:113-135; src/cli/commands/metadata.py:140-149]

### Testing standards
- **Unit tier** for the pure classifier, the mapper orphan guard (MagicMock repo), the CLI (`CliRunner`, repos patched), and `_render_backtestable` (`capsys`). **Component tier** for the real-repo orphan round-trip and the metadata transition (in-memory SQLite fixtures). No integration/e2e — no engine, no C extensions. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — Red first for `is_backtestable`/`total_bars`, the `sync_qualification` keep-bars branch, and the CLI command. [Source: development-principles.md]

### Project Structure Notes
- **New:** `src/services/metadata/backtestable.py` (`is_backtestable`, `total_bars`, `NonBacktestableTicker`).
- **Modified:** `src/services/firstrate/instrument_mapper.py` (`sync_qualification` keep-bars branch + `_has_bars` + `total_bars` import), `src/cli/commands/metadata.py` (`backtestable` subcommand + `_render_backtestable` + `SyncCatalogInstrumentRepository`/`NonBacktestableTicker`/`total_bars` imports).
- **New tests:** `tests/unit/services/metadata/test_backtestable.py`.
- **Modified tests:** `tests/unit/services/firstrate/test_instrument_mapper.py` (orphan guard), `tests/unit/cli/commands/test_metadata.py` (`backtestable` CLI + render), `tests/component/services/firstrate/test_instrument_mapper.py` (keep-bars round-trip), `tests/component/db/test_instrument_metadata_repository.py` (transition).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.5] — story statement + 3 acceptance criteria; FR25
- [Source: _bmad-output/planning-artifacts/epics.md:142] — "Unresolved tickers keep their bars but are flagged non-backtestable (excluded from qualification)"
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:5-16] — the Story-3.1-deferred orphan this story closes ("must not null an identity whose partition still holds bars")
- [Source: src/services/firstrate/instrument_mapper.py:141-182] — `sync_qualification` (the null-vs-keep decision to amend)
- [Source: src/db/repositories/instrument_metadata_repository_sync.py:87-171] — `list_by_status` / `count_by_status` / `apply_venue_override` (enumeration + transition)
- [Source: src/db/models/catalog_instrument.py:92-106] — the five `bar_count_*` fields (`total_bars`)
- [Source: src/cli/commands/metadata.py:43-153] — `metadata` group + `unresolved`/`coverage` idioms to mirror
- [Source: src/services/metadata/coverage_report.py] — frozen-dataclass / pure-service precedent
- [Source: src/config.py:153-156] — `firstrate_catalog_name` default (`--catalog` fallback)

### Open questions (non-blocking — proceed with the documented default)
1. **Backtestable definition.** Default: backtestable ⟺ `resolution_status == RESOLVED` (venue known), independent of whether bars exist. A `RESOLVED` ticker with zero bars is counted backtestable (it has a venue; the "has data" refinement is an Epic-5 loader concern). Flag if the team wants the universe to additionally require `total_bars > 0`.
2. **`--catalog` scope.** Default: bar evidence is looked up in the single configured ETF catalog (`firstrate_catalog_name`), overridable via `--catalog`. `instrument_metadata` is catalog-global (keyed by ticker), so the flag set is catalog-independent; only the *bars-retained* evidence is catalog-scoped. Flag if a multi-catalog sweep is wanted.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/services/metadata/test_backtestable.py tests/unit/services/firstrate/test_instrument_mapper.py tests/unit/cli/commands/test_metadata.py tests/component/services/firstrate/test_instrument_mapper.py tests/component/db/test_instrument_metadata_repository.py` — 95 pass (classifier + CLI/render + AC3 transition; mapper unchanged after revert).
- `uv run pytest tests/unit tests/component` — post-revert full run (see Change Log).
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found.

### Completion Notes List

- **AC1** — `is_backtestable(status)` (pure) keys the backtestable universe strictly on `resolution_status == RESOLVED`; `VENUE_UNRESOLVED` tickers are excluded and flagged. `metadata backtestable` reports the RESOLVED count (via the indexed `count_by_status`) and lists the flagged `VENUE_UNRESOLVED` set (via `list_by_status`). The real backtest path also already excludes them: `backtest_loader` gates on `nautilus_id`, which Story 3.1 nulls for `VENUE_UNRESOLVED`.
- **AC2** — bars are never dropped: this story only reads; the Parquet and `catalog_instruments.bar_count_*` are untouched, and the CLI surfaces `bars_retained` per flagged ticker as evidence. A ticker with no catalog row reports 0 bars (still flagged). Note: the identity-nulling is *intentionally kept* (see Review Findings) — the ticker is unreachable-by-id (correctly excluded), not dropped.
- **AC3** — a Story 3.3 override flip (`VENUE_UNRESOLVED → RESOLVED`) transitions a ticker into the enumerated backtestable universe with no re-import; the component test proves it over the real sync repo (metadata-level flip, no bar op).
- **AC4** — DB unset/unreachable → single yellow `Metadata DB not available` warning, no traceback (mirrors `coverage`/`unresolved`); report-only, always exit 0.
- **Design** — classifier/`total_bars`/`NonBacktestableTicker` are pure (`src/services/metadata/backtestable.py`, beside `coverage_report.py`); the CLI owns only session wiring + rendering. No new columns/migrations/settings, no venue guessing, no parquet deletion, no async/web/explorer surface, no changes to the provider/venue-map/resolution service/gate. **The planned `sync_qualification` keep-bars change was reverted during review** (it re-admitted guessed-venue tickers to backtests) — the mapper is unchanged from Story 3.1.

### Change Log

| Date | Change |
|---|---|
| 2026-07-13 | Story 3.5 drafted (SM). Status → ready-for-dev. |
| 2026-07-13 | Implemented pure backtestable classifier (`is_backtestable`/`total_bars`/`NonBacktestableTicker`), a `sync_qualification` keep-bars fix, `metadata backtestable [--catalog]` CLI + `_render_backtestable`; unit + component tests (TDD). Status → review. |
| 2026-07-13 | Code review (Blind Hunter · Edge Case Hunter). **Reverted** the `sync_qualification` keep-bars change (High: kept id re-admits guessed-venue tickers to backtests — `backtest_loader` gates on `nautilus_id`, not `resolution_status`). Patched report transparency (surface UNRESOLVED never-attempted count; empty-DB note; catalog-scoped bars column). Re-deferred the reachable-AND-excluded fix to Epic 5. Gates green. Status → done. |

### File List

- `src/services/metadata/backtestable.py` (new — pure `is_backtestable` / `total_bars` / `NonBacktestableTicker`)
- `src/cli/commands/metadata.py` (modified — `backtestable` subcommand + `_collect_non_backtestable` + `_render_backtestable` + imports)
- `tests/unit/services/metadata/test_backtestable.py` (new — classifier/aggregate)
- `tests/unit/cli/commands/test_metadata.py` (modified — `backtestable` CLI + render + registration)
- `tests/component/db/test_instrument_metadata_repository.py` (modified — AC3 transition)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified — recorded the reverted keep-bars approach + Epic-5 re-scope)
- *(reverted, unchanged from Story 3.1: `src/services/firstrate/instrument_mapper.py` + its unit/component tests)*
