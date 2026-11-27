"""The existing regime tools say their volatility terciles are hindsight (tasks.md 2.10).

``get_regime_breakdown`` and ``get_sleeve_correlation`` keep their inputs and cells; only
their ``definitions`` text gains the sentence. The cells stay pinned by the existing e2e
tests (``tests/e2e/mcp_server/test_stdio_study_flow.py``, ``test_stdio_portfolio_flow.py``).
"""

import pytest

from src.mcp_server.analysis.correlation import DEFINITIONS as SLEEVE_DEFINITIONS
from src.mcp_server.analysis.run_reads import BREAKDOWN_DEFINITIONS

pytestmark = pytest.mark.component


@pytest.mark.parametrize(
    ("definitions", "days"),
    [(BREAKDOWN_DEFINITIONS, "run's own days"), (SLEEVE_DEFINITIONS, "shared days")],
)
def test_tercile_cut_is_named_as_hindsight(definitions, days):
    text = definitions["volatility"]
    assert days in text
    assert "partial hindsight view" in text
    assert "run_regime_attribution" in text


def test_breakdown_definitions_keep_their_other_entries():
    assert set(BREAKDOWN_DEFINITIONS) == {"trend", "volatility", "return", "trades"}
