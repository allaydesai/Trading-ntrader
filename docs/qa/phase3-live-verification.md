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

## Phase-gate evidence index

> Added by Story 4.7 (AC #4; NFR33, AR22). This is the map from the phase gate's five
> categories to the procedures that evidence them. Each result below is copied from that
> procedure's own Result log, which stays the source of truth: update the procedure first, then
> this row. ✅ passed live · ⚠️ partial · ⏳ defined, not run · ℹ️ prepared, not run. The
> evidence is operator-run and cannot live in CI (NFR32/NFR33). Every behaviour here is also
> proven against broker doubles in the unit, component and integration tiers.

| Category | Procedure | What it proves live | Latest result |
|---|---|---|---|
| **Connectivity** | P1 | A node builds, connects to IBKR paper, starts and stops cleanly | ✅ 2026-08-28 |
| | P3 | Real-time RTH bars arrive for a configured instrument | ✅ 2026-08-28 |
| | P4 | A real disconnect withholds trading permission | ⚠️ 2026-08-28: criteria evidenced, probe exits 1 on its own assertion |
| | P5 | `ntrader live check`: connectivity from the CLI, exit 4 when unreachable | ✅ 2026-08-28 |
| **Gate refusal** | P2 | `gate:account` verifies the connected account is paper | ✅ 2026-08-28 |
| | P5 | The static gate refuses a real-money config (exit 3) before any socket | ✅ 2026-08-28 |
| **A complete round trip** | P7 | The first strategy-owned position, and a clean Ctrl-C stop | ✅ 2026-09-01 (criterion 2 closed). Later rows: ⚠️ 2026-09-01 (later), the `flatten_position.py` dry-run incident; the criterion stays closed on its transcripts. ℹ️ 2026-09-10, NFR1 ruled, no run |
| | P11 | A closed position recorded at its volume-weighted prices and full commission | ✅ 2026-09-21 |
| | P12 | A `kill -9` mid-run loses no trade that had closed | ✅ 2026-09-21 |
| | P13 | A session rejected on every order stays up and says so | ✅ 2026-09-22 |
| **Restart resume** | P9 | `live status` against a live session, then a `kill -9` read as stale | ✅ 2026-08-28 |
| | P10 | A restarted, already-traded session restores its evidence | ✅ 2026-09-11 |
| | P14 | A restarted session is warm before its first live bar | ✅ 2026-09-28 |
| | P17 | Startup reconciliation against the real broker (P17a read-only; P17b refusal) | ⚠️ 2026-09-28: P17a ✅; P17b still ⏳ (operator only) |
| | P19 | Resume mid-position: stop and restart across an open position, no artificial exit | ⚠️ 2026-09-28: P19b ✅; P19a criterion 1 not literally met, accepted by PO ruling (see Result log) |
| **Reconciliation** | P15 | The broker's positions and cash, matched against TWS | ✅ 2026-09-28 |
| | P16 | `ntrader live reconcile` on demand, including against a running session | ✅ 2026-09-28 |
| | P17 | Startup reconciliation, as above | ⚠️ 2026-09-28: P17a ✅; P17b still ⏳ (operator only) |
| | P18 | Runtime alignment: a TWS change corrected, a reconnect re-established | ⏳ 2026-09-27 (operator only) |
| | P20 | Corporate actions absorbed and named; a reverse split refused | ⏳ 2026-09-28 (operator only) |

**Where the gate stands (2026-09-28, updated at the Epic 4 retrospective).** Connectivity, gate
refusal and the complete round trip have passed live, with P4 partial. A live E2E pass on
2026-09-28 (14:17–14:41 ET, `p3-epic4-base` worktree, f892d56) closed most of Epic 4's read-only
and resume procedures: **P14, P15, P16 (A+B), P17a and P19b passed live**; **P19a's criterion 1**
printed a non-zero `EXTERNAL` grep count on a position-holding account and was accepted as a
documented exception by PO ruling rather than re-run (see P19's Result log — the filter's own
discard-lines, not an unfiltered import, are what matched). **Still genuinely not run**: P17b,
P18, P20 (all operator-only — they start strategies or change a broker position by hand) and the
09:25 ET NFR3 variants. Running those remains the operator's phase-gate step.

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
position handling on stop. **[Corrected 2026-08-28]** Story 3.1 removed the call; stopping now
leaves positions untouched. This procedure's live "before" (a session-owned position flattened by
`on_stop()`) was never captured — see the entries below.

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

