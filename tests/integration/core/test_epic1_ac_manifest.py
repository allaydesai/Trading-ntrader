"""The acceptance suite's own guard: manifest and tests stay in bijection.

Without this, the traceability the acceptance summary reports is only as good as
whoever last edited it. A criterion whose test is deleted would simply stop
appearing in the table — silently turning "40/40 PASS" into "39/39 PASS", which
reads exactly as green.

So: every id in :data:`epic1_criteria.CRITERIA` must be claimed by exactly one
test, and no test may claim an id the manifest does not define (``criterion()``
already refuses that at import time; this pins the other direction).
"""

import pytest

from tests.integration.core import (
    epic1_criteria,
    test_epic1_ac_cli,
    test_epic1_ac_data,
    test_epic1_ac_gate,
    test_epic1_ac_node,
    test_epic1_ac_safety,
)

pytestmark = pytest.mark.integration

#: Every module allowed to carry Epic-1 acceptance criteria. Listed rather than
#: globbed so a new acceptance module has to be admitted deliberately.
ACCEPTANCE_MODULES = (
    test_epic1_ac_gate,
    test_epic1_ac_node,
    test_epic1_ac_safety,
    test_epic1_ac_data,
    test_epic1_ac_cli,
)


def _claimed_ids() -> list[str]:
    """Every criterion id claimed by a test, with duplicates preserved."""
    claimed: list[str] = []
    for module in ACCEPTANCE_MODULES:
        for member in vars(module).values():
            candidates = [member]
            if isinstance(member, type):
                candidates.extend(vars(member).values())
            for candidate in candidates:
                marks = getattr(candidate, "pytestmark", None)
                if not isinstance(marks, list):
                    continue
                claimed.extend(
                    mark.args[0]
                    for mark in marks
                    if getattr(mark, "name", None) == "acceptance_criterion"
                )
    return claimed


def test_every_acceptance_criterion_is_claimed_by_exactly_one_test():
    claimed = _claimed_ids()
    declared = set(epic1_criteria.CRITERIA)

    unevidenced = sorted(declared - set(claimed))
    assert not unevidenced, (
        f"these acceptance criteria have no test: {unevidenced}. Either write one or "
        "remove the criterion from the manifest — an unclaimed criterion vanishes from "
        "the summary rather than failing it."
    )

    duplicated = sorted({ac_id for ac_id in claimed if claimed.count(ac_id) > 1})
    assert not duplicated, (
        f"these criteria are claimed by more than one test: {duplicated}. The summary "
        "reports one row per id, so a second claim silently hides behind the first."
    )


def test_the_manifest_covers_every_epic_one_story():
    """Seven stories, and the flattened view agrees with the grouped one."""
    stories = [story for story, _criteria in epic1_criteria.STORIES]

    assert stories == ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"]
    assert len(epic1_criteria.CRITERIA) == sum(
        len(criteria) for _story, criteria in epic1_criteria.STORIES
    ), "two stories declare the same criterion id, so one of them was flattened away"

    for story, criteria in epic1_criteria.STORIES:
        assert criteria, f"story {story} declares no acceptance criteria"
        for ac_id, label in criteria:
            assert ac_id.startswith(story), f"{ac_id} is filed under story {story}"
            assert label and len(label) <= 56, (
                f"{ac_id}'s label is unusable in the summary table: {label!r}"
            )
