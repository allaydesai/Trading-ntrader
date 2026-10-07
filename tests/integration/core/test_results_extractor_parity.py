"""Story 5.1, AC #3 and #8 — one calculation path for backtests and live hosts.

Integration tier and ``--forked``: real ``BacktestEngine`` runs.

The fingerprint below was captured from the **pre-change** extractor at
``40e8f1c``, before ``src/core/results_extractor.py`` was edited (story Task
1.2). It is a constant on purpose: the "before" code no longer exists to
re-run (the Story 4.4 precedent, ``test_warmup_backtest_parity.py``).

The venue is the production shape — ``OmsType.HEDGING``, ``AccountType.MARGIN``
— because every production backtest venue is HEDGING (story F7). The price path
is choppy on purpose: a winners-only path leaves drawdown, profit factor and
Calmar at ``None``/0, and a fingerprint that never exercises a formula cannot
prove that formula unchanged.
"""

import math
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.results_extractor import LiveHost, ResultsExtractor
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

pytestmark = pytest.mark.integration

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
BAR_TYPE = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
VENUE = Venue("NASDAQ")
T0 = 1_700_000_000 * 1_000_000_000
MINUTE = 60_000_000_000
#: Two sines: a slow trend plus a fast wobble, so the 3/5 crossover whipsaws
#: and loses on 5 of its 16 round trips.
CLOSES = tuple(round(100 + 6 * math.sin(i / 6) + 4 * math.sin(i / 2.3), 2) for i in range(240))
STARTING_BALANCE = 100_000.0
#: One year, so CAGR and Calmar are computed rather than ``None``.
START = datetime(2023, 11, 14, tzinfo=UTC)
END = datetime(2024, 11, 14, tzinfo=UTC)

#: `ResultsExtractor(engine, VENUE, starting_balance=100_000.0)` over the run
#: below, `extract_results(START, END).to_dict()` minus `result_id`, captured
#: from the code as it stood at 40e8f1c.
BACKTEST_RESULT_BEFORE = {
    "total_return": 0.5940473000000001,
    "total_trades": 16,
    "winning_trades": 11,
    "losing_trades": 5,
    "largest_win": 10312.5,
    "largest_loss": -1608.18,
    "final_balance": 159404.73,
    "win_rate": 68.75,
    "sharpe_ratio": 0.1598475825977981,
    "sortino_ratio": None,
    "volatility": 0.04762842783555194,
    "profit_factor": 14.537056072088328,
    "risk_return_ratio": 0.8415917592738971,
    "avg_return": 0.0371544366778256,
    "avg_win_return": 0.05803503041092515,
    "avg_loss_return": -0.008782869534993396,
    "total_pnl": 59404.73,
    "total_pnl_percentage": 59.40473000000001,
    "expectancy": 3494.395882352942,
    "avg_win": 5799.308181818183,
    "avg_loss": -731.2766666666668,
    "max_winner": 10312.5,
    "max_loser": -1608.18,
    "min_winner": 1265.84,
    "min_loser": 0.0,
    "max_drawdown": -0.01609835877183291,
    "max_drawdown_duration_days": None,
    "cagr": 0.5925249405190294,
    "calmar_ratio": 36.80654338228333,
    "total_fees": None,
    "total_commissions": None,
}

#: `extract_equity_curve(START, END)` over the same run, same commit. The
#: trailing `time: 0` point is the position still open at the end of data
#: (`ts_closed == 0`), a pre-existing quirk recorded in deferred-work, not
#: this story's to change.
EQUITY_CURVE_BEFORE = [
    {"time": 1700000900, "value": 102205.14},
    {"time": 1700001800, "value": 111382.91},
    {"time": 1700002520, "value": 110043.42},
    {"time": 1700003540, "value": 117708.61},
    {"time": 1700004320, "value": 122384.21},
    {"time": 1700005160, "value": 122342.93},
    {"time": 1700006120, "value": 134970.4},
    {"time": 1700006840, "value": 134780.64},
    {"time": 1700007860, "value": 139722.45},
    {"time": 1700008700, "value": 149093.2},
    {"time": 1700009480, "value": 146998.93},
    {"time": 1700010500, "value": 159648.22},
    {"time": 1700011220, "value": 162845.46},
    {"time": 1700012180, "value": 164907.46},
    {"time": 1700013080, "value": 179992.04},
    {"time": 1700013800, "value": 177094.46},
    {"time": 0, "value": 177094.46},
]


def _bars() -> list[Bar]:
    out = []
    for index, close in enumerate(CLOSES):
        price = Price.from_str(f"{close:.2f}")
        ts = T0 + (index + 1) * MINUTE
        out.append(Bar(BAR_TYPE, price, price, price, price, Quantity.from_int(1_000), ts, ts))
    return out


def _crossover() -> SMACrossover:
    return SMACrossover(
        SMAConfig(instrument_id=AAPL.id, bar_type=BAR_TYPE, fast_period=3, slow_period=5)
    )


def _flipping_momentum() -> SMAMomentum:
    """Long on a golden cross, short on a death cross — each reversal one order."""
    return SMAMomentum(
        SMAMomentumConfig(
            instrument_id=AAPL.id,
            bar_type=BAR_TYPE,
            trade_size=Decimal("10"),
            order_id_tag="002",
            fast_period=3,
            slow_period=5,
            allow_short=True,
        )
    )


