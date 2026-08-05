# Story 1.2: Isolate the Live Client ID from the Historical Data Client

Status: in-progress

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want the live session to use a different IBKR client ID than the catalog fetcher,
so that I can run a live session while a historical import is in flight without either evicting the
other.

## Acceptance Criteria

1. **Given** `IBKRSettings`, **When** the settings are loaded, **Then** a new `ibkr_live_client_id`
   field exists with default `10`, configurable via env `IBKR_LIVE_CLIENT_ID` — **And**
   `ibkr_client_id` keeps its default of `1` (FR5, AR18).
2. **Given** a configuration where `ibkr_live_client_id == ibkr_client_id`, **When** `IBKRSettings`
   is instantiated, **Then** a Pydantic `ValidationError` is raised at construction whose message
   names **both** field names — **And** the error comes from a `@model_validator(mode="after")`, so
   it fires regardless of which of the two fields was overridden (env, init kwarg, or `.env` file).
3. **Given** the documented client-ID reservation, **When** a developer reads the field
   descriptions, **Then** they state the allocation: historical = `ibkr_client_id`, live session =
   `ibkr_live_client_id`, on-demand reconcile = `ibkr_live_client_id + 1` (AR34 — reserved here,
   consumed in Epic 4 Story 4.6).
4. **Given** this repository's own documented configuration (`.env.example`, `docs/setup/IBKR_SETUP.md`,
   `docker-compose.yml`, `README.md`), **When** an operator follows it verbatim, **Then** no path
   yields `ibkr_client_id == ibkr_live_client_id` and no path leaves the historical client's
   *effective* ID range overlapping the live/reconcile reservation — **And** `IBKR_LIVE_CLIENT_ID`
   reaches the app under Docker as well as on bare metal.
5. **Given** `DataCatalogService.ibkr_client`, **When** `IBKR_CLIENT_ID` is unset in the
   environment, **Then** its fallback resolves to the historical default `1`, not `10` — so the
   unset-env path cannot silently occupy the live session's reserved ID
   (`src/services/data_catalog.py:138`).
6. **Given** the test suite, **When** `tests/unit/test_ibkr_config.py` runs, **Then** it covers the
   default, the env override, the equality rejection from both override directions, and the
   non-equal permit — requiring no broker, network, or database (NFR32, NFR34).

## Tasks / Subtasks

