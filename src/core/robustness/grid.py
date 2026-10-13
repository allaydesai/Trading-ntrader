"""Parameter grids: the cells a sweep runs and which of them are neighbours (S3.1, S3.2).

An axis is ``{"values": [...]}`` or an inclusive ``{"min", "max", "step"}``
(step defaults to 1). Integral ranges give ints, so a cell's stored parameters
equal a hand-run's. Cells are expanded in axis declaration order, and two cells
are adjacent when they differ on exactly one axis by one step of that axis.
"""

from decimal import Decimal
from itertools import product
from math import prod
from typing import Any

Axis = dict[str, Any]


def _number(value: float) -> int | float:
    """An integral float as an int, so ``5.0`` and ``5`` are the same parameter."""
    return int(value) if float(value).is_integer() else float(value)


def axis_values(axis: Axis) -> list[Any]:
    """Every value of one axis, in order."""
    if axis.get("values") is not None:
        return list(axis["values"])
    low, high = Decimal(str(axis["min"])), Decimal(str(axis["max"]))
    step = Decimal(str(axis.get("step") or 1))
    values, current = [], low
    while current <= high:
        values.append(_number(float(current)))
        current += step
    return values


def size(axes: dict[str, Axis]) -> int:
    """How many cells the grid has, without expanding it."""
    return prod(len(axis_values(axis)) for axis in axes.values()) if axes else 0


def expand(axes: dict[str, Axis]) -> list[dict[str, Any]]:
    """Every cell of the grid, in axis declaration order."""
    names = list(axes)
    return [dict(zip(names, combo)) for combo in product(*(axis_values(axes[n]) for n in names))]


def around(params: dict[str, Any], axes: list[str], *, pct: float) -> dict[str, Axis]:
    """Axes probing each named parameter at ``-pct``, itself and ``+pct``.

    Ints stay ints (rounded) and duplicates are dropped, so a small window is
    probed at the neighbours that actually differ.
    """
    grid: dict[str, Axis] = {}
    for name in axes:
        centre = params[name]
        if isinstance(centre, bool) or not isinstance(centre, (int, float)):
            continue
        probes = [centre * (1 - pct), centre, centre * (1 + pct)]
        values: list[int | float]
        if isinstance(centre, int):
            values = list(dict.fromkeys(int(round(p)) for p in probes))
        else:
            values = list(dict.fromkeys(_number(round(p, 10)) for p in probes))
        grid[name] = {"values": values}
    return grid


def adjacent(cells: list[dict[str, Any]], axes: list[str]) -> dict[int, list[int]]:
    """Neighbour indexes per cell: one step along one axis, never a diagonal."""
    positions = {
        name: {v: i for i, v in enumerate(sorted({c[name] for c in cells}, key=_sort_key))}
        for name in axes
    }
    coords = [tuple(positions[name][cell[name]] for name in axes) for cell in cells]
    by_coord = {coord: i for i, coord in enumerate(coords)}
    neighbours: dict[int, list[int]] = {i: [] for i in range(len(cells))}
    for i, coord in enumerate(coords):
        for axis in range(len(axes)):
            for delta in (-1, 1):
                other = list(coord)
                other[axis] += delta
                j = by_coord.get(tuple(other))
                if j is not None:
                    neighbours[i].append(j)
    return neighbours


def _sort_key(value: Any) -> tuple[int, Any]:
    return (0, float(value)) if isinstance(value, (int, float)) else (1, str(value))
