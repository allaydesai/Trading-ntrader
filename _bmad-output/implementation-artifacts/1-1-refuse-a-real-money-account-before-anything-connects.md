# Story 1.1: Refuse a Real-Money Account Before Anything Connects

Status: in-progress

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want the system to decide whether a connection is permitted purely from configuration, before any
network activity happens,
so that a port typo or a stale environment variable can never reach a real-money account.

## Acceptance Criteria

1. **Given** `evaluate_gate(settings, cli_flags)` in `src/core/live_gate.py`, **When** it is called with any combination of settings, **Then** it returns a `GateDecision` without opening a socket, reading a file, or querying a database — **And** the module's runtime import graph contains nothing from `nautilus_trader`, `sqlalchemy`, `ibapi`, `httpx`, `redis`, `src.db.*`, or `src.services.*` (AR16).
2. **Given** settings where `ibkr_trading_mode == "paper"`, `ibkr_port` ∈ {7497, 4002}, and `tws_account` is empty or begins with `DU`/`DF`, **When** the gate is evaluated, **Then** the decision permits the connection with `mode == GateMode.PAPER` and `refusal is None`.
3. **Given** settings failing **any** one of those conditions — `ibkr_trading_mode == "live"`, or `ibkr_port` is 7496/4001 **or any other unrecognised port**, or `tws_account` is set to a non-`DU`/`DF` prefix — **When** the gate is evaluated, **Then** the decision is a refusal carrying a `GateRefusal` whose `reason` names the specific failing condition — **And** an unrecognised port refuses rather than defaulting to permitted (fail-closed).
4. **Given** the real-money crossing mechanism, **When** the gate is evaluated with `--real-money` set but `NTRADER_REAL_MONEY_ACCOUNT` unset, **or** with the env var set but the CLI flag absent, **or** with both present but the env value not matching `tws_account` exactly, **Then** every one of those combinations refuses — **And** only both-present-and-exactly-matching permits, with `mode == GateMode.REAL_MONEY` (FR10, AR15) — **And** no interactive prompt exists on this path: absence of either declaration is a refusal, not a question.
5. **Given** a refusal is produced, **When** its message is rendered, **Then** any account identifier in it is masked to its last 3 characters — **And** the raw account string appears nowhere in the returned `GateDecision`, including its `repr()` (NFR26).
6. **Given** `IBKRSettings`, **When** the settings are loaded, **Then** an `ntrader_real_money_account` field exists (env `NTRADER_REAL_MONEY_ACCOUNT`, default `""`, `repr=False`), so the real-money declaration reaches the gate as typed environment configuration and never as a raw `os.environ` read (FR52, NFR25).
7. **Given** the test suite, **When** `tests/unit/core/test_live_gate.py` runs, **Then** it covers the full truth table including every refusal reason, and requires no broker, network, or database (NFR34, NFR32).

## Tasks / Subtasks

