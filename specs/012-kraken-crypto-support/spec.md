# Feature Specification: Kraken Crypto Data Support

**Feature Branch**: `012-kraken-crypto-support`
**Created**: 2026-02-28
**Status**: Draft
**Input**: User description: "Add support for crypto using kraken api and its python library https://github.com/btschwertfeger/python-kraken-sdk I would like to run backtests using kraken data feed. Later on live trading support will also be added with Kraken exchange."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fetch Historical Crypto Data from Kraken (Priority: P1)

A trader wants to download historical OHLCV bar data for a cryptocurrency pair (e.g., BTC/USD) from the Kraken exchange so they can backtest trading strategies against crypto markets.

**Why this priority**: Without historical data, no backtesting is possible. This is the foundational capability that all other features depend on.

**Independent Test**: Can be fully tested by requesting historical data for a crypto pair over a date range and verifying bars are stored locally, ready for backtesting.

**Acceptance Scenarios**:

1. **Given** valid Kraken credentials and a supported crypto pair, **When** the user requests historical bar data for a date range, **Then** the system fetches OHLCV bars from Kraken and persists them in the local data catalog.
2. **Given** historical data already exists locally for the requested pair and date range, **When** the user requests the same data again, **Then** the system returns cached data without re-fetching from Kraken.
3. **Given** a partially cached date range, **When** the user requests a broader range, **Then** the system detects gaps, fetches only the missing data from Kraken, and merges it with the existing cache.
4. **Given** Kraken API rate limits are approached, **When** multiple data requests are in progress, **Then** the system throttles requests to stay within Kraken's rate limits without failing.

---

### User Story 2 - Run Backtests with Kraken Crypto Data (Priority: P2)

A trader wants to run their existing or new trading strategies against Kraken crypto market data, using the same backtest workflow they already use for equities.

**Why this priority**: This delivers the core value — turning historical data into actionable strategy insights. Depends on P1 for data availability.

**Independent Test**: Can be fully tested by running a backtest with a known strategy against previously fetched Kraken data and verifying results (trades, PnL, metrics) are produced.

**Acceptance Scenarios**:

1. **Given** Kraken historical data is available for BTC/USD, **When** the user runs a backtest specifying Kraken as the data source and a valid strategy, **Then** the backtest completes and produces performance results (trades, equity curve, standard metrics).
2. **Given** a strategy originally designed for equities, **When** the user runs it against crypto data, **Then** the system correctly handles crypto-specific properties (fractional quantities, 24/7 market hours, crypto instrument identifiers).
3. **Given** a backtest request via CLI or web UI, **When** the user specifies "kraken" as the data source, **Then** the system routes data loading through the Kraken data pipeline transparently.

---

### User Story 3 - Configure Kraken Connection Settings (Priority: P3)

A trader wants to securely configure their Kraken API credentials and connection preferences so the system can authenticate with the Kraken exchange.

**Why this priority**: Configuration is required for P1 to work, but it's a one-time setup task with lower ongoing user interaction.

**Independent Test**: Can be fully tested by providing Kraken API credentials via environment variables and verifying the system accepts and validates them without exposing secrets.

**Acceptance Scenarios**:

1. **Given** Kraken API key and secret are set as environment variables, **When** the system starts, **Then** it loads and validates the credentials without exposing them in logs or configuration output.
2. **Given** missing or invalid Kraken credentials, **When** the user attempts to fetch data, **Then** the system provides a clear error message indicating the configuration issue.
3. **Given** the user wants to switch between data sources (IBKR and Kraken), **When** both are configured, **Then** the system allows selecting either source per backtest without conflict.

---

### Edge Cases

