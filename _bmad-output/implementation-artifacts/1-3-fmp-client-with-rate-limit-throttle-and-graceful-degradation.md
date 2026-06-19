# Story 1.3: FMP Client with Rate-Limit Throttle & Graceful Degradation

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the system,
I want a sync `httpx` FMP client that fetches a ticker profile within the rate limit and degrades gracefully on error,
so that bulk resolution across ~5,039 tickers never exceeds quota and a provider failure never aborts the import.

## Acceptance Criteria

1. **Given** an `FMPClient` (lazy-init, configured from `FMPSettings`), **When** `fetch_profile(ticker)` is called, **Then** it issues a single `GET /stable/profile?symbol={ticker}` with `apikey` auth.
2. **Given** the configured rate limit (default 300/min), **When** many tickers are resolved in bulk, **Then** requests are throttled (reusing the existing IBKR `RateLimiter` sliding-window pattern) and never exceed the quota.
3. **Given** a transient error response, **When** a request fails, **Then** the client retries with backoff.
4. **Given** an `httpx.TimeoutException` or `httpx.HTTPStatusError`, **When** it occurs, **Then** it is caught specifically (never a bare `except`) and surfaced as a degradation signal — never raised in a way that aborts the whole import.
5. **Given** `/stable/profile` returns an empty top-level array `[]` (ticker unknown to FMP), **When** the response is handled, **Then** it is treated as "no data" (to be mapped downstream to descriptive `N/A` + `VENUE_UNRESOLVED`), not as an error.
6. **Given** unit tests, **When** the client is tested, **Then** `httpx` is mocked and no real network call is made.

## Tasks / Subtasks

