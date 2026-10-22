"""The fill model every backtest path shares, seeded so a run can be reproduced."""

from nautilus_trader.backtest.models import FillModel


def make_fill_model(seed: int) -> FillModel:
    """Build the backtest fill model with its random draws fixed by ``seed``.

    Nautilus seeds Python's process-wide ``random`` module here rather than a
    generator of its own, so the same seed gives the same fills only while
    nothing else draws from ``random`` during the run. A worker process that
    runs one backtest (the research MCP) meets that; a long-lived process that
    runs other threads beside the engine (the web server) may not.
    """
    return FillModel(
        prob_fill_on_limit=0.95,
        prob_fill_on_stop=0.95,
        prob_slippage=0.01,
        random_seed=seed,
    )
