# Story 2.2: Per-Archive ZIP Extraction

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want a per-archive ZIP extraction stage that extracts one letter-batched archive, hands its `.txt` files to the parser, and cleans up before the next,
so that peak disk usage stays bounded to one batch and an interrupted import leaves the catalog consistent and resumable.

## Acceptance Criteria

1. **AC1 — One archive at a time, with cleanup.** A `src/services/firstrate/zip_extractor.py` component processes a set of letter-batched ZIP archives (up to 26 per timeframe) and extracts **exactly one archive at a time**, handing the extracted `.txt` files to an injected caller (the parser hand-off seam) and **deleting the extracted `.txt` files before moving to the next archive**. Peak on-disk extracted footprint is bounded to a single archive's contents (never two archives staged simultaneously).
2. **AC2 — Interruption leaves no readable partial output.** When extraction or the hand-off fails mid-archive (raised exception / simulated interruption), the component leaves **no stray extracted `.txt` files** behind on disk and **surfaces the failure cleanly to the caller** (the exception propagates / is re-raised — never swallowed). This is the extraction-stage analogue of the Phase-1 metadata-gatekeeper keeping partial output invisible to explorer/backtest.
3. **AC3 — Archive-granular resume.** On a re-run after interruption, extraction **resumes at archive granularity without manual bookkeeping**: already-completed archives are skipped based on an observable-state predicate supplied by the caller (e.g. catalog/metadata state), **not** a hand-maintained ledger file written by the extractor.
4. **AC4 — Component tests prove the behavior.** Component tests (and unit tests as needed) exercise the extractor with **real temporary ZIP fixtures** (no network, no live data) and verify: per-archive extract-then-cleanup (AC1), the interruption/no-stray-files + clean-failure behavior (AC2), and archive-granular resume / skip (AC3).

## Tasks / Subtasks

