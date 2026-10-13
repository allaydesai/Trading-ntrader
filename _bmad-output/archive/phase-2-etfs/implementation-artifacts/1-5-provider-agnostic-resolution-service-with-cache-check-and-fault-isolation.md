# Story 1.5: Provider-Agnostic Resolution Service with Cache-Check & Fault Isolation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want an `InstrumentMetadataService` that resolves a ticker cache-first through a provider-agnostic interface and isolates per-ticker failures,
so that later asset classes reuse it unchanged and a single ticker failure never blocks the batch.

## Acceptance Criteria

1. **Given** the service interface, **When** `InstrumentMetadataService.resolve(ticker)` is called, **Then** it returns an `InstrumentMetadata` domain model only — no FMP types or raw JSON cross the boundary.
2. **Given** an existing `instrument_metadata` row with `resolution_status = RESOLVED`, **When** `resolve(ticker)` is called, **Then** the FMP API is NOT called (cache hit) and metadata is returned from the store.
3. **Given** no cached row for the ticker, **When** `resolve(ticker)` is called, **Then** the `FMPMetadataProvider` is invoked and the result is upserted into `instrument_metadata`.
4. **Given** the provider raises or degrades for one ticker, **When** a batch is resolved, **Then** the degraded result is recorded and resolution of the remaining tickers continues uninterrupted (per-ticker fault isolation).
5. **Given** a hypothetical second provider, **When** it is added, **Then** consumers depend only on the interface + domain model, and the swap touches only `providers/` plus service wiring.

## Tasks / Subtasks

