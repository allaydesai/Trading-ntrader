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
| 2026-08-09 | Story 1.5 (incidental) | ℹ️ corroborating only — **P1 re-run still outstanding** | Not a formal P1 re-run, but P2 below drove the same build → connect → run → stop → dispose sequence through the hardened `build_trading_node()` and ended `loop.is_running=False` / `loop.is_closed=True` with no shutdown problems reported. The hardened-probe re-run flagged above is still worth doing on its own terms; this is corroborating evidence, not a substitute. |
| 2026-08-05 | Allay (Gateway made available mid-session) | ✅ ok | First run surfaced a real bug: the probe's original `asyncio.run(...)`-wrapped implementation called the synchronous `node.dispose()` from inside a coroutine still executing on that same running loop. `TradingNode.dispose()` (`nautilus_trader/live/node.py:451-458`) calls `loop.stop()` whenever it finds the loop running — correct when `dispose()` runs after `node.run()` has already returned control, fatal here: the loop stopped mid-flight and `asyncio.run()`'s own cleanup raised `RuntimeError: Event loop stopped before Future completed.` (`RESULT: fail reason=RuntimeError ...`, exit 1) *after* the node had already connected, found account `DU4076626`, reconciled (0 discrepancies — it also surfaced one pre-existing residual position from earlier manual activity, unrelated to this story), run, and shut down every engine/client cleanly per the logs. Fixed the probe (not `live_node_builder.py` — the bug was entirely in how the script drove the node's loop) to own an explicit `asyncio.new_event_loop()` end to end and call `node.stop()` → `node.dispose()` only after `run_until_complete` had already returned control, matching the "normal with asyncio.run" branch `dispose()`'s own source comments describe. Re-ran twice after the fix: both **exit code 0**, both `RESULT: ok mode=build-start-stop elapsed=…`, both ending in `loop.is_running=False` / `loop.is_closed=True`. The second of those two runs is the AC #6 pass-criterion-2 evidence — a fresh process reconnected with the same `client_id=10` immediately after the prior process's clean shutdown, no stale-session delay, no single-use carryover. |

## Procedure P2: receive real-time RTH bars for a configured instrument

**Introduced by**: Story 1.5 — Receive Real-Time RTH Bars for a Configured Instrument
**Verifies**: AC #1 (the session requests `REALTIME`, not the `DELAYED_FROZEN` fetch default),
AC #2 (`use_regular_trading_hours=True` reaches the subscription), AC #3 (a bar that closes at the
venue is delivered to the node and logged with its instrument and timestamp).
**Tool**: `scripts/diagnostics/live_bars_probe.py`

The probe is **read-only**. It subscribes to market data and submits no order; Epic 1 contains no
order-submission code path to reach.

### Preconditions

- Everything Procedure P1 requires (paper Gateway/TWS running, gate-passing configuration, no other
  process holding `IBKR_LIVE_CLIENT_ID`).
- **The market must be open.** `ibkr_use_rth=True` means no bar closes outside regular trading
  hours, so a run outside RTH receives zero bars. That is a precondition failure, not an AC
  failure, and the probe says so in its own failure message.
- `--run-seconds` must exceed the bar interval — a 1-minute bar type needs well over 60 seconds
  for a bar to close *and* be published. The default of 150 covers two 1-minute bars.
- The account must hold a real-time market-data subscription for the instrument. Without one IBKR
  serves delayed data and reports the downgrade **only** as a log warning (code 10167), which is
  precisely why the observer carries its own freshness guard.

### Command

```bash
uv run python scripts/diagnostics/live_bars_probe.py \
    --bar-type AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL --run-seconds 150
```

`--host`, `--port` and `--account` override the corresponding settings for a checkout that has no
`.env` (a git worktree, for instance — `.env` is untracked). They are not a way around the safety
gate: `evaluate_gate` still runs on the resulting configuration, so a non-paper port or a
non-paper account prefix is refused exactly as it would be from `.env`, and neither `--real-money`
nor `NTRADER_REAL_MONEY_ACCOUNT` is reachable from this script at all.

### Expected output

```
[probe] building node host=127.0.0.1 port=4002 client_id=10 trader_id=PAPER-BARPROBE1 bar_types=['AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL'] lines_budget=100
[probe] node built (config + observer + LogGuard); building clients...
[probe] starting node...
[probe] waiting up to 300s for engines to connect...
[probe] connected (data + exec)
[probe] observing bars for 150s...
[probe] bars so far: 0 ({})
...
[probe] bars so far: 2 ({'AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL': 2})
[probe] stopping and disposing node...
[probe] disposed loop.is_running=False loop.is_closed=True
RESULT: ok mode=subscribe-observe-stop bars=2 counts={'AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL': 2} elapsed=<N>.NN
```

The process exits with code `0`.

