# Story 1.6: Per-Import Resolution Summary Reporting

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a per-import resolution summary,
so that I can see at a glance how many tickers resolved cleanly, had descriptive gaps, or have unresolved venues.

## Acceptance Criteria

1. **Given** a completed batch resolution, **When** the summary is produced, **Then** a `ResolutionSummary` with the fixed fields `resolved`, `descriptive_gaps`, and `venue_unresolved` is returned **And** the counts accurately reflect the `resolution_status` of the processed tickers.
2. **Given** the summary, **When** an import completes, **Then** it is surfaced in the CLI output **And** emitted via structlog with fields `ticker`, `provider`, `resolution_status`, `error`.

## Tasks / Subtasks

- [x] **Task 1: `ResolutionSummary.from_results()` — the counting logic** (AC: #1) — *write the tests in `test_instrument_metadata.py` FIRST (TDD Red→Green)*
  - [x] Add a classmethod `from_results(cls, results: Iterable["InstrumentMetadata"]) -> "ResolutionSummary"` to the **existing** `ResolutionSummary` model in `src/models/instrument_metadata.py`. Do NOT create a new module — the model's own docstring already says "the counting logic that populates it lives in Story 1.6", so this is its designated home. [Source: src/models/instrument_metadata.py:104-120; architecture.md:445-446]
  - [x] **Counting semantics — two mutually-exclusive status counts + one orthogonal gap count:**
    - `resolved` = count of results with `resolution_status == ResolutionStatus.RESOLVED`.
    - `venue_unresolved` = count of results with `resolution_status == ResolutionStatus.VENUE_UNRESOLVED`.
    - `descriptive_gaps` = count of results where **any** descriptive field is `NA_SENTINEL`. This is an **orthogonal** dimension: it overlaps the status counts (a `RESOLVED` ticker with `sector == "N/A"` is counted in BOTH `resolved` and `descriptive_gaps`). This matches FR6 ("count resolved automatically, count with descriptive gaps, count with unresolved venue" — three independent metrics) and the model's field docstrings. Do NOT make `descriptive_gaps` mutually exclusive with `resolved`. [Source: epics.md FR6; src/models/instrument_metadata.py:110-119]
  - [x] **Descriptive fields are EXACTLY the five that can hold `NA_SENTINEL`:** `company_name`, `sector`, `industry`, `country`, `currency`. `venue` is excluded (never the sentinel — it is a code or `None`); `asset_type` and `ipo_date` are excluded (typed; absent → `None`, never `NA_SENTINEL`). Reference the canonical `NA_SENTINEL` import — never the literal `"N/A"`. [Source: src/models/instrument_metadata.py:84-93,95-101; fmp_provider.py:87-93 (`_descriptive`)]
  - [x] **`UNRESOLVED` status is counted in neither `resolved` nor `venue_unresolved`.** Post-resolution every result is `RESOLVED` or `VENUE_UNRESOLVED` (the provider/`_degraded` only ever emit those two), so `UNRESOLVED` should not appear in a results list — but `from_results` must not crash or miscount if it does. A row that is `UNRESOLVED` with descriptive gaps still increments `descriptive_gaps`. [Source: src/models/instrument_metadata.py:43-45; fmp_provider.py:80-82,99]
  - [x] Keep it a pure, side-effect-free aggregation (no logging, no I/O) — iterate once, return the populated `ResolutionSummary`. Consider a small private helper `_has_descriptive_gap(md) -> bool` (module-level or staticmethod) so the comprehension stays readable and `< 50` lines.

- [x] **Task 2: Per-import reporting helpers in `import_reporting.py`** (AC: #2) — *tests FIRST (TDD)*
  - [x] Add helpers to the **existing** `src/cli/commands/import_reporting.py` (the architecture-designated MODIFIED file). Do NOT create `metadata.py` — that CLI command group is Story 3.2, and the import command that wires these helpers in is Story 2.7. This story delivers the reusable reporting surface only. [Source: architecture.md:489-492; epics.md#Story-2.7, #Story-3.2]
  - [x] Add `import structlog` and a module logger `logger = structlog.get_logger(__name__)` (repo convention — `import_reporting.py` is currently click+rich only). [Source: fmp_provider.py:23; instrument_metadata_service.py:31]
  - [x] `def build_resolution_summary_text(summary: ResolutionSummary) -> str` — plain-text block mirroring the existing `build_summary_text` style (title + `━` rule + aligned `Resolved:` / `Descriptive gaps:` / `Venue unresolved:` lines). Pure; returns a string, prints nothing. [Source: import_reporting.py:64-99]
  - [x] `def _print_resolution_summary(summary: ResolutionSummary) -> None` — Rich `Table(title="Resolution Summary")` with `Metric`/`Value` columns and the three rows, mirroring the existing `_print_summary` Rich idiom (reuse the module-level `console`). [Source: import_reporting.py:121-150]
  - [x] `def log_resolution_results(results: list[InstrumentMetadata]) -> None` — emit ONE structlog event per ticker (`event="metadata_resolution_outcome"`) with the four required fields: `ticker=r.ticker`, `provider=r.metadata_provider`, `resolution_status=r.resolution_status.value`, `error=None`. Use `logger.info`. This satisfies AC #2's per-ticker structlog contract. [Source: architecture.md:444; epics.md#Story-1.6 AC2]
    - **Why `error=None`:** the domain `InstrumentMetadata` carries no error attribute, so the per-ticker *outcome* line has no error string. Genuine per-ticker failure strings are already emitted by `InstrumentMetadataService.resolve_batch` (Story 1.5, `done`) as `metadata_resolution_failed` (`ticker`, `provider`, `error`). The `error` key is kept in this line for a stable four-field schema; do NOT fabricate a reason string from `resolution_status`. [Source: instrument_metadata_service.py:64-79]
  - [x] Import the **domain** `InstrumentMetadata` + `ResolutionSummary` from `src.models.instrument_metadata`. There is no ORM ambiguity in this file (it never touches the SQLAlchemy ORM), so no aliasing is needed here — but still import the domain model explicitly, not via the service. [Source: src/models/instrument_metadata.py:26-32]
  - [x] **Do NOT modify `InstrumentMetadataService` (Story 1.5, `done`)** or call `resolve_batch` from `import_reporting.py`. The helpers take an already-resolved `list[InstrumentMetadata]` (the output of `resolve_batch`) — the import pipeline (Story 2.4/2.7) owns the call chain. [Source: 1-5 story File List; epics.md#Story-2.4]

- [x] **Task 3: Unit tests — pure, no DB, no network, no Nautilus** (AC: #1, #2)
  - [x] **AC #1 counting** — extend the existing `TestResolutionSummary` class in `tests/unit/models/test_instrument_metadata.py` (do NOT create a new model-test module; `from_results` lives on the model already tested there). Module already has `pytestmark`/`@pytest.mark.unit` on its classes — match the established style. [Source: tests/unit/models/test_instrument_metadata.py:146-160]
    - Build small `InstrumentMetadata` fixtures and assert `from_results` counts. Cases:
      - **All clean RESOLVED** (no `N/A` fields) → `resolved=N`, `descriptive_gaps=0`, `venue_unresolved=0`.
      - **RESOLVED but with a descriptive gap** (e.g. `sector == NA_SENTINEL`, venue present) → counted in BOTH `resolved` AND `descriptive_gaps` (proves orthogonality — the key disambiguation).
      - **VENUE_UNRESOLVED degraded record** (all five descriptive fields `N/A`, `venue=None`) → `venue_unresolved += 1` AND `descriptive_gaps += 1`.
      - **Each descriptive field independently triggers a gap** — parametrize over `{company_name, sector, industry, country, currency}` so a future field-list edit can't silently regress; and assert that a `venue`-only/`asset_type`-only/`ipo_date`-only absence does NOT increment `descriptive_gaps`.
      - **Empty results** → all-zero summary (no crash).
      - **Mixed batch** (a few of each) → all three counts correct simultaneously.
      - Optionally a stray `UNRESOLVED` row → not in `resolved`/`venue_unresolved`, but its descriptive gaps still count.
  - [x] **AC #2 reporting** — new `tests/unit/cli/commands/test_import_reporting.py`. First body line `pytestmark = pytest.mark.unit` (a 1.4 review finding flagged missing unit markers — do not repeat). The dir `tests/unit/cli/commands/` already exists (has `test_import_data.py`); add `__init__.py` only if the sibling pattern requires it (it already has one). [Source: 1-5 story Dev Notes; tests/unit/cli/commands/]
    - `build_resolution_summary_text` → assert the returned string contains the three counts (e.g. `"Resolved:"`, the numbers).
    - `_print_resolution_summary` → call it (Rich prints to the module `console`); assert it does not raise. Optionally capture with `capsys`/Rich `Console(record=True)` if asserting content, but a smoke-call is sufficient for the table path.
    - `log_resolution_results` → monkeypatch the module `logger` (or use `structlog`'s testing capture) and assert one `info` call **per result** with exactly the four fields `ticker`, `provider`, `resolution_status`, `error` and the right values (mirror the logger-assertion idiom in `test_instrument_metadata_service.py`). Assert `resolution_status` is the `.value` string, not the enum. [Source: tests/unit/services/metadata/test_instrument_metadata_service.py (logger monkeypatch + structured-call assert)]

- [x] **Task 4: Verify** (AC: all)
  - [x] `make test-unit` green — new model tests + new `test_import_reporting.py` + full suite, no regressions (baseline **1034** unit tests after Story 1.5; now **1052** — +18). [Source: 1-5 story Debug Log]
  - [x] `make lint` clean — mind the F401/F821 import gate. New imports each used in the same edit that adds them: in the model — `Iterable` (from `collections.abc` or `typing`); in `import_reporting.py` — `structlog`, domain `InstrumentMetadata`, `ResolutionSummary`. [Source: CLAUDE.md "structural import gate"]
  - [x] `make typecheck` clean — `mypy` targets `src/core src/services` per the Makefile, so `src/cli/` and `src/models/` are NOT in the default typecheck set; still, fully annotated the new helpers (`-> str`, `-> None`, `list[InstrumentMetadata]`, `from_results(...) -> ResolutionSummary`) to match repo discipline and the import-gate F821 check. [Source: Makefile typecheck; CLAUDE.md]
  - [x] Size limits: files `< 500` lines, functions `< 50`, classes `< 100`, line length `≤ 100`. `import_reporting.py` is now 267 lines, `instrument_metadata.py` 163 — both well under 500; no line > 100. [Source: CLAUDE.md Foundational Rules]

### Review Findings

- [x] [Review][Patch] Quote `sprint-status.yaml` date scalar [_bmad-output/implementation-artifacts/sprint-status.yaml:38]

## Dev Notes

### Scope boundaries (do NOT do here)
- **Counting + reporting helpers only.** No CLI command/subcommand wiring (the import command that calls these is Story 2.7; `metadata coverage`/`metadata unresolved` are Story 3.2's new `metadata.py`). No `venue_overrides.csv` (Story 3.3). No unresolved-venue *report* — that is Story 3.2, despite the architecture file note bundling "resolution summary + unresolved-venue report" onto `import_reporting.py`; this story does only the **resolution summary** half. [Source: architecture.md:491-492; epics.md#Story-2.7, #Story-3.2, #Story-3.3]
- **Do NOT modify `InstrumentMetadataService`, `FMPMetadataProvider`, `FMPClient`, `venue_map.py`, the domain/ORM models' *existing* fields, or the repositories.** They are `done` (Stories 1.2–1.5). The only structural addition to a `done` file is the `from_results` classmethod on `ResolutionSummary` — which that file's docstring already reserves for this story. [Source: src/models/instrument_metadata.py:5-6,107-108; sprint-status.yaml:54-58]
- **No new DB columns, migrations, or settings.** Everything needed exists (the `ResolutionSummary` model from Story 1.2, the resolved `list[InstrumentMetadata]` from Story 1.5's `resolve_batch`).
- **No async variant.** Resolution + its reporting are the sync CLI import write-path. [Source: 1-5 story Dev Notes]

### What this story plugs into (the seam to Story 2.7)
```
import command (Story 2.4/2.7)                                  ← later consumer (NOT this story)
  results = InstrumentMetadataService.resolve_batch(tickers)    ← Story 1.5 (done) → list[InstrumentMetadata]
  summary = ResolutionSummary.from_results(results)             ← THIS STORY (counting, AC #1)
  log_resolution_results(results)                               ← THIS STORY (per-ticker structlog, AC #2)
  _print_resolution_summary(summary)                            ← THIS STORY (CLI surface, AC #2)
```
Story 2.7 AC: "it incorporates the Epic 1 `ResolutionSummary` (resolved / descriptive-gaps / venue-unresolved counts)." So 1.6 builds the parts; 2.7 calls them inside the import summary. Build them as standalone, independently-testable helpers — do not couple them to the import flow. [Source: epics.md#Story-2.7]

### Counting semantics — the one thing to get exactly right
| Counter | Rule | Mutually exclusive? |
|---|---|---|
| `resolved` | `resolution_status == RESOLVED` | yes (vs `venue_unresolved`, by status) |
| `venue_unresolved` | `resolution_status == VENUE_UNRESOLVED` | yes (vs `resolved`, by status) |
| `descriptive_gaps` | any of `{company_name, sector, industry, country, currency}` `== NA_SENTINEL` | **NO — orthogonal**, overlaps both above |

- A fully-clean ETF → `resolved` only. A `RESOLVED` ETF missing `sector` → `resolved` **and** `descriptive_gaps`. A degraded/unknown ticker → `venue_unresolved` **and** `descriptive_gaps` (all five descriptive fields are `N/A` in `_degraded`). [Source: fmp_provider.py:97-118; instrument_metadata_service.py:88-102]
- This is why the three counts can sum to **more** than `len(results)` — that is correct, not a bug. Add a code comment saying so, so a future reader doesn't "fix" it.
- `venue` is **never** `NA_SENTINEL` (the model's `venue_never_na_sentinel` validator enforces it), so `venue` is structurally excluded from the descriptive-gap check. [Source: src/models/instrument_metadata.py:95-101]

### Per-ticker structlog contract (AC #2)
- Required fields, exactly: `ticker`, `provider`, `resolution_status`, `error`. [Source: architecture.md:444; epics.md#Story-1.6 AC2]
- `resolution_status` is logged as the **string value** (`r.resolution_status.value`), consistent with `fmp_provider._degraded` (`resolution_status=status.value`). [Source: fmp_provider.py:100-105]
- `provider` comes from `r.metadata_provider` (e.g. `"FMP"`, or `"UNKNOWN"` for a service-built error record). [Source: fmp_provider.py:65; instrument_metadata_service.py:81-86]
- `error=None` on these outcome lines — see Task 2 rationale. The real failure error is already logged by `resolve_batch` (Story 1.5). Two complementary log streams; do not duplicate the service's failure log here. [Source: instrument_metadata_service.py:70-78]

### Previous-story intelligence (1.1–1.5)
- **`resolve_batch` is `done` and returns `list[InstrumentMetadata]`** with per-ticker fault isolation — that list is this story's sole input. Failures already produce a `VENUE_UNRESOLVED` all-`N/A` record in the list (so they correctly land in `venue_unresolved` + `descriptive_gaps`). [Source: instrument_metadata_service.py:64-79]
- **`ResolutionSummary` exists** (Story 1.2) with zeroed defaults and field docstrings naming the three semantics — add the classmethod, don't redefine the model. [Source: src/models/instrument_metadata.py:104-120]
- **`NA_SENTINEL` is defined once** in `src/models/instrument_metadata.py:24` — import it, never write the literal `"N/A"` (PR-checklist item). [Source: architecture.md:452-453]
- **TDD strictly followed in 1.1–1.5** (tests Red first). Do the same: `from_results` and the reporting helpers get failing tests first.
- **1.4 review findings not to repeat:** add `pytestmark = pytest.mark.unit` to the new test module; quote scalar values in `sprint-status.yaml`. [Source: 1-4 / 1-5 Review Findings]

### Git intelligence (recent commits — patterns to follow)
`53ef682 feat(metadata): add provider-agnostic resolution service …` · `8d7c5fe fix(metadata): harden venue normalization …` · `0f9274c feat(metadata): add FMP provider adapter and venue map` · `ab3bfd2 feat(metadata): add FMP client …` · `7bdeb53 feat(db): add instrument_metadata cache table …`. Patterns: `feat(metadata): …` commit scope; `logger = structlog.get_logger(__name__)`; small pure functions + module-level helpers to stay under size limits; tests committed with implementation; mirror the AAA + `unittest.mock` test idiom. A reasonable commit subject here: `feat(metadata): add per-import resolution summary counting and reporting`. [Source: git log; CLAUDE.md Commit Format — `feat`, scope `metadata`, no AI references]

### Project Structure Notes
- **Modified files:** `src/models/instrument_metadata.py` (+ `ResolutionSummary.from_results`), `src/cli/commands/import_reporting.py` (+ summary/render/log helpers).
- **New test file:** `tests/unit/cli/commands/test_import_reporting.py`. **Extended test file:** `tests/unit/models/test_instrument_metadata.py` (`TestResolutionSummary`).
- Placement matches the architecture source tree: `ResolutionSummary` in `models/instrument_metadata.py`; resolution summary in `cli/commands/import_reporting.py`. [Source: architecture.md:463-465,489-492]

### Testing standards
- **Unit tier only** (`make test-unit`, parallel) — pure counting + pure render/log helpers; no DB, no Nautilus, no network. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — failing tests first. [Source: development-principles.md]
- Mirror the AAA + `unittest.mock` style and the logger-monkeypatch assertion from `tests/unit/services/metadata/test_instrument_metadata_service.py`; `pytestmark = pytest.mark.unit` at module top of the new test file.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.6] — story statement + the 2 acceptance criteria; FR6
- [Source: _bmad-output/planning-artifacts/architecture.md:438-446] — FMP client error & reporting pattern; `ResolutionSummary` fixed field names; structlog fields `ticker`/`provider`/`resolution_status`/`error`; "surfaced in the CLI summary and per-import logging"
- [Source: _bmad-output/planning-artifacts/architecture.md:463-465,489-492] — source-tree placement: `ResolutionSummary` in the domain model; resolution summary in `cli/commands/import_reporting.py`
- [Source: src/models/instrument_metadata.py:104-120] — `ResolutionSummary` model (zeroed defaults; docstring reserving the counting logic for this story)
- [Source: src/models/instrument_metadata.py:24,84-101] — `NA_SENTINEL`; the five descriptive fields; `venue` never-sentinel validator
- [Source: src/services/metadata/instrument_metadata_service.py:64-86] — `resolve_batch` → `list[InstrumentMetadata]`; existing `metadata_resolution_failed` per-ticker failure log; `_provider_name`
- [Source: src/services/metadata/providers/fmp_provider.py:77-118] — `_to_domain` status logic; `_degraded` all-`N/A` record; `resolution_status=status.value` log shape; `PROVIDER_NAME`
- [Source: src/cli/commands/import_reporting.py:64-150] — existing `build_summary_text` / `_print_summary` idioms to mirror (plain-text block + Rich table)
- [Source: tests/unit/models/test_instrument_metadata.py:146-160] — existing `TestResolutionSummary` to extend
- [Source: tests/unit/services/metadata/test_instrument_metadata_service.py] — logger-monkeypatch + structured-call assertion idiom for AC #2

### Open questions (non-blocking — proceed with the documented default)
1. **`from_results` location.** Default: classmethod on `ResolutionSummary` in the domain model (its docstring reserves the slot). Alternative considered + rejected: a free function in `import_reporting.py` — rejected because counting is pure domain logic reusable beyond the CLI (a future async explorer summary could call it). Confirm if the team prefers a free function.
2. **Per-ticker `error` field.** Default: `error=None` on the `metadata_resolution_outcome` line; real errors stay on `resolve_batch`'s `metadata_resolution_failed`. Alternative: add an optional `error` attribute to `InstrumentMetadata` or thread an error-map through the helper — deferred as scope creep into a `done` model. Flag if Story 2.7 needs a single combined per-ticker line.
3. **Descriptive-gap field list as a shared constant.** Default: an explicit local tuple in `from_results` (`("company_name","sector","industry","country","currency")`). If Story 4.4's N/A-aware explorer rendering needs the same list, promote it to a module-level constant in `instrument_metadata.py` then — not pre-emptively here.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context)

### Debug Log References

- `uv run pytest tests/unit/models/test_instrument_metadata.py::TestResolutionSummaryFromResults` — 12 failed (Red, `AttributeError: from_results`), then 28 passed after Green.
- `uv run pytest tests/unit/cli/commands/test_import_reporting.py` — 6 passed.
- `make test-unit` — **1052 passed** in 15.45s (baseline 1034 + 18 new; no regressions).
- `make lint` — All checks passed.
- `make typecheck` — Success: no issues found in 66 source files.

### Completion Notes List

- **AC #1** — `ResolutionSummary.from_results()` classmethod added to the existing domain model (its docstring reserved the slot). Two mutually-exclusive status counts (`resolved`, `venue_unresolved`) plus the orthogonal `descriptive_gaps` (any of the five descriptive fields `== NA_SENTINEL`). Counting is pure, single-pass, side-effect-free, with a module-level `_has_descriptive_gap` helper and an explicit `_DESCRIPTIVE_FIELDS` tuple. A code comment documents that the three counts can intentionally sum to more than `len(results)`.
- **AC #2** — Three helpers added to the existing `import_reporting.py`: `build_resolution_summary_text` (pure plain-text block), `_print_resolution_summary` (Rich table), and `log_resolution_results` (one `metadata_resolution_outcome` structlog event per ticker with the fixed four-field schema `ticker`/`provider`/`resolution_status`/`error`). `error=None` by design — genuine failures stay on `resolve_batch`'s `metadata_resolution_failed` stream.
- **Scope respected** — no CLI command wiring, no `InstrumentMetadataService` changes, no new DB columns/migrations/settings, no async variant. Helpers built as standalone, independently-testable units for the Story 2.7 seam.
- **Open questions** — proceeded with all three documented defaults (classmethod home; `error=None`; local field tuple; the tuple was promoted to a module-level `_DESCRIPTIVE_FIELDS` constant within the model module only, not pre-emptively shared).

### Change Log

| Date | Change |
|---|---|
| 2026-06-19 | Implemented Story 1.6: `ResolutionSummary.from_results()` counting + per-import resolution reporting helpers (`build_resolution_summary_text`, `_print_resolution_summary`, `log_resolution_results`). +18 unit tests. Status → review. |

### File List

- `src/models/instrument_metadata.py` (modified — `from_results` classmethod, `_has_descriptive_gap` helper, `_DESCRIPTIVE_FIELDS` constant, `Iterable` import)
- `src/cli/commands/import_reporting.py` (modified — `structlog` + domain-model imports, module `logger`, three reporting helpers)
- `tests/unit/models/test_instrument_metadata.py` (modified — `TestResolutionSummaryFromResults` class + fixtures)
- `tests/unit/cli/commands/test_import_reporting.py` (new — reporting-helper unit tests)
