"""Parameter sensitivity: a plateau score per cell and the cell to pick (S3.2).

A cell's neighbours are the grid cells one step away on one axis. Its
stability is the median neighbour result over its own; its plateau value is
the median of itself and its neighbours. The recommended cell is the best
plateau value (the centre of the broadest good region), the raw best is the
best single result, and the gap between them says how much a spike is worth.
"""

from statistics import median
from typing import Any

from src.core.robustness.grid import adjacent

Values = dict[int, float | None]


def _ratio(numerator: float | None, chosen: float | None) -> float | None:
    if numerator is None or chosen is None or chosen <= 0:
        return None
    return round(numerator / chosen, 6)


def neighbourhood(
    cells: list[dict[str, Any]], values: Values, axes: list[str], index: int
) -> dict[str, Any]:
    """One cell against its present neighbours: the worst and the median neighbour ratio."""
    chosen = values.get(index)
    neighbours = {j: values[j] for j in adjacent(cells, axes)[index] if values.get(j) is not None}
    present = [v for v in neighbours.values() if v is not None]
    return {
        "index": index,
        "params": cells[index],
        "chosen": chosen,
        "neighbours": neighbours,
        "min_ratio": _ratio(min(present), chosen) if present else None,
        "median_ratio": _ratio(median(present), chosen) if present else None,
    }


def _cell_rows(cells: list[dict[str, Any]], values: Values, axes: list[str]) -> list[dict]:
    neighbours = adjacent(cells, axes)
    rows = []
    for i, cell in enumerate(cells):
        value = values.get(i)
        if value is None:
            continue
        near = [v for j in neighbours[i] if (v := values.get(j)) is not None]
        rows.append(
            {
                "index": i,
                "params": cell,
                "value": value,
                "neighbours": [j for j in neighbours[i] if values.get(j) is not None],
                "stability": _ratio(median(near), value) if near else None,
                "plateau": round(median([value, *near]), 6),
            }
        )
    return rows


def sensitivity_map(
    cells: list[dict[str, Any]], values: Values, axes: list[str], *, metric: str
) -> dict[str, Any]:
    """The grid with a stability score per cell, the recommended cell and the raw best."""
    rows = _cell_rows(cells, values, axes)
    recommended = max(rows, key=lambda r: r["plateau"]) if rows else None
    raw_best = max(rows, key=lambda r: r["value"]) if rows else None
    gap = None
    if recommended is not None and raw_best is not None:
        gap = round(raw_best["value"] - recommended["value"], 6)
    return {
        "metric": metric,
        "axes": axes,
        "cells": rows,
        "missing": [i for i in range(len(cells)) if values.get(i) is None],
        "recommended": recommended,
        "raw_best": raw_best,
        "gap": gap,
        "basis": (
            "stability = median neighbour result / the cell's own; neighbours are one grid "
            "step away on one axis; recommended = the best median of a cell and its neighbours"
        ),
    }
