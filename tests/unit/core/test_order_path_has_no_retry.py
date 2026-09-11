"""AST scan proving the order path forbids retry, backoff, and resubmission
(Story 3.4, AC #2, AR24/AR43).

Unit tier: a pure ``ast`` walk over source text — no import, no execution, no
Nautilus. The same discipline ``test_live_stop_path_is_inert.py`` documents:
a docstring that merely *mentions* "retry" would satisfy a substring search
without the code ever doing it, so every rule here matches on the AST's own
node kind and identifier, never on raw text.

Three independent rules, each proven non-vacuous by planting its own
forbidden shape in a throwaway module and re-running the scanner over it
(``TestTheProbeCanActuallyFail``, the established twin name from
``test_live_dependency_invariance.py:198``):

(a) no import of a known retry/backoff library;
(b) no decorator whose dotted name contains "retry" or "backoff";
(c) no ``submit_order``/``submit_order_list`` call sitting inside a
    ``for``/``while`` loop or an ``except`` handler, anywhere in its ancestor
    chain — not merely the same enclosing function.
"""

import ast
from pathlib import Path

import pytest

from tests.component.core.test_live_dependency_invariance import LIVE_MODULE_GLOBS

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: AC #2 — retry libraries this order path must never import. Pinned as an
#: exact set (CLAUDE.md's membership-pinned-lists discipline): shrinking this
#: silently would let a real retry dependency slip past rule (a) unnoticed.
FORBIDDEN_RETRY_LIBRARIES = frozenset({"tenacity", "backoff", "retrying", "stamina"})

#: AC #2 — the two order-submitting calls rule (c) forbids inside a loop or
#: an except handler. Pinned as an exact set for the same reason.
FORBIDDEN_LOOP_SUBMIT_CALLS = frozenset({"submit_order", "submit_order_list"})

#: Story 3.1's `STRATEGY_MODULES` shape (`test_live_stop_path_is_inert.py:109-115`):
#: globbed, not listed, so a new strategy file is scanned the day it appears.
#: `custom/` is an unversioned git submodule and is excluded by construction —
#: `Path.glob("*.py")` does not descend into it.
_STRATEGY_MODULES = tuple(
    sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in (PROJECT_ROOT / "src" / "core" / "strategies").glob("*.py")
        if path.name != "__init__.py"
    )
)


def _scanned_module_paths() -> tuple[Path, ...]:
    """`LIVE_MODULE_GLOBS` plus every built-in strategy module, resolved to
    absolute paths. Recomputed per call (not module-level) so a file added or
    removed mid-session is always reflected — the modules themselves rarely
    change within a test run, but the glob is cheap and this avoids any
    import-order staleness.
    """
    paths: list[Path] = []
    for pattern in LIVE_MODULE_GLOBS:
        paths.extend(sorted(PROJECT_ROOT.glob(pattern)))
    paths.extend(PROJECT_ROOT / rel for rel in _STRATEGY_MODULES)
    return tuple(paths)


def _import_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".")[0])
    return roots


def _forbidden_import_hits(tree: ast.Module) -> set[str]:
    return _import_roots(tree) & FORBIDDEN_RETRY_LIBRARIES


def _decorator_text(decorator: ast.expr) -> str:
    try:
        return ast.unparse(decorator)
    except Exception:  # pragma: no cover - defensive only, ast.unparse is stdlib
        return ""


def _forbidden_decorator_hits(tree: ast.Module) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                text = _decorator_text(decorator).lower()
                if "retry" in text or "backoff" in text:
                    hits.append(f"{node.name}: @{_decorator_text(decorator)}")
    return hits


