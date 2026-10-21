# Story 5.4: Record the Conditions the Session Actually Ran Under

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want each sealed session to carry the conditions that produced it,
so that I can read a metric difference against its causes and its sampling noise instead of
over-interpreting it.

## Why this story is shaped the way it is

Read this before designing anything. Story 5.3 built the seal, and its `config_snapshot` already
carries a `session_conditions` document. Today that document holds exactly one key,
`equity_series`: Story 5.2's namespaced fragment, with the series and its limitations. This story
adds AR7's flat keys beside it, so that a sealed record states the conditions it ran under. It
also takes the run-row wording 5.3 left open (`start_date`, `data_source`).

Drafted 2026-10-11 against head `d57262c` (worktree `hx-p3e5-5-4-…`, clean; SQLAlchemy 2.0.43,
`nautilus-trader 1.220.0`). Three facts decide the design:

1. **Every condition AR7 names is either already in the seal's hands or fixed by code.**
   - Starting capital is the operator's `--starting-capital` (5.3 R1a). The closed-trade count is
     5.2's `equity_series.closed_trades`. The open positions are the session view 5.3 already
     reads (R2a).
   - RTH and the market-data tier are not choices a session can make: the node builder refuses to
     build a node without both (F3, F4).
   - The price basis is structural: live bars are IBKR's, unadjusted and SMART-routed (F5).

   So nothing new has to be *recorded during the run*. No writer on the live path, no migration.
2. **One fact the AC names has no durable source.** AC #4 says `start_date` is "the session's
   first start". Nothing durable records a first start (5.3 F4). 5.3's PO ruling R3a stores
   `created_at` instead, and leaves one function, `period_start`, as the seam this story may
   change. The AC's literal wording is not met by `created_at`. **→ PO ruling R1** (memory: a
   literally-unmet condition goes back to the PO; never judge "intent met" yourself).
3. **Two pieces of routed debt are recording questions, not computing questions.**
   - 5.3 deferred (b): a truncated session seals like a complete one, and its last run's
     `runtime_flags` stay only on the session row.
   - 5.2 deferred (d) and 4.7: whether *this* session absorbed reconciliation-owned activity
     (a corporate action, a correction) that the trades table never sees.

   Both are outside the epic AC. Both were routed here to be dispositioned. **→ PO rulings R2,
   R3.**

### Measured facts — cite, don't re-derive

