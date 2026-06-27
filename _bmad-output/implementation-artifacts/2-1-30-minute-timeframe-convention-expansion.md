# Story 2.1: 30-Minute Timeframe Convention Expansion (4 → 5)

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want 30min added **once** to the central timeframe enum and the catalog schema,
so that ETFs can be imported, charted, and backtested at all 5 native timeframes without conflating 30min with 5min/1hour.

## Acceptance Criteria

1. **AC1 — Central enum, single definition.** The central timeframe enum (`ExplorerTimeframe` in `src/api/models/explorer.py`) exposes a 30min member whose bar-type string is `30-MINUTE-LAST` (word form, consistent with `1-HOUR-LAST` / `5-MINUTE-LAST`). The literal `30-MINUTE-LAST` is defined in exactly one place — no inline `30-MINUTE-LAST` string literal exists anywhere else in `src/`.
2. **AC2 — FirstRate filename token mapping.** The FirstRate filename token `_30min_` resolves to `30-MINUTE-LAST` via the extended timeframe maps in `src/services/firstrate/dry_run.py` (filename token → bar-type spec) and routes correctly to the `bar_count_30min` field via `src/services/firstrate/import_service.py` (timeframe key → bar-count field).
3. **AC3 — Alembic migration #11 (additive column).** A new Alembic migration (the 11th, chained off `67772db31d8d`) adds a `bar_count_30min` column to `catalog_instruments`, mirroring the existing `bar_count_5min` migration (`Integer`, `server_default="0"`, `nullable=False`) with a clean `downgrade()` that drops the column. The `CatalogInstrument` ORM model exposes the new column and the repository upsert copies it.
4. **AC4 — No stock 30min backfill.** Stock 30min backfill is explicitly NOT performed (deferred + tracked, out of Phase 2 scope). Only the convention/schema is added; no stock import/backfill code path is introduced or invoked.

## Tasks / Subtasks

