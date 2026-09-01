"""AST scan proving the stop path submits and seals nothing (Story 2.6, AC #2/#3).

Unit tier: an ``ast`` walk over source text imports nothing and touches no
Nautilus, no broker, no event loop. Structural rather than textual on purpose —
Stories 2.1, 2.3 and 2.5 each recorded the same trap: a docstring that merely
*mentions* a forbidden name satisfies a substring search without the code ever
calling it. Every scan here asserts on the AST's own attribute/function
**name**, and every scan proves it is not vacuous by finding the six methods
when pointed at a module that genuinely calls them.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: The modules AC #2 names, by path relative to the repo root.
#:
#: ``src/cli/commands/live_start.py`` joined the list at review (2026-08-22).
#: AC #2 names "the CLI", and `claim_session`/`exit_with`/`release_quietly` —
#: including the only `mark_stopped()` call outside the runner — were inside
#: `live.py` and therefore covered until this story's file-size split moved
#: them out. The guard's coverage shrank as a side effect of a refactor, which
#: is exactly how a structural guarantee rots.
#: ``src/core/live_strategy_guard.py`` joined at Story 2.7 (AC #7). Its
#: containment path runs *while positions are open*, and its whole design turns
#: on `degrade()` rather than `stop()` — because `stop()` runs strategy-owned
#: teardown code mid-containment, whatever that hook's body happens to be
#: (Story 3.1 removed `sma_crossover`'s flatten; the verb argument is about
#: *when* strategy code runs, not about one strategy's current body). So if any
#: module needs this scan, it is that one. This list is hand-maintained and its
#: own comment below records that it already shrank once through the omission.
STOP_PATH_MODULES = (
    "src/core/live_session_runner.py",
    "src/core/live_session_signals.py",
    "src/core/live_session_steady_state.py",
    "src/core/live_session_node.py",
    "src/core/live_strategy_guard.py",
    "src/cli/commands/live.py",
    "src/cli/commands/live_start.py",
)

#: Story 3.2's ``src/core/live_order_path.py`` is deliberately **NOT** in the
#: list above, and the reason is structural rather than an oversight: this
#: scan is call-callee-only (``_called_names`` below), so it forbids any
#: **call** to a forbidden order method anywhere in a scanned module — and
#: that module's whole job is to call the wrapped originals
#: (``base(*args, **kwargs)`` inside its suppression wrapper) when the
#: connection is healthy. Adding it here would make the module's own
#: legitimate pass-through path an automatic scan failure. Its callers (the
#: runner, the CLI) call only ``install_order_path(...)`` — a name this scan
#: does not forbid — so their own membership stays clean without needing the
#: module itself on the list.

#: ``src/core/live_exec_avg_px.py`` (2026-09-01) is likewise **NOT** on the list,
#: for a simpler reason: it is build-time only. It runs once inside
#: ``node.build()``, replaces one adapter-private dict, and is never reached
#: again — least of all from the stop path. It calls no order or position method
#: at all, so its membership would be inert rather than protective.

#: The order/position-mutating methods AC #2 forbids on the stop path. Any
#: call whose *attribute or function name* matches one of these is a hit,
#: regardless of what object it is called on (`self.close_all_positions(...)`,
#: `node.trader.close_all_positions(...)` and a bare `close_all_positions(...)`
#: must all be caught alike).
FORBIDDEN_ORDER_METHODS = frozenset(
    {
        "close_all_positions",
        "close_position",
        "cancel_all_orders",
        "cancel_order",
        "submit_order",
        "submit_order_list",
    }
)

#: AC #2's final clause, "**and no `flatten`**", enforced as its own scan
#: (review fix, 2026-08-22). It used to be collected into the set above and
#: then intersected with :data:`FORBIDDEN_ORDER_METHODS`, which does not
#: contain it — so the clause was dead code that could never produce a hit,
#: while the scan cheerfully reported `flatten` present in `live.py` every run.
#: Matched on *identifiers* (any callee or attribute whose name contains
#: `flatten`), never on raw source text: prose in these modules discusses
#: flattening (this file's own probes do too, and `live.py`'s operator warning
#: did until Story 3.1 rewrote it), and a substring check over the file would
#: fire on that prose — which is why the dead form was tolerable and the live
#: form must not be textual.
FORBIDDEN_FLATTEN_FRAGMENT = "flatten"

#: AC #3 — the stop path shares no code with the seal path.
FORBIDDEN_SEAL_NAMES = frozenset({"SEALED", "sealed_at", "sealed_run_id"})

#: Story 3.1, AC #3 — every non-submodule strategy file. `custom/` is an
#: unversioned git submodule this repo cannot pin or scan; both scans below
#: exclude it (disclosed here per the story's clarification of the amended AC).
#:
#: **Globbed, not listed** (review fix, 2026-08-29): the first implementation
#: hand-listed the two built-ins, which recreated exactly the failure mode
#: CLAUDE.md's Anti-Patterns section names — a guard list that rots invisibly
#: because nothing asserts its completeness. A new `src/core/strategies/*.py`
#: is now scanned the day it appears, not the day someone remembers to add it.
#: The sibling `LIVE_MODULE_GLOBS` (`test_live_dependency_invariance.py:39`)
#: is globbed for the same reason and says so.
STRATEGY_MODULES = tuple(
    sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "src" / "core" / "strategies").glob("*.py")
        if path.name != "__init__.py"
    )
)

#: The lifecycle hooks AC #3 scans. Deliberately excludes `on_bar`: it
#: legitimately submits orders, and scanning it would make the guard
#: unsatisfiable by design, not by accident.
#:
#: Membership is pinned by :meth:`TestStrategyLifecycleHooksAreInert
#: .test_the_lifecycle_hook_list_is_complete` (review fix, 2026-08-29):
#: previously only ``on_stop``'s presence and ``on_bar``'s absence were
#: asserted anywhere, so six of the seven could be dropped and the AC #3 scan
#: would quietly stop covering them.
LIFECYCLE_HOOKS = (
    "on_start",
    "on_stop",
    "on_resume",
    "on_reset",
    "on_dispose",
    "on_degrade",
    "on_fault",
)


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _called_names(source: str) -> set[str]:
    """Every function/attribute name that appears as the callee of a Call node.

    Covers ``self.close_all_positions(...)``, ``node.trader.close_all_positions(...)``
    and a bare ``close_all_positions(...)`` alike: an ``ast.Attribute`` callee
    contributes its ``.attr``, an ``ast.Name`` callee contributes its ``.id``.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def _lifecycle_hook_bodies(source: str) -> list[tuple[str, str]]:
    """``(hook_name, unparsed_source)`` for every lifecycle-hook method in the module.

    Scope is direct-hook-body only: each hook's own ``FunctionDef`` node is
    unparsed and fed through :func:`_called_names` / :func:`_referenced_names`
    independently of the rest of the module, so a call in ``on_bar`` (which
    legitimately submits orders) can never leak into an ``on_stop`` scan and
    vice versa. A hook that delegated the flatten to a private helper method
    would evade this — disclosed limitation, matches AR43's letter rather
    than a call-graph analysis this repo does not have.

    Both ``ast.FunctionDef`` and ``ast.AsyncFunctionDef`` are matched (review
    fix, 2026-08-29): the first implementation matched the sync form only, so
    an ``async def on_stop`` was invisible to the scan — a second evasion path
    beside the private-helper one, and undisclosed. Nautilus's own hooks are
    sync, which is what kept it from mattering, not the guard.
    """
    tree = ast.parse(source)
    hooks: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name in LIFECYCLE_HOOKS
        ):
            hooks.append((node.name, ast.unparse(node)))
    return hooks


