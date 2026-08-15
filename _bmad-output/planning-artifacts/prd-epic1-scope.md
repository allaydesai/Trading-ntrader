# PRD Extract — Phase 3, Epic 1 Scope Only

> **Why this file exists:** the harness's traceability gate (`_trace_gate` in
> `bmad_harness.py`) feeds the whole `prd_path` document to a reviewer and demands
> every acceptance criterion in it map to a passing test. The full `prd.md` covers
> all five Phase-3 epics (FR1–FR53); this run implements Epic 1 only (FR1–FR12).
> Feeding the reviewer the full document makes the gate structurally unpassable —
> it always reports Epic 2–5 criteria as unmet. This extract scopes the gate to
> what this run actually built: Epic 1's functional requirements, the NFRs that
> govern it, and the 1.1–1.7 acceptance criteria. See `planning/005-ntrader-p3-epic1-run.md`
> (bmad_harness project) and `planning/004-enhancement-backlog.md` for the general
> fix this stands in for.

## Scope Boundary

This run delivers **Epic 1: Live Broker Connection & Real-Money Safety Gate** only.
The operator can point NTrader at an IBKR **paper** account, connect as both a data
source and an execution destination, and receive real-time RTH bars — while a
real-money account is refused at startup before any socket opens. Crossing into
real money requires two independent, deliberate declarations and cannot be reached
by a port typo, a stale env var, or a copied command.

**Standalone deliverable:** connects to IBKR paper, streams real-time bars, and
refuses a live account with a distinct exit code. **No orders exist yet** — order
submission is Epic 3's scope and is explicitly out of bounds here.

## Functional Requirements — Live Connectivity & Market Data (E1)

- **FR1:** System can connect to Interactive Brokers as a live data source and receive real-time bars
  for subscribed instruments.
- **FR2:** System can connect to Interactive Brokers as an execution destination capable of submitting
  and tracking orders.
- **FR3:** System can operate on real-time market data rather than delayed data.
- **FR4:** System can restrict trading activity to regular trading hours, matching the session basis of
  the backtest data.
- **FR5:** System can maintain live broker connections without conflicting with a historical-data
  client operating concurrently.
- **FR6:** System can detect loss of broker connectivity and refrain from submitting orders while
  disconnected.
- **FR7:** System can run a live trading process alongside the existing backtest and data-fetch
  components without trading-framework initialization conflicts.

## Functional Requirements — Trading Safety & Account Gating (E1)

- **FR8:** System can determine whether the configured broker connection targets a paper account or a
  real-money account.
- **FR9:** System can refuse to start a session against a real-money account, before establishing any
  connection and before any order can be submitted.
- **FR10:** Operator can deliberately authorize real-money trading through an explicit mechanism that
  cannot be triggered by configuration error alone.
- **FR11:** System can report a safety refusal distinguishably from other startup failures.
- **FR12:** System can enable order submission for sessions permitted to trade, while defaulting to
  non-trading operation.

## Non-Functional Requirements Governing Epic 1

Selected from the full NFR set (`prd.md`) as the ones Epic 1 is accountable for.

### Reliability
- **No blind trading:** zero orders submitted while the execution client is disconnected or while
  reconciliation is incomplete.
- **Failure isolation:** a failure in one strategy does not terminate the session or affect other
  strategies within it.

### Integration (IBKR)
- **Rate-limit compliance:** the existing 45 req/s discipline governs live requests; historical warm-up
  requests respect IBKR's pacing rules.
- **Market data line budget:** a session's instrument count stays within the account's concurrent
  subscription limit. Exceeding it fails at startup with a clear message rather than silently dropping
  subscriptions.
- **Client ID isolation:** the live session never contends with the historical-data client for a client
  ID.
- **Gateway restart tolerance:** the daily restart/auto-logoff is handled as an expected event, not an
  error condition.
- **Safe degradation:** when IBKR is unreachable, the session halts trading safely and reports it. It
  never estimates, assumes, or proceeds on stale state.

### Security
- **Credential hygiene:** connection settings and credentials exclusively via typed environment
  configuration; never in a session specification, never committed, never logged.
- **Account identifiers are not logged in full.**
- **Elevated blast radius acknowledged:** the same credentials that previously permitted only data reads
  now authorize order submission. No new secrets are introduced, but `ibkr_read_only=False` is only ever
  set for a session that has passed the paper/live gate — the gate, not `read_only`, is the load-bearing
  control.

### Testability
- **Automated tests never touch a real broker.** Tested against broker doubles across the unit /
  component / integration (`--forked`) tiers.
