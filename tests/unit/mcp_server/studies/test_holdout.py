"""Which trials saw the locked out-of-sample window (S5.2, S6.3)."""

from datetime import date
from types import SimpleNamespace

import pytest

from src.mcp_server.studies.candidates import sees_holdout

pytestmark = pytest.mark.unit

STUDY = SimpleNamespace(is_end=date(2019, 12, 31))


def _trial(role: str, end: date) -> SimpleNamespace:
    return SimpleNamespace(role=role, end=end)


@pytest.mark.parametrize(
    ("role", "end", "seen"),
    [
        ("out_of_sample", date(2020, 12, 31), True),
        ("walk_forward", date(2020, 12, 31), True),
        ("breadth", date(2020, 12, 31), True),
        ("breadth", date(2019, 12, 31), False),
        ("in_sample", date(2019, 12, 31), False),
        ("cost_stress", date(2019, 12, 31), False),
        # Buy-and-hold on the holdout runs another strategy: it reveals nothing of this one.
        ("benchmark", date(2020, 12, 31), False),
    ],
)
def test_a_trial_of_the_study_strategy_past_the_in_sample_end_saw_the_holdout(role, end, seen):
    assert sees_holdout(STUDY, _trial(role, end)) is seen
