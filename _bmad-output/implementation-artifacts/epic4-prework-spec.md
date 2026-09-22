# Epic 4 Pre-work Spec — D2, D3, D4, and the `flatten_position.py --confirm` fix

**Date:** 2026-09-22 · **Owner:** Allay (operator-run, before the Epic 4 harness waves start)
**Origin:** Epic 3 retrospective (`epic-3-retro-2026-09-21.md`) action items 2, 3, 4 and 7, all
committed by Allay. D2/D3/D4 were ruled at the Epic 2 retro on 2026-08-28 and have carried
since. None of these is a BMAD story; each is one focused commit on `015-paper-trading`.
**Sequencing:** the Epic 4 harness run (`bmad_harness` `planning/006`) branches its
integration worktree off the `015-paper-trading` tip, so anything landed here is inherited by
every Epic 4 worker. Land, push, then create the worktree.

Every claim below marked *measured* was re-measured today against the working tree at `9354c61`
(Nautilus 1.220.0, Python 3.11.13); nothing is carried from memory.

## Recommended order and budget

| # | Item | Size | Why this position |
|---|---|---|---|
| 1 | **D2** — CI runs `tests/integration/db` | ~1 h | Smallest; two workflow lines plus one `__init__.py`. Unblocks trusting the DB tests Epic 4 will add. |
| 2 | **Flatten** — make `--confirm` work again | ~3 h | Epic 4's live procedures (restart across an open position, reconcile) will leave positions to close. The tool is currently unable to do its one job. |
| 3 | **D3** — `exit_outcome` marker protocol | ~3 h | Story 4.6 adds a new CLI exit outcome (discrepancy). Land the protocol before the sixth hand-maintained string, per the ruling. |
| 4 | **D4** — size-cap guard | ~4 h | Largest, least urgent to Epic 4's correctness. Do last; can slip to "alongside wave 1" without harm. |

**Common ground rules**

- One commit per item, conventional-commit subject, full gate green before each:
  `uv run ruff check . && make typecheck && make test-unit && make test-component && make test-integration`.
- Each item appends a disposition line to `deferred-work.md` under a new heading
  `## Dispositions from the Epic 4 pre-work (2026-09-DD)`, striking the item in place per the
  file's convention. Do not edit the retro documents.
- No story file, no `sprint-status.yaml` change. `epic-4` stays `backlog` until the harness
  creates the first story.
- AR22 says agents make no CI changes. D2 is a CI change, which is exactly why it is here as
  operator work and not in a story.

---

## 1 · D2 — Stop `--ignore`-ing `tests/integration/db` in CI

### Problem

`tests/integration/db/` holds 114 tests (repositories, session service, stop/start cycles,
trade persistence, migration truth, CLI-against-DB). CI has never run any of them: both the
`integration-tests` job and the `coverage-report` job in `.github/workflows/ci.yml` pass
`--ignore=tests/integration/db`. Five Epic 2 stories and two Epic 3 stories independently
recorded "not CI-gated" for this reason. Epic 4 is DB-and-reconciliation heavy.

### Facts (measured 2026-09-22)

- Both CI jobs **already** provision a `postgres:16` service with the same credentials as
  `DATABASE_URL`, and both **already** run `uv run alembic upgrade head` before pytest. The
  `integration-tests` job also provisions Redis. Nothing infrastructural is missing.
- Locally, with Postgres and Redis up:
  `uv run pytest tests/integration/db -q -n auto --forked` → **114 passed in 5.25 s**.
