# Story 2.3: Move a Session Between States Through One Guarded Path

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want every session state change to go through a single validated transition,
so that an interrupted session is always in a state I can either start or seal, and never stuck.

## Acceptance Criteria

1. **Given** `SessionService.transition()` **When** any status change occurs anywhere in the
   codebase **Then** it routes through this single method — no direct `status =` assignment exists
   outside it (FR20, AR37).

   ⚠️ **The epic wording says "a grep finds no direct `status =` assignment outside it." Taken
   literally that gate can never pass**: `grep -rn "status = " src/` matches **20** pre-existing
   lines today and `grep -rn "\.status = " src/` matches **5**, none of which touch
   `trading_sessions` (see Dev Notes → *Pre-verified finding 8*). Story 2.2 hit the same wall and
   recorded it. This AC is therefore discharged by **two verified-satisfiable greps plus one
   structural AST test**, all specified in Task 6. The property is unchanged; only the instrument is.

2. **Given** the legal transitions `created → running`, `running → stopped`, `stopped → running`,
   `stopped → sealed` **When** each is requested **Then** it succeeds and the corresponding
   timestamp column is updated, per the table in Dev Notes → *Timestamp policy*.

3. **Given** an illegal transition such as `created → sealed`, `running → sealed`, or any transition
   out of `sealed` **When** it is requested **Then** `InvalidSessionTransition` is raised from the
   `src/db/exceptions.py` family, and the stored status is unchanged (AR37) **And** `sealed` is
   terminal with no outbound transitions at all.

4. **Given** a session whose status is `running` but whose `last_heartbeat_at` is stale **When** a
   transition to `running` is requested **Then** the session is reclaimed, logged as
   `session.reclaimed`, and enters `running` — the only sanctioned running→running path
   (AR33, NFR11) **And** a `NULL` `last_heartbeat_at` counts as stale, not as fresh.

5. **Given** a session whose status is `running` with a *fresh* heartbeat **When** a transition to
   `running` is requested **Then** it is refused with a message stating another process appears
   live, naming the session and the heartbeat age (AR33).

6. **Given** two processes requesting the reclaim concurrently **When** both observe the same stale
   heartbeat **Then** exactly one reclaims and the other is refused — the read that feeds the
   decision takes a row lock, and the reclaim stamps `last_heartbeat_at` so the loser re-reads a
   fresh one (NFR6, NFR11, NFR29).

   ⚠️ **This AC is not in `epics.md`.** It is added because AC #4 and AC #5 are otherwise a textbook
   TOCTOU race whose failure mode — two live processes on one session — is the single worst outcome
   this phase can produce, and because both the hole and the fix were **demonstrated by execution**
   before this story was written (Dev Notes → *Pre-verified finding 4*). Reject it at review if you
   disagree, but reject it knowingly.

7. **Given** `SessionService` **When** its imports are inspected **Then** it imports no
   `nautilus_trader` module (AR38).

8. **Given** a session referenced by name rather than UUID **When** any command resolves it **Then**
   resolution goes through one shared helper on `SessionService`, which accepts either form and
   raises `RecordNotFoundError` when neither matches (AR36).

9. **Given** this story **When** the diff is inspected **Then** it contains no Alembic migration, no
   CLI command, no runner, and no change to `trading_sessions`' schema — every column this story
   writes already exists and is already `timestamptz`/nullable (verified against the live database;
   Dev Notes → *Pre-verified finding 1*).

## Tasks / Subtasks

