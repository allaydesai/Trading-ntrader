# Story 1.1: FMP Configuration & httpx Runtime Dependency

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system operator,
I want FMP connection settings supplied via typed environment configuration and `httpx` promoted to a runtime dependency,
so that the metadata loader can authenticate to FMP without hardcoded credentials and with a properly-classified HTTP client.

## Acceptance Criteria

1. **Given** a `.env` containing `FMP_API_KEY`, **When** application `Settings` load, **Then** `Settings.fmp` is an `FMPSettings` instance exposing `fmp_api_key`, `fmp_base_url` (default `https://financialmodelingprep.com/stable`), `fmp_rate_limit` (default `300`, per-minute), and `fmp_request_timeout` — **And** `FMPSettings` is nested as `Settings.fmp`, mirroring `Settings.ibkr` / `Settings.kraken`.
2. **Given** `FMP_API_KEY` is unset, **When** `Settings` load, **Then** a clear Pydantic validation error is raised (the field is required).
3. **Given** the settings object is logged or `repr()`'d, **When** its representation is produced, **Then** `fmp_api_key` is not exposed (`repr=False`).
4. **Given** `pyproject.toml`, **When** `uv add httpx` is run, **Then** `httpx` (>=0.28.1) is recorded as a main runtime dependency (no longer dev-group only) **And** `make lint` and `make typecheck` pass.

## Tasks / Subtasks

