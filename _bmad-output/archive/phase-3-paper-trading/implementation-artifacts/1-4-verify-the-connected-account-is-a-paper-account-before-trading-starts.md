# Story 1.4: Verify the Connected Account Is a Paper Account Before Trading Starts

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want the system to check what account it is *actually* connected to, not just what the config
claimed,
so that a real-money account listening on a paper port is still caught.

## Acceptance Criteria

1. **Given** the node has connected and the gateway has reported its managed accounts, **When** the
   `gate:account` phase runs, **Then** every reported account must carry a paper prefix (`DU`/`DF`)
   for the sequence to continue — a single non-paper account in the reported set refuses the whole
   connection (FR8, AR14).
2. **Given** Layer 2 evaluates a configuration Layer 1 would refuse, **When** the decision is
   produced, **Then** Layer 1's refusal is returned unchanged — Layer 2 is a strict *additional*
   condition and can never permit anything Layer 1 refused (FR9, "never weaken the gate").
3. **Given** a `REAL_MONEY` decision from Layer 1, **When** Layer 2 runs, **Then** the authorized
   account (`NTRADER_REAL_MONEY_ACCOUNT`) must appear in the set the gateway actually reports
   **And** must not carry a paper prefix — closing the `deferred-work.md` item recorded against
   Story 1.1, which is explicitly assigned to this story (FR10).
4. **Given** verification cannot obtain evidence — the node is not connected, or the gateway named
   no account — **When** the decision is produced, **Then** it is a refusal, never a permit
   (fail-closed).
5. **Given** any Layer 2 refusal, **When** it is enforced against a live node, **Then** the node is
   stopped before the function returns, no strategy is started, and the *same* refusal outcome as
   the static gate is raised — `GateRefusedError` carrying a `GateRefusal`, the exception Story 1.7
   maps to exit code `3` (FR9, AR14, AR28).
6. **Given** a strategy is already `RUNNING` when verification is called, **When** verification
   runs, **Then** it refuses and stops the node rather than blessing an account that strategies are
   already trading on — making AR39's "after connection, strictly before any strategy is started"
   ordering enforced at runtime, not merely documented (AR39).
7. **Given** verification logs its result, **When** the records are inspected, **Then** they carry
   `phase="gate:account"` and `status="started"|"ok"|"failed"` (AR39) **And** every account
   identifier in a log record *or* in a refusal message is masked to its last 3 characters via the
   existing `mask_account()` (NFR26).