| # | Fact | Where |
|---|---|---|
| F1 | `session_conditions` today is `{"equity_series": {...}}` and nothing else. 5.2's `_session_conditions` builds it. `SessionResults.merged_into(snapshot)` deep-copies the snapshot, keeps any keys its `session_conditions` already holds, adds `equity_series` beside them, and refuses a second `equity_series`. `run_identity` builds a base snapshot with **no** `session_conditions` (5.3's scope boundary: "5.4 adds its keys before `merged_into`"). | `src/core/session_equity.py:238-269`; `src/services/session_seal_service.py:230-270, 369-386` |
| F2 | `StrategyConfigSnapshot.session_conditions` is `dict[str, Any] \| None`. It is omitted when empty, so every backtest row stays byte-identical (5.2 R2a). Any JSON-native dict persists. **A `Decimal` is not JSON-native**: money must be a string (AR29's convention). | `src/models/config_snapshot.py:51-75` |
| F3 | **Market-data tier.** No live session can run on anything but REALTIME. `resolve_live_market_data_type` overrides the fetch default to REALTIME and refuses any explicit non-REALTIME value. The node builder calls it on every start. A broker-side downgrade (IB 10167) stops the session: `evaluate_bar_freshness`'s verdict reads "this session is stopping rather than acting on prices it would report as live". The name constant is `REQUIRED_LIVE_MARKET_DATA_TYPE_NAME = "REALTIME"`. The PRD's `decisions.marketDataTier` is "Real-time IBKR subscription active on the paper account". | `src/core/live_market_data.py:40-42, 76-116`; `src/core/live_node_builder.py:378`; `src/core/live_bar_observer.py:168-196`; `prd.md:46` |
| F4 | **RTH.** `resolve_live_use_rth` refuses `ibkr_use_rth is not True` on every start. There is no named constant: it returns the literal `True`. | `src/core/live_market_data.py:119-136`; `src/core/live_node_builder.py:379` |
| F5 | **Price basis.** Live bars are IBKR's: unadjusted, SMART-routed. A backtest's bars are FirstRate's: split- and dividend-adjusted, consolidated. The PRD records this as "a permanent, structural divergence source, not a defect to chase". AR7 names the two values `"firstrate_adjusted"` and `"ibkr_unadjusted_smart"`. A backtest writes **no** `session_conditions` at all (F2; AR44 forbids touching the orchestrator). | `prd-epic5-scope.md` "Comparison Integrity"; `architecture.md:428-431`; `docs/agent/nautilus.md` "Corporate actions" |
| F6 | **Starting capital** (R1a). It is checked by `require_sealable_capital` and passed as given to `save_backtest_results(initial_capital=…)`. The column is `Numeric(20,2)`, and `create_backtest_run`'s refresh hands back the stored value: typed `1E+5` reads `100000.00`. The report already shows the stored value (`SealReport.initial_capital = run.initial_capital`). `session_conditions` is built **before** the write, because the snapshot is a write argument, so it cannot read `run.initial_capital`. | `session_seal_service.py:157-175, 452-470`; `tests/unit/services/test_session_seal_service.py:570-577` |
| F7 | **Closed-trade count.** `equity_series.closed_trades = len(legs)`, and legs are the closed rows only. The report's `closed_trade_count` reads it (one source). AR7's flat `closed_trade_count` would be a second source for the same number; 5.2's code review deferred "pin the two equal" here (CLAUDE.md: a duplicated literal needs its own equality pin). | `session_equity.py:259-269, 313-325`; `session_seal_service.py:468`; `deferred-work.md:4152-4155` |
| F8 | **Open positions at seal.** R2a refuses any view position, any other-account position and any unreadable view. So a successful 5.4 seal always has `view.positions == ()`, and `open_positions_at_seal` is always `[]` until 5.5's `--force`. The view nets **every** open position on the account per instrument, including `EXTERNAL`/`INTERNAL-DIFF` ones (`ViewPosition` docstring: "plus any reconciliation-stamped ones"; pinned by `test_several_open_positions_in_one_instrument_are_summed`). So the split's extra shares, held by a synthetic owner after the strategy exits (4.7's debt routed to 5.4), **are** an open position at seal: the seal refuses, naming them. | `session_seal_service.py:324-337`; `src/core/live_session_view.py:16-23, 394-427`; `src/models/reconciliation.py` `ViewPosition`; `tests/component/core/test_live_session_view.py:228-243`; `deferred-work.md:3639-3644` |
| F9 | **Start date** (R3a). `period_start(row)` returns `trading_sessions.created_at`, the only always-present instant never after the first start. `last_started_at` is overwritten on every claim. `owner_epoch` counts claims **and** reclaims, so `owner_epoch == 1` means exactly one `→ running` edge ever. `runtime_flags` is cleared on every `→ running`. | `session_seal_service.py:209-227`; 5.3 F4; `src/db/models/trading_session.py:50-64, 113-131` |
| F10 | **The last run's flags.** `runtime_flags` is `{"v": 1, "failed_strategies": [...], "all_failed": bool, "order_rejections": {...}}`, or `NULL` when nothing happened. It describes **the last process run only**: it is cleared on every `→ running`. `order_rejections.last.reason` is already redacted (NFR26). The seal's `_claim` has the locked row in hand. | `session_service.py:338-344, 480-501, 506-615`; `session_seal_service.py:299-321` |
| F11 | **Reconciliation-owned activity.** No durable per-session record exists: `runtime_flags` holds neither key, and the evidence is in `reconcile.*` logs only. `equity_series.excludes` already names `reconciliation_owned_activity` **structurally** on every record. The engine cache keeps each position's fill list with its `strategy_id` (closed ones too, `flush_on_start=False`). Whether a routine restart leaves `EXTERNAL`/`INTERNAL-DIFF` positions there after Story 4.5's exec filter is **unmeasured**. | `deferred-work.md:4103-4110`; `session_equity.py:59-61`; `src/core/live_cache.py:137-157`; `src/core/live_trade_recorder.py:148` |
| F12 | **The config-snapshot panel** renders every top-level key of `config_snapshot` as `{{ key \| replace('_',' ') \| title }}` / `{{ value }}`, with `additional_params = run.config_snapshot`. So `session_conditions` renders as one "Session Conditions" entry whose value is the dict's text. The same `additional_params` feed `cli_command`, which appends `--session-conditions {…}` to a nonsensical `ntrader backtest run` line for a paper record. That is pre-existing behaviour of an AR44 file; 5.6 owns the view verification. | `templates/partials/config_snapshot.html`; `src/api/models/backtest_detail.py:166-183, 488-522` (read only) |
| F13 | **`data_source`.** `PAPER_DATA_SOURCE = "ibkr_live"` was 5.3's placeholder ("5.4 may refine the wording"). A backtest writes `catalog`, `catalog:<name>`, `ibkr`, `kraken` or `mock`. `build_configuration` maps only `kraken` to a venue, so `ibkr_live` leaves the symbol as is. | `session_seal_service.py:70-73`; 5.3 F8; `backtest_detail.py:503-509` |
| F14 | **Sizes** (`measure_module`): `session_seal_service.py` is 326 of 500 statement lines; `seal_session` 35 of 50; `_claim` 15; `_write_run` 15; `SealReport` 17 of 100. A new small module costs nothing against the caps. | `tests/unit/governance/test_size_caps.py::measure_module` |
| F15 | **Parallel stories.** Both extend the same seal in other worktrees, so keep this story's edit to the seal service small and the conditions document in its own module: the merge stays additive.<br>• 5.5 replaces `_refuse_unless_flat` with `--force` and sets the disposition wording.<br>• 5.8 records a withheld period "in `session_conditions` (Story 5.4)". | `prd-epic5-scope.md` Stories 5.5, 5.8; `deferred-work.md:4186-4190` |
| F16 | **The seal's broker pins are hand-listed.** `TestTheSealReachesNoBroker.MODULES = (SEAL_MODULE, SEAL_CLI_MODULE)` scans direct imports and called names. A new module on the seal path escapes it unless added by hand: the guard-list hazard CLAUDE.md describes. | `tests/unit/services/test_session_seal_service.py:873-897` |

### PO rulings — REQUESTED (options presented; recommendation marked)

- **R1 — The run's `start_date`.** AC #4 says "the session's first start", and 5.3 stores
  `created_at` (R3a). No migration is allowed.
  - **[a] Keep `created_at`, and say so in the record (recommended).** `session_conditions` gains
    `start_date_basis: "session_created_at"`. AC #4 is amended to "the earliest instant the system
    durably holds that is never after the first start". CAGR is biased low, never high, by the
    gap between create and first start (usually minutes: `live create` then `live start`). One
    source for every session, with its meaning stated.
  - **[b] Exact when knowable.** If `owner_epoch == 1`, use `last_started_at`: that is the only
    start ever, so it is the first. Otherwise use `created_at`. `start_date_basis` records which
    (`"first_start"` / `"session_created_at"`). This is exact only for single-run sessions; the
    multi-run norm still falls back. It is 5.3's R3b, which the PO declined then: two sources,
    and which applies depends on history.
  - **[c] A durable first-start writer.** Stamp `first_started_at` on the `CREATED → RUNNING`
    edge into `runtime_flags`, under a key the `→ running` clear preserves. This changes Story
    2.7's "cleared every run" contract on the live path, and every pre-existing session still
    falls back. Not recommended.
- **R2 — The truncated-session record** (5.3 deferred (b), PO-accepted that a truncated session
  seals like a complete one).
  - **[a] Copy the sealed row's `runtime_flags` into `session_conditions` as `last_run_flags`
    (recommended).** It is `null` when the row has none. The name states its reach: the last
    process run only (F10). A record whose last run contained a strategy failure, or ended in a
    rejection streak, then says so where the comparison is read. Plain JSON, copied as stored
    (already redacted, NFR26).
  - **[b] Record nothing.** The flags stay readable on the session row only, and the run record
    cannot show them.
- **R3 — Reconciliation-owned activity** (5.2 deferred (d) and Story 4.7's split debt).
  - **[a] Structural only (recommended).** `price_basis = "ibkr_unadjusted_smart"` names the
    permanent divergence source, and `equity_series.excludes` already names
    `reconciliation_owned_activity` on every record. Per-session detection is recorded in
    deferred-work as having no durable source. A cache-derived flag is one-directional: absence
    is not proof. Its false-positive rate after a restart is unmeasured (F11), and it would widen
    the 4.6 view that 5.5 is extending in parallel.
  - **[b] Detect at seal from the engine cache.** Record every instrument with an
    `EXTERNAL`/`INTERNAL-DIFF`-owned position, open or closed, as
    `reconciliation_owned_instruments`. This widens `SessionView` with one field (not
    `LOAD_METHODS`), and needs a component measurement that a 4.5-era restart creates no such
    position.

**Dispositions recorded without a ruling** (the PO may overrule any):
- **`data_source` stays `"ibkr_live"`** (F13). It identifies the live IBKR source (AC #4), it is
  distinct from every backtest value, and the existing view renders it unmapped. The 5.3
  "may refine" comment becomes this story's decision, pinned by a test.
- **`market_data_tier = "REALTIME"`**, imported from `REQUIRED_LIVE_MARKET_DATA_TYPE_NAME` (F3)
  and never re-typed. **`rth_only = True`**, a named constant pinned to `resolve_live_use_rth`'s
  behaviour (F4). Both are recorded as *policy the session could not run without*; a test ties
  each to the builder's refusal.
- **`starting_capital` is a string at the column's own scale**: `str(capital.quantize(…))`, with
  the cent read from `BacktestRun.initial_capital`'s `Numeric` scale. Capital is already
  whole-cent (5.1's rule), so this changes representation, never value: typed `1E+5` records
  `"100000.00"`, equal to the stored column. Not normalisation: no scaling and no reference to
  any other account (AC #5).
- **`closed_trade_count` is read from `equity_series.closed_trades`**: one computation, two
  places, pinned equal (F7). The report's `closed_trade_count` then reads the flat AR7 key.
- **`open_positions_at_seal` is built from `view.positions`** as
  `[{"instrument_id", "quantity": str}]`. It is `[]` under R2a (F8). 5.5's `--force` makes it
  non-empty without touching this story's code. The 4.7 split debt is satisfied by the view's
  every-owner net (F8): proven, not re-implemented.
- **`"firstrate_adjusted"` exists as a named constant** (`BACKTEST_PRICE_BASIS`) so that
  "distinct from a backtest's" is checkable. **A backtest still writes no `session_conditions`**
  (F2, AR44); the vocabulary is pinned closed (`PRICE_BASES`).

### Design decisions (drafted on R1a / R2a / R3a; a different ruling amends them)

- **D-A — `src/services/session_conditions.py` owns the AR7 document.** It is new, small and pure:
  no DB, no I/O. Its one edge into `src.core` is `live_market_data` (F3), the direction the
  architecture allows. It holds:
  - `SCHEMA_VERSION = 1`;
  - `PAPER_PRICE_BASIS = "ibkr_unadjusted_smart"`, `BACKTEST_PRICE_BASIS = "firstrate_adjusted"`,
    and `PRICE_BASES` (both);
  - `MARKET_DATA_TIER = REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`;
  - `RTH_ONLY = True`;
  - `START_DATE_BASIS = "session_created_at"` (R1a);
  - `AR7_KEYS`: the exact tuple of AR7's seven names;
  - `build_session_conditions(*, starting_capital, closed_trade_count, open_positions,
    last_run_flags, start_date_basis) -> dict[str, Any]`.

  The builder returns JSON-native values only. It refuses a negative `closed_trade_count`, and
  refuses `open_positions` that are not `ViewPosition`s, with `ValueError`/`TypeError`; the seal
  wraps them into `SealRefusedError` the way `_reconstruct` does. *Why its own module:* 5.5 and
  5.8 extend the same document in parallel (F15). The seal service gains about six lines, so the
  merge stays additive.
- **D-B — The seal service wires it in one place.**
  - `_Claimed` gains `runtime_flags: dict | None`, a deep copy taken in `_claim` (plain values
    across the async boundary, the existing rule).
  - `_write_run` gains the view's positions. It builds
    `identity.config_snapshot | {"session_conditions": build_session_conditions(...)}` and then
    calls `results.merged_into(...)`, so `equity_series` lands **beside** the AR7 keys (F1). The
    count passed is `results.session_conditions[EQUITY_SERIES_KEY]["closed_trades"]`.
  - `seal_session`'s statement count stays under 50. If it would not, move the argument assembly
    into a helper; never baseline.
  - `SealReport.closed_trade_count` reads `conditions["closed_trade_count"]`.
  - `period_start` is unchanged under R1a. Its docstring's "Story 5.4 owns … and may replace this
    source" becomes the ruling.
  - The `PAPER_DATA_SOURCE` comment records the disposition.
- **D-C — The document's shape is pinned exactly, both ways.**
  - Unit: the builder's key set equals `AR7_KEYS` plus the ruled extras (`start_date_basis`,
    `last_run_flags`).
  - Unit: the stored `session_conditions`' key set equals that plus `equity_series`.
  - Integration/db: the same, read back from Postgres.
  - Each value's type is pinned: `str` capital, `bool` RTH, `int` count and version, `list`
    positions.
  - `AR7_KEYS` itself is pinned against a literal copied from `prd-epic5-scope.md`, so a dropped
    name goes red. The builder's output is pinned against `AR7_KEYS`, so a key never added also
    goes red. That is the two-directional discipline CLAUDE.md asks for.
- **D-D — The equality pins.** `closed_trade_count == equity_series.closed_trades` (unit and
  integration/db). `starting_capital == str(run.initial_capital)` read back from Postgres,
  including the `1E+5` case. `PAPER_PRICE_BASIS != BACKTEST_PRICE_BASIS`.
  `MARKET_DATA_TIER == REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`. `RTH_ONLY is
  resolve_live_use_rth(<rth on>)`, and the resolver refuses `False`.
- **D-E — AC #6 is proven by rendering, not by assertion.** A unit test renders the **existing**
  `templates/partials/config_snapshot.html` through Jinja2 (`FileSystemLoader("templates")`),
  with `view.configuration = build_configuration(<a sealed run>)`. It asserts that "Session
  Conditions" and each AR7 key and value appear in the HTML. Both files are **read**, never
  written. A `git diff` check in Task 6 proves `src/api/**`, `templates/**` and
  `src/services/backtest_query.py` are zero-diff.
- **D-F — The seal's broker pins cover the new module.** `TestTheSealReachesNoBroker.MODULES`
  gains `src/services/session_conditions.py` in the commit that creates it (F16).

## Acceptance Criteria

Epic ACs 1–6 are copied verbatim from `_bmad-output/planning-artifacts/prd-epic5-scope.md` (Story
5.4). Each is followed by how this story proves it. ACs 7–9 are this story's own, from the routed
debt. *Proofs are written for R1a/R2a/R3a.*

1. **Given** a sealed session
   **When** its `config_snapshot` is written
   **Then** it contains a `session_conditions` key holding `starting_capital`, `rth_only`, `price_basis`,
   `market_data_tier`, `closed_trade_count`, `open_positions_at_seal`, and `schema_version: 1` (FR43,
   FR45, AR7).
   *Proof:*
   - (unit) the builder emits exactly `AR7_KEYS` plus the ruled extras, and `AR7_KEYS` equals the
     PRD's seven names;
   - (unit) the snapshot handed to `save_backtest_results` carries them beside `equity_series`;
   - (integration/db) the sealed row's `config_snapshot->'session_conditions'`, read back from
     Postgres, holds every key with `schema_version = 1`.

2. **Given** `price_basis`
   **When** it is set for a paper session
   **Then** it records `"ibkr_unadjusted_smart"`, distinct from a backtest's
   `"firstrate_adjusted"` — the permanent structural divergence source named up front (FR45).
   *Proof:* unit tests pin the paper value, the inequality and the closed `PRICE_BASES`. An
   integration/db test reads `"ibkr_unadjusted_smart"` back. A backtest row written in the same
   test carries no `session_conditions` at all, so its snapshot stays byte-identical (5.2 R2a).

3. **Given** the closed-trade count
   **When** it is recorded
   **Then** it travels with the record so a difference can be read against sampling noise (FR43).
   *Proof:* `closed_trade_count` equals `equity_series.closed_trades` and `performance_metrics.
   total_trades` (unit and integration/db, with 4 closed legs and with 0). The report's
   `closed_trade_count` is the stored key's value.

4. **Given** the run row's own columns
   **When** they are populated
   **Then** `initial_capital` matches the session's starting capital, `start_date` is the session's
   ~~first start~~ **creation (`trading_sessions.created_at`), recorded as
   `session_conditions.start_date_basis = "session_created_at"`**, `end_date` is the seal time, and
   `data_source` identifies the live IBKR source.
   *Amended 2026-10-11 by PO ruling R1a:* nothing durable records a first start and no migration
   is allowed, so `start_date` is the earliest instant the system durably holds that is never
   after the first start. The record says so. This is a **disclosed deviation** from the epic's
   "first start" (Dev Agent Record).
   *Proof (integration/db):*
   - `initial_capital == Decimal(session_conditions.starting_capital)`;
   - `end_date == sealed_at`;
   - `data_source == "ibkr_live"`;
   - `start_date == created_at`, with `session_conditions.start_date_basis == "session_created_at"`.

5. **Given** starting capital
   **When** the record is written
   **Then** the paper session's own starting balance is stored as-is, with no normalization logic
   anywhere — comparability comes from matching the accounts, not from arithmetic (AR7).
   *Proof:*
   - (unit) `starting_capital` is the operator's capital at the column's scale, never scaled,
     and never read from the linked backtest (a link with a different capital leaves it
     unchanged);
   - (unit) `1E+5` records `"100000.00"`;
   - (integration/db) it equals the stored `initial_capital`.

6. **Given** the new key
   **When** the existing config-snapshot panel renders it
   **Then** it displays without any UI change, because it is ordinary JSONB in an existing column (AR7).
   *Proof:* D-E's render test over the unmodified partial and presentation model, plus Task 6.3's
   zero-diff check.

7. **Given** the conditions recorded at seal
   **When** their provenance is inspected
   **Then** `rth_only` and `market_data_tier` are tied to the policy the node builder enforces
   on every start (F3, F4), never re-typed independently.
   *Proof:* D-D's pins. `resolve_live_use_rth` refuses `False`, and
   `resolve_live_market_data_type` refuses an explicit non-REALTIME value: the recorded values are
   the only ones a session could have run on.

8. **Given** a session whose strategy has exited but whose engine cache still holds a synthetic
   owner's shares (the split's extra shares, Story 4.7)
   **When** seal is attempted
   **Then** those shares are an open position at seal: under R2a the seal refuses, naming the
   instrument and quantity. `open_positions_at_seal` is built from the same view, so 5.5's
   `--force` records them rather than hiding them.
   *Proof:*
   - (component) a strategy position closed to flat plus an `EXTERNAL`-owned `+10` reads as
     `AAPL.NASDAQ +10` in the view;
   - (unit) the seal refuses that view, naming `+10`;
   - (unit) the builder lists a given view position verbatim.

9. **Given** the routed debt (5.3 (b), 5.2 (d), 4.7) and R2/R3
   **When** the record is written
   **Then** under R2a, `last_run_flags` is the sealed row's `runtime_flags` as stored, or `null`.
   Under R3a, nothing per-session is claimed about reconciliation-owned activity; the structural
   statement stays in `price_basis` and `equity_series.excludes`, and the disposition is recorded
   in deferred-work.
   *Proof:* unit tests (flags present, absent, and a strategy failure plus a rejection summary
   copied deep and equal). An integration/db test copies a real `runtime_flags` document.

## Tasks / Subtasks

- [x] **Task 0 — Gate and standing checks** (AC: all)
  - [x] 0.1 **PO rulings R1–R3 are recorded in the Dev Agent Record before any edit.** A
    non-recommended ruling amends D-A/D-B and the AC #4/#9 proofs first.
  - [x] 0.2 `git status` clean. Record baselines with counts in the Debug Log:
    - `uv run pytest tests/unit -n auto`;
    - `uv run pytest tests/component` (sequential);
    - `uv run pytest tests/integration/db` (test DB up).

    Record any pre-existing failure before editing.
- [x] **Task 1 — Unit tier, red first** (AC: #1–#9)
  - [x] 1.1 New `tests/unit/services/test_session_conditions.py`:
    - `AR7_KEYS` equals the PRD's seven names (a literal copied from `prd-epic5-scope.md` AR7);
    - the builder's key set is exact (D-C);
    - every value is JSON-native (`json.loads(json.dumps(doc)) == doc`), and each type is pinned;
    - `schema_version == 1`;
    - price basis: the paper value, the inequality and the closed vocabulary;
    - `MARKET_DATA_TIER == REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`; `RTH_ONLY` against
      `resolve_live_use_rth`, both values;
    - capital: `100000.00` and `1E+5` both give `"100000.00"`, and the scale is read from the
      column;
    - `open_positions` is listed verbatim with quantity as a string, and `()` gives `[]`;
    - a negative count, or a non-`ViewPosition`, is refused;
    - `last_run_flags`: `None` → `None`; a document → an equal **deep** copy (mutate the input
      afterwards, and the output is unchanged).
  - [x] 1.2 `tests/unit/services/test_session_seal_service.py` (additions; existing tests stay):
    - the stored snapshot's `session_conditions` holds AR7 plus extras plus `equity_series`;
    - `closed_trade_count == equity_series.closed_trades`, and the report reads the flat key;
    - `last_run_flags` is copied from the locked row;
    - the capital in the conditions is the operator's, not the linked backtest's;
    - a synthetic-owner view position refuses, naming it (AC #8);
    - `PAPER_DATA_SOURCE == "ibkr_live"` (AC #4 disposition);
    - `TestTheSealReachesNoBroker.MODULES` gains the new module (D-F).

    **Update, don't delete,** 5.3's `test_the_snapshot_carries_the_series_beside_the_base_keys`:
    its `session_conditions == {"equity_series": …}` expectation is exactly what this story
    widens. Record that edit in the Dev Agent Record.
  - [x] 1.3 D-E render test: `tests/unit/api/test_config_panel_renders_session_conditions.py` (or
    under `tests/ui/` if that suite's fixtures fit; record which and why). It renders the
    existing partial via Jinja2 with `build_configuration(<sealed run stand-in>)`, and asserts
    the "Session Conditions" label plus every AR7 key and value in the HTML.
  - [x] 1.4 AC #8 component half: `tests/component/core/test_live_session_view.py`. Add one test:
    a strategy position closed to flat, plus an `EXTERNAL`-owned `+10`, reads as one
    `AAPL.NASDAQ` row of `+10`. Use the existing `_position` helper.
- [x] **Task 2 — Implement** (AC: #1–#9), only after Task 1 is red for the right reason.
  - [x] 2.1 `src/services/session_conditions.py` (D-A).
  - [x] 2.2 `session_seal_service.py` wiring (D-B), with docstring and comment updates
    (`period_start`, `PAPER_DATA_SOURCE`, and the module docstring's "Does not own").
  - [x] 2.3 Size caps: new code under 500 / 100 / 50. `test_size_caps.py` must pass with **no**
    new baseline entry.
- [x] **Task 3 — Integration/db tier** (AC: #1–#5, #9), in `tests/integration/db/test_session_seal.py`
  (additions, on the existing `_seed` and the rolled-back `db_session`; read-backs are raw SQL
  after `expire_all()`):
  - [x] 3.1 Seal the 4-leg session. Read `config_snapshot -> 'session_conditions'` back and
    assert:
    - the exact key set and every AR7 value;
    - `closed_trade_count == equity_series.closed_trades == metrics.total_trades == 4`;
    - `Decimal(starting_capital) == initial_capital`;
    - `start_date == created_at`, with the basis recorded;
    - `data_source == 'ibkr_live'`.
  - [x] 3.2 Typed `1E+5` → the conditions read `"100000.00"`, equal to the column.
  - [x] 3.3 Zero legs → `closed_trade_count` 0, `open_positions_at_seal` `[]`.
  - [x] 3.4 R2a: seed `runtime_flags` through the real `SessionService.record_strategy_failure`
    while running, then stop and seal. The flags are copied equal. A session with none
    records `null`.
  - [x] 3.5 A backtest row written beside it has no `session_conditions` key (AC #2).
- [x] **Task 4 — Docs and routed debt**
  - [x] 4.1 `docs/agent/persistence.md`: the `session_conditions` document (keys, sources, the
    two equality pins, `start_date_basis`), beside the existing seal section.
  - [x] 4.2 `docs/agent/architecture.md`: `src/services/session_conditions.py` in the source tree.
  - [x] 4.3 `README.md`: validate the `live seal` section. If it lists the report's
    `session_conditions` contents, update it; otherwise leave it unchanged and say so.
  - [x] 4.4 `deferred-work.md`, a new "## Deferred from: story-5.4 (2026-10-11)":
    - the R3 disposition (per-session reconciliation-owned detection: no durable source; the
      cache-derived option and its unmeasured restart false positives);
    - F12's `cli_command` observation → **5.6**;
    - R1's recorded limitation, if R1a.
  - [x] 4.5 Disposition every routed item in the Dev Agent Record:
    - 5.2 (d) and the deferred `closed_trade_count` pin;
    - 5.3 (b), (c) and (f);
    - 4.7's two split items;
    - the PRD's "Story 5.2 / 5.4" and "Story 5.4" routed-debt lines.
- [x] **Task 5 — Mutation sweep.** Copy the target file to `/tmp/p54/`, mutate, run the target
  tests, then restore by copying back. **Never `git stash`.** Record kill or survive for each:
  - M1 the builder drops `market_data_tier` → 1.1;
  - M2 the builder adds an unlisted key → 1.1;
  - M3 `closed_trade_count` from `len(trades)` (open rows included) instead of the series → 1.2 / 3.1;
  - M4 capital not quantised (`str(capital)`) → 1.1 / 3.2;
  - M5 `price_basis` set to `BACKTEST_PRICE_BASIS` → 1.1 / 3.1;
  - M6 conditions added **after** `merged_into` with a shallow `|`, replacing `equity_series` → 1.2;
  - M7 `last_run_flags` aliased rather than copied → 1.1;
  - M8 `open_positions_at_seal` hard-coded `[]` → 1.1;
  - M9 `RTH_ONLY = False` → 1.1;
  - M10 D-F's module removed from `MODULES` → the review check (a guard-list omission is not
    self-detecting; record the survival honestly, as 4.8's M7 did).
- [x] **Task 6 — Gates**
  - [x] 6.1 `uv run ruff format .` · `uv run ruff check .` · `make typecheck`.
  - [x] 6.2 Run each suite and record its counts against Task 0.2:
    - `uv run pytest tests/unit -n auto`;
    - `uv run pytest tests/component` (sequential);
    - `uv run pytest tests/integration/db`;
    - `uv run pytest tests/integration -n auto --forked`.
  - [x] 6.3 `git diff --stat` against the base shows these untouched:
    - `src/api/**`, `templates/**` and `src/services/backtest_query.py`;
    - `src/core/backtest_orchestrator.py`, `src/core/results_extractor.py` and
      `src/core/session_equity.py`;
    - `src/services/backtest_persistence.py` and both backtest repositories;
    - `src/db/models/**` and `alembic/**`.

### Review Findings

Code review 2026-10-11, three parallel layers:
- **Blind Hunter:** the diff only.
- **Edge Case Hunter:** the diff plus the project. Its probes are in `/tmp/p54_ech/`.
- **Acceptance Auditor:** the diff, the story, the PRD extract and CLAUDE.md.

The auditor rated all nine ACs **met** and every binding constraint **held**. Its one real defect
was a cache-hidden `ruff` I001. Raw: 13 blind, 6 edge and 7 auditor findings. After dedup and
triage: **0 decision-needed, 13 patch, 2 defer, 8 dismissed.**

Dismissed:
- **NFR26 on `last_run_flags`.** `detail` is redacted and cut to one line at write
  (`_one_redacted_line`), `reason` is redacted at write (Story 3.7), and the flags hold no raw
  account by construction.
- **`ibapi` reached transitively through `live_market_data`.** It is an enum module and opens no
  connection; the seal CLI already reaches `ibapi` through `live_session_view`.
- **`import copy` missing.** It is present (`session_seal_service.py:28`).
- **A clean run then stop is untested.** `_seed`'s stopped path is CREATED→RUNNING→STOPPED, and
  `→ running` clears the flags to NULL, so the first integration test is that case.
- **Position quantity form and order.** It is the same `str()` 5.3's report already prints; the
  view sorts by `instrument_id` and drops zero rows.
- **The panel test pins a Python repr.** The per-key assertions do not depend on key order; the
  whole-text check is exact for the input given.
- **README "stored exactly as given".** That is 5.3's text and its column behaviour; the
  conditions equal the column.
- **The AC #2 backtest side is narrow.** That is acceptable under AR44.

- [x] [Review][Patch] **P1 — The new seal-service test import is in the third-party block
  (I001)**, hidden by ruff's cache. `ruff check --no-cache` fails, and CI would fail.
  [tests/unit/services/test_session_seal_service.py:14] (auditor)
- [x] [Review][Patch] **P2 — `_render` crashes on a list whose first item is a mapping and a
  later one is not.** A hand-edited or restored `runtime_flags` reaches it through
  `last_run_flags`. The human report then raises `AttributeError` **after the commit**, the
  command exits 1 with a traceback, and a retry is refused as already sealed. That breaks 5.3's
  rule that a committed seal is never reported as failed. Render items that are not mappings as
  text. [src/services/session_seal_service.py:572-576] (edge)
- [x] [Review][Patch] **P3 — Free text breaks the human form's one-line-per-key shape.**
  - A `reason` holding `\n` prints a second line at column 0, which reads as a top-level key.
  - `{}` renders as a bare header.
  Escape newlines in rendered text, and render an empty mapping as `{}`.
  [src/services/session_seal_service.py:565-580] (edge)
- [x] [Review][Patch] **P4 — A `runtime_flags` that is not an object refuses a stopped session
  forever.** That contradicts R2a's "as stored" and the rule that a stopped session must always
  be sealable. Copy any stored value deep, as stored. [src/services/session_conditions.py:146-149]
  (edge)
- [x] [Review][Patch] **P5 — `closed_trade_count` and `performance_metrics.total_trades` are never
  checked equal.** The unit test seals a record whose metrics say 99 while its conditions say 3,
  and its docstring claims "three, everywhere". Refuse a mismatch, naming both counts (the
  backfill-count precedent), and correct the test. [src/services/session_seal_service.py
  `_conditions`] (blind)
- [x] [Review][Patch] **P6 — `_conditions` turns a `TypeError` (a code defect) into an
  operator-safe refusal.** Narrow the catch to `ValueError`/`ArithmeticError`, the `_reconstruct`
  precedent. [src/services/session_seal_service.py `_conditions`] (blind)
- [x] [Review][Patch] **P7 — A key in both the AR7 keys and the results' `session_conditions`
  would be overwritten silently** by `merged_into`, which guards only `equity_series`; that file
  is zero-diff. Refuse any overlap in the seal before merging. [src/services/session_seal_service.py
  `_write_run`] (blind+edge)
- [x] [Review][Patch] **P8 — `_Claimed.runtime_flags` defaults to `None`,** so a second
  construction site that forgets it would record a clean run. Make the field required.
  [src/services/session_seal_service.py:139] (blind)
- [x] [Review][Patch] **P9 — The builder's `start_date_basis` parameter accepts any string,** and
  a caller could make the record contradict its own `start_date`. Production never passes it.
  Remove the parameter and always record the ruled constant.
  [src/services/session_conditions.py `build_session_conditions`] (blind)
- [x] [Review][Patch] **P10 — A non-finite capital is refused as "fractions of a cent".** Refuse
  it in its own words. [src/services/session_conditions.py `_capital`] (blind)
- [x] [Review][Patch] **P11 — The module docstring says RTH is "never re-typed"**, but `RTH_ONLY`
  is a literal pinned by a test (there is no upstream constant). It also does not say these values
  are the enforced policy asserted at seal time. Correct both. [src/services/session_conditions.py
  docstring] (blind)
- [x] [Review][Patch] **P12 — Test and traceability gaps:**
  - the CLI JSON branch does not assert `start_date_basis`;
  - the AR36 fixture's "realistic" flags lack the free-text `detail` / `last.reason`;
  - the `5.4g`/`5.4h`/`5.4i` markers cite the PRD, although ACs 7–9 are the story's own;
  - the `5.4h` text says "recorded" where the seal refuses.

  [tests/unit/cli/commands/test_live_seal_cli.py; marker sites] (blind+edge+auditor)
- [x] [Review][Patch] **P13 — Three Dev Agent Record claims are inaccurate:**
  - "`ruff check` clean" was a warm-cache result;
  - M3 was swapped from `len(trades)` to `result.total_trades` with no note;
  - the closed-trade-count "CLI report" pin overstates what the CLI test checks.

  [story Dev Agent Record] (auditor)
- [x] [Review][Defer] **W1 — Under 5.5's `--force`, the builder cannot record "unknown" or
  skipped (other-account) positions.** `[]` or a partial list would read as flat. Unreachable
  today, because R2a refuses first. [src/services/session_conditions.py:130-140] — deferred to
  Story 5.5 (edge)
- [x] [Review][Defer] **W2 — `rth_only` / `market_data_tier` / `price_basis` are asserted at
  seal time from the policy every Phase-3 build enforces, not recorded per run.** A future build
  that relaxes RTH or REALTIME would make a later seal state the new policy for a session run
  under the old one. [src/services/session_conditions.py] — deferred to whoever relaxes either
  policy (blind)

## Dev Notes

### The current surface — exact extension points

- `src/services/session_seal_service.py:369-386` — `_write_run`. The conditions join
  `identity.config_snapshot` here, **before** `results.merged_into(...)`.
- `src/services/session_seal_service.py:299-321` — `_claim`. Copy `row.runtime_flags` (deep)
  into `_Claimed`.
- `src/services/session_seal_service.py:411-477` — `seal_session`. It passes `view.positions`
  to `_write_run`, and reads `closed_trade_count` from the flat key.
- `src/core/session_equity.py:238-256` — `merged_into`. **Unchanged.** It already keeps the AR7
  keys and refuses a second `equity_series`.
- `src/core/live_market_data.py:42` — `REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`, imported, never
  re-typed.
- `tests/unit/services/test_session_seal_service.py` — the `Harness` (a stub `AsyncSession`,
  with `reconstruct_session_results` and the persistence service as spies) is the seam to extend.
  The stub `_save` already quantises `initial_capital` like the real refresh.

### Sketch (shape, not a contract — the tests are the contract)

```python
# src/services/session_conditions.py
SCHEMA_VERSION = 1
PAPER_PRICE_BASIS = "ibkr_unadjusted_smart"
BACKTEST_PRICE_BASIS = "firstrate_adjusted"
PRICE_BASES = (BACKTEST_PRICE_BASIS, PAPER_PRICE_BASIS)
MARKET_DATA_TIER = REQUIRED_LIVE_MARKET_DATA_TYPE_NAME
RTH_ONLY = True
START_DATE_BASIS = "session_created_at"
AR7_KEYS = ("starting_capital", "rth_only", "price_basis", "market_data_tier",
            "closed_trade_count", "open_positions_at_seal", "schema_version")

def build_session_conditions(*, starting_capital, closed_trade_count, open_positions,
                             last_run_flags, start_date_basis=START_DATE_BASIS) -> dict: ...

# session_seal_service._write_run
conditions = build_session_conditions(
    starting_capital=..., closed_trade_count=results.session_conditions[EQUITY_SERIES_KEY]["closed_trades"],
    open_positions=open_positions, last_run_flags=claimed.runtime_flags)
snapshot = results.merged_into({**identity.config_snapshot, "session_conditions": conditions})
```

### Scope boundaries — do NOT build

- No `--force`, no null-exit rows, no disposition wording: those are 5.5's.
- No withheld period: that is 5.8's. Leave the builder's signature easy to widen by keyword.
- No change to `session_equity.py`, `results_extractor.py`, `BacktestResult`,
  `BacktestPersistenceService`, the backtest repositories, any DB model, any migration, or
  `StrategyConfigSnapshot` (its `session_conditions` field already takes any dict).
- No change to `src/api/**`, `templates/**` or `backtest_query.py`. **If the render test seems to
  need one, the design is wrong. Stop and ask.**
- No broker read, no node, no order. NFR33: this story is database-only, so **no P-number** in
  `docs/qa/phase3-live-verification.md`. **Never run `live seal` against a real session.**
- No live-path writer (R1c / R2 / R3c are not recommended). `runtime_flags`' clear semantics are
  untouched.

### Hazards

- **5.3's exact-snapshot test widens.** `test_the_snapshot_carries_the_series_beside_the_base_keys`
  asserts `session_conditions == {"equity_series": …}`. It must change, deliberately, with its
  reason. Do not weaken it to a subset check: pin the new exact set.
- **AR36's word scan.** `_ar36_violations` scrubs `closed_trades` and `closed_trade_count`. The
  human report now prints `open_positions_at_seal` and `last_run_flags`. Check no AR36 stem
  (`close/halt/kill/pause/finalize`) appears in the new key names. `last_run_flags` content can
  carry an `error_type`/`detail` string: render it through the existing `_render`, and check that
  the CLI AR36 test still passes with a realistic flags document.
- **`last_run_flags` and NFR26.** The rejection summary's `reason` is redacted at write
  (Story 3.7). Copy it as stored, and do not re-render or reformat it. The CLI report prints it,
  so keep a test with a `failed_strategies` entry, and confirm no raw account appears (the flags
  never hold one by construction).
- **JSONB has no `Decimal` and no tuple.** Strings and lists only. The builder's JSON round-trip
  test catches a tuple that would come back from Postgres as a list.
- **A test that cannot fail** (restated every story since Epic 2):
  - 1.1's exact key set needs its literal-from-the-PRD sibling;
  - 1.3 must render the real file, not a copy;
  - 3.1 must read through fresh SQL after `expire_all()`.
- **Import placement.** Land each import with its first use in the same edit (F401 is
  unfixable, and the commit gate blocks).

### Project Structure Notes

- New: `src/services/session_conditions.py`, `tests/unit/services/test_session_conditions.py`,
  and the D-E render test. Each is a genuinely new concept: the AR7 document, and its
  render-proof.
- Modified: `src/services/session_seal_service.py`,
  `tests/unit/services/test_session_seal_service.py`,
  `tests/integration/db/test_session_seal.py`,
  `tests/component/core/test_live_session_view.py`, the docs, `deferred-work.md` and
  `sprint-status.yaml` (this story's key only).
- No guard list in CLAUDE.md changes: no `live_*` module is split or created, no stdlib import
  is added to a live-path module, and no membership-pinned constant changes. D-F's
  `MODULES` is a test-local list and is edited in the same commit.

### References

- [Source: `_bmad-output/planning-artifacts/prd-epic5-scope.md` — Story 5.4, AR7, AR35, routed debt]
- [Source: `_bmad-output/planning-artifacts/architecture.md:221-226, 428-431, 529-536`]
- [Source: `_bmad-output/planning-artifacts/prd.md:46` — `marketDataTier`]
- [Source: `_bmad-output/implementation-artifacts/5-3-seal-a-stopped-session-into-a-comparable-run-record.md` — R1a–R4a, D-A..D-K, scope boundaries]
- [Source: `_bmad-output/implementation-artifacts/5-2-reconstruct-a-sealed-sessions-equity-curve-from-its-trades.md` — `equity_series`, `merged_into`]
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md:3629-3644, 4103-4110, 4152-4155, 4171-4192`]
- [Source: `src/services/session_seal_service.py`, `src/core/session_equity.py`, `src/core/live_market_data.py`, `src/core/live_session_view.py`, `src/models/config_snapshot.py`]
- [Source: `templates/partials/config_snapshot.html`, `src/api/models/backtest_detail.py` — read only]

## Dev Agent Record

### PO rulings — RULED 2026-10-11: R1a · R2a · R3a (all recommended)

Recorded before any edit (Task 0.1). PO notes with the ruling, binding on implementation:
- **R1a.** `start_date` stays `trading_sessions.created_at`, recorded as
  `session_conditions.start_date_basis = "session_created_at"`. AC #4 was amended in this file
  to match. **This is a disclosed deviation from the epic's "first start":** nothing durable
  records a first start, and there is no migration this phase. CAGR can read low by the
  create→first-start gap, never high. Recorded in deferred-work (story-5.4 (b)).
- **R2a.** The sealed row's `runtime_flags` are copied, as stored, into
  `session_conditions.last_run_flags` (`null` when the row has none). They cover the last
  process run only.
- **R3a.** Per-session reconciliation-owned detection goes to deferred-work (story-5.4 (a)), not
  this story. The structural statement stays in `price_basis` and `equity_series.excludes`.
- **Dispositions confirmed by the PO:**
  - `data_source` stays `"ibkr_live"`;
  - `"firstrate_adjusted"` exists only as a named constant, and a backtest still writes no
    `session_conditions`;
  - `open_positions_at_seal` has the shape `[{instrument_id, quantity}]`.
- **Constraints:** no new column, no live-path writer, no broker access, zero UI-path edits.
- **How "keep everything inside the existing session_conditions builder" was read:** every new
  fact rides in the one `config_snapshot["session_conditions"]` JSONB document, written by the
  builder the story's D-A specified (`src/services/session_conditions.py`). The ruling's own
  gloss lists what it excludes (a new column, a live-path writer, broker access, UI edits), and a
  new pure Python module is none of those. 5.2's `session_equity._session_conditions` (the
  `equity_series` fragment) is unchanged: `session_equity.py` is on the zero-diff list.

### Agent Model Used

Claude Opus 5.5 (`claude-opus-5-5`), in the harness worktree `hx-p3e5-5-4-20261011-093755-1`,
branch `harness/s5-4-20261011-093755-1`.

### Debug Log References

- **Baselines (Task 0.2), head `d57262c`, clean tree:**
  - `uv run pytest tests/unit -n auto`: **3685 passed**;
  - `uv run pytest tests/component` (sequential): **1975 passed, 16 skipped**;
  - `uv run pytest tests/integration/db`: **143 passed**.

  No pre-existing failure.
- **Red first.** `test_session_conditions.py` failed at collection
  (`ModuleNotFoundError: src.services.session_conditions`). The seal-service additions then
  failed with 8 `KeyError`/`AttributeError`s (no AR7 keys stored, no `build_session_conditions`
  seam).
  - Two tests were green on first run, by design. Both are characterisations of the R2a refusal
    that already exists and of the view's every-owner net (F8), proven rather than
    re-implemented: the synthetic-owner refusal (unit) and the component view test.
  - One test fix was my own: ibapi's `MarketDataTypeEnum.REALTIME` is the int `1`, not an enum
    member, so the tier pin reads it through `MarketDataTypeEnum.toStr`.
- **After (Task 6.2):**
  - unit: **3732 passed** (+47);
  - component: **1976 passed, 16 skipped** (+1);
  - integration/db: **150 passed** (+7);
  - `uv run pytest tests/integration -n auto --forked`: **369 passed, 2 skipped** (the two
    IBKR-gated parity tests).
- **One false alarm, recorded honestly.** My first integration/db re-run started at the same
  moment as the forked integration run; both hit the same test database. It reported
  `2 failed, 11 errors` ("Session name 'seal-alpha' already exists"). Run alone, integration/db
  is 150/150 green. Never run the two concurrently.
- **Gates (6.1):** `ruff format --check` (579 files), `ruff check` and `make typecheck` (123
  files) are all clean. One import-order fix was needed: the auto-formatter had sorted the new
  module's import as third-party while the module did not exist yet.
  - **Corrected by code review P13:** the original "`ruff check` clean" was a **warm-cache**
    result. A second I001 of the same cause, in `test_session_seal_service.py`, only showed
    under `ruff check --no-cache`. It is fixed, and every gate since has been run with
    `--no-cache`.
- **Size caps (2.3):** `test_size_caps.py` 14/14, no new baseline entry.
  - `session_seal_service.py` 340/500 statement lines, with `seal_session` 36/50 and `_Claimed`
    9/100;
  - `session_conditions.py` 85.
  - After the code-review patches: `session_seal_service.py` 357/500, `_conditions` 22/50,
    `seal_session` 36/50; `session_conditions.py` 77.
- **Zero-diff (6.3):** `git diff --name-only` touches none of:
  - `src/api/**`, `templates/**` or `src/services/backtest_query.py`;
  - `src/core/backtest_orchestrator.py`, `src/core/results_extractor.py` or
    `src/core/session_equity.py`;
  - `src/services/backtest_persistence.py`;
  - `src/db/**` or `alembic/**`.
- **Mutation sweep (Task 5)**, via `/tmp/p54/mutate.py`: copy to `/tmp/p54/orig`, mutate, run
  the target tests, copy back. Never `git stash`. **11/12 killed:**
  - M1 drop `market_data_tier` — KILLED;
  - M2 unlisted key — KILLED;
  - M3 count from `result.total_trades` instead of the series — KILLED. *(Corrected by code
    review P13: Task 5 planned M3 as `len(trades)`, but `_conditions` never sees the rows, so
    the expressible recount was `result.total_trades`. The unit test, with 5 rows against a
    series of 3, would kill either. This was swapped with no note, which was my mistake.)*
  - M4 capital not quantised — KILLED;
  - M5 backtest price basis — KILLED;
  - M6 shallow merge after `merged_into` (replaces `equity_series`) — KILLED;
  - M7 flags shallow-copied — KILLED;
  - M8 positions hard-coded `[]` — KILLED;
  - M9 `RTH_ONLY = False` — KILLED;
  - **M10 the new module dropped from `TestTheSealReachesNoBroker.MODULES` — SURVIVED.** This is
    the guard-list class CLAUDE.md names: a hand-maintained list has no completeness check, the
    4.8 M7 precedent. It is disclosed, not hidden. The list was edited in the same commit that
    created the module (D-F);
  - M11 `_claim` drops the flags — KILLED;
  - M12 `start_date_basis` dropped — KILLED.

  `git status` was confirmed restored after the sweep.

### Completion Notes List

- Ultimate context engine analysis completed - comprehensive developer guide created.
- **What shipped.** `src/services/session_conditions.py` is new and pure: no database, no clock,
  no I/O.
  - **Vocabulary:** `SCHEMA_VERSION`, `PAPER_PRICE_BASIS`/`BACKTEST_PRICE_BASIS`/`PRICE_BASES`,
    `MARKET_DATA_TIER` (imported `REQUIRED_LIVE_MARKET_DATA_TYPE_NAME`), `RTH_ONLY`,
    `START_DATE_BASIS`, `AR7_KEYS`, `CAPITAL_QUANTUM` (read from
    `backtest_runs.initial_capital`'s own `Numeric` scale).
  - **`build_session_conditions(...)`** returns AR7's seven keys plus `start_date_basis` and
    `last_run_flags`. Every value is JSON-native; money is a string at the column's scale.
  - **Refusals** (`TypeError`/`ValueError`): a non-finite capital (P10), a capital the column
    would round, a negative or non-int count, a non-`ViewPosition`.
  - *(After code review: `last_run_flags` takes any stored JSON, never refused (P4).
    `start_date_basis` is no longer a parameter (P9).)*
- **The seal service gained a few lines:**
  - `_Claimed.runtime_flags`, a deep copy taken in `_claim`;
  - `_conditions(...)`, which wraps builder refusals into `SealRefusedError`;
  - `_write_run(..., conditions)`, which puts the conditions into the base snapshot **before**
    `merged_into`, so `equity_series` lands beside them;
  - `SealReport.closed_trade_count`, which now reads the flat AR7 key;
  - updated docstrings and comments (module "Does not own", `period_start`,
    `PAPER_DATA_SOURCE`).

  No change to `session_equity.py`, `StrategyConfigSnapshot`, the persistence service, the
  repositories, models or migrations.
- **ACs:** each AC 1–9 has unit evidence (`5.4a`–`5.4i` markers); ACs 1–5 and 9 also against
  Postgres. Evidence beyond the unit tier:
  - AC #6: the real panel template, rendered through the UI's own `Jinja2Templates` and
    `build_configuration`, unmodified;
  - AC #8: a component view test.
- **5.3's exact-snapshot test widened, deliberately.**
  `test_the_snapshot_carries_the_series_beside_the_base_keys` pinned
  `session_conditions == {"equity_series": …}`. It now pins the exact new key set, never a
  subset, and `equity_series` unchanged. The CLI test's `_report` fixture now carries the real
  builder's document. A new CLI test proves every key prints in both forms with no AR36 stem,
  including a realistic `last_run_flags`.
- **Routed debt — every item dispositioned:**
  - **5.2 deferred (d) / 4.7 "a lot held across a split…" / PRD "Story 5.2 / 5.4" line →
    recorded structurally, detection deferred (R3a).** `price_basis = "ibkr_unadjusted_smart"`
    names the permanent divergence source on every record, beside `equity_series.excludes`'
    `reconciliation_owned_activity`. Per-session detection → deferred-work story-5.4 (a).
  - **5.2 code review "5.4's `closed_trade_count` will duplicate `equity_series.closed_trades`"
    → FIXED.** One computation, passed into the builder.
    - It is pinned equal in the seal-service unit test and in integration/db, where it also
      equals `performance_metrics.total_trades`.
    - Since code review P5, the seal **refuses** a result whose `total_trades` disagrees.
    - *(Corrected by P13: the CLI test does not pin the count; its fixture states it twice.)*
  - **4.7 "The split's extra shares are unmanaged once the strategy exits" / PRD "Story 5.4"
    line (`open_positions_at_seal` must account for them) → DONE by construction, proven.** The
    view nets every owner, so the shares are an open position at seal. R2a refuses, naming them
    (unit). The component test proves the view reports a synthetic owner's `+10` after the
    strategy is flat. `open_positions_at_seal` is built from the same view, so 5.5's `--force`
    records them.
  - **5.3 deferred (b) truncated session → R2a.** `last_run_flags`, last process run only;
    deferred-work story-5.4 (c).
  - **5.3 deferred (c) first-start source → R1a.** `created_at` kept and stated
    (`start_date_basis`), AC #4 amended. This is the disclosed deviation; deferred-work
    story-5.4 (b).
  - **5.3 deferred (f) `data_source` wording → kept `"ibkr_live"`** (PO-confirmed), pinned by a
    unit test.
- **Found while building, routed:** the detail page's "CLI Command to Replicate" appends
  `--session-conditions {…}` for a paper record. That is pre-existing behaviour of an AR44 file
  → Story 5.6 (deferred-work story-5.4 (d)).
- **No P-number:** the story is database-only (NFR33), and no `live seal` / `live start` was run
  against a real session.

### File List

- `src/services/session_conditions.py` (new)
- `src/services/session_seal_service.py` (modified)
- `tests/unit/services/test_session_conditions.py` (new)
- `tests/unit/api/test_config_panel_renders_session_conditions.py` (new)
- `tests/unit/services/test_session_seal_service.py` (modified)
- `tests/unit/cli/commands/test_live_seal_cli.py` (modified)
- `tests/component/core/test_live_session_view.py` (modified)
- `tests/integration/db/test_session_seal.py` (modified)
- `docs/agent/persistence.md` (modified)
- `docs/agent/architecture.md` (modified)
- `README.md` (modified)
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — this story's key only)
- `_bmad-output/implementation-artifacts/5-4-record-the-conditions-the-session-actually-ran-under.md` (new)

### Change Log

- 2026-10-11 — Story drafted (create-story). PO rulings R1a/R2a/R3a recorded. AC #4 amended
  (R1a, disclosed deviation).
- 2026-10-11 — Implemented (dev-story): AR7's `session_conditions` keys on every sealed record via
  `src/services/session_conditions.py`, plus the seal wiring, unit/component/integration-db
  tests, docs and deferred-work. Status → review.
- 2026-10-11 — Code review (three layers): 0 decision-needed, 13 patch, 2 defer, 8 dismissed.
  The PO chose batch-apply (0), and all 13 patches landed test-first.
  - **P2/P3:** the human render handles any stored JSON, one line per key, so a committed seal
    is never reported as failed.
  - **P4:** `last_run_flags` takes any stored shape.
  - **P5:** a count disagreement refuses.
  - **P6:** narrowed the refusal catch.
  - **P7:** a key overlap refuses.
  - **P8:** `runtime_flags` is a required field.
  - **P9:** the basis parameter is gone.
  - **P10:** a non-finite capital gets its own refusal.
  - **P11–P13:** docstring, test/marker and record corrections.

  W1 is deferred to Story 5.5 and W2 to whoever relaxes RTH or REALTIME. Mutation sweep 15/16
  killed (M10, the guard-list gap, disclosed). After the review:
  - unit **3744**;
  - component **1976 passed, 16 skipped**;
  - integration/db **150**;
  - forked integration **369 passed, 2 skipped**;
  - `ruff check --no-cache`, `ruff format --check` and `make typecheck` clean.

  Status → done.