- [x] **Task 1: Promote `httpx` to a runtime dependency** (AC: #4)
  - [x] Run `uv add httpx` — adds `httpx>=0.28.1` to `[project].dependencies` in `pyproject.toml`. **Do NOT edit `pyproject.toml` by hand** (a bash-guard hook blocks it; `uv` is the only path).
  - [x] Run `uv remove --dev httpx` — `httpx` is currently in `[dependency-groups].dev` (line 33). `uv add` writes it to the main list but leaves the dev entry, producing a duplicate. Remove the dev entry so it appears in exactly one place.
  - [x] Verify with `grep -n httpx pyproject.toml`: exactly one `httpx>=0.28.1` line, under `[project].dependencies`, and none under `dev`.
  - [x] Run `uv sync` then `make lint` and `make typecheck` — both must pass.
- [x] **Task 2: Add `FMPSettings` and nest it as `Settings.fmp`** (AC: #1, #3)
  - [x] In `src/config.py`, add an `FMPSettings(BaseSettings)` class immediately after `KrakenSettings` (and before/near `FirstRateSettings`), mirroring the existing classes' shape and `model_config` block.
  - [x] Fields (env var = field name upper-cased, the established convention — `case_sensitive: False`):
    - `fmp_api_key: str = Field(..., repr=False, description="FMP API key")` → `FMP_API_KEY` — **required** (use `...`, no default) and `repr=False` (mirrors `kraken_api_secret`).
    - `fmp_base_url: str = Field(default="https://financialmodelingprep.com/stable", description="FMP API base URL")` → `FMP_BASE_URL`.
    - `fmp_rate_limit: int = Field(default=300, ge=1, description="Max FMP requests per minute (Starter quota)")` → `FMP_RATE_LIMIT`.
    - `fmp_request_timeout: int = Field(default=30, ge=1, description="Per-request timeout in seconds")` → `FMP_REQUEST_TIMEOUT`. (Architecture lists the field but no default; 30s is chosen — a single `/stable/profile` GET is fast. Confirm with the team if a different value is preferred.)
  - [x] Reuse the exact `model_config` dict used by `KrakenSettings`/`FirstRateSettings`: `{"env_file": ".env", "env_file_encoding": "utf-8", "case_sensitive": False, "extra": "ignore"}`.
  - [x] In the `Settings` class, add the nested field alongside `ibkr`/`kraken`/`firstrate`/`catalog`:
    `fmp: FMPSettings = Field(default_factory=FMPSettings, description="FMP metadata provider settings")`. (Added a `# type: ignore[arg-type]` on `default_factory`: because `fmp_api_key` is required, mypy's pydantic plugin no longer sees `FMPSettings` as a zero-arg callable — `FMPSettings()` populates the key from env at runtime.)
- [x] **Task 3: Wire env files & docs so the required key doesn't break existing flows** (AC: #1, #2) — **regression guard, do not skip**
  - [x] Add `FMP_API_KEY=` (with a placeholder/comment) to `.env.example`, plus optional commented `FMP_BASE_URL`, `FMP_RATE_LIMIT`, `FMP_REQUEST_TIMEOUT` lines, in an `# FMP Metadata Provider Settings` block (follow the existing Kraken/FirstRate comment style).
  - [x] Add a real `FMP_API_KEY` to local `.env` (gitignored) **and** to `.env.dev` / `.env.qa` (a dummy value like `test-fmp-key` is fine for those — they're test envs). `get_settings()` selects `.env.dev`/`.env.qa` via the `ENV` var; making the key required means any `Settings()` built under those envs now needs it.
  - [x] Search for test fixtures / conftests that construct `Settings()` or call `get_settings()` and would now fail without `FMP_API_KEY` (`grep -rn "get_settings\|Settings(" tests/`). Where a fixture builds real settings, ensure the env var is set (monkeypatch or the test `.env`). **Chosen guard:** an autouse fixture in the root `tests/conftest.py` (`_ensure_fmp_api_key`) seeds `FMP_API_KEY` if absent. This is CI-safe — CI checks out **no** `.env`, so the var would otherwise be missing for every `Settings()` construction; verified by running the config suite with `.env` removed.
- [x] **Task 4: Unit tests (write first — TDD Red→Green)** (AC: #1, #2, #3)
  - [x] New file `tests/unit/test_fmp_settings.py`, mirroring `tests/unit/services/test_kraken_settings.py` structure (Arrange/Act/Assert, `monkeypatch`, `_env_file=None` to bypass `.env`).
  - [x] Test: defaults — `fmp_base_url == "https://financialmodelingprep.com/stable"`, `fmp_rate_limit == 300`, `fmp_request_timeout == 30` (construct with `FMP_API_KEY` set so the required field is satisfied).
  - [x] Test: required — `monkeypatch.delenv("FMP_API_KEY", raising=False)` then `with pytest.raises(ValidationError): FMPSettings(_env_file=None)`.
  - [x] Test: env overrides — set `FMP_API_KEY`, `FMP_BASE_URL`, `FMP_RATE_LIMIT`, `FMP_REQUEST_TIMEOUT` via `monkeypatch.setenv`; assert each loads. (Plus `ge=1` range-validation tests for rate limit + timeout.)
  - [x] Test: `repr(FMPSettings(...))` (and `str(...)`) does **not** contain the api-key value, confirming `repr=False`.
  - [x] Test: `Settings(fmp_api_key...)` — `isinstance(get_settings().fmp, FMPSettings)` and `Settings().fmp.fmp_api_key` reads from env (integration with the nested factory).
- [x] **Task 5: Verify** (AC: all)
  - [x] `make test-unit` green (949 passed); `make lint` clean; `make typecheck` clean.

## Dev Notes

### Why this is story 1 (brownfield, no project-init)
This is a Phase 2 continuation on a mature codebase. There is **no scaffolding/init story** — the architectural foundation is inherited. The first story is deliberately the smallest reusable seam: typed config + correcting the `httpx` classification gap. [Source: epics.md#Additional-Requirements; architecture.md#Net-New-Dependency-Decision]

### Pattern to copy exactly — `src/config.py`
`FMPSettings` must be a near-clone of the existing `KrakenSettings` (`src/config.py:81`) / `FirstRateSettings` (`src/config.py:127`) shape:
- Subclass `BaseSettings`; one `Field(...)` per setting with `description=`.
- The `repr=False` precedent is `kraken_api_secret` (`src/config.py:85`).
- The nesting precedent is `ibkr`/`kraken`/`firstrate`/`catalog` via `Field(default_factory=...)` (`src/config.py:229-246`).
- Env mapping is **field-name → UPPER_SNAKE** with `case_sensitive: False` (e.g. `fmp_api_key` ← `FMP_API_KEY`). Do **not** add an `env_prefix`; the codebase encodes the prefix in the field name itself, matching `kraken_*` / `ibkr_*`.

### `httpx` dependency facts
- Currently dev-group only: `pyproject.toml:33` `httpx>=0.28.1`. Already imported by app code at `src/api/ui/backtests.py:828` — so the promotion fixes a real classification bug, it isn't speculative. [Source: architecture.md#Net-New-Dependency-Decision]
- httpx **0.28.1 is current stable** — no version bump, no SDK. The FMP client (Story 1.3) will use sync `httpx`. [Source: architecture.md:184]

### ⚠️ Critical regression risk — required field changes `Settings()` construction
Making `fmp_api_key` required (`...`) means **every** construction of `Settings()` / `FMPSettings()` (including via `Settings.fmp`'s `default_factory`) now raises `ValidationError` if `FMP_API_KEY` is absent from env/`.env`. This can break the app boot and any test that builds real settings. Task 3 is the guard: seed the var into `.env`, `.env.example`, `.env.dev`, `.env.qa`, and any settings-building test fixture. **Run the full `make test-unit` (not just the new file) to catch fallout.** [Source: AC #2; architecture.md#FMPSettings]

### Scope boundaries (do NOT do here)
- No `FMPClient`, no HTTP calls, no `instrument_metadata` table, no rate-limiter, no venue map — those are Stories 1.2–1.6. This story is **config + dependency only**.
- No `RateLimiter` instantiation yet; `fmp_rate_limit` is just a stored int consumed later (Story 1.3). [Source: epics.md#Story-1.3]

### Project Structure Notes
- Single modified file in `src/`: `src/config.py` (add `FMPSettings`, nest `Settings.fmp`). [Source: architecture.md:462 "config.py — MODIFIED: add FMPSettings, nest Settings.fmp"]
- `pyproject.toml` modified **only via `uv`** (guard-enforced). [Source: architecture.md:502]
- New test: `tests/unit/test_fmp_settings.py` (root `tests/unit/`, alongside `test_ibkr_config.py`, `test_catalog_settings.py`, `test_config.py`).
- Env files touched: `.env` (local, gitignored), `.env.example`, `.env.dev`, `.env.qa`.
- Conforms to size limits (file <500 / function <50 / class <100 / line ≤100). `FMPSettings` is ~12 lines — well within bounds.

### Testing standards
- Unit tier (pure config, no Nautilus, no DB, no network) → `make test-unit` (parallel). [Source: CLAUDE.md Decision Heuristics]
- TDD non-negotiable: write `tests/unit/test_fmp_settings.py` first (Red), implement `FMPSettings` (Green). [Source: development-principles.md]
- Mirror the Arrange/Act/Assert + `monkeypatch` + `_env_file=None` idiom from `tests/unit/services/test_kraken_settings.py`.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.1] — story statement + acceptance criteria
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — "first story is `FMPSettings` + `uv add httpx`"; `FMPSettings` field spec
- [Source: _bmad-output/planning-artifacts/architecture.md#Net-New-Dependency-Decision] (lines 173–187) — httpx promotion rationale + action
- [Source: _bmad-output/planning-artifacts/architecture.md#FMPSettings] (lines 395–399) — exact field/env/default spec
- [Source: _bmad-output/planning-artifacts/prd.md] — FR39 (typed FMP env config), NFR13 (300/min Starter quota), NFR19 (credential hygiene, `repr=False`, `.env` excluded)
- [Source: src/config.py:81-124] — `KrakenSettings` pattern to mirror (incl. `repr=False` at :85)
- [Source: src/config.py:229-258] — `Settings` nested-settings `default_factory` pattern
- [Source: pyproject.toml:33] — current dev-group `httpx`; src/api/ui/backtests.py:828 — existing app usage

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (1M context)

### Debug Log References

- `make typecheck` initially failed: `Argument "default_factory" to "Field" has incompatible type "type[FMPSettings]"`. Cause: making `fmp_api_key` required (`...`) means mypy's pydantic plugin no longer treats `FMPSettings` as a zero-arg callable (env-population is invisible to the type checker). Resolved with a targeted `# type: ignore[arg-type]` + explanatory comment on the `default_factory`. Note: `make typecheck` checks `src/core src/services` and follows the import into `src/config.py` — `src/config.py` is not directly listed but is reached transitively.
- CI-safety simulation: temporarily removed `.env` and ran `tests/unit/test_config.py` + `tests/unit/test_fmp_settings.py` with `FMP_API_KEY` unset → 29 passed, confirming the `tests/conftest.py` autouse guard covers the CI case (CI checks out no `.env`).

### Completion Notes List

- Added `FMPSettings(BaseSettings)` in `src/config.py` (between `KrakenSettings` and `FirstRateSettings`) with `fmp_api_key` (required, `repr=False`), `fmp_base_url`, `fmp_rate_limit` (default 300, `ge=1`), `fmp_request_timeout` (default 30, `ge=1`); reused the standard `model_config` block. Nested as `Settings.fmp` via `default_factory`, mirroring `ibkr`/`kraken`/`firstrate`/`catalog`.
- Promoted `httpx>=0.28.1` from the dev group to `[project].dependencies` via `uv add httpx` + `uv remove --dev httpx` (single entry verified). Fixes a real classification gap — `httpx` is already imported by app code in `src/api/ui/backtests.py`.
- Regression guard: the required `fmp_api_key` makes every `Settings()` construction depend on `FMP_API_KEY`. Mitigated by (a) seeding `.env`/`.env.dev`/`.env.qa` and documenting in `.env.example`, and (b) an autouse `tests/conftest.py` fixture that seeds `FMP_API_KEY` when absent — the CI-safe path, since CI has no `.env`.
- TDD: wrote `tests/unit/test_fmp_settings.py` first (15 tests, RED on missing import), then implemented to GREEN. Full unit suite: 949 passed, no regressions. `make lint` and `make typecheck` clean.
- All 4 acceptance criteria satisfied (nested `Settings.fmp` + fields/defaults; required-field `ValidationError`; `repr=False` masking; `httpx` runtime dep with lint/typecheck green).

### File List

- `src/config.py` — MODIFIED: added `FMPSettings` class; nested `Settings.fmp` (with `# type: ignore[arg-type]`).
- `tests/unit/test_fmp_settings.py` — NEW: 15 unit tests for `FMPSettings` (defaults, required field, env overrides, range validation, repr/str masking, `Settings.fmp` nesting).
- `tests/conftest.py` — MODIFIED: added autouse `_ensure_fmp_api_key` fixture (CI-safe regression guard).
- `pyproject.toml` — MODIFIED (via `uv`): `httpx>=0.28.1` moved from dev group to `[project].dependencies`.
- `uv.lock` — MODIFIED (via `uv`): lockfile updated for the dependency reclassification.
- `.env.example` — MODIFIED: added `# FMP Metadata Provider Settings` block (`FMP_API_KEY` + commented optional vars).
- `.env`, `.env.dev`, `.env.qa` — MODIFIED (gitignored, local/test envs): added `FMP_API_KEY=test-fmp-key`.

## Change Log

| Date       | Description                                                                                  |
| ---------- | -------------------------------------------------------------------------------------------- |
| 2026-06-17 | Implemented Story 1.1: added `FMPSettings` + nested `Settings.fmp`, promoted `httpx` to a runtime dependency, wired env files + CI-safe conftest guard. 15 new unit tests; 949 unit tests green. Status → review. |