- `uv run pytest tests/integration --collect-only` (the integration job's target, no ignore)
  → **315 collected, no errors**.
- `uv run pytest tests/ --collect-only` (the coverage job's target, no ignore) →
  **1 collection error**: `import file mismatch` on `test_session_service.py`, because
  `tests/integration/db/` has no `__init__.py` while `tests/unit/services/` does, and both
  contain a `test_session_service.py`. This is the "structural reason" the deferred-work
  entries allude to. Adding an empty `tests/integration/db/__init__.py` resolves it:
  **4724 collected, no errors** (verified, then reverted).
- `tests/integration/db/conftest.py` applies `pytest.mark.skipif(not is_postgres_available())`
  to every test in the directory. In CI that skip must never fire silently, or the gate is
  decorative. The workflow already documents the same concern for Redis
  (`test_live_cache_namespace.py`: "here the tests must actually execute").
- `test_migration_schema.py` runs alembic in a subprocess against `DATABASE_URL` with a
  per-worker `search_path`; it needs `alembic.ini` at the cwd (repo root in CI) and psycopg2
  (the macOS fork-segfault it documents does not apply on Linux runners).

### Change

1. `.github/workflows/ci.yml`: delete the `--ignore=tests/integration/db \` line from **both**
   pytest invocations (integration job and coverage-report job).
2. Add `tests/integration/db/__init__.py` (empty, or a one-line docstring explaining the
   duplicate-basename reason so nobody deletes it).
3. `tests/integration/db/conftest.py`: make the availability skip fail loudly in CI:
   ```python
   _PG_AVAILABLE = is_postgres_available()
   if not _PG_AVAILABLE and os.environ.get("CI"):
       raise RuntimeError("CI provisions Postgres; tests/integration/db must run, not skip")
   pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, reason=...)
   ```
   GitHub sets `CI=true` on every runner. Developers without Postgres keep the skip.
4. Nothing else. Do not touch the `--cov-fail-under=64` threshold; adding executed tests can
   only raise measured coverage.

### Tests / verification

- Push to a branch, or run the workflow on `015-paper-trading`, and confirm all three jobs
  green with `tests/integration/db` present in the integration job's output (grep the log for
  `tests/integration/db/test_session_service.py`) on **both** matrix versions (3.11 and 3.12).
- Locally, the coverage job's exact command minus `--cov-report=html`:
  `uv run pytest tests/ -n auto --cov=src --cov-fail-under=64 -q` must pass.

### Acceptance criteria

- No `--ignore=tests/integration/db` remains anywhere in `.github/`.
- CI runs the 114 DB tests on every PR and fails if Postgres is unavailable.
- `deferred-work.md` disposition strikes D2 and the seven "not CI-gated" entries that cite it
  (story-2.2 code review ×2, story-2.5, story-2.6, story-2.8, story-3.6 ×2 — search for
  `integration/db`).

### Out of scope / risks

- Runtime grows by a few seconds per job. If the coverage job's wall clock matters, note it,
  do not re-add the ignore.
- If 3.12 surfaces an asyncpg/psycopg2 wheel issue the 3.11 run does not, pin the DB tests to
  3.11 with a matrix condition rather than re-ignoring them.

---

## 2 · `flatten_position.py --confirm` — restore the tool's one job

### Problem

`scripts/diagnostics/flatten_position.py` is the only tool in the repository that submits an
order on purpose: it closes one position on the paper account after reading the position from
the broker and refusing anything that does not exactly offset it. It has been broken by two
independent mechanisms:

1. **2026-09-01** — a dry run (no `--confirm`) submitted and filled a real order. Cause: the
   strategy was added before `run_async()`, and `TradingNodeKernel.start_async` ends with
   `self._trader.start()`, which starts every strategy already added; `on_start()` submitted
   before any check ran. Fixed in `29e9130` by not constructing or adding the strategy until
   after the broker read, the offset check, and `--confirm`. Guarded by
   `tests/component/scripts/test_flatten_position.py` (10 tests).
2. **2026-09-11** — with that fix, `--confirm` can never submit. Cause (*measured* in the
   installed Nautilus 1.220.0, `trading/trader.py:392-397`): `Trader.add_strategy()` on a
   `RUNNING` trader without a `Controller` logs `Cannot add a strategy to a running trader`
   and returns without adding, so the following `start_strategy(strategy.id)` raises
   `ValueError: Cannot start strategy, _FlattenStrategy-None not found`. Fails safe (no order
   reaches the broker) but the tool cannot close anything. Two live positions since then were
   closed with throwaway, uncommitted scripts.

The two fixes contradict each other under 1.220.0: the strategy must be added **before** the
trader starts (mechanism 2), and it must not be able to submit when the trader starts it
(mechanism 1).

### Design (recommended): register early, inert by construction, arm by explicit call

- `_FlattenStrategy` is constructed and added to `node.trader` **before** `node.build()` /
  `run_async()`, the pre-`29e9130` shape, so the trader starts it normally.
- Its `on_start()` **does nothing** (no subscription, no order). Every other Nautilus lifecycle
  hook (`on_bar`, `on_event`, `on_order_*`, `on_position_*`, `on_stop`, `on_reset`) is either
  absent or record-only. The class has exactly one submit path, a plain method that no
  framework hook calls:
  ```python
  def submit_close(self, side: OrderSide, quantity: int) -> None:
      instrument = self.cache.instrument(self._instrument_id)
      if instrument is None: raise FlattenError(...)
      order = self.order_factory.market(instrument_id=..., order_side=side,
                                        quantity=Quantity.from_int(quantity))
      self.submitted.append(order)
      self.submit_order(order)
  ```
  The side and quantity move from the constructor to this call, because they are only known to
  be correct after the broker read.
- `_run` becomes: build node with strategy already added → `run_async()` → `_await_connected`
  → `_held_net_quantity` → `_check_offsets` → if not `confirm`, report and return (the strategy
  is running but has been told nothing) → else `strategy.submit_close(side, quantity)` →
  `_await_fill` → report.
- The tool's safety property is restated from "nothing capable of submitting was registered"
  to **"the registered strategy has one submit path, it is not reachable from any lifecycle
  hook, and `_run` reaches it only after the offset check and `--confirm`."** Both halves are
  tested structurally and behaviourally (below).

**Alternatives considered and rejected**

- Configure a `Controller` so `add_strategy` is permitted on a running trader — leans on a
  Nautilus internal (`_has_controller`) whose semantics are undocumented for this use.
- `node.trader.stop()` → add → `start()` — stops the data/exec actors mid-connection; a
  reconnect inside a diagnostic that exists to be boring is the wrong trade.
- Submit through `exec_engine.execute(SubmitOrder)` with no strategy — loses the fill/reject
  callbacks `_await_fill` depends on and invents a second order path (AR43 spirit).

### Tests

Rewrite `tests/component/scripts/test_flatten_position.py` around the new property; the fake
`_Trader` must **mirror 1.220.0**: `add_strategy` on a running trader records nothing and
returns (so the `29e9130` shape fails the suite — this is the regression test for mechanism 2),
and `start()`/`start_strategy` set `is_running` and call `strategy.on_start()` (so an
`on_start` that submits fails the suite — the regression test for mechanism 1). Cases:

- `test_a_dry_run_submits_nothing` — strategy added and started, `strategy.submitted == []`,
  positions unchanged.
- `test_a_refused_run_submits_nothing` — wrong side, wrong quantity, flat account, other
  instrument: `FlattenError`, `submitted == []`.
- `test_a_confirmed_run_submits_exactly_one_order` — `len(submitted) == 1`, matching side and
  quantity, fill observed, positions cleared.
- `test_submission_happens_after_the_offset_check` — the fake records call order; `submit_close`
  is invoked after `_held_net_quantity` returned.
- `test_the_strategy_is_added_before_the_trader_starts` — added while `is_running` is False.
- `test_on_start_submits_nothing` — drive `on_start()` directly on a strategy with a fake
  `submit_order`; assert it was never called.
- `test_no_lifecycle_hook_can_reach_submit_order` — AST scan of the script (same shape as
  `tests/unit/core/test_live_stop_path_is_inert.py`): within `class _FlattenStrategy`, the only
  function whose body calls `submit_order` is `submit_close`, and no `on_*` method calls
  `submit_close`.
- Keep `TestTeardown` (node disposed on refusal).

### Live verification (operator)

Cannot be fully verified until a real position exists to close. Do this in order:

1. Dry run and a deliberately wrong-side run against the live paper account with a flat
   instrument: both must report and refuse with **zero** `order.submitted` in the log.
2. The first time an Epic 4 procedure leaves a position open, close it with `--confirm` and
   record the result in `docs/qa/phase3-live-verification.md`'s housekeeping notes.
3. Update the three "Know before starting" notes that say the tool is broken
   (`phase3-live-verification.md` around lines 1298, 1387, 1493).

### Acceptance criteria

- `--confirm` on a live paper position produces exactly one order that offsets it; no
  `Cannot add a strategy to a running trader` in the log.
- Without `--confirm`, or on any refusal, `grep -c order.submitted` on the log is 0.
- Both regression mechanisms are pinned by tests that fail if either past shape is restored.
- `deferred-work.md` strikes the 2026-09-11 entry ("broken again, by a different mechanism").

### Out of scope

The node is still built without a `cache=` argument (noted 2026-09-01), so the tool never
exercises the Redis serializer. Leave it; it is a diagnostic, not a session.

---

## 3 · D3 — `exit_outcome` marker protocol in `live_check.py`

### Problem

`src/core/live_check.py` decides a CLI's exit code by walking the exception's MRO and looking
each class **name** up in `_OUTCOME_BY_EXCEPTION_NAME` (14 string entries), and separately
decides whether the message is safe to print by looking names up in
`_SAFE_MESSAGE_EXCEPTION_NAMES` (11 entries). The two sets have drifted (five names in the
second that are not in the first), every new typed failure needs a hand edit in the pure module
plus a name-pinning test in a component suite, and matching by name collides:

- *Measured:* `classify_failure(sqlalchemy.exc.TimeoutError())` →
  `BROKER_UNREACHABLE` (**exit 4**) because SQLAlchemy's pool-timeout class is also named
  `TimeoutError`. A DB pool timeout in `live start`/`live status` would tell the operator to
  restart a healthy Gateway. The D2 ruling explicitly names this collision as the thing to fix.

Ruling (Epic 2 retro, 2026-08-28): an `exit_outcome` marker attribute, now, changing no
already-documented exit code (AR28: 0/1/2/3/4; README lines 215 and 219).

### Constraints that shape the design

- `live_check.py` must stay framework-free. `TestModulePurity` forbids it importing
  `nautilus_trader`, `ibapi`, `sqlalchemy`, `src.db`, `src.services`, `src.api`,
  `src.core.live_node_builder`, `src.core.live_check_driver`. So the exceptions must come to
  `live_check`'s vocabulary, not the reverse.
- `live_check.py` already imports `src.core.live_gate`, and eight modules import `live_check`.
  `src/db/exceptions.py` has **no imports at all** today; making it import `live_check` would
  pull `live_gate` and `structlog` into the DB exception module. Avoid that with a leaf module.
- Story 4.6 will add a discrepancy outcome; the design must make that a one-line addition on
  the new exception, not an edit to `live_check`.

### Design

1. **New leaf module `src/core/exit_outcome.py`** (stdlib only): move `LiveCheckOutcome` and
   the `EXIT_*` constants / `EXIT_CODES` table there; `live_check.py` re-exports them so every
   existing import keeps working. Define the marker:
   ```python
   class ExitOutcomeMarker(Protocol):
       exit_outcome: ClassVar[LiveCheckOutcome]     # which exit code describes this failure
       operator_safe_message: ClassVar[bool]         # is str(exc) this codebase's own text?
   ```
   Markers are **class attributes on the exception classes**, not a base class: several of
   these already inherit from other families (`InvalidSessionTransition(BacktestStorageError)`)
   and a marker base would force multiple inheritance across four modules.
2. **Mark every exception currently in either name set**, preserving today's behaviour exactly:

   | Exception (module) | `exit_outcome` | exit | `operator_safe_message` |
   |---|---|---|---|
   | `GateRefusedError` (live_node_builder) | `GATE_REFUSED` | 3 | True |
   | `BrokerUnreachableError` (live_check) | `BROKER_UNREACHABLE` | 4 | True |
   | `LiveNodeConfigError` (live_node_builder) | `CONFIG_ERROR` | 1 | True |
   | `LiveMarketDataError` (live_market_data) | `CONFIG_ERROR` | 1 | True |
   | `InvalidCheckWindowError` (live_check) | `CONFIG_ERROR` | 1 | True |
   | `RedisUnreachableError` (live_cache) | `CONFIG_ERROR` | 1 | True |
   | `InvalidSessionTransition` (db/exceptions) | `CONFIG_ERROR` | 1 | True |
   | `RecordNotFoundError` (db/exceptions) | `CONFIG_ERROR` | 1 | True |
   | `LiveCheckError` (live_check) | `ERROR` | 1 | True |
   | `SessionReclaimedError` (live_session_record) | `ERROR` | 1 | True |
   | `NoStrategyStartedError` (live_strategy_guard) | `ERROR` | 1 | True |

   `StrategyGuardError` and `SessionStopRequested` are unmarked today and stay unmarked
   (generic exit 1, type-name-only message; `SessionStopRequested` never reaches the classifier).
3. **`classify_failure`** walks the MRO and returns the first class's `exit_outcome` if it is a
   `LiveCheckOutcome`. Builtins cannot carry attributes, so a small residual map keyed by
   **class identity** (not name) covers them: `KeyboardInterrupt → INTERRUPTED`,
   `ConnectionError`, `socket.gaierror`, builtin `TimeoutError → BROKER_UNREACHABLE`
   (`socket` is stdlib; `asyncio.TimeoutError is TimeoutError` on 3.11, measured). Anything
   else → `ERROR`. `sqlalchemy.exc.TimeoutError` is not the builtin and carries no marker, so it
   now classifies `ERROR` (exit 1). Delete `_OUTCOME_BY_EXCEPTION_NAME`.
4. **`failure_message`** walks the MRO for `operator_safe_message is True`; delete
   `_SAFE_MESSAGE_EXCEPTION_NAMES`. The docstring's NFR26 rationale stays.
5. **Guard against silent drift**: a unit test enumerates every class under `src/core/live_*.py`,
   `src/core/live_check.py` and `src/db/exceptions.py` whose name ends in `Error`/`Transition`
   and asserts each is either in an explicit `UNMARKED = {StrategyGuardError, ...}` set or
   carries both marker attributes with valid types. A new typed failure then fails a test until
   its author decides its exit code, which is the whole point of the ruling.

### Tests to change

- `tests/unit/core/test_live_check.py::TestClassifyFailure` and
  `TestStoryTwoFiveClassifications`: replace name-keyed stand-ins with stand-ins that carry the
  marker; keep the MRO/precedence, KeyboardInterrupt, socket-failure and "unrelated class named
  like ours is not special-cased" cases (the last one now passes trivially and should assert the
  SQLAlchemy collision is gone: a stand-in named `TimeoutError` that is not the builtin →
  `ERROR`).
- `tests/component/core/test_live_check_driver.py::TestExceptionNameCoupling` and
  `tests/component/core/test_session_runner_phases.py::test_the_real_class_still_carries_the_name_the_map_uses`:
  delete the name pins; **keep** `test_the_real_class_maps_to_the_documented_exit_code` and
  `test_the_messages_of_the_four_reach_the_operator` unchanged — they are the behavioural
  contract and must pass before and after.
- `TestModulePurity` gains `src.core.exit_outcome` as an *allowed* import (it is stdlib-only)
  and a sibling purity test for the new leaf module.
- Add the SQLAlchemy regression at component tier, importing the real
  `sqlalchemy.exc.TimeoutError`: `EXIT_CODES[classify_failure(exc)] == 1`.

### Acceptance criteria

- README's exit-code table (lines 215, 219) is unchanged and still true; AR28 untouched.
- No string-keyed exception map remains in `live_check.py`.
- Every exit code in the table above is asserted on the **real** class at component tier.
- `sqlalchemy.exc.TimeoutError` exits 1, pinned by a test.
- `deferred-work.md` strikes D3 and the four entries that route to it (story-1.7, story-2.5
  "fourth name", story-2.7 "fifth name", and the Epic 2 retro row).

### Note for Story 4.6

The `live reconcile` discrepancy outcome becomes: add `DISCREPANCY` to `LiveCheckOutcome`, a
new distinct exit code to `EXIT_CODES` (the existing "outcome added without a code fails a
test" loop enforces this), document it in README's table, and set `exit_outcome` on the new
exception. No edit to `classify_failure`.

---

## 4 · D4 — Size-cap guard: build the ratchet, keep the caps

### Problem

CLAUDE.md states caps of <500 lines per file, <50 per function, <100 per class, measured on
executable statements since the Epic 2 retro (D4), enforced by nothing but ruff's line-length
rule. Epic 3 disclosed 5+ overages with no mechanical catch. The retro's action item is: build
the AST guard, or formally retire the wording.

### Facts (measured 2026-09-22, on executable lines, `src/` only)

Metric used: the set of distinct source lines occupied by `ast.stmt` nodes, excluding docstring
expressions; a compound statement (`if/for/while/with/try/def/class/match`) contributes only its
header line, so a function's size is its own statements plus its nested bodies' statements.

| Cap | Raw-line overages | Executable-line overages |
|---|---|---|
| File > 500 | 25 files | **3**: `core/backtest_runner.py` 952 · `services/data_catalog.py` 635 · `api/ui/backtests.py` 524 |
| Class > 100 | — | **36** (largest: `MinimalBacktestRunner` 926, `DataCatalogService` 612, `LiveSessionRunner` 400, `ImportService` 373, `BacktestOrchestrator` 325) |
| Function > 50 | — | **62** (largest: `run_backtest` 174, `run_backtest_async` 168, `_reproduce_backtest_async` 164, `save_trades_from_positions` 137, `_import_ticker` 135, `build_trading_node_config` 128, `run_backtest_submit` 126) |

**Metric rulings (decided 2026-09-22, so the first person to hit one does not relitigate it):**

1. **Field annotations count.** In a Pydantic model or dataclass every `name: type = default` line
   is an `AnnAssign` statement and is counted. This is why `IBKRSettings` measures 101. Not
   special-cased: a hundred-field model is a real size signal, and exempting annotations would
   exempt the one kind of class that is nothing but them.
2. **Multi-line literals count every line.** A 40-line mapping, a SQL string, a template — all
   40 lines count, because a reader has to read them and ruff's formatter prevents gaming by
   joining lines. A docstring is the *only* multi-line expression excluded, and only when it is
   in docstring position (first statement of a module, class or function body).
3. **Nested functions roll up into their parent.** A closure's lines count toward the enclosing
   function and are not keyed separately, otherwise a long function hides itself in closures.
   Methods roll up into their class; classes and functions both roll up into the file.

Two readings follow. The executable-line metric vindicates the Epic 2 ruling for **files**: the
Phase 3 modules the raw count flagged (`live_session_runner.py` 1085 → 484,
`live_strategy_guard.py` 639 → 169, `live_session_steady_state.py` 700 → 218) are
docstring-dominated and clear the cap. But **98 classes and functions** are over their caps
today, most of them Phase 1/2 backtest and import code. An "allowlist of sanctioned exceptions"
with 98 entries is not an allowlist; it is the absence of a rule.

### Recommendation: build it, as a ratchet

Keep the caps as the target for **new** code and enforce "no overage may grow and no new overage
may appear", which is what the disclose-and-record convention already asks for by hand.

**`tests/unit/governance/test_size_caps.py`** (unit tier, `ast` only, same shape as
`tests/unit/core/test_live_stop_path_is_inert.py` and the AR37 guard in
`tests/unit/services/test_session_service.py`):

1. `measure_module(path) -> Measurement(file_lines, classes: dict[str,int], functions: dict[str,int])`
   using the metric above. Function keys are qualified (`Class.method` or `func`); nested
   functions roll into their parent and are not keyed separately (ruling 3 above).
2. **File cap — hard, with a three-entry allowlist** carrying each file's recorded ceiling:
   `{"src/core/backtest_runner.py": 952, "src/services/data_catalog.py": 635,
   "src/api/ui/backtests.py": 524}`. A listed file may not exceed its ceiling; any other file may
   not exceed 500.
3. **Class and function caps — ratchet baseline**: a `SIZE_BASELINE` mapping
   `"src/…/file.py::Qualified.name" → measured value` generated once from today's 98 overages.
   The test fails when (a) an unlisted class/function exceeds its cap, (b) a listed one exceeds
   its recorded value, or (c) a listed one now measures **at or under** its cap — stale entries
   must be removed, so the baseline only shrinks. Shrinking without dropping under the cap is
   allowed without editing the baseline (the entry is a ceiling, not an exact value).
4. A `python -m tests.unit.governance.test_size_caps --print-baseline` entry point prints the
   current overages in baseline form, so regenerating after a deliberate split is mechanical.
5. Metric pinned by fixtures on synthetic source strings, one per ruling above plus the basics:
   a 600-line docstring-only module measures 0; a compound statement counts once; a non-docstring
   multi-line string in statement position counts every line; a model of ten annotated fields
   measures 10; a function with a 20-line closure measures its own lines plus 20 and the closure
   has no key of its own.

**Wording changes (same commit):** CLAUDE.md "Size limits" bullet: replace "a unit-tier AST
guard … is pending" with "enforced by `tests/unit/governance/test_size_caps.py`: new code must be
under the caps; an overage in existing code is a baseline entry that may only shrink. To exceed a
cap deliberately, add the entry **with a one-line reason** in the same commit — that is the
disclose-and-record step." Keep the "budget a split before the edit … guard lists" sentence
verbatim; the ratchet creates no incentive to split blindly because it forbids growth, not size.

**Alternative (retire the wording)**: delete the three caps from CLAUDE.md and
`project-context.md`, keep only ruff's line length. Rejected here because the file cap is
genuinely met on the honest metric everywhere except three legacy modules, and because every
Phase 3 story already pays the disclosure cost; a guard makes that cost buy something.

### Tests / acceptance criteria

- The guard passes on the current tree with exactly 3 file entries and 98 baseline entries.
- Adding 60 executable lines to `src/core/live_session_runner.py::LiveSessionRunner` makes it
  fail with a message naming the entry, its ceiling, and the measured value (verify by hand,
  then revert).
- Removing a baseline entry whose subject is still over the cap fails; leaving an entry whose
  subject dropped under the cap fails.
- `make test-unit` runtime grows by well under a second (one `ast.parse` per `src/` file).
- CLAUDE.md wording updated as above; `docs/agent/conventions.md` if it repeats the caps.
- `deferred-work.md` strikes D4 (the Epic 2 retro row, "◑ wording only") and the story-2.6
  "enforce it or amend the guideline" entry.

---

## Close-out

When all four are landed: push `015-paper-trading`, append one Log line to
`bmad_harness/planning/006-ntrader-p3-epic4-run.md` naming the four commits, then create the
Epic 4 integration worktree from the new tip. The Epic 4 charter already forbids workers from
touching CI, migrations and `flatten_position.py`, so nothing here needs re-explaining to them
beyond what the scoped PRD extract carries.
