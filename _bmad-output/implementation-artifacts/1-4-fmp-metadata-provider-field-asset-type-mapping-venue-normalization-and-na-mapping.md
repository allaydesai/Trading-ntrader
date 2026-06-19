# Story 1.4: FMP Metadata Provider — Field/Asset-Type Mapping, Venue Normalization & N/A Mapping

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want an `FMPMetadataProvider` that maps FMP profile JSON to the `InstrumentMetadata` domain model, normalizes the venue, and applies the `N/A` sentinel,
so that all FMP-specific mapping is contained in one adapter and no FMP types leak to consumers.

## Acceptance Criteria

1. **Given** a populated FMP profile JSON, **When** the provider maps it, **Then** `companyName`, `sector`, `industry`, `country`, `ipoDate`, and `currency` populate the corresponding domain fields — **And** any absent/empty descriptive field is set to `NA_SENTINEL`.
2. **Given** the FMP `isEtf` / `isFund` flags, **When** asset type is derived, **Then** `asset_type` is set to the correct `AssetType` (`ETF` / `FUND` / `EQUITY`).
3. **Given** the FMP `exchange` label, **When** venue normalization runs against `FMP_EXCHANGE_TO_VENUE` in `venue_map.py`, **Then** a confidently-mapped label (`NASDAQ`, `NYSE`, …) yields the corresponding Nautilus venue code and `resolution_status = RESOLVED`.
4. **Given** a label in `AMBIGUOUS_LABELS` (incl. `AMEX`) or a blank/unmapped label, **When** venue normalization runs, **Then** `venue` is `None` and `resolution_status = VENUE_UNRESOLVED` — no guessed default venue is ever assigned.
5. **Given** unit tests, **When** the provider and venue map are tested, **Then** the venue map (label→venue + ambiguous routing) and the provider (JSON→domain + `N/A` mapping) are covered with mocked input.

## Tasks / Subtasks