- [x] **Task 1: Add the real-money declaration to typed settings** (AC: #6)
  - [x] In `src/config.py`, add to `IBKRSettings` immediately after `tws_account` (`src/config.py:33`):
    ```python
    ntrader_real_money_account: str = Field(
        default="",
        repr=False,
        description=(
            "Exact IBKR account ID the operator has explicitly authorized for real-money "
            "trading. Empty = paper only. Must be accompanied by the CLI --real-money flag; "
            "neither declaration alone permits a real-money connection."
        ),
    )
    ```
  - [x] Env mapping is field-name → UPPER_SNAKE with `case_sensitive: False` (the established convention) → `NTRADER_REAL_MONEY_ACCOUNT`. Do **not** add an `env_prefix`.
  - [x] `repr=False` matches the adjacent `tws_account` / `tws_password` fields (`src/config.py:32-33`) and `kraken_api_secret` (`src/config.py:85`) — it keeps a full account ID out of any settings repr (NFR26). `tests/unit/test_ibkr_config.py` already asserts this property for the neighbouring fields; extend that pattern if you add a repr test here.
  - [x] **No regression risk here:** the field has a default, so no existing `Settings()` construction changes behaviour. Do **not** repeat Phase 2's required-field conftest guard — it is not needed.
- [x] **Task 2: Document the env vars the gate actually reads** (AC: #6) — *small but safety-relevant, do not skip*
  - [x] In `.env.example`, add `IBKR_TRADING_MODE=paper` next to the existing `TRADING_MODE=paper` (line 8) with a comment: `TRADING_MODE` is the **ib-gateway container's** variable (`docker-compose.yml:50`); the app reads `IBKR_TRADING_MODE`, which compose maps from it (`docker-compose.yml:86`). On a bare-metal `.env` run there is no such mapping — see Dev Notes ⚠️.
  - [x] Add a commented `# NTRADER_REAL_MONEY_ACCOUNT=` line with a one-line warning that it does nothing without `--real-money` and that setting it alone causes the gate to refuse.
  - [x] Do **not** delete or rename the existing `TRADING_MODE` line — `docker-compose.yml` interpolates it.
- [x] **Task 3: Write the failing unit tests first (TDD Red)** (AC: #1–#5, #7)
  - [x] New file `tests/unit/core/test_live_gate.py`, mirroring the Arrange/Act/Assert + `monkeypatch` + `_env_file=None` idiom in `tests/unit/services/test_kraken_settings.py`.
  - [x] Mark every test `@pytest.mark.unit` (registered in `pytest.ini:25`; `pytest.ini` — not `pyproject.toml` — is the effective pytest config, and `--strict-markers` is on).
  - [x] Add a local helper that builds settings with **all four** gate-relevant fields passed explicitly, so a developer's real environment can never leak into a test:
    ```python
    def _settings(*, mode="paper", port=7497, account="", real_money_account=""):
        return IBKRSettings(
            _env_file=None,
            ibkr_trading_mode=mode,
            ibkr_port=port,
            tws_account=account,
            ntrader_real_money_account=real_money_account,
        )
    ```
    (init kwargs outrank env vars in pydantic-settings, so this is airtight.)
  - [x] **Permit cases** (AC #2): parametrize ports 7497 and 4002 × accounts `""`, `"DU1234567"`, `"DF1234567"` → all permitted, `mode is GateMode.PAPER`, `refusal is None`.
  - [x] **Paper refusal cases** (AC #3): parametrize `(mode, port, account) → expected GateRefusalReason` covering `NON_PAPER_TRADING_MODE` (mode `live`), `NON_PAPER_PORT` (7496, 4001, **and an arbitrary unrecognised port such as 9999 — the fail-closed case**), `NON_PAPER_ACCOUNT_PREFIX` (`"U1234567"`).
  - [x] **Refusal precedence** (AC #3): assert a settings object that fails *several* conditions at once reports the reason first in the documented order (mode → port → prefix), so the reason is deterministic.
  - [x] **Real-money truth table** (AC #4) — all four rows, parametrized:
    | `--real-money` | `NTRADER_REAL_MONEY_ACCOUNT` | `tws_account` | outcome |
    |---|---|---|---|
    | `True` | `""` | any | refuse `REAL_MONEY_FLAG_WITHOUT_ENV` |
    | `False` | `"U1234567"` | any | refuse `REAL_MONEY_ENV_WITHOUT_FLAG` |
    | `True` | `"U1234567"` | `"U7654321"` / `""` | refuse `REAL_MONEY_ACCOUNT_MISMATCH` |
    | `True` | `"U1234567"` | `"U1234567"` | permit, `mode is GateMode.REAL_MONEY` |
  - [x] Assert the env-only row refuses **even when the rest of the configuration is perfectly paper-clean** — a stale `NTRADER_REAL_MONEY_ACCOUNT` is itself a refusal condition (see Dev Notes "The one non-obvious rule").
  - [x] **Masking** (AC #5): with `tws_account="DU1234567"` and a refusal produced, assert `"***567" in decision.refusal.message` **and** `"DU1234567" not in repr(decision)`.
  - [x] **Consistency invariant**: across every parametrized row above, assert `decision.permitted is (decision.refusal is None)` — a decision is never half-refused.
  - [x] **Purity** (AC #1): parse the module with `ast` rather than probing `sys.modules` (pytest has already imported half the world by then). Iterate **`tree.body` only** — top-level nodes — which naturally skips the `if TYPE_CHECKING:` block (an `ast.If` node), exactly as intended:
    ```python
    FORBIDDEN = {"nautilus_trader", "sqlalchemy", "ibapi", "httpx",
                 "requests", "redis", "psycopg2", "asyncpg", "socket"}

    tree = ast.parse(Path(live_gate.__file__).read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FORBIDDEN
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in FORBIDDEN
            assert root != "src"          # the gate needs NO runtime src.* import
    ```
    Resolve the path via `Path(live_gate.__file__)`, not a relative string — the test must not depend on the working directory. The `root != "src"` assertion is the strong form: with zero runtime `src.*` imports there is no transitive path by which a forbidden library can enter the gate's import graph, so checking direct imports is sufficient.
  - [x] Run and confirm **RED** (module does not exist yet) before writing any implementation.
- [x] **Task 4: Implement `src/core/live_gate.py` (Green)** (AC: #1–#5)
  - [x] Module docstring: *"Pure paper/live safety gate — no I/O, no framework imports."* Follow `src/core/sma_logic.py` as the house style for a framework-free core module.
  - [x] Types (all `@dataclass(frozen=True)`, matching `src/models/data_load_result.py`; enums as `(str, Enum)`, matching `src/core/sma_logic.py:7`):
    - `GateMode(str, Enum)`: `PAPER = "paper"`, `REAL_MONEY = "real_money"`
    - `GateRefusalReason(str, Enum)`: `NON_PAPER_TRADING_MODE`, `NON_PAPER_PORT`, `NON_PAPER_ACCOUNT_PREFIX`, `REAL_MONEY_FLAG_WITHOUT_ENV`, `REAL_MONEY_ENV_WITHOUT_FLAG`, `REAL_MONEY_ACCOUNT_MISMATCH` (values = lowercase snake_case of the name)
    - `GateFlags`: `real_money: bool = False` — the `cli_flags` parameter's type
    - `GateRefusal`: `reason: GateRefusalReason`, `message: str` (**already masked** at construction)
    - `GateDecision`: `permitted: bool`, `mode: GateMode`, `refusal: GateRefusal | None = None`
  - [x] Module constants: `PAPER_PORTS: frozenset[int] = frozenset({7497, 4002})`, `PAPER_ACCOUNT_PREFIXES: tuple[str, ...] = ("DU", "DF")`, `ACCOUNT_MASK_VISIBLE_CHARS = 3`.
  - [x] `mask_account(account: str) -> str` — public (Story 1.4 reuses it for post-connection logging): `""` → `""`; length ≤ 3 → `"***"`; else `f"***{account[-3:]}"`.
  - [x] `evaluate_gate(settings: "IBKRSettings", cli_flags: GateFlags) -> GateDecision` implementing exactly:
    ```
    env = settings.ntrader_real_money_account.strip()
    account = settings.tws_account.strip()

    if cli_flags.real_money or env:               # real-money crossing branch
        if not env:      refuse REAL_MONEY_FLAG_WITHOUT_ENV
        if not cli_flags.real_money:
                         refuse REAL_MONEY_ENV_WITHOUT_FLAG
        if not account or env != account:
                         refuse REAL_MONEY_ACCOUNT_MISMATCH
        permit(GateMode.REAL_MONEY)

    if settings.ibkr_trading_mode != "paper":     # paper branch
                         refuse NON_PAPER_TRADING_MODE
    if settings.ibkr_port not in PAPER_PORTS:
                         refuse NON_PAPER_PORT
    if account and not account.upper().startswith(PAPER_ACCOUNT_PREFIXES):
                         refuse NON_PAPER_ACCOUNT_PREFIX
    permit(GateMode.PAPER)
    ```
    The order above **is** the refusal precedence. Do not reorder.
  - [x] Maintain the invariant `permitted is (refusal is None)` on every return path.
  - [x] Note the deliberate asymmetry: the real-money branch **does not** also evaluate mode/port/prefix. Two matching declarations are the crossing mechanism (AR15); validating the account actually reachable on that port is Layer 2's job (Story 1.4). Do not "harden" this by ANDing the paper conditions into the real-money branch — that would make the crossing unreachable and silently dead.
  - [x] Comparison rules: the real-money match is **exact and case-sensitive** after `.strip()` ("matching the target account exactly", AR15). The paper-prefix check normalizes with `.strip().upper()` before `startswith`.
  - [x] Split the two branches into `_evaluate_real_money_crossing(...)` and `_evaluate_paper(...)` private helpers so every function stays under 50 lines.
  - [x] Refusal messages: one clear operator-facing sentence per reason, naming the specific failing condition and the offending value — port and mode verbatim, **any account identifier only via `mask_account()`**. Never place the raw account in `GateRefusal` or `GateDecision`.
  - [x] Type the `settings` parameter without importing `src.config` at runtime:
    ```python
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from src.config import IBKRSettings
    ```
    and quote the annotation. `src/config.py` imports `ibapi` at module scope (`src/config.py:9`), so a runtime import would drag a broker library into the gate's import graph and break AC #1. The gate only reads attributes, so nothing else is needed.
  - [x] Google-style docstrings with Args/Returns on `evaluate_gate` and `mask_account`; full type hints (mypy runs over `src/core`).
  - [x] Run the tests to **GREEN**.
- [x] **Task 5: Verify** (AC: all)
  - [x] `make test-unit` — the new file green **and** no regressions in the existing suite.
  - [x] `make format && make lint && make typecheck` — all clean.
  - [x] `make test-coverage` — `src/core/live_gate.py` should sit at or near 100%; it must not drag the >80% `src/core` threshold down.
  - [x] Grep check: `grep -rn "os.environ\|getenv" src/core/live_gate.py` returns nothing.

### Review Findings

_Code review 2026-08-03 — 3 layers (Blind Hunter, Edge Case Hunter, Acceptance Auditor). 3 decision-needed (resolved), 13 patch, 6 deferred, 4 dismissed as noise._

**Decisions — resolved by Allay 2026-08-03:**

- [x] [Review][Decision] **Empty `TWS_ACCOUNT` permits PAPER with zero account evidence** → **(a) ACCEPTED.** `if account and not account.upper().startswith(...)` (`src/core/live_gate.py:191`) short-circuits on `""`, which is the field default (`src/config.py:33`) and what `docker-compose.yml:89` passes when the host var is unset. Layer 1 then rests on two weak conditions: `ibkr_trading_mode` is self-declared and pinned to its `"paper"` default on bare metal (the `TRADING_MODE` gap), and `ibkr_port` is a convention TWS lets the operator edit. Worst case reachable from this repo's own documented setup: bare metal, `IBKR_TRADING_MODE` never set, `TWS_ACCOUNT` unset, TWS logged into a live account on port 7497 → `permitted=True, mode=PAPER`. AC #2 explicitly mandates this behaviour, and the tests lock it in, so the code is spec-compliant. **Resolution:** behaviour stands; Layer 2 (Story 1.4) is load-bearing, not belt-and-braces. Carries one patch — the Dev Note claim "the gate is an AND of three independent conditions" must be amended, since with an unset account on bare metal Layer 1 is one condition (the port).
- [x] [Review][Decision] **Real-money crossing permits a `DU`/`DF` paper account and never checks mode/port** → **(a) ACCEPTED — no change.** `_evaluate_real_money_crossing` (`src/core/live_gate.py:160-169`) receives only the two declarations, so `--real-money` + `NTRADER_REAL_MONEY_ACCOUNT=DU1234567` + matching `TWS_ACCOUNT` permits with `mode=REAL_MONEY` against a demo account on a paper port. **Reason for deferring:** Layer 2 catches it post-connection. Logged to `deferred-work.md` so Story 1.4 inherits it as a required post-connection check.
- [x] [Review][Decision] **`GateDecision.mode` on a refusal is misleading in both directions** → **(a) `mode: GateMode | None = None`.** The branch that produced the refusal was hard-coded as the `mode` argument (`src/core/live_gate.py:146,154,162` all `REAL_MONEY`; `:176,185,194` all `PAPER`), so a stale env var with no `--real-money` yielded `mode=REAL_MONEY` for an operator trying to run paper. **Resolution:** refusals set `mode=None`; permits keep their `GateMode`. No information is lost — `refusal.reason` already distinguishes the branch (`REAL_MONEY_*` vs `NON_PAPER_*`). No existing test asserts `mode` on a refusal, so nothing breaks. Carries one patch.

**Patches — 11 applied 2026-08-03, 2 still open:**

- [x] [Review][Patch] `GateDecision.mode` → `GateMode | None`, refusals set `None`; `_assert_consistent` now pins `permitted is (mode is not None)` on every parametrized row [src/core/live_gate.py]
- [x] [Review][Patch] Amended the Dev Note "AND of three independent conditions" — on bare metal with an unset account Layer 1 is the port alone; Layer 2 stated as load-bearing [this file, Dev Notes ⚠️ section]
- [x] [Review][Patch] Purity check rewritten as a whitelist over `ast.walk` — now catches `import src.*`, function/method-level, try-wrapped, nested, and relative imports; 12 meta-test cases prove the guard can actually fail [tests/unit/core/test_live_gate.py]
- [x] [Review][Patch] `GateFlags.real_money` compared by identity (`is True`) — `"false"` / `"0"` / `["x"]` no longer read as consent [src/core/live_gate.py]
- [ ] [Review][Patch] **OPEN** — "Set both" guidance is wrong under Docker, where compose's `environment:` block outranks `.env`. The `docs/setup/IBKR_SETUP.md` half is FIXED; the `.env.example` half is blocked by `.claude/hooks/protect-files.sh` and needs the same one-off escalation Task 2 used [.env.example:8-13]
- [ ] [Review][Patch] **OPEN** — `NTRADER_REAL_MONEY_ACCOUNT` is absent from the `ntrader-app` `environment:` block, so the real-money crossing is unreachable under Docker while `.env.example` describes it as a live control. Deliberately skipped: `docker-compose.yml` is outside this story's footprint [docker-compose.yml:82-93; .env.example:20-22]
- [x] [Review][Patch] `project-context.md` security rule now names **both** `IBKR_TRADING_MODE` and `TRADING_MODE`, with the Docker/bare-metal split spelled out [_bmad-output/project-context.md:149]
- [x] [Review][Patch] `IBKR_SETUP.md` Step 2 block restores `TRADING_MODE` alongside `IBKR_TRADING_MODE`; the note now states which variable wins under each launch path and names the different-values trap [docs/setup/IBKR_SETUP.md:40-73]
- [x] [Review][Patch] File List now includes `docs/setup/IBKR_SETUP.md`, `_bmad-output/project-context.md`, and `deferred-work.md` [this file, File List]
- [x] [Review][Patch] Mismatch branch split into "TWS_ACCOUNT not set" and "declarations disagree"; both dead `or '(unset)'` fallbacks removed [src/core/live_gate.py]
- [x] [Review][Patch] `mask_account` now strips its own input and withholds the tail until the value exceeds `2 * ACCOUNT_MASK_VISIBLE_CHARS`; boundary (len 4/5/6/7) and whitespace/newline cases tested [src/core/live_gate.py]
- [x] [Review][Patch] Both `ntrader_real_money_account` tests clear the lowercase env alias too (`case_sensitive: False`) [tests/unit/test_ibkr_config.py]
- [x] [Review][Patch] `_settings()` docstring now states the limit of its isolation: `_env_file=None` does not disable `os.environ`, so the guarantee covers only the fields in the signature [tests/unit/core/test_live_gate.py]

**Verification after patches:** `test_live_gate.py` + `test_ibkr_config.py` → **69 passed** (was 38). `make test-unit` → **1539 passed**, 0 failed (was 1518; +21 tests, no regressions). `make format` / `make lint` / `make typecheck` → clean. Coverage on `src/core/live_gate.py` → **64 statements, 0 missed, 100%**.

**Residual, not patched:** when two *different* accounts both mask to `***` (each ≤6 chars) the mismatch message shows the same redacted value twice. Real IBKR account IDs are 8–9 characters, so this is unreachable in practice; the `(unset)` case that was concrete is fixed.

**Deferred (real, pre-existing, or out of this story's scope):**

- [x] [Review][Defer] Invalid `IBKR_TRADING_MODE` (`"Paper"`, `" paper"`) dies with a pydantic traceback instead of a gate refusal [src/config.py:23-25] — deferred, pre-existing
- [x] [Review][Defer] `ENV=dev/qa/prod` env-file selection never reaches nested `IBKRSettings`; the gate always reads `.env` [src/config.py:268-270,305-325] — deferred, pre-existing
- [x] [Review][Defer] `model_dump()` still returns raw account/password — `repr=False` is display-only [src/config.py:32-42] — deferred, pre-existing
- [x] [Review][Defer] `(str, Enum)` makes `str(GateMode.PAPER)` render `"GateMode.PAPER"`, not `"paper"` [src/core/live_gate.py:132-147] — deferred, spec-mandated house style
- [x] [Review][Defer] Multi-account `TWS_ACCOUNT="DU123,U765"` passes the `startswith` prefix check [src/core/live_gate.py:191] — deferred, pre-existing
- [x] [Review][Defer] Real-money crossing permits a `DU`/`DF` paper account [src/core/live_gate.py:160-169] — deferred by decision, Layer 2 catches it post-connection (Story 1.4)

## Dev Notes

### Why this story is first, and what "first" means here
Phase 3 is a brownfield continuation on branch `015-paper-trading`. **There is no project-initialization story** (AR1) — the foundation is inherited. Epic 1 leads on two sequencing principles that must not be rearranged: *riskiest assumption first* and **safety before capability** — the gate ships before any code path exists that can submit an order, so no window ever exists in which accidental real-money execution is reachable. This story is therefore a pure function and its truth table, and nothing else. [Source: epics.md#Epic-List; epics.md#Additional-Requirements AR1]

### The one non-obvious rule — a stale env var is itself a refusal
`NTRADER_REAL_MONEY_ACCOUNT` set **without** `--real-money` refuses, even when mode, port, and account are all perfectly paper. This is deliberate and is what AC #4 states ("every one of those combinations refuses"). The gate cannot distinguish "operator intends real money but forgot the flag" from "leftover env var" — they are the same input — so it fails closed on both. The operational consequence is that an operator who once authorized a real-money account must unset the variable to run paper again; that friction is the feature. [Source: epics.md#Story-1.1; architecture.md#D3]

### ⚠️ `TRADING_MODE` is not the variable the gate reads
`IBKRSettings.ibkr_trading_mode` is populated from **`IBKR_TRADING_MODE`** (field name → UPPER_SNAKE, no `env_prefix`). `.env.example:8` documents `TRADING_MODE=paper`, and the local `.env` sets `TRADING_MODE` — neither reaches `ibkr_trading_mode`. Under Docker the gap is bridged (`docker-compose.yml:86` maps `IBKR_TRADING_MODE: ${TRADING_MODE:-paper}`), but on a bare-metal `.env` run **setting `TRADING_MODE=live` leaves the gate seeing `"paper"`**. The documentation gap is real and Task 2 closes it. Do not "fix" it by renaming the field or adding an `env_prefix`; that would break every other `ibkr_*` setting.

**Amended after code review (2026-08-03) — do not restore the earlier claim.** This note previously argued that "the gate's other two conditions still catch a real live account, which is precisely why the gate is an AND of three independent conditions rather than one flag." That overstates Layer 1's coverage in exactly the scenario it was defending. On a bare-metal run the mode condition **cannot fail** (`ibkr_trading_mode` is pinned to its `"paper"` default because `IBKR_TRADING_MODE` is unset), and the prefix condition is **skipped entirely** when `tws_account` is empty — which is the field's default, and what `docker-compose.yml:89` passes when the host variable is unset. In that configuration Layer 1 reduces to a single condition: the port. And the port is a convention TWS lets the operator change in Global Configuration, not an enforcement.

So the honest statement is: **Layer 1 is an AND of up to three conditions, and Layer 2 (Story 1.4) is load-bearing, not belt-and-braces.** Reviewed and accepted by Allay on 2026-08-03 — AC #2 mandates permitting an empty account, and the behaviour stands — but Story 1.4's post-connection account verification is the control that closes this, and it must not be descoped or deferred on the assumption that Layer 1 already covers it.

### Never read `ibkr_read_only` as the safety control
`ibkr_read_only` defaults to `True` today but is set to `False` for any trading session (Story 1.3) — it is deliberately off during operation. **The gate is the load-bearing control; `read_only` is not.** Reading it as a safety condition is an explicitly listed review-rejection anti-pattern. It plays no part in `evaluate_gate()`. [Source: architecture.md#Enforcement-Guidelines AR43; prd.md:507-509]

### Vocabulary is normative (AR36)
This mechanism is a **gate** — never "guard", "check", "validator", or "guardrail" in code, symbols, messages, or comments. Module `live_gate.py`, function `evaluate_gate()`, types `GateDecision` / `GateRefusal` / `GateFlags` / `GateMode`. The `live_` prefix mirrors the `ntrader live` CLI group arriving in Story 1.7. [Source: architecture.md#Naming-Patterns]

### Return, don't raise
`evaluate_gate()` **returns** a `GateDecision` — it never raises and never exits. Callers decide what a refusal means: Story 1.3's node builder raises before constructing any client config; Story 1.7's CLI maps a refusal to exit code 3. Keeping the decision a value is what makes the full truth table unit-testable without a broker (NFR34). [Source: architecture.md#D3 AR16]

### Scope boundaries — do NOT do these here
- **No CLI.** No `live` Click group, no `--real-money` flag wiring, no exit codes (3 = gate refusal, 4 = connectivity). That is Story 1.7 / AR28. This story defines `GateFlags.real_money` as a plain dataclass field; nothing constructs it yet except tests.
- **No Layer 2.** Post-connection account verification against the account the gateway actually reports is Story 1.4. This story is Layer 1, static, pre-connection only.
- **No `ibkr_live_client_id`.** That field and its equality validator are Story 1.2.
- **No `TradingNode`, no `live_node_builder.py`, no Nautilus of any kind.** Story 1.3.
- **No new dependency.** `pyproject.toml`/`uv.lock` must be unchanged by this story (AR3). Nothing here needs one.
- **No `docs/qa/phase3-live-verification.md`.** Gate refusal is automated (NFR34), not an operator procedure.

### Project Structure Notes
- **NEW** `src/core/live_gate.py` — the only new source file. Sits alongside `sma_logic.py`, `risk_management.py`, and the other framework-free core modules. [Source: architecture.md#Delta-Project-Tree]
- **MOD** `src/config.py` — one field added to `IBKRSettings`. (The delta tree lists `config.py` as modified for `ibkr_live_client_id` + `RedisSettings`; those belong to Stories 1.2 and Epic 2 respectively.)
- **MOD** `.env.example` — documentation only.
- **NEW** `tests/unit/core/test_live_gate.py` — joins `test_backtest_orchestrator.py`, `test_results_extractor.py`, `test_strategy_factory.py` in `tests/unit/core/`.
- Untouched by construction: `src/api/**`, `templates/**`, `src/db/**`, `src/services/**`, every strategy file. [Source: architecture.md AR44]
- Size limits hold comfortably: the module lands around 120–150 lines (limit 500), each function well under 50.

### Testing standards
- **Unit tier** — pure Python, no Nautilus, no DB, no network → `make test-unit` (runs `-n auto`). [Source: CLAUDE.md Decision Heuristics; project-context.md#Testing-Rules]
- **TDD is non-negotiable**: Task 3 (tests) must be RED before Task 4 (implementation). [Source: development-principles.md:12]
- `pytest.ini` is the effective config (it takes precedence over the `[tool.pytest.ini_options]` block in `pyproject.toml`, which lists a *different*, narrower marker set). `unit` is registered at `pytest.ini:25`; `--strict-markers` is on, so an unregistered marker is a hard error.
- Coverage: `make test-coverage` covers `src/core` + `src/strategies`, ≥80% threshold. The gate is the phase's one mandated-by-NFR test (NFR34) — it should be at or near 100%.
- Never edit `pyproject.toml` by hand — a bash-guard hook blocks it; `uv add`/`uv remove` is the only path. (Nothing in this story requires it.)

### Commit hygiene for this repo
- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three points (`.githooks/pre-commit`, the Claude bash-guard, CI). Make dependent changes — import plus its usage — in a single edit. Run `make install-hooks` once per clone.
- Stage and commit in **separate** Bash calls (`git add <files>` then `git commit`); a combined one-liner is rejected by the commit gate.
- Commit format `<type>(<scope>): <subject>`, e.g. `feat(live): add pre-connection paper/live safety gate`. Never reference AI or Claude in commit messages.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.1] (lines 466–509) — story statement and the acceptance criteria this file numbers
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — AR1 (no init story), AR3 (zero new deps), AR13 (Layer 1 conditions), AR15 (two-channel crossing), AR16 (pure `evaluate_gate`), AR17 (`read_only` scoping + masking), AR36 (vocabulary), AR43 (anti-patterns), AR44 (untouched files)
- [Source: _bmad-output/planning-artifacts/architecture.md#Authentication-&-Security] (lines 250–271) — D3, the two-layer gate design
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming-Patterns] (lines 367–392) — `live_` prefix, `live_gate.py` → `evaluate_gate()`, `GateDecision`, `GateRefusal`
- [Source: _bmad-output/planning-artifacts/architecture.md#Delta-Project-Tree] (lines 501–504, 543) — `src/core/live_gate.py` and `tests/unit/core/test_live_gate.py` as the story's file footprint
- [Source: _bmad-output/planning-artifacts/prd.md#Journey-4] (lines 360–376) — the `IBKR_PORT=7496` muscle-memory scenario this gate exists to refuse
- [Source: _bmad-output/planning-artifacts/prd.md] — FR8–FR12 (detection, pre-connection refusal, deliberate crossing, distinguishable refusal), NFR25/NFR26/NFR27 (credential hygiene, account masking, gate-not-`read_only`), NFR32/NFR34 (no broker in automated tests; the gate must have one)
- [Source: src/config.py:14-78] — `IBKRSettings`: `ibkr_port` (:19), `ibkr_trading_mode` (:23), `ibkr_read_only` (:26), `tws_account` (:33), shared `model_config` (:73)
- [Source: src/config.py:85] — `repr=False` precedent (`kraken_api_secret`)
- [Source: src/core/sma_logic.py:1-12] — house style for a pure, framework-free core module and the `(str, Enum)` convention
- [Source: src/models/data_load_result.py] — frozen-dataclass result-object precedent
- [Source: tests/unit/services/test_kraken_settings.py:14-30] — Arrange/Act/Assert + `monkeypatch` + `_env_file=None` settings-test idiom
- [Source: pytest.ini:13-31] — `--strict-markers` and the registered `unit` marker
- [Source: .env.example:8; docker-compose.yml:50,86] — the `TRADING_MODE` vs `IBKR_TRADING_MODE` gap
- [Source: _bmad-output/project-context.md] — Pydantic-settings rule, structlog, Decimal, size limits, TDD tiers
- [Source: CLAUDE.md] — commit format, import gate, staging discipline, `pyproject.toml` via `uv` only

## Dev Agent Record

### Agent Model Used

claude-opus-5[1m] (Opus 5, 1M context)

### Debug Log References

- `uv run pytest tests/unit/test_ibkr_config.py -k real_money` — **RED** confirmed before Task 1's field
  existed (`AttributeError: 'IBKRSettings' object has no attribute 'ntrader_real_money_account'`).
- `uv run pytest tests/unit/core/test_live_gate.py` — **RED** confirmed before Task 4
  (`ModuleNotFoundError: No module named 'src.core.live_gate'`), then **GREEN** at 35 passed.
- `make test-unit` — 1518 passed, 0 failed (includes the 35 new gate tests and 3 new settings tests).
- `make format` / `make lint` / `make typecheck` — all clean. Lint initially flagged I001 in the new test
  file after the auto-formatter hoisted `from src.core.live_gate import (...)` into the third-party
  import block; corrected by regrouping all `src.*` imports below `pytest`.
- `make test-coverage` — `src/core/live_gate.py` **60 statements, 0 missed, 100%**.
- `make test-integration` — 23 failed / 144 passed. **Pre-existing baseline, not caused by this story**:
  the same 23 recorded at Phase 2 close, all Nautilus C-extension `signal 5` crashes in
  catalog/backtest/Kraken integration tests. Zero references to `live_gate` or
  `ntrader_real_money_account` anywhere in the failure output. The one extra failure seen inside
  `make test-coverage` (`test_trades_api.py::…::test_equity_curve_endpoint_with_nonexistent_backtest`,
  closed TCPTransport) passes in isolation and under `--forked` — un-forked whole-suite contamination.
- `grep -rn "os.environ\|getenv" src/core/live_gate.py` — no matches (exit 1), as AC #1 requires.

### Completion Notes List

- **AC #1 (purity)** — `src/core/live_gate.py` imports only `dataclasses`, `enum`, and `typing` at
  runtime; `IBKRSettings` is referenced through a `TYPE_CHECKING` guard with a quoted annotation, so
  `ibapi` (imported at `src/config.py:9`) never enters the gate's import graph. `TestGatePurity` proves
  this by parsing the module with `ast` and walking `tree.body` only — top-level nodes, which skips the
  `if TYPE_CHECKING:` block — asserting no forbidden root and **no runtime `src.*` import at all**.
- **AC #2/#3 (paper truth table)** — permits are parametrized over ports {7497, 4002} × accounts
  {`""`, `DU…`, `DF…`}. Refusals cover live mode, 7496, 4001, and an unrecognised 9999/1234 (fail-closed),
  plus a non-`DU`/`DF` prefix. Precedence (mode → port → prefix) is asserted with settings that fail
  several conditions at once, so the reported reason is deterministic.
- **AC #4 (real-money crossing)** — all four rows plus three extra edges: an empty `tws_account` to match
  against, a case-differing account (the match is exact and case-sensitive after `.strip()`), and
  surrounding whitespace on both declarations. `test_stale_env_var_refuses_even_when_configuration_is_paper_clean`
  pins the non-obvious rule: a leftover `NTRADER_REAL_MONEY_ACCOUNT` refuses even with perfect paper
  settings. No interactive prompt exists on this path.
- **Deliberate asymmetry preserved** — the real-money branch does not re-evaluate mode/port/prefix;
  `test_real_money_crossing_does_not_evaluate_paper_conditions` locks that in so a later "hardening"
  change that makes the crossing unreachable fails a test rather than silently dying.
- **AC #5 (masking)** — refusal messages carry account identifiers only through `mask_account()`. Tests
  assert `"***567" in message`, the raw string absent from both the message and `repr(decision)`, on the
  mismatch and prefix paths. An empty account renders as `(unset)` rather than a dangling blank.
- **AC #6 (typed settings)** — `ntrader_real_money_account` added to `IBKRSettings` immediately after
  `tws_account`, `default=""`, `repr=False`, env `NTRADER_REAL_MONEY_ACCOUNT` via the existing
  field-name → UPPER_SNAKE convention (no `env_prefix` added). Three tests cover default, env load, and
  repr suppression.
- **Consistency invariant** — `_assert_consistent()` asserts `permitted is (refusal is None)` on every
  parametrized row; `_refuse()` is the single construction point for refusals, so the invariant cannot
  drift.
- **Scope held** — no CLI, no `--real-money` wiring, no exit codes, no Layer 2 account verification, no
  `ibkr_live_client_id`, no Nautilus, no new dependency (`pyproject.toml` and `uv.lock` untouched).
  `evaluate_gate()` returns; it never raises and never exits.
- **`.env.example` (Task 2)** — `.claude/hooks/protect-files.sh` blocks every `.env*` path through
  Edit/Write, catching this secret-free tracked template collaterally. Rather than bypass the guardrail
  silently, the block was raised with Allay, who approved a one-off shell write; the hook itself is
  unchanged. `TRADING_MODE` was preserved (docker-compose interpolates it) and `IBKR_TRADING_MODE` added
  alongside it with the bare-metal-vs-Docker gap spelled out.
- **Pre-existing working-tree changes carried in, not authored here**: `repr=False` on
  `tws_password`/`tws_account` in `src/config.py`, the two matching repr tests in
  `tests/unit/test_ibkr_config.py`, and `docs/setup/IBKR_SETUP.md` were already modified before this
  story ran. They are listed below for completeness since they land in the same commit.

### File List

**New**
- `src/core/live_gate.py`
- `tests/unit/core/test_live_gate.py`

**Modified**
- `src/config.py` — `ntrader_real_money_account` field on `IBKRSettings` (plus pre-existing
  `repr=False` on `tws_password`/`tws_account`)
- `tests/unit/test_ibkr_config.py` — 3 tests for the new field (plus 2 pre-existing repr tests)
- `.env.example` — `IBKR_TRADING_MODE` documented next to `TRADING_MODE`; commented
  `NTRADER_REAL_MONEY_ACCOUNT` with its no-op-without-`--real-money` warning
- `docs/setup/IBKR_SETUP.md` — Step 2 `.env` block and the `TRADING_MODE` vs `IBKR_TRADING_MODE`
  note. **Added to this list by code review 2026-08-03** — it shipped in commit `f1a8a0b` but was
  disclosed only as prose in the Completion Notes, never listed here
- `_bmad-output/project-context.md` — the `#### Security` rule on changing the trading mode.
  **Added to this list by code review 2026-08-03** — undisclosed at implementation time. This is
  the repo's governing AI-rules file, so an unlisted edit to it propagates silently
- `_bmad-output/implementation-artifacts/sprint-status.yaml` — story status transitions
- `_bmad-output/implementation-artifacts/1-1-refuse-a-real-money-account-before-anything-connects.md` — this file
- `_bmad-output/implementation-artifacts/deferred-work.md` — **NEW**, opened by code review
  2026-08-03 for Phase 3's non-blocking findings

**Untouched by construction:** `src/api/**`, `templates/**`, `src/db/**`, `src/services/**`, every
strategy file, `pyproject.toml`, `uv.lock`.

## Change Log

| Date       | Description                                                                 |
| ---------- | --------------------------------------------------------------------------- |
| 2026-08-03 | Story created — comprehensive developer context assembled. Status → ready-for-dev. |
| 2026-08-03 | Implemented Tasks 1–5: `ntrader_real_money_account` setting, `.env.example` documentation, `src/core/live_gate.py` (pure Layer 1 gate), and 35 unit tests covering the full truth table. 100% coverage on the new module; unit suite 1518 passed. Status → review. |
| 2026-08-03 | Code review (3 adversarial layers). 3 decisions resolved by Allay: empty-account permit ACCEPTED (Layer 2 is load-bearing), paper-account real-money crossing ACCEPTED (deferred to Story 1.4), `GateDecision.mode` → `GateMode \| None` on refusals. 11 of 13 patches applied; 2 remain open (both `.env.example` / `docker-compose.yml`). 5 items deferred to `deferred-work.md`. Unit suite 1539 passed, gate module still 100%. Status → in-progress. |