def _referenced_names(source: str) -> set[str]:
    """Every attribute/name identifier in the module, for the AC #3 scan.

    Broader than ``_called_names``: ``SessionStatus.SEALED`` is an attribute
    *access*, never called, and ``sealed_at``/``sealed_run_id`` appear as
    plain attribute reads and keyword arguments, not calls.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


class TestTheStopPathSubmitsNothing:
    """AC #2 — zero order/position calls anywhere on the five stop-path modules."""

    @pytest.mark.parametrize("relative_path", STOP_PATH_MODULES)
    def test_no_forbidden_order_method_is_called(self, relative_path):
        called = _called_names(_source(relative_path))
        hits = called & FORBIDDEN_ORDER_METHODS
        assert not hits, f"{relative_path} calls forbidden order/position method(s): {hits}"

    @pytest.mark.parametrize("relative_path", STOP_PATH_MODULES)
    def test_nothing_named_flatten_is_called(self, relative_path):
        """AC #2's "and no `flatten`" clause, which used to be dead code.

        Identifier-based, not textual — see :data:`FORBIDDEN_FLATTEN_FRAGMENT`.
        """
        hits = {
            name
            for name in _called_names(_source(relative_path))
            if FORBIDDEN_FLATTEN_FRAGMENT in name.lower()
        }
        assert not hits, f"{relative_path} calls a flatten-shaped method: {hits}"

    @pytest.mark.parametrize(
        "probe, expected",
        [
            ("self.close_all_positions(instrument_id=x)", "close_all_positions"),
            ("node.trader.close_position(pos)", "close_position"),
            ("self.cancel_all_orders(x)", "cancel_all_orders"),
            ("cancel_order(order)", "cancel_order"),
            ("self.submit_order(order)", "submit_order"),
            ("self.submit_order_list(bracket)", "submit_order_list"),
        ],
    )
    def test_the_scan_detects_every_forbidden_name_it_claims_to(self, probe, expected):
        """Non-vacuity for all six names, via planted probes.

        Review fix, 2026-08-22. This used to lean entirely on
        `sma_crossover.py` genuinely calling `close_all_positions` — which
        proved 1 of 6, and, worse, was the *same* assertion as the Story 3.1
        known-limit pin below. When 3.1 lands and both go red, deleting them
        would have left this scan with **no** non-vacuity guard at all: the
        precise regression Story 2.3's AR37 guard is cited for. Planted probes
        do not evaporate when production code changes.
        """
        assert expected in _called_names(probe)

    def test_the_flatten_scan_is_not_vacuous(self):
        """And the flatten clause, which had never been proved detectable."""
        called = _called_names("self.flatten_all_positions()")
        assert any(FORBIDDEN_FLATTEN_FRAGMENT in name for name in called)

    def test_the_flatten_scan_does_not_fire_on_prose(self):
        """The reason it is an identifier scan: prose in the scanned modules
        discusses flattening, and a textual check would trip on it. `live.py`
        said "flattens" in its own operator warning until Story 3.1 rewrote
        that text; the docstrings that explain *why* the flatten was removed
        remain, so the hazard this control covers outlives the warning.
        """
        prose = 'x = "sma_crossover.on_stop() no longer flattens its own positions"'
        assert not {n for n in _called_names(prose) if FORBIDDEN_FLATTEN_FRAGMENT in n}