- [x] **Task 1: `venue_map.py` — the single source of truth for FMP→Nautilus venue translation** (AC: #3, #4) — *write `test_venue_map.py` FIRST (TDD Red→Green)*
  - [x] Create `src/services/metadata/venue_map.py`. This is a NEW module in the existing `src/services/metadata/` package (created in Story 1.3); do **not** put it under `src/services/firstrate/`. [Source: architecture.md:382-384, 425-427, 478]
  - [x] Define module-level `FMP_EXCHANGE_TO_VENUE: dict[str, str]` — confidently-mapped FMP exchange labels → Nautilus venue codes. **Seed conservatively** with the two confident equity venues only: `{"NASDAQ": "NASDAQ", "NYSE": "NYSE"}`. Nautilus venue codes in this repo are bare strings like `NASDAQ` / `NYSE` / `ARCA` (see `InstrumentId.from_str("SPY.ARCA")` / `"AAPL.NASDAQ"`). [Source: src/services/firstrate/instrument_mapper.py:127; firstrate_csv_parser.py:103; catalog_metadata.py:23]
  - [x] Define `AMBIGUOUS_LABELS: frozenset[str] = frozenset({"AMEX"})`. **`AMEX` is the load-bearing ambiguous case** — FMP labels SPY's exchange `AMEX`, but SPY's real Nautilus venue is `ARCA`; the label covers the ARCA/BATS/NYSE-American split and must NOT be guessed. [Source: architecture.md:298-300, 426-427; reference_fmp_api memory "Venue gotcha"]
  - [x] Define `def normalize_venue(label: str | None) -> str | None`:
    - `if not label: return None` (blank/`None`/empty → unresolved).
    - `key = label.strip().upper()`.
    - `if key in AMBIGUOUS_LABELS: return None` (ambiguous → unresolved, **never guessed**).
    - `return FMP_EXCHANGE_TO_VENUE.get(key)` (confident → code; unmapped → `None`). **No `or "DEFAULT"`, no fallback venue.** [Source: architecture.md:425-427 "No guessed default"]
  - [x] **Conservative-by-design note:** seeding only `NASDAQ`/`NYSE` means more tickers route to `VENUE_UNRESOLVED` — that is the SAFE direction. Unresolved tickers are surfaced by the Story 3.2 report and fixed via `venue_overrides.csv` (Story 3.3); a wrongly-guessed venue would silently corrupt a backtest. Do NOT pad the map with uncertain labels to reduce the unresolved count. Adding a label requires empirical FMP verification (open question #1 below).
  - [x] `test_venue_map.py` cases: `NASDAQ`→`"NASDAQ"`; `NYSE`→`"NYSE"`; `AMEX`→`None`; `""`/`None`→`None`; unmapped (`"XYZ"`)→`None`; case/whitespace-insensitive (`"nasdaq"`, `" NYSE "`)→mapped; membership `"AMEX" in AMBIGUOUS_LABELS`; and an assertion that an unmapped label never yields a non-`None` code (no-guess invariant).

- [x] **Task 2: `FMPMetadataProvider` adapter — FMP JSON → domain, asset type, venue, `N/A` mapping** (AC: #1, #2, #3, #4) — *write `test_fmp_provider.py` FIRST (TDD)*
  - [x] Create `src/services/metadata/providers/__init__.py` (NEW sub-package; one-line docstring mirroring `src/services/metadata/__init__.py`) and `src/services/metadata/providers/fmp_provider.py`. [Source: architecture.md:480-482]
  - [x] Define `class FMPMetadataProvider`:
    - Class constant `PROVIDER_NAME = "FMP"` (used for `metadata_provider` and log fields; do not hardcode `"FMP"` inline elsewhere).
    - `def __init__(self, client: FMPClient | None = None) -> None: self._client = client or FMPClient()`. The injected `client` is the **test seam** — unit tests pass a fake/stub `fetch_profile` and never touch httpx or settings. (Real construction is CI-safe anyway: `fmp_api_key` defaults to `""`.) [Source: fmp_client.py:80-93; 1-3 story Completion Notes "fmp_api_key defaults to ''"]
  - [x] `def resolve(self, ticker: str) -> InstrumentMetadata`:
    - `profile = self._client.fetch_profile(ticker)` → `dict | None`.
    - `if profile is None: return self._degraded(ticker)` (unknown ticker **or** client degradation — Story 1.3's `fetch_profile` returns `None` for both; this is the "downstream mapping to descriptive `N/A` + `VENUE_UNRESOLVED`" that 1.3 AC #5 defers to this story). [Source: 1-3 story AC #5, lines 19, 56]
    - `else: return self._to_domain(ticker, profile)`.
    - **Signature contract:** `resolve(ticker) -> InstrumentMetadata` (domain model only — no FMP types, no raw dict, no httpx exceptions leak out). Story 1.5's `InstrumentMetadataService` calls this exact signature behind the provider-agnostic seam; keep it stable. [Source: architecture.md:285-289, 522-531 ADR-5]
  - [x] `def _to_domain(self, ticker: str, profile: dict[str, Any]) -> InstrumentMetadata` (the pure mapper — **no I/O, no clock**, so it unit-tests deterministically with a dict):
    - Descriptive fields via a helper `_descriptive(value) -> str`: `None`/empty-after-strip → `NA_SENTINEL`, else the stripped string. Apply to `company_name`←`companyName`, `sector`←`sector`, `industry`←`industry`, `country`←`country`, `currency`←`currency`. [Source: architecture.md:413-419; epics.md#Story-1.4 AC1]
    - `ipo_date`←`ipoDate`: parse with `date.fromisoformat` on a non-blank value, else `None`. **`ipo_date` is a typed `date` and can NEVER hold `NA_SENTINEL`** — absent/invalid → `None`. Mirror the `_parse_ipo_date` shape in `instrument_mapper.py:36-53` (blank→`None`, `ValueError`→log + `None`); keep a local copy (do not couple `metadata` → `firstrate`). [Source: instrument_mapper.py:36-53]
    - `asset_type` via `_asset_type(profile) -> AssetType`: `if profile.get("isEtf"): ETF` · `elif profile.get("isFund"): FUND` · `else: EQUITY`. (FMP returns booleans; `.get(..)` truthiness handles missing keys.) [Source: epics.md#Story-1.4 AC2; reference_fmp_api memory]
    - `venue = normalize_venue(profile.get("exchange"))` — use the FMP **`exchange`** short-label field (NOT `exchangeFullName`). [Source: reference_fmp_api memory; venue_map Task 1]
    - `resolution_status = ResolutionStatus.RESOLVED if venue is not None else ResolutionStatus.VENUE_UNRESOLVED`. **Status is driven by venue ALONE** — a record with full descriptive data but an ambiguous venue is `VENUE_UNRESOLVED`; descriptive `N/A` gaps do NOT downgrade status (those are counted separately in Story 1.6). [Source: epics.md#Story-1.4 AC3/AC4; architecture.md:445-446]
    - Return `InstrumentMetadata(ticker=ticker, metadata_provider=self.PROVIDER_NAME, venue=venue, currency=…, asset_type=…, company_name=…, sector=…, industry=…, country=…, ipo_date=…, resolution_status=…)`. Leave `resolved_at` defaulted (`None`) — see Task 3 / open question #2.
  - [x] `def _degraded(self, ticker: str) -> InstrumentMetadata` (the unknown-ticker / no-data record):
    - All descriptive fields = `NA_SENTINEL` (`company_name`, `sector`, `industry`, `country`, `currency`).
    - `venue = None`, `asset_type = None`, `ipo_date = None`, `resolution_status = ResolutionStatus.VENUE_UNRESOLVED`, `metadata_provider = PROVIDER_NAME`, `ticker = ticker`.
    - Emit a `structlog` debug/info (fields `ticker`, `provider=PROVIDER_NAME`, `resolution_status`) — the import-time summary lives in 1.6, but a per-ticker breadcrumb here is consistent with the repo's structlog convention. Do not raise. [Source: architecture.md:442-444; fmp_client.py:25]
  - [x] **Never assign `NA_SENTINEL` to `venue`.** The domain `@field_validator("venue")` will raise if you do (`src/models/instrument_metadata.py:95-101`); venue is a real code or `None`. Treat that validator as a backstop, not the primary guard. [Source: instrument_metadata.py:95-101; architecture.md:418-419]

- [x] **Task 3: Unit tests — mocked input, no network, no DB** (AC: #1–#5) — written alongside Tasks 1–2 (TDD)
  - [x] `tests/unit/services/metadata/__init__.py` already exists (Story 1.3) — do not recreate. Add `test_venue_map.py` (Task 1) and `test_fmp_provider.py` here. Mirror the Arrange/Act/Assert + `unittest.mock` idioms already in `tests/unit/services/metadata/test_fmp_client.py`. [Source: 1-3 story File List]
  - [x] **Provider test seam:** pass a stub client — e.g. a tiny `class _StubClient: def __init__(self, result): self._r = result` with `def fetch_profile(self, ticker): return self._r` (or a `MagicMock(spec=FMPClient)` whose `fetch_profile.return_value` is set). No httpx, no `MockTransport` needed at this layer (the client is already covered in 1.3). The pure `_to_domain` can also be tested directly with a dict.
  - [x] Cases to cover:
    - **AC1 (populated):** full profile dict → `company_name`/`sector`/`industry`/`country`/`currency` populated from the JSON; `ipo_date` parsed to a `date`.
    - **AC1 (gaps):** profile with `""`/missing descriptive fields → each becomes `NA_SENTINEL`; blank/missing `ipoDate` → `ipo_date is None` (NOT the sentinel).
    - **AC2:** `isEtf=True`→`AssetType.ETF`; `isEtf=False, isFund=True`→`FUND`; both absent/false→`EQUITY`.
    - **AC3:** `exchange="NASDAQ"`→`venue=="NASDAQ"` and `resolution_status==RESOLVED`.
    - **AC4:** `exchange="AMEX"`→`venue is None` and `status==VENUE_UNRESOLVED`; blank `exchange`→same; unmapped `exchange="XETRA"`→same. Assert no venue is fabricated.
    - **SPY regression guard:** a fully-populated profile with `exchange="AMEX"` (the real 1.3 live-smoke shape: name "…SPDR S&P 500 ETF Trust", `isEtf=True`) → descriptive fields RESOLVED, `asset_type==ETF`, but `venue is None` + `status==VENUE_UNRESOLVED`. This is the canonical "descriptive-resolved / venue-unresolved" split. [Source: 1-3 story Debug Log "SPY → exchange AMEX"]
    - **Degraded (client `None`):** `fetch_profile` returns `None` → all descriptive fields `NA_SENTINEL`, `venue is None`, `asset_type is None`, `ipo_date is None`, `status==VENUE_UNRESOLVED`, `metadata_provider=="FMP"`, `ticker` preserved.
    - **No-leak / type contract:** `resolve(...)` returns an `InstrumentMetadata` instance (assert `isinstance`); `metadata_provider == FMPMetadataProvider.PROVIDER_NAME == "FMP"`.
    - **Venue-never-sentinel invariant:** no mapping path produces `venue == NA_SENTINEL` (the domain validator would raise — assert mapping succeeds for the AMEX/degraded cases without raising).

- [x] **Task 4: Verify** (AC: all)
  - [x] `make test-unit` green — new `test_venue_map.py` + `test_fmp_provider.py` plus full suite, no regressions (baseline **991** unit tests after Story 1.3). [Source: 1-3 story Debug Log]
  - [x] `make lint` clean — mind the F401/F821 import gate: import `Any`, `date`, `structlog`, `InstrumentMetadata`, `ResolutionStatus`, `AssetType`, `NA_SENTINEL`, `FMPClient`, `normalize_venue` and ensure each is used in the same edit that adds it. [Source: CLAUDE.md "structural import gate"]
  - [x] `make typecheck` clean — `mypy` targets `src/core src/services strategies`, so **both** `venue_map.py` and `providers/fmp_provider.py` are directly checked. Full annotations (`-> InstrumentMetadata`, `-> str | None`, `dict[str, Any]`). [Source: 1-3 story Task 5; Makefile typecheck]
  - [x] Size limits: each file < 500 lines, `FMPMetadataProvider` < 100 lines, methods < 50, line length ≤ 100. [Source: CLAUDE.md Foundational Rules]

## Dev Notes

### Scope boundaries (do NOT do here)
- **Adapter + venue map only.** No `InstrumentMetadataService` (Story 1.5), **no cache-check-before-fetch** (the cache lookup is the *service's* job in 1.5 — the provider always asks the client), no repository/DB calls, no ORM↔domain persistence. The provider produces a fresh domain `InstrumentMetadata`; it never reads or writes `instrument_metadata`. [Source: architecture.md:359 step 5 "cache-check" is the service; epics.md#Story-1.5]
- **No `ResolutionSummary` counting** (Story 1.6). The provider sets each record's `resolution_status`; aggregating the counts is later. [Source: epics.md#Story-1.6]
- **No `venue_overrides.csv`** load/merge (Story 3.3 / `venue_overrides.py`). The provider only consults the in-code `FMP_EXCHANGE_TO_VENUE` map; operator overrides are merged downstream in the import-orchestration step, never inside the provider. [Source: architecture.md:428-430 ADR-7]
- **Do NOT define a `Protocol`/ABC interface here.** The provider-agnostic *interface* belongs to Story 1.5 (the service depends on it). Keep `FMPMetadataProvider.resolve(ticker) -> InstrumentMetadata` concrete; 1.5 will extract/match the interface. [Source: architecture.md:284-292 ADR-5; epics.md#Story-1.5]
- **Do NOT modify `fmp_client.py`.** It is `done` (Story 1.3). The provider *consumes* `FMPClient.fetch_profile`; it does not change it. [Source: 1-3 story Status done]

### The provider boundary (ADR-5 — the whole point of this story)
```
InstrumentMetadataService.resolve(ticker)   ← Story 1.5 (cache-check wraps this)
  → FMPMetadataProvider.resolve(ticker) -> InstrumentMetadata   ← THIS STORY (adapter)
      → FMPClient.fetch_profile(ticker) -> dict | None          ← Story 1.3 (done)
      → normalize_venue(label) -> str | None                    ← THIS STORY (venue_map)
```
The contract: **no FMP types or raw JSON cross out of `FMPMetadataProvider`.** Consumers (mapping, explorer, backtest) get only the domain model. Adding a second provider later touches only `providers/` + service wiring. [Source: architecture.md:522-531]

### Three-state `N/A`, by field type (the mapping discipline — most divergence-prone)
| Field(s) | Type | Absent/empty maps to |
|---|---|---|
| `company_name`, `sector`, `industry`, `country`, `currency` | `str` (descriptive) | `NA_SENTINEL` (`"N/A"`) |
| `venue` | `str` | `None` — **never** `NA_SENTINEL` (validator-enforced) |
| `ipo_date` | `date` | `None` (typed; can't hold a sentinel string) |
| `asset_type` | `AssetType` enum | `None` only in the degraded/unknown record; else `ETF`/`FUND`/`EQUITY` |

`NA_SENTINEL` is **imported** from `src/models/instrument_metadata.py` — never write the literal `"N/A"`. The constant already exists (Story 1.2). [Source: architecture.md:413-419; instrument_metadata.py:24]

### Status is venue-driven, not descriptive-driven
`resolution_status` has exactly three values; this story produces only `RESOLVED` (venue mapped) or `VENUE_UNRESOLVED` (venue ambiguous/blank/unmapped, or whole-profile missing). `UNRESOLVED` is the DB default for never-attempted rows — the provider, by definition, has attempted, so it never returns `UNRESOLVED`. A descriptive `N/A` gap on an otherwise-venue-resolved ticker stays `RESOLVED` (the gap is counted later in 1.6). [Source: epics.md#Story-1.4 AC3/AC4; instrument_metadata.py:35-45]

### FMP profile field map (verified in planning)
`GET /stable/profile?symbol={T}` returns a one-element array; `FMPClient` already unwraps it to `data[0]` (the dict you receive) or `None`. Relevant keys: `companyName`, `sector`, `industry`, `country`, `currency`, `ipoDate` (ISO `YYYY-MM-DD`), `isEtf`/`isFund` (bool), `exchange` (short label e.g. `"NASDAQ"`/`"NYSE"`/`"AMEX"`), `exchangeFullName` (long name — **not** used for venue). [Source: reference_fmp_api memory; fmp_client.py:158-169]

### Previous-story intelligence (1.1 / 1.2 / 1.3)
- **Domain model is ready** — `InstrumentMetadata`, `AssetType`, `ResolutionStatus`, `NA_SENTINEL` all live in `src/models/instrument_metadata.py` (Story 1.2). Import them; do not redefine. The `venue` validator already rejects the sentinel. [Source: instrument_metadata.py]
- **`FMPClient` is ready and `done`** — lazy httpx, single `/stable/profile` GET, `apikey` auth, retry/backoff, returns `dict | None`. `None` = empty-`[]` unknown ticker **or** degraded error (the provider treats both as the degraded record). [Source: 1-3 story]
- **TDD was followed strictly** in 1.1–1.3 (tests Red first). Do the same: `venue_map` and provider tests before implementation.
- **`fmp_api_key` defaults to `""`** so `FMPClient()` construction is CI-safe; but unit tests should inject a stub client and never construct the real one. There is **no** `_ensure_fmp_api_key` autouse fixture (1.3 corrected the 1.2 doc that claimed one). [Source: 1-3 story Completion Notes]
- **`pyproject.toml` is `uv`-only** (bash-guard blocks hand edits) — irrelevant here, no new deps (`httpx`/`structlog`/`pydantic` all present). [Source: 1-3 story Dev Notes]

### Git intelligence (recent commits — patterns to follow)
`ddcb03e fix(metadata): harden FMP client per code review` · `ab3bfd2 feat(metadata): add FMP client …` · `7bdeb53 feat(db): add instrument_metadata cache table …` · `0a75de2 feat(config): add FMPSettings …`. Established patterns: structlog `logger = structlog.get_logger(__name__)`; small focused classes with injectable test seams; `feat(metadata): …` / `fix(metadata): …` commit scope; tests committed with implementation. Mirror the `fmp_client.py` module style (module-level constants, one-line class docstring to stay under the 100-line class rule). [Source: git log; fmp_client.py]

### Project Structure Notes
- **New files:** `src/services/metadata/venue_map.py`, `src/services/metadata/providers/__init__.py`, `src/services/metadata/providers/fmp_provider.py`, `tests/unit/services/metadata/test_venue_map.py`, `tests/unit/services/metadata/test_fmp_provider.py`.
- **Modified files:** none expected (`providers/` is a new sub-package; do not edit `src/services/metadata/__init__.py` unless you choose to re-export `FMPMetadataProvider` — optional, and if so add `# noqa: F401  # re-export`). Do not touch `fmp_client.py`, the domain model, or any DB file.
- Placement matches the architecture source tree exactly: `venue_map.py` and `providers/fmp_provider.py` under `src/services/metadata/`; tests under `tests/unit/services/metadata/`. [Source: architecture.md:474-482, 506-510]

### Testing standards
- **Unit tier only** (`make test-unit`, parallel) — pure mapping logic + a stubbed client; no DB, no Nautilus, no network. This is the correct and only tier for 1.4. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — failing tests first for `normalize_venue` and `FMPMetadataProvider`. [Source: development-principles.md]
- Mirror the AAA + `unittest.mock` style already in `tests/unit/services/metadata/test_fmp_client.py`.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.4] — story statement + the 5 acceptance criteria
- [Source: _bmad-output/planning-artifacts/architecture.md:284-292 ADR-5] — provider-agnostic seam; FMP-specifics isolated; no FMP types leak; domain-model-only return
- [Source: _bmad-output/planning-artifacts/architecture.md:296-303 ADR-6] — curated FMP→Nautilus venue map; `AMBIGUOUS_LABELS` incl. `AMEX`; no guessed venues
- [Source: _bmad-output/planning-artifacts/architecture.md:413-430] — `N/A` sentinel pattern (single constant, three states) + venue-resolution pattern
- [Source: _bmad-output/planning-artifacts/architecture.md:474-482, 506-510, 522-531] — source-tree placement of `venue_map.py` / `providers/fmp_provider.py` + the provider boundary diagram
- [Source: src/models/instrument_metadata.py] — `InstrumentMetadata`, `AssetType`, `ResolutionStatus`, `NA_SENTINEL`, the `venue` validator (Story 1.2)
- [Source: src/services/metadata/fmp_client.py] — `FMPClient.fetch_profile` contract (`dict | None`); module style to mirror (Story 1.3)
- [Source: src/services/firstrate/instrument_mapper.py:36-53, 94, 127] — `_parse_ipo_date` shape; Nautilus venue codes are bare strings (`SPY.ARCA`, `AAPL.NASDAQ`)
- [Source: tests/unit/services/metadata/test_fmp_client.py] — unit-test idioms to mirror (AAA, `unittest.mock`)
- [Source: reference_fmp_api memory] — `/stable/profile` fields, the `exchange`-label venue gotcha (SPY→AMEX), `isEtf`/`isFund`

### Open questions (non-blocking — proceed with the documented default)
1. **Venue-map seed breadth:** seeded conservatively with `NASDAQ`/`NYSE` only; `AMEX` ambiguous. FMP may return other confident short labels (e.g. a distinct `ARCA`/`BATS` label, or Nasdaq tier variants) across the ~5,039-ticker run. Default: keep the seed minimal and let the Story 3.2 unresolved-venue report + `venue_overrides.csv` (3.3) drive additions, with each new map entry empirically verified against FMP. Confirm whether a wider initial seed is wanted (faster auto-resolution vs. stricter no-guess safety).
2. **`resolved_at` ownership:** the provider leaves `resolved_at = None`; Story 1.5's service is the natural place to stamp it at upsert (single clock site, deterministic provider tests). Confirm with 1.5 — if the provider should stamp it instead, that's a one-line change (and 1.5 tests would need to tolerate a non-`None` value).

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m]

### Debug Log References

- `make test-unit` → **1021 passed** in 12.55s (baseline 991 after Story 1.3 + 30 new = 1021; no regressions).
- New-module suites: `test_venue_map.py` 12 passed, `test_fmp_provider.py` 18 passed (56 total in `tests/unit/services/metadata/`).
- `make lint` → All checks passed (ruff auto-sorted imports in the two new test files; no F401/F821).
- `make typecheck` → Success: no issues found in 64 source files (`venue_map.py` + `providers/fmp_provider.py` both directly checked).

### Completion Notes List

- **Task 1 — `venue_map.py`:** `FMP_EXCHANGE_TO_VENUE` seeded conservatively (`NASDAQ`/`NYSE` only), `AMBIGUOUS_LABELS = {"AMEX"}`, `normalize_venue(label)` with no fallback venue (blank/ambiguous/unmapped → `None`; case/whitespace-insensitive). No-guess invariant covered by a dedicated test.
- **Task 2 — `FMPMetadataProvider`:** `PROVIDER_NAME = "FMP"`; `__init__(client=None)` injectable test seam (`client or FMPClient()`, CI-safe). `resolve(ticker) -> InstrumentMetadata` only — no FMP types leak. Module-level pure helpers `_descriptive` (→ `NA_SENTINEL`), `_parse_ipo_date` (local copy of the firstrate shape; blank/invalid → `None`, never sentinel), `_asset_type` (`isEtf`→ETF, `isFund`→FUND, else EQUITY). `_to_domain` sets `resolution_status` venue-driven (`RESOLVED`/`VENUE_UNRESOLVED`); `_degraded` returns the all-`N/A` record with a structlog breadcrumb. `resolved_at` left defaulted (`None`) per open question #2 (stamped by Story 1.5's service).
- **Task 3 — tests:** stub-client seam (`_StubClient`), no network/DB. Covers AC1 populated + gaps, AC2 asset types, AC3/AC4 venue resolution, the SPY descriptive-resolved/venue-unresolved regression guard, the degraded `None` record, and the no-leak/type contract. Venue-never-sentinel invariant verified implicitly (AMEX/degraded paths construct without the domain validator raising).
- **Task 4 — verify:** all gates green (see Debug Log). Sizes: `venue_map.py` 40 lines, `fmp_provider.py` 118 lines, `FMPMetadataProvider` class ~55 lines, all methods < 50, line length ≤ 100.
- **Scope honored:** no service/cache-check (1.5), no `ResolutionSummary` counting (1.6), no `venue_overrides.csv` (3.3), no `Protocol`/ABC, `fmp_client.py` untouched. Did not re-export the provider from `src/services/metadata/__init__.py` (optional; left as-is).

### File List

- `src/services/metadata/venue_map.py` (new)
- `src/services/metadata/providers/__init__.py` (new)
- `src/services/metadata/providers/fmp_provider.py` (new)
- `tests/unit/services/metadata/test_venue_map.py` (new)
- `tests/unit/services/metadata/test_fmp_provider.py` (new)
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified — status → review)

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-06-18 | Story 1.4 drafted (ready-for-dev): `FMPMetadataProvider` adapter (FMP profile JSON → `InstrumentMetadata` domain, `isEtf`/`isFund` → `AssetType`, descriptive `N/A` mapping, degraded unknown-ticker record) + `venue_map.py` (`FMP_EXCHANGE_TO_VENUE` seeded `NASDAQ`/`NYSE`, `AMBIGUOUS_LABELS`={`AMEX`}, `normalize_venue` with no guessed default). Status venue-driven (`RESOLVED`/`VENUE_UNRESOLVED`). Unit tests for venue map + provider with stubbed client (no network/DB). |
| 2026-06-18 | Story 1.4 implemented (→ review): `venue_map.py` + `providers/fmp_provider.py` added with TDD (Red→Green). 30 new unit tests (12 venue map + 18 provider); full suite 1021 passed, lint + typecheck clean. All 5 ACs satisfied; scope boundaries honored (no service/cache/summary/overrides). |
