# Tasks

## 1. Spec registry
- [x] 1.1 Unit (failing first): `tests/unit/services/futures/test_contract_specs.py`: the
      shipped file loads, all five roots present with expected multiplier/venue/tick;
      bad files (unknown key, bad tick precision, initial < maintenance, IB mismatch,
      unknown schema version, unknown root lookup) raise `FuturesSpecError` naming the field.
- [x] 1.2 Add `contract_specs.yaml` and pydantic loader `contract_specs.py`.

## 2. Instrument builder
- [x] 2.1 Unit (failing first): `tests/unit/services/futures/test_instrument_builder.py`:
      each root builds a valid `FuturesContract` (id, multiplier, increment, precision,
      asset class, never-expiring window, info), precision follows bars, margin conversion
      with a reference price, venue mismatch rejected.
- [x] 2.2 Implement `build_futures_contract()`.

## 3. Dispatcher
- [x] 3.1 Unit (failing first): `tests/unit/services/firstrate/test_build_instrument.py`:
      FUTURES -> `FuturesContract`; STOCK/ETF/None -> identical result to `build_equity`.
- [x] 3.2 Component: `load_from_catalog` with a FUTURES row returns a `FuturesContract`
      (`tests/component/services/firstrate/test_catalog_backtest_loader.py`).
- [x] 3.3 Add `build_instrument()` and route `load_from_catalog` through it.

## 4. Engine acceptance
- [x] 4.1 Component: futures contract added to a Nautilus `Cache` round-trips.
- [x] 4.2 Integration (`--forked`): a `BacktestEngine` with a margin venue accepts every
      root's contract and bars at the contract's precision and runs.

## 5. Schema
- [x] 5.1 Update the single-head pin test (failing first) to the new revision.
- [x] 5.2 Migration + ORM fields; upgrade/downgrade/upgrade against a scratch DB on
      `ntrader-pg`.
- [x] 5.3 Update `CLAUDE.md` head, `docs/agent/persistence.md`.
