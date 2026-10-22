"""The data split: the holdout is locked and must come after the in-sample window (S1.2)."""

from datetime import date

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies.split import (
    Split,
    check_split,
    clamp_to_in_sample,
    require_in_sample,
    require_out_of_sample,
)

pytestmark = pytest.mark.unit

SPLIT = Split(date(2000, 1, 1), date(2015, 12, 31), date(2016, 1, 1), date(2025, 12, 31))


def _code(fn, *args) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args)
    return exc.value.code


def test_a_holdout_after_the_in_sample_window_is_accepted():
    check_split(SPLIT)


@pytest.mark.parametrize(
    "split",
    [
        Split(date(2000, 1, 1), date(2015, 12, 31), date(2015, 12, 31), date(2025, 12, 31)),
        Split(date(2016, 1, 1), date(2025, 12, 31), date(2000, 1, 1), date(2015, 12, 31)),
        Split(date(2015, 1, 1), date(2000, 1, 1), date(2016, 1, 1), date(2025, 12, 31)),
    ],
    ids=["overlap", "holdout-first", "reversed"],
)
def test_bad_splits_are_refused(split):
    assert _code(check_split, split) == "invalid_split"


def test_an_in_sample_window_passes_and_one_day_further_is_locked():
    require_in_sample(SPLIT, date(2000, 1, 1), date(2015, 12, 31))
    require_in_sample(SPLIT, date(1995, 1, 1), date(2010, 1, 1))  # before IS is not locked
    assert _code(require_in_sample, SPLIT, date(2010, 1, 1), date(2016, 1, 1)) == "holdout_locked"


def test_out_of_sample_runs_use_exactly_the_locked_window():
    require_out_of_sample(SPLIT, date(2016, 1, 1), date(2025, 12, 31))
    assert _code(require_out_of_sample, SPLIT, date(2017, 1, 1), date(2025, 12, 31)) == (
        "invalid_window"
    )


def test_clamping_cuts_the_holdout_and_says_so():
    start, end, notes = clamp_to_in_sample(SPLIT, date(2010, 1, 1), date(2020, 1, 1))
    assert (start, end) == (date(2010, 1, 1), date(2015, 12, 31))
    assert "out-of-sample window is locked" in notes[0]


def test_clamping_defaults_to_the_whole_in_sample_window():
    assert clamp_to_in_sample(SPLIT, None, None) == (date(2000, 1, 1), date(2015, 12, 31), [])


def test_a_window_wholly_in_the_holdout_is_refused():
    assert _code(clamp_to_in_sample, SPLIT, date(2018, 1, 1), date(2019, 1, 1)) == (
        "holdout_locked"
    )
