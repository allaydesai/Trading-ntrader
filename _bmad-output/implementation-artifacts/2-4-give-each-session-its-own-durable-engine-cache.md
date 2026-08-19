# Story 2.4: Give Each Session Its Own Durable Engine Cache

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a restarted process to rejoin its own engine state and never another session's,
so that session identity survives restarts and two sessions can never contaminate each other's
orders and positions.

## Acceptance Criteria

**Given** `Settings`
**When** configuration is loaded
**Then** a new `RedisSettings` (host, port, db) is available from env, pointing at the provisioned
`redis:7-alpine` service (AR11).

**Given** a session
**When** its Nautilus `trader_id` is derived
**Then** it is computed deterministically from the session as `PAPER-<short-session-id>`, producing
the identical value on every process run of that session (AR10)
**And** two different sessions produce different `trader_id` values.

**Given** the node configuration
**When** it is built
**Then** it uses `CacheConfig(database=DatabaseConfig(...))` pointed at Redis, namespaced by
`trader_id` (AR10).

**Given** a session that is stopped and started again
**When** the node comes up
**Then** it rejoins its own Redis namespace rather than starting empty (FR19).

**Given** a conflict between cached state and broker state
**When** the design is inspected
**Then** Redis is documented and treated as disposable cache — rebuildable from IBKR — with the
broker authoritative (AR10; enforcement lands in Epic 4).

---

