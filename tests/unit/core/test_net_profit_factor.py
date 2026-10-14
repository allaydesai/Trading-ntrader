"""The stored profit factor is net of commission.

Found by the research MCP's phase 4 exit check (2026-10-13): the figure persisted as
``profit_factor`` was Nautilus's ``Profit Factor`` statistic, which sums each position's
``realized_return`` — a price-only ratio of average close over average open. Commission
never entered it, and neither did size, so a 2× cost-stress run kept the in-sample value
to four decimals while the breadth and profit-factor gate rows judged on it. The gates'
G0 rule says commissions are on; the profit factor they read now agrees.
"""

import inspect
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.analysis.statistics.profit_factor import ProfitFactor
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money

from src.core import backtest_runner, results_extractor
from src.core.metrics import net_profit_factor
from src.core.results_extractor import (
    LiveHost,
    ResultsExtractor,
    _ClosedLegs,
    _Ledger,
    _SettledPortfolio,
)

STARTING = Decimal("100000")


def _leg(pnl: str, realized_return: float, *, ts_closed: int):
    return SimpleNamespace(
        id=SimpleNamespace(value=f"P-{ts_closed}"),
        strategy_id="SMACrossover-000",
        realized_pnl=Money(Decimal(pnl), USD),
        realized_return=realized_return,
        is_closed=True,
        is_open=False,
        ts_closed=ts_closed,
    )


#: Two wins and two losses whose price returns net to a 1.0 gross profit factor while
#: the dollar P&L (which carries the commission) says 2.142857.
LEGS = (
    _leg("1000", 0.01, ts_closed=1),
    _leg("-400", -0.01, ts_closed=2),
    _leg("500", 0.01, ts_closed=3),
    _leg("-300", -0.01, ts_closed=4),
)
NET = 1500 / 700


def _primed_analyzer(legs=LEGS) -> PortfolioAnalyzer:
    analyzer = PortfolioAnalyzer()
    analyzer.register_statistic(ProfitFactor())  # the engine registers it; a bare one has none
    analyzer.calculate_statistics(_Ledger.over(legs, STARTING), list(legs))
    return analyzer


@pytest.mark.unit
class TestNetProfitFactor:
    def test_it_is_the_dollar_wins_over_the_dollar_losses(self):
        assert net_profit_factor(_primed_analyzer().realized_pnls(USD)) == pytest.approx(NET)

    def test_it_differs_from_the_price_return_statistic_the_extractor_used_to_store(self):
        analyzer = _primed_analyzer()
        gross = analyzer.get_performance_stats_returns()["Profit Factor"]
        assert gross == pytest.approx(1.0)
        assert net_profit_factor(analyzer.realized_pnls(USD)) != pytest.approx(gross)

    def test_no_losses_is_none_not_infinity(self):
        assert net_profit_factor(pd.Series([10.0, 5.0])) is None

    def test_no_wins_is_zero(self):
        assert net_profit_factor(pd.Series([-10.0, -5.0])) == 0.0

    @pytest.mark.parametrize("pnls", [None, pd.Series([], dtype=float)])
    def test_nothing_closed_is_none(self, pnls):
        assert net_profit_factor(pnls) is None

    def test_a_breakeven_leg_is_on_neither_side(self):
        assert net_profit_factor(pd.Series([10.0, 0.0, -5.0])) == pytest.approx(2.0)


@pytest.mark.unit
class TestTheExtractorStoresTheNetFigure:
    def test_a_settled_host_reports_the_net_profit_factor(self):
        host = LiveHost(
            portfolio=_SettledPortfolio(_primed_analyzer()),
            cache=_ClosedLegs(LEGS),
            starting_balance=STARTING,
            final_balance=Money(STARTING + 800, USD),
        )
        result = ResultsExtractor(
            engine=host, settings=SimpleNamespace(default_balance=1_000_000)
        ).extract_results()

        assert result.profit_factor == pytest.approx(NET)
        assert (result.winning_trades, result.losing_trades) == (2, 2)

    @pytest.mark.parametrize(
        "extract",
        [
            results_extractor.ResultsExtractor.extract_results,
            backtest_runner.MinimalBacktestRunner._extract_results,
        ],
        ids=["results_extractor", "backtest_runner"],
    )
    def test_neither_extraction_path_reads_the_price_return_statistic(self, extract):
        source = inspect.getsource(extract)
        assert '"Profit Factor"' not in source
        assert "net_profit_factor(" in source