- **Broker-dependent behavior is verified by operator-run procedures against the real paper account.**
  These are phase-gate evidence and cannot live in CI.
- **The safety gate has an automated test**, since it must hold regardless of environment.

## Epic 1: Live Broker Connection & Real-Money Safety Gate

**Covers:** FR1–FR12 · NFR15, NFR16, NFR17, NFR19, NFR20, NFR26, NFR27, NFR34 · AR2, AR13–AR22

### Story 1.1: Refuse a Real-Money Account Before Anything Connects

**Acceptance Criteria:**

**Given** `evaluate_gate(settings, cli_flags)` in `src/core/live_gate.py`
**When** it is called with any combination of settings
**Then** it returns a `GateDecision` without opening a socket, reading a file, or querying a database
(AR16)
**And** the module imports nothing from `nautilus_trader`, SQLAlchemy, or any I/O library.

**Given** settings where `ibkr_trading_mode == "paper"`, `ibkr_port` ∈ {7497, 4002}, and `tws_account`
is empty or begins with `DU`/`DF`
**When** the gate is evaluated
**Then** the decision permits the connection.

**Given** settings failing **any** one of those conditions — mode is `live`, or the port is 7496/4001
or any unrecognised port, or `tws_account` is set to a non-paper prefix
**When** the gate is evaluated
**Then** the decision is a refusal carrying a `GateRefusal` reason naming the specific failing
condition
**And** an unrecognised port refuses rather than defaults to permitted (fail-closed).

**Given** the operator intends real-money trading
**When** the gate is evaluated with `--real-money` set but `NTRADER_REAL_MONEY_ACCOUNT` unset, or
with the env var set but the CLI flag absent, or with both present but the env value not matching
the target account exactly
**Then** every one of those combinations refuses
**And** only both-present-and-exactly-matching permits (FR10, AR15)
**And** no interactive prompt exists on this path — absence of either declaration is refusal, not a
question.

**Given** a refusal is produced
**When** its message is rendered
**Then** any account identifier in it is masked to its last 3 characters (NFR26).

**Given** the test suite
**When** `tests/unit/core/test_live_gate.py` runs
**Then** it covers the full truth table including every refusal path, and requires no broker,
network, or database (NFR34, NFR32).

### Story 1.2: Isolate the Live Client ID from the Historical Data Client

**Acceptance Criteria:**

**Given** `IBKRSettings`
**When** the settings are loaded
**Then** a new `ibkr_live_client_id` field exists with default `10`, configurable via env, while
`ibkr_client_id` keeps its default of `1` (FR5, AR18).

**Given** a configuration where `ibkr_live_client_id` equals `ibkr_client_id`
**When** `IBKRSettings` is instantiated
**Then** a Pydantic validation error is raised at construction naming both fields
**And** the error is raised by a model validator, so it fires regardless of which field was
overridden.

**Given** the documented client-ID reservation
**When** a developer reads the field descriptions
**Then** they state the allocation: historical = `ibkr_client_id`, live session =
`ibkr_live_client_id`, on-demand reconcile = `ibkr_live_client_id + 1` (AR34, reserved here and
consumed in Epic 4).

### Story 1.3: Assemble and Start a TradingNode Against IBKR Paper

**Acceptance Criteria:**

**Given** `src/core/live_node_builder.py`
**When** it builds a node
**Then** it produces a `TradingNodeConfig` using `InteractiveBrokersDataClientConfig` and
`InteractiveBrokersExecClientConfig`, registering `InteractiveBrokersLiveDataClientFactory` and
`InteractiveBrokersLiveExecClientFactory` (FR2, AR2).

**Given** the builder is invoked
**When** it begins assembling client configuration
**Then** it calls `evaluate_gate()` first and raises without constructing any client config if the
decision is a refusal — so no connecting code is reachable past a failed gate (FR9).

**Given** the gate has permitted the connection
**When** the node is configured
**Then** `ibkr_read_only` is set to `False` scoped to this process only, and never read as a safety
control anywhere (FR12, NFR27, AR17, AR43).

**Given** the node initialises Nautilus logging
**When** it starts
**Then** its log guard is registered through the existing `set_nautilus_log_guard()` before any
other Nautilus component initialises in-process (FR7, AR20)
**And** an integration test under `--forked` starts a node in a process that has already run a
`BacktestEngine` and observes no C-logging double-init panic.

**Given** the node's client ID
**When** the data and execution clients connect
**Then** both use `ibkr_live_client_id`, not `ibkr_client_id` (FR5).