- [x] **Task 0: Clear the collision that will otherwise break the whole app** (AC: #4) — **DO THIS
      FIRST. Read this task fully before writing a line of code.**
  - [x] **The problem, verified empirically on this machine:** the local `.env` sets
        `IBKR_CLIENT_ID=10` (`.env:11`). `IBKRSettings` reads `.env` through its own
        `model_config["env_file"]` (`src/config.py:82-87`), so today
        `IBKRSettings().ibkr_client_id == 10` and `get_settings().ibkr.ibkr_client_id == 10`
        (`IBKRSettings(_env_file=None)` returns `1` — the `.env` is the only reason it is 10).
        The moment AC #1's default `10` and AC #2's equality validator land, **every**
        `IBKRSettings()` / `Settings()` / `get_settings()` construction on this machine raises
        `ValidationError`. That is not a test failure — it takes down the CLI (`src/cli/main.py`),
        the web app, `src/db/session.py`, and the ~11 test files that build real settings.
  - [x] **This is correct behaviour, not a bug to design around.** `IBKR_CLIENT_ID=10` genuinely
        collides with the reservation. Do **not** weaken the validator, do not pick a different
        default, do not add an escape hatch. Fix the configuration.
  - [x] **Escalate to Allay before proceeding.** `.env` is blocked by
        `.claude/hooks/protect-files.sh` (matches `.env*` by basename), and it holds live
        credentials. Story 1.1 hit the same block on `.env.example` and resolved it by raising it
        rather than bypassing the hook — do the same. Ask for `.env:11` to be changed to
        `IBKR_CLIENT_ID=1`, which is what `.env.example:25` and `docker-compose.yml:86` already
        specify. **Recommended resolution: change `.env`, not the defaults.**
  - [x] **Why `1` and not some other number** — `IBKRHistoricalClient.connect()` rotates
        `base_client_id + offset` for `offset in 1..max_id_rotations`, default `5`
        (`src/services/ibkr_client.py:188-219`). The historical client's *effective* range is
        therefore `[base, base+5]`. With `base=1` that is **1–6**, leaving live `10` and reconcile
        `11` untouched. With today's `base=10` the rotation range is **10–15**, which swallows both
        the live session ID and the reconcile ID — the exact eviction FR5 exists to prevent, and
        one the equality validator cannot see because it compares configured bases only.
  - [x] Confirm the fix before continuing:
        `uv run python -c "from src.config import IBKRSettings; print(IBKRSettings().ibkr_client_id)"`
        must print `1`.
  - [x] If Allay declines to change `.env`, **stop and report** — do not proceed with a partial
        implementation that leaves the repo unable to construct settings.
- [x] **Task 1: Write the failing unit tests first (TDD Red)** (AC: #1, #2, #6)
  - [x] Extend `tests/unit/test_ibkr_config.py` (do **not** create a new file — the settings tests
        for `IBKRSettings` already live there, including Story 1.1's three
        `ntrader_real_money_account` tests at `:112-156`). Add a
        `class TestIBKRClientIdIsolation` alongside the existing `TestIBKRConfiguration`.
  - [x] Mark every new test `@pytest.mark.unit` — `pytest.ini:25` registers it and
        `--strict-markers` is on (`pytest.ini:15`), so an unregistered marker is a hard error.
        `pytest.ini`, not `pyproject.toml`, is the effective config.
  - [x] Follow the Arrange/Act/Assert + `monkeypatch` + `_env_file=None` idiom from
        `tests/unit/services/test_kraken_settings.py:19-28`. **Always pass `_env_file=None`** in
        these tests so a developer's `.env` cannot decide the outcome, and clear **both** casings of
        any env var you depend on — `case_sensitive: False` (`src/config.py:85`) makes the lowercase
        spelling an equally valid alias. Story 1.1's review patched exactly this omission:
        ```python
        monkeypatch.delenv("IBKR_CLIENT_ID", raising=False)
        monkeypatch.delenv("ibkr_client_id", raising=False)
        monkeypatch.delenv("IBKR_LIVE_CLIENT_ID", raising=False)
        monkeypatch.delenv("ibkr_live_client_id", raising=False)
        ```
  - [x] **Defaults** (AC #1): with all four env names cleared and `_env_file=None`, assert
        `ibkr_live_client_id == 10` **and** `ibkr_client_id == 1`. Assert both in the same test —
        the pair is the contract, not either value alone.
  - [x] **Env override** (AC #1): `monkeypatch.setenv("IBKR_LIVE_CLIENT_ID", "20")` →
        `IBKRSettings(_env_file=None).ibkr_live_client_id == 20`. Confirms the field-name →
        UPPER_SNAKE mapping works with no `env_prefix`.
  - [x] **Equality rejection, both directions** (AC #2) — this is what proves it is a *model*
        validator and not a field validator on one side:
        | override | value | expectation |
        |---|---|---|
        | `ibkr_live_client_id` | `1` (collides with the default `ibkr_client_id`) | `ValidationError` |
        | `ibkr_client_id` | `10` (collides with the default `ibkr_live_client_id`) | `ValidationError` |
        | both, equal | `7` / `7` | `ValidationError` |
        | both, distinct | `1` / `10` | constructs, no error |
        Use `pytest.raises(ValidationError)` (`from pydantic import ValidationError`, as
        `test_kraken_settings.py:6` does). Assert **both** literal strings `"ibkr_client_id"` and
        `"ibkr_live_client_id"` appear in `str(excinfo.value)` — AC #2 requires the error to name
        both fields, and an operator reading a traceback needs to know which two settings to
        reconcile.
  - [x] **Env-driven collision**: set `IBKR_CLIENT_ID=10` via `monkeypatch` with
        `_env_file=None` and no init kwargs → `ValidationError`. This is the real-world shape of the
        failure (a stale env var), and it is the one Task 0 just cleared out of `.env`.
  - [x] **Reservation documentation** (AC #3): assert the reservation is machine-readable from the
        field metadata, so it cannot rot silently:
        ```python
        desc = IBKRSettings.model_fields["ibkr_live_client_id"].description
        assert "ibkr_client_id" in desc and "+ 1" in desc
        ```
        Keep the assertion loose (substrings, not full-string equality) — it must survive a reworded
        description but fail if the reservation is dropped.
  - [x] Run and confirm **RED** before writing any implementation.
- [x] **Task 2: Add the field and the validator (Green)** (AC: #1, #2, #3)
  - [x] In `src/config.py`, add immediately after `ibkr_client_id` (`src/config.py:20`):
        ```python
        ibkr_live_client_id: int = Field(
            default=10,
            description=(
                "Client ID for the live trading session's IBKR data + execution clients. "
                "Reservation: historical fetch = ibkr_client_id (which rotates up to "
                "ibkr_client_id + 5 on connect retries), live session = ibkr_live_client_id, "
                "on-demand reconcile = ibkr_live_client_id + 1. Must differ from ibkr_client_id."
            ),
        )
        ```
  - [x] Amend `ibkr_client_id`'s description (`src/config.py:20`) from the bare "Unique client
        identifier" to name its half of the reservation and its rotation range, so the two
        descriptions read as one allocation table rather than two unrelated fields.
  - [x] Add the model validator on `IBKRSettings`, mirroring `KrakenSettings.validate_key_secret_pair`
        (`src/config.py:105-126`) — same `@model_validator(mode="after")` decorator, same
        `raise ValueError(...)` style, same `-> "IBKRSettings"` return annotation. `model_validator`
        is **already imported** at `src/config.py:10`; do not add an import (an unused or duplicated
        import hard-blocks the commit — see Dev Notes "Commit hygiene").
        ```python
        @model_validator(mode="after")
        def validate_client_ids_distinct(self) -> "IBKRSettings":
            """Live and historical clients must not share an IBKR client ID.

            IBKR evicts the older connection when two clients present the same ID, so a
            shared ID would silently drop either the running session or an in-flight
            historical import (FR5).
            """
            if self.ibkr_live_client_id == self.ibkr_client_id:
                raise ValueError(
                    f"ibkr_client_id and ibkr_live_client_id must differ "
                    f"(both are {self.ibkr_client_id}). Reservation: historical="
                    f"ibkr_client_id, live session=ibkr_live_client_id, "
                    f"reconcile=ibkr_live_client_id + 1."
                )
            return self
        ```
  - [x] Place the validator **after** the `get_market_data_type_enum()` method and **before**
        `model_config`, matching how `KrakenSettings` orders fields → validator → `model_config`.
  - [x] **Scope discipline — do not add any of these:** `ge=`/`le=` bounds on either field, a check
        that `ibkr_live_client_id + 1 != ibkr_client_id`, rotation-range overlap detection, or a
        normalizing `BeforeValidator`. AC #2 specifies equality and nothing else. Wider checks are
        recorded as open questions at the end of this file for Allay to rule on.
  - [x] Run the tests to **GREEN**.
- [x] **Task 3: Make the repo's own documentation consistent with the reservation** (AC: #3, #4)
  - [x] `docs/setup/IBKR_SETUP.md:44` currently instructs `IBKR_CLIENT_ID=10  # Can be any number
        (1-999)` — **this is where the local `.env`'s colliding value came from.** Change it to
        `IBKR_CLIENT_ID=1`, and replace the "any number" comment with the reservation: historical
        `1` (rotates 1–6 on retry), live session `10`, reconcile `11`. Add `IBKR_LIVE_CLIENT_ID=10`
        to the same block.
  - [x] `docs/setup/IBKR_SETUP.md:155` — the Error 326 troubleshooting step says "Change
        `IBKR_CLIENT_ID` in `.env` to a different number (1-999)". Bound it: pick a value **below
        the live reservation**, since 10 and 11 are taken and the historical client auto-rotates
        five IDs above whatever base you choose.
  - [x] `README.md:412` — add an `IBKR_LIVE_CLIENT_ID` row directly under the existing
        `IBKR_CLIENT_ID` row (default `10`, description naming the reservation). CLAUDE.md makes
        keeping README in sync a standing rule.
  - [x] `docker-compose.yml` — add `IBKR_LIVE_CLIENT_ID: ${IBKR_LIVE_CLIENT_ID:-10}` immediately
        after `IBKR_CLIENT_ID: ${IBKR_CLIENT_ID:-1}` (`docker-compose.yml:86`) in the `ntrader-app`
        `environment:` block. **Not optional:** `ntrader-app` has no `env_file:` directive and the
        image never copies `.env`, so a setting absent from this block is unreachable in a
        container. Story 1.1's code review caught exactly this omission for
        `NTRADER_REAL_MONEY_ACCOUNT` (`docker-compose.yml:94`) — do not repeat it. Verify with
        `docker compose config` (no daemon needed) and confirm `IBKR_LIVE_CLIENT_ID: "10"` resolves
        into `ntrader-app`.
  - [x] `.env.example` — add `IBKR_LIVE_CLIENT_ID=10` under `IBKR_CLIENT_ID=1` (`.env.example:25`)
        with a two-line comment stating the reservation and that the two must differ.
        **`protect-files.sh` blocks `.env*` through Edit/Write** — this is the same escalation Task 0
        already raises. Batch it into that one conversation with Allay rather than discovering it
        mid-task; Story 1.1's precedent was an approved one-off shell write, with the hook left
        unchanged.
- [x] **Task 4: Close the unset-env fallback that occupies the live ID** (AC: #5)
  - [x] `src/services/data_catalog.py:138` reads
        `os.environ.get("IBKR_CLIENT_ID", "10")` — when `IBKR_CLIENT_ID` is unset the historical
        catalog client connects on **10**, the live session's reserved ID, with a rotation range of
        10–15 that also swallows reconcile's 11. The equality validator cannot catch this: it
        compares settings fields, and this code path never reads settings at all.
  - [x] Change the fallback literal `"10"` → `"1"` and update the docstring at
        `src/services/data_catalog.py:129` (`IBKR_CLIENT_ID (default: 10)`) to match. One literal,
        one docstring line — this aligns the unset-env path with `.env.example:25` and
        `docker-compose.yml:86`, which both already say `1`.
  - [x] Update the one test that pins the old fallback:
        `tests/unit/services/test_data_catalog.py:355` asserts
        `client_id=10` for the defaults case. Change to `client_id=1`. Leave
        `test_data_catalog.py:333` alone — it passes an explicit `42` and is unaffected.
  - [x] **Do not** refactor this path onto typed settings in this story. Routing it through
        `settings.ibkr.ibkr_client_id` is the right long-term fix per CLAUDE.md ("never hardcode
        IBKR connection details") but it changes the catalog import path's configuration source,
        which is outside FR5's footprint. Record it in
        `_bmad-output/implementation-artifacts/deferred-work.md` under a new
        `## Deferred from: story-1.2` heading instead.
- [x] **Task 5: Verify** (AC: all)
  - [x] `make test-unit` — new tests green **and** no regressions. Expect the baseline from Story
        1.1 (**1539 passed**) plus your new tests; a *drop* from 1539 or any error mentioning
        `ValidationError` on `IBKRSettings` means Task 0 was not actually resolved.
  - [x] `make format && make lint` — clean. (`make typecheck` covers `src/core src/strategies` only,
        so `src/config.py` is not in its scope; run it anyway to confirm nothing else moved.)
  - [x] Smoke-test the real construction path — the failure mode this story can cause is total, so
        prove it directly:
        ```bash
        uv run python -c "from src.config import get_settings; s=get_settings().ibkr; print(s.ibkr_client_id, s.ibkr_live_client_id)"
        uv run python -m src.cli.main --help
        ```
        Expect `1 10` and a normal help screen.
  - [x] `docker compose config` — valid, with `IBKR_CLIENT_ID: "1"` and `IBKR_LIVE_CLIENT_ID: "10"`
        both resolving into `ntrader-app`.
  - [x] Confirm `pyproject.toml` and `uv.lock` are unchanged (AR3 — zero new dependencies).

### Review Findings

_Code review 2026-08-04 — three adversarial layers (Blind Hunter, Edge Case Hunter, Acceptance
Auditor). All findings below were re-verified empirically against the working tree before being
recorded; two subagent claims were disproved and dropped._

**Resolution:** both decisions were ruled on by Allay (widen the validator; add `ge=1`, skip
`le=999`) and applied TDD Red→Green, along with every patch except the `.env.example` rewrite,
which `protect-files.sh` blocks pending explicit approval. Post-change verification: **1561 unit
tests pass** (baseline 1547 + 14 new), `make format` / `make lint` / `make typecheck` clean,
`get_settings().ibkr` → `1 10`, CLI help normal, `docker compose config` resolves `1`/`10`,
`pyproject.toml` and `uv.lock` unchanged. Boundary sweep confirms bases `5`–`11` are now rejected
and `1`–`4` accepted at the default live ID.

**Decision needed** — both are the open questions Task 2's scope note deliberately left for Allay.
They are recorded here because the review independently reached the same two gaps from three
directions, which raises their priority above "someday".

- [x] [Review][Decision] **The validator rejects equality only, so the rotation range still walks
      over both reserved IDs** — `IBKRHistoricalClient.connect()` rotates `base..base+5`
      (`src/services/ibkr_client.py:218-219`), so with the default `ibkr_live_client_id=10` every
      base in `5..11` passes `validate_client_ids_distinct` and still collides: `5..10` reaches the
      live ID, `6..11` reaches reconcile's `11`. Verified — `IBKRSettings(_env_file=None,
      ibkr_client_id=5)` constructs cleanly, as does `ibkr_client_id=11`. The reverse direction is
      equally open: `ibkr_client_id=1` with `ibkr_live_client_id=3` also constructs, putting the
      live ID inside the historical rotation window. The equality check rejects exactly one of the
      seven colliding values. Options: (a) widen the validator to reject range intersection,
      (b) leave as-is and rely on documentation, (c) defer to Story 1.3 when the live ID gains its
      first consumer. [src/config.py:107]
- [x] [Review][Decision] **Neither field has bounds; `0` is IBKR's master client ID** — verified
      constructing successfully: `ibkr_client_id=0`, `-5`, `2147483648`, and
      `ibkr_live_client_id=0`. Client ID `0` binds to orders placed manually in TWS, which is the
      exact cross-talk this story exists to prevent; `-5` additionally makes rotation walk `-5..0`
      through it. The change also *removed* the only stated bound (the old `IBKR_SETUP.md:44`
      "any number (1-999)") without replacing it with an enforced one. Note `kraken_rate_limit`
      already uses `ge=1, le=20` as in-file precedent (`src/config.py:129-131`). Options:
      (a) add `ge=1, le=999` to both fields, (b) bound in documentation only, (c) accept.
      [src/config.py:20-37]

**Patch**

- [x] [Review][Patch] **The new validator leaks secrets into every traceback it produces** — the
      first `model_validator` on `IBKRSettings`, so this vector is introduced by this change.
      pydantic-settings feeds the *entire* environment in as the model input (`extra: "ignore"`),
      and pydantic renders `input_value={...}` on a model-validator failure. Verified with
      `IBKR_CLIENT_ID=10`: the message ends `...LKQos12kfQvNNLxhw7Tvk0'}`, which is the last 22 of
      the 32 characters of `FMP_API_KEY`. `repr=False` on `tws_password`/`tws_account` does not
      help — the leak is in the raw input dict, not the model repr. This fires precisely when an
      operator is misconfigured and most likely to paste the traceback into an issue, a chat, or
      CI output. Fix: add `"hide_input_in_errors": True` to `model_config`.
      [src/config.py:116-121]
- [x] [Review][Patch] **Two script entry points still default the client ID to `10`** — the same
      stale fallback AC #5 fixed in `data_catalog.py`, left in place elsewhere. Both read
      `os.environ` directly and never construct `IBKRSettings`, so the validator is structurally
      blind to them, and both then rotate `10→15`, swallowing reconcile's `11` too. The reconnect
      probe is the worse of the two: its modes are `no-stop` and `fetch-then-hang`, i.e. it is
      designed to hold the ID without releasing it. Fix: `10` → `1`, matching
      `data_catalog.py:139`. [scripts/diagnostics/ibkr_reconnect_probe.py:139,
      scripts/venue/resolve_venues_ibkr.py:143]
- [x] [Review][Patch] **`IBKR_SETUP.md` contradicts itself three ways on the safe range** — `:92`
      says raising the base to `8` "would silently swallow both reserved IDs"; `:96-97` then lands
      on the rule "Keep `IBKR_CLIENT_ID` below the live reservation", which permits that very `8`
      (and `5`–`9`); `:181` says "stay at `4` or lower"; and the table at `:85` presents `1–6` as
      the historical reservation while `:181` authorizes base `4`, whose range reaches `9`. An
      operator gets a different answer depending on which line they read. Fix: state one rule —
      `1`–`4`, i.e. at least 6 below `IBKR_LIVE_CLIENT_ID` — everywhere.
      [docs/setup/IBKR_SETUP.md:85,92,96-97,181]
- [ ] [Review][Patch] **PARTIAL — `.env.example` still says "must differ"** — `README.md:412-413`
      and both field descriptions (`src/config.py`) are done: they now state the 1–4 bound and that
      the ranges must not overlap in either direction. `.env.example:25-29` is **not** done —
      `.claude/hooks/protect-files.sh` blocks `.env*` through Edit/Write, and this needs the same
      one-off approval Task 0 and Story 1.1 used. It is the file that gets copied to `.env`, and it
      still hardcodes "effective range 1-6" as though fixed rather than `base..base+5`, so it gives
      no signal that changing the base moves the range. Verified the current values (`1`/`10`) do
      construct successfully, so this is wording only, not a broken example.
      [.env.example:25-29]
- [x] [Review][Patch] **The range predicate recorded in `deferred-work.md` is wrong** — it proposes
      rejecting `ibkr_client_id <= ibkr_live_client_id + 1 <= ibkr_client_id + 5`, which tests only
      whether *reconcile* lands in the rotation window. With `ibkr_client_id=5,
      ibkr_live_client_id=10` it evaluates `5 <= 11 <= 10` → false, so it would not reject, even
      though the range `5..10` swallows the live ID. Since this is the exact question Allay is
      being asked to rule on, the predicate must be correct: it needs to test the live ID too
      (`base <= live <= base+5 or base <= live+1 <= base+5`).
      [_bmad-output/implementation-artifacts/deferred-work.md:81-83]
- [x] [Review][Patch] **No upgrade note for operators the old docs told to use `10`** — the
      pre-change `IBKR_SETUP.md:44` instructed `IBKR_CLIENT_ID=10  # Can be any number (1-999)`.
      Anyone who followed it now gets an uncaught `ValidationError` from `get_settings()` at module
      scope, making *every* command unreachable including `--help` (verified via
      `src/cli/main.py:19` and `src/db/session.py:11`). The troubleshooting section was edited in
      this same change and still does not mention it. Fix: one line in the Error-326 /
      troubleshooting block. [docs/setup/IBKR_SETUP.md]
- [x] [Review][Patch] **The new docstring hardcodes `10`/`11` although both derive from a
      configurable setting** — "10 and 11 belong to the live session and reconcile" becomes false
      the moment `IBKR_LIVE_CLIENT_ID` is set to anything else, which `test_live_client_id_loads_
      from_env` proves is supported by setting it to `20`. Fix: name the setting, not its current
      default. [src/services/data_catalog.py:129-130]
- [x] [Review][Patch] **The validator docstring and the reference doc state opposite failure
      modes** — `src/config.py:103` asserts "IBKR evicts the older connection", while
      `docs/setup/IBKR_SETUP.md:78` says a duplicate ID "either gets error 326 or evicts the
      incumbent". These are materially different outcomes: error 326 refuses the *newcomer* (loud,
      safe), eviction kills the live session mid-position (silent, severe). The justification for
      hard-failing at construction rests on the severe reading. Fix: state one, with the condition
      under which each occurs. [src/config.py:101-106, docs/setup/IBKR_SETUP.md:78]

**Deferred** — pre-existing, not caused by this change. Recorded in `deferred-work.md`.

- [x] [Review][Defer] **`DataCatalogService` bypasses typed settings entirely** — already recorded
      by the dev under `## Deferred from: story-1.2`; the review independently confirmed it from
      two layers. No new entry needed. [src/services/data_catalog.py:135-141]
- [x] [Review][Defer] **`ENV=dev|qa` does not propagate to nested `IBKRSettings`, and `.env.dev` /
      `.env.qa` still hold `IBKR_CLIENT_ID=10`** — deferred, pre-existing (recorded from Story
      1.1). Verified inert *today*: `ENV=dev` yields `ibkr_client_id=1`, because the nested
      `default_factory=IBKRSettings` reads `.env` regardless. That inertness is the hazard — the
      day propagation is fixed, dev and qa fail at startup. Both files are untracked and local.
      [src/config.py:302-304, .env.dev:15, .env.qa:15]
- [x] [Review][Defer] **`--client-id 0` is silently swallowed** — `client_id or
      settings.ibkr.ibkr_client_id` treats `0` as unset and prints the fallback in the confirmation
      table. `scripts/venue/resolve_venues_ibkr.py:143` uses `is not None` for the same option, so
      the two paths disagree. Untouched by this change. [src/cli/commands/data.py:463]
- [x] [Review][Defer] **`IBKR_CLIENT_ID=` (set but empty) crashes with a bare `ValueError`** — the
      `os.environ.get(..., "1")` default applies only when *unset*, so `int("")` raises with no
      mention of which variable is at fault, and the docstring's "default: 1" does not hold. The
      existing test covers unset, not set-empty. Pre-existing pattern.
      [src/services/data_catalog.py:139-142]
- [x] [Review][Defer] **Entry points surface raw pydantic tracebacks for config errors** — no
      readable config-error path at `src/cli/main.py` or the web app. Pre-existing; interacts with
      the upgrade-breakage patch above. [src/cli/main.py:19, src/db/session.py:11]

**Dismissed (4)** — recorded so they are not re-raised next review.

- `test_data_catalog.py:355` "has no environment isolation" — **false**. It runs under
  `patch.dict(os.environ, {}, clear=True)`; the whole environment is cleared.
- "`model_validator` import may be missing (F821)" — **false**. Already imported at
  `src/config.py:10`; lint and typecheck pass.
- "`ibkr_live_client_id` has no consumer and `+ 1` is unenforced" — **by design**. Story 1.3 wires
  the consumer, Story 4.6 uses `+ 1`; this story's scope note forbids adding either here.
- "`test_reservation_is_documented_in_field_metadata` asserts prose, not behavior" — **working as
  specified**. Task 1 deliberately designed it as a loose-substring anti-rot guard on the field
  metadata, and it does exactly that.

## Dev Notes

### What this story is, and how small it should stay
One field, one validator, and the documentation and container plumbing that make the reservation
real rather than aspirational. There is **no new module**, no CLI, no Nautilus, no test file. If
the diff grows past `src/config.py`, `tests/unit/test_ibkr_config.py`, `src/services/data_catalog.py`
(one literal), `tests/unit/services/test_data_catalog.py` (one literal), four documentation files,
and `docker-compose.yml`, something has gone wrong.

### The client-ID allocation, in full
| Consumer | ID | Effective range | Arrives in |
|---|---|---|---|
| Historical catalog fetch | `ibkr_client_id` = `1` | **1–6** (rotates `base+1..base+5` on connect timeout, `src/services/ibkr_client.py:188-219`) | already shipped |
| Live session (data + exec clients) | `ibkr_live_client_id` = `10` | 10 | Story 1.3 consumes it |
| On-demand `reconcile` (read-only) | `ibkr_live_client_id + 1` = `11` | 11 | Epic 4, Story 4.6 (AR34/G3) |

The gap between 6 and 10 is not decoration — it is the headroom that keeps the historical client's
rotation ceiling clear of the live reservation. **The default `10` is chosen because of that
rotation range**, which is why an operator who "just picks a different number" for
`IBKR_CLIENT_ID` can still collide without the validator firing. Say so in the docs (Task 3), not
only here.

### Why a shared client ID is a real failure, not a style violation
IBKR does not multiplex a client ID: a second connection presenting an ID already in use either gets
error 326 or evicts the incumbent. `IBKRHistoricalClient` already carries scar tissue from this — the
whole rotation mechanism at `src/services/ibkr_client.py:188-219` exists because a SIGKILL'd run
leaves the Gateway holding an ID for tens of seconds. A live session sharing that base would be
knocked off its socket mid-position by an ordinary catalog import. That is what FR5 forbids.

### ⚠️ The validator will fire on this machine the moment you add it
Covered in Task 0 and repeated here because it is the one way this story breaks everything: `.env:11`
sets `IBKR_CLIENT_ID=10`, `IBKRSettings` reads `.env` directly, and `10 == 10`. Verified:
`IBKRSettings().ibkr_client_id` → `10` today, while `IBKRSettings(_env_file=None).ibkr_client_id` →
`1`. Blast radius is every `get_settings()` caller — `src/cli/main.py`, `src/db/session.py`,
`src/api/**`, `src/core/backtest_runner.py`, and ~11 test files. **Resolve `.env` before writing the
validator**, and never write a test that constructs `IBKRSettings()` without `_env_file=None`.

Related, already recorded in `deferred-work.md` from Story 1.1 and **still true**: `ENV=dev/qa/prod`
does not propagate into nested `IBKRSettings` (`src/config.py:268-270`), so `IBKRSettings` always
reads `.env` regardless of `ENV`. Do not try to fix that here — but do not be surprised by it either.

### AR36 vocabulary — a note to prevent a wrong "correction"
AR36 bans *guard*, *check*, *validator*, and *guardrail* **as synonyms for the safety gate**. It does
not ban Pydantic's own vocabulary. `@model_validator` / `validate_client_ids_distinct` is correct
naming here, exactly as `KrakenSettings.validate_key_secret_pair` already is. Do not rename this to
`gate_*` — this is field validation, not the safety gate.

### Scope boundaries — do NOT do these here
- **No consumer.** Nothing reads `ibkr_live_client_id` yet. Story 1.3 wires it into
  `TradingNodeConfig`'s data + exec clients; Story 4.6 uses `+ 1` for `reconcile`. Adding a consumer
  here creates a second, half-built live path.
- **No `live_node_builder.py`, no `TradingNode`, no Nautilus import.** Story 1.3.
- **No changes to `src/core/live_gate.py`.** Client ID is not a gate condition — the gate's inputs
  are mode, port, account, and the real-money declarations, and nothing else (AR13). Adding a
  client-ID condition to the gate would be a spec violation.
- **No new dependency** — `pyproject.toml` / `uv.lock` unchanged (AR3). Never hand-edit
  `pyproject.toml`; a bash-guard hook blocks it and `uv add`/`uv remove` is the only path. Nothing
  here needs one.
- **No `RedisSettings`.** The delta tree lists it on the same `config.py` line as this field; it
  belongs to Epic 2 (AR11).
- **No refactor of `DataCatalogService` onto typed settings.** Task 4 changes one literal and one
  docstring line; the wider fix is deferred work.

### Project Structure Notes
- **MOD** `src/config.py` — one field, one amended description, one model validator on
  `IBKRSettings`. First validator on this class; `KrakenSettings` (`:105-126`) is the in-file
  precedent. [Source: architecture.md#Delta-Project-Tree lines 494-495]
- **MOD** `tests/unit/test_ibkr_config.py` — new `TestIBKRClientIdIsolation` class. No new test file.
- **MOD** `src/services/data_catalog.py` — one literal + one docstring line (AC #5).
- **MOD** `tests/unit/services/test_data_catalog.py` — one asserted literal.
- **MOD** `docker-compose.yml`, `README.md`, `docs/setup/IBKR_SETUP.md`, `.env.example` —
  documentation and container plumbing.
- Untouched by construction: `src/api/**`, `templates/**`, `src/db/**`, `src/core/**` (including
  `live_gate.py`), every strategy file. [Source: architecture.md AR44]
- Size limits hold trivially: `src/config.py` grows by roughly 25 lines against a 500-line file
  limit; the validator is ~12 lines against a 50-line function limit.

### Testing standards
- **Unit tier** — pure Python, no Nautilus, no DB, no network → `make test-unit` (runs `-n auto`).
  [Source: CLAUDE.md Decision Heuristics; project-context.md#Testing-Rules]
- **TDD is non-negotiable**: Task 1 (tests) RED before Task 2 (implementation).
  [Source: development-principles.md:12]
- `pytest.ini` is the effective config; `unit` is registered at `pytest.ini:25` and
  `--strict-markers` is on.
- `make test-coverage` measures `src/core` + `src/strategies` only — `src/config.py` is outside it,
  so this story does not move the coverage number. That is expected; do not chase it.

### Commit hygiene for this repo
- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three
  points (`.githooks/pre-commit`, the Claude bash-guard, CI). `model_validator` is already imported at
  `src/config.py:10` — do not add a second import line for it. Run `make install-hooks` once per clone.
- Stage and commit in **separate** Bash calls (`git add <files>` then `git commit`); a combined
  one-liner is rejected by the commit gate.
- Commit format `<type>(<scope>): <subject>`, e.g. `feat(config): reserve a dedicated live IBKR
  client id`. Never reference AI or Claude in commit messages.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.2] (lines 511–535) — story statement and
  the three acceptance criteria this file numbers 1–3
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — AR3 (zero new deps),
  AR18 (the field + validator), AR34/G3 (the `+ 1` reconcile reservation), AR36 (vocabulary),
  AR44 (untouched files)
- [Source: _bmad-output/planning-artifacts/architecture.md#Infrastructure-&-Deployment] (lines
  306–308) — D4, client-ID allocation
- [Source: _bmad-output/planning-artifacts/architecture.md#Delta-Project-Tree] (lines 494–495) —
  `config.py` MOD footprint
- [Source: _bmad-output/planning-artifacts/architecture.md] (lines 685–689) — G3, the reconcile
  collision and its `+ 1` resolution
- [Source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-08-03.md:254,315] —
  FR5 → Story 1.2 coverage; NFR17 client-ID isolation is this story
- [Source: src/config.py:20] — `ibkr_client_id`, the field to sit beside
- [Source: src/config.py:82-87] — `IBKRSettings.model_config`: `env_file: ".env"`,
  `case_sensitive: False` (both matter for the tests)
- [Source: src/config.py:105-126] — `KrakenSettings.validate_key_secret_pair`, the
  `@model_validator(mode="after")` precedent in this file
- [Source: src/services/ibkr_client.py:188-219] — `connect(max_id_rotations=5)`, the rotation that
  makes the historical client's effective range `base..base+5`
- [Source: src/services/data_catalog.py:129,138] — the `IBKR_CLIENT_ID` default-`10` fallback (AC #5)
- [Source: tests/unit/test_ibkr_config.py:112-156] — Story 1.1's settings-test shape, including the
  both-casings `delenv` pattern its review added
- [Source: tests/unit/services/test_kraken_settings.py:6,19-28] — `ValidationError` import and the
  Arrange/Act/Assert + `monkeypatch` + `_env_file=None` idiom
- [Source: tests/unit/services/test_data_catalog.py:333,355] — the two tests that pin catalog client
  IDs
- [Source: docker-compose.yml:83-94] — the `ntrader-app` `environment:` block and the
  `NTRADER_REAL_MONEY_ACCOUNT` passthrough precedent
- [Source: .env.example:25; .env:11; docs/setup/IBKR_SETUP.md:44,155; README.md:412] — the four
  places that document a client ID, two of which currently say `10`
- [Source: _bmad-output/implementation-artifacts/1-1-refuse-a-real-money-account-before-anything-connects.md]
  — previous story: `.env*` hook escalation precedent, both-casings `delenv` patch, Docker-passthrough
  review finding, 1539-test baseline
- [Source: _bmad-output/implementation-artifacts/deferred-work.md] — the `ENV=dev/qa/prod` nested-
  settings gap, still open and relevant to how `IBKRSettings` reads `.env`
- [Source: _bmad-output/project-context.md:46-47] — all config via Pydantic BaseSettings; nested
  settings each carry their own validators
- [Source: CLAUDE.md] — commit format, import gate, staging discipline, README sync rule,
  `pyproject.toml` via `uv` only

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m]

### Debug Log References

**Task 0 — collision verified before any code was written:**

```
$ uv run python -c "from src.config import IBKRSettings; \
    print('with .env :', IBKRSettings().ibkr_client_id); \
    print('no .env   :', IBKRSettings(_env_file=None).ibkr_client_id)"
with .env : 10
no .env   : 1
```

Confirmed the story's premise exactly: `.env:11` was the sole reason the historical client
resolved to `10`. Escalated to Allay, who approved a one-off shell write to both `.env` and
`.env.example` (the `.claude/hooks/protect-files.sh` `.env*` block applies to Edit/Write, not
Bash; the hook was left unchanged, matching Story 1.1's precedent). Post-fix:

```
$ uv run python -c "from src.config import IBKRSettings; print(IBKRSettings().ibkr_client_id)"
1
```

**Task 1 — RED confirmed** before implementation: 8 failures across the new
`TestIBKRClientIdIsolation` class, each for the correct reason —
`AttributeError: 'IBKRSettings' object has no attribute 'ibkr_live_client_id'` (3),
`Failed: DID NOT RAISE ValidationError` (4), `KeyError: 'ibkr_live_client_id'` (1).

**Task 5 — verification:**

| Check | Result |
| --- | --- |
| `make test-unit` | **1547 passed** (1539 Story-1.1 baseline + 8 new), 0 failures |
| `make format` | 397 files left unchanged |
| `make lint` | All checks passed |
| `make typecheck` | Success, no issues in 81 source files |
| `get_settings().ibkr` smoke test | `1 10` |
| `python -m src.cli.main --help` | normal help screen |
| `docker compose config` | `IBKR_CLIENT_ID: "1"` and `IBKR_LIVE_CLIENT_ID: "10"`, both resolving into `ntrader-app` |
| `pyproject.toml` / `uv.lock` | unchanged (AR3 satisfied) |

### Completion Notes List

- **The blocking prerequisite was real and is cleared.** `.env:11` set `IBKR_CLIENT_ID=10`, which
  would have made every `IBKRSettings()` / `get_settings()` construction raise `ValidationError`
  the moment the validator landed — taking down the CLI, the web app, `src/db/session.py`, and
  ~11 test files. Resolved by fixing the configuration (`IBKR_CLIENT_ID=1`), not by weakening the
  validator, choosing a different default, or adding an escape hatch.
- **Traced the collision to its source.** `docs/setup/IBKR_SETUP.md:44` instructed
  `IBKR_CLIENT_ID=10  # Can be any number (1-999)` — that document is where the local `.env`'s
  value came from. Fixing only `.env` would have left the next operator to reintroduce it, so the
  doc now states the allocation instead of inviting an arbitrary pick.
- **AC #1** — `ibkr_live_client_id` added with default `10`, env-configurable via
  `IBKR_LIVE_CLIENT_ID`; `ibkr_client_id` keeps its default of `1`. Both asserted in one test,
  since the pair is the contract rather than either value alone.
- **AC #2** — `validate_client_ids_distinct` is a `@model_validator(mode="after")` mirroring the
  in-file `KrakenSettings.validate_key_secret_pair` precedent. Rejection is proven from all three
  override directions (live-side, historical-side, both-equal) plus the env-driven shape, which is
  what demonstrates it is a model validator and not a one-sided field validator. The message names
  both fields, asserted literally.
- **AC #3** — the reservation lives in the field descriptions and is asserted from
  `model_fields[...].description` with loose substrings, so it survives rewording but fails if the
  reservation is dropped. `ibkr_client_id`'s description was amended from the bare "Unique client
  identifier" so the two read as one allocation table.
- **AC #4** — all four documented configuration surfaces now agree on `1` / `10`:
  `.env.example`, `docs/setup/IBKR_SETUP.md` (both the setup block and the Error-326
  troubleshooting step), `README.md`, and `docker-compose.yml`. The compose entry is not
  cosmetic — `ntrader-app` has no `env_file:` directive and the image never copies `.env`, so
  without it `IBKR_LIVE_CLIENT_ID` would be unreachable in a container. This is the same omission
  Story 1.1's review caught for `NTRADER_REAL_MONEY_ACCOUNT`; verified with `docker compose config`.
- **AC #5** — `src/services/data_catalog.py` unset-env fallback changed `"10"` → `"1"` (one
  literal, one docstring line). This path never constructs settings, so the validator cannot see
  it; aligning the literal is the whole of the in-scope fix.
- **AC #6** — 8 unit tests in `TestIBKRClientIdIsolation`, all `@pytest.mark.unit`, no broker, no
  network, no database. Every one passes `_env_file=None` and clears **both** casings of both env
  vars via a shared `_clear_client_id_env` helper — `case_sensitive: False` makes the lowercase
  spelling an equally valid alias, the exact omission Story 1.1's review had to patch.
- **Scope held.** No consumer of `ibkr_live_client_id` (Story 1.3 wires it into `TradingNodeConfig`;
  Story 4.6 uses `+ 1`), no `live_node_builder.py`, no Nautilus import, no change to
  `src/core/live_gate.py`, no `RedisSettings`, no new dependency, no `ge=`/`le=` bounds, no
  rotation-overlap check. Both deliberately-omitted wider checks are recorded as open questions in
  `deferred-work.md` for Allay to rule on rather than decided unilaterally.
- **Two items deferred** under a new `## Deferred from: story-1.2` heading: (1)
  `DataCatalogService` reading IBKR connection settings from `os.environ` rather than typed
  settings — a CLAUDE.md violation and a blind spot the validator structurally cannot cover, noted
  alongside its equally-stale port fallback (`7497` vs the `4002` everything else uses); (2) the
  validator comparing configured bases only, so `IBKR_CLIENT_ID=8` passes while its rotation range
  8–13 swallows both reserved IDs. The second is documented for operators in `IBKR_SETUP.md` since
  it cannot be enforced in code within this story's scope.

### File List

**Modified — source:**
- `src/config.py` — added `ibkr_live_client_id`, amended `ibkr_client_id`'s description, added the `validate_client_ids_distinct` model validator
- `src/services/data_catalog.py` — unset-env `IBKR_CLIENT_ID` fallback `"10"` → `"1"`, docstring updated

**Modified — tests:**
- `tests/unit/test_ibkr_config.py` — new `TestIBKRClientIdIsolation` class (8 tests)
- `tests/unit/services/test_data_catalog.py` — defaults assertion `client_id=10` → `client_id=1`

**Modified — configuration & documentation:**
- `.env.example` — added `IBKR_LIVE_CLIENT_ID=10` with the reservation comment (shell write, approved)
- `docker-compose.yml` — added `IBKR_LIVE_CLIENT_ID: ${IBKR_LIVE_CLIENT_ID:-10}` to `ntrader-app`
- `docs/setup/IBKR_SETUP.md` — setup block corrected to `1`/`10`, new "Client ID reservation" section, Error-326 step bounded
- `README.md` — `IBKR_LIVE_CLIENT_ID` row added, `IBKR_CLIENT_ID` row now names its rotation range

**Modified — process artifacts:**
- `_bmad-output/implementation-artifacts/deferred-work.md` — new `## Deferred from: story-1.2` section
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions

**Modified — untracked by git (gitignored, changed under explicit approval):**
- `.env` — line 11 `IBKR_CLIENT_ID=10` → `IBKR_CLIENT_ID=1` (Task 0; local machine only, not committed)

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-08-04 | Story created — comprehensive developer context assembled. Status → ready-for-dev. |
| 2026-08-04 | Task 0: `.env` client-ID collision escalated, approved, and cleared (`10` → `1`); traced its origin to `IBKR_SETUP.md:44`. |
| 2026-08-04 | Tasks 1–2: `ibkr_live_client_id` field + `validate_client_ids_distinct` model validator, TDD Red → Green (8 new unit tests). |
| 2026-08-04 | Tasks 3–4: reservation propagated to `.env.example`, `docker-compose.yml`, `IBKR_SETUP.md`, `README.md`; `DataCatalogService` unset-env fallback aligned to `1`. |
| 2026-08-04 | Task 5: verified — 1547 unit tests pass (baseline 1539 + 8), lint/format/typecheck clean, compose resolves both IDs, zero new dependencies. Status → review. |
| 2026-08-04 | Code review (3 adversarial layers): 2 decisions, 8 patches, 5 deferred, 4 dismissed. Both decisions ruled on and applied — validator widened from equality to rotation-range intersection, `ge=1` added to both fields, `hide_input_in_errors` added to stop the new validator echoing secrets into tracebacks. 7 of 8 patches applied; `.env.example` wording blocked on hook approval. 1561 unit tests pass (1547 + 14). Status → in-progress. |