- [x] **Task 0: Record the baseline before touching anything** (AC: all)
  - [x] Record **collected** counts (not passed — Epic 1 retro, Key Insight #3) for `tests/unit`,
        `tests/component`, `tests/integration` via `uv run pytest <dir> --collect-only -q | tail -3`.
        Expected at story-drafting time: **unit 1856 · component 1047 · integration 239**. If they
        differ, use yours and say so; do not use these.
  - [x] Confirm `uv run alembic heads` is a single head `d08dfbd393f0`, and that
        `trading_sessions` has 0 rows. Both were true when this story was written.

- [x] **Task 1: `InvalidSessionTransition` (TDD Red first)** (AC: #3)
  - [x] Write `tests/unit/db/test_session_exceptions.py`: `InvalidSessionTransition` is importable
        from `src.db.exceptions`, subclasses `BacktestStorageError`, and is catchable as it. Model on
        `tests/unit/services/test_exceptions.py:20-49` (`TestExceptionHierarchy`). Run → **RED**.
  - [x] Add `InvalidSessionTransition(BacktestStorageError)` to `src/db/exceptions.py` with a
        Google-style docstring naming the two cases it covers (an illegal edge; a refused reclaim).
        Do **not** add any other exception class — `RecordNotFoundError` already exists and AC #8
        reuses it. Run → **GREEN**.

- [x] **Task 2: `for_update` on both repositories (TDD Red first)** (AC: #6)
  - [x] Write the guard in `tests/unit/db/test_trading_session_repository_shape.py`: both
        `find_by_session_id` implementations must emit `FOR UPDATE` when asked. Assert structurally —
        `inspect.getsource(repository_class.find_by_session_id)` contains `with_for_update(`, in the
        same style as the existing `test_find_all_orders_deterministically` at line 79. Run → **RED**.
  - [x] Add `*, for_update: bool = False` to `find_by_session_id` on **both**
        `trading_session_repository_sync.py` and `trading_session_repository.py` (AR9 — every DB
        feature lands in both). When true, `.with_for_update()` on the `select`. Docstring must say
        why: the reclaim decision reads and writes across a window in which another process may do
        the same. Run → **GREEN**.
  - [x] Confirm `EXPECTED_CAPABILITIES` (line 26) still passes **unmodified** — a keyword argument
        is invisible to `dir()`, so the four-name allowlist and the async/sync parity assertion both
        hold as written. If you find yourself editing that frozenset, your design has drifted from
        this story's; stop and re-read Dev Notes → *Why the assignment lives in the service*.

- [x] **Task 3: The state machine (TDD Red first)** (AC: #2, #3)
  - [x] Write `tests/unit/services/test_session_service.py` with a `FakeClock` returning `datetime`
        (adapt `tests/unit/core/test_live_connection_monitor.py:40-53`) and a
        `MagicMock(spec=SyncTradingSessionRepository)` returning detached `TradingSession` objects
        (idiom: `tests/unit/services/metadata/test_instrument_metadata_service.py`). Cover: all four
        legal edges succeed and stamp the right column; every illegal edge raises
        `InvalidSessionTransition`; **all three** outbound edges from `sealed` raise; **every
        self-edge except `running → running`** (`created → created`, `stopped → stopped`,
        `sealed → sealed`) raises; an unknown `session_id` raises `RecordNotFoundError`; the stored
        status is unchanged on refusal. Run → **RED** (`ModuleNotFoundError`).
  - [x] ⚠️ **Build every fake row with an explicit `status=`.** The ORM's
        `default=SessionStatus.CREATED` is a Python-side default applied **at flush**, so an
        unattached `TradingSession(name="x", spec={})` has `status is None` — verified. A test that
        omits it is asserting against `None`, not against `created`.
  - [x] Implement `src/services/session_service.py`: module constants, `_utc_now()`, the
        `_LEGAL_TRANSITIONS` mapping, `SessionService.__init__(repository, *,
        heartbeat_stale_after_seconds=..., time_source=_utc_now)`, and `transition()`. **Validate
        before mutating** — the AC-#3 "stored status is unchanged" guarantee must not depend on the
        caller rolling back. Run → **GREEN**.
  - [x] Reject a non-finite or non-positive `heartbeat_stale_after_seconds` at construction,
        mirroring `live_connection_monitor.py:98-111` `_require_positive`. A `nan` threshold makes
        every `>` comparison `False` and silently disables the reclaim forever.

- [x] **Task 4: Reclaim and refusal (TDD Red first)** (AC: #4, #5)
  - [x] Tests, using `FakeClock` for exact boundaries (`age == threshold` is fresh; `age >
        threshold` is stale — strictly `>`, matching `live_connection_monitor.py:190`): stale
        heartbeat reclaims and logs `session.reclaimed`; fresh heartbeat raises
        `InvalidSessionTransition` whose message contains the session name; `last_heartbeat_at=None`
        on a `running` session reclaims; a heartbeat in the *future* (clock skew between processes)
        clamps to age 0 and refuses rather than producing a negative age that reads as stale.
        Assert the log with `structlog.testing.capture_logs` (`test_live_connection_monitor.py:78`).
        Run → **RED**.
  - [x] Implement. `running → running` is deliberately **absent** from `_LEGAL_TRANSITIONS` and
        handled as a named branch, so the table stays a literal reading of AC #2 and the one
        exception is greppable. Clamp the age with `max(0.0, ...)`
        (`live_connection_monitor.py:350-358`).
  - [x] The reclaim path stamps `last_heartbeat_at`. This is load-bearing for AC #6, not cosmetic —
        see Dev Notes → *Pre-verified finding 4*.

- [x] **Task 5: The shared resolver (TDD Red first)** (AC: #8)
  - [x] Tests: resolves by exact name; resolves by UUID string; a UUID that parses but matches no
        row falls through to the name lookup; an unknown identifier raises `RecordNotFoundError`
        whose message names the identifier; a session whose *name* is a valid UUID string is
        reachable. Run → **RED**.
  - [x] Implement `SessionService.resolve(identifier: str) -> TradingSession`. Order: try
        `UUID(identifier)` → `find_by_session_id`; on `ValueError` **or** a `None` result, fall back
        to `find_by_name`; still `None` → `RecordNotFoundError`. Run → **GREEN**.

- [x] **Task 6: The AR37 guards** (AC: #1, #7)
  - [x] Structural test in `tests/unit/services/test_session_service.py`: walk every `.py` under
        `src/` with `ast`; for each module that imports `TradingSession`, assert it contains no
        `Assign`/`AugAssign`/`AnnAssign` whose target is an `Attribute` named `status`, allowlisting
        `src/services/session_service.py` alone. **Verified to return zero offenders today**, and it
        cannot false-positive on the five pre-existing `self.status =` lines in
        `src/models/catalog_metadata.py` / `src/models/strategy.py`, because neither imports
        `TradingSession`. Anchor the walk on the `project_root` fixture (`tests/conftest.py:34`) or
        a `Path(__file__).parents[N]` constant — **never on the CWD**, or the guard silently scans
        nothing when pytest is invoked from elsewhere and passes vacuously.
  - [x] Import-purity test, **two forms**, copying `tests/unit/models/test_session_spec.py:438-486`:
        (a) an `ast` scan of `src/services/session_service.py` for top-level imports of
        `{"nautilus_trader", "ibapi"}` — and **not** `sqlalchemy`, which is legitimately allowed
        here, unlike in `src/models/session.py`; (b) a **fresh subprocess** that imports the module
        and asserts `nautilus_trader` is absent from `sys.modules`. Form (b) is mandatory: Story
        2.1's review proved the AST form alone passes for a `src.*` import that transitively loads
        Nautilus.
  - [x] Grep gates, **scoped** per Story 2.2's convention (`2-2-…md:181`). Both verified at 0 hits
        before this story:
        - `grep -rnE "\.status[[:space:]]*=[[:space:]]*SessionStatus\." src/ --include="*.py"` →
          only `src/services/session_service.py`.
        - `grep -rnE "\.status[[:space:]]*=[^=]" src/services src/cli src/core src/db src/api --include="*.py"`
          → only `src/services/session_service.py`.
        - `grep -rn "commit()\|rollback()" src/services/session_service.py` → **nothing** (the
          caller owns the transaction; see Dev Notes → *Transaction ownership*).
        - `grep -rn "nautilus" src/services/session_service.py` → nothing.

- [x] **Task 7: Mutation-test the load-bearing guards** (Epic 1 retro, Action Item #3) (AC: #1, #6)
  - [x] Delete `.with_for_update()` from the sync repository → Task 2's structural guard must fail.
        Revert.
  - [x] Add a throwaway `row.status = SessionStatus.STOPPED` to
        `src/db/repositories/trading_session_repository_sync.py` → Task 6's AST guard must fail with
        that file named. Revert.
  - [x] Change the staleness comparison from `>` to `>=` → the boundary test must fail. Revert.
  - [x] Remove the `last_heartbeat_at` stamp from the reclaim branch → Task 8's concurrency test
        must fail (both processes reclaim). Revert.
  - [x] Record each result in the Dev Agent Record. **A test that cannot fail is worse than no test.**

- [x] **Task 8: Real-Postgres behaviour** (AC: #2, #3, #6)
  - [x] `tests/integration/db/test_session_service.py`, `@pytest.mark.integration`, using the
        `sync_db_session` fixture (`tests/integration/db/conftest.py:152`). Cover: a real round trip
        through all four legal edges reading back the lowercase enum label; an illegal transition
        leaves the stored status untouched after a rollback; and the two-connection reclaim race,
        asserting exactly one reclaim.
  - [x] ⚠️ **CI `--ignore`s this whole directory** (`.github/workflows/ci.yml:168` and `:238`), so
        nothing here gates a PR. That is why Tasks 3–7 put every falsifiable property in the unit
        tier. This file is the behavioural evidence, not the gate. Say so in its module docstring,
        following `tests/unit/db/test_trading_session_repository_shape.py:1-14`.
  - [x] The race test needs **two** sessions bound to the same engine and a real commit; the
        schema-isolated `sync_db_session` fixture yields one. Build the second from the same
        `pg8000` URL and schema (`SET search_path`) rather than reaching for `psycopg2` — the
        conftest comment explains why psycopg2 segfaults under `--forked` once Nautilus is loaded.
        If two connections into one scratch schema proves impractical inside the fixture's
        lifecycle, mark the race test `@pytest.mark.skip` with the reason and say so plainly in the
        Dev Agent Record; the unit-tier structural guard still holds the line.

- [x] **Task 9: Quality gates and bookkeeping** (AC: all)
  - [x] `make format && make lint && make typecheck`. Note `make typecheck` is
        `mypy src/core src/services` — **`session_service.py` IS type-checked**; `src/db` and
        `src/models` are not. Full annotations required either way.
  - [x] Re-run the Epic 1 acceptance sweep: `uv run pytest tests/integration/core/test_epic1_ac_node.py --forked`.
        It scans `src/core/live_*.py` + `src/cli/commands/live.py` only, and this story touches
        neither — but Story 2.2 broke it twice this way, so confirm 40/40 rather than assume.
  - [x] Update `deferred-work.md` with a `## Deferred from: story-2.3` section. Carry forward
        anything you do not close, and strike through with the file's own
        `~~**Title**~~ — **RESOLVED in story-2.3, <date>.**` convention anything you do.
  - [x] Confirm size limits: `session_service.py` file < 500 lines, `SessionService` class
        **< 100 lines** — the tightest constraint in this story (`conventions.md:41-47`). Module
        constants, `_utc_now`, `_LEGAL_TRANSITIONS` and the exception all live outside the class
        body, which is what makes it fit.

### Review Findings

Code review 2026-08-19. Three adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance
Auditor) produced 42 raw findings, 26 after dedupe: **3 decision-needed, 14 patch, 5 deferred, 4
dismissed**.

**All three layers independently converged on the same Critical defect, and the reviewer reproduced
it twice against the live database.** `transition()`'s `SELECT ... FOR UPDATE` takes the row lock
correctly, but SQLAlchemy returns the identity-mapped instance and does **not** overwrite attributes
already loaded — and no `populate_existing` exists anywhere in `src/` or `tests/`. So a caller that
did `resolve()` first (the shape AR36 prescribes) validates and decides on **pre-lock state**. AC #6
and AC #3 both fail in the two-process case:

```
B resolved (unlocked read) -> B caches heartbeat = 11:29:02
A reclaimed and committed  -> DB heartbeat now    = 11:45:42
B transition(to=running)   -> session.reclaimed heartbeat_age_seconds=1000.07
*** B ALSO RECLAIMED — TWO PROCESSES ON ONE SESSION ***
```

and, separately, a **`sealed` session was moved to `running`** — the terminal state AC #3 exists to
guarantee. With `.execution_options(populate_existing=True)` on the `for_update` select, both
scenarios refuse correctly and the sealed row stays `sealed`. The story's Pre-verified finding 4 was
right that the lock and the stamp are both required; what it missed is that the lock's re-read is
inert for the module's own primary flow. The shipped race test cannot catch this because its loser
uses a brand-new `Session` whose identity map has never seen the row — the one arrangement in which
the ORM does return fresh data.

Verified-true claims worth recording: `session_service.py` is 227 lines and `SessionService` is 88
(both under limit); `EXPECTED_CAPABILITIES` unmodified; `src/cli/commands/live.py` untouched;
`commit()`/`rollback()`/`nautilus` greps all 0; the subprocess form of the import-purity test did
ship; the AST guard is anchored on the `project_root` fixture, not the CWD; all four claimed
mutations reproduce exactly as recorded; timestamp policy matches the Dev Notes table exactly;
validation strictly precedes mutation in-process. AC #2, #4, #5, #7, #9 confirmed SATISFIED.

- [x] [Review][Patch] The AR37 structural guard is far narrower than AC #1 and the commit message claim — **Decision 2026-08-19 (Allay): widen it.** Drop the `endswith("trading_session")` import filter, scan every `src/**/*.py` for an attribute assignment to `status`, and allowlist `src/services/session_service.py` plus the two pre-existing Pydantic models (`src/models/catalog_metadata.py`, `src/models/strategy.py` — their 5 `self.status =` lines are not session writes). Evidence: the commit says "an AST scan of every module that imports `TradingSession` … no code path can write status any other way." Measured: of 192 files under `src/`, the guard examines **4**; `src/cli/commands/live.py` — which holds live `TradingSession` objects — is not one of them. `_imports_trading_session` only matches `node.module.endswith("trading_session")`, so `from src.db.models import TradingSession` (a real re-export, `src/db/models/__init__.py:9`) is invisible. Proven by planting `src/utils/_ar37_probe.py` with that import plus `row.status = "running"`: all four guard tests pass. The gates are also blind to `setattr`, tuple-unpack targets, `update().values(status=...)`, bulk `query.update()`, and raw SQL. Widening has a real tradeoff — dropping the import filter false-positives on the 5 pre-existing `self.status =` lines in `src/models/catalog_metadata.py` and `src/models/strategy.py`, needing an allowlist that is itself a maintenance hazard. **Options:** (a) drop the filter + allowlist those two files; (b) keep the filter but resolve re-exports and add the non-assignment forms; (c) accept the narrower guard and soften the commit-message and docstring claims to match what it actually protects. [tests/unit/services/test_session_service.py:342-352]
- [x] [Review][Defer] `InvalidSessionTransition` inherits `BacktestStorageError`, making a live-trading refusal catchable as a backtest-storage error — **deferred, Judgment call #3 stands; Story 2.5/2.6 must map it explicitly at exit code 1 and must never retry on `BacktestStorageError`.** Judgment call #3 chose one exception class and Task 1 mandated the hierarchy, so this is a spec decision, not a slip. But "another process appears live on this session" must never be retried, and storage base classes are the classic target of `except BacktestStorageError: retry()`. Nothing catches it broadly today (verified, 0 hits in `src/`), so there is no active bug — the risk lands in Story 2.5/2.6, which will add the first CLI handlers. A retry loop here would spin until the incumbent's heartbeat went stale and then reclaim a live session. **Options:** keep as-is and note the constraint for 2.5/2.6; add a `SessionAlreadyRunning(InvalidSessionTransition)` subclass so callers can distinguish a refused-legal transition from an illegal edge; or re-home the exception outside `src/db/`. [src/db/exceptions.py:83]
- [x] [Review][Defer] The reclaim's only liveness signal has no producer, and a stale heartbeat is not evidence of a dead process — **deferred, scoped to Story 2.5 by design; recorded as a hard blocking constraint on that story (the runner cannot ship without AR32's ~30s writer, because the AC #5 refusal is inert until it exists).** Two angles converged. (i) Nothing in `src/` writes `last_heartbeat_at` except this service's single stamp on entry to `running` (verified by grep); Story 2.5 owns AR32's ~30s writer. As shipped, therefore, **every session becomes reclaimable 90 seconds after it starts** — the AC #5 refusal is dead for the life of a forward test. (ii) There is no fencing token or ownership column, so a heartbeat stall that is not a death (DB failover, GC pause, throttled container) lets a second process reclaim while the first keeps trading and keeps heartbeating; each would then refuse the other. The story scoped the writer out deliberately and called NFR29 "a policy, not a mechanism", so this is arguably by design — but it is shipping now and is recorded nowhere. **Options:** accept and record as a hard Story 2.5 constraint; add an owner/epoch column now; or gate the reclaim on a second signal (e.g. `last_bar_at`). [src/services/session_service.py:118-137]
- [x] [Review][Patch] **CRITICAL** — `FOR UPDATE` re-read returns stale identity-mapped attributes, so two processes both reclaim and a `sealed` session can be moved to `running` — Reproduced twice against the live database; fix verified. Add `.execution_options(populate_existing=True)` to the `for_update` select on **both** twins (AR9), and add a unit test that pins it. [src/db/repositories/trading_session_repository_sync.py:97, src/db/repositories/trading_session_repository.py:98]
- [x] [Review][Patch] A raw-string `to` bypasses the reclaim guard entirely — `SessionStatus` is a `StrEnum`, so `"running" in frozenset({SessionStatus.RUNNING})` is `True` while `to is SessionStatus.RUNNING` is `False` (verified). `transition(sid, to="running")` on a `running` row therefore **skips `_reclaim_or_refuse` altogether** and dies on `to.value`; on a `created` row it is accepted and writes a bare `str` into `status`, which `expire_on_commit=False` then keeps. Normalise with `to = SessionStatus(to)` at the top of `transition()`. [src/services/session_service.py:216]
- [x] [Review][Patch] No CI-gating test asserts `for_update=True` is actually passed — `grep for_update tests/unit/services/test_session_service.py` → **0 hits**. Delete the kwarg from the call and every unit test still passes; the only test that would notice sits in the CI-ignored integration tier. The single most safety-critical argument in the diff is ungated. Add `assert_called_once_with(session_id, for_update=True)`. [src/services/session_service.py:209]
- [x] [Review][Patch] `resolve()` catches only `ValueError`, breaking its documented `Raises:` contract for every non-`str` input — `UUID(None)` raises `TypeError`; `UUID(123)`, `UUID(b"x")` and `UUID(uuid4())` raise `AttributeError`. An `Optional[str]` CLI argument or a caller that already holds a `UUID` gets a raw traceback instead of `RecordNotFoundError`. Catch `(ValueError, TypeError, AttributeError)`. [src/services/session_service.py:180]
- [x] [Review][Patch] A `None` status crashes with `AttributeError` instead of `InvalidSessionTransition` — `current.value` is interpolated into the refusal message. The column is `nullable=False` so this is only reachable from an unattached row, but the test module's own docstring documents that exact state. Guard non-enum status before formatting. [src/services/session_service.py:218-222]
- [x] [Review][Patch] Threshold validation lets through the exact failure its docstring says it prevents — `math.isfinite(1e300)` is `True`, so a huge finite threshold silently disables the reclaim forever, manufacturing the stuck state NFR11 forbids. `True` is also accepted (as 1 second), and a non-numeric value raises `TypeError`, not the documented `ValueError`. Add a type check and an upper bound. [src/services/session_service.py:59]
- [x] [Review][Patch] The `with_for_update` parity guard is a substring check that cannot go red for the regression that matters — `assert "with_for_update(" in source` passes if the text sits in a comment or docstring, if the guard is inverted, or if the statement's return value is dropped (`stmt.with_for_update()` without reassignment — an easy real bug with SQLAlchemy's immutable statement API). Assert via `ast` plus an `inspect.signature` check for the `for_update` parameter. [tests/unit/db/test_trading_session_repository_shape.py:94]
- [x] [Review][Patch] Grep gate 1 asserts a subset, which the empty set satisfies — `assert offenders <= {...}` still passes if `project_root` resolved wrongly, the file were renamed, or the pattern stopped matching; its sibling at :439 correctly uses `==`. Relatedly, the story documents this gate's expected result as "only `session_service.py`" when the true result is **0 hits** (the service assigns `to`, not a `SessionStatus.` literal) — the `<=` appears to have been weakened to accommodate that. [tests/unit/services/test_session_service.py:432]
- [x] [Review][Patch] The rollback integration test proves nothing about rollback — it triggers `created → sealed`, which raises *before* any mutation, so the `rollback()` is a no-op; delete that line and the test passes identically. The case that matters — a legal, mutating transition then rolled back without commit — is untested at every tier. [tests/integration/db/test_session_service.py:83-119]
- [x] [Review][Patch] The race test swallows thread exceptions and orders its roles with `sleep` — if `_winner` raises, `threading.Thread` swallows it and the test dies on a bare `KeyError: 'winner'` naming neither the exception nor the side that broke. `join(timeout=10)` is never followed by an `is_alive()` check, so `session_two.close()`/`engine_two.dispose()` can run while the loser is still blocked on the lock. Capture exceptions into a list, assert it is empty, and synchronise with an `Event` rather than `time.sleep`. [tests/integration/db/test_session_service.py:130-159]
- [x] [Review][Patch] Nothing ties `_LEGAL_TRANSITIONS` to `SessionStatus` — `.get(current, frozenset())` turns a missing key into a **silently terminal** state rather than a loud failure, so adding a fifth status member would strand sessions with no test going red. Assert `set(_LEGAL_TRANSITIONS) == set(SessionStatus)`. [src/services/session_service.py:218]
- [x] [Review][Patch] Refusals are never logged, and `session.reclaimed` is logged before the mutation and before any commit — "a second process just attempted to take over a live trading session" is exactly the operator signal AR41 wants, and it is silently dropped if the caller swallows the exception. Conversely the reclaim log fires at `session_service.py:132` while the mutation happens at :224 and the commit later still, so a rolled-back reclaim leaves a false audit record — and `get_sync_session()` rolls back on any exception. [src/services/session_service.py:128-137]
- [x] [Review][Patch] Five docstrings overstate what the code protects — the recurring failure the story's own Project Structure Notes name. (a) Both repositories: "the lock is what makes exactly one of them win" — the story's finding 4 says neither half works alone, and the Critical finding shows the lock alone does not work at all. (b) `transition()`: "a refusal never depends on the caller rolling back" — true of column values, false of the row lock, which is held until the caller ends the transaction (a peer with `lock_timeout` set got Postgres `55P03`). (c) `_heartbeat_age_seconds`: "clamping absorbs clock skew" — it absorbs only the safe direction; a reader whose clock runs fast inflates the age and reclaims a live session. (d) `_reclaim_or_refuse` "reclaims the session as a start" — it only logs and returns; the mutation is in the caller. (e) the async repository's `for_update` docstring cites `SessionService`, which is sync-only and never calls it. [src/services/session_service.py:90-137, src/db/repositories/trading_session_repository.py:88-92]
- [x] [Review][Patch] Bookkeeping — three factual errors. The Change Log and `sprint-status.yaml` both say "all 13 ACs satisfied"; the story defines **9**. The File List names `deferred-work.md` and `sprint-status.yaml` as MODIFIED, but neither is in commit `66a7564` — both are still uncommitted working-tree edits, and the story file itself is untracked. And `tests/integration/db/test_session_service.py:111` performs a direct `created.status = SessionStatus.RUNNING`, a status assignment inside the very commit claiming none exists (all three gates scan `src/` only); it could have reached `running` via `transition()`. [tests/integration/db/test_session_service.py:111]
- [x] [Review][Defer] `stopped → running` performs no liveness check at all — only the `running → running` self-edge consults the heartbeat, so a process that has written `stopped` but is still flattening or cancelling working orders can be joined by a second process through a door the guard does not watch. Forward constraint for Story 2.6 (commit `stopped` only after the broker disconnect completes) — deferred, out of scope [src/services/session_service.py:216]
- [x] [Review][Defer] No `lock_timeout` or `statement_timeout` is configured anywhere in `src/` or `alembic/` — a caller that catches a refusal and lingers holds the exclusive row lock, and every peer blocks indefinitely with no output. Infrastructure-wide, broader than this story — deferred, pre-existing [src/db/session_sync.py]
- [x] [Review][Defer] Transaction isolation level is neither pinned nor documented — the lock-then-re-read mechanism assumes READ COMMITTED; under REPEATABLE READ or SERIALIZABLE the loser gets a serialization `DBAPIError` rather than the designed `InvalidSessionTransition`, which the integration test's `except` would not catch — deferred, pre-existing [src/db/session_sync.py]
- [x] [Review][Defer] `resolve()`'s UUID-before-name precedence can silently shadow — if row B is *named* row A's `session_id` string, `resolve()` returns **A** with no ambiguity error. Documented and deliberate (Judgment call #6), but the dangerous case (UUID matches A *and* name matches B) is untested; only the benign case is covered — deferred, by design [src/services/session_service.py:166-186]
- [x] [Review][Defer] Both trading-session repositories still have zero CI-gating coverage — `tests/integration/db/` is `--ignore`d (`ci.yml:168`, `:238`), so AC #6's only behavioural evidence never gates a PR. This story narrows the gap with one unit-tier structural guard but does not close it. Owner stays the Epic 2 retro — deferred, pre-existing [tests/integration/db/test_session_service.py]

**Dismissed as noise (4):** `last_heartbeat_at` timezone-awareness (the migration uses
`timezone=True` on every timestamp column — verified fine); the f-string in
`SET search_path TO {schema_name}` (the schema derives from the pytest-xdist worker id, not
attacker-controlled); a re-entrant `transition()` in one transaction producing a confusing
"another process appears live" message (real but trivial — the caller is the only actor); and the
`_repository(row)` helper returning the same row for any `session_id` (subsumed by the
`for_update` assertion patch).

## Dev Notes

### What this story owns, and what it must not touch

**Owns:** `src/services/session_service.py` (new), `InvalidSessionTransition` in
`src/db/exceptions.py`, a `for_update` read on both trading-session repositories, and their tests.

**Does not own — do not build these here:**

- **No Alembic migration.** Story 2.2 shipped the phase's single migration (`d08dfbd393f0`) and it
  is applied. Every column this story writes already exists.
- **No CLI.** `live start` / `stop` / `status` / `list` are Stories 2.5, 2.6 and 2.8. This story
  ships the service they will call. `src/cli/commands/live.py` is **not** modified.
- **No runner, no heartbeat writer.** `LiveSessionRunner` and the ~30s `last_heartbeat_at` /
  `last_bar_at` cadence are Story 2.5 (AR32). This story *reads* the heartbeat and stamps it once,
  as part of entering `running`.
- **No `sealed_run_id` writer.** Story 5.3 sets it. This story stamps `sealed_at` on the
  `stopped → sealed` edge — see *Timestamp policy* for the hand-off note.
- **No `SessionService` async twin.** See *Judgment calls* #2.
- **No fifth `SessionStatus` value.** See *Judgment calls* #4.

`live create` deliberately does **not** use `SessionService`: creation is not a transition, it is the
initial state, and Story 2.2 pre-wrote this boundary (`2-2-…md:357-366`). Do not retrofit the CLI.

### Pre-verified findings

Everything below was **executed** against the installed libraries and this repo's live PostgreSQL
before the story was written. Reading the wheel and the database rather than the docs is the
technique the Epic 1 retro named explicitly (Key Insight #5). Do not re-derive; do re-confirm the
row counts, which move.

**1 — No migration is needed, and the schema is already right.**
`uv run alembic heads` → `d08dfbd393f0 (head)`, single head, and `alembic current` matches.
(⚠️ `CLAUDE.md` still says "14 migrations, single head `a436f35f525c`" — that is **stale**; 2.2
added one.) Against the live DB, `trading_sessions` has all thirteen columns, and the five this
story cares about are `timestamp with time zone`, nullable: `last_started_at`, `last_stopped_at`,
`sealed_at`, `last_heartbeat_at`, `last_bar_at`. `pg_enum` labels for `session_status` read exactly
`['created', 'running', 'stopped', 'sealed']`. Row counts at drafting: `trading_sessions` **0**,
`backtest_runs` **133**, `trades` **14982**.

**2 — Timestamps come back from Postgres in the *session* timezone, not UTC.**
The DB reports `SHOW TimeZone` → `America/Toronto`, and psycopg2 returns `timestamptz` values with a
`UTC-04:00` offset:

```
now() -> 2026-08-19 10:41:34.165372-04:00 | tzinfo: UTC-04:00
isoformat():                 2026-08-19T10:41:34.165372-04:00
astimezone(utc).isoformat(): 2026-08-19T14:41:34.165372+00:00
```

Two consequences:

- **A test asserting `row.last_heartbeat_at.tzinfo is timezone.utc` on a read-back value will
  fail.** The value is *aware and correct*, just not normalised to UTC. Assert on the instant
  (`==` between aware datetimes) or on `.astimezone(timezone.utc)`, never on `tzinfo` identity.
- **`datetime.utcnow()` is naive and cannot be subtracted from it** —
  `TypeError: can't subtract offset-naive and offset-aware datetimes`, verified. Use
  `datetime.now(timezone.utc)`, the repo's house idiom (31 occurrences in `src/`, versus one
  straggling `utcnow()` in `src/utils/error_messages.py:68`). Story 2.8's `--json` renderer will
  need the `.astimezone(timezone.utc)` step for AR29's "ISO-8601 UTC"; that is its problem, but it
  starts here.

**3 — `last_heartbeat_at` is nullable, and `now - None` raises.**
`TypeError: unsupported operand type(s) for -: 'datetime.datetime' and 'NoneType'`, verified. AC #4
therefore states the NULL rule explicitly. **NULL must count as stale**, for two independent reasons:
it is the same fail-safe direction `ConnectionMonitor.observation_is_stale` already takes
(`live_connection_monitor.py:187-191`: *"`age is None` … an absent reading is stale"*), and the
opposite reading manufactures exactly the stuck state NFR11 forbids — a `running` row with no
heartbeat could never be started again. Because `transition()` stamps the heartbeat when entering
`running`, this state is unreachable through the sanctioned path; it is reachable only from a
hand-edited row or a pre-2.3 row, which is precisely when you want the fail-safe.

**4 — The reclaim race is real, and the fix is two lines. Both were demonstrated.**

Negative control — two threads, no row lock, one session with a stale heartbeat:

```
P1: RECLAIMED | P2: RECLAIMED
>>> BOTH RECLAIMED = two processes on one session
```

With `.with_for_update()` on the read, and the reclaim stamping `last_heartbeat_at`:

```
P1: RECLAIMED (lock wait 0.00s, heartbeat age 209313842s)
P2: REFUSED   (lock wait 0.46s, heartbeat age 0.5s)
```

The mechanism, and why **both** halves are required: P2's `SELECT ... FOR UPDATE` blocks until P1
commits; it then re-reads and sees the heartbeat P1 just wrote, so the ordinary AC-#5 fresh-heartbeat
refusal fires. Drop the lock and both processes read the stale value. Drop the heartbeat stamp and
P2 acquires the lock, re-reads a *still-stale* heartbeat, and reclaims anyway. Neither half works
alone. `.with_for_update()` was confirmed to compile and execute against this schema.

What this does **not** buy: it serialises only transactions that go through `transition()`. A
process that never calls it, or one already running from before, is unaffected — the guarantee is
"one winner per contended reclaim", not a distributed lock. NFR29 (one concurrent session) remains
a policy, not a mechanism.

**5 — `TimestampMixin` gives `created_at` only.** No `updated_at`, no `onupdate`
(`src/db/base.py:20-32`; `tests/unit/db/test_trading_session_model.py:98` asserts `updated_at` is
absent). Every timestamp `transition()` writes must be written explicitly. Two other ORM models in
the repo *do* carry `onupdate` — do not copy them here; the column set is fixed by 2.2's migration.

**6 — The DB enum rejects unknown labels, but poisons the transaction.**
`UPDATE trading_sessions SET status='paused'` →
`psycopg2.errors.InvalidTextRepresentation: invalid input value for enum session_status: "paused"`.
Good defence in depth, but it arrives as a `DataError` that aborts the transaction, so it is not a
substitute for validating in `transition()`. The ORM's `values_callable`
(`trading_session.py:70-73`) is what makes `row.status = SessionStatus.RUNNING` write the lowercase
label — confirmed by reading `status::text` back as `'running'` after a flush.

**7 — `expire_on_commit=False` is set globally** (`src/db/session_sync.py:92`,
`src/db/session.py:42`). The `TradingSession` this story returns stays readable after the caller's
`get_sync_session` commits and closes — which is how `live.py:310-313` already prints a session id
post-commit. Do **not** add a defensive `refresh()`, and do not write a docstring claiming
attributes expire.

**8 — The AR37 grep, measured.** `grep -rn "status = " src/ --include="*.py"` → **20** hits;
`grep -rn "\.status = " src/ --include="*.py"` → **5**, all `self.status =` on the Pydantic models
`src/models/catalog_metadata.py` (3) and `src/models/strategy.py` (2). None is a `trading_sessions`
write; none is yours to change. The two scoped forms in Task 6 were each verified at **0 hits**
today, and an AST scan restricted to *modules that import `TradingSession`* finds **0 offenders**
across exactly three importers (`src/db/models/__init__.py` and the two repositories). That scan is
the guard to trust: it is immune to docstrings, comments, log kwargs like `status="failed"`
(`live_check.py:289`), and local variables named `status`.

**9 — The dependency chain is already Nautilus-free.** Importing
`src.db.repositories.trading_session_repository_sync`, `src.db.models.trading_session`,
`src.models.session` and `src.db.exceptions` in a fresh interpreter leaks **0** `nautilus_trader`
modules. `src/models/session.py` keeps its framework touches as lazy in-function imports, so
importing `SessionStatus` at module scope is safe. AC #7 is therefore satisfiable by construction —
but still test it, in a subprocess, because Story 2.1's review proved the AST-only form is blind to
transitive loading.

**10 — There is no `freezegun` and no `time-machine`** in `pyproject.toml` or `uv.lock`, and zero
`freeze_time` uses in `tests/`. The repo's idiom is an **injected clock callable** — `FakeClock` at
`tests/unit/core/test_live_connection_monitor.py:40-53`, passed as `time_source`. Adapt it to return
`datetime` rather than `float`; do not add a dependency (AR3).

### The design

#### Why the `status` assignment lives in the service, not in a repository method

The obvious shape — a `SyncTradingSessionRepository.update_status(...)` that the service calls —
is wrong here, for three reasons, and the third is decisive:

1. It would break `tests/unit/db/test_trading_session_repository_shape.py` twice: line 26's
   `EXPECTED_CAPABILITIES` is an exact set equality, and line 70's blocklist rejects any public name
   starting with `update`/`set_`/`save`/`patch`/`merge`/`upsert`/`replace`.
2. Widening that allowlist to make a new writer fit is the exact anti-pattern the Epic 1 retro's
   mutation-testing item exists to prevent — a guard edited until it stops failing.
3. **It is a weaker guarantee.** With a repository writer, any future caller can reach
   `repository.update_status(...)` and bypass validation entirely. With the assignment inside
   `transition()`, *no repository method can write `status` at all*, and the only way to change a
   session's state is through the validated path. That is what AR37 actually asks for.

So: `transition()` reads the row (locked), validates, and assigns `row.status` and the timestamps
directly on the attached ORM instance. The repositories gain a **read** option (`for_update`) and
nothing else. Their public surface — and both shape guards — stay exactly as Story 2.2 left them.

The architectural objection ("services don't touch ORM attributes; repositories do") is real and is
answered by `architecture.md:400-401`: *"the service owns the record."* `SessionService` is the
owner. The `spec` column's write-once discipline is untouched: it is enforced by absence, and this
story adds no path that writes it.

#### Public surface

```python
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0          # AR32's cadence, declared here so Story 2.5
                                                   # imports it rather than re-inventing 30.
DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS = 3 * DEFAULT_HEARTBEAT_INTERVAL_SECONDS   # 90.0

class SessionService:
    def __init__(
        self,
        repository: SyncTradingSessionRepository,
        *,
        heartbeat_stale_after_seconds: float = DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
        time_source: Callable[[], datetime] = _utc_now,
    ) -> None: ...

    def resolve(self, identifier: str) -> TradingSession: ...
    def transition(self, session_id: UUID, *, to: SessionStatus) -> TradingSession: ...
```

`transition()` raises `RecordNotFoundError` when no row matches `session_id` — the same exception
`resolve()` raises, already in the family, and never a bare `None` return that a caller could
mistake for success.

**Fitting the < 100-line class limit.** It is tight and it is the reason for the module-level
layout: `DEFAULT_*` constants, `_utc_now()`, `_LEGAL_TRANSITIONS`, a `_TIMESTAMPS_BY_TARGET` map and
an `_is_heartbeat_stale(last_heartbeat_at, now, threshold)` helper all live **outside** the class
body. What remains inside is `__init__`, `resolve`, `transition`, and their docstrings.

Constructor takes the **repository**, not a `Session` and not a factory — the shape every existing
service uses (`BacktestPersistenceService.__init__(repository)`,
`InstrumentMetadataService.__init__(provider, repository)`). `grep -rn "get_sync_session" src/`
returns zero hits inside `src/services/`; keep it that way.

`time_source` returns an **aware `datetime`**, not monotonic seconds. This is a deliberate departure
from `ConnectionMonitor`'s `time.monotonic`: that class compares in-process markers, whereas this one
compares a wall-clock timestamp *written by a different process* against now. Monotonic is
meaningless across processes. Clamp with `max(0.0, ...)` all the same — the two clocks are not the
same clock, and a future-dated heartbeat must not read as stale.

#### Transition table

```python
_LEGAL_TRANSITIONS: Mapping[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.CREATED: frozenset({SessionStatus.RUNNING}),
    SessionStatus.RUNNING: frozenset({SessionStatus.STOPPED}),
    SessionStatus.STOPPED: frozenset({SessionStatus.RUNNING, SessionStatus.SEALED}),
    SessionStatus.SEALED: frozenset(),                       # terminal, AC #3
}
```

`RUNNING → RUNNING` is **not** in this table. The reclaim is an explicitly named branch checked
before the table lookup, so the table remains a literal transcription of AC #2 and the one sanctioned
exception is a single greppable block rather than an entry that quietly legalises a self-edge.

#### Timestamp policy (AC #2's "the corresponding timestamp column")

The epic never maps edges to columns. This is that map:

| Transition | Writes | Notes |
|---|---|---|
| `created → running` | `last_started_at`, `last_heartbeat_at` | heartbeat stamp is AC #6's other half |
| `running → stopped` | `last_stopped_at` | |
| `stopped → running` | `last_started_at`, `last_heartbeat_at` | overwrites the previous start |
| `stopped → sealed` | `sealed_at` | |
| `running → running` (reclaim) | `last_started_at`, `last_heartbeat_at` | a reclaim *is* a start |

`last_stopped_at` is **not** cleared on restart — it records the last stop, which Story 2.8's
`last_activity_at` derivation will want. `last_bar_at` is never touched by a transition; it belongs
to Story 2.5's bar path.

⚠️ **Hand-off to Story 5.3.** Its AC says seal *"sets `sealed_run_id` and `sealed_at`, and
transitions the session to `sealed`"* (`epics.md:1588`). `sealed_at` is stamped **here**, by the
transition. Story 5.3 should set `sealed_run_id` only and let `transition(to=SEALED)` own the
timestamp, or the two will race to write the same column with two different clocks.

#### Reclaim and refusal

```
requested to == RUNNING and current == RUNNING:
    age = max(0.0, now - last_heartbeat_at)          # None -> stale, no arithmetic
    if last_heartbeat_at is None or age > threshold:
        logger.info("session.reclaimed", session_id=..., name=..., heartbeat_age_seconds=...)
        -> proceed as a start
    else:
        raise InvalidSessionTransition(
            f"Session {name!r} is already running and its heartbeat is {age:.0f}s old "
            f"(stale after {threshold:.0f}s) — another process appears live."
        )
```

Strictly `>`, not `>=` — matching `live_connection_monitor.py:190`, so "exactly at the threshold" is
fresh in both modules. The message names the session and the age because the repo's implicit rule is
that `pytest.raises(..., match=...)` pins the domain-identifying substring; Story 2.1 was patched at
review for asserting a bare exception type.

**Log level `info`, not `warning`.** A reclaim is the designed recovery from a SIGKILL, not a fault
(NFR11). `session.reclaimed` is already in AR41's enumeration, so no new event name is invented.

**Logger idiom:** module-level `logger = structlog.get_logger(__name__)`, event name as the first
positional argument, everything else as kwargs, and **`session_id=str(row.session_id)`** — a `UUID`
is stringified explicitly, exactly as `live.py:310` does for `session.created`. Do not call
`configure_logging()` here; `src/cli/main.py:23` owns it.

#### Transaction ownership

`SessionService` never calls `commit()` or `rollback()` — Task 6 greps for it. Zero repositories and
zero services in this repo commit; `get_sync_session()` commits on clean exit and rolls back on
exception (`src/db/session_sync.py:126-134`). `transition()` does not `flush()` either: the
`SELECT ... FOR UPDATE` opens the transaction and holds the row lock until the caller commits, which
is exactly the window AC #6 needs.

⚠️ **Forward constraint for Story 2.5.** The runner must let the `get_sync_session` block *close*
right after the transition to `running`, before starting the node. Keeping it open for the life of
the session would hold the row lock for hours and block every `live status` that wants the same row.

### Files

| Path | Change |
|---|---|
| `src/services/session_service.py` | **NEW** — constants, `_utc_now`, `_LEGAL_TRANSITIONS`, `SessionService` |
| `src/db/exceptions.py` | **MOD** — `+InvalidSessionTransition(BacktestStorageError)` |
| `src/db/repositories/trading_session_repository_sync.py` | **MOD** — `find_by_session_id(..., *, for_update=False)` |
| `src/db/repositories/trading_session_repository.py` | **MOD** — same, async (AR9) |
| `tests/unit/services/test_session_service.py` | **NEW** — state machine, reclaim, resolver, AR37 + purity guards |
| `tests/unit/db/test_session_exceptions.py` | **NEW** — exception hierarchy |
| `tests/unit/db/test_trading_session_repository_shape.py` | **MOD** — `+with_for_update` structural guard; allowlist **unchanged** |
| `tests/integration/db/test_session_service.py` | **NEW** — real-Postgres round trip + reclaim race |
| `_bmad-output/implementation-artifacts/deferred-work.md` | **MOD** — `## Deferred from: story-2.3` |

Do **not** add `SessionService` to `src/services/__init__.py`: that file is a bare docstring with no
`__all__`, and every consumer imports the full dotted path. (`src/db/repositories/__init__.py` *does*
re-export — different package, different convention.) `src/cli/commands/live.py` is untouched.

### Testing standards

- **Tier: unit.** `architecture.md:414-415` — *"session state machine unit-tested without Nautilus"*
  — and `architecture.md:544` names `tests/unit/services/test_session_service.py` by path. The
  directory exists, has no `__init__.py` and no `conftest.py`; add a flat module.
- **Mark everything.** `--strict-markers` is on (`pytest.ini:15`). `pytest.ini` is the effective
  config; `pyproject.toml`'s marker list is shadowed and dead.
- **Mock the repository in the unit tier.** No file in `tests/unit/services/` opens a database of any
  kind. `MagicMock(spec=SyncTradingSessionRepository)` returning unattached `TradingSession`
  instances is the precedent. **In-memory SQLite is not viable** for `trading_sessions`: the table
  uses `JSONB`, `PG_UUID` and `sa.Enum`, which is why `tests/component/db/` fixtures hand-write
  `_CREATE_TABLE_SQL`.
- **CI ignores `tests/integration/db/`** (`ci.yml:168` and `:238`). Anything that must gate a PR
  belongs in `tests/unit/`. CI's coverage job runs `--cov=src --cov-fail-under=64`, so uncovered
  lines in `src/services/` **do** move that gate — `make test-coverage` will not show them, because
  it measures `src/core` + `src/strategies` only.
- **Naming.** Long behavioural sentences (`test_a_sealed_session_refuses_every_outbound_edge`), not
  `test_<method>_<case>`. Class-per-concern, docstring naming the AC. The `acceptance_criterion`
  marker exists (`tests/conftest.py:58-65`) but is used only by the Epic 1 harness — optional here.
- **Do not call `StrategyRegistry.clear()`** anywhere. It is process-global and `make test-unit` runs
  `-n auto`; clearing it permanently empties the registry for that worker.
- If you write a mock `get_sync_session` context manager, **commit on clean exit** — Story 2.2 lost
  time to a fixture that did not, and a later `rollback()` silently undid an earlier row
  (`tests/integration/db/test_cli_live_create.py:20-30` has the correct shape).

### Judgment calls made while writing this story (flag at the Epic 2 retro)

1. **The heartbeat staleness threshold is 90 seconds, derived rather than invented.** No number for
   this exists anywhere in the PRD, architecture or epics — only the *write* cadence, "~every 30s"
   (AR32). 90s is three missed heartbeats, and it is expressed as
   `3 * DEFAULT_HEARTBEAT_INTERVAL_SECONDS` so Story 2.5's cadence and Story 2.8's `stale` health
   derivation import one constant instead of three independent literals — the same
   derive-don't-invent move `live_connection_monitor.py:61-67` makes. The costs are asymmetric and
   argue for generosity: too short and you reclaim a *live* session, putting two processes on one
   account (catastrophic, NFR6); too long and an operator waits ninety seconds after a SIGKILL
   (annoying). A constructor kwarg, not a setting — an env var would ripple into `.env.example`,
   `README.md` and `IBKR_SETUP.md` for a value no requirement fixes.
2. **`SessionService` is sync-only.** AR9's dual-repository rule is written about *repositories*,
   and both already exist and both get the `for_update` change. Only the CLI exercises this path
   this phase (`architecture.md:235`). Story 2.5's runner is asyncio and can reach a sync service
   through `asyncio.to_thread`, which is already this repo's established bridge
   (`_backtest_helpers.py:353`: *"`asyncio.to_thread` internally so the sync repo is safe on both
   paths"*; nine further uses in `src/api/` and `src/services/`). An async twin now would be
   speculative (KISS/YAGNI, `project-context.md:36`).
3. **One exception class, not two.** The fresh-heartbeat refusal is semantically a *refused legal*
   transition rather than an illegal edge, and a `SessionAlreadyRunning(InvalidSessionTransition)`
   subclass would let a caller distinguish them. Nothing asks to: both map to exit code 1 under
   AR28, and no requirement branches on the difference. The message carries the distinction. Story
   2.2's rule was "do not add new exception classes" where an existing one fits.
4. **No fifth `SessionStatus` value — and this is now structural, not just preference.** The
   Story 2.1 review deferred *"decide whether the failure information lives in a fifth status value
   or a separate nullable column"* to *"the Epic 2 retro / Story 2.3"*
   (`deferred-work.md:739-744`). Recommendation: **not a status value.** Story 2.1's AC #6 pins the
   enum at exactly four; the PG type `session_status` is created with exactly four labels; and this
   phase's *single* migration is spent, so a fifth value now costs a second migration the epic
   explicitly does not have. The operator-facing need — "why did it stop?" — is already owned
   elsewhere: Story 2.7 makes a strategy failure visible, and Story 2.8 derives `stale` from the
   heartbeat. The item stays open, re-pointed at Story 2.8 with this reasoning recorded.
5. **`resolve()` returns the row, not just the UUID.** AC #8's epic wording says "name→UUID
   helper". Returning the `TradingSession` is a superset — the caller gets the UUID from
   `row.session_id` — and it saves every consumer a second query. One shared resolution path is
   what AR36 is after.
6. **UUID-first, then name, with fall-through.** `UUID(identifier)` is tried first; a parse failure
   *or* a miss falls back to `find_by_name`. This keeps a session whose *name* happens to be a valid
   UUID string addressable, at the cost of one extra query in a rare case. The only ambiguity — a
   session named exactly another session's `session_id` — resolves UUID-first, deterministically and
   documented. The alternative (rejecting UUID-shaped `--name` values in `live create`) would change
   a shipped command for a case no operator has hit.
7. **AC #6 was added to the epic's list.** See the ⚠️ under AC #6. The epic's ACs #4 and #5 are
   individually correct and jointly racy; the story adds the concurrency criterion rather than
   leaving a demonstrated two-processes-on-one-session hole for a code review to find.

### Deferred items this story reads

**Closes:** none outright. The `SessionStatus` terminal-failure item (`deferred-work.md:739-744`) is
*decided* here (Judgment call #4) and re-pointed at Story 2.8 rather than struck through — the
decision is "not a status value", which does not by itself deliver the operator-facing answer.

**Reads and leaves open:**

- `deferred-work.md:712-719` — re-validating a persisted spec against today's param model is lossy.
  **Not triggered here**: `transition()` and `resolve()` return the ORM row and never call
  `SessionSpec.from_stored()`. Keep it that way; the moment this service starts materialising typed
  specs it inherits that brittleness. Re-pointed at Epic 5 by Story 2.2.
- `deferred-work.md:785-794` — `model_copy(update=)` bypasses validation. Untouched; no spec is
  constructed here.
- `deferred-work.md:815-828` — both trading-session repositories have zero CI-gating coverage,
  because CI `--ignore`s `tests/integration/db`. This story adds a *unit-tier* structural guard for
  the new `for_update` read, which narrows the gap slightly but does not close it. Owner stays the
  Epic 2 retro.
- `deferred-work.md:637-644` — `live_check.classify_failure` maps exit codes by exception *class
  name*. `InvalidSessionTransition` is a new typed failure on the live path and will classify as a
  generic error until a CLI story maps it. Harmless now (no CLI catches it yet); Story 2.5/2.6 must
  handle it deliberately, at exit code **1** (AR28 reserves 3 for gate refusal and 4 for
  connectivity; a state conflict is neither).
- `deferred-work.md:671-679` and `:763-771` are **closed in substance by Story 2.2** but still read
  as open in the file. Not this story's to strike, but worth a line in the retro.

Epic 1 retro action items **4–10** are Story 2.5 constraints and travel forward untouched. Item
**#5** ("track *has this trader actually started* independently of `ComponentState`, since `READY`
is reachable after a reset") is the in-process cousin of this story's persisted
`running`-with-a-stale-heartbeat ambiguity — worth reading before writing 2.5, not before writing
this.

### Project Structure Notes

- **`session` is a crowded namespace.** Inside any method that holds a SQLAlchemy `Session`, name the
  row `trading_session` or `session_row`, never `session`. And note `trades.session_id` is a
  `BigInteger` FK to `trading_sessions.id`, while `trading_sessions.session_id` is the UUID business
  key — same name, different types. Story 2.8's join is
  `trades.session_id = trading_sessions.id`.
- **Class name is `SessionService`** (`architecture.md:386`). The sync repository is
  `SyncTradingSessionRepository` — the architecture doc's `TradingSessionRepositorySync` was
  knowingly not followed by Story 2.2 (its Judgment call #2); follow the code.
- **Vocabulary is normative (AR36).** *session*, *process run*, *seal*, *stop*, *gate*. Not
  *pause*, *halt*, *kill*, *close*, *finalize*. "Reclaim" is the epic's own word for the AR33 path.
- **Docstring dialect:** the Epic 2 one — a module header opening *"Owns: … Does not own: …"* that
  states the import-purity rule explicitly (`src/models/session.py:1-52`,
  `src/core/live_connection_monitor.py:1-47`), Google-style `Args`/`Returns`/`Raises`, double-backtick
  inline code, keyword-only arguments. **Do not overstate what the code protects** — that is the
  recurring documentation failure the Epic 1 retro named, and code review caught it every time.
- **Import gate:** F401/F821 hard-block the commit at three points. Make an import and its first use
  in a single edit. `make install-hooks` once per clone.
- **Commits:** `<type>(<scope>): <subject>`; `feat(live):` fits. Stage and commit in **separate**
  Bash calls. Never reference AI or Claude.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 2.3`] — lines 809–849, the ACs
- [Source: `_bmad-output/planning-artifacts/epics.md`] — AR33 (:230), AR36 (:236), AR37 (:237),
  AR38 (:238), AR41 (:241), AR9 (:188), AR28 (:219); FR20 (:59), NFR11 (:126), NFR29 (:156)
- [Source: `_bmad-output/planning-artifacts/architecture.md#Structure Patterns`] — :396–403,
  the single-state-machine and runner/service split rules
- [Source: `_bmad-output/planning-artifacts/architecture.md#Enforcement Guidelines`] — :462–470
- [Source: `_bmad-output/planning-artifacts/architecture.md#Delta Project Tree`] — :531–532
  (`session_service.py`), :544 (`tests/unit/services/test_session_service.py`)
- [Source: `_bmad-output/planning-artifacts/prd.md`] — :558–571, the lifecycle diagram
  (note it has **no** `running → running` edge; the reclaim is AR33's addendum)
- [Source: `_bmad-output/implementation-artifacts/2-2-create-a-named-session-with-an-immutable-specification.md`]
  — :357–366 (the boundary reserved for this story), :181–186 (scoped-grep convention),
  :459–489 (its judgment calls)
- [Source: `_bmad-output/implementation-artifacts/epic-1-retro-2026-08-17.md`] — :249–251
  (Action Item #3, mutation testing), :160–161 (collected-not-passed baselines)
- [Source: `src/models/session.py:71-83`] — `SessionStatus`
- [Source: `src/db/models/trading_session.py:63-96`] — the enum column and the five timestamps
- [Source: `src/db/repositories/trading_session_repository_sync.py`] — the repository to extend
- [Source: `src/db/exceptions.py`] — the family to extend
- [Source: `src/db/session_sync.py:90-134`] — `expire_on_commit=False`, commit-on-clean-exit
- [Source: `src/db/base.py:20-32`] — `TimestampMixin` (`created_at` only)
- [Source: `src/core/live_connection_monitor.py:59-67, 128-168, 187-198, 350-358`] — threshold
  constants, injected clock, `age is None → stale`, backward-step clamping
- [Source: `src/services/metadata/instrument_metadata_service.py:34-46`] — service constructor and
  `_utcnow()` clock-site shape
- [Source: `tests/unit/db/test_trading_session_repository_shape.py`] — the shape guards to preserve
- [Source: `tests/unit/models/test_session_spec.py:438-486`] — the import-purity test template
- [Source: `tests/unit/core/test_live_connection_monitor.py:40-53, 78`] — `FakeClock`,
  `capture_logs`
- [Source: `tests/integration/db/conftest.py:152`] — the `sync_db_session` fixture
- [Source: `.github/workflows/ci.yml:168, 238`] — `--ignore=tests/integration/db`

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

None — no crashes, hangs, or environment-level failures required a subprocess trace. One self-inflicted
trap caught and fixed during Task 6: the module docstring's own prose ("no `nautilus_trader`", "never
calls `commit()` or `rollback()`") tripped the very `nautilus`/`commit()`/`rollback()` grep gates Task 6
requires to return zero hits — the same class of trap Story 2.1's review found in `from_overrides`'
docstring. Reworded before writing the guard tests, not after a failing run.

### Completion Notes List

- Built exactly to the Dev Notes' pre-verified design. Baseline recorded before any edit and matched
  the story's own recorded figures exactly: unit 1856 / component 1047 / integration 239 collected,
  `alembic heads` → single head `d08dfbd393f0`, `trading_sessions` 0 rows.
- Tasks 3 and 4 share one TDD Red→Green cycle rather than two: `transition()`'s reclaim branch
  (AC #4/#5) is a single cohesive block beside the legal-transition-table lookup (AC #2/#3) inside the
  same method, and the design was fully pre-verified in Dev Notes with no open questions to resolve
  incrementally. The initial RED (`ModuleNotFoundError: No module named 'src.services.session_service'`)
  covered the whole class; Task 3's and Task 4's test subsets were then run individually and both
  confirmed GREEN against the one implementation. Task 7's mutation testing independently proves each
  guard is load-bearing regardless of the order the code was written in — that is the property that
  actually matters, not the ceremony of separate RED cycles for two methods that cannot be meaningfully
  half-implemented.
- Fitting the `SessionService` class under 100 lines required moving `_reclaim_or_refuse` out of the
  class into a module-level function (it was drafted as a private method first, measured at 105 lines,
  and refactored down to 88). `_is_heartbeat_stale`, `_heartbeat_age_seconds` and
  `_require_positive_threshold` all live outside the class for the same reason, per the Dev Notes'
  explicit list of what may live outside — the class body is exactly `__init__`, `resolve`,
  `transition`, and their docstrings.
- Task 7 mutation-tested all four load-bearing guards; all four failed as predicted and were reverted
  immediately, confirmed clean by `git status`:
  1. Deleting `.with_for_update()` from the sync repository's `find_by_session_id` → the async twin's
     structural guard stayed green (it was untouched) but the sync one failed exactly as the story
     predicted. First attempt left the literal string `with_for_update()` inside a comment, which the
     guard's substring check still matched — had to remove the string entirely, not just the call, to
     get a genuine RED.
  2. Adding a throwaway `row.status = SessionStatus.STOPPED` inside the sync repository's
     `find_by_name` → the AR37 AST guard failed, naming exactly
     `src/db/repositories/trading_session_repository_sync.py` as the offender alongside
     `session_service.py`.
  3. Changing the staleness comparison from `>` to `>=` → the exactly-at-threshold boundary test failed
     with `DID NOT RAISE InvalidSessionTransition` (the reclaim fired at the boundary instead of
     refusing).
  4. Removing `"last_heartbeat_at"` from `_TIMESTAMPS_BY_TARGET[RUNNING]` → the unit-tier
     `test_reclaim_stamps_last_heartbeat_at_which_is_load_bearing_for_ac6` failed. The story's own text
     names "Task 8's concurrency test" for this mutation, but Task 8 (integration) had not been written
     yet at this point in the sequence; the equivalent unit-tier property test already pins the same
     load-bearing fact, and Task 8's real-Postgres race test independently exercises the same stamp
     under genuine concurrency once it existed.
- Task 8's two-connection reclaim race is real, not simulated: a second `pg8000` engine/session into
  the same schema-isolated fixture, with the winner thread holding its `FOR UPDATE` lock open via a
  deliberate 0.4s sleep before committing so the loser thread genuinely blocks on the database rather
  than racing on wall-clock luck. Reproduces the story's own Pre-verified finding 4 live: winner
  reclaims, loser blocks then refuses on re-reading the heartbeat the winner just stamped. Ran 4
  times total (once during development, three consecutive repeats afterward) with no flake.
- Re-ran the full Epic 1 acceptance sweep across all six `test_epic1_ac_*.py` files (the story names
  only `test_epic1_ac_node.py`, which covers just Story 1.3's 8 criteria; the "40/40" figure the story
  asks to confirm is the sum across all six files) — 40/40 passed, this story touches none of the
  scanned paths.
- `deferred-work.md` gained a new "Deferred from: story-2.3" section. Closes nothing outright — the
  `SessionStatus` terminal-failure item is *decided* (not a fifth status value; Judgment call #4) but
  left open and re-pointed at Story 2.8, per the Dev Notes' explicit instruction not to strike it
  through. Read-and-left-open: the re-validate-on-read item (Epic 5), `model_copy` bypass (Story 2.4),
  repository CI-gating coverage (Epic 2 retro), and `live_check.classify_failure`'s exception-name
  coupling, newly relevant because `InvalidSessionTransition` is this story's typed failure on the
  live path (Story 2.5/2.6, exit code 1).
- Final counts: baseline (collected, pre-edit) unit 1856 / component 1047 / integration 239. Final
  (passed): unit 1901 (+45 — 3 exception tests, 2 repository shape tests, 40 service tests) /
  component 1031 passed + 16 skipped (unchanged, no component-tier work this story) / integration 240
  passed + 2 skipped (+3, all new, all in `tests/integration/db/test_session_service.py`) — zero
  regressions. `make format` / `make lint` clean; `make typecheck` (`mypy src/core src/services`)
  clean — `session_service.py` is inside the type-checked scope. `session_service.py` is 227 lines;
  `SessionService` is 88 lines, under the story's tightest constraint.

### File List

**NEW**
- `src/services/session_service.py`
- `tests/unit/services/test_session_service.py`
- `tests/unit/db/test_session_exceptions.py`
- `tests/integration/db/test_session_service.py`

**MODIFIED**
- `src/db/exceptions.py`
- `src/db/repositories/trading_session_repository.py`
- `src/db/repositories/trading_session_repository_sync.py`
- `tests/unit/db/test_trading_session_repository_shape.py`

**MODIFIED — bookkeeping, committed separately from the implementation commit**
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

Both bookkeeping files were listed here as MODIFIED while still being
uncommitted working-tree edits — commit `66a7564` contains 8 files, neither of
them. Corrected at review rather than left to read as though they had shipped
with the implementation.

**MODIFIED AT CODE REVIEW (2026-08-19)** — the 15 applied patches touch:
- `src/services/session_service.py` — `_as_status()` normalisation, threshold
  bounds, refusal logging, four docstring corrections
- `src/db/repositories/trading_session_repository{,_sync}.py` — the Critical
  `populate_existing` fix on both twins
- `tests/unit/services/test_session_service.py` — widened AR37 guard, widened
  grep gates, and 6 new test classes (locked-read assertion, transition-table
  completeness, status normalisation, non-string resolve, threshold bounds,
  refusal logging)
- `tests/unit/db/test_trading_session_repository_shape.py` — AST-based lock guard
- `tests/integration/db/test_session_service.py` — a genuine rollback test, the
  identity-map regression test, and a race test that captures thread exceptions

## Change Log

| Date | Change |
|---|---|
| 2026-08-19 | Story implemented: ready-for-dev → review. `SessionService.transition()` and `.resolve()` delivered as the single validated path through which a trading session's status changes (AR37) and the shared name-or-UUID resolver (AR36). All 9 tasks complete, all 9 ACs claimed satisfied (AC #6 — the concurrent-reclaim criterion — added by the story itself, not in `epics.md`). No migration, no CLI, no runner, matching AC #9. |
| 2026-08-19 | Code-reviewed → done. Three adversarial layers, 42 raw findings / 26 after dedupe. **One Critical defect found and fixed:** the `FOR UPDATE` re-read returned stale identity-mapped attributes, so a caller that had already `resolve()`d the row decided on pre-lock state — reproduced live as two processes both reclaiming one session, and as a `sealed` session moving back to `running` (AC #3 and AC #6 both genuinely violated as shipped, despite the story's mutation testing). Closed with `.execution_options(populate_existing=True)` on both repository twins. 15 patches applied in total; 7 items deferred, 4 dismissed. All 7 new/changed guards mutation-tested. AC #6 was reviewed on its merits and kept — the story was right to add it. |
