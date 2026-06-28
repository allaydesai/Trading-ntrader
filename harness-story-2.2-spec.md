# Harness Scope — Story 2.2: Per-Archive ZIP Extraction

> This is the **single-story scope** handed to the BMAD harness for this run.
> The PO-sim and the verification (TEA) gate must evaluate **only** the
> acceptance criteria below — NOT the other Phase-2 stories. Source of truth is
> `_bmad-output/planning-artifacts/epics.md` (Epic 2, Story 2.2) and
> `architecture.md` (FirstRate import pipeline).

## Story

As the system,
I want a per-archive ZIP extraction stage that extracts one archive, hands it to
the parser, and cleans up before the next,
So that peak disk usage stays bounded to one batch and an interrupted import
leaves the catalog consistent.

## Scope for this run

- Implement ONLY Story 2.2: a `src/services/firstrate/zip_extractor.py` component
  that extracts FirstRate ETF ZIP archives **one at a time**, exposes the extracted
  `.txt` files to a caller, and deletes them before moving to the next archive.
- Do NOT implement parsing, conversion to Parquet, the import pipeline, dry-run
  scan, verification, idempotency, venue resolution, or explorer work (Stories
  2.3–2.7, E3–E5). Provide only the extraction stage and the seam the parser will
  later call; stub/inject the parser hand-off rather than implementing it.
- No database schema change and no Alembic migration are required for this story.
- No live data sources, no network, no broker/exchange I/O.

## Acceptance Criteria

**AC1 — One archive at a time, with cleanup.**
Given a `src/services/firstrate/zip_extractor.py` component, When it processes a set
of letter-batched ZIP archives (26 per timeframe), Then it extracts exactly one
archive at a time and deletes the extracted `.txt` files before moving to the next
archive (peak disk bounded to a single archive's contents).

**AC2 — Interruption leaves no readable partial output.**
Given an import interrupted mid-archive, When the run halts, Then no partially-written
output is left readable as valid downstream (consistent with the Phase-1
metadata-gatekeeper keeping partial output invisible to explorer/backtest). For this
extraction-only story, this means extraction never leaves stray `.txt` files behind
on failure and signals failure cleanly to the caller.

**AC3 — Archive-granular resume.**
Given a re-run after interruption, When extraction resumes, Then it resumes at
archive granularity without manual bookkeeping (already-completed archives are
skipped / not re-extracted based on observable state, not a hand-maintained ledger).

**AC4 — Component tests prove the behavior.**
Given component tests, When the extractor is exercised, Then per-archive
extract-then-cleanup, the interruption/no-stray-files behavior, and archive-granular
resume are each verified with real temporary ZIP fixtures (no network, no live data).

## Verification guidance (for the TEA gate)

The objective `test_command` for this run is `ruff check . && mypy . &&
make test-unit && make test-component` — infra-light, no live Postgres. Map each AC:

- **AC1** — covered by a PASSING component test that builds ≥2 small temp ZIP
  fixtures, runs the extractor, and asserts (a) only one archive's `.txt` files exist
  at a time (observed via the per-archive hand-off), and (b) extracted files are
  deleted before the next archive.
- **AC2** — covered by a PASSING test that injects a failure during/after extraction
  of one archive and asserts no stray extracted `.txt` files remain and the failure
  is raised/surfaced cleanly (not swallowed).
- **AC3** — covered by a PASSING test that simulates a resume (some archives already
  done) and asserts completed archives are skipped without a manual ledger.
- **AC4** — satisfied by the above living under `tests/component/` (and/or
  `tests/unit/`) following the repo's existing test layout, plus `ruff`/`mypy` clean.

`unmet` should list an AC only when its evidence above is genuinely missing from the
test output or the tree.
