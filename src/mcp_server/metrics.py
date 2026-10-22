"""Units and direction for every stored performance metric.

Units follow how NTrader stores each value: returns, drawdown, volatility and
win rate are fractions (0.25 = 25 %); ``total_pnl_percentage`` comes from
Nautilus already in percent; money is in the account currency. Drawdown is
stored negative, so "higher" (nearer zero) is better. ``direction`` is what
``compare_runs`` uses to mark the best run; ``None`` means not ranked.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Direction = Literal["higher", "lower"] | None


@dataclass(frozen=True)
class MetricSpec:
    unit: str
    direction: Direction


_F, _R, _C, _N = "fraction", "ratio", "currency", "count"

METRIC_SPECS: dict[str, MetricSpec] = {
    "total_return": MetricSpec(_F, "higher"),
    "final_balance": MetricSpec(_C, "higher"),
    "cagr": MetricSpec(_F, "higher"),
    "sharpe_ratio": MetricSpec(_R, "higher"),
    "sortino_ratio": MetricSpec(_R, "higher"),
    "max_drawdown": MetricSpec(_F, "higher"),
    "max_drawdown_date": MetricSpec("datetime", None),
    "calmar_ratio": MetricSpec(_R, "higher"),
    "volatility": MetricSpec(_F, "lower"),
    "risk_return_ratio": MetricSpec(_R, "higher"),
    "avg_return": MetricSpec(_F, "higher"),
    "avg_win_return": MetricSpec(_F, "higher"),
    "avg_loss_return": MetricSpec(_F, "higher"),
    "total_trades": MetricSpec(_N, None),
    "winning_trades": MetricSpec(_N, None),
    "losing_trades": MetricSpec(_N, None),
    "win_rate": MetricSpec(_F, "higher"),
    "profit_factor": MetricSpec(_R, "higher"),
    "expectancy": MetricSpec(_C, "higher"),
    "avg_win": MetricSpec(_C, "higher"),
    "avg_loss": MetricSpec(_C, "higher"),
    "total_pnl": MetricSpec(_C, "higher"),
    "total_pnl_percentage": MetricSpec("percent", "higher"),
    "max_winner": MetricSpec(_C, "higher"),
    "max_loser": MetricSpec(_C, "higher"),
    "min_winner": MetricSpec(_C, None),
    "min_loser": MetricSpec(_C, None),
}


#: Why a run whose equity reached zero is not evidence of anything.
INSOLVENT_NOTE = (
    "equity reached zero or below (max drawdown at or beyond -100%): the position size "
    "exceeds the account, so this run's returns, drawdown and ratios are meaningless. "
    "Check the resolved trade_size against the starting balance."
)


def insolvent(max_drawdown: float | Decimal | None) -> bool:
    """True when a drawdown says the equity went to zero or below (drawdown is a fraction)."""
    return max_drawdown is not None and float(max_drawdown) <= -1.0
