"""Studies end to end against Postgres: holdout lock, ledger, budget, status (S1.1-S1.3, S6.4)."""

from datetime import date
from uuid import uuid4

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.studies import lifecycle
from src.mcp_server.studies.submit import prepare_in_sample, reserve_trial
from tests.integration.mcp_server.conftest import requires_postgres
from tests.integration.mcp_server.study_fixtures import make_ctx, open_study

pytestmark = [pytest.mark.integration, requires_postgres]

NO_ARGS = dict(
    strategy=None,
    symbol=None,
    start=None,
    end=None,
    timeframe=None,
    catalog=None,
    params={},
    starting_balance=None,
    over_budget_reason=None,
)


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    return make_ctx(tmp_path, monkeypatch)


def _submit(ctx, study="sma-aapl", **args):
    job, warnings = prepare_in_sample(study, **{**NO_ARGS, **args})
    recorded = reserve_trial(ctx.runner, job)
    return recorded, warnings


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


def test_create_study_locks_the_holdout_and_reads_gates(ctx):
    created = open_study(ctx)
    study = created["study"]
    assert study["status"] == "exploring"
    assert study["symbols"] == ["AAPL", "MSFT"]
    assert study["split"]["out_of_sample"] == {
        "start": "2020-01-01",
        "end": "2020-12-31",
        "locked": True,
    }
    assert study["pass_criteria"] == ["G0", "G1", "G2", "G3"]
    assert study["budget"] == {"used": 0, "budget": 3, "remaining": 3}
    assert [e["kind"] for e in study["events"]] == ["created"]
    assert created["warnings"] == []


def test_create_study_refusals(ctx):
    open_study(ctx)
    assert _code(open_study, ctx) == "study_exists"
    assert _code(open_study, ctx, slug="b", strategy="buy_and_hold") == "invalid_study"
    assert _code(open_study, ctx, slug="c", pass_criteria=["G9"]) == "unknown_gate"
    assert _code(open_study, ctx, slug="d", out_of_sample_start=date(2019, 6, 1)) == (
        "invalid_split"
    )
    bad_space = {"fast_period": {"values": [-1]}}
    assert _code(open_study, ctx, slug="e", param_space=bad_space) == "invalid_param_space"


def test_without_readable_gates_the_study_warns_and_uses_fallback_ids(
    tmp_path, monkeypatch, isolated_results
):
    ctx = make_ctx(tmp_path, monkeypatch, gates=None)
    created = open_study(ctx)
    assert created["study"]["pass_criteria"] == ["G0", "G1", "G2", "G3", "G4"]
    assert "Gates not read from the vault" in created["warnings"][0]


def test_an_in_sample_trial_is_recorded_before_it_runs(ctx):
    open_study(ctx)
    recorded, warnings = _submit(ctx, params={"fast_period": 7})
    assert recorded["trial"] == 1 and recorded["role"] == "in_sample"
    assert recorded["budget"] == {"used": 1, "budget": 3, "remaining": 2}
    assert ctx.runner.store.status(recorded["job_id"])["state"] == "queued"
    assert any("outside the study's declared space" in w for w in warnings)
    study = lifecycle.get_study(ctx.runner.store, "sma-aapl")["study"]
    row = study["ledger"][0]
    assert row["state"] == "pending" and row["counted"] is True
    assert (row["start"], row["end"]) == ("2018-01-01", "2019-12-31")
    assert row["params"]["fast_period"] == 7


def test_the_holdout_and_the_study_definition_are_enforced(ctx):
    open_study(ctx)
    assert _code(_submit, ctx, end=date(2020, 3, 1)) == "holdout_locked"
    assert _code(_submit, ctx, strategy="momentum") == "study_mismatch"
    assert _code(_submit, ctx, symbol="SPY") == "study_mismatch"
    assert _code(_submit, ctx, timeframe="1-HOUR") == "study_mismatch"
    assert _code(_submit, ctx, study="nope") == "unknown_study"
    assert _submit(ctx, symbol="msft", timeframe="1-day")[0]["trial"] == 1


