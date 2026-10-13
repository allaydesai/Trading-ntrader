"""Story 5.2, AC #2 and #5 — a sealed session's trade rows reproduce its backtest.

Integration tier and ``--forked``: real ``BacktestEngine`` runs, the harness of
``test_results_extractor_parity.py`` (Story 5.1). Each engine's closed positions
become the trade rows the live path writes — through the recorder's own
``aggregate_closed_position`` and ``SqlTradeRecord.persist``'s field mapping —
and are handed to :func:`reconstruct_session_results`, which never sees the
engine.

The proof needs a run that **ends flat**. A backtest that ends mid-position
feeds its open leg to the analyzer (a return filed at the epoch, an entry
commission scored as a loser — Story 5.1's deferred artifacts), and a sealed
session's closed-trade sequence has no such leg (story D-D). The golden run,
which ends mid-position, is the sibling: it pins those artifacts, by name, as
the only difference.
"""

from decimal import Decimal

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.stubs.events import TestEventStubs

from src.core.live_trade_recorder import aggregate_closed_position
from src.core.results_extractor import ResultsExtractor
from src.core.session_equity import EQUITY_SERIES_KEY, reconstruct_session_results
from src.db.models.trade import Trade as TradeRow
from src.models.trade import TradeCreate
from tests.integration.core.test_results_extractor_parity import (
    AAPL,
    BAR_TYPE,
    CLOSES,
    END,
    MINUTE,
    START,
    STARTING_BALANCE,
    T0,
    VENUE,
    _crossover,
)

pytestmark = pytest.mark.integration

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
#: The 5.1 path ends short. A steep rise after it: the golden cross closes that
#: short (a signal closes the opposite side *or* opens, never both) and nothing
#: crosses again, so the run ends flat.
CLOSES_ENDING_FLAT = CLOSES + tuple(round(CLOSES[-1] + 1.5 * (i + 1), 2) for i in range(15))
#: The precision a trade row can carry. Prices are stored at 8 dp
#: (``Numeric(20, 8)``, ``to_price_decimal``), and a return is a small
#: difference of two of them, so every return-derived field is rebuilt to a few
#: parts in 1e9. Measured at code review, worst over both runs: ``profit_factor``
#: 4.28e-9, ``avg_loss_return`` 3.92e-9, ``max_drawdown`` / ``calmar_ratio``
#: 2.13e-9 (the drawdown compounds every quantized return), ``cagr`` 0 (it is
#: ledger-based). AC #2's first draft said ``1e-9``; the flat run's drawdown
#: (2.13e-9) refutes it, so the proof states this measured bound instead. A
#: wrong sign, basis or leg differs at order one.
REL = 1e-8
AC2_FIELDS = ("max_drawdown", "cagr", "calmar_ratio")
#: Every field the golden run's open leg moves past ``REL`` (story F5):
#: the epoch-0 return (Sharpe, volatility, risk-return) and the open leg's
#: entry commission scored as a loser (expectancy, average/min loser).
OPEN_LEG_ARTIFACTS = {
    "sharpe_ratio",
    "volatility",
    "risk_return_ratio",
    "expectancy",
    "avg_loss",
    "min_loser",
}


def _engine(closes: tuple[float, ...]) -> BacktestEngine:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    engine.add_venue(
        VENUE,
        oms_type=OmsType.HEDGING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_BALANCE, USD)],
        base_currency=USD,
    )
    engine.add_instrument(AAPL)
    bars = []
    for index, close in enumerate(closes):
        price = Price.from_str(f"{close:.2f}")
        ts = T0 + (index + 1) * MINUTE
        bars.append(Bar(BAR_TYPE, price, price, price, price, Quantity.from_int(1_000), ts, ts))
    engine.add_data(bars)
    engine.add_strategy(_crossover())
    engine.run()
    return engine