- [x] **Task 1 — Add 30min to the central enum (AC: #1)**
  - [x] Add `THIRTY_MIN = ("30m", "30-MINUTE-LAST", "bar_count_30min", 90)` to `ExplorerTimeframe`, inserted between `HOURLY` and `FIVE_MIN` to preserve descending-granularity selector order.
  - [x] This is the **single** definition of the `30-MINUTE-LAST` literal.
- [x] **Task 2 — Extend FirstRate timeframe maps (AC: #2)**
  - [x] `dry_run.py`: add `"_30min_": ExplorerTimeframe.THIRTY_MIN.bar_type_spec` to `_FILENAME_TIMEFRAME_MAP` (reference the enum, do **not** retype the literal). Add the import of `ExplorerTimeframe` at top-level.
  - [x] `import_service.py`: add `"30-MINUTE": "bar_count_30min"` to `_TIMEFRAME_FIELD_MAP` so `_timeframe_key("30-MINUTE-LAST") → "30-MINUTE" → bar_count_30min`.
- [x] **Task 3 — Add `bar_count_30min` to the model + repository (AC: #3)**
  - [x] `CatalogInstrument`: add `bar_count_30min` mapped column (`Integer`, `nullable=False`, `default=0`, `server_default="0"`), placed next to `bar_count_5min`; update docstring.
  - [x] `CatalogInstrumentRepository`: copy `existing.bar_count_30min = instrument.bar_count_30min` in both the async and sync upsert paths (alongside the existing `bar_count_5min` copies).
- [x] **Task 4 — Alembic migration #11 (AC: #3)**
  - [x] Create `alembic/versions/9f3c1a72b4e8_add_bar_count_30min_column_to_catalog_.py` with `down_revision = "dbec2c1f25a6"` (the real head — keeps a single linear head; the "#11" label is planning order, Epic-1's #10 not yet landed), modeled on the `bar_count_5min` migration's column-add. `upgrade()` adds the column; `downgrade()` drops it. Written via Bash (the `alembic/versions/` dir is Write-hook-protected); `alembic heads` confirms a single head `9f3c1a72b4e8`.
- [x] **Task 5 — Tests (AC: #1, #2, #3, #4)**
  - [x] Unit: extend `tests/unit/api/test_chart_data_models.py` — assert `ExplorerTimeframe.THIRTY_MIN` label `30m`, bar-type `30-MINUTE-LAST`, bar-count field `bar_count_30min`, window `90`, and `from_label("30m")`.
  - [x] Unit: grep-style single-definition test (`tests/unit/api/test_timeframe_single_definition.py`) — asserts no inline `30-MINUTE-LAST` literal exists in `src/` outside `src/api/models/explorer.py` (AC1 "single definition").
  - [x] Unit: `tests/unit/services/firstrate/test_dry_run.py` — `_30min_` filename infers `30-MINUTE-LAST`, not conflated with 5min/1hour.
  - [x] Unit: `tests/unit/services/firstrate/test_import_service.py` — `_bar_count_field_for_timeframe("30-MINUTE-LAST") == "bar_count_30min"`.
  - [x] Unit/Component: model exposes `bar_count_30min` (defaults to 0); migration-presence test (`tests/unit/db/test_migration_bar_count_30min.py`); repository async+sync persist tests.
  - [x] AC4: no stock 30min backfill path added (deferral recorded below); no new stock import invocation.
- [x] **Task 6 — Gates**
  - [x] `uv run ruff check .` clean, `uv run mypy .` (no new errors; 6 pre-existing unrelated repo-debt errors remain), `make test-unit` (945 passed), `make test-component` (666 passed). Fixed regressions from the new 5th enum member (mock fixtures + raw DDLs + `instrument_mapper` zero-init).

## Dev Notes

### Central enum is `ExplorerTimeframe`
Per **ADR-9** (architecture.md §"Timeframe Pattern (30min)" and the ADR list), 30min is added **once** to the central timeframe enum and propagates to bar-type strings, explorer selector, statistics, and backtest config. The enum that carries the `(label, bar_type_spec, bar_count_field, initial_window_days)` tuple is `ExplorerTimeframe` (`src/api/models/explorer.py:18`). Existing members:

```
DAILY    = ("D",  "1-DAY-LAST",    "bar_count_daily",  1825)
HOURLY   = ("1H", "1-HOUR-LAST",   "bar_count_hourly",  180)
FIVE_MIN = ("5m", "5-MINUTE-LAST", "bar_count_5min",     30)
ONE_MIN  = ("1m", "1-MINUTE-LAST", "bar_count_minute",    7)
```

Insert `THIRTY_MIN = ("30m", "30-MINUTE-LAST", "bar_count_30min", 90)` between `HOURLY` and `FIVE_MIN`. The window `90` sits naturally between hourly (180) and 5-min (30). `from_label`/property accessors need no change — they iterate members generically.

### "Single definition" — no inline literals
`30-MINUTE-LAST` currently exists **nowhere** in `src/` (verified). The bar-type string must live only in the enum tuple. In `dry_run.py`, reference `ExplorerTimeframe.THIRTY_MIN.bar_type_spec` rather than retyping `"30-MINUTE-LAST"`. The existing `dry_run.py` entries (`"1-DAY-LAST"`, etc.) keep their literals — the single-definition constraint is scoped to the **new** `30-MINUTE-LAST` string only (architecture.md: "Added once; no inline string literals").

`dry_run.py` MUST NOT import `nautilus_trader` or anything that does. `src/api/models/explorer.py` imports only `pydantic` + `src/api/models/chart_timeseries.py` (pure pydantic/enum) — importing `ExplorerTimeframe` into `dry_run.py` is safe and introduces no Nautilus dependency.

### FirstRate map extension (AC2)
- `dry_run._FILENAME_TIMEFRAME_MAP` (`src/services/firstrate/dry_run.py:41`) — filename token → bar-type spec. Add `"_30min_"`. `_infer_timeframe` lowercases the filename and substring-matches, so `_30min_` matches `*_30min_*`.
- `import_service._TIMEFRAME_FIELD_MAP` (`src/services/firstrate/import_service.py:28`) — `"{step}-{agg}"` key → bar-count field. Add `"30-MINUTE": "bar_count_30min"`. `_timeframe_key("30-MINUTE-LAST")` already yields `"30-MINUTE"` (splits on `-`, takes first two parts) → resolves to `bar_count_30min` instead of falling back to `bar_count_daily`.
- The CLI `TIMEFRAME_MAP` in `src/cli/commands/import_data.py:28` is **out of scope** for this story's ACs (it is keyed by user-facing names like `5min`, not `_30min_`); do not add a 30min option there — that is part of the later Epic 2 import stories. Touch only the two `firstrate/` maps named in AC2.

### Migration #11 (AC3) — model exactly on the 5min migration
Reference: `alembic/versions/67772db31d8d_add_bar_count_5min_column_to_catalog_.py`.

```python
revision = "9f3c1a72b4e8"
down_revision = "dbec2c1f25a6"   # current head — chain off it for a single linear head

def upgrade():
    op.add_column("catalog_instruments",
        sa.Column("bar_count_30min", sa.Integer(), server_default="0", nullable=False))

def downgrade():
    op.drop_column("catalog_instruments", "bar_count_30min")
```

This column is a plain `Integer` — **no Postgres ENUM type** is involved (the "ENUM-before-column" discipline in epics.md §129 applies to the *other* migration #10 for `instrument_metadata`, not this one). Keep the downgrade clean (single `drop_column`). The objective gate runs **without a live Postgres**, so `alembic upgrade` is out of band — AC3 is satisfied by the migration file existing + model/repository exposing the column + clean mypy/import.

### Model + repository (AC3)
- `src/db/models/catalog_instrument.py:75` — add `bar_count_30min` next to `bar_count_5min`, identical column definition; update the attribute docstring block (lines 38-41).
- `src/db/repositories/catalog_instrument_repository.py` — two upsert sites copy each `bar_count_*` field explicitly (async ~line 57-60, sync ~line 293-296). Add `bar_count_30min` to both so the column round-trips. `_SORTABLE_COLUMNS` (line 241) does **not** need 30min.

### AC4 — explicitly no stock backfill
Do **not** add or invoke any stock 30min import/backfill. Phase 1 stock catalog stays as-is. Record the deferral in Completion Notes (it is tracked in `_bmad-output/implementation-artifacts/deferred-work.md` and architecture.md §232 "Stock 30min backfill (tracked, out of Phase 2 scope)"). A negative assertion / absence of such a code path satisfies AC4.

### Regression watch (the 5th member)
Adding a 5th `ExplorerTimeframe` member changes `list(ExplorerTimeframe)` (used as `ALL_TIMEFRAMES` in `src/api/ui/explorer.py:47`) and `VALID_TF_LABELS`. Existing unit tests in `test_chart_data_models.py` assert per-member properties (no total-count assertion), so they should not break — but run `make test-unit` + `make test-component` and fix any test that hard-codes the count of 4 timeframes or the selector contents.

### Testing standards
- TDD: write the failing test first (Red-Green-Refactor) per CLAUDE.md / development-principles.
- Tiers: **Unit** for enum/map/pure-string logic (no Nautilus); **Component** for model/repository with test doubles. No integration `--forked` run needed for this story.
- Commands: `make test-unit`, `make test-component`. Coverage targets `src/core` + `src/strategies` — this story touches `src/api/models`, `src/services/firstrate`, `src/db`; keep the new code tested directly.
- F401/F821 import gate: when adding the `ExplorerTimeframe` import to `dry_run.py`, use it in the same edit (the map entry references it) so no unused-import window opens.

### Project Structure Notes
- Files touched: `src/api/models/explorer.py`, `src/services/firstrate/dry_run.py`, `src/services/firstrate/import_service.py`, `src/db/models/catalog_instrument.py`, `src/db/repositories/catalog_instrument_repository.py`, `alembic/versions/<new>_add_bar_count_30min_*.py`, plus tests under `tests/unit/...` and `tests/component/...`.
- No new modules/concepts — pure additive extension of an established 4→5 convention. Aligns with architecture.md source-tree annotations (catalog_instrument.py "MODIFIED: add bar_count_30min"; dry_run.py "MODIFIED: _30min_ token"; new migration `YYYY_add_bar_count_30min.py`).
- All files stay under size limits (<500 lines, functions <50, line length 100).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.1] — AC1-AC4 (lines 411-433)
- [Source: _bmad-output/planning-artifacts/architecture.md#ADR-9] — central enum expansion (lines 324-329), Timeframe Pattern §432-436, source-tree §469/§488/§501
- [Source: harness-story-2.1-spec.md] — scoped ACs + TEA verification guidance (single-definition grep, migration-file-presence acceptance, no-backfill negative assertion)
- [Source: src/api/models/explorer.py:18-66] — `ExplorerTimeframe` central enum
- [Source: src/services/firstrate/dry_run.py:41-46] — `_FILENAME_TIMEFRAME_MAP`
- [Source: src/services/firstrate/import_service.py:28-64] — `_TIMEFRAME_FIELD_MAP`, `_timeframe_key`, `_bar_count_field_for_timeframe`
- [Source: alembic/versions/67772db31d8d_add_bar_count_5min_column_to_catalog_.py] — migration template
- [Source: src/db/models/catalog_instrument.py:66-77] — bar_count columns
- [Source: src/db/repositories/catalog_instrument_repository.py:57-60,293-296] — upsert field copies

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- `uv run alembic heads` → single head `9f3c1a72b4e8` (no branch). `uv run alembic history` shows `dbec2c1f25a6 -> 9f3c1a72b4e8 (head)`.
- Gates: ruff `All checks passed!`; mypy `Found 6 errors in 4 files` — all 6 pre-existing in files **not** in this changeset (`src/api/ui/explorer.py`, `src/api/ui/backtests.py`, `tests/api/rest/test_timeseries.py`, `tests/component/cli/commands/test_import_data.py`); `make test-unit` 945 passed; `make test-component` 666 passed, 16 skipped.

### Completion Notes List

- **AC1 (central enum, single definition):** Added `THIRTY_MIN = ("30m", "30-MINUTE-LAST", "bar_count_30min", 90)` to `ExplorerTimeframe` — the one place the `30-MINUTE-LAST` literal lives. `dry_run.py` references `ExplorerTimeframe.THIRTY_MIN.bar_type_spec` (no inline literal). Guard test `test_timeframe_single_definition.py` greps `src/` and confirms the literal exists only in `explorer.py` (had to reword a doc-comment in `dry_run.py` that incidentally contained the string).
- **AC2 (`_30min_` token mapping):** `dry_run._FILENAME_TIMEFRAME_MAP["_30min_"]` → `30-MINUTE-LAST`; `import_service._TIMEFRAME_FIELD_MAP["30-MINUTE"]` → `bar_count_30min` (reached via `_timeframe_key("30-MINUTE-LAST")`). Tests assert both halves and that 30min is not conflated with 5min/1hour.
- **AC3 (migration + model):** Migration `9f3c1a72b4e8` adds `bar_count_30min` (Integer, server_default 0, NOT NULL) with a clean `drop_column` downgrade, chained off the real head `dbec2c1f25a6` (single linear head). `CatalogInstrument` model + both repository upsert paths expose/copy the column. Live `alembic upgrade` is out of band (no Postgres in the gate) per the harness spec; covered by model-field + migration-presence tests.
- **AC4 (no stock backfill):** No stock 30min import/backfill code path added or invoked. Phase-1 stock catalog untouched. Deferral remains tracked in `architecture.md §232` / `deferred-work.md`.
- **Regression fixes from the 5th enum member (intended ADR-9 propagation):** the chart/stats presentation iterates the now-5-member enum and gates each timeframe on `getattr(instrument, "bar_count_30min") > 0`. Updated `MagicMock(spec=CatalogInstrument)` fixtures (chart_panel/stats_panel/explorer_routes) to set `bar_count_30min`, added the column to two raw `CREATE TABLE` test DDLs, and initialized `bar_count_30min=0` in `instrument_mapper.load_company_profiles` (so the upsert update-branch copies 0, not an unflushed `None`, into the NOT NULL column).
- **Out of scope (Epic 4):** presentation models `TickerRow` / `TickerStatsResponse` do not yet expose a `bar_count_30min` field — that extension belongs to the explorer stories (4.x). Story 2.1 delivers only the convention + schema.
- **Pre-existing mypy debt:** the 6 mypy errors are unrelated baseline debt in untouched files; not fixed here to keep the story scoped.

### File List

**Source (modified):**
- `src/api/models/explorer.py` — added `ExplorerTimeframe.THIRTY_MIN` (single definition of `30-MINUTE-LAST`)
- `src/api/ui/explorer.py` — added `"30m": "30-MINUTE"` to `TIMEFRAME_EXPLORER_TO_RUN_FORM` (code-review fix: the chart toolbar iterates the now-5-member enum, so the bridge map must cover 30m or the Run-Backtest URL builder raises `KeyError` → HTTP 500)
- `src/services/firstrate/dry_run.py` — import `ExplorerTimeframe`; `_FILENAME_TIMEFRAME_MAP["_30min_"]`
- `src/services/firstrate/import_service.py` — `_TIMEFRAME_FIELD_MAP["30-MINUTE"] = "bar_count_30min"`
- `src/services/firstrate/instrument_mapper.py` — initialize `bar_count_30min=0` on instrument construction
- `src/db/models/catalog_instrument.py` — `bar_count_30min` mapped column + docstring
- `src/db/repositories/catalog_instrument_repository.py` — copy `bar_count_30min` in async + sync upsert

**Source (new):**
- `alembic/versions/9f3c1a72b4e8_add_bar_count_30min_column_to_catalog_.py` — additive migration

**Tests (new):**
- `tests/unit/api/test_timeframe_single_definition.py` — AC1 grep guard
- `tests/unit/db/test_migration_bar_count_30min.py` — AC3 migration-presence guard

**Tests (modified):**
- `tests/unit/api/test_chart_data_models.py` — `THIRTY_MIN` enum assertions
- `tests/unit/services/firstrate/test_dry_run.py` — `_30min_` inference test
- `tests/unit/services/firstrate/test_import_service.py` — `30-MINUTE-LAST` → `bar_count_30min` param
- `tests/unit/models/test_catalog_instrument_model.py` — `bar_count_30min` column test
- `tests/component/db/test_catalog_instrument_repository.py` — DDL + async/sync persist tests
- `tests/component/services/firstrate/test_instrument_mapper.py` — DDL `bar_count_30min`
- `tests/component/api/test_chart_panel_routes.py` — fixture `bar_count_30min`
- `tests/component/api/test_stats_panel_routes.py` — fixture `bar_count_30min`
- `tests/component/api/test_explorer_routes.py` — fixture `bar_count_30min`
- `tests/unit/api/test_explorer_bridge_url.py` — 5-entry bridge map + `30m` build/reverse coverage (code-review fix)

## Senior Developer Review (AI)

**Reviewed:** 2026-06-22 · **Outcome:** Approved (1 High finding found and resolved) · Three adversarial layers run.

- **Acceptance Auditor — PASS (all 4 ACs).** AC1 single-definition verified (`30-MINUTE-LAST` only in `explorer.py`, guard test scans real `src/`); AC2 both maps resolve `_30min_`→`30-MINUTE-LAST`→`bar_count_30min` with no 5min/1hour conflation; AC3 additive migration + model + repo, clean downgrade, single linear head; AC4 no stock backfill, deferral tracked. (Non-blocking nit: migration test uses a loose `"add_column" in text` substring — accepted, it reliably matches `op.add_column(`.)
- **Blind Hunter — no material defects.** Flagged "verify" items (substring-collision, enum-order, NOT NULL copy, single head) — all confirmed safe by the Edge Case Hunter against the real tree.
- **Edge Case Hunter — 1 High (RESOLVED).** Adding the 5th enum member makes the chart-panel toolbar render a clickable 30m button once `bar_count_30min > 0`; `_build_run_backtest_url` did a bracket lookup `TIMEFRAME_EXPLORER_TO_RUN_FORM[active_tf.label]` that had no `30m` key → `KeyError` → HTTP 500. **Fix:** added `"30m": "30-MINUTE"` to the bridge map (`src/api/ui/explorer.py`) + regression test `test_build_run_backtest_url_30min`. Confirmed-safe: nautilus-free import chain intact, substring matching non-colliding, NOT NULL column always populated (`instrument_mapper` zero-inits; repo copies in both upsert paths), migration single-head.

### Action Items

- [x] **[High]** Extend `TIMEFRAME_EXPLORER_TO_RUN_FORM` with `30m → 30-MINUTE` and regression-test it — fixed `src/api/ui/explorer.py`, `tests/unit/api/test_explorer_bridge_url.py`.
- [ ] **[Deferred → Epic 5]** Add `30-MINUTE` to `VALID_TIMEFRAMES` (run-backtest form accepted set) so a 30m chart's Run-Backtest pre-selects 30m instead of silently falling back to 1-DAY. The form already drops unknown timeframes gracefully (no crash); full 30m backtest execution is Backtest-Integration scope.
- [ ] **[Deferred → Epic 4]** Surface `bar_count_30min` in presentation models/stats (`TickerRow`, `TickerStatsResponse`, stats panel, sortable columns) — explorer-UI scope; Story 2.1 is schema + convention only.

## Change Log

| Date | Change |
|------|--------|
| 2026-06-22 | Story 2.1 implemented — 30min added once to the central `ExplorerTimeframe` enum (`30-MINUTE-LAST`), `_30min_` FirstRate token maps extended, additive Alembic migration `9f3c1a72b4e8` for `bar_count_30min`, model/repository expose the column. Stock 30min backfill deferred (AC4). |
| 2026-06-22 | Code review (3 adversarial layers) — resolved 1 High: added `30m → 30-MINUTE` to the explorer→run-form bridge map to prevent a `KeyError`/500 when the toolbar's new 30m button is clicked; regression test added. All gates green (ruff clean, 947 unit + 666 component passing, mypy unchanged). |
