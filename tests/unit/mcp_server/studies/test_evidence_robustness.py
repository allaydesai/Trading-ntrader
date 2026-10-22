"""Robustness evidence read from a study's stored analyses."""

from types import SimpleNamespace

import pytest

from src.mcp_server.studies.evidence_robustness import walk_forward_facts

pytestmark = pytest.mark.unit


class _Repo:
    def __init__(self, analyses):
        self._analyses = analyses

    def analyses(self, study, kind=None):
        return [a for a in self._analyses if kind is None or a.kind == kind]


def test_walk_forward_facts_carry_the_cells_that_did_not_finish():
    analysis = SimpleNamespace(
        kind="walk_forward",
        status="completed",
        candidate_pk=1,
        analysis_id="w1",
        result={
            "efficiency": 0.9,
            "windows": [{}, {}],
            "windows_used": 2,
            "negative_windows": 0,
            "excluded_windows": [],
            "runs": {"j1": "r1", "j2": "r2"},
            "missing_cells": ["j3", "j4", "j5"],
        },
    )
    facts = walk_forward_facts(_Repo([analysis]), None, SimpleNamespace(id=1))
    assert facts is not None and facts.missing_cells == 3
