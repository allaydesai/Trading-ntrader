"""Guard against a typed failure silently escaping the exit_outcome protocol (D3).

Unit tier, ``ast`` only — importing every ``src/core/live_*.py`` for real would
pull in Nautilus, which the unit tier forbids, and ``src/db/exceptions.py``
must stay importable with no dependency of its own (see
``TestExitOutcomeModuleIsStdlibOnly`` below). Same shape as
``tests/unit/core/test_live_stop_path_is_inert.py``: a structural scan over
source text, not a textual one, so a docstring that merely mentions
``exit_outcome`` cannot satisfy it.

The Epic 2 retro's D3 ruling: every exception class that reaches
``live_check.classify_failure`` must declare its exit code deliberately, via
an ``exit_outcome``/``operator_safe_message`` marker pair, or be named in
``UNMARKED`` below. ``src/core/live_*.py`` is a glob, not a hand-maintained
list — CLAUDE.md's guard-list-rot anti-pattern (a split silently escaping a
hand-maintained list) cannot happen to this one, and ``src/db/exceptions.py``
and ``src/core/live_check.py`` (itself matched by the glob) are added
explicitly for the other two locations the ruling names.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Exceptions deliberately left unmarked: generic exit 1 (`ERROR`), type-name-
#: only message. `StrategyGuardError` is a tripwire for a strategy shape that
#: does not exist in the repo today (see its own docstring) — it is a live-path
#: exception that genuinely reaches `classify_failure`, just not usefully.
#: (`SessionStopRequested`, `src/core/live_session_signals.py`, needs no entry
#: here at all: its name ends in neither `Error` nor `Transition`, so this
#: scan's filter never selects it in the first place — and it never reaches
#: `classify_failure` regardless, consumed inside the runner's own stop path.)
#:
#: The five below are `src/db/exceptions.py` classes that predate D3 and have
#: nothing to do with it: they are backtest-persistence errors from a wholly
#: different code path (`src/services/backtest_persistence.py` and friends)
#: that never reaches `ntrader live *`'s CLI or `live_check.classify_failure`.
#: Only `InvalidSessionTransition` and `RecordNotFoundError` — both raised on
#: the live-session path — are D3's concern in this module.
UNMARKED: frozenset[str] = frozenset(
    {
        "StrategyGuardError",
        "BacktestStorageError",
        "ValidationError",
        "DatabaseConnectionError",
        "DuplicateRecordError",
        "InstrumentMappingError",
    }
)


def _target_files() -> list[Path]:
    files = sorted((REPO_ROOT / "src" / "core").glob("live_*.py"))
    files.append(REPO_ROOT / "src" / "db" / "exceptions.py")
    return files


def _error_classes(path: Path) -> list[ast.ClassDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and (node.name.endswith("Error") or node.name.endswith("Transition"))
    ]


def _carries_marker(class_node: ast.ClassDef, marker_name: str) -> bool:
    return any(
        isinstance(stmt, ast.AnnAssign)
        and isinstance(stmt.target, ast.Name)
        and stmt.target.id == marker_name
        for stmt in class_node.body
    )


class TestNoTypedFailureEscapesTheMarkerProtocol:
    """The whole point of the ruling: a new typed failure fails this test
    until its author decides its exit code, rather than silently classifying
    as a generic error and going unnoticed.
    """

    def test_every_error_class_is_marked_or_explicitly_unmarked(self):
        undeclared: list[str] = []
        partially_marked: list[str] = []

        for path in _target_files():
            for node in _error_classes(path):
                if node.name in UNMARKED:
                    continue
                has_outcome = _carries_marker(node, "exit_outcome")
                has_safe = _carries_marker(node, "operator_safe_message")
                if has_outcome and has_safe:
                    continue
                if not has_outcome and not has_safe:
                    undeclared.append(f"{path.relative_to(REPO_ROOT)}::{node.name}")
                else:
                    partially_marked.append(f"{path.relative_to(REPO_ROOT)}::{node.name}")

        assert undeclared == [], (
            "typed failure(s) with neither marker and not in UNMARKED — decide "
            f"their exit code or add them to UNMARKED deliberately: {undeclared}"
        )
        assert partially_marked == [], (
            f"class(es) carrying only one of exit_outcome/operator_safe_message "
            f"(both or neither, never one): {partially_marked}"
        )

    def test_unmarked_set_names_only_classes_that_still_exist(self):
        """Stale entries must be removed — an allowlist that never shrinks
        when its subject is renamed or deleted is not a guard.
        """
        all_names = {node.name for path in _target_files() for node in _error_classes(path)}

        stale = UNMARKED - all_names
        assert stale == frozenset(), f"UNMARKED names classes that no longer exist: {stale}"


class TestExitOutcomeModuleIsStdlibOnly:
    """Sibling of ``test_live_check.py::TestModulePurity`` for the new leaf
    module: any exception class anywhere in the codebase must be able to
    import ``exit_outcome`` for its markers without acquiring a framework or
    I/O dependency by doing so — this is exactly what lets
    ``src/db/exceptions.py`` (no imports of its own before D3) carry one.
    """

    def test_module_imports_only_the_standard_library(self):
        from src.core import exit_outcome

        source = Path(exit_outcome.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        # Anything under `src.*` or a third-party package would defeat the
        # point of a leaf module every exception class can depend on.
        offenders = [name for name in imported if name.startswith("src.")]
        assert offenders == [], f"exit_outcome must stay stdlib-only, found: {offenders}"