- [x] **Task 1: Extract the provider-agnostic interface** (AC: #1, #5) — *write `test_instrument_metadata_service.py` FIRST (TDD Red→Green)*
  - [x] Create `src/services/metadata/providers/base.py` defining a `MetadataProvider` **Protocol** (`typing.Protocol`) with the single method `def resolve(self, ticker: str) -> InstrumentMetadata: ...`. This is the seam Story 1.4 deliberately deferred ("Do NOT define a `Protocol`/ABC interface here. The provider-agnostic *interface* belongs to Story 1.5"). [Source: 1-4 story Dev Notes "Scope boundaries"; architecture.md:284-292 ADR-5]
  - [x] Add a one-line module docstring mirroring the package style. Import `InstrumentMetadata` from `src.models.instrument_metadata` (the **Pydantic domain** model) — annotate the Protocol method's return with it. Mark `@runtime_checkable` only if a test actually asserts `isinstance` (optional; structural typing does not require it).
  - [x] **Do NOT modify `FMPMetadataProvider`.** It already has `resolve(ticker) -> InstrumentMetadata` (Story 1.4, `done`) and therefore *structurally* satisfies the Protocol with zero changes — Protocols are duck-typed. Adding `class FMPMetadataProvider(MetadataProvider)` is unnecessary and would couple the adapter to the interface; leave it concrete. [Source: src/services/metadata/providers/fmp_provider.py:62-75]
  - [x] The service depends on the **Protocol** (`provider: MetadataProvider`), never on `FMPMetadataProvider` concretely — this is what makes AC #5 true (swap a provider, touch only `providers/` + the service-construction call site).

- [x] **Task 2: `InstrumentMetadataService` — cache-first resolve + ORM↔domain bridge** (AC: #1, #2, #3) — *tests FIRST (TDD)*
  - [x] Create `src/services/metadata/instrument_metadata_service.py`. NEW module in the existing `src/services/metadata/` package; **NOT** under `src/services/firstrate/`, and the name is `InstrumentMetadataService` — never reuse Phase 1's `src/services/firstrate/metadata_service.py::MetadataService` (catalog-instrument CRUD, a different thing). [Source: architecture.md:382-391; 476]
  - [x] `logger = structlog.get_logger(__name__)` (repo convention). [Source: fmp_provider.py:23; instrument_mapper.py:21]
  - [x] Define `class InstrumentMetadataService` with constructor injecting BOTH dependencies — they are the unit-test seams:
    - `def __init__(self, provider: MetadataProvider, repository: SyncInstrumentMetadataRepository) -> None`.
    - The **sync** repository (`src/db/repositories/instrument_metadata_repository_sync.py::SyncInstrumentMetadataRepository`) is correct: resolution is the **CLI import write-path** (Story 2.4). The async repo is for explorer *reads* and is out of scope here. [Source: architecture.md:472, 570; session_sync.py docstring]
    - Session lifecycle is the **caller's** responsibility (Story 2.4 opens `get_sync_session()` and constructs the repo). The service does not open sessions, commit, or close — keeping it pure-ish and unit-testable with a mocked repo. [Source: session_sync.py:98-134 `get_sync_session`]
  - [x] `def resolve(self, ticker: str) -> InstrumentMetadata`: the cache-first single-ticker path —
    1. `existing = self._repository.get_by_ticker(ticker)` → ORM row or `None`.
    2. **Cache hit (AC #2):** `if existing is not None and existing.resolution_status == ResolutionStatus.RESOLVED:` log a debug breadcrumb (`ticker`, `provider`, `cache_hit=True`) and `return self._to_domain(existing)` — **the provider (and thus FMP) is NOT called.** [Source: architecture.md:440-441 "Cache-check before fetch: skip the API call if a row exists with resolution_status=RESOLVED"]
    3. **Cache miss / re-attempt (AC #3):** otherwise call `domain = self._provider.resolve(ticker)`, stamp `domain.resolved_at = _utcnow()`, persist via `persisted = self._repository.upsert(self._to_orm(domain))`, and `return self._to_domain(persisted)`.
    - **`VENUE_UNRESOLVED` and `UNRESOLVED` rows are NOT cache hits** — only `RESOLVED` short-circuits. Re-attempting an unresolved row each run is intentional (the venue worklist; overrides are merged separately in Story 3.3). Document this in a comment so a future reader doesn't "optimize" it into a bug. [Source: architecture.md:440-441 — hit condition is `RESOLVED` *only*; epics.md#Story-1.5 AC2/AC3]
  - [x] **`resolved_at` clock — single site, deterministic (closes 1.4 open question #2).** Add a module-level `def _utcnow() -> datetime: return datetime.now(timezone.utc)` and call it only in `resolve`. The provider leaves `resolved_at=None` by design; the **service** stamps it at upsert. Mirror the ORM's `updated_at = datetime.now(timezone.utc)`. Unit tests assert `resolved_at is not None` (or `monkeypatch` `_utcnow` for an exact value). [Source: 1-4 story open question #2 + Completion Notes "resolved_at … stamped by Story 1.5's service"; db/models/instrument_metadata.py:65-69]
  - [x] **ORM↔domain bridge (this service owns it — the repos explicitly defer it).** Two private helpers:
    - `def _to_orm(self, domain: InstrumentMetadata) -> OrmInstrumentMetadata` — construct the SQLAlchemy ORM row from the Pydantic domain. Field-for-field copy EXCEPT: `asset_type = domain.asset_type.value if domain.asset_type is not None else None` (ORM column is `String`, domain is the `AssetType` enum). `resolution_status` passes through unchanged (ORM column is typed `Mapped[ResolutionStatus]`). [Source: db/models/instrument_metadata.py:53,59-63; models/instrument_metadata.py:86,92]
    - `def _to_domain(self, orm: OrmInstrumentMetadata) -> InstrumentMetadata` — inverse: `asset_type = AssetType(orm.asset_type) if orm.asset_type is not None else None`; everything else direct. Returns the **domain** model so no ORM/SQLAlchemy type leaks to consumers (AC #1). [Source: models/instrument_metadata.py:60-101]
    - **Import-alias discipline:** the ORM and domain classes share the name `InstrumentMetadata`. Import the ORM aliased, e.g. `from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata`, and keep `InstrumentMetadata` bound to the Pydantic domain model. Both model docstrings warn "never cross-import them unaliased." [Source: models/instrument_metadata.py:8-12; db/models/instrument_metadata.py:5-7]

- [x] **Task 3: Batch resolution with per-ticker fault isolation** (AC: #4) — *tests FIRST (TDD)*
  - [x] `def resolve_batch(self, tickers: Iterable[str]) -> list[InstrumentMetadata]`: iterate `tickers`, calling `self.resolve(t)` for each inside a `try/except Exception`. On **any** exception (provider raised, DB upsert failed, etc.): log `structlog.exception`/`error` with fields `ticker`, `provider`, `error` (str), append a degraded error record (see below) to the results, and **continue** — one ticker never aborts the rest. [Source: epics.md#Story-1.5 AC4; architecture.md:443-444 "never abort the whole import"; NFR8/NFR15]
  - [x] Degraded error record on exception: build an `InstrumentMetadata` with `ticker=t`, `metadata_provider=` the provider's name if discoverable else `"UNKNOWN"`, all descriptive fields = `NA_SENTINEL`, `venue=None`, `asset_type=None`, `ipo_date=None`, `resolution_status=ResolutionStatus.VENUE_UNRESOLVED`. Do NOT re-raise. (This mirrors `FMPMetadataProvider._degraded` but is the service's own last-resort guard for when the provider itself blew up rather than degrading cleanly.) [Source: fmp_provider.py:97-118 `_degraded`]
  - [x] **Note on the two degradation paths:** the FMP provider already returns a clean degraded record (never raises) for *FMP-specific* failures — empty `[]`, timeout, HTTP error (Story 1.3 `fetch_profile` → `None`, Story 1.4 `_degraded`). So in practice `resolve_batch`'s `except` catches only *unexpected* faults (a bug, a DB error, a future provider that does raise). Both paths satisfy AC #4 "raises **or** degrades." Do not duplicate the provider's graceful FMP handling in the service. [Source: 1-3 story AC #4/#5; 1-4 story `_degraded`; fmp_provider.py:70-75]
  - [x] Keep `resolve_batch` thin — it is a fault-isolating loop over `resolve`, not a second resolution implementation. No `ResolutionSummary` counting here (Story 1.6 aggregates the returned list). [Source: epics.md#Story-1.6; instrument_metadata.py:104-120 `ResolutionSummary`]

- [x] **Task 4: Unit tests — mocked provider + mocked repository, no network, no DB** (AC: #1–#5)
  - [x] Add `tests/unit/services/metadata/test_instrument_metadata_service.py`. `tests/unit/services/metadata/__init__.py` already exists (Story 1.3) — do not recreate. First line of the module body: `pytestmark = pytest.mark.unit` (required marker — a 1.4 review finding flagged missing unit markers). Mirror the AAA + `unittest.mock` idioms in `test_fmp_provider.py` / `test_fmp_client.py`. [Source: 1-4 Review Findings; tests/unit/services/metadata/test_fmp_provider.py:1-30]
  - [x] **Seams:** pass a stub/`MagicMock` provider whose `resolve` returns a canned domain `InstrumentMetadata` (or raises), and a `MagicMock(spec=SyncInstrumentMetadataRepository)` whose `get_by_ticker`/`upsert` are scripted. No httpx, no session, no `get_sync_session`. A `MagicMock(spec=...)` repo also guards against typo'd method names.
  - [x] Cases to cover:
    - **AC #2 (cache hit):** `get_by_ticker` returns an ORM row with `resolution_status=RESOLVED` → `provider.resolve` asserted **NOT called** (`provider.resolve.assert_not_called()`), `upsert` **NOT called**, and the returned object is the domain `InstrumentMetadata` mapped from that row (assert a field, e.g. `venue`, `asset_type` round-trips str→enum).
    - **AC #3 (cache miss):** `get_by_ticker` returns `None` → `provider.resolve` called once with the ticker, `upsert` called once, `resolved_at is not None` on the returned domain model.
    - **Re-attempt on non-RESOLVED cache:** `get_by_ticker` returns a row with `resolution_status=VENUE_UNRESOLVED` → provider **IS** called and `upsert` runs (assert the unresolved row is not treated as a hit).
    - **AC #1 (no leak / type contract):** `resolve(...)` returns an `isinstance(..., InstrumentMetadata)` (the **domain** import); assert the result is not the ORM type. Optionally assert the service was constructed with a `MetadataProvider`-typed arg (Protocol satisfied structurally).
    - **AC #4 (fault isolation — provider raises):** provider `resolve` raises for the 2nd of 3 tickers (e.g. `side_effect=[md_a, RuntimeError("boom"), md_c]`) → `resolve_batch(["A","B","C"])` returns 3 records, the middle one is the degraded `VENUE_UNRESOLVED`/all-`N/A` record, and `resolve` was still attempted for "C" (loop didn't abort). Assert the error was logged.
    - **AC #4 (fault isolation — provider degrades cleanly):** provider returns a `VENUE_UNRESOLVED` degraded record (no raise) for one ticker → it is recorded and the batch completes; no exception surfaces.
    - **AC #5 (provider swap):** construct the service with a second stub provider exposing the same `resolve(ticker) -> InstrumentMetadata` (a different `metadata_provider` value) → service behaves identically; no service code change needed. Demonstrates consumers bind to the Protocol, not FMP.
    - **ORM↔domain `asset_type` enum bridge:** a cache-hit ORM row with `asset_type="ETF"` → domain `asset_type == AssetType.ETF`; a cache-miss domain with `asset_type=AssetType.ETF` → the ORM handed to `upsert` has `asset_type == "ETF"` (string). Inspect the `upsert` call arg.

- [x] **Task 5: Verify** (AC: all)
  - [x] `make test-unit` green — new `test_instrument_metadata_service.py` plus full suite, no regressions (baseline **1021** unit tests after Story 1.4). [Source: 1-4 story Debug Log]
  - [x] `make lint` clean — mind the F401/F821 import gate: `datetime`, `timezone`, `Iterable`, `structlog`, `Protocol` (or `runtime_checkable`), domain `InstrumentMetadata`/`AssetType`/`ResolutionStatus`/`NA_SENTINEL`, `OrmInstrumentMetadata` (aliased), `SyncInstrumentMetadataRepository`, `MetadataProvider` — each used in the same edit that adds it. [Source: CLAUDE.md "structural import gate"]
  - [x] `make typecheck` clean — `mypy` targets `src/core src/services strategies`, so `instrument_metadata_service.py` and `providers/base.py` are both directly checked. Full annotations (`-> InstrumentMetadata`, `Iterable[str]`, `-> list[InstrumentMetadata]`, helper return types). [Source: 1-4 story Task 4; Makefile typecheck]
  - [x] Size limits: each file < 500 lines, `InstrumentMetadataService` < 100 lines, methods < 50, line length ≤ 100. [Source: CLAUDE.md Foundational Rules]

### Review Findings

- [x] [Review][Decision] Exception-path degraded records are returned but not persisted — Dismissed: return-only is sufficient; the batch result list is the intended recording behavior for this service-level fallback.
- [x] [Review][Patch] Provider identity is lost on service-built error records — Fixed: service-built degraded records now use a discoverable non-empty `PROVIDER_NAME`, falling back to `UNKNOWN`. [`src/services/metadata/instrument_metadata_service.py:129`]
- [x] [Review][Patch] Fault-isolation logging omits the provider field — Fixed: exception-path logging now includes `provider`. [`src/services/metadata/instrument_metadata_service.py:125`]
- [x] [Review][Patch] Fault-isolation test does not assert the required error log — Fixed: provider exception test now monkeypatches the logger and asserts the full structured log call. [`tests/unit/services/metadata/test_instrument_metadata_service.py:397`]
- [x] [Review][Patch] `MetadataProvider.resolve` docstring contradicts the raise-handling contract — Fixed: Protocol docstring now states callers isolate provider exceptions. [`src/services/metadata/providers/base.py:46`]

## Dev Notes

### Scope boundaries (do NOT do here)
- **Service + interface only.** No `venue_overrides.csv` load/merge (Story 3.3 / `venue_overrides.py`); no `ResolutionSummary` counting/reporting (Story 1.6); no CLI command wiring (Story 1.6 / 2.x); no `catalog_instruments` qualification sync (Story 3.1 / ADR-3); no import-pipeline integration (Story 2.4). [Source: architecture.md ADR-3/ADR-7; epics.md#Story-1.6, #Story-3.1, #Story-3.3, #Story-2.4]
- **Do NOT modify `FMPMetadataProvider`, `FMPClient`, `venue_map.py`, the domain/ORM models, or the repositories.** They are `done` (Stories 1.2–1.4). The service *consumes* them. The only new files are the Protocol and the service (+ its test). [Source: sprint-status.yaml:54-57]
- **No async service.** The async repo exists for explorer reads; the resolution write-path is sync (CLI). Do not add an async `resolve` variant in this story. [Source: architecture.md:472, 570]
- **No new DB columns, migrations, or settings.** Everything needed already exists (migration #10, `instrument_metadata` table + enum from Story 1.2). [Source: architecture.md:356-360]

### The provider boundary (ADR-5 — the whole point of this story)
```
import_service / explorer / backtest config            ← later consumers
  → InstrumentMetadataService.resolve(ticker)           ← THIS STORY (cache-check + ORM↔domain + clock)
      → MetadataProvider (Protocol)                     ← THIS STORY (the seam)
          → FMPMetadataProvider.resolve -> InstrumentMetadata   ← Story 1.4 (done; structurally satisfies it)
              → FMPClient.fetch_profile -> dict | None          ← Story 1.3 (done)
              → normalize_venue(label) -> str | None            ← Story 1.4 (done)
  → SyncInstrumentMetadataRepository.get_by_ticker / upsert     ← Story 1.2 (done; ORM rows)
```
**Contract:** no FMP types, raw JSON, or SQLAlchemy ORM instances cross out of `InstrumentMetadataService`. Consumers receive only the Pydantic domain `InstrumentMetadata`. Adding a second provider later touches only `providers/` + the one construction site. [Source: architecture.md:284-292, 522-531]

### The two `InstrumentMetadata` classes (alias or suffer)
| Name | Module | Kind | Role |
|---|---|---|---|
| `InstrumentMetadata` (domain) | `src/models/instrument_metadata.py` | Pydantic `BaseModel` | What `resolve` returns; what the provider produces |
| `InstrumentMetadata` (ORM) | `src/db/models/instrument_metadata.py` | SQLAlchemy `Base` | What the repository persists / returns |

Import the ORM **aliased** (`as OrmInstrumentMetadata`); keep `InstrumentMetadata` = domain. The service is the *only* place both meet — that is by design (ADR-3/ADR-5: the service owns ORM↔domain mapping; the repos' docstrings say so verbatim). [Source: db/repositories/instrument_metadata_repository_sync.py:6-9; models/instrument_metadata.py:8-12]

### Cache-check semantics (precise)
- **Hit ⇔ row exists AND `resolution_status == RESOLVED`.** Only then is the provider skipped. [Source: architecture.md:440-441]
- `None` row, `UNRESOLVED`, or `VENUE_UNRESOLVED` → **miss** → provider called, result upserted. Re-attempting `VENUE_UNRESOLVED` rows is intentional (they are the venue worklist; a later FMP run or override merge may resolve them). [Source: epics.md#Story-1.5 AC2/AC3; architecture.md ADR-7]
- The cache hit returns the **persisted** metadata mapped back to the domain model — not a fresh provider fetch — so `resolved_at`, `venue`, etc. come from the store. [Source: epics.md#Story-1.5 AC2]

### Field-mapping crib (ORM ↔ domain)
| Field | Domain type | ORM type | Mapping rule |
|---|---|---|---|
| `ticker`, `metadata_provider`, `venue`, `currency`, `company_name`, `sector`, `industry`, `country` | `str`/`Optional[str]` | `String` | direct |
| `asset_type` | `Optional[AssetType]` (enum) | `Optional[str]` | `enum.value` ⇄ `AssetType(str)`; `None`↔`None` |
| `ipo_date` | `Optional[date]` | `Date` | direct |
| `resolution_status` | `ResolutionStatus` | `Mapped[ResolutionStatus]` | direct (ORM column is enum-typed) |
| `resolved_at` | `Optional[datetime]` | `TIMESTAMP(tz=True)` | service stamps on miss; direct on hit |
| `created_at`/`updated_at` | n/a (domain omits) | via `TimestampMixin`/`onupdate` | DB-managed; do not set in `_to_orm` |

Do not set `created_at`/`updated_at` in `_to_orm` — `TimestampMixin` + the `onupdate` hook own them. [Source: db/models/instrument_metadata.py:21,65-69]

### Previous-story intelligence (1.1–1.4)
- **Provider is ready and `done`** — `FMPMetadataProvider(client=None).resolve(ticker) -> InstrumentMetadata` (domain). Returns a clean degraded record (never raises) for unknown/timeout/HTTP-error; leaves `resolved_at=None` for the service to stamp. Construct it with no args in the real wiring (CI-safe; `fmp_api_key` defaults to `""`), or inject a fake in tests. [Source: fmp_provider.py:62-118; 1-4 Completion Notes]
- **Repositories are ready and `done`** — sync + async, `get_by_ticker(ticker) -> Orm | None` and `upsert(orm) -> orm` (flush+refresh; `expire_on_commit=False`). They take ORM instances; mapping is *your* job. [Source: instrument_metadata_repository_sync.py]
- **Domain model is ready** — three-state `N/A` enforced; `venue` validator rejects `NA_SENTINEL`; `resolution_status` defaults `UNRESOLVED`; `ResolutionSummary` defined (counting is 1.6). Import these; do not redefine. [Source: models/instrument_metadata.py]
- **TDD strictly followed in 1.1–1.4** (tests Red first). Do the same: service + Protocol tests before implementation.
- **1.4 review findings to not repeat:** add the `pytestmark = pytest.mark.unit` marker; quote scalar values in `sprint-status.yaml`. [Source: 1-4 Review Findings]

### Git intelligence (recent commits — patterns to follow)
`8d7c5fe fix(metadata): harden venue normalization per code review` · `0f9274c feat(metadata): add FMP provider adapter and venue map` · `ddcb03e fix(metadata): harden FMP client …` · `ab3bfd2 feat(metadata): add FMP client …` · `7bdeb53 feat(db): add instrument_metadata cache table …`. Established patterns: `feat(metadata): …` / `fix(metadata): …` commit scope; `logger = structlog.get_logger(__name__)`; small focused classes with injectable test seams; tests committed with implementation; module-level pure helpers + a one-line class docstring to stay under the 100-line class rule. Mirror `fmp_provider.py` module style. [Source: git log; fmp_provider.py]

### Project Structure Notes
- **New files:** `src/services/metadata/providers/base.py`, `src/services/metadata/instrument_metadata_service.py`, `tests/unit/services/metadata/test_instrument_metadata_service.py`.
- **Modified files:** none required. (Optionally re-export `InstrumentMetadataService` from `src/services/metadata/__init__.py` — if you do, add `# noqa: F401  # re-export`. Currently `__init__.py` is a bare docstring.) Do not touch the provider, client, venue map, models, or repos.
- Placement matches the architecture source tree exactly: `instrument_metadata_service.py` at `src/services/metadata/`; the Protocol under `providers/`; test under `tests/unit/services/metadata/`. [Source: architecture.md:474-482, 506-510]

### Testing standards
- **Unit tier only** (`make test-unit`, parallel) — pure orchestration with a mocked provider + mocked repo; no DB, no Nautilus, no network. This is the correct and only tier for 1.5 (the architecture lists `test_instrument_metadata_service.py` under `tests/unit/`). [Source: architecture.md:509; CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — failing tests first for the service. [Source: development-principles.md]
- Mirror the AAA + `unittest.mock` style in `tests/unit/services/metadata/test_fmp_provider.py` / `test_fmp_client.py`; `pytestmark = pytest.mark.unit` at module top.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.5] — story statement + the 5 acceptance criteria
- [Source: _bmad-output/planning-artifacts/architecture.md:284-292 ADR-5] — provider-agnostic `InstrumentMetadataService` interface; FMP-specifics isolated; domain-model-only return; swap touches only `providers/` + wiring
- [Source: _bmad-output/planning-artifacts/architecture.md:262-269 ADR-3] — `instrument_metadata` is source of truth; ORM↔domain ownership; single writer = import pipeline
- [Source: _bmad-output/planning-artifacts/architecture.md:438-446] — cache-check-before-fetch (hit ⇔ `RESOLVED`); structlog fields; never-abort discipline
- [Source: _bmad-output/planning-artifacts/architecture.md:474-482, 522-531, 533-540] — source-tree placement + provider/data boundary diagrams
- [Source: src/models/instrument_metadata.py] — domain `InstrumentMetadata`, `AssetType`, `ResolutionStatus`, `NA_SENTINEL`, the `venue` validator (Story 1.2)
- [Source: src/db/models/instrument_metadata.py] — ORM `InstrumentMetadata` (PK ticker, `asset_type` String, `resolution_status` enum-typed, `resolved_at`, `TimestampMixin`)
- [Source: src/db/repositories/instrument_metadata_repository_sync.py] — `SyncInstrumentMetadataRepository.get_by_ticker` / `upsert` contract (ORM in/out); docstring assigning ORM↔domain mapping to "the Story 1.5 service"
- [Source: src/services/metadata/providers/fmp_provider.py] — `FMPMetadataProvider.resolve(ticker) -> InstrumentMetadata`; `_degraded` record shape; module style to mirror (Story 1.4)
- [Source: src/db/session_sync.py:98-134] — `get_sync_session` context manager (caller-side wiring for Story 2.4; not used by the service directly)
- [Source: src/services/firstrate/instrument_mapper.py:36-53] — `_parse_ipo_date` / structlog idiom reference

### Open questions (non-blocking — proceed with the documented default)
1. **`resolve_batch` location.** This story puts the fault-isolating batch loop in the service (AC #4 says "When a batch is resolved"). Story 2.4's import pipeline may prefer to drive the per-ticker loop itself (interleaving bar import with metadata resolution per ticker). Default: provide `resolve_batch` here as the canonical fault-isolated entry point; 2.4 can call it or call `resolve` per ticker inside its own loop — both honor AC #4. Confirm with 2.4's design when reached.
2. **`metadata_provider` on a service-built error record.** When the *provider itself* raises (not a clean degrade), the service can't read a provider name off the result. Default: use a literal `"UNKNOWN"` (or expose `PROVIDER_NAME` via the Protocol). Low stakes — these records are the unresolved worklist anyway; Story 3.2 surfaces them by `resolution_status`, not provider.
3. **`resolved_at` exactness in tests.** Default: assert `resolved_at is not None` (clock injected via module-level `_utcnow`). If a deterministic value is wanted, `monkeypatch` `_utcnow` — flag if the team prefers an injected-clock constructor param instead.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context)

### Debug Log References

- TDD Red: `test_instrument_metadata_service.py` failed collection with `ModuleNotFoundError: src.services.metadata.instrument_metadata_service` before implementation.
- TDD Green: 12/12 new tests pass.
- `make lint` — initially I001 (formatter reordered the import block); auto-fixed with `ruff check --fix`, then clean.
- `make typecheck` — `Success: no issues found in 66 source files`.
- `make test-unit` — `1034 passed` (baseline 1021 after Story 1.4 + new), zero regressions.
- Code review patch verification — `uv run pytest tests/unit/services/metadata/test_instrument_metadata_service.py` passed (12/12), `make lint` clean, `make typecheck` clean.

### Completion Notes List

- **Task 1 — Protocol seam:** added `src/services/metadata/providers/base.py` with a `typing.Protocol` `MetadataProvider` (single `resolve(ticker) -> InstrumentMetadata`). `@runtime_checkable` omitted — no test asserts `isinstance` on the Protocol (structural typing covers AC #5). `FMPMetadataProvider` left untouched; it satisfies the Protocol structurally.
- **Task 2 — Service + ORM↔domain bridge:** added `src/services/metadata/instrument_metadata_service.py`. `resolve` is cache-first: only `resolution_status == RESOLVED` short-circuits the provider; `None`/`UNRESOLVED`/`VENUE_UNRESOLVED` re-attempt (commented so it isn't "optimized" into a bug). Module-level `_utcnow()` is the single clock site (closes 1.4 open question #2); the service stamps `resolved_at` on miss. `_to_orm`/`_to_domain` own the `asset_type` enum⇄str bridge; ORM imported aliased as `OrmInstrumentMetadata`. `created_at`/`updated_at` left DB-managed.
- **Task 3 — Fault isolation:** `resolve_batch` loops `resolve` inside `try/except Exception`; on any raise it logs `metadata_resolution_failed` (ticker, error) and appends a last-resort degraded record (`metadata_provider="UNKNOWN"`, descriptive fields `N/A`, `venue/asset_type/ipo_date=None`, `VENUE_UNRESOLVED`) and continues. Clean provider degrades (no raise) pass through and are recorded as-is.
- **Open questions (resolved with documented defaults):** (1) `resolve_batch` lives in the service as the canonical fault-isolated entry point — 2.4 may still call `resolve` per ticker. (2) provider-raised error record uses literal `"UNKNOWN"`. (3) `resolved_at` asserted `is not None` via module-level `_utcnow`.
- **Scope respected:** no changes to provider/client/venue map/models/repositories; no summary counting (1.6), no overrides merge (3.3), no CLI wiring, no async variant. `src/services/metadata/__init__.py` left as a bare docstring (no re-export added).

### File List

- `src/services/metadata/providers/base.py` (new) — `MetadataProvider` Protocol
- `src/services/metadata/instrument_metadata_service.py` (new) — `InstrumentMetadataService`
- `tests/unit/services/metadata/test_instrument_metadata_service.py` (new) — 12 unit tests
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status → review

## Change Log

- 2026-06-19 — Implemented Story 1.5: `MetadataProvider` Protocol seam + `InstrumentMetadataService` (cache-first resolve, ORM↔domain bridge, single `_utcnow` clock, `resolve_batch` per-ticker fault isolation). 12 new unit tests; all 5 ACs satisfied. lint/typecheck clean; 1034 unit tests pass. Status: ready-for-dev → review.
