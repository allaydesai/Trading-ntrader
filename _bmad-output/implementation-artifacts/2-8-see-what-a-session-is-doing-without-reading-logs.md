# Story 2.8: See What a Session Is Doing Without Reading Logs

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want status and list commands that tell me whether a session is trading, idle, degraded, or dead,
so that I can check in after two days away and know immediately whether anything needs my attention.

[Source: `_bmad-output/planning-artifacts/epics.md:996-1000`, verbatim]

This is the last story of Epic 2 and the **reader** for everything the epic has written: Story 2.2's
columns and `ix_trading_sessions_status` index were added "for Story 2.8's `list`", Story 2.3 set the
90s staleness threshold "so Story 2.5's cadence and Story 2.8's `stale` health agree", Story 2.5
produces the `last_heartbeat_at`/`last_bar_at` data, and Story 2.7 wrote `runtime_flags` explicitly
so this story can read it back ("this story **writes** `runtime_flags`; it does not read it back for
display. Story 2.8 reads it in the same single-row query it already needs" —
`2-7-…md:669-673`). Nothing after this story depends on it; Epic 3's Story 3.7 later widens the
status surface for rejections.

## Acceptance Criteria

The epic gives this story six ACs (`epics.md:996-1036`). Four more are added here, each traceable to
a deferred-work action explicitly routed to this story or to a Story 2.7 handoff. AC #2 resolves the
one question `deferred-work.md` leaves open for this story ("how `degraded` covers both senses AR32
names") — the resolution is recorded as Judgment call #1 in Dev Notes.

1. **`ntrader live status <session>` answers from the database alone.**
   **Given** `ntrader live status <session>`
   **When** it is run from a **different process** than the runner
   **Then** it reports state, closed-trade count, open positions, last activity, and derived
   health — answering all of it from the database, with no dependency on the runner's memory
   (FR22, NFR23, AR32). Concretely:
   - `<session>` is resolved by the shared `SessionService.resolve()` (name or UUID, AR36) — no
     second resolver.
   - *state* is the `SessionStatus` enum value (`created`/`running`/`stopped`/`sealed`), rendered
     as its bare `StrEnum` value.
   - *closed-trade count* and *open positions* per AC #8.
   - *last activity* (`last_activity_at`) is the greatest non-NULL of `last_heartbeat_at`,
     `last_bar_at`, `last_started_at`, `last_stopped_at`, `sealed_at` — NULL for a `created`
     session that has never started, rendered as `never` in text output and `null` in `--json`.
   - The command never constructs a `TradingNode`, never imports `live_session_runner`, and works
     with the runner process absent entirely.

2. **Health derives from five values with a pinned precedence, at query time only.**
   **Given** health derivation
   **When** a session's status, heartbeat, bar recency, and `runtime_flags` are evaluated
   **Then** health resolves to exactly one of `stopped`, `stale`, `degraded`, `trading`, `idle`
   (FR49, NFR24, AR32), evaluated in this precedence order:
   1. `stopped` — status ≠ `running` (covers `created`, `stopped`, `sealed`).
   2. `stale` — status = `running` and the heartbeat is stale: `last_heartbeat_at` is NULL **or**
      its age exceeds the threshold **strictly** (`>` not `>=`, exactly-at-threshold reads fresh —
      matching `_is_heartbeat_stale`, `src/services/session_service.py:159-171`). The threshold is
      `DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS` (90.0, = 3 × `DEFAULT_HEARTBEAT_INTERVAL_SECONDS`)
      imported from `src/services/session_service.py:49` — **never a new literal**.
   3. `degraded` — status = `running`, heartbeat fresh, and `runtime_flags` records an impairment
      per AC #4.
   4. `trading` — status = `running`, heartbeat fresh, no impairment, and `last_bar_at` is recent:
      non-NULL with age ≤ `DEFAULT_BAR_FRESH_AFTER_SECONDS` (300.0), a new constant pinned equal
      to `DEFAULT_NO_BARS_AFTER_SECONDS` (`src/core/live_session_steady_state.py:90`) by a drift
      test.
   5. `idle` — status = `running`, heartbeat fresh, no impairment, `last_bar_at` NULL or older
      than the bar-fresh threshold.
   **And** health is always derived at query time, never stored: no new column, no migration, no
   `status =` assignment anywhere this story touches, and a test proves two `status` invocations
   straddling the staleness threshold flip the rendered health without any intervening write.
   **And** future-dated timestamps clamp to age 0 (fresh), matching `_heartbeat_age_seconds`
   (`session_service.py:142-156`).

3. **Silence is distinguishable from death.**
   **Given** a session that is running but has not traded
   **When** status is queried
   **Then** it reports `idle`, distinguishable from a session that has died (NFR24) — and the
   converse: a SIGKILLed process (row still `running`, heartbeat frozen) reports `stale`, never
   `idle` or `trading`.

4. **`degraded` covers both senses AR32 names, from one derivation.**
   **Given** a running session with a fresh heartbeat
   **When** `runtime_flags` is non-NULL and records either impairment
   **Then** health is `degraded` when **(a)** `failed_strategies` is non-empty or `all_failed` is
   true (Story 2.7's contained-strategy sense — the writer is live today), or **(b)** a
   `connection_lost_at` key is present (AR32's connection-lost sense — the reader is wired by this
   story; **no writer exists yet and this story must not add one**: reading connection state is
   Epic 4's broker-authoritative work, and `ConnectionMonitor.trading_permitted` /
   `confirm_state_reestablished` stay unread per `deferred-work.md` "Action for Epic 4").
   **And** the dormant `connection_lost_at` branch is pinned by a unit test whose docstring says
   Epic 4 supplies the writer, following Story 2.6's pinned-residual pattern.
   **And** the derivation tolerates unknown `runtime_flags` keys and any `v` value without
   crashing — `v` is bumped only when a key's *meaning* changes (`session_service.py:407-410`);
   render what is understood, ignore the rest.
   **And** the false green Story 2.7 documented cannot render: a session whose every strategy is
   dead (`all_failed: true`) but whose heartbeat and `last_bar_at` keep advancing (the runner's
   own `note_bar` subscribes before any strategy) reports `degraded`, never `trading` (pinned by
   test).

5. **A contained strategy failure is visible from another process.**
   **Given** a strategy that has failed
   **When** the operator queries session status
   **Then** the failure is visible rather than silent (FR49 — this is Story 2.7's epic AC, scoped
   there to this story): `status` renders each `failed_strategies` entry (`spec_strategy_id`,
   `strategy_id` when present, `error_type`, `handler`, `at`) and a distinct line when
   `all_failed` is true, using AR36-sanctioned vocabulary (*contained*, *failed*, *degraded* —
   never *halted*, *killed*, *paused*), mirroring `_print_contained_failures`
   (`src/cli/commands/live.py:480-533`).
   **And** `detail` strings are rendered verbatim with `markup=False, highlight=False` — they were
   already redacted at the catch site (NFR26); re-masking with `mask_account(text)` destroys the
   payload (`'***ged'`) and is forbidden.
   **And** because `runtime_flags` is cleared only on `-> running`, a *stopped* session's failures
   remain readable — the evidence survives to the moment the operator investigates.

6. **`ntrader live list` renders every session deterministically.**
   **Given** `ntrader live list`
   **When** it is run
   **Then** it renders a Rich table of all sessions with state and accumulated trade count,
   matching existing CLI table conventions (FR21), in `find_all()`'s pinned order
   (`created_at DESC, id DESC` — newest first; the ORDER BY exists for this story,
   `trading_session_repository_sync.py:125-142`), including at least: name, `session_id`, state,
   health, closed-trade count, and last activity. An empty table prints a first-party
   "no sessions" line, exit 0.

7. **`--json` on both commands, with the AR29 contract exact.**
   **Given** either command with `--json`
   **When** output is produced
   **Then** it emits plain objects with snake_case keys, ISO-8601 UTC timestamps, and money as
   Decimal-strings (FR51, AR29). `status --json` carries **exactly** the seven top-level keys
   `session_id`, `name`, `status`, `closed_trade_count`, `open_positions`, `last_activity_at`,
   `health` — pinned by a set-equality test, not membership. `list --json` emits a JSON array of
   objects, one per session, whose keys are drawn from the same vocabulary. Timestamps come from
   tz-aware UTC datetimes via `isoformat()` (the `runtime_flags` `at` convention); nothing routes
   through `utcnow()`. No money field exists this story (`closed_trade_count`/`open_positions` are
   ints); the Decimal-string rule is recorded for the seal-time keys that will need it.
   `--json` output goes to stdout unpolluted — no Rich markup, no log lines interleaved.

8. **Trade counts come from `trading_sessions` joined to `trades`, never `backtest_runs`.**
   **Given** trade counts before any seal
   **When** they are read
   **Then** they come from `trading_sessions` joined to `trades` — no runs row exists until seal
   (AR5) — with the join on the **typed key**: `trades.session_id` is a `BigInteger` FK to
   `trading_sessions.id` (the internal PK), **not** the UUID business key
   (`src/db/models/trade.py:93-98` — "same name, different types", the hazard Story 2.3 flagged).
   `closed_trade_count` = rows with `exit_timestamp IS NOT NULL`; `open_positions` = rows with
   `exit_timestamp IS NULL` (the null-exit open-position shape). Epic 3 owns live trade
   persistence, so today every session honestly reports 0 and 0 — the queries are still real and
   are proven against inserted rows in the DB tier.

9. **This story is a pure reader, and the guards prove it.**
   **Given** the full diff
   **When** the standing scans run
   **Then**: AR37's single-assigner scan still finds `session_service.py` as the only module
   assigning `TradingSession.status` (set equality, `tests/unit/services/test_session_service.py:524-557`);
   no new module imports `SessionRecordPort` or writes through it; the CLI status path imports no
   Nautilus and never touches `live_session_runner`; no Alembic migration is added (every column
   read already exists; head stays `b7c419e2a3d8`, single); and any repository method added ships
   on **both** twins (AR9) with `EXPECTED_CAPABILITIES` updated in
   `tests/unit/db/test_trading_session_repository_shape.py:27`.

10. **Exit codes stay inside AR28's table.**
    **Given** failures
    **When** either command exits
    **Then**: success is 0 (including an empty list and a session whose health is `stale` —
    reporting bad news is a *successful report*); an unknown session is `RecordNotFoundError`,
    already mapped to exit 1 with a first-party message via the existing
    `classify_failure`/`failure_message`/`EXIT_CODES` (`src/core/live_check.py:86-160`) — reused,
    not duplicated; bad invocation is Click's own exit 2. **No new exception names** enter
    `_OUTCOME_BY_EXCEPTION_NAME` or `_SAFE_MESSAGE_EXCEPTION_NAMES` (the marker-protocol revisit
    is overdue and belongs to the Epic 2 retro, not here), and no exit code outside
    0/1/2/3/4 is invented.

## Tasks / Subtasks

- [x] Task 1: Re-verify the story's factual baseline before writing any code (AC: all)
  - [x] Record collected baseline counts per tier (unit / component / integration --forked / e2e)
        exactly as Story 2.7 did — baselines are *collected* counts, pre-edit
  - [x] Re-confirm: `runtime_flags` write shape at `session_service.py:498-519` (`v`, `all_failed`,
        `failed_strategies`, `**existing` carry-forward); the `-> running` clear at `:324-332`;
        `trades.session_id` typed as BigInteger→`trading_sessions.id`; `find_all()` ordering;
        `RecordNotFoundError` already in the outcome map; `SessionService` class at 98/100 by AST;
        `live.py` at 533 lines
  - [x] Verify no file named `test_live_session_health.py` or `test_live_status_cli.py` exists
        anywhere in the tree (basename-uniqueness rule)
- [x] Task 2: Health domain — failing tests first, then `src/core/live_session_health.py` (AC: 2, 3, 4)
  - [x] Write the truth-table unit tests (new `tests/unit/cli/…` is wrong tier — put them in
        `tests/unit/core/test_live_session_health.py`): all five values, the precedence order, the
        strict-`>` boundary (age exactly 90.0 → fresh), NULL heartbeat → `stale`, future timestamp
        → clamp to fresh, NULL `last_bar_at` → `idle`, bar age exactly 300.0 → `trading`,
        `all_failed` false-green pin, `failed_strategies` non-empty pin, dormant
        `connection_lost_at` pin (docstring: Epic 4 writes it), unknown-keys/`v`-tolerance pin,
        derived-not-stored pin (clock-stepped double derivation, zero writes)
  - [x] Implement: `SessionHealth` StrEnum (`trading/idle/degraded/stale/stopped`),
        `DEFAULT_BAR_FRESH_AFTER_SECONDS = 300.0`, and a pure
        `derive_health(status, last_heartbeat_at, last_bar_at, runtime_flags, *, now, …thresholds)`
        taking primitives only (AR38 discipline: no SQLAlchemy, no Nautilus, no `src.services`
        import — this module must survive `TestImportPurity` if added there). Import the 90s
        threshold's *value* via a parameter default sourced from one place — see Dev Notes on the
        import direction problem — never restate the literal
  - [x] Add the drift pin: `DEFAULT_BAR_FRESH_AFTER_SECONDS == DEFAULT_NO_BARS_AFTER_SECONDS`
  - [x] Add a `StatusReport` frozen dataclass (primitives only) + pure builders/renderers:
        `build_status_report(...)`, `render_status(report) -> str`,
        `status_json_payload(report) -> dict` — the `render_report` precedent from
        `live_check.py:441-478` (renderer in core, printing in CLI). If the module approaches the
        500-line cap, the pre-agreed split is renderers into `src/core/live_status_render.py`
- [x] Task 3: Repository read surface — both twins (AC: 8, 9)
  - [x] Failing tests first in `tests/integration/db/test_trading_session_repository.py` (sibling
        file; real Postgres; evidence-not-gate — see Dev Notes) proving closed/open counts against
        inserted `trades` rows joined on `trading_sessions.id`, including the
        typed-key mutation check (join on UUID must fail or return nothing)
  - [x] Add `trade_counts_by_session(...)` (recommended shape: one grouped query returning
        `{trading_sessions.id: (closed_count, open_count)}`; single-session variant acceptable —
        NFR: listing has no perf target) to **both** `TradingSessionRepository` and
        `SyncTradingSessionRepository` in the same commit
  - [x] Update `EXPECTED_CAPABILITIES` in `test_trading_session_repository_shape.py:27` (equality
        set — both twins must gain the same name)
- [x] Task 4: `live status` command (AC: 1, 5, 10)
  - [x] Failing CLI tests first in new `tests/unit/cli/commands/test_live_status_cli.py`:
        `CliRunner`, patch-target constants `_STATUS_GET_SYNC_SESSION` / `_STATUS_SESSION_REPO`
        pointing **into the new module**, row stand-ins with **every branched field set
        explicitly** (bare MagicMock attributes are truthy — the standing trap,
        `test_live_cli.py:893-903`)
  - [x] Implement `status` in new `src/cli/commands/live_status.py`: resolve via
        `SessionService.resolve()` inside one short-lived `get_sync_session()` block (the
        `claim_session` shape, `live_start.py:31-54`, minus the transition), fetch counts, call
        `derive_health`/`build_status_report`, print via module `console` with
        `markup=False, highlight=False` for anything third-party-tainted; failures exit through
        the existing `classify_failure`/`failure_message`/`EXIT_CODES` with a
        `live status failed: …` prefix
  - [x] Render the session's `trader_id` in the **human-readable** output, derived via the
        framework-free `src/core/live_trader_id.py` (Story 2.4 kept it framework-free for exactly
        this — `2-4-…md:471`); do NOT add it to `status --json` (AR29's key set is exact)
  - [x] Register in `live.py` via `live.add_command(...)` (2 lines — `live.py` is over cap at 533;
        command bodies must NOT land there)
  - [x] No module-scope `get_settings()` in the new module (the blast-radius item —
        `deferred-work.md` §story-2.5: a settings validation error must not kill `--help`)
- [x] Task 5: `live list` command (AC: 6, 10)
  - [x] Failing tests first: table renders in `find_all()` order (assert **ordered list**, never
        set membership), empty-table line, exit 0 both ways
  - [x] Implement `list` in `live_status.py` using `find_all()` + grouped counts + per-row
        `derive_health`; Rich `Table` matching existing CLI table conventions (survey
        `src/cli/commands/` for the established `rich.table.Table` idiom before writing)
- [x] Task 6: `--json` on both (AC: 7)
  - [x] Failing tests first: **set equality** on the seven `status --json` keys; `json.loads`
        round-trip on captured stdout; ISO-8601 UTC offsets present; `list --json` is an array;
        no Rich/ANSI pollution in `--json` mode
  - [x] Implement: `--json` flag on both commands emitting via `json.dumps` (stable key order
        acceptable but not contractual), payloads built by the pure core builders
- [x] Task 7: Degraded-strategy visibility (AC: 4, 5)
  - [x] Failing tests first: a row whose `runtime_flags` carries two `failed_strategies` entries
        renders both with `spec_strategy_id`/`error_type`/`handler`/`at`; `all_failed: true` gets
        its distinct line; a stopped session's failures still render; vocabulary asserted
        (*contained*/*failed*/*degraded* present; forbidden stems absent per AR36 — with the
        `closed_trade_count` carve-out documented, see Dev Notes)
  - [x] Implement in the renderer (core), not the CLI wrapper
- [x] Task 8: Guard-list integration for the new modules (AC: 9)
  - [x] Widen `LIVE_MODULE_GLOBS` in `tests/component/core/test_live_dependency_invariance.py:36`
        from `"src/cli/commands/live.py"` to `"src/cli/commands/live*.py"` — this closes the
        disclosed gap that already leaves `live_start.py` unswept; confirm `live_start.py` and the
        new module pass before committing
  - [x] Add `src/cli/commands/live_status.py` to `EXEMPT_MODULES` in
        `tests/unit/core/test_live_node_never_exits.py:74-78` (it raises `SystemExit`); keep the
        disjointness assertion green
  - [x] `src/core/live_session_health.py` (and `live_status_render.py` if split): swept
        automatically by the Epic 1 `_live_module_sources()` glob — when the dependency guard
        fires on a new stdlib import, add the name **by hand with the reason inline** (the
        `socket`/`signal`/`re` precedent), never via `sys.stdlib_module_names`; add the module to
        `TestImportPurity.MODULES` in `tests/component/core/test_session_runner_phases.py:1211-1221`
  - [x] Do **NOT** add either new module to `STOP_PATH_MODULES` in
        `test_live_stop_path_is_inert.py` — a status renderer legitimately names `sealed_at` and
        the `sealed` status, which `FORBIDDEN_SEAL_NAMES` would trip; status is not on the stop
        path
  - [x] Extend the AR36 CLI vocabulary test to the new module with the trading-term exemption made
        explicit (assert forbidden stems absent from operator-facing strings *except* the
        `closed trade` / `closed_trade_count` trading term — the ban is scoped to lifecycle
        synonyms, per the Story 1.2 precedent)
- [x] Task 9: Resolve-precedence pin (AC: 1)
  - [x] One test pinning `resolve()`'s documented precedence: a row *named* with another row's
        `session_id` string resolves to the **UUID match** (lookup order
        `find_by_session_id` → `find_by_name`, `session_service.py:561-575`); docstring cites the
        deferred-work UUID-shadowing item; rejecting UUID-shaped names at creation stays open for
        the retro
- [x] Task 10: Mutation sweep — break, observe red, revert, re-scan (AC: 2, 4, 6, 7, 8)
  - [x] Named mutations, each observed red then reverted, with the observed failure recorded in
        the Debug Log: (1) staleness `>` → `>=`; (2) precedence swap (degraded before stale);
        (3) trading/idle swap; (4) drop the `all_failed`/`failed_strategies` branch;
        (5) count NULL-exit rows as closed; (6) join `trades` on the UUID; (7) drop the list
        ordering; (8) rename `last_activity_at` in the payload; (9) delete the
        `connection_lost_at` branch; (10) hardcode `DEFAULT_BAR_FRESH_AFTER_SECONDS = 60.0`
        (drift pin must catch)
  - [x] Re-scan the tree for leftover mutation markers (`grep -rn "MUTATION" src/ tests/` → zero)
- [x] Task 11: End-to-end proof, operator procedure, and closeout (AC: all)
  - [x] End-to-end: `create → status → list` against a real database. Architecture names
        `tests/e2e/test_live_cli.py`, but that basename already exists at
        `tests/unit/cli/commands/test_live_cli.py` and the basename-uniqueness rule forbids a
        second — name it `test_live_status_e2e.py` (or similar, verified unique) and note the
        deviation; follow the existing e2e DB harness if one exists; if the e2e tier has no
        Postgres fixture, land it beside the repository tests in `tests/integration/db/` and
        disclose the placement in Completion Notes
  - [x] Append **Procedure P9** to `docs/qa/phase3-live-verification.md`: `live status` against a
        genuinely running session from a second terminal (`trading`/`idle` observed), then
        `kill -9` and observe `stale` at the next `status`; note P8 REMAINS NOT RUN and is still
        the gate before Epic 2 closes — P9 does not replace it
  - [x] Full validation: `make format && make lint && make typecheck`; unit, component,
        integration `--forked`; Epic 1 acceptance sweep still 40/40; baselines re-measured and
        deltas recorded
  - [x] Update `deferred-work.md`: close the "how `degraded` covers both senses" half (decision
        recorded here); record the fencing-column disposition per Judgment call #3; record the
        `SessionService` class-size resolution (health never entered the class); append the
        repository CI-gating note to the **existing** item (never start a second one); append any
        AR41 observations to the existing AR41 item
  - [x] Update story File List, Change Log, Completion Notes; set Status to `review`

### Review Findings

Adversarial review, 2026-08-24 (Blind Hunter / Edge Case Hunter / Acceptance Auditor; 33 raw
findings, deduplicated to 25, 1 dismissed). No layer failed. **The production code is very nearly
right — 17 of the 24 surviving findings are about tests and docs, not behaviour.** The dominant
theme is guards that cannot fail: the story's Debug Log records ten mutations broken and observed
red, but three separate properties the ACs single out are provably unguarded, each confirmed here
by mutation rather than asserted.

Two Blind Hunter findings were dismissed or downgraded once project context was applied: the
naive-datetime crash (columns are `TIMESTAMP(timezone=True)`, drivers return aware datetimes) and
the `str`-status identity comparison (the ORM returns real `SessionStatus` members; kept as a Low
latent-inconsistency patch, not a live defect).

**All 3 decisions resolved with Allay and all 21 patches applied the same day.** The two deferred
items are in `deferred-work.md` under `code review of 2-8…`.

**The fixes were themselves mutation-proven**, to the standard this story set — twelve mutations,
each broken, run, and reverted by a script rather than by hand. **Two survived the first pass and
were caught before closeout**, which is the same defect class this review was convened over:

1. *Drop the degraded fallback line* survived, because every flag set in the new invariant test hit
   a **named** branch — the fallback never fired. Fixed by pinning its actual reachable case: a
   `failed_strategies` list that is truthy but holds no mappings is degraded by the derivation
   while every named renderer branch is empty.
2. *Crop long names again* survived, because the `overflow="fold"` finding shipped with no test at
   all. Fixed with a 100-character name (the real `MAX_SESSION_NAME_LENGTH`) asserting the tail
   survives and no ellipsis appears.

After both, **all 12 mutations are killed.** Final suites: unit **2377** (+42), component 1290 / 16
skipped (unchanged), integration `--forked` 278 / 2 skipped (unchanged), DB tier 85, e2e 1, Epic 1
acceptance sweep **40/40**, `make format`/`lint`/`typecheck` clean. Zero regressions.

- [x] [Review][Decision→accepted, documented] **A database failure exits 4 — "broker unreachable" —
      from two commands that never touch the broker.**
      **Allay's call (2026-08-24): accept the code, correct the documentation.** 4 is inside AR28's
      table, so AC #10 is satisfied on its face, and every remapping option was worse — adding
      `SQLAlchemyError` is what AC #10 forbids, catching locally duplicates the table, and
      narrowing the `"TimeoutError"` key touches Epic 1's pinned surface for a hazard that is
      really about name-keyed matching in general. Both `--help` exit-code tables now document 4
      and say plainly that it is **not** a broker diagnosis from these commands.
      **Residual, flagged for the Epic 2 retro:** a script branching on 4 still cannot distinguish
      a dead Gateway from a dead database. That is the concrete case that forces the
      marker-protocol revisit `deferred-work.md` already records as overdue. `classify_failure` walks the MRO **by class name**, and
      `sqlalchemy.exc.TimeoutError` (SQLAlchemy pool exhaustion; `pool_timeout` is configured at
      `src/db/session_sync.py:57`) matches the `"TimeoutError"` entry intended for a socket that
      accepts but never answers → `BROKER_UNREACHABLE` → exit 4. Verified by execution. Both new
      commands document only `0/1/2` in their own `--help`, so the CLI contradicts itself, and a
      script branching on 4 goes and restarts a healthy IB Gateway. **Why this needs your call:**
      every obvious fix collides with a constraint — adding `SQLAlchemyError` to
      `_OUTCOME_BY_EXCEPTION_NAME` is exactly what AC #10 forbids ("**No new exception names**"),
      catching DB errors locally duplicates the table the Dev Notes say to reuse, and narrowing
      the `"TimeoutError"` key to the builtin touches Epic 1's pinned surface.
      [`src/core/live_check.py:142`, `src/cli/commands/live_status.py:107-110`]

- [x] [Review][Decision→recorded as Judgment call #9] **`--json` reports `"health": "degraded"` with
      nothing to act on.**
      **Allay's call (2026-08-24): the seven-key contract stands; record the conflict.** Now
      Judgment call #9 in Dev Notes, with the retro question it raises (whether AR29 should define
      an error/detail envelope for `--json` across all commands). Completion Notes corrected — the
      "no deviations" claim no longer swallows it. AC #7
      pins the payload to exactly seven keys and the code obeys it (pinned by
      `test_failed_strategies_never_leak_into_json`), but the Dev Notes' own instruction was
      "**render once in core, print twice, test both**" — the lesson from Story 2.7's review, where
      an operator-facing trailer was true on one path and false on the other. Both cannot hold: a
      `--json` consumer can see *that* a session is degraded but never *why*. The code took the
      right side of the conflict; what is missing is that Completion Notes claim "**No deviations
      from the story's own design**" and never record this as a judgment call for the retro.
      **Your call:** record it as a judgment call and leave the contract alone (recommended), or
      widen the payload. [`src/core/live_session_health.py:303-321`]

- [x] [Review][Decision→fixed, generic fallback] **A `connection_lost_at` degradation renders no
      explanation at all.**
      **Allay's call (2026-08-24): render a cause for every sense, with a catch-all.** New
      `_render_degradation()` un-nests the block and renders whichever sense fired — entries,
      `all_failed`, `connection_lost_at` — plus a closing fallback so that *no* degraded report can
      ever render without a cause, including a future sense this reader does not know about. The
      fallback's own reachable case is pinned by test (`failed_strategies` truthy but holding no
      mappings). Epic 4's writer now lights the connection-lost path up with zero changes here.
      This also absorbed the separate `all_failed`-nesting patch below.
      `_is_degraded` returns true on three independent conditions, but `render_status` gates the
      entire explanation block behind `if report.failed_strategies:` — so AR32's connection-lost
      sense produces a bare `health: degraded` with no line naming a cause, on both output paths.
      Confirmed by execution. **Why this needs your call:** the reader is deliberately dormant
      (AC #4 — Epic 4 owns the writer), so adding operator-facing text for a sense nothing writes
      yet is a scope judgment, not a mechanical fix. Options: render a cause line now so Epic 4's
      writer lights up complete, or leave it and let Epic 4 add the rendering with its writer.
      [`src/core/live_session_health.py:370`]

- [x] [Review][Patch→fixed] **CRITICAL — the threshold pin the story claims does not exist.** Dev Notes:
      "A unit test pins that the CLI wiring passes exactly that constant — no restated literal
      anywhere." No such test is in the tree: `DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS` appears in
      `tests/` only inside `test_session_service.py`, and the new health tests restate the literal
      `90.0` in eight places. Mutation-verified: replacing both call sites with `9000.0` leaves the
      entire unit tier green (2335 passed) plus 6/6 e2e and 40/40 health tests. The one property
      AC #2 singles out as "**never a new literal**" is the one property nothing guards.
      [`src/cli/commands/live_status.py:75,101`; `tests/unit/cli/commands/test_live_status_cli.py`]

- [x] [Review][Patch→fixed] **HIGH — swapping `closed_trade_count` and `open_positions` passes the entire
      suite.** Mutation-verified: inverting the tuple unpacking at both loaders leaves 67/67 tests
      green. The test that should pin it asserts single-character substrings (`"3" in output`,
      `"1" in output`) against output already dense with digits from a UUID and an ISO timestamp —
      so it is simultaneously non-discriminating and latently flaky — and its health assertion is a
      disjunction (`"trading" in output or "idle" in output`) that cannot fail. The e2e check
      asserts `0 == 0`, invisible to a swap. Today every count is 0 (Epic 3 owns live trade
      persistence), so an inversion would land silently and only surface once real trades exist —
      the difference between "flattened out" and "holding three live positions".
      [`src/cli/commands/live_status.py:61,98-99`; `tests/unit/cli/commands/test_live_status_cli.py:175`]

- [x] [Review][Patch→fixed] **HIGH — `stale` is never exercised through the command, and AC #2's
      derived-not-stored proof is at the wrong level.** The AC requires "a test proves **two
      `status` invocations** straddling the staleness threshold flip the rendered health **without
      any intervening write**"; the test calls the pure `derive_health()` twice with a stepped
      clock — no `CliRunner`, no repository mock, and no assertion that nothing was written. Both
      load-bearing halves are untested. Worse, `grep -c stale` returns **0** for both the CLI and
      e2e test files: the only rows with a null heartbeat are paired with `status=CREATED`, which
      short-circuits at precedence step 1. AC #3's whole SIGKILL story rests on P9, which is ⛔ not
      run. [`tests/unit/core/test_live_session_health.py:204-231`]

- [x] [Review][Patch→fixed] **`ntrader live list` reports its failures as "live status failed".** Found
      independently by all three layers. `_exit_on_failure` is shared but hardcodes the `status`
      prefix, and `list_sessions` calls it — so an operator troubleshooting a failed `list` sees an
      error attributed to a command they never ran. This is the exact defect the Dev Notes wrote a
      warning against ("write the two-line equivalent with the **right prefix**"), one command
      over. No failure-path test for `list` exists at all.
      [`src/cli/commands/live_status.py:109`, called from `:175`]

- [x] [Review][Patch→fixed] **The `all_failed` line renders only when `failed_strategies` is non-empty.**
      The derivation treats the two keys as independent (`_is_degraded` returns true on
      `all_failed` alone), but the renderer nests the "Every strategy … can no longer trade." line
      inside `if report.failed_strategies:`. A row with `{"all_failed": true,
      "failed_strategies": []}` reports `health: degraded` with no explanation on either path.
      Renderer and derivation disagree about what `all_failed` alone means; no test covers the
      combination. [`src/core/live_session_health.py:370-375`]

- [x] [Review][Patch→fixed] **Rendering runs outside the try/except, so a malformed `runtime_flags`
      document gives the operator a raw traceback instead of AR28's exit table.** Only
      `_load_report()` is guarded; `render_status()` and `json.dumps()` are not. `runtime_flags` is
      unconstrained JSONB with no server-side schema, and `_render_failed_strategies` calls
      `.get()` on every element. Confirmed by execution: `failed_strategies: ["sma_crossover"]` →
      `AttributeError: 'str' object has no attribute 'get'`; `failed_strategies: 3` → `TypeError`;
      `runtime_flags: ["boom"]` → `AttributeError`. The module docstring's tolerance claim ("an
      unknown key or an unexpected `v` never raises") covers key *names* only, never value *types*
      — and the tolerance test varies only keys. The one command whose job is to explain a session
      becomes the one command that cannot report on it.
      [`src/core/live_session_health.py:277-280,332-341`; `src/cli/commands/live_status.py:144-149`]

- [x] [Review][Patch→fixed] **The extended AR36 vocabulary scans ship without the non-vacuity probe the
      story's own Dev Notes require.** Both new scans do
      `text.replace("closed trade", "").replace("closed_trade_count", "")` before matching the five
      forbidden stems. The exemption is explicit (as required), but nothing proves the scrub is
      narrower than the scan — no planted forbidden stem, and no mutation among the ten broke a
      vocabulary test. This is precisely the vacuous-guard failure mode Dev Notes flag ("plant a
      forbidden-stem probe proving the extended scan fires").
      [`tests/unit/cli/commands/test_live_status_cli.py:400-413`;
      `tests/unit/core/test_live_session_health.py:441-452`]

- [x] [Review][Patch→fixed] **Procedure P9's `$!` captures `tee`'s PID, so the `kill -9` step kills the
      wrong process.** In a backgrounded pipeline `$!` is the PID of the **last** command, and the
      procedure pipes the runner into `tee` before echoing "runner pid". The operator kills `tee`;
      the runner survives, or dies nondeterministically on `SIGPIPE` at its next write — not the
      clean no-teardown kill the procedure specifies. Pass criterion 3 then fails after the 95s
      wait for a reason unrelated to the code, and the epic's gating procedure records a false
      negative. Needs `$(jobs -p)`/`pgrep -f`, or dropping the pipe for a direct redirect.
      [`docs/qa/phase3-live-verification.md:914-922`]

- [x] [Review][Patch→fixed] **`--json` writes human prose to stdout and emits no JSON on every failure
      path.** `_exit_on_failure` prints to the module `console` (whose file is stdout) and never
      consults `as_json`. Confirmed by execution: `live status missing --json` puts
      `live status failed: No trading session matches 'missing-session'` on stdout and exits 1, so
      `| jq` dies on a parse error. AC #7 pins `--json` as the machine-readable channel; it is
      polluted on exactly the paths a monitoring script most needs to handle. `CliRunner` merges
      the streams by default, so no existing test could tell them apart.
      [`src/cli/commands/live_status.py:109`]

- [x] [Review][Patch→fixed] **`test_module_has_no_module_scope_get_settings_call` does not test module
      scope, and is vacuous today.** It iterates `tree.body` then `ast.walk`s each node, which
      descends into top-level function and class bodies — so the check is "no `get_settings()` call
      anywhere in the file". Adding the *lazy, inside-the-command-body* call that the deferred-work
      blast-radius item actually recommends would fail this test with the message "called at module
      scope", which would be false. It passes today only because the module never calls
      `get_settings` at all. [`tests/unit/cli/commands/test_live_status_cli.py:156-169`]

- [x] [Review][Patch→fixed] **The named "typed-key mutation check" never calls the repository it claims to
      guard.** `test_the_join_is_on_the_internal_id_not_the_business_uuid` builds its own ad-hoc
      `select()` and asserts Postgres rejects it — it never invokes `trade_counts_by_session` and
      makes no assertion about any count, so it would pass unchanged if the repository joined on
      the UUID. Accuracy-only, not a coverage hole: mutating the real join was verified to fail 4
      sibling count tests with `operator does not exist: bigint = uuid`. The property is protected
      — by different tests than the one whose name and docstring claim it.
      [`tests/integration/db/test_trading_session_repository.py:263-293`]

- [x] [Review][Patch→fixed] **The ISO-8601-UTC tests are made true by their fixtures, not by the code.**
      `status_json_payload` calls `.isoformat()` and neither normalizes to UTC nor asserts
      awareness; both tests that check for a `+00:00` suffix feed in datetimes that are aware-UTC
      by construction. A value arriving aware-but-not-UTC would emit `…-04:00` and violate the
      docstring's promise with no test able to observe it. Low risk in practice (the columns are
      `TIMESTAMP(timezone=True)`), but no test in the diff ever feeds a real database timestamp
      through the arithmetic — the only real-DB row has every timestamp column null.
      [`src/core/live_session_health.py:316-318`; `tests/unit/core/test_live_session_health.py`]

- [x] [Review][Patch→fixed] **`test_json_mode_has_no_ansi_or_rich_pollution` cannot fail.** `CliRunner`
      captures a non-tty stream, so Rich disables colour unconditionally and the `"\x1b[" not in
      output` assertion holds no matter what the command prints — delete `markup=False,
      highlight=False` or print the table outright and it still passes. Needs `color=True` on the
      runner to have any force; the sibling `json.loads` assertion is what actually works.
      [`tests/unit/cli/commands/test_live_status_cli.py`]

- [x] [Review][Patch→fixed] **Unused module-level `structlog` logger, against Judgment call #7.** Bound at
      import, never referenced in the 205-line module. Ruff cannot flag it (assigned, so no
      F401/F841). Judgment call #7 says `status`/`list` emit zero events by design, and the
      `deferred-work.md` closeout repeats it — a dormant logger in a module whose `--json` contract
      depends on stdout carrying nothing but JSON is a trap the next editor will spring.
      [`src/cli/commands/live_status.py:43`]

- [x] [Review][Patch→fixed] **`status` renders `trader_id` but `list` silently does not.** `_load_report`
      passes `trader_id=derive_trader_id(...)`; `_load_reports` omits the argument, so every listed
      report carries `trader_id=None`. Not a crash (the renderer guards), but an operator cannot
      correlate an IB Gateway trader id from `live list`, and the reason is an omitted keyword
      argument in one of two near-identical loaders rather than a stated policy. No test pins the
      asymmetry either way. [`src/cli/commands/live_status.py:76` vs `:88-102`]

- [x] [Review][Patch→fixed] **`derive_health` compares the status by identity while `build_status_report`
      defensively coerces it 140 lines later.** `if status is not SessionStatus.RUNNING` versus
      `status=SessionStatus(status).value`. Both cannot be right. Not reachable from the two
      in-repo callers (the ORM returns real enum members), but a `str` would produce a report
      saying `state: running` and `health: stopped` simultaneously — the identical hazard
      `session_service._as_status` was written to close. `==` costs nothing and removes the
      inconsistency. [`src/core/live_session_health.py:142` vs `:284`]

- [x] [Review][Patch→fixed] **`live list` ellipsizes session names longer than ~77 characters, and
      creation accepts up to 100.** The seven-column table's fixed content (a 36-char `no_wrap`
      UUID, two count headers, a 26-char ISO timestamp) leaves `Name` roughly 77 characters inside
      `Console(width=200)`, and Rich crops rather than wraps. The displayed name cannot be pasted
      back into `ntrader live status <name>`. Mitigated by the intact `Session ID` column and the
      full name in `--json`. No test uses a name anywhere near the limit the CLI permits.
      [`src/cli/commands/live_status.py:187-197`]

- [x] [Review][Patch→fixed] **`_exit_on_failure` is annotated `-> None` though it always raises**, forcing
      two `return  # pragma: no cover` lines at the call sites. `typing.NoReturn` would let the
      checker prove those branches dead and would catch the day someone adds an early `return` that
      lets a failed `status` fall through into `render_status(report)` with `report` unbound.
      [`src/cli/commands/live_status.py:107`]

- [x] [Review][Patch→fixed] **Completion Notes miscount `list --json`'s array shape as an undisclosed
      deviation.** The story mandates it in two places (AC #7: "`list --json` emits a JSON array of
      objects, one per session"; Judgment call #6). Only two of the three listed deviations are
      genuine — both of those check out (`Console(width=200)` matches the `strategy.py` precedent;
      the phantom-row fix is real and correctly documented). Miscounting a mandated behaviour as a
      deviation devalues the list a reviewer is meant to trust.
      [story file, Completion Notes]

- [x] [Review][Defer] **Two new functions exceed CLAUDE.md's 50-line function cap** — `derive_health`
      (63 lines) and `build_status_report` (76), both docstring-dominated (≈12 and ≈20 executable
      statements). [`src/core/live_session_health.py:94,225`] — deferred, pre-existing: 195
      functions under `src/` already exceed 50 raw lines and `deferred-work.md` already records
      that the limit "is plainly not measured on raw lines today". The story's size disclosure is
      incomplete on its face, but no action is implied until the retro settles the measurement.

- [x] [Review][Defer] **`_age_seconds`'s clamp docstring gives a rationale the code does not need,
      and the real consequence is undocumented** — no comparison in the module would misread a
      negative age, so the clamp's stated purpose is wrong; what it actually does is mask clock
      skew between the runner host and the querying host for the duration of the skew.
      [`src/core/live_session_health.py:57-68`] — deferred, pre-existing: the function deliberately
      mirrors `session_service._heartbeat_age_seconds`, which carries the same clamp and the same
      unstated skew behaviour. Fixing one without the other would break the intentional symmetry.

**Dismissed (1):** `stale` conflating "never sent a heartbeat" with "heartbeat stopped" — the
window does not exist. `_TIMESTAMPS_BY_TARGET` stamps `last_heartbeat_at` in the same write that
sets `status = running` (`session_service.py:136`), so no row can be `running` with a null
heartbeat.

## Dev Notes

### Why this story exists, and what it consumes

Epic 2's goal sentence ends with this story: "sessions listable and inspectable from another
terminal, and a dead session distinguishable from a quiet one" (`epics.md:715-717`). The CLI runs in
a different process from the runner, so health cannot be answered from memory — that is
architecture G1 (`architecture.md:670-677`), which added the heartbeat columns and the five-value
health vocabulary (AR32, `epics.md:229`). The PRD forbids the alternatives: no daemon, no PID files
(`prd.md:550-556` — so no liveness probe; the heartbeat columns ARE the liveness signal), no web UI
("zero UI work is a success criterion", live monitoring is Growth, `prd.md:535-539, 903-906`).

Everything this story reads already exists — **no migration** (head stays `b7c419e2a3d8`, single):

| Field | Written by | Where |
|---|---|---|
| `status` | `SessionService.transition()` only (AR37) | `session_service.py` |
| `last_heartbeat_at` | steady-state tick ~30s + startup heartbeat | Story 2.5 |
| `last_bar_at` | `note_bar` per bar, persisted on the tick | Story 2.5 |
| `last_started_at`/`last_stopped_at`/`sealed_at` | `_TIMESTAMPS_BY_TARGET` stamping | Story 2.3 |
| `runtime_flags` (JSONB, nullable) | `_record_strategy_failure` only; cleared on `-> running` | Story 2.7 |
| `trades.session_id` (BigInteger FK) | nobody yet — Epic 3 | Story 2.2 migration |

### Judgment calls taken while drafting (flag all for the Epic 2 retro)

1. **`degraded` covers both AR32 senses through one derivation** — the only remaining open half of
   deferred-work's fifth-status item. Sense (a), contained strategies, has a live writer today
   (`failed_strategies`/`all_failed`); sense (b), connection lost, gets a **dormant reader** on the
   pre-planned `connection_lost_at` key (`"v"` exists so this key can be added without a bump —
   `session_service.py:407-410`) and **no writer**: writing it requires consulting
   `ConnectionMonitor`, which `deferred-work.md` reserves for Epic 4 ("calling
   `confirm_state_reestablished` would satisfy the type signature while defeating the design"),
   and the current connection reader is known to lie under IB error 1101. Wiring the reader now
   means Epic 4's writer lights up `degraded` with zero changes to this story's code.
2. **Bar-recency threshold = 300s, pinned equal to `DEFAULT_NO_BARS_AFTER_SECONDS`.** No number
   exists anywhere for the `trading`/`idle` split. Reusing the no-bars watchdog's 300s beats
   inventing a second constant — both answer "how long without a bar is worth remarking on" — and
   deferred-work already records that 300 itself is invented and awaiting P6/P8 evidence; the drift
   pin means one future correction fixes both. With 1-min bars in RTH, `trading` holds; outside
   RTH a session honestly reads `idle`.
3. **Fencing/epoch column: deferred again, re-argued on its merits** (the old "single migration is
   spent" argument is dead and is not used). Deferred-work names 2.8 "the natural place" because
   it makes a stale-but-fresh-looking row operator-visible — but its own caveat cuts the other
   way: the column "changes the meaning of every write on `SessionRecordPort`, so it belongs to a
   story that owns that port, not to a bystander." This story owns **no** port writes; it is the
   phase's one pure reader. Disposition: `status` renders `last_started_at` alongside heartbeat
   age (the two facts an operator needs to notice a suspicious row), and the fencing decision
   moves to the first Epic 3/4 story that touches `SessionRecordPort` write semantics, where
   NFR6's two-processes hazard becomes order-adjacent. **Ratified by Allay, 2026-08-24** — the
   dev agent may cite this when updating the deferred-work items in Task 11.
4. **`SessionService` is untouched** — resolving the class-size action routed here: the health
   derivation never enters the class (it lives in `src/core/live_session_health.py`), so neither a
   split nor the module-level-body workaround is needed; the class stays at 98/100. The CLI reuses
   `resolve()` as-is.
5. **`last_activity_at` = greatest non-NULL of the five activity timestamps** (heartbeat, bar,
   started, stopped, sealed). The AC names "last activity" without defining it; this definition is
   answerable from the single row and degrades to NULL for never-started sessions.
6. **`list --json` is a JSON array** of per-session objects. AR29 pins only the `status --json`
   keys; the array is the obvious machine-readable shape for a list and stays inside AR29's
   formatting rules.
7. **No structlog events from `status`/`list`.** They are read-only; their console output *is* the
   product. AR41's carve-out makes command-scoped events a separate unenumerated vocabulary, and
   adding none avoids growing the AR41 backlog item.
8. **Health precedence puts `stale` above `degraded`.** A stale heartbeat means the process is
   likely dead — reporting `degraded` (which implies "running but impaired") on the strength of
   flags from a dead run would be the more misleading answer. The flags still render in the body.
9. **`--json` carries no degradation cause, and AC #7 wins over the "print twice" Dev Note.**
   *(Added at code review, 2026-08-24 — ratified by Allay.)* Two binding clauses conflict: AC #7
   pins `status --json` to **exactly** seven keys by set equality, while "Previous-story
   intelligence" says "render once in core, print twice, **test both**" — 2.7's lesson about a
   trailer that was true on one path and false on the other. Both cannot hold: a `--json` consumer
   can see *that* a session is degraded but never *why*. **Resolution: the seven-key contract
   stands.** AC #7's set equality is explicit and specific; the Dev Note is general guidance, and
   AR29 named the key set at the architecture level. The human path carries the full explanation,
   and `list --json`/`status --json` remain a stable machine contract rather than one that grows a
   key per failure mode. The original implementation already chose this — what was missing is that
   Completion Notes claimed "no deviations" and never recorded the conflict, so a later reader
   would have read the omission as an oversight rather than a decision. **Flag at the Epic 2
   retro:** whether AR29 should define an *error/detail* envelope for `--json` across all commands,
   which is the shape that would satisfy both clauses without per-story contract drift.

### The health derivation — placement and the import-direction problem

`derive_health` is a pure function over primitives:
`(status: SessionStatus, last_heartbeat_at, last_bar_at, runtime_flags: dict | None, now) -> SessionHealth`.
It lives in **`src/core/live_session_health.py`** (the `live_*` family, auto-swept by the glob
guards). One subtlety: the 90s threshold constant lives in `src/services/session_service.py`, and
core live modules must not import `src.services` (`TestImportPurity` forbids it). Resolution: the
function takes `heartbeat_stale_after_seconds` as a **required-by-caller parameter**; the CLI passes
`DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS` imported from `session_service` (the CLI already imports
services legally). A unit test pins that the CLI wiring passes exactly that constant — no restated
literal anywhere. (`DEFAULT_HEARTBEAT_INTERVAL_SECONDS` itself lives in `src/models/session.py:78`,
which core may import — models are framework-free by design — but the *derived* 90s constant is the
one Story 2.3 fixed, so import the real thing at the CLI layer rather than re-deriving 3× in core.)

`runtime_flags` document shape, verbatim from the only writer (`session_service.py:498-519`):

```python
trading_session.runtime_flags = {
    **existing,                      # unknown keys survive — connection_lost_at is pre-planned
    "v": RUNTIME_FLAGS_VERSION,      # 1; additions are NOT bumps
    "all_failed": bool(existing.get("all_failed", False)) or all_failed,
    "failed_strategies": entries,    # list of 6-key dicts:
    # {strategy_id, spec_strategy_id, error_type, handler, at (ISO-8601 UTC), detail}
}
```

`NULL` means *nothing to report*. `handler` ∈ `{"handle_bar", "handle_event", "start"}`. `detail`
is one already-redacted line. Do not route the document through any pydantic model with
`extra="forbid"` (Story 2.7's trap #3). Do not mutate it — this story never writes it at all.

### The false-green trap (why AC #4's last clause exists)

Story 2.7 measured dispatch order `['observer', 'note_bar', 's1']` — the runner's own `note_bar`
subscribes at the `subscribe` phase, before any strategy. So bars keep advancing `last_bar_at` and
the heartbeat keeps beating **even when every strategy is dead**. Without the `runtime_flags` branch,
health would read `trading` for a session that cannot place an order — the exact regression the 2.7
column-now decision was taken to prevent (`deferred-work.md:864-873`). The pin test for this is the
single most load-bearing test in the story.

### CLI placement, size budget, and conventions

- `src/cli/commands/live.py` is **533/500** — over cap, disclosed. It may gain only the
  `live.add_command(...)` registration lines. Command bodies go in **new
  `src/cli/commands/live_status.py`** (the `live_start.py` split precedent; its docstring records
  the same motive).
- Follow `check`'s output model: pure renderer in core returns a string; CLI prints it once. Rich
  markup only for first-party text; anything that could embed third-party text prints
  `markup=False, highlight=False`.
- Exit path: reuse `classify_failure`/`failure_message`/`EXIT_CODES` from `live_check` — 
  `RecordNotFoundError` is already mapped (exit 1) and already safe-messaged ("the identifier that
  matched nothing"). Do not copy `exit_with` verbatim (its prefix says `live start failed`); write
  the two-line equivalent with the right prefix in `live_status.py`.
- DB access: one short-lived `get_sync_session()` block per command
  (`claim_session` shape). Sync repository only (CLI side of the dual-repo rule).
- The commands read rows and render; **no transition, no reclaim, no port write**. `live status`
  on a `stale` session must NOT trigger the reclaim — that is `start`'s job (AR33).
- AR36 hazard, spelled out: the vocabulary scan stem-matches `pause|halt|kill|close|finalize` in
  operator-facing strings. `closed_trade_count` and "closed trades" contain the `close` stem — but
  the ban is scoped to *lifecycle synonyms* (the Story 1.2 precedent: AR36 bans words "as synonyms
  for" the concept, not globally), and "closed trade" is the trading-domain term AR29 itself
  mandates. Make the exemption explicit in the test rather than silently weakening the scan.
- Timestamps render ISO-8601 UTC from tz-aware datetimes; the DB columns are
  `TIMESTAMP(timezone=True)`. Never route through `src/utils/error_messages.py`'s straggling
  `utcnow()` (known deferred item).
- Heartbeat display: render the *age* (e.g. `heartbeat: 12s ago`) not just the raw timestamp —
  the operator question is "how stale", and the age is what the derivation actually used. Note
  heartbeat writes are batched by up to one 30s interval (Story 2.5), so ages up to ~60s are
  normal for a healthy session; 90s = three missed beats is the generous-by-design threshold
  (rationale at `session_service.py:45-48`).

### Previous-story intelligence (what will bite you)

- **MagicMock truthiness**: every field a renderer branches on must be set explicitly on row
  stand-ins, or tests silently take the wrong branch (`test_live_cli.py:893-903`).
- **`capture_logs()` is banned** for anything touching `session_id` context (strips
  `merge_contextvars`; `cache_logger_on_first_use=True` makes it order-dependent). Status emits no
  events, so this mostly means: don't add events just to test them.
- **Assert ordered lists, never set membership** — the list-ordering AC is exactly the kind of
  test that goes vacuous as a set.
- **Mutation-prove every guard**: "Assume yours is one of the tests that cannot fail until you
  have broken the code and watched it go red" (2.7 Debug Log). Ten named mutations in Task 10.
- **AST/vocabulary scans need non-vacuity probes** — when extending the AR36 test, plant a
  forbidden-stem probe proving the extended scan fires.
- **The dependency guard will fire** on new stdlib imports in `live_*` modules (it caught `re` and
  `traceback` in 2.7); add each name by hand with the reason inline.
- **Registration is process-global**: if any test touches `StrategyRegistry`, force `discover()`
  before snapshotting (2.7's inverse-leak bug). Status tests shouldn't need the registry at all.
- **No `freezegun`/`time-machine`** — inject `now`/time_source, the `_utc_now` pattern.
- **Repository tests and CI**: `tests/integration/db/` is `--ignore`d in CI (evidence, not a
  gate) — the standing hole for `TradingSessionRepository`. The count queries cannot run on the
  in-memory SQLite component tier (JSONB + PG enum on the model), so they land beside their
  siblings; the CLI unit tests carry the gated behavioral coverage via mocks. Append this to the
  existing CI-gating deferred item — the fifth entry, not a new item.
- **Settings blast radius**: `get_settings()` at module scope kills `--help` on a bad env var.
  Import it lazily inside command bodies (and note `get_sync_session` reaches settings — that's
  inside the body already).
- **Both `--json` and human paths must be exercised for the degraded rendering** — 2.7's review
  caught an operator-facing trailer that was true on one path and false on the other
  (`all_strategies_failed` branch); render once in core, print twice, test both.

### What this story must NOT do

- **No fencing/epoch column, no migration** (Judgment call #3 — flagged for Allay).
- **No writer for `connection_lost_at`**; no reading `ConnectionMonitor` state (Epic 4).
- **No fifth `SessionStatus` value** — settled; the PG enum has exactly four labels.
- **No `resume()` CLI** (2.7 Judgment call #8 — a retro question, not this story).
- **No touch** of `live_session_runner.py`, `live_session_steady_state.py`,
  `live_session_record.py`, `session_record.py`, the port, or anything under
  `src/core/strategies/` (AR40: `grep -rn "is_live" src/core/strategies/` stays zero).
- **No new status assignment, no transition calls** from the new commands.
- **AR44 untouched surfaces**: `src/api/**`, `templates/**`, `backtest_query.py`, comparison
  views, `BacktestOrchestrator`, import/catalog commands — zero diffs.
- **No `--json` on any other command** (`test_start_has_no_json_option` pins D8's scoping).
- **No new exception names** in the outcome map or safe-message set; no invented exit codes.
- **No perf work**: NFR grants listing no target; the grouped-count query is a courtesy, not a
  requirement.

### Testing requirements summary

Tiers: health truth table + renderers + CLI = **unit** (no Nautilus, no DB); repository counts +
end-to-end `create → status → list` = **DB tier** (`tests/integration/db/`, real Postgres, local
evidence) unless an e2e Postgres harness already exists; nothing here needs `--forked` (no
`LiveDataEngine` is ever constructed). Markers on every test. TDD strictly: every task starts with
its failing tests. Full regression + Epic 1 sweep (40/40) before review. Coverage floor: CI runs
`--cov=src --cov-fail-under=64` without `--forked` — pure-function health code is cheap coverage;
don't let the CLI wrapper be the only thing testing derivation logic.

### Project Structure Notes

- New: `src/core/live_session_health.py` (pure domain — StrEnum, thresholds, derivation, report
  builder, renderers; pre-agreed split target `src/core/live_status_render.py` if the cap nears),
  `src/cli/commands/live_status.py` (Click commands + registration into the existing `live`
  group), `tests/unit/core/test_live_session_health.py`,
  `tests/unit/cli/commands/test_live_status_cli.py`.
- Modified: `src/cli/commands/live.py` (registration lines only), both trading-session
  repositories + shape-test allowlist, `tests/component/core/test_live_dependency_invariance.py`
  (glob widened), `tests/unit/core/test_live_node_never_exits.py` (exemption),
  `tests/component/core/test_session_runner_phases.py` (`TestImportPurity.MODULES`),
  `tests/integration/db/test_trading_session_repository.py`,
  `docs/qa/phase3-live-verification.md` (P9), `deferred-work.md` (closeouts per Task 11).
- Naming: `live_` prefix mirrors the CLI group (architecture Naming Patterns); `SessionHealth`
  StrEnum follows `SessionStatus`'s bare-value rendering rationale.
- Import law (architecture Boundaries): `cli/commands/live_status.py → services (SQL yes,
  Nautilus no)` and `→ core/live_session_health (pure)`; core health module imports models at
  most; **nothing imports the runner**.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md:996-1036`] — Story 2.8 verbatim (six ACs)
- [Source: `epics.md:229` (AR32), `:230` (AR33), `:218-220` (AR27/28/29), `:236-241`
  (AR36-AR41), `:244` (AR44), `:184` (AR5), `:187` (AR8), `:188` (AR9)]
- [Source: `epics.md:144-145`] — NFR23/NFR24 verbatim; `:126` NFR11
- [Source: `_bmad-output/planning-artifacts/architecture.md:670-677`] — G1, origin of the health
  model; `:275-284` D8; `:420-422` `--json` format; `:352-354` D1→D8 read-path dependency;
  `:384-392` naming; `:569-591` boundaries; `:541-554` test placement
- [Source: `_bmad-output/planning-artifacts/prd.md:550-556`] — no daemon/PID; `:535-539, 903-906`
  — no UI/monitoring; `:886-891` FR48-FR51; `:830-832` FR21/FR22
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md:851-874`] — degraded-both-senses
  (the open half this story closes); `:1226-1238` SessionService size action; `:1518-1527` +
  `:1633-1642` fencing token; `:864-873` false-green rationale; §story-2.5 blast radius, AR41
  running item, no-bars 300s provenance
- [Source: `_bmad-output/implementation-artifacts/2-7-keep-one-failing-strategy-from-taking-down-the-session.md:669-673`]
  — scope handoff; `:94-112` column-now decision; `:1149-1181` document shape + traps;
  `:1562-1566` guard lists; `:1341-1373` tier/test doctrine
- [Source: `src/services/session_service.py:45-55, 142-171, 324-332, 407-410, 498-519, 549-575`]
- [Source: `src/db/models/trading_session.py:61-117`; `src/db/models/trade.py:86-131, 150-154`]
- [Source: `src/db/repositories/trading_session_repository_sync.py:125-142`] — `find_all` ordering
- [Source: `src/core/live_check.py:41-45, 86-160, 441-478`] — exit codes, outcome map, renderer
  precedent
- [Source: `src/cli/commands/live.py:78-80, 424-533`; `src/cli/commands/live_start.py:31-74`]
- [Source: `tests/unit/cli/commands/test_live_cli.py:35-45, 846-921, 893-903, 950-955`]
- [Source: `tests/unit/db/test_trading_session_repository_shape.py:27`;
  `tests/unit/services/test_session_service.py:524-557, 1184-1192`]
- [Source: `tests/component/core/test_live_dependency_invariance.py:36`;
  `tests/unit/core/test_live_node_never_exits.py:42-78`;
  `tests/unit/core/test_live_stop_path_is_inert.py:36-44`;
  `tests/integration/core/test_epic1_ac_node.py:152-164`]

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 — `claude-sonnet-5`.

### Debug Log References

**Baselines re-confirmed pre-edit, all matched the story's cited numbers exactly:** unit 2266
passed; component 1289 passed / 16 skipped; integration (whole `tests/integration` dir,
`--forked`) 265 passed / 2 skipped; e2e 1 passed; Epic 1 sweep 40/40.
`session_service.py`'s `runtime_flags` write shape, the `-> running` clear, `trades.session_id`
typing, `find_all()` ordering, `RecordNotFoundError`'s existing exit-1 mapping, `SessionService`
at 98/100 by line count, and `live.py` at 533/534 lines all matched the story's citations. Neither
`test_live_session_health.py` nor `test_live_status_cli.py` existed anywhere in the tree.

**Ten named mutations (Task 10), each broken, observed red, and reverted:**

1. Staleness `>` → `>=` in `derive_health` — 1 test failed (`test_age_exactly_at_the_threshold_reads_fresh_not_stale`).
2. Precedence swap, `degraded` checked before `stale` — 1 test failed
   (`test_a_stale_heartbeat_outranks_a_degraded_flag`).
3. `trading`/`idle` branches swapped — 8 tests failed across three test classes.
4. `_is_degraded`'s `all_failed`/`failed_strategies` branch dropped — 5 tests failed, including
   the false-green pin (`test_the_false_green_is_closed`).
5. `trade_counts_by_session`'s `closed`/`open_` filters both set to `exit_timestamp IS NULL` — 3
   DB-tier tests failed (`assert (1, 1) == (2, 1)`, etc.).
6. The repository join changed from `Trade.session_id == TradingSession.id` to
   `TradingSession.session_id` (the UUID column) — 4 tests failed with a real Postgres
   `ProgrammingError: operator does not exist: bigint = uuid`, exactly the type-mismatch the AC
   predicted, not a silent wrong answer.
7. `_load_reports()` re-sorted rows alphabetically by name instead of passing `find_all()`'s order
   through — the **first** attempt at this mutation passed vacuously, because the fixture's two
   session names ("newer-session"/"older-session") happened to alphabetize the same as their
   intended `find_all()` order. Caught before it could ship: the fixture was renamed to
   "zzz-newer-session"/"aaa-older-session" (find_all order and alphabetical order now disagree),
   re-run, and the mutation then failed as expected (`assert 747 < 598`).
8. `status_json_payload`'s `last_activity_at` key renamed to `last_activity` — 7 tests failed
   across both the core and CLI test files (set-equality pins plus direct `KeyError`s).
9. The dormant `connection_lost_at` branch in `_is_degraded` deleted — 1 test failed
   (`test_a_dormant_connection_lost_at_key_reads_degraded`), confirming the reader really is wired
   even though nothing writes the key yet.
10. `DEFAULT_BAR_FRESH_AFTER_SECONDS` hardcoded to `60.0` — the drift pin caught it
    (`assert 60.0 == 300.0`), exactly the mutation it exists for.

Tree re-scanned after all ten reverts: `grep -rn "MUTATION" src/ tests/` → zero hits.

**One real (non-mutation) bug found and fixed by the TDD cycle itself**, not listed among the ten
because it was never intended: the first `trade_counts_by_session` implementation used
`func.count(case((Trade.exit_timestamp.is_(None), 1)))` for the open-position count. A `LEFT
OUTER JOIN` from a session with **zero** trades still produces one phantom row with every `Trade`
column `NULL`, and `exit_timestamp IS NULL` is trivially true for that phantom row too — so a
trade-less session reported `(0, 1)` instead of `(0, 0)`, one phantom open position that does not
exist. Caught by `test_a_session_with_no_trades_reports_zero_and_zero` on the very first real-DB
run. Fixed by switching both counts to `func.count(Trade.id).filter(...)`: the `FILTER` clause
still admits the phantom row, but `COUNT(Trade.id)` ignores it because `Trade.id` itself is `NULL`
on that row — standard "count a non-null column" semantics doing the work `case()` could not.

**One vacuous-guard finding**, folded into mutation #7 above rather than reported twice: the
first version of `test_renders_every_session_in_find_all_order` picked fixture names whose
alphabetical order coincided with `find_all()`'s intended order, so a mutation that silently
re-sorted the rows passed by accident. Corrected before Task 10 closed.

`test_epic1_ac_node.py::test_no_new_dependency_was_added_for_the_live_path` fired on this story's
own `json` import in `live_status.py` — the Epic 1 dependency guard doing exactly its job, the
same `socket`/`signal`/`re`/`traceback` precedent every prior live story hit once. Added `json` to
`_STDLIB_AND_FIRST_PARTY` by hand with the reason inline; not via `sys.stdlib_module_names`.

**Final validation, all green:** `make format` (484 files unchanged), `make lint` (all checks
passed), `mypy src/core src/services` (103 files, no issues — pre-existing, unrelated
`live_start.py:54` return-type note confirmed present before this story's changes via `git
stash`). unit 2335 passed (+69); component 1290 passed / 16 skipped (+1, the widened
`TestImportPurity.MODULES` parametrize); integration (whole dir, `--forked`) 278 passed / 2
skipped (+13); e2e 1 passed (unchanged); Epic 1 acceptance sweep 40/40 (re-confirmed after the
`json` guard fix). Zero regressions anywhere.

### Completion Notes List

Delivers `ntrader live status <session>` and `ntrader live list`, both read-only and both
answering from `trading_sessions` alone — no `TradingNode`, no import of `live_session_runner`,
no transition, no port write. All 11 tasks and every subtask complete; all 10 ACs satisfied.

**`src/core/live_session_health.py` (new, 376 lines)** owns the five-value `SessionHealth`
vocabulary, `derive_health()`'s fixed precedence (`stopped` → `stale` → `degraded` → `trading` →
`idle`), the `StatusReport` frozen dataclass, and the pure `build_status_report`/`render_status`/
`status_json_payload` builders — the `render_report` split `live_check.py` already models,
renderer in core, printing in the CLI. Framework-free per AC #9: no SQLAlchemy, no Nautilus, no
`src.services` import, verified both by a module-level test and by inclusion in
`TestImportPurity.MODULES`. `DEFAULT_BAR_FRESH_AFTER_SECONDS = 300.0` is a literal, pinned equal
to `live_session_steady_state.DEFAULT_NO_BARS_AFTER_SECONDS` by a same-value test rather than an
import — importing that module would drag the CLI's read path toward the node-facing stack this
story is deliberately clear of (it transitively imports `nautilus_trader` via
`live_connection_probe`).

**`degraded` covers both AR32 senses from one derivation (AC #4, Judgment call #1), closing the
one open half `deferred-work.md` left for this story.** Sense (a) — contained strategies — reads
Story 2.7's live writer (`all_failed` or non-empty `failed_strategies`). Sense (b) — connection
lost — is a **dormant** reader on the pre-planned `connection_lost_at` key; no writer was added,
because writing it needs `ConnectionMonitor`, which stays Epic 4's. The dormant branch is pinned
by a unit test whose docstring says Epic 4 supplies the writer. The false-green trap Story 2.7
documented — every strategy dead but the heartbeat and `last_bar_at` still advancing, because
`note_bar` subscribes before any strategy — is closed by putting `degraded` ahead of `trading` in
the precedence, and is the single most load-bearing test in the story
(`test_the_false_green_is_closed`).

**`src/db/repositories/trading_session_repository_sync.py` and the async twin** each gain
`trade_counts_by_session(session_ids=None)`, a single grouped `LEFT OUTER JOIN` query on the typed
key (`trades.session_id`, a `BigInteger` FK to `trading_sessions.id` — never the UUID business
key), returning `{id: (closed_count, open_count)}` for every requested session, or every session
when no filter is given. `EXPECTED_CAPABILITIES` in the shape test widened to include it on both
twins (AR9).

**`src/cli/commands/live_status.py` (new, 205 lines)** holds both command bodies, kept out of
`live.py` (already 533/500, over cap, disclosed — gained only the two `add_command` lines, now
538). `status` resolves via `SessionService.resolve()` inside one short-lived `get_sync_session()`
block (the `claim_session` shape minus the transition), reads counts, derives the report, and
renders the session's `trader_id` (via the framework-free `derive_trader_id`) in human output
only — never in `--json`, whose seven keys are pinned by a set-equality test at both the core and
CLI layers. `list` renders a Rich `Table` in `find_all()`'s pinned order; an empty result prints a
first-party "No sessions found." line, exit 0 either way. Both failure paths reuse
`classify_failure`/`failure_message`/`EXIT_CODES` from `live_check.py` with a `live status
failed: …` prefix — no new exception names, no invented exit codes (AC #10). `Console(width=200)`
(the `strategy.py` precedent) — `list`'s seven-column table, including a 36-character UUID column,
ellipsized into unreadable noise at Rich's default 80-column non-tty fallback width; CliRunner
tests caught this immediately.

**Degraded-strategy visibility (AC #5, #7)** renders in the core renderer, not the CLI: each
`failed_strategies` entry's `spec_strategy_id`, `strategy_id` (when present), `error_type`,
`handler`, `at`, and `detail` (rendered verbatim — already redacted at the catch site per NFR26;
re-masking with `mask_account` would destroy the payload). A distinct line appears when
`all_failed` is true. Because `runtime_flags` is cleared only on `-> running`, a stopped session's
failures still render — pinned by test. AR36 vocabulary (*contained*, *failed*, *degraded*; never
*pause*, *halt*, *kill*, *close*, *finalize*) is asserted with the `closed trade`/
`closed_trade_count` exemption made explicit in the test itself, per the Story 1.2 precedent.

**Guard-list integration (AC #9):** `LIVE_MODULE_GLOBS` widened from the single `live.py` name to
`src/cli/commands/live*.py`, closing the disclosed gap that already left `live_start.py` unswept
since Story 2.6's split, and covering `live_status.py` from day one. `live_status.py` added to
`EXEMPT_MODULES` in the node-never-exits scan (it legitimately raises `SystemExit`).
`live_session_health.py` added to `TestImportPurity.MODULES`. Neither new module was added to
`STOP_PATH_MODULES` — a status renderer legitimately names `sealed_at`/`sealed`, which
`FORBIDDEN_SEAL_NAMES` would trip, and status sits nowhere on the stop path.
`test_epic1_ac_node.py`'s separate (older) dependency guard — not named in the story's Dev
Notes, found by running the full integration sweep — needed `json` added to its
`_STDLIB_AND_FIRST_PARTY` allowlist by hand, the same discipline every prior stdlib addition in
that file already follows.

**Resolve-precedence pin (AC #1, Task 9):** one test in `test_session_service.py::TestResolve`
proves a row *named* another row's `session_id` string resolves to the UUID match — `resolve()`
never even calls `find_by_name` in that case. Closes the "silently shadow" half of the
deferred-work UUID-shadowing item; rejecting UUID-shaped `--name` values at creation stays open
for the Epic 2 retro, as the item's own action text already scoped it.

**End-to-end proof lands in `tests/integration/db/test_live_status_e2e.py`, not
`tests/e2e/`, disclosed per the story's own instruction.** The architecture-named basename
(`tests/e2e/test_live_cli.py`) collides with the existing `tests/unit/cli/commands/test_live_cli.py`
under this repo's basename-uniqueness rule, and the e2e tier has no Postgres fixture at all (only
`tests/e2e/test_simple_backtest.py`, which needs none) — so it lands beside its sibling
`test_cli_live_create.py`, mirroring that file's `get_sync_session` patch-the-real-session idiom
at **both** modules that import it (`live.py` for `create`, `live_status.py` for `status`/`list`).
Six tests: create → status (human + `--json`) → resolve-by-id → list (human + `--json`) → unknown
session exits 1.

**Procedure P9 appended to `docs/qa/phase3-live-verification.md`**, modeled on P8's structure:
`live status` from a second terminal against a genuinely running session (`trading`/`idle`
observed), then `kill -9` on the runner and `stale` observed at the next query past the 90s
threshold, plus a no-write check (`last_started_at` unchanged across every query). Logged
`⛔ not run` — no automated test can fabricate a real heartbeat advancing or a real process going
silent under `kill -9`; both are recorded as this procedure's specific, non-substitutable job.
**Explicitly notes Procedure P8 remains not run and is still the gate before Epic 2 closes — P9
does not replace it or narrow its scope.**

**`deferred-work.md` closeouts (Task 11):** the "how `degraded` covers both senses" half — the one
question left open for this story — is now struck, citing the derivation and the dormant-branch
test. The fencing/epoch column is deferred again on its merits at **both** places that raised it
(the story-2.6 review finding and story-2.7's re-opening), ratified by Allay: this story owns no
`SessionRecordPort` writes, so adding the column here would be exactly the "bystander" move
`deferred-work.md`'s own caveat warns against; `status` renders `last_started_at` alongside the
heartbeat's age instead, and the decision moves to the first Epic 3/Epic 4 story that owns a port
write. The `SessionService` class-size action is struck: the health derivation needed no new
method on the class at all, so the class stays at 98/100 untouched. The repository CI-gating item
gained a fifth entry (this story's new DB-tier tests are `--ignore`d for the same structural
reason as every sibling in that thread, though the CLI unit tests carry gated mock coverage of the
wiring). The AR41 running item gained a note that this story emits zero new structlog events by
design (Judgment call #7) — recorded so the list is not mistaken for silently incomplete.

**No deviations from the story's own design.** The `Console(width=200)` choice and the phantom-row
`count(case(...))` bug are the only two things not spelled out verbatim in the story text, and both
are documented above and in the code itself. (`list --json`'s array shape was listed here as a
third; **corrected at code review** — the story mandates it in two places, AC #7 "emits a JSON array
of objects, one per session" and Judgment call #6, so it is not a deviation at all. Miscounting a
mandated behaviour devalues the list a reviewer is meant to trust.)

**Added at code review, 2026-08-24:** one further conflict was resolved rather than deviated from,
and is now recorded as Judgment call #9 — AC #7's exact seven-key `--json` contract versus the
"print twice, test both" Dev Note. The contract stands; the reasoning is in Dev Notes.

### File List

**New:**
- `src/core/live_session_health.py`
- `src/cli/commands/live_status.py`
- `tests/unit/core/test_live_session_health.py`
- `tests/unit/cli/commands/test_live_status_cli.py`
- `tests/integration/db/test_live_status_e2e.py`

**Modified:**
- `src/cli/commands/live.py` (registration lines only: import + two `live.add_command(...)` calls)
- `src/db/repositories/trading_session_repository_sync.py` (`trade_counts_by_session`)
- `src/db/repositories/trading_session_repository.py` (`trade_counts_by_session`, async twin)
- `tests/unit/db/test_trading_session_repository_shape.py` (`EXPECTED_CAPABILITIES` widened)
- `tests/integration/db/test_trading_session_repository.py` (trade-counts test classes, both twins)
- `tests/unit/services/test_session_service.py` (resolve-precedence pin test)
- `tests/component/core/test_live_dependency_invariance.py` (`LIVE_MODULE_GLOBS` widened)
- `tests/unit/core/test_live_node_never_exits.py` (`EXEMPT_MODULES` widened)
- `tests/component/core/test_session_runner_phases.py` (`TestImportPurity.MODULES` widened)
- `tests/integration/core/test_epic1_ac_node.py` (`_STDLIB_AND_FIRST_PARTY` widened with `json`)
- `docs/qa/phase3-live-verification.md` (Procedure P9 appended)
- `_bmad-output/implementation-artifacts/deferred-work.md` (five closeouts per Task 11)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (status transitions)

## Change Log

- 2026-08-24: Story created (ready-for-dev). Ultimate context engine analysis completed —
  comprehensive developer guide created from epics/PRD/architecture extraction, Story 2.1–2.7
  intelligence, deferred-work triage (four items routed here), and direct code verification
  (`trades.session_id` typing, `resolve()` precedence, exit-code map, guard-list inventory).
  Eight judgment calls recorded in Dev Notes; #3 (fencing column deferred) flagged for Allay.
- 2026-08-24: Story implemented -> review. ready-for-dev -> in-progress -> review, same session as
  creation. `ntrader live status`/`live list` delivered: the five-value health derivation with a
  pinned precedence (`src/core/live_session_health.py`, new), `trade_counts_by_session` on both
  repository twins (typed-key join), both CLI commands (`src/cli/commands/live_status.py`, new),
  degraded-strategy visibility, `--json` on both commands with AR29's exact seven-key contract,
  the resolve-precedence pin, all guard-list integration, ten named mutations broken/observed
  red/reverted, and Procedure P9. One real bug (not a planned mutation) found and fixed by the
  TDD cycle itself: the phantom-row `count(case(...))` defect in the first
  `trade_counts_by_session` draft, caught by the very first real-DB test run against a
  trade-less session. `deferred-work.md`'s "how `degraded` covers both senses" item closed; the
  fencing-column question deferred again on its merits (ratified by Allay) rather than added here;
  the `SessionService` class-size action closed by not needing to happen. Final: unit 2335
  (+69), component 1290 / 16 skipped (+1), integration (whole dir, `--forked`) 278 / 2 skipped
  (+13), e2e 1 (unchanged), Epic 1 acceptance sweep 40/40, format/lint/typecheck clean, zero
  regressions.
- 2026-08-24: Story code-reviewed -> done, closing Epic 2's last story. Three adversarial layers
  (Blind Hunter with the diff only / Edge Case Hunter with project read access / Acceptance Auditor
  with the story) produced 33 raw findings, 25 after dedup, 1 dismissed. **17 of the 24 surviving
  findings were tests and docs, not behaviour** — the production code was very nearly right. The
  dominant theme was guards that cannot fail: three properties the ACs single out were provably
  unguarded, each confirmed by mutation rather than asserted — the `DEFAULT_HEARTBEAT_STALE_AFTER_
  SECONDS` pin the story claimed to have written but did not (setting it to `9000.0` left all 2335
  unit tests green), a closed/open trade-count transposition that passed all 67 tests, and `stale`
  appearing **zero** times in the CLI and e2e files despite being the health value AC #3 exists
  for. All 3 decisions resolved with Allay: exit-4-on-DB-failure accepted and documented rather
  than remapped (residual flagged for the retro); the seven-key `--json` contract upheld and the
  conflict recorded as Judgment call #9; every degradation sense now renders a cause, with a
  catch-all fallback. 21 patches applied. Behaviour changes beyond tests: `_render_degradation`
  (all three senses + fallback), `_as_utc` normalisation at the builder boundary, tolerant
  `runtime_flags` parsing so a malformed JSONB document cannot traceback past AR28's exit table,
  per-command failure prefixes (`live list failed` no longer says `live status failed`), `--json`
  failures routed to stderr so stdout stays parseable, `!=` instead of identity on the status
  comparison, and `overflow="fold"` so a 100-character session name is not silently cropped. The
  review's own fixes were mutation-proven: 12 mutations, 2 survived the first pass and were fixed
  before closeout, all 12 killed after. Final: unit 2377 (+42), component 1290 / 16 skipped,
  integration `--forked` 278 / 2 skipped, DB tier 85, e2e 1, Epic 1 sweep 40/40,
  format/lint/typecheck clean, zero regressions. Two items deferred to `deferred-work.md`.
