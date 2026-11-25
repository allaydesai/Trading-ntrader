# crypto-import Specification

## Purpose
Importing FirstRate crypto files as UTC trade bars and Kraken OHLCVT history into its own catalog:
faithful bars, anomaly reporting, and only spec coins at catalog intervals. Created by archiving
change `add-crypto-backtesting` (`openspec/changes/archive/2026-11-25-add-crypto-backtesting/`).

## Requirements

### Requirement: FirstRate crypto files import as UTC trade bars
The system SHALL import FirstRate crypto files (`YYYY-MM-DD HH:MM:SS,O,H,L,C,V` intraday,
`YYYY-MM-DD,O,H,L,C,V` daily) through `import --format firstrate --asset-class crypto`,
reading timestamps as UTC with no daylight-saving adjustment, stamping each bar at its open,
stamping a daily row dated `D` at `D 00:00 UTC`, and writing `-LAST-EXTERNAL` bars at the
spec's fixed price precision with 8-decimal volume.

#### Scenario: hourly bars across a US DST change
- **WHEN** the BTC hourly rows for 2024-03-10 are imported
- **THEN** 24 bars are written for that date, each at the file's clock time in UTC

#### Scenario: daily bar timestamp
- **WHEN** the daily row `2026-05-01,...` is imported
- **THEN** its bar opens at 2026-05-01 00:00 UTC

#### Scenario: extra price precision
- **WHEN** a 1-minute row carries `534.96827`
- **THEN** it is stored as `534.9683` and counted in the per-file rounding warning

### Requirement: Crypto imports are faithful and report anomalies
The importer SHALL keep every valid source row, SHALL NOT fill gaps or alter volume, and SHALL
report as warnings the gaps longer than ten bars, zero-volume bars and the first bar from which
all volumes are whole numbers. A coin without a spec SHALL fail only its own files.

#### Scenario: volume rounding regime
- **WHEN** a file's volumes become whole numbers from 2026-03-21 00:00 UTC onwards
- **THEN** the import succeeds and its result warns that volume is whole-coin from that bar

#### Scenario: row counts
- **WHEN** all three coins and five timeframes are imported into an empty catalog
- **THEN** each series' bar count equals its source file's line count, and a re-run skips them

### Requirement: Kraken OHLCVT history imports into its own catalog
The system SHALL import Kraken's downloadable OHLCVT files (`{ALTNAME}_{minutes}.csv`,
headerless `timestamp,open,high,low,close,volume,trades`, UNIX seconds at the bar open) through
`import --format kraken --asset-class crypto --catalog <name>`, as the same
`{COIN}/USD.KRAKEN-...-LAST-EXTERNAL` bars, rounding, warnings and re-run rules a FirstRate
crypto import uses.

#### Scenario: Kraken BTC history
- **WHEN** `XBTUSD_60.csv` is imported into `kraken-crypto`
- **THEN** its bars are `BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL`, stamped at each row's UNIX
  second in UTC, the count equals the file's line count, and a re-run skips the series

#### Scenario: whole archive directory
- **WHEN** the source directory also holds `DOGEUSD_60.csv` and `XBTUSD_15.csv`
- **THEN** neither is imported and the import does not fail

### Requirement: Kraken imports take only spec coins and catalog intervals
A Kraken import SHALL map a file to a coin by the coin spec's Kraken altname, import only the
1, 5, 60 and 1440-minute intervals, and ignore other pairs and intervals. A 30-minute request
or another asset class SHALL exit 2.

#### Scenario: a 30-minute request
- **WHEN** `import --format kraken --timeframe 30min` runs
- **THEN** it exits 2 because Kraken publishes no 30-minute candles
