# fx-import Specification

## Purpose
Importing FirstRate spot FX bar files into a Parquet catalog: New York session timestamps, fixed
per-pair precision, session-anomaly reporting, streaming and idempotent re-runs. Created by
archiving change `add-fx-backtesting` (`openspec/changes/archive/2026-11-23-add-fx-backtesting/`).

## Requirements
### Requirement: FirstRate FX files import through the CLI
`import --format firstrate --asset-class fx <dir>` SHALL import every `{PAIR}_full_{timeframe}.txt`
file in `<dir>` (or one level below) whose pair has a spec, defaulting to all five timeframes,
reading 7-field intraday rows (`YYYYMMDD,HH:MM:SS,O,H,L,C,V`) and 6-field daily rows
(`YYYYMMDD,O,H,L,C,V`), and writing `MID` bars.

#### Scenario: hourly file
- **WHEN** `EURUSD_full_1hour.txt` is imported into `firstrate-fx`
- **THEN** its rows become bars on `EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL` and the catalog
  row has `ticker EURUSD`, `asset_class FX`, `currency USD`

#### Scenario: unknown pair
- **WHEN** a file's pair has no spec
- **THEN** that file fails naming the pair, and the other pairs still import

### Requirement: FX timestamps follow the New York session
Intraday FX rows SHALL be converted from US/Eastern wall clock to UTC like the futures parser
and stamped at the bar open. A daily row dated `D` SHALL be stamped at its session open,
`D-1 17:00` America/New_York.

#### Scenario: Sunday open
- **WHEN** an hourly row is `20250706,17:00:00,...`
- **THEN** its `ts_event` is 2025-07-06 21:00:00 UTC

#### Scenario: Monday daily bar
- **WHEN** a daily row is dated `20250707`
- **THEN** its `ts_event` is 2025-07-06 21:00:00 UTC (Sunday 17:00 EDT)

### Requirement: FX prices use one fixed precision per pair
Every price the FX parser emits SHALL have the pair's spec precision, padding stripped zeros
and rounding longer values half-even. Rows changed by rounding SHALL be counted in the
result warnings.

#### Scenario: float noise
- **WHEN** a USDJPY row carries `108.33599`
- **THEN** it is stored as `108.336` and counted as rounded

### Requirement: Session anomalies are kept and reported
The FX importer SHALL keep bars outside Sunday 17:00 to Friday 17:00 New York (Saturday bars,
off-hour Sunday opens, Sunday-dated daily rows) and SHALL report their count as warnings.

#### Scenario: Saturday bar
- **WHEN** the EURUSD hourly file contains a bar at 2023-08-05 19:00 ET
- **THEN** it is imported and the result warns of 1 session anomaly

### Requirement: FX import streams, verifies and is idempotent
The FX importer SHALL write in bounded chunks with footer-count and sampled read-back
verification, and SHALL skip a pair/timeframe whose recorded end and on-disk count match the
source, as the futures importer does.

#### Scenario: 1-minute file
- **WHEN** the 6.05M-row EURUSD 1-minute file is imported
- **THEN** peak RSS stays under 1.5 GB and the catalog holds 6,050,178 bars
