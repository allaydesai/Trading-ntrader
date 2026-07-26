# Story 3.4: Venue Coverage Report & Completeness Gate

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a deterministic `metadata coverage` CLI report plus a completeness gate,
so that I can answer "is any ticker missing a venue?" authoritatively, and the system refuses to declare the ETF universe complete until venue coverage is 100% — with no venue ever inferred or guessed to satisfy the gate.

## Acceptance Criteria

1. **Given** the `metadata coverage` CLI subcommand, **When** it runs, **Then** it reports current venue coverage (a per-status breakdown + a coverage percentage) and the exact count of `resolution_status = VENUE_UNRESOLVED` rows, backed by a single indexed grouped-count query over `ix_instrument_metadata_resolution_status` (O(1) w.r.t. row count — no full-table scan, no per-row Python loop over the store).
2. **Given** any ticker still has `resolution_status = VENUE_UNRESOLVED`, **When** the completeness gate is evaluated, **Then** the gate reports **FAIL** (the ETF universe is NOT marked complete) and no venue is inferred or guessed to satisfy it; with `metadata coverage --gate`, the process exits non-zero (exit code 1) so the gate is scriptable in CI/automation.
3. **Given** 0 rows with `resolution_status = VENUE_UNRESOLVED`, **When** the completeness gate is evaluated, **Then** the gate reports **PASS** (venue coverage = 100%); with `metadata coverage --gate` the process exits 0.
4. **Given** the metadata DB is unconfigured (`DATABASE_URL` unset) or unreachable, **When** the subcommand runs, **Then** it degrades gracefully with a single clear warning (no traceback), mirroring the existing `metadata unresolved` DB-unconfigured handling. Under `--gate` a DB that cannot be evaluated exits non-zero (the gate is NOT silently treated as passing).

## Tasks / Subtasks

