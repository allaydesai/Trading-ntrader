"""Pytest configuration, fixtures, and acceptance-criteria reporting."""

import gc
import re
from pathlib import Path

import pytest

#: Name of the marker a test carries to declare which acceptance criterion it
#: evidences: ``@pytest.mark.acceptance_criterion(id, text, source)``.
ACCEPTANCE_MARKER = "acceptance_criterion"

#: Key under which the marker's payload travels on ``TestReport.user_properties``.
#: It has to travel that way rather than being read off the item directly: under
#: ``-n auto`` the test runs in an xdist worker and only the report crosses back
#: to the controller, and under ``--forked`` it runs in a fork of that worker.
#: ``user_properties`` is part of the report and survives both hops.
_ACCEPTANCE_PROPERTY = "acceptance-criterion"

#: Field separator inside that payload. ASCII unit separator, so no criterion
#: text can contain it by accident.
_FIELD_SEP = "\x1f"

#: Criterion id -> {"text", "source", "outcome"}, filled on the controller as
#: reports arrive. Module state rather than a stash because
#: ``pytest_terminal_summary`` and ``pytest_runtest_logreport`` are the same
#: process and the same plugin instance.
_acceptance_results: dict[str, dict[str, str]] = {}

_STORY_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)")


@pytest.fixture
def project_root():
    """Get project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def test_data_dir(project_root):
    """Get test data directory."""
    return project_root / "data"


@pytest.fixture(autouse=True)
def cleanup():
    """
    Auto-cleanup between tests.

    Runs after every test to:
    - Force garbage collection (clears C extension refs)
    - Prevent state leakage between tests
    """
    yield  # Test runs here
    gc.collect()  # Force cleanup


def pytest_configure(config):
    """Register the acceptance-criterion marker (``--strict-markers`` is on)."""
    config.addinivalue_line(
        "markers",
        "acceptance_criterion(id, text, source): tie this test to one acceptance "
        "criterion from a requirements document, so the run's terminal summary "
        "reports, per criterion, whether a test actually evidenced it.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Stamp the criterion onto the report before anything can skip the test.

    ``tryfirst`` matters: a ``skipif`` marker raises from another plugin's
    ``pytest_runtest_setup``, and a criterion whose test was skipped must still
    appear in the summary — reported as SKIP — rather than vanishing from it.
    """
    marker = item.get_closest_marker(ACCEPTANCE_MARKER)
    if marker is None:
        return
    payload = _FIELD_SEP.join(str(arg) for arg in (marker.args + ("", "", ""))[:3])
    item.user_properties.append((_ACCEPTANCE_PROPERTY, payload))


def pytest_runtest_logreport(report):
    """Fold each phase's report into the criterion's worst-so-far outcome."""
    for name, value in report.user_properties:
        if name != _ACCEPTANCE_PROPERTY:
            continue
        ac_id, text, source = (str(value).split(_FIELD_SEP) + ["", "", ""])[:3]
        entry = _acceptance_results.setdefault(
            ac_id, {"text": text, "source": source, "outcome": "PASS"}
        )
        if report.failed:
            entry["outcome"] = "FAIL"
        elif report.skipped and entry["outcome"] == "PASS":
            entry["outcome"] = "SKIP"


def pytest_terminal_summary(terminalreporter):
    """Print the criterion-by-criterion result table.

    Deliberately terse and uncoloured. This block is the artifact a reviewer
    reads to answer "is every acceptance criterion evidenced by a passing test",
    and it may be read through a truncated tail of the run's output, so every
    line has to earn its width.
    """
    if not _acceptance_results:
        return

    reporter = terminalreporter
    total = len(_acceptance_results)
    passed = sum(1 for entry in _acceptance_results.values() if entry["outcome"] == "PASS")
    sources = sorted({entry["source"] for entry in _acceptance_results.values() if entry["source"]})

    reporter.write_sep("=", f"acceptance criteria ({passed}/{total} PASS)")
    story = None
    for ac_id in sorted(_acceptance_results, key=_sort_key):
        entry = _acceptance_results[ac_id]
        if (this_story := _story_of(ac_id)) != story:
            story = this_story
            reporter.write_line(f"Story {story}")
        reporter.write_line(f"  {ac_id} {entry['outcome']}  {entry['text']}")

    for source in sources:
        reporter.write_line(f"source: {source}")
    reporter.write_line(
        f"{passed}/{total} acceptance criteria evidenced by a passing test in this run"
        if passed == total
        else f"{total - passed} of {total} acceptance criteria NOT evidenced by a passing test"
    )


def _story_of(ac_id: str) -> str:
    """``"1.4b"`` -> ``"1.4"``; anything unparseable groups under itself."""
    match = _STORY_PREFIX.match(ac_id)
    return match.group(1) if match else ac_id


def _sort_key(ac_id: str) -> tuple:
    """Order ids numerically by story, then lexically by the suffix."""
    match = _STORY_PREFIX.match(ac_id)
    if match is None:
        return ((), ac_id)
    numbers = tuple(int(part) for part in match.group(1).split("."))
    return (numbers, ac_id[match.end() :])
