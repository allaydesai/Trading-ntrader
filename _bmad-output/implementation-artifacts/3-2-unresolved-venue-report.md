# Story 3.2: Unresolved-Venue Report

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a `metadata unresolved` CLI report listing every ticker that lacks a resolved venue together with a derived reason,
so that I have a concrete, actionable worklist to drive the ETF universe toward 100% venue coverage — and a basis for filling in `venue_overrides.csv` (Story 3.3).

## Acceptance Criteria

1. **Given** imported ETFs with some venues unresolved, **When** the operator runs the `metadata unresolved` CLI subcommand, **Then** it lists every ticker with `resolution_status = VENUE_UNRESOLVED` (queried via the indexed `ix_instrument_metadata_resolution_status`), each with its provider and a derived reason (e.g. "unknown to FMP — no profile returned" vs "FMP venue label blank, ambiguous (e.g. AMEX), or unmapped").
2. **Given** the report, **When** it is produced, **Then** it is rendered as readable CLI output (a Rich table with a total count), the tickers are ordered deterministically (by ticker), and the output is usable as the basis for filling in `venue_overrides.csv` (the report surfaces the `ticker` column the override file keys on).
3. **Given** zero rows with `VENUE_UNRESOLVED`, **When** the subcommand runs, **Then** it prints a clear "all venues resolved — nothing to fix" message (no empty table, exit code 0), so the operator can trust the empty result.
4. **Given** the metadata DB is unconfigured (`DATABASE_URL` unset), **When** the subcommand runs, **Then** it degrades gracefully with a clear warning (no traceback), mirroring the existing CLI DB-unconfigured handling.

## Tasks / Subtasks

