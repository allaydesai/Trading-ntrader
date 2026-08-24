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
#: on `degrade()` rather than `stop()` precisely because `stop()` would reach
#: `sma_crossover.on_stop()`'s `close_all_positions()` — so if any module needs
#: this scan, it is that one. This list is hand-maintained and its own comment
#: below records that it already shrank once through the omission.
STOP_PATH_MODULES = (
    "src/core/live_session_runner.py",
    "src/core/live_session_signals.py",
    "src/core/live_session_steady_state.py",
    "src/core/live_session_node.py",
    "src/core/live_strategy_guard.py",
    "src/cli/commands/live.py",
    "src/cli/commands/live_start.py",
)

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
#: `flatten`), never on raw source text: the CLI's own operator warning says
#: "still flattens its own positions", and a substring check over the file
#: would fire on that prose — which is why the dead form was tolerable and the
#: live form must not be textual.
FORBIDDEN_FLATTEN_FRAGMENT = "flatten"

#: AC #3 — the stop path shares no code with the seal path.
FORBIDDEN_SEAL_NAMES = frozenset({"SEALED", "sealed_at", "sealed_run_id"})

#: The known, disclosed exception: `sma_crossover.on_stop` still flattens its
#: own positions today. `node.stop()` reaches it through
#: `Trader._stop() -> Strategy.on_stop()`, which is real Nautilus machinery
#: this story does not touch — see the ⚠️ under Story 2.6's AC #2. Story 3.1
#: removes the call; this test is the tripwire that proves when it has.
KNOWN_RESIDUAL_MODULE = "src/core/strategies/sma_crossover.py"


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
        """The reason it is an identifier scan: `live.py` says "flattens" in
        its own operator warning, and a textual check would trip on it.
        """
        prose = 'x = "sma_crossover.on_stop() still flattens its own positions"'
        assert not {n for n in _called_names(prose) if FORBIDDEN_FLATTEN_FRAGMENT in n}

    def test_the_known_limit_is_pinned_sma_crossover_still_flattens_today(self):
        """The known-limit pin (Task 7's fourth RED bullet).

        A RED here means Story 3.1 landed and deleted the call — **delete
        this test, do not weaken it.** Until then, this assertion documents
        the gap rather than hiding it: AC #2's ⚠️ says this story does not
        make the epic's clause true end to end, and this test is how that
        stays true in code, not only in prose.
        """
        called = _called_names(_source(KNOWN_RESIDUAL_MODULE))
        assert "close_all_positions" in called, (
            "sma_crossover.on_stop() no longer calls close_all_positions() — if Story 3.1 "
            "removed it, DELETE this test (do not weaken it); Story 2.6's AC #2 residual is closed."
        )


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