class TestStrategyLifecycleHooksAreInert:
    """Story 3.1, AC #3 — no strategy lifecycle hook may flatten positions,
    submit/cancel orders, or branch on live-vs-backtest mode, in any
    built-in strategy under ``src/core/strategies/`` (``custom/`` excluded —
    see :data:`STRATEGY_MODULES`).

    Replaces the deleted
    ``test_the_known_limit_is_pinned_sma_crossover_still_flattens_today`` pin
    in spirit: that pin asserted the defect *existed*; this scan asserts it
    can never return, in any lifecycle hook, for all six
    :data:`FORBIDDEN_ORDER_METHODS` names — not just the one call Story 3.1
    happens to remove today.
    """

    @pytest.mark.parametrize("relative_path", STRATEGY_MODULES)
    def test_no_forbidden_order_method_is_called_in_a_lifecycle_hook(self, relative_path):
        source = _source(relative_path)
        offenders = [
            (hook_name, hits)
            for hook_name, hook_source in _lifecycle_hook_bodies(source)
            if (hits := _called_names(hook_source) & FORBIDDEN_ORDER_METHODS)
        ]
        assert not offenders, (
            f"{relative_path} calls forbidden order/position method(s) in a lifecycle hook: "
            f"{offenders}"
        )

    @pytest.mark.parametrize("relative_path", STRATEGY_MODULES)
    def test_is_live_is_never_referenced(self, relative_path):
        """AR40 — strategies are mode-agnostic; ``is_live`` = 0 everywhere."""
        referenced = _referenced_names(_source(relative_path))
        assert "is_live" not in referenced, (
            f"{relative_path} references `is_live` — strategies must be mode-agnostic (AR40)"
        )

    @pytest.mark.parametrize("forbidden_name", sorted(FORBIDDEN_ORDER_METHODS))
    def test_the_scan_catches_every_forbidden_name_planted_in_on_stop(self, forbidden_name):
        """Non-vacuity, driven from the frozenset itself — not hardcoded
        probe/expected pairs (unlike
        :meth:`TestTheStopPathSubmitsNothing.test_the_scan_detects_every_forbidden_name_it_claims_to`,
        which never consults :data:`FORBIDDEN_ORDER_METHODS`). Removing a name
        from the frozenset weakens this probe — but only by *deleting its own
        test case*, which is a silent shrink, not a red. That is what
        :meth:`test_the_forbidden_method_list_is_complete` is for; this probe
        proves detection, that one proves membership. Neither alone kills
        mutation M5.
        """
        probe = f"class Probe:\n    def on_stop(self):\n        self.{forbidden_name}()\n"
        hooks = _lifecycle_hook_bodies(probe)
        assert [name for name, _ in hooks] == ["on_stop"]
        hits = _called_names(hooks[0][1]) & FORBIDDEN_ORDER_METHODS
        assert forbidden_name in hits

    @pytest.mark.parametrize("forbidden_name", sorted(FORBIDDEN_ORDER_METHODS))
    def test_the_scan_does_not_fire_on_the_same_call_in_on_bar(self, forbidden_name):
        """Scope negative control: ``on_bar`` legitimately submits orders and
        is not in :data:`LIFECYCLE_HOOKS`, so a call planted there must never
        be flagged.
        """
        probe = f"class Probe:\n    def on_bar(self, bar):\n        self.{forbidden_name}()\n"
        assert _lifecycle_hook_bodies(probe) == []

    def test_the_is_live_scan_catches_a_planted_reference(self):
        probe = (
            "class Probe:\n    def on_start(self):\n        if self.is_live:\n            pass\n"
        )
        assert "is_live" in _referenced_names(probe)

    def test_the_scan_catches_a_forbidden_call_in_an_async_hook(self):
        """Review fix, 2026-08-29 — the scan matched ``ast.FunctionDef`` only,
        so ``async def on_stop`` was an undisclosed evasion path beside the
        private-helper one. Nautilus's hooks are sync, which is what kept this
        from mattering; nothing in the guard did.
        """
        probe = "class Probe:\n    async def on_stop(self):\n        self.close_all_positions(1)\n"
        hooks = _lifecycle_hook_bodies(probe)
        assert [name for name, _ in hooks] == ["on_stop"]
        assert "close_all_positions" in _called_names(hooks[0][1]) & FORBIDDEN_ORDER_METHODS

    def test_the_forbidden_method_list_is_complete(self):
        """Membership pin — what actually kills mutation M5 (review fix,
        2026-08-29).

        Every other consumer of :data:`FORBIDDEN_ORDER_METHODS` *intersects*
        with it, so dropping a name only ever weakens a scan or deletes a
        parametrized probe's own case. Nothing went red and no count was
        asserted, which made the guard silently disableable — the precise
        defect class this file exists to prevent. Asserted as an exact set:
        adding a name deliberately is a one-line edit here, and removing one
        accidentally is now impossible.
        """
        assert FORBIDDEN_ORDER_METHODS == frozenset(
            {
                "close_all_positions",
                "close_position",
                "cancel_all_orders",
                "cancel_order",
                "submit_order",
                "submit_order_list",
            }
        )

    def test_the_lifecycle_hook_list_is_complete(self):
        """Membership pin for :data:`LIFECYCLE_HOOKS` (review fix,
        2026-08-29): only ``on_stop``'s presence and ``on_bar``'s absence were
        asserted anywhere, so six of the seven could be dropped and the AC #3
        scan would quietly stop covering them. ``on_bar``'s exclusion is
        load-bearing and asserted here too — it legitimately submits orders.
        """
        assert LIFECYCLE_HOOKS == (
            "on_start",
            "on_stop",
            "on_resume",
            "on_reset",
            "on_dispose",
            "on_degrade",
            "on_fault",
        )
        assert "on_bar" not in LIFECYCLE_HOOKS

    def test_the_strategy_module_glob_finds_the_known_built_ins(self):
        """Non-vacuity for the glob (review fix, 2026-08-29). Every scan in
        this class is parametrized over :data:`STRATEGY_MODULES`; if the glob
        ever returned empty — a moved package, a renamed directory — every one
        of them would silently vanish rather than fail. Pins the two built-ins
        that exist today and the ``custom/`` exclusion, without pinning the
        list to exactly those two (that is what the glob is for).
        """
        assert "src/core/strategies/sma_crossover.py" in STRATEGY_MODULES
        assert "src/core/strategies/sma_momentum.py" in STRATEGY_MODULES
        assert not [path for path in STRATEGY_MODULES if "custom/" in path]
        assert not [path for path in STRATEGY_MODULES if path.endswith("__init__.py")]