- [x] **Task 1 — Create the `ZipExtractor` component (AC: #1, #2, #3)**
  - [x] New file `src/services/firstrate/zip_extractor.py` using **only stdlib** `zipfile` / `pathlib` / `tempfile` / `shutil` (+ `structlog` for logging, matching the package). No new dependencies, no DB, no Nautilus import, no network.
  - [x] Define a frozen `ArchiveBatch` dataclass exposing the per-archive hand-off contract: `archive: Path`, `txt_files: tuple[Path, ...]`, `staging_dir: Path`.
  - [x] Define a small `ArchiveResult` dataclass for the return summary: `archive: Path`, `status: Literal["extracted", "skipped"]`, `txt_count: int`.
  - [x] `ZipExtractor.extract_archives(archives, handler, is_complete=None) -> list[ArchiveResult]`:
    - `archives: Sequence[Path]` — ordered ZIP paths (letter-batched order is the caller's; the extractor preserves input order).
    - `handler: Callable[[ArchiveBatch], None]` — the **injected parser hand-off seam**, called exactly once per extracted archive (do NOT implement parsing here).
    - `is_complete: Callable[[Path], bool] | None` — optional resume predicate; when it returns `True` for an archive, that archive is skipped (not re-extracted) and recorded as `"skipped"`.
- [x] **Task 2 — Per-archive extract → hand-off → cleanup loop (AC: #1)**
  - [x] For each archive (in input order): if `is_complete(archive)` is truthy, record `skipped` and `continue` (Task 4 covers this).
  - [x] Stage extraction into a fresh **per-archive** `tempfile.TemporaryDirectory` (under an optional configurable `staging_root`, else the system temp). Only one staging dir exists at a time → peak disk bounded to one archive (AC1).
  - [x] Extract **only `.txt` members**, flattening to basename and rejecting unsafe/absolute/`..` entries (zip-slip guard). Build `ArchiveBatch` and invoke `handler(batch)` exactly once.
  - [x] On normal completion, the `TemporaryDirectory` context exit deletes the extracted `.txt` files **before** the loop advances to the next archive (AC1).
- [x] **Task 3 — Clean failure + no stray files on interruption (AC: #2)**
  - [x] Wrap extraction + hand-off per archive so the `TemporaryDirectory` is removed on **both** success and exception (context-manager `with`). Any exception (corrupt ZIP, handler/interruption failure) propagates to the caller after cleanup — **never swallowed**.
  - [x] Confirm no extracted `.txt` files remain anywhere (staging dir removed) after a mid-archive failure; the archive is left not-complete so it re-runs on resume.
- [x] **Task 4 — Archive-granular resume via observable-state predicate (AC: #3)**
  - [x] The extractor never writes its own ledger. Completed-archive detection is delegated to the injected `is_complete` predicate (caller derives it from catalog/metadata — consistent with `import_service` using metadata as source of truth). Default (`None`) → nothing is pre-completed (full run).
  - [x] Log one structured line per archive decision (`extracted` / `skipped`) for operator visibility.
- [x] **Task 5 — Tests with real temporary ZIP fixtures (AC: #1, #2, #3, #4)**
  - [x] Component: `tests/component/services/firstrate/test_zip_extractor.py` — build ≥2 small real temp ZIPs (stdlib `zipfile`), run the extractor with a recording stub handler, and assert:
    - **AC1**: at the moment `handler` is called for archive N, exactly that archive's `.txt` files exist on disk (observed inside the handler), and after the call (before archive N+1) they are gone — only one archive staged at a time.
    - **AC2**: a handler that raises mid-archive (and a corrupt-ZIP case) leaves **no** stray `.txt` files and the exception propagates (assert via `pytest.raises`); not swallowed.
    - **AC3**: with an `is_complete` predicate returning `True` for already-done archives, those are skipped (handler not invoked for them) and remaining archives still process — no ledger file created by the extractor.
  - [x] Unit (as needed): `tests/unit/services/firstrate/test_zip_extractor.py` — zip-slip / non-`.txt` member filtering, empty-archive handling, input-order preservation, `discover_archives` sorting, and `ArchiveBatch`/`ArchiveResult` shape. (Keep pure-logic checks here; no Nautilus.)
- [x] **Task 6 — Gates**
  - [x] `uv run ruff check .` clean; `uv run mypy .` clean ("Success: no issues found in 338 source files"); `make test-component` green (684 passed, 16 skipped); `make test-unit` 1069 passed with 2 **pre-existing** failures in files this story never touches (see Debug Log).

## Dev Notes

### ADR-8 is the governing decision
Per **ADR-8** (`architecture.md` §"Import Pipeline & Catalog", lines 316-322): *"Process one ZIP at a time: extract → parse (reuse `FirstRateCsvParser`) → write Parquet → verify row count → delete extracted `.txt` before the next archive. Peak disk ≈ one batch. Idempotent resume at archive granularity; Phase 1 metadata-gatekeeper keeps any partial Parquet invisible to explorer/backtest on interrupt."* **This story implements only the `extract → [hand off] → delete extracted .txt before next archive` portion.** Parse / Parquet-write / row-count-verify / metadata-upsert are explicitly out of scope (Stories 2.3–2.7) — the parser hand-off is an **injected seam** (`handler` callback), stubbed in tests.

### Scope boundaries (from harness-story-2.2-spec.md)
- Implement **only** `src/services/firstrate/zip_extractor.py`. Do NOT implement parsing, Parquet conversion, the import pipeline wiring, dry-run scan, verification, idempotency-by-date-range, venue resolution, or explorer work.
- **No DB schema change, no Alembic migration.** No live data sources, no network, no broker I/O.
- Provide the extraction stage **and the seam** the parser will later call — inject/stub the hand-off rather than implementing it.

### Why per-archive temp dir + context-manager cleanup
`tempfile.TemporaryDirectory` (one per archive, entered/exited inside the loop body) gives both AC1 and AC2 for free: exit on the normal path deletes the staged `.txt` files **before** the next iteration (AC1, peak = one batch); exit on the exception path deletes them too, so an interruption leaves **no stray files** (AC2). The exception is allowed to propagate out of `extract_archives` after cleanup — that is the "signals failure cleanly to the caller" requirement (verification: assert with `pytest.raises`, not a swallowed/`failed` status). Contrast with `import_service._import_ticker`, which catches-and-returns a `failed` `ImportResult` per ticker; here the spec explicitly wants the failure **raised**, because a mid-archive halt should stop the run consistently and resume at archive granularity.

### Resume is observable-state, not a ledger (AC3)
ADR-5 / the `import_service` classifier establish that **metadata (not a filesystem scan or a hand-maintained file) is the source of truth** for "what was successfully imported" (see `import_service._classify_ticker`, lines 322-420, and its ADR-5 note at lines 311-320). Mirror that here: the extractor must **not** write its own progress ledger. Instead it accepts an injected `is_complete(archive) -> bool` predicate; the later import wiring (Story 2.4/2.6) will supply one derived from catalog/metadata state. Default `None` means "treat nothing as complete" (full run). This keeps the extractor a pure stage and keeps resume decisions where the source-of-truth lives.

### Archive shape & ordering
FirstRate ETF data ships as **letter-batched ZIP archives, up to 26 per timeframe** (A–Z); each archive contains the `{TICKER}_full_{timeframe}_adjsplitdiv.txt` files for tickers in that letter bucket (see `import_service._extract_ticker`, lines 458-474, for the `.txt` filename convention, and `memory: project_firstrate_import_patterns`). The extractor takes an already-ordered `Sequence[Path]` of ZIPs and preserves input order — it does not need to know the letter convention itself. A small module-level `discover_archives(source_dir)` helper that returns sorted `*.zip` paths is acceptable (pure stdlib, testable) but the caller may also pass archives explicitly; keep the core method archive-list-driven.

### Hand-off contract (the seam)
`handler: Callable[[ArchiveBatch], None]` is called exactly once per extracted archive, after the `.txt` files are on disk and before they are deleted. `ArchiveBatch` carries `archive` (source ZIP), `txt_files` (extracted paths, basename-flattened), and `staging_dir`. The later parser stage (Story 2.4) will plug `FirstRateCsvParser` + catalog write in behind this seam; for this story tests pass a recording stub. Do **not** import `nautilus_trader` or `FirstRateCsvParser` in `zip_extractor.py` — the seam keeps the extractor Nautilus-free and unit-testable without `--forked`.

### Safety: only `.txt`, zip-slip guard
Extract only members whose name ends in `.txt`; flatten to `Path(member).name` and skip any member with an absolute path or `..` segment (defends against zip-slip even though FirstRate archives are trusted — cheap, stdlib-only). Non-`.txt` members (e.g. readmes) are ignored. An archive with zero `.txt` members hands off an empty `txt_files` tuple and logs a warning (the caller decides what an empty batch means); it still counts as processed for AC1's one-at-a-time semantics.

### Project Structure Notes
- **New file:** `src/services/firstrate/zip_extractor.py` (matches `architecture.md` source-tree annotation line 513/562: *"`firstrate/zip_extractor.py` — NEW: per-archive extract/cleanup"*). Stays under size limits (<500 lines, functions <50, line length 100).
- **New tests:** `tests/component/services/firstrate/test_zip_extractor.py` (primary, per `architecture.md` line 513) and `tests/unit/services/firstrate/test_zip_extractor.py` (pure-logic edges).
- No edits to `import_service.py` / `dry_run.py` this story — wiring the extractor into the import pipeline is Story 2.4. This story delivers the standalone stage + seam.

### Testing standards
- **TDD** (CLAUDE.md / development-principles): write the failing test first (Red-Green-Refactor).
- **Tiers:** Unit for pure logic (member filtering, zip-slip, ordering); **Component** for the full extract→handoff→cleanup loop with real temp ZIPs and a stub handler. No integration `--forked` (no Nautilus, no engine). Commands: `make test-unit`, `make test-component`.
- **Real fixtures:** build ZIPs in `tmp_path` with stdlib `zipfile.ZipFile(..., "w")`; no network, no committed binary fixtures.
- **Import gate (F401/F821):** add imports and their usages in the same edit; keep `zip_extractor.py` Nautilus-free.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.2] — AC1–AC4 (lines 435-457)
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-8] — per-archive extract→import→cleanup, peak disk, archive-granular resume (lines 316-322)
- [Source: _bmad-output/planning-artifacts/architecture.md] — source-tree `firstrate/zip_extractor.py` "NEW: per-archive extract/cleanup" + pipeline diagram `zip_extractor (one ZIP) → FirstRateCsvParser → catalog.write_data()` (lines 513, 552, 562)
- [Source: harness-story-2.2-spec.md] — scoped ACs + TEA verification guidance (one-at-a-time observed via hand-off; clean-failure raised not swallowed; resume without ledger; real temp ZIP fixtures)
- [Source: src/services/firstrate/import_service.py:311-474] — ADR-5 metadata-as-source-of-truth classifier (resume pattern to mirror) + `_extract_ticker` `.txt` filename convention
- [Source: src/services/firstrate/parsers/base.py:84-124] — `BaseParser.parse_file` signature the later seam feeds (context only; not called this story)
- [Source: memory project_firstrate_import_patterns] — letter-batched archive layout, required env vars, import gotchas

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New tests: `uv run pytest tests/unit/services/firstrate/test_zip_extractor.py tests/component/services/firstrate/test_zip_extractor.py -q` → **12 passed** (6 unit + 6 component).
- Gates: `uv run ruff check .` → `All checks passed!` (after autofixing I001 import-order in the two new test files); `uv run mypy .` → `Success: no issues found in 338 source files`.
- `make test-component` → 684 passed, 16 skipped. `make test-unit` → 1069 passed, **2 failed**.
- **The 2 unit failures are pre-existing and unrelated to Story 2.2** (verified by running them in isolation — they reproduce with no involvement of `zip_extractor`, which they do not import):
  - `tests/unit/db/test_migration_bar_count_30min.py::test_migration_chains_off_current_head` — asserts the 2.1 migration's `down_revision` is `dbec2c1f25a6`, but commit `ae3f004` ("linearize 2.1 migration after Epic 1 to resolve double head") re-pointed it to `f051a079629c`. The test was not updated alongside that branch-setup commit. Out of scope (Story 2.1 / alembic linearization).
  - `tests/unit/services/metadata/test_fmp_client.py::TestFMPClientFetchProfile::test_returns_first_profile_element` — asserts the outbound `apikey` query param is non-empty; fails because no `FMP_API_KEY` is configured in the test environment. Config/env-dependent Epic-1 metadata-test debt. Out of scope.

### Completion Notes List

- **AC1 (one archive at a time + cleanup):** `ZipExtractor.extract_archives` iterates archives in input order; each archive is staged into its own `tempfile.TemporaryDirectory`, handed to the injected `handler` exactly once, then the temp dir is deleted on context exit **before** the loop advances. Component test observes — from inside the handler — that exactly the current archive's `.txt` files exist and no earlier archive's staging dir survives (`other_dirs_alive_at_call == [[], []]`), proving peak footprint = one batch.
- **AC2 (clean failure, no stray files):** the per-archive `with tempfile.TemporaryDirectory(...)` removes the staging dir on the exception path too, so a handler raise (simulated interruption) and a corrupt-ZIP (`zipfile.BadZipFile`) both leave **zero** stray `.txt` files under the staging root and **re-raise** to the caller (asserted via `pytest.raises`) — never swallowed. The failing archive is left not-complete, so a re-run retries it.
- **AC3 (archive-granular resume):** resume is delegated to an injected `is_complete(archive)` predicate (observable state — caller derives it from catalog/metadata, mirroring `import_service`'s metadata-as-source-of-truth). The extractor writes **no** ledger of its own (test asserts the staging root stays empty after the run). `None` predicate → full run.
- **AC4 (component tests):** real temp ZIPs built with stdlib `zipfile`; no network, no live data, no Nautilus. Covered by `tests/component/.../test_zip_extractor.py` (AC1/AC2/AC3) + unit edges in `tests/unit/.../test_zip_extractor.py`.
- **Scope discipline:** only `src/services/firstrate/zip_extractor.py` added (plus its tests). No parsing/Parquet/import-wiring/dry-run/verification/idempotency/venue/explorer work; no DB schema change, no migration, no new dependency. The parser hand-off is the injected `handler` seam (stubbed in tests), per harness-story-2.2-spec.md. `zip_extractor.py` deliberately does **not** import `nautilus_trader`, keeping it unit-testable without `--forked`.
- **Safety:** members are filtered to `.txt` only and flattened to basename (`Path(name).name`), which neutralizes zip-slip path traversal; files are streamed (`shutil.copyfileobj`) so large real archives are not buffered in memory.

### File List

**Source (new):**
- `src/services/firstrate/zip_extractor.py` — per-archive ZIP extraction stage (`ZipExtractor`, `ArchiveBatch`, `ArchiveResult`, `discover_archives`)

**Tests (new):**
- `tests/component/services/firstrate/test_zip_extractor.py` — AC1/AC2/AC3 end-to-end with real temp ZIPs + recording stub handler
- `tests/unit/services/firstrate/test_zip_extractor.py` — pure-logic edges (member filtering, zip-slip flatten, empty archive, ordering, discovery, dataclass shapes)

**Planning/tracking (modified):**
- `_bmad-output/implementation-artifacts/2-2-per-archive-zip-extraction.md` — this story file
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story 2.2 status transitions

## Review Findings

Three adversarial layers run (Blind Hunter, Edge Case Hunter, Acceptance Auditor). Acceptance Auditor: **all 4 ACs PASS, scope respected**. 3 patch findings applied, the rest dismissed as by-design or out-of-scope.

- [x] [Review][Patch] Duplicate `.txt` basename across nested ZIP dirs silently overwrote → data loss + miscount `[zip_extractor.py:_extract_txt_members]` — now raises `ValueError` on collision (no silent loss), tested + cleanup-on-raise verified.
- [x] [Review][Patch] `staging_root` that did not exist failed late, deep in the first extraction `[zip_extractor.py:extract_archives]` — now created up front (`mkdir(parents=True, exist_ok=True)`), tested.
- [x] [Review][Patch] Backslash-separator member names defeated basename flattening on POSIX `[zip_extractor.py:_extract_txt_members]` — names normalized (`\\`→`/`) before basename, tested.
- [x] [Review][Dismiss] Missing-path raises `FileNotFoundError` not `BadZipFile` — docstring `Raises` already says "any failure (e.g. BadZipFile)"; non-exhaustive by design.
- [x] [Review][Dismiss] `is_complete` raise / corrupt-ZIP aborts remaining batch — by design (ADR-8 "surface cleanly"; re-run resumes via predicate). Consistent with AC2.
- [x] [Review][Dismiss] symlink member / zip-bomb / handler retaining staging dir — out-of-scope (trusted vendor data; documented contract; no escape since basename-flattened inside staging dir).

## Senior Developer Review (AI)

**Reviewed:** 2026-06-28 · **Outcome:** Approved (3 patches applied during review; no unresolved High/Med) · Three adversarial layers run.

- **Acceptance Auditor — PASS (all 4 ACs).** AC1 one-at-a-time + cleanup observed from inside the handler (`files_present_at_call == [2,1]`, `other_dirs_alive_at_call == [[],[]]`); AC2 handler-raise and `BadZipFile` both re-raise with zero stray files; AC3 resume via injected `is_complete` predicate, no ledger written (staging root stays empty); AC4 real stdlib temp ZIPs, no network/Nautilus. Scope clean — only `zip_extractor.py` added, no parse/Parquet/DB/migration/dependency, parser hand-off stubbed.
- **Edge Case Hunter — 1 High + 2 Med (all RESOLVED).** High: duplicate-basename silent overwrite → fixed (raise). Med: late-failing `staging_root` → fixed (create up front); Med: backslash flattening on POSIX → fixed (normalize separators). Confirmed-good: cleanup on every exit path (success/raise/BadZipFile), large-member streaming, empty-archive + empty-list handling, case-insensitive `.txt` match.
- **Blind Hunter — converged on the duplicate-basename defect (resolved).** Other flags (zip-bomb, symlink, handler-retains-staging) dismissed as out-of-scope for trusted vendor data / documented contract.

### Action Items

- [x] **[High]** Raise on duplicate `.txt` basename instead of silent overwrite — fixed `src/services/firstrate/zip_extractor.py`, regression test `test_duplicate_basename_raises_and_leaves_no_stray_files`.
- [x] **[Med]** Create `staging_root` up front to avoid late mid-run failure — fixed, test `test_missing_staging_root_is_created`.
- [x] **[Med]** Normalize backslash separators before basename flattening — fixed, test `test_backslash_separator_member_is_flattened`.

## Change Log

| Date | Change |
|------|--------|
| 2026-06-28 | Story 2.2 implemented — added `src/services/firstrate/zip_extractor.py`: per-archive ZIP extraction with one-at-a-time staging + cleanup (AC1), clean-failure/no-stray-files on interruption (AC2), and archive-granular resume via injected observable-state predicate / no ledger (AC3). Parser hand-off is an injected callback seam (stubbed). 12 new tests (6 unit + 6 component) with real temp ZIP fixtures. ruff + mypy clean; component suite green; the 2 failing unit tests are pre-existing and unrelated (migration-chain + FMP-apikey env). |
| 2026-06-28 | Code review (3 adversarial layers) — resolved 1 High + 2 Med: raise on duplicate `.txt` basename (no silent data loss), create `staging_root` up front, normalize backslash separators before flattening. 3 regression tests added (15 zip-extractor tests total). All gates green (ruff clean, mypy clean across 338 files, firstrate unit+component suites pass). |