def _row(index: int, position) -> TradeRow:
    """The ``trades`` row the live path writes for one closed position.

    Through the recorder's own :func:`aggregate_closed_position` (review patch
    2: never a hand-mapped row that would share the module's assumptions about
    which side and which prices the recorder stores), then the field mapping of
    ``SqlTradeRecord.persist`` (``src/services/trade_record.py:118-154``) —
    every step but the database write.
    """
    recorded = aggregate_closed_position(TestEventStubs.position_closed(position), position)
    trade = TradeCreate(**recorded.trade.model_dump(), session_id=1, backtest_run_id=None)
    return TradeRow(
        id=index,
        backtest_run_id=trade.backtest_run_id,
        session_id=trade.session_id,
        instrument_id=trade.instrument_id,
        trade_id=trade.trade_id,
        venue_order_id=trade.venue_order_id,
        client_order_id=trade.client_order_id,
        order_side=trade.order_side,
        quantity=trade.quantity,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        commission_amount=trade.commission_amount,
        commission_currency=trade.commission_currency,
        fees_amount=trade.fees_amount,
        entry_timestamp=trade.entry_timestamp,
        exit_timestamp=trade.exit_timestamp,
        profit_loss=recorded.profit_loss,
        profit_pct=recorded.profit_pct,
        holding_period_seconds=recorded.holding_period_seconds,
    )


def _rows(engine: BacktestEngine) -> list[TradeRow]:
    closed = [position for position in engine.cache.positions() if position.is_closed]
    return [_row(index, position) for index, position in enumerate(closed, start=1)]


def _both_paths(closes: tuple[float, ...]):
    engine = _engine(closes)
    try:
        still_open = [position for position in engine.cache.positions() if position.is_open]
        extractor = ResultsExtractor(engine=engine, venue=VENUE, starting_balance=STARTING_BALANCE)
        backtest = extractor.extract_results(START, END).to_dict()
        backtest_curve = extractor.extract_equity_curve(START, END)
        sealed = reconstruct_session_results(
            _rows(engine),
            starting_capital=Decimal(str(STARTING_BALANCE)),
            start=START,
            end=END,
        )
    finally:
        engine.dispose()
    return still_open, backtest, backtest_curve, sealed


def _fields_that_differ(paper: dict, backtest: dict) -> set[str]:
    differ = set()
    for name, value in backtest.items():
        if name == "result_id":
            continue
        other = paper[name]
        if isinstance(value, float) and isinstance(other, float):
            if other != pytest.approx(value, rel=REL, abs=1e-12):
                differ.add(name)
        elif other != value:
            differ.add(name)
    return differ


class TestTheTradeRowsReproduceTheBacktest:
    """AC #2, #5 — one calculation path: the rows' record *is* the backtest's."""

    @pytest.mark.acceptance_criterion(
        "5.2b", "max drawdown, CAGR and Calmar come from the extractor's code path", SOURCE
    )
    def test_a_run_that_ends_flat_agrees_field_for_field(self):
        still_open, backtest, backtest_curve, sealed = _both_paths(CLOSES_ENDING_FLAT)

        assert still_open == [], "the precondition: this run must end flat"
        paper = sealed.result.to_dict()
        assert paper["total_trades"] == backtest["total_trades"] > 10
        assert paper["losing_trades"] > 0 and paper["max_drawdown"] < 0
        assert paper["cagr"] is not None and paper["calmar_ratio"] is not None
        for name in AC2_FIELDS:
            assert paper[name] == pytest.approx(backtest[name], rel=REL), name
        assert _fields_that_differ(paper, backtest) == set()

        curve = sealed.session_conditions[EQUITY_SERIES_KEY]["curve"]
        assert [point["time"] for point in curve] == [point["time"] for point in backtest_curve]
        assert [point["value"] for point in curve] == pytest.approx(
            [point["value"] for point in backtest_curve], abs=0.01
        )

    def test_a_run_that_ends_mid_position_differs_only_by_the_open_leg(self):
        """The sibling: 5.1's deferred open-leg artifacts are the sole difference."""
        still_open, backtest, _, sealed = _both_paths(CLOSES)

        assert len(still_open) == 1, "the precondition: the golden run ends mid-position"
        paper = sealed.result.to_dict()
        assert paper["total_trades"] == backtest["total_trades"]
        for name in AC2_FIELDS:
            assert paper[name] == pytest.approx(backtest[name], rel=REL), name
        assert _fields_that_differ(paper, backtest) == OPEN_LEG_ARTIFACTS
