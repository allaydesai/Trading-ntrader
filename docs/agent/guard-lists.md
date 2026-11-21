# Guard lists and pinned constants

Read this before editing `src/core/live_*.py`, `src/cli/commands/live*.py`, a port, or any
constant that enumerates what code emits or dispatches. Moved verbatim from `CLAUDE.md`
(2026-11-21) so it loads on demand instead of every session; code comments that cite
"CLAUDE.md Anti-Patterns" or "CLAUDE.md's membership-pinned lists" refer to this file.


- Never instantiate `LiveLogger` or `Logger` directly — use LogGuard via `set_nautilus_log_guard()`
- Never reuse a `BacktestEngine` instance across runs — create fresh each time
- Never import from `src.core.strategies.custom.*` in core code — custom/ is a git submodule
- Never run integration tests without `--forked` — C extensions corrupt shared state
- Never hardcode IBKR/Kraken connection details — use env var settings classes
- Never split a `src/core/live_*.py` or `src/cli/commands/live*.py` module without re-checking the
  **hand-maintained guard lists** in the same commit — `NODE_FACING_MODULES` / `EXEMPT_MODULES`
  (`tests/unit/core/test_live_node_never_exits.py`), `STOP_PATH_MODULES`
  (`tests/unit/core/test_live_stop_path_is_inert.py`), `TestImportPurity.MODULES`
  (`tests/component/core/test_session_runner_phases.py`). Story 2.6's file-size split made
  `live_start.py` silently escape two of them, and nothing asserts the lists are complete, so an
  omission is invisible. `LIVE_MODULE_GLOBS` and `STRATEGY_MODULES` are globbed and need no action.
  Story 3.7 added `src/core/live_order_rejections.py` to `NODE_FACING_MODULES` and
  `TestImportPurity.MODULES` in the commit that created it, and deliberately **not** to
  `STOP_PATH_MODULES` (it calls no order method and is not on the stop path).
  A fifth list, `_STDLIB_AND_FIRST_PARTY` (`tests/integration/core/test_epic1_ac_node.py`), is
  triggered by a **new import** rather than by a split — adding any stdlib import to a live-path
  module fails that scan until the name is added by hand (added to this list 2026-08-30 by code
  review, after Story 3.3's `from decimal import Decimal` tripped it and the story's "zero
  guard-list edits" claim turned out to be false)
- **Membership-pinned lists** (added 2026-08-29): `FORBIDDEN_ORDER_METHODS` and `LIFECYCLE_HOOKS`
  (`tests/unit/core/test_live_stop_path_is_inert.py`) are asserted as exact sets, because every
  other consumer only *intersects* with them — dropping a name weakened a scan or deleted a
  parametrized probe's own case without anything going red. Change either deliberately, in the
  membership test and the constant together. `ORDER_CREATING_METHODS`
  (`src/core/live_order_path.py`, added 2026-08-30) joins this discipline: pinned as an exact set in
  `tests/component/core/test_live_order_path.py`, and separately asserted a *subset* of
  `FORBIDDEN_ORDER_METHODS` against a duplicated literal there. **A duplicated literal needs its own
  equality pin** (added 2026-08-30 by code review): the copy existed with nothing tying it to its
  source, so a *shrink* of the real set would leave the stale name in the copy and the subset
  assertion passing while meaning nothing. Production code genuinely cannot import from `tests/`, but
  a test file can — `tests/__init__.py` exists — so the copy is now asserted equal to the real
  constant by import. Keep the copy (it forces a deliberate, visible edit in both files) **and** the
  equality pin. `EMITTED_ORDER_EVENTS` (`src/core/live_order_path.py`, added 2026-08-30) joins the
  discipline too: every **order-lifecycle** record name `OrderEventObserver` emits (diagnostic and
  boundary records — `order.observer_failed`, `order.suppressed`, `order.suppression_failed` — are
  deliberately outside it), pinned as an exact set in
  `tests/component/core/test_live_order_path.py`, with the NFR26 anti-field scan parametrized from
  it — a dropped name would otherwise exempt that event type from the scan without anything going
  red. **An exact-set pin against hand-written constants is one-directional** (corrected 2026-08-30
  by code review, which found the original claim here false): comparing the tuple to a literal set
  built from the *same* constants catches a name *dropped from the tuple*, but never a record name
  *never added* to it, so a new emitter silently escapes the scan — the exact failure the pin was
  written to prevent. When a constant enumerates what some code *emits* or *dispatches*, pin it
  against **the code**, not against a second hand-written list:
  `TestEveryDispatchedRecordNameIsPinned` derives the emitted set by driving every entry in the
  observer's own `_dispatch` map and comparing the captured record names, so both directions go red
- **`EMITTED_TRADE_EVENTS`** (`src/core/live_trade_recorder.py`) joined the discipline at Story 3.5
  and was **changed deliberately at Story 3.6**: `PERSISTED_EVENT` (`trade.persisted`, AR41's fixed
  milestone name) was added alongside `AGGREGATED_EVENT`, following the same two-directional,
  `_dispatch`-driven pin `EMITTED_ORDER_EVENTS` uses (`tests/component/core/
  test_live_trade_recorder.py`) — `trade.recorder_failed`, `trade.commission_mixed_currency`,
  `trade.commission_unavailable`, `trade.persist_refused` and `trade.persist_skipped` stay
  deliberately outside it as diagnostic/boundary records, the `order.observer_failed` precedent.
- **`SessionRecordPort`'s exact-method pin** (`tests/unit/core/test_live_session_record.py`) went
  from **three methods to four at Story 3.7**: `record_order_rejections` joined
  `record_activity`/`mark_stopped`/`record_strategy_failure`. The port is `@runtime_checkable`, so
  the widening also breaks every `isinstance(double, SessionRecordPort)` until **all six**
  hand-written doubles in `tests/` grow the method — `grep -rn "def record_strategy_failure"
  tests/` is the list, and 3.6 found five `SpyRecord`s where its own text estimated two. The
  widening was argued, not inherited: Story 3.6's D-F refused the same widening for the trade sink
  because *a trade is not a session-row fact*; a refusal summary is one, lives in the same
  `runtime_flags` document, and is written on the same tick by the same executor.
  **Widened 4 → 5 at Story 5.8** (PO-sanctioned 2026-10-11): `record_trading_withheld`, on the
  same terms. The doubles now number six component `SpyRecord`s plus the unit `_HandWrittenRecord`;
  the grep is still the list. Its key, `runtime_flags["reconcile_withheld"]`, is the **one
  `runtime_flags` key the `-> running` edge carries forward** (PO ruling Q1). Every other key
  still clears per run. The shape lives in `src/services/reconcile_withheld.py`.
- **`EXPECTED_CAPABILITIES`** (`tests/unit/db/test_trading_session_repository_shape.py`) — an
  exact-set allowlist for both `TradingSessionRepository` twins, **changed deliberately at Story
  3.6**: `stamp_activity_if_owner` and `insert_trade_if_absent` joined it (AR9, both twins in the
  same story). Neither name starts with a forbidden mutator prefix
  (`update/set_/save/patch/merge/upsert/replace`), and neither writes `spec` — AC #7 of Story 2.2
  is about there being no write path to `spec`, not about there being no write path at all.
  **Changed deliberately again at Story 5.3**: `find_session_trades` (the seal reads a session's
  trade rows in DB id order) and `backfill_trades_run_id` (the seal links them to the run it
  creates, AR8 — only rows not yet linked, and the seal checks the count) joined it, both twins,
  same rules.
- **`EMITTED_RECONCILE_EVENTS`** (`src/core/live_startup_reconcile.py`) joined the discipline at
  Story 4.7: `reconcile.ok`, `reconcile.discrepancy`, `reconcile.cash_changed` — the startup
  phase's records, with `reconcile.local_snapshot_failed` deliberately outside it as a
  diagnostic. **Widened 3 → 4 at Story 5.7**: `reconcile.inexpressible_remainder` (a broker
  quantity finer than the instrument's size precision, named once; its `size_step` field is
  `10**-size_precision`, never the possibly coarser `size_increment` — code review R2) joined
  it, in the constant, its exact-set pin and the NFR26 parametrized scenario together. There
  is no dispatch map to drive, so
  `TestEveryEmittedReconcileRecordIsPinned` (`tests/unit/core/test_live_startup_reconcile.py`)
  pins it **against the code both ways**:
  - an AST scan of every `_emit` in the module, which catches a name never added;
  - the phase driven through every path, which catches a name listed but never emitted.

  The NFR26 scan is parametrized from the constant. **`LOAD_METHODS`**
  (`src/core/live_session_view.py`) went from three reads to four at Story 4.7 (`load_account`,
  PO ruling). It was widened in the constant and in its exact pin together. The Redis `MONITOR`
  proof now asserts the account read is inside it. **Widened 4 → 5 at Story 5.5**: `load_order`
  (PO ruling R3b — the seal reports the session's working orders), in the constant, its exact pin
  and a second `MONITOR` proof (`read_session_holdings`, asserting an `:orders:` read) together.
  Only the seal's `read_session_holdings` calls it; `read_session_view` (`live reconcile`) loads
  no order.
- **`EMITTED_BAR_FEED_EVENTS`** (`src/core/live_bar_resubscribe.py`) joined the discipline at
  Story 5.9:
  - `bars.resubscribed` and `bars.resubscribe_failed`, from the shim that makes the IB adapter's
    bar resubscribe work after a reconnect;
  - `bars.catch_up` and `bars.catch_up_order_withheld`, from `live_catch_up_gate.py`, which
    withholds any order raised on a bar handled more than 120 s past its close.

  It is pinned against the code both ways in `tests/component/core/test_live_bar_resubscribe.py`
  (an AST scan of every log call's record name in both modules, any namespace, plus every
  path driven), and the NFR26 scan is parametrized from it. The gate's `CATCH_UP_GRACE_SECONDS`
  duplicates `DEFAULT_DELAYED_DATA_GRACE_SECONDS`, so the order path stays framework-free; an
  equality pin ties the two.
- **`FROZEN_VIEWS`** (`tests/unit/governance/test_ar44_frozen_views.py`, Story 5.6): Phase 3's
  zero-UI-change criterion (AR44), pinned byte for byte. It holds a SHA-256 for every file under
  `src/api/**` and `templates/**`, plus `src/services/backtest_query.py` and
  `src/cli/commands/compare.py` (the comparison view). It covers the AC's four surfaces only (PO
  ruling R2b): `static/js` and the CLI's `show`/`history` are deliberately outside it. Any changed,
  added or removed file goes red: that is a success-criterion violation, not a judgment call.
  **Never regenerate the manifest to make it pass.** Only a recorded PO decision may regenerate
  it (`python -m tests.unit.governance.test_ar44_frozen_views --print-manifest`), and the first
  story allowed to change a view (Growth's "Paper" badge) retires it deliberately.

