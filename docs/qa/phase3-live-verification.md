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
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — the hardened-probe re-run is done; P1 is no longer outstanding** | This closes the ⚠️ re-run flagged on 2026-08-05, which had been the outstanding item on P1 ever since. Run against the paper Gateway on `127.0.0.1:4002`, account `***626`. **Pass criterion 1 (clean exit)**: `RESULT: ok mode=build-connect-run-stop loop_closed=True elapsed=15.71`, exit **0**, preceded by `[probe] connected (data + exec)` — so the hardened probe's poll genuinely saw both engines report connected, which is the specific behaviour the 2026-08-05 note asked to be re-confirmed — and ending `[probe] disposed loop.is_running=False loop.is_closed=True` with the prompt returning immediately. **Pass criterion 2 (no single-use carryover)**: run a second time in a fresh process moments later, same `client_id=10`, identical result — `RESULT: ok … loop_closed=True elapsed=15.41`, exit **0**. No stale-session delay and no carryover, confirming again that `TradingNode` is not subject to `BacktestEngine`'s single-use constraint. |
| 2026-08-07 | — | ℹ️ not re-run | Story 1.4 added an **opt-in** `--verify-account` flag to this probe. With the flag absent — which is P1's documented command — the code path, the printed lines and the `RESULT:` line are unchanged, so the ⚠️ re-run below is still the outstanding item for P1 and this story neither satisfies nor invalidates it. |
| 2026-08-05 (post-review) | — | ⚠️ **re-run required** | Code review hardened the probe after this procedure last passed: it now polls until both engines report connected before printing `RESULT: ok` (previously `ok` was printed whenever nothing raised, which `kernel.start_async()` does not do on connection failure), drives shutdown from a `finally` so a mid-run failure can no longer leak the socket, loop and kernel thread pool, rejects `--run-seconds < 1`, handles Ctrl-C, and passes an explicit event loop to `build_trading_node()`. The 2026-08-05 pass below remains valid evidence for AC #6 — the node genuinely connected, reconciled and shut down cleanly per its logs — but it was produced by the *older* probe. Re-run P1 once a Gateway is next available to confirm the hardened probe still reports `ok`, and record the result here. No `live_node_builder.py` behaviour that AC #6 depends on was changed. |
| 2026-08-09 | Story 1.5 (incidental) | ℹ️ corroborating only — **P1 re-run still outstanding** | Not a formal P1 re-run, but P3 below drove the same build → connect → run → stop → dispose sequence through the hardened `build_trading_node()` and ended `loop.is_running=False` / `loop.is_closed=True` with no shutdown problems reported. The hardened-probe re-run flagged above is still worth doing on its own terms; this is corroborating evidence, not a substitute. |
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
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — all three criteria met live; P2 has now been run** | The first execution of this procedure against a real gateway; the entry below was tooling evidence only. Run against the paper Gateway on `127.0.0.1:4002`. **Criterion 1 (the gateway's own account is paper)**: `[probe] gate:account ok mode=paper accounts=***626`, then `RESULT: ok mode=build-connect-run-stop loop_closed=True gate_account=paper elapsed=15.41`, exit **0**. The masked suffix matches the account the gateway itself named through `managedAccounts`, which is the whole point of the check — the configured `TWS_ACCOUNT` is not its source. **Criterion 2 (AR39 ordering)**: read straight off the transcript — `[probe] connected (data + exec)` → `[probe] verifying connected account (phase=gate:account)...` → `[probe] gate:account ok …` → `[probe] running for 5s...`. The verification sits after connect and before anything resembling trading, exactly as AC #6 asks to be inspectable. **Criterion 3 (NFR26 masking, scoped correctly)**: measured on a saved transcript. `grep '^\[probe\]' … \| grep -c '<account>'` → **0**, so nothing *this codebase* prints carries the unmasked identifier, and `accounts=***` appears twice. The unscoped count over the whole log is **4**, non-zero exactly as this procedure predicts, and every one of them is third-party: `AccountState(account_id=INTERACTIVE_BROKERS-<account>…)` emitted twice by `Portfolio` and once by `ExecClient-INTERACTIVE_BROKERS`, plus the adapter's ``Account `<account>` found in the connected TWS/Gateway``. So the leak is real, is the IB adapter's own stdout, and remains deferred work rather than a failure of this story. Log: `logs/p2-verify-20260828.log`. ⚠️ Note for re-runs: extract the account from `.env` with the inline comment stripped — a naive `cut -d= -f2` picks up the trailing comment, and the scoped grep then trivially returns 0 for the wrong reason, which looks like a pass. |
| 2026-08-07 | — | ⏳ **not yet run** | Defined but **not** executed against a live gateway, so per this file's own policy it is not evidence that P2 passes. Two reasons, both environmental: the story was implemented in a detached git worktree, which has no `.env` (the file is gitignored and hook-protected, and creating one was out of bounds), and the sandbox this session ran in declined the invocations that would have supplied the connection settings another way. What *was* exercised, and is only evidence that the tooling exists: the probe imports and runs, `--verify-account` parses and appears in `--help`, and an invocation with no account configured stopped at `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set` — Story 1.3's guard firing before any socket. Layer 2's behaviour itself is covered by 22 component tests against a node double and 26 unit tests over the decision truth table; what those cannot show, and what P2 exists for, is that a *real* IBKR paper gateway reports a `DU`-prefixed account through `managedAccounts` where this code reads it. Run P2 from a checkout that has `.env` the next time a Gateway is available and record the result here. **Informational: this does not gate Story 1.4.** |

## Procedure P3: receive real-time RTH bars for a configured instrument

**Introduced by**: Story 1.5 — Receive Real-Time RTH Bars for a Configured Instrument
**Verifies**: AC #1 (the session requests `REALTIME`, not the `DELAYED_FROZEN` fetch default),
AC #2 (`use_regular_trading_hours=True` reaches the subscription), AC #3 (a bar that closes at the
venue is delivered to the node and logged with its instrument and timestamp).
**Tool**: `scripts/diagnostics/live_bars_probe.py`

