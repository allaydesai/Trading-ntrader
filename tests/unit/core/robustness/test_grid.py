"""Parameter grids: expansion, neighbourhoods and adjacency (S3.1, S3.2)."""

import pytest

from src.core.robustness import grid
from src.core.robustness.grid import adjacent, around, axis_values, expand, size

pytestmark = pytest.mark.unit


def test_axis_values_from_a_list_or_an_inclusive_range():
    assert axis_values({"values": [3, 2, 2]}) == [3, 2, 2]
    assert axis_values({"min": 2, "max": 4}) == [2, 3, 4]
    assert axis_values({"min": 0.25, "max": 0.45, "step": 0.1}) == [0.25, 0.35, 0.45]


def test_integral_ranges_give_ints_so_stored_params_match_hand_runs():
    values = axis_values({"min": 5.0, "max": 15.0, "step": 5.0})
    assert values == [5, 10, 15]
    assert all(isinstance(v, int) for v in values)


def test_size_is_the_product_of_axis_lengths_without_expanding():
    axes = {"a": {"values": [1, 2, 3]}, "b": {"min": 1, "max": 1000}}
    assert size(axes) == 3000


@pytest.mark.parametrize(
    "axis",
    [
        {"min": 0.1, "max": 0.3, "step": 0.1},
        {"min": 5, "max": 60, "step": 7},
        {"min": 1, "max": 1},
        {"min": 0.05, "max": 0.15, "step": 0.025},
        {"values": [3, 1, 2]},
    ],
)
def test_size_counts_exactly_the_values_an_axis_expands_to(axis):
    assert size({"a": axis}) == len(axis_values(axis))


def test_size_of_an_over_fine_range_is_counted_not_built(monkeypatch):
    monkeypatch.setattr(grid, "axis_values", lambda axis: pytest.fail("expanded"))
    assert size({"period": {"min": 1, "max": 10_000_000, "step": 1}}) == 10_000_000


def test_expand_walks_axes_in_declaration_order():
    cells = expand({"a": {"values": [1, 2]}, "b": {"values": ["x", "y"]}})
    assert cells == [
        {"a": 1, "b": "x"},
        {"a": 1, "b": "y"},
        {"a": 2, "b": "x"},
        {"a": 2, "b": "y"},
    ]


def test_around_probes_each_declared_axis_at_minus_and_plus_pct():
    axes = around({"window": 10, "entry": 0.3, "name": "x"}, ["window", "entry"], pct=0.2)
    assert axes == {"window": {"values": [8, 10, 12]}, "entry": {"values": [0.24, 0.3, 0.36]}}


def test_around_dedupes_rounded_int_neighbours():
    assert around({"window": 2}, ["window"], pct=0.2) == {"window": {"values": [2]}}


def test_adjacent_cells_differ_by_one_step_on_one_axis():
    cells = expand({"a": {"values": [1, 2, 3]}, "b": {"values": [10, 20]}})
    neighbours = adjacent(cells, ["a", "b"])
    # (a=2, b=10) is index 2: beside (1,10), (3,10) and (2,20); never the diagonal.
    assert sorted(neighbours[2]) == [0, 3, 4]
    assert sorted(neighbours[0]) == [1, 2]