def test_the_budget_needs_a_reason_to_go_over(ctx):
    open_study(ctx)
    for _ in range(3):
        _submit(ctx)
    assert _code(_submit, ctx) == "over_budget"
    recorded, _ = _submit(ctx, over_budget_reason="one more on a hunch")
    assert recorded["budget"] == {"used": 4, "budget": 3, "remaining": -1}
    study = lifecycle.get_study(ctx.runner.store, "sma-aapl")["study"]
    assert study["ledger"][-1]["override_reason"] == "one more on a hunch"
    assert study["events"][-1]["kind"] == "over_budget"


def test_settling_voids_failed_jobs_and_links_saved_runs(ctx):
    open_study(ctx, trial_budget=2)
    store = ctx.runner.store
    first, _ = _submit(ctx)
    second, _ = _submit(ctx)
    run_id = str(uuid4())
    store.update(first["job_id"], state="succeeded", run_id=run_id)
    store.update(second["job_id"], state="failed")
    study = lifecycle.get_study(store, "sma-aapl")["study"]
    assert [r["state"] for r in study["ledger"]] == ["completed", "void"]
    assert study["ledger"][0]["run_id"] == run_id
    assert study["budget"]["used"] == 1
    assert _submit(ctx)[0]["trial"] == 3  # the voided trial freed its slot


def test_a_job_that_cannot_be_recorded_is_cancelled(ctx, monkeypatch):
    open_study(ctx)
    from src.db.repositories.research_repository import SyncResearchRepository

    def boom(self, trial):
        raise RuntimeError("db down")

    monkeypatch.setattr(SyncResearchRepository, "add_trial", boom)
    with pytest.raises(RuntimeError):
        _submit(ctx)
    (job,) = ctx.runner.store.list()
    assert job["state"] == "cancelled"


def test_status_changes_need_a_reason_and_a_valid_transition(ctx):
    open_study(ctx)
    store = ctx.runner.store
    update = lifecycle.update_study
    assert _code(update, store, "sma-aapl", reason=" ", status="parked") == "reason_required"
    assert _code(update, store, "sma-aapl", reason="x") == "nothing_to_change"
    assert _code(update, store, "sma-aapl", reason="x", status="promoted") == "invalid_transition"
    assert _code(update, store, "sma-aapl", reason="x", status="frozen") == "invalid_status"
    parked = update(store, "sma-aapl", reason="waiting on data", status="parked")
    assert parked["study"]["status"] == "parked"
    assert _code(_submit, ctx) == "invalid_study_state"
    resumed = update(store, "sma-aapl", reason="data arrived", status="active")
    assert resumed["study"]["status"] == "exploring"
    extended = update(store, "sma-aapl", reason="worth a wider grid", extend_budget_by=10)
    assert extended["study"]["budget"]["budget"] == 13
    kinds = [e["kind"] for e in extended["study"]["events"]]
    assert kinds == ["created", "status_changed", "status_changed", "budget_extended"]


def test_rejected_studies_stay_searchable(ctx):
    open_study(ctx)
    open_study(ctx, slug="sma-msft", symbols=["MSFT"], tags=["other"])
    lifecycle.update_study(ctx.runner.store, "sma-aapl", reason="no edge", status="rejected")
    found = lifecycle.list_studies(ctx.runner.store, status="rejected")["studies"]
    assert [s["slug"] for s in found] == ["sma-aapl"]
    assert found[0]["status_reason"] == "no edge"
    by_symbol = lifecycle.list_studies(ctx.runner.store, symbol="msft")["studies"]
    assert {s["slug"] for s in by_symbol} == {"sma-aapl", "sma-msft"}
    assert [
        s["slug"] for s in lifecycle.list_studies(ctx.runner.store, tag="other")["studies"]
    ] == ["sma-msft"]