def _build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _has_loop_or_except_ancestor(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Walk the FULL ancestor chain to the module root — not merely the same
    enclosing function — so a call nested inside further structure (a
    ``try``/``with``/``if`` inside the loop, for instance) is still caught.
    """
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.For | ast.AsyncFor | ast.While | ast.ExceptHandler):
            return True
        current = parents.get(current)
    return False


def _callee_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _forbidden_loop_submit_hits(tree: ast.Module) -> list[str]:
    parents = _build_parent_map(tree)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name not in FORBIDDEN_LOOP_SUBMIT_CALLS:
            continue
        if _has_loop_or_except_ancestor(node, parents):
            hits.append(f"{name} at line {node.lineno}")
    return hits


def _scan_source(source: str) -> dict[str, list[str]]:
    tree = ast.parse(source)
    return {
        "imports": sorted(_forbidden_import_hits(tree)),
        "decorators": _forbidden_decorator_hits(tree),
        "loop_submits": _forbidden_loop_submit_hits(tree),
    }


class TestTheModuleSetIsNonVacuous:
    def test_at_least_eight_modules_are_scanned(self):
        paths = _scanned_module_paths()

        assert len(paths) >= 8, f"expected the live + strategy modules, found {paths}"

    def test_live_order_path_is_included(self):
        names = {path.name for path in _scanned_module_paths()}

        assert "live_order_path.py" in names

    def test_sma_crossover_is_included(self):
        names = {path.name for path in _scanned_module_paths()}

        assert "sma_crossover.py" in names


class TestNoRetryLibraryIsImported:
    def test_no_scanned_module_imports_a_forbidden_retry_library(self):
        offenders: dict[str, set[str]] = {}
        for path in _scanned_module_paths():
            hits = _forbidden_import_hits(ast.parse(path.read_text(encoding="utf-8")))
            if hits:
                offenders[str(path.relative_to(PROJECT_ROOT))] = hits

        assert not offenders, f"forbidden retry library imports found: {offenders}"


class TestNoRetryOrBackoffDecorator:
    def test_no_scanned_module_carries_a_retry_or_backoff_decorator(self):
        offenders: dict[str, list[str]] = {}
        for path in _scanned_module_paths():
            hits = _forbidden_decorator_hits(ast.parse(path.read_text(encoding="utf-8")))
            if hits:
                offenders[str(path.relative_to(PROJECT_ROOT))] = hits

        assert not offenders, f"retry/backoff decorators found: {offenders}"


class TestNoSubmitOrderInsideALoopOrExceptHandler:
    def test_no_scanned_module_calls_submit_order_inside_a_loop_or_except(self):
        offenders: dict[str, list[str]] = {}
        for path in _scanned_module_paths():
            hits = _forbidden_loop_submit_hits(ast.parse(path.read_text(encoding="utf-8")))
            if hits:
                offenders[str(path.relative_to(PROJECT_ROOT))] = hits

        assert not offenders, f"submit_order call(s) inside a loop/except found: {offenders}"

    def test_close_position_inside_a_for_loop_is_not_flagged(self):
        """The sma_crossover shape (`sma_crossover.py:206, 241`):
        `for position in positions: self.close_position(position)` is one
        close per position, not a retry — this rule must not generalise to
        every order-touching call inside a loop, only the two named ones.
        """
        source = (
            "class S:\n"
            "    def on_stop(self):\n"
            "        for position in self.cache.positions_open():\n"
            "            self.close_position(position)\n"
        )

        hits = _forbidden_loop_submit_hits(ast.parse(source))

        assert hits == []


class TestForbiddenSetsArePinnedExactly:
    """CLAUDE.md's membership-pinned-lists discipline: an intersection-only
    consumer lets a shrunk set pass silently, so both sets are asserted equal
    to their literal spelling here.
    """

    def test_forbidden_retry_libraries_is_pinned(self):
        assert FORBIDDEN_RETRY_LIBRARIES == frozenset(
            {"tenacity", "backoff", "retrying", "stamina"}
        )

    def test_forbidden_loop_submit_calls_is_pinned(self):
        assert FORBIDDEN_LOOP_SUBMIT_CALLS == frozenset({"submit_order", "submit_order_list"})


class TestTheProbeCanActuallyFail:
    """Non-vacuity twin (the `test_live_dependency_invariance.py:198` idiom):
    plant each forbidden shape in a throwaway module and confirm the SAME
    scanner functions used above actually go red — one case per rule.
    """

    def test_the_import_rule_catches_a_planted_tenacity_import(self):
        source = "import tenacity\n\n\ndef f():\n    pass\n"

        result = _scan_source(source)

        assert result["imports"] == ["tenacity"]

    def test_the_import_rule_catches_a_planted_from_import(self):
        source = "from backoff import on_exception\n"

        result = _scan_source(source)

        assert result["imports"] == ["backoff"]

    def test_the_decorator_rule_catches_a_planted_retry_decorator(self):
        source = (
            "import tenacity\n\n\n"
            "@tenacity.retry(stop=tenacity.stop_after_attempt(3))\n"
            "def submit():\n"
            "    pass\n"
        )

        result = _scan_source(source)

        assert result["decorators"] != []

    def test_the_decorator_rule_catches_a_planted_backoff_decorator(self):
        source = "@my_backoff_wrapper\ndef submit():\n    pass\n"

        result = _scan_source(source)

        assert result["decorators"] != []

    def test_the_loop_rule_catches_submit_order_inside_a_for_loop(self):
        source = (
            "class S:\n"
            "    def go(self, orders):\n"
            "        for order in orders:\n"
            "            self.submit_order(order)\n"
        )

        result = _scan_source(source)

        assert "submit_order at line 4" in result["loop_submits"]

    def test_the_loop_rule_catches_submit_order_inside_a_while_loop(self):
        source = (
            "class S:\n"
            "    def go(self, order):\n"
            "        while True:\n"
            "            self.submit_order(order)\n"
            "            break\n"
        )

        result = _scan_source(source)

        assert result["loop_submits"] != []

    def test_the_loop_rule_catches_submit_order_inside_an_except_handler(self):
        source = (
            "class S:\n"
            "    def go(self, order):\n"
            "        try:\n"
            "            risky()\n"
            "        except Exception:\n"
            "            self.submit_order(order)\n"
        )

        result = _scan_source(source)

        assert result["loop_submits"] != []

    def test_the_loop_rule_walks_the_full_ancestor_chain_not_just_the_function(self):
        """A call nested inside further structure (`if` inside the loop)
        must still be caught — proving the ancestor walk does not stop at
        the nearest enclosing statement.
        """
        source = (
            "class S:\n"
            "    def go(self, orders):\n"
            "        for order in orders:\n"
            "            if order.is_ready:\n"
            "                with open('x') as f:\n"
            "                    self.submit_order(order)\n"
        )

        result = _scan_source(source)

        assert result["loop_submits"] != []

    def test_a_bare_submit_order_call_outside_any_loop_is_not_flagged(self):
        """The clean baseline every rule above must NOT fire on."""
        source = "class S:\n    def go(self, order):\n        self.submit_order(order)\n"

        result = _scan_source(source)

        assert result["loop_submits"] == []

    def test_the_scan_finds_nothing_in_a_module_with_none_of_the_shapes(self):
        source = "def f(x):\n    return x + 1\n"

        result = _scan_source(source)

        assert result == {"imports": [], "decorators": [], "loop_submits": []}


class TestTheScannerWorksOverAPlantedFile:
    """The `tmp_path` module form Task 2.3 names explicitly: read a real
    file off disk (not just a source string) through the exact function the
    production scan calls.
    """

    def test_a_planted_file_with_all_three_shapes_is_caught(self, tmp_path):
        planted = tmp_path / "planted_retry.py"
        planted.write_text(
            "import tenacity\n\n\n"
            "@tenacity.retry(stop=tenacity.stop_after_attempt(3))\n"
            "def helper():\n"
            "    pass\n\n\n"
            "class S:\n"
            "    def go(self, orders):\n"
            "        for order in orders:\n"
            "            self.submit_order(order)\n",
            encoding="utf-8",
        )

        result = _scan_source(planted.read_text(encoding="utf-8"))

        assert result["imports"] == ["tenacity"]
        assert result["decorators"] != []
        assert result["loop_submits"] != []
