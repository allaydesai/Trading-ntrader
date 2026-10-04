"""Research MCP phase 2 (S5.1): a frozen candidate cannot drift, by absence of any write path.

The repository's public surface is pinned as an exact set (the
``EXPECTED_CAPABILITIES`` precedent): a method that could update or delete a
candidate or an event, of any name, turns this red. Changing the set is a
deliberate edit here and in the repository together.
"""

import pytest

from src.db.models.research import ResearchCandidate, ResearchStudyEvent
from src.db.repositories.research_repository import SyncResearchRepository

pytestmark = pytest.mark.unit

EXPECTED_CAPABILITIES = frozenset(
    {
        "add_study",
        "find_study",
        "search_studies",
        "add_event",
        "events",
        "add_trial",
        "trials",
        "pending_trials",
        "settle_trial",
        "trials_of_runs",
        "add_candidate",
        "candidates",
    }
)

MUTATOR_WORDS = ("update", "delete", "remove", "set_", "save", "patch", "merge", "replace")


def _public_names() -> frozenset[str]:
    return frozenset(
        name
        for name in dir(SyncResearchRepository)
        if not name.startswith("_") and name != "session"
    )


def test_repository_exposes_exactly_the_expected_capabilities():
    assert _public_names() == EXPECTED_CAPABILITIES


def test_no_capability_names_a_generic_mutation():
    assert [n for n in _public_names() if any(w in n for w in MUTATOR_WORDS)] == []


@pytest.mark.parametrize("model", [ResearchCandidate, ResearchStudyEvent])
def test_immutable_models_refuse_an_orm_update(model):
    from sqlalchemy import event

    from src.db.models.research import _refuse_update

    assert event.contains(model, "before_update", _refuse_update)
