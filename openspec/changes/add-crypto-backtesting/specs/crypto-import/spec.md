## ADDED Requirements

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
