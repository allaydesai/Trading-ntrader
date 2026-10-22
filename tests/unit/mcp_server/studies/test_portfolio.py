"""Which run is the candidate sleeve, who may peek at the holdout, and how weights split (S9.3)."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import portfolio

pytestmark = pytest.mark.unit


def _trial(role: str, *, version: int = 1, end: date = date(2019, 12, 31), run_id=None):
    return SimpleNamespace(
        role=role,
        version=version,
        end=end,
        state="completed",
        run_id=run_id or uuid4(),
        study_pk=7,
        candidate_pk=None,
    )


def _study(status: str = "exploring", version: int = 1):
    return SimpleNamespace(
        id=7, slug="s", status=status, current_version=version, is_end=date(2019, 12, 31)
    )


class _Repo:
    def __init__(self, candidates):
        self._candidates = candidates
        self.session = object()

    def candidates(self, study):
        return self._candidates


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


def test_the_candidate_run_is_the_newest_oos_then_the_source_then_the_newest_in_sample(
    monkeypatch,
):
    first, second = _trial("in_sample"), _trial("in_sample")
    oos = _trial("out_of_sample", end=date(2020, 12, 31))
    candidate = SimpleNamespace(id=3, version=1, source_run_id=first.run_id)
    trials = [first, second, oos]
    monkeypatch.setattr(portfolio, "oos_runs", lambda session, pk: [oos.run_id])
    assert portfolio._candidate_run(_Repo([candidate]), _study(), trials, None) == (
        oos,
        "newest_out_of_sample",
    )
    monkeypatch.setattr(portfolio, "oos_runs", lambda session, pk: [])
    assert portfolio._candidate_run(_Repo([candidate]), _study(), trials, None) == (
        first,
        "candidate_source",
    )
    assert portfolio._candidate_run(_Repo([]), _study(), trials, None) == (
        second,
        "newest_in_sample",
    )
    given = str(second.run_id).upper()
    assert portfolio._candidate_run(_Repo([]), _study(), trials, given) == (second, "given")
    assert _code(portfolio._candidate_run, _Repo([]), _study(), trials, str(uuid4())) == (
        "not_a_trial"
    )
    assert _code(portfolio._candidate_run, _Repo([]), _study(version=2), trials, None) == "no_run"


def test_holdout_runs_of_a_study_still_exploring_are_refused():
    peek = _trial("out_of_sample", end=date(2020, 12, 31))
    clean = _trial("in_sample")
    other = SimpleNamespace(**{**vars(peek), "study_pk": 99})  # another study's holdout
    assert _code(portfolio._check_holdout, _study("exploring"), [clean, peek]) == "holdout_peek"
    portfolio._check_holdout(_study("exploring"), [clean, other])
    for status in ("frozen", "tested", "promoted"):
        portfolio._check_holdout(_study(status), [peek])


def test_the_candidate_weight_scales_the_held_weights_down():
    held = [portfolio.SleeveSpec(run_id="a", weight=1), portfolio.SleeveSpec(run_id="b", weight=3)]
    without, with_ = portfolio._weights(held, "c", None)
    assert without == {"a": 0.25, "b": 0.75}
    assert with_ == pytest.approx({"a": 0.25 * 2 / 3, "b": 0.75 * 2 / 3, "c": 1 / 3})
    _, heavy = portfolio._weights(held, "c", 0.5)
    assert heavy == pytest.approx({"a": 0.125, "b": 0.375, "c": 0.5})
    for bad in (0.0, 1.0, 1.5):
        assert _code(portfolio._weights, held, "c", bad) == "invalid_weights"


def test_sleeves_are_named_with_positive_weights():
    assert _code(portfolio.parse_sleeves, []) == "too_few_sleeves"
    assert _code(portfolio.parse_sleeves, [{"run_id": "a", "weight": 0}]) == "invalid_weights"
    assert _code(portfolio.parse_sleeves, [{"run_id": "a", "weigth": 1}]) == "invalid_weights"
    (spec,) = portfolio.parse_sleeves([{"run_id": "a"}])
    assert spec.weight == 1.0