> **Numbering note.** Story 1.5 drafted this procedure as "P2" while Story 1.4 was in flight on a
> parallel branch, which drafted its own "P2". Story 1.4 landed first, so its account-gate procedure
> keeps the P2 slot and this one became **P3** on merge. Any Story 1.5 artifact that says "Procedure
> P2" — the story file, `deferred-work.md`, the sprint-status entry — means this section.

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
- ~~`[WARN] InteractiveBrokersInstrumentProvider: No loading configured…` emitted **once** alongside
  a successful `Loaded 1 instruments`. The execution client constructs its own instrument provider,
  which this story deliberately leaves at its default — Epic 1 has no order path that would need
  it. The *data* client's provider is the one carrying `load_ids`, and it loads.~~
  ⚠️ **Struck 2026-08-28: this warning was never benign, and the reasoning above expired with Epic
  1.** "No order path that would need it" stopped being true the moment Epic 2 started strategies.
  The execution client dereferences its own provider when translating an order —
  `self.instrument_provider.find(order.instrument_id).is_inverse`, with no `None` check
  (`adapters/interactive_brokers/execution.py:525`) — so an unloaded provider is an
  `AttributeError: 'NoneType' object has no attribute 'is_inverse'` raised *inside* the adapter's
  `submit_order`, where no strategy can see it. It stayed invisible only because a second defect
  upstream (the missing default route, see Procedure P7's 2026-08-28 entries) meant no order ever
  reached the client to trigger it. Both are fixed in `src/core/live_node_builder.py`; the exec
  client now loads the same `load_ids` as the data client.
- `[WARN] ExecClient-…: Cannot generate list[FillReport]: not yet implemented` — an adapter
  limitation surfaced by startup reconciliation, present since Story 1.3.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — all three criteria met live** | Run inside RTH (Friday 10:09–10:12 ET) against the paper Gateway on `127.0.0.1:4002`, account `***626`, with `--run-seconds 150`. This closes the criterion the 2026-08-09 run could not reach. **Criterion 1**: `Setting Market DataType to REALTIME` logged exactly once and **zero** occurrences of IB code 10167 — the account's real-time entitlement is genuine, not a silent delayed downgrade. **Criterion 2**: `AAPL.NASDAQ` qualified (`ConId=265598`), `Loaded 1 instruments`, and `Subscribed AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars`. **Criterion 3**: **4 bars** closed at the venue and were delivered — `RESULT: ok mode=subscribe-observe-stop bars=4 counts={'AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL': 4} republished=0 elapsed=160.61`, exit **0** — with `live_bars.received` carrying the instrument, `ts_event` and close for each (closes 316.68, 316.79, 316.56, 316.29 at 14:09:48, 14:10:05, 14:11:05, 14:12:05 UTC). Shutdown clean: `loop.is_running=False loop.is_closed=True`. Log: `logs/p3-bars-20260828.log`. Two things worth recording. The first bar arrived **1 second** after the subscription was accepted, far inside the 1-minute bar interval — that is a backfill bar delivered on subscription, not a bar that closed in-window, so a probe run of less than one full interval could still print a non-zero `bars=` count; the three later bars are the ones that prove live delivery. And `live_bars.received` appears **twice per bar** in the transcript (8 lines for 4 bars) because it is emitted through both the Nautilus C logger and structlog; the structlog line's own monotonic `count=1..4` is the reliable counter, and this is not duplicate delivery. |
| 2026-08-09 | Story 1.5 dev session | ⚠️ **partial — precondition not met (market closed)** | Run against the live paper Gateway on `127.0.0.1:4002`, account `DU4076626`, `--run-seconds 12` and `20`. **Pass criteria 1 and 2 met live**: the gate passed, both engines connected, `Setting Market DataType to REALTIME` was logged, `AAPL.NASDAQ` was qualified (`ConId=265598`) and `Loaded 1 instruments`, the observer dispatched its paced subscription (`live_bars.subscribed count=1 remaining=0`) and the data client reported `Subscribed AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars`. No 10167 and no delayed-data warning appeared, so the account's real-time entitlement is genuine. **Pass criterion 3 not met, for a precondition reason**: 2026-08-09 is a Sunday and the contract's own `tradingHours` came back `20260809:CLOSED`, so no bar could close. The probe reported this correctly rather than printing `ok` — `RESULT: fail reason=ProbeError msg=no bars received in 20s …`, exit 1 — which is itself the evidence that the zero-bar path is not silently green. Shutdown was clean both runs (`loop.is_running=False loop.is_closed=True`, no shutdown problems). Also observed and unrelated to this story: startup reconciliation found a pre-existing residual `AAPL.NASDAQ` position of 4 shares at the paper account from earlier manual activity (the same residual Story 1.3's P1 run noted) and generated an inferred fill for it; nothing in this story trades. **Re-run during RTH to close pass criterion 3.** |

## Procedure P4: observe a real disconnect and watch trading permission be withheld

**Introduced by**: Story 1.6 — Detect Connection Loss and Withhold Trading Permission
**Verifies**: AC #1 and AC #2 — a live socket alone does *not* grant trading permission, confirming
re-established state does, and a genuine disconnect withdraws it while emitting `connection.lost`.
**Tool**: `scripts/diagnostics/live_connection_loss_probe.py` (renamed from
`live_connection_probe.py` by the Story 2.4 review, once `src/core/live_connection_probe.py` took
that basename)

> **Numbering note.** Story 1.6 drafted this procedure as "P2" while Stories 1.4 and 1.5 were in
> flight on parallel branches. Story 1.4's account-gate procedure holds the P2 slot and Story 1.5's
> bars procedure holds P3, so this one became **P4** on merge into the Epic 1 integration branch.
> Any Story 1.6 artifact that says "Procedure P2" — the story file, `deferred-work.md`, the
> sprint-status entry — means this section.

**This procedure is informational evidence, not a gate.** AC #1–#5 are proven by the automated
unit tests (`tests/unit/core/test_live_connection_monitor.py`) and component tests
(`tests/component/core/test_live_connection_probe.py`), which is what NFR32/NFR34 require. P4
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
uv run python scripts/diagnostics/live_connection_loss_probe.py --hold-seconds 3
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
| 2026-08-28 | Allay (Claude Code session) | ⚠️ **all three pass criteria evidenced live, but the probe exits 1 on its own assertion — a harness bug, not a product one** | First execution against a real gateway (`127.0.0.1:4002`, `--hold-seconds 3`); the two entries below were dry runs only. **The behaviour P4 exists to show did happen, and the transcript carries it.** Criterion 1: `[probe] status connected=True detail=ib socket connected, client ready` followed by `[probe] observed -> state=recovering permitted=False` — a live socket alone did **not** grant permission (NFR10). Criterion 2: `[probe] confirmed -> state=connected permitted=True`. Criterion 3: after `node.stop()`, `[probe] observed -> state=lost permitted=False downtime=0.00s`, with `connection.lost detail='ib client is stopped or disposed' session_id=probe-connection-0001 state=lost` emitted through structlog with the session bound (FR6). Shutdown clean, `loop.is_closed=True`. **But `RESULT: fail reason=ProbeError msg=the disconnect was observed on the readiness flag, not the socket: ib client is stopped or disposed`, exit 1.** That is the probe's own final `_expect` (`live_connection_loss_probe.py:341-343`) requiring the string `socket` in `status.detail`, and it cannot pass by the route this probe takes. `read_connection_status` tests `_client_is_unusable` **before** the socket flag (`src/core/live_connection_probe.py:114-118`), and this procedure produces its disconnect by stopping the node — which makes the client unusable — so the earlier branch always wins and the detail reads `ib client is stopped or disposed`, never `ib socket not connected`. The socket drop itself *was* independently verified in the same run: `_await_socket_disconnect` polls `_is_ib_connected` directly and returned normally, and had it not, the probe would have failed with its other message (`the ib socket flag was still set 30.0s after node.stop()`). So the assertion is both redundant and mis-specified against the mechanism the probe uses. **Consequence for this file:** the procedure's documented "Expected output" line `[probe] status connected=False detail=ib socket not connected` is unreachable via `node.stop()` on nautilus-trader 1.220.0 and should be corrected. **To close P4 cleanly**, either relax the assertion to accept the stopped/disposed detail as a superset (the socket poll already proved the drop), or produce the disconnect by stopping the **Gateway** rather than the node, which leaves the client usable and lets the socket branch report. The latter was not attempted here because it would have interrupted the other procedures still running against that Gateway. Log: `logs/p4-connloss-20260828.log`. Recorded as ⚠️ rather than ✅ deliberately: this file's policy is that the probe's own verdict is the result, and its verdict was `fail`. |
| 2026-08-09 (post-review) | — | ⛔ **still not run** | Code review hardened the probe after this procedure was written: the node build moved inside the `try/finally` (a failure there previously skipped shutdown entirely), `node.build()` now runs under a bounded connection-retry budget (the adapter reconnects *indefinitely* by default and never consults `IBKR_CONNECTION_TIMEOUT`, so an unreachable Gateway hung the probe forever with no `RESULT:` line), the run-task join is bounded, and the disconnect wait now polls the **socket** flag specifically rather than `ConnectionStatus.connected` — which cleared as soon as the *readiness* flag dropped and would have certified that as a genuine disconnect. The `confirm_state_reestablished(status)` call was also updated for the new signature. Re-verified as a dry run only: `RESULT: fail reason=config_error ...`, exit 1, and the `finally` now demonstrably runs (`[probe] disposing node...`, `loop.is_closed=True`). |
| 2026-08-07 | — | ⛔ **not run** | No live evidence available in this session, for two independent reasons: (1) no IB Gateway or TWS was listening — all four IB ports (`4002`, `7497`, `4001`, `7496`) refused a TCP connection when probed; (2) this story was implemented in a git worktree that has no `.env` (it is gitignored, so it is not carried into a worktree), and `.env` is Edit/Write-protected by `.claude/hooks/protect-files.sh` — creating one was neither attempted nor appropriate. **Tooling evidence only** (explicitly *not* a pass, per this file's own policy): the probe was executed as a dry run and behaved correctly on the fail-closed path — it reported `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set ...`, exit 1, **before opening any socket**, and `--hold-seconds 0` was rejected by argument validation. AC #1–#5 are covered by 31 unit tests and 12 component tests that require no broker (NFR32). Re-run this procedure once a Gateway and a populated `.env` are both available, and record the result here. |

## Procedure P5: check broker connectivity and the gate from the CLI

**Introduced by**: Story 1.7 — Check Broker Connectivity and the Gate from the CLI
**Verifies**: AC #1 (the command evaluates the gate, connects, verifies the account, subscribes,
reports bars, disconnects cleanly, exits `0`), AC #2 (a refused configuration exits **3** with no
connection attempted) and AC #3 (a permitted configuration that cannot reach the broker exits **4**).
**Tool**: the CLI itself — `ntrader live check`. There is no diagnostic script for this story; the
command *is* the artifact under test.

**This procedure is informational evidence, not a gate.** AC #1–#6 are proven by the automated unit
tests (`tests/unit/core/test_live_check.py`, `tests/unit/cli/commands/test_live_cli.py`) and
component tests (`tests/component/core/test_live_check_driver.py`), which is what NFR32/NFR34
require. P5 exists to show the same exit codes against a real Gateway.

### Preconditions

- IB Gateway or TWS running and **logged into a paper account**, with "Enable ActiveX and Socket
  EClients" on and the API socket accepting connections from `127.0.0.1`. Note that an open TCP
  port is *not* sufficient: a Gateway sitting at its login screen accepts the socket and then never
  sends `managedAccounts`, which the check correctly reports as exit **4** (see the result log).
- Connection settings reach `IBKRSettings` — from `.env` at the repository root, or as environment
  variables on the command line. `live check` deliberately has **no** `--host` / `--port` /
  `--account` flags: connection settings live only in `IBKRSettings` (FR52, NFR25).
- No other process is holding `IBKR_LIVE_CLIENT_ID` on the Gateway.
- For pass criterion 3, run **during regular trading hours**. With `use_rth=True` no bar closes
  outside RTH, so zero bars outside the session is a precondition failure, not an AC failure — and
  the command says so and still exits `0`.

### What it does — and does not — do

It evaluates the Layer 1 gate, builds and starts a node, waits for both engines to report
connected, runs Layer 2's `gate:account` verification against what the gateway actually reports,
subscribes to one instrument, watches for bars, then stops and disposes. **It submits no order** —
Epic 1 has no order path at all — starts no strategy, writes no database row, and creates no
session. It never reads or writes `.env`, and it has no `--real-money` flag.

### Command

```bash
# From a checkout with a populated .env:
uv run python -m src.cli.main live check --observe-seconds 90

# From a checkout without one (a git worktree, for instance — .env is gitignored):
IBKR_HOST=127.0.0.1 IBKR_PORT=4002 IBKR_TRADING_MODE=paper TWS_ACCOUNT=DU0000000 \
  IBKR_LIVE_CLIENT_ID=10 IBKR_CLIENT_ID=1 \
  uv run python -m src.cli.main live check --observe-seconds 90
```

The two negative runs need neither a market nor a Gateway, and are the cheapest evidence for FR11:

```bash
# Gate refusal -> exit 3, with no socket opened
IBKR_PORT=4001 ... uv run python -m src.cli.main live check --observe-seconds 0

# Gate passes, broker unreachable -> exit 4
IBKR_PORT=4002 ... uv run python -m src.cli.main live check --observe-seconds 0   # Gateway stopped
```

### Expected output

```
<TIMESTAMP> [info     ] gate.static   mode=paper phase=gate:static status=ok
<TIMESTAMP> [info     ] live_check.building   client_id=10 host=127.0.0.1 port=4002 trader_id=PAPER-LIVECHECK
<TIMESTAMP> [info     ] live_check.connected  endpoint=127.0.0.1:4002 (client_id=10)
<TIMESTAMP> [info     ] gate.account  accounts=***626 mode=paper phase=gate:account status=ok
<TIMESTAMP> [info     ] live_check.observing  seconds=90.0
<TIMESTAMP> [info     ] live_bars.received    bar_type=AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL close=... count=1
live check: ok (exit code 0)
  gate passed, account verified, 1 bar(s) received on 1 subscription(s), disconnected cleanly
  mode: paper
  accounts: ***626
  subscriptions: AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL
  bars received: 1 (AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL=1)
  elapsed: 95.12s
```

A gate refusal instead prints, before any socket exists:

```
<TIMESTAMP> [error    ] gate.refused  message='IBKR_PORT 4001 is not a known paper port (4002, 7497). ...' phase=gate:static reason=non_paper_port status=failed
live check: gate_refused (exit code 3)
  IBKR_PORT 4001 is not a known paper port (4002, 7497). Any other port is refused, including unrecognised ones.
  refusal reason: non_paper_port
  elapsed: 0.00s
```

Exit codes (AR28):

| Outcome | Meaning | Exit |
| --- | --- | --- |
| `ok` | The whole sequence completed. Zero bars outside RTH still lands here, with the shortfall named | 0 |
| `config_error` | Unusable configuration — empty `TWS_ACCOUNT`, a non-REALTIME market-data type, an unparseable bar type | 1 |
| `error` | A delayed feed was suspected, `--require-bars` was set and none arrived, or an unexpected failure | 1 |
| `interrupted` | Operator pressed Ctrl-C. Deliberately `1`, not `130`: AR28's table has no `130` | 1 |
| (usage) | Click rejected the arguments | 2 |
| `gate_refused` | Either gate layer refused. **No socket is opened on Layer 1** | 3 |
| `broker_unreachable` | The gate passed but both engines never reported connected | 4 |

### Pass criteria

1. **A refused configuration exits 3 and connects to nothing** — the `gate.refused` line appears
   with `phase=gate:static`, no `live_check.building` line follows it, and the process exits `3`.
2. **An unreachable broker exits 4** — distinct from criterion 1, so a script can tell "refused to
   trade a live account" from "failed to connect".
3. **A reachable paper gateway exits 0** — `gate.account` reports a masked `DU`/`DF` account,
   at least one `live_bars.received` line appears (inside RTH), and the node disposes cleanly
   (`loop.is_closed=True`, no `shutdown problems:` line in the summary).

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — criterion 3 met live; P5 now complete** | Run inside RTH (Friday, 10:18–10:22 ET) against a **logged-in** paper Gateway on `127.0.0.1:4002` — the precondition the 2026-08-11 attempt never had, where the TCP port accepted but the API handshake never completed. `live check --observe-seconds 240` reported `live check: ok (exit code 0)`, `gate passed, account verified, 5 bar(s) received on 1 subscription(s), disconnected cleanly`, `mode: paper`, `accounts: ***626`, `bars received: 5`, `elapsed: 250.57s`, ending `loop.is_running=False loop.is_closed=True` with no `shutdown problems:` line. Criteria 1 and 2 keep their 2026-08-11 live evidence (exit **3** on a gate refusal with no `live_check.building` line; exit **4** twice, including against a Gateway whose port accepted but whose handshake never completed). **All three criteria are now met live.** Log: `logs/p5-check-rerun-20260828.log`. Two findings from the same session, both recorded in `deferred-work.md` — neither changes this verdict, and the second is the more serious of the two. |
| 2026-08-28 (first attempt) | Allay (Claude Code session) | ⚠️ **`ok` reported with 0 bars, inside RTH — a real defect, not a precondition miss** | The first `--observe-seconds 90` run at 10:14 ET, minutes after P3 had taken 4 bars on the same instrument, received **zero** bars and still exited **0** with the summary text *"no bars closed during the observation window. Outside regular trading hours this is expected"* — while the market was open. The cause is in the log and is not the market: 324 ms after `Subscribed AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL bars` (req id **10007**), IB reported `HMDS data farm connection is broken:ushmds` (2105) and then `Failed to request live updates (disconnected). (code: 10182, req_id=10007)`. The farm recovered 514 ms later (2106) and the market-data farm flapped and recovered too (2103 → 2104), but **nothing re-requested the live-update stream**, so the subscription stayed dead for the whole window and teardown closed with `No historical data query found for ticker id:10007` (366). Two distinct problems. **(a) A transient data-farm blip at subscribe time silently and permanently kills a bar subscription** — there is no retry on 10182. That matters far more for Procedure P6 than for this one: a 6.5-hour session that takes this blip goes silent for the rest of the day while continuing to heartbeat, and P6's criterion 4 ("no bar-processing backlog") would be measuring a dead stream rather than a healthy one. **(b) `live check` cannot tell "market closed" from "subscription died"** and asserts the former; it has no RTH awareness, so its zero-bar message is misleading precisely when something is wrong. `--require-bars` turns this into exit 1 and is the recommended flag for any scripted use. The 240-second re-run immediately afterwards carried no 2103/2105/10182/366 codes at all and took 5 bars, which is what isolates this to the farm blip rather than to `live check` itself. Log: `logs/p5-check-20260828.log`. |
| 2026-08-11 | Story 1.7 dev session | ⚠️ **partial — criteria 1 and 2 met live, criterion 3 not met (precondition)** | Run from a worktree with no `.env`, settings supplied as environment variables. **Criterion 1 met live**: `IBKR_PORT=4001` produced `gate.refused ... phase=gate:static reason=non_paper_port`, the summary `live check: gate_refused (exit code 3)`, `elapsed: 0.00s`, and **exit code 3** — no `live_check.building` line, so no node was constructed and no socket opened. **Criterion 2 met live, twice, in both of its shapes**: against `IBKR_PORT=7497` with nothing listening (exit **4**), and — the more interesting case — against the **running** paper Gateway on `127.0.0.1:4002`, whose TCP port accepts a connection but whose API handshake never completes (IB error 502 `Couldn't connect to TWS...`, then `Client failed to initialize; connection timeout`). The check reported `broker_unreachable (exit code 4)` rather than hanging or reporting `ok`, which is precisely the failure mode exit 4 exists for. Retried with a fresh `IBKR_LIVE_CLIENT_ID=17` to rule out a client id the Gateway was still holding: identical result, so this is the Gateway's own state (not logged in / API not accepting), not a stale id. **Criterion 3 not met, for that precondition reason** — no API handshake ever completed, so `gate:account` was never reached and no bar could arrive. Two things worth recording from these runs: the connect deadline was honoured to the millisecond (`elapsed: 45.04s` for `--connect-timeout 45`, `30.04s` for `30`), which is what the shared build+connect budget was changed to guarantee — an earlier additive version took **115s** to report an unreachable gateway; and shutdown was clean on every run (`loop.is_running=False`, `loop.is_closed=True`, `DISPOSED`, no shutdown problems reported). **Re-run criterion 3 against a logged-in paper Gateway during RTH.** |

## Procedure P6: run a session unattended for a full RTH day

**Introduced by**: Story 2.5 — Start a Session in the Foreground with an Ordered Startup Sequence
**Verifies**: AC #7 (a session started and left alone sustains 6.5 hours without operator
intervention and without accumulating a bar-processing backlog — NFR7, NFR2), and observes AC #2's
phase ordering and AC #5's heartbeat cadence against a real gateway rather than a double.
**Tool**: the CLI itself — `ntrader live start`. There is no diagnostic script for this story; the
command *is* the artifact under test.

