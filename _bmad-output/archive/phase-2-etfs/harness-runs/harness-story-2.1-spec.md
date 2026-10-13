# Harness Scope — Story 2.1: 30-Minute Timeframe Convention Expansion (4 → 5)

> This is the **single-story scope** handed to the BMAD harness for this run.
> The PO-sim and the verification (TEA) gate must evaluate **only** the
> acceptance criteria below — NOT the other Phase-2 stories. Source of truth is
> `_bmad-output/planning-artifacts/epics.md` (Epic 2, Story 2.1) and
> `architecture.md` (30min convention, ADR-8/ADR-9).

## Story

As the system,
I want 30min added once to the central timeframe enum and the catalog schema,
So that ETFs can be imported, charted, and backtested at all 5 native timeframes
without conflating 30min with 5min/1hour.

## Acceptance Criteria

**AC1 — Central enum, single definition.**
Given the central timeframe enum, When 30min is added, Then the bar-type string is
`30-MINUTE-LAST` (word form, consistent with `1-HOUR-LAST` / `5-MINUTE`) and is
defined in exactly one place — no inline string literals elsewhere.

**AC2 — FirstRate filename token mapping.**
Given the FirstRate filename token `_30min_`, When the timeframe maps in
`firstrate/dry_run.py` and `firstrate/import_service.py` are extended, Then
`_30min_` resolves to `30-MINUTE-LAST`.

**AC3 — Alembic migration #11 (additive column).**
Given a new Alembic migration (the 11th), When `alembic upgrade head` runs, Then a
`bar_count_30min` column is added to `catalog_instruments`, matching the existing
`bar_count_minute` / `bar_count_hourly` / `bar_count_daily` naming. This is a
direct analog of the existing `bar_count_5min` migration
(`alembic/versions/67772db31d8d_*`). The Postgres ENUM-before-column / downgrade
discipline of the repo applies.

**AC4 — No stock 30min backfill.**
Given the existing Phase-1 stock catalog, When this story completes, Then stock
30min backfill is explicitly NOT performed (deferred + tracked, out of Phase-2
scope) — only the convention/schema is added.

## Verification guidance (for the TEA gate)

The objective `test_command` for this run is `ruff check . && mypy . &&
make test-unit && make test-component` — it runs **without a live Postgres**, so
DB-apply of the migration is out of band. Map each AC as follows:

- **AC1** — covered by a PASSING unit/component test asserting the enum exposes the
  30min member and yields `30-MINUTE-LAST`, plus `mypy`/`ruff` clean. Treat a
  grep-style assertion that no inline `30-MINUTE-LAST` literal exists outside the
  enum module as satisfying the "single definition" clause.
- **AC2** — covered by a PASSING test asserting `_30min_` → `30-MINUTE-LAST`
  through the `firstrate/dry_run.py` and `firstrate/import_service.py` maps.
- **AC3** — the migration cannot be `alembic upgrade`-applied here (no DB). Accept
  as met when: (a) the migration file exists under `alembic/versions/`, (b) it adds
  `bar_count_30min` to `catalog_instruments` with a clean downgrade, mirroring the
  `bar_count_5min` migration, and (c) the `CatalogInstrument` model / repository
  exposes the new column and imports cleanly under `mypy`. A test asserting the
  model field / migration presence satisfies this AC. Do **not** require a live
  `alembic upgrade` in the gate.
- **AC4** — met when no stock 30min import/backfill code path is added and the
  deferral is recorded (story notes / sprint-status). A negative assertion (no
  backfill invocation) or the absence of such a change satisfies this AC.

`unmet` should list an AC only when its evidence above is genuinely missing from
the test output or the tree — not merely because a live DB step wasn't run.