8. **Given** the whole story, **When** the diff is reviewed, **Then** no order-submission path, no
   session runner, no CLI command and no new dependency has been added — `pyproject.toml` and
   `uv.lock` are byte-identical (AR3, and Epic 1's "safety before capability" rule).

## Tasks / Subtasks

- [x] **Task 1: Failing unit tests for the pure Layer 2 decision (TDD Red)** (AC: #1, #2, #3, #4, #7)
  - [x] Extend `tests/unit/core/test_live_gate.py` — do **not** create a second gate test file. The
        decision function lives in `src/core/live_gate.py`, so its tests belong beside the Layer 1
        truth table they must stay consistent with.
  - [x] Reuse the existing `_settings(...)` helper and `_assert_consistent(...)` invariant checker
        verbatim. Every new test must call `_assert_consistent` — the
        `permitted is (refusal is None) is (mode is not None)` invariant is the gate's contract and
        Layer 2 must not be the path that breaks it.
  - [x] **AC #1 — paper path.** `frozenset({"DU4076626"})` permits with `mode is GateMode.PAPER`.
        `frozenset({"DU4076626", "U1234567"})` **refuses** with
        `GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER` — one real-money account anywhere in the
        reported set is a refusal, even though the configured account is clean. That case is the
        entire point of Layer 2; write it first.
  - [x] **AC #2 — Layer 1 dominance.** For each Layer 1 refusal (non-paper mode, non-paper port,
        non-paper configured prefix, and each real-money crossing refusal), assert
        `evaluate_account_gate(...)` returns a decision whose `refusal.reason` is *identical to*
        `evaluate_gate(...)`'s, even when `reported_accounts` is a perfectly clean paper set.
        Parametrise off the same table Layer 1's tests use.
  - [x] **AC #3 — real-money crossing.** With `--real-money` + `NTRADER_REAL_MONEY_ACCOUNT` +
        matching `TWS_ACCOUNT`: (a) reported set containing the authorized account and no paper
        prefix → permits with `mode is GateMode.REAL_MONEY`; (b) reported set missing it →
        `REPORTED_ACCOUNT_NOT_AUTHORIZED`; (c) the authorized account itself carrying `DU`/`DF` →
        `REPORTED_ACCOUNT_IS_PAPER`. Case (c) is the deferred-work item this story is charged with
        closing — the operator believes real orders are going out while they land on a demo account.
  - [x] **AC #4 — fail closed.** An empty `reported_accounts`, and a set containing only blank or
        whitespace strings, both refuse with `ACCOUNT_NOT_REPORTED`. Assert explicitly that
        `permitted is False`; "it did not raise" is not the assertion.
  - [x] **AC #7 — masking.** Every new refusal message must contain `"***"` and must **not** contain
        any full account string used in the test. Add one test that scans all Layer 2 refusal
        messages for the raw account substring — Story 1.1's review found masking gaps twice.
  - [x] **Purity is preserved.** `TestGatePurity`'s `ALLOWED_RUNTIME_IMPORTS` frozenset
        (`{"dataclasses", "enum", "typing"}`) must be left **unchanged**. That forbids
        `collections.abc` — so type the parameter as `frozenset[str]` (a builtin generic needing no
        import), not `Iterable[str]`. If a task tempts you to widen that whitelist, the design is
        wrong, not the test.
  - [x] Record the RED output in Debug Log References before writing Task 2.
- [x] **Task 2: Implement the pure Layer 2 decision in `src/core/live_gate.py`** (AC: #1, #2, #3, #4, #7)
  - [x] Add four `GateRefusalReason` members. Values are lowercase snake_case, matching the six that
        already exist:
        `ACCOUNT_NOT_REPORTED`, `REPORTED_ACCOUNT_NOT_PAPER`, `REPORTED_ACCOUNT_NOT_AUTHORIZED`,
        `REPORTED_ACCOUNT_IS_PAPER`. Add two more used only by Task 4's seam:
        `NODE_NOT_CONNECTED`, `STRATEGY_STARTED_BEFORE_ACCOUNT_GATE`.
  - [x] Rename the private `_refuse` to a public `build_refusal(reason, message) -> GateDecision`
        and update its seven call sites. Reason: Task 4's module must construct refusals too, and
        the `permitted`/`mode`/`refusal` invariant must keep exactly **one** constructor. Do not
        copy the three-line `GateDecision(permitted=False, mode=None, ...)` shape into a second
        module.
  - [x] Public signature — mirrors `evaluate_gate`, settings injected, never fetched:
        ```python
        def evaluate_account_gate(
            settings: "IBKRSettings",
            cli_flags: GateFlags,
            reported_accounts: frozenset[str],
        ) -> GateDecision:
        ```
  - [x] **Body order is the safety property.** Call `evaluate_gate(settings, cli_flags)` first and
        return its decision unchanged if it refused (AC #2). Only then normalise
        `reported_accounts` (`strip()`, drop empties) and branch on `decision.mode`.
  - [x] Paper branch: refuse `ACCOUNT_NOT_REPORTED` on an empty normalised set; otherwise refuse
        `REPORTED_ACCOUNT_NOT_PAPER` if **any** account fails
        `account.upper().startswith(PAPER_ACCOUNT_PREFIXES)`. Reuse the existing module constant —
        do not re-spell `("DU", "DF")`.
  - [x] Real-money branch: refuse `ACCOUNT_NOT_REPORTED` on an empty set; refuse
        `REPORTED_ACCOUNT_IS_PAPER` if the authorized account carries a paper prefix; refuse
        `REPORTED_ACCOUNT_NOT_AUTHORIZED` if it is absent from the reported set.
        **Compare exactly** (after `strip()` only, no case folding) — Layer 1 compares
        `env_account != account` exactly, and case-folding here would make *more* accounts match,
        which on the real-money path means permitting more. Note the deliberate asymmetry in a
        comment: the prefix test upper-cases (mirroring `_evaluate_paper`), the identity test does
        not.
  - [x] Every refusal message renders accounts through `mask_account()` and sorts the masked values
        so the text is deterministic (a `frozenset` has no stable iteration order).
  - [x] Update the module docstring: Layer 2's *decision* now lives here and is still pure; Layer 2's
        *evidence gathering and enforcement* (reading from a connected node, stopping it) lives in
        `src/core/live_account_gate.py`. The current wording says Layer 2 "lives elsewhere" — correct
        that rather than leaving it contradicting the code.
  - [x] GREEN: `uv run pytest tests/unit/core/test_live_gate.py -v`.
- [x] **Task 3: Failing component tests for the enforcement seam (TDD Red)** (AC: #4, #5, #6, #7)
  - [x] New file `tests/component/core/test_live_account_gate.py`, every test
        `@pytest.mark.component`. Component tier because the module under test imports the Nautilus
        IB adapter; nothing here constructs a real `TradingNode`.
  - [x] Copy `test_live_node_builder.py`'s `_assert_c_logging_state_is_unchanged` autouse fixture
        (assert on the *delta*, not the absolute state — the component tier shares a worker process
        with tests that do initialise C logging).
  - [x] Write a `_FakeNode` double: `.kernel.exec_engine.check_connected()`, `.trader.strategy_states()`,
        and an `async stop_async()` that records it was awaited. Put it in the test file, not in
        `tests/component/doubles/` — `doubles/` holds reusable Nautilus-object doubles
        (`test_engine.py`, `test_order.py`, `test_position.py`); a one-file structural stub does not
        earn a shared module yet. Epic 2's `TestLiveNode` can promote it.
  - [x] **Seed `IB_CLIENTS` explicitly and clean it up.** The adapter's account list is read from
        `nautilus_trader.adapters.interactive_brokers.factories.IB_CLIENTS`, a module-level dict
        that leaks between tests sharing a process (already documented in
        `tests/integration/core/test_live_node_lifecycle.py`'s docstring). Use a fixture that
        inserts a stub client exposing `accounts() -> set[str]` under
        `(host, port, ibkr_live_client_id)` and **removes it in teardown**. A test that forgets this
        poisons every later test in the worker.
  - [x] **AC #5 — the refusal outcome.** A non-paper reported account raises `GateRefusedError`,
        `exc.value.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER`, and the fake
        node recorded that `stop_async()` was awaited **before** the exception surfaced. Assert both
        — "it raised" alone does not prove the node was stopped.
  - [x] **AC #5 — permit path.** A clean paper account returns a permitted `GateDecision` with
        `mode is GateMode.PAPER` and `stop_async()` was **not** called.
  - [x] **AC #4 — not connected.** `check_connected()` returning `False` raises `GateRefusedError`
        with `NODE_NOT_CONNECTED`, and never reads the account list.
  - [x] **AC #4 — no client registered.** With `IB_CLIENTS` empty for the configured key, the
        function refuses `ACCOUNT_NOT_REPORTED` rather than raising `KeyError` — the adapter
        renaming or re-keying that dict must fail closed, not explode.
  - [x] **AC #6 — ordering guard.** `strategy_states()` returning `{StrategyId-ish: "RUNNING"}`
        refuses with `STRATEGY_STARTED_BEFORE_ACCOUNT_GATE` and stops the node. A state of
        `"INITIALIZED"`/`"READY"` (strategies added, none started) must **not** refuse — that is the
        normal AR39 position and a false positive there would block every session Epic 2 starts.
  - [x] **AC #5 — shutdown failure must not mask the refusal.** A `stop_async()` that raises still
        produces `GateRefusedError` with the account reason, not the shutdown exception.
  - [x] **AC #7 — logging.** Use `structlog.testing.capture_logs` (the in-repo convention —
        `tests/unit/services/test_kraken_settings.py:8`). Assert a `phase="gate:account"` record with
        `status="started"`, then one with `status="ok"` on the permit path and `status="failed"` on
        the refusal path; assert no captured record's rendered values contain the full account.
  - [x] Record the RED output before writing Task 4.
- [x] **Task 4: Implement the enforcement seam `src/core/live_account_gate.py`** (AC: #4, #5, #6, #7)
  - [x] New module. Docstring states: this is Layer 2's impure half — it reads what the gateway
        reported and enforces the decision against a live node; the decision itself is
        `evaluate_account_gate` in `live_gate.py`; the *runner* still owns the node's lifecycle
        (AR38) and this function only stops it, never disposes it.
  - [x] Reuse `GateRefusedError` from `live_node_builder` — do **not** define a second exception
        class. AC #5's "the same refusal outcome as the static gate" is exactly this. Import
        direction `live_account_gate → live_node_builder → live_gate` introduces no cycle.
  - [x] ```python
        def gateway_reported_accounts(settings: IBKRSettings) -> frozenset[str]:
        ```
        Look up `IB_CLIENTS.get((settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id))`
        and return `frozenset()` when absent. Those three fields are exactly the key
        `get_cached_ib_client` builds (`factories.py:112`) from the values Story 1.3's builder
        passes as `ibg_host`/`ibg_port`/`ibg_client_id`.
  - [x] ```python
        async def verify_connected_account(
            node: "TradingNode",
            settings: IBKRSettings,
            *,
            cli_flags: GateFlags | None = None,
        ) -> GateDecision:
        ```
        `async`, not sync — see the `dispose()`/`stop()` trap in Dev Notes. Import `TradingNode`
        under `TYPE_CHECKING` only, so the double in Task 3 needs no Nautilus construction.
  - [x] Order inside the function: log `status="started"` → connection guard → strategy-ordering
        guard → `gateway_reported_accounts` → `evaluate_account_gate` → on refusal
        `await node.stop_async()` (best-effort, wrapped) then `raise GateRefusedError(refusal)`; on
        permit log `status="ok"` and return the decision.
  - [x] Log fields: `phase="gate:account"`, `status=...`, `mode=...`, and masked accounts only.
        Emit `status="failed"` with `reason=refusal.reason.value` before stopping the node, so the
        record exists even if shutdown then hangs.
  - [x] Keep this module free of a phase enum or a generic phase emitter — AR39's full sequence is
        the runner's contract (Epic 2). This module emits **its own** phase's records and nothing
        more. Story 1.3 was held to the same line.
  - [x] GREEN: `uv run pytest tests/component/core/test_live_account_gate.py -v`.
- [x] **Task 5: Wire the phase into the diagnostic probe** (AC: #5, #7)
  - [x] Add an opt-in `--verify-account` flag to `scripts/diagnostics/live_node_probe.py`. Default
        `False`, so **Procedure P1's documented command and behaviour are unchanged** — P1 is Story
        1.3's evidence and must not acquire a new failure mode.
  - [x] When set, call `await verify_connected_account(node, settings, cli_flags=GateFlags())`
        immediately after `_await_connected` returns and before the run-seconds sleep — the exact
        AR39 position. Print the masked account and the resulting mode.
  - [x] Add a `RESULT: fail reason=account_gate_refused refusal=<reason>` branch returning exit `1`,
        alongside the existing `gate_refused` branch. Do not reuse the `gate_refused` string —
        an operator reading the QA log must be able to tell Layer 1 from Layer 2.
  - [x] Do **not** touch `ibkr_reconnect_probe.py`, `run_kill_scenarios.sh`, or
        `run_reconnect_scenarios.sh`.
- [ ] **Task 6: Document Procedure P2 and record the live result** (AC: #5, #7)
  - [x] Append **Procedure P2: verify the connected account at the `gate:account` phase** to
        `docs/qa/phase3-live-verification.md`, following P1's exact section shape: Introduced by /
        Verifies / Tool / Preconditions / Command / Expected output / failure-mode table / Pass
        criteria / Result log.
  - [x] Command: `PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py --run-seconds 5 --verify-account`
  - [x] Pass criteria: the probe prints a `gate:account` permit naming the **masked** account, exits
        `0`, and the full account string appears nowhere in stdout.
  - [ ] Run it against the local paper Gateway and record the result in the Result log with the
        date. A dry run is not evidence — the QA doc says so itself. **Informational evidence only:
        it never gates the story.**
- [x] **Task 7: Close the loop on records and quality gates** (AC: #8)
  - [x] In `deferred-work.md`, strike through the Story 1.1 item "Real-money crossing permits a
        `DU`/`DF` paper account" and mark it **RESOLVED in story-1.4**, naming
        `REPORTED_ACCOUNT_IS_PAPER` — follow the existing `~~strikethrough~~ + RESOLVED` format the
        Story 1.2 entry already uses. Record anything new this story surfaces but does not fix.
  - [x] `uv run ruff check .`, `uv run ruff format .`, `make typecheck` (covers `src/core`), and the
        unit + component tiers. Record before/after test counts as *collected*, not passed — Story
        1.3's review had a finding retracted for confusing the two.
  - [x] Verify `git diff --stat` shows no change to `pyproject.toml` / `uv.lock` (AC #8).
  - [x] Update `sprint-status.yaml`: **only** the
        `1-4-verify-the-connected-account-is-a-paper-account-before-trading-starts` key. Never the
        `epic-1` key.

### Review Findings

Three adversarial layers (Blind Hunter — diff only; Edge Case Hunter — diff + project; Acceptance
Auditor — diff + spec). Outcome: **0 decision-needed, 17 patch, 8 defer, 4 dismissed**. All 8 ACs
were judged satisfied by the Acceptance Auditor, and every factual claim in the Dev Agent Record was
independently reproduced. The patches below are hardening the layers found *inside* satisfied ACs.

**Applied — fail-open paths (the ones that mattered)**

- [x] [Review][Patch] Unexpected exceptions escaped the gate with the node still up [src/core/live_account_gate.py] — every attribute the seam reaches (`node.kernel`, `node.trader`, `IB_CLIENTS`, `client.accounts()`) belongs to a third party. A Nautilus rename surfaced as an `AttributeError` propagating out with the node **connected and about to start strategies** — the exact opposite of the fail-closed contract. Now `_decide` converts any exception into an `ACCOUNT_VERIFICATION_ERROR` refusal and stops the node.
- [x] [Review][Patch] `check_connected()` is vacuously `True` on a node whose `build()` never ran [src/core/live_account_gate.py] — `ExecutionEngine.check_connected()` iterates registered clients and returns `True` on an empty dict, so the `NODE_NOT_CONNECTED` guard passed for a node with no socket. Now also requires `exec_engine.registered_clients` to be non-empty.
- [x] [Review][Patch] `asyncio.CancelledError` aborted the refusal before it was raised [src/core/live_account_gate.py] — it is a `BaseException`, so it escaped `except Exception` in `_stop_node` and killed `verify_connected_account` before `raise GateRefusedError`, leaving Story 1.7's exit-code mapping nothing to map. Now caught explicitly.
- [x] [Review][Patch] An unbounded `stop_async()` could withhold the refusal indefinitely [src/core/live_account_gate.py] — `kernel.stop_async()` awaits two operator-settable timeouts in series. Now bounded by `STOP_TIMEOUT_SECONDS`; on timeout the refusal is raised anyway.

**Applied — evidence and diagnostics**

- [x] [Review][Patch] The log and the probe rendered the *unstripped* account set [src/core/live_gate.py] — the decision strips and drops blanks, but the rendering did not, so IBKR's trailing-comma `managedAccounts` payload printed `accounts=, ***626` and a padded entry masked to the wrong three characters. New public `normalize_reported_accounts()` is now the single definition of the evidence, used by decision and rendering alike.
- [x] [Review][Patch] The probe printed a *second, later* read of the account set [scripts/diagnostics/live_node_probe.py] — the adapter clears `_account_ids` on disconnect, so a blip put `accounts=` next to a verdict reached on different evidence. `verify_connected_account` now takes an optional `reported_accounts`, and the probe reads once and hands the gate the same set it prints.
- [x] [Review][Patch] Masked duplicates rendered as `***567, ***567` [src/core/live_gate.py, src/core/live_account_gate.py] — distinct accounts can share their last three characters. De-duplicated after masking.
- [x] [Review][Patch] Third-party exception text was interpolated into a log record [src/core/live_account_gate.py] — adapter and broker error strings routinely embed the account. `_stop_node` now logs the exception *type* only; pinned by a test asserting the message text is absent (NFR26).
- [x] [Review][Patch] `_UNEXPLAINED_REFUSAL` was stamped `ACCOUNT_NOT_REPORTED` [src/core/live_account_gate.py] — a reason with a distinct documented operator meaning, which would have sent an operator to restart a healthy gateway over an internal invariant break. Now `ACCOUNT_VERIFICATION_ERROR`.
- [x] [Review][Patch] Refusal event renamed to AR41's enumerated `gate.refused` [src/core/live_account_gate.py] — was `live_gate.account`, which is neither past-tense nor an AR41 name.
- [x] [Review][Patch] `_check_placement` → `_placement_refusal` [src/core/live_account_gate.py] — AR36 makes the vocabulary normative (*gate*, never *guard*/*check*/*validator*).

**Applied — the case-folding rationale was inverted**

- [x] [Review][Patch] The comment justifying `account.upper()` on the paper path cited a rationale true only of the opposite path [src/core/live_gate.py] — in `_evaluate_reported_real_money` matching a paper prefix *triggers* a refusal, so folding refuses more; in `_evaluate_reported_paper` it *suppresses* one, so folding **permits** more. A safety-gate comment claiming a fold is refusal-widening when it is permit-widening is worse than no comment. Rewritten to state the real trade-off and why consistency with Layer 1 still wins.

**Applied — tests that asserted less than they claimed**

- [x] [Review][Patch] `test_refusal_message_is_deterministic_across_orderings` would pass with `sorted()` deleted [tests/unit/core/test_live_gate.py] — two frozensets built from the same strings iterate identically within a process, so it never produced two orderings. Replaced with a direct assertion that the masked values appear in sorted order.
- [x] [Review][Patch] `test_a_failing_shutdown_does_not_mask_the_refusal` would pass with `_stop_node`'s error branch replaced by `pass` [tests/component/core/test_live_account_gate.py] — the one record telling an operator the node may still be up was unpinned. Now asserted.
- [x] [Review][Patch] AC #5's "no strategy is started" clause had no assertion [tests/component/core/test_live_account_gate.py] — structurally vacuous, but the checkbox claimed more than the test showed. Added `test_no_strategy_is_started_on_any_path`.
- [x] [Review][Patch] The `NOT_YET_STARTED_STATES` allowlist was compared only against the test double's own strings [tests/component/core/test_live_account_gate.py] — the double defined the contract it then verified. Now pinned against the real `ComponentState` member names.
- [x] [Review][Patch] `_FakeExecEngine` could not express the vacuous-`True` state, making the unbuilt-node path untestable [tests/component/core/test_live_account_gate.py] — the double now models `registered_clients` and reproduces the real method's behaviour.

**Applied — documentation**

- [x] [Review][Patch] P2 cited two different AC sets for the same procedure, and its failure table omitted two reachable refusals [docs/qa/phase3-live-verification.md] — corrected to story AC #1/#6/#7 throughout; `reported_account_not_authorized` and `account_verification_error` rows added.
- [x] [Review][Patch] P2's NFR26 pass criterion could never pass [docs/qa/phase3-live-verification.md] — Nautilus's own IB adapter prints the raw account to stdout on every successful connection (P1's 2026-08-05 log entry quotes it), so the documented `grep -c … # must print 0` would fail on a *correct* run for a reason unrelated to this story's masking. Scoped to `[probe]` lines, with the third-party surface recorded as deferred work.
- [x] [Review][Patch] The standing "a gate refusal is logged nowhere" deferred item was made stale by this story [deferred-work.md] — annotated as partially resolved (Layer 2 now logs; Layer 1 still does not).

**Deferred** — all eight recorded in `deferred-work.md` under *Deferred from: code review of story-1.4*:

- [x] [Review][Defer] Procedure P2 still not run against a live gateway — deferred, environmental (no `.env` in this worktree; sandbox declined the alternative)
- [x] [Review][Defer] ⚠️ **Epic 2 blocker** — `NautilusKernel.start_async()` has no hook between "engines connected" and "trader started", so `gate:account` cannot be placed by polling once strategies exist — deferred to Story 2.5
- [x] [Review][Defer] `READY` is reachable after a reset, so the ordering guard has a blind spot across in-process session restarts — deferred to Story 2.5
- [x] [Review][Defer] The ordering guard ignores actors and exec algorithms — deferred, deliberately not widened (AC #6 says "strategy", and widening compounds the Epic 2 placement problem)
- [x] [Review][Defer] Nautilus itself prints the raw account to stdout — deferred, pre-existing and third-party
- [x] [Review][Defer] The paper path does not check `TWS_ACCOUNT` is among the reported accounts — deferred, blocked on settling the Layer 1 casing asymmetry first
- [x] [Review][Defer] `tests/unit/core/test_live_gate.py` exceeds CLAUDE.md's 500-line limit — deferred, pre-existing (508 before this story) and the repo has clear precedent against applying it to tests
- [x] [Review][Defer] The probe's `if problems:` check is unreachable on exception paths — deferred, pre-existing from Story 1.3; the information reaches stderr, only not the `RESULT:` line

**Dismissed** (4): async tests missing an `asyncio` marker (`pytest.ini` sets `asyncio_mode = auto`; the tests demonstrably run); `strategy_states()` might not return `str` (`trading/trader.py` returns `{k: v.state.name}` — verified, and now pinned by a test); the seam's placement check preempting Layer 1 (both outcomes refuse and stop the node — a diagnostic nuance, not a weakening, and `test_layer_one_refusal_reaches_the_caller_unchanged` covers the real path); shutdown problems "discarded" by the probe (they are printed to stderr in the `finally` — demoted, not lost, and recorded as deferred instead).

## Dev Notes

### What this story is, and where its edges are

Two files change under `src/`: `live_gate.py` gains the pure Layer 2 decision, and a new
`live_account_gate.py` enforces it against a connected node. Plus their tests, a probe flag, and the
QA procedure. It produces a **verdict and a shutdown** — it does not start strategies, does not
submit orders, does not own a session, and does not add a CLI command.

If the diff touches `src/config.py`, `src/api/**`, `templates/**`, `alembic/**`, any strategy file,
or `src/core/live_node_builder.py` beyond nothing at all, stop and re-read the scope boundaries.

### Why Layer 2 exists at all — the hole it actually plugs

A reviewer will reasonably ask: the IB execution client *already* refuses to connect when the
configured account is not among the gateway's managed accounts
(`adapters/interactive_brokers/execution.py:203-213` — `fault()` then `ValueError`). What is left
for Layer 2?

Three things, and they are the tests worth writing:

1. **A gateway that manages more than one account.** Membership is satisfied by the *configured*
   account alone. A paper-port session whose gateway also manages `U1234567` passes Layer 1 (config
   is clean) and passes the adapter's check (`DU…` is a member) while the process is one
   misconfiguration away from a real account. Layer 2 requires **every** reported account to be
   paper. `deferred-work.md` already records the sibling gap on the Layer 1 side ("Multi-account
   `TWS_ACCOUNT` is validated on its first element only").
2. **The real-money crossing was never checked against reality.** Layer 1 compares
   `NTRADER_REAL_MONEY_ACCOUNT` against `TWS_ACCOUNT` — two config values that can agree with each
   other and disagree with the world. Layer 2 is the first check against what the broker says.
3. **A paper-prefixed real-money authorization.** `--real-money` + `NTRADER_REAL_MONEY_ACCOUNT=DU…`
   currently permits `mode=REAL_MONEY` against a demo account. The operator believes real orders are
   going out. This is the item `deferred-work.md` assigns to this story by name.

### ⚠️ Trap: `node.dispose()` from inside the running loop hangs, then breaks the loop

This is why `verify_connected_account` is `async` and stops with `await node.stop_async()`.

Verified against the installed wheel (`nautilus_trader/live/node.py`):

- `TradingNode.stop()` (line 374) checks `if self.kernel.loop.is_running():` and, when it is,
  merely **schedules** `stop_async()` as a task. Called from inside a coroutine already running on
  that loop it returns immediately, having stopped nothing yet — so "stop then raise" would raise
  before the node was actually down.
- `TradingNode.dispose()` (line 402) busy-waits with a **synchronous** `time.sleep(0.1)` while
  `self.kernel.is_running()`. Called from inside the loop it blocks the very loop that would run the
  stop task: it spins until `timeout_disconnection` expires, then calls `loop.stop()` mid-flight.
  Story 1.3's probe hit exactly this and it cost a live run (see the 2026-08-05 entry in
  `docs/qa/phase3-live-verification.md`).

So: **`await node.stop_async()`, never `stop()`, never `dispose()`.** Disposal and loop ownership
stay with the caller (AR38). Say so in the docstring so Epic 2's runner does not "helpfully" add a
`dispose()` here.

### Where the gateway's account list actually comes from

`InteractiveBrokersClient.accounts()` (`adapters/interactive_brokers/client/account.py:41-50`)
returns the set parsed from IBKR's `managedAccounts` message, which the gateway sends **automatically
on successful API connection** (`client/account.py:198-206`). That is the gateway's own statement of
what it manages — the ground truth AR14 asks for.

Reach it through `factories.IB_CLIENTS`, the adapter's module-level cache keyed on
`(host, port, client_id)` (`factories.py:112-124`). Two reasons this beats the alternatives:

- It is a plain module attribute plus a documented public method — **no private attribute access**.
  Going through `node.kernel.exec_engine` would require `_clients` *and* the client's `_client`.
- `node.cache.accounts()` is the wrong source: those `AccountId`s are built from the **configured**
  `account_id` the exec client was handed (`execution.py:172`, `_set_account_id`), so they echo
  config back at you. Layer 2 exists precisely because config is not evidence.

Do **not** call `get_cached_ib_client(...)` to fetch it — that function *creates and starts* a client
when the key is absent. Read the dict with `.get()` and fail closed on `None`.

### Never weaken the gate — the two rules that govern this diff

1. **Layer 2 begins by running Layer 1 and returning its refusal unchanged.** Structurally, Layer 2
   can then only ever be Layer-1-AND-something-more. There is no code path in which a reported
   account rescues a refused configuration.
2. **Case folding is not symmetric and the asymmetry is deliberate.** The paper-prefix test
   upper-cases, mirroring `_evaluate_paper` (`live_gate.py:214`) — consistency with Layer 1 matters
   and a real gateway never reports lowercase. The real-money identity test does **not** case-fold:
   Layer 1 compares exactly, and folding would make more accounts match, which on that path means
   permitting more. Write the comment; a future reader will otherwise "fix" the inconsistency.

### Scope boundaries — do NOT do these here

- **No order path, no `submit_order` wrapper, no trading-permitted flag.** Epic 1 ships the safety
  control *before* the capability. Story 1.6 owns the connection-loss permission flag.
- **No `live_session_runner.py`, no phase enum, no `STARTUP_PHASES` tuple.** AR39's full ordered
  sequence is Epic 2's contract (Story 2.5). This story emits its own phase's log records in AR39's
  field format and stops there — the same line Story 1.3 was held to.
- **No CLI, no exit-code mapping.** Story 1.7 maps `GateRefusedError` → `3`. Your obligation is to
  raise that same class so the mapping already covers Layer 2.
- **No `src/core/live_node_builder.py` changes.** It builds config; it does not read runtime state.
  Adding verification there would give it a second, lifecycle-shaped responsibility.
- **No reconciliation, no position/cash reads.** Epic 4 (`gate:account` precedes `reconcile`).
- **No `src/config.py` changes.** Every field Layer 2 reads already exists.
- **No new dependency** (AR3).
- **No README change.** No new environment variable and no new operator command; the probe flag is a
  diagnostic documented in `docs/qa/`.

### Previous story intelligence (Story 1.3, and 1.2/1.1 before it)

- **`GateRefusedError` and `LiveNodeConfigError` are the established seam** (`live_node_builder.py`).
  Story 1.3's review specifically forbade collapsing them. Reuse `GateRefusedError`; do not invent a
  third.
- **Story 1.3's review caught a lowercase `TWS_ACCOUNT` reaching the exec client un-normalised, and
  fixed it *because of this story*** — `_resolve_account` (`live_node_builder.py:83-98`) now
  upper-cases, with a docstring that names "Story 1.4's Layer 2 verification" as the reason. That
  normalisation is already done for you; do not redo it, and do not undo it.
- **A malformed `trader_id` panics in Rust and aborts the process** (exit 134, uncatchable). Any new
  parametrised test that constructs Nautilus identifiers must use well-formed values. Task 3's
  `strategy_states()` keys can be plain strings on the double — you do not need real `StrategyId`s.
- **`mask_account()` is public precisely so Layer 2 reuses it.** Story 1.1's review found masking
  gaps twice; Task 1 has a dedicated test for it.
- **`.env` is already gate-clean**: host `127.0.0.1`, port `4002`, account `DU4076626`, mode
  `paper`, live client `10`. So Task 6's live run has a real target and its expected masked value is
  `***626`. `.env*` is hook-protected — if you think you need to edit it, escalate instead.
- **The component tier shares a worker process.** `test_live_node_builder.py`'s autouse fixture
  asserts the C-logging *delta*, not the absolute state, for exactly that reason (observed
  2026-08-05: an absolute check errored all 38 tests in a full run while passing standalone). Copy
  the delta form.
- **Known pre-existing hazard, not yours to fix:** running the whole `tests/integration` tree in one
  `--forked` invocation can SIGTRAP when `tests/integration/api/test_trades_api.py` shares a worker
  (`src/api/web.py:22` runs `init_logging()` at import time). Recorded in `deferred-work.md` under
  story-1.3. This story adds no integration test, so it should not meet it.

### Git intelligence — what the last five commits establish

`64691ce` (`feat(live): assemble a TradingNode for IBKR paper trading`) is the direct parent of this
work and the shape to copy: one focused module + component tests + integration tests + a diagnostic
+ a QA procedure, then a hardening pass after adversarial review. `a67a0ee` and `6530b53` show that
this epic records its story and review outcomes in `_bmad-output/` in the same commit family.
`52b363b` reworked the commit gate to inspect **staged files only** — so `git add <files>` and
`git commit` as *separate* Bash calls is the reliable sequence, and partial commits work.

### Project Structure Notes

- **MOD** `src/core/live_gate.py` — pure Layer 2 decision, four+two new refusal reasons, `_refuse` →
  `build_refusal`, docstring correction. Stays import-pure.
- **NEW** `src/core/live_account_gate.py` — Layer 2 enforcement. Module prefix `live_` per the
  architecture's naming pattern; "gate" is the sanctioned vocabulary word (never "guard", "check",
  or "validator"). [Source: architecture.md#Naming-Patterns]
- **NEW** `tests/component/core/test_live_account_gate.py`; **MOD**
  `tests/unit/core/test_live_gate.py`.
- **MOD** `scripts/diagnostics/live_node_probe.py` (opt-in flag only), `docs/qa/phase3-live-verification.md`.
- **MOD** `_bmad-output/implementation-artifacts/deferred-work.md`, `sprint-status.yaml`.
- Import direction (AR38): `live_account_gate` may import `nautilus_trader.*`, `src.core.live_gate`,
  `src.core.live_node_builder`, `src.config` (types) and `structlog`. It must **not** import
  SQLAlchemy, `src.db.*`, or any service.

### Testing standards

- **Unit tier** for the pure decision — it must stay runnable with no Nautilus import
  (`pytest.ini:25`). **Component tier** for the seam. **No integration test is required**: nothing
  here constructs a `TradingNode`, and anything that would needs a live gateway, which NFR32 forbids
  automated tests from touching.
- **No automated test connects to a broker** (NFR32); broker-dependent behaviour is operator-verified
  (NFR33) in `docs/qa/phase3-live-verification.md`.
- **TDD is non-negotiable**: Task 1 RED before Task 2, Task 3 RED before Task 4. Record both.
- `--strict-markers` is on; mark every test.
- `make test-coverage` measures `src/core`, so both changed modules are inside the >80% bar.

### Commit hygiene for this repo

- Structural import gate: unused (F401) / undefined (F821) imports hard-block the commit at three
  points. Add an import and its usage in the same edit.
- Stage and commit in **separate** Bash calls; the gate inspects staged files only.
- Commit format `<type>(<scope>): <subject>`; no AI/assistant references in the message.

### References

- Story AC source: [Source: _bmad-output/planning-artifacts/epics.md#Story-1.4:584-609]
- AR14 (Layer 2 contract): [Source: _bmad-output/planning-artifacts/epics.md:196]
- AR39 (ordered startup phases): [Source: _bmad-output/planning-artifacts/epics.md:239]
- AR38 (runner owns the node): [Source: _bmad-output/planning-artifacts/epics.md:238]
- AR28 / exit code 3: [Source: _bmad-output/planning-artifacts/architecture.md#D8:283-289]
- D3 two-layer gate, crossing must match **the connected account**:
  [Source: _bmad-output/planning-artifacts/architecture.md:252-270]
- NFR26 (accounts never logged in full): [Source: _bmad-output/planning-artifacts/epics.md:150]
- NFR32/NFR33 (no broker in automated tests; operator-verified instead):
  [Source: docs/qa/phase3-live-verification.md#What-this-file-is]
- Deferred item this story closes:
  [Source: _bmad-output/implementation-artifacts/deferred-work.md#Deferred-from-code-review-of-story-1.1]
- Naming/vocabulary and doubles location:
  [Source: _bmad-output/planning-artifacts/architecture.md#Implementation-Patterns]
- Nautilus facts verified against the installed 1.220.0 wheel: `live/node.py:374-460`,
  `adapters/interactive_brokers/execution.py:194-234`,
  `adapters/interactive_brokers/client/account.py:41-50,198-206`,
  `adapters/interactive_brokers/factories.py:112-124`, `trading/trader.py:226-236`.

## Dev Agent Record

### Agent Model Used

claude-opus-5 (Claude Code, Claude Agent SDK)

### Debug Log References

**Task 1 RED** — `uv run pytest tests/unit/core/test_live_gate.py -q`

```
ImportError while importing test module '.../tests/unit/core/test_live_gate.py'.
tests/unit/core/test_live_gate.py:15: in <module>
    from src.core.live_gate import (
E   ImportError: cannot import name 'evaluate_account_gate' from 'src.core.live_gate'
1 error in 0.10s
```

**Task 2 GREEN** — same command: `82 passed in 0.35s`. The split was measured, not inferred:
`pytest tests/unit/core/test_live_gate.py --collect-only -q -k "AccountGate"` reports
`26/82 tests collected (56 deselected)` — 56 pre-existing cases, 26 new.

**Task 3 RED** — `uv run pytest tests/component/core/test_live_account_gate.py -q`

```
tests/component/core/test_live_account_gate.py:19: in <module>
    from src.core.live_account_gate import (
E   ModuleNotFoundError: No module named 'src.core.live_account_gate'
1 error in 0.48s
```

**Task 4 GREEN** — same command: `22 passed in 0.93s`.

**Regression + gates**

| Check | Command | Result |
| --- | --- | --- |
| Unit tier | `uv run pytest tests/unit -n auto` | `1587 passed` (baseline **1561 collected** → +26) |
| Component tier | `uv run pytest tests/component -n auto` | `859 passed, 16 skipped` = **875 collected** (baseline **853 collected** → +22) |
| Node lifecycle (regression on the `_refuse` rename) | `uv run pytest tests/integration/core/test_live_node_lifecycle.py --forked` | `2 passed` |
| Lint | `uv run ruff check .` | `All checks passed!` |
| Format | `uv run ruff format --check .` | `396 files already formatted` |
| Types | `make typecheck` | `Success: no issues found in 76 source files` |
| Coverage of the changed modules | `--cov=src.core.live_gate --cov=src.core.live_account_gate` | `live_gate.py 94 stmts, 0 missed, 100%` · `live_account_gate.py 44 stmts, 0 missed, 100%` |
| Dependencies (AC #8) | `git status --porcelain` | `pyproject.toml` and `uv.lock` absent from the diff |

Baselines are **collected** counts on both tiers, taken before any edit
(`pytest --collect-only -q`), to avoid the passed-vs-collected confusion that produced a retracted
finding in Story 1.3's review.

**Task 5/6 — live probe.** `PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py
--run-seconds 3 --verify-account` in this worktree produced:

```
[probe] building node host=127.0.0.1 port=7497 client_id=10 trader_id=PAPER-PROBE0001
RESULT: fail reason=config_error msg=Cannot build an IBKR execution client: TWS_ACCOUNT is not set.
```

That is Story 1.3's guard firing correctly, not a Layer 2 result: this is a detached git worktree
and `.env` is gitignored, so no connection settings exist here (port `7497` is the `IBKRSettings`
default, not the operator's `4002`). Supplying them as one-shot environment variables instead was
declined by the session's command sandbox, and creating `.env` was out of bounds. See the
Completion Notes for what this does and does not leave outstanding.

### Completion Notes List

**What was built.** Layer 2 of the two-layer gate, split along the purity line the existing code
already establishes:

- `src/core/live_gate.py` gains `evaluate_account_gate(settings, cli_flags, reported_accounts)` — a
  pure decision, no new imports, so `TestGatePurity`'s `ALLOWED_RUNTIME_IMPORTS` whitelist
  (`{"dataclasses", "enum", "typing"}`) is untouched. `reported_accounts` is typed `frozenset[str]`
  precisely so no `collections.abc` import is needed to widen it.
- `src/core/live_account_gate.py` is the impure half: it reads what the gateway reported, enforces
  the decision, and stops the node on a refusal.

**The safety property, stated as code rather than as a comment.** `evaluate_account_gate` *starts*
by calling `evaluate_gate` and returns its refusal unchanged. Layer 2 is therefore structurally
Layer-1-AND-more; there is no path on which a clean reported account rescues a configuration Layer 1
refused. Six parametrised cases lock that in
(`TestAccountGateLayerOneDominance`), each asserting the propagated reason *and message* are
identical to Layer 1's.

**Three genuine holes closed, none of them theoretical:**

1. *A gateway that also manages a real-money account.* Layer 1 judges the configured account; the IB
   exec client separately checks that account is one the gateway manages
   (`execution.py:203-213`). Neither notices a second, non-paper account on the same gateway. Layer 2
   requires **every** reported account to be paper.
2. *The crossing was never checked against reality.* Layer 1 compares `NTRADER_REAL_MONEY_ACCOUNT`
   against `TWS_ACCOUNT` — two config values that can agree with each other and disagree with the
   broker. Layer 2 requires the gateway to actually report the authorized account.
3. *A paper-prefixed real-money authorization* (`--real-money` + `NTRADER_REAL_MONEY_ACCOUNT=DU…`)
   previously permitted `mode=REAL_MONEY` against a demo account. Now `REPORTED_ACCOUNT_IS_PAPER`.
   This is the `deferred-work.md` item Story 1.1's review assigned to this story by name; it is
   struck through and marked RESOLVED there.

**Case folding is deliberately asymmetric, and commented as such in the source.** The paper-prefix
test upper-cases (mirroring `_evaluate_paper`, where folding can only *refuse* more); the real-money
identity test does not (Layer 1 compares exactly, and folding there would make more accounts match,
which on that path means permitting more). `test_identity_match_is_case_sensitive_so_folding_cannot_widen_it`
exists so a future reader cannot "tidy" the inconsistency away without a test failing.

**`verify_connected_account` is a coroutine, and that is load-bearing.** Verified against the
installed 1.220.0 wheel: `TradingNode.stop()` merely *schedules* `stop_async` when it finds the loop
running (`live/node.py:374-388`), so "stop then raise" would raise before the node was down; and
`TradingNode.dispose()` busy-waits on a synchronous `time.sleep` that blocks the very loop the stop
task needs (`live/node.py:402-460`) — the deadlock-then-`loop.stop()` failure Story 1.3's probe hit
live on 2026-08-05. So the seam does `await node.stop_async()` and nothing else; disposal and loop
ownership stay with the caller (AR38), documented in the docstring so Epic 2's runner does not
"helpfully" add a `dispose()`.

**AC #6/AR39 ordering is enforced at runtime, not just documented.** With no runner in Epic 1, the
only honest way to satisfy "runs after connection and strictly before any strategy is started" is a
self-checking precondition. `_check_placement` refuses `NODE_NOT_CONNECTED` if the exec engine is
not connected and `STRATEGY_STARTED_BEFORE_ACCOUNT_GATE` if any strategy has left the
not-yet-started states. That set is an **allowlist** (`{"PRE_INITIALIZED", "READY"}`, per the FSM
table at `common/component.pyx:1569-1571`), so a component state Nautilus adds later reads as
*started* and refuses, rather than silently passing the guard. Both guards run before any account is
read — asserted by a double whose `accounts()` raises if touched.

**Deviation from the task text, recorded rather than hidden.** Task 3 wrote the unstarted states as
`"INITIALIZED"/"READY"`; the actual Nautilus member is `PRE_INITIALIZED`. The implementation and
tests use the real names.

**Test counts, every figure measured.** As delivered: `tests/unit/core/test_live_gate.py` 56 → 82
cases (+26, confirmed by `-k "AccountGate"` deselecting exactly the 56 pre-existing ones); unit tier
1561 collected → 1587; component tier 853 collected → 875 (+22). **After code review** the patches
added 10 more: unit tier **1588** collected, component tier **884**. Combined
`tests/unit tests/component tests/api tests/ui` → **2648 passed, 16 skipped** (2638 before the
review patches). Baselines are *collected* counts taken before any edit, never *passed* counts — the
distinction that produced a retracted finding in Story 1.3's review.

**Post-review gate re-run** — `ruff check .` All checks passed · `ruff format --check .` 396 files
already formatted · `make typecheck` Success, 76 source files · coverage `live_gate.py` 97 stmts 0
missed **100%**, `live_account_gate.py` 52 stmts 0 missed **100%** · `test_live_node_lifecycle.py
--forked` 2 passed · `pyproject.toml`/`uv.lock` still absent from the diff.

**⚠️ Task 6 is deliberately left unchecked.** Procedure P2 is fully written into
`docs/qa/phase3-live-verification.md` following P1's exact shape — preconditions, command, expected
output, failure-mode table, pass criteria (including a `grep -c` for the raw account, since NFR26 is
the kind of thing that should be checked rather than trusted). It has **not** been run against a
live gateway: this worktree has no `.env` (gitignored and hook-protected), and the sandbox declined
the invocations that would have supplied the settings another way. Per the QA document's own stated
policy, a dry run is not evidence, so the Result-log row says `⏳ not yet run` and explains why
rather than claiming a pass. This is informational evidence, explicitly non-blocking for the story —
Layer 2's behaviour is covered by 26 unit cases over the decision truth table and 22 component tests
against a node double; what P2 alone can show is that a *real* IBKR paper gateway reports a
`DU`-prefixed account through `managedAccounts` where this code reads it.

**P1 is unaffected.** `--verify-account` is opt-in and defaults to off, so P1's documented command,
printed lines and `RESULT:` line are byte-for-byte unchanged. A note to that effect is in P1's
Result log so nobody re-opens the question. `AccountGateRefused` in the probe deliberately produces
`reason=account_gate_refused`, distinct from Layer 1's `reason=gate_refused`, because an operator
needs to know whether a socket was ever opened.

**Scope held.** No order path, no runner, no CLI, no phase enum, no `src/config.py` change, no
`live_node_builder.py` change, no new dependency, no README change. The only edit outside this
story's own files is the `_refuse` → `build_refusal` rename inside `live_gate.py` (seven call sites,
mechanical), done so the refusal-construction invariant keeps exactly one constructor across both
layers rather than being re-spelled by hand in a second module.

### File List

**New**

- `src/core/live_account_gate.py`
- `tests/component/core/test_live_account_gate.py`
- `_bmad-output/implementation-artifacts/1-4-verify-the-connected-account-is-a-paper-account-before-trading-starts.md`

**Modified**

- `src/core/live_gate.py`
- `tests/unit/core/test_live_gate.py`
- `scripts/diagnostics/live_node_probe.py`
- `docs/qa/phase3-live-verification.md`
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

**Unchanged, by construction**: `pyproject.toml`, `uv.lock`, `src/config.py`,
`src/core/live_node_builder.py`, `src/api/**`, `templates/**`, `alembic/**`, every strategy file,
`.env*`, and the reconnect/kill diagnostic scripts.

## Traceability

Every acceptance criterion below maps to named, currently-passing tests. Re-verified
2026-08-11 against the tree at `09296ec`: 1588 unit passed, 868 component passed (16 skipped,
all pre-existing and unrelated), 18 integration passed (2 skipped — `IBKR_AVAILABLE=1` not set),
`ruff check` clean, `mypy src/core src/services` clean.

| AC | Requirement | Verified by | Tier | Status |
| --- | --- | --- | --- | --- |
| 1 | Every reported account must carry a `DU`/`DF` prefix; one non-paper account refuses the whole connection (FR8, AR14) | `TestAccountGatePaperPath`; `TestVerifyConnectedAccountRefuses::test_non_paper_reported_account_stops_the_node_and_raises`; `TestVerifyConnectedAccountPermits::test_paper_accounts_permit_and_leave_the_node_running` | unit + component | PASS |
| 2 | Layer 1's refusal is returned unchanged — Layer 2 can never permit what Layer 1 refused (FR9) | `TestAccountGateLayerOneDominance` (6 parametrised cases asserting propagated reason *and* message are identical to Layer 1's); `TestVerifyConnectedAccountRefuses::test_layer_one_refusal_reaches_the_caller_unchanged` | unit + component | PASS |
| 3 | A `REAL_MONEY` authorization must appear in the gateway's reported set **and** must not carry a paper prefix (FR10) | `TestAccountGateRealMoneyCrossing::{test_authorized_account_reported_by_the_gateway_permits, test_authorized_account_absent_from_the_reported_set_refuses, test_paper_prefixed_authorization_refuses, test_identity_match_is_case_sensitive_so_folding_cannot_widen_it, test_a_real_money_gateway_may_also_manage_paper_accounts}`; `TestVerifyConnectedAccountRealMoney` | unit + component | PASS |
| 4 | No evidence — node not connected, or gateway named no account — is a refusal, never a permit (fail-closed) | `TestAccountGateFailsClosedWithoutEvidence`; `TestVerifyConnectedAccountRefuses::{test_unconnected_node_refuses_without_reading_any_account, test_gateway_reporting_nothing_refuses, test_a_raising_adapter_refuses_rather_than_escaping, test_a_raising_trader_refuses_rather_than_escaping, test_an_unbuilt_node_is_not_connected_despite_check_connected_saying_so}`; `TestGatewayReportedAccounts::test_no_registered_client_yields_an_empty_set_rather_than_raising` | unit + component | PASS |
| 5 | Any Layer 2 refusal stops the node before returning, starts no strategy, and raises the *same* `GateRefusedError` the static gate raises (FR9, AR14, AR28) | `TestVerifyConnectedAccountRefuses::{test_non_paper_reported_account_stops_the_node_and_raises, test_no_strategy_is_started_on_any_path, test_a_cancelled_shutdown_still_delivers_the_refusal, test_a_hanging_shutdown_does_not_withhold_the_refusal, test_a_failing_shutdown_does_not_mask_the_refusal}` | component | PASS |
| 6 | A strategy already started when verification runs causes a refusal, enforcing AR39's ordering at runtime (AR39) | `TestVerifyConnectedAccountRefuses::test_a_started_strategy_refuses_before_the_account_is_even_read` (5 cases: running, starting, stopping, degraded, unknown-state-fails-closed); `TestNotYetStartedStates::test_the_allowlist_names_real_component_states`; `TestVerifyConnectedAccountPermits::test_added_but_unstarted_strategies_do_not_trip_the_ordering_guard` | component | PASS |
| 7 | Log records carry `phase="gate:account"` and `status="started"\|"ok"\|"failed"` (AR39); every account identifier in a log record *or* refusal message is masked via `mask_account()` (NFR26) | `TestAccountGatePhaseLogging` (6 tests, incl. `test_no_log_record_ever_carries_a_full_account[permit\|refusal]`); `TestAccountGateMasking` (incl. `test_no_layer_two_refusal_leaks_a_full_account`, 4 cases); `TestMaskAccount` | unit + component | PASS |
| 8 | No order-submission path, session runner, CLI command or new dependency; `pyproject.toml` and `uv.lock` byte-identical (AR3) | `git diff 6530b53 76e2c2b -- pyproject.toml uv.lock` returns empty; neither file appears in `76e2c2b`'s diffstat (9 files changed, none of them a manifest) | git | PASS |

**Test files.** `tests/unit/core/test_live_gate.py` (83 collected) and
`tests/component/core/test_live_account_gate.py` (31 collected). Both modules changed by this
story — `src/core/live_gate.py` and `src/core/live_account_gate.py` — are at 100% coverage.

**One item outstanding, non-blocking and unchanged:** Procedure P2 in
`docs/qa/phase3-live-verification.md` is fully documented but has **not** been run against a live
gateway (no `.env` in this worktree; `.env*` is hook-protected). Per that document's own policy its
Result Log records "not yet run" rather than claiming a pass. This is a live-hardware verification
gap, not an unmet acceptance criterion — no AC above depends on it.

## Change Log

| Date | Change |
| --- | --- |
| 2026-08-07 | Story created from `epics.md` Story 1.4, status `ready-for-dev`. |
| 2026-08-07 | Layer 2 implemented TDD Red→Green across two tiers: pure `evaluate_account_gate` in `src/core/live_gate.py` (+4 account refusal reasons, +2 placement reasons, `_refuse` → public `build_refusal`), enforcement seam `src/core/live_account_gate.py`, 26 new unit cases, 22 new component tests, opt-in `--verify-account` probe flag, Procedure P2 documented. Closed the Story 1.1 deferred real-money/paper-prefix finding. Status → `review`, with Task 6's live run outstanding and recorded as non-blocking. |
| 2026-08-11 | Added the `## Traceability` section: an explicit AC → test mapping for all 8 ACs, re-verified against the tree at `09296ec`. Documentation only — no source, test or configuration change. |
| 2026-08-09 | Code-reviewed by three adversarial layers → 0 decision-needed, 17 patches applied, 8 deferred, 4 dismissed. All 8 ACs confirmed satisfied. Patches closed four fail-open paths inside satisfied ACs — an unguarded third-party attribute chain that escaped with the node still up, a vacuously-`True` connection check on an unbuilt node, `CancelledError` aborting before the refusal was raised, and an unbounded post-refusal `stop_async()` — plus an inverted case-folding rationale, an unstripped/undeduplicated account rendering, third-party exception text reaching a log record, AR41/AR36 naming, and four tests that asserted less than they claimed. Added `ACCOUNT_VERIFICATION_ERROR` and `normalize_reported_accounts()`. Status → `done`. |