- [x] **Task 1: Repository — list rows by resolution status** (AC: #1, #2) — *write the test in `tests/component/db/test_instrument_metadata_repository.py` FIRST (TDD Red→Green)*
  - [x] Add `list_by_status(self, status: ResolutionStatus) -> list[InstrumentMetadata]` to `src/db/repositories/instrument_metadata_repository_sync.py`: `select(InstrumentMetadata).where(InstrumentMetadata.resolution_status == status).order_by(InstrumentMetadata.ticker)`, returning `list(result.scalars().all())`. The `where` clause is served by the existing `ix_instrument_metadata_resolution_status` index (do not add a new index/migration). [Source: src/db/models/instrument_metadata.py:59-71; src/db/repositories/instrument_metadata_repository_sync.py:72-83]
  - [x] Import `ResolutionStatus` from `src.models.instrument_metadata` at module top (the ORM model already imports it there; keep the sync repo consistent). Fully annotate the return type (`list[InstrumentMetadata]`) for the F821 gate. Keep the function `< 50` lines and the file `< 500`. [Source: CLAUDE.md Foundational Rules; src/db/models/instrument_metadata.py:18]
  - [x] Do **not** touch the async repository (`instrument_metadata_repository.py`) — this report is a sync CLI path only; no web/API consumer exists for it in this story.

- [x] **Task 2: Reason derivation — pure function** (AC: #1) — *tests FIRST (TDD)*
  - [x] Add a pure module-level function `unresolved_reason(md: InstrumentMetadata) -> str` in a new `src/services/metadata/unresolved_report.py` (the domain-facing derivation lives beside the metadata services, not in the CLI layer, so it is unit-testable without Click). It takes the **ORM** `InstrumentMetadata` row and returns a human-readable reason string.
    - **Discriminator:** the FMP provider's `_degraded` path (ticker unknown / no profile) sets `asset_type = None` and every descriptive field to `NA_SENTINEL`; the found-but-venue-unmapped path (`_to_domain`) always sets a concrete `asset_type` (ETF/EQUITY/FUND) via `_asset_type()`. So `asset_type is None` ⇒ "unknown to FMP", else ⇒ "found, venue label unusable". Use `asset_type` as the primary signal (it is `None` **only** on the degraded path). [Source: src/services/metadata/providers/fmp_provider.py:77-118]
    - Return `"unknown to <provider> — no profile returned"` when `md.asset_type is None` (degraded), else `"<provider> venue label blank, ambiguous (e.g. AMEX), or unmapped"`. Interpolate `md.metadata_provider` for the provider name so the reason is provider-agnostic. **Do not** claim more precision than the persisted row supports: the raw FMP `exchange` label is normalized to `None` and never stored, so blank-vs-ambiguous-vs-unmapped cannot be distinguished post-hoc — the reason is deliberately best-effort and honest about that. [Source: src/services/metadata/venue_map.py:23-43 (label → None, not persisted); Dev Notes "Why the reason is derived, not stored"]
  - [x] Keep the function pure (no I/O, no clock), `< 50` lines, fully return-type-annotated. `NA_SENTINEL` need not be imported — the `asset_type is None` discriminator is sufficient and cleaner than sniffing descriptive sentinels. [Source: CLAUDE.md size limits]

- [x] **Task 3: CLI — new `metadata` command group + `unresolved` subcommand** (AC: #1, #2, #3, #4) — *tests FIRST (TDD, CliRunner)*
  - [x] Create `src/cli/commands/metadata.py` with `@click.group() def metadata(): """Instrument metadata inspection commands."""` and a `@metadata.command("unresolved")` subcommand. Register it in `src/cli/main.py`: `from src.cli.commands.metadata import metadata` + `cli.add_command(metadata)` (mirrors `report`/`data` group registration). [Source: src/cli/commands/report.py:19-22; src/cli/main.py:35-42]
  - [x] The `unresolved` command:
    1. Open a sync session via `get_sync_session()` inside a `try/except` that catches `RuntimeError` (DB unconfigured — it raises "Database not configured…") and any `DatabaseConnectionError`, printing a single yellow warning line and returning (AC4 — no traceback). [Source: src/db/session_sync.py:97-135; src/cli/commands/report.py:33-52 (try/except console idiom)]
    2. `rows = SyncInstrumentMetadataRepository(session).list_by_status(ResolutionStatus.VENUE_UNRESOLVED)`.
    3. Delegate rendering to a pure helper `_render_unresolved(rows) -> None` (or return a Rich `Table` from a builder + a separate print) so the formatting is unit-testable without a live DB. On empty `rows`: print `"✓ All venues resolved — no unresolved-venue tickers."` and return (AC3). Otherwise: print a Rich `Table` (columns **Ticker**, **Provider**, **Reason**) with one row per ticker (reason via `unresolved_reason`), preceded/followed by a count line `"<N> ticker(s) with unresolved venue"`, and a footer hint: `"Add the correct venue for each in venue_overrides.csv (Story 3.3) to resolve."` (AC1, AC2). [Source: src/cli/commands/import_data.py:8-9,42 (Console/Table idiom); src/cli/commands/report.py Table usage]
  - [x] Keep every function `< 50` lines and the file `< 500`. Extract the table build into a small helper if the command body grows past the limit. No new settings, no new migration, no network/provider call — this is a pure read over the local cache table. [Source: CLAUDE.md size limits; Dev Notes "Scope boundaries"]

- [x] **Task 4: Unit tests** (AC: #1, #2, #3, #4)
  - [x] **Reason derivation (`tests/unit/services/metadata/test_unresolved_report.py`, `@pytest.mark.unit`):**
    - Degraded row (`asset_type=None`, descriptives `NA_SENTINEL`, provider `"FMP"`) → reason contains `"unknown to FMP"` / `"no profile"`.
    - Found-but-unmapped row (`asset_type=AssetType.ETF`, `company_name="SPDR S&P 500"`, `venue=None`, provider `"FMP"`) → reason contains `"blank, ambiguous"` / `"unmapped"` and does **not** claim "unknown to FMP".
    - Provider interpolation: a row with `metadata_provider="OTHER"` renders `"OTHER"` in the reason (provider-agnostic). Use the **ORM** `InstrumentMetadata` (build via kwargs like the component `_make_metadata`) — not the Pydantic domain model. [Source: tests/component/db/test_instrument_metadata_repository.py:62-70 (_make_metadata idiom)]
  - [x] **CLI (`tests/unit/cli/commands/test_metadata.py`, `CliRunner`, `@pytest.mark.unit`):** patch `get_sync_session` (a context manager) and `SyncInstrumentMetadataRepository` (or patch `list_by_status`) so no real DB is touched — mirror the `patch(...)` idiom in `test_import_data.py`.
    - **Populated:** repo returns 2 VENUE_UNRESOLVED ORM rows (one degraded, one found-unmapped) → exit code 0, output contains both tickers, both reason phrasings, the total-count line, and the `venue_overrides.csv` hint (AC1, AC2).
    - **Empty:** repo returns `[]` → exit code 0, output contains the "All venues resolved" message and no table header (AC3).
    - **DB unconfigured:** `get_sync_session` raises `RuntimeError("Database not configured…")` → exit code 0 (graceful), output contains a clear warning, no traceback (`result.exception` is `None` or handled) (AC4).
  - [x] **Rendering helper (optional but preferred):** a direct unit test of `_render_unresolved`/table-builder over a fixed row list asserting column contents + ordering, so formatting is covered without CliRunner. [Source: test_import_reporting.py progress-line unit-test precedent]

- [x] **Task 5: Component test — real sync repo round-trip** (AC: #1, #2)
  - [x] In `tests/component/db/test_instrument_metadata_repository.py` add a test for `list_by_status`: seed a mix of statuses via `_make_metadata` (e.g. `RESOLVED` `AAA`, `VENUE_UNRESOLVED` `CCC`, `VENUE_UNRESOLVED` `BBB`, `UNRESOLVED` `DDD`), call `list_by_status(ResolutionStatus.VENUE_UNRESOLVED)`, and assert it returns exactly `["BBB", "CCC"]` (only VENUE_UNRESOLVED, ordered by ticker). Reuse the file's existing `sync_session` fixture + `_CREATE_TABLE_SQL`. [Source: tests/component/db/test_instrument_metadata_repository.py:48-70]

- [x] **Task 6: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new `ResolutionStatus` import in the sync repo; new module imports).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate new functions.
  - [x] `make test-unit` green (reason + CLI + rendering deltas, no regressions). `make test-component` green for the new repo test.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.
  - [x] Smoke: `uv run python -m src.cli.main metadata unresolved --help` renders; `... metadata unresolved` runs (may warn if DB unconfigured — that is AC4).

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Acceptance Auditor: PASS — all 4 ACs satisfied, no scope violations, size limits respected. Fixes applied and re-verified (1864 unit+component pass, ruff+mypy clean).

- [x] [Review][Patch] Query-time DB error escaped as a raw traceback (Edge, High) — `get_sync_session()` is lazy, so a DB-down / not-migrated / bad-URL failure surfaces inside `list_by_status.execute`, not on `with`-entry. The CLI caught only `(RuntimeError, DatabaseConnectionError)`, so an `OperationalError`/`ProgrammingError` tracebacked. Fixed: `list_by_status` now translates `OperationalError → DatabaseConnectionError` (mirrors `upsert`), and the CLI `except` was broadened to `SQLAlchemyError` (covers Postgres-down, missing-table, malformed URL). Added CLI query-error + connection-error tests and a repo translation test. [src/db/repositories/instrument_metadata_repository_sync.py:list_by_status; src/cli/commands/metadata.py:unresolved]
- [x] [Review][Patch] Unescaped Rich markup in DB-sourced cells (Edge/Blind, Low) — a `ticker`/`provider` containing a Rich metacharacter (e.g. `[`) could raise `MarkupError`/mis-render. Fixed: `escape()` the ticker and provider cells (and the warning-line exception text). Added a markup-escape render test. [src/cli/commands/metadata.py:_render_unresolved]
- [x] [Review][Patch] Weak/misleading test assertions (Blind, Low ×3) — the "orders rows" render test asserted only membership (now asserts `index("SPY") < index("ZZZ")`); the graceful-degradation test's near-vacuous `"not" in output` now asserts the actual `"Metadata DB not available"` warning; the provider-agnostic reason test covered only the degraded branch (added a found-branch non-FMP case). [tests/unit/cli/commands/test_metadata.py; tests/unit/services/metadata/test_unresolved_report.py]
- [x] [Review][Dismiss→Documented] Cross-provider misclassification of the `asset_type is None` discriminator (Edge/Blind, Low) — the reason string is provider-agnostic but the discriminator is FMP-specific. Only FMP writes `VENUE_UNRESOLVED` rows today, and the module docstring + open question #1 already document the deliberate best-effort two-way classification. No code change; accepted as documented design boundary (exact-label fidelity would need a schema change, out of scope).

## Dev Notes

### The core idea (why this story exists)
Epic 3 gates ETF-universe completion on 100% venue coverage with **no inferred venues** (Story 3.4). Story 3.1 made the FMP-resolved venue authoritative and left `VENUE_UNRESOLVED` tickers unqualified. Story 3.2 gives the operator the **worklist**: every ticker the conservative venue map (ADR-6) could not confidently resolve, with a reason, so they can fill in `venue_overrides.csv` (Story 3.3). The conservative map *deliberately* routes ambiguous labels (notably `AMEX`) to `VENUE_UNRESOLVED` rather than guess — this report is the surfacing mechanism that makes that safe-by-default choice actionable. [Source: src/services/metadata/venue_map.py:1-14; architecture.md venue-correctness risk]

### Why the reason is derived, not stored
The persisted `instrument_metadata` row does **not** store the raw FMP `exchange` label — `normalize_venue` collapses blank/ambiguous/unmapped all to `venue=None` (Story 1.4/ADR-6). So the report cannot reconstruct the *exact* original label after the fact. What it **can** derive reliably from the row is a two-way classification:
- **`asset_type is None`** ⇒ the provider's `_degraded` path ran (no profile / unknown ticker) → "unknown to FMP".
- **`asset_type` set** (ETF/EQUITY/FUND) ⇒ a profile came back but its `exchange` label was blank, ambiguous (AMEX), or unmapped → "venue label unusable".

`asset_type` is the clean discriminator: `_to_domain` **always** assigns a concrete `AssetType` via `_asset_type()`, while `_degraded` sets it `None`. The AC lists "blank FMP label, ambiguous AMEX, ticker unknown to FMP" as *examples* of reasons — this story provides an honest, derivable reason, not a fabricated three-way precision the data can't support. If the team later wants exact-label fidelity, that requires persisting the raw label (a schema change) — out of scope here. [Source: src/services/metadata/providers/fmp_provider.py:53-118; src/services/metadata/venue_map.py:23-43]

### The seam (data + control flow)
```
instrument_metadata (cache table, populated by Epic 1 resolution + Story 3.1 import)
  rows where resolution_status = VENUE_UNRESOLVED  (venue IS NULL, indexed)
        │
  metadata unresolved  (CLI, this story)
        │  SyncInstrumentMetadataRepository.list_by_status(VENUE_UNRESOLVED)  ← indexed SELECT, ordered by ticker
        │  unresolved_reason(row)  ← pure derivation (asset_type discriminator)
        ▼
  Rich table: Ticker | Provider | Reason  +  count  +  "→ venue_overrides.csv" hint
```
Read-only over the local cache — no network, no provider call, no write. [Source: architecture.md two-table boundary; src/db/models/instrument_metadata.py:59-71]

### Scope boundaries (do NOT do here)
- **No `venue_overrides.csv` load/merge/write** — that is Story 3.3. This report only *surfaces* the tickers; it does not write the override file or flip any `resolution_status`.
- **No coverage count / completeness gate** — that is Story 3.4 (`metadata coverage` + the 0-unresolved gate). This story lists; it does not compute a pass/fail gate. (`list_by_status` is the reusable primitive 3.4 will build its indexed count on, but do not add the count command here.)
- **No exclude/flag/keep-bars** — Story 3.5.
- **No new DB columns / migrations / settings** — the index and `resolution_status` enum already exist; the report reads what Epic 1 + Story 3.1 already persisted.
- **No async/web endpoint** — CLI sync path only. Do not touch the async repo or add an API route.
- **No changes to the FMP provider, venue map, or resolution service** — all `done`; this story only *reads* their persisted output.

### Design decision — new `metadata` command group
There is no existing `metadata` CLI group (verified: `main.py` registers `run`, `data`, `backtest`, `strategy`, `report`, `history`, `import`). FR21/24 both describe `metadata <subcommand>` verbs (`metadata unresolved`, later `metadata coverage`), so a dedicated `@click.group()` named `metadata` is the natural home, and Story 3.4 will add `coverage` to the same group. Mirror the `report` group's structure (group docstring + `@group.command()` subcommands). [Source: src/cli/main.py:6-42; src/cli/commands/report.py:19-30; epics.md FR21/FR24]

### Design decision — derivation lives in `services/metadata`, not the CLI
`unresolved_reason` is placed in `src/services/metadata/unresolved_report.py` (beside the other metadata services) rather than inline in the Click command, so it is a pure, unit-testable primitive with no Click dependency — matching the project's "pure logic → unit tier" heuristic and the mapper/dry-run precedent. The CLI command stays thin: open session → query → render. [Source: CLAUDE.md Decision Heuristics; src/services/firstrate/dry_run.py (pure helpers consumed by CLI)]

### Previous-story intelligence
- **Story 3.1 (done)** established that `VENUE_UNRESOLVED` ⇔ `venue is None`, and that the import pipeline is the single writer of `catalog_instruments`. This report never writes — it reads `instrument_metadata`, the source of truth for resolved metadata. [Source: 3-1 story Dev Notes]
- **`ix_instrument_metadata_resolution_status` (Story 1.2, done)** already indexes `resolution_status`, so `list_by_status` is an indexed lookup — the same index Story 3.4 will use for its O(1) count. [Source: src/db/models/instrument_metadata.py:71]
- **DB-unconfigured idiom:** `get_sync_session()` raises `RuntimeError` when `DATABASE_URL` is unset; existing CLI commands catch and print a friendly line rather than tracing back. `import_data._open_metadata_session` degrades to a yellow warning — mirror that tone. [Source: src/db/session_sync.py:118-135; src/cli/commands/import_data.py:96-113]

### Testing standards
- **Unit tier** for the pure `unresolved_reason` derivation, the table/render helper, and the CLI command (CliRunner with `get_sync_session`/repo patched — no DB, no Nautilus). **Component tier** for the real-repo `list_by_status` round-trip over the in-memory SQLite fixture. No integration/e2e — no engine, no C extensions. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — Red first for `list_by_status`, `unresolved_reason`, and the CLI command. [Source: development-principles.md]
- Build ORM rows in tests via the `_make_metadata` kwargs idiom already in the component test; keep `@pytest.mark.unit` / component markers consistent with siblings.

### Git intelligence
Scope precedent: Epic-3 story 3-1 committed as `feat(epic3): story 3-1 — …`. Subject here: `feat(epic3): story 3-2 — unresolved-venue report`. No AI/claude references in the message. Stage then commit in **separate** Bash calls (the bash-guard commit gate rejects a `git add && git commit` one-liner). [Source: CLAUDE.md Commit Format + Editing with Auto-Linter]

### Project Structure Notes
- **New:** `src/cli/commands/metadata.py` (group + `unresolved`), `src/services/metadata/unresolved_report.py` (`unresolved_reason`).
- **Modified:** `src/cli/main.py` (register `metadata`), `src/db/repositories/instrument_metadata_repository_sync.py` (`list_by_status`).
- **New tests:** `tests/unit/services/metadata/test_unresolved_report.py`, `tests/unit/cli/commands/test_metadata.py`.
- **Modified tests:** `tests/component/db/test_instrument_metadata_repository.py` (`list_by_status` round-trip).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.2] — story statement + 2 acceptance criteria; FR21
- [Source: _bmad-output/planning-artifacts/epics.md FR21/FR24] — `metadata` subcommand family (unresolved now, coverage in 3.4)
- [Source: src/db/repositories/instrument_metadata_repository_sync.py:72-83] — sync repo `get_by_ticker` idiom to mirror for `list_by_status`
- [Source: src/db/models/instrument_metadata.py:59-71] — `resolution_status` enum column + `ix_instrument_metadata_resolution_status` index
- [Source: src/services/metadata/providers/fmp_provider.py:53-118] — `_to_domain` (asset_type always set) vs `_degraded` (asset_type None) — the reason discriminator
- [Source: src/services/metadata/venue_map.py:23-43] — `normalize_venue` → `None` for blank/ambiguous/unmapped (label not persisted)
- [Source: src/cli/commands/report.py:19-52] — `@click.group()` + subcommand + try/except console idiom
- [Source: src/cli/main.py:6-42] — command-group registration
- [Source: src/db/session_sync.py:97-135] — `get_sync_session` (raises RuntimeError when unconfigured)
- [Source: tests/component/db/test_instrument_metadata_repository.py:36-70] — `sync_session` fixture + `_make_metadata` idiom
- [Source: tests/unit/cli/commands/test_import_data.py:1-14] — CliRunner + patch idiom for CLI unit tests

### Open questions (non-blocking — proceed with the documented default)
1. **Reason precision.** Default: two-way derived reason (`unknown to FMP` vs `venue label blank/ambiguous/unmapped`) from `asset_type`. Storing the raw FMP `exchange` label for exact three-way reasons is a schema change — deferred/out of scope. Flag if the team wants exact-label fidelity.
2. **Output format.** Default: Rich table (Ticker/Provider/Reason) + count + overrides hint. A machine-readable `--format csv` that directly emits `ticker,venue`-shaped rows could seed `venue_overrides.csv`, but that overlaps Story 3.3's file ownership — left out to keep the boundary clean. Flag if a plain/CSV dump is wanted now.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/services/metadata/test_unresolved_report.py tests/unit/cli/commands/test_metadata.py tests/component/db/test_instrument_metadata_repository.py` — 22 passed (3 reason + 6 CLI + repo round-trip incl. 2 new `list_by_status`).
- `uv run pytest tests/unit tests/component` — 1858 passed, 16 skipped (pre-existing report-command skips), no regressions.
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found in 346 source files.

### Completion Notes List

- **AC1** — `SyncInstrumentMetadataRepository.list_by_status(status)` (indexed `where` on `resolution_status`, ordered by ticker) + `metadata unresolved` CLI list every `VENUE_UNRESOLVED` row with provider and a derived reason. `unresolved_reason` (pure) discriminates the two FMP paths via `asset_type` (`None` ⇒ `_degraded`/unknown; set ⇒ profile found, label blank/ambiguous/unmapped).
- **AC2** — Rendered as a Rich table (Ticker/Provider/Reason) with a total-count line, deterministic ticker order, and a `venue_overrides.csv` footer hint (basis for Story 3.3).
- **AC3** — Empty result prints "✓ All venues resolved — no unresolved-venue tickers." with exit 0 (no empty table).
- **AC4** — DB-unconfigured (`get_sync_session` raises `RuntimeError`, or `DatabaseConnectionError`) prints a single yellow warning and returns cleanly (no traceback), mirroring the existing CLI idiom.
- **Design** — new `metadata` Click group (no prior one existed; Story 3.4 `coverage` will join it); reason derivation placed in `src/services/metadata/unresolved_report.py` as a pure, unit-testable primitive. Reason is a deliberate two-way classification — the raw FMP `exchange` label is never persisted, so exact blank-vs-ambiguous fidelity is out of scope (documented open question #1).
- **Scope respected** — no override load/merge (3.3), no coverage count/gate (3.4), no exclude/flag (3.5), no new columns/migrations/settings, no async/web path, no changes to provider/venue-map/resolution service.

### Change Log

| Date | Change |
|---|---|
| 2026-07-13 | Story 3.2 drafted (SM). Status → ready-for-dev. |
| 2026-07-13 | Implemented `list_by_status`, pure `unresolved_reason`, `metadata unresolved` CLI + group registration; unit + component tests (TDD). Gates green (ruff, mypy, 1858 pass). Status → review. |
| 2026-07-13 | Code review (3 adversarial layers). Applied 2 patches (query-time DB-error graceful catch, Rich-markup escaping) + tightened 3 weak test assertions; documented 1 cross-provider limitation. 1864 unit+component pass, ruff+mypy clean. Status → done. |

### File List

- `src/db/repositories/instrument_metadata_repository_sync.py` (modified — `list_by_status`, `ResolutionStatus` import)
- `src/services/metadata/unresolved_report.py` (new — pure `unresolved_reason`)
- `src/cli/commands/metadata.py` (new — `metadata` group + `unresolved` subcommand + `_render_unresolved`)
- `src/cli/main.py` (modified — register `metadata` group)
- `tests/unit/services/metadata/test_unresolved_report.py` (new — reason derivation)
- `tests/unit/cli/commands/test_metadata.py` (new — CLI + render + registration)
- `tests/component/db/test_instrument_metadata_repository.py` (modified — `list_by_status` round-trip + empty)