**The automated tests are proxies, and this procedure is what closes AC #7.**
`tests/component/core/test_session_steady_state.py::TestSixAndAHalfHoursWithoutWaitingForThem`
drives 390 synthesized bars and 780 heartbeat ticks through an injected clock and sleeper. That
proves the *shape* — O(1) per bar, one bounded write per interval, no container that grows — and it
is what NFR32/NFR34 require of the suite. It cannot prove a real trading day: it does not exercise
the IB adapter's own reconnect behaviour, the Rust logger, Redis under six hours of writes, or a
Postgres connection that has been idle between heartbeats. Only this procedure does.

### Preconditions

- Everything Procedure P5 requires: IB Gateway or TWS **logged into a paper account**, API socket
  accepting connections from `127.0.0.1`, connection settings reaching `IBKRSettings`, and no other
  process holding `IBKR_LIVE_CLIENT_ID`.
- **Redis running and reachable** at `REDIS_HOST`/`REDIS_PORT`. A live session always runs with the
  Redis-backed engine cache (AR10). If it is down the session refuses at `node:build` with
  `RedisUnreachableError` and exit **1** — worth provoking once deliberately (`REDIS_PORT=6399`)
  before the real run, because the alternative shape (no preflight) is a process that prints
  nothing and never returns.
- **Postgres running**, `alembic upgrade head` applied, and a session already created:
  `ntrader live create --name rth-day-1 --strategy sma_crossover --bar-type AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`
- Start **before 09:30 ET** and leave it until after 16:00 ET. Starting mid-session is still useful
  evidence but does not satisfy the 6.5-hour criterion.

### What it does — and does not — do

