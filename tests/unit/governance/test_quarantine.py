"""The quarantine list: known-flaky tests the PR pipeline skips until they are fixed.

``@pytest.mark.quarantine(reason="...")`` takes a test out of the PR lane
(``-m "not quarantine"`` in ``.github/workflows/ci.yml``). The nightly workflow
still runs every quarantined test, so a fix shows up there before the marker is
removed. A quarantine is a debt, not a skip: each entry must say why, and the
list is capped so it cannot grow unnoticed.

The rule is enforced on the marks pytest actually applies, at collection
(``tests/quarantine.py``, called from ``tests/conftest.py``), so it holds
however the marker is spelled: an aliased ``mark`` import, a class or module
``pytestmark``, or a parametrized test, which counts once per case.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.quarantine import MAX_QUARANTINED, check_quarantine

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Item:
    """The two things ``check_quarantine`` reads from a collected pytest item."""

    def __init__(self, nodeid: str, *marks: pytest.MarkDecorator) -> None:
        self.nodeid = nodeid
        self._marks = [m.mark for m in marks]

    def get_closest_marker(self, name: str):
        return next((m for m in self._marks if m.name == name), None)


def _q(**kwargs) -> pytest.MarkDecorator:
    return pytest.mark.quarantine(**kwargs)


def test_a_quarantine_with_a_reason_passes():
    check_quarantine([_Item("t::a", _q(reason="flaky under load")), _Item("t::b")])


@pytest.mark.parametrize(
    "mark",
    [
        pytest.mark.quarantine,  # bare
        _q(reason=None),
        _q(reason=0),
        _q(reason=True),
        _q(reason="   "),
        _q(),
    ],
    ids=["bare", "None", "zero", "True", "blank", "no-kwarg"],
)
def test_a_quarantine_without_a_string_reason_is_refused(mark):
    with pytest.raises(pytest.UsageError, match="t::a"):
        check_quarantine([_Item("t::a", mark)])


def test_the_cap_counts_tests_not_decorators():
    # One module-level pytestmark over six tests is six quarantined tests.
    items = [_Item(f"t::case{i}", _q(reason="flaky")) for i in range(MAX_QUARANTINED + 1)]
    with pytest.raises(pytest.UsageError, match=f"cap {MAX_QUARANTINED}"):
        check_quarantine(items)


def test_the_cap_itself_is_allowed():
    check_quarantine([_Item(f"t::case{i}", _q(reason="flaky")) for i in range(MAX_QUARANTINED)])


def test_the_marker_is_registered(pytestconfig: pytest.Config):
    registered = [line.split(":")[0].split("(")[0] for line in pytestconfig.getini("markers")]
    assert "quarantine" in registered


def test_the_check_runs_before_marker_deselection(tmp_path: Path):
    """``-m "not quarantine"`` must not hide a bad entry from the check."""
    (tmp_path / "conftest.py").write_text(
        "import pytest\n"
        "from tests.quarantine import check_quarantine, register\n"
        "def pytest_configure(config):\n"
        "    register(config)\n"
        "@pytest.hookimpl(tryfirst=True)\n"
        "def pytest_collection_modifyitems(items):\n"
        "    check_quarantine(items)\n"
    )
    (tmp_path / "test_bad.py").write_text(
        "from pytest import mark\n@mark.quarantine\ndef test_x():\n    pass\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "not quarantine", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, result.stdout + result.stderr
    assert "quarantine without a reason" in result.stderr
    assert "test_x" in result.stderr
