# Story 2.1: Define a Session as a List of Strategy Specifications

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the operator,
I want a session's definition modelled as a list of strategy specs rather than one strategy plus
instruments,
so that running several strategies in one session later is a configuration change, never a refactor.

## Acceptance Criteria

1. **Given** `src/models/session.py`, **When** the domain model is defined, **Then** `SessionSpec` holds an ordered, non-empty **collection** of `StrategySpec`, each carrying its own strategy identifier, resolved parameters, and target instruments expressed as bar-type strings (FR13, AR36) — **And** a spec with two entries validates successfully even though this phase always supplies one — **And** the same instrument appearing in two different `StrategySpec` entries is legal, not a duplicate.
2. **Given** a `StrategySpec`, **When** it is constructed, **Then** its strategy identifier is resolved through the existing `StrategyRegistry` and stored in **canonical** form (so the alias `smacrossover` and the canonical `sma_crossover` produce equal specs) — **And** an unknown strategy fails validation with a `pydantic.ValidationError` whose message lists the registered names.
3. **Given** strategy parameters, **When** a spec is built through `StrategySpec.from_overrides(...)`, **Then** they resolve through the existing `StrategyLoader.build_strategy_params()` chain (overrides → `_settings_map` → Pydantic defaults) and are frozen into the spec (FR53) — **And** `settings` is an injected argument; the model never calls `get_settings()` itself.
4. **Given** a `SessionSpec`, **When** its fields are inspected, **Then** neither it nor `StrategySpec` declares any host, port, username, password, or account identifier field — connection settings live only in `IBKRSettings`/env (FR52, NFR25) — **And** a full `model_dump_json()` of a spec contains none of those values.
5. **Given** a `SessionSpec` instance, **When** any attempt is made to rebind one of its attributes **or** to append to its strategy collection, **Then** both are rejected — **And** the model round-trips **losslessly** to and from JSON for JSONB storage, `Decimal` parameter values included (`SessionSpec.model_validate_json(spec.model_dump_json()) == spec`).
6. **Given** `SessionStatus`, **When** it is defined, **Then** it is a `StrEnum` with exactly `created`, `running`, `stopped`, `sealed` in that order (FR20, AR36) — **And** `str(SessionStatus.CREATED) == "created"` (not `"SessionStatus.CREATED"`).
7. **Given** bar-type strings supplied as a spec's target instruments, **When** the spec is constructed, **Then** they are validated through the existing `resolve_live_bar_types()` and an INTERNAL-aggregated, composite, non-time-aggregated, unparseable, or intra-spec duplicate entry fails validation with that function's own message (NFR16, AR21) — **And** an empty bar-type collection is rejected.
8. **Given** the module `src/models/session.py`, **When** its **top-level** import graph is inspected, **Then** it contains nothing from `nautilus_trader`, `ibapi`, `sqlalchemy`, or `redis` — every framework touch is a lazy import inside the validator that needs it, so importing `src.models` stays cheap for the web/API paths that already do.
9. **Given** the test suite, **When** `tests/unit/models/test_session_spec.py` runs under `make test-unit`, **Then** it covers every criterion above and requires no broker, network, or database (NFR32).

## Tasks / Subtasks

