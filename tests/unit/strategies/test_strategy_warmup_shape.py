"""Story 4.4, AC #1/#2 — every in-repo strategy warms the same, mode-agnostic way.

AST scans over ``STRATEGY_MODULES`` (globbed in
``tests/unit/core/test_live_stop_path_is_inert.py``, so a new
``src/core/strategies/*.py`` is scanned the day it appears; the ``custom/``
submodule is excluded there and here). What they pin:

- **No execution-mode branch** (AR40): zero ``is_live`` identifiers anywhere in
  a strategy file.
- **The live stream starts only after history** (AC #1): ``on_start`` calls
  ``register_indicator_for_bars`` and ``request_bars`` and never
  ``subscribe_bars``; exactly one method calls ``subscribe_bars``; it is the
  one ``on_start`` passes as ``request_bars``'s ``callback``; and the call is
  its **last** statement, so the crossover baseline is recorded before the
  first live bar can arrive.
- **The history callback cannot trade** (AR43): it is not in
  ``LIFECYCLE_HOOKS``, so the stop-path scan never looks at it — this closes
  that gap for the new method only.
- **No live coupling**: a strategy imports nothing from ``src.core.live_*``.

Every scan has a planted-probe twin, the repo rule since Story 2.3's AR37 guard
passed a probe it should have caught.
"""

import ast

import pytest

from tests.unit.core.test_live_stop_path_is_inert import (
    FORBIDDEN_ORDER_METHODS,
    PROJECT_ROOT,
    STRATEGY_MODULES,
)

pytestmark = pytest.mark.unit


def _tree(relative_path: str) -> ast.Module:
    return ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def _methods(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    """Every method of every class in the module, by name."""
    methods: dict[str, ast.FunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods[item.name] = item
    return methods


def _called(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _identifiers(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _history_callback_name(on_start: ast.FunctionDef) -> str | None:
    """The method ``on_start`` hands ``request_bars`` as ``callback=self.<name>``."""
    for call in ast.walk(on_start):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "request_bars"
        ):
            for keyword in call.keywords:
                value = keyword.value
                if keyword.arg == "callback" and isinstance(value, ast.Attribute):
                    return value.attr
    return None


def _shape_problems(tree: ast.Module) -> list[str]:
    """Everything AC #1/#2 forbids, as readable strings; empty when conformant."""
    problems: list[str] = []
    if "is_live" in _identifiers(tree):
        problems.append("branches on is_live")
    methods = _methods(tree)
    on_start = methods.get("on_start")
    if on_start is None:
        return problems + ["has no on_start"]
    started = _called(on_start)
    for required in ("register_indicator_for_bars", "request_bars"):
        if required not in started:
            problems.append(f"on_start does not call {required}")
    if "subscribe_bars" in started:
        problems.append("on_start subscribes before history has loaded")
    callback = _history_callback_name(on_start)
    subscribers = sorted(n for n, m in methods.items() if "subscribe_bars" in _called(m))
    if callback is None or subscribers != [callback]:
        problems.append(f"subscribe_bars is called from {subscribers}, not the history callback")
    elif not (
        isinstance(last := methods[callback].body[-1], ast.Expr)
        and "subscribe_bars" in _called(last)
    ):
        problems.append("subscribe_bars is not the history callback's last statement")
    if callback is not None and callback in methods:
        if hits := _called(methods[callback]) & FORBIDDEN_ORDER_METHODS:
            problems.append(f"the history callback calls {sorted(hits)}")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("src.core.live_"):
            problems.append(f"imports {node.module}")
    return problems


class TestEveryStrategyWarmsTheSameWay:
    @pytest.mark.parametrize("relative_path", STRATEGY_MODULES)
    def test_the_warm_up_shape_holds(self, relative_path):
        assert _shape_problems(_tree(relative_path)) == []

    def test_both_built_ins_are_scanned(self):
        assert "src/core/strategies/sma_crossover.py" in STRATEGY_MODULES
        assert "src/core/strategies/sma_momentum.py" in STRATEGY_MODULES


CONFORMANT = """
class S:
    def on_start(self):
        self.register_indicator_for_bars(self.bar_type, self.fast)
        self.request_bars(self.bar_type, start=s, callback=self._loaded)

    def _loaded(self, request_id):
        self._prev = self.fast.value
        self.subscribe_bars(self.bar_type)
"""


class TestTheScanCanFail:
    """Planted probes — each mutation of the conformant shape must be caught."""

    def test_the_conformant_probe_passes(self):
        assert _shape_problems(ast.parse(CONFORMANT)) == []

    @pytest.mark.parametrize(
        ("mutation", "expected"),
        [
            (
                (
                    "        self.request_bars(",
                    "        if self.is_live:\n            pass\n        self.request_bars(",
                ),
                "branches on is_live",
            ),
            (
                ("        self.register_indicator_for_bars(self.bar_type, self.fast)\n", ""),
                "on_start does not call register_indicator_for_bars",
            ),
            (
                (
                    "callback=self._loaded)",
                    "callback=self._loaded)\n        self.subscribe_bars(b)",
                ),
                "on_start subscribes before history has loaded",
            ),
            (
                (
                    "        self.subscribe_bars(self.bar_type)\n",
                    "        self.subscribe_bars(self.bar_type)\n        self._prev = None\n",
                ),
                "subscribe_bars is not the history callback's last statement",
            ),
            (
                ("        self._prev = self.fast.value\n", "        self.submit_order(o)\n"),
                "the history callback calls ['submit_order']",
            ),
        ],
    )
    def test_each_mutation_is_named(self, mutation, expected):
        old, new = mutation
        planted = CONFORMANT.replace(old, new)
        assert planted != CONFORMANT, "the probe no longer patches the conformant source"

        assert expected in _shape_problems(ast.parse(planted))

    def test_a_live_import_is_caught(self):
        planted = "from src.core.live_session_warmup import X\n" + CONFORMANT

        assert "imports src.core.live_session_warmup" in _shape_problems(ast.parse(planted))
