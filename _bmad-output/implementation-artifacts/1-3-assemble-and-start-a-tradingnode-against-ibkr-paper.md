# Story 1.3: Assemble and Start a TradingNode Against IBKR Paper

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a Nautilus `TradingNode` configured for IBKR that starts cleanly inside this codebase,
so that live trading runs on the same framework as backtesting rather than a second execution path.

## Acceptance Criteria

1. **Given** `src/core/live_node_builder.py`, **When** it builds a node, **Then** it produces a
   `TradingNodeConfig` carrying an `InteractiveBrokersDataClientConfig` and an
   `InteractiveBrokersExecClientConfig`, and the node has
   `InteractiveBrokersLiveDataClientFactory` and `InteractiveBrokersLiveExecClientFactory`
   registered under the adapter's own client key (FR2, AR2).
2. **Given** the builder is invoked, **When** it begins assembling client configuration, **Then**
   it calls `evaluate_gate()` **first** and raises `GateRefusedError` carrying the `GateRefusal`
   without constructing **any** client config — proving no connecting code is reachable past a
   failed gate, asserted by ordering, not only by outcome (FR9).
3. **Given** the gate has permitted the connection, **When** the node is configured, **Then**
   `ibkr_read_only` is `False` on the in-process settings view the exec client config is built
   from — **And** the caller's own settings object is left unmutated, nothing is written back to
   `.env` or `os.environ`, and `ibkr_read_only` is never read as a condition anywhere in the module
   (FR12, NFR27, AR17, AR43).
4. **Given** the node initialises Nautilus logging, **When** it starts, **Then** its log guard is
   registered through the existing `set_nautilus_log_guard()` (`src/utils/logging.py:117`) before
   any other Nautilus component initialises in-process (FR7, AR20) — **And** an integration test
   under `--forked` constructs a node in a process that has already run a `BacktestEngine` and
   observes no C-logging double-init panic.
5. **Given** the node's client ID, **When** the data and execution client configs are built,
   **Then** both carry `ibkr_live_client_id`, not `ibkr_client_id` (FR5).
6. **Given** the node is stopped, **When** shutdown completes, **Then** the process exits without
   leaving a running event loop or an unclosed connection, and a second node can be built in a
   fresh process without inheriting `BacktestEngine`'s single-use constraint — verified by an
   operator-run procedure recorded in `docs/qa/phase3-live-verification.md`, since this behaviour
   cannot be exercised without a live gateway (NFR33, AR22).
7. **Given** the whole epic, **When** dependency files are reviewed, **Then** no new dependency has
   been added — `pyproject.toml` and `uv.lock` are byte-identical, since the IB live adapter already
   ships with the installed `nautilus-trader` 1.220.0 (AR3).

## Tasks / Subtasks

