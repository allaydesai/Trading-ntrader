## ADDED Requirements

### Requirement: FirstRate futures files import through the CLI
`import --format firstrate --asset-class futures <dir>` SHALL import every
`{ROOT}_full_{timeframe}_*.txt` file in `<dir>` (or one level below) whose root has a contract
spec, defaulting to all five timeframes, without a `company_profiles.csv`.

#### Scenario: daily file with open interest
- **WHEN** a 7-column daily file `ES_full_1day_...txt` is imported
- **THEN** every row becomes a bar on `ES.CME-1-DAY-LAST-EXTERNAL` and the open interest
  column is ignored

#### Scenario: unknown root
- **WHEN** a file's root has no contract spec
- **THEN** that file's result is `failed` naming the unknown root and other roots still import

### Requirement: Futures prices use one fixed precision per root
Every price the futures parser emits for a root SHALL have the spec's
`continuous_price_precision`, padding shorter values and rounding longer ones half-even.

#### Scenario: mixed decimals
- **WHEN** an ES file contains `5918.9` and `5918.99`
- **THEN** both are stored and read back at precision 2 (`5918.90`, `5918.99`)

### Requirement: Futures timestamps convert like equities
The futures parser SHALL convert US/Eastern wall-clock timestamps to UTC exactly as the
equity parser does, including DST transitions, and stamp bars at the bar open.

#### Scenario: Globex open
- **WHEN** a row is stamped `2025-07-06 18:00:00`
- **THEN** its `ts_event` and `ts_init` are 2025-07-06 22:00:00 UTC

### Requirement: Futures import streams and verifies without a full read-back
The futures importer SHALL parse and write a file in bounded chunks, one Parquet part per
chunk, and SHALL verify the write from Parquet footer row counts and sampled bars.

#### Scenario: ES 1-minute
- **WHEN** the 6.4M-row ES 1-minute file is imported
- **THEN** peak RSS stays well under 1.5 GB and the catalog row count equals the source rows

### Requirement: Futures re-runs are idempotent
A re-run SHALL skip a root/timeframe whose recorded end matches the source and whose Parquet
footers hold the recorded bar count, and SHALL otherwise overwrite it without duplicates.

#### Scenario: interrupted write
- **WHEN** a bar type's Parquet parts hold fewer rows than `catalog_instruments` records
- **THEN** the next run re-imports it

### Requirement: Futures catalog rows carry contract fields
Futures `catalog_instruments` rows SHALL have `asset_class = FUTURES`, `ticker = ROOT`,
`nautilus_id = ROOT.VENUE` and the spec's `multiplier`, `tick_size` and `currency`, with
`contract_kind = continuous` and `roll_rule = firstrate-ratio-adjusted`.

#### Scenario: ES row
- **WHEN** ES is imported into `firstrate-futures`
- **THEN** its row has nautilus id `ES.CME`, multiplier 50, tick size 0.25, currency USD

### Requirement: Equity imports keep every decimal
The equity/ETF FirstRate parser SHALL emit every price of a file at one precision, the widest
any price in the file carries, so the catalog never truncates a price on read-back.

#### Scenario: mixed decimals in a stock file
- **WHEN** a stock file contains `5918.9` and `5918.99`
- **THEN** both bars read back from the catalog as `5918.90` and `5918.99`
