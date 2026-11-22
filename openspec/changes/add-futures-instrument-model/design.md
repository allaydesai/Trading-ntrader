# Design: futures contract-spec registry and instrument model

## Spec file

`src/services/futures/contract_specs.yaml` lives next to its loader so it ships with the
code and changes in the same PR as anything that reads it. It is versioned two ways:
`schema_version` (shape; bump when fields change, the loader accepts only known versions)
and `specs_as_of` (content date). Margins carry their own `as_of` date and an
"approximate" note: they are hand-curated broker/exchange levels that go stale, good enough
for backtest sizing, never authoritative for live trading.

Each root entry holds: `name`, `venue` (as IB shows it: CME, NYMEX, COMEX, CBOT),
`asset_class` (Nautilus asset class name), `underlying`, `currency`, `multiplier`,
`tick_size`, `price_precision` (precision of the exchange tick), `continuous_price_precision`
(the decimals the ratio-adjusted series carries; measured on the 1-hour/1-minute files),
`session` (ET open/close/days/daily break), `margin` (initial/maintenance USD, as_of),
`commission_per_contract_usd` (per side, approximate all-in IB fixed rate) and `ib`
(`sec_type: FUT`, `symbol`, `exchange`, `trading_class`, `currency`, `multiplier`).

Validation is pydantic with `extra="forbid"` (a typo is an error, not a silently ignored
key) plus cross-field checks: tick decimals fit `price_precision`, continuous precision is
not coarser than the tick, initial margin >= maintenance, and the `ib` block agrees with the
top-level venue/currency/multiplier. Any failure raises `FuturesSpecError` naming the file
and the dotted field path (`roots.ES.multiplier: ...`).

## Instrument ID scheme

Continuous series: `{ROOT}.{VENUE}`, e.g. `ES.CME`, `CL.NYMEX`, `GC.COMEX`, `ZN.CBOT`,
with `raw_symbol = ROOT`. Specific contracts (M5) will use IB's local symbol, e.g.
`ESZ6.CME`, so the two never collide.

## Continuous contract activation / expiration

Nautilus' matching engine rejects orders before `activation_ns` and after `expiration_ns`
and force-closes the instrument at expiration. A continuous series has no expiry, so it is
modelled as never-expiring: `activation_ns = 0` (1970-01-01) and
`expiration_ns = 2100-01-01T00:00Z` (constants `CONTINUOUS_ACTIVATION_NS` /
`CONTINUOUS_EXPIRATION_NS`). `info["contract_kind"] = "continuous"` and
`info["adjustment"] = "ratio"` record what it really is.

## Price precision and increment

Ratio-adjusted prices are off the exchange tick grid (ES 5922.58, ZN 6 decimals), so the
continuous instrument's `price_increment` is `10^-precision`, not the exchange tick. The
exchange tick stays in the spec (`tick_size`) and in `info` for M3 (slippage/rounding) and
M5 (live orders). Precision follows the bars when bars are given (same rule as
`build_equity`, because Nautilus rejects a bar/instrument precision mismatch), otherwise
`continuous_price_precision`.

## Margin

Nautilus expresses `margin_init`/`margin_maint` as a fraction of notional; the spec holds
dollars per contract. `build_futures_contract(reference_price=...)` converts
(`usd / (price * multiplier)`); without a reference price the margins are left unset and
the dollar values travel in `info`. M3 decides how margin is enforced in the engine.

## Dispatcher

`build_instrument(asset_class, nautilus_id, ticker, bars)` lives beside `build_equity`.
Only `FUTURES` (case-insensitive) is routed to the futures builder; every other value,
including `None`, keeps the existing equity behaviour so no current catalog changes. The
futures path looks the spec up by ticker (= root) and refuses a `nautilus_id` whose venue
disagrees with the spec.

## DB columns

Nullable, no backfill: `multiplier NUMERIC(18,6)`, `tick_size NUMERIC(18,9)`,
`currency VARCHAR(3)`, `contract_kind VARCHAR(20)` with a CHECK of
`continuous|specific` (NULL allowed), `expiry DATE`, `roll_rule VARCHAR(50)`. Equity rows
leave them NULL. M2's importer fills them from the spec.