It runs the eight AR39 phases, registers the bar observer and the strategies *after* the account
gate has passed, and then serves the session until the node stops. It **does** submit orders if the
strategy's logic fires — `sma_crossover` is a real strategy and this is a real paper account. It
writes `last_heartbeat_at` and `last_bar_at` to `trading_sessions`, and nothing else: no `trades`
row is written by this story (Epic 3 owns that).

> ⚠️ **That first claim was false when written, and only became true on 2026-08-28.** Until that
> date **no session had ever submitted an order**, for two independent defects sitting in series on
> the order path, each of which fully masked the next: the execution client was never given a
> default route (so `ExecEngine` dropped every order for a `NASDAQ`-venued instrument), and its
> instrument provider was never loaded (so the adapter raised `AttributeError` while translating
> the order it finally received). Both are fixed in `src/core/live_node_builder.py` and covered by
> regression tests; see Procedure P7's 2026-08-28 entries for the measurements. Any P6 run before
> that date observed a session that could not trade, whatever its phase log said.

⚠️ **Stopping does not yet leave positions alone.** `sma_crossover.on_stop()` still calls
`close_all_positions()` (Story 3.1's to remove), so do **not** read this procedure as evidence about
position handling on stop.

⚠️ **Reconciliation did not happen.** `reconcile` and `warmup` log `started`/`ok` and do nothing at
all in this epic. A clean phase log is not evidence that any state was reconciled.

### Command

```bash
# Foreground, in a terminal you can leave open. Ctrl-C is Story 2.6's; until then
# the way to end the run is to stop the Gateway or `kill <pid>` (SIGTERM — the
# kernel's own handler runs the teardown, proven in the Story 2.5 smoke run).
# NEVER `kill -9`: nothing runs, and the row stays `running` for the full
# 90-second staleness threshold — pass criterion 5 cannot be met that way.
uv run python -m src.cli.main live start rth-day-1 2>&1 | tee logs/rth-day-1.log
```

### Expected output

Sixteen phase records, in this order, each with the session's id bound:

```
<TS> [info ] session.phase  phase=gate:static  session_id=<uuid> status=started
<TS> [info ] gate.static    phase=gate:static  session_id=<uuid> status=ok mode=paper
<TS> [info ] session.phase  phase=node:build   session_id=<uuid> status=started
<TS> [info ] session.phase  phase=node:build   session_id=<uuid> status=ok
<TS> [info ] session.phase  phase=node:connect session_id=<uuid> status=started
<TS> [info ] session.connected  endpoint=127.0.0.1:4002 (client_id=10)
<TS> [info ] session.phase  phase=node:connect session_id=<uuid> status=ok
<TS> [info ] gate.account   phase=gate:account session_id=<uuid> status=started
<TS> [info ] gate.account   phase=gate:account session_id=<uuid> status=ok accounts=***626
... reconcile, warmup, subscribe, trading — each started then ok ...
<TS> [info ] session.started  trader_id=PAPER-<8 hex> strategies=['sma_crossover']
```

Note what `session_id` does **not** reach: Nautilus's own `TRADER_ID.COMPONENT_ID` stdout lines.
That logger is Rust-side and never passes through structlog. On that half the correlation is the
`PAPER-<8 hex>` prefix, which is derived from the same session id.

### Pass criteria

1. **The sixteen phase records appear in `PHASE_SEQUENCE` order**, and nothing after a `failed`.
2. **The session is still running 6.5 hours later** with no operator intervention — no restart, no
   Ctrl-C, no manual reconnect.
3. **`last_heartbeat_at` advanced roughly every 30 seconds throughout**, including across any
   connection loss. Count it afterwards; ~780 distinct values over a full day is the expectation,
   and a long flat stretch is the finding worth reporting:
   ```sql
   SELECT name, status, last_started_at, last_heartbeat_at, last_bar_at,
          EXTRACT(EPOCH FROM (now() - last_heartbeat_at)) AS heartbeat_age_seconds
     FROM trading_sessions WHERE name = 'rth-day-1';
   ```
4. **No bar-processing backlog.** `last_bar_at` stays within about a minute of the most recent bar
   for a 1-minute subscription throughout, and the process's RSS is flat between the first and last
   hour (`ps -o rss= -p <pid>` at both ends; a few MB of drift is noise, a monotonic climb is not).
5. **The row ends `stopped`, not `running`**, once the process exits — that is what makes the
   session startable again the next morning.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-28 | Allay (Claude Code session) | ℹ️ **not run — prepared for the next pre-open window** | Not attempted, for one reason that cannot be worked around on the day: the session was available from **10:10 ET**, and criterion 2 requires a start **before 09:30** to claim 6.5 unattended hours. A partial ~5.8-hour run was considered and rejected — it would have occupied the Gateway's `IBKR_LIVE_CLIENT_ID` for the whole session, blocking P7's outstanding half, P8 and P9 (all of which did run and pass that day), and it would still have needed a re-run for the full claim. What was prepared instead, so the next attempt is a single command before the open: the session exists (`rth-day-1`, `session_id=b0151c65-1e67-4333-91e4-57ff4fd7f0c2`, created exactly as this procedure specifies — `sma_crossover`, `AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`, default parameters), and `scripts/diagnostics/run_p6_rth_day.sh` wraps `live start` with the preconditions checked up front, a straight-to-file redirect (never `\| tee`, for P9's `$!` reason), 5-minute RSS + session-row sampling into a CSV for criteria 3 and 4, and a SIGTERM-not-SIGKILL stop so criterion 5 can be met. ⚠️ **Read the 2026-08-28 P5 entry before trusting a P6 run.** A transient IB data-farm drop at subscribe time was measured that day to kill a bar subscription permanently with no retry (code 10182 on the subscription's request id); a session that takes that blip keeps heartbeating and keeps its row looking healthy while receiving nothing at all, which would make criterion 4 measure a dead stream and read as a pass. The runbook greps the transcript for codes 10182/2103/2105/366 and warns, but that is a detector, not a fix — check it before recording a result. |
| — | — | ⛔ **not run** | Written with Story 2.5. Epic 1's retrospective records that **no live procedure has ever completed a full end-to-end run**, and 2 of 5 never ran at all; P6 is longer than any of them. Do not treat the green automated suite as evidence for AC #7 — see the note above this procedure's preconditions for exactly what the proxies do and do not prove. |

## Procedure P7: stop a running session with Ctrl-C, and force-exit with a second

**Introduced by**: Story 2.6 — Stop a Session Without Ending It and Without Touching Positions
**Verifies**: AC #1, #2 (the runner half), #4, #5, #6 against a real gateway.
**Tool**: the CLI itself — `ntrader live start`, stopped with a real signal. There is no diagnostic
script for this story; the command *is* the artifact under test.

**The automated tests are proxies, and this procedure is what closes the gap they cannot.**
`tests/integration/core/test_live_session_signal_ownership.py` sends real OS signals to a real
`TradingNode` and proves the process-level mechanics — re-arm survives node construction, a signal in
the synchronous window is not lost, two signals force-exit with code 1. What it cannot prove is
identity across a *broker* connection: whether a real IBKR paper session, stopped and restarted
against a live gateway, actually resumes trading under the same `trader_id` and rejoins its own Redis
namespace, and what the operator actually sees on the terminal.

### What it does not do

It does **not** prove FR18 end to end — see the ⚠️ under Story 2.6's AC #2. A session that traded
will have been flattened by `sma_crossover.on_stop()` (Story 3.1 removes that). Run this procedure
once with a position open and once without, and report both.

> ⚠️ **"A session that traded" described nothing that had ever happened, until 2026-08-28.** The
> 2026-08-23 run read that sentence as merely blocked by the market being closed; it was blocked by
> two defects on the order path as well (see this procedure's 2026-08-28 entries). So the flatten
> warning this procedure, P6 and P8 all carry had **never** been exercised against a
> session-owned position — `close_all_positions()` filters by `strategy_id`, and the only position
> the account has held throughout is the `EXTERNAL` residual. Story 3.1 should read those warnings
> in that light rather than as settled observations.

### Preconditions

Everything Procedure P6 requires: IB Gateway or TWS logged into a paper account, Redis running, and a
session already created:
`ntrader live create --name stop-test-1 --strategy sma_crossover --bar-type AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`

### Command

```bash
uv run python -m src.cli.main live start stop-test-1 2>&1 | tee logs/stop-test-1.log
# Watch the phase log reach `trading`, then:
#   first Ctrl-C  -> should print the stop line and exit 0
#   (start it again, then within ~1s send two Ctrl-Cs rapidly) -> should force-exit with code 1
```

### Expected output

A graceful stop, once the sequence has started serving:

```
<TS> [info ] session.stopped  session_id=<uuid>  signal=SIGINT  trader_started=True
Session stopped: stop-test-1 (SIGINT)
Positions were left at the broker by the runner. ⚠️  sma_crossover.on_stop() still flattens its own
positions (Story 3.1 removes this) — check the broker before assuming a position survived the stop.
```

Exit code `0` (`echo $?`).

A forced exit, two rapid signals:

```
<TS> [error] session.force_exit  exit_code=1  reason=...
Force exit: a second stop signal arrived; abandoning the teardown (exit 1).
```

Exit code `1`.

### Pass criteria

1. **First Ctrl-C**: the phase log ends, `session.stopped` appears with `signal=SIGINT`, the process
   exits **0**, and the row reads `stopped` with `last_stopped_at` set.
2. **Broker state before and after are identical** except for whatever `on_stop()` flattened — record
   the IBKR positions page (or `reconcile` output, once Epic 4 has one) at both ends.
3. **Ctrl-C during `node:build`** (start with the Gateway down or slow so the phase is genuinely slow)
   is **noticed** — the regression this story exists for; before it, the signal was lost for the whole
   of that phase.
4. **Two rapid Ctrl-Cs force-exit with code 1** and print the force-exit line before the process dies.
5. **Restarting the same session by name** reuses the same `session_id` and the same `PAPER-<8 hex>`
   `trader_id`, and the Redis namespace has no new prefix (`redis-cli --scan --pattern 'trader-PAPER-*'`).
6. **`kill -9` leaves the row `running`**; the next `live start` logs `session.reclaimed` and comes up
   — this half is already covered by Story 2.3's own reclaim suite (`tests/integration/db/test_session_service.py`),
   re-run here only to confirm it still holds against a session that this story's stop path touched.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| — | — | ⛔ **not run** | Written with Story 2.6. Nothing in this story's automated suite requires IB Gateway/TWS or Redis — see the story's Dev Notes, "Blockers and preconditions". `⛔ not run` is an acceptable and expected entry; a dry run is not a pass, per this file's own policy at the top. |
| 2026-08-28 | Allay (Claude Code session) | ⛔ **criterion 2's position-open half is BLOCKED — no live session can open a position at all** | Attempted inside RTH (Friday, 10:44–10:52 ET) specifically to close the half the 2026-08-23 run could not, using a session built to make a fill easy: `p7-position-test` (`session_id=621bb88c-5ea0-4c90-9ed3-6a05641605fa`), `sma_crossover` on **`NVDA.NASDAQ`** — an instrument the account holds no position in, so `_generate_buy_signal`'s `has_long` check could not block a new one — with `fast_period=2, slow_period=3` for a ~3-bar warmup and `position_size_pct=0.5` (~$5,000, against $106,969 buying power). It worked as far as the strategy: two runs each produced a real signal within ~2.5 minutes — `Position sizing: Portfolio=$1,000,000, Size%=0.5%, Price=$225.93, Qty=22 (~$5,000 notional)` then `Generated SELL signal`. **The order never reached the broker, and cannot.** `ExecEngine` refused it outright: `Cannot execute command: no execution client configured for NASDAQ or 'client_id' None, SubmitOrder(order=MarketOrder(SELL 22 NVDA.NASDAQ MARKET GTC …))`. The order stops at `OrderInitialized` — there is no `OrderSubmitted`, `OrderAccepted`, `OrderFilled`, `OrderDenied` or `OrderRejected`, and no `PositionOpened`. Cause: `InteractiveBrokersExecClientConfig` is constructed with **no `routing=`** (`src/core/live_node_builder.py:333-339`), so it defaults to serving only its own `INTERACTIVE_BROKERS` venue, while every IB instrument in this codebase carries the exchange as its venue (`NVDA.NASDAQ`, `AAPL.NASDAQ`). Nothing routes. Both attempts confirmed it; broker state was byte-identical across the stop, `GrossPositionValue 1274.71 / NetLiquidation 32517.49` before and after, with the only position throughout being the pre-existing `Residual Position(LONG 4 AAPL.NASDAQ, id=AAPL.NASDAQ-EXTERNAL)`. **This is not a P7 failure — it is a blocker sitting underneath P7**, recorded in `deferred-work.md`. Until it is fixed, criterion 2's position-open half is unreachable by any means, and so is the warning this procedure prints about `sma_crossover.on_stop()` flattening positions: `close_all_positions()` had nothing of its own to flatten here for a second, deeper reason than the market being closed on 2026-08-23. ⚠️ It also falsifies two claims already written into this file: Procedure P6's "It **does** submit orders if the strategy's logic fires", and P7's own "A session that traded will have been flattened by `sma_crossover.on_stop()`". No session has ever traded. Logs: `logs/p7-position-20260828-104431.log`, `logs/p7-position-20260828-104828.log`. |
| 2026-08-28 (post-fix) | Allay (Claude Code session) | ⚠️ **both order-path blockers FIXED and proven live as far as order submission; criterion 2 still not closed — no fill, for a third-party reason** | Re-run after fixing the blocker the entry below found, which turned out to be **two** defects sitting in series, each fully masking the next. **Fix 1 — default routing.** `routing=RoutingConfig(default=True)` now passed on `InteractiveBrokersExecClientConfig`. Verified against the installed 1.220.0 source rather than assumed: Nautilus registers a client as the engine's default only when the config asks (`live/node_builder.py:252-254`), and its own fallback fires **only** for a client built `venue=None` (`execution/engine.pyx:421-429`); the IB exec client is built `venue=IB_VENUE` (`execution.py:157`) so it never qualified, while the IB *data* client is built `venue=None` (`data.py:115`) so it did — which is exactly why market data worked and hid this for two epics. **Proven live**: `ExecClient-INTERACTIVE_BROKERS: Submit MarketOrder(SELL 22 NVDA.NASDAQ MARKET GTC …)` appeared in `logs/p7-position-20260828-153506.log` — a line that had **never** appeared in any transcript before, and the exact thing `Cannot execute command: no execution client configured for NASDAQ` had been replacing. **Fix 2 — the exec client's instrument provider**, revealed *by* fix 1 within the same run: the adapter dereferences `self.instrument_provider.find(order.instrument_id).is_inverse` with no `None` check (`execution.py:525`), so the first order ever to reach the client died as `AttributeError("'NoneType' object has no attribute 'is_inverse'")` raised **inside** the adapter's own `submit_order`, where no strategy can see it. The exec client now loads the same `load_ids` as the data client; **proven live**: `InteractiveBrokersInstrumentProvider: Adding instrument=Equity(id=NVDA.NASDAQ …)` now appears on the exec side. Regression cover: `TestExecutionRouting` (drives Nautilus's real `TradingNodeBuilder.build_exec_clients`, with an anti-tautology twin that counts **zero** under the stock `RoutingConfig` and a control showing an `INTERACTIVE_BROKERS`-venued order routed even before the fix) and `TestInstrumentLoading::test_the_exec_client_loads_the_same_instruments_as_the_data_client`. Full component tier 1297 pass, unit 2377 pass. **Why criterion 2 is still open**: four further attempts (`154402` client_id 17, `154259`, `154527` on a fresh `p7-position-msft` session) all received **zero bars**, because IB killed each bar subscription at birth with `Historical Market Data Service error message:Trading TWS session is connected from a different IP address (code: 162, req_id=…)`. It is **not** a client-id conflict (reproduced on id 17) and **not** contract-specific (reproduced on MSFT as well as NVDA) — it is account-wide, and it began mid-session at ~15:39 UTC after earlier runs the same hour had taken bars normally. **Cause confirmed by the operator: they signed into IBKR on their mobile device mid-session.** IBKR permits one active session per account, so the mobile login evicted the Gateway's market-data entitlement while leaving its API socket connected — which is why the session still connected, still passed both gate layers, still reported `Subscribed … bars`, and still heartbeated, while receiving nothing. Worth knowing for every future procedure: a phone in your pocket silently kills a running session's data feed, and nothing in the session says so. No bars → no crossover → no order → no fill. The one `OrderFilled` in the MSFT transcript is the **inferred** fill reconciliation generates for the pre-existing `AAPL.NASDAQ-EXTERNAL` residual, not a session fill. **To close criterion 2**: resolve the competing IBKR login (mobile app, client portal, or a second TWS/Gateway holding the paper account), then re-run `./scripts/diagnostics/run_p7_position.sh` inside RTH. The order path itself is no longer the obstacle. ⚠️ This is the **third** instance of the same class of defect — an IB subscription error that permanently kills a stream with nothing retrying it (10182 on 2026-08-28 for P5, now 162 here); see `deferred-work.md`. |
| 2026-08-23 | Allay | ⚠️ **5 of 6 pass, 1 partial** | Run against a real IB Gateway on the paper port (4002), account `***626`, Redis and Postgres up, after the Story 2.6 code review's fixes. Session `p7-stop-test`, `session_id=6ffd1556-a854-4a2f-9b0c-7b2f14958a6c`, `trader_id=PAPER-6ffd1556`. Logs: `logs/p7-c1-graceful.log`, `logs/p7-c3-build.log`, `logs/p7-c4-force.log`, `logs/p7-c5-restart.log`. Signals were delivered with `proc.send_signal` to the CLI process directly (not through `uv run`, which does not forward them). **Criterion 3 is a partial pass and is the one thing to read.** Detail below. |

#### Result detail — 2026-08-23

1. **First Ctrl-C — ✅ PASS.** `session.stopped session_id=6ffd1556-… signal=SIGINT trader_started=True`,
   console `Session stopped: p7-stop-test (SIGINT)` plus the Story 3.1 residual warning, exit **0**.
   Row read `stopped` with `last_stopped_at=2026-08-23 11:19:19.822510-04:00`. Neither of the review's
   two new warnings fired, correctly: a signal *did* end it and the teardown reported no problems.
2. **Broker state identical — ✅ PASS.** A pre-existing `LONG 4 AAPL.NASDAQ` position, `id=AAPL.NASDAQ-EXTERNAL`,
   was present before and logged again as `Residual Position(LONG 4 AAPL.NASDAQ, id=AAPL.NASDAQ-EXTERNAL)`
   at the teardown of the *second* run — so it survived both stops. `NetLiquidation 32470.14` /
   `GrossPositionValue 1238.4` identical across runs. Zero orders submitted (the single order-shaped
   record is an *inferred* `OrderFilled` generated by reconciliation for that same EXTERNAL position).
   ⚠️ **This is not the "with a position open" variant the procedure asks for.** It was **Sunday**, the
   market was closed, `use_rth=True` means no bar closes, so no session could open a position of its
   own and `sma_crossover.on_stop()`'s `close_all_positions()` had nothing of its own to flatten — it
   filters by `strategy_id`, and this position is `EXTERNAL`. **The position-open half of criterion 2
   remains unverified** and must be re-run inside RTH.
3. **Ctrl-C during `node:build` — ⚠️ PARTIAL.** Forced slow by pointing `IBKR_HOST` at a non-routable
   address with the port left at 4002 so the static gate still passed. **The signal is now noticed**:
   `session.stopped … signal=SIGINT trader_started=False` was logged 6.0s into the phase
   (`node:build status=started` 15:20:08.212 → `session.stopped` 15:20:14.217). Before the review's
   D1 fix it was discarded entirely, so the regression this story exists for **is closed**.
   **But the process does not then stop.** It ran on through the adapter's remaining reconnect
   attempts and had not exited 200s after the signal, when the harness killed it. Cause:
   `request_node_stop` hands off with `loop.call_soon_threadsafe(node.stop)`, and the loop is *not
   running* during `build_clients`' synchronous connect, so the callback sits queued; the stop can
   only take effect at the next phase boundary, which is after all three attempts (~4 minutes).
   Mitigation, verified in the same window: **the operator's second Ctrl-C force-exits immediately**
   (criterion 4 below was run *during* a slow build precisely to prove this). Recorded in
   `deferred-work.md`.
4. **Two rapid Ctrl-Cs — ✅ PASS.** Exit **1**, **0.0s** after the second signal, with both the console
   `Force exit: a second stop signal arrived; abandoning the teardown (exit 1).` and the
   `session.force_exit` log record. Run in the hardest case — mid-`node:build` against an unreachable
   gateway — so it doubles as the escape hatch for criterion 3.
5. **Restart reuses identity — ✅ PASS.** Same `session_id=6ffd1556-a854-4a2f-9b0c-7b2f14958a6c`,
   same `trader_id=PAPER-6ffd1556`, `gate:account` re-verified `accounts=***626 mode=paper`.
   Distinct `trader-PAPER-*` prefixes in Redis: **70 before, 70 after**, with exactly **1** matching
   this session — no new namespace.
6. **`kill -9` leaves the row `running`, next start reclaims — ✅ PASS.** The killed criterion-3 run
   left `status=running` with a 60s-old heartbeat (which also proves AR32's *startup* heartbeat writes
   against a real database — it had ticked twice during a phase that never reached the steady loop).
   The next `live start` logged
   `session.reclaimed heartbeat_age_seconds=128.881652 name=p7-stop-test` and came up.

**Housekeeping:** this run left a real `trading_sessions` row, `p7-stop-test`
(`6ffd1556-a854-4a2f-9b0c-7b2f14958a6c`), now `stopped`, and one `trader-PAPER-6ffd1556` Redis
namespace. The repositories are write-once and expose no delete, so both are named here rather than
removed with raw SQL — the same posture Story 2.5 took with `smoke-1787319225`.

---

## Procedure P8: contain a failing strategy without losing the session

**Introduced by**: Story 2.7 — Keep One Failing Strategy from Taking Down the Session
**Verifies**: AC #1, #2, #4, #5 and #7 against a real gateway.
**Tool**: the CLI itself — `ntrader live start` with a two-strategy session, one of which is fed a
bar it cannot survive. There is no diagnostic script for this story; the command *is* the artifact
under test, plus one `psql` query run from a **second terminal**.

**The automated tests are proxies, and this procedure is what closes the gap they cannot.**
`tests/integration/core/test_live_strategy_failure_survives.py` builds a real `LiveDataEngine` in a
fresh interpreter and proves the process-level mechanic by return code — guarded `rc=0`, unguarded
`rc=1` — and `tests/component/core/test_session_runner_strategy_failure.py` proves sibling delivery
on a real `MessageBus` in both registration orders. What none of them touch is a **broker**: whether
a contained failure leaves an IBKR paper account's positions and orders untouched, whether the
session keeps heartbeating and receiving real market data afterwards, and whether the failure is
readable from another process while the session is still running.

### What it does — and does not — do

It does **not** prove NFR12 for every handler. Only `handle_bar` and `handle_event` are wrapped; a
raising `LiveClock` timer callback is contained by Nautilus itself but **invisibly** (measured:
swallowed at the pyo3 boundary, exit 0, nothing printed), and no repo strategy uses timers today.

It **cannot** prove AC #6's engine-level backstop without deliberately breaking something the guard
does not cover — the runner's own `note_bar` or the `LiveBarObserver`. That is out of scope here;
AC #6 is pinned by a config assertion and a Nautilus-default canary instead.

It does **not** reach the "all strategies failed" path unless both strategies are made to fail.
Criterion 5 covers the start-path half; the runtime half (`session.all_strategies_failed`) is
optional and should be reported as not run if it was not attempted.

### Preconditions

Everything Procedure P7 requires — IB Gateway or TWS logged into a **paper** account, Redis and
Postgres running — plus `alembic upgrade head` (this story adds the second Phase-3 migration,
`b7c419e2a3d8`, so a database still at `d08dfbd393f0` has no `runtime_flags` column and criterion 2
cannot be run at all).

⚠️ **Run inside RTH.** `use_rth=True` means no bar closes outside regular trading hours, and this
procedure needs bars to arrive: with the market closed neither the failure nor the sibling's
continued delivery can be observed. Procedure P7's criterion 2 was left partially unverified for
exactly this reason.

⚠️ **The CLI cannot express a two-strategy session today** (`src/cli/commands/live.py`'s `--strategy`
is singular — Story 2.7 deliberately does not add `multiple=True`). Create the two-strategy spec
in-process, the same way `tests/component/core/test_session_runner_strategy_failure.py` does, or run
the single-strategy variant and report criterion 1's sibling half as not run.

### Command

```bash
# Terminal 1 — the session under test.
uv run python -m src.cli.main live start contain-test-1 2>&1 | tee logs/contain-test-1.log

# Terminal 2 — while it is still running. This is AC #4's whole point.
psql "$DATABASE_URL" -c "SELECT name, status, last_heartbeat_at, last_bar_at, runtime_flags \
  FROM trading_sessions WHERE name = 'contain-test-1';"
```

The raiser is a real `sma_crossover` fed a `0.00` close, which divides by it at
`sma_crossover.py:150` and raises `decimal.DivisionByZero` — a genuine failure through the real
`Actor.handle_bar` re-raise, not a monkeypatched `raise`. Nautilus accepts a zero-priced `Bar`
(measured). If a contrived bar cannot be injected against a live feed, point the session at a probe
strategy that raises on its first bar instead, and say which was used.

### Expected output

```
<TS> [error] strategy.failed  session_id=<uuid>  strategy_id=SMACrossover-000
             spec_strategy_id=sma_crossover  error_type=DivisionByZero  handler=handle_bar
             traceback=...
<TS> [warning] strategy.degraded  strategy_id=SMACrossover-000  state=degraded
... the session keeps logging, `momentum` keeps receiving bars, the heartbeat keeps advancing ...
```

and, on Ctrl-C:

```
Session stopped: contain-test-1 (SIGINT).
⚠️  1 strategy was contained during this run and stopped trading:
      sma_crossover (SMACrossover-000) — DivisionByZero in handle_bar at 14:03:11Z
    The session kept running; the other strategies were unaffected. See `strategy.failed` in the
    log for the traceback, and `runtime_flags` on the session's row for the same facts from
    another process.
```

Exit code **0** (`echo $?`) — the session ran and it stopped. AR28's table has no code for "a
strategy failed" and Story 1.7 recorded that inventing one is worse than a generic failure.

### Pass criteria

1. **The session survives and the sibling keeps trading.** `strategy.failed` appears **exactly
   once**, the process stays up, `momentum` keeps logging bars *after* the failure, and
   `last_heartbeat_at` keeps advancing. A per-bar repeat of `strategy.failed` is a **fail** — the
   latch is broken.
2. **The failure is readable from a second process while the session is still running.**
   The `psql` query above returns `runtime_flags` carrying `{"v": 1, "all_failed": false,
   "failed_strategies": [{"spec_strategy_id": "sma_crossover", "error_type": "DivisionByZero", …}]}`.
   Note that it appears on the **next heartbeat tick** (~30s), not instantly: the write is queued by
   the guard and drained by the steady-state tick, deliberately (AC #10). ⚠️ Check the `detail` field
   carries **no** unmasked account identifier and **no** traceback.
3. **The IBKR positions and orders page is identical before and after the contained failure.**
   Nothing closed, nothing cancelled, nothing submitted. Record the account's positions, open orders
   and `NetLiquidation` at both ends. This is NFR14 and AR43, and it is the criterion that matters
   most: the isolation verb is `degrade()` precisely *because* `stop()` would run
   `sma_crossover.on_stop()`'s `close_all_positions()`.
4. **Restarting clears the flag.** Stop the session, start it again, and confirm `runtime_flags` is
   back to `NULL` — a failure from a previous process run must never be reported against the current
   one.
5. **A strategy that fails in `on_start` does not stop the others starting.** Run a session whose
   first spec raises during `on_start` (an unqualified instrument, or a deliberately invalid
   parameter): the second strategy still starts, the phase log reaches `trading status=ok`, and
   `session.started` names only the strategies that actually started. Then make **every** spec fail
   and confirm the `trading` phase logs `failed`, the sequence stops, and the CLI exits **1** with
   the `NoStrategyStartedError` message naming the specs.
6. **The degraded strategy stays registered and warm.** `Trader.strategy_states()` still lists it as
   `DEGRADED` (not removed), and it is still subscribed to its bar type. ⚠️ Known and accepted: its
   `on_stop()` will **not** run at teardown, because `Trader._stop()` guards on `is_running`. Today
   that is strictly safer; Story 3.1 must revisit it once the flatten is gone.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — all six criteria met live** | Run inside RTH (Friday, 10:26–10:53 ET) against the paper Gateway on `127.0.0.1:4002`, account `***626`, Redis and Postgres up, alembic at `b7c419e2a3d8`. Session `contain-test-2`, `session_id=41b3e662-d1ad-4ab9-85df-2d8619aba53a`, `trader_id=PAPER-41b3e662`, two strategies: **`sma_crossover` on `MSFT.NASDAQ`** (the raiser) and **`momentum` on `AAPL.NASDAQ`** (the sibling). Harness: `scripts/diagnostics/live_contain_probe.py`; second-process reader: `scripts/diagnostics/p8_query_flags.py`. Logs: `logs/p8-contain-20260828.log` (run 1, negative control), `logs/p8-contain2-20260828.log` (runs the criteria), `logs/p8-contain3-20260828.log` (restart, criteria 4 and 6). **This closes exactly the gap the entry below names** — that note said "what remains unverified here is specifically the *broker-facing* half: criteria 2, 3 and 6", and all three are now met against a real gateway. Detail and deviations below. |
| — | — | ⛔ **not run** | Written with Story 2.7. Nothing in this story's automated suite requires IB Gateway/TWS, Redis or RTH — see the story's Dev Notes, "Blockers and preconditions". `⛔ not run` is an acceptable and expected entry; a dry run is not a pass, per this file's own policy at the top. The process-level claim (AC #1/#3) **is** covered automatically and by return code, in `tests/integration/core/test_live_strategy_failure_survives.py`, including the inverted `rc=1` proof — so what remains unverified here is specifically the *broker-facing* half: criteria 2, 3 and 6. |

#### Result detail — 2026-08-28

1. **The session survives and the sibling keeps trading — ✅ PASS.** `strategy.failed` appears
   **exactly once** (`error_type=DivisionByZero handler=handle_bar strategy_id=SMACrossover-000
   spec_strategy_id=sma_crossover`), followed by one `strategy.degraded`. Real MSFT bars kept
   arriving for the remaining 150s and the count did **not** repeat, so the latch holds. The sibling
   kept receiving throughout: `momentum`'s delivered-bar count went **2 → 4** *after* the failure, on
   the real AAPL feed, and `last_heartbeat_at` advanced across it (10:27:04 → 10:28:04 → …). The
   process stayed up and stopped only when the harness sent SIGINT, exiting **0**.
2. **Readable from a second process while still running — ✅ PASS.** With the runner still alive,
   a separate process read `status=running` and:
   `{"v": 1, "all_failed": false, "failed_strategies": [{"at": "2026-08-28T14:27:19.504862+00:00",
   "detail": "[<class 'decimal.DivisionByZero'>]", "handler": "handle_bar", "error_type":
   "DivisionByZero", "strategy_id": "SMACrossover-000", "spec_strategy_id": "sma_crossover"}]}` —
   the documented shape exactly. The timing is the documented one too: the failure landed at
   10:27:19 and the flag was still `null` at 10:27:2x because the heartbeat had last ticked at
   10:27:04; it appeared on the **next** tick at 10:28:04, which is AC #10's queue-and-drain, not a
   lag. ⚠️ Checked as the criterion asks: `detail` carries **no** account identifier and **no**
   traceback — it is the bare exception class.
3. **IBKR positions and orders identical across the contained failure — ✅ PASS.** Read from the
   node's own cache immediately before and after: BEFORE `['AAPL.NASDAQ LONG 4
   id=AAPL.NASDAQ-EXTERNAL']`, AFTER `['AAPL.NASDAQ LONG 4 id=AAPL.NASDAQ-EXTERNAL']` — the same
   pre-existing residual position, unchanged. **Zero orders were submitted by the failing strategy**,
   which is the point: the raise happens at `sma_crossover.py:150`, *before* `submit_order` on line
   254, so `degrade()` never let the position-touching path run. Confirmed twice (runs 2 and 3).
4. **Restarting clears the flag — ✅ PASS.** After the run stopped, the row kept
   `status=stopped` with the failure still recorded — correct, it is the record of that run. On the
   **next** `live start` of the same session the flag read `runtime_flags: null` while
   `status=running`, so a previous process run's failure is never reported against the current one.
5. **`on_start` failure isolation — ✅ PASS, both halves.** ⚠️ First, the procedure's two suggested
   levers do **not** work against this codebase and should be corrected here: "an unqualified
   instrument" cannot raise, because `sma_crossover.on_start` only calls `subscribe_bars`
   (`sma_crossover.py:79-81`) and Nautilus answers an unloadable contract with a data-client log
   line (`Cannot subscribe to bars …: instrument not found`) rather than an exception; and "a
   deliberately invalid parameter" is refused when the **spec** is built, since `SMAParameters`
   bounds `fast_period`/`slow_period` at `ge=1, le=200` (`src/models/strategy.py:27-28`). The
   failure was therefore injected with a registered probe strategy whose `on_start` raises —
   `p8_start_raiser`, in `scripts/diagnostics/p8_start_failure_strategy.py`, kept out of `src/` so
   it never enters the production registry — and this entry says so, as the procedure requires.
   **First half** (session `p8-startfail-1`, spec `[p8_start_raiser, momentum]`): `strategy.
   start_failed error_type=RuntimeError handler=start spec_strategy_id=p8_start_raiser` was logged,
   the second strategy still started, the phase log reached `phase=trading status=ok`, and
   `session.started` named **only** what actually started — `strategies=['momentum']`. Final states
   `{P8StartRaiser-000: 'FAULTED', SMAMomentum-002: 'RUNNING'}`, exit **0**. **Second half**
   (session `p8-allfail-1`, spec `[p8_start_raiser]` alone): the `trading` phase logged
   `error_type=NoStrategyStartedError phase=trading status=failed`, the sequence stopped before
   `session.started`, `all_failed=True`, and the process exited **1** with the message naming the
   specs — *"No strategy in this session started, so it cannot trade. Every specification failed:
   p8_start_raiser. See the `strategy.start_failed` records for each one's error and traceback."*
   Note the exit code observed is the harness's, not the CLI's: `live start` cannot run either of
   these sessions, because `p8_start_raiser` is not registered in its process and the stored spec
   would fail to resolve. AR28's mapping of this error to exit 1 is covered by the automated suite.
   Logs: `logs/p8-startfail-20260828.log`, `logs/p8-allfail-20260828.log`.
6. **The degraded strategy stays registered and warm — ✅ PASS.**
   `strategy_states: {StrategyId('SMACrossover-000'): 'DEGRADED', StrategyId('SMAMomentum-002'):
   'RUNNING'}` — degraded, still listed, not removed. And it is **still subscribed**: bar-topic
   handler counts were `{MSFT: 2, AAPL: 2}` both before and after the failure, unchanged. Delivery
   nonetheless stops, and the two facts are consistent rather than contradictory — `Actor.handle_bar`
   gates on the component's FSM state, so a DEGRADED strategy stops *processing* while its bus
   subscription survives, which is what lets it resume. The measured counts show it: the raiser's
   delivered-bar count sat at 27 from the failure to teardown while real MSFT bars kept arriving.

**How the failure was produced, and the four deviations from the procedure as written.** All four
are disclosed because each one changes what the evidence does and does not cover.

- **The two-strategy spec was built in-process**, via the same `SyncTradingSessionRepository.create`
  that `live create` uses — the sanctioned workaround for `--strategy` being singular. Everything
  after the spec is the production path: `claim_session`, `SqlSessionRecord`, `LiveSessionRunner`,
  the eight AR39 phases, the guard, Redis and Postgres.
- **The raiser runs on `MSFT.NASDAQ`, not AAPL.** This is the deviation that matters most, and it is
  what makes criterion 3 meaningful rather than self-defeating. `_generate_sell_signal` closes any
  open long on its own instrument (`sma_crossover.py:239-244`) *before* it reaches the division — so
  with the raiser on AAPL, the account's residual `LONG 4 AAPL.NASDAQ` would have been closed by a
  real order on the way to the failure, and criterion 3 would have failed for a reason that has
  nothing to do with containment. Pointing the raiser at an instrument the account holds no position
  in is what isolates the criterion. `materialise_strategy` derives `instrument_id` from
  `bar_types[0]`, so the spec's bar type is the whole mechanism.
- **25 flat priming bars precede the 0.00 bar.** A single zero bar is not sufficient and run 1
  proved it: `on_bar` returns early until both SMAs are initialised (`sma_crossover.py:105-106`), and
  crossover detection needs a prior `fast >= slow`. Priming at a **constant** price initialises both
  averages to the identical value, which triggers neither branch and submits no orders; the 0.00 bar
  then drags fast below slow from equality, which is exactly the bearish guard. The failure is
  otherwise entirely real — the traceback runs guard (`live_strategy_guard.py:438`) → Nautilus's own
  `Actor.handle_bar` re-raise → `sma_crossover.py:114` → `:192` → `:250` → **`:150`**, raising
  `decimal.DivisionByZero` from `position_value / current_price`. No monkeypatched `raise`, no probe
  strategy standing in for one.
- **A bar-counting wrapper sits between `Actor.on_bar` and the strategy's own `on_bar`** and appears
  in the traceback as `live_contain_probe.py:139`. It increments a counter and delegates; it is
  installed after `subscribe_bars`, so the guard remains outermost and the re-raise path is
  unchanged.

**Two observations worth carrying forward.** Run 1 is a useful negative control: with cold
indicators, the real `sma_crossover` **silently ignored a zero-priced bar** — it stored it, updated
both SMAs with it, and returned at the initialisation guard. A zero or otherwise absurd print is
therefore not rejected, merely deferred, and it poisons both moving averages on the way past; that is
adjacent to the known "indicators start cold" issue and is not something this story owns. Separately,
the injected bars reach `LiveBarObserver` like any other, so they advance `last_bar_at` — visible in
the row as `last_bar_at=10:27:19`, the injection instant. Anything reading `last_bar_at` as evidence
of *market* activity should know that.

**Housekeeping:** the 2026-08-28 session left six real `trading_sessions` rows, all ending `stopped`
except the unused one, plus their `trader-PAPER-*` Redis namespaces: `contain-test-1`
(`bfb677c3-…`, the negative control), `contain-test-2` (`41b3e662-…`), `p8-startfail-1`
(`c949e58c-…`), `p8-allfail-1` (`72808350-…`), `p7-position-test` (`621bb88c-…`), `p9-status-test`
(`14a0b7a8-…`), and `rth-day-1` (`b0151c65-…`, still `created`, waiting for P6). The repositories are
write-once and expose no delete, so they are named here rather than removed with raw SQL, following
the posture Stories 2.5 and 2.6 took. ⚠️ Four of them are **not ordinary sessions** and no CLI can
reproduce them: `contain-test-1` and `contain-test-2` carry two-strategy specs (the latter naming
`MSFT.NASDAQ`), and `p8-startfail-1` / `p8-allfail-1` name `p8_start_raiser`, a strategy registered
only by `scripts/diagnostics/p8_start_failure_strategy.py`. **`live start` cannot run those last
two** — the stored spec will not resolve in a process that has not imported that module, and it will
fail with a pydantic `ValidationError` naming the registered strategies rather than anything more
mysterious. Re-run all four through `live_contain_probe.py`.

## Procedure P9: `live status` against a genuinely running session, then a `kill -9`

**Introduced by**: Story 2.8 — See What a Session Is Doing Without Reading Logs
**Verifies**: AC #1, #2, #3 against a real running process — that `status` answers from the
database alone, from a **different process** than the runner, and that it tells a quiet session
from a dead one.
**Tool**: the CLI itself — `ntrader live start` in one terminal, `ntrader live status` in a
second, and `kill -9` on the runner's PID. No diagnostic script; the two commands are the
artifact under test.

### What it does — and does not — do

It does **not** re-verify anything Procedure P8 covers (contained-strategy visibility,
`runtime_flags`, broker-side position identity) — `⛔ not run` there is unrelated to this
procedure's result, and this one does not replace it: P8 remains the gate before Epic 2 closes.
It does **not** exercise `degraded` from a real connection loss — that sense's writer is Epic 4's,
and this story's `connection_lost_at` reader is proven dormant by a unit test instead (see the
story's Dev Notes on the false-green trap).

### Preconditions

Everything Procedure P7/P8 require — IB Gateway or TWS logged into a **paper** account, Redis and
Postgres running, `alembic upgrade head`. Run inside RTH so the session actually observes bars
(`trading` is otherwise unreachable — outside RTH the honest answer is `idle`, which is also worth
recording once).

### Command

```bash
# Terminal 1 — start a session and note its PID.
#
# NOT `... | tee logfile &` with `$!`: in a backgrounded pipeline `$!` is the PID
# of the LAST command, so it would name `tee`, and the `kill -9` below would kill
# the log writer while the runner survived (or died later and nondeterministically
# on SIGPIPE) — a false negative on pass criterion 3, for a reason unrelated to
# the code. Redirect straight to the file so `$!` really is the runner.
uv run python -m src.cli.main live start p9-status-test > logs/p9-status-test.log 2>&1 &
RUNNER_PID=$!
echo "runner pid: $RUNNER_PID"

# Sanity-check before trusting it: this must print the `live start` process.
ps -p "$RUNNER_PID" -o pid=,command=

# Terminal 2 — while it is running, and again ~35s later (past one heartbeat tick).
uv run python -m src.cli.main live status p9-status-test
uv run python -m src.cli.main live status p9-status-test --json

# Then, back in Terminal 1, kill the runner hard — no SIGTERM/SIGINT, no teardown.
# (Terminal 1, because $RUNNER_PID is a shell variable of that shell. From
# Terminal 2, use the numeric PID printed above.)
kill -9 "$RUNNER_PID"

# Wait past the 90s staleness threshold (three missed heartbeats), then query again.
sleep 95
uv run python -m src.cli.main live status p9-status-test
```

### Expected output

While running, inside RTH, after at least one bar has closed:

```
session: p9-status-test (<uuid>)
  state: running
  health: trading
  closed trades: 0, open positions: 0
  heartbeat: <N>s ago
  last started: <iso-8601>
  trader_id: PAPER-<hex8>
  last activity: <iso-8601>
```

After `kill -9` and the 90s wait:

```
session: p9-status-test (<uuid>)
  state: running
  health: stale
  ...
```

### Pass criteria

1. **`status` answers with the runner alive, from a different process.** The command in Terminal 2
   never blocks on or waits for Terminal 1; it returns immediately from the database alone (AC #1).
2. **`health` reads `trading` once a bar has closed inside RTH**, and `idle` if queried before the
   first bar or outside RTH — both are legitimate, distinguishable answers, never the same word
   (AC #3, the "silence is distinguishable from death" half).
3. **After `kill -9`, `health` reads `stale` — never `idle` and never `trading`** — once the
   heartbeat's age exceeds 90s. This is AC #3's other half, the one an automated test cannot touch
   for real: nothing but a genuinely dead process proves the heartbeat actually stops advancing.
4. **The row is untouched by the query.** `status` triggers no reclaim, no transition and no write
   — confirm `last_started_at` is unchanged across every query in this procedure, including the
   one after `kill -9` (AR33: only `start` may reclaim a stale session).
5. **`--json` parses** and carries exactly the seven pinned keys, matching the human output's
   `state`/`health` values.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-08-28 | Allay (Claude Code session) | ✅ **pass — all five criteria met live** | Run inside RTH (Friday, 10:39–10:42 ET) against the paper Gateway on `127.0.0.1:4002`, Redis and Postgres up. Session `p9-status-test`, `session_id=14a0b7a8-c578-4877-8062-8a7618c6dcb5`, `trader_id=PAPER-14a0b7a8`. Driver: `scripts/diagnostics/run_p9_status.sh`, which only sequences the two real CLI commands. Logs: `logs/p9-driver-20260828.log`. **Criterion 1** — `live status` answered from a second process while the runner was alive, returning immediately from the database without blocking on the runner. **Criterion 2** — `health: trading` with the runner alive, twice (heartbeat `8s ago`, then `15s ago`), `state: running`. **Criterion 3** — after `kill -9` on the runner, and past the 90s threshold, `health: **stale**` with `heartbeat: 113s ago` — never `idle`, never `trading`. `state` stayed `running`, which is what makes the reading meaningful: the row still claims a live session and only the heartbeat age exposes that it is dead. **Criterion 4** — `last_started_at` was **identical** at all three sample points (`2026-08-28 10:39:03.759909-04:00` before any query, after both queries, and after the `kill -9`), so no query reclaimed, transitioned or wrote. **Criterion 5** — `--json` parsed and carried exactly the seven pinned keys (`session_id, name, status, closed_trade_count, open_positions, last_activity_at, health`), with `health` matching the human output in both states (`trading`, then `stale`). Three things worth recording. **(a)** `idle` was captured in a follow-up run at 10:55 ET, closing the last part of criterion 2: querying a session during its `node:build`/`node:connect` window — `status: running` with `last_bar_at` still null — returned `health: idle` on four consecutive samples. All three values named by criteria 2 and 3 have therefore now been observed live and are distinct: **`idle`** (running, no bar yet), **`trading`** (running, bar seen), **`stale`** (heartbeat older than 90s). Log: `logs/p9-idle-20260828.log`. **(b)** The first `live_bars.received` appeared within 8 seconds of start, so `health` reached `trading` on a **backfill** bar delivered at subscription rather than one that closed inside the window — the same immediate-first-bar behaviour recorded under P3 on the same day. It is a real received bar and the criterion is met, but "a bar has closed" is not what made it flip. **(c)** Incidental, and corroborating AR33 against a real database: an earlier aborted attempt was killed with `kill -9`, and the next `live start` 30 seconds later refused with `session.reclaim_refused heartbeat_age_seconds=30.94 stale_after_seconds=90.0` and the message *"already running and its heartbeat is 31s old … another process appears live"* — the reclaim guard correctly declining a not-yet-stale session, exit 1. ⚠️ **Harness note for whoever re-runs this.** The procedure warns against `\| tee` because `$!` would name `tee`; there are two further variants of the same trap, both hit and fixed here. Backgrounding a shell **function** makes bash fork a subshell, so `$!` names that subshell — measured, `$!` gave a `bash run_p9_status.sh` PID whose grandchild was the runner, and a `kill -9` on it would have left the runner heartbeating and produced a false negative on criterion 3. And `uv run` spawns its own python child and **does not forward signals** (this file already records that under P7). The reliable form is to resolve the runner by pattern — `pgrep -f "bin/python3 -m src.cli.main live start <session>"` — rather than trusting `$!` at all. |
| — | — | ⛔ **not run** | Written with Story 2.8. `⛔ not run` is an acceptable and expected entry, per this file's own policy at the top — a dry run is not a pass. The health-derivation logic (AC #2) is fully covered by `tests/unit/core/test_live_session_health.py`'s truth table and by the mutation sweep (Story 2.8, Task 10); what this procedure alone can show is the two facts no unit test can fabricate — a real heartbeat actually advancing while a session trades, and a real process actually going silent under `kill -9`. **Procedure P8 remains the gate before Epic 2 closes; this procedure does not replace it or narrow its scope.** |