- [x] **Task 1: Write the failing unit tests first (TDD Red)** (AC: #1–#9)
  - [x] New file `tests/unit/models/test_session_spec.py`. Follow the class-per-model + Arrange/Act/Assert idiom in `tests/unit/models/test_config_snapshot.py:9-38`. Mark **every** test `@pytest.mark.unit` (registered at `pytest.ini:25`; `--strict-markers` is on at `pytest.ini:15`, so an unregistered marker is a hard error).
  - [x] `tests/unit/models/` has **no `__init__.py`** — do not add one; the directory is collected by rootdir-relative discovery like its siblings.
  - [x] Build a local helper so no test hand-writes a settings object:
    ```python
    def _spec(strategy_id="sma_crossover", overrides=None, bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",)):
        from src.config import get_settings
        return StrategySpec.from_overrides(
            strategy_id=strategy_id,
            overrides=overrides or {},
            settings=get_settings(),
            bar_types=bar_types,
        )
    ```
    `get_settings()` is fine **in the test** (it reads `.env` defaults); the point of AC #3 is that the *model* never calls it.
  - [x] **Shape** (AC #1): a two-entry `SessionSpec` validates; `len(spec.strategies) == 2`; order is preserved. Include one case where **both** entries name the same instrument — it must validate (two strategies legitimately trading one instrument) and `spec.subscription_bar_types` must contain it **once**, case-insensitively deduped, in first-seen order.
  - [x] **Empty collection** (AC #1): `SessionSpec(strategies=())` raises `ValidationError` with error type `too_short`.
  - [x] **Canonicalisation** (AC #2): `_spec(strategy_id="smacrossover").strategy_id == "sma_crossover"`; and a spec built from the alias `== ` one built from the canonical name.
  - [x] **Unknown strategy** (AC #2): `StrategySpec(strategy_id="nope", bar_types=(...))` raises `ValidationError`, and the rendered message contains `"nope"` **and** at least `"sma_crossover"` and `"momentum"`. Assert `pydantic.ValidationError` specifically — see Dev Notes ⚠️ "KeyError escapes a validator".
  - [x] **Parameter chain** (AC #3): `_spec(overrides={"fast_period": 12}).parameters` == `{"fast_period": 12, "slow_period": 20, "portfolio_value": Decimal("1000000"), "position_size_pct": Decimal("10.0")}` — the override wins, the `_settings_map` fills `slow_period`/`portfolio_value`/`position_size_pct`, and the values are `Decimal`, not `float` or `str`.
  - [x] **Invalid parameters** (AC #3): `StrategySpec(strategy_id="sma_crossover", parameters={"fast_period": 50, "slow_period": 10}, bar_types=(...))` raises `ValidationError` naming `slow_period` (the `SMAParameters` cross-field rule at `src/models/strategy.py:55-61`).
  - [x] **No credentials** (AC #4): assert `set(SessionSpec.model_fields) | set(StrategySpec.model_fields)` contains no name whose lowercase form includes any of `host`, `port`, `username`, `password`, `account`, `secret`, `api_key`, `token`. Then build a spec, dump it with `model_dump_json()`, and assert the real values of `settings.ibkr.tws_account` / `tws_password` / `ibkr_host` are absent from the string (guard the assertion with `if value:` so it is meaningful only when the env actually sets them).
  - [x] **Frozen** (AC #5): `spec.strategies = ()` raises `ValidationError` with error type `frozen_instance`; `spec.strategies.append(...)` raises `AttributeError` (it is a tuple); same two checks on a `StrategySpec`.
  - [x] **JSON round trip** (AC #5): `SessionSpec.model_validate_json(spec.model_dump_json()) == spec`, **and** separately assert `type(restored.strategies[0].parameters["portfolio_value"]) is Decimal`. The equality assertion alone is the one that matters, but the explicit `Decimal` check is what makes a future regression legible — see Dev Notes ⚠️ "Decimal does not survive a naive round trip".
  - [x] **Status enum** (AC #6): `[m.value for m in SessionStatus] == ["created", "running", "stopped", "sealed"]` (exact list, exact order — this pins "exactly four"); `str(SessionStatus.CREATED) == "created"`; `f"{SessionStatus.SEALED}" == "sealed"`; `SessionStatus("created") is SessionStatus.CREATED`.
  - [x] **Bar types** (AC #7): parametrize the refusal cases through `StrategySpec(...)` and assert `ValidationError` for each. All six strings below are verified to be refused by `resolve_live_bar_types` today:
    | input | refused because |
    |---|---|
    | `"AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"` | INTERNAL-aggregated |
    | `"AAPL.NASDAQ-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"` | composite — **but the INTERNAL branch fires first**, so assert `ValidationError`, *not* the composite message |
    | `"AAPL.NASDAQ-100-TICK-LAST-EXTERNAL"` | not time-aggregated |
    | `"garbage"` | unparseable |
    | `("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL", "aapl.nasdaq-1-minute-last-external")` | intra-spec duplicate (case-insensitive) |
    | `()` | empty — pydantic `too_short`, not a `resolve_live_bar_types` message |
  - [x] **Top-level import purity** (AC #8): parse the module with `ast` rather than probing `sys.modules` (pytest has imported half the world by then). Iterate **`tree.body` only** — top-level nodes — so the lazy imports inside function bodies are correctly ignored:
    ```python
    FORBIDDEN = {"nautilus_trader", "ibapi", "sqlalchemy", "redis", "psycopg2", "asyncpg"}

    tree = ast.parse(Path(session_module.__file__).read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FORBIDDEN
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN
    ```
    Resolve the path via `Path(session_module.__file__)`, never a relative string — the test must not depend on the working directory. (This is the same guard shape as `tests/unit/core/test_live_gate.py`, narrowed: `src.*` imports are *permitted* here, unlike the gate.)
  - [x] Run and confirm **RED** (module does not exist yet) before writing any implementation.
- [x] **Task 2: Implement `src/models/session.py` (Green)** (AC: #1–#8)
  - [x] Module docstring stating what it owns (the immutable session specification and its status vocabulary) and what it does **not** (the DB row, the state machine, the runner) — follow `src/core/live_market_data.py:1-26` as the house style for an owns/does-not-own header.
  - [x] `SessionStatus(StrEnum)` with `CREATED/RUNNING/STOPPED/SEALED` = `"created"/"running"/"stopped"/"sealed"`, in that order. **`StrEnum`, not `(str, Enum)`** — AC #6 requires it and it is what makes `str()`/f-string/JSON render the value. See Dev Notes ⚠️ "StrEnum is a deliberate divergence".
  - [x] Three module-level private helpers, each doing its framework import **lazily, inside the function body** (AC #8), mirroring the precedent at `src/models/strategy.py:119-121`:
    ```python
    def _lookup_strategy(strategy_id: str) -> "StrategyDefinition":
        from src.core.strategy_registry import StrategyRegistry
        try:
            return StrategyRegistry.get(strategy_id)
        except KeyError:
            raise ValueError(
                f"Unknown strategy {strategy_id!r}. Registered strategies: "
                f"{', '.join(sorted(StrategyRegistry.get_names()))}."
            ) from None
    ```
    ```python
    def _normalise_parameters(definition, parameters: dict[str, Any]) -> dict[str, Any]:
        if definition.param_model is None:
            raise ValueError(
                f"Strategy {definition.name!r} registers no parameter model, so its parameters "
                "cannot be validated or stored losslessly. Add "
                "StrategyRegistry.set_param_model() at the bottom of its module."
            )
        try:
            return definition.param_model.model_validate(parameters).model_dump()
        except PydanticValidationError as exc:
            raise ValueError(f"Invalid parameters for {definition.name!r}: {exc}") from exc
    ```
    ```python
    def _resolve_bar_types(raw: Sequence[str]) -> tuple[str, ...]:
        from src.core.live_market_data import LiveMarketDataError, resolve_live_bar_types
        try:
            return tuple(str(bt) for bt in resolve_live_bar_types(list(raw)))
        except LiveMarketDataError as exc:
            raise ValueError(str(exc)) from exc
    ```
    Every one of these **must** convert its native exception to `ValueError` — see Dev Notes ⚠️. Import `ValidationError as PydanticValidationError` at module top level (pydantic is not on the forbidden list).
  - [x] `StrategySpec(BaseModel)` with `model_config = ConfigDict(frozen=True)`:
    - `strategy_id: str = Field(..., min_length=1, description="Canonical StrategyRegistry name")`
    - `parameters: dict[str, Any] = Field(default_factory=dict, ...)`
    - `bar_types: tuple[str, ...] = Field(..., min_length=1, ...)`
  - [x] One `@model_validator(mode="before")` on `StrategySpec` — **not** `mode="after"`. An after-validator cannot assign to a frozen model, and the parameter normalisation needs `strategy_id` and `parameters` together, which a field validator cannot see:
    ```python
    @model_validator(mode="before")
    @classmethod
    def _resolve_through_registry(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_id = data.get("strategy_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return data          # let the field constraint report the real problem
        definition = _lookup_strategy(raw_id.strip())
        resolved = dict(data)
        resolved["strategy_id"] = definition.name          # canonical, AC #2
        resolved["parameters"] = _normalise_parameters(definition, data.get("parameters") or {})
        if data.get("bar_types") is not None:
            resolved["bar_types"] = _resolve_bar_types(data["bar_types"])
        return resolved
    ```
    The `isinstance(data, dict)` and `raw_id` guards are load-bearing: without them, `model_validate` on a non-dict, or a missing/blank `strategy_id`, raises from inside the helper instead of producing pydantic's own "field required" error.
  - [x] `StrategySpec.from_overrides(...)` classmethod — the constructor the CLI will use in Story 2.2, and the one AC #3 is about. Mirrors `BacktestRequest.from_cli_args` (`src/models/backtest_request.py:306-391`) as the house classmethod-constructor pattern:
    ```python
    @classmethod
    def from_overrides(cls, *, strategy_id: str, overrides: dict[str, Any],
                       settings: Any, bar_types: Sequence[str]) -> "StrategySpec":
        from src.core.strategy_factory import StrategyLoader
        parameters = StrategyLoader.build_strategy_params(strategy_id, dict(overrides), settings)
        return cls(strategy_id=strategy_id, parameters=parameters, bar_types=tuple(bar_types))
    ```
    `settings` is **injected, never fetched** — the same discipline `build_trading_node_config` states at `src/core/live_node_builder.py:163-165`. Keyword-only, so a positional mix-up is impossible.
  - [x] `SessionSpec(BaseModel)` with `model_config = ConfigDict(frozen=True)`:
    - `schema_version: int = Field(default=1, ge=1, ...)` — the spec is written once and read forever; this is the same versioning convention AR7 mandates for `session_conditions`.
    - `strategies: tuple[StrategySpec, ...] = Field(..., min_length=1, ...)` — **tuple, not list.** See Dev Notes ⚠️ "frozen is shallow".
  - [x] `SessionSpec.subscription_bar_types` — a `@property` returning every strategy's bar types flattened, deduplicated **case-insensitively**, in first-seen order. This is what Story 2.5 hands to `build_trading_node_config(bar_types=...)`. Key on `bar_type.upper()`, matching `resolve_live_bar_types`'s own dedup key (`src/core/live_market_data.py:205`). Without this, a naive `[bt for s in spec.strategies for bt in s.bar_types]` is rejected by the node builder the moment two strategies share an instrument.
  - [x] Google-style docstrings with Args/Returns/Raises on every public symbol; full type hints. Sizes: the module lands ~150–200 lines (limit 500), every function well under 50.
  - [x] Run the tests to **GREEN**.
- [x] **Task 3: Export the new symbols** (AC: #1, #6)
  - [x] Add `from .session import SessionSpec, SessionStatus, StrategySpec` to `src/models/__init__.py` and add all three to `__all__`, keeping it alphabetically sorted as it already is.
  - [x] ⚠️ Make the import and the `__all__` entries in a **single edit** — a half-applied edit leaves an F401 that hard-blocks the commit (`.githooks/pre-commit`, the Claude bash-guard, and CI all reject it).
  - [x] Confirm `python -c "import src.models"` still does **not** import Nautilus: `uv run python -c "import sys, src.models; print('nautilus_trader' in sys.modules)"` must print `False`. This is AC #8's real-world consequence and the reason the helpers import lazily.
- [x] **Task 4: Verify** (AC: all)
  - [x] `make test-unit` — the new file green **and** no regressions. Record the baseline as **collected** counts taken *before* any edit, not passed counts (Epic 1 retro, Key Insight #3).
  - [x] `make format && make lint` — clean. Note `make typecheck` runs `mypy src/core src/services` only, so it does **not** cover `src/models` — do not read a clean typecheck as evidence this file is typed. Type it fully anyway (project rule), and sanity-check by eye.
  - [x] `make test-coverage` covers `src/core` + `src/strategies` only — `src/models/session.py` will **not** appear in the report. Do not chase a coverage number for it; AC #9 is the coverage contract here.
  - [x] Grep checks: `grep -n "get_settings\|os.environ\|getenv" src/models/session.py` returns nothing; `grep -n "^from nautilus\|^import nautilus\|^from ibapi\|^import ibapi\|^from sqlalchemy" src/models/session.py` returns nothing.
  - [x] Mutation check on the one load-bearing new guard (Epic 1 retro, Action Item #3): temporarily change `strategies: tuple[...]` to `list[...]`, confirm the frozen-append test **fails**, then revert. A test that cannot fail is worse than no test.

## Dev Notes

### What this story is
The whole of Epic 2 rests on one immutable value object. This story builds that object and nothing
else — no table, no service, no CLI, no runner. It is deliberately the smallest possible first story
in the epic, for the same reason Story 1.1 was a pure function: the shape it fixes here is the shape
seven later stories consume, and the cost of getting it wrong compounds.
[Source: epics.md#Story-2.1 (lines 722–759); architecture.md#Naming-Patterns (line 387)]

### Why "a list" is the entire point of the story
FR13 is a **shape** requirement, not a capability requirement. This phase always supplies exactly one
strategy (NFR29 — one concurrent session, multi-strategy is not a goal). The story exists so that the
day a second strategy is wanted, it is a configuration change rather than a refactor of every consumer
that assumed `spec.strategy`. That is why AC #1 mandates a two-entry spec must validate even though
nothing will ever build one this phase — **do not "simplify" it to a single strategy with a TODO.**

### ⚠️ `frozen=True` is shallow — this is why `strategies` is a tuple
Pydantic's `frozen=True` blocks *attribute rebinding* only. A `list` field on a frozen model still
accepts `.append()`, which would let a caller add a strategy to a spec that FR14 says is immutable for
the session's whole life. Verified against pydantic 2.11.9:

```
class Outer(BaseModel):
    model_config = ConfigDict(frozen=True)
    items: list[Inner]
o.items.append(...)      # succeeds — the spec grew after being "frozen"
o.titems.append(...)     # AttributeError — tuples have no append
```

So `strategies: tuple[StrategySpec, ...]`. `bar_types: tuple[str, ...]` for the same reason.

**The residual, stated honestly:** `parameters: dict[str, Any]` is *still* mutable in place —
`spec.strategies[0].parameters["x"] = 1` succeeds on a frozen model. This is a known and accepted
boundary, not an oversight. The immutability guarantee FR14 actually needs is on the **record**, and
that is enforced in Story 2.2 by there being no code path in either repository that updates the `spec`
column — the same write-once discipline `config_snapshot` already has. Mutating an in-memory copy
loaded from the DB corrupts nothing. Document this limit in the model's docstring; do **not** write a
docstring claiming the spec is deeply immutable. (Epic 1 retro, "Challenges": documentation
repeatedly overstated protection in the safety-relevant direction, three separate times.)

### ⚠️ `Decimal` does not survive a naive JSON round trip — this is what AC #5 is defending
`SMAParameters.portfolio_value` and `position_size_pct` are `Decimal`
(`src/models/strategy.py:29-37`). Stored in a bare `dict[str, Any]` and round-tripped, pydantic
serialises `Decimal("1000000")` to the JSON string `"1000000"` and **deserialises it back as `str`**,
because `Any` gives it nothing to coerce toward. Verified:

```
M(params={"portfolio_value": Decimal("1000000")})
→ '{"params":{"portfolio_value":"1000000"}}'
→ back.params["portfolio_value"] is '1000000'   # str, not Decimal — round trip is LOSSY
```

The fix is the `_normalise_parameters` helper: because the before-validator re-runs on
`model_validate_json`, the parameters are re-coerced **through the strategy's own registered
`param_model`** on the way back in, and `SMAParameters.model_validate({"portfolio_value": "1000000"})`
restores the `Decimal`. Verified end-to-end: round trip equality `True`, type `Decimal`. This is not
incidental — it is the only reason AC #5 is satisfiable, so do not "optimise" the before-validator
into an after-validator or skip normalisation when parameters arrive already-typed.

### ⚠️ A `KeyError` raised inside a pydantic validator does **not** become a `ValidationError`
Pydantic v2 converts only `ValueError` and `AssertionError`. Anything else propagates untouched.
Verified: a validator raising `KeyError` escapes as a raw `KeyError`, and a custom exception escapes
as itself. Two live cases in this story:

- `StrategyRegistry.get()` raises **`KeyError`** on an unknown name (`src/core/strategy_registry.py:186`).
- `resolve_live_bar_types()` raises **`LiveMarketDataError`**, a plain `Exception` subclass
  (`src/core/live_market_data.py:60-68`).

Both must be caught and re-raised as `ValueError`, or AC #2 and AC #7 silently fail — the spec would
still reject bad input, but with the wrong exception type, and every caller expecting
`pydantic.ValidationError` (the CLI in Story 2.2, the API later) would crash instead of reporting.
Use `from None` on the registry case so the operator sees one clean message rather than a chained
`KeyError`; use `from exc` on the bar-type case so the original stays in the traceback.

### ⚠️ `StrEnum` is a deliberate divergence from the repo's `(str, Enum)` habit
Every other enum in this codebase is `(str, Enum)` (`src/core/live_gate.py`, `src/models/strategy.py:12`).
AC #6 mandates `StrEnum` (Python 3.11+, and `requires-python = ">=3.11"` holds). This is deliberate,
not an inconsistency to be "corrected": with `(str, Enum)`, `str(GateMode.PAPER)` renders
`"GateMode.PAPER"` rather than `"paper"` — which is exactly wrong for a value headed into JSONB, a
`--json` payload, and a DB enum column. That defect is already logged against Story 1.1
(`deferred-work.md:45-51`, "worth settling before Story 1.7 logs a GateDecision"). New code takes the
correct form; retrofitting the existing enums is a separate, project-wide call and **is not this
story's job**.

### Target instruments are bar-type strings, and they get validated here
The AC says "target instruments" without naming a form. The live path has exactly one form:
`build_trading_node_config(settings, *, trader_id, bar_types: Sequence[str] = (), ...)`
(`src/core/live_node_builder.py:145-152`). So `StrategySpec.bar_types` holds strings of the shape
`AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL`, and Story 2.5 hands them straight through.

Validating them **at spec construction** rather than at session start is load-bearing, not
belt-and-braces: FR14 freezes the spec for the session's entire life, so a spec accepted with an
unusable bar type can never be repaired — the operator has to create a whole new session. And the
failure modes `resolve_live_bar_types` catches are all silent ones: an INTERNAL bar type never reaches
the IBKR adapter, a composite bar type publishes on a topic the subscription never listens to, and
both produce a connected session that receives nothing, forever, with no error anywhere
(`src/core/live_market_data.py:170-197`). This is the Epic 1 retro's Key Insight #2 — config-time
validation beats kernel-time validation on the safety-critical path — applied at the earliest point
that exists.

**Reuse `resolve_live_bar_types`; do not write a second bar-type parser.** It already handles parse
failure, INTERNAL aggregation, composite forms, non-time-aggregated specs, and case-insensitive
duplicates, each with an operator-facing message.

### The one cross-strategy subtlety: duplicates mean different things at different levels
`resolve_live_bar_types` **rejects** a repeated bar type, because each subscription burns one IBKR
market-data line and a duplicate costs a line while delivering nothing new
(`src/core/live_market_data.py:199-211`). That is correct **within one strategy**. It is wrong
**across strategies** — two strategies trading AAPL is a legitimate configuration, and the session
needs exactly one subscription serving both.

So: validate each `StrategySpec`'s own `bar_types` through `resolve_live_bar_types` (intra-spec
duplicates rejected), and let `SessionSpec.subscription_bar_types` do the cross-strategy
deduplication before anything reaches the node builder. Without that property, the first two-strategy
session ever configured would be refused at startup by a function doing exactly its job. AC #1's
"same instrument in two entries is legal" and the two-entry test case both exist to pin this.

### Parameters: what `build_strategy_params` does, and one thing it silently does not
The chain lives on **`StrategyLoader`**, not `StrategyFactory` — `epics.md` line 744 says
"`StrategyFactory.build_strategy_params()`", and no such method exists. The real symbol is
`StrategyLoader.build_strategy_params(strategy_type, overrides, settings)` at
`src/core/strategy_factory.py:296-380`, in the same module. Resolution order is override →
`param_model._settings_map` → Pydantic default (`:344-370`), returning `model_dump()`.

**Known limitation, do not try to fix it here:** the function iterates
`param_model_cls.model_fields`, so an override key that is *not* a field of the param model is
**silently dropped** — `overrides={"fast_perios": 12}` produces a spec with the default `fast_period`
and no error. Pre-existing, affects the backtest path identically, and widening it is a change to a
shared function outside this story's footprint. Record it in `deferred-work.md` under
"Deferred from: story-2.1" so Story 2.2's CLI can decide whether to validate override keys before
calling.

Every currently registered strategy has a `param_model` (`sma_crossover`, `momentum`, and the five
`custom/` submodule strategies), so `_normalise_parameters`' `None` branch is defensive rather than
reachable today — but it must exist, because `StrategyRegistry.register()` makes `param_model`
optional (`src/core/strategy_registry.py:84`) and a `None` would otherwise surface as an
`AttributeError` deep inside pydantic.

### Canonical names, because the registry is forgiving and the spec must not be
`StrategyRegistry.get()` resolves direct name → alias → underscore/hyphen-stripped fuzzy match
(`src/core/strategy_registry.py:170-186`). `sma_crossover`, `smacrossover`, and `sma` all return the
same definition. If the spec stored the operator's raw input, two sessions running an identical
strategy would hold non-equal specs, and every later comparison — including Epic 5's backtest
comparison — would be reading noise. Store `definition.name`.

### This story closes one item from `deferred-work.md`, partially
`deferred-work.md:38-43` records that `IBKRSettings.model_dump()` returns account and password in
clear, and flags that it "becomes live the moment Epic 2 persists session configuration." AC #4 is
that moment, handled the only way that actually works: the spec has **no field** those values could
occupy, so no serialisation of it can leak them. The underlying `IBKRSettings.model_dump()` behaviour
is unchanged and stays deferred — do not claim otherwise in the completion notes.

### Scope boundaries — do NOT do these here
- **No Alembic migration, no `trading_sessions` table, no ORM model, no repositories.** Story 2.2 owns
  the phase's single migration and both repositories (AR4, AR8, AR9).
- **No `SessionService`, no `transition()`, no `InvalidSessionTransition`.** Story 2.3 (AR37). This
  story defines the `SessionStatus` vocabulary; it defines no transitions between the values.
- **No `trader_id` derivation, no `RedisSettings`.** Story 2.4 (AR10, AR11).
- **No CLI.** No `live create`, no `ntrader live` additions of any kind. Stories 2.2 / 2.5 / 2.8.
- **No runner, no Nautilus node, no startup phases.** Story 2.5 (AR39) — and note the Epic 1 retro's
  `NautilusKernel.start_async()` hook finding is a **2.5** constraint, not a 2.1 one.
- **No `SessionConditions`.** `architecture.md`'s delta tree (line 518-519) lists it in this same file
  with `schema_version=1`, but it belongs to FR45/AR7 and lands in Epic 5, Story 5.4. Adding it now
  would be dead code carrying a schema nothing writes.
- **`name` and `linked_backtest_run_id` are NOT spec fields.** They are `trading_sessions` columns
  (AR4), captured at creation in Story 2.2. The spec is what the session *runs*, not how it is
  addressed or what it is compared against.
- **No new dependency.** `pyproject.toml` / `uv.lock` must be unchanged (AR3). Nothing here needs one,
  and a bash-guard hook blocks editing `pyproject.toml` by hand regardless.

### Judgment calls made while writing this story (flag at the Epic 2 retro)
1. **`tuple`, not `list`, for `strategies` and `bar_types`.** The AC says "list"; a pydantic `list`
   field on a frozen model is appendable, which defeats FR14. Read "list" as "ordered collection of
   ≥1", which a tuple satisfies. Proven, not assumed — see the frozen-is-shallow note above.
2. **Bar-type validation folded into this story.** AC #7 is not in `epics.md`'s AC text for 2.1;
   "target instruments" is. Rationale in the section above — the spec is frozen forever, so this is
   the last moment the check is free.
3. **`schema_version` on `SessionSpec`.** Not mandated for `spec` (AR7 mandates it for
   `session_conditions`). Added because the spec is write-once into JSONB and read by 2.5, 2.8 and
   every Epic 5 story; without it a future field addition has no way to tell old rows from new.
4. **`from_overrides` as a classmethod constructor rather than validator-internal settings access.**
   Keeps `get_settings()` out of the model (AC #3) while still putting the resolution chain *in* the
   model where Story 2.2's CLI can reach it in one call. Mirrors `BacktestRequest.from_cli_args`.
5. **`subscription_bar_types` as a derived property.** Small addition; prevents Story 2.5 from
   writing the naive flatten that the node builder rejects.

### Project Structure Notes
- **NEW** `src/models/session.py` — domain model, joins `backtest_request.py`, `strategy.py`,
  `config_snapshot.py`. Domain models (`src/models/`) and DB models (`src/db/models/`) are never mixed
  (project-context.md:111); the `TradingSession` ORM class is Story 2.2's, in `src/db/models/`.
  [Source: architecture.md#Delta-Project-Tree lines 517-519]
- **MOD** `src/models/__init__.py` — three exports.
- **NEW** `tests/unit/models/test_session_spec.py` — the exact path the architecture's delta tree
  names (line 545).
- Untouched by construction: `src/api/**`, `templates/**`, `src/db/**`, `src/services/**`,
  `src/core/**`, every strategy file, `alembic/**`. [Source: architecture.md AR44]
- Reads from `src/core/*` (registry, factory, live_market_data) at validation time only — the import
  direction `models → core` matches `src/models/strategy.py`'s existing dependency on the registry, so
  no new cycle is introduced. Confirmed: none of the three modules imports `src.models`.

### Testing standards
- **Unit tier** — no DB, no network, no broker → `make test-unit` (runs `pytest tests/unit -n auto`).
  Note the tier is defined by **directory**, not by marker, and its "no Nautilus" description is a
  convention rather than an enforcement: 14 files under `tests/unit/` already import `nautilus_trader`
  transitively. This story's tests will too, via `StrategyRegistry.discover()`. That is expected and
  fine — do not try to mock the registry out to avoid it, since AC #2 is specifically about resolving
  through the real one. [Source: Makefile:34-37; CLAUDE.md Decision Heuristics]
- **TDD is non-negotiable**: Task 1 must be RED before Task 2. [Source: development-principles.md]
- `pytest.ini` is the effective config, not the `[tool.pytest.ini_options]` block in `pyproject.toml`
  (which lists a different, narrower marker set). `unit` is registered at `pytest.ini:25`.
- `StrategyRegistry` holds **class-level** state and `discover()` imports the `custom/` git submodule.
  Do not call `StrategyRegistry.clear()` in these tests — it is process-global and `-n auto` runs
  tests in parallel workers; clearing it can strand a sibling test in the same worker with an empty
  registry. Read from it; never reset it.
- Assert on the unknown-strategy message by substring (`"sma_crossover" in str(exc)`), not by exact
  string — the registered set includes the `custom/` submodule strategies and will change as that
  submodule does.

### Commit hygiene for this repo
- Structural import gate: an unused (F401) or undefined (F821) import hard-blocks the commit at three
  points (`.githooks/pre-commit`, the Claude bash-guard, CI). Make dependent changes — import plus its
  usage — in a single edit. Run `make install-hooks` once per clone.
- Stage and commit in **separate** Bash calls (`git add <files>`, then `git commit`).
- Commit format `<type>(<scope>): <subject>`, e.g.
  `feat(live): model a session as an immutable list of strategy specs`. Never reference AI or Claude.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story-2.1] (lines 722–759) — story statement and the acceptance criteria this file numbers
- [Source: _bmad-output/planning-artifacts/epics.md#Epic-2] (lines 713–721) — epic scope, FR/NFR/AR coverage
- [Source: _bmad-output/planning-artifacts/epics.md#Additional-Requirements] — AR3 (zero new deps), AR4 (`trading_sessions` columns — Story 2.2), AR7 (`schema_version` convention), AR9 (dual repositories — Story 2.2), AR36 (normative vocabulary), AR37 (state machine — Story 2.3), AR38 (runner/service boundary), AR44 (untouched files)
- [Source: _bmad-output/planning-artifacts/epics.md#FR-Coverage-Map] (lines 278–318) — FR13, FR20, FR52, FR53 all owned by Epic 2
- [Source: _bmad-output/planning-artifacts/architecture.md#Naming-Patterns] (lines 384–392) — `src/models/session.py` → `SessionSpec`, `StrategySpec`, `SessionStatus` (StrEnum)
- [Source: _bmad-output/planning-artifacts/architecture.md#Delta-Project-Tree] (lines 517–519, 545) — the story's exact file footprint
- [Source: _bmad-output/planning-artifacts/prd.md] — FR13 (multi-strategy session definition), FR14 (immutable spec), FR20 (four states), FR52/FR53 (credentials via env only; instruments and params at creation), NFR25, NFR29
- [Source: _bmad-output/implementation-artifacts/epic-1-retro-2026-08-17.md] — Key Insight #2 (config-time > kernel-time validation), #3 (baseline as collected counts), Action Item #3 (mutation testing on new load-bearing guards)
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:38-43] — the `model_dump()` credential leak flagged as going live "the moment Epic 2 persists session configuration"
- [Source: _bmad-output/implementation-artifacts/deferred-work.md:45-51] — `(str, Enum)` vs `StrEnum` rendering, previously deferred
- [Source: src/core/strategy_registry.py:150-186] — `get()` with alias/fuzzy resolution, raising `KeyError` whose message already lists available names
- [Source: src/core/strategy_registry.py:84,232-291] — `param_model` is optional at registration; `discover()` also scans the `custom/` submodule
- [Source: src/core/strategy_factory.py:296-380] — `StrategyLoader.build_strategy_params()`, the real home of the resolution chain
- [Source: src/core/live_market_data.py:134-215] — `resolve_live_bar_types()`, its five refusal cases and its upper-cased dedup key
- [Source: src/core/live_market_data.py:60-68] — `LiveMarketDataError` is a plain `Exception`, not a `ValueError`
- [Source: src/core/live_node_builder.py:145-175] — `build_trading_node_config(..., bar_types: Sequence[str])`, the consumer that fixes the bar-type-string form, and its "injected, never fetched" settings discipline
- [Source: src/models/strategy.py:29-37] — `SMAParameters` `Decimal` fields, the reason the JSON round trip needs re-coercion
- [Source: src/models/strategy.py:55-61] — the `slow_period > fast_period` cross-field rule used by the invalid-parameters test
- [Source: src/models/strategy.py:108-136] — precedent for lazy-importing `StrategyRegistry` inside a validator
- [Source: src/models/backtest_request.py:306-391] — `from_cli_args`, the house classmethod-constructor pattern `from_overrides` follows
- [Source: tests/unit/models/test_config_snapshot.py:9-38] — the model unit-test idiom (class per model, `pytest.raises(ValidationError)`, `.errors()` inspection)
- [Source: tests/unit/core/test_live_gate.py] — the `ast`-based import-purity guard this story narrows
- [Source: pytest.ini:13-31] — `--strict-markers`, registered `unit` marker
- [Source: Makefile:34-37,83-86,100-102] — `test-unit` is directory-scoped; coverage and mypy both exclude `src/models`
- [Source: _bmad-output/project-context.md:44-57,102-115] — imports, `TYPE_CHECKING`, Decimal money, docstrings, size limits, domain-vs-DB model separation
- [Source: CLAUDE.md] — commit format, structural import gate, staging discipline, `pyproject.toml` via `uv` only

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

None — implementation went RED → GREEN on the first pass with no debugging required. The one
adjustment made after GREEN was cosmetic: `from_overrides`'s docstring originally said "never calls
`get_settings()`", which is literally true but tripped Task 4's own grep gate
(`grep -n "get_settings\|os.environ\|getenv"`), since the check does not distinguish code from prose.
Reworded to "never loads settings on its own" so the gate returns clean, as the story's own Task 4
subtask requires.

### Completion Notes List

- All three symbols (`SessionSpec`, `StrategySpec`, `SessionStatus`) implemented exactly to the Dev
  Notes' pre-verified design: `StrEnum` for the status vocabulary, `tuple[...]` (not `list[...]`) for
  both `strategies` and `bar_types` per the frozen-is-shallow finding, a `mode="before"` validator on
  `StrategySpec` that canonicalises the strategy id, normalises parameters through the strategy's own
  `param_model`, and validates `bar_types` through `resolve_live_bar_types`, converting both
  `KeyError` (registry) and `LiveMarketDataError` (bar types) to `ValueError` so pydantic turns them
  into `ValidationError`.
- TDD followed literally: `tests/unit/models/test_session_spec.py` (25 tests) was written first and
  confirmed RED (`ModuleNotFoundError: No module named 'src.models.session'`) before
  `src/models/session.py` was written. All 25 tests passed on the first implementation attempt — no
  red-green iteration was needed beyond the initial RED confirmation.
- Verified empirically, not assumed: baseline `tests/unit` collected count was 1718 (taken before any
  edit, per the Epic 1 retro's collected-not-passed lesson); after the story's changes, `make
  test-unit` reports 1743 passed — exactly 1718 + 25 new, zero regressions. `make format` and `make
  lint` are clean. `make typecheck` (`mypy src/core src/services`) is unaffected, as the story's own
  notes predicted it would not cover `src/models`; a standalone `mypy src/models/session.py
  --ignore-missing-imports` run as a sanity check (not part of the make target, per project rule to
  type fully anyway) reports no issues. `uv run python -c "import sys, src.models; print('nautilus_trader'
  in sys.modules)"` prints `False`, confirming AC #8's real-world consequence.
- Also ran the flat `make test-coverage` target (`pytest tests --cov=src/core --cov=src/strategies`)
  as a sanity check beyond the story's own Task 4 minimum. It reported 10 failures, all pre-existing
  and unrelated to this story — `test_live_check.py`, `test_epic1_ac_*.py`, `test_trades_api.py` — none
  touch `src/models/session.py` or anything this story added. Confirmed pre-existing by re-running
  `tests/unit/core/test_live_check.py` in isolation: 42/42 pass. This matches the cross-test-state
  contamination pattern already documented in `deferred-work.md`'s story-1.3 section (a flat,
  unforked, non-tiered `pytest tests` run does not get the isolation `make test-unit`/`test-component`/
  `test-integration --forked` each provide) — not a regression this story introduced.
- Mutation check performed exactly as Task 4 specifies: temporarily changed `strategies:
  tuple[StrategySpec, ...]` to `list[StrategySpec]`, re-ran
  `TestFrozen::test_session_spec_strategies_tuple_has_no_append`, watched it fail with `Failed: DID
  NOT RAISE <class 'AttributeError'>`, then reverted. The guard is load-bearing, not vacuous.
- Recorded the one open item Dev Notes flagged in advance — `StrategyLoader.build_strategy_params`
  silently drops an override key that is not a param-model field — as a new "Deferred from: story-2.1"
  section in `deferred-work.md`, exactly as instructed, for Story 2.2's CLI to decide on.
- Scope discipline held: no Alembic migration, no ORM model, no repositories, no `SessionService`, no
  `trader_id` derivation, no CLI, no runner. Confirmed by `git status` — only the five files below
  touched.
- One judgment call beyond the five the story already flagged for the Epic 2 retro: the grep-check
  false-positive above is worth a broader note for anyone writing docstrings for the `src/models/`
  layer in this repo — a docstring that *mentions* a forbidden call (to explain what a function does
  *not* do) can trip Task-4-style textual gates meant to catch actual usage. Not fixed generically
  here since only this one instance existed.

### File List

- `src/models/session.py` (new) — `SessionSpec`, `StrategySpec`, `SessionStatus`, and their three
  private lazy-import helpers.
- `src/models/__init__.py` (modified) — exports `SessionSpec`, `SessionStatus`, `StrategySpec`,
  alphabetically ordered into the existing `__all__`.
- `tests/unit/models/test_session_spec.py` (new) — 25 unit tests covering AC #1–#9.
- `_bmad-output/implementation-artifacts/deferred-work.md` (modified) — new "Deferred from: story-2.1"
  section recording the pre-existing override-key-drop limitation in `StrategyLoader.build_strategy_params`.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (modified) — story status
  `ready-for-dev` → `in-progress` → `review`.

## Change Log

| Date | Change |
|---|---|
| 2026-08-17 | Story drafted — comprehensive context assembled from epics.md, architecture.md, the Epic 1 retrospective, deferred-work.md, and the existing registry/factory/live-market-data source. Design verified by execution against pydantic 2.11.9 and the live registry before drafting. |
| 2026-08-17 | Implemented per Dev Notes' pre-verified design. TDD Red→Green on the first pass: 25 new unit tests, all passing; 1743 total unit tests passed (1718 baseline + 25 new), zero regressions. `make format`/`make lint` clean; mutation check on the frozen-tuple guard confirmed it fails when weakened. Recorded the pre-existing `build_strategy_params` override-drop limitation in `deferred-work.md` under "Deferred from: story-2.1". Status: ready-for-dev → review. |
