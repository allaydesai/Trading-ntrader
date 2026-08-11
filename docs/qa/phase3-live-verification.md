# Phase 3 Live Verification Procedures

**Status**: Active — appended to as new Phase 3 stories require operator-run evidence.

## What this file is

Phase 3 (paper trading against IBKR) introduces behaviour that genuinely cannot be exercised in
CI: connecting to a live TWS/Gateway process, holding a real socket open, and confirming a clean
process shutdown. NFR32 keeps automated tests from ever touching a real broker, and NFR33 requires
that behaviour to be verified some other way. This file is that other way — a log of
operator-run procedures, one per story that introduces broker-dependent behaviour, each with its
preconditions, the exact command, the expected output, and the pass criteria it is evidence for.

A procedure's result is recorded here (or in the story's Dev Agent Record) only when it was
actually run against a live gateway. A dry run — the script parses its arguments, imports
cleanly, or fails the gate as expected with no gateway running — is not evidence that the
procedure passed; it is evidence that the tooling exists to run it.

## Procedure P1: build, start and stop a node against IBKR paper

**Introduced by**: Story 1.3 — Assemble and Start a TradingNode Against IBKR Paper
**Verifies**: AC #6 — the process exits with no lingering event loop or unclosed connection after
shutdown, and a second invocation in a fresh process succeeds without inheriting
`BacktestEngine`'s single-use constraint.
**Tool**: `scripts/diagnostics/live_node_probe.py`

### Preconditions

- IB Gateway or TWS is running and logged into a **paper** account, listening on the port
  configured by `IBKR_PORT` in `.env` (Gateway paper = `4002`, TWS paper = `7497`).
- `.env` has `TWS_ACCOUNT`, `IBKR_HOST`, `IBKR_PORT`, `IBKR_TRADING_MODE=paper`,
  `IBKR_LIVE_CLIENT_ID` set to values that pass the Layer 1 gate (`src/core/live_gate.py`) — the
  probe refuses before opening any socket if they do not, and reports `RESULT: fail
  reason=gate_refused ...` rather than attempting a connection.
- No other process is currently holding `IBKR_LIVE_CLIENT_ID` on the Gateway (a prior run that
  was killed rather than stopped cleanly can leave the id reserved for tens of seconds).

### Command

```bash
PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py --run-seconds 5
```

### Expected output

```
[probe] building node host=127.0.0.1 port=4002 client_id=10 trader_id=PAPER-PROBE0001
[probe] node built (config + LogGuard); building clients...
[probe] starting node...
[probe] waiting up to 300s for engines to connect...
[probe] connected (data + exec)
[probe] running for 5s...
[probe] stopping and disposing node...
[probe] disposed loop.is_running=False loop.is_closed=True
RESULT: ok mode=build-connect-run-stop loop_closed=True elapsed=<N>.NN
```

The process exits with code `0`.

`RESULT: ok` is only printed when **both engines actually reported connected**. This matters:
`kernel.start_async()` does not raise when a connection fails (`system/kernel.py:1012-1013` logs a
warning and returns), so a probe that only checked "nothing raised" would print `ok` for a node
that never reached the Gateway. The wait is a poll against `IBKR_CONNECTION_TIMEOUT`, not a fixed
sleep, so a healthy local Gateway still connects in well under a second.

Failure modes and their exit codes:

| Output | Meaning | Exit |
| --- | --- | --- |
| `RESULT: fail reason=gate_refused refusal=<reason>` | Layer 1 gate refused; no socket was opened | 1 |
| `RESULT: fail reason=config_error msg=...` | Unusable configuration (bad `trader_id`, empty `TWS_ACCOUNT`, non-positive timeout) | 1 |
| `RESULT: fail reason=ProbeError msg=engines did not connect...` | Gateway not reachable on the configured port, or not logged in | 1 |
| `RESULT: fail reason=interrupted` | Operator pressed Ctrl-C | 130 |
| `RESULT: fail reason=<Type> msg=...` | Anything else; full traceback on stderr | 1 |

### Pass criteria (AC #6)

1. **Clean exit** — after `RESULT: ok ...` prints, the shell prompt returns immediately; no
   lingering asyncio event loop or open socket keeps the process alive (verify with
   `lsof -p <pid>` before the process exits if timing needs confirming, or simply that the
   command does not hang after printing `[probe] disposed`).