- [x] **Task 1: Create the `src/services/metadata/` package** (AC: #1) — the reusable, provider-agnostic keystone package
  - [x] Create `src/services/metadata/__init__.py` with a one-line docstring (mirror `src/services/__init__.py`). This is a **NEW package** and must live at `src/services/metadata/` — **NOT** under `src/services/firstrate/` (it is asset-class- and provider-agnostic). [Source: architecture.md:382-384, 474]
  - [x] Do **not** add `venue_map.py`, `fmp_provider.py`, `instrument_metadata_service.py`, or `venue_overrides.py` here — those are Stories 1.4/1.5. This story creates **only** `fmp_client.py` inside the package.

- [x] **Task 2: Sync sliding-window rate limiter** (AC: #2) — *write tests first (TDD Red→Green)*
  - [x] ⚠️ **The existing `RateLimiter` (`src/services/ibkr_client.py:52`) and `KrakenRateLimiter` (`src/services/kraken_client.py:200`) are `async`** (`asyncio.Lock` + `asyncio.sleep`, per-**second**). `FMPClient` is **sync** (ADR-4: "sync matches the CLI execution path"). You CANNOT reuse them directly. **Replicate the sliding-window deque *algorithm* in a sync form** — `time.monotonic()` + `time.sleep()`, no asyncio. [Source: ibkr_client.py:62-101; architecture.md:272-280 ADR-4]
  - [x] Define `class _FMPRateLimiter` (module-private) in `fmp_client.py`:
    - `__init__(self, requests_per_minute: int = 300, *, _now: Callable[[], float] = time.monotonic, _sleep: Callable[[float], None] = time.sleep)` — store `requests_per_minute`, `window = 60.0` (seconds), `self.requests: deque[float] = deque()`. The injectable `_now`/`_sleep` are a **test seam** so throttle behavior is verifiable without real 60s waits (a per-minute window makes real-time tests infeasible). [Source: kraken_client.py:200-222 sliding-window shape]
    - `def acquire(self) -> None`: loop — drop timestamps older than `now - window` from the left; if `len(self.requests) < requests_per_minute` append `now` and return; else `sleep_time = self.requests[0] + window - now`, `self._sleep(sleep_time)` if positive, then re-check. Mirror the IBKR `acquire` loop structure exactly, minus `async`/`await`/`Lock`. [Source: ibkr_client.py:84-101]
  - [x] Write `tests/unit/services/metadata/test_fmp_client.py` rate-limiter tests FIRST, modeled on `TestKrakenRateLimiter` (`tests/unit/services/test_kraken_client.py:568-612`): default rate (300), custom rate, within-limit-proceeds-immediately, exceeding-limit-calls-sleep. Use injected `_now`/`_sleep` fakes (e.g. a list-backed clock + a `sleep` spy that advances the clock) so tests run instantly and assert sleep was invoked with the expected window remainder. **No real `time.sleep`.**

- [x] **Task 3: `FMPClient` — lazy httpx, single profile GET, retry/backoff, graceful degradation** (AC: #1, #3, #4, #5) — *write tests first (TDD)*
  - [x] Define `class FMPClient` in `src/services/metadata/fmp_client.py`.
    - `__init__(self, settings: FMPSettings | None = None, *, max_retries: int = 3, backoff_base: float = 0.5, transport: httpx.BaseTransport | None = None)`:
      - `self._settings = settings or get_settings().fmp` (import `get_settings` from `src.config`). [Source: src/config.py — `Settings.fmp` from Story 1.1]
      - Store `max_retries`, `backoff_base` (no `FMPSettings` field carries these — Story 1.1 settings are `fmp_api_key`/`fmp_base_url`/`fmp_rate_limit`/`fmp_request_timeout` only; expose them as ctor params with defaults, mirroring `data_catalog.py`'s `max_retries: int = 3`). [Source: 1-1 story File List; data_catalog.py:773]
      - `self._rate_limiter = _FMPRateLimiter(self._settings.fmp_rate_limit)`.
      - `self._transport = transport` (test seam — lets unit tests inject `httpx.MockTransport`).
      - `self._client: httpx.Client | None = None` (**lazy** — do NOT create the httpx client in `__init__`; build on first use). [Source: architecture.md:273 "lazy-init"]
    - `def _get_client(self) -> httpx.Client`: lazily build/cache `httpx.Client(base_url=self._settings.fmp_base_url, timeout=self._settings.fmp_request_timeout, transport=self._transport)`. Passing `transport=None` to httpx is valid (uses the default real transport).
    - `def close(self) -> None` + `__enter__`/`__exit__`: close the underlying client if built (so the CLI path can use `with FMPClient() as c:`). Mirror standard httpx client lifecycle.
  - [x] `def fetch_profile(self, ticker: str) -> dict[str, Any] | None`:
    - `self._rate_limiter.acquire()` **before every attempt** (throttle covers retries too).
    - Build the request: `GET "/profile"` (the base URL already ends in `/stable`, so the effective URL is `…/stable/profile`), with `params={"symbol": ticker, "apikey": self._settings.fmp_api_key}`. **Do not** put the key in the path or log it. [Source: architecture.md:132 "GET /stable/profile?symbol={T}"; epics.md#Story-1.3 AC1]
    - `resp.raise_for_status()` then `data = resp.json()`.
    - **Empty-array handling (AC #5):** FMP returns a top-level JSON **array**. If `data == []` (or not a non-empty list) → `return None` ("no data", unknown ticker) — **not** an error, no retry. Otherwise return `data[0]` (the single profile dict). [Source: architecture.md:135; epics.md#Story-1.3 AC5]
    - Log a `structlog` debug/info on success with `ticker`, `provider="FMP"` (do NOT log `apikey`).
  - [x] **Retry/backoff (AC #3) + specific-exception degradation (AC #4):** wrap the attempt in a retry loop (`for attempt in range(self.max_retries + 1)`):
    - **Retry** (with exponential backoff `self.backoff_base * 2 ** attempt`, slept via `time.sleep`) on **transient** failures only: `httpx.TimeoutException`, `httpx.TransportError` (connection), and `httpx.HTTPStatusError` whose `resp.status_code == 429 or >= 500`.
    - **Do NOT retry** non-transient 4xx (e.g. 401/403/404). Catch them in the same `except (httpx.TimeoutException, httpx.HTTPStatusError)` block but break out.
    - On retries exhausted OR a non-transient caught error → **return `None`** (the degradation signal) and `logger.warning(...)`/`logger.error(...)` with fields `ticker`, `provider="FMP"`, `error=str(exc)` (and `status_code` if available). **Never re-raise in a way that aborts the import** (AC #4). The caller (Story 1.5 service) treats `None` as degradation. [Source: architecture.md:438-444 "Catch specific httpx exceptions … never bare except … never abort"]
    - **Never** use a bare `except:` or `except Exception:` around the HTTP call — catch the specific httpx types named in AC #4. [Source: epics.md#Story-1.3 AC4]
  - [x] **Return contract:** `dict | None`. `None` means "no usable profile" for **both** the empty-`[]` case (AC #5) and the degraded-error case (AC #4) — the architecture maps both to descriptive `N/A` + `VENUE_UNRESOLVED` downstream, so the client need not distinguish them in its return value (the distinction is captured in the log `error` field). Do **not** map to `NA_SENTINEL`/`VENUE_UNRESOLVED` here — that is the **provider's** job (Story 1.4). The client returns raw FMP JSON or `None`. [Source: architecture.md:135-136, 286-289 ADR-5 — no FMP-specific mapping in the client]

- [x] **Task 4: Unit tests — mocked httpx, no network** (AC: #1, #3, #4, #5, #6) — written alongside Tasks 2–3 (TDD)
  - [x] Create `tests/unit/services/metadata/__init__.py` (package marker — the existing tree uses `__init__.py` per test dir; see `tests/unit/__init__.py`). [Source: `find tests -name __init__.py`]
  - [x] **Mock httpx via `httpx.MockTransport`** injected through the `transport=` ctor seam (cleanest for httpx — no monkeypatching of internals, no network). A handler maps the request to a canned `httpx.Response`. Assert on `request.url.path == "/profile"`, `request.url.params["symbol"]`, and that `apikey` is present in params. This satisfies AC #6 (no real network) without `respx` (not a dependency). [Source: httpx MockTransport; pyproject has no respx]
  - [x] Tests to cover:
    - **AC #1:** a populated profile array → `fetch_profile("SPY")` returns `data[0]`; assert the request path/params (single GET, `symbol=SPY`, `apikey` sent).
    - **AC #5:** handler returns `[]` → returns `None`, and the transport was called **exactly once** (no retry on empty).
    - **AC #3:** handler returns `503` (or raises `httpx.TimeoutException`) for the first N calls then `200` → returns the profile; assert the transport was invoked `>1` time (retried). Patch `time.sleep` (module-level in `fmp_client`) so backoff doesn't actually wait.
    - **AC #4 (timeout):** handler always raises `httpx.TimeoutException` → `fetch_profile` returns `None` (degraded, not raised); assert it retried `max_retries+1` times then gave up.
    - **AC #4 (HTTPStatusError, non-transient):** handler returns `401` → returns `None`, **not retried** (assert one call), and an error/warning was logged.
    - **AC #4 (5xx exhausted):** handler always `500` → returns `None` after `max_retries+1` attempts.
    - **AC #2:** rate-limiter tests from Task 2 (instant via injected clock/sleep).
    - **AC #6:** no test makes a real network call (all via `MockTransport`); add an assertion-by-construction note. Patch `time.sleep` in the module so retry/backoff tests are instant.

- [x] **Task 5: Verify** (AC: all)
  - [x] `make test-unit` green (new `test_fmp_client.py` plus full suite — no regressions; baseline 965 unit tests from Story 1.2).
  - [x] `make lint` clean — watch the F401/F821 import gate: import `httpx`, `time`, `deque`, `Any`, `Callable`, `get_settings`, `FMPSettings`, `structlog` and ensure each is used in the same edit that adds it. [Source: CLAUDE.md "structural import gate"]
  - [x] `make typecheck` clean — `mypy` targets `src/core src/services strategies`, so `src/services/metadata/fmp_client.py` **is** directly checked. Full type annotations required (`-> dict[str, Any] | None`, etc.). [Source: CLAUDE.md commands; Makefile typecheck target]
  - [x] Size limits: `fmp_client.py` < 500 lines, `FMPClient`/`_FMPRateLimiter` classes < 100 lines each, methods < 50 lines, line length ≤ 100. [Source: CLAUDE.md Foundational Rules]

## Dev Notes

### Scope boundaries (do NOT do here)
- **Client only.** No `FMPMetadataProvider`, no `venue_map.py`, no `N/A`/venue mapping (Story 1.4). No `InstrumentMetadataService`, **no cache-check-before-fetch** (Story 1.5 — the cache check lives in the *service*, not the client). No `ResolutionSummary` counting (Story 1.6). The client's job is narrow: throttle + one GET + retry + return raw JSON or `None`. [Source: epics.md Stories 1.4–1.6; architecture.md:359 "Dual repositories … cache-check-before-fetch" is step 5, the service layer]
- **No FMP→domain mapping and no `InstrumentMetadata` import.** The client must not import the domain model or emit `NA_SENTINEL`/`VENUE_UNRESOLVED` — keeping FMP-specifics isolated is ADR-5's whole point (no FMP types leak; but equally, no domain types leak *into* the raw client). [Source: architecture.md:284-292 ADR-5]
- **Do not depend on `/stable/profile-bulk`** — it is often Premium/Ultimate-gated. One `GET /stable/profile` per ticker, period. [Source: architecture.md:133, 281]

### The sync-vs-async rate-limiter trap (the #1 gotcha)
Both in-repo rate limiters are `async`. The architecture says "reuse the IBKR `RateLimiter` pattern" — that means the **sliding-window deque algorithm**, *not* the async class. A sync client cannot `await limiter.acquire()`. Build `_FMPRateLimiter` as a plain sync class with `time.monotonic()`/`time.sleep()` and a `deque[float]` of monotonic timestamps, window = 60s, cap = `fmp_rate_limit` (300). Inject `_now`/`_sleep` for tests so you never sleep 60s in CI. [Source: ibkr_client.py:52-101 (async original); kraken_client.py:200-222]

### FMP request specifics (verified in planning)
- Base URL from `FMPSettings.fmp_base_url` already includes `/stable` (default `https://financialmodelingprep.com/stable`). So the request path is just `/profile` and the full URL is `…/stable/profile`. Don't double-append `/stable`. [Source: 1-1 story AC1; architecture.md:132]
- Auth is the `apikey` **query param** (not a header), alongside `symbol`. FMP `/stable/profile` returns a **top-level JSON array** (one element for a known ticker, `[]` for unknown). One call covers all PRD metadata fields — no second endpoint needed. [Source: architecture.md:132-135; reference_fmp_api memory]
- A wrong/expired key surfaces as HTTP 401/403 on **every** ticker → universal degradation to `None`. That is correct per-ticker fault-isolation behavior, but it would silently turn the whole run into N/A. **Mitigation:** log non-transient 4xx at `error` level with `status_code` so a misconfigured key is loud in the logs (and later visible as ~0 resolved in the Story 1.6 `ResolutionSummary`). See open question below.

### Graceful degradation contract
`fetch_profile` returns `dict | None` and **never raises a network error to the caller**. Transient failures (timeout, connection, 429, 5xx) retry with exponential backoff; exhausted or non-transient → `None`. Catch **only** `httpx.TimeoutException`, `httpx.TransportError`, `httpx.HTTPStatusError` — never a bare `except`/`except Exception` (the F-gate won't catch this; the reviewer/PR checklist will — AC #4 is explicit). Programming errors (e.g. `ValueError` from bad JSON shape) should NOT be swallowed by the httpx handler; let them surface (they indicate a bug, not a provider degradation). [Source: architecture.md:438-444; epics.md#Story-1.3 AC4]

### httpx testing — use `MockTransport`, not `respx`
`respx` is not a dependency and shouldn't be added for this. `httpx.MockTransport(handler)` is built into httpx 0.28.1 and slots straight into the `transport=` ctor seam, giving full request assertions with zero network. Patch the module-level `time.sleep` (`patch("src.services.metadata.fmp_client.time.sleep")`) so backoff/retry tests run instantly. The existing test style uses `unittest.mock` (`patch`, `MagicMock`) — stay consistent. [Source: tests/api/test_backtests.py:10; tests/unit/services/test_kraken_client.py:6]

### httpx is now a runtime dependency
Story 1.1 promoted `httpx>=0.28.1` from the dev group to `[project].dependencies` (commit `0a75de2`). It is importable in `src/` without any `pyproject.toml` change here. **Do not** run `uv add httpx` again. [Source: 1-1 story File List; CLAUDE.md "UV only"]

### Previous-story intelligence (1.1 + 1.2)
- **`FMP_API_KEY` is required** and seeded for tests by the autouse `_ensure_fmp_api_key` fixture in root `tests/conftest.py` — so any test constructing `FMPClient()` (which calls `get_settings().fmp`) gets a valid key without a `.env`. CI-safe. [Source: 1-1 story Dev Notes; 1-2 story Dev Notes:118]
- **TDD was followed strictly** in 1.1 and 1.2 (tests Red first). Do the same: rate-limiter and client tests before implementation.
- 1.2 delivered the persistence layer (`instrument_metadata` table, domain model, dual repos, migration `f051a079629c`) — but **none of it is touched here**. The client neither reads nor writes the cache (that wiring is the 1.5 service). [Source: 1-2 story File List]
- **pyproject.toml is `uv`-only** (bash-guard hook blocks hand edits) — irrelevant here (no new deps), noted for safety.

### Project Structure Notes
- **New files:** `src/services/metadata/__init__.py`, `src/services/metadata/fmp_client.py`, `tests/unit/services/metadata/__init__.py`, `tests/unit/services/metadata/test_fmp_client.py`.
- **Modified files:** none expected (the package `__init__.py` is new, not a modification). Do not edit `src/services/__init__.py`.
- File placement matches the architecture source tree exactly: `src/services/metadata/fmp_client.py` = "FMPClient: sync httpx + rate-limit throttle". [Source: architecture.md:474-477]
- Tests land in `tests/unit/services/metadata/` per the architecture test tree. [Source: architecture.md:506-509]

### Testing standards
- **Unit tier** (`make test-unit`, parallel) — pure logic, mocked httpx, no DB, no Nautilus, no network. This is the correct and only tier for 1.3. [Source: CLAUDE.md Decision Heuristics; ntrader-testing skill]
- TDD non-negotiable — failing tests first for `_FMPRateLimiter` and `FMPClient`. [Source: development-principles.md]
- Mirror Arrange/Act/Assert + `unittest.mock.patch` idioms from `tests/unit/services/test_kraken_client.py`.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.3] — story statement + the 6 acceptance criteria
- [Source: _bmad-output/planning-artifacts/architecture.md:270-282 ADR-4] — sync httpx, one profile call, throttle, retry-with-backoff, graceful degradation, no profile-bulk
- [Source: _bmad-output/planning-artifacts/architecture.md:284-292 ADR-5] — provider-agnostic seam; FMP-specifics isolated (client returns raw, no domain mapping)
- [Source: _bmad-output/planning-artifacts/architecture.md:438-446] — FMP client error & reporting pattern (cache-check is service-layer; catch specific httpx exceptions; structlog fields)
- [Source: _bmad-output/planning-artifacts/architecture.md:474-477, 506-509] — source-tree placement of `fmp_client.py` and its unit tests
- [Source: src/services/ibkr_client.py:52-101] — sliding-window `RateLimiter` algorithm to replicate in **sync** form
- [Source: src/services/kraken_client.py:200-222] — second sliding-window precedent (`KrakenRateLimiter`)
- [Source: tests/unit/services/test_kraken_client.py:568-612] — rate-limiter unit-test shape to mirror
- [Source: src/services/data_catalog.py:773] — `max_retries: int = 3` precedent
- [Source: src/config.py] — `Settings.fmp` / `FMPSettings` (Story 1.1); `get_settings()`
- [Source: tests/conftest.py] — autouse `_ensure_fmp_api_key` (CI-safe `Settings()` construction)
- [Source: src/services/firstrate/import_service.py:23] — `logger = structlog.get_logger(__name__)` pattern

### Open questions (non-blocking — proceed with the documented default)
1. **Bad-API-key handling:** the documented behavior degrades a 401/403 to `None` per ticker (fault isolation) and logs it loudly at `error` level. An alternative is to fail fast on a 401 (it's a config error, not a data gap). Default chosen: **degrade + loud log**, consistent with NFR15 "never abort the whole import." Confirm with the team if fail-fast-on-auth is preferred.
2. **`max_retries`/`backoff_base` as ctor params** (defaults 3 / 0.5s) rather than new `FMPSettings` fields — keeps Story 1.1's settings surface frozen. If these should be env-configurable, that's a small follow-up to `FMPSettings`.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8[1m] (BMAD dev-story workflow)

### Debug Log References

- `make test-unit` → 990 passed (965 baseline + 25 new), no regressions.
- `make lint` → clean. `make typecheck` → success (61 source files).
- Live smoke test against real FMP API (key from `.env`): `SPY` → profile dict
  (`State Street SPDR S&P 500 ETF Trust`, exchange `AMEX`); unknown ticker `ZZZZ…`
  → `None`. Logs showed only `ticker`/`provider` (no `apikey` leak).

### Completion Notes List

- TDD followed strictly: 25 failing tests written first (Red — `ModuleNotFoundError`),
  then `fmp_client.py` implemented to Green.
- **httpx base-URL join verified empirically** before coding: `httpx.Client(base_url=".../stable")`
  + `get("/profile")` yields path `/stable/profile` (httpx's `_merge_url` preserves the
  base path; it is NOT naive RFC-3986 urljoin). Tests assert `path == "/stable/profile"`.
- **Sync rate limiter:** `_FMPRateLimiter` replicates the IBKR/Kraken sliding-window deque
  algorithm in sync form (`time.monotonic`/`time.sleep`, 60s window). Injectable `_now`/`_sleep`
  seams keep throttle tests instant (deterministic fake clock; the fake `sleep` overshoots by
  a 1e-9 epsilon, mirroring real `time.sleep`, so a request on the exact window boundary ages out).
- **Graceful degradation:** catches `httpx.HTTPStatusError` and `(httpx.TimeoutException,
  httpx.TransportError)` only — never a bare `except`. Transient (429/5xx/timeout/transport)
  retried with exponential backoff (`backoff_base * 2**attempt`); non-transient 4xx logged at
  `error` (loud, so a bad key is visible) and not retried. All failures degrade to `None`.
  A non-httpx bug (e.g. `ValueError`) is deliberately NOT swallowed (test asserts it surfaces).
- **apikey never logged:** degradation logs use `error=f"HTTP {status}"` for status errors (the
  full URL — which carries `apikey` — is never put in a log field).
- **Doc discrepancy noted (non-blocking):** Dev Notes claimed an autouse `_ensure_fmp_api_key`
  fixture exists in `tests/conftest.py` — it does NOT. Not needed: `FMPSettings.fmp_api_key`
  defaults to `""`, so `FMPClient()` construction is CI-safe, and unit tests inject `MockTransport`.
- **Refactor:** extracted a `_degraded()` helper (DRY for the 3 degradation log sites) to keep
  the `FMPClient` class within the <100-line convention (99 lines; file 169 lines).
- Open questions 1 (degrade-on-401 vs fail-fast) and 2 (`max_retries`/`backoff_base` as ctor
  params, not `FMPSettings` fields) implemented per the documented defaults.

### File List

- `src/services/metadata/__init__.py` (new) — package marker + docstring
- `src/services/metadata/fmp_client.py` (new) — `_FMPRateLimiter` + `FMPClient`
- `tests/unit/services/metadata/__init__.py` (new) — test package marker
- `tests/unit/services/metadata/test_fmp_client.py` (new) — 25 unit tests (MockTransport, no network)

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-06-17 | Story 1.3 drafted (ready-for-dev): sync `FMPClient` (lazy httpx, single `/stable/profile` GET, `apikey` auth) with a sync sliding-window `_FMPRateLimiter` (300/min), exponential-backoff retry on transient errors, specific-httpx-exception graceful degradation to `None`, empty-`[]` "no data" handling. Unit tests via `httpx.MockTransport` (no network). New `src/services/metadata/` package. |
| 2026-06-18 | Story 1.3 implemented (review): `src/services/metadata/{__init__,fmp_client}.py` + 25 unit tests. All 6 ACs satisfied. `make test-unit` 990 passed (no regressions), lint clean, typecheck success. Live-smoke verified against real FMP API (SPY resolved; unknown ticker → `None`; no apikey leak). |