- [x] **Task 1: Repository — grouped count by resolution status** (AC: #1) — *write the test in `tests/component/db/test_instrument_metadata_repository.py` FIRST (TDD Red→Green)*
  - [x] Add `count_by_status(self) -> dict[ResolutionStatus, int]` to `src/db/repositories/instrument_metadata_repository_sync.py`: `select(InstrumentMetadata.resolution_status, func.count()).group_by(InstrumentMetadata.resolution_status)`, returning `{status: count for status, count in rows}`. The `group_by` on the indexed `resolution_status` column is served by `ix_instrument_metadata_resolution_status` — a single grouped index scan, O(distinct statuses) = O(1) w.r.t. row count, NOT a full-table scan and NOT a Python-side count. Statuses with no rows are simply absent from the dict (callers use `.get(status, 0)`). [Source: src/db/repositories/instrument_metadata_repository_sync.py:87-111 (`list_by_status` idiom); src/db/models/instrument_metadata.py:71 (index)]
  - [x] Import `func` from `sqlalchemy` at module top (already imports `select`). Translate `OperationalError → DatabaseConnectionError` inside the method (mirror `list_by_status`/`upsert`) so the CLI can degrade gracefully. Keep the function `< 50` lines, fully return-type-annotated (F821 gate). [Source: src/db/repositories/instrument_metadata_repository_sync.py:108-111]
  - [x] Do **not** touch the async repository — this gate is a sync CLI path only; no web/API consumer exists in this story.

- [x] **Task 2: Pure coverage aggregate** (AC: #1, #2, #3) — *tests FIRST (TDD)*
  - [x] Add a frozen dataclass `VenueCoverage` (plus a `from_counts` classmethod) in a new `src/services/metadata/coverage_report.py` — the domain-facing coverage math lives beside the other metadata services (like `unresolved_report.py`), pure and unit-testable without Click or a DB. Fields: `resolved: int`, `venue_unresolved: int`, `unresolved: int`.
    - `from_counts(counts: dict[ResolutionStatus, int]) -> VenueCoverage`: read each status via `counts.get(status, 0)` (absent ⇒ 0).
    - `decided` property = `resolved + venue_unresolved` (the rows resolution has reached a venue verdict on — the coverage denominator).
    - `total` property = `resolved + venue_unresolved + unresolved` (all metadata rows).
    - `coverage_pct` property = `100.0` when `decided == 0` (vacuously complete — nothing undecided), else `resolved / decided * 100`. Denominator is `decided`, NOT `total`, so the gate identity holds exactly: `is_complete ⟺ coverage_pct == 100.0` (AC3's "(venue coverage = 100%)"). `UNRESOLVED` rows (never-attempted) are surfaced separately by the CLI, not folded into the percentage — folding them would break the gate identity and misreport "not attempted" as "failed to resolve venue". [Source: epics.md Story 3.4 AC3; Dev Notes "Why the denominator is `decided`, not `total`"]
    - `is_complete` property (the gate) = `venue_unresolved == 0`. This is the ONLY gate condition (AC2/AC3 key strictly on `VENUE_UNRESOLVED`). Do NOT gate on `UNRESOLVED` — a never-attempted row is a resolution-not-run signal, not a venue that "couldn't be resolved"; gating on it would conflate the two states the enum deliberately separates. [Source: src/models/instrument_metadata.py:36-46; epics.md FR24]
  - [x] Keep the aggregate pure (no I/O, no clock), fully annotated, `< 100` lines for the class. Use `from src.models.instrument_metadata import ResolutionStatus`.

- [x] **Task 3: CLI — `metadata coverage` subcommand + `--gate` flag** (AC: #1, #2, #3, #4) — *tests FIRST (TDD, CliRunner)*
  - [x] Add `@metadata.command("coverage")` with a `--gate/--no-gate` boolean flag (default `--no-gate`) to `src/cli/commands/metadata.py`. Docstring: report venue coverage + completeness verdict; `--gate` makes the process exit non-zero when the universe is not complete (for CI). [Source: src/cli/commands/metadata.py:41-59 (`unresolved` idiom); src/cli/commands/reproduce.py:58 (`sys.exit(1)` precedent)]
  - [x] The command body:
    1. Open a sync session via `get_sync_session()` inside a `try/except (RuntimeError, DatabaseConnectionError, SQLAlchemyError)` that prints a single yellow warning line (mirror `unresolved`). **On the gate path (`--gate`) a DB error must NOT be treated as passing:** after printing the warning, `sys.exit(2)` (distinct from the gate-FAIL code 1) so automation sees "could not evaluate" ≠ "passed". Without `--gate`, return cleanly (exit 0) after the warning (AC4). [Source: src/cli/commands/metadata.py:54-58]
    2. `counts = SyncInstrumentMetadataRepository(session).count_by_status()` → `coverage = VenueCoverage.from_counts(counts)`.
    3. Delegate rendering to a pure helper `_render_coverage(coverage) -> None`: a Rich `Table` (columns **Status**, **Count**) with one row per `ResolutionStatus` (RESOLVED / VENUE_UNRESOLVED / UNRESOLVED, showing `.get→0`), a total line, a `Venue coverage: {pct:.1f}% ({resolved}/{decided} decided)` line, an explicit `Unresolved venues (VENUE_UNRESOLVED): {n}` line, a `Not yet attempted (UNRESOLVED): {n}` line when `> 0`, and a bold verdict line: `[green]✓ COMPLETE — venue coverage 100% (gate PASS)[/green]` or `[red]✗ INCOMPLETE — {n} ticker(s) VENUE_UNRESOLVED (gate FAIL)[/red]`. When `total == 0`, print a clear "no metadata rows yet — nothing to gate" note (gate still PASS vacuously, but flagged). (AC1, AC2, AC3)
    4. After rendering, if `--gate` and `not coverage.is_complete`: `sys.exit(1)` (AC2). Otherwise return (exit 0) (AC3). Keep the render side-effect-free of exit logic — the command decides the exit code, the helper only prints (unit-testable without SystemExit). [Source: CLAUDE.md size limits; Dev Notes "Gate exit codes"]
  - [x] Keep every function `< 50` lines and the file `< 500`. `sys` import at module top (add `import sys`). No new settings, no migration, no network/provider call. [Source: CLAUDE.md Foundational Rules]

- [x] **Task 4: Unit tests** (AC: #1, #2, #3, #4)
  - [x] **Coverage aggregate (`tests/unit/services/metadata/test_coverage_report.py`, `@pytest.mark.unit`):**
    - All resolved (`{RESOLVED: 10}`) → `coverage_pct == 100.0`, `is_complete is True`, `decided == 10`, `total == 10`.
    - Mixed with unresolved venue (`{RESOLVED: 8, VENUE_UNRESOLVED: 2}`) → `coverage_pct == 80.0`, `is_complete is False`, `venue_unresolved == 2`.
    - `UNRESOLVED` excluded from pct but counted in total (`{RESOLVED: 5, UNRESOLVED: 5}`) → `coverage_pct == 100.0` (decided=5), `is_complete is True` (no VENUE_UNRESOLVED), `total == 10`, `unresolved == 5` — proves never-attempted rows don't fail the gate or dilute the percentage.
    - Empty (`{}`) → `total == 0`, `decided == 0`, `coverage_pct == 100.0`, `is_complete is True` (vacuous). Absent statuses default to 0.
    - Gate identity: for a case with `VENUE_UNRESOLVED == 0`, assert `is_complete is True and coverage_pct == 100.0`; for `VENUE_UNRESOLVED > 0`, assert `is_complete is False and coverage_pct < 100.0`.
  - [x] **CLI (`tests/unit/cli/commands/test_metadata.py`, `CliRunner`, `@pytest.mark.unit`):** patch `get_sync_session` + `SyncInstrumentMetadataRepository` (mirror `_patch_session_and_repo`). Set `mock_repo.return_value.count_by_status.return_value = {...}`.
    - **Complete, no flag:** counts `{RESOLVED: 3}` → exit 0, output contains `100.0%`, `COMPLETE`, `gate PASS`.
    - **Incomplete, no flag:** counts `{RESOLVED: 2, VENUE_UNRESOLVED: 1}` → exit 0 (report-only, no flag), output contains `INCOMPLETE`, `gate FAIL`, `66.7%`, and `1 ticker(s) VENUE_UNRESOLVED`.
    - **Incomplete + `--gate`:** same counts + `["coverage", "--gate"]` → **exit code 1**, output still shows FAIL (AC2).
    - **Complete + `--gate`:** `{RESOLVED: 3}` + `--gate` → exit 0 (AC3).
    - **DB unconfigured, no flag:** `get_sync_session.__enter__` raises `RuntimeError(...)` → exit 0, `result.exception is None`, output contains `Metadata DB not available` (AC4).
    - **DB unconfigured + `--gate`:** same → **exit code 2** (could-not-evaluate ≠ pass), warning present, no traceback (AC4).
    - **DB query error + `--gate`:** `count_by_status.side_effect = OperationalError(...)` → exit code 2, warning present (query-time failure under the lazy session).
  - [x] **Render helper (`TestRenderCoverage`, `capsys`, no CliRunner):** direct `_render_coverage(VenueCoverage.from_counts({RESOLVED:2, VENUE_UNRESOLVED:1}))` asserts the status counts, `66.7%`, and the FAIL verdict text; a complete case asserts PASS; an empty case asserts the "no metadata rows" note.

- [x] **Task 5: Component test — real sync repo grouped count** (AC: #1)
  - [x] In `tests/component/db/test_instrument_metadata_repository.py` add `test_count_by_status_groups_and_counts`: seed via `_make_metadata`/`upsert` a mix (e.g. 2× `RESOLVED`, 1× `VENUE_UNRESOLVED`, 1× `UNRESOLVED`), call `count_by_status()`, assert `== {ResolutionStatus.RESOLVED: 2, ResolutionStatus.VENUE_UNRESOLVED: 1, ResolutionStatus.UNRESOLVED: 1}`. Add `test_count_by_status_empty_returns_empty_dict`: no rows → `{}`. Reuse the file's `sync_session` fixture + `_CREATE_TABLE_SQL`. [Source: tests/component/db/test_instrument_metadata_repository.py:236-279 (`list_by_status` round-trip precedent)]

- [x] **Task 6: Verify** (AC: all)
  - [x] `uv run ruff check .` clean — mind the F401/F821 import gate (new `func`, `sys`, `VenueCoverage` imports).
  - [x] `uv run mypy .` — no **new** errors vs the known baseline (`reference_mypy_baseline_debt`: 6 pre-existing in `ui/explorer`, `ui/backtests` + 2 tests). Fully annotate new functions/dataclass.
  - [x] `make test-unit` green (coverage aggregate + CLI + render deltas, no regressions). `make test-component` green for the new repo test.
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`.
  - [x] Smoke: covered by `CliRunner` tests (help/registration + exit codes 0/1/2 + DB-unconfigured warning); the harness permission mode blocks the live `python -m src.cli.main` subprocess, so the identical command paths are exercised via `CliRunner.invoke` instead.

### Review Findings

Adversarial review (Blind Hunter · Edge Case Hunter · Acceptance Auditor). Acceptance Auditor: **PASS** — all 4 ACs satisfied, no scope violations, size limits respected. 2 patches applied + re-verified; 1 dismissed.

- [x] [Review][Patch] Coverage line rounds to "100.0%" while the verdict is gate FAIL (blind+edge, Med) — `f"{coverage_pct:.1f}"` collapses 99.95–99.99…% onto "100.0%", so e.g. `{RESOLVED: 9999, VENUE_UNRESOLVED: 1}` printed `Venue coverage: 100.0%` right next to `✗ INCOMPLETE … (gate FAIL)`. `is_complete` (exact float) drove the correct exit code, so no functional gate impact — but the operator/CI line was self-contradictory. Fixed: `_render_coverage` clamps the *displayed* percentage to `99.9%` when `not is_complete` and the value would round to ≥100.0, so the number can never read 100.0% while the verdict is FAIL. Added a render test at `RESOLVED=9999, VENUE_UNRESOLVED=1`. [src/cli/commands/metadata.py:_render_coverage]
- [x] [Review][Patch] Un-caveated green "COMPLETE / 100%" when `decided == 0` but `total > 0` (blind+edge, Low) — the "vacuous PASS" transparency note keyed on `total == 0`, so a store holding only never-attempted `UNRESOLVED` rows (resolution never ran) rendered `Venue coverage: 100.0% (0/0 decided)` + green `✓ COMPLETE` with no caveat, even though those tickers have no venue. Per-AC the gate correctly keys strictly on `VENUE_UNRESOLVED` (Auditor confirmed AC2/AC3 compliant; open-question #1 default), and the raw `UNRESOLVED` count is already surfaced — this was purely a transparency gap in the code's own caveat. Fixed: the note now also fires on `decided == 0 and total > 0` ("resolution has not run on any row; PASS is vacuous"). Gate semantics unchanged. Added a render test for the pure-`UNRESOLVED` case. [src/cli/commands/metadata.py:_render_coverage]
- [x] [Review][Dismiss] `count_by_status` dict keys deserialize as `str` not enum → all `.get(ResolutionStatus.X)` miss → gate silently a no-op (blind, Low-conditional) — false positive. The `resolution_status` column is a `sa.Enum(ResolutionStatus)`, whose result processor maps the stored value back to the enum member on both SQLite and Postgres; the component test `test_count_by_status_groups_and_counts` asserts the exact enum-keyed map over real SQLite and passes. Doubly robust: `ResolutionStatus` is a `str`-enum with `name == value`, so even a raw-string key would satisfy `.get(ResolutionStatus.X)` (equal value and equal hash). No change needed.

## Dev Notes

### The core idea (why this story exists)
Epic 3 gates ETF-universe completion on **100% venue coverage with no inferred venues**. Story 3.1 made the FMP-resolved venue authoritative; 3.2 gave the operator the worklist (`metadata unresolved`); 3.3 let them fix it (`venue_overrides.csv` merge). Story 3.4 is the **authoritative answer + the hard gate**: a single indexed count that says "is any ticker missing a venue?" and a PASS/FAIL verdict the operator (and CI, via `--gate`) can trust. The gate never guesses a venue to make itself pass — a `VENUE_UNRESOLVED` row is a FAIL, full stop (ADR-6 conservative venue map: ambiguous labels stay unresolved rather than get guessed). [Source: epics.md Epic 3 intro + FR24; architecture.md venue-correctness risk]

### Why the denominator is `decided`, not `total`
The enum has three states (`src/models/instrument_metadata.py:36-46`):
- **RESOLVED** — venue known (backtestable).
- **VENUE_UNRESOLVED** — resolution ran, venue could not be determined (the gate blocker).
- **UNRESOLVED** — resolution never attempted for this row (transient; should not persist post-import).

`coverage_pct = resolved / (resolved + venue_unresolved)` deliberately excludes `UNRESOLVED`, so the gate identity holds **exactly**: `is_complete ⟺ coverage_pct == 100.0` (AC3 states 0 `VENUE_UNRESOLVED` ⟹ "venue coverage = 100%"). Folding `UNRESOLVED` into the denominator would (a) make coverage < 100% even with 0 `VENUE_UNRESOLVED`, contradicting AC3, and (b) misreport "resolution not yet run" as "venue could not be resolved" — two states the schema keeps separate on purpose. `UNRESOLVED` rows are still **surfaced** on their own report line (transparency), just kept out of the ratio and the gate. Post-import they're ~always 0, so in practice `decided == total`. [Source: epics.md Story 3.4 AC1–AC3]

### Gate exit codes (`--gate`)
- **exit 0** — universe complete (0 `VENUE_UNRESOLVED`), or report-only run (`--no-gate`, always 0 regardless of verdict).
- **exit 1** — `--gate` and universe INCOMPLETE (≥1 `VENUE_UNRESOLVED`). The scriptable FAIL.
- **exit 2** — `--gate` and the DB could not be evaluated (unset/unreachable). A gate that can't read the store must **not** report PASS — an unevaluable gate is distinct from a passing one, so CI treats it as an error, not a green light. Without `--gate`, DB-unconfigured is a plain warning + exit 0 (mirrors `metadata unresolved` AC4). [Source: src/cli/commands/metadata.py:54-58; src/cli/commands/reproduce.py:58 (`sys.exit(1)`); CLAUDE.md "never touch live data" — this is read-only over the local cache]

### The seam (data + control flow)
```
instrument_metadata (cache table; populated by Epic-1 resolution + Story 3.1 import + Story 3.3 overrides)
        │  grouped indexed COUNT(*) GROUP BY resolution_status   ← ix_instrument_metadata_resolution_status
        ▼
  count_by_status()  →  {RESOLVED: n, VENUE_UNRESOLVED: m, UNRESOLVED: k}
        │
  VenueCoverage.from_counts(...)   ← pure: coverage_pct, is_complete (gate)
        ▼
  metadata coverage [--gate]  (CLI, this story)
        │  _render_coverage: Rich table + pct + PASS/FAIL verdict
        ▼  exit 0 / 1 / 2   (only under --gate)
```
Read-only over the local cache — no network, no provider call, no write. [Source: architecture.md two-table boundary; src/db/models/instrument_metadata.py:59-71]

### Scope boundaries (do NOT do here)
- **No venue inference/guessing** — a `VENUE_UNRESOLVED` row is a FAIL; never fabricate a venue to pass the gate (AC2, ADR-6).
- **No exclude/flag/keep-bars enumeration of the backtestable universe** — that is Story 3.5. This story counts + gates; it does not enumerate or mutate which tickers enter a backtest.
- **No override load/merge** — that is Story 3.3 (`apply-overrides`, already done). This story reads the post-merge state.
- **No new DB columns / migrations / settings** — the index and `resolution_status` enum already exist.
- **No async/web endpoint** — CLI sync path only. Do not touch the async repo or add an API route.
- **No changes to the FMP provider, venue map, or resolution service** — all `done`; this story only *reads* their persisted output.

### Design decision — grouped count vs. per-status count
A single `COUNT(*) GROUP BY resolution_status` returns all three counts in one indexed query — strictly cheaper than three separate `COUNT WHERE status = ?` calls, and it yields the whole coverage picture (numerator, denominator, and the `UNRESOLVED` line) at once. Both forms are "backed by the index"; the grouped form is the natural fit for a *coverage* report (which needs the breakdown), where 3.2's `list_by_status` was the natural fit for a *worklist*. [Source: src/db/models/instrument_metadata.py:71]

### Design decision — verdict math in `services/metadata`, gate exit in the CLI
`VenueCoverage` (pure aggregate: pct + `is_complete`) lives in `src/services/metadata/coverage_report.py` beside `unresolved_report.py`, unit-testable with no Click/DB. The CLI command owns only the exit-code decision (`sys.exit`) and rendering, so the gate *logic* is tested without `SystemExit` and the exit *wiring* is tested via `CliRunner`. Matches the project's "pure logic → unit tier" heuristic and the 3.2 precedent (`unresolved_reason` pure; CLI thin). [Source: CLAUDE.md Decision Heuristics; src/services/metadata/unresolved_report.py]

### Previous-story intelligence
- **Story 3.2 (done)** added the `metadata` Click group + `list_by_status` (indexed filter). This story adds `coverage` to the same group and `count_by_status` (indexed grouped count) beside it — same index, same graceful-degradation idiom (`(RuntimeError, DatabaseConnectionError, SQLAlchemyError)` → yellow warning; query-time failures surface inside the lazy session, so the `except` must wrap the query, not just the `with`-entry). [Source: 3-2 story Review Findings; src/cli/commands/metadata.py:49-58]
- **Story 3.3 (done)** made overrides flip rows to `RESOLVED`, so after `apply-overrides` a re-run of `metadata coverage` should show coverage climbing toward 100%. The gate reads the post-merge truth. [Source: src/db/repositories/instrument_metadata_repository_sync.py:113-147]
- **`ix_instrument_metadata_resolution_status` (Story 1.2, done)** indexes `resolution_status` — the grouped count is an index scan. [Source: src/db/models/instrument_metadata.py:71]

### Testing standards
- **Unit tier** for the pure `VenueCoverage` aggregate, the `_render_coverage` helper, and the CLI command (`CliRunner` with `get_sync_session`/repo patched — no DB, no Nautilus). **Component tier** for the real-repo `count_by_status` grouped round-trip over the in-memory SQLite fixture. No integration/e2e — no engine, no C extensions. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — Red first for `count_by_status`, `VenueCoverage`, and the CLI command. [Source: development-principles.md]
- Build ORM rows in component tests via the `_make_metadata` kwargs idiom; keep `@pytest.mark.unit` / component markers consistent with siblings.

### Project Structure Notes
- **New:** `src/services/metadata/coverage_report.py` (`VenueCoverage`).
- **Modified:** `src/cli/commands/metadata.py` (`coverage` subcommand + `_render_coverage` + `import sys`), `src/db/repositories/instrument_metadata_repository_sync.py` (`count_by_status`, `func` import).
- **New tests:** `tests/unit/services/metadata/test_coverage_report.py`.
- **Modified tests:** `tests/unit/cli/commands/test_metadata.py` (`coverage` CLI + render + registration), `tests/component/db/test_instrument_metadata_repository.py` (`count_by_status` round-trip).

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-3.4] — story statement + 3 acceptance criteria; FR24
- [Source: _bmad-output/planning-artifacts/epics.md:127] — "B-tree index powers the O(1) completeness gate"
- [Source: src/db/repositories/instrument_metadata_repository_sync.py:87-111] — `list_by_status` (indexed filter + OperationalError translation) to mirror for `count_by_status`
- [Source: src/db/models/instrument_metadata.py:59-71] — `resolution_status` enum column + `ix_instrument_metadata_resolution_status` index
- [Source: src/models/instrument_metadata.py:36-46] — the three `ResolutionStatus` states
- [Source: src/cli/commands/metadata.py:41-77] — `metadata` group + `unresolved` command + render-helper idiom to mirror
- [Source: src/cli/commands/reproduce.py:58] — `sys.exit(1)` precedent for non-zero CLI exit
- [Source: tests/unit/cli/commands/test_metadata.py:42-49] — `_patch_session_and_repo` idiom for CLI unit tests
- [Source: tests/component/db/test_instrument_metadata_repository.py:236-294] — `list_by_status` round-trip precedent for the grouped-count test

### Open questions (non-blocking — proceed with the documented default)
1. **Coverage denominator.** Default: `decided = resolved + venue_unresolved` (excludes never-attempted `UNRESOLVED`), so `is_complete ⟺ coverage_pct == 100%` per AC3. `UNRESOLVED` is surfaced on its own line but not in the ratio. Flag if the team wants `total` as the denominator (would decouple the gate identity from 100%).
2. **DB-unevaluable exit code under `--gate`.** Default: exit 2 (distinct from FAIL=1), so CI never reads "couldn't reach DB" as PASS. Flag if a single non-zero code (1 for any non-pass) is preferred.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run pytest tests/unit/services/metadata/test_coverage_report.py tests/unit/cli/commands/test_metadata.py tests/component/db/test_instrument_metadata_repository.py` — 50 passed (6 coverage-aggregate + CLI/render deltas + 3 new `count_by_status` repo round-trips).
- `uv run pytest tests/unit tests/component` — 1908 passed, 16 skipped (pre-existing report-command skips), no regressions.
- `uv run ruff check .` — All checks passed. `uv run mypy .` — Success: no issues found in 350 source files.

### Completion Notes List

- **AC1** — `SyncInstrumentMetadataRepository.count_by_status()` (single indexed `COUNT(*) GROUP BY resolution_status`, O(distinct statuses)) + `metadata coverage` CLI report a per-status breakdown, a coverage percentage, and the exact `VENUE_UNRESOLVED` count. `VenueCoverage` (pure) does the math.
- **AC2** — Any `VENUE_UNRESOLVED` row → verdict INCOMPLETE / "gate FAIL"; no venue is inferred. `metadata coverage --gate` exits 1 (scriptable). Report-only (`--no-gate`) always exits 0 regardless of verdict.
- **AC3** — 0 `VENUE_UNRESOLVED` → COMPLETE / "gate PASS", coverage 100.0% (denominator is `decided`, so the gate identity `is_complete ⟺ coverage_pct == 100` holds exactly); `--gate` exits 0.
- **AC4** — DB unset/unreachable → single yellow warning, no traceback. Under `--gate` an unevaluable gate exits 2 (distinct from FAIL=1) so CI never reads "unreachable" as "passed"; without `--gate` it exits 0.
- **Design** — coverage math (`VenueCoverage`) placed in `src/services/metadata/coverage_report.py` (pure, beside `unresolved_report.py`); the CLI owns only rendering + the exit-code decision. `UNRESOLVED` (never-attempted) rows are surfaced on their own report line but excluded from the ratio and the gate — kept distinct from `VENUE_UNRESOLVED` (couldn't-resolve).
- **Scope respected** — no venue inference, no exclude/flag/keep-bars enumeration (3.5), no override load/merge (3.3), no new columns/migrations/settings, no async/web path, no changes to provider/venue-map/resolution service. Read-only over the local cache table.
- **Smoke** — the harness permission mode blocks launching `python -m src.cli.main`; the identical command paths (help/registration, exit 0/1/2, DB-unconfigured) are covered by `CliRunner` unit tests.

### Change Log

| Date | Change |
|---|---|
| 2026-07-13 | Story 3.4 drafted (SM). Status → ready-for-dev. |
| 2026-07-13 | Implemented `count_by_status` (indexed grouped count), pure `VenueCoverage` aggregate, `metadata coverage [--gate]` CLI + `_render_coverage`; unit + component tests (TDD). Gates green (ruff, mypy, 1908 pass). Status → review. |
| 2026-07-13 | Code review (3 adversarial layers; Auditor PASS on all 4 ACs). Applied 2 patches (display-percent clamp so it can't read 100.0% while FAIL; vacuous-PASS caveat now fires on `decided==0` not just `total==0`) + 2 render tests; dismissed 1 false positive (enum-key round-trip, disproven by component test). 1910 unit+component pass, ruff+mypy clean. Status → done. |

### File List

- `src/db/repositories/instrument_metadata_repository_sync.py` (modified — `count_by_status`, `func` import)
- `src/services/metadata/coverage_report.py` (new — pure `VenueCoverage` aggregate)
- `src/cli/commands/metadata.py` (modified — `coverage` subcommand + `_render_coverage` + `sys`/`VenueCoverage` imports)
- `tests/unit/services/metadata/test_coverage_report.py` (new — coverage/gate math)
- `tests/unit/cli/commands/test_metadata.py` (modified — `coverage` CLI + render + registration)
- `tests/component/db/test_instrument_metadata_repository.py` (modified — `count_by_status` round-trip + empty + error translation)
