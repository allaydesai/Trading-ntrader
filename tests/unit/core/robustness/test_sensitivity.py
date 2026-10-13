"""The sensitivity map: plateau over spike (S3.2)."""

import pytest

from src.core.robustness.grid import expand
from src.core.robustness.sensitivity import neighbourhood, sensitivity_map

pytestmark = pytest.mark.unit

AXES = {"entry": {"values": [0.3, 0.35, 0.4, 0.45]}, "exit": {"values": [0.6, 0.65, 0.7]}}
CELLS = expand(AXES)


def _values(spike_at: dict, spike: float) -> dict[int, float | None]:
    values: dict[int, float | None] = {}
    for i, cell in enumerate(CELLS):
        plateau = 1.0 if cell["entry"] <= 0.4 else 0.4  # a broad plateau, a weak edge
        values[i] = spike if cell == spike_at else plateau
    return values


def test_recommends_the_plateau_centre_not_the_isolated_spike():
    spike_cell = {"entry": 0.45, "exit": 0.7}
    result = sensitivity_map(CELLS, _values(spike_cell, 1.6), list(AXES), metric="sharpe_ratio")
    assert result["raw_best"]["params"] == spike_cell
    assert result["recommended"]["params"]["entry"] <= 0.4
    assert result["gap"] == pytest.approx(1.6 - result["recommended"]["value"])
    spike = next(c for c in result["cells"] if c["params"] == spike_cell)
    assert spike["stability"] == pytest.approx(0.7 / 1.6)  # median of neighbours 1.0 and 0.4
    assert result["metric"] == "sharpe_ratio"


def test_missing_cells_are_listed_and_never_neighbours():
    values = _values({"entry": 0.45, "exit": 0.7}, 1.6)
    values[0] = None
    result = sensitivity_map(CELLS, values, list(AXES), metric="sharpe_ratio")
    assert result["missing"] == [0]
    cell_1 = next(c for c in result["cells"] if c["index"] == 1)
    assert 0 not in cell_1["neighbours"]


def test_neighbourhood_of_a_cell_gives_the_worst_and_median_neighbour_ratio():
    values = _values({"entry": 0.45, "exit": 0.7}, 1.6)
    centre = CELLS.index({"entry": 0.4, "exit": 0.65})
    result = neighbourhood(CELLS, values, list(AXES), centre)
    assert result["chosen"] == 1.0
    assert result["min_ratio"] == pytest.approx(0.4)  # the (0.45, 0.65) neighbour
    assert result["median_ratio"] == pytest.approx(1.0)
    assert len(result["neighbours"]) == 4


def test_a_non_positive_chosen_value_has_no_ratio():
    values = {i: -0.5 for i in range(len(CELLS))}
    result = neighbourhood(CELLS, values, list(AXES), 0)
    assert result["min_ratio"] is None and result["median_ratio"] is None


def test_all_missing_gives_no_recommendation():
    result = sensitivity_map(CELLS, {i: None for i in range(len(CELLS))}, list(AXES), metric="x")
    assert result["recommended"] is None and result["raw_best"] is None and result["gap"] is None
