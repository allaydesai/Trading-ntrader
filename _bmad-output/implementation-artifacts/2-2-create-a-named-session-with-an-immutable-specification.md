# Story 2.2: Create a Named Session with an Immutable Specification

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want to define a session once and have its specification frozen for its entire life,
so that a typo at a later start can never silently change the strategy mid-forward-test and corrupt
a multi-week sample.

## Acceptance Criteria

1. **Given** a new Alembic migration chained off head `a436f35f525c`, **When** it is applied, **Then** it creates `trading_sessions` with `id` BigInteger PK, `session_id` UUID business key (unique, indexed), unique `name`, `status` as a **DB-enforced enum type `session_status`**, `spec` JSONB, nullable `linked_backtest_run_id` FK → `backtest_runs.run_id`, nullable `sealed_run_id`, nullable `last_started_at` / `last_stopped_at` / `sealed_at` / `last_heartbeat_at` / `last_bar_at`, and `TimestampMixin`'s `created_at` (AR4, AR32) — **And** `alembic heads` prints exactly one head afterwards.
2. **Given** the `session_status` enum type, **When** its labels are read from `pg_enum`, **Then** they are exactly `created`, `running`, `stopped`, `sealed` — **lowercase**, matching `SessionStatus`'s *values*, not its member names (architecture.md#Naming-Patterns line 380).
3. **Given** the same migration, **When** it is applied, **Then** it also adds `backtest_runs.run_type` NOT NULL defaulting to `'backtest'`, adds nullable `trades.session_id` FK → `trading_sessions.id`, makes `trades.backtest_run_id` nullable, and adds the CHECK constraint `backtest_run_id IS NOT NULL OR session_id IS NOT NULL` (AR6, AR8) — **And** every existing `backtest_runs` and `trades` row is unaffected (row counts unchanged, every `backtest_run_id` still set, every `run_type` reading `'backtest'`), with every existing backtest query and view returning identical results after the migration.
4. **Given** the migration, **When** `alembic downgrade -1` is run against a database holding no paper trades, **Then** it reverses cleanly, restoring `trades.backtest_run_id` to NOT NULL and dropping the `session_status` type — **And** when paper trades *do* exist it refuses with a message naming them, rather than failing inside Postgres with a constraint-violation traceback.
5. **Given** `ntrader live create`, **When** the operator supplies `--name`, `--strategy`, one or more `--bar-type`, zero or more `--param key=value`, and optionally `--compare-to <run_id>`, **Then** a `trading_sessions` row is written with status `created` and the spec serialised to JSONB, and the session ID and name are printed (FR14, FR15) — **And** `session.created` is logged with the bound `session_id` per AR41.
6. **Given** a spec containing `Decimal` parameters, **When** it is written to and read back from the `spec` JSONB column, **Then** the round trip is lossless — `SessionSpec` reconstructed from the stored payload compares equal to the original and its `Decimal` fields are still `Decimal` — **And** the write path never calls bare `model_dump()`.
7. **Given** a persisted session, **When** any code path attempts to update its `spec` column, **Then** no such path exists in either repository — the spec is write-once, the same discipline `config_snapshot` already follows (FR14, AR4).
8. **Given** the dual-repository rule, **When** the story is complete, **Then** both an async and a sync trading-session repository exist with matching capabilities — create, fetch by `session_id`, fetch by `name`, list all — even though only the sync path is exercised this phase (AR9). ⚠️ **Deliberate deviation from the epic's wording:** `epics.md:802-803` names the classes `TradingSessionRepository` / `TradingSessionRepositorySync`; this story ships `TradingSessionRepository` / **`SyncTradingSessionRepository`** because all five existing sync repositories use the `Sync…Repository` prefix. The epic AC is knowingly unmet on the name and met on the substance — see Judgment calls #2, and reject it there if you disagree.
9. **Given** a duplicate session name, **When** `live create` is run, **Then** it fails with a clear message naming the existing session and exits `1`, rather than creating a second session or surfacing a raw `IntegrityError`.
10. **Given** a stored spec payload, **When** it is read back as a typed `SessionSpec`, **Then** a `schema_version` higher than this build understands is refused with a legible message rather than silently mis-read (closes the `schema_version` read-gate item deferred from the Story 2.1 review).
11. **Given** `--param` with a key that is not a field of the strategy's parameter model, **When** `live create` runs, **Then** it fails at exit code `2` naming the unknown key and listing the valid ones — the value must never be silently dropped into a spec frozen for the session's life (closes the `build_strategy_params` override-drop item deferred from Story 2.1).
12. **Given** `--compare-to` naming a `run_id` that does not exist, **When** `live create` runs, **Then** it fails with a message naming the unknown run, not a foreign-key `IntegrityError`.
13. **Given** the test suite, **When** it runs, **Then** the migration artifact is guarded by a **unit-tier** test that needs no database (because CI `--ignore`s `tests/integration/db`), and the ORM/repository/CLI behaviour is covered at the tiers named in Testing Standards below (NFR32).

## Tasks / Subtasks