`RESULT: ok` is printed **only when at least one bar actually arrived**. This matters for the same
reason it did in P1: a subscription that never delivers anything raises nothing. The IB data client
logs `Cannot subscribe to bars for …: instrument not found` and returns when the contract was not
loaded (`adapters/interactive_brokers/data.py:248-254`), and IB declines a subscription with a
warning rather than an error — so a probe that only checked "nothing raised" would report `ok` for
a session that saw nothing at all.

Four log lines are the evidence for AC #1–#3 and should be present in a passing run:

| Line | Evidences |
| --- | --- |
| `InteractiveBrokersClient-…: Setting Market DataType to REALTIME` | AC #1 — the session overrode the `DELAYED_FROZEN` fetch default |
| `InteractiveBrokersInstrumentProvider: Loaded 1 instruments` / `Contract qualified for <ID>` | the contract the subscription needs is loaded |
| `DataClient-INTERACTIVE_BROKERS: Subscribed <bar type> bars` | AC #2 — the RTH-restricted subscription was accepted |
| `live_bars.received instrument=… bar_type=… ts_event=… close=…` | AC #3 — a closed bar reached the node, logged with instrument and timestamp |

Failure modes and their exit codes:

| Output | Meaning | Exit |
| --- | --- | --- |
| `RESULT: fail reason=gate_refused refusal=<reason>` | Layer 1 gate refused; no socket was opened | 1 |
| `RESULT: fail reason=config_error msg=...` | Unusable configuration — bad `trader_id`, empty `TWS_ACCOUNT`, non-positive timeout, non-REALTIME market-data type, RTH disabled, unparseable bar type, or more subscriptions than `IBKR_MARKET_DATA_LINES` | 1 |
| `RESULT: fail reason=ProbeError msg=engines did not connect...` | Gateway not reachable on the configured paper port, or not logged in | 1 |
| `RESULT: fail reason=ProbeError msg=no bars received...` | Market closed, no market-data subscription, or contract not loaded — read the four evidence lines above to tell which | 1 |
| `RESULT: fail reason=ProbeError msg=delayed market data suspected...` | The observer measured a bar lag beyond its threshold and shut the session down | 1 |
| `RESULT: fail reason=interrupted` | Operator pressed Ctrl-C | 130 |

### Pass criteria

1. **REALTIME requested** — `Setting Market DataType to REALTIME` appears, and no IB code 10167
   ("Requested market data is not subscribed. Displaying delayed market data.") does.
2. **RTH subscription accepted** — `Subscribed <bar type> bars` appears for every configured bar
   type.
3. **Bars delivered and logged** — at least one `live_bars.received` line carrying the instrument
   and the bar's `ts_event`, and `RESULT: ok` with a non-zero `bars=` count.

### Known benign log lines

- `[ERROR] <trader-id>.TradingNode:` with an **empty message** during shutdown. This is Nautilus
  logging `str(asyncio.CancelledError())` — an empty string — at `live/node.py:371-372` when the
  run task is cancelled, which is exactly what an orderly probe shutdown does. Not a failure, and
  not specific to this probe.
- `[WARN] InteractiveBrokersInstrumentProvider: No loading configured…` emitted **once** alongside a
  successful `Loaded 1 instruments`. The execution client constructs its own instrument provider,
  which this story deliberately leaves at its default — Epic 1 has no order path that would need
  it. The *data* client's provider is the one carrying `load_ids`, and it loads.
- `[WARN] ExecClient-…: Cannot generate list[FillReport]: not yet implemented` — an adapter
  limitation surfaced by startup reconciliation, present since Story 1.3.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-09 | Story 1.5 dev session | ⚠️ **partial — precondition not met (market closed)** | Run against the live paper Gateway on `127.0.0.1:4002`, account `DU4076626`, `--run-seconds 12` and `20`. **Pass criteria 1 and 2 met live**: the gate passed, both engines connected, `Setting Market DataType to REALTIME` was logged, `AAPL.NASDAQ` was qualified (`ConId=265598`) and `Loaded 1 instruments`, the observer dispatched its paced subscription (`live_bars.subscribed count=1 remaining=0`) and the data client reported `Subscribed AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars`. No 10167 and no delayed-data warning appeared, so the account's real-time entitlement is genuine. **Pass criterion 3 not met, for a precondition reason**: 2026-08-09 is a Sunday and the contract's own `tradingHours` came back `20260809:CLOSED`, so no bar could close. The probe reported this correctly rather than printing `ok` — `RESULT: fail reason=ProbeError msg=no bars received in 20s …`, exit 1 — which is itself the evidence that the zero-bar path is not silently green. Shutdown was clean both runs (`loop.is_running=False loop.is_closed=True`, no shutdown problems). Also observed and unrelated to this story: startup reconciliation found a pre-existing residual `AAPL.NASDAQ` position of 4 shares at the paper account from earlier manual activity (the same residual Story 1.3's P1 run noted) and generated an inferred fill for it; nothing in this story trades. **Re-run during RTH to close pass criterion 3.** |
