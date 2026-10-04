"""Research study tables against real Postgres (throwaway schema)."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models.research import (
    ImmutableRecordError,
    ResearchCandidate,
    ResearchStudy,
    ResearchTrial,
)
from src.db.repositories.research_repository import SyncResearchRepository
from tests.integration.mcp_server.conftest import requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


def _study(slug: str = "rsi2-qqq", **overrides) -> ResearchStudy:
    fields = dict(
        slug=slug,
        title="Cumulative RSI on QQQ",
        hypothesis="Washouts revert",
        strategy_type="sma_crossover",
        symbols=["QQQ"],
        timeframe="1-DAY",
        catalog="e2e-test",
        starting_balance=Decimal("1000000"),
        param_space={"fast_period": {"values": [5, 10]}},
        is_start=date(2000, 1, 1),
        is_end=date(2015, 12, 31),
        oos_start=date(2016, 1, 1),
        oos_end=date(2025, 12, 31),
        pass_criteria=["G0", "G1"],
        trial_budget=40,
        tags=["mean-reversion"],
    )
    return ResearchStudy(**{**fields, **overrides})


def _trial(study: ResearchStudy, job_id: str = "job-1", **overrides) -> ResearchTrial:
    fields = dict(
        study_pk=study.id,
        version=1,
        role="in_sample",
        job_id=job_id,
        counted=True,
        strategy_type="sma_crossover",
        symbol="QQQ",
        params={},
        config_hash="h" * 64,
        start=date(2000, 1, 1),
        end=date(2015, 12, 31),
    )
    return ResearchTrial(**{**fields, **overrides})


def _candidate(study: ResearchStudy) -> ResearchCandidate:
    return ResearchCandidate(
        study_pk=study.id,
        version=1,
        source_run_id=uuid4(),
        strategy_type="sma_crossover",
        symbol="QQQ",
        timeframe="1-DAY",
        catalog="e2e-test",
        params={"fast_period": 10},
        starting_balance=Decimal("1000000"),
        fill_seed=42,
        candidate_hash="c" * 64,
    )


def test_study_found_by_slug_or_id_with_defaults(isolated_results):
    with isolated_results() as session:
        repo = SyncResearchRepository(session)
        study = repo.add_study(_study())
        assert study.status == "exploring" and study.current_version == 1
        assert not study.contaminated
        assert repo.find_study("rsi2-qqq").id == study.id
        assert repo.find_study(str(study.study_id), for_update=True).id == study.id
        assert repo.find_study("nope") is None


def test_holdout_must_follow_the_in_sample_window(isolated_results):
    with pytest.raises(IntegrityError, match="chk_study_holdout_after"):
        with isolated_results() as session:
            SyncResearchRepository(session).add_study(_study(oos_start=date(2015, 6, 1)))


def test_search_filters_combine(isolated_results):
    with isolated_results() as session:
        repo = SyncResearchRepository(session)
        repo.add_study(_study("a-qqq"))
        repo.add_study(_study("b-spy", symbols=["SPY"], tags=["trend"], status="parked"))
        assert [s.slug for s in repo.search_studies(symbol="qqq")] == ["a-qqq"]
        assert [s.slug for s in repo.search_studies(tag="trend")] == ["b-spy"]
        assert [s.slug for s in repo.search_studies(status="parked")] == ["b-spy"]
        assert [s.slug for s in repo.search_studies(text="cumulative")] == ["b-spy", "a-qqq"]


def test_trials_settle_once(isolated_results):
    with isolated_results() as session:
        repo = SyncResearchRepository(session)
        study = repo.add_study(_study())
        trial = repo.add_trial(_trial(study))
        run_id = uuid4()
        repo.settle_trial(trial, state="completed", run_id=run_id)
        assert repo.trials_of_runs([run_id])[0].job_id == "job-1"
        with pytest.raises(ValueError, match="already completed"):
            repo.settle_trial(trial, state="void", run_id=None)


@pytest.mark.parametrize("which", ["candidate", "event"])
def test_candidates_and_events_are_immutable(isolated_results, which):
    with isolated_results.begin() as session:  # committed, so a second session sees the rows
        repo = SyncResearchRepository(session)
        study = repo.add_study(_study())
        candidate = repo.add_candidate(_candidate(study))
        event = repo.add_event(study, "frozen", "plateau centre", {"version": 1})
    with pytest.raises(ImmutableRecordError):
        with isolated_results() as session:
            if which == "candidate":
                row = session.get(ResearchCandidate, candidate.id)
                row.params = {"fast_period": 99}
            else:
                row = session.get(type(event), event.id)
                row.reason = "rewritten"
            session.flush()


def test_one_candidate_per_version(isolated_results):
    with pytest.raises(IntegrityError, match="uq_candidate_version"):
        with isolated_results() as session:
            repo = SyncResearchRepository(session)
            study = repo.add_study(_study())
            repo.add_candidate(_candidate(study))
            repo.add_candidate(_candidate(study))