class TestTheStopPathNeverSeals:
    """AC #3 — stop and seal share no code path."""

    @pytest.mark.parametrize("relative_path", STOP_PATH_MODULES)
    def test_no_seal_vocabulary_is_referenced(self, relative_path):
        referenced = _referenced_names(_source(relative_path))
        hits = referenced & FORBIDDEN_SEAL_NAMES
        assert not hits, f"{relative_path} references seal vocabulary: {hits}"

    def test_the_seal_scan_is_not_vacuous(self):
        """A planted probe, since no module in this repo seals yet (Epic 5)."""
        probe = "from src.models.session import SessionStatus\nx = SessionStatus.SEALED\n"
        referenced = _referenced_names(probe)
        assert "SEALED" in referenced


class TestTheVocabularyRuleAr36:
    """AR36 — the stop path says *stop*, never *pause*, *halt*, *kill*,
    *close* or *finalize*, in log event names and operator-facing text.

    Scoped to the two modules this story writes prose in: the signal policy
    and the runner's wiring. The other three stop-path modules are existing
    Story 2.5 code this story does not rewrite, and re-litigating their prose
    is out of scope.
    """

    NEW_OR_MODIFIED_FOR_STOP = (
        "src/core/live_session_signals.py",
        "src/core/live_session_runner.py",
        # Story 2.7's new module writes operator-facing prose of its own —
        # `strategy.failed`, `strategy.degraded`, `session.all_strategies_failed`
        # and their `detail=` text. AR36's vocabulary applies to it for the same
        # reason: an operator reading a contained failure must not see it
        # described as a kill or a halt. (`strategy.halted` would fail here.)
        "src/core/live_strategy_guard.py",
        # Story 3.2's `src/core/live_order_path.py` is deliberately **NOT**
        # added here — it is not a stop-path module (see the STOP_PATH_MODULES
        # comment above for why it is excluded from that list too). Hygiene
        # regardless, stated rather than enforced: this scan's word-boundary
        # regex (`\b{word}\w*\b`) matches the literal string `"close_position"`
        # (underscore is `\w`), so a raw method-name literal inside a log call
        # would trip it if the module ever joined the list. Today it never
        # appears as a literal — `log.warning(SUPPRESSED_EVENT, method=method_name,
        # ...)` passes the name through a variable, which this scan does not see
        # at all (it only walks `ast.Constant` string nodes inside log/print
        # calls).
        #
        # ⚠️ AR36 EXEMPTION, ruled 2026-08-30 during code review rather than
        # left as an accident of what the scan can see. At *runtime* a
        # suppressed close emits `order.suppressed method=close_position`, so
        # the forbidden `close` stem does reach an operator — the scan misses
        # it only because the value arrives through a variable. The exemption
        # is granted, and the reason is that AR36 governs how the **session**
        # is described (a stop must not read as a kill, a halt or a close),
        # not the spelling of a Python method name carried in a structured
        # field of an `order.*` record. Neutralising the value (`position_exit`
        # and friends) would cost the direct method-to-log correspondence that
        # makes a suppression record actionable, for no gain against the rule's
        # actual purpose. Recorded here so the next review does not re-open it.
    )
    #: AR36's list, with ``close`` **restored** (review fix, 2026-08-22,
    #: decision D3). It had been dropped silently — and it is the one word
    #: AC #3 and `epics.md:236` both name verbatim, while `finalize` (which
    #: AR36 assigns to *seal*, not *stop*) was kept and hits nothing.
    #:
    #: The reason it was dropped is real: `close_loop`, `is_closed`,
    #: `close_all_positions` are legitimate *code*. So the scan now looks only
    #: at **operator-facing text** — log event names, log message values and
    #: console strings — which is what the rule is for: an operator reading a
    #: stop in the log must not see it described as a close, a kill or a halt.
    #: Word-boundary matched, so "closed" in a sentence is a hit but
    #: "disclosure" is not.
    FORBIDDEN_WORDS = ("pause", "halt", "kill", "close", "finalize")

    @staticmethod
    def _operator_facing_strings(tree: ast.AST) -> list[str]:
        """String literals that reach an operator: log calls and `print`.

        Deliberately NOT every constant in the file — docstrings and inline
        comments explain the code to a developer and legitimately discuss
        closing loops and killing processes. AR36 governs what the *session*
        is called where an operator reads it.
        """
        found: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name not in {"debug", "info", "warning", "error", "critical", "exception", "print"}:
                continue
            for piece in [*node.args, *(kw.value for kw in node.keywords)]:
                for inner in ast.walk(piece):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        found.append(inner.value)
        return found

    @pytest.mark.parametrize("relative_path", NEW_OR_MODIFIED_FOR_STOP)
    def test_no_forbidden_vocabulary_reaches_an_operator(self, relative_path):
        tree = ast.parse(_source(relative_path))
        offenders = [
            (word, text)
            for text in self._operator_facing_strings(tree)
            for word in self.FORBIDDEN_WORDS
            if re.search(rf"\b{word}\w*\b", text.lower())
        ]
        assert not offenders, f"{relative_path} uses forbidden AR36 vocabulary: {offenders}"

    def test_the_vocabulary_scan_is_not_vacuous(self):
        """A planted probe, since no module in this repo violates AR36 today.

        Story 2.6 shipped this scan with no non-vacuity check at all, which
        its own Testing standards require of every AST scan.
        """
        probe = ast.parse('log.info("session.killed", detail="the session was closed")')
        texts = self._operator_facing_strings(probe)
        hits = {w for t in texts for w in self.FORBIDDEN_WORDS if re.search(rf"\b{w}\w*\b", t)}
        assert {"kill", "close"} <= hits, hits

    def test_the_vocabulary_scan_ignores_code_identifiers_and_comments(self):
        """The other half: `loop.close()` and a docstring about killing a
        process are not AR36 violations, and a scan that said so would be
        un-satisfiable — which is why `close` was dropped in the first place.
        """
        probe = ast.parse('"""Kill the loop and close it."""\nloop.close()\nx = is_closed()')
        assert self._operator_facing_strings(probe) == []
