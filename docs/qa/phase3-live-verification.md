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
| 2026-08-05 (post-review) | — | ⚠️ **re-run required** | Code review hardened the probe after this procedure last passed: it now polls until both engines report connected before printing `RESULT: ok` (previously `ok` was printed whenever nothing raised, which `kernel.start_async()` does not do on connection failure), drives shutdown from a `finally` so a mid-run failure can no longer leak the socket, loop and kernel thread pool, rejects `--run-seconds < 1`, handles Ctrl-C, and passes an explicit event loop to `build_trading_node()`. The 2026-08-05 pass below remains valid evidence for AC #6 — the node genuinely connected, reconciled and shut down cleanly per its logs — but it was produced by the *older* probe. Re-run P1 once a Gateway is next available to confirm the hardened probe still reports `ok`, and record the result here. No `live_node_builder.py` behaviour that AC #6 depends on was changed. |
| 2026-08-05 | Allay (Gateway made available mid-session) | ✅ ok | First run surfaced a real bug: the probe's original `asyncio.run(...)`-wrapped implementation called the synchronous `node.dispose()` from inside a coroutine still executing on that same running loop. `TradingNode.dispose()` (`nautilus_trader/live/node.py:451-458`) calls `loop.stop()` whenever it finds the loop running — correct when `dispose()` runs after `node.run()` has already returned control, fatal here: the loop stopped mid-flight and `asyncio.run()`'s own cleanup raised `RuntimeError: Event loop stopped before Future completed.` (`RESULT: fail reason=RuntimeError ...`, exit 1) *after* the node had already connected, found account `DU4076626`, reconciled (0 discrepancies — it also surfaced one pre-existing residual position from earlier manual activity, unrelated to this story), run, and shut down every engine/client cleanly per the logs. Fixed the probe (not `live_node_builder.py` — the bug was entirely in how the script drove the node's loop) to own an explicit `asyncio.new_event_loop()` end to end and call `node.stop()` → `node.dispose()` only after `run_until_complete` had already returned control, matching the "normal with asyncio.run" branch `dispose()`'s own source comments describe. Re-ran twice after the fix: both **exit code 0**, both `RESULT: ok mode=build-start-stop elapsed=…`, both ending in `loop.is_running=False` / `loop.is_closed=True`. The second of those two runs is the AC #6 pass-criterion-2 evidence — a fresh process reconnected with the same `client_id=10` immediately after the prior process's clean shutdown, no stale-session delay, no single-use carryover. |

## Procedure P2: observe a real disconnect and watch trading permission be withheld

**Introduced by**: Story 1.6 — Detect Connection Loss and Withhold Trading Permission
**Verifies**: AC #1 and AC #2 — a live socket alone does *not* grant trading permission, confirming
re-established state does, and a genuine disconnect withdraws it while emitting `connection.lost`.
**Tool**: `scripts/diagnostics/live_connection_probe.py`

**This procedure is informational evidence, not a gate.** AC #1–#5 are proven by the automated
unit tests (`tests/unit/core/test_live_connection_monitor.py`) and component tests
(`tests/component/core/test_live_connection_probe.py`), which is what NFR32/NFR34 require. P2
exists to show the same behaviour against a real Gateway, through the same reader a running
session would poll.

### Preconditions

- IB Gateway or TWS is running and logged into a **paper** account, listening on the port
  configured by `IBKR_PORT` in `.env` (Gateway paper = `4002`, TWS paper = `7497`).
- `.env` exists at the repository root and has `TWS_ACCOUNT`, `IBKR_HOST`, `IBKR_PORT`,
  `IBKR_TRADING_MODE=paper` and `IBKR_LIVE_CLIENT_ID` set to values that pass the Layer 1 gate
  (`src/core/live_gate.py`). Without them the probe fails *before opening any socket* — an empty
  `TWS_ACCOUNT` reports `RESULT: fail reason=config_error ...`, and a non-paper configuration
  reports `RESULT: fail reason=gate_refused ...`.
- No other process is currently holding `IBKR_LIVE_CLIENT_ID` on the Gateway.

### What it does — and does not — do

The disconnect in step 3 is produced by **stopping the node**, which is a real, clean broker
disconnect observed through the production reader. Nothing is killed, no process is signalled, no
order is submitted, and no market-data subscription is made. Epic 1 has no order path at all.

### Command

```bash
uv run python scripts/diagnostics/live_connection_probe.py --hold-seconds 3
```

The probe puts the repository root on `sys.path` itself, so it runs identically from any working
directory and needs no `PYTHONPATH` prefix.

### Expected output

```
[probe] building node host=127.0.0.1 port=4002 client_id=10 trader_id=PAPER-PROBE0001
[probe] node built (config + LogGuard); building clients...
[probe] starting node...
[probe] waiting up to 300s for engines to connect...
[probe] status connected=True detail=ib socket connected, client ready
[probe] observed -> state=recovering permitted=False
[probe] confirmed -> state=connected permitted=True
[probe] holding the connection for 3s...
[probe] stopping node to produce a real disconnect...
[probe] status connected=False detail=ib socket not connected
<TIMESTAMP> [warning  ] connection.lost   detail='ib socket not connected' session_id=probe-connection-0001 state=lost
[probe] observed -> state=lost permitted=False downtime=0.00s
[probe] disposing node...
[probe] disposed loop.is_closed=True
RESULT: ok mode=connect-permit-drop final_state=lost permitted=False elapsed=<N>.NN
```

The `connection.lost` line is emitted by the monitor through `structlog`, interleaved with the
probe's own `[probe]` lines on stdout. It is Pass criterion 3's evidence, so it belongs in the
transcript rather than being left implicit.

The process exits with code `0`.

The two lines that carry the evidence are `observed -> state=recovering permitted=False` — a live
socket did **not** grant permission (NFR10) — and `observed -> state=lost permitted=False` after a
real disconnect (FR6). A `connection.lost` structlog event is emitted between them, carrying the
bound `session_id`.

Failure modes and their exit codes:

| Output | Meaning | Exit |
| --- | --- | --- |
| `RESULT: fail reason=gate_refused refusal=<reason>` | Layer 1 gate refused; no socket was opened | 1 |
| `RESULT: fail reason=config_error msg=...` | Unusable configuration (missing `.env`, empty `TWS_ACCOUNT`, bad `trader_id`, non-positive timeout) | 1 |
| `RESULT: fail reason=ProbeError msg=engines did not connect...` | Gateway not reachable on the configured port, or not logged in | 1 |
| `RESULT: fail reason=ProbeError msg=the ib socket flag was still set...` | The adapter's socket flag never cleared after `node.stop()` — investigate before trusting the reading | 1 |
| `RESULT: fail reason=interrupted` | Operator pressed Ctrl-C | 130 |
| `RESULT: fail reason=<Type> msg=...` | Anything else; full traceback on stderr | 1 |

### Pass criteria

1. **Permission is not granted by connectivity alone** — the first `observed ->` line shows
   `state=recovering permitted=False` while the reader reported `connected=True`.
2. **Confirmation grants it** — `confirmed -> state=connected permitted=True`.
3. **A real disconnect withdraws it** — the second `observed ->` line shows `state=lost
   permitted=False`, and a `connection.lost` event appears in the log with the session bound.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-09 (post-review) | — | ⛔ **still not run** | Code review hardened the probe after P2 was written: the node build moved inside the `try/finally` (a failure there previously skipped shutdown entirely), `node.build()` now runs under a bounded connection-retry budget (the adapter reconnects *indefinitely* by default and never consults `IBKR_CONNECTION_TIMEOUT`, so an unreachable Gateway hung the probe forever with no `RESULT:` line), the run-task join is bounded, and the disconnect wait now polls the **socket** flag specifically rather than `ConnectionStatus.connected` — which cleared as soon as the *readiness* flag dropped and would have certified that as a genuine disconnect. The `confirm_state_reestablished(status)` call was also updated for the new signature. Re-verified as a dry run only: `RESULT: fail reason=config_error ...`, exit 1, and the `finally` now demonstrably runs (`[probe] disposing node...`, `loop.is_closed=True`). |
| 2026-08-07 | — | ⛔ **not run** | No live evidence available in this session, for two independent reasons: (1) no IB Gateway or TWS was listening — all four IB ports (`4002`, `7497`, `4001`, `7496`) refused a TCP connection when probed; (2) this story was implemented in a git worktree that has no `.env` (it is gitignored, so it is not carried into a worktree), and `.env` is Edit/Write-protected by `.claude/hooks/protect-files.sh` — creating one was neither attempted nor appropriate. **Tooling evidence only** (explicitly *not* a pass, per this file's own policy): the probe was executed as a dry run and behaved correctly on the fail-closed path — it reported `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set ...`, exit 1, **before opening any socket**, and `--hold-seconds 0` was rejected by argument validation. AC #1–#5 are covered by 31 unit tests and 12 component tests that require no broker (NFR32). Re-run P2 once a Gateway and a populated `.env` are both available, and record the result here. |