- What happens when Kraken API is temporarily unavailable? The system retries with exponential backoff and reports a clear error after exhausting retries.
- What happens when the user requests a crypto pair not supported by Kraken? The system validates the pair against Kraken's available instruments and returns a descriptive error before attempting to fetch.
- What happens when Kraken returns partial or malformed data? The system validates data integrity, quarantines corrupted records, and reports what was successfully stored.
- What happens when fetching very large date ranges (years of data)? The system chunks the request into manageable time windows to avoid timeouts and memory issues.
- What happens with crypto market gaps (exchange maintenance windows)? The system treats maintenance gaps as expected and does not flag them as data errors.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST fetch historical OHLCV bar data from the Kraken exchange for any Kraken-supported spot trading pair.
- **FR-002**: System MUST persist fetched Kraken data in the local data catalog using the same storage format as existing data sources.
- **FR-003**: System MUST cache fetched data locally and serve subsequent requests from cache when available, avoiding redundant API calls.
- **FR-004**: System MUST detect gaps in locally cached data and fetch only the missing portions from Kraken.
- **FR-005**: System MUST enforce Kraken API rate limits to prevent request throttling or account penalties.
- **FR-006**: System MUST support running backtests against Kraken crypto data using the same workflow as existing data sources (CLI and web UI).
- **FR-007**: System MUST handle crypto-specific properties: fractional position sizes, 24/7 trading hours, and crypto instrument identifiers.
- **FR-008**: System MUST load Kraken API credentials exclusively from environment variables, never from hardcoded values or configuration files checked into version control.
- **FR-009**: System MUST validate Kraken API credentials at configuration load time and provide clear error messages for missing or invalid credentials.
- **FR-010**: System MUST support multiple bar timeframes (1-minute, 5-minute, 15-minute, 1-hour, 4-hour, 1-day) for historical data retrieval.
- **FR-011**: System MUST allow the user to select "kraken" as a data source in backtest requests, routing data operations through the Kraken data pipeline.

### Key Entities

- **Kraken Credentials**: API key and secret pair used to authenticate with the Kraken exchange. Loaded from environment, never persisted in plaintext.
- **Crypto Instrument**: A tradeable cryptocurrency pair on Kraken (e.g., BTC/USD, ETH/USD). Includes properties like minimum order size, tick size, and lot precision.
- **Historical Bar**: A single OHLCV candlestick record for a crypto instrument at a given timeframe. Compatible with the existing bar storage format.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can fetch historical crypto data for any Kraken spot pair and have it available for backtesting within a single command or UI action.
- **SC-002**: Backtests using Kraken crypto data produce the same categories of results (trades, metrics, equity curves) as backtests using existing data sources.
- **SC-003**: Repeated requests for the same data range complete in under 2 seconds when data is already cached locally.
- **SC-004**: The system respects Kraken API rate limits, with zero rate-limit violations during normal operation (fetching data for up to 10 pairs sequentially).
- **SC-005**: All Kraken credentials remain secure — never appearing in logs, error messages, or version-controlled files.
- **SC-006**: Users can switch between IBKR and Kraken data sources per backtest without reconfiguration or system restart.

## Scope & Boundaries

### In Scope

- Historical OHLCV bar data retrieval from Kraken Spot API
- Local caching and gap detection for Kraken data
- Backtest execution using Kraken data through existing engine
- Kraken credential management via environment variables
- CLI and web UI support for selecting Kraken as data source

### Out of Scope (Future Phases)

- Live trading / order execution via Kraken (planned for future phase)
- Kraken Futures market data
- Kraken WebSocket real-time streaming
- Portfolio management across multiple exchanges simultaneously
- Kraken account management (deposits, withdrawals, transfers)

## Assumptions

- Users have an active Kraken account with API access enabled
- Kraken's public and private REST API endpoints are available and stable
- The python-kraken-sdk library (https://github.com/btschwertfeger/python-kraken-sdk) is the designated client library for Kraken API access
- Historical OHLCV data from Kraken can be converted to the same internal bar format used by the existing backtest engine
- Kraken's API rate limits are sufficient for practical backtesting data retrieval (not requiring paid tiers)
- Spot trading pairs are the initial focus; futures support may follow in a later phase

## Dependencies

- python-kraken-sdk library for Kraken API communication
- Existing data catalog infrastructure for storage and caching
- Existing backtest engine for strategy execution
- Kraken exchange API availability