def _finished_engine(oms_type: OmsType = OmsType.HEDGING, strategy=None) -> BacktestEngine:
    """A backtest that has run to the end — ``end()`` has primed its analyzer."""
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    engine.add_venue(
        VENUE,
        oms_type=oms_type,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_BALANCE, USD)],
        base_currency=USD,
    )
    engine.add_instrument(AAPL)
    engine.add_data(_bars())
    engine.add_strategy(strategy if strategy is not None else _crossover())
    engine.run()
    return engine


def _netting_engine_with(strategy) -> BacktestEngine:
    return _finished_engine(OmsType.NETTING, strategy)


def _fingerprint(extractor: ResultsExtractor) -> tuple[dict, list]:
    result = extractor.extract_results(START, END).to_dict()
    result.pop("result_id")
    return result, extractor.extract_equity_curve(START, END)


def _backtest_path(engine: BacktestEngine) -> tuple[dict, list]:
    return _fingerprint(
        ResultsExtractor(engine=engine, venue=VENUE, starting_balance=STARTING_BALANCE)
    )


class TestTheBacktestPathIsUnchanged:
    """AC #3."""

    def test_the_backtest_path_is_bit_identical_to_the_pre_change_code(self):
        engine = _finished_engine()
        result, curve = _backtest_path(engine)
        engine.dispose()

        assert result == BACKTEST_RESULT_BEFORE
        assert curve == EQUITY_CURVE_BEFORE


def _live_path(engine: BacktestEngine) -> tuple[dict, list]:
    host = LiveHost.settle(engine, starting_balance=Decimal(str(STARTING_BALANCE)))
    return _fingerprint(ResultsExtractor(engine=host))


class TestOneCalculationPath:
    """AC #8 — a finished backtest engine *is* a host exposing ``portfolio`` and
    ``cache``, so settling it as a live host must change nothing."""

    def test_the_live_path_and_the_backtest_path_agree_on_one_engine(self):
        engine = _finished_engine()

        live_result, live_curve = _live_path(engine)
        backtest_result, backtest_curve = _backtest_path(engine)
        engine.dispose()

        assert live_result == BACKTEST_RESULT_BEFORE
        assert live_curve == EQUITY_CURVE_BEFORE
        # `settle` primes a copy and writes nothing to its source: the backtest path,
        # reading the engine's own analyzer afterwards, still sees what `end()` left.
        assert backtest_result == BACKTEST_RESULT_BEFORE
        assert backtest_curve == EQUITY_CURVE_BEFORE

    def test_the_settled_analyzer_carries_the_ledger_not_the_engines_balances(self):
        """Code review 2026-10-07: the copy above starts from what `end()` primed, so a
        `settle` that skipped priming would still pass it. A different capital is a
        `PnL%` only the session's own ledger can produce."""
        engine = _finished_engine()
        host = LiveHost.settle(engine, starting_balance=Decimal("50000"))
        result = ResultsExtractor(engine=host).extract_results(START, END)
        engine.dispose()

        realized = BACKTEST_RESULT_BEFORE["total_pnl"]
        assert result.final_balance == pytest.approx(50_000 + realized)
        assert result.total_pnl == pytest.approx(realized)
        assert result.total_pnl_percentage == pytest.approx(realized / 50_000 * 100)
        assert result.total_trades == BACKTEST_RESULT_BEFORE["total_trades"]


class TestNettingLegs:
    """AC #7 end-to-end — the live shape (NETTING) on a real engine."""

    def test_a_netting_engine_counts_every_closed_leg(self):
        engine = _finished_engine(OmsType.NETTING)
        closed_legs = [
            p
            for p in [*engine.cache.positions(), *engine.cache.position_snapshots()]
            if p.is_closed
        ]
        raw_closed = len(engine.cache.positions_closed())

        live_result, _curve = _live_path(engine)
        engine.dispose()

        assert len(closed_legs) == 16
        assert raw_closed < len(closed_legs), "the undercount D-E exists for (story F6)"
        assert live_result["total_trades"] == 16
        assert live_result["winning_trades"] + live_result["losing_trades"] == 16
        # Same fills, same money: for a strategy that goes flat before re-entering,
        # NETTING changes how legs are stored, not what they realised.
        assert live_result["final_balance"] == BACKTEST_RESULT_BEFORE["final_balance"]

    def test_canary_a_netting_flip_leaves_no_closed_leg_in_the_cache(self):
        """CANARY (code review 2026-10-07). A flip in one order makes the engine open a
        *new* ``Position`` over the closed one with no snapshot
        (``execution/engine.pyx:1440-1442, 1600``; ``cache.pyx:2124``), so every
        closed leg of a flipping strategy is gone from the cache, and ``settle`` over
        a node sees none of them. This is why a seal builds its source from ``trades``
        (one row per ``PositionClosed``) — Stories 5.2/5.3. If this fails, Nautilus
        started keeping flipped legs: revisit ``docs/agent/nautilus.md``."""
        engine = _netting_engine_with(_flipping_momentum())
        filled = [o for o in engine.cache.orders() if o.filled_qty.as_double() > 0]
        legs = [*engine.cache.positions(), *engine.cache.position_snapshots()]
        account_change = (
            engine.cache.account_for_venue(VENUE).balance_total(USD).as_double() - STARTING_BALANCE
        )

        live_result, _curve = _live_path(engine)
        engine.dispose()

        assert len(filled) > 2, "the strategy flipped"
        assert [leg for leg in legs if leg.is_closed] == []
        assert account_change != 0.0, "the flips realised money the cache no longer holds"
        assert live_result["total_trades"] == 0
