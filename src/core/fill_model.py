"""The fill model every backtest path shares, seeded so a run can be reproduced."""

from typing import Literal

from nautilus_trader.backtest.models import FillModel

from src.core.fill_models import GapAwareFillModel

FillModelKind = Literal["default", "gap_aware"]


def make_fill_model(seed: int, kind: str = "default") -> FillModel:
    """Build the backtest fill model with its random draws fixed by ``seed``.

    ``kind="gap_aware"`` (a config's ``backtest.fill_model``) builds a ``GapAwareFillModel``
    with the same seeded probabilities: it differs only in the orders it reprices (stops
    gapped through, market-on-open exits). See ``src/core/fill_models.py``.

    Nautilus seeds Python's process-wide ``random`` module here rather than a
    generator of its own, so the same seed gives the same fills only while
    nothing else draws from ``random`` during the run. A worker process that
    runs one backtest (the research MCP) meets that; a long-lived process that
    runs other threads beside the engine (the web server) may not.

    Raises:
        ValueError: ``kind`` is not a known fill model.
    """
    models = {"default": FillModel, "gap_aware": GapAwareFillModel}
    if kind not in models:
        raise ValueError(f"Unknown fill model {kind!r}; expected one of {sorted(models)}")
    return models[kind](
        prob_fill_on_limit=0.95,
        prob_fill_on_stop=0.95,
        prob_slippage=0.01,
        random_seed=seed,
    )
