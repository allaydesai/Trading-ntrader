# Proposal: futures contract-spec registry and instrument model (M1)

## Why

NTrader can only synthesise `Equity` instruments. FirstRate continuous, ratio-adjusted
futures series (ES, NQ, CL, GC, ZN under `/srv/market-data/raw/futures/`) cannot be
backtested until there is a source of truth for each contract's economics (multiplier,
tick, margin, commission) and a way to turn it into a Nautilus `FuturesContract`. This is
milestone M1 of the futures plan; M2-M5 build on it.

## What changes

- A versioned, hand-curated spec file `src/services/futures/contract_specs.yaml` (one entry
  per root) validated on load by pydantic models in `src/services/futures/contract_specs.py`.
  Invalid files fail with an error that names the file and the offending field path.
- `build_futures_contract()` (`src/services/futures/instrument_builder.py`) turns a spec into
  a Nautilus 1.220 `FuturesContract` for the continuous series.
- `build_instrument()` (`src/services/firstrate/backtest_loader.py`) dispatches on the
  catalog row's `asset_class`: `FUTURES` builds a futures contract, everything else keeps
  today's `build_equity` path byte for byte. `load_from_catalog` calls the dispatcher.
- Alembic migration adding nullable futures columns to `catalog_instruments`
  (`multiplier`, `tick_size`, `currency`, `contract_kind`, `expiry`, `roll_rule`) and the
  matching ORM fields.

## Interfaces crossed

- **DB schema / migration**: `catalog_instruments` gains six nullable columns; the single
  Alembic head moves from `dc41856246ad` to the new revision. Pinned by
  `tests/unit/core/test_results_extractor.py::...test_there_is_one_migration_head...`
  and cited in `CLAUDE.md` (both updated).
- **Instrument construction seam**: `load_from_catalog` now goes through `build_instrument`.
  `build_equity`'s signature and output are unchanged.

## Guard lists / pinned constants

- No `src/core/live_*.py` or `src/cli/commands/live*.py` module is touched, so none of the
  live guard lists in `docs/agent/guard-lists.md` apply.
- No `FROZEN_VIEWS` file (`src/api/**`, `templates/**`) is touched.
- The migration-head pin above is the only pinned constant that changes.

## Out of scope (later milestones)

- M2: the futures CSV parser/importer, the `firstrate-futures` catalog, writing the new
  `catalog_instruments` columns from the spec at import time, fixed-precision padding.
- M3: margin enforcement (dollar margins to Nautilus margin model), per-contract commission
  model, multiplier-aware position sizing, the futures smoke backtest.
- M4: dashboard/reporting of contracts, multiplier, notional.
- M5: live IB resolution of specific contracts and rolling (the IB fields are stored now so
  M5 needs no new spec fields).
- Open interest (dropped for now), bar timestamp semantics (unchanged).