2. **No single-use carryover** — running the same command a second time, in a fresh process,
   succeeds identically. `TradingNode` is not subject to `BacktestEngine`'s single-use-per-process
   constraint (CLAUDE.md Gotcha #4 is a `BacktestEngine`-specific rule, not a Nautilus-wide one),
   and this run is the evidence for that claim in this codebase specifically.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-07 | — | ℹ️ not re-run | Story 1.4 added an **opt-in** `--verify-account` flag to this probe. With the flag absent — which is P1's documented command — the code path, the printed lines and the `RESULT:` line are unchanged, so the ⚠️ re-run below is still the outstanding item for P1 and this story neither satisfies nor invalidates it. |
| 2026-08-05 (post-review) | — | ⚠️ **re-run required** | Code review hardened the probe after this procedure last passed: it now polls until both engines report connected before printing `RESULT: ok` (previously `ok` was printed whenever nothing raised, which `kernel.start_async()` does not do on connection failure), drives shutdown from a `finally` so a mid-run failure can no longer leak the socket, loop and kernel thread pool, rejects `--run-seconds < 1`, handles Ctrl-C, and passes an explicit event loop to `build_trading_node()`. The 2026-08-05 pass below remains valid evidence for AC #6 — the node genuinely connected, reconciled and shut down cleanly per its logs — but it was produced by the *older* probe. Re-run P1 once a Gateway is next available to confirm the hardened probe still reports `ok`, and record the result here. No `live_node_builder.py` behaviour that AC #6 depends on was changed. |
| 2026-08-05 | Allay (Gateway made available mid-session) | ✅ ok | First run surfaced a real bug: the probe's original `asyncio.run(...)`-wrapped implementation called the synchronous `node.dispose()` from inside a coroutine still executing on that same running loop. `TradingNode.dispose()` (`nautilus_trader/live/node.py:451-458`) calls `loop.stop()` whenever it finds the loop running — correct when `dispose()` runs after `node.run()` has already returned control, fatal here: the loop stopped mid-flight and `asyncio.run()`'s own cleanup raised `RuntimeError: Event loop stopped before Future completed.` (`RESULT: fail reason=RuntimeError ...`, exit 1) *after* the node had already connected, found account `DU4076626`, reconciled (0 discrepancies — it also surfaced one pre-existing residual position from earlier manual activity, unrelated to this story), run, and shut down every engine/client cleanly per the logs. Fixed the probe (not `live_node_builder.py` — the bug was entirely in how the script drove the node's loop) to own an explicit `asyncio.new_event_loop()` end to end and call `node.stop()` → `node.dispose()` only after `run_until_complete` had already returned control, matching the "normal with asyncio.run" branch `dispose()`'s own source comments describe. Re-ran twice after the fix: both **exit code 0**, both `RESULT: ok mode=build-start-stop elapsed=…`, both ending in `loop.is_running=False` / `loop.is_closed=True`. The second of those two runs is the AC #6 pass-criterion-2 evidence — a fresh process reconnected with the same `client_id=10` immediately after the prior process's clean shutdown, no stale-session delay, no single-use carryover. |

## Procedure P2: verify the connected account at the `gate:account` phase

**Introduced by**: Story 1.4 — Verify the Connected Account Is a Paper Account Before Trading
Starts
**Verifies**: story AC **#1**, **#6** and **#7** — the account the gateway *actually* reports
carries a paper prefix (#1), the verification sits between `node:connect` and anything resembling
trading per AR39 (#6), and the account identifier is masked to its last 3 characters wherever
*this codebase* renders it (#7 / NFR26). AC numbers throughout this section are the **story file's**,
matching how P1 cites Story 1.3's.
**Tool**: `scripts/diagnostics/live_node_probe.py --verify-account`

P1's tool, one opt-in flag. The flag is opt-in precisely so P1's command keeps the behaviour it was
verified with: without `--verify-account` nothing about this probe changed.

### Preconditions

- All of P1's preconditions, unchanged.
- The gateway is logged into a **paper** account, and *only* paper accounts. Layer 2 requires
  **every** account the gateway names to carry a `DU`/`DF` prefix — a gateway that also manages a
  real-money account is refused by design, and that refusal is the procedure working, not failing.

### Command

```bash
PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py --run-seconds 5 --verify-account
```

### Expected output

```
[probe] building node host=127.0.0.1 port=4002 client_id=10 trader_id=PAPER-PROBE0001
[probe] node built (config + LogGuard); building clients...
[probe] starting node...
[probe] waiting up to 300s for engines to connect...
[probe] connected (data + exec)
[probe] verifying connected account (phase=gate:account)...
[probe] gate:account ok mode=paper accounts=***626
[probe] running for 5s...
[probe] stopping and disposing node...
[probe] disposed loop.is_running=False loop.is_closed=True
RESULT: ok mode=build-connect-run-stop loop_closed=True gate_account=paper elapsed=<N>.NN
```

The process exits with code `0`.

Note what `accounts=***626` is and is not: it is the set the **gateway** named in its
`managedAccounts` message, not the `TWS_ACCOUNT` the probe was configured with. Those two agreeing
is the point of the procedure — if the configuration were the source, the check would be verifying
itself.

Failure modes and their exit codes (P1's table still applies; these are the additions):

| Output | Meaning | Exit |
| --- | --- | --- |
| `RESULT: fail reason=account_gate_refused refusal=reported_account_not_paper` | The gateway named an account without a `DU`/`DF` prefix. The node was stopped before the run phase | 1 |
| `RESULT: fail reason=account_gate_refused refusal=account_not_reported` | The gateway named no account at all — fail-closed, never a permit | 1 |
| `RESULT: fail reason=account_gate_refused refusal=node_not_connected` | Verification ran before the execution client reported connected (a probe/runner ordering bug, not an operator error) | 1 |
| `RESULT: fail reason=account_gate_refused refusal=strategy_started_before_account_gate` | A strategy was already started. Unreachable from this probe, which starts none; it exists for Epic 2's runner | 1 |
| `RESULT: fail reason=account_gate_refused refusal=reported_account_is_paper` | `--real-money` was authorized for a `DU`/`DF` account. Not reachable from this probe, which always passes `GateFlags()` | 1 |
| `RESULT: fail reason=account_gate_refused refusal=reported_account_not_authorized` | `--real-money` was authorized for an account the gateway does not report. Also unreachable from this probe | 1 |
| `RESULT: fail reason=account_gate_refused refusal=account_verification_error` | The gate could not complete — a Nautilus/adapter attribute it reads has moved. Fail-closed: the node was stopped. Report it; it means the code needs updating for the installed wheel | 1 |
| `RESULT: fail reason=gate_refused refusal=...` | **Layer 1**, before any socket was opened. Deliberately a different `reason=` string from Layer 2's | 1 |

### Pass criteria

1. **The gateway's own account is paper** — `[probe] gate:account ok mode=paper accounts=***NNN`
   prints, and `RESULT: ok ... gate_account=paper` follows. Exit code `0`.
2. **The phase runs in AR39's position** — the `gate:account` line appears *after*
   `[probe] connected (data + exec)` and *before* `[probe] running for Ns...`. Read the ordering off
   the transcript; that ordering is what AC #6 asks to be able to inspect.
3. **Nothing *this codebase* prints leaks the account (NFR26)** — check it rather than trusting it,
   but scope the check correctly:
   ```bash
   PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py --run-seconds 5 \
     --verify-account | tee /tmp/p2.log
   grep '^\[probe\]' /tmp/p2.log | grep -c '<your full account id>'   # must print 0
   grep -c '<your full account id>' /tmp/p2.log                       # will NOT be 0 — see below
   ```
   **The unscoped grep cannot pass, and that is not this story's masking failing.** Nautilus's own
   IB adapter prints the raw account to stdout on every successful connection — `Account
   \`DU…\` found in the connected TWS/Gateway` (`adapters/interactive_brokers/execution.py`) and
   `Managed accounts set: {…}` (`client/account.py`) — through its own C logger, which the probe
   does not configure. P1's own 2026-08-05 result-log entry above quotes that line verbatim, so the
   leak predates this story. NFR26 binds *our* log records and refusal messages; the third-party
   stdout surface is recorded as deferred work for a later story to suppress or route.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-07 | — | ⏳ **not yet run** | Defined but **not** executed against a live gateway, so per this file's own policy it is not evidence that P2 passes. Two reasons, both environmental: the story was implemented in a detached git worktree, which has no `.env` (the file is gitignored and hook-protected, and creating one was out of bounds), and the sandbox this session ran in declined the invocations that would have supplied the connection settings another way. What *was* exercised, and is only evidence that the tooling exists: the probe imports and runs, `--verify-account` parses and appears in `--help`, and an invocation with no account configured stopped at `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set` — Story 1.3's guard firing before any socket. Layer 2's behaviour itself is covered by 22 component tests against a node double and 26 unit tests over the decision truth table; what those cannot show, and what P2 exists for, is that a *real* IBKR paper gateway reports a `DU`-prefixed account through `managedAccounts` where this code reads it. Run P2 from a checkout that has `.env` the next time a Gateway is available and record the result here. **Informational: this does not gate Story 1.4.** |