**Given** the node is stopped
**When** shutdown completes
**Then** the process exits without leaking a running event loop or an unclosed connection, and a
second node can be built in a fresh process without inheriting `BacktestEngine`'s single-use
constraint.

**Given** the whole epic
**When** dependency files are reviewed
**Then** no new dependency has been added — `pyproject.toml` and `uv.lock` are unchanged, since the
IB live adapter and Redis cache backend already ship with the installed `nautilus-trader` 1.220.0
(AR3).

### Story 1.4: Verify the Connected Account Is a Paper Account Before Trading Starts

**Acceptance Criteria:**

**Given** the node has connected and the gateway has reported its account ID
**When** the startup sequence reaches the `gate:account` phase
**Then** the reported account ID must carry a paper prefix (`DU`/`DF`) for the sequence to continue
(FR8, AR14).

**Given** the reported account ID does not carry a paper prefix
**When** verification runs
**Then** the node shuts down immediately, no strategy is started, and the same refusal outcome as
the static gate is produced (FR9).

**Given** account verification runs
**When** it logs its result
**Then** the account identifier appears masked to its last 3 characters (NFR26).

**Given** the verification is placed in the startup sequence
**When** its position is inspected
**Then** it runs after connection and strictly before any strategy is started (AR39).

### Story 1.5: Receive Real-Time RTH Bars for a Configured Instrument

**Acceptance Criteria:**

**Given** a session-configured node
**When** market data is requested
**Then** the market data type is `REALTIME`, explicitly overriding the `DELAYED_FROZEN` default that
exists for paper data fetching (FR3, AR21)
**And** a session that cannot obtain real-time data fails loudly rather than silently falling back to
delayed.

**Given** an instrument subscription
**When** bars are delivered
**Then** they are restricted to regular trading hours via `ibkr_use_rth=True` (FR4, AR21).

**Given** a subscribed instrument
**When** a bar closes at the venue
**Then** the bar is delivered to the node and logged with its instrument and timestamp (FR1).

**Given** a session whose instrument count exceeds the account's concurrent market-data-line budget
**When** the session starts
**Then** it fails at startup with a message naming the limit and the requested count, rather than
silently dropping subscriptions (NFR16, NFR30).

**Given** historical or streaming requests are issued
**When** request volume is measured
**Then** the existing 45 req/s pacing discipline governs them (NFR15).

### Story 1.6: Detect Connection Loss and Withhold Trading Permission

**Acceptance Criteria:**

**Given** an established broker connection
**When** the connection drops
**Then** a `connection.lost` event is logged with the session identifier bound (FR6, AR41)
**And** the runner's trading-permitted state becomes false.

**Given** the connection is re-established
**When** recovery completes
**Then** `connection.restored` is logged and trading permission is restored only after state is
re-established, never on reconnect alone (NFR10).

**Given** a transient disconnect
**When** reconnection is measured
**Then** it completes within 60 seconds, and the elapsed time is observable in the logs (NFR4).

**Given** IB Gateway performs its scheduled daily restart
**When** the session observes the disconnect
**Then** it is handled as an expected event on the normal reconnect path, not raised as an error
condition (NFR19).

**Given** the broker is unreachable for longer than the reconnect window
**When** the session evaluates its state
**Then** it halts trading and reports it, and never proceeds on stale state (NFR20).

**Note:** the order path that consults this permission flag arrives in Epic 3; this story delivers
the detection and the flag, verified directly against broker doubles.

### Story 1.7: Check Broker Connectivity and the Gate from the CLI

**Acceptance Criteria:**

**Given** the `ntrader` CLI
**When** `ntrader live check` is run
**Then** it evaluates the gate, connects, verifies the account, subscribes to a configured
instrument, prints the bars it receives, disconnects cleanly, and exits `0` (FR1–FR5).

**Given** a configuration that the gate refuses
**When** `ntrader live check` is run
**Then** it prints the specific refusal reason and exits with code **3**, distinct from every other
failure (FR11, AR28)
**And** no connection attempt appears in the logs.

**Given** the gate passes but the broker is unreachable
**When** the command runs
**Then** it exits with code **4**, so a script can tell "refused to trade a live account" apart from
"failed to connect" (FR11, AR28).

**Given** the command's output
**When** it streams
**Then** it uses structured `structlog` console output consistent with existing CLI commands (FR48).

**Given** the `live` group is registered
**When** `ntrader live --help` is shown
**Then** `check` is listed, and the group is wired into `src/cli/main.py` alongside the existing
command groups.
