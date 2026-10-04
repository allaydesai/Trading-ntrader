"""The trial budget counts in-sample trials, queued or done, but not voided ones (S1.3)."""

from types import SimpleNamespace

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies.ledger import budget, numbering, require_budget, used

pytestmark = pytest.mark.unit

STUDY = SimpleNamespace(slug="s", trial_budget=3)


def _trial(counted=True, state="completed"):
    return SimpleNamespace(counted=counted, state=state)


def test_pending_and_completed_count_void_and_uncounted_do_not():
    trials = [_trial(state="pending"), _trial(), _trial(state="void"), _trial(counted=False)]
    assert used(trials) == 2
    assert budget(STUDY, trials) == {"used": 2, "budget": 3, "remaining": 1}


def test_the_last_trial_fits_and_the_next_needs_a_reason():
    require_budget(STUDY, [_trial(), _trial()], 1, None)
    with pytest.raises(ToolFailure) as exc:
        require_budget(STUDY, [_trial()] * 3, 1, None)
    assert exc.value.code == "over_budget"
    assert "over_budget_reason" in exc.value.fix


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_reason_is_no_reason(reason):
    with pytest.raises(ToolFailure):
        require_budget(STUDY, [_trial()] * 3, 1, reason)


def test_a_reason_allows_going_over():
    require_budget(STUDY, [_trial()] * 3, 1, "one more on a hunch, recorded")


def test_every_row_has_an_entry_and_only_counted_roles_have_a_trial_number():
    roles = ["in_sample", "benchmark", "benchmark", "in_sample", "out_of_sample", "in_sample"]
    trials = [SimpleNamespace(role=r, counted=r == "in_sample", state="completed") for r in roles]
    trials[3].state = "void"  # a voided trial keeps its number, so later numbers never shift
    assert numbering(trials) == [(1, 1), (2, None), (3, None), (4, 2), (5, None), (6, 3)]
