# Proposal: streaming FirstRate futures importer (M2)

## Why

M1 gave NTrader futures contract specs and a `FuturesContract` builder, but there is still no
way to get FirstRate futures bars into a catalog. `data import --asset-class futures` fails
(no parser is registered), the equity parser drops every 7-column daily futures row, it loads
whole files and reads the whole series back (a 6.4M-row ES 1-minute file won't fit this box),
and the import refuses to run without `company_profiles.csv`. A latent bug also affects
equities: Nautilus' catalog stores a batch at its first bar's price precision, so a file that
mixes `5918.9` and `5918.99` comes back truncated.

## What changes

- `FirstRateFuturesParser` (`src/services/firstrate/parsers/firstrate_futures_parser.py`),
  registered for `AssetClass.FUTURES`: reads 6- or 7-column rows (open interest dropped) in
  fixed-size pandas chunks, converts US/Eastern to UTC exactly like the equity parser, and
  emits every price of a root at the spec's `continuous_price_precision` (ES/NQ 2, CL 3,
  GC 1, ZN 6).
- `FuturesImportService` (`src/services/firstrate/futures_import_service.py`), a subclass of
  `ImportService`: spec-driven `catalog_instruments` rows (no company profiles), one Parquet
  part per chunk, verification from Parquet footers plus sampled bars read back by timestamp,
  the inherited idempotent skip/reimport classifier plus an on-disk row-count check.
- CLI: `import --format firstrate --asset-class futures` routes to
  `src/cli/commands/import_futures.py`; futures default to all five timeframes like ETFs;
  the target catalog directory is created on first use; `--dry-run` accepts 7-column daily
  files and shows per-root date ranges.
- Equity/ETF parser: one fixed precision per file (the widest any price carries), so the
  mixed-precision truncation cannot happen. Files with uniform decimals are unchanged.
- `catalog_instruments` upserts now copy the M1 futures columns (`multiplier`, `tick_size`,
  `currency`, `contract_kind`, `expiry`, `roll_rule`); M1 added the columns but not the
  repository's update list.
- The source last-date probe accepts 7-column rows (futures daily) so daily re-runs skip.

## Interfaces crossed

- **CLI**: `--asset-class futures` now imports (it used to exit 2 with "No parser
  registered"); its default timeframes are all five. No flag or exit code changes.
- **Parser registry**: `get_parser(AssetClass.FUTURES)` returns the new parser.
- **Record shape**: futures `catalog_instruments` rows carry `asset_class = FUTURES`,
  `ticker = ROOT`, `nautilus_id = ROOT.VENUE`, `contract_kind = continuous`,
  `roll_rule = firstrate-ratio-adjusted`. M1's `build_instrument` dispatcher already keys on
  these.

## Guard lists / pinned constants

- No `src/core/live_*.py`, `src/cli/commands/live*.py`, `src/api/**` or `templates/**` file
  is touched, so no live guard list or `FROZEN_VIEWS` entry applies.
- No migration: the columns came with M1 (`3f56b46c78eb` stays the single head).
- Size caps: `ImportService` and `_run_import` sit exactly at their baseline ceilings, so the
  futures path is a subclass in a new module and the CLI branch lives in `import_firstrate`.

## Out of scope

- M3: margin model, per-contract commissions, multiplier-aware sizing, smoke backtest.
- Bar timestamp semantics (bars stay stamped at the bar open, as for equities).
- Open interest (dropped), individual contracts and rolls (M5).