- [x] **Task 1: Write the failing component tests for config assembly (TDD Red)** (AC: #1, #2, #3, #5)
  - [x] New file `tests/component/core/test_live_node_builder.py`. **Component tier, not unit** —
        the module imports `nautilus_trader.config`, which the `unit` tier is defined to exclude
        (`pytest.ini:25`: "Pure Python unit tests (no Nautilus)"). `tests/component/core/` already
        exists and holds only `__init__.py`; this is its first test file.
  - [x] Mark every test `@pytest.mark.component`. `--strict-markers` is on (`pytest.ini:15`), so an
        unregistered marker is a hard error.
  - [x] **Verified safe for the parallel, non-forked component tier:** assembling
        `TradingNodeConfig` + both IB client configs does **not** touch the Nautilus C logging
        subsystem. Confirmed empirically — `is_logging_initialized()` is still `False` after
        building the full config. Anything that constructs a `TradingNode` must go in Task 3's
        integration tier instead.
  - [x] Build settings the way `tests/unit/core/test_live_gate.py:26-46` does: a `_settings(...)`
        helper passing `_env_file=None` **and** every field the code under test reads, as init
        kwargs. Init kwargs outrank the environment in pydantic-settings, so the developer's shell
        cannot become test input. Fields this module reads: `ibkr_trading_mode`, `ibkr_port`,
        `ibkr_host`, `tws_account`, `ntrader_real_money_account`, `ibkr_live_client_id`,
        `ibkr_client_id`, `ibkr_read_only`, `ibkr_connection_timeout`, `ibkr_request_timeout`.
  - [x] **AC #1 — shape.** A permitted build returns a `TradingNodeConfig` whose `data_clients` and
        `exec_clients` each hold exactly one entry keyed by
        `nautilus_trader.adapters.interactive_brokers.common.IB` (`"INTERACTIVE_BROKERS"`), of type
        `InteractiveBrokersDataClientConfig` / `InteractiveBrokersExecClientConfig`.
  - [x] **AC #5 — client ID.** With `ibkr_client_id=1, ibkr_live_client_id=10`, assert
        `cfg.data_clients[IB].ibg_client_id == 10` **and** `cfg.exec_clients[IB].ibg_client_id == 10`
        — and add a negative assertion that neither equals `ibkr_client_id`. Repeat with a
        non-default pair (e.g. `2` / `20`) so the test proves the wiring, not the default.
  - [x] **AC #2 — gate ordering, not just gate outcome.** Two tests:
        1. A refusing configuration (e.g. `ibkr_port=7496`) raises `GateRefusedError`, and the
           raised exception exposes the `GateRefusal` — assert
           `exc.value.refusal.reason is GateRefusalReason.NON_PAPER_PORT` and that the operator
           message is carried through verbatim.
        2. **No client config was constructed.** `monkeypatch.setattr` the two config classes *in
           the builder's own module namespace* with a sentinel that raises if called, then assert
           the refusal path still raises `GateRefusedError` — not the sentinel's error. This is the
           only test that actually proves the ordering AC #2 asks for; asserting "it raised" alone
           passes even if the configs were built first and thrown away.
  - [x] **AC #3 — three assertions, no fourth.**
        1. A permitted build with `ibkr_read_only=True` on the input still yields an exec client
           config — the gate is the control, not this flag (AR43).
        2. After the call, the caller's `settings.ibkr_read_only` is still `True` — the `False` is
           an in-process derived view, never a mutation of shared state.
        3. Source-level: parse the module with `ast` and assert `ibkr_read_only` appears exactly
           once and never inside an `if` / `while` / boolean test.
           `tests/unit/core/test_live_gate.py:373-500` is the in-repo precedent for an AST-based
           structural assertion, including its own "the guard must be able to fail" meta-test —
           follow that shape, and include the meta-test.
  - [x] **Missing account.** `tws_account=""` passes the gate (Story 1.1 permits an empty account on
        the paper path) but cannot produce a usable exec client — see the trap in Dev Notes. Assert
        it raises `LiveNodeConfigError` naming `TWS_ACCOUNT`, and that the message contains only the
        masked form of any account it echoes (NFR26).
  - [x] Run `uv run pytest tests/component/core/test_live_node_builder.py -v` and **record the RED
        output** in Debug Log References before writing Task 2.
- [x] **Task 2: Implement the config assembly** (AC: #1, #2, #3, #5)
  - [x] New file `src/core/live_node_builder.py`. Module docstring states what it owns (config
        assembly + node construction) and what it does not (the runner owns lifecycle — AR38).
  - [x] Two module-local exceptions, matching the house pattern of module-local exception classes
        (`src/services/exceptions.py`, `src/services/results_store.py`):
        ```python
        class GateRefusedError(Exception):
            """The safety gate refused the connection; no client config was constructed."""
            def __init__(self, refusal: GateRefusal) -> None:
                super().__init__(refusal.message)
                self.refusal = refusal

        class LiveNodeConfigError(Exception):
            """Configuration cannot produce a usable node."""
        ```
        `GateRefusedError` is the seam Story 1.7 maps to exit code **3** (AR28) — it must stay
        distinguishable from every other failure, so do **not** collapse the two into one class.
  - [x] Public API — settings are **injected, never fetched**. Do not call `get_settings()` inside
        this module; `evaluate_gate(settings, cli_flags)` already established the convention and it
        is what makes the module testable:
        ```python
        def build_trading_node_config(
            settings: IBKRSettings,
            *,
            trader_id: str,
            cli_flags: GateFlags | None = None,
        ) -> TradingNodeConfig: ...

        def build_trading_node(
            settings: IBKRSettings,
            *,
            trader_id: str,
            cli_flags: GateFlags | None = None,
        ) -> TradingNode: ...
        ```
        `trader_id` is a required parameter with no default and no derivation logic — Epic 2 owns
        deriving `PAPER-<short-session-id>` from the session (AR10). Do not invent it here.
  - [x] **Order of operations inside `build_trading_node_config` — this ordering is AC #2:**
        1. `decision = evaluate_gate(settings, cli_flags or GateFlags())`
        2. `if not decision.permitted: raise GateRefusedError(decision.refusal)`
        3. resolve the account, raising `LiveNodeConfigError` if empty
        4. derive the in-process settings view (`ibkr_read_only=False`)
        5. only now construct the two client configs and the `TradingNodeConfig`
  - [x] **`ibkr_read_only`** — one line, used, never read:
        ```python
        trading_settings = settings.model_copy(update={"ibkr_read_only": False})
        ```
        Build the exec client config from `trading_settings`. `model_copy` skips validation (fine —
        no field changed that the validator inspects) and leaves the caller's object untouched.
        **Do not** add an enforcement mechanism around this flag and **do not** branch on it: see
        "The `ibkr_read_only` truth" in Dev Notes for why there is nothing to enforce against.
  - [x] **Client config fields to set explicitly** (defaults verified against the installed 1.220.0
        wheel — everything not listed here stays at its default):
        | Field | Value | Why explicit |
        |---|---|---|
        | `ibg_host` | `settings.ibkr_host` | adapter default `"127.0.0.1"` happens to match, but the setting is the contract |
        | `ibg_port` | `settings.ibkr_port` | **adapter default is `None`** and `get_cached_ib_client` raises on a `None` port |
        | `ibg_client_id` | `settings.ibkr_live_client_id` | AC #5 |
        | `connection_timeout` | `settings.ibkr_connection_timeout` | typed settings own this, not the adapter |
        | `request_timeout` | `settings.ibkr_request_timeout` | data client only |
        | `account_id` (exec only) | resolved account | see the `TWS_ACCOUNT` trap below |
  - [x] **Leave `TradingNodeConfig.environment` at its default `Environment.LIVE`.** Nautilus's
        `Environment` (`BACKTEST` / `SANDBOX` / `LIVE`) describes the *execution machinery*, not the
        broker account. `SANDBOX` means Nautilus simulates fills locally — picking it because "we
        are paper trading" would silently replace IBKR's execution with a simulator and destroy the
        one thing this phase exists to measure. Paper trading runs in `LIVE`. Do not set this field.
  - [x] Leave `dockerized_gateway` at `None`. The process model is an operator-run TWS/Gateway
        (D5); passing a `DockerizedIBGatewayConfig` makes Nautilus manage a Docker container and
        forces `ibg_port=None`, which is a different architecture than the one that was decided.
  - [x] Log one event on success at debug: dotted lowercase past-tense per AR41, e.g.
        `logger.debug("live_node.configured", client_id=..., account=mask_account(account), trader_id=...)`.
        **Reuse `mask_account` from `src/core/live_gate.py:94`** — do not write a second masking
        helper (NFR26). Never log host+port+account together at INFO.
  - [x] `structlog.get_logger(__name__)` at module scope, per project convention.
  - [x] Green: the Task 1 tests pass. Keep the module under the 500-line file limit and every
        function under 50 lines (it should land near 120 lines total).
- [x] **Task 3: Write the failing integration test for node construction + LogGuard (TDD Red)** (AC: #4)
  - [x] New file `tests/integration/core/test_live_node_lifecycle.py`, marked
        `@pytest.mark.integration`. `tests/integration/core/` already exists with an `__init__.py`.
  - [x] **`--forked` is mandatory here and is not a formality.** Two independent reasons: the
        Nautilus C logging subsystem is process-global and single-init, and the IB adapter keeps
        **module-level global caches** — `IB_CLIENTS`, `IB_INSTRUMENT_PROVIDERS`, `GATEWAYS` in
        `nautilus_trader/adapters/interactive_brokers/factories.py:41-43` — which would leak between
        tests in a shared process. Run via `make test-integration`.
  - [x] **Test A — the AC #4 coexistence test.** In one forked process:
        ```python
        engine = BacktestEngine(config=BacktestEngineConfig(
            trader_id="BACKTESTER-001", logging=LoggingConfig(log_level="ERROR")))
        assert is_logging_initialized()          # the engine claimed the subsystem
        node = build_trading_node(settings, trader_id="PAPER-a1b2c3d4")
        assert node is not None                  # no panic, no abort
        ```
        **Verified behaviour to assert, not guess:** `NautilusKernel` guards its own init with
        `if not is_logging_initialized():` (`nautilus_trader/system/kernel.py:190`), so the second
        component builds fine and `node.kernel.get_log_guard()` returns **`None`**. Assert that
        explicitly — a test that asserts a non-None guard here will fail for the right reason and
        be "fixed" the wrong way.
  - [x] **Test B — node-first.** In a separate forked test, build the node in a clean process and
        assert `node.kernel.get_log_guard()` is **not** None and that
        `get_nautilus_log_guard()` (`src/utils/logging.py:133`) now returns that same object —
        this is the half of AC #4 that proves registration actually happened.
  - [x] **Do not call `node.build()` or `node.run()` in any automated test.** `build()` runs the
        factories, and `get_cached_ib_client` calls `client.start()`, which opens a socket to the
        gateway. Automated tests never touch a real broker (NFR32). Connection behaviour is Task 5's
        operator procedure.
  - [x] `TradingNodeConfig` construction detail worth knowing before you hit it:
        `LoggingConfig(bypass_logging=True)` is **rejected** in a LIVE environment with
        `InvalidConfiguration: was set True when not safe to bypass logging in a LIVE context`.
        Use `log_level="ERROR"` to keep test output quiet instead.
  - [x] `TraderId` requires a `-` in the value. An invalid value **panics in Rust and aborts the
        process** rather than raising a Python exception — a malformed `trader_id` will read as a
        mysterious crash, not a test failure. Use `"PAPER-a1b2c3d4"` in tests.
  - [x] Record the RED output before Task 4.
- [x] **Task 4: Implement node construction and LogGuard registration** (AC: #4)
  - [x] `build_trading_node()`:
        1. `config = build_trading_node_config(...)` — the gate runs inside it, so a refusal still
           raises before a node exists
        2. `node = TradingNode(config=config)` — this is the call that may initialise C logging
        3. **immediately** register the guard, before returning and before any other Nautilus
           component can be constructed:
           ```python
           guard = node.kernel.get_log_guard()
           if guard is not None:
               set_nautilus_log_guard(guard)
           ```
           The `is not None` check is required: when a `BacktestEngine` already owns the subsystem
           the kernel returns `None`, and storing `None` is meaningless.
           `set_nautilus_log_guard` is already first-write-wins (`src/utils/logging.py:129`), so
           calling it never clobbers an existing guard.
        4. `node.add_data_client_factory(IB, InteractiveBrokersLiveDataClientFactory)` and
           `node.add_exec_client_factory(IB, InteractiveBrokersLiveExecClientFactory)`
        5. return the node — **not** built, **not** running (AR38: the runner owns the lifecycle)
  - [x] The factory `name` must match the `data_clients` / `exec_clients` dict key. Nautilus splits
        the key on `-` (`nautilus_trader/live/node_builder.py:162`), so use the adapter's own `IB`
        constant on both sides rather than a hand-typed string.
  - [x] `src/api/web.py:22-23` is the in-repo precedent for "init Nautilus logging, then hand the
        guard to `set_nautilus_log_guard`". `src/services/ibkr_client.py:145-166` is the precedent
        for preserving a guard a Nautilus component handed back. Follow them; do not call
        `init_logging()` directly and never construct `Logger`/`LiveLogger` yourself (CLAUDE.md
        Gotcha #1).
- [x] **Task 5: Operator verification procedure** (AC: #6)
  - [x] New `scripts/diagnostics/live_node_probe.py`, following the shape of the existing
        `scripts/diagnostics/ibkr_reconnect_probe.py` (module docstring with a Usage block, argparse,
        a single parseable `RESULT: ok|fail ...` line on stdout). It builds a node via
        `build_trading_node()`, calls `node.build()`, runs it briefly, stops and disposes it, and
        reports. **This is a diagnostic, not the runner** — say so in its docstring so nobody
        mistakes it for `live_session_runner.py` (Epic 2, Story 2.5).
  - [x] Shutdown sequence to exercise: `node.stop()` → `node.dispose()`. `dispose()` waits up to
        `timeout_disconnection` (default 10s) for the kernel to stop, then tears down the loop.
  - [x] New `docs/qa/phase3-live-verification.md` — the phase-gate evidence file named by AR22.
        Create it with a short preamble (what this file is, why these procedures cannot live in CI —
        NFR33) and **Procedure P1: build, start and stop a node against IBKR paper**, covering:
        preconditions (Gateway or TWS running on the configured paper port, `.env` set), the exact
        command, expected output, and the two pass criteria of AC #6 — the process exits with no
        lingering event loop or socket, and a second invocation in a fresh process succeeds.
        Later stories append P2, P3, … to this same file.
  - [x] `docs/qa/` already exists (it holds the Phase 1/2 test-plan documents) — add the file, do
        not create a parallel directory.
  - [x] Running P1 requires a live gateway. If one is not available in this session, leave P1's
        result unrecorded and say so plainly in Completion Notes — do **not** mark AC #6 verified
        from a dry run.
- [x] **Task 6: Verify and record** (AC: #7 and the repo gates)
  - [x] `make test-component` — expect **815 + N** passing (815 is today's collected baseline).
  - [x] `make test-integration` — expect **169 + N** passing. Note there are pre-existing failures
        in this tier from before Phase 3; compare against a baseline run rather than assuming zero.
        **⚠️ Annotated by code review 2026-08-05 (Decision 2):** this box is checked, but
        `make test-integration` as literally invoked (`Makefile:59`) shows this story's 2 tests
        crashing with signal 5. Cause is the pre-existing `src/api/web.py:22` import-time
        `init_logging()` hazard, reproduced independently during review; both tests pass under
        `--forked` in every combination that excludes `test_trades_api.py`. Ruled acceptable — but
        the box must not sit silently over a red suite, hence this note. 169/815 confirmed correct
        as **collected** baselines (826 − 11 = 815; 171 − 2 = 169).
  - [x] `make test-unit` — expect **1561** unchanged. This story adds no unit-tier test.
  - [x] `make format && make lint && make typecheck`. `make typecheck` covers `src/core` and
        `src/services`, so `live_node_builder.py` **is** type-checked — annotate every signature.
  - [x] **AC #7:** `git diff --stat pyproject.toml uv.lock` must be empty. Nothing here needs a new
        dependency. Never hand-edit `pyproject.toml`; a bash-guard hook blocks it and `uv add` is
        the only sanctioned path.
  - [x] Append any non-blocking findings to `_bmad-output/implementation-artifacts/deferred-work.md`
        under a new `## Deferred from: story-1.3` heading — including the `READ_ONLY_API` item
        described in Dev Notes, which Epic 3 needs to know about.
  - [x] Fill in Dev Agent Record: Debug Log References (RED evidence for Tasks 1 and 3, plus the
        verification table), Completion Notes List, File List.

### Review Findings

Code review 2026-08-05. Three adversarial layers (Blind Hunter — diff only, no spec, no project
access; Edge Case Hunter — diff + project + installed wheel; Acceptance Auditor — diff + spec).
48 raw findings → 3 decisions / 23 patches / 7 deferred / 5 dismissed after dedup and triage.

**Decisions — all three ruled by Allay 2026-08-05; each became a patch:**

1. **`trader_id` validation → guard it here.** Validate the format before handing it to Nautilus
   and raise `LiveNodeConfigError`, mirroring the existing `TWS_ACCOUNT` guard. Rationale: AR10
   gives Epic 2 ownership of *deriving* trader_id; validating a value this function is handed is
   not derivation, and this module already exists to turn framework traps into legible errors.
2. **AC #4 → accept as met, annotate.** The evidence is real (both tests pass under `--forked` in
   every combination excluding `test_trades_api.py`) and the root cause is a documented
   pre-existing `src/api/web.py` import-time side effect, not a defect in this story. Task 6's
   `make test-integration` checkbox is to be annotated so a checked box never sits silently over a
   red suite. The prior "do not touch `src/api/**`" ruling stands.
3. **Event loop → accept and forward a `loop` param.** Add `loop: AbstractEventLoop | None = None`
   and pass it through to `TradingNode`. No lifecycle logic — the caller still owns
   build/run/stop, so AR38 holds — but the dependency becomes explicit before Epic 2's runner is
   written against an implicit one.

**Resolved decisions, now patches:**

- [x] [Review][Decision] `trader_id` is unvalidated and a malformed value aborts the whole process
      — `TradingNodeConfig(trader_id=...)` eagerly coerces to `TraderId`, which panics in Rust
      (`crates/model/src/identifiers/trader_id.rs:64`) when the string lacks a `-`. Verified by
      execution: exit code 134 (SIGABRT). The panic is **not** catchable — `try/except
      BaseException` does not stop it. Reachable from the component tier, so one bad
      parametrisation kills a pytest worker rather than failing a test. `trader_id=""` separately
      raises a bare `ValueError`, which is neither `GateRefusedError` nor `LiveNodeConfigError`
      and so contradicts the documented `Raises:` contract. The story's Dev Notes flagged this
      trap *for test authors* ("use `PAPER-a1b2c3d4` in tests") but never decided whether
      production should guard. AR10 gives Epic 2 ownership of *deriving* trader_id — validating a
      value it is handed is arguably not derivation. **Options:** (a) validate the format here and
      raise `LiveNodeConfigError`, mirroring the existing `TWS_ACCOUNT` guard; (b) leave it to
      Epic 2's runner and accept the abort risk in Epic 1; (c) accept as-is and document.
      [src/core/live_node_builder.py:114]
- [x] [Review][Decision] AC #4's required `--forked` evidence is red under `make test-integration`
      as the repo actually invokes it — independently reproduced: `pytest
      tests/integration/api/test_trades_api.py tests/integration/core/test_live_node_lifecycle.py
      --forked -n auto` → 2 failed (signal 5), 20 passed; `Makefile:59` is exactly that
      invocation. Note Allay **already ruled** on the narrow fix-vs-document question (documented
      in sprint-status: fixing `src/api/web.py` is off-limits for this story). The remaining open
      question is different and is a status question, not a code question: AC #4 demands "an
      integration test **under `--forked`** … observes no double-init panic", and that evidence
      only holds under a non-standard file-subsetted run, while Task 6's `make test-integration`
      box is checked and the story claims AC #4 met. **Options:** (a) accept AC #4 as met on
      subset evidence and annotate the checkbox; (b) mark AC #4 partial and hold the story at
      `in-progress` until `src/api/web.py` is fixed in its own story; (c) reopen the web.py fix
      now. [tests/integration/core/test_live_node_lifecycle.py]
- [x] [Review][Decision] `build_trading_node` silently binds the node to an ambient or
      auto-created event loop — `TradingNode.__init__` does `loop = loop or
      asyncio.get_event_loop()` (`live/node.py:63`) and `build_trading_node` neither accepts nor
      forwards a `loop`. Verified: called from plain sync code with no loop set, it manufactures
      an orphan loop nobody will run; the kernel, its executor, signal handlers, and
      `stop()`/`dispose()` all bind to it, so `node.stop()` later calls `run_until_complete` on
      the wrong loop. From a non-main thread it raises `RuntimeError` instead. Neither precondition
      is documented. AR38 gives the runner lifecycle ownership, which is why this is a ruling and
      not a patch. **Options:** (a) accept `loop: AbstractEventLoop | None = None` and forward it;
      (b) assert a current loop exists and document the precondition; (c) leave entirely to Epic
      2's runner. [src/core/live_node_builder.py:140]

**Patches (fix is unambiguous):**

- [x] [Review][Patch] `sprint-status.yaml` contradicts three other documents on whether AC #6 was
      verified live — it still says "AC #6 partially unverified — no live IBKR Gateway was
      available this session" while the story file, `docs/qa/phase3-live-verification.md` Result
      Log, and `deferred-work.md` all record a live pass on 2026-08-05. The tracker is the system
      of record and currently asserts the opposite of the truth.
      [_bmad-output/implementation-artifacts/sprint-status.yaml:58]
- [x] [Review][Patch] The "stale baselines" entry in `deferred-work.md` is itself factually wrong
      and its action item would corrupt correct documentation — it compares *passed* counts
      against *collected* baselines. Independently re-verified this review: `tests/component` =
      826 collected − 11 new = **815**; `tests/integration` = 171 collected − 2 new = **169** —
      exactly the figures the story's Task 6 gave and explicitly labelled "today's collected
      baseline". 799 is the *passed* count (799 + 16 skipped = 815). The same false claim is
      propagated into `sprint-status.yaml`. Both must be retracted.
      [_bmad-output/implementation-artifacts/deferred-work.md:800]
- [x] [Review][Patch] The probe prints `RESULT: ok` without ever verifying the node connected —
      `kernel.start_async()` does not raise on connection failure (`system/kernel.py:1012-1013`
      logs a warning and returns), and `run_async` catches only `CancelledError`, so a node that
      never reached the gateway still yields exit 0. Compounding it: `timeout_connection` defaults
      to **60.0s** while the probe sleeps **5s**, so it always tears down before the connection
      outcome is even determined. `docs/qa/phase3-live-verification.md` cites that `RESULT: ok`
      line as AC #6 evidence. Add a connectivity check before declaring ok, and default
      `--run-seconds` above `timeout_connection`. [scripts/diagnostics/live_node_probe.py:84]
- [x] [Review][Patch] `_run()` has no `try/finally` — any failure between `node.build()` and
      `node.dispose()` skips dispose entirely: the socket stays open, `IB_CLIENTS` keeps a started
      client, the run task is left pending, the loop is never closed, and the kernel's
      `ThreadPoolExecutor` (`system/kernel.py:265-272`, only shut down inside `dispose()`) keeps
      non-daemon threads that the interpreter joins at exit — so the process can hang, directly
      contradicting the QA doc's "shell prompt returns immediately" pass criterion. Note line 93
      (`run_until_complete(run_task)`) sits *outside* the `except asyncio.CancelledError`, so a
      real `run_async` failure escapes there. [scripts/diagnostics/live_node_probe.py:69]
- [x] [Review][Patch] `test_node_first_registers_its_own_log_guard` fails whenever it is not first
      in its process — reproduced: plain `uv run pytest
      tests/integration/core/test_live_node_lifecycle.py` → **1 failed, 1 passed**, dying on
      `assert None is not None`. Its comment asserts a precondition ("a clean process, nothing has
      initialised logging yet") that it never arranges or checks, and it also depends on the
      module-global guard still being `None` (first-write-wins). Skip or assert on
      `is_logging_initialized()` up front. Both tests' cleanup also sits outside `try/finally`.
      [tests/integration/core/test_live_node_lifecycle.py:88]
- [x] [Review][Patch] Factory registration has zero automated coverage on any tier, and Nautilus
      fails a missing registration **silently** — `node_builder.py:230-232` logs an error and
      `continue`s rather than raising. Omitting either line, or transposing the data and exec
      factories (near-identical adjacent lines — textbook copy-paste target), leaves every test in
      this story green; the failure surfaces only in a live probe run whose success signal is
      itself unreliable. [src/core/live_node_builder.py:149]
- [x] [Review][Patch] The `if guard is not None:` branch is untested — the coexistence test never
      reads `get_nautilus_log_guard()` at all, so the engine-first path has no assertion on the
      module-global. **Corrected during triage:** the Blind Hunter's stated consequence (that
      removing the branch would overwrite a real guard with `None`) is **wrong** —
      `set_nautilus_log_guard` is first-write-wins (`src/utils/logging.py:128-130`, verified), so a
      `None` write is a no-op and cannot clobber anything. The branch is belt-and-braces, not
      load-bearing. The patch stands on the weaker but real ground: the test should observe the
      global so the engine-first path is actually covered.
      [src/core/live_node_builder.py:145]
- [x] [Review][Patch] A lowercase `TWS_ACCOUNT` passes the gate but is passed through
      un-normalised — `live_gate.py:214` compares `account.upper().startswith(...)`, so
      `du4076626` is permitted; the module strips but does not upper-case, so
      `account_id="du4076626"` reaches the factory and becomes
      `AccountId("InteractiveBrokers-du4076626")`. IBKR reports account IDs uppercase, so Story
      1.4's Layer-2 verification and every downstream `AccountId` lookup will mismatch on a
      configuration the gate declared safe. Verified by execution.
      [src/core/live_node_builder.py:79]
- [x] [Review][Patch] `ibkr_connection_timeout` / `ibkr_request_timeout` accept `0` and negative
      values and flow straight into both live clients — neither field carries a `ge=` constraint
      (unlike `ibkr_client_id`, which does). Nautilus feeds them to `asyncio.wait_for`, where a
      non-positive timeout means "expire immediately", so `IBKR_CONNECTION_TIMEOUT=0` silently
      guarantees the session can never connect and surfaces as an opaque timeout rather than a
      config error. This module is the first consumer to hand these to a live client. Verified by
      execution. [src/core/live_node_builder.py:96]
- [x] [Review][Patch] `--run-seconds 0` or a negative value produces `RESULT: ok` from a run that
      never ran — `argparse` applies `type=int` with no range check, and `asyncio.sleep(0)` /
      `sleep(-5)` both return in ~0.0s (verified), so the node barely reaches its first `await`
      before being stopped, yet the probe emits the same evidence line an operator would paste
      into the QA log. [scripts/diagnostics/live_node_probe.py:104]
- [x] [Review][Patch] Ctrl-C during the documented run window is swallowed and still reports
      `RESULT: ok` — the kernel installs a loop-level SIGINT handler and sets `signal.signal(SIGINT,
      SIG_DFL)` (`system/kernel.py:549-561`), so no `KeyboardInterrupt` is raised; the handler
      schedules `stop_async()` and the probe then calls `node.stop()` a second time concurrently,
      orphaning the first task. Outside the loop-running windows a raw `KeyboardInterrupt` escapes
      `except Exception` entirely: no `RESULT:` line, no dispose, and `client_id=10` left reserved
      on the Gateway — the exact stale-ID condition the QA doc's own preconditions warn about.
      [scripts/diagnostics/live_node_probe.py:87]
- [x] [Review][Patch] The component file's module docstring justifies the entire non-forked,
      parallel tier placement on `is_logging_initialized()` staying `False`, but no test asserts
      it — the claim was verified once by hand and left unguarded. A Nautilus upgrade or a new
      import that initialises C logging at config-assembly time would turn the parallel component
      suite into intermittent native crashes with nothing pointing here. An autouse fixture
      asserting it before and after each test costs one function.
      [tests/component/core/test_live_node_builder.py:1]
- [x] [Review][Patch] Missing config-field assertions — nothing asserts `cfg.trader_id`,
      `exec_clients[IB].account_id`, or either timeout actually lands on the built config, so
      swapping `connection_timeout` and `request_timeout` keeps the suite green. The `strip()` on
      the account is also unasserted. Relatedly,
      `test_permitted_build_yields_exec_client_even_when_input_read_only_is_true` asserts only
      `is not None`, which holds for every possible input including one where read-only handling
      was removed entirely — `TestConfigShape` already covers that much.
      [tests/component/core/test_live_node_builder.py:149]
- [x] [Review][Patch] The integration `_settings()` helper omits `ibkr_read_only`,
      `ibkr_connection_timeout` and `ibkr_request_timeout`, reintroducing the exact
      "developer's shell becomes test input" hazard the component helper documents and mitigates —
      `_env_file=None` disables only the dotenv *file*; `os.environ` remains an active
      pydantic-settings source. [tests/integration/core/test_live_node_lifecycle.py:38]
- [x] [Review][Patch] The AST anti-pattern guard has holes — `_is_inside_boolean_test` walks only
      `ast.If`/`ast.While` tests plus `ast.BoolOp`/`ast.IfExp`, so a branch via `assert
      settings.ibkr_read_only`, `match`, or a comprehension `if` passes undetected (all three
      confirmed MISSED by executing the helpers against mutated sources). It also over-reports on
      `ast.IfExp` by marking the whole expression including body and `orelse`. Separately,
      `_ibkr_read_only_occurrences` cannot see `ast.keyword.arg`, so rewriting the line as
      `model_copy(update=dict(ibkr_read_only=False))` yields **zero** occurrences and fails the
      `== 1` assertion for the opposite reason. Exposure is narrow today because the count
      assertion catches any *added* reference — only replacing the sole existing occurrence slips
      through — but Dev Notes claims this "makes that mechanical", which overstates it.
      [tests/component/core/test_live_node_builder.py:186]
- [x] [Review][Patch] The gate-ordering test has no positive control — its passing condition is
      "`GateRefusedError` was raised", identical to the weaker test its own docstring disparages.
      The sentinels add value only if they are the objects production actually calls (they are
      today — verified). Move the imports inline or alias them and the test goes green while
      proving nothing. Add the inverse assertion: with sentinels installed and *permitted*
      settings, `AssertionError("client config constructed…")` must fire. The author supplied
      exactly this meta-check for the AST guard but not here.
      [tests/component/core/test_live_node_builder.py:127]
- [x] [Review][Patch] `Path(live_node_builder.__file__).read_text()` has no `encoding=` argument
      and the target file is full of em dashes — on any platform or CI image whose locale encoding
      is not UTF-8 this decodes to mojibake or raises `UnicodeDecodeError`, failing the test for a
      reason unrelated to its assertion. [tests/component/core/test_live_node_builder.py:208]
- [x] [Review][Patch] `cli_flags or GateFlags()` should be `if cli_flags is None` — the annotation
      declares `None` as the sentinel, and `or` additionally swallows any falsy instance. Harmless
      today, but if `GateFlags` ever gains `__bool__`/`__len__` or becomes an empty-tuple-like
      type, an operator's explicit all-defaults declarations are silently replaced on the input
      path to a live-trading safety gate, with no log line.
      [src/core/live_node_builder.py:74]
- [x] [Review][Patch] The probe's `load_dotenv()` runs at **import** time, mutating `os.environ`
      process-wide for anything that imports the module, and it resolves `.env` by walking up from
      the calling file while pydantic's `env_file=".env"` resolves relative to the **cwd** — run
      from another directory the two silently disagree about which file supplies config. It also
      breaks the very premise the `TWS_ACCOUNT` guard rests on ("`.env` never reaches
      `os.environ`", which is what makes the factory's `os.environ.get("TWS_ACCOUNT")` fallback
      unreachable). The module also binds an unused `structlog` logger while every output is a
      bare `print`. [scripts/diagnostics/live_node_probe.py:35]
- [x] [Review][Patch] The probe's catch-all discards the traceback — the payload of a *diagnostic*
      tool — collapsing an arbitrary Nautilus/IB adapter failure to `reason=RuntimeError
      msg=<one line>` and deleting the frame that would say whether the fault was in
      `build_trading_node`, `node.build()`, or the adapter. `str(e)` on an adapter exception can
      also carry host, port and account straight to stdout, while the builder deliberately routes
      the same account through `mask_account()`. Print `traceback.format_exc()` to stderr
      alongside the parseable line. [scripts/diagnostics/live_node_probe.py:130]
- [x] [Review][Patch] `assert decision.refusal is not None` is stripped under `python -O`,
      degrading a safety-gate refusal into `AttributeError: 'NoneType' object has no attribute
      'message'` — and callers with a dedicated `except GateRefusedError` arm (the probe has one)
      would misreport it as a generic failure. The invariant itself was verified sound on every
      path through `evaluate_gate` (`_refuse()` is the only refusal producer and always populates
      `refusal`), so this is hardening, not a live bug. Prefer an explicit `if decision.refusal is
      None: raise LiveNodeConfigError(...)`. [src/core/live_node_builder.py:76]
- [x] [Review][Patch] Both IB clients sharing one `ibg_client_id` is correct *only* because the
      adapter de-duplicates via a module-level cache keyed on `(host, port, client_id)`
      (`factories.py:114`) — a fact stated nowhere in this module, and known only from the
      integration test's docstring. A reader of `live_node_builder.py` alone would reasonably read
      it as the collision FR5 forbids. One comment naming the shared-client-cache contract settles
      it. [src/core/live_node_builder.py:95]
- [x] [Review][Patch] Both integration tests are `async def` with no `await` and no explicit
      `@pytest.mark.asyncio`, depending implicitly on `asyncio_mode=auto` in a config file they
      never reference — yet the running loop is load-bearing (`TradingNode` resolves
      `asyncio.get_event_loop()` internally). If that mode ever changes, pytest does not error:
      the tests stop exercising anything while the suite stays green.
      [tests/integration/core/test_live_node_lifecycle.py:56]

**Deferred (real, but pre-existing or owned by a later story — logged in deferred-work.md):**

- [x] [Review][Defer] `set_nautilus_log_guard` is an unsynchronised check-then-act, and
      `NautilusKernel.__init__` does its own unlocked `if not is_logging_initialized()` — two
      concurrent constructions can double-init the C subsystem (CLAUDE.md Gotcha #1's panic)
      [src/utils/logging.py:127] — deferred, pre-existing
- [x] [Review][Defer] A `TradingNode` construction that fails *after* the kernel claims C logging
      (e.g. off the main thread, where `_setup_loop` → `signal.signal` raises) leaves the guard
      unregistered and the subsystem torn down on GC [src/core/live_node_builder.py:140] —
      deferred, pre-existing
- [x] [Review][Defer] A gate refusal is logged nowhere — `src/core/live_gate.py` contains zero
      logging calls (verified), and this module's logging surface was deliberately capped at one
      debug event by the spec. Natural home is Story 1.7's exit-code mapping
      [src/core/live_gate.py] — deferred, pre-existing
- [x] [Review][Defer] `TradingNodeConfig` is built with no `LoggingConfig` and no explicit
      `timeout_connection`/`timeout_disconnection`/`timeout_post_stop`, silently inheriting
      Nautilus defaults for a live session — and those timeouts govern exactly the shutdown
      behaviour AC #6 cares about [src/core/live_node_builder.py:114] — deferred, Epic 2 runner
- [x] [Review][Defer] AC #4's literal wording ("before any other Nautilus component initialises
      in-process") describes an ordering the Nautilus API does not expose — `NautilusKernel.__init__`
      builds MessageBus, Cache, Portfolio, all engines and Trader before returning, so the guard
      can only be registered after. The implementation registers at the earliest reachable point;
      the AC text is the defect [AC #4] — deferred, spec-text amendment
- [x] [Review][Defer] `model_copy(update=...)` writes straight into `__dict__` with no
      field-existence or type validation, so a typo key is silently accepted; relatedly, an
      `IBKRSettings` built by a caller via `model_construct`/`model_copy` could carry overlapping
      client IDs and this module would propagate them into both live clients unchallenged
      [src/core/live_node_builder.py:90] — deferred, pre-existing
- [x] [Review][Defer] Task 1's NFR26 sub-assertion ("the message contains only the masked form of
      any account it echoes") is vacuously satisfied — the only scenario tested (`tws_account=""`)
      echoes no account at all, so there is nothing to mask
      [tests/component/core/test_live_node_builder.py:253] — deferred, minor

**Dismissed (5):** the `trading_settings` `model_copy` no-op and the `== 1` AST assertion that
cements it (both explicitly mandated by Dev Notes' "The `ibkr_read_only` truth" — Blind Hunter
flagged them without spec access, which is itself evidence the line is not self-evident, but the
decision is the spec's and it stands); `market_data_type`/RTH taking adapter defaults (Story 1.5's
contract); the exec client "missing" `request_timeout` (false positive — the field does not exist
on `InteractiveBrokersExecClientConfig`, `config.py:262-267`); a failing `run_async` exception
being "silently swallowed" (false positive — awaiting the already-failed task does re-raise it,
though it does still skip `dispose()`, which is captured as a patch above); and the module being
152 lines against Task 2's "should land near 120" (soft guidance; the hard CLAUDE.md limits —
file <500, function body <50 — are met and `make lint` passes).

## Dev Notes

### What this story is, and where its edges are

One new module (`src/core/live_node_builder.py`), one component test file, one integration test
file, one diagnostic script, one new QA document. It produces a **configured, unstarted** node. It
does not run one, does not subscribe to anything, does not own a lifecycle, and does not know what
a session is.

If the diff touches `src/core/live_gate.py`, `src/config.py`, any strategy file, `src/api/**`,
`templates/**`, or `alembic/`, something has gone wrong.

### The three Nautilus facts that decide this story's structure

All three were verified against the installed wheel this session, not recalled from documentation.

1. **`TradingNodeConfig` assembly is logging-free.** Building the config and both IB client configs
   leaves `is_logging_initialized()` at `False`. That is why config assembly is component-tier and
   node construction is integration-tier — the split is forced by the framework, not a preference.
2. **`NautilusKernel` already guards its own logging init** (`nautilus_trader/system/kernel.py:190`,
   `if not is_logging_initialized():`) and exposes the result via `kernel.get_log_guard()`. When a
   `BacktestEngine` got there first, that returns `None`. Verified end to end:
   ```
   after engine: logging_initialized = True
   node built OK; node guard is None -> True
   ```
   So AC #4's "no double-init panic" is a property the framework gives us — our job is to *register*
   the guard when we are the ones who created it, and to prove the coexistence holds.
3. **`get_cached_ib_client` is keyed on `(host, port, client_id)`**
   (`nautilus_trader/adapters/interactive_brokers/factories.py:112`). The data client and the
   execution client sharing `ibkr_live_client_id` therefore share **one** underlying socket. That is
   correct and is what AC #5 asks for — a reviewer seeing "two clients, one ID" should not read it
   as the collision FR5 forbids. FR5 is about the *historical* client, which lives on
   `ibkr_client_id` (effective range 1–6 after rotation) and is a different socket entirely.

### ⚠️ Trap: the exec client factory asserts on `TWS_ACCOUNT` and will not see your `.env`

`InteractiveBrokersLiveExecClientFactory.create` does this
(`nautilus_trader/adapters/interactive_brokers/factories.py:299-302`):

```python
ib_account = config.account_id or os.environ.get("TWS_ACCOUNT")
assert ib_account, f"Must pass `{config.__class__.__name__}.account_id` or set `TWS_ACCOUNT` env var."
```

Two consequences:

- **`account_id` must be passed explicitly.** pydantic-settings reads `.env` into the settings
  object; it does **not** export into `os.environ`. Relying on the fallback would work only for an
  operator who happens to have exported the variable in their shell, and would violate the
  typed-settings rule (project-context.md:46) in the bargain.
- **An empty `tws_account` must fail early, in our code.** The gate deliberately permits an empty
  `TWS_ACCOUNT` on the paper path (`src/core/live_gate.py:214` — the prefix check is `if account and
  ...`). So a legitimately gate-permitted configuration can still be unable to produce an exec
  client, and the failure would otherwise surface as a bare `AssertionError` from deep inside a
  third-party factory during `node.build()`. Raise `LiveNodeConfigError` at config-assembly time
  naming `TWS_ACCOUNT` instead. This is the difference between an operator-legible error and a
  stack trace.

The factory then wraps it as `AccountId(f"{name or IB_VENUE.value}-{ib_account}")`, so pass the raw
account (`"DU4076626"`), not a pre-formatted one.

### The `ibkr_read_only` truth — read this before implementing AC #3

**Nautilus's IB adapter has no read-only knob for an operator-run gateway.** `read_only_api` exists
only on `DockerizedIBGatewayConfig` (`.../interactive_brokers/config.py:66`), which configures a
container Nautilus itself launches — an architecture D5 explicitly did not choose. Neither
`InteractiveBrokersExecClientConfig` nor `InteractiveBrokersDataClientConfig` has such a field;
verified field-by-field against the installed wheel.

That is not a gap to work around — it is exactly what NFR27 and AR43 already say: **the gate is the
load-bearing control, and `ibkr_read_only` is a declaration of intent.** So:

- Set it (`model_copy(update={"ibkr_read_only": False})`) on the derived view the exec config is
  built from, so the declaration is real and the line is not dead code.
- **Never branch on it.** `if settings.ibkr_read_only: ...` anywhere in this module is the AR43
  anti-pattern and must be rejected in review. The AST test in Task 1 makes that mechanical.
- **Never invent an enforcement mechanism** — no wrapper that refuses `submit_order`, no flag
  threaded into the runner. There is no order path yet; Epic 1's whole point is that the safety
  control ships before the capability it constrains.

**Related, and worth recording now rather than discovering in Epic 3:** `docker-compose.yml:51` sets
`READ_ONLY_API: "yes"` unconditionally on the `ib-gateway` service. An operator using the compose
gateway will have orders refused at the gateway regardless of anything in this codebase. That is
harmless for Epic 1 (no orders exist) and out of scope here — but add it to `deferred-work.md` so
Epic 3 does not lose a day to it.

### Scope boundaries — do NOT do these here

- **No Redis, no `CacheConfig`, no `RedisSettings`.** The delta tree lists "Redis CacheConfig" among
  this file's eventual contents, but AR10/AR11 place both in Epic 2, and the session-derived
  `trader_id` that makes the Redis namespace meaningful does not exist yet. Adding it now produces a
  cache keyed on a placeholder.
- **No `trader_id` derivation.** Required parameter, no default, no `PAPER-` prefixing logic here.
  Epic 2 owns it (AR10).
- **No `market_data_type` / `use_regular_trading_hours` / market-data-line budget.** Story 1.5 owns
  FR3, FR4 and AR21, including the "fail loudly rather than fall back to delayed" behaviour. Leave
  both at the adapter defaults — which, note, are already `market_data_type=1` (REALTIME) and
  `use_regular_trading_hours=True`. **Do not read this as "1.5 is already done"**: 1.5's contract is
  that these are set *explicitly from settings* and that a session which cannot get real-time data
  fails rather than degrades. Also note `IBKRSettings.ibkr_market_data_type` currently defaults to
  `"DELAYED_FROZEN"` (`src/config.py:84`) — wiring settings through *today* would make live data
  delayed, which is precisely why it waits for 1.5 to handle the override deliberately.
- **No Layer 2 account verification.** Story 1.4 owns checking what the gateway actually reports
  (FR8/AR14). This story's account handling is only "produce a usable exec client config".
- **No connection-loss detection or trading-permitted flag.** Story 1.6.
- **No CLI, no `live` group, no exit codes.** Story 1.7 maps `GateRefusedError` → exit `3`. Your job
  is to make that mapping possible by raising a distinguishable exception.
- **No strategies, no actors.** The node's `strategies` list stays empty this story.
- **No `live_session_runner.py`.** Epic 2, Story 2.5. `build_trading_node()` returns an unbuilt,
  unstarted node precisely so the runner can own `build()`/`run()`/`stop()` (AR38).
- **No reconciliation configuration.** `LiveExecEngineConfig.reconciliation` already defaults to
  `True`; Epic 4 owns tuning it (AR25). Do not set reconciliation fields here.
- **No startup-phase logging framework.** AR39's ordered phase sequence
  (`gate:static → node:build → node:connect → gate:account → reconcile → warmup → subscribe →
  trading`) is the *runner's* contract, and the runner is Epic 2. This story supplies the first two
  phases as callable functions; it must not grow a phase enum, a phase emitter, or a
  `phase=<name> status=...` log convention of its own. One debug event on success is the whole
  logging surface here.
- **No new dependency** (AR3).
- **No README change.** This story adds no environment variable and no operator command; the probe
  is a diagnostic documented in `docs/qa/`. CLAUDE.md's README-sync rule is satisfied by leaving it
  alone.

### Previous story intelligence (Story 1.2, and Story 1.1 before it)

- **The client-ID allocation is settled and this story is its first consumer.** Historical =
  `ibkr_client_id` = `1`, effective range **1–6** (rotates `base+1..base+5` on connect timeout);
  live session = `ibkr_live_client_id` = `10`; reconcile = `11` (Epic 4). `validate_client_ids_distinct`
  (`src/config.py:109-141`) now rejects any overlap between the historical rotation range and the
  reserved pair, in both directions, with `ge=1` on both fields. You are consuming the `10`; you are
  not re-deciding it.
- **The local `.env` is already correct.** Story 1.2's Task 0 changed `IBKR_CLIENT_ID` `10` → `1`.
  Verified this session: `IBKRSettings()` resolves host `127.0.0.1`, port `4002`, account
  `DU4076626`, mode `paper`, client `1`, live client `10`, `read_only True`. **The gate permits this
  configuration today** (paper mode, 4002 ∈ paper ports, `DU` prefix) — so the probe in Task 5 has a
  real target, and no Task-0-style escalation is expected. Note `.env` uses inline comments
  (`IBKR_PORT=4002       # ...`); python-dotenv strips them correctly — verified, the port parses as
  the int `4002` and the account as `"DU4076626"` with no trailing comment.
- **`.env*` is hook-protected** (`.claude/hooks/protect-files.sh` matches by basename). If you find
  yourself needing to change it, escalate to Allay as Stories 1.1 and 1.2 both did — do not bypass
  the hook.
- **`ENV=dev|qa|prod` does not propagate into nested `IBKRSettings`** (`src/config.py:332-336` —
  `Field(default_factory=IBKRSettings)`; recorded in `deferred-work.md` twice, where the line number
  predates Story 1.2's additions). `IBKRSettings` always reads `.env`. Do not try to fix it here; do
  not be surprised by it.
- **Story 1.1's review found masking gaps twice.** `mask_account` exists and is public precisely so
  the Layer-2 and node paths reuse it. Use it.
- **Story 1.2 is `in-progress`, not `done`** — one `.env.example` wording rewrite is still blocked on
  hook approval. It is documentation-only and its values are already correct, so it does not block
  this story.

### Git intelligence — what the last five commits establish

`f1a8a0b` / `cb789bb` (gate) and `03d12cb` / `8c8d8f9` (client ID) show the working shape of this
epic: a small, single-purpose module plus its tests, followed by a hardening commit after adversarial
review. `8c8d8f9`'s diff also shows that a config change here ripples into `README.md`,
`docs/setup/IBKR_SETUP.md`, `docker-compose.yml` and `scripts/**` — this story does not change
configuration, which is why its footprint is narrower. `52b363b` reworked the commit gate: it now
inspects **staged files only**, so `git add <files>` followed by `git commit` in a separate call is
the reliable sequence.

### Project Structure Notes

- **NEW** `src/core/live_node_builder.py` — the delta tree's description is "TradingNodeConfig
  assembly: IB data/exec client configs, factories, Redis CacheConfig, LogGuard registration,
  REALTIME + RTH settings". This story delivers the first, second and fourth of those; the Redis
  cache is Epic 2 and REALTIME/RTH is Story 1.5. [Source: architecture.md#Delta-Project-Tree:504-506]
- **NEW** `tests/component/core/test_live_node_builder.py` — first test file in an existing but
  empty package.
- **NEW** `tests/integration/core/test_live_node_lifecycle.py` — the delta tree lists this under
  `tests/integration/` directly; `tests/integration/core/` is the closer match to how this repo
  organises the tier (it already holds `test_backtest_catalog_integration.py`). Either is
  defensible; prefer `core/`.
- **NEW** `scripts/diagnostics/live_node_probe.py`, **NEW** `docs/qa/phase3-live-verification.md`.
- **MOD** `_bmad-output/implementation-artifacts/deferred-work.md`, `sprint-status.yaml`.
- Untouched by construction: `src/api/**`, `templates/**`, `src/db/**`, `src/config.py`,
  `src/core/live_gate.py`, every strategy file, `alembic/**`.
  [Source: architecture.md AR44]
- Import direction (AR38, the law): `live_node_builder` may import `nautilus_trader.*`,
  `src.core.live_gate`, `src.config` (types only) and `src.utils.logging`. It must **not** import
  SQLAlchemy, `src.db.*`, or any service.

### Testing standards

- **Component tier** (`make test-component`, `-n auto`, no fork) for config assembly — Nautilus
  imports are allowed here and the assembly path provably does not initialise C logging.
- **Integration tier** (`make test-integration`, `-n auto --forked`) for anything constructing a
  `TradingNode`. The `integration_cleanup` autouse fixture (`tests/integration/conftest.py:41`)
  double-`gc.collect()`s after each test; `--forked` is still required on top of it.
- **No automated test connects to a broker** (NFR32). Broker-dependent behaviour is operator-verified
  (NFR33) in `docs/qa/phase3-live-verification.md`.
- **TDD is non-negotiable**: Task 1 RED before Task 2, Task 3 RED before Task 4. Record both.
- `pytest.ini` is the effective config (not `pyproject.toml`); `--strict-markers` is on.
- `make test-coverage` measures `src/core` + `src/strategies`, so this module **is** in scope for the
  >80% coverage bar — unlike Story 1.2's `src/config.py` change, which was not.

### Commit hygiene for this repo

- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three
  points (`.githooks/pre-commit`, the Claude bash-guard, CI). Add imports and their usages in the
  same edit. Run `make install-hooks` once per clone.
- Stage and commit in **separate** Bash calls; the gate inspects staged files only.
- Commit format `<type>(<scope>): <subject>`, e.g. `feat(live): assemble a TradingNode for IBKR paper`.
  Never reference AI or Claude in commit messages.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.3] (lines 537–582) — story statement and
  the seven acceptance criteria this file numbers 1–7
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — AR2 (TradingNode at
  1.220.0 + the confirmed IB surface), AR3 (zero new deps), AR17 (`read_only` after Layer 1, masked
  accounts), AR20 (LogGuard via `set_nautilus_log_guard`), AR21 (REALTIME/RTH — Story 1.5),
  AR22 (`docs/qa/phase3-live-verification.md`), AR38 (runner owns the node), AR39 (startup phase
  order), AR41 (log event naming), AR43 (anti-patterns), AR44 (untouched surface)
- [Source: _bmad-output/planning-artifacts/architecture.md#Foundation] (lines 153–182) — the version
  check: `TradingNode` is the live host API at 1.220.0, `LiveNode` does not exist there
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure-&-Deployment] (lines
  304–324) — D4/D5, foreground process, operator-run gateway, LogGuard registration point
- [Source: _bmad-output/planning-artifacts/architecture.md#Structure-Patterns] (lines 394–416) —
  runner-owns-node / service-owns-record, startup phase sequence, test tier placement
- [Source: _bmad-output/planning-artifacts/architecture.md#Delta-Project-Tree] (lines 504–506,
  551) — `live_node_builder.py` and `test_live_node_lifecycle.py` footprints
- [Source: _bmad-output/planning-artifacts/architecture.md#Enforcement-Guidelines] (lines 460–480) —
  the five review-rejectable anti-patterns, two of which this story could plausibly trip
- [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-03.md:253,256,261]
  — FR2 / FR7 / FR12 → Story 1.3 coverage
- [Source: src/core/live_gate.py:45,59,94,120,214] — `GateFlags`, `GateRefusal`, `mask_account`,
  `evaluate_gate`, and the `if account and ...` that lets an empty `TWS_ACCOUNT` pass the gate
- [Source: src/config.py:20,37,53,60,84,109-141,369] — `IBKRSettings`, `ibkr_live_client_id`,
  `ibkr_read_only`, `tws_account`, `ibkr_market_data_type`, `validate_client_ids_distinct`,
  `get_settings` (uncached, and not to be called from this module)
- [Source: src/utils/logging.py:117,129,133] — `set_nautilus_log_guard` (first-write-wins) and
  `get_nautilus_log_guard`
- [Source: src/api/web.py:22-23] — the init-then-register precedent
- [Source: src/services/ibkr_client.py:23-50,145-166] — `_guard_nautilus_logging` and guard
  preservation across client rebuilds; also `connect(max_id_rotations=5)`, the rotation that fixes
  the historical client's effective range at 1–6
- [Source: tests/unit/core/test_live_gate.py:26-46,373-500] — the `_settings(...)` isolation helper
  and the AST structural-assertion pattern (with its own meta-test), both reused here
- [Source: tests/integration/conftest.py:1-62] — why `--forked`, and the `integration_cleanup` fixture
- [Source: scripts/diagnostics/ibkr_reconnect_probe.py:1-45] — the diagnostic-script shape to follow
- [Source: pytest.ini:14-35] — `--strict-markers`, the registered markers, `asyncio_mode = auto`
- [Source: docker-compose.yml:44-52] — `ib-gateway` with `READ_ONLY_API: "yes"` (the Epic 3 note)
- [Source: _bmad-output/implementation-artifacts/1-2-isolate-the-live-client-id-from-the-historical-data-client.md]
  — previous story: the client-ID allocation table, the `.env` escalation precedent, the 1561-test
  baseline
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — open items, including the
  `ENV=dev/qa/prod` nested-settings gap and `DataCatalogService`'s `os.environ` reads
- [Source: _bmad-output/project-context.md:46,53,61-69,89-100,104-114,131-136] — typed settings,
  type hints, Nautilus rules, test pyramid, size limits, LogGuard
- [Source: CLAUDE.md] — commit format, import gate, staging discipline, `uv`-only dependencies,
  typecheck scope (`src/core src/services`)
- Installed-wheel verification performed while writing this story (nautilus-trader **1.220.0**):
  `nautilus_trader/live/node.py` (TradingNode API, `dispose()` timeout behaviour),
  `nautilus_trader/system/kernel.py:176-245` (logging init guard, `get_log_guard`),
  `nautilus_trader/live/node_builder.py:143-167` (factory-name ↔ config-key mapping),
  `nautilus_trader/adapters/interactive_brokers/factories.py:41-43,112,170-318` (global client
  caches, cache key, the `TWS_ACCOUNT` assert),
  `nautilus_trader/adapters/interactive_brokers/common.py:29` (`IB = "INTERACTIVE_BROKERS"`),
  `nautilus_trader/adapters/interactive_brokers/config.py:66` (`read_only_api` is
  dockerized-gateway-only)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

**Task 1 RED** — `uv run pytest tests/component/core/test_live_node_builder.py -v` before
`src/core/live_node_builder.py` existed:
```
ERROR collecting tests/component/core/test_live_node_builder.py
ModuleNotFoundError: No module named 'src.core.live_node_builder'
```
Collection error (11 tests never ran) rather than individual failures — expected, since the
import is at module scope. GREEN after Task 2: 11/11 passed.

**Task 3 RED** — `uv run pytest tests/integration/core/test_live_node_lifecycle.py -v --forked`
before `build_trading_node()` existed:
```
ImportError: cannot import name 'build_trading_node' from 'src.core.live_node_builder'
```
GREEN after Task 4: 2/2 passed (`--forked`, standalone and in most multi-file combinations —
see the SIGTRAP finding below for the one combination that still fails).

**Verification table** (Task 6):

| Command | Result |
| --- | --- |
| `make test-unit` | 1561 passed, unchanged (this story adds no unit-tier test) |
| `make test-component` (full) | 810 passed, 16 skipped — baseline 799 (see finding below) + 11 new |
| `make test-integration` (full) | 144 passed, 25 failed, 2 skipped — baseline 144 passed / 23 failed / 2 skipped (pre-existing, unrelated to this story) + this story's 2 new tests, which crash with SIGTRAP **only** in this full-collection combination (see deferred-work.md, "Deferred from: story-1.3") |
| `tests/integration/core/test_live_node_lifecycle.py` alone, `--forked`, `-n auto`/`-n 1` | 2 passed, every run (5+ repetitions) |
| `make format` | 401 files unchanged |
| `make lint` | All checks passed |
| `make typecheck` (`src/core src/services`) | Success: no issues found in 82 source files |
| `git diff --stat pyproject.toml uv.lock` | empty (AC #7) |
| `PYTHONPATH=. uv run python scripts/diagnostics/live_node_probe.py --run-seconds 5` (live IBKR paper Gateway, mid-session) | 1st run: `RESULT: fail reason=RuntimeError ...` exit 1 (probe loop-management bug, fixed — see Completion Notes); after fix, two consecutive runs: both `RESULT: ok mode=build-start-stop ...` exit 0, both `loop.is_running=False`/`loop.is_closed=True` — AC #6 verified live |

### Completion Notes List

- Delivered `src/core/live_node_builder.py` (152 lines): `build_trading_node_config()` (AC #1,
  #2, #3, #5) and `build_trading_node()` (AC #4), plus `GateRefusedError` and
  `LiveNodeConfigError`. TDD Red→Green→Refactor followed for both halves (Tasks 1–2, then 3–4).
- All three Nautilus facts the story's Dev Notes asserted were re-verified empirically during
  implementation, independent of the story's own verification: config assembly leaves
  `is_logging_initialized() == False`; a `TradingNode` built after a `BacktestEngine` gets
  `kernel.get_log_guard() is None`; a `TradingNode` built first gets a real guard that
  `set_nautilus_log_guard` then exposes via `get_nautilus_log_guard()`.
- `ibkr_read_only` is derived once via `model_copy(update=...)`, used to build the exec client
  config, and never read as a condition anywhere in the module — enforced by both a passing test
  and a companion test that proves the AST check can actually fail (mirrors
  `test_live_gate.py`'s own meta-test pattern).
- **Finding — pre-existing test-suite baselines were stale.** The story's Task 6 expected 815
  passing component tests and 169 passing integration tests as pre-story baselines. Measured
  fresh this session: component baseline is 799 (not 815); integration baseline is 144 passed /
  23 failed / 2 skipped (not 169 passing — those 23 failures pre-date this story and are
  unrelated to it). This story's own numbers are internally consistent with the measured
  baselines (799 + 11 = 810 component; the 2 new integration tests pass in isolation). Recorded
  in deferred-work.md so the stale figures aren't propagated by the next story.
- **Finding — a collection-order hazard crashes this story's 2 integration tests, but only when
  `tests/integration/api/test_trades_api.py` is collected in the same `pytest --forked` run.**
  Root cause: `src/api/web.py:22-23` calls Nautilus `init_logging()` at module-import time, which
  runs once in the shared xdist worker process during collection — before any per-test fork —
  and poisons native logging/async state that a live `TradingNode` (but not a `BacktestEngine`)
  depends on. Confirmed by bisection across 7+ full runs at `-n 1/4/auto`: both new tests pass
  100% of the time alone or in any file combination excluding that one file, and fail
  deterministically with it. `src/api/**` is off-limits for this story (Dev Notes), so this is
  documented as a new deferred-work.md finding rather than fixed here — `make test-integration`
  as literally invoked will show 2 failures from this story until it is addressed.
  `TestCoexistenceWithBacktestEngine`/`TestLogGuardRegistrationOnNodeFirst` are `async def` tests
  (matches `TradingNode`'s documented expectation of a running event loop) — this was tried as a
  first fix, confirmed not to resolve the SIGTRAP (the mechanism is native-thread state, not
  Python-level event-loop absence), and kept anyway since it is the more correct calling
  convention for a live component.
- **AC #6 verified live — a Gateway became available mid-session.** Running Procedure P1 for
  real, rather than a dry run, immediately paid for itself: the first live run surfaced a genuine
  bug the automated tests could not reach, since none of them call `node.build()`/`run()` (NFR32).
  The probe's original `asyncio.run(...)`-wrapped implementation called the synchronous
  `node.dispose()` from inside a coroutine still executing on that same running loop;
  `TradingNode.dispose()` (`nautilus_trader/live/node.py:451-458`) calls `loop.stop()` whenever it
  finds the loop running, which is correct only when `dispose()` runs after `node.run()` has
  already returned control. Result: `RuntimeError: Event loop stopped before Future completed.`
  *after* the node had already connected to the Gateway, found account `DU4076626`, reconciled
  execution state (0 discrepancies — one pre-existing residual position surfaced, unrelated to
  this story), run, and shut every engine/client down cleanly per the logs. The bug was entirely
  in the probe script's loop management, not in `build_trading_node()` — fixed by having the probe
  own an explicit `asyncio.new_event_loop()` end to end and only call `node.stop()` →
  `node.dispose()` after `run_until_complete` had returned. Re-ran twice after the fix: both
  `RESULT: ok`, exit `0`, both ending `loop.is_running=False` / `loop.is_closed=True` — the second
  run is the AC #6 "no single-use carryover" evidence, a fresh process reusing `client_id=10`
  immediately after the prior process's clean shutdown. Full account in
  `docs/qa/phase3-live-verification.md`'s Result Log.
- No new dependency (AC #7): `pyproject.toml` / `uv.lock` diff is empty.

### File List

- `src/core/live_node_builder.py` (new)
- `tests/component/core/test_live_node_builder.py` (new)
- `tests/integration/core/test_live_node_lifecycle.py` (new)
- `scripts/diagnostics/live_node_probe.py` (new)
- `docs/qa/phase3-live-verification.md` (new)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified — new "Deferred from:
  story-1.3" section)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — status transitions)
- `_bmad-output/implementation-artifacts/1-3-assemble-and-start-a-tradingnode-against-ibkr-paper.md`
  (modified — this file: tasks, Dev Agent Record, Change Log, Status)

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-08-05 | Story created — comprehensive developer context assembled; Nautilus 1.220.0 surface verified against the installed wheel. Status → ready-for-dev. |
| 2026-08-05 | Implemented. `src/core/live_node_builder.py` delivers `build_trading_node_config()` and `build_trading_node()` per AC #1–#5; TDD Red→Green across Tasks 1–4 (11 component tests, 2 integration tests). AC #7 confirmed (no dependency change). Two findings recorded in deferred-work.md: stale pre-story test-count baselines, and a pre-existing `src/api/web.py` import-time-logging-init hazard that crashes this story's 2 integration tests only when collected alongside `test_trades_api.py`. Status → review. |
| 2026-08-05 | Code-reviewed (3 adversarial layers, 48 raw findings → 3 decisions / 26 patches / 7 deferred / 5 dismissed). All 3 decisions ruled by Allay and all 26 patches applied. **Critical the story missed:** an unvalidated `trader_id` with no `-` panics in Rust and *aborts the process* (exit 134, uncatchable) — reproduced as a RED that killed the pytest worker rather than failing a test; now guarded by `_validate_trader_id` raising `LiveNodeConfigError`. Also fixed in `live_node_builder.py`: an explicit `loop` parameter (it was silently binding to an ambient/auto-created loop), account `.upper()` normalisation (a lowercase `TWS_ACCOUNT` passes the gate but would mismatch Story 1.4's Layer 2 check), non-positive timeout rejection, the `-O`-strippable `assert` on the refusal path, and `cli_flags is None` over `or`. The probe was substantially hardened: it now polls until both engines report connected before printing `RESULT: ok` (`kernel.start_async()` does not raise on connection failure, and it was sleeping 5s against a 60s connection timeout), drives shutdown from a `finally`, rejects `--run-seconds < 1`, handles Ctrl-C, and prints tracebacks to stderr. Tests gained a C-logging-state guard fixture, config-field assertions, a positive control for the gate-ordering sentinels, factory-registration assertions, process-state preconditions (the node-first test previously failed unforked), and an AST guard that now catches `assert`/`match`/comprehension/`not`/`dict(...)` shapes it used to miss. Two documentation defects corrected: `sprint-status.yaml` claimed AC #6 was unverified while three other documents recorded the live pass, and the "stale baselines" deferred-work entry was itself wrong (815/169 were correct *collected* counts — re-verified 826−11 and 171−2) and has been retracted. Final: component 837 passed / 16 skipped, unit 1561 unchanged, typecheck clean (82 files), lint/format clean, no dependency change. Status → done. |
| 2026-08-05 | A Gateway became available mid-session; ran Procedure P1 live for AC #6. Surfaced and fixed a real bug in `scripts/diagnostics/live_node_probe.py` (called `node.dispose()` from inside a still-running loop, not a `live_node_builder.py` defect) — first run failed with `RuntimeError: Event loop stopped before Future completed.` after the node had already connected/reconciled/run/shut-down cleanly per the logs; fixed by having the probe own its loop end to end and call `stop()`/`dispose()` only after `run_until_complete` returned. Two clean runs after the fix (`RESULT: ok`, exit 0, `loop.is_closed=True`) close out AC #6, including the second-fresh-process pass criterion. `docs/qa/phase3-live-verification.md` Result Log and `deferred-work.md` updated accordingly. |
