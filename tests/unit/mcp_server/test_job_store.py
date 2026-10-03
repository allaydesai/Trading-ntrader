"""File-backed job store: one directory per job (S2.1, S2.2)."""

import json

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.jobs.store import JobStore

pytestmark = pytest.mark.unit

PAYLOAD = {"kind": "backtest", "spec": {"strategy": "sma_crossover"}, "resolved": {"x": 1}}


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs")


def test_create_writes_request_and_queued_status(store):
    job_id = store.create(PAYLOAD)
    job_dir = store.job_dir(job_id)
    assert json.loads((job_dir / "request.json").read_text()) == PAYLOAD
    status = store.status(job_id)
    assert status["state"] == "queued"
    assert status["job_id"] == job_id
    assert status["kind"] == "backtest"


def test_ids_are_unique_and_sort_by_creation(store):
    ids = [store.create(PAYLOAD) for _ in range(5)]
    assert len(set(ids)) == 5
    assert [s["job_id"] for s in store.list(limit=10)] == list(reversed(ids))


def test_update_merges_fields(store):
    job_id = store.create(PAYLOAD)
    store.update(job_id, state="running", pid=123)
    status = store.status(job_id)
    assert status["state"] == "running"
    assert status["pid"] == 123
    assert status["kind"] == "backtest"


def test_list_filters_by_state_and_limits(store):
    a, b, c = (store.create(PAYLOAD) for _ in range(3))
    store.update(b, state="succeeded")
    assert [s["job_id"] for s in store.list(state="queued")] == [c, a]
    assert len(store.list(limit=1)) == 1


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "..", "nope"])
def test_unknown_or_malformed_ids_are_refused(store, bad):
    with pytest.raises(ToolFailure) as exc:
        store.status(bad)
    assert exc.value.code == "unknown_job"


def test_log_tail(store):
    job_id = store.create(PAYLOAD)
    store.log_path(job_id).write_text("\n".join(f"line {i}" for i in range(10)) + "\n")
    assert store.log_tail(job_id, 3) == ["line 7", "line 8", "line 9"]


def test_log_tail_without_log(store):
    assert store.log_tail(store.create(PAYLOAD), 5) == []


def test_progress_and_result_are_optional(store):
    job_id = store.create(PAYLOAD)
    assert store.progress(job_id) is None
    assert store.result(job_id) is None
    store.write_json(job_id, "progress.json", {"phase": "loading_data"})
    assert store.progress(job_id) == {"phase": "loading_data"}


def test_request_round_trips(store):
    assert store.request(store.create(PAYLOAD)) == PAYLOAD