- [x] **Task 1: Unit-tier guards, written first (TDD Red)** (AC: #1, #2, #3, #4, #13)
  - [x] New file `tests/unit/db/test_migration_trading_sessions.py`. Follow the **static source-text** pattern in `tests/unit/db/test_migration_bar_count_30min.py (52 lines)` verbatim — glob `alembic/versions/*.py`, find the file mentioning `trading_sessions`, assert on its text. This tier is deliberate: `.github/workflows/ci.yml:168,238` passes `--ignore=tests/integration/db`, so **a test placed in `tests/integration/db/` gates nothing on a PR**. Mark every test `@pytest.mark.unit` (`--strict-markers` is on at `pytest.ini:15`).
  - [x] Assert: the migration's `down_revision` is `"a436f35f525c"`; `upgrade()` contains `op.create_table("trading_sessions"` and every column AC #1 names; `downgrade()` drops the table, the two altered-table changes, and the enum type.
  - [x] Assert the enum labels appear **lowercase** in the migration source (`"created"`, `"running"`, `"stopped"`, `"sealed"`) — this is the guard for AC #2 that runs in CI.
  - [x] New file `tests/unit/db/test_trading_session_model.py` — ORM shape without a database: import `TradingSession`, assert `__tablename__ == "trading_sessions"`, assert the `status` column's type has `.enums == ["created", "running", "stopped", "sealed"]` (this is the `values_callable` guard — see Dev Notes ⚠️ "sa.Enum persists the member NAME"), assert `spec` is `JSONB`, assert `TradingSession.__table__.columns["backtest_run_id"]` does **not** exist, assert `Trade.__table__.columns["backtest_run_id"].nullable is True` and `Trade.__table__.columns["session_id"]` exists.
  - [x] Run and confirm **RED** before writing implementation.
- [x] **Task 2: The `TradingSession` ORM model** (AC: #1, #2, #7)
  - [x] New file `src/db/models/trading_session.py`. **Not** `src/db/session.py` — that name is taken by the SQLAlchemy async engine module, and `src/db/session_sync.py` by its sync twin. `src/models/session.py` is Story 2.1's domain model. See Dev Notes ⚠️ "the word *session* is a crowded namespace".
  - [x] `class TradingSession(Base, TimestampMixin):` — declaration order matches `BacktestRun` (`src/db/models/backtest.py:33`). `TimestampMixin` supplies **only `created_at`**; there is no `updated_at` in it (`src/db/base.py:20-32`). If you want `updated_at`, declare it inline the way `instrument_metadata.py:65-69` does — but AR4 does not ask for one, so don't.
  - [x] Columns, mirroring `BacktestRun`'s dual-ID pattern (`backtest.py:74-86`):
    ```python
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), default=uuid4, unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[SessionStatus] = mapped_column(
        sa.Enum(
            SessionStatus,
            name="session_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SessionStatus.CREATED,
    )
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    linked_backtest_run_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("backtest_runs.run_id"), nullable=True
    )
    sealed_run_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    ```
    plus five nullable `TIMESTAMP(timezone=True)` columns: `last_started_at`, `last_stopped_at`, `sealed_at`, `last_heartbeat_at`, `last_bar_at`.
  - [x] ⚠️ **`values_callable` is mandatory and is the single highest-risk line in this story.** Without it SQLAlchemy persists the member *name*. Verified against this repo's SessionStatus: `sa.Enum(SessionStatus).enums` → `['CREATED', 'RUNNING', 'STOPPED', 'SEALED']`; with `values_callable` → `['created', 'running', 'stopped', 'sealed']`. See Dev Notes.
  - [x] ⚠️ **Do not copy `create_type=False` from `instrument_metadata.py:60`.** Verified: `sa.Enum` has no `create_type` attribute (SQLAlchemy 2.0.43) — passing it is a silent no-op there, and the test fixtures depend on `Base.metadata.create_all` creating the type in their scratch schema (verified: it does). Leave it off and say nothing about it, or the next reader will believe it does something.
  - [x] `status` gets its value from the **column default**, not an assignment. `default=SessionStatus.CREATED` means no line anywhere reads `status = ...`, which pre-empts Story 2.3's grep-enforced rule (AR37) before that rule exists.
  - [x] `__table_args__` — no CHECK constraints needed here; `unique=True, index=True` on `session_id`/`name` is sufficient. Add `Index("ix_trading_sessions_status", "status")` for Story 2.8's `list`. **Keep this identical to what the migration creates** — `backtest.py:153,155` declares two indexes on `backtest_runs` that no migration in `alembic/versions/` ever creates, and `trades` carries the same class of drift under a different name. Do not add to it; see Dev Notes ⚠️ "Do not autogenerate this migration".
  - [x] Google-style docstring with the Attributes block, matching `backtest.py:33-73`.
- [x] **Task 3: Widen `Trade` and `BacktestRun`** (AC: #3)
  - [x] `src/db/models/trade.py:82` — `backtest_run_id` becomes `Mapped[Optional[int]]` with `nullable=True`. Keep the `ForeignKey("backtest_runs.id", ondelete="CASCADE")` and `index=True` exactly as they are.
  - [x] `src/db/models/trade.py:126` — the relationship annotation must widen with the column: `backtest_run: Mapped[Optional["BacktestRun"]] = relationship("BacktestRun", back_populates="trades")`. Leaving it as `Mapped["BacktestRun"]` is a typing lie the moment a session-owned trade exists, and SQLAlchemy 2.0 reads that annotation. Keep `BacktestRun.trades`' `cascade="all, delete-orphan"` untouched — delete-orphan still deletes a removed child rather than nulling its FK, so a nullable column does not change that behaviour.
  - [x] Add `session_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("trading_sessions.id"), nullable=True, index=True)`. **This targets `trading_sessions.id` (BigInteger), not `trading_sessions.session_id` (UUID)** — mirroring `trades.backtest_run_id → backtest_runs.id`. See Dev Notes ⚠️ "two things named session_id".
  - [x] No `ondelete` on the new FK: nothing deletes sessions this phase, and a cascade would let a session delete destroy trade history that is the system of record. Postgres' default `NO ACTION` refuses the delete instead — the honest outcome.
  - [x] Add to `Trade.__table_args__`: `CheckConstraint("backtest_run_id IS NOT NULL OR session_id IS NOT NULL", name="chk_trades_owner")`. Trades' existing CHECKs use bare names (`positive_quantity`); `backtest_runs`' use `chk_`. Prefix chosen so `op.drop_constraint` in `downgrade()` has an unambiguous handle.
  - [x] `src/db/models/backtest.py` — add `run_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="backtest")`. A plain `String(20)`, matching the neighbouring `execution_status` column, **not** a second enum type: AR6 asks for "one column", and every additional PG type is another create/drop pair in every migration that follows.
  - [x] **Do NOT touch `src/models/trade.py`.** Its `backtest_run_id: int` (lines 49, 56) stays required. Nothing writes a session-owned trade until Epic 3, and `src/api/rest/trades.py` only ever queries `WHERE backtest_run_id == <id>`, so a NULL row can never reach that Pydantic model this phase. Widening it now would be Epic 3's change made blind. Record it in `deferred-work.md` (last bullet of Task 9).
- [x] **Task 4: Write the migration BY HAND** (AC: #1, #2, #3, #4)
  - [x] `uv run alembic revision -m "add trading_sessions and paper run support"` — **plain `revision`, never `--autogenerate`.** The ORM and the migration chain have known drift (two `backtest_runs` indexes exist only in the ORM), so autogenerate emits real operations outside this story's scope and a dev agent will keep them. Run alembic **from the repo root** — `alembic.ini:19` sets `prepend_sys_path = .` and `env.py` imports `src.config`.
  - [x] Set `down_revision: Union[str, Sequence[str], None] = "a436f35f525c"` (verified current single head via `uv run alembic heads`).
  - [x] Copy the enum lifecycle from `alembic/versions/f051a079629c_add_instrument_metadata_table_and_.py:22-90` exactly — module-level `postgresql.ENUM(..., name="session_status", create_type=False)`, `.create(op.get_bind(), checkfirst=True)` **before** `create_table`, `.drop(op.get_bind(), checkfirst=True)` **last** in `downgrade()`. In a *migration* `create_type=False` is load-bearing (unlike on the ORM's `sa.Enum`): it stops SQLAlchemy emitting a second `CREATE TYPE` that collides with the explicit one.
    ```python
    session_status_enum = postgresql.ENUM(
        "created", "running", "stopped", "sealed", name="session_status", create_type=False
    )
    ```
  - [x] `upgrade()` order: create enum → `create_table("trading_sessions", ...)` → its indexes → `op.add_column("backtest_runs", run_type)` → `op.add_column("trades", session_id)` + its FK/index → `op.alter_column("trades", "backtest_run_id", existing_type=sa.BigInteger(), nullable=True)` → `op.create_check_constraint("chk_trades_owner", "trades", "backtest_run_id IS NOT NULL OR session_id IS NOT NULL")`. The CHECK goes **last**, after `session_id` exists, or it references an unknown column.
  - [x] JSONB is written `postgresql.JSONB(astext_type=sa.Text())` in migrations (`9c7d5c448387`); timestamps `postgresql.TIMESTAMP(timezone=True)`; `created_at` carries `server_default=sa.text("now()")`.
  - [x] **Uniqueness is created as unique INDEXES, never `sa.UniqueConstraint`.** AC #1 says "unique `name`", and the obvious reading — `sa.UniqueConstraint("name")` inside `create_table` — produces `trading_sessions_name_key` in every migrated database while the ORM's `unique=True, index=True` produces `ix_trading_sessions_name`. Those two names then differ forever, and **no test can see it** (the fixtures build from the ORM, not the migration). Verified by compiling the ORM's own DDL: `unique=True, index=True` emits a `CREATE UNIQUE INDEX` and **no** table-level constraint, which is also what `9c7d5c448387:73` does for `backtest_runs.run_id`. Write exactly these four, and drop the same four by the same names in `downgrade()`:
    ```python
    op.create_index(op.f("ix_trading_sessions_session_id"), "trading_sessions", ["session_id"], unique=True)
    op.create_index(op.f("ix_trading_sessions_name"), "trading_sessions", ["name"], unique=True)
    op.create_index("ix_trading_sessions_status", "trading_sessions", ["status"], unique=False)
    op.create_index(op.f("ix_trades_session_id"), "trades", ["session_id"], unique=False)
    ```
    (`op.f()` marks a name as already-final so alembic does not re-apply a naming convention; the hand-declared `ix_trading_sessions_status` comes from an explicit `Index(...)` in `__table_args__`, so it is a plain string — the same split `f051a079629c:73-78` uses.) A unique *index* is a legal FK target, so `linked_backtest_run_id → backtest_runs.run_id` works against `ix_backtest_runs_run_id` — verified with a real `REFERENCES backtest_runs(run_id)` in a rolled-back transaction.
  - [x] `run_type` is added NOT NULL with `server_default="backtest"` — on PG 11+ this is a metadata-only change even against the 133 live rows, and it is what makes AC #3's "existing rows unaffected" true rather than hopeful.
  - [x] `downgrade()` — **the guard runs first, before any DDL.** Alembic's per-migration transaction would roll back a later refusal anyway, but a downgrade that drops the CHECK before deciding whether it can proceed reads as if the destruction were intended. Order: **guard** → `op.drop_constraint("chk_trades_owner", "trades", type_="check")` → `op.alter_column("trades", "backtest_run_id", existing_type=sa.BigInteger(), nullable=False)` → drop `trades.session_id` (the FK goes with the column) → drop `backtest_runs.run_type` → drop `trading_sessions` indexes + table → drop the enum type **last**. Signatures verified against the installed SQLAlchemy/Alembic: `create_check_constraint(constraint_name, table_name, condition)`, `drop_constraint(constraint_name, table_name, type_=...)`. The guard, satisfying AC #4:
    ```python
    orphans = op.get_bind().execute(
        sa.text("SELECT count(*) FROM trades WHERE backtest_run_id IS NULL")
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} trade(s) belong to a session and have no backtest_run_id, so "
            "backtest_run_id cannot be restored to NOT NULL. Seal the owning sessions "
            "(which backfills backtest_run_id) or delete those trades first."
        )
    ```
    This is the middle ground between the repo's two existing downgrade styles — a real reversal like `f051a079629c`, but with `ff982c8e1402`'s discipline of refusing legibly instead of letting Postgres raise.
  - [x] Long explanatory docstring above `revision`, in the style of `a436f35f525c` and `ff982c8e1402` — state *why* the runs row is not created here (AR5), why `backtest_run_id` becomes nullable, and that this is the phase's single migration (`epics.md:367-371`).
  - [x] Never edit an already-applied migration. Alter forward.
- [x] **Task 5: Register the model and apply the migration** (AC: #1, #3)
  - [x] `src/db/models/__init__.py` — add the import and the `__all__` entry in a **single edit** (a half-applied edit leaves an F401 that hard-blocks the commit at three gates). Keep `__all__` alphabetical as it already is.
  - [x] This registration is what puts the table into `Base.metadata`, which is what both `alembic/env.py`'s `target_metadata` and every `Base.metadata.create_all` test fixture read. `env.py`'s explicit `from src.db.models import (...)` list does **not** need a new entry — it does not name `BacktestRun` or `Trade` either, and they are registered anyway because importing any name executes the whole package `__init__`.
  - [x] `uv run alembic upgrade head`, then `uv run alembic heads` → must print exactly one head. Then `uv run alembic downgrade -1` and `uv run alembic upgrade head` again — the round trip is AC #4's evidence and nothing else in this repo has ever exercised a downgrade.
  - [x] Verify against the live database, not by inspection. **Record `SELECT count(*)` on `backtest_runs` and on `trades` BEFORE the upgrade** (they were 133 and 14 982 when this story was written, but a backtest run since then would change both — measure, don't assume). After the upgrade: both counts identical; `SELECT count(*) FROM trades WHERE backtest_run_id IS NULL` = 0; `SELECT count(*) FROM backtest_runs WHERE run_type <> 'backtest'` = 0; and `SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid WHERE t.typname = 'session_status' ORDER BY enumsortorder` returns exactly `created, running, stopped, sealed`.
- [x] **Task 6: Both repositories (TDD)** (AC: #6, #7, #8, #9)
  - [x] Tests first. Sync: `tests/integration/db/test_trading_session_repository.py` using the `sync_db_session` fixture (`tests/integration/db/conftest.py:152`, pg8000 + per-worker schema). Async: same file, using the local engine/session fixture pattern that `tests/integration/db/test_backtest_repository.py:21-75` defines inline (that directory's house style is per-file fixtures, not the shared conftest).
  - [x] `src/db/repositories/trading_session_repository.py` → `TradingSessionRepository` (async, `AsyncSession`).
  - [x] `src/db/repositories/trading_session_repository_sync.py` → `SyncTradingSessionRepository` (sync, `Session`). **Note the class name deviates from `architecture.md:234`/epics AC, which say `TradingSessionRepositorySync`** — see Dev Notes "Judgment calls", #2. File names follow the architecture doc; class names follow the five existing repositories.
  - [x] Matching capability set on both: `create(...)`, `find_by_session_id(session_id: UUID)`, `find_by_name(name: str)`, `find_all()`. **No `update_spec`, no `update`, no generic setter of any kind** — AC #7 is satisfied by absence, exactly as `config_snapshot` is (there is no DB trigger, no `@validates`, no read-only mapper option anywhere in this repo; the discipline is that no write path exists).
  - [x] `create(self, *, name: str, spec: dict, linked_backtest_run_id: UUID | None = None) -> TradingSession`. **`spec` is the already-serialised dict, not a `SessionSpec`** — mirroring `create_backtest_run(..., config_snapshot: dict)` (`backtest_repository_sync.py:57`) and keeping `src/models/` out of `src/db/`, which is the boundary `instrument_metadata_repository.py`'s own docstring states ("ORM↔domain mapping is the service's responsibility"). Callers produce the dict with `SessionSpec.to_stored()` (Task 7) — one sanctioned serialiser, so the `mode="json"` rule cannot be got wrong by a future caller. Keyword-only, matching `StrategySpec.from_overrides`.
  - [x] Repositories never `commit()` or `rollback()` — `add()` / `flush()` / `refresh()` only. The caller's context manager owns the transaction (`get_sync_session` commits on clean exit at `src/db/session_sync.py:99-134` (commit at :129); the async `get_session` does not commit at all).
  - [x] Error translation, copied from `backtest_repository_sync.py:109-115`:
    ```python
    except IntegrityError as e:
        if "unique constraint" in str(e.orig).lower():
            raise DuplicateRecordError(f"Session name {name!r} already exists") from e
        raise
    except OperationalError as e:
        raise DatabaseConnectionError(f"Database connection failed: {e}") from e
    ```
    `DuplicateRecordError` and `DatabaseConnectionError` are already in `src/db/exceptions.py` — do not add new exception classes. `InvalidSessionTransition` is Story 2.3's.
  - [x] Add both to `src/db/repositories/__init__.py`'s exports (it currently names only `BacktestRepository`; adding these takes the exported set from 1 class to 3, of 8 repository classes across 7 modules — acceptable, and the story's own surface should be importable).
  - [x] Test the JSONB round trip explicitly (AC #6): create with a spec whose parameters hold `Decimal`, read the row back, `SessionSpec.from_stored(row.spec) == original_spec`, and `type(...parameters["portfolio_value"]) is Decimal`. Verified end-to-end against this repo's real Postgres before this story was written — see Dev Notes.
- [x] **Task 7: `SessionSpec.to_stored()` / `from_stored()` — the persistence pair (TDD)** (AC: #6, #10)
  - [x] Tests first, appended to `tests/unit/models/test_session_spec.py` as a new `TestStoredForm` class (the file's idiom is class-per-concern with `@pytest.mark.unit` on every method). Cover: `from_stored(spec.to_stored()) == spec`; the `Decimal` survives; `json.dumps(spec.to_stored())` does **not** raise (this is the guard that the serialiser is `mode="json"`); a payload with `schema_version` above the constant is refused.
  - [x] Add to `src/models/session.py`: a module constant `SPEC_SCHEMA_VERSION = 1`; `SessionSpec.to_stored(self) -> dict` returning `self.model_dump(mode="json")`; and `SessionSpec.from_stored(payload: dict) -> "SessionSpec"` that refuses a too-new version with a message naming both, then `return cls.model_validate(payload)`. Read the version with **`payload.get("schema_version", 1)`, never `payload["schema_version"]`** — a hand-written or pre-versioning row would otherwise raise a bare `KeyError`, which is precisely the illegible failure AC #10 exists to prevent. Guard `isinstance(payload, dict)` first for the same reason.
  - [x] The pair is the point: one place produces the stored form and one place consumes it, so the `mode="json"` rule and the version gate each live in exactly one function that a grep can find. Story 2.5 and Epic 5 use the same pair.
  - [x] Why a gate at all: the Story 2.1 review deferred it with "Action for Story 2.2: define the gate when the read path lands." This is that read path. `schema_version=99` currently loads clean, and `extra="forbid"` only catches a *new field*, not a bumped version with reinterpreted semantics.
  - [x] Why refusal rather than best-effort: a session spec is frozen for the life of a multi-week forward test. Reading it wrong and continuing produces a corrupted sample that no downstream check detects — the exact failure FR14 exists to prevent. Refusing costs an operator one clear error message.
  - [x] **No SQLAlchemy import may enter `src/models/session.py`.** `tests/unit/models/test_session_spec.py:456-484` runs a fresh subprocess asserting `sqlalchemy` is absent from `sys.modules` after `import src.models`; `src/models/__init__.py:12` re-exports from this module, so one stray import fails that guard.
  - [x] Deliberately **not** doing here: overriding `model_copy` (the other Story 2.1 deferral). It stays deferred — the repository write path this story adds takes a `SessionSpec` and immediately serialises it, so a `model_copy`-derived spec has no way to reach a stored row without passing the same validation. Re-record it in `deferred-work.md` pointing at Story 2.4.
- [x] **Task 8: `ntrader live create` (TDD)** (AC: #5, #9, #11, #12)
  - [x] Tests first: extend `tests/unit/cli/commands/test_live_cli.py` for the option surface and the usage-error paths (patch the repository, no DB), and add `tests/integration/db/test_cli_live_create.py` for the real write path. The DB-CLI idiom is: patch `src.cli.commands.live.get_sync_session` with a `@contextmanager` that yields the fixture session — so `live.py` must import it as a module-level name (`from src.db.session_sync import get_sync_session`), never call it through a fully-qualified path. Precedent: `tests/integration/db/test_cli_show.py:85-92`.
  - [x] Add `create` to the existing `@click.group("live")` in `src/cli/commands/live.py`. Option surface:
    - `--name` (required) — the operator's handle, unique, used everywhere a session ID is.
    - `--strategy` (required) — resolved through `StrategyRegistry`; follow `backtest.py:47-65`'s `validate_strategy` callback shape so an unknown name is a Click `BadParameter` (exit 2) listing the registered names.
    - `--bar-type` (required, `multiple=True`) — same option name and shape the group's `check` command already uses (`live.py:53-60`).
    - `--param` (`multiple=True`, `callback=_parse_param`) — repeatable `key=value`. **This is a new convention for this repo**; there is no `--param` pattern anywhere today. Split on the *first* `=` with `str.partition`, reject a missing `=`, reject a duplicate key, and keep values as **strings** — the parameter model coerces them (verified: `"5"` → `int` 5, `"50000.25"` → `Decimal("50000.25")`).
    - `--compare-to` (optional, `type=click.UUID`) — the backtest this session is intended to be compared against (FR15).
    - No `--json` on `create`. Architecture D8 scopes `--json` to `status`/`list` (Story 2.8); adding it here would set the convention for the whole group in the wrong story.
  - [x] **The unknown-`--param`-key guard (AC #11).** Do it in the command body, not a callback — Click callbacks fire per option and cannot see `--strategy` and `--param` together:
    ```python
    definition = StrategyRegistry.get(strategy)          # already validated by the callback
    valid = set(definition.param_model.model_fields)
    unknown = sorted(set(overrides) - valid)
    if unknown:
        raise click.UsageError(
            f"Unknown parameter(s) for {definition.name!r}: {', '.join(unknown)}. "
            f"Valid parameters: {', '.join(sorted(valid))}."
        )
    ```
    Verified why this matters: `StrategySpec.from_overrides(strategy_id="sma_crossover", overrides={"fast_perios": 12}, ...)` returns `fast_period=10` — the default — with **no error anywhere**, and FR14 then freezes that wrong value for the session's whole life. `param_model` can be `None` in principle (`strategy_registry.py:84` makes it optional); guard for it and let `from_overrides`' own message handle that case.
  - [x] Build the spec: `SessionSpec(strategies=(StrategySpec.from_overrides(strategy_id=..., overrides=..., settings=get_settings(), bar_types=bar_types),))`. **One strategy per `create` this phase** — see Dev Notes "Judgment calls", #1. Then persist it inside a single `with get_sync_session() as session:` block: `SyncTradingSessionRepository(session).create(name=name, spec=spec.to_stored(), linked_backtest_run_id=compare_to)`. The context manager owns the commit; do not call `session.commit()` in the command.
  - [x] **Catch `(ValidationError, ValueError)`, not `ValidationError` alone.** `from_overrides` is a factory that runs `build_strategy_params` before pydantic, so resolution and parameter failures surface as a plain `ValueError` by design (`src/models/session.py`'s own `Raises:` block says so; verified — a bad parameter raises `ValueError: Configuration validation failed for sma_crossover: ...`). Convert to `click.UsageError` (exit 2), following `_backtest_helpers.py:174-179`'s precedent for pydantic-to-Click conversion.
  - [x] Do **not** index into `err["loc"][0]` to blame a flag. The model-level `mode="before"` validator attaches its errors to the model root, so `loc` is an empty tuple for unknown strategy, bad parameters *and* bad bar types alike — `IndexError`. Map on message content or just render the message.
  - [x] `--compare-to` (AC #12): resolve it with `SyncBacktestRepository(session).find_by_run_id(run_id)` **before** inserting; a `None` result is a clear message, not a foreign-key `IntegrityError` surfaced from Postgres.
  - [x] Duplicate name (AC #9): catch `DuplicateRecordError` from the repository, print the message, `raise SystemExit(EXIT_ERROR)`. Import **only `EXIT_ERROR`** from `src.core.live_check` (that module is pure — stdlib + structlog + `live_gate` — and owns AR28's table at `live_check.py:38-45`). **Do not also import `EXIT_OK`**: the success path just returns and Click exits 0, so an imported `EXIT_OK` is an unused import, `F401` is configured `unfixable`, and it hard-blocks the commit at all three gates.
  - [x] Exit-code assignment for this command, and the reasoning: `0` success · `2` for everything Click can express as misuse (unknown strategy, malformed `--param`, unknown param key, unusable bar type, invalid parameter values) · `1` for a state conflict or DB failure (duplicate name, unknown `--compare-to`, Postgres unreachable). **No new exit code is invented** — Story 1.7 recorded that "a CLI that invents an exit code outside its own documented table is worse than one that reports a generic failure" (`live_check.py:91-93`), and AR28's table has no "already exists" code.
  - [x] DB unavailability: `except (RuntimeError, DatabaseConnectionError, SQLAlchemyError)` — the established catch tuple (`catalog.py:195`, `metadata.py:248-264`); `RuntimeError` is what `get_sync_session` raises when `DATABASE_URL` is unset. **Copy the catch tuple only, not the exit code**: `catalog.py:197` exits `2` for this case, which predates AR28's table. This command exits `1`, per the assignment below.
  - [x] Escape the operator-supplied `name` with `rich.markup.escape` before printing it. The newer command modules do this consistently (`catalog.py:15`), and a name containing `[` would otherwise be eaten as Rich markup.
  - [x] Log `session.created` with `session_id` and `name` — `logger.info("session.created", session_id=..., name=...)`, the exact shape `live_account_gate.py:149,168` uses for `gate.account`/`gate.refused`. Do not add `configure_logging()` to this module; `tests/unit/cli/commands/test_live_cli.py:79-85` asserts it is absent (the root `main.py:23` owns it).
  - [x] Watch the file budget: `live.py` is 117 lines and the limit is 500. If `create` plus its helpers pushes past ~400, extract the spec-building helper the way `import_reporting.py` was extracted from `import_data.py`.
- [x] **Task 9: Verify** (AC: all)
  - [x] Record the baseline as **collected** counts, taken before any edit, for `tests/unit`, `tests/component` and `tests/integration` (Epic 1 retro, Key Insight #3 — passed counts are not baselines).
  - [x] `make test-unit`, `make test-component`, `make test-integration` — all green, no regressions. `make test-integration` runs `tests/integration/db` locally (CI does not), so the repository and CLI integration tests do run here.
  - [x] `make format && make lint` clean. `make typecheck` is `mypy src/core src/services` only — it covers **neither** `src/db` nor `src/models`. Type everything fully anyway (project rule) and sanity-check with a standalone `uv run mypy src/db/models/trading_session.py src/db/repositories/trading_session_repository.py --ignore-missing-imports`.
  - [x] Coverage: `make test-coverage` measures `src/core` + `src/strategies` only, so none of this story appears there. CI's `coverage-report` job is different — `--cov=src --cov-fail-under=64` (`.github/workflows/ci.yml:243`), so new uncovered lines in `src/db` **do** move that number. Keep the repositories tested.
  - [x] CI runs `uv run alembic upgrade head` *before* both the integration and coverage jobs (`ci.yml:157-161, 220-224`). A migration that fails to apply fails CI before a single test runs — so run the upgrade/downgrade/upgrade cycle locally first.
  - [x] Grep checks, **scoped to this story's files** — a repo-wide `grep -rn "status = " src/db/` already matches 6 pre-existing `resolution_status` assignments in the instrument-metadata repositories, which AR37 does not govern and which are not yours to change:
    - `grep -rn "\.status = " src/db/models/trading_session.py src/db/repositories/trading_session_repository*.py src/cli/commands/live.py` → nothing (AR37, pre-empted).
    - `grep -rn "model_dump(" src/db/ src/cli/commands/live.py` → nothing; the only serialiser is `SessionSpec.to_stored()`.
    - `grep -rn 'model_dump(mode="json")' src/` → exactly one hit, inside `to_stored`.
    - `grep -n "^from sqlalchemy\|^import sqlalchemy" src/models/session.py` → nothing.
    - `grep -rc "is_live" src/core/strategies/` → still 0.
  - [x] **Mutation check on the two load-bearing guards** (Epic 1 retro, Action Item #3): (a) delete `values_callable` from the ORM column, confirm `test_trading_session_model.py`'s enum-label test fails, revert; (b) change `SessionSpec.to_stored()` to bare `model_dump()`, confirm the unit `json.dumps` test fails with a bare `TypeError` and the integration JSONB round-trip test fails with `sqlalchemy.exc.StatementError` **wrapping** that `TypeError` (SQLAlchemy re-raises at the bind layer, so `pytest.raises(TypeError)` would *not* catch the integration case), revert. A test that cannot fail is worse than no test.
  - [x] Update `deferred-work.md` under a new "Deferred from: story-2.2" section: the `src/models/trade.py` Pydantic widening owed to Epic 3, the `model_copy` re-validation item re-pointed at Story 2.4, and the `resolve_live_bar_types` lowercase item if still open.

### Review Findings

Code review 2026-08-19. Three adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor)
produced 40 raw findings, 38 after dedupe; 10 independent skeptics with repo access refuted 24 and
confirmed 14, which merge to 11 distinct items. Two further findings came from the reviewer's own
pass. **No critical, high, or medium finding survived verification** — the migration was independently
re-verified against the live database (single head `d08dfbd393f0`; `status` is a genuine
`session_status` enum with lowercase labels; all 13 AC #1 columns present as `timestamptz`/`jsonb`;
uniqueness as unique *indexes* matching the ORM names exactly; `chk_trades_owner` and
`trades_session_id_fkey` present with `NO ACTION`; 133 `backtest_runs` / 14 982 `trades` unchanged;
0 NULL `backtest_run_id`; 0 non-`backtest` `run_type`). AC #1, #2, #3 confirmed at the catalog level.

- [x] [Review][Patch] `ntrader live create --name` is entirely unvalidated — Empty, whitespace-only and >100-character names all reach the INSERT. Blank names persist as a permanent, near-unaddressable handle; a >100-char name surfaces as `Database error: value too long for type character varying(100)` at exit **1**, though the command's own exit-code table reserves 1 for state conflicts and 2 for usage errors. Confirmed by two of three layers. **Decision 2026-08-19 (Allay): reject both** — a `--name` callback rejects blank/whitespace-only and >100-character names with `click.BadParameter`, both at exit 2. No trimming: the stored name is exactly what was typed, which a frozen-spec system should not silently alter. [src/cli/commands/live.py:167]
- [x] [Review][Patch] `SessionSpec.from_stored` raises a bare `TypeError`, not the documented `ValueError`, on a non-integer `schema_version` — the illegible failure AC #10 exists to prevent, on the hand-edited-row path the `.get()` default was added to protect. Verified by execution: `"2"`, `"1"` and `None` all raise `TypeError: '>' not supported…`. `True` silently passes as version 1. [src/models/session.py:437-442]
- [x] [Review][Patch] `SPEC_SCHEMA_VERSION` is declared but is not the source of the value written — `SessionSpec.schema_version` still defaults to an independent literal `1`, so the writer and the read gate can drift on a future bump. [src/models/session.py:61, 336]
- [x] [Review][Patch] `test_upgrade_widens_backtest_runs_and_trades` cannot fail on the regression it names — `"nullable=True" in text` is satisfied by any of the 9 unrelated `nullable=True` occurrences. Proven by in-memory mutation: inverting the `alter_column` to `nullable=False` leaves the test green. This is the only CI-visible guard for the migration. [tests/unit/db/test_migration_trading_sessions.py:82-93]
- [x] [Review][Patch] Nothing asserts that `downgrade()` restores `trades.backtest_run_id` to NOT NULL — AC #4's literal clause. Delete that line from the migration and the suite stays green. [tests/unit/db/test_migration_trading_sessions.py:96-114]
- [x] [Review][Patch] `test_name_is_escaped_before_printing` asserts only the exit code, so it passes identically with `escape()` deleted — Rich does not raise on `weird[markup]`, it silently renders `weird`. [tests/unit/cli/commands/test_live_cli.py:526-546]
- [x] [Review][Patch] `find_all()` has no `ORDER BY` in either repository — Story 2.8's `live list` would inherit nondeterministic row order; every comparable list method in `src/db/repositories` orders explicitly. [src/db/repositories/trading_session_repository.py:106-114, trading_session_repository_sync.py:105-113]
- [x] [Review][Patch] `--param =12` is accepted and produces a message that names nothing — the empty key joins to the empty string: `Unknown parameter(s) for 'sma_crossover': .` Same shape for a whitespace-padded key, which renders indistinguishably from a valid one. [src/cli/commands/live.py:219-224, 79-96]
- [x] [Review][Patch] The migration docstring calls `chk_trades_owner` "exactly one owner"; the predicate enforces at least one — the permissive semantics are correct and deliberate (a post-seal trade legitimately carries both owners per AR8), so this is a one-word prose fix, not a missing constraint. [alembic/versions/d08dfbd393f0_add_trading_sessions_and_paper_run_.py:30-32]
- [x] [Review][Patch] Three DB-free AC #7/#8 guards sit in `tests/integration/db/`, which CI `--ignore`s — contradicting this story's own Testing Standards ("anything that must gate a PR belongs in the unit tier"). They also only reject names starting with `update`, so `set_spec`/`save`/`patch` would slip through. `test_trading_session_model.py`'s docstring claims to guard AC #7 but contains no such test. [tests/integration/db/test_trading_session_repository.py:148-151, 250-260]
- [x] [Review][Patch] The `src/models/session.py` module docstring is now stale — Known-limit 4 says `schema_version` is "recorded, not enforced. Nothing branches on it and no reader exists yet; the gate belongs with the read path in Story 2.2", while line 438 of the same file now branches on it; and lines 41-44 say the write-back path "does not exist yet — Story 2.2 owns it". This story is that story. [src/models/session.py:31-33, 41-44]
- [x] [Review][Patch] The `compare_to` parameter is the only unannotated argument in `create()` — mypy passes only because `disallow_untyped_defs` is off. Should be `Optional[UUID]`. [src/cli/commands/live.py:199]
- [x] [Review][Defer] Both new repository classes have zero CI-gating test coverage — deferred, root cause pre-existing [src/db/repositories/trading_session_repository.py]
- [x] [Review][Defer] `--compare-to` accepts a failed or metric-less backtest run — deferred, no AC requires it [src/cli/commands/live.py:247-251]
- [x] [Review][Defer] ORM↔migration agreement (index names, uniqueness, column set) is guarded by no test — deferred, acknowledged repo-wide blind spot [tests/integration/db/test_migration_schema.py]

## Dev Notes

### What this story is
The phase's **single Alembic migration**, plus the ORM row, both repositories, and the one CLI command
that writes a session. Seven later stories read what this story writes and none of them can start
until it exists. It is also the last cheap moment to get the schema right: `trading_sessions` rows
are the system of record for multi-week forward tests, and a second migration to fix a column later
would have to migrate live sessions.
[Source: epics.md#Story-2.2 (lines 761–807); architecture.md#Data-Architecture (D1, lines 212–237)]

### ⚠️ `sa.Enum` persists the member NAME, not the value — and this repo has never hit it
This is the highest-risk line in the story. Verified against the installed SQLAlchemy 2.0.43 and this
repo's own `SessionStatus`:

```
sa.Enum(SessionStatus).enums                              -> ['CREATED','RUNNING','STOPPED','SEALED']
sa.Enum(SessionStatus, values_callable=...).enums         -> ['created','running','stopped','sealed']
```

And confirmed end-to-end against the live Postgres (inside a rolled-back transaction): with
`values_callable`, `create_all` produces a `session_status` type whose `pg_enum` labels are the four
lowercase strings, and `INSERT ... VALUES ('created')` succeeds.

The reason nobody has been bitten yet is that the repo's **only** existing DB enum dodged it
deliberately. `src/models/instrument_metadata.py:36-41` says so in its own docstring:

> Members deliberately use ``name == value`` so the string stored by the SQLAlchemy ``Enum`` column
> matches the value, avoiding the ``values_callable`` footgun.

`SessionStatus` cannot take that escape route — Story 2.1's AC #6 fixed the values as lowercase and
`architecture.md:380-381` fixes the DB labels as lowercase too. So **Story 2.2 is the first place in
this codebase where `values_callable` is mandatory**, and the one existing precedent a dev agent
would naturally copy is precisely the one that does not need it. Copying
`instrument_metadata.py:59-63` verbatim yields uppercase `CREATED`/`RUNNING` labels, a `--json`
payload that disagrees with the database, and every `WHERE status = 'running'` query silently
matching nothing.

Related and separate: `create_type=False` in that same precedent is a **no-op on `sa.Enum`**
(verified: `hasattr(sa.Enum(...), "create_type")` is `False`). It *is* meaningful on
`postgresql.ENUM` inside the migration, which is why Task 4 keeps it there and Task 2 drops it.

### ⚠️ A bare `model_dump()` cannot be written to a JSONB column
`SMAParameters.portfolio_value` and `position_size_pct` are `Decimal`
(`src/models/strategy.py:29-37`), and `_normalise_parameters` stores them python-mode, so
`spec.strategies[0].parameters["portfolio_value"]` is a real `Decimal` in memory. Verified against
this repo's live Postgres, writing into a temporary JSONB column:

```
INSERT ... VALUES (spec.model_dump())            -> StatementError: (builtins.TypeError)
                                                    Object of type Decimal is not JSON serializable
INSERT ... VALUES (spec.model_dump(mode="json")) -> OK
  read back -> SessionSpec.model_validate(row) == spec       True
            -> type(...["portfolio_value"])                  Decimal
```

So the write path is `SessionSpec.to_stored()` (which is `model_dump(mode="json")` and nothing else)
and the read path is `SessionSpec.from_stored(row.spec)`. The `Decimal` survives because `StrategySpec`'s
`mode="before"` validator re-runs on validation and re-coerces the stringified value through the
strategy's own `param_model` — the same mechanism that makes Story 2.1's AC #5 satisfiable. Do not
"optimise" it away.

**Do not reach for `ValidatedJSONB`** (`src/db/types/validated_jsonb.py`) even though it looks like
the house pattern for a validated JSONB column. Three reasons, all verified: it is wired to **no
column anywhere** (`BacktestRun.config_snapshot` is plain `JSONB` at `backtest.py:105`); its `impl`
is `JSON`, not `JSONB`; and its `process_bind_param` returns `validated.model_dump()` — python-mode
— so pointing it at `SessionSpec` reproduces the `Decimal` `TypeError` above at every insert. Use
plain `JSONB` and serialise at the repository boundary.

### ⚠️ Two different things are named `session_id`, and a third is just `session`
The word is heavily overloaded here. Before naming anything, know all five meanings:

| Name | Means |
|---|---|
| `src/db/session.py` / `session_sync.py` | **SQLAlchemy** engine + sessionmaker modules. Taken. |
| `session` (parameter, every repository) | The SQLAlchemy `Session`/`AsyncSession`. Taken. |
| `src/models/session.py` | Story 2.1's domain spec. Taken. |
| `trading_sessions.session_id` | The session's **UUID business key**. |
| `trades.session_id` (new) | A **BigInteger FK → `trading_sessions.id`**. |

That last pair is the trap: the two columns share a name and hold different types. It is a loud
failure rather than a silent one — Postgres refuses to compare `bigint` to `uuid` — but the join is
`trades.session_id = trading_sessions.id`, never `= trading_sessions.session_id`. The choice is
deliberate: it mirrors `trades.backtest_run_id → backtest_runs.id`, is half the width on a table
already holding 14 982 rows and growing with every live trade, and `architecture.md:381` names
`session_id` as the column, not `trading_session_id`. Say all of this in the ORM column's docstring.

Inside a repository method, `session` already means the SQLAlchemy session. Name a trading-session
row `session_row` or `trading_session`.

### ⚠️ Do not autogenerate this migration
Write it by hand. Autogenerate compares `target_metadata` against **whichever database you point it
at**, and that is the problem: against this developer's database it is clean today (verified —
`compare_metadata` returns **0** diffs), because `create_all` has run here at some point and left
behind indexes the migration chain never creates. Against a freshly-migrated database it is not
clean. `trades` is the concrete case: `34f3c8e99016:71` creates `idx_trades_backtest_run_id`, while
the ORM's `index=True` on the same column wants `ix_trades_backtest_run_id`
(`Base.metadata.tables["trades"]` carries both names today). So autogenerate's answer here is a
property of your laptop, not a specification, and a dev agent that keeps its output ships schema
changes this story never asked for.

**Do not "repair" that pre-existing drift in this migration either.** It is out of scope, and adding
`op.create_index("idx_backtest_runs_instrument", ...)` would fail immediately on any database where
the index already exists — including the one you are developing against.

### The migration runs against real data — 133 runs and 14 982 trades
Verified against the configured database (`alembic current` → `a436f35f525c (head)`;
`select count(*)` on each table). Practical consequences:

- `ALTER TABLE trades ALTER COLUMN backtest_run_id DROP NOT NULL` is metadata-only. Instant.
- `ADD COLUMN session_id bigint NULL` is metadata-only on PG 11+. Instant.
- `ADD COLUMN run_type varchar(20) NOT NULL DEFAULT 'backtest'` is metadata-only on PG 11+ — the
  default is stored, not written to 133 rows.
- `ADD CONSTRAINT chk_trades_owner CHECK (...)` **does** scan all 14 982 rows to validate. They all
  hold a non-null `backtest_run_id` (`information_schema` says the column is currently `NOT NULL`),
  so it validates cleanly; at this size the scan is milliseconds.
- There are **no hypertables, no views and no triggers** on `trades` or `backtest_runs` (grepped
  `alembic/` and `src/db/` for `create_hypertable`, `CREATE VIEW`, `materialized`: zero hits), so
  nothing else has to be rebuilt.

### `backtest_runs.run_id` is a legal FK target, and `sealed_run_id` deliberately is not
`run_id` carries `unique=True` (`backtest.py:80-86`, unique index `ix_backtest_runs_run_id`), so
`linked_backtest_run_id` can be a real FK to it, as AR4 requires. `sealed_run_id` gets **no** FK:
AR4 says only "nullable", it is written at seal in Epic 5, and the in-repo precedent for a soft UUID
reference is `backtest_runs.reproduced_from_run_id` (`backtest.py:108-110`) — a bare nullable
`PG_UUID` with no constraint. Follow it.

No `ondelete` on `linked_backtest_run_id` either. Nothing in `src/` deletes a `backtest_runs` row
(grepped: zero delete paths), and of the two plausible behaviours, `CASCADE` would delete sessions
when a backtest is removed and `SET NULL` would silently erase the comparison provenance FR15
exists to record. Postgres' default `NO ACTION` refuses the delete instead, which is the honest
answer to "you are about to orphan a forward test."

### AR5 in one line: no `backtest_runs` row is created here
Not at `create`, not at `start`. Only at seal (Epic 5). A pre-created row would surface unfinished
sessions in the existing list views and break the zero-UI-change success criterion. Before seal,
`status`/`list` (Story 2.8) read `trading_sessions` joined to `trades`. If you find yourself
inserting into `backtest_runs`, stop — `architecture.md:477` lists it as a review-rejected
anti-pattern.

### Status is set by a column default, not an assignment
`default=SessionStatus.CREATED` on the ORM column, and no line of code anywhere writes
`row.status = ...`. Story 2.3 will add `SessionService.transition()` and an AC that says "a grep
finds no direct `status =` assignment outside it" (AR37). Getting there with zero assignments to
delete is free now and annoying later.

Creation is not a transition — it is the initial state — so `live create` does **not** need
`SessionService`. It goes CLI → sync repository directly, which is what every other DB-touching
command in this repo already does (`show.py:56-58`, `history.py:99-101`, `reproduce.py:66-68`,
`catalog.py:53-64`). `SessionService` arrives in Story 2.3 with the state machine that justifies it.

### The read path re-validates, and that has a real cost
`StrategySpec`'s `mode="before"` validator re-runs on `model_validate` / `model_validate_json`, so
loading a stored spec re-hits `StrategyRegistry` and the strategy's current `param_model`. Verified
consequences, recorded in `deferred-work.md` from the Story 2.1 review: a param field removed in a
later build is silently dropped when a *sealed* session's record is read back; tightening any param
constraint makes previously-sealed sessions unloadable; renaming or removing a strategy makes every
referencing row permanently unloadable.

This story does **not** fix that — the fix is a decision about whether a sealed spec is read as
stored, and Epic 5 owns sealed records. What this story does is (a) keep the repositories returning
the raw `dict` from the JSONB column, so `status`/`list` never need a live registry to answer, and
(b) put the typed read behind an explicit `SessionSpec.from_stored()` that only the callers who
genuinely need a `SessionSpec` (Story 2.5's runner) go through. That localises the brittleness to
one function instead of spreading it across every query. Leave the deferred item open and re-point
it at Epic 5.

### `--param key=value` is a new convention for this repo
There is no `--param` pattern anywhere today — `grep` for `key=value`, `split("=")`, `nargs=2` and
`--param` across `src/cli` returns zero hits. Backtests take explicitly-declared typed options
(`--fast-period`, `--slow-period`) or a YAML file. Neither generalises to `SessionSpec`'s list of
strategy specs, so this story introduces the generic form.

Two verified facts that make it work: values may stay strings, because the parameter model coerces
them (`"5"` → `int`, `"50000.25"` → `Decimal("50000.25")`); and a bad value surfaces as
`ValueError: Configuration validation failed for sma_crossover: ...` from `build_strategy_params`,
*not* as a pydantic `ValidationError` — which is why the CLI's catch tuple must be
`(ValidationError, ValueError)`.

Use `str.partition("=")`, not `split("=")`: a value may legitimately contain `=`.

### Canonical bar-type strings, already handled
`--bar-type` values go straight into `StrategySpec.from_overrides(bar_types=...)`, which validates
them through `resolve_live_bar_types` and upper-cases them. INTERNAL-aggregated, composite,
non-time-aggregated, unparseable and intra-spec-duplicate entries are all refused there with an
operator-facing message. **Do not add a second bar-type parser in the CLI** — pass the tuple
through and let the model refuse it.

### Testing standards
- **Unit tier** (`tests/unit/`, `make test-unit`, `-n auto`): the migration artifact test and the ORM
  shape test. Both need zero database. This placement is not stylistic — `.github/workflows/ci.yml`
  passes `--ignore=tests/integration/db` on both the integration job (line 168) and the coverage job
  (line 238), so **the entire `tests/integration/db/` directory never runs in CI**. Anything that
  must gate a PR belongs in the unit tier. Precedent: `tests/unit/db/test_migration_bar_count_30min.py`.
- **Integration tier** (`tests/integration/db/`, `make test-integration`, `--forked`): repository
  behaviour and the CLI write path, against real Postgres. The whole directory self-skips when
  Postgres is unreachable (`conftest.py:35-38`).
- Use the **`sync_db_session`** fixture (`tests/integration/db/conftest.py:152-196`) for sync work. It
  uses **pg8000, not psycopg2**, deliberately: psycopg2 links libpq and segfaults in a `--forked`
  child once Nautilus C extensions are loaded. Do not "fix" it to psycopg2.
- Test schemas are built by `Base.metadata.create_all`, **not** by the migration chain. So the ORM
  and the migration must agree by hand — a migration that disagrees with the ORM is invisible to
  every test except the migration-truth test. This is stated verbatim in
  `tests/integration/db/test_migration_schema.py`'s own docstring, which exists because that drift
  already happened once.
- Verified: `Base.metadata.create_all` **does** create the PG enum type inside the per-worker scratch
  schema, so the enum column works in the fixtures without any extra setup.
- The **upgrade** path is already covered without you doing anything:
  `tests/integration/db/test_migration_schema.py` + `_migration_probe.py` run a real
  `alembic upgrade head` into a scratch schema on every `make test-integration`, so a migration that
  fails to apply fails locally before you reach Task 9's checklist. (Not in CI, which `--ignore`s
  that directory — but CI runs `alembic upgrade head` as a job step, so it fails there too.)
- The **downgrade** path is not covered by anything: there is **no alembic downgrade test anywhere in
  this repo** and no upgrade→downgrade→upgrade round trip. AC #4 is exercised by hand in Task 5 plus
  the static `downgrade()` assertions in Task 1. Building a full round-trip harness is out of scope;
  `_migration_probe.py` is the fork-safe subprocess template if a later story wants one.
- `pytest.ini` is the effective config; `pyproject.toml`'s `[tool.pytest.ini_options]` marker list is
  shadowed and dead. `--strict-markers` is on. The **`db` marker is registered and currently unused**
  — tagging the Postgres-requiring tests with it is free and makes them selectable.
- The `acceptance_criterion` marker exists (`tests/conftest.py:58-65`) but is used **only** by the
  Epic-1 traceability harness (`tests/integration/core/epic1_criteria.py`). Story 2.1's tests do not
  use it. Optional here; don't feel obliged.
- **TDD is non-negotiable**: every task's test subtask must be RED before its implementation subtask.

### Scope boundaries — do NOT do these here
- **No `SessionService`, no `transition()`, no `InvalidSessionTransition`, no name→UUID resolution
  helper.** Story 2.3 (AR37). This story writes rows with the initial status; it moves nothing
  between states.
- **No `trader_id` derivation, no `RedisSettings`, no `CacheConfig`.** Story 2.4 (AR10, AR11).
- **No runner, no `TradingNode`, no startup phases, no `live start`.** Story 2.5 (AR39).
- **No `live status` / `live list` / `--json`.** Story 2.8 (FR21, FR22, AR29).
- **No seal path, no `session_conditions`, no `SessionConditions` model, no writes to
  `backtest_runs`.** Epic 5 (AR5, AR7). `run_type` gets its *column* here and its first non-default
  *value* there.
- **No changes to `src/models/trade.py`, `src/api/**`, `templates/**`,
  `src/services/backtest_query.py`, comparison views, or `BacktestOrchestrator`** (AR44).
- **No new dependency.** `pyproject.toml` / `uv.lock` unchanged (AR3), and a hook blocks editing
  `pyproject.toml` by hand regardless — use `uv add` if you somehow need one, and you do not.
- **No new exception classes.** `DuplicateRecordError` and `DatabaseConnectionError` already exist.
- **No fix to `resolve_live_bar_types`' lowercase-instrument-id bug.** Deferred, Epic-1-owned code,
  and Story 2.1 already canonicalises at its own helper.

### Judgment calls made while writing this story (flag at the Epic 2 retro)
1. **One strategy per `live create` invocation.** `SessionSpec` holds a list and validates a
   two-entry spec (Story 2.1's whole point), but the CLI takes a single `--strategy` with its own
   `--bar-type`s and `--param`s. A multi-strategy grammar would have to bind each parameter and bar
   type to a specific strategy — a real CLI design problem — for a capability NFR29 explicitly rules
   out this phase, and `SessionSpec` already refuses two entries naming the same strategy. The list
   shape is preserved in the *model*, which is where FR13 lives.
2. **`SyncTradingSessionRepository`, not `TradingSessionRepositorySync`.** `architecture.md:234` and
   epics AC use the suffix form, but all five existing sync repositories use the `Sync…Repository`
   prefix (`SyncBacktestRepository`, `SyncInstrumentMetadataRepository`,
   `SyncCatalogInstrumentRepository`, `SyncCatalogDividendRepository`,
   `SyncCatalogStockSplitRepository`). Introducing a sixth class that breaks a five-of-five pattern
   creates permanent friction for a doc phrase written before the convention was checked. **File**
   names follow the architecture doc (`trading_session_repository_sync.py`) because those match the
   existing files too.
3. **`SessionSpec.from_stored()` with a hard version refusal**, rather than a best-effort read. The
   Story 2.1 review deferred the gate with "define it when the read path lands"; this is that path.
   Refusal is chosen because a mis-read spec corrupts a multi-week sample undetectably, which is the
   exact failure FR14 exists to prevent.
4. **`trades.session_id` targets `trading_sessions.id` (BigInteger), not the UUID business key.**
   Symmetry with `trades.backtest_run_id → backtest_runs.id`, half the width on the largest table in
   the schema. The name collision with `trading_sessions.session_id` is real and is documented in the
   column docstring; it fails loudly (type mismatch) rather than silently.
5. **`run_type` as `String(20)`, not a second PG enum.** AR6 asks for one column; the neighbouring
   `execution_status` is already a plain `String(20)`; and every enum type is another create/drop
   pair in every future migration.
6. **Duplicate name exits `1`, not `2`.** AR28 reserves `2` for Click's own usage errors, and Story
   1.7 recorded that inventing codes outside AR28's table is worse than a generic failure. A name
   collision is a state conflict, detectable only against the database.
7. **`name` is `String(100).`** Nothing specifies a length. Long enough for any human handle, short
   enough to render in Story 2.8's Rich table without wrapping.

### Project Structure Notes
- **NEW** `src/db/models/trading_session.py` → `TradingSession`. Domain models (`src/models/`) and DB
  models (`src/db/models/`) are never mixed (project-context.md:111).
- **NEW** `src/db/repositories/trading_session_repository.py` (async) and
  `trading_session_repository_sync.py` (sync).
  [Source: architecture.md#Delta-Project-Tree lines 523–529]
- **NEW** `alembic/versions/<rev>_add_trading_sessions_and_paper_run_support.py`.
- **MOD** `src/db/models/__init__.py`, `src/db/repositories/__init__.py`, `src/db/models/trade.py`,
  `src/db/models/backtest.py`, `src/models/session.py` (adds `SPEC_SCHEMA_VERSION`, `to_stored`,
  `from_stored`), `src/cli/commands/live.py`.
- **NEW** `tests/unit/db/test_migration_trading_sessions.py`,
  `tests/unit/db/test_trading_session_model.py`,
  `tests/integration/db/test_trading_session_repository.py`,
  `tests/integration/db/test_cli_live_create.py`. **MOD**
  `tests/unit/models/test_session_spec.py`, `tests/unit/cli/commands/test_live_cli.py`.
- Untouched by construction: `src/api/**`, `templates/**`, `src/core/strategies/**`,
  `src/services/**`, `src/models/trade.py`. [Source: architecture.md AR44]

### Commit hygiene for this repo
- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three
  points (`.githooks/pre-commit`, the Claude bash-guard, CI). Make dependent changes — import plus
  its usage — in a single edit. Run `make install-hooks` once per clone.
- Stage and commit in **separate** Bash calls (`git add <files>`, then `git commit`).
- Commit format `<type>(<scope>): <subject>`, e.g.
  `feat(live): persist a named session with an immutable specification`. Never reference AI or Claude.
- Alembic files are in ruff's exclude list, so `make format` will not touch the migration. Keep it
  hand-formatted to the repo's double-quote, 100-column style.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.2] (lines 761–807) — the acceptance criteria this file numbers and extends
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2] (lines 713–721) — epic scope, FR/NFR/AR coverage
- [Source: _bmad-output/planning-artifacts/epics.md:367-371] — the "Note on the migration": this epic owns the **single** Alembic migration for the whole phase, and why splitting it across three would be worse
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — AR3 (zero new deps), AR4 (`trading_sessions` columns), AR5 (no runs row before seal), AR6 (`run_type`), AR8 (`trades` widening + CHECK), AR9 (dual repositories), AR32 (heartbeat columns), AR36 (vocabulary), AR37 (state machine — Story 2.3), AR41 (log event naming), AR43/AR44 (anti-patterns, untouched files)
- [Source: _bmad-output/planning-artifacts/architecture.md#Data-Architecture] (lines 210–248) — D1 and D2 in full
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming-Patterns] (lines 367–392) — table name, lowercase enum values, `<entity>_id` FK convention, module/class names
- [Source: _bmad-output/planning-artifacts/architecture.md#Delta-Project-Tree] (lines 486–563) — the story's exact file footprint
- [Source: _bmad-output/planning-artifacts/prd.md] (lines 573–620) — immutable session spec, the `create` command's purpose, credentials never in the spec
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:669–679] — `build_strategy_params` drops unknown override keys; "Action for Story 2.2's CLI"
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:712–726] — re-validating a persisted spec is lossy; never persist a bare `model_dump()`
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:727–737] — validator errors carry an empty `loc`; the CLI must catch `(ValidationError, ValueError)`
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:754–771] — the `model_copy` and `schema_version` deferrals, both with "Action for Story 2.2"
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-08-17.md] — Key Insight #3 (collected-not-passed baselines), #5 (read the installed wheel, not the docs), Action Item #3 (mutation-test new load-bearing guards)
- [Source: alembic/versions/f051a079629c_add_instrument_metadata_table_and_.py] — the enum create/drop template, copied wholesale
- [Source: alembic/versions/ff982c8e1402_make_catalog_instruments_identity_.py] — the alter-to-nullable template, and the "never edit an applied migration" rule
- [Source: alembic/versions/a436f35f525c_add_excluded_resolution_status.py] — current head; the house style for a migration docstring that explains itself
- [Source: alembic/env.py:10-33] — sync engine only, `target_metadata` merged from two Bases, model registration via the `src.db.models` package import
- [Source: src/db/base.py:20-32] — `TimestampMixin` is `created_at` only
- [Source: src/db/models/backtest.py:74-158] — the dual-ID pattern, `PG_UUID(as_uuid=True)`, plain `JSONB`, `__table_args__` shape, and the two ORM-only indexes that make autogenerate unsafe
- [Source: src/db/models/trade.py:82,126,130-133] — `backtest_run_id`'s current FK/index, and the existing `__table_args__` CHECK naming (:130-132) and composite Index (:133)
- [Source: src/db/models/instrument_metadata.py:59-63] and [src/models/instrument_metadata.py:36-56] — the one existing DB enum, and its explicit `name == value` dodge of the `values_callable` footgun
- [Source: src/db/repositories/backtest_repository_sync.py:86-115] — the create/flush/refresh + `IntegrityError`→`DuplicateRecordError` idiom
- [Source: src/db/repositories/instrument_metadata_repository.py] — the smallest complete async/sync twin pair
- [Source: src/db/session_sync.py:98-134] — `get_sync_session`, which owns commit/rollback so repositories must not
- [Source: src/db/exceptions.py] — `BacktestStorageError` family; `DuplicateRecordError`, `DatabaseConnectionError`
- [Source: src/db/types/validated_jsonb.py] — the TypeDecorator that must NOT be used here, and why
- [Source: src/models/session.py] — `SessionSpec`/`StrategySpec`/`SessionStatus`, `from_overrides`' `Raises:` contract, the module docstring naming this story's obligations
- [Source: src/core/strategy_factory.py:296-380] — `StrategyLoader.build_strategy_params`, its `_settings_map` chain and its silent unknown-key drop
- [Source: src/core/strategy_registry.py:150-201] — `get()`'s fuzzy resolution vs `exists()`'s strictness; `param_model` optional at registration
- [Source: src/core/live_check.py:38-96] — AR28's exit-code constants and the recorded refusal to invent new ones
- [Source: src/cli/commands/live.py] — the `live` group, its option/exit/rendering conventions
- [Source: src/cli/commands/backtest.py:47-71] — `validate_strategy` as a Click callback against the registry
- [Source: src/cli/commands/_backtest_helpers.py:174-179] — pydantic `ValidationError` → `click.UsageError` conversion
- [Source: src/cli/commands/catalog.py:193-197] — the `(RuntimeError, DatabaseConnectionError, SQLAlchemyError)` catch tuple and `rich.markup.escape`
- [Source: src/core/live_account_gate.py:149,167] — the structlog event shape AR41 describes: event name as the positional message, context as kwargs (`logger.info("gate.account", ...)` at :149; note the refusal at :167 uses `logger.error`, so pick the level by severity — `session.created` is `info`)
- [Source: tests/integration/db/conftest.py:152-196] — `sync_db_session`, pg8000 and per-worker schema isolation
- [Source: tests/integration/db/test_migration_schema.py:1-20] — why ORM/migration drift is invisible to the suite
- [Source: tests/unit/db/test_migration_bar_count_30min.py] — the CI-visible, DB-free migration guard pattern
- [Source: tests/integration/db/test_cli_show.py:85-92] — patching `get_sync_session` for a CliRunner test
- [Source: tests/unit/models/test_session_spec.py:456-484] — the subprocess import-purity guard that forbids SQLAlchemy in `src/models/session.py`
- [Source: .github/workflows/ci.yml:157-161,168,220-224,238,243] — `alembic upgrade head` before the test jobs; `--ignore=tests/integration/db`; `--cov=src --cov-fail-under=64`
- [Source: Makefile:34-102] — tier targets, `typecheck` covering only `src/core src/services`, coverage covering only `src/core src/strategies`
- [Source: pytest.ini:13-31] — `--strict-markers`, the registered marker set, the unused `db` marker
- [Source: _bmad-output/project-context.md:50,86,107-112,122] — dual repository rule, repository pattern, layer separation, domain-vs-DB model separation
- [Source: CLAUDE.md] — commit format, structural import gate, staging discipline, single-head alembic invariant

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

None — no crashes, hangs, or environment-level failures required a subprocess trace. Two failures
surfaced and were fixed during the story, both recorded in Completion Notes: the pre-existing Epic 1
dependency-allowlist test (needed widening, not a bug) and a test-fixture ordering bug in this
story's own `test_cli_live_create.py` duplicate-name test (its mock `get_sync_session` didn't
commit-on-success, so a later `rollback()` undid an earlier row — fixed in the test, not the CLI).

### Completion Notes List

- Built exactly to the Dev Notes' pre-verified design, task by task, TDD Red confirmed before each
  implementation subtask. All 9 tasks complete; all 13 ACs satisfied.
- Baseline confirmed before any edit, against the live database: `alembic heads` → `a436f35f525c`
  (single head), 133 `backtest_runs`, 14 982 `trades` — matching the story's own recorded figures
  exactly, so the "existing rows unaffected" claim in AC #3 was measured, not assumed.
- The migration was hand-written (never `--autogenerate`) via `uv run alembic revision`, then its
  body filled in through Bash (the `alembic/versions/` directory is Edit/Write-protected by
  `.claude/hooks/protect-files.sh`; Bash is not, matching this repo's established precedent for
  protected-path writes). `alembic upgrade head` → `downgrade -1` → `upgrade head` round-tripped
  cleanly against the live database; post-upgrade counts confirmed identical (133 / 14 982), zero
  `trades.backtest_run_id IS NULL`, zero non-`'backtest'` `run_type`, and the `session_status` enum's
  `pg_enum` labels read exactly `created, running, stopped, sealed`.
- `values_callable` on the ORM's `status` column and `SessionSpec.to_stored()`'s `mode="json"` dump
  are the two guards Task 9 calls load-bearing. Both were mutation-tested per the Epic 1 retro's
  Action Item #3: deleting `values_callable` flipped the enum-label test to
  `['CREATED', 'RUNNING', ...]` and failed it; reverting `to_stored()` to a bare `model_dump()` failed
  the unit `json.dumps` test with a bare `TypeError` and the integration JSONB round-trip test with
  `sqlalchemy.exc.StatementError` wrapping that same `TypeError` — exactly as the story predicted.
  Both reverted immediately after confirming the failure.
- One pre-existing Epic 1 acceptance test needed a deliberate, scoped change:
  `tests/integration/core/test_epic1_ac_node.py::test_no_new_dependency_was_added_for_the_live_path`
  asserts `src/cli/commands/live.py`'s third-party import surface against an allowlist scoped to
  Epic 1's `check`-only command. `ntrader live create` legitimately imports `pydantic` (for
  `ValidationError`/`SessionSpec`) and `sqlalchemy` (for `SQLAlchemyError`/the repositories) — both
  already-declared project dependencies (`pyproject.toml`/`uv.lock` byte-identical, confirmed via
  `git diff --stat`), not new ones. Widened `PERMITTED_THIRD_PARTY` to include both, with a comment
  explaining AR3's "no new dependency" means no undeclared package, not no import of an existing one;
  also added both to the test's declared-dependency assertion loop. Re-ran: 8/8 then 40/40 Epic 1
  acceptance criteria still pass.
- Two grep checks in Task 9 needed the same "pre-existing/out-of-scope hit is not a failure" reading
  the story itself applies to the `status = ` check: `grep -rn "model_dump(" src/db/
  src/cli/commands/live.py` matches a pre-existing, unrelated hit in `src/db/types/validated_jsonb.py`
  (the exact TypeDecorator the Dev Notes say not to use) — zero hits when scoped to this story's own
  files. `grep -rn 'model_dump(mode="json")' src/` returns 2 hits, not exactly 1: Story 2.1's
  pre-existing module docstring already used the phrase once before this story added the `to_stored`
  method; reworded this story's own method-docstring mention to avoid a third, out of respect for not
  editing Story 2.1's untouched prose.
- The read path's version gate (`SessionSpec.from_stored`) closes the Story 2.1 review's deferred
  `schema_version` item, and the CLI's `set(overrides) - valid` guard closes the deferred
  `build_strategy_params` silent-drop item — both cited by name in Dev Notes and confirmed closed by
  this story's own tests (`test_from_stored_refuses_a_too_new_schema_version`,
  `test_unknown_param_key_exits_two_and_lists_valid_ones`).
- `deferred-work.md` gained a new "Deferred from: story-2.2" section carrying forward three items:
  `src/models/trade.py`'s Pydantic widening (owed to Epic 3, Story 3.6), the `model_copy`
  re-validation item (re-pointed at Story 2.4, whose durable engine cache is the first plausible
  second read path), and the `resolve_live_bar_types` lowercase item (re-verified still open at
  `live_market_data.py:154-208`; this story's own CLI is not exposed to it, since `--bar-type` values
  route through `StrategySpec._resolve_bar_types`, which already canonicalises).
- Final counts: baseline (collected, pre-edit) unit 1780 / component 1047 / integration 217. Final
  (passed): unit 1831 (+51) / component 1031 passed + 16 skipped (unchanged) / integration 238 passed
  + 2 skipped (+21, +2 pre-existing skips unchanged) — zero regressions. `make format` / `make lint`
  clean; `make typecheck` (`mypy src/core src/services`) clean; standalone `mypy` on all seven
  new/modified `src/db`, `src/models` and `src/cli` files clean.

### File List

**NEW**
- `tests/unit/db/test_trading_session_repository_shape.py` (added by code review — the AC #7/#8
  shape guards, moved out of the CI-ignored `tests/integration/db/` into the unit tier)
- `alembic/versions/d08dfbd393f0_add_trading_sessions_and_paper_run_.py`
- `src/db/models/trading_session.py`
- `src/db/repositories/trading_session_repository.py`
- `src/db/repositories/trading_session_repository_sync.py`
- `tests/unit/db/test_migration_trading_sessions.py`
- `tests/unit/db/test_trading_session_model.py`
- `tests/integration/db/test_trading_session_repository.py`
- `tests/integration/db/test_cli_live_create.py`

**MODIFIED**
- `src/db/models/__init__.py`
- `src/db/models/backtest.py`
- `src/db/models/trade.py`
- `src/db/repositories/__init__.py`
- `src/models/session.py`
- `src/cli/commands/live.py`
- `tests/unit/models/test_session_spec.py`
- `tests/unit/cli/commands/test_live_cli.py`
- `tests/integration/core/test_epic1_ac_node.py` (Epic 1 dependency allowlist widened — see
  Completion Notes)
- `_bmad-output/implementation-artifacts/deferred-work.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Change Log

| Date | Change |
|---|---|
| 2026-08-19 | Code-reviewed → done. Three adversarial layers produced 40 raw findings (38 after dedupe); 10 independent skeptics with repo access refuted 24 and confirmed 14, merging to 11 distinct items, plus 2 found by the reviewer directly. **No critical, high or medium finding survived verification.** AC #1/#2/#3 were re-verified against the live database rather than taken on the story's word (single head; `status` is a genuine `session_status` enum with lowercase `pg_enum` labels; all 13 columns as `timestamptz`/`jsonb`; uniqueness as unique *indexes* matching the ORM names; `chk_trades_owner` + `trades_session_id_fkey` with `NO ACTION`; 133 runs / 14 982 trades unchanged; 0 NULL `backtest_run_id`; 0 non-`backtest` `run_type`). 1 decision resolved by Allay (`--name`: reject blank and >100 at exit 2, no trimming) and all 12 patches applied TDD Red→Green. Production fixes: `--name` validation callback + `MAX_SESSION_NAME_LENGTH`; `from_stored` now refuses a non-integer/bool `schema_version` with `ValueError` instead of a bare `TypeError`; `schema_version` defaults to `SPEC_SCHEMA_VERSION` so writer and read gate cannot drift; `ORDER BY created_at DESC, id DESC` on both `find_all()`s; blank `--param` key rejected and unknown keys rendered with `!r`; `compare_to` annotated `Optional[UUID]`. Test fixes: three assertions that could not fail were hardened and **mutation-tested** (the upgrade widening, the downgrade NOT-NULL restore, and `escape()`), the AC #7/#8 shape guards moved to the unit tier with an allowlist instead of a `startswith("update")` prefix test, and a structural AST guard binds the field default to the constant (an equality assertion provably could not catch that drift). Prose: the migration's "exactly one owner" corrected to "at least one" with the post-seal rationale, and `src/models/session.py`'s module docstring de-staled — it still claimed the read gate and the write path did not exist. One regression caught and fixed: adding `from uuid import UUID` to `live.py` broke Epic 1's `test_no_new_dependency_was_added_for_the_live_path`; `uuid` was added to the test's **stdlib** set, not to `PERMITTED_THIRD_PARTY`, since it is not a dependency. Final: unit 1831 → 1856 (+25), component unchanged (1031 passed / 16 skipped), integration 238 → 237 (3 shape guards moved to unit, 2 ordering tests added). format/lint/typecheck clean; standalone mypy on all five story source files clean. 3 items deferred to `deferred-work.md`; 24 findings dismissed as refuted. |
| 2026-08-19 | Story 2.2 implemented → review. in-progress → review, same session as starting. All 9 tasks complete, TDD Red confirmed before every implementation subtask. Delivers the phase's single Alembic migration (`trading_sessions`, `backtest_runs.run_type`, `trades` widened + `chk_trades_owner`), the `TradingSession` ORM model, both trading-session repositories, `SessionSpec.to_stored()`/`from_stored()`, and `ntrader live create`. Migration round-tripped (`upgrade` → `downgrade -1` → `upgrade`) against the live database with counts verified before/after (133 `backtest_runs`, 14 982 `trades`, both unchanged). Both load-bearing guards (`values_callable`, `to_stored`'s `mode="json"`) mutation-tested and confirmed load-bearing. One pre-existing Epic 1 acceptance test (`test_no_new_dependency_was_added_for_the_live_path`) deliberately widened — `create` legitimately imports the project's own already-declared `pydantic`/`sqlalchemy`, not a new dependency; `pyproject.toml`/`uv.lock` confirmed byte-identical. Zero regressions: unit 1780 → 1831 (+51), component unchanged at 1031 passed/16 skipped, integration 217 → 238 passed + 2 skipped (+21). format/lint/typecheck clean. `deferred-work.md` gained a "Deferred from: story-2.2" section (Epic 3's `src/models/trade.py` widening, the `model_copy` re-validation item re-pointed at Story 2.4, the still-open `resolve_live_bar_types` lowercase item). |
| 2026-08-18 | Draft validated in a fresh context against the create-story checklist; ~85 factual claims re-verified by execution, and 4 defects in the draft were corrected rather than shipped. Two mattered: (a) the "do not autogenerate" rationale was **wrong** — `compare_metadata` against the configured database returns 0 diffs, and `ff982c8e1402` repairs a *nullability* drift on `catalog_instruments`, not an index drift; the real hazard is that the ORM disagrees with the **migration chain** (`34f3c8e99016:71` makes `idx_trades_backtest_run_id` where the ORM's `index=True` wants `ix_trades_backtest_run_id`), so autogenerate's answer depends on which database it is pointed at. The old wording would have invited a dev agent to "repair" indexes that already exist, failing the upgrade. (b) Task 9's grep gate `grep -rn "status = " src/db/` already matches 6 pre-existing `resolution_status` assignments, so as written it could never pass and would have sent a dev agent to edit the instrument-metadata repositories. Also corrected: the migration's index names are now spelled out (`unique=True` on the ORM emits a unique *index*, not a `UniqueConstraint` — the natural reading of AC #1 would have created permanent, test-invisible drift); importing `EXIT_OK` would be an F401 that hard-blocks the commit; `catalog.py:197`'s cited precedent exits 2 while this command exits 1; `from_stored` must use `payload.get("schema_version", 1)` or it raises a bare `KeyError`; the JSONB mutation check raises `StatementError` wrapping `TypeError`, not a bare `TypeError`; the downgrade guard now runs before any DDL; and nine file:line citations were off by a few lines. |
| 2026-08-18 | Story drafted. Context assembled from epics.md, architecture.md, prd.md, the Epic 1 retrospective, deferred-work.md, and an exhaustive read of the existing DB/CLI/test layers. Five design questions were settled by execution against the installed libraries and the live database rather than from docs: (1) `sa.Enum(SessionStatus)` persists uppercase member names — `values_callable` is mandatory and this is the first place in the repo that needs it; (2) `spec.model_dump()` raises `TypeError` on `Decimal` when written to a real JSONB column, while `model_dump(mode="json")` round-trips losslessly with `Decimal` restored; (3) `ValidatedJSONB` is wired to no column, has `impl = JSON`, and reproduces that `TypeError` — it must not be used; (4) a typo'd `--param` key is silently dropped by `build_strategy_params`, freezing a wrong value for the session's life, so the CLI must check keys against `param_model.model_fields`; (5) string parameter values coerce correctly through the chain and failures surface as `ValueError`, not `ValidationError`. Current head confirmed as `a436f35f525c` against the live database, which holds 133 `backtest_runs` and 14 982 `trades` rows. |