> **[Corrected 2026-08-28, amended 2026-08-29 at code review]** Story 3.1 landed and removed the
> flatten. FR18 is proven **in a backtest** by
> `tests/integration/test_sma_strategy_nautilus.py::TestOnStopEquivalenceAcrossVariants` (the
> story's evidence path per the retro amendment, `epics.md:1166–1169`). It is **not** proven end to
> end: no live session has ever been stopped while holding a session-owned position, so this
> procedure's own "before" was never captured (see the note below) and neither was its "after". The
> first version of this correction said "proven end to end" — that overstated backtest evidence as
> live evidence, inside the section that exists to name exactly this gap. The live half remains
> open and belongs to Story 3.2's order-path work.

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
Positions were left untouched by the stop — the strategy did not submit any exit order on your
behalf.
```

**[Corrected 2026-08-28]** the console text above is Story 3.1's wording (`src/cli/commands/live.py`);
the pre-3.1 text warned that `sma_crossover.on_stop()` still flattened.

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
2. **Broker state before and after are identical, full stop** (Story 3.1: `on_stop()` no longer
   flattens anything) — record the IBKR positions page (or `reconcile` output, once Epic 4 has one)
   at both ends.
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
| 2026-08-28 (post-fix) | Allay (Claude Code session) | ⚠️ **both order-path defects FIXED and proven live as far as order submission; criterion 2's position-open half DEFERRED TO EPIC 3 (Story 3.2) on Allay's call — it does not gate Epic 2** | **Scope note, decided 2026-08-28:** neither defect below is an Epic 2 failure. Epic 2 asserts no order reaches the broker — its board entry reads "Still no orders", and its only order language is negative (Story 2.6: "no order is submitted on the stop path") or reporting (Story 2.8: "a session that is running but has not traded"). Orders were merely being *attempted*, because Story 2.5 starts real strategies. Criterion 2's **no-position** half passed on 2026-08-23; the **position-open** half needs an order path, which is Epic 3's subject, so it moves to Story 3.2 and Epic 2 closes without it. Re-run after fixing the blocker the entry below found, which turned out to be **two** defects sitting in series, each fully masking the next. **Fix 1 — default routing.** `routing=RoutingConfig(default=True)` now passed on `InteractiveBrokersExecClientConfig`. Verified against the installed 1.220.0 source rather than assumed: Nautilus registers a client as the engine's default only when the config asks (`live/node_builder.py:252-254`), and its own fallback fires **only** for a client built `venue=None` (`execution/engine.pyx:421-429`); the IB exec client is built `venue=IB_VENUE` (`execution.py:157`) so it never qualified, while the IB *data* client is built `venue=None` (`data.py:115`) so it did — which is exactly why market data worked and hid this for two epics. **Proven live**: `ExecClient-INTERACTIVE_BROKERS: Submit MarketOrder(SELL 22 NVDA.NASDAQ MARKET GTC …)` appeared in `logs/p7-position-20260828-153506.log` — a line that had **never** appeared in any transcript before, and the exact thing `Cannot execute command: no execution client configured for NASDAQ` had been replacing. **Fix 2 — the exec client's instrument provider**, revealed *by* fix 1 within the same run: the adapter dereferences `self.instrument_provider.find(order.instrument_id).is_inverse` with no `None` check (`execution.py:525`), so the first order ever to reach the client died as `AttributeError("'NoneType' object has no attribute 'is_inverse'")` raised **inside** the adapter's own `submit_order`, where no strategy can see it. The exec client now loads the same `load_ids` as the data client; **proven live**: `InteractiveBrokersInstrumentProvider: Adding instrument=Equity(id=NVDA.NASDAQ …)` now appears on the exec side. Regression cover: `TestExecutionRouting` (drives Nautilus's real `TradingNodeBuilder.build_exec_clients`, with an anti-tautology twin that counts **zero** under the stock `RoutingConfig` and a control showing an `INTERACTIVE_BROKERS`-venued order routed even before the fix) and `TestInstrumentLoading::test_the_exec_client_loads_the_same_instruments_as_the_data_client`. Full component tier 1297 pass, unit 2377 pass. **Why criterion 2 is still open**: four further attempts (`154402` client_id 17, `154259`, `154527` on a fresh `p7-position-msft` session) all received **zero bars**, because IB killed each bar subscription at birth with `Historical Market Data Service error message:Trading TWS session is connected from a different IP address (code: 162, req_id=…)`. It is **not** a client-id conflict (reproduced on id 17) and **not** contract-specific (reproduced on MSFT as well as NVDA) — it is account-wide, and it began mid-session at ~15:39 UTC after earlier runs the same hour had taken bars normally. **Cause confirmed by the operator: they signed into IBKR on their mobile device mid-session.** IBKR permits one active session per account, so the mobile login evicted the Gateway's market-data entitlement while leaving its API socket connected — which is why the session still connected, still passed both gate layers, still reported `Subscribed … bars`, and still heartbeated, while receiving nothing. Worth knowing for every future procedure: a phone in your pocket silently kills a running session's data feed, and nothing in the session says so. No bars → no crossover → no order → no fill. The one `OrderFilled` in the MSFT transcript is the **inferred** fill reconciliation generates for the pre-existing `AAPL.NASDAQ-EXTERNAL` residual, not a session fill. **To close criterion 2**: resolve the competing IBKR login (mobile app, client portal, or a second TWS/Gateway holding the paper account), then re-run `./scripts/diagnostics/run_p7_position.sh` inside RTH. The order path itself is no longer the obstacle. ⚠️ This is the **third** instance of the same class of defect — an IB subscription error that permanently kills a stream with nothing retrying it (10182 on 2026-08-28 for P5, now 162 here); see `deferred-work.md`. |
| 2026-09-01 | Allay (Claude Code session) | ✅ **criterion 2's position-open half is CLOSED — the first strategy-owned position this project has ever opened, filled, and kept across a stop.** One upstream defect and one self-inflicted regression found and fixed on the way | Run inside RTH (Tuesday, 09:47–10:33 ET). Preflight took 3 live NVDA bars and exit 0, so the 162 mobile-login eviction was genuinely gone. **Read the three phases below in order; the middle one is a mistake I made during this session and is recorded as such.** ⓵ **The first real fill, and it killed the node.** Session `p7-position-test`, `sma_crossover` on `NVDA.NASDAQ` (`fast_period=2 slow_period=3 position_size_pct=0.5`). At 13:56:05Z a SELL signal produced `order.submitted` → `OrderSubmitted` → `order.accepted` with `venue_order_id=101` 218ms later, and the order **filled at the broker** (23 NVDA short). The fill then raised `TypeError: Encoding objects of type nautilus_trader.model.objects.Price is unsupported` in `MsgSpecSerializer.serialize`; `ExecEngine` logged `Initiating graceful shutdown due to unexpected exception` and the node tore itself down. **Root cause, read off the wheel:** the IB adapter stores the order's average fill price as a `Price` *object* (`adapters/interactive_brokers/execution.py:188`, written `:1037`) and copies it straight into `OrderFilled.info["avg_px"]` (`:1096`); `OrderFilled.to_dict_c` stringifies every other field but passes `info` through by reference (`model/events/order.pyx:4810`); and a live session serializes every fill into the Redis cache (AR10) via `_apply_event_to_order` → `Cache.update_order` → `CacheDatabaseAdapter.update_order` (`cache/database.pyx:1159`). A backtest has no cache database, which is why two epics never saw it. **The damaging part is the ordering:** `_apply_event_to_order` runs *before* `publish_c(topic=f"events.order.{...}")` at `execution/engine.pyx:1176`, so the `OrderFilled` was **never published** — no `order.filled` record, no `Portfolio` update, no position event, while the order really had filled. Story 3.3's `order.filled` was therefore unobtainable live for reasons entirely outside Story 3.3. Fixed in `src/core/live_exec_avg_px.py`: the adapter's `_order_avg_prices` map is replaced with one storing `str(value)`. Safe because that map is written at one site and read at exactly three, all three only assigning into `info["avg_px"]` — both halves pinned against the adapter's own source. The upstream defect is pinned as a canary (`TestTheWheelDefect`) so an upgrade that fixes it makes the module deletable by name. ⓶ **A self-inflicted regression, disclosed rather than quietly fixed.** The fix is delivered by subclassing the IB exec-client factory. I first named that subclass `_AvgPxSafeExecClientFactory` — and Nautilus decides whether to call `cache.set_specific_venue(Venue("INTERACTIVE_BROKERS"))` by comparing `factory.__name__` against that **literal class-name string** (`live/node_builder.py:265-268`, under its own comment "Temporary handling for setting specific 'venue' for portfolio"). With the branch silent, `Cache.account_for_venue` resolves the *instrument's* venue `NASDAQ` instead of the account's issuer (`cache.pyx:3704-3705`), finds no account, and `Portfolio.initialize_positions` breaks with `initialized = False` (`portfolio.pyx:347-353`) — a **one-shot** call, so the flag can never flip later. `kernel.start_async` then returns fail-quiet at `kernel.py:1024`, the line immediately before `self._trader.start()`. **Symptom: four consecutive sessions connected, reported both engines healthy, raised nothing, and died 120s later with `the session did not start within 120s`.** It was briefly and wrongly attributed in this file to a pre-existing race; it was not. **No test tier caught it**, because none builds a real `TradingNode` *and* inspects portfolio initialisation — the integration tier builds nodes but only asserts the factories are registered, which the broken version satisfied. The wrapper is now named `InteractiveBrokersLiveExecClientFactory` (stock factory imported aliased), with the constraint documented at the class and two new tests: one pinning our `__name__`, one canarying Nautilus's branch so an upgrade that reworks it becomes a decision rather than a live-gateway discovery. ⓷ **The run that closes the criterion.** Fresh session `p7-fill-0901` (`session_id=483c0655-87a4-4294-9dcf-1e5cf83cd507`, `trader_id=PAPER-483c0655`), same recipe. `session.connected` → `session.started` → `Subscribed NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL bars`. At 14:32:05Z: `Position sizing: Portfolio=$1,000,000, Size%=0.5%, Price=$217.86, Qty=22`, `Generated BUY signal`, then the **complete lifecycle in structured records** — `order.submitted` (`client_order_id=O-20260901-143205-483c0655-000-1`), `order.accepted` (`venue_order_id=103`, `strategy_id=SMACrossover-000`), and **`order.filled`** carrying `fill_qty=22 cum_qty=22 order_qty=22 last_px=217.83 commission='1.00 USD' currency=USD trade_id=00025b45.6a9b76bb.01.01 venue_order_id=103 position_id=NVDA.NASDAQ-SMACrossover-000`. **That is Story 3.2's AC #7 (the real broker commission) and Story 3.3's AC #1/#2/#5 evidenced live in one record.** `cum_qty == order_qty == 22` makes fill-path finality readable from the record alone, which is exactly what AC #5 asks. Then `PositionOpened(position_id=NVDA.NASDAQ-SMACrossover-000, side=LONG, quantity=22, avg_px_open=217.83)` — **strategy-owned**, not the `EXTERNAL` residual every prior run had to settle for. **Criterion 2, both halves:** broker state was **byte-identical across the stop** — `GrossPositionValue 6090.87 / NetLiquidation 32524.02` before and after — the SIGINT produced `session.stopped signal=SIGINT trader_started=True` and `Session stopped: p7-fill-0901 (SIGINT)`, and the position **survived**: `Residual Position(LONG 22 NVDA.NASDAQ, id=NVDA.NASDAQ-SMACrossover-000)` at teardown, with **zero** close/flatten lines in the transcript. **This also closes Story 3.1's FR18 live half** — the note at `:676-684` recording that "no live session has ever been stopped while holding a session-owned position" is now discharged: one was, and `on_stop()` left it alone. **Story 3.3's two review-decided fields both fired live, unprompted:** reconciliation-sourced records carry `reconciliation=True` with `commission='0.00 USD'` and `strategy_id=INTERNAL-DIFF`, plainly distinguishable from the live fill's `1.00 USD` (Task 4.2's whole purpose), and those same records carry `order_qty_unknown=True` — the review's "mark the record" decision, exercised by a real first-sight-without-`OrderInitialized` order rather than by a test. **NFR26 holds live: zero of this codebase's structured records carried `account_id`** (measured, not assumed). Detection grep: one `code: 162`, at 14:32:27.706 — the same instant as the SIGINT, and the benign `API historical data query cancelled` variant, not the competing-login one; no `10182`, no `366`. **Gates:** format/lint/typecheck clean, unit 2421, component 1427/16 skipped, integration 281/2 skipped, e2e pass, Epic 1 sweep 40/40 ACs / 43 tests. Four integration assertions moved from `is` to `issubclass` (the registered factory is a subclass now), reason recorded inline. **Two findings recorded in `deferred-work.md`, neither actioned here.** (1) **NFR1 cannot be met as defined:** `bar_close_to_submit_ms=65682.202` against a `<1000` target, but the latency is not real — IB timestamps a 1-minute bar at its *open* and delivers it ~5s after close, so the true decision-to-submit interval was **~1ms** (bar received 14:32:05.681, order submitted 14:32:05.682) and the 65s is one bar interval plus delivery lag. `_log_submitted`'s docstring calls the anchor "the venue bar close", which is false for this adapter, and the value sits inside `MAX_PLAUSIBLE_LATENCY_NS` so the `implausible_latency_ms` escape hatch does not catch it. Epic-level definition question, not a story bug. (2) **An unapplied fill strands an order in `orders_open` forever:** phase ⓵'s crash left the order `ACCEPTED` with its id still in `trader-PAPER-621bb88c:index:orders_open`, and the adapter cannot self-heal (`Cannot generate list[FillReport]: not yet implemented`). **Housekeeping.** The orphaned `SHORT 23 NVDA.NASDAQ` from phase ⓵ was closed on Allay's explicit instruction with the new `scripts/diagnostics/flatten_position.py` — BUY 23 filled at 217.84, commission 1.00 USD, `venue_order_id=102`, net back to +0. That tool runs both gates, reads the position from the broker, and refuses unless `--side`/`--quantity` exactly offset it (both refusals verified before arming — ⚠️ **but see the 2026-09-01 (later) row: that verification was worthless, because at the time a separate bug was preventing the trader from starting at all**). ⚠️ It builds its node without a `cache=` argument, so it ran `database=None` and **never invoked the serializer** — it is not evidence for the phase ⓵ fix. This run leaves a real `LONG 22 NVDA.NASDAQ` position open under `p7-fill-0901`, deliberately preserved as criterion 2's evidence; it is the operator's to close. Logs: `logs/p7-position-20260901-095200.log` (the fill and the crash), `logs/flatten-nvda-20260901-101157.log`, `logs/p7-position-20260901-102929.log` (**the passing run**). |
| 2026-09-01 (later) | Allay (Claude Code session) | ⚠️ **`flatten_position.py` submitted and filled an order on a DRY RUN. Defect found, fixed, and covered; the NVDA position is flat and verified.** | Asked to close the `LONG 22 NVDA.NASDAQ` the row above left open, I ran the tool **without** `--confirm` first, as a read-only check. It traded: `OrderFilled(client_order_id=O-20260901-144714-FLATTEN01-000-1, venue_order_id=104, order_side=SELL, last_qty=22, last_px=217.87, commission=1.10 USD)` at 14:47:15Z, followed by `PositionOpened(side=SHORT, signed_qty=-22.0)` — all of it **before** the tool printed `broker reports net +0`, which is the line its own offset check reads. **Cause.** The strategy was added to the node before `run_async()`, and `TradingNodeKernel.start_async` ends with `self._trader.start()` (`system/kernel.py:1027`), which starts every strategy already added. `on_start` submitted from there — before the connect wait, before the broker read, before the offset check, and before the `--confirm` branch. `--confirm` gated only whether `start_strategy` was called *afterwards*. `live_session_runner.py` avoids the identical trap by construction (it adds no strategy until `_phase_trading`, long after `_phase_node_connect` started the node), and the tool's comment cited the runner's `start_strategy` call as proof that `run_async()` does not start strategies — a misreading of *why* the runner is safe. **Why the earlier verification missed it.** The refusals recorded in the row above were hand-checked while the misnamed exec-client factory was still causing `start_async` to return early at `kernel.py:1025`, before `_trader.start()`. The trader never started, so `on_start` never fired and the dry run genuinely was inert — a true observation about a build that no longer exists. Committing the factory fix removed that accidental safety net. **The strike-through on that row's parenthetical is deliberate: treat it as withdrawn, not qualified.** **Fix.** The strategy is now constructed and added only after the broker read, the offset check, and the operator's arming — so a dry run hands the trader nothing capable of submitting. Covered by `tests/component/scripts/test_flatten_position.py` (10 tests), which drives `_run` against a fake node and asserts **registration**, not submission: reintroducing the defect fails exactly `test_a_dry_run_hands_the_trader_nothing_that_could_submit` and `test_a_refused_run_armed_nothing`, verified by re-applying it. **Account state, verified after the fix on a genuinely inert dry run** (`logs/flatten-nvda-verify-20260901-150058.log`): `broker reports net +0 NVDA.NASDAQ`, `GrossPositionValue 1298.85` (= the 4 AAPL residual alone, down from 6093.33), and the only teardown residual is `Position(LONG 4 AAPL.NASDAQ, id=AAPL.NASDAQ-EXTERNAL)` — no NVDA position, and no `_FlattenStrategy` position, i.e. this run registered nothing. **NVDA is flat.** Net effect on the account: the accidental SELL 22 closed the very position it was asked to close, at 217.87 vs the 217.83 open, for 1.10 USD commission. ⚠️ **Criterion 2's evidence is consumed.** The row above preserved that position deliberately as the live artifact; it no longer exists. The criterion stays closed on the transcripts already recorded (`logs/p7-position-20260901-102929.log`), which are complete — but the live position they describe cannot now be re-inspected. Logs: `logs/flatten-nvda-dryrun-20260901-104713.log` (the dry run that traded), `logs/flatten-nvda-verify-20260901-150058.log` (the fixed, inert one). |
| 2026-09-10 | Allay (Claude Code session) | ℹ️ **NFR1 ruled (option (c)); no live run.** | The `bar_close_to_submit_ms = 65682.202` in the 2026-09-01 row was produced by a field that measured from the bar's *open* (this adapter's `bar.ts_event`), so it is one 60s interval plus IB's delivery lag, not a latency. Ruling: `bar_close_to_submit_ms` is now anchored on the close (`ts_event + interval`) and is the unbounded divergence window; a new `bar_arrival_to_submit_ms` (anchored on `bar.ts_init`, the bar's arrival in this process) is the interval NFR1's `< 1s` is judged on. Re-derived from that row's own timestamps: ~5682ms close-to-submit (bar close 14:32:00.000Z, submit 14:32:05.682Z) and ~3.5ms arrival-to-submit. NFR1 amended in `epics.md:113` / `prd.md:925`; disposition in `deferred-work.md`. **The next RTH session that submits an order is the first transcript to carry both fields** — read NFR1 from `bar_arrival_to_submit_ms` on a steady-state bar, and expect `bar_close_to_submit_ms` in the 5–6s band. |
| 2026-08-23 | Allay | ⚠️ **5 of 6 pass, 1 partial** | Run against a real IB Gateway on the paper port (4002), account `***626`, Redis and Postgres up, after the Story 2.6 code review's fixes. Session `p7-stop-test`, `session_id=6ffd1556-a854-4a2f-9b0c-7b2f14958a6c`, `trader_id=PAPER-6ffd1556`. Logs: `logs/p7-c1-graceful.log`, `logs/p7-c3-build.log`, `logs/p7-c4-force.log`, `logs/p7-c5-restart.log`. Signals were delivered with `proc.send_signal` to the CLI process directly (not through `uv run`, which does not forward them). **Criterion 3 is a partial pass and is the one thing to read.** Detail below. |

#### Result detail — 2026-08-23

1. **First Ctrl-C — ✅ PASS.** `session.stopped session_id=6ffd1556-… signal=SIGINT trader_started=True`,
   console `Session stopped: p7-stop-test (SIGINT)` plus the Story 3.1 residual warning (the
   warning printed on that date; Story 3.1 has since replaced that trailer), exit **0**.
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
   most: the isolation verb is `degrade()` precisely *because* `stop()` would run the strategy's own
   `on_stop()` mid-containment — the wrong time for any strategy-owned side effect to run, regardless
   of what that hook does today (Story 3.1 removed `sma_crossover`'s flatten, but the reasoning is
   about the verb, not one strategy's current body).
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
   `DEGRADED` (not removed), and it is still subscribed to its bar type. `Trader._stop()` itself still
   guards on `is_running` and skips a `DEGRADED` strategy's `on_stop()` — unchanged Nautilus behaviour
   — but **[Partly resolved 2026-08-28, Story 3.1 AC #6; scope corrected 2026-08-29 at code
   review]** the runner's teardown now explicitly calls
   `live_session_runner.stop_degraded_strategies`, so the degraded strategy's own `on_stop()` still
   runs (contained per-strategy) and it ends `STOPPED`, not stuck `DEGRADED`. **What is not
   resolved:** on the dominant *signal* stop path the node has already been stopped before the
   runner's `finally` reaches the helper, so `on_stop()`'s `unsubscribe_bars` does not reach a live
   data engine — the subscription is not torn down against the broker, it simply stops mattering
   because the engines are down. The unsubscribe flows through a running engine only on the
   phase-failure paths. Verify the terminal `STOPPED` state and that `on_stop()` ran; do **not**
   read this criterion as evidence that a live unsubscribe was delivered. Pinned by
   `tests/integration/core/test_live_strategy_failure_survives.py::TestStopDegradedStrategiesAgainstARealTrader`
   and `tests/component/core/test_session_runner_stop.py::TestStopDegradedStrategies`, with the
   runner's own call site pinned by
   `tests/component/core/test_session_runner_stop.py::TestStopDegradedStrategies::test_the_runner_reaches_the_helper_on_the_stop_path`.

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

## Procedure P10: restore evidence on a restarted, already-traded session

**Introduced by**: Story 3.4 — Never Resubmit an Order That Is Already Working
**Verifies**: AC #3/#4's restart path, observationally — that a real restarted process actually
restores its Redis-backed order/counter state before trading resumes, and that both NFR1 latency
fields are present on a live `order.submitted` record. The restore mechanism itself (AC #3) and
the never-resubmit guarantee (AC #1/#2/#4) are proven exhaustively against broker doubles in
`tests/component/core/test_live_order_recovery.py` and
`tests/integration/core/test_live_order_survives_restart.py` (real Redis, `--forked`) — this
procedure is NFR32-scoped observational evidence only, not a substitute for either.

### What it does — and does not — do

It reads a transcript for the restore facts a double cannot fabricate: a real `Cached N order(s)
from database` line and a real `Set ClientOrderIdGenerator client_order_id count to N` line,
both **before** `session.started`, on a session that has actually traded before. It does **not**
stage a disconnect mid-submit and does **not** stage a working order surviving a restart —
`sma_crossover`'s market orders fill in milliseconds inside RTH, so there is no way to leave one
`SUBMITTED`/`ACCEPTED` across a restart on demand with the built-in strategy. The in-flight timeout
and reconnect paths (AC #1/#4) are broker-double evidence by design (NFR32) and stay that way.

### Preconditions

Story 3.2 Task 8.1's, unchanged: (a) inside RTH; (b) **no other IBKR login** — no mobile app, no
client portal (error 162 evicts market data); (c) the bare non-compose Gateway, never the
compose-managed one (`READ_ONLY_API: "yes"` there refuses the first order); (d) Redis up; (e)
strategy is `sma_crossover` only. **Do not use `p7-position-test`** — its namespace holds a
stranded `ACCEPTED` order and startup stalls on it (`deferred-work.md:2309-2325`, Story 4.2's to
fix, not this one's). Use `p7-fill-0901` (holds a `FILLED` order and, unless the operator has
closed it, a real `LONG 22 NVDA` position) or a fresh session that has traded once already.

### Command

```bash
# Restart an already-traded session and capture the transcript.
uv run python -m src.cli.main live start p7-fill-0901 > logs/p10-restart-<date>.log 2>&1 &
RUNNER_PID=$!

# Let it run past at least one signal / one order.submitted, then stop it cleanly.
sleep 180
kill -INT "$RUNNER_PID"   # uv run does not forward signals to a backgrounded &; resolve by
                          # pattern with pgrep if this does not reach the real runner PID.

# Read the transcript for the restore and counter evidence.
grep -n "Cached .* order(s) from database\|Set ClientOrderIdGenerator client_order_id count to" \
  logs/p10-restart-<date>.log
grep -n "session.started\|order.submitted" logs/p10-restart-<date>.log
grep -c -E "162|10182|366" logs/p10-restart-<date>.log   # D6's standing rule — before anything
                                                          # else is recorded
```

### Expected output

```
Cached 1 order(s) from database
Set ClientOrderIdGenerator client_order_id count to 1
session.started ...
order.submitted client_order_id=O-...-000-2 bar_close_to_submit_ms=... bar_arrival_to_submit_ms=...
```

### Pass criteria

1. **The restore is real and ordered correctly** — `Cached N order(s) from database` with `N ≥ 1`,
   and `Set ClientOrderIdGenerator client_order_id count to N`, both appear **before**
   `session.started` in the transcript.
2. **The counter carries forward** — any new `order.submitted` record's `client_order_id` has a
   trailing counter strictly greater than `N`, and no `client_order_id` value appears twice across
   every `order.submitted` record in the transcript.
3. **Both NFR1 fields are present on the same record** — `bar_close_to_submit_ms` and
   `bar_arrival_to_submit_ms` both appear on the same `order.submitted` line, discharging the
   2026-09-10 ruling's "not yet re-measured live" note (`deferred-work.md:2306-2307`) if an order
   is submitted during the run.
4. **`grep -c` for `162`, `10182`, `366` is checked before anything else is recorded** (D6's
   standing rule) — a nonzero count invalidates the run as evidence of anything else.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-11 | Allay (Claude Code session) | ✅ **pass — all four criteria met live** | Run inside RTH (Friday, 10:29–10:54 ET) against the bare (non-compose) IB Gateway on `127.0.0.1:4002`, Redis and Postgres up, no competing IBKR login confirmed by the operator beforehand. **Deviation from 8.1's suggested session, deliberate and disclosed**: `p7-fill-0901` was not used. A read-only precheck (`flatten_position.py` without `--confirm`) showed the broker flat (`net +0 NVDA.NASDAQ`), but starting `p7-fill-0901` showed its own `Portfolio` logging `NVDA.NASDAQ net_position=22` after reconciliation — its startup reconciliation log named only `Reconciling NET position for AAPL.NASDAQ`, never NVDA, so the stale cached position was never corrected. Since this session's strategy config is tuned `fast_period=2, slow_period=3` (fast, deliberate crossovers), continuing on it risked exactly the failure mode found below, on a position that was never real. Routed to Story 4.2 rather than worked around; the session was stopped at 200s with zero orders submitted and left untouched. A fresh session, `p10-fresh-0911`, was created instead (`sma_crossover`, `fast_period=2, slow_period=3, portfolio_value=1000000, position_size_pct=0.5` — the same proven fast-crossover tuning as `p7-fill-0901`/P7), which is exactly the doc's sanctioned alternative ("a fresh session that has traded once already"). **Criterion 1 (restore, ordered correctly)** — on restart, `Cached 4 orders from database` and `Set ClientOrderIdGenerator client_order_id count to 1` both appear before `session.started`. **Criterion 2 (counter carries forward, zero duplicates)** — the two post-restart orders carry `client_order_id` suffixes `-000-2` and `-000-3` (both `> 1`), and no `client_order_id` repeats anywhere across either leg's transcript. **Criterion 3 (both NFR1 fields, same record)** — present on every `order.submitted`, pre- and post-restart: pre-restart `bar_arrival_to_submit_ms=1.999 bar_close_to_submit_ms=5412.626`; post-restart `bar_arrival_to_submit_ms=3.359 bar_close_to_submit_ms=5725.408` and `=3.607`/`=5725.656` — discharging the 2026-09-10 ruling's live re-measurement. **Criterion 4 (D6 check first)** — `grep -c` for `162`/`10182`/`366` came back non-zero on every transcript; every hit was inspected and is a false positive — digits inside a nanosecond timestamp (e.g. `...149366000Z` contains `366`) or the benign `Historical Market Data Service ... query cancelled (code: 162)` that fires on every clean `SIGINT` teardown as bar subscriptions are cancelled — never `Client login has been superseded` or any other real competing-login text. Zero duplicate orders from the recovery mechanism this story owns, in both legs. **A live, previously undocumented defect outside this story's scope was found and is recorded in full in `deferred-work.md`'s new "story-3.4" addendum**: the post-restart SELL crossover produced *two* real fills (client_order_ids `-000-2` from `close_position()` and `-000-3` from the strategy's own "open new short" branch), because `sma_crossover.py`'s `_generate_sell_signal`/`_generate_buy_signal` compute `has_long`/`has_short` once and never refresh it after calling `close_position()`. Broker-verified net after: `SHORT 22 NVDA.NASDAQ`, while the session's own `PositionClosed` event reported `side=FLAT` — local bookkeeping was wrong. This is unrelated to Story 3.4's exec-engine recovery mechanism (which produced the correctly-incrementing, zero-duplicate counter above) and does not reopen this story; it is a `sma_crossover` strategy bug, live-confirmed for the first time and **fixed the same day** (`if`/`elif` instead of two independent `if`s; two new integration tests, `TestReversalSubmitsExactlyOneOrder` in `tests/integration/test_sma_strategy_nautilus.py`; full detail in `deferred-work.md`'s "story-3.4" addendum). **Also found**: `flatten_position.py --confirm` (fixed by commit `29e9130` on 2026-09-01) is itself now broken by a fresh Nautilus-side regression — `Trader.add_strategy()` refuses an already-RUNNING trader (`Cannot start strategy, _FlattenStrategy-None not found.`), so `--confirm` can never reach `start_strategy()`. It fails **safe** (no order reaches the broker — reconfirmed by an immediate dry-run read showing the position unchanged), but the tool cannot currently perform its one job. The resulting `SHORT 22 NVDA` was closed with a throwaway one-off script (not committed) built on the pre-fix ordering, using the quantity/side already confirmed twice by the broken tool's own read-only path moments apart; broker re-verified `net +0 NVDA.NASDAQ` afterward. A pre-existing, unrelated `LONG 4 AAPL.NASDAQ` was also found still open at the broker during the sweep — not caused by this session, not touched, flagged to the operator. |
| — | — | ⛔ *(superseded by the row above)* | Written with Story 3.4, 2026-09-11, before any live run. Left here for the history of the standing Epic 2 retro rule this row's resolution follows (run each story's live procedure before the story closes; 3.2 Task 8.4 / 3.3 Task 6.3 precedent). |

## Procedure P11: a closed position is recorded at its volume-weighted prices and full commission

**Introduced by**: Story 3.5 — Aggregate Partial Fills into One Position
**Verifies**: AC #4 and AC #6 live — that the runner actually subscribes the trade recorder and
that a real `PositionClosed` produces exactly one `trade.aggregated` record — plus AC #3's
commission sum on whatever legs the session happens to fill. AC #1/#2's volume-weighting
arithmetic is proven exhaustively against real `Position` objects built from hand-constructed
`OrderFilled` events in `tests/component/core/test_live_trade_recorder.py` (NFR32); this
procedure is observational evidence of the wiring and the degenerate (single-fill) case, not a
substitute for it.

### What it does — and does not — do

A single market order on a small quantity fills whole, in milliseconds, inside RTH — there is no
way to stage a genuine partial fill on demand with the built-in `sma_crossover` strategy, so AC
#1/#2's multi-fill VWAP is broker-double evidence by design (the same NFR32 shape every prior
Epic 3 procedure in this file has recorded). If IBKR happens to fill an order in pieces (several
`order.filled` records sharing one `client_order_id`, `cum_qty` climbing), this run becomes
opportunistic evidence for AC #1 too — checked, not staged. What this run *is* evidence for: the
runner wiring (AC #6), the single-fill-each-side degenerate case (AC #4), and the commission sum
across the two legs of one round trip (AC #3). What it is **not** evidence for: that the recorded
trade matches the broker's own view of the position — `PositionClosed` disagreed with the broker
once already (2026-09-11, `side=FLAT` while short 22; `deferred-work.md:2439-2445` in the P10
addendum) — broker-truth reconciliation is Story 4.3's.

**Flip note (code review, 2026-09-12).** `sma_crossover` never flips (a reversal only closes,
`438b0e4`), so this run cannot exercise it — but if any leg ever *does* close by a flip, or the
cache cannot vouch for the closed leg, the transcript shows one `trade.commission_unavailable`
warning beside a `trade.aggregated` record whose `commission`/`currency`/`fill_count` are `None`.
That is the recorder working as designed, not a failure; pass criterion 4 (zero
`trade.recorder_failed`) is unaffected, and criterion 2's commission check applies only to a
leg with no such warning.

### Preconditions

Story 3.2 Task 8.1's, unchanged: inside RTH; **no other IBKR login** (mobile app and client
portal included — error 162); the bare non-compose Gateway (`READ_ONLY_API: "yes"` on the
compose one refuses the first order); Redis up; strategy `sma_crossover` only
(`fast_period=2, slow_period=3`, the proven fast-crossover tuning that produces a reversal within
minutes inside RTH). **Use a fresh session** — `p7-position-test` is poisoned (a stranded
`ACCEPTED` order, `deferred-work.md:2309-2325`) and `p7-fill-0901` carries a stale
`net_position=22` the broker does not hold (`deferred-work.md:2503-2519`), routed to Story 4.2.
**Know before starting**: `flatten_position.py --confirm` is currently broken by a Nautilus-side
regression and fails safe (`deferred-work.md:2475-2501`) — a position left open at the end of the
run stays open at the broker, by design (stop never flattens, AR40/NFR14), and there is no
working tool to close it on purpose. Say so to the operator before the run, not after.

### Command

```bash
# Start a fresh session and let it complete at least one round trip (entry fill, exit fill).
uv run python -m src.cli.main live start <fresh-session-name> > logs/p11-<date>.log 2>&1 &
RUNNER_PID=$!

sleep <until at least one reversal has fired>
kill -INT "$RUNNER_PID"   # resolve by pgrep pattern if this PID is a wrapper, not the runner

# Read the transcript.
grep -n "trade.aggregated\|trade.recorder_failed\|PositionClosed\|order.filled" logs/p11-<date>.log
grep -c -E "162|10182|366" logs/p11-<date>.log   # D6's standing rule — before anything else
```

### Expected output

```
order.filled ... client_order_id=O-...-000-1 ...
order.filled ... client_order_id=O-...-000-2 ...
trade.aggregated position_id=... entry_price=... exit_price=... quantity=... fill_count=2
  commission=... currency=USD realized_pnl=... trade_key=...
```

### Pass criteria

1. **Exactly one `trade.aggregated` record follows each Nautilus `PositionClosed` line**, bound
   with the session's `session_id` context.
2. **For a leg with one entry fill and one exit fill**, `trade.aggregated`'s `entry_price`/
   `exit_price` equal those two fills' `last_px` exactly (AC #4, live), and `commission` equals
   the sum of the two fills' `commission` values (AC #3, live). If IBKR fills an order in pieces
   (several `order.filled` records sharing one `client_order_id`), `entry_price`/`exit_price`
   equal the VWAP of those fills computed by hand from the transcript (AC #1/#2, live —
   opportunistic, not staged).
3. **`fill_count` equals the number of distinct `trade_id`s** across the leg's `order.filled`
   records (excluding any marked `duplicate=True`).
4. **Zero `trade.recorder_failed` records** anywhere in the transcript.
5. **`grep -c` for `162`, `10182`, `366` inspected before anything else is recorded** (D6's
   standing rule) — every hit inspected; timestamp digits and the benign teardown
   `query cancelled (code: 162)` are known false positives (3.4:363-368).

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-21 | Allay (Claude Code session) | ✅ **pass — all five criteria met live** | Run inside RTH (Monday, 14:02–14:05 ET) against the bare (non-compose) IB Gateway on `127.0.0.1:4002`, Redis and Postgres up. **Preflight first**: `live_bars_probe.py --bar-type NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL --run-seconds 150` returned `RESULT: ok ... bars=3` with zero competing-login text, so the 162 eviction that wrecked the 2026-08-28 attempts was genuinely absent before anything traded (`logs/precheck-nvda-20260921.log`). Fresh session `p11-0921` (`session_id=efe5fb99-a077-4d7b-88ac-88f10d09ab72`, `trader_id=PAPER-efe5fb99`), `sma_crossover` on `NVDA.NASDAQ`, `fast_period=2 slow_period=3 portfolio_value=1000000 position_size_pct=0.5` — the proven fast-crossover recipe, per 8.1's fresh-session instruction. **Criterion 5 (D6 check, read before anything else)** — `grep -c -E "162|10182|366"` = 2; one is timestamp digits, the other is `Historical Market Data Service error message:API historical data query cancelled: 10005 (code: 162, req_id=10005)` at 18:05:31Z, i.e. the benign SIGINT-teardown variant. Zero hits for `superseded` or `different IP address`. **Criterion 1** — one strategy-owned `PositionClosed(position_id=NVDA.NASDAQ-SMACrossover-000, ... side=FLAT, avg_px_open=227.21, avg_px_close=227.23, realized_pnl=-1.67 USD, duration_ns=180000000000)` at 18:05:05Z, followed by exactly one `trade.aggregated`, bound with the session's `session_id`. **Criterion 2** — `entry_price=227.21000000` equals the entry fill's `last_px=227.21` (`O-20260921-180205-efe5fb99-000-1`, `commission='1.00 USD'`) and `exit_price=227.23000000` equals the exit fill's `last_px=227.23` (`O-20260921-180505-efe5fb99-000-2`, `commission='1.11 USD'`); `commission=2.11` is their sum exactly (AC #3/#4, live). Both legs filled whole (`cum_qty == order_qty == 22` on each), so no partial fill occurred and AC #1/#2's multi-fill VWAP stays broker-double evidence by design — checked for, not staged, exactly as 8.3 says. **Criterion 3** — `fill_count=2` equals the two distinct `trade_id`s (`00025b45.6ab1df5d.01.01`, `0000dc8f.6bacab3e.01.01`); zero records carry `duplicate=True` anywhere in the transcript. **Criterion 4** — zero `trade.recorder_failed`. ⚠️ **A second `trade.aggregated` fired in the same run, for a position the session never asked for — the recorder handled it correctly, and it is recorded here because the thing underneath it is new.** At 18:02:05, between the strategy's `order.accepted` and its own fill, the IB adapter's position poll saw NVDA go `0 -> 22` and classified it `External position change detected (likely option exercise)`; `ExecEngine` reconciled that report into a synthetic `NVDA.NASDAQ-INTERNAL-DIFF` position with an inferred fill (`Portfolio: NVDA.NASDAQ net_position=22`), the real fill for `venue_order_id=105` then took `net_position=44` — **the adapter double-counted one 22-share order against its own poll** — and the next poll (`44 -> 22`) generated the offsetting inferred fill that closed `INTERNAL-DIFF` at 227.16. That whole round trip opened and closed inside 14 ms (`holding_period_seconds=0`, `realized_pnl=-1.1`) and produced one `trade.aggregated` of its own, immediately followed by `trade.persist_skipped reason=reconciliation_owned` — recorded, never written to `trades`, which is the designed behaviour. Criterion 1 reads on the strategy-owned close and is unaffected. The double-count itself is broker-truth territory (Story 4.3's, the same family as the 2026-09-11 `side=FLAT`-while-short finding) and is **not** actioned here. **Broker state** — the run ended `Portfolio: NVDA.NASDAQ net_position=0` with no NVDA residual at teardown and `session.stopped ... signal=SIGINT trader_started=True`; nothing was left open, so the broken `flatten_position.py --confirm` never came into play. Log: `logs/p11-20260921.log`. |
| — | — | ⛔ *(superseded by the row above)* | Written with Story 3.5, 2026-09-11, before any live run. No Gateway was reachable at drafting time (`nc -z 127.0.0.1 4002/4001/7497` all closed) and it was outside RTH (Friday 20:13 ET, after the 16:00 close) — the standing Epic 2 retro rule (run each story's live procedure before the story closes; 3.2 Task 8.4 / 3.3 Task 6.3 / 3.4 Task 8.4 precedent) means this story goes to `review` with this row unresolved, not `done`. |

## Procedure P12: a session killed mid-run still has every trade that closed before the kill

**Introduced by**: Story 3.6 — Persist Each Trade the Moment It Closes
**Verifies**: NFR8 live (AC #3) — that a `SIGKILL` genuinely loses nothing already committed —
plus AC #1's live wiring (`trades` rows actually land against `session_id`, `live status`'s
`closed trades:` counter finally moves off zero) and AC #6's `trade.persisted` record. AC #2's
field-mapping and AC #4's retry/idempotency are proven exhaustively at the unit/component/
integration tiers (`tests/unit/services/test_trade_record_adapter.py`,
`tests/component/core/test_live_trade_recorder.py`,
`tests/integration/db/test_trade_record.py`); this procedure is live evidence of the kill
guarantee specifically, which only a real process death can demonstrate.

### What it does — and does not — do

The whole point of this story is that a trade write commits **before** the handler returns and
before `trade.persisted` is logged (D-A) — so a `kill -9` timed right after that line proves the
row survived a death the process had no chance to react to. What this run **is** evidence for:
the commit-before-log ordering (AC #1, AC #3), the live `owner_epoch` mechanics (a next
`live start` reclaiming at the incremented epoch), and that `live status` reports a non-zero
`closed trades:` count for the first time this project has ever produced one. What it is **not**
evidence for, stated rather than left implicit: a genuine database outage under trading load (AC
#4's retry-and-recover path is component-tier evidence only — staging a real Postgres failure
live is not attempted); the reclaim refusal at a trade write itself (AC #7/#8b — proven at the
integration tier with two real Postgres connections; running two live processes against one
paper account on purpose is exactly the NFR6 catastrophe this project exists to prevent, and is
never staged live); broker-truth reconciliation (Story 4.3's).

### Preconditions

Story 3.4/3.5's Task 8's, unchanged, plus one new step: inside RTH; **no other IBKR login**
(mobile app and client portal included — error 162); the bare non-compose Gateway
(`READ_ONLY_API: "yes"` on the compose one refuses the first order); Redis **and Postgres** up;
**`alembic upgrade head` run first** — this story's migration (`85c949ac0374`) must be applied
before the session starts, or `owner_epoch`/the trade-key index will not exist; strategy
`sma_crossover` only (`fast_period=2, slow_period=3`). **Use a fresh session name** —
`p7-position-test` and `p7-fill-0901` are both poisoned (see P11's preconditions for why).
**Know before starting**: `flatten_position.py --confirm` is still broken and fails safe (P11) —
a position left open at the end of the run stays open at the broker by design.

### Command

```bash
# Confirm the migration landed before starting.
uv run alembic current   # must read 85c949ac0374 (head)

# Start a fresh session and let it complete at least one round trip.
uv run python -m src.cli.main live start <fresh-session-name> > logs/p12-<date>.log 2>&1 &

# Watch for the first trade.persisted line, then kill within seconds of it —
# resolve the PID by pgrep, never $! (P9's harness note: $! is often a wrapper, not the runner).
tail -f logs/p12-<date>.log | grep -m1 "trade.persisted"
kill -9 "$(pgrep -f 'live start <fresh-session-name>')"

# Read the transcript, then compare against the database.
grep -n "trade.aggregated\|trade.persisted\|trade.recorder_failed\|trade.persist_refused" logs/p12-<date>.log
grep -c -E "162|10182|366" logs/p12-<date>.log   # D6's standing rule — before anything else

uv run python -m src.cli.main live status <fresh-session-name>

psql "$DATABASE_URL" -c \
  "SELECT trade_id, client_order_id, entry_price, exit_price, commission_amount, profit_loss \
   FROM trades WHERE session_id = <pk from live status or the sessions table>"

# Then, on the same session — must reclaim, this time on purpose.
uv run python -m src.cli.main live start <fresh-session-name> > logs/p12-reclaim-<date>.log 2>&1 &
grep -n "owner_epoch\|session.reclaimed" logs/p12-reclaim-<date>.log
```

### Expected output

```
trade.aggregated position_id=... trade_key=... entry_price=... exit_price=...
trade.persisted trade_key=... inserted=True attempt=first session_id=... entry_price=... ...
```
then, after the kill, `live status` reports `closed trades: N` with `N >= 1`, and `psql` returns
exactly the `trade_key`s (`trade_id`, `client_order_id` pair) the transcript's `trade.persisted`
lines named, with `entry_price`/`exit_price`/`commission_amount`/`profit_loss` equal to the
transcript's.

### Pass criteria

1. **Each `PositionClosed` is followed by exactly one `trade.aggregated` and one
   `trade.persisted`** with the same `trade_key`, bound with `session_id` (AC #1, AC #6).
2. **After the kill, `psql` returns exactly the `trade_key`s the transcript's `trade.persisted`
   lines named**, with column values equal to the transcript's (AC #3 — the comparison this
   procedure exists to make).
3. **`live status <name>` reports `closed trades: N`, `N >= 1`** — the first non-zero reading
   this counter has ever produced in a live session.
4. **Zero `trade.recorder_failed`, zero `trade.persist_refused`** anywhere in the transcript.
5. **The next `live start` on the same session reclaims after the 90s staleness window**, and the
   reclaimed row's `owner_epoch` is exactly one higher than the killed run's own claim (P9's
   criterion 4, inverted — this time the row *must* change, not stay put).
6. **`grep -c` for `162`, `10182`, `366` inspected before anything else is recorded** (D6's
   standing rule).
7. **Record the Task 1.3 number observed live**: the wall-clock gap between the `PositionClosed`
   Nautilus line and the corresponding `trade.persisted` line, for comparison against the
   sub-millisecond local measurement (D-A's cost claim).

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-21 | Allay (Claude Code session) | ✅ **pass — all seven criteria met live; NFR8 holds against a real `kill -9` 189 ms after the commit** | Run inside RTH (Monday, 14:06–14:14 ET), same Gateway/Redis/Postgres preconditions as P11's row above, on the same preflight. **Migration first, and it mattered**: the database was at `b7c419e2a3d8`, and `live list` was failing outright (`ProgrammingError`, the missing `owner_epoch` column) until `alembic upgrade head` applied `85c949ac0374` — so the precondition is load-bearing, not ceremonial. Fresh session `p12-0921` (`session_id=064feace-6262-4716-84f6-87be0c41b88d`, `trader_id=PAPER-064feace`), same recipe as P11. **Criterion 6 (D6 check, read first)** — the kill transcript's `grep -c -E "162|10182|366"` = 2, and **both are timestamp digits**: zero `code: 162`, `code: 10182` or `code: 366` matches anywhere in it. The reclaim transcript has 3 hits, one of which is the benign teardown `query cancelled: 10005 (code: 162, req_id=10005)`. Zero `superseded` / `different IP address` in either. **Criterion 1** — entry fill 18:10:05.933Z (`O-20260921-181005-064feace-000-1`, `last_px=227.12`), exit fill 18:11:05.809Z (`O-20260921-181105-064feace-000-2`, `last_px=227.14`), then one `trade.aggregated` (18:11:05.810460Z) and one `trade.persisted attempt=first inserted=True` (18:11:05.816399Z), both carrying `trade_key=NVDA.NASDAQ-SMACrossover-000:O-20260921-181105-064feace-000-2` and the bound `session_id`. (A second `trade.aggregated` fired for a reconciliation-owned `INTERNAL-DIFF` position, the same adapter double-count P11's row documents, and was correctly `trade.persist_skipped reason=reconciliation_owned` — not a persisted trade, and not bearing on this criterion.) **The `kill -9` was delivered at 18:11:06.005Z**, 189 ms after the `trade.persisted` line, and the transcript's last line is the strategy's own `<--[EVT] PositionClosed(` print at 18:11:05.816587Z — the process died mid-event-propagation, which is exactly the death this procedure needs. **Criterion 2 (the comparison this procedure exists to make)** — after the kill, `trades` holds exactly one row for the session (`id=14984`) and it matches the transcript field for field: `trade_id=NVDA.NASDAQ-SMACrossover-000`, `client_order_id=O-20260921-181105-064feace-000-2`, `entry_price=227.12000000`, `exit_price=227.14000000`, `commission_amount=2.11000000` `USD`, `profit_loss=-1.67000000`, `quantity=22.00000000`, `holding_period_seconds=59`, `entry_timestamp=2026-09-21 14:10:06-04`, `exit_timestamp=2026-09-21 14:11:05-04`. **NFR8 holds live: a `SIGKILL` the process had no chance to react to lost nothing already committed.** **Criterion 3** — `live status p12-0921` reports `closed trades: 1, open positions: 0`, **the first non-zero reading this counter has ever produced in a live session**. **Criterion 4** — zero `trade.recorder_failed`, zero `trade.persist_refused`. **Criterion 5** — the next `live start p12-0921` (18:13:05Z, after the window) logged `session.reclaimed heartbeat_age_seconds=119.563624` then `session.started`, and the row's `owner_epoch` moved `1 -> 2`, exactly one higher than the killed run's claim. Incidentally re-evidences P10's criteria 1/2: `Cached 4 orders from database` and `Set ClientOrderIdGenerator client_order_id count to 2` both appear before `session.started`. That reclaim run was SIGINT-stopped before it opened any position. **Criterion 7 (the Task 1.3 number, live) — and the procedure's own phrasing is wrong.** The gap it asks for is negative: `trade.persisted` (18:11:05.816399Z) precedes the strategy-side Nautilus `PositionClosed(` print (18:11:05.816587Z) by 0.19 ms, because the recorder's handler runs ahead of the strategy's own logger. The measurable equivalents from the same transcript: closing `order.filled` → `trade.persisted` = **6.44 ms**, of which `trade.aggregated` → `trade.persisted` = **5.94 ms** is the persist step itself. So D-A's sub-millisecond local measurement understates the live cost by roughly an order of magnitude (~6 ms, against a local Postgres) — still four orders of magnitude inside a 1-minute bar, so the decision D-A rests on is unchanged, but the number on record should be ~6 ms, not sub-millisecond. **Broker state** — `net_position=0` at the kill, and the reclaim run's startup reconciliation found no NVDA position to reconcile, so nothing was left open at the broker. Logs: `logs/p12-20260921.log`, `logs/p12-reclaim-20260921.log`. |
| — | — | ⛔ *(superseded by the row above)* | Written with Story 3.6, 2026-09-12, before any live run. No Gateway was reachable at drafting time (4001/4002/7496/7497 all closed, no docker daemon) and it was outside RTH (Saturday) — the standing Epic 2 retro rule (3.2/3.3/3.4/3.5 precedent) means this story goes to `review` with this row unresolved, not `done`. |

---

## Procedure P13: a session rejected on every order stays up and says so

**Introduced by**: Story 3.7 — Keep Trading After a Rejection
**Verifies**: AC #1 live (a real IBKR rejection with a real venue reason, followed by the
session's next crossover submitting a fresh order), AC #2 live (the node does not terminate and
nothing is contained), AC #3 live (the condition is readable from a **second terminal** —
`live status` reads `health: degraded` with the refusal block, `live list` shows `degraded`,
`runtime_flags.order_rejections` on the row matches the transcript), and AC #4 live (zero
`trades` rows for a run whose every order was refused).

### What it does — and does not — do

Half of this story shipped under Story 3.3: `order.rejected` with a verbatim `venue_reason`
already exists, and Nautilus's own `on_order_rejected`/`on_order_denied` are no-ops, so "the
session continues" is already the mechanical truth. This procedure is **not** evidence that the
story created that; it is evidence that the story's *pins* describe reality, and — the actual
deliverable — that a rejection is now visible **from another process**, which nothing before
this story could show.

What this run is **not** evidence for, stated rather than left implicit: a strategy that
overrides `on_order_rejected` and raises (AC #2c — component-tier only; no strategy in this repo
overrides it, and adding one to provoke a live `strategy.failed` would be staging a defect);
`OrderDenied` from the risk engine (see **measured fact** below — the paper account is a MARGIN
account, so the notional checks that would produce one are skipped entirely; the denial path is
component-tier evidence only); the retry/idempotency of the tick write (AR42 — component tier);
the reclaim refusal at a rejection write (integration tier, two real Postgres connections —
running two live processes against one paper account is the NFR6 catastrophe this project exists
to prevent, and is never staged live).

### Preconditions

P12's, unchanged: inside RTH; **no other IBKR login** (mobile app and client portal included —
error 162); the bare non-compose Gateway (`READ_ONLY_API: "yes"` on the compose one refuses the
first order); Redis **and** Postgres up; `uv run alembic current` reads `85c949ac0374` — this
story adds **no** migration, so the head must be unchanged before *and* after; a **fresh session
name**; `flatten_position.py --confirm` is still broken and fails safe (P11), so a position left
open at the end of the run stays open at the broker by design.

Plus, specific to this procedure:

**Measured fact (re-measured 2026-09-21 against the installed 1.220.0 wheel):**
`RiskEngine._check_orders_risk` (`risk/engine.pyx:626`) returns early at
**`risk/engine.pyx:652`** — `if account.is_margin_account: return True  # TODO: Determine risk
controls for margin` — *before* any `NOTIONAL_EXCEEDS_*` branch is reached. The live paper
account is `account_type=MARGIN` (`logs/p12-20260921.log`, 18:06:25Z, `free=32_542.54 USD`,
`BuyingPower: 108475.14`). **Consequence:** an oversized market order of **either** side reaches
IBKR and is rejected *there* (expected code 201, the margin rejection) rather than being denied
locally — which is exactly the venue rejection AC #1's letter ("rejected by IBKR") needs. If a
future wheel removes that early return, a BUY would instead be denied locally
(`NOTIONAL_EXCEEDS_FREE_BALANCE`, since `MarginAccount.balance_impact` is `-notional` for a BUY
and `+notional` for a SELL) and only a SELL would reach IBKR — in that case at least one SELL
crossover is required for criterion 1, and the BUY crossovers will surface as `order.denied`.
**Re-measure the line before running**, and record which you found.

**⚠️ Read `BuyingPower` from the transcript before the first crossover fires.** The recipe below
sizes the order far above it on purpose. If the account's buying power has grown past the sizing,
**the order fills** and the operator owns a real ~$500k paper position with no working tool to
close it (P11's warning, restated). Read the first
`ExecClient-INTERACTIVE_BROKERS: {… 'USD': {…}}` line in the transcript and confirm the sizing is
still far above it before letting a second crossover run.

### Command

```bash
uv run alembic current   # must read 85c949ac0374 (head), before AND after

# ~$500,000 notional (≈ 2,200 NVDA at $227) against BuyingPower ≈ 108,475 measured 2026-09-21.
# fast_period=2/slow_period=3 gives two crossovers within minutes on 1-minute bars.
uv run python -m src.cli.main live create --name p13-<date> \
  --strategy sma_crossover \
  --bar-type NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL \
  --param fast_period=2 --param slow_period=3 \
  --param portfolio_value=1000000 --param position_size_pct=50

uv run python -m src.cli.main live start p13-<date> > logs/p13-<date>.log 2>&1 &

# D6's standing rule — inspected BEFORE anything else is recorded.
grep -c -E "162|10182|366" logs/p13-<date>.log

# Confirm the sizing is still far above buying power, before a second crossover runs.
grep -m1 "BuyingPower" logs/p13-<date>.log

# Wait for TWO crossovers (minutes with this tuning), then from a SECOND TERMINAL:
uv run python -m src.cli.main live status p13-<date>
uv run python -m src.cli.main live list
uv run python -m src.cli.main live status p13-<date> --json | jq keys

# Read the transcript.
grep -n "order.submitted\|order.rejected\|order.denied\|order.accepted" logs/p13-<date>.log
grep -n "rejection_tally_failed\|observer_failed\|strategy.failed\|rejection_record_failed" \
  logs/p13-<date>.log
grep -n "Unhandled order warning or error code" logs/p13-<date>.log   # fact 5, see criterion 1

# Cross-process: the column must match the transcript.
psql "$DATABASE_URL" -c \
  "SELECT runtime_flags->'order_rejections' FROM trading_sessions WHERE name='p13-<date>'"
psql "$DATABASE_URL" -c \
  "SELECT count(*) FROM trades WHERE session_id = (SELECT id FROM trading_sessions \
   WHERE name='p13-<date>')"

# Then SIGINT the run, and confirm the block survives the stop.
kill -INT "$(pgrep -f 'live start p13-<date>')"
uv run python -m src.cli.main live status p13-<date>
```

### Expected output

In the transcript:

```
order.submitted client_order_id=O-<...>-000-1 instrument_id=NVDA.NASDAQ ...
order.rejected  client_order_id=O-<...>-000-1 venue_reason=<IB's own text> due_post_only=False
order.submitted client_order_id=O-<...>-000-2 ...      # the NEXT crossover, after the rejection
order.rejected  client_order_id=O-<...>-000-2 venue_reason=<IB's own text>
```

and, from the second terminal, after the second rejection and at least one heartbeat tick:

```
session: p13-<date> (<uuid>)
  state: running
  health: degraded
  closed trades: 0, open positions: 0
  heartbeat: Ns ago
  orders refused: 2 rejected, 0 denied (2 in a row with no acceptance between)
    first refusal at 2026-..-..T..:..:..+00:00
    most recent at 2026-..-..T..:..:..+00:00 — NVDA.NASDAQ O-<...>-000-2 rejected by the venue
    reason: <IB's own text, with any account-shaped token masked>
  This session asked for orders and did not get them. It is not a quiet market.
```

### Pass criteria

1. **At least one `order.rejected` record with a non-empty `venue_reason` that is not `UNKNOWN`
   and with `reconciliation` absent/false**, preceded by the adapter's own `[ERROR]`/`[WARNING]`
   line naming the code (expected **201**; record whichever appears — the adapter logs the code,
   the event does not carry it) — AC #1's letter.
   *If only `venue_reason="UNKNOWN" reconciliation=True` appears*, **fact 5 fired**: the venue
   answered with a code outside `ORDER_REJECTION_CODES = {201, 203, 321, 10289, 10293}`
   (`adapters/interactive_brokers/client/error.py:40`), so `_handle_order_error` logged
   `Unhandled order warning or error code` and emitted **no event** (`:231-236`), and Story 3.4's
   in-flight sweep resolved the order minutes later as `UNKNOWN`. Record that line and its code,
   note that the tally still counted the refusal, and count this criterion **met with the fact-5
   caveat**.
2. **The next crossover's `order.submitted` appears *after* the first `order.rejected`**, and the
   session is still heartbeating throughout (`live status` from a second terminal reads
   `state: running` with a fresh heartbeat) — AC #1's "remains eligible to trade". The process
   stays alive until the operator's `SIGINT`.
3. **Zero `order.rejection_tally_failed`, zero `order.observer_failed`, zero `strategy.failed`,
   zero `session.rejection_record_failed`** anywhere in the transcript — AC #2.
4. **From a second terminal, after the second rejection and at least one heartbeat tick:**
   `live status p13-<date>` reads `health: degraded` and the refusal block with `rejected: 2`
   (or more), a streak ≥ 2, `first_at`, and the last refusal's instrument, `client_order_id` and
   **redacted** reason; `live list` shows `degraded` in the Health column and **no** reason text;
   `live status p13-<date> --json | jq keys` is exactly the seven AR29 keys with
   `"health": "degraded"` — AC #3.
5. **`psql`'s `runtime_flags->'order_rejections'` matches the transcript's counters**, and after
   a clean `SIGINT` stop the block still renders (flags survive a stop; they are cleared only on
   the next `-> running` edge) — AC #3, cross-process.
6. **`SELECT count(*) FROM trades WHERE session_id = <pk>` is `0`**, and `live status` reads
   `closed trades: 0, open positions: 0` — AC #4. A rejection is not a trade.
7. **`grep -c -E "162|10182|366"` inspected before anything else is recorded** (D6's standing
   rule), and `net_position=0` at the end with nothing open at the broker. A rejected order opens
   nothing — but confirm it, because a **fill** here is the one bad outcome this recipe can
   produce.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-22 | Allay (Claude Code session) | ✅ **pass — all seven criteria met live** | Run inside RTH (Tuesday, 09:37–09:44 ET / 13:37–13:44Z). Preflight: Gateway paper port 4002 open only (4001/7496/7497 closed); Redis and Postgres up; `alembic current` = `85c949ac0374` before **and** after (no migration); no other live session running; `risk/engine.pyx:652` re-measured unchanged (`if account.is_margin_account: return True`). Fresh session `p13-0922` (`session_id=44c0b4df-da9d-465c-8312-2802daae57aa`, `trader_id=PAPER-44c0b4df`), same recipe as drafted. `BuyingPower` read from the first `ExecClient-INTERACTIVE_BROKERS` line = **108,490.67 USD** (P12-era measurement was 108,475.14) — comfortably below the ~$500k order size, so nothing filled. **Criterion 1** — first `order.rejected` at 13:41:05.567Z, non-empty `venue_reason`, not `UNKNOWN`, `reconciliation` absent from the event and confirmed `false` in the DB row; the adapter's own line names the code: `[ERROR] ... Order rejected - reason:...margin requirements... (code: 201, req_id=109)` — the predicted 201, no fact-5 caveat (`Unhandled order warning or error code` never appears). **Criterion 2** — the next crossover's `order.submitted` (`...-000-2`) fired at 13:42:05.525Z, after the first rejection; the session kept heartbeating and kept submitting through four crossovers (all rejected, none denied) until the operator's SIGINT — it never stopped itself. **Criterion 3** — zero `order.rejection_tally_failed`, zero `order.observer_failed`, zero `strategy.failed`, zero `session.rejection_record_failed` anywhere in the transcript. **Criterion 4** — from a second terminal after the second rejection, `live status p13-0922` read `health: degraded` with the refusal block (`2 rejected` at that point, `4 rejected` by the time of the later checks — the criterion allows "or more"), `first_at`/most-recent timestamp, instrument and `client_order_id` populated; `live list` showed `degraded` in the Health column with no reason text; `live status --json \| jq keys` is exactly the seven AR29 keys with `"health": "degraded"` (operational note: the CLI's `logging_configured` line also goes to stdout ahead of the JSON, so `grep '^{'` is needed before piping to `jq` non-interactively). **Criterion 5** — `psql`'s `runtime_flags->'order_rejections'` matched the transcript exactly (`rejected: 4, consecutive: 4, reconciliation: false`, last `client_order_id=...-000-4`) both before and after a clean `SIGINT`; the block survived the stop (`live status` post-stop read `state: stopped, health: stopped` with the same 4-rejection block still rendered). **Criterion 6** — `SELECT count(*) FROM trades ...` = 0 before and after the stop; `live status` read `closed trades: 0, open positions: 0` throughout. **Criterion 7** — `grep -c -E "162\|10182\|366"` inspected first, D6 rule: 1 hit before trading began (`...T13:37:17.408162000Z`, a timestamp digit, not a code), 3 total by the end, the extra two from the controlled-teardown line `Historical Market Data Service error message:API historical data query cancelled: 10005 (code: 162, req_id=10005)` plus its `Unhandled error: 162` echo — the same benign shutdown pattern P12's row documents (message text is a cancelled historical-data query on unsubscribe, not a duplicate-session signal); zero `superseded`/`different IP address` anywhere. `net_position=0` at the end, nothing left open at the broker. Story 3.7 can move from `review` to `done`. Log: `logs/p13-0922.log`. |
| — | — | ⛔ *(superseded by the row above)* | Written and first attempted 2026-09-21. **Gateway check, recorded rather than assumed**: at 15:56 ET (Monday, still inside RTH) ports 4001, 4002, 7496 and 7497 were all closed — no Gateway or TWS running — with ~4 minutes of RTH left, so the two crossovers this recipe needs could not have completed even had one been started. The story therefore went to `review`, not `done` — the standing Epic 2 retro rule (3.2–3.6 precedent). |

## Procedure P14: a restarted session is warm before its first live bar

**Introduced by**: Story 4.4 — Warm Indicators from History Before the Live Stream Starts
**Verifies**: AC #4 live (every registered indicator initialised at the first live bar, and
`warmup.completed` logged before any bar reaches the strategy), AC #5 live (the history request's
duration string and the absence of an IB pacing error), AC #6 live (the 09:25 ET variant is
subscribed and trading by 09:30), and AC #7's absence case (zero `warmup.failed` on a healthy run).

> **Numbering note.** Epic 4's stories are implemented in parallel worktrees. If another Epic 4
> story also appended a "Procedure P14", the integrator renumbers one of them; the content of this
> section does not depend on its number.

### What it does — and does not — do

A normal `live start` of `sma_crossover`. On start, the strategy registers its two SMAs, asks IBKR
for history (`request_bars`), and subscribes to live bars only from that request's callback. The
runner's warm-up watch logs `warmup.completed` inside the callback, *before* the strategy's own
code subscribes, so the record necessarily precedes every live bar — this run is evidence that the
shape holds against the real adapter, not that the ordering is timing-dependent.

What this run is **not** evidence for, stated rather than left implicit: backtest parity (AC #3 —
integration tier, `tests/integration/core/test_warmup_backtest_parity.py`, fill lists byte-identical
to the pre-change code); the never-answering history request (AC #7's contained path — component
tier; staging a real IB failure means provoking a pacing violation or a competing login, which is
the NFR6/D6 hazard this file exists to avoid); `momentum` (the paper account has no business
running its default `trade_size` of 1,000,000 shares — see `deferred-work.md`, story-4.4).

### Preconditions

- Inside RTH for the main run (live bars arrive only in RTH, `ibkr_use_rth=True`); the NFR3
  variant starts at **09:25 ET**.
- The bare non-compose paper Gateway, **no other IBKR login** (error 162); Redis and Postgres up;
  `uv run alembic current` unchanged (this story adds no migration).
- A **fresh session name**. Paper orders may result from a crossover — the P10–P13 precedent;
  `flatten_position.py --confirm` works again as of the Epic 4 pre-work if a position is left open.
- Run the `live_bars_probe.py` preflight first (P11/P12's standing rule): 3 bars, zero
  competing-login text.

### Command

```bash
uv run python -m src.cli.main live create --name p14-<date> \
  --strategy sma_crossover \
  --bar-type NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL \
  --param fast_period=10 --param slow_period=20

uv run python -m src.cli.main live start p14-<date> > logs/p14-<date>.log 2>&1 &

# D6's standing rule — inspected BEFORE anything else is recorded. An IB historical pacing
# violation also surfaces as code 162 ("Historical Market Data Service error message").
grep -c -E "162|10182|366" logs/p14-<date>.log

# The history request as the data client logs it at INFO: `Request <bar_type> bars <start> to
# <end>` (`live/data_client.py:858-864`). The adapter derives IB's duration string from that range
# (`timedelta_to_duration_str`), which it logs only at DEBUG.
grep -n "Request NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL bars" logs/p14-<date>.log

# The milestone, its fields, and its position relative to the first bar and session.started.
grep -n "warmup\.\|session.started\|session.phase" logs/p14-<date>.log
grep -n -m1 "Bar(NVDA\|bar.received\|last_bar_at" logs/p14-<date>.log

# Then SIGINT the run.
kill -INT "$(pgrep -f 'live start p14-<date>')"
```

For the NFR3 variant, start the same command (fresh name, `p14-open-<date>`) at 09:25 ET and read
the `session.started` timestamp and the first `order.*`/bar record against 09:30:00 ET.

### Pass criteria

1. **D6's grep inspected first**; every hit explained (timestamp digits, or the benign teardown
   `query cancelled (code: 162)` P12/P13 document). **No** pacing-violation text.
2. **The history request spans whole days** — the `Request … bars` range is at least one day
   (five, for this recipe), so the adapter sends `N D`/`N W`, never `N S` — AC #5, D-H.
3. **Exactly one `warmup.completed`** for `sma_crossover`, at `info` level, with
   `indicators_initialized=true`, `not_initialized=[]`, a plausible `elapsed_ms` (record it), and
   `requested_from` about five calendar days before the start — AC #4.
4. **Order of records:** `phase=warmup status=ok` → `warmup.completed` → `session.started` → the
   first live bar — AC #4's "logged before the first bar was processed", and D-B's
   "`session.started` only after warm-up".
5. **Zero `warmup.failed`, zero `warmup.discarded`, zero `strategy.start_failed`** — AC #7's absence
   case.
6. *(NFR3 variant only)* `session.started` before **09:30:00 ET**, and the strategy's first bar is
   the 09:30 bar — AC #6.
7. **Informational, not pass/fail — the history/live seam** (code review 2026-09-22, deferred to
   Story 4.5). Record the last history bar's close (`requested_from` + the window, or the
   `Received <Bar[N]>` line) and the strategy's first live bar's `ts_event`. Exactly one bar step
   apart is the clean case; two steps is a **gap** (a bar closed while the request was in flight
   and was published before the strategy subscribed); zero is a **duplicate** (a bar already in
   the history was published after it subscribed, and was fed to the SMAs twice). Either reading
   is expected occasionally — record which one this run shows.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-22 | Story 4.4 dev session | ⏳ **defined, not run** | Gateway check recorded rather than assumed: at 16:39 ET (Tuesday, after the RTH close) ports 4001, 4002, 7496 and 7497 were all closed — no Gateway or TWS running; Redis (6379) and Postgres (5432) were up. No live bar can arrive outside RTH in any case. Informational evidence only, never a gate for this story; the broker-double proofs are `test_strategy_warmup_engine.py` (real `DataEngine`, AC #4), `test_session_runner_warmup.py` (runner, AC #7) and `test_warmup_backtest_parity.py` (AC #3). |
| 2026-09-28 | Epic 4 retro live E2E (14:17–14:41 ET, Mon) | ✅ **passed** | Run from the `p3-epic4-base` worktree (f892d56), against a real paper Gateway. 5-day warm-up before the first live bar, seam clean (no `warmup.seam_gap`/`warmup.seam_duplicate_dropped`). D6's grep run first (162/10182/366, with the timestamp-digit false-positive fixed by requiring a `code[: =]+`/`error[ =:]+` prefix): only the benign teardown `query cancelled (code: 162)` hit. Logs: `logs/*-0928.log` in that worktree. |

## Procedure P15: read the broker's positions and cash, and match them against TWS

**Introduced by**: Story 4.1 — Read the Broker's Authoritative View of Positions and Cash
**Verifies**:
- AC #1 live: positions (instrument, quantity, average price) and `TotalCashValue` are read for
  the configured account, and they match what TWS shows.
- AC #3 live: on a flat account, the read is a success reading `positions=0 flat=True`, not a
  failure.
- AC #5 live: the read completes in well under 30 s against the real Gateway.
- NFR26: the account is masked in every line this code prints.

**Tool**: `scripts/diagnostics/live_node_probe.py --verify-account --read-broker-state`

> **Numbering note.** Epic 4's stories are implemented in parallel worktrees. If another Epic 4
> story also appended a "Procedure P15", the integrator renumbers one of them. The content of this
> section does not depend on its number.

### What it does, and what it does not do

This procedure is strictly a read. The probe:
- builds a node, with Layer 1 before any socket and Layer 2 (`gate:account`) after connecting;
- adds **no strategy**, so nothing on this node can submit an order;
- uses an in-memory cache, so it writes nothing to Redis.

Once connected it calls `read_broker_state(node)`. That sends a fresh `reqPositions` through the IB
adapter and reads cash from the account-summary push the exec client received at connect. The
positions answer is classified from the adapter's own request future, because the adapter's return
value conflates a flat account with a failed read (Story 4.1, F1). Cash comes from IBKR's
`TotalCashValue`, never from Nautilus's `AccountBalance`, which is net liquidation or an invented
`400000` (F5).

Positions and cash can be read outside RTH. Only a running, logged-in Gateway is needed.

This run is **not** evidence for the failure paths:
- a timed-out read;
- a dropped socket;
- an unresolvable contract;
- a cash tag that never arrives.

Staging any of them against a real Gateway means disturbing the connection, which this file
avoids. Those paths are proven against the real adapter code at component tier:
`tests/component/core/test_live_broker_state_adapter.py`.

### Preconditions

- The bare, non-compose paper Gateway, logged in, and **no other IBKR login** (error 162).
- **No live session running.** The probe connects on `ibkr_live_client_id`. The Gateway would
  refuse the probe's connection (client id in use); it would not evict the session. Stop the
  session first anyway so that the reading is not taken mid-trade.
- Run from a checkout whose `.env` carries `TWS_ACCOUNT`, `IBKR_PORT` and `IBKR_TRADING_MODE=paper`.
  Without them the probe stops at `config_error` before any socket opens.
- TWS (or the Gateway's account window) open beside it, showing the same account's Portfolio and
  Account panes.

### Command

```bash
PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py \
  --run-seconds 1 --verify-account --read-broker-state | tee logs/p15-<date>.log
```

### Expected output (shape)

```
[probe] connected (data + exec)
[probe] verifying connected account (phase=gate:account)...
[probe] gate:account ok mode=paper accounts=***NNN
[probe] reading broker state (positions + cash)...
… [info] reconcile.broker_state_retrieved account=***NNN cash={'USD': '<C>'} elapsed_ms=<ms> position_count=<N> positions={…}
[probe] broker state account=***NNN positions=<N> flat=<True|False>
[probe] position <INSTRUMENT> qty=<+/-Q> avg_price=<P>        # one line per open position
[probe] cash USD total_cash=<C>
[probe] broker state read in <T> ms
RESULT: ok mode=build-connect-run-stop loop_closed=True gate_account=paper broker_state=ok positions=<N> elapsed=<E>
```

A failed read prints `[probe] broker state failed: <detail>` and then
`RESULT: fail reason=broker_state_unavailable failure=<reason>`, and exits `1`. It never prints a
flat reading. For adapter drift (`failure=adapter_incompatible`) the detail names the exception
type and where it was raised.

### Pass criteria

1. `RESULT: ok … broker_state=ok positions=<N>`, exit `0`, with the `gate:account ok` line before
   the broker-state lines (AR39's order).
2. **Positions match TWS.** Every `position` line matches TWS's Portfolio window, instrument for
   instrument and share for share, with the sign giving long or short. No TWS position is missing.
   A position printed as `IB-CONID-<n> (unresolved …)` still counts as present. Record the list.
   On a flat account: `positions=0 flat=True`, and TWS shows no position.
3. **Cash matches TWS.** The `cash USD total_cash=` line equals TWS's *Total Cash Value* for the
   base currency. Record both values.
4. **Timed (AC #5).** The reader's own `elapsed_ms` on the `reconcile.broker_state_retrieved`
   record is under 30000. That is the number AC #5 is judged on, measured on the loop's clock over
   the whole read. Record it together with the probe's wall-clock `broker state read in <T> ms`,
   which should agree to within a few ms. A healthy local Gateway should read in well under a
   second.
5. **Masked (NFR26).** `grep '^\[probe\]' logs/p15-<date>.log | grep -c '<full account id>'`
   prints `0`. The unscoped grep will not be `0`, because the adapter prints the raw account itself
   (P2's criterion 3 explains why).
6. **Nothing traded.** `grep -c "order.submitted\|SubmitOrder" logs/p15-<date>.log` prints `0`.
7. **Informational, not pass/fail: IBKR's average price.** For any open position, record the
   printed `avg_price` beside TWS's *Avg Price* and the fill price in the session transcript that
   opened it. IBKR documents a stock's `avgCost` as commission-inclusive, and Nautilus's
   `avg_px_open` is not. This reading tells Story 4.6 how far apart they are (D-G).

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-22 | Story 4.1 dev session | ⏳ **defined, not run** | Attempted at 21:15 ET (Tuesday). This procedure does not need RTH, but the story's harness worktree has no `.env`: it is gitignored, and the charter forbids creating or reading one. The attempt, `live_node_probe.py --run-seconds 1 --verify-account --read-broker-state`, therefore stopped exactly where it should, before any socket was opened: `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set …`. That is evidence the tooling runs and fails closed, not that P15 passes. Whether a Gateway was listening could not be checked: the session's sandbox refused every port probe (`nc`, `lsof`). Informational only, never a gate for Story 4.1. The broker-double proofs are `test_live_broker_state_adapter.py`, which uses the real IB client, exec client, instrument provider and `ExecutionEngine` with only the socket stood in. It covers AC #1, #3 and #5 and the D-C no-cancel property. `test_live_broker_state.py` covers every AC #4 reason. Run P15 from a checkout that has `.env` the next time a Gateway is up. |
| 2026-09-28 | Epic 4 retro live E2E (14:17–14:41 ET, Mon) | ✅ **passed** | Run from the `p3-epic4-base` worktree (f892d56), which does carry `.env`. Broker read completed in 71 ms. Positions and cash matched TWS; nothing traded. Alembic stayed at `85c949ac0374`. Logs: `logs/p15-0928.log`. |

## Procedure P16: check a session's positions and cash against IBKR on demand

**Introduced by**: Story 4.6 — Check Positions and Cash Against IBKR on Demand
**Verifies**:
- AC #1 live: `ntrader live reconcile <session>` connects, reads IBKR's positions and cash, reads
  the session's own view, prints an explicit result and disconnects.
- AC #2 live (Variant B): it runs on `ibkr_live_client_id + 1` beside a running session without
  evicting it.
- AC #3/#5 live: every line names an instrument (or currency) with the session's value, IBKR's value
  and the difference; a clean account exits `0`.
- AC #6 live: the whole command completes in under 30 s.
- NFR26: the account is masked in every line the command prints.

**Tool**: `uv run python -m src.cli.main live reconcile <name-or-id>`

> **Numbering note.** Epic 4's stories are implemented in parallel worktrees. If another Epic 4
> story also appended a "Procedure P16", the integrator renumbers one of them. The content of this
> section does not depend on its number.

### What it does, and what it does not do

It is strictly a read, on both sides:

- **Broker side.** It builds its own node on `IBKR_LIVE_CLIENT_ID + 1`. The node carries no
  strategy, controller or bar observer, so nothing on it can submit an order, and it uses an
  in-memory cache, so it writes no Redis. Layer 1 of the gate runs before any socket and Layer 2
  (`gate:account`) after connecting. Once connected it calls Story 4.1's `read_broker_state`.
- **Session side.** It reads the session's durable engine cache in Redis
  (`trader-PAPER-<8hex>:`) through Nautilus's own adapter, with load methods only. That covers the
  net of its open positions per instrument, and the last `TotalCashValue` its engine received.
  This read is proven write-free by `MONITOR` at integration tier
  (`tests/integration/core/test_live_session_view_redis.py`).

It then compares both sides exactly and prints every line. It changes **nothing** anywhere: no
transition, no record, no Redis key, no order.

A session whose engine cache is empty has no view to compare. That covers a session that never ran,
or one whose Redis was flushed. It exits `1` with `no_engine_state`, never "flat". The check
happens right after Layer 1 and before any IBKR socket (the order is gate → session-state precheck
→ node), so it costs no Gateway connection.

This run is **not** evidence for the failure paths:
- a never-answering broker;
- an unreadable cache;
- the budget clamp.

Those are proven at component tier (`tests/component/core/test_live_reconcile.py`,
`test_live_session_view.py`).

### Preconditions

- The bare, non-compose paper Gateway, logged in, and **no other IBKR login** (error 162). RTH is
  not needed.
- A session that **has run at least once against this Redis**. `live list` names it; an empty
  namespace reads `no_engine_state`.
- A checkout whose `.env` carries `TWS_ACCOUNT`, `IBKR_PORT` and `IBKR_TRADING_MODE=paper`. Without
  `TWS_ACCOUNT` the command stops at `config_error` before any socket opens.
- TWS (or the Gateway's account window) open beside it, showing the account's Portfolio and
  Account panes.
- **Variant A:** the session is stopped. **Variant B (operator only):** the session is running.
  The session itself may trade, per the P10–P13 precedent; the reconcile never does.

### Command

```bash
time uv run python -m src.cli.main live reconcile <session> 2>&1 | tee logs/p16-<date>.log
echo "exit=${PIPESTATUS[0]}"

# Variant B only, run against the *session's* log, around the reconcile's timestamp: the session
# must show no eviction and no disconnect.
grep -n -E "326|1100|connection\.lost" logs/<session-log>
```

### Expected output (shape)

```
reconcile: session <name> (status=<status>) against IBKR on client_id=<live+1>
… [info] gate.static … status=ok
… [info] live_check.building … client_id=<live+1> trader_id=PAPER-RECONCILE
… [info] reconcile.broker_state_retrieved account=***NNN … elapsed_ms=<ms>
… [info] reconcile.session_view_read trader_id=PAPER-<8hex> … elapsed_ms=<ms>
reconcile session=<name> trader_id=PAPER-<8hex> account=***NNN broker_read_at=<iso>
position <INSTRUMENT> session=<+/-Q|0> broker=<+/-Q|0> avg_price=<P> match|difference=<D> DISCREPANCY
cash USD session=<C|unknown> broker=<C> match|difference=<D> DISCREPANCY
RESULT: clean — positions and cash match IBKR exactly (positions=<N> currencies=1) elapsed_ms=<ms>
   or
RESULT: discrepancy — positions=<n> cash=<n> line(s) differ from IBKR (…); nothing was changed elapsed_ms=<ms>
```

### Pass criteria

1. **An explicit result and the right exit code.** `RESULT: clean` with exit `0`, or
   `RESULT: discrepancy` with exit `5`. The `gate.static` line comes before the build line, and the
   build line before the broker-state line (AR39's order).
2. **Positions match TWS.** Every `position` line's `broker=` value matches TWS's Portfolio,
   instrument for instrument and share for share, with the sign giving long or short. Every
   `session=` value is what the session last believed. Record each discrepancy line and say whether
   it is expected, for example an `EXTERNAL` position opened since the session last ran.
3. **Cash explained.** The `cash USD broker=` value equals TWS's *Total Cash Value*. For a stopped
   session, a cash difference is expected if the account moved since it last ran; record the
   reason.
4. **Timed (AC #6).** The `time` output's *real* value is under 30 s, and no
   `reconcile.budget_exceeded` record was logged. Also record the report's
   `elapsed_ms` (start to verdict) and the reader's own `elapsed_ms` on
   `reconcile.broker_state_retrieved`.
5. **Masked (NFR26).** `grep -c '<full account id>'` over the lines the command itself prints
   (`reconcile:`, `reconcile session=`, `position`, `cash`, `RESULT:`, `live reconcile failed:`)
   is `0`. The adapter's own log lines print the raw account (P2's criterion 3 explains why), so the
   unscoped grep will not be `0`.
6. **Nothing traded.** `grep -c "order.submitted\|SubmitOrder" logs/p16-<date>.log` prints `0`.
7. **The reservation.** The build line reads `client_id=<IBKR_LIVE_CLIENT_ID + 1>` and
   `trader_id=PAPER-RECONCILE`. *(Variant B)* The session's log shows no `326`, no `1100` and no
   `connection.lost` at the reconcile's timestamp, and `live status <session>` still reads
   `trading` or `idle`.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-27 | Story 4.6 dev session | ⏳ **defined, not run** | No Gateway: at 11:22 ET (Sunday) a TCP connect to ports 4001, 4002, 7496 and 7497 was refused on all four. Two attempts ran the real CLI, after the code review's gate-first reorder:
(1) `live reconcile p13-0922` printed `reconcile: session p13-0922 (status=stopped) against IBKR on client_id=11` (the `+ 1` reservation), then `gate.static … status=ok`, then `reconcile.session_view_failed reason=no_engine_state` in 5.5 ms, and `live reconcile failed: … (no_engine_state) …`, exit `1`, before any IBKR socket. The session row was read from PostgreSQL and Redis was PINGed; no Gateway connection was attempted.
(2) `live reconcile p7-fill-0901` passed the same header, `gate.static` and the session-state precheck, then stopped at the builder, before any IBKR socket: `live reconcile failed: Cannot build an IBKR execution client: TWS_ACCOUNT is not set …`, exit `1`.
The worktree has no `.env`, and the charter forbids creating or reading one. **Session side, read for real (load-only, no broker needed):** `p7-fill-0901`'s engine cache (18 keys) holds **AAPL.NASDAQ +4 and NVDA.NASDAQ +22**. That is the namespace the Epic 3 retro cites for a phantom position the broker did not hold, so a Variant A run against it should name any line IBKR no longer carries. Its `accountSummary` key exists, but it was read here with a placeholder account, so cash read `unknown`. The namespaces of `p12-0921`, `p13-0922` and `rth-day-1` are **empty** on this Redis, and the reader reported `no_engine_state` for each rather than "flat". Pick a session that has run against this Redis for Variant A. Informational only, never a gate for Story 4.6. The broker-double proofs are `test_live_reconcile.py` (driver, including the `+ 1` pin on the real builder), `test_live_session_view.py`, `test_live_session_view_redis.py` (real Redis and `MONITOR`), `test_reconciliation_service.py` and `test_live_reconcile_cli.py`. |
| 2026-09-28 | Epic 4 retro live E2E (14:17–14:41 ET, Mon) | ✅ **passed (A and B)** | Run from the `p3-epic4-base` worktree (f892d56). Variant A: `live reconcile` on a genuinely stale session exited `5` (`EXIT_DISCREPANCY`) in ~11 s. Variant B: run on `ibkr_live_client_id + 1` beside a running session, clean, no disturbance to the running session. Logs: `logs/p16-0928.log`. |

## Procedure P17: startup reconciliation against the real broker

> Written as P16 by Story 4.2 in parallel with Story 4.6's P16; renumbered to P17 at integration.

**Introduced by**: Story 4.2 — Reconcile Against the Broker Before Any Strategy Trades
**Verifies**:
- AC #2 / #5 live (P17a): after Nautilus's own startup pass, the `reconcile` phase's body proves
  the cache matches IBKR exactly (`reconcile.ok`, 0 discrepancy), naming whatever the framework
  imported (`reconcile.discrepancy resolution="framework"`), inside NFR5's 30 s.
- AC #3 / D-D live (P17b): a session whose Redis cache holds a strategy position the broker does
  not hold — the `p7-fill-0901` shape — **refuses to start**, naming the instrument and both
  quantities, and submits nothing.

**Tools**: `scripts/diagnostics/live_node_probe.py --verify-account --reconcile` (P17a);
`ntrader live start <session>` (P17b).


### What it does, and what it does not do

**P17a is strictly read-only against the broker.** The probe builds a node (Layer 1 before any
socket, Layer 2 after connecting), adds **no strategy**, and uses an **in-memory** cache — so the
snapshot taken before the node runs is empty, Nautilus's own pass imports every broker position —
as `INTERNAL-DIFF` since Story 4.5 (the adapter's fabricated `EXTERNAL` order is filtered, D-A;
before it, as `EXTERNAL`) — inside `run_async`, and `reconcile_at_startup` then compares the cache against a fresh
`read_broker_state`. Any broker-ward correction the phase makes lands in that throwaway cache,
never in a session's Redis namespace. Nothing can submit an order. It does not need RTH.

**P17b is not read-only**: `live start` starts strategies if reconciliation passes. It is written
for the operator and is **never** run by an automated story session.

### Preconditions

- A running, logged-in paper Gateway on the configured paper port; no session running on
  `ibkr_live_client_id` (the probe shares it — P15's precondition).
- P17a: nothing else. It works on a flat account (`positions=0`) and on one holding positions.
- P17b: a session whose Redis namespace holds a strategy-owned position the broker does **not**
  hold. `p7-fill-0901` was such a session on 2026-09-11 (P10's result row: its cache logged
  `NVDA.NASDAQ net_position=22` on a flat broker). Confirm the broker side read-only first
  (P17a, or TWS). **Do not run P17b inside RTH on a session whose cache might agree with the
  broker** — if reconciliation passes, its strategies start and may trade.

### Command

```bash
# P17a — read-only.
uv run python scripts/diagnostics/live_node_probe.py --run-seconds 1 --verify-account \
  --reconcile > logs/p17a-<date>.log 2>&1
grep -E "^\[probe\]|RESULT|reconcile\.(ok|discrepancy)" logs/p17a-<date>.log

# P17b — operator only; starts strategies if reconciliation passes.
uv run python -m src.cli.main live start p7-fill-0901 > logs/p17b-<date>.log 2>&1
echo "exit=$?"
grep -E "session.phase|reconcile\.(ok|discrepancy)|live start failed" logs/p17b-<date>.log
grep -c -E "162|10182|366" logs/p17b-<date>.log   # D6's standing rule, checked first
```

### Expected output

```
# P17a
[probe] gate:account ok mode=paper accounts=['***626']
[probe] waiting for Nautilus's own reconciliation (trader started)...
[probe] reconciling against the broker (phase=reconcile)...
[probe] reconciled AAPL.NASDAQ qty=+4          # one line per broker position, if any
[probe] reconcile ok positions=1 discrepancies=1 open_orders=0 synthetic_positions=1 elapsed_ms=...
RESULT: ok mode=build-connect-run-stop loop_closed=True gate_account=paper reconcile=ok positions=1 discrepancies=1 ...

# P17b
session.phase phase=reconcile status=started
reconcile.discrepancy instrument_id=NVDA.NASDAQ kind=strategy_position resolution=refused
  local_quantity=22 strategy_quantity=22 broker_quantity=0 reason=strategy_position_contradicted
session.phase phase=reconcile status=failed error_type=ReconciliationFailedError
live start failed: Startup reconciliation refused to let this session trade (strategy_position_contradicted): ...
exit=1
```

### Pass criteria

1. **P17a:** `RESULT: ok … reconcile=ok positions=<N> discrepancies=<M>`, exit `0`, with
   `gate:account ok` before the reconcile lines (AR39's order). On a flat account `positions=0`.
2. **P17a — the framework's imports are named, not silent.** Every broker position appears as one
   `reconcile.discrepancy` record — `resolution="framework"` for each the framework imported (the
   in-memory cache knew nothing), `resolution="broker"` for any the phase had to correct itself —
   and `discrepancies` equals the number of those records. `positions` matches TWS instrument for instrument, share for
   share (P15's criterion 2).
3. **P17a — timed (NFR5).** `reconcile.ok`'s `elapsed_ms` is under 30000. Record it.
4. **P17a — masked (NFR26).** `grep '^\[probe\]' logs/p17a-<date>.log | grep -c '<full account
   id>'` prints `0`.
5. **Both — nothing traded.** `grep -c "order.submitted\|SubmitOrder"` prints `0` in each log.
6. **P17b — refused, not resolved.** `phase=reconcile status=failed`, no `warmup`/`subscribe`/
   `trading` phase record after it, one `resolution=refused` record naming the instrument with
   `strategy_quantity` ≠ `broker_quantity`, the operator message names the instrument and both
   quantities, and exit is `1`. `live list` shows the session `stopped`.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-27 | Story 4.2 dev session | ⏳ **P17a defined, not run; P17b defined, not run (operator only)** | P17a attempted: `live_node_probe.py --run-seconds 1 --verify-account --reconcile` stopped at `RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set…` before any socket opened — the story's harness worktree has no `.env` (gitignored; the charter forbids creating or reading one), the Story 4.1 P15 precedent. Note for the operator: the probe needs the repo root importable (`PYTHONPATH=.` or run it via `python -m`/`runpy`), a pre-existing property of the script. P17b needs a session with a stale strategy position and starts strategies if reconciliation passes, so it is never run by an automated session. Both are informational evidence only (NFR33), never a gate: the same logic is proven against broker doubles and a real `LiveExecutionEngine` in `tests/component/core/test_live_startup_reconcile_engine.py` and `test_session_runner_phases.py`. |
| 2026-09-28 | Epic 4 retro live E2E (14:17–14:41 ET, Mon) | ✅ **P17a passed**; P17b still not run | Run from the `p3-epic4-base` worktree (f892d56). P17a on a flat account: `reconcile.ok`, 0 discrepancies. P17b (needs a stale strategy position and starts strategies if reconciliation passes) remains operator-only and was not run this session. Logs: `logs/p17a-0928.log`. |

## Procedure P18: runtime state stays aligned with the broker

> Written by Story 4.3. If a story running in parallel also appended a "Procedure P18", the
> integrator renumbers one of them; the content of this procedure does not depend on its number.

**Introduced by**: Story 4.3 — Keep Runtime State Aligned with the Broker
**Verifies**:
- AC #1 / D-B live (P18a): a strategy entry produces **one** round trip — the IB adapter's
  "External position change detected" report, and its phantom `INTERNAL-DIFF` round trip
  (P11/P12), are gone.
- AC #2 live (P18b): a position change the session did not make, on an instrument a session
  strategy trades but does not hold, is corrected broker-ward within two runtime cycles and logged
  `reconcile.discrepancy scope=runtime resolution=broker` with the instrument and quantities.
- AC #3 live (P18c): a clean hour logs at most two `reconcile.ok scope=runtime` records.
- AC #4 live (P18d): after a Gateway restart mid-session, no order is sent until
  `reconcile.ok scope=reconnect` and then `connection.restored` appear, in that order.
- D-D live (P18e, PO ruling 2A): closing a strategy's own position in TWS stops the session,
  exit `1`, naming the instrument and both quantities; the positions left at IBKR are untouched.

**Tools**: `ntrader live start <session>`; TWS (P18b, P18e); the Gateway's own restart (P18d).

### What it does, and what it does not do

**None of P18 is read-only.** Every part runs `live start`, whose strategies may trade, and P18b /
P18e change a position at the broker by hand in TWS. It is written for the operator and is
**never** run by an automated story session. It does not need `--real-money` and must never be
run with it.

### Preconditions

- A running, logged-in paper Gateway on the configured paper port, inside RTH (so bars arrive and a
  strategy can enter). No IBKR mobile app or client portal session while it runs (the 162 rule).
- A session whose strategy trades an instrument you are willing to trade by hand in TWS (P18b uses
  a second instrument from the same session spec, or a fresh session on it).
- **Know before starting (Story 4.5, amended by PR #35 D5a):** P18b's hand-bought share is a
  holding no strategy owns. At the session's **next** start, if the strategy trading that
  instrument is flat there it is not started (`strategy.resume_refused reason=unowned_position`,
  PO ruling D-C: B) until you remove the share by hand in TWS; if it holds the same side it
  resumes beside the share (`strategy.resumed_beside_excess`) and never trades it. Sibling
  strategies start either way. That is the intended behaviour, not a P18 failure.
- D6's standing rule first: grep every transcript for `162`, `10182`, `366` before reading it.

### Command

```bash
uv run python -m src.cli.main live start <session> > logs/p18-<date>.log 2>&1 &
# P18a: wait for an entry, then:
grep -c "External position change detected" logs/p18-<date>.log          # expect 0
grep -c "reconcile.position_update_deferred" logs/p18-<date>.log         # expect >= 1 per fill
grep -c "trade.aggregated" logs/p18-<date>.log                           # one per round trip
# P18b: in TWS, buy 1 share of an instrument the session trades but holds nothing in; wait 2-3 min.
grep -E "reconcile\.(discrepancy|ok)" logs/p18-<date>.log | grep "scope=runtime"
# P18c: leave it clean for an hour.
grep -c "reconcile.ok.*scope=runtime" logs/p18-<date>.log
# P18d: restart the Gateway (not the session); wait for it to come back.
grep -nE "connection\.(lost|restored|halted)|scope=reconnect|order\.(submitted|suppressed)" \
  logs/p18-<date>.log
# P18e: in TWS, close the strategy's own position by hand; wait 2-3 min.
wait; echo "exit=$?"
grep -E "resolution=refused|live start failed" logs/p18-<date>.log
```

### Expected output

```
# P18b (about two minutes after the TWS trade)
reconcile.discrepancy scope=runtime instrument_id=MSFT.NASDAQ kind=position resolution=broker
  local_quantity=0 strategy_quantity=0 broker_quantity=1
reconcile.ok scope=runtime cycles=1 positions=... instruments=...

# P18d
connection.lost ...
connection.state_changed previous=connected current=lost
connection.state_changed previous=lost current=recovering
reconcile.ok scope=reconnect cycles=1 ...
connection.restored downtime_seconds=... reconnect_seconds=...

# P18e
reconcile.discrepancy scope=runtime instrument_id=NVDA.NASDAQ kind=strategy_position
  resolution=refused reason=strategy_position_contradicted local_quantity=22 ... broker_quantity=0
live start failed: Runtime reconciliation stopped this session (runtime, strategy_position_contradicted): ...
exit=1
```

### Pass criteria

1. **P18a:** zero `External position change detected` lines; one `trade.aggregated` per real round
   trip (P11/P12 recorded two per entry, one of them `INTERNAL-DIFF`); no `trade.persist_skipped
   reason=reconciliation_owned` for an entry that nobody changed at the broker.
2. **P18b:** within ~3 minutes of the TWS trade, one `reconcile.discrepancy scope=runtime
   resolution=broker` naming the instrument, `local_quantity=0` and the broker's quantity; then a
   `reconcile.ok scope=runtime`. Record the minutes between the trade and the record (the accepted
   1–2 cycle lag).
3. **P18c:** at most two `reconcile.ok scope=runtime` records in the clean hour, and no
   `reconcile.broker_state_retrieved` line from the runtime cycle.
4. **P18d:** between `connection.lost` and `connection.restored`: zero `order.submitted`; any order
   the strategy attempted appears as `order.suppressed`; `reconcile.ok scope=reconnect` precedes
   `connection.restored`. If the Gateway took longer than 60 s, `connection.halted` appears too —
   expected, and it must still be followed by `connection.restored`.
5. **P18e:** exit `1`; the message names the instrument and both quantities and says positions at
   IBKR were not touched; TWS shows no order from the session after the manual close; `live list`
   shows the session `stopped`.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-27 | Story 4.3 dev session | ⏳ **Defined, not run (operator only)** | Every part needs `live start` (strategies may trade), and P18b/P18e change a broker position by hand — the story charter forbids submitting orders or opening, closing or flattening positions, so no part is read-only. The one read-only surface this story touches — the exec-client factory, which now installs the D-B patch, reached by `live check` — was attempted: `ntrader live check` stopped at `Cannot build an IBKR execution client: TWS_ACCOUNT is not set` in 0.00 s, before any socket opened, because the harness worktree has no `.env` (the P15/P17a precedent; the charter forbids creating or reading one). Informational evidence only (NFR33), never a gate: the same logic is proven against broker doubles and a real `LiveExecutionEngine` in `tests/unit/core/test_live_runtime_reconcile.py`, `tests/component/core/test_live_runtime_reconcile_engine.py`, `test_session_runner_runtime_reconcile.py` and `test_live_exec_position_reports.py`. |

## Procedure P19: a session restarted across an open position resumes it

> Written by Story 4.5. Story 4.7, in parallel, also appended a "Procedure P19"; the integration
> merge (2026-09-28) kept this one as P19 and renumbered 4.7's P20.

**Introduced by**: Story 4.5 — Resume a Strategy Mid-Position
**Verifies**:
- D-A live (P19a): Nautilus's own startup pass imports a broker position **once**, as
  `INTERNAL-DIFF`, with no `EXTERNAL` order — the IB adapter's fabricated per-position order is
  filtered (`filter_unclaimed_external_orders=True`).
- AC #1 / #2 live (P19b): a session stopped holding a position resumes it — `strategy.resumed`
  with `quantity == broker_quantity` and `opened_before_this_run=True`, `reconcile.ok` with
  `synthetic_positions=0` — and the strategy enters nothing on a same-side signal.
- AC #3 / #5 live (P19b): the eventual exit is **one** order of the resumed quantity, and the round
  trip is **one** `trade.persisted` whose `ts_opened` is the first run's, in the same `session_id`.
- AC #4 live (P19b): `reconcile.ok` → `strategy.resumed` → `warmup.completed` → the first bar, in
  that order, before any `order.submitted`.
- D-F (informational, P19b): the history/live seam reading — `warmup.seam_duplicate_dropped` /
  `warmup.seam_gap` — replaces P14 criterion 7's hand comparison.

**Tools**: `scripts/diagnostics/live_node_probe.py --verify-account --reconcile` (P19a);
`ntrader live start <session>` and TWS (P19b).

### What it does, and what it does not do

**P19a is strictly read-only against the broker** — P17a's command, read for a different fact: the
probe adds no strategy and uses an in-memory cache, so nothing can submit an order and no session's
Redis namespace is touched. It needs an account that **holds** a position (a flat account shows
nothing to import). It does not need RTH.

**P19b is not read-only**: `live start` starts strategies, which enter and exit at IBKR paper. It
is written for the operator and is **never** run by an automated story session. It does not need
`--real-money` and must never be run with it.

### Preconditions

- A running, logged-in paper Gateway on the configured paper port, inside RTH for P19b. No IBKR
  mobile app or client portal session while it runs (the 162 rule). D6's standing rule first:
  grep every transcript for `162`, `10182`, `366` before reading it.
- **P19b: a fresh session** created after Story 4.5 (`live create --name p19-<date> --strategy
  sma_crossover --bar-type NVDA.NASDAQ-1-MINUTE-LAST-EXTERNAL`). A session restarted mid-position
  *before* Story 4.5 refuses to start (`session.resume_refused reason=legacy_position_import`,
  exit 1, D-D) — that refusal is itself worth recording once, on such a session, but it is not P19b.
- The account holds **nothing** on the session's instrument that the session did not open —
  a same-side excess would make run 2 resume beside it (`strategy.resumed_beside_excess`, PR #35
  D5a) and muddy the "same position" proof, and an opposite-side or flat-beside-unowned holding
  means the strategy is not started (`strategy.resume_refused`, D-C); either way P19b cannot run.
- **Know before starting:** `custom/sma_crossover_long_only` still flattens in `on_stop()`
  (`deferred-work.md:2196`, owner: the submodule repo). Use the built-in `sma_crossover`.
- P19b leaves a position open between its two runs, by design, and possibly at the end if no exit
  signal comes. Close a leftover **by hand in TWS** or with `scripts/flatten_position.py --confirm`
  (whose first live use is still to be recorded — `deferred-work.md:3043-3052`).

### Command

```bash
# P19a — read-only, on an account holding a position.
uv run python scripts/diagnostics/live_node_probe.py --run-seconds 1 --verify-account \
  --reconcile > logs/p19a-<date>.log 2>&1
grep -E "^\[probe\]|RESULT|reconcile\.(ok|discrepancy)|EXTERNAL|INTERNAL-DIFF" logs/p19a-<date>.log

# P19b run 1 — wait for an entry (`order.filled`), then Ctrl-C once.
uv run python -m src.cli.main live start p19-<date> > logs/p19b-1-<date>.log 2>&1
# Record the IBKR position in TWS. It must be unchanged by the stop.

# P19b run 2 — the same session, by name.
uv run python -m src.cli.main live start p19-<date> > logs/p19b-2-<date>.log 2>&1
grep -nE "reconcile\.(ok|discrepancy)|strategy\.resum|warmup\.(completed|seam)|order\.submitted|\
trade\.(aggregated|persisted)|session\.started" logs/p19b-2-<date>.log
# After the exit fills, Ctrl-C once, then:
psql "$DATABASE_URL" -c "SELECT session_id, trade_id, venue_order_id, client_order_id, \
  entry_timestamp, exit_timestamp FROM trades WHERE session_id = \
  (SELECT id FROM trading_sessions WHERE name = 'p19-<date>') ORDER BY exit_timestamp;"
```

### Expected output

```
# P19a
[probe] reconciled NVDA.NASDAQ qty=+22
[probe] reconcile ok positions=1 discrepancies=1 open_orders=0 synthetic_positions=1 elapsed_ms=...
RESULT: ok ... reconcile=ok positions=1 discrepancies=1 ...
# and no "EXTERNAL" line from Nautilus's own reconciliation of the position

# P19b run 2
session.phase phase=reconcile status=ok
reconcile.ok scope=startup positions=1 instruments={'NVDA.NASDAQ': '22'} discrepancies=0 synthetic_positions=0
strategy.resumed strategy_id=SMACrossover-000 instrument_id=NVDA.NASDAQ side=LONG quantity=22
  broker_quantity=22 opened_before_this_run=True open_orders=0 ...
warmup.completed strategy_id=sma_crossover indicators_initialized=True ...
session.started ...
order.submitted ... side=SELL quantity=22          # only on the opposite crossover
trade.aggregated strategy_id=SMACrossover-000 opening_order_id=<run 1's> ...
trade.persisted ...
```

### Pass criteria

1. **P19a:** every broker position appears once, and the framework's import of it is
   `INTERNAL-DIFF` — `grep -c EXTERNAL` on Nautilus's reconciliation lines prints `0`.
   `RESULT: ok`. Nothing traded (`grep -c "order.submitted\|SubmitOrder"` prints `0`).
2. **P19b, the stop:** TWS shows the same position before and after the Ctrl-C; run 1's log has
   no `order.submitted` after `session.stopped`.
3. **P19b, the resume:** run 2's `reconcile.ok` has `discrepancies=0` and `synthetic_positions=0`;
   exactly one `strategy.resumed`, with `quantity == broker_quantity` and
   `opened_before_this_run=True`; its line precedes `warmup.completed`, which precedes the first bar
   and any `order.submitted`.
4. **P19b, no fresh entry:** no `order.submitted` with the entry's side while the position is open.
5. **P19b, one exit:** on the opposite crossover, exactly one `order.submitted` of the resumed
   quantity, and TWS goes flat.
6. **P19b, one trade:** exactly one `trade.aggregated` for the round trip (none for a synthetic
   owner), its `opening_order_id` is run 1's entry order, and the `trades` query shows one row for
   it with run 1's `entry_timestamp`, in the same `session_id` as every earlier row of this
   session.
7. **Informational, not pass/fail — the seam:** record any `warmup.seam_duplicate_dropped` /
   `warmup.seam_gap` line from either run. A gap is named, never replayed (PO ruling D-F: B). The
   duplicate record's `ts_event` equal to `last_history_ts_event` is also the first live evidence
   that IB stamps a republished history bar and its live twin alike (`deferred-work.md`, story-4.5
   review deferral) — record it.

### Variants

- **P19b day two — the signature journey (Journey 2).** Run 1 near the close; stop it (Ctrl-C) with
  the position open; let the Gateway take its **overnight restart** with nothing running; start
  run 2 the next morning. The pass criteria are the same, and in addition run 2's `node:connect`
  reconnects to a Gateway process that never saw run 1. `session.phase phase=reconcile
  status=ok` with `synthetic_positions=0` is the whole point: the broker's answer, not any
  in-memory state, is what the resume rests on.
- **P19b at 09:25 ET (NFR3).** Start run 2 five minutes before the open. Record the time of
  `session.started` and of the first `strategy`-owned bar: the resumed strategy must be
  reconciled, warm and subscribed by 09:30. The lookback is whole days for `>= 1 minute` bars
  (Story 4.4), so a pre-open start never sends a seconds window that returns nothing.
- **P19c, the covered excess (PO ruling D-C, 2026-09-28; amended by PR #35 D5a, 2026-09-30 —
  operator only).** Between runs, buy a few extra shares of the strategy's instrument by hand in
  TWS. Run 2's `reconcile` phase ends `ok` (a holding that covers the strategy's own is not a
  contradiction at startup); the strategy **is started** and resumes beside the excess — one
  `strategy.resumed_beside_excess` names the instrument, `unowned_quantity`, `strategy_quantity`
  and `broker_quantity`, then `strategy.resumed` names its own lot — and its next opposite signal
  sells **only its own quantity**: TWS still shows the extra shares afterwards. (Before D5a the
  strategy was not started here, `strategy.resume_refused reason=unowned_position`; that record
  now belongs to the flat and opposite-side shapes only.) To see the refusal, instead leave the
  strategy flat and buy by hand: the strategy is not started, its siblings are, and the console
  prints the remedy. Selling shares by hand (IBKR below the strategy's own) must refuse the
  **whole** start at `reconcile`, exit 1.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-28 | Story 4.5 dev session | ⏳ **P19a defined, not run; P19b defined, not run (operator only)** | Port check at 11:31 ET Monday: 4001/4002/7496/7497 **closed** — no Gateway was running; Redis 6379 and Postgres 5432 open. P19a also needs `TWS_ACCOUNT`, and this harness worktree has no `.env` (the charter forbids creating or reading one — the P15/P17a/P18 precedent). P19b needs a position opened and closed at the broker, which the charter forbids an automated session to do. Informational evidence only (NFR33), never a gate: the same logic is proven against broker doubles and real engines in `tests/component/core/test_live_session_resume_engine.py` (process A → B across a shared cache database, under the session's own exec config), `tests/component/core/test_session_runner_resume.py`, `tests/component/core/test_strategy_own_book.py`, `tests/component/core/test_warmup_seam.py`, `tests/integration/core/test_resume_round_trip_redis.py` (real Redis) and `tests/integration/db/test_trade_record.py` (FR19, real Postgres). |
| 2026-09-28 | Epic 4 retro live E2E (14:17–14:41 ET, Mon) | ⚠️ **P19a criterion 1 not literally met (accepted, PO ruling); P19b passed** | Run from the `p3-epic4-base` worktree (f892d56). **P19a**, on an account holding a (short) position: `grep -c EXTERNAL` on the reconciliation lines printed `3`, not `0` — `Filtering unclaimed EXTERNAL orders`, an inferred `OrderFilled` at `position_id=NVDA.NASDAQ-EXTERNAL`, and `Filtered 1 unclaimed EXTERNAL orders`; `INTERNAL-DIFF` import still happened and nothing traded. On a flat account the same run read `0`, as specified. **PO ruling (Epic 4 retro, 2026-09-28): accepted as a documented exception, not a blocker** — these are the filter's own log lines naming what it discarded, not an unfiltered `EXTERNAL` order reaching the cache; D-A's actual guarantee (no fabricated `EXTERNAL` order reaches the cache) held. The pass criterion's literal wording should be read as "no unclaimed `EXTERNAL` order is imported", not "the substring `EXTERNAL` never appears in the log." **P19b**: `reconcile.ok` with `discrepancies=0`/`synthetic_positions=0`, one `strategy.resumed` with `opened_before_this_run=True`, one `trade.aggregated`/`trade.persisted` on the exit, and the `trades` row carried run 1's `entry_timestamp` in the same `session_id`. `flatten_position.py` also exercised live for the first time: dry-run wrong-side/wrong-qty refused with zero orders, `--confirm` filled a 21-share BUY and the broker read back flat. Logs: `logs/p19a-0928.log`, `logs/p19b-*-0928.log`. |

## Procedure P20: corporate actions are absorbed through broker-authoritative state

> Written by Story 4.7 as "Procedure P19". Story 4.5, in parallel, also appended a P19, so the
> integration merge (2026-09-28) renumbered this one P20; its content does not depend on its
> number. The same merge updated the parts Story 4.5 changed: the ⚠️ precondition, the expected
> `synthetic_positions` and the `strategy.resume_refused` that now follows an absorbed start.

**Introduced by**: Story 4.7 — Absorb Corporate Actions Through Broker-Authoritative State
**Verifies**:
- AC #1 / #2 live (P20a): a forward split or stock dividend on an instrument a session's strategy
  holds, across an overnight stop, is absorbed at the next start without manual intervention. It
  is named as one `reconcile.discrepancy` with the before and after quantities, and the
  `reconcile` phase is not refused. (Since Story 4.5 the strategy on that instrument is then not
  started, because the split's extra shares belong to no strategy — see the ⚠️ precondition.)
- AC #1 / #2 live (P20b): a cash dividend credited while the session was stopped is named at the
  next start as `reconcile.cash_changed`. On demand, `live reconcile` shows the difference with
  "session cash as of <time>".
- The NFR14 floor live (P20c, only if one occurs): a reverse split on a held position refuses the
  start, naming the likely cause.

**Tools**: `ntrader live start <session>` (P20a/b/c); `ntrader live reconcile <session>` (P20b,
read-only, `IBKR_LIVE_CLIENT_ID + 1`); IBKR's corporate-actions notices / the issuer's
announcement for the ex-date.

### What it does, and what it does not do

**It cannot be staged.** A corporate action happens at the issuer's schedule, not ours. Paper
accounts receive IBKR's processing of real corporate actions, but a paper account's handling is
itself unverified. If the paper account never reflects the action, **that is the finding**:
record it rather than a pass or a fail.

**P20a and P20c are not read-only.** `live start` starts strategies, and a position must already
be held across the ex-date. They are written for the operator and are **never** run by an
automated story session. **P20b's `live reconcile` half is read-only** against a stopped session
and may be run at any time; it never evicts a running session.

### Preconditions

- A running, logged-in paper Gateway on the configured paper port; no IBKR mobile app or client
  portal session (the 162 rule); a populated `.env` in the operator's checkout.
- P20a: a session whose strategy holds a position in an instrument with an announced **forward
  split** or **stock dividend** ex-date. Stop the session (Ctrl-C) the trading day before the
  ex-date; leave the position open at IBKR.
- **⚠️ P20a: the strategy holding the split is not started after the absorbed start.**
  - Since Story 4.5 a split absorbed at startup leaves strategy +10 beside `INTERNAL-DIFF` +10
    (the engine no longer imports the adapter's `EXTERNAL` order), and the +10 belongs to no
    strategy. Since PR #35 D5a (PO ruling 2026-09-30) the per-strategy resume check starts the
    strategy beside that same-side excess (`strategy.resumed_beside_excess`, P19c's shape) —
    before it, the strategy was refused (`strategy.resume_refused reason=unowned_position`).
  - The extra shares are never traded: the strategy manages its own +10 only. The remedy for the
    orphan shares is manual: remove them in TWS (`docs/agent/nautilus.md`, "Corporate actions").
  - SIGINT the run as soon as `phase=reconcile status=ok` is read. The criteria below need
    nothing after that line.
- P20b: any held instrument with a **cash dividend** paid while the session is stopped, or any
  other credit to the account's cash (interest). Note the cash in TWS, and the time of the
  session's last fill, before stopping.
  - IBKR pushes the account summary only every few minutes, so a fill in the last minutes before
    the stop can be part of the difference too. Compare it with the note's `as of` time.
  - A `live start` that fails *after* connecting (a `gate:account` refusal, a failed broker read)
    uses up the "before": the retried start names nothing. Run P20b's `live reconcile` first.
- D6's standing rule first: grep every transcript for `162`, `10182`, `366` before reading it.

### Command

```bash
# P20b, read-only, before the next start: the stopped session's cash against IBKR's.
uv run python -m src.cli.main live reconcile <session> > logs/p20b-<date>.log 2>&1; echo "exit=$?"
grep -E "^cash |^note:|RESULT" logs/p20b-<date>.log

# P20a / P20b / P20c — operator only; starts strategies if reconciliation passes.
uv run python -m src.cli.main live start <session> > logs/p20-<date>.log 2>&1 &
RUNNER_PID=$!   # the `live start` process itself: nothing is piped
# Wait until the reconcile phase has settled before reading anything.
until grep -qE "phase=reconcile status=(ok|failed)" logs/p20-<date>.log \
      || ! kill -0 "$RUNNER_PID" 2>/dev/null; do sleep 2; done
grep -c -E "162|10182|366" logs/p20-<date>.log            # D6's standing rule, checked first
grep -E "session.phase phase=reconcile|reconcile\.(discrepancy|cash_changed|ok)|strategy\.resume" \
  logs/p20-<date>.log
kill -INT "$RUNNER_PID"; wait "$RUNNER_PID"; echo "exit=$?"   # before any signal (see the ⚠️ above)
```

### Expected output

```
# P20b (live reconcile on the stopped session)
cash USD session=100000.52 broker=100123.97 difference=+123.45 DISCREPANCY
note: session cash as of 2026-10-02T20:00:01.250000+00:00 is the last TotalCashValue ...
RESULT: discrepancy — positions=0 cash=1 line(s) differ from IBKR ...; nothing was changed
exit=5

# P20a / P20b (the next live start)
session.phase phase=reconcile status=started
reconcile.cash_changed scope=startup currency=USD before=100000.52 after=100123.97
  difference=123.45 recorded_at=2026-10-02T20:00:01.250000+00:00
reconcile.discrepancy scope=startup instrument_id=NVDA.NASDAQ kind=position resolution=framework
  local_quantity=10 strategy_quantity=10 broker_quantity=20
reconcile.ok scope=startup ... discrepancies=1 synthetic_positions=1
session.phase phase=reconcile status=ok
strategy.resumed_beside_excess spec_strategy_id=... instrument_id=NVDA.NASDAQ
  unowned_quantity=10 strategy_quantity=10 broker_quantity=20      # P20a, since PR #35 D5a
strategy.resumed strategy_id=SMACrossover-000 instrument_id=NVDA.NASDAQ quantity=10 ...

# P20c (a reverse split)
reconcile.discrepancy ... kind=strategy_position resolution=refused local_quantity=10
  strategy_quantity=10 broker_quantity=5 likely_cause="the broker holds fewer shares than the
  strategy believes — a reverse split, a partial sale outside the session, or a lost fill; or,
  because the session's net already matches the broker, ..."
live start failed: Startup reconciliation refused to let this session trade ...
exit=1
```

### Pass criteria

1. **P20a:** the `reconcile` phase is **not** refused. Exactly one `reconcile.discrepancy` names
   the instrument, with `local_quantity` = the pre-split quantity, `broker_quantity` = TWS's
   post-split quantity, and `resolution` `framework` or `broker`. `reconcile.ok` follows with
   `discrepancies` ≥ 1. `live reconcile` right after the start reports the position clean. One
   `strategy.resumed_beside_excess` names the split's extra shares as `unowned_quantity` beside
   the strategy's own, and the strategy **is** started (PR #35 D5a, PO ruling 2026-09-30 —
   before it, this read `strategy.resume_refused reason=unowned_position` and the strategy was
   not started; a transcript from before that date shows the old record).
2. **P20a — the lot is untouched.** The run is stopped before any signal (the ⚠️ precondition),
   and no `order.submitted` appears in the transcript. `reconcile.ok`'s `synthetic_positions`
   counts the reconciliation-owned positions holding the split's extra shares, and TWS shows
   the post-split quantity. The split's unadjusted P&L and those unmanaged extra shares are the
   documented costs (`docs/agent/nautilus.md`, "Corporate actions"), not a failure.
3. **P20b:** `live reconcile` before the start exits `5`, names the cash difference, and its
   note says `session cash as of <time>`, where the time is the previous run's last summary.
   The next start logs one `reconcile.cash_changed` with the same before, after and difference,
   and still reaches `phase=reconcile status=ok`. Before calling it a dividend, check that the
   session's last fill predates the `as of` time.
4. **P20c (only if one occurs):** exit `1`. The refusal names the instrument, both quantities
   and "reverse split". Nothing is written, and TWS shows no order from the session. Since Story
   4.5 a namespace created after it never caches the order behind the 1.4S abort, and one that
   did is refused before the framework runs (`session.resume_refused
   reason=legacy_position_import`, exit 1). If the process instead aborts inside `node:connect`
   (a Rust panic), record it against Story 4.5's 1.4S item. This procedure does not own it.
5. **All parts:** in every transcript, `grep -c '<full account id>'` prints `0` (NFR26).

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-09-28 | Story 4.7 dev session | ⏳ **Defined, not run** | A corporate action cannot be staged, and P20a/P20c need `live start` across a position held at the broker. The story charter forbids submitting orders or opening, closing or flattening positions, so they are operator-only. P20b's read-only half (`live reconcile`) needs a configured account. The one read-only attempt, `ntrader live check`, stopped at `Cannot build an IBKR execution client: TWS_ACCOUNT is not set` in 0.00 s, with `gate:static ok` and no socket opened: this harness worktree has no `.env`, and the charter forbids creating or reading one (the P15/P17a/P18 precedent). This is informational evidence only (NFR33), never a gate. The same logic is proven against broker doubles and a real `LiveExecutionEngine`:<br>• `tests/component/core/test_live_corporate_actions_engine.py`: forward split absorbed at startup and at runtime, the lot untouched; a reverse split refused and stopped;<br>• `tests/unit/core/test_live_startup_reconcile.py`: `reconcile.cash_changed`, the likely cause, the emitted-name pin;<br>• `tests/component/core/test_session_runner_phases.py` / `test_session_runner_runtime_reconcile.py`: through `runner.run()`;<br>• `tests/component/core/test_live_session_view.py`: "session cash as of";<br>• `tests/integration/core/test_live_startup_cash_redis.py`: against a real Redis, together with the `MONITOR` load-only proof in `test_live_session_view_redis.py`. |

## Procedure P21: a stranded order is cleared by the running session

**Introduced by**: Story 4.8 — Clear a Stranded Order After a Broker-Confirmed Fill or Cancel
**Verifies**:
- AC #1 live (P21a): a strategy's order that the cache still shows open after IBKR cancelled or
  filled it while the session was stopped clears to `CANCELED` within two runtime cycles of the
  session first seeing it gone. One `reconcile.stale_order_cleared` names it.
- AC #3 (P21b, informational only): a session with no resting order does not ask IBKR for its
  open orders.
- Story 4.8's open question (P21c, only if it can be arranged): whether a restart straight into a
  poisoned `p7-position-test`-shaped namespace still stalls inside `node:connect`.
- The false-clear guard (P21d, **a pass condition before Story 4.8 is `done`** — code review
  ruling D1, 2026-10-06): an order IBKR still holds across a **Gateway restart** is **not**
  cleared. IBKR's `reqOpenOrders` answers only "the open orders placed from this client"; if a
  restarted Gateway stops attributing the order to the session's client id, the order reads as
  absent and would be cleared `CANCELED` locally while it is still live at IBKR.

**Tools**: `ntrader live start <session>`; TWS (to cancel the resting order by hand while the
session is stopped); `redis-cli` (read-only, to confirm the order's status in the namespace).

### What it does, and what it does not do

**It is not read-only.** It needs a session holding a **resting** order: a limit order away from
the market, which the built-in strategies never place (market orders only). The order must then be
cancelled in TWS while the session is stopped. It is written for the operator and is **never** run
by an automated story session.

**P21b cannot be fully observed from the logs.** The stale-order check logs nothing on a cycle with
no candidate. With no candidate it never asks IBKR, so it can never log
`reconcile.stale_order_check_failed`. The only check is that the record is absent. The proof that
IBKR is not asked is the unit and component tests (AC #3), not this procedure.

**P21c may never be runnable.** The historical stall happened inside `node:connect`, before any
code from this story runs. If the operator cannot rebuild a namespace in that shape, record that
and leave the question open. Do not claim P21a closes it.

### Preconditions

- A running, logged-in paper Gateway on the configured paper port; no IBKR mobile app or client
  portal session (the 162 rule); a populated `.env` in the operator's checkout.
- A session whose strategy leaves a resting `LIMIT` order at IBKR (a custom test strategy, or a
  strategy parameter that places one). Read its `client_order_id` from the session's
  `order.submitted` / `order.accepted` records.
- Stop the session (Ctrl-C) while the order is still `ACCEPTED`, then **cancel the order in TWS**.
  Confirm TWS shows no open order from the session's client id.
- D6's standing rule first: grep every transcript for `162`, `10182`, `366` before reading it.

### Command

```bash
uv run python -m src.cli.main live start <session> > logs/p21-<date>.log 2>&1 &
RUNNER_PID=$!   # the `live start` process itself: nothing is piped
until grep -q "session.started" logs/p21-<date>.log || ! kill -0 "$RUNNER_PID" 2>/dev/null; do
  sleep 2
done
grep -c -E "162|10182|366" logs/p21-<date>.log            # D6's standing rule, checked first
# Runtime cycles run every 60 s; the clear needs two conclusive reads 60 s apart. If the order
# was filled by hand, the position correction comes first (its instrument is skipped while its
# row stands), so the clear lands on about the fourth cycle: allow 300 s for either variant.
sleep 300
grep -E "reconcile\.(ok|stale_order_cleared|stale_order_clear_failed|stale_order_check_failed)|order\.canceled" \
  logs/p21-<date>.log
kill -INT "$RUNNER_PID"; wait "$RUNNER_PID"; echo "exit=$?"
```

### Expected output

```
reconcile.ok scope=startup ... open_orders=1
session.phase phase=reconcile status=ok
...
reconcile.ok scope=runtime ... open_orders=1
order.canceled ... client_order_id=O-... reconciliation=True
reconcile.stale_order_cleared scope=runtime instrument_id=AAPL.NASDAQ strategy_id=...
  client_order_id=O-... side=BUY quantity=10
  detail="the broker no longer lists this order as open; whether it filled or was cancelled
  could not be determined from here"
...
exit=0
```

### Pass criteria

1. **P21a:** exactly one `reconcile.stale_order_cleared` names the order's `client_order_id`:
   for the cancelled-in-TWS variant on about the second runtime cycle (~2–3 minutes after
   `session.started`); for the filled-by-hand variant on about the fourth (~4–5 minutes), because
   the position correction comes first. No `reconcile.stale_order_clear_failed` appears. The
   order's only `order.canceled` carries `reconciliation=True` — the engine's own reconciliation
   event, logged by the order observer, not a cancel the strategy sent. Nothing is sent to IBKR:
   no `order.submitted` for that order, and TWS shows no new order activity. The runtime
   `reconcile.ok` is throttled to at most hourly after the first clean cycle, so the lower
   `open_orders` count is read from the **next restart's** `reconcile.ok scope=startup` (or from
   the order's status in the namespace with `redis-cli`), not from this transcript.
2. **P21a — never a fill.** No `order.filled` for the order appears, and the transcript holds no
   price or commission for it. If the order was filled by hand in TWS rather than cancelled, the
   position step's `reconcile.discrepancy scope=runtime resolution=broker` carries the holding.
   The order still clears `CANCELED`; that is ruling D-C's documented cost, not a failure.
3. **P21b (informational):** on a clean session with no resting order, no
   `reconcile.stale_order_*` record appears at all.
4. **P21c (only if arranged):** record whether `phase=node:connect` reaches `status=ok`, and how
   long it took. Either result is a finding for the deferred-work item, not a pass or a fail.
5. **P21d (pass condition before `done`):** see the sub-procedure below. **No**
   `reconcile.stale_order_cleared` names the order, and it is still `ACCEPTED` in the namespace.
6. **All parts:** in every transcript, `grep -c '<full account id>'` prints `0` (NFR26).

### P21d: a Gateway restart does not strand a live order (ruling D1)

Run it **before** P21a: its cleanup step is P21a's precondition.

1. Start the session and let its strategy leave a resting `LIMIT` order at IBKR, **GTC**, so it
   survives the restart. Read its `client_order_id`. Stop the session (Ctrl-C).
2. Restart the paper Gateway (log out and back in, or wait for its daily restart). In TWS, confirm
   the order is still working, and note the client id it shows against the order.
3. Start the session and wait 300 s, exactly as in P21a's command.
4. **Pass:** no `reconcile.stale_order_cleared` and no `reconcile.stale_order_clear_failed` name
   the order; a `reconcile.stale_order_check_failed` streak (if any) is not a failure. The order is
   still `ACCEPTED` in the namespace (`redis-cli`, read-only).
5. **Fail:** the order is cleared while TWS still shows it working. That is the false clear D1
   names. Stop, record the TWS client id against the session's `IBKR_LIVE_CLIENT_ID`, and do not
   mark Story 4.8 `done`: the code needs a guard (D1 option 2, a recorded client id).
6. Cleanup: stop the session and cancel the order in TWS. That is P21a's precondition.

### Result log

| Date | Operator | Result | Notes |
| ---- | -------- | ------ | ----- |
| 2026-10-06 | Story 4.8 dev session | ⏳ **Defined, not run** | P21 needs a resting order left at IBKR and cancelled in TWS while the session is stopped. The story charter forbids submitting or cancelling orders, so P21 is operator-only. The same logic is proven against broker doubles, a real `LiveExecutionEngine`, and the real IB adapter's `get_open_orders`:<br>• `tests/unit/core/test_live_stranded_orders.py`: candidates, the debounce, containment, the never-`FILLED` AST scan, the emitted-name pin;<br>• `tests/component/core/test_live_stranded_orders_engine.py`: the clear as the framework's own `OrderCanceled(reconciliation=True)`, the inconclusive read against the real adapter, and the canaries for why `generate_order_status_reports` is not used;<br>• `tests/component/core/test_live_runtime_reconcile_engine.py`: end to end through `RuntimeReconciler`, including the filled-while-away case. Task 1.4's stall probe, re-run at component tier, still does not reproduce the `p7-position-test` stall. |
| 2026-10-06 | Story 4.8 code review | ⏳ **P21d added, not run** | Ruling D1: P21d (a Gateway restart does not strand a live order) is a pass condition before 4.8 is `done`. Also corrected: the `order.canceled … reconciliation=True` line the clear produces, the 300 s wait for the filled-by-hand variant, and where the lower `open_orders` count can be observed. |
