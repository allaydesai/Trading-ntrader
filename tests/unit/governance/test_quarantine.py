"""The quarantine list: known-flaky tests the PR pipeline skips until they are fixed.

``@pytest.mark.quarantine(reason="...")`` takes a test out of the PR lane
(``-m "not quarantine"`` in ``.github/workflows/ci.yml``). The nightly workflow
still runs every quarantined test, so a fix shows up there before the marker is
removed. A quarantine is a debt, not a skip: each entry must say why, and the
list is capped so it cannot grow unnoticed.
"""

import ast
from pathlib import Path
from typing import TypeGuard

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[2]

#: Raise this only deliberately, in the same commit as the new quarantine.
MAX_QUARANTINED = 5


def _is_quarantine_attr(node: ast.AST) -> TypeGuard[ast.Attribute]:
    """``<anything>.mark.quarantine``: the marker, called or bare."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "quarantine"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _reason(call: ast.Call | None) -> str:
    """The ``reason=`` string literal, or "" when it is missing or not a string."""
    if call is None:
        return ""
    for keyword in call.keywords:
        if (
            keyword.arg == "reason"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        ):
            return keyword.value.value.strip()
    return ""


def scan(source: str) -> list[tuple[int, str]]:
    """Every quarantine marker in ``source`` as ``(line, reason)``.

    A bare ``@pytest.mark.quarantine`` (no call) still deselects the test, so it
    counts too, with an empty reason.
    """
    tree = ast.parse(source)
    called = {
        id(node.func): node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_quarantine_attr(node.func)
    }
    return [
        (node.lineno, _reason(called.get(id(node))))
        for node in ast.walk(tree)
        if _is_quarantine_attr(node)
    ]


def _repo_entries() -> list[tuple[str, str]]:
    entries = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        for line, reason in scan(path.read_text()):
            entries.append((f"{path.relative_to(TESTS_ROOT)}:{line}", reason))
    return entries


def test_every_quarantine_says_why():
    missing = [where for where, reason in _repo_entries() if not reason]
    assert not missing, f"quarantine without a reason= string: {missing}"


def test_the_quarantine_list_stays_small():
    count = len(_repo_entries())
    assert count <= MAX_QUARANTINED, (
        f"{count} quarantined tests (cap {MAX_QUARANTINED}): fix one before adding another"
    )


def test_the_marker_is_registered(pytestconfig: pytest.Config):
    registered = [line.split(":")[0].split("(")[0] for line in pytestconfig.getini("markers")]
    assert "quarantine" in registered


class TestTheScanCannotBeSidestepped:
    def test_a_called_marker_with_a_reason_is_found_once(self):
        assert scan('@pytest.mark.quarantine(reason="flaky")\ndef test_x(): ...\n') == [
            (1, "flaky")
        ]

    def test_a_bare_marker_counts_and_has_no_reason(self):
        assert scan("@pytest.mark.quarantine\ndef test_x(): ...\n") == [(1, "")]

    def test_a_module_level_pytestmark_counts(self):
        assert scan("pytestmark = pytest.mark.quarantine\n") == [(1, "")]

    @pytest.mark.parametrize("value", ["None", "True", "0", '"   "', "REASON"])
    def test_a_reason_that_is_not_a_non_empty_string_literal_is_no_reason(self, value):
        assert scan(f"@pytest.mark.quarantine(reason={value})\ndef test_x(): ...\n") == [(1, "")]