⚠️ **AC #6 and AC #7 are added by this story and are not in `epics.md`.** Both are consequences of
behaviour that was *executed* against nautilus-trader 1.220.0 while drafting (see *Pre-verified
findings* #1 and #3), not preferences. Story 2.3 set the precedent for adding an AC when the epic's
list is individually correct but jointly leaves a demonstrated hole. Flag both at the Epic 2 retro.

**Given** Redis is unreachable (not started, wrong host, wrong port)
**When** a node is constructed with a Redis-backed `CacheConfig`
**Then** the failure is detected by a bounded preflight check that raises a named, actionable error
naming host and port — **not** by the process hanging forever
**And** the preflight adds no new dependency (AR3).

**Given** the three `CacheConfig` fields that decide the Redis key namespace —
`use_trader_prefix`, `use_instance_id`, `flush_on_start`
**When** the cache config is built
**Then** all three are passed **explicitly** rather than inherited from Nautilus defaults, and a test
asserts each value by name
**And** `TradingNodeConfig.load_state` / `save_state` are left at `False` (they are a different
mechanism — see *Pre-verified findings* #6 — and this story does not change them).

## Tasks / Subtasks

- [x] **Task 1 — `RedisSettings` on `Settings` (AC: #1)**
  - [x] RED: `tests/unit/test_config.py` (or a new `tests/unit/test_redis_settings.py` if that file
        is crowded) — assert defaults `redis_host="127.0.0.1"`, `redis_port=6379`, `redis_db=0`;
        assert env override via `monkeypatch.setenv("REDIS_HOST", ...)`; assert `Settings().redis`
        is a `RedisSettings`.
  - [x] RED: assert `RedisSettings(redis_db=1)` raises — see *Judgment call #3*, the `db` index
        cannot be honoured at nautilus-trader 1.220.0 and must not be silently ignored.
  - [x] RED: assert `redis_port` rejects `0` and values `> 65535`, and `redis_host` rejects empty.
  - [x] GREEN: add `RedisSettings(BaseSettings)` to `src/config.py` with the same `model_config`
        block every other settings class uses, and `redis: RedisSettings = Field(default_factory=...)`
        on `Settings` alongside `ibkr` / `kraken` / `fmp` / `firstrate` / `catalog`.
  - [x] Wire `REDIS_HOST: redis` into `docker-compose.yml`'s app service env block (the existing
        `REDIS_URL` there is dead config — see *Pre-verified findings* #5). Do **not** remove
        `REDIS_URL`; do **not** edit `.env.example` without asking (hook-protected — see *Blockers*).

- [x] **Task 2 — deterministic `trader_id` derivation (AC: #2)**
  - [x] RED: `tests/unit/core/test_live_trader_id.py` — same UUID in, same string out across
        repeated calls; two different UUIDs produce different strings; output is exactly
        `PAPER-<8 lowercase hex>`; a non-`UUID` argument raises `TypeError` with a message naming
        what was passed.
  - [x] RED: the load-bearing one — assert `TraderId(derive_trader_id(u)).get_tag()` equals the
        8-hex tag, for 200 random UUIDs. This is what AR23's client order IDs actually embed
        (*Pre-verified findings* #4); a hyphen anywhere in the tag silently truncates it.
  - [x] GREEN: `src/core/live_trader_id.py` — `TRADER_ID_PREFIX = "PAPER"`,
        `SHORT_SESSION_ID_LENGTH = 8`, `derive_trader_id(session_id: UUID) -> str`.
        No Nautilus import, no SQLAlchemy import, no settings read.
  - [x] RED+GREEN: import-purity guard in the same test file, copying the template at
        `tests/unit/models/test_session_spec.py:438-486` — importing `src.core.live_trader_id` in a
        fresh interpreter leaks zero `nautilus_trader` and zero `sqlalchemy` modules.

- [x] **Task 3 — the cache module (AC: #3, #6, #7)**
  - [x] RED: `tests/component/core/test_live_cache.py` — `build_cache_config(redis_settings)`
        returns a `CacheConfig` whose `database.type == "redis"`, `database.host`/`port` match the
        settings, and whose `use_trader_prefix is True`, `use_instance_id is False`,
        `flush_on_start is False`. Assert each by name (AC #7): these three are the whole of AC #4.
  - [x] RED: `check_redis_reachable()` against a closed port raises `RedisUnreachableError` naming
        host and port, and returns within its timeout — assert the wall-clock bound, because the
        failure this guards against is an unbounded hang, not a wrong exception type.
  - [x] GREEN: `src/core/live_cache.py` — `RedisUnreachableError`,
        `build_cache_config(settings: RedisSettings) -> CacheConfig`,
        `check_redis_reachable(settings: RedisSettings, *, timeout: float = 2.0) -> None`.
        Preflight uses **stdlib `socket` only** (AR3 — no `redis` package is installed and none may
        be added; see *Pre-verified findings* #2).
  - [x] Verify the RESP `PING` handshake against a live Redis before trusting it. The socket-level
        wire format is the one thing in this story that was **not** pre-verified — no Redis was
        running on this machine at drafting time. If inline `PING\r\n` → `+PONG\r\n` does not behave
        as expected, fall back to a bare TCP connect and say so in the docstring rather than
        claiming a protocol check the code does not perform.

- [x] **Task 4 — wire the cache into node assembly (AC: #3, #6)**
  - [x] RED: extend `tests/component/core/test_live_node_builder.py` — `build_trading_node_config`
        with a `cache=` argument returns a config whose `.cache` is that `CacheConfig`; omitting it
        leaves `.cache is None` (every existing test in that file must keep passing unchanged).
  - [x] RED: `build_trading_node(..., cache=...)` calls the preflight **before** constructing
        `TradingNode`, and a `RedisUnreachableError` propagates with no node built. Assert ordering
        the way the gate's ordering is asserted (`test_live_node_builder.py` already has the
        pattern): the failure must happen before any object that could hang exists.
  - [x] GREEN: add a keyword-only `cache: CacheConfig | None = None` parameter to both
        `build_trading_node_config` and `build_trading_node`; pass it to `TradingNodeConfig(cache=...)`;
        call `check_redis_reachable` in `build_trading_node` when `cache is not None` and
        `cache.database is not None`. Keep the "nothing that can fail runs after a client config
        exists" ordering property the module docstring already claims.
  - [x] Update `live_node_builder.py`'s module docstring: it currently disclaims "Redis caching
        (Epic 2)" and "the session's `trader_id` (Epic 2 derives it)". The first is now owned here;
        the second still is not — point it at `src/core/live_trader_id.py`.
  - [x] Confirm `wc -l src/core/live_node_builder.py` stays **under 500** (it is 493 today — this
        is why the cache lives in its own module, see *Judgment call #1*).

- [x] **Task 5 — prove the namespace actually persists and actually isolates (AC: #4)**
  - [x] Start Redis first — see *Blockers*. This task cannot be written without it.
  - [x] RED: `tests/integration/core/test_live_cache_namespace.py`, marked `@pytest.mark.integration`
        and skipped with a clear reason when Redis is unreachable. Construct a `CacheDatabaseAdapter`
        directly for trader A, `add(key, value)`, close; construct a **second** adapter for the same
        trader A and assert `keys()` still contains it (this is FR19's "rejoins rather than starting
        empty"); construct a third for trader B and assert it sees none of A's keys.
  - [x] Mutation-test the guarantee (Epic 1 retro Action Item #3): flip `use_instance_id` to `True`
        in a scratch copy and confirm the rejoin assertion **fails**. If it still passes, the test
        is not measuring what AC #4 claims.
  - [x] Clean up after the test — `flush()` the scratch trader namespaces, or use trader tags
        derived from a per-test UUID so a leftover key can never make a later run pass falsely.
  - [x] No `TradingNode` is constructed in this test. Constructing one requires a live IB gateway;
        the cache adapter does not.

- [x] **Task 6 — document disposability (AC: #5)**
  - [x] State it in `src/core/live_cache.py`'s module docstring: Redis holds **cache** state only,
        it is rebuildable from IBKR, IBKR is authoritative on conflict, and **nothing in this story
        enforces that** — reconciliation is Epic 4 (FR35, AR25). Do not overstate what the code
        protects; that is the recurring documentation failure the Epic 1 retro named.
  - [x] Add the same two sentences to `docs/agent/nautilus.md` under a short "Live session cache"
        heading, so the next agent finds it without reading this story.
  - [x] RED+GREEN: a docstring-presence test is **not** wanted here. AC #5 is satisfied by the
        design being inspectable, and a regex over prose is a test that fails on rewording.

- [x] **Task 7 — record what this story leaves open**
  - [x] Append `## Deferred from: story-2.4 (2026-08-19)` to
        `_bmad-output/implementation-artifacts/deferred-work.md` with, at minimum: the `redis_db`
        index that 1.220.0 cannot honour; the 8-hex truncation collision bound; the `msgpack`
        encoding lock-in; and the outcome of the two items *Deferred items this story reads* points
        at this story.
  - [x] Run `make lint`, `make typecheck`, `make test-unit`, `make test-component` clean before
        marking any task complete.

## Dev Notes

### What this story owns, and what it must not touch

**Owns:** `RedisSettings` in `src/config.py`, `src/core/live_trader_id.py` (new),
`src/core/live_cache.py` (new), a `cache=` parameter on both `live_node_builder` entry points, the
`REDIS_HOST` line in `docker-compose.yml`, and their tests.

**Does not own — do not build these here:**

- **No runner, no `live start`.** Story 2.5 owns `LiveSessionRunner`, the startup phase sequence,
  and every CLI surface. This story ships the pieces `node:build` will assemble. `src/cli/commands/live.py`
  is **not** modified.
- **No reconciliation.** AC #5 asks only that disposability be *documented*. The broker-authoritative
  overwrite is Epic 4 (FR35, AR25). Do not write a reconcile path.
- **No Alembic migration.** Story 2.2 spent the phase's single migration (`d08dfbd393f0`). Nothing
  here touches the schema. `trader_id` is **derived, never stored** — see *Judgment call #2*.
- **No `MessageBusConfig(database=...)`.** AR10 names the *cache* only. A Redis-backed message bus is
  a separate Nautilus subsystem with its own external-stream semantics, and nothing in this phase
  asks for it.
- **No change to `load_state` / `save_state`.** Different mechanism, see *Pre-verified findings* #6.
- **No new dependency.** AR3 is absolute and there is no `redis` package in `uv.lock`. If you reach
  a point where one seems necessary, that is a HALT and a question for Allay, not a `uv add`.

### ⚠️ Blockers to clear before Task 5

**Redis is not running on this machine and Docker is not up.** Verified at drafting: port 6379
closed, `docker ps` → *"Cannot connect to the Docker daemon"*, no Homebrew `redis` formula
installed, no `redis-cli` on `PATH`. Tasks 1–4 do not need Redis; **Task 5 cannot be written without
it.** Either start the compose service (`docker compose up -d redis` once the daemon is running) or
install one locally (`brew install redis && brew services start redis`) — this repo already runs
PostgreSQL via Homebrew rather than Docker, so the Homebrew route matches the existing local setup.

**`.env.example` is hook-protected.** `.claude/hooks/protect-files.sh:21` blocks every `.env*` path
outright. Story 1.2 hit exactly this and needed a one-off approval from Allay. If you conclude
`.env.example` should gain `REDIS_HOST` / `REDIS_PORT`, **ask** — do not work around the hook. The
defaults are correct for a local run without it, so this is documentation, not function.

### Pre-verified findings

Everything below was **executed** against the installed `nautilus-trader 1.220.0` in this repo's
`.venv` while drafting. Reading the wheel rather than the docs is the technique the Epic 1 retro
named (Key Insight #5). Do not re-derive these; do re-confirm anything that looks surprising.

**1 — Constructing a Redis-backed cache against an unreachable Redis hangs forever. This is the
single most important fact in this story.**

```
IMPORTED
CONFIG BUILT, constructing adapter...
EXIT CODE: 124        # timeout(1) killed it — 45s with no output, no exception, no traceback
```

`CacheDatabaseAdapter.__init__` is where it blocks, and `DatabaseConfig(timeout=2)` does **not**
bound it. That constructor is not optional or deferred: `NautilusKernel.__init__` calls it eagerly
whenever `config.cache.database` is set (`system/kernel.py:300-312`), and `TradingNode(config=...)`
constructs the kernel. So the moment Task 4 lands, `build_trading_node()` with a Redis-backed cache
and Redis down produces a process that prints nothing and never returns — indistinguishable, to an
operator, from a slow IB gateway. AC #6 exists for this, and it is the same shape as Epic 1 retro
Action Item #10 ("apply a `--connect-timeout`-shaped bound around `node.build()`").

Reproduce it yourself before writing the preflight; it takes 40 seconds and it is worth seeing:

```bash
timeout 45 uv run python -u -c "
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.config import CacheConfig, DatabaseConfig
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.serialization.serializer import MsgSpecSerializer
import msgspec
cfg = CacheConfig(database=DatabaseConfig(type='redis', host='127.0.0.1', port=6399, timeout=2))
print('constructing...', flush=True)
CacheDatabaseAdapter(trader_id=TraderId('PAPER-a1b2c3d4'), instance_id=UUID4(),
    serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True), config=cfg)
print('constructed', flush=True)"; echo "EXIT: $?"
```

**2 — There is no Redis client in this project, and there must not be one.**
`import redis` → `ModuleNotFoundError`. `uv.lock` has no `redis` entry; `pyproject.toml` has no
`redis` dependency. Nautilus's Redis client is Rust-side (`nautilus_pyo3.RedisCacheDatabase`,
`cache/database.pyx:160`) and exposes no reachability probe. AR3 says zero new dependencies and
architecture.md:636 says the phase adds none. **The preflight must therefore be stdlib `socket`.**
`pyproject.toml` is additionally hook-blocked against direct editing.

**3 — Three `CacheConfig` fields decide whether AC #4 is true, and all three are silent defaults.**
Read from `nautilus_trader/cache/config.py:57-68`:

| Field | Default | What it does to this story |
|---|---|---|
| `use_trader_prefix` | `True` | Keys get a `trader-` prefix. Needed for the namespace to be readable/greppable. |
| `use_instance_id` | `False` | **Load-bearing.** `instance_id` is a fresh `UUID4` per process (`TradingNodeConfig.instance_id` is `None` by default). Flip this to `True` and every restart writes to a brand-new namespace — AC #4 becomes false with no error anywhere. |
| `flush_on_start` | `False` | **Load-bearing.** `True` wipes the namespace on every start (`system/kernel.py:1245`). AC #4 becomes false, loudly-in-Redis and silently-in-code. |

The whole of "a restarted process rejoins its own state" rests on two third-party defaults staying
put across upgrades. That is exactly the situation `live_node_builder.py:235-241` already refuses to
tolerate for `market_data_type` / `use_regular_trading_hours`, in a comment worth re-reading:
*"A data-integrity property that depends on a third-party default is one upgrade away from changing
silently."* Same reasoning, same treatment — hence AC #7.

**4 — `TraderId.get_tag()` splits on the LAST hyphen, and client order IDs embed the result.**
Executed:

```
'PAPER-a1b2c3d4'       -> OK  | get_tag() = a1b2c3d4
'PAPER-a1b2c3d4-e5f6'  -> OK  | get_tag() = e5f6          # <-- everything before the last '-' is gone
'paper-a1b2c3d4'       -> OK  | get_tag() = a1b2c3d4      # lowercase is accepted; no normalisation
```

`ClientOrderIdGenerator` builds `O-{YYYYMMDD-HHMMSS}-{trader_tag}-{strategy_tag}-{count}` where
`trader_tag = trader_id.get_tag()` (`common/generators.pyx:40, 145-151`). So AR23's session-stable
client order IDs are stable **only through the tag**, not through the whole `trader_id`. Consequence:
the short session ID must contain **no hyphen**. `str(uuid)` has four of them and would reduce the
tag to the UUID's last 12 hex characters via a path that raises nothing — use `uuid.hex`, never
`str(uuid)`. The unit test in Task 2 asserts the round trip through `get_tag()` precisely because a
`str()`-vs-`.hex` slip produces a *valid* `TraderId` and a wrong tag.

**5 — `REDIS_URL` already exists in config and is consumed by nothing.**
`.env.example:61` sets `REDIS_URL=redis://localhost:6379` and `docker-compose.yml:80` passes
`REDIS_URL: redis://redis:6379` into the app service. A repo-wide grep for `redis` across `src/` and
`tests/` returns **zero** hits — it is dead config that predates this phase, and `Settings`'
`extra: "ignore"` swallows it. AR11 asks for host/port/db fields, not a URL, so introducing
`REDIS_HOST`/`REDIS_PORT`/`REDIS_DB` alongside it is correct. Two consequences:

- **Under Docker the new default is wrong.** `redis_host` defaults to `127.0.0.1`, which inside the
  app container is the app container. The compose app service needs `REDIS_HOST: redis`. That file
  is *not* hook-protected — add the line.
- **Leave `REDIS_URL` alone.** Removing it means editing `.env.example` (blocked) for no functional
  gain. Note it in `deferred-work.md` instead.

**6 — `load_state`/`save_state` are a different mechanism and are not this story's lever.**
`TradingNodeConfig` defaults: `cache=None`, `load_state=False`, `save_state=False`,
`environment=Environment.LIVE`, `instance_id=None`, `timeout_connection=60.0`,
`timeout_reconciliation=30.0`, `timeout_post_stop=10.0`. `load_state` gates `Trader.load()`
(`system/kernel.py:484`), which drives each strategy's `on_load(state)` user dictionary — **not**
orders, positions, accounts or instruments. Those come back from the cache database regardless. This
repo's strategies implement no `on_save`/`on_load`, so flipping either flag would add nothing and
change shutdown behaviour. Leave both at `False` and say why in the docstring.

**7 — The payoff of AC #4 is concrete and worth naming: the client-order-ID counter survives.**
`Strategy.on_start` reads `self.cache.client_order_ids(strategy_id=self.id)` and calls
`self.order_factory.set_client_order_id_count(len(...))` (`trading/strategy.pyx:353-368`). With an
empty cache that count restarts at zero and a restarted session regenerates client order IDs it has
already used — which is precisely the duplicate-order failure NFR6 forbids and AR23 says is
"inherited, not reinvented". The inheritance only works if the cache is durable *and* namespaced to
this session. That is this story, in one sentence.

**8 — Nanosecond precision is not at risk, despite the warning in the wheel.**
`CacheDatabaseAdapter`'s docstring warns that Redis stores int64 to 17 digits and 19-digit
nanosecond timestamps lose 2. The kernel already defuses it: `MsgSpecSerializer(...,
timestamps_as_str=True,  # Hard-coded for now)` at `system/kernel.py:307-310`. No action; recorded so
the warning does not cost the next reader an hour.

### The design

#### `derive_trader_id` — pure, framework-free, one line of policy

```python
def derive_trader_id(session_id: UUID) -> str:
    """PAPER-<first 8 hex chars of the session UUID>."""
```

Three properties, each deliberate:

- **Takes a `UUID`, not a `str`.** A `str` parameter invites passing a session *name*, which would
  produce a plausible-looking `trader_id` for the wrong session and silently bind a session to
  another one's Redis namespace. Reject non-`UUID` loudly.
- **`.hex`, never `str()`.** See *Pre-verified findings* #4.
- **`PAPER` is hardcoded, not configurable.** It doubles as a safety signal: every client order ID
  this system ever produces carries `PAPER` in it, visible in TWS and in IBKR's own order log. A
  configurable prefix would let a misconfiguration erase that signal. Real-money crossing (AR15) is
  designed but unexercised; when it is exercised, that story owns the prefix question.

#### Where the code lives, and why not in `live_node_builder.py`

`src/core/live_node_builder.py` is **493 lines**. `CLAUDE.md` and `project-context.md:104` both cap
files at 500. Adding a settings translation, a socket preflight, an exception class and their
docstrings blows through it. The architecture's delta tree annotates `live_node_builder.py` with
"Redis CacheConfig" (`architecture.md:504-506`), which this satisfies in substance: the builder is
where the cache reaches the node, it just is not where the cache is *built*. Two new modules:

| Module | Imports Nautilus? | Imports SQLAlchemy? | Why separate |
|---|---|---|---|
| `src/core/live_trader_id.py` | **No** | **No** | `SessionService` may never import Nautilus (AR38), and Story 2.8's `live status` will want to show a session's `trader_id`. Keeping the derivation framework-free means every layer can reach it. |
| `src/core/live_cache.py` | Yes (`CacheConfig`, `DatabaseConfig`) | No | Settings→config translation plus the preflight. |

Both take the `live_` prefix the architecture fixes for this phase (`architecture.md:384`).

#### Public surface

```python
# src/core/live_trader_id.py
TRADER_ID_PREFIX: str = "PAPER"
SHORT_SESSION_ID_LENGTH: int = 8
def derive_trader_id(session_id: UUID) -> str: ...

# src/core/live_cache.py
class RedisUnreachableError(Exception): ...
def build_cache_config(settings: RedisSettings) -> CacheConfig: ...
def check_redis_reachable(settings: RedisSettings, *, timeout: float = 2.0) -> None: ...

# src/core/live_node_builder.py  (MODIFIED — keyword-only, defaulted, additive)
def build_trading_node_config(..., cache: CacheConfig | None = None) -> TradingNodeConfig: ...
def build_trading_node(..., cache: CacheConfig | None = None) -> TradingNode: ...
```

`cache` **must** be keyword-only and defaulted to `None`, for three verified reasons:

- Every existing caller keeps working untouched — `live_check_driver.py:113`
  (`node_factory: NodeFactory = build_trading_node`), `test_live_node_lifecycle.py:120,153`, and six
  call sites in `test_epic1_ac_data.py`. `NodeFactory` is `Callable[..., TradingNode]`
  (`live_check_driver.py:85`), so an *optional* added parameter type-checks; a required one would
  not change the alias but would break every one of those call sites at runtime.
- A `None` cache means an in-memory Nautilus cache — which is exactly what `ntrader live check`
  wants. A diagnostic that answers "can I reach the broker?" must not start requiring Redis.
- It keeps this story's blast radius to additions only, which is what lets Task 4 assert that every
  pre-existing test in `test_live_node_builder.py` still passes unmodified.

#### Ordering inside `build_trading_node`

`build_trading_node_config` already guarantees that nothing which can fail runs after a client config
exists. Extend the same discipline one step outward: the Redis preflight runs **before**
`TradingNode(config=...)`, because that constructor is the thing that hangs. Concretely — gate
refusal → config validation → market data → *Redis preflight* → `TradingNode(...)`. Assert the
ordering in a test, not just the outcome; the module's existing gate-ordering tests show the shape.

#### `redis_db` has nowhere to go

`DatabaseConfig`'s full field list at 1.220.0 is `type, host, port, username, password, ssl, timeout`.
There is **no** database-index field, and the whole config is msgpack-encoded straight into Rust
(`cache/database.pyx:160-164`), so there is no side channel either. AR11 names `db`, so the field
ships — but a value we cannot honour must not be silently dropped. See *Judgment call #3*.

#### Why no `SessionService` involvement

The derivation needs only a `UUID`. Routing it through `SessionService` would give the service a
reason to know about Nautilus identifiers, which AR38 forbids in spirit even though a bare string
would not technically import Nautilus. Story 2.5's runner already holds the `TradingSession` row it
gets back from `SessionService.resolve()`; it calls `derive_trader_id(row.session_id)` itself.

### Files

| Path | Change |
|---|---|
| `src/config.py` | **MOD** — `+RedisSettings`; `+Settings.redis` |
| `src/core/live_trader_id.py` | **NEW** — `TRADER_ID_PREFIX`, `SHORT_SESSION_ID_LENGTH`, `derive_trader_id` |
| `src/core/live_cache.py` | **NEW** — `RedisUnreachableError`, `build_cache_config`, `check_redis_reachable` |
| `src/core/live_node_builder.py` | **MOD** — `cache=` on both entry points; preflight call; docstring |
| `docker-compose.yml` | **MOD** — `+REDIS_HOST: redis` on the app service |
| `docs/agent/nautilus.md` | **MOD** — "Live session cache" note (AC #5) |
| `tests/unit/core/test_live_trader_id.py` | **NEW** — determinism, distinctness, `get_tag()` round trip, import purity |
| `tests/unit/test_config.py` | **MOD** — `RedisSettings` defaults, env override, `redis_db` refusal |
| `tests/component/core/test_live_cache.py` | **NEW** — cache config fields by name, bounded preflight failure |
| `tests/component/core/test_live_node_builder.py` | **MOD** — `cache=` plumbing, preflight-before-construction ordering |
| `tests/integration/core/test_live_cache_namespace.py` | **NEW** — persistence across adapters, isolation between trader IDs |
| `_bmad-output/implementation-artifacts/deferred-work.md` | **MOD** — `## Deferred from: story-2.4` |

Do **not** add either new module to `src/core/__init__.py` — consumers import the full dotted path,
which is the convention `live_gate`, `live_market_data` and `live_node_builder` all follow.

### Testing standards

- **Tier boundaries are decided by what the test imports, and they are not negotiable here.**
  - `test_live_trader_id.py` → **unit**. Zero Nautilus imports (the `TraderId` round-trip assertion
    is the one exception and belongs in the component tier if you would rather keep the unit tier
    literally Nautilus-free — either placement is defensible; pick one and say which in the
    docstring).
  - `test_live_cache.py` → **component**. It imports `nautilus_trader.config`, which
    `pytest.ini:25` defines the unit tier to exclude. Copy the module docstring and the
    `_assert_c_logging_state_is_unchanged` autouse fixture from
    `tests/component/core/test_live_node_builder.py:1-70` verbatim — that fixture asserts on the
    *delta*, not the absolute state, for a reason documented in place.
  - `test_live_cache_namespace.py` → **integration**, `--forked`, and skipped when Redis is
    unreachable. It constructs a real Rust-backed cache adapter.
- **Never construct a `TradingNode` outside the integration tier.** `NautilusKernel.__init__` claims
  the C logging subsystem, and the component tier runs `-n auto` unforked.
- **Mark everything.** `--strict-markers` is on. `pytest.ini` is the effective config;
  `pyproject.toml`'s marker list is shadowed and dead.
- **Naming.** Long behavioural sentences
  (`test_two_different_sessions_never_share_a_redis_namespace`), not `test_<method>_<case>`.
  Class-per-concern, docstring naming the AC.
- **Assert the timeout, not just the exception.** The defect AC #6 guards is an unbounded hang. A
  test that only asserts `pytest.raises(RedisUnreachableError)` passes just as happily against an
  implementation that takes four minutes to get there.
- **Mutation-test the two load-bearing defaults** (Epic 1 retro Action Item #3). Flip
  `use_instance_id=True`, confirm Task 5's rejoin assertion fails, revert. If it does not fail, the
  test is decorative.
- **CI coverage.** Verified: `.github/workflows/ci.yml:168` and `:238` `--ignore=tests/integration/db`
  — `integration/db`, **not** `integration/core`. Task 5's file therefore *does* run in CI, which
  makes the "skip cleanly when Redis is unreachable" requirement load-bearing rather than a courtesy:
  CI has no Redis service. CI's coverage job runs `--cov=src --cov-fail-under=64`, so new uncovered
  lines in `src/core/` **do** move the gate.
- **Do not call `StrategyRegistry.clear()`.** Process-global; `make test-unit` runs `-n auto`.

### Judgment calls made while writing this story (flag at the Epic 2 retro)

1. **Two new modules instead of extending `live_node_builder.py`.** Forced, not preferred:
   the file is 493 lines against a 500-line cap. The architecture's delta tree puts "Redis
   CacheConfig" in `live_node_builder.py`; the split honours that in substance (the builder is still
   where the cache reaches the node) while keeping both files inside the size rule. The
   `live_trader_id` / `live_cache` split is a second, separate call: it keeps the derivation
   importable from Nautilus-free layers (AR38).
2. **`trader_id` is derived on demand, never stored.** No column, no migration — which matters,
   because the phase's single migration is already spent. AR10's word is "derived", and a stored copy
   would introduce the possibility of a row whose stored `trader_id` disagrees with its own
   `session_id`. The cost is that `live status` must call `derive_trader_id` rather than read a
   column; that is one import.
3. **A non-zero `REDIS_DB` is refused, not ignored.** `DatabaseConfig` has no database-index field at
   1.220.0 (*The design*, above), so a `REDIS_DB=1` an operator sets to isolate something would have
   no effect whatsoever. Silently dropping a config value in a system whose entire theme is "two
   sessions must never contaminate each other" is the wrong failure mode, even though `trader_id`
   namespacing already provides the isolation the operator was reaching for. The field ships because
   AR11 names it; the validator explains why only `0` is accepted and points at the Nautilus
   limitation by version. The alternative — omitting the field — was rejected because it silently
   under-delivers AC #1.
4. **8 hex characters, per AR10's "short", with the collision bound disclosed rather than
   engineered away.** 8 hex = 2³² values; the birthday bound is ~1 in 10⁷ at 100 sessions and ~1 in
   10⁴ at 1000. `TraderId` would accept the full 32-char hex and remove the risk by construction, but
   AR10 says *short*, the tag appears in every client order ID an operator reads in TWS, and this
   project will not run thousands of concurrent sessions. Recorded in `deferred-work.md` with the
   arithmetic so a future high-volume story can revisit it rather than rediscover it. If you disagree
   and want 32, that is a legitimate call — make it explicitly and say so in the completion notes.
5. **The preflight is `socket`, not `redis-py`.** AR3 forbids new dependencies and
   `pyproject.toml` is hook-blocked. A ~15-line stdlib probe is the whole cost; a dependency for it
   would be the larger change.
6. **`build_cache_config` takes `RedisSettings`, not `Settings`.** Narrowest possible parameter,
   matching the way `live_node_builder` takes `IBKRSettings` rather than `Settings`. Injected, never
   fetched — neither new module calls `get_settings()`.
7. **AC #6 and AC #7 were added to the epic's list.** See the ⚠️ above AC #6. Same move Story 2.3
   made with its AC #6, and for the same reason: the epic's criteria are individually right and
   jointly leave a demonstrated hole.

### Deferred items this story reads

**Closes:** none outright.

**Reads and resolves:**

- `deferred-work.md`, *"Deferred from: story-2.2"* — **`SessionSpec.model_copy(update=...)` bypasses
  validation**, re-pointed at this story with the note *"revisit once the durable engine cache gives
  a second code path that reads a `SessionSpec` back into memory — that is the first place a
  `model_copy`-derived spec could plausibly originate from."* **It does not.** This story reads a
  `UUID` and `RedisSettings`; no `SessionSpec` is constructed, read or round-tripped anywhere in it.
  The predicted second path is Story 2.5's runner, which materialises the stored spec to build
  strategies. Re-point it there, with that reasoning recorded — do not strike it through.

**Reads and leaves open:**

- `deferred-work.md`, *"Deferred from: story-2.1"* — re-validating a persisted spec against today's
  param model is lossy. Untouched; no spec is read here. Still pointed at Epic 5.
- `deferred-work.md`, *"Deferred from: story-2.3"* — `live_check.classify_failure` maps exit codes by
  exception *class name*. `RedisUnreachableError` is a **new typed failure on the live path** and
  will classify generically until a CLI story maps it. Nothing on the live path calls it yet, so this
  is harmless today. **Action for Story 2.5:** map it deliberately. Exit code **1** is wrong-ish and
  **4** is arguable — AR28 defines 4 as "broker connectivity failure" and Redis is not the broker.
  Recommend **1**, with the reasoning that 4 must stay scriptably specific to IBKR. Decide there,
  not here.
- `deferred-work.md`, *"Deferred from: code review of story-2.2"* — both trading-session repositories
  have zero CI-gating coverage. Untouched by this story; owner stays the Epic 2 retro.

Epic 1 retro Action Items **4–10** remain Story 2.5 constraints. Item **#10** ("apply a
`--connect-timeout`-shaped bound around `node.build()`") is the direct ancestor of this story's
AC #6 — read it before writing the preflight; the two are the same defect at two layers.

### Project Structure Notes

- **`session` is a crowded namespace.** `src/db/session.py` is the async SQLAlchemy engine,
  `src/db/session_sync.py` its sync twin, `src/models/session.py` the domain spec,
  `src/db/models/trading_session.py` the ORM row. Inside any method holding a SQLAlchemy `Session`,
  name the row `trading_session`, never `session`. And `trades.session_id` is a `BigInteger` FK to
  `trading_sessions.id`, while `trading_sessions.session_id` is the UUID business key — same name,
  different types. This story only ever wants the UUID.
- **Vocabulary is normative (AR36).** *session*, *process run*, *seal*, *stop*, *gate*. Not *pause*,
  *halt*, *kill*, *close*, *finalize*. A restart is a new **process run** of the same **session** —
  that distinction is the whole point of this story and the docstrings should use it precisely.
- **Docstring dialect:** the Epic 2 one — a module header opening *"Owns: … Does not own: …"* that
  states the import-purity rule explicitly (`src/models/session.py:1-52`,
  `src/core/live_node_builder.py:1-20`), Google-style `Args`/`Returns`/`Raises`, double-backtick
  inline code, keyword-only arguments. **Do not overstate what the code protects** — the recurring
  documentation failure the Epic 1 retro named, caught by code review every time. AC #5 is the trap
  here: the correct docstring says Redis *is treated as* disposable and that **Epic 4** enforces it.
- **Settings conventions:** field names carry their own prefix (`ibkr_host`, not `host`), so env vars
  fall out as `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB`. Copy the `model_config` block verbatim from a
  sibling class. `hide_input_in_errors` is on `IBKRSettings` for a reason (a Story 1.2 security fix —
  pydantic-settings renders the whole environment into validation tracebacks); include it on
  `RedisSettings` too, since `RedisSettings` has a validator that can fire.
- **Import gate:** F401/F821 hard-block the commit at three points. Make an import and its first use
  in a **single** edit. `make install-hooks` once per clone.
- **Commits:** `<type>(<scope>): <subject>`; `feat(live):` fits. Stage and commit in **separate** Bash
  calls. Never reference AI or Claude.

### References

- [Source: `_bmad-output/planning-artifacts/epics.md#Story 2.4`] — lines 851–886, the ACs and the
  cross-epic ordering note
- [Source: `_bmad-output/planning-artifacts/epics.md`] — AR3 (:179), AR10 (:189), AR11 (:190),
  AR23 (:211), AR28 (:219), AR36 (:236), AR38 (:238), AR39 (:239); FR19 (prd.md:827)
- [Source: `_bmad-output/planning-artifacts/architecture.md#D2`] — :239–248, Redis-backed cache,
  session-scoped namespace
- [Source: `_bmad-output/planning-artifacts/architecture.md#Delta Project Tree`] — :493–506
  (`config.py` `+RedisSettings`; `live_node_builder.py` "Redis CacheConfig")
- [Source: `_bmad-output/planning-artifacts/architecture.md`] — :351–352 (D2 before D7, E2 before
  E3), :384–392 (naming), :460–472 (enforcement guidelines), :636 (zero new dependencies)
- [Source: `_bmad-output/implementation-artifacts/epic-1-retro-2026-08-17.md`] — :174–195 (Epic 2
  dependencies), :240–262 (Action Items #3 and #10)
- [Source: `_bmad-output/implementation-artifacts/2-3-move-a-session-between-states-through-one-guarded-path.md`]
  — :310–425 (the pre-verified-findings convention), :586–609 (testing standards), :611–657
  (judgment-call convention)
- [Source: `_bmad-output/implementation-artifacts/deferred-work.md`] — the `model_copy` item pointed
  at this story; the `classify_failure` item
- [Source: `nautilus_trader/cache/config.py:37-68`] — `CacheConfig` fields and their defaults
- [Source: `nautilus_trader/common/config.py` `DatabaseConfig`] — full field list; **no** `db` field
- [Source: `nautilus_trader/system/kernel.py:298-312`] — cache-database construction, eager
- [Source: `nautilus_trader/system/kernel.py:484`] — `load_state` → `Trader.load()`
- [Source: `nautilus_trader/system/kernel.py:1245`] — `flush_on_start`
- [Source: `nautilus_trader/cache/database.pyx:131-186, 891`] — adapter constructor, `add`, `keys`,
  `flush`
- [Source: `nautilus_trader/common/generators.pyx:40, 117-152`] — `get_tag()` and the client order ID
  format
- [Source: `nautilus_trader/trading/strategy.pyx:353-368`] — client-order-ID count restored from cache
- [Source: `src/core/live_node_builder.py:145-270, 333-390`] — the two entry points to extend;
  :235–241 for the explicit-over-default precedent
- [Source: `src/config.py:20-161, 283-376`] — `IBKRSettings` shape, `Settings` composition
- [Source: `src/db/models/trading_session.py:57-59`] — `session_id` is a `PG_UUID(as_uuid=True)`
- [Source: `src/services/session_service.py:233-259`] — `resolve()`, which Story 2.5 pairs with
  `derive_trader_id`
- [Source: `tests/component/core/test_live_node_builder.py:1-70`] — tier justification + the
  C-logging-delta autouse fixture to copy
- [Source: `tests/unit/models/test_session_spec.py:438-486`] — the import-purity test template
- [Source: `docker-compose.yml:26-41, 79-80`] — the `redis:7-alpine` service and the dead `REDIS_URL`
- [Source: `.claude/hooks/protect-files.sh:21`] — `.env*` is blocked

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m]

### Debug Log References

- `CacheDatabaseAdapter.__init__` against an unreachable Redis: `timeout 45 ... ; EXIT: 124` —
  reproduced the story's *Pre-verified finding* #1 before writing the preflight.
- `check_redis_reachable("127.0.0.1", 6379)` against the live Redis: `redis.reachable` logged in
  0.001s. This is the debug event that only fires when the reply starts with `+PONG`, so the inline
  RESP `PING` handshake is confirmed working — Task 3's one un-pre-verified item is closed.
- Namespace probe against live Redis: keys land as
  `trader-PAPER-aaaa1111:general:story24:probe`; a second adapter on the same trader id sees the
  key, a different trader id sees `[]`.
- Mutation run (`.hex` → `str()` in `derive_trader_id`): **5 tests failed**, including
  `test_the_tag_contains_no_hyphen` (`assert '-' not in '39267514-3b04-'`). Reverted; 16/16 green.

### Completion Notes List

All 7 ACs are satisfied. Three things went differently from the plan and are the items a reviewer
should look at first.

**1 — `check_redis_reachable` takes `host`/`port`, not `RedisSettings`.** The story specified
`check_redis_reachable(settings: RedisSettings, ...)`. That is wrong on contact with `build_trading_node`,
which takes `IBKRSettings` and has no `RedisSettings` to hand. More importantly, a settings-shaped
preflight can check a *different* Redis than the one the node will use, since the `CacheConfig` may
have been built from a different settings object. The signature now takes the values the node
actually connects to — `cache.database.host` / `cache.database.port` — so the preflight and the
connection cannot drift. `None` falls back to loopback/6379, mirroring what `DatabaseConfig`
documents its own `None` to mean.

**2 — `read_ibkr_connection_status` moved to a new `src/core/live_connection_probe.py`.** Adding the
cache seam took `live_node_builder.py` to **515 lines**, past this repo's 500-line limit — the exact
risk the story's *Judgment call #1* flagged, realised. The connection reader was the part of that
module least about assembling a node, and its suite was already named
`tests/component/core/test_live_connection_probe.py`. Pure move, no behaviour change, one import line
updated in that suite, plus a docstring cross-reference in `live_connection_monitor.py`. No
production module imported it (verified by grep — only the test did). `live_node_builder.py` is now
**416** lines and `live_connection_probe.py` is **130**. This is a Story 1.6 surface touched by a
Story 2.4 task; call it out at review if the split is unwanted.

**3 — an existing Epic 1 acceptance test caught the new `socket` import, correctly.**
`test_no_new_dependency_was_added_for_the_live_path` (AC 1.3h) enumerates every third-party module
the live path imports and refuses anything undeclared. `socket` is stdlib, but the guard's
`_STDLIB_AND_FIRST_PARTY` set is deliberately hand-curated, so it failed. That is the guard working:
it forced an explicit confirmation that AR3 still holds. Added `socket` to the set **with the reason
inline** rather than switching the guard to `sys.stdlib_module_names` — an auto-derived allowlist
would have waved this through silently, and on a "zero new dependencies" gate for a trading system
the manual step is the value. 40/40 Epic 1 acceptance criteria pass again.

Smaller findings worth carrying forward:

- **Cache writes are asynchronous.** `add()` followed by `keys()` on the same open adapter returns
  `[]`; `close()` is what makes the write readable. This failed the namespace test on first run and
  is now documented in the test and in `docs/agent/nautilus.md`. Epic 3 and 4 will hit it.
- **`CacheDatabaseAdapter.flush()` is `FLUSHDB`** — it clears the whole database, not the trader's
  namespace. The integration tests therefore never call it; they derive trader IDs from a fresh
  `uuid4()` per test instead, so no leftover key can make a later run pass falsely.
- **The AC #7 mutation proof is a test, not a manual procedure.**
  `TestTheNamespaceGuaranteeIsLoadBearing` builds a config with `use_instance_id=True` by hand and
  asserts the rejoin *fails*. Epic 1 retro Action Item #3 asked for mutation testing as a named
  verification step; encoding it as a test means it re-runs every build rather than living only here.
- **The deferred `model_copy` item is resolved as not-triggered.** Story 2.2 predicted this story
  would give a second code path that reads a `SessionSpec` back into memory. It does not — nothing
  here constructs a `SessionSpec`. Re-pointed at Story 2.5's runner in `deferred-work.md`.

Not done, and deliberately: `.env.example` gained no `REDIS_HOST`/`REDIS_PORT`. The file is blocked
by `.claude/hooks/protect-files.sh:21` and the story said to ask rather than work around it. The
defaults are correct for a local run, so this is a documentation gap only; it is recorded in
`deferred-work.md`.

**Verification run (all green):**

| Gate | Result |
|---|---|
| `ruff check .` | All checks passed |
| `mypy src/core src/services` | Success: no issues in 93 source files |
| `pytest tests/unit -n auto` | **1950 passed** |
| `pytest tests/component -n auto` | **1055 passed**, 16 pre-existing skips |
| `pytest tests/integration/core --forked` | **68 passed**, 2 pre-existing skips, **40/40** Epic 1 ACs |
| `pytest tests/e2e tests/api` | **115 passed** |
| New tests added | **59** — 15 `test_redis_settings` + 16 `test_live_trader_id` + 17 `test_live_cache` + 7 `TestRedisCacheWiring` + 4 `test_live_cache_namespace` |
| Redis-down skip path | verified — `REDIS_PORT=6399` skips all 4 with an actionable reason |
| File size limits | `live_node_builder.py` 416, `live_cache.py` 215, `live_trader_id.py` 84, `live_connection_probe.py` 130 — all < 500 |

### File List

| Path | Change |
|---|---|
| `src/config.py` | MOD — `+RedisSettings` (host/port/db, two validators); `+Settings.redis` |
| `src/core/live_trader_id.py` | NEW — `TRADER_ID_PREFIX`, `SHORT_SESSION_ID_LENGTH`, `derive_trader_id` |
| `src/core/live_cache.py` | NEW — `RedisUnreachableError`, `build_cache_config`, `check_redis_reachable` |
| `src/core/live_connection_probe.py` | NEW — `read_ibkr_connection_status` + 2 helpers, moved verbatim from `live_node_builder` |
| `src/core/live_node_builder.py` | MOD — `cache=` on both entry points, preflight before `TradingNode(...)`, probe removed, docstring |
| `src/core/live_connection_monitor.py` | MOD — docstring cross-reference to the probe's new home |
| `docker-compose.yml` | MOD — `+REDIS_HOST`, `+REDIS_PORT` on the app service |
| `docs/agent/nautilus.md` | MOD — `+## Live Session Cache (Redis)` (AC #5) |
| `tests/unit/test_redis_settings.py` | NEW — defaults, env overrides, range + `redis_db` refusal, nesting |
| `tests/unit/core/test_live_trader_id.py` | NEW — determinism, distinctness, format, wrong-input, import purity |
| `tests/component/core/test_live_cache.py` | NEW — cache config fields by name, bounded preflight, `get_tag()` round trip |
| `tests/component/core/test_live_node_builder.py` | MOD — `+TestRedisCacheWiring` (7 tests) |
| `tests/component/core/test_live_connection_probe.py` | MOD — import updated to the probe's new module |
| `tests/integration/core/test_live_cache_namespace.py` | NEW — rejoin, namespacing, isolation, mutation proof |
| `tests/integration/core/test_epic1_ac_node.py` | MOD — `socket` added to `_STDLIB_AND_FIRST_PARTY` with its reason |
| `_bmad-output/implementation-artifacts/deferred-work.md` | MOD — `## Deferred from: story-2.4` |

## Change Log

| Date | Change |
|---|---|
| 2026-08-19 | Story created — `backlog` → `ready-for-dev`. |
| 2026-08-19 | Implemented Tasks 1–7 TDD Red→Green. `RedisSettings`, deterministic `trader_id`, Redis `CacheConfig` + bounded reachability preflight, node-builder seam, live-Redis namespace proof. `read_ibkr_connection_status` extracted to `live_connection_probe.py` to stay inside the 500-line file limit. All gates green; 40/40 Epic 1 ACs still pass. `in-progress` → `review`. |
