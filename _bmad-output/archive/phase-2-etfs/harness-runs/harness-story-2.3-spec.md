# Harness Scope — Story 2.3: Dry-Run Scan with FMP-Aware Estimate

> This is the **single-story scope** handed to the BMAD harness for this run.
> The PO-sim and the verification (TEA) gate must evaluate **only** the
> acceptance criteria below — NOT the other Phase-2 stories. Source of truth is
> `_bmad-output/planning-artifacts/epics.md` (Epic 2, Story 2.3) and
> `architecture.md` (FirstRate import pipeline + instrument-metadata layer).

## Story

As the operator,
I want a dry-run that scans the ETF source archives and reports what would be
imported without writing data,
So that I can validate the source and estimate disk **and metadata-resolution
work** before committing to a full import.

## What already exists (build ON this — do NOT rewrite it)

A dry-run scanner already exists from Story 1.6:

- `src/services/firstrate/dry_run.py` — `scan_firstrate_directory`,
  `validate_schema_sample`, `estimate_parquet_bytes`, `build_dry_run_report`,
  `format_bytes`. It is a **pure-Python, read-only** module that MUST NOT import
  `nautilus_trader` or touch the DB / Nautilus C extension (see its docstring).
- `src/models/catalog.py` — `DryRunReport`, `TimeframeSummary`, `SchemaMismatch`.
  The report already carries per-timeframe ticker/file/byte counts,
  `distinct_ticker_count`, `total_source_bytes`, `estimated_parquet_bytes`, and
  6-column schema-mismatch detection.
- `src/cli/commands/import_data.py` — `import_firstrate` with a
  `--dry-run/--no-dry-run` flag, `_run_dry_run`, and `_print_dry_run_report`.

So **AC1 (counts / 6-col schema / timeframes / no writes) and the disk estimate
are largely DONE for the stocks path.** This story extends that scanner to the
**ETF source** and adds the two pieces Story 2.3 specifically requires that do
not yet exist:

1. **Per-ticker date ranges** in the report (epics: "per-ticker date ranges").
2. The **FMP-aware metadata estimate** (epics: "how many tickers would require
   resolution vs. are already cached").

## Scope for this run

- Extend the existing dry-run so that, run against the **ETF** source
  (`--asset-class etf`), it additionally reports:
  - **per-ticker date ranges** (earliest / latest observed date per ticker),
    derived cheaply from the source files (e.g. first + last data line) — no full
    file parse, no Nautilus, no DB.
  - an **FMP-aware estimate**: of the distinct scanned tickers, how many are
    **already cached/resolved** in the instrument-metadata store vs. how many
    would **require FMP resolution** on a real import.
- The FMP-aware estimate is **read-only and offline**: it must classify tickers
  using only locally-cached metadata state and MUST NOT call FMP / the network /
  any provider, and MUST NOT write the catalog or DB. A ticker counts as
  "cached" iff the metadata store already holds a `RESOLVED` record for it
  (`ResolutionStatus.RESOLVED`); everything else counts as "needs resolution".
- Keep `dry_run.py` pure: the cache lookup must come in through an **injected
  seam** (a small read-only "is this set of tickers already resolved?" reader
  Protocol / callable), the same way the parser hand-off was injected in Story
  2.2 — so `dry_run.py` still does NOT import nautilus or open a DB session
  itself, and component tests can pass a fake reader. The CLI
  (`import_data.py`) wires the real metadata-store-backed reader.
- Surface both additions in the `DryRunReport` model and in
  `_print_dry_run_report` output, and keep the existing
  `"No data written — dry run only."` trailer.

### Out of scope (do NOT implement — later stories / epics)
- The actual import / parse / Parquet conversion / catalog write (Story 2.4).
- Real FMP resolution, venue resolution, or any network/provider call (Story
  2.5+ / metadata epic). Idempotency, verification, explorer work (2.6–2.7).
- Any DB schema change or Alembic migration — **this story needs none**.
- Any change to financial calculations or previously produced numbers.

## Acceptance Criteria

**AC1 — ETF scan reports counts, schema, timeframes, per-ticker date ranges, no writes.**
Given a dry-run pointed at the ETF source directory (`--asset-class etf`), When it
runs, Then it reports distinct ticker count, the detected 6-column schema (flagging
mismatches), the timeframes present, and **per-ticker date ranges**, and writes
nothing to the catalog or DB (the existing `"No data written — dry run only."`
trailer still prints).

**AC2 — Estimated disk footprint.**
Given the scan, When disk usage is estimated, Then an estimated Parquet disk
footprint for the full import is reported (existing `estimate_parquet_bytes` /
`estimated_parquet_bytes`, surfaced for the ETF source).

**AC3 — FMP-aware metadata estimate.**
Given the dry-run output, When it summarizes metadata work, Then it reports how many
distinct tickers are **already cached/resolved** vs. how many **would require FMP
resolution**, computed **offline** from locally-cached metadata only (no network /
no provider call), via an injected read-only reader so `dry_run.py` stays pure.

**AC4 — Tests prove the behavior, infra-light.**
Given unit/component tests, When the new behavior is exercised, Then per-ticker date
ranges and the cached-vs-needs-resolution split are each verified with real temporary
fixtures and a **fake** metadata-cache reader (no network, no live data, no
Postgres), and `ruff` + `mypy` are clean.

## Verification guidance (for the TEA gate)

Objective `test_command`: `ruff check . && mypy . && make test-unit &&
make test-component` — infra-light, no live Postgres. Map each AC:

- **AC1** — a PASSING test builds a small temp ETF source tree (a couple tickers,
  ≥1 timeframe, 6-column rows with distinct first/last dates), runs the scanner, and
  asserts distinct ticker count, timeframe grouping, 6-col schema handling, and
  correct per-ticker earliest/latest dates; plus the CLI/report writes nothing.
- **AC2** — a PASSING assertion that a non-zero `estimated_parquet_bytes` is reported
  for the ETF scan (and `format_bytes` renders it).
- **AC3** — a PASSING test passes a **fake** cache reader marking some tickers
  RESOLVED and the rest unknown, and asserts the report's cached vs. needs-resolution
  counts match — with the fake asserting it was never asked to hit a network/provider.
- **AC4** — the above live under `tests/unit/` and/or `tests/component/` per the
  repo's existing layout, with `ruff`/`mypy` clean.

`unmet` should list an AC only when its evidence above is genuinely missing from the
test output or the tree.

## The one decision you may need to escalate

The FMP-aware estimate needs to read cached metadata state, but `dry_run.py` is
contractually DB-free / nautilus-free. The intended resolution is the **injected
read-only reader seam** above (pure module + CLI wires the real store), and tests
use a fake reader — proceed on that. **Escalate** only if implementing AC3 would
force `dry_run.py` itself to open a DB session / import nautilus, or would require a
schema change or a real provider/network call — none of which are in scope here.
