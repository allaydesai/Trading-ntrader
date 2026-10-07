"""Component tests for the live host (Story 5.1, Task 3).

Component tier: a real ``Cache``, a real ``Portfolio`` (whose constructor
registers the same 17 statistics every backtest's portfolio carries — story
F9) and real ``Position`` objects built by applying real ``OrderFilled``
events, the ``test_live_trade_recorder.py`` precedent. NETTING re-entry is
produced the way ``ExecutionEngine`` produces it: snapshot the closed position,
then reopen the same object in place (measured, story Debug Log).

Never constructs a ``TradingNode`` or ``BacktestEngine``; the autouse guard is
copied verbatim from ``test_live_trade_recorder.py``.
"""

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.accounting.factory import AccountFactory
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import (
    AccountType,
    LiquiditySide,
    OmsType,
    OrderSide,
    OrderType,
)
from nautilus_trader.model.events import AccountState, OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    PositionId,
    StrategyId,
    TradeId,
    TraderId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import AccountBalance, Money, Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.live_trade_recorder import RECONCILIATION_STRATEGY_IDS
from src.core.results_extractor import LiveHost, ResultsExtractor, _Ledger

pytestmark = pytest.mark.component

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
MSFT = TestInstrumentProvider.equity(symbol="MSFT", venue="NASDAQ")
EURGBP = TestInstrumentProvider.default_fx_ccy("EUR/GBP")
TRADER_ID = TraderId("TESTER-000")
STRATEGY = StrategyId("SMACrossover-000")
IB_ACCOUNT = AccountId("IB-DU1234567")
IB = Venue("IB")
STARTING = Decimal("100000")
ONE_DOLLAR = Money(Decimal("1.00"), USD)
#: One fill a day: Nautilus's returns statistics downsample to daily bins, so
#: legs closed within one day would leave Sharpe undefined for a reason that
#: has nothing to do with the code under test.
DAY = 86_400 * 1_000_000_000
#: The legs close on 1970's first days (``DAY`` steps from the epoch); a year's
#: window makes CAGR and Calmar computable.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
EPOCH_PLUS_ONE_YEAR = datetime(1971, 1, 1, tzinfo=UTC)
_COUNTER = itertools.count()


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-101``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a "
        "TradingNode."
    )


def _fill(side, qty, px, ts, *, instrument=AAPL, strategy=STRATEGY, commission=ONE_DOLLAR):
    n = next(_COUNTER)
    return OrderFilled(
        trader_id=TRADER_ID,
        strategy_id=strategy,
        instrument_id=instrument.id,
        client_order_id=ClientOrderId(f"O-{n}"),
        venue_order_id=VenueOrderId(f"V-{n}"),
        account_id=IB_ACCOUNT,
        trade_id=TradeId(f"T-{n}"),
        position_id=PositionId(f"{instrument.id}-{strategy}"),
        order_side=side,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_int(qty),
        last_px=Price.from_str(px),
        currency=instrument.quote_currency,
        commission=commission,
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=ts,
        ts_init=ts,
    )


class _Session:
    """A live-kernel-shaped ``(portfolio, cache)`` pair — what a ``TradingNode``
    exposes (story F10) — with the session's legs applied the engine's way."""

    def __init__(self) -> None:
        clock = TestClock()
        self.cache = Cache()
        for instrument in (AAPL, MSFT, EURGBP):
            self.cache.add_instrument(instrument)
        self.portfolio = Portfolio(MessageBus(TRADER_ID, clock), self.cache, clock)
        self._open: dict[PositionId, Position] = {}
        self._ts = DAY

    def _next_ts(self) -> int:
        self._ts += DAY
        return self._ts

    def round_trip(self, entry_px, exit_px, *, qty=100, instrument=AAPL, strategy=STRATEGY):
        """One closed leg. A second round trip on the same instrument and
        strategy re-uses the NETTING position id: the closed position is
        snapshotted and the same object reopens in place, as the engine does."""
        self.open_leg(entry_px, qty=qty, instrument=instrument, strategy=strategy)
        return self.close_leg(exit_px, qty=qty, instrument=instrument, strategy=strategy)

    def close_leg(self, exit_px, *, qty=100, instrument=AAPL, strategy=STRATEGY):
        position = self._open[PositionId(f"{instrument.id}-{strategy}")]
        commission = Money(Decimal("1.00"), instrument.quote_currency)
        exit_fill = _fill(
            OrderSide.SELL,
            qty,
            exit_px,
            self._next_ts(),
            instrument=instrument,
            strategy=strategy,
            commission=commission,
        )
        position.apply(exit_fill)
        self.cache.update_position(position)
        return position

    def open_leg(self, entry_px, *, qty=100, instrument=AAPL, strategy=STRATEGY):
        position_id = PositionId(f"{instrument.id}-{strategy}")
        commission = Money(Decimal("1.00"), instrument.quote_currency)
        entry = _fill(
            OrderSide.BUY,
            qty,
            entry_px,
            self._next_ts(),
            instrument=instrument,
            strategy=strategy,
            commission=commission,
        )
        position = self._open.get(position_id)
        if position is None:
            position = Position(instrument=instrument, fill=entry)
            self.cache.add_position(position, OmsType.NETTING)
            self._open[position_id] = position
            return position
        self.cache.snapshot_position(position)
        position.apply(entry)
        self.cache.update_position(position)
        return position

    def add_ib_account(self, *, starting=100_000, net_liquidation=400_000) -> None:
        """An IB-shaped margin account whose total is the adapter's invented
        ``400000`` (story F14) — present and readable, so ignoring it means something."""
        states = [
            AccountState(
                account_id=IB_ACCOUNT,
                account_type=AccountType.MARGIN,
                base_currency=USD,
                reported=True,
                balances=[AccountBalance(Money(v, USD), Money(0, USD), Money(v, USD))],
                margins=[],
                info={},
                event_id=UUID4(),
                ts_event=0,
                ts_init=0,
            )
            for v in (starting, net_liquidation)
        ]
        account = AccountFactory.create(states[0])
        account.apply(states[1])
        self.cache.add_account(account)

    def all_legs(self) -> list[Position]:
        return [*self.cache.positions(), *self.cache.position_snapshots()]


def _realized(legs) -> Decimal:
    return sum((leg.realized_pnl.as_decimal() for leg in legs), Decimal(0))


def _mixed_session() -> _Session:
    """Two winners and a loser on AAPL (NETTING re-entries), a winner on MSFT."""
    session = _Session()
    session.round_trip("100.00", "110.00")  # +1000 - 2
    session.round_trip("110.00", "105.00")  # -500 - 2
    session.round_trip("105.00", "112.00")  # +700 - 2
    session.round_trip("200.00", "204.00", instrument=MSFT)  # +400 - 2
    return session


class TestTheNettingUndercountIsReal:
    """Task 3.1 — the hazard AC #7 exists for, measured at this tier."""

    def test_a_raw_netting_cache_reports_only_the_current_leg_as_closed(self):
        session = _mixed_session()

        legs = [leg for leg in session.all_legs() if leg.is_closed]
        assert len(legs) == 4
        assert len(session.cache.positions_closed()) == 2  # AAPL's current leg + MSFT
        assert len(session.cache.position_snapshots()) == 2  # AAPL's two earlier legs
        assert len({leg.id for leg in legs}) == 4, "snapshot ids are unique (story F5)"


class TestNeverReadsTheBrokersBalance:
    """AC #5 — the routed Story 4.1 F5 debt."""

    def test_the_final_balance_and_total_pnl_come_from_the_sessions_own_ledger(self):
        session = _mixed_session()
        session.add_ib_account()
        realized = _realized(session.all_legs())

        host = LiveHost.settle(session, starting_balance=STARTING)
        result = ResultsExtractor(engine=host, venue=IB).extract_results()

        assert realized == Decimal("1592.00")
        assert result.final_balance == float(STARTING + realized)
        assert result.total_pnl == float(realized)
        assert result.total_pnl_percentage == pytest.approx(float(realized / STARTING * 100))
        assert result.total_return == pytest.approx(float(realized / STARTING))
        assert 400_000.0 not in (result.final_balance, result.total_pnl)

    def test_sibling_the_same_cache_through_the_backtest_path_reads_400000(self):
        """Proves the account is present and readable — so ignoring it is a choice."""
        session = _mixed_session()
        session.add_ib_account()

        result = ResultsExtractor(
            engine=session, venue=IB, starting_balance=float(STARTING)
        ).extract_results()

        assert result.final_balance == 400_000.0


class TestTheAnalyzerIsPrimed:
    """AC #6 — no ``TradingNode`` ever calls ``calculate_statistics`` (story F2)."""

    #: Every analyzer-derived field ``extract_results`` reads (results_extractor.py,
    #: the stats-key mapping), plus the custom ones it computes from the analyzer.
    _STATS = (
        "sharpe_ratio",
        "sortino_ratio",
        "volatility",
        "profit_factor",
        "risk_return_ratio",
        "avg_return",
        "avg_win_return",
        "avg_loss_return",
        "total_pnl",
        "total_pnl_percentage",
        "expectancy",
        "avg_win",
        "avg_loss",
        "max_winner",
        "max_loser",
        "min_winner",
        "min_loser",
        "max_drawdown",
        "cagr",
        "calmar_ratio",
    )

    def test_every_statistic_the_extractor_reads_is_computed(self):
        host = LiveHost.settle(_mixed_session(), starting_balance=STARTING)

        result = ResultsExtractor(engine=host).extract_results(EPOCH, EPOCH_PLUS_ONE_YEAR)

        missing = [name for name in self._STATS if getattr(result, name) is None]
        assert missing == []
        assert result.max_winner == pytest.approx(998.0)
        assert result.max_loser == pytest.approx(-502.0)
        assert result.min_winner == pytest.approx(398.0)

    def test_sibling_an_unsettled_live_portfolio_sees_none_of_the_trades(self):
        """Measured: unprimed, the returns statistics are ``None`` and the PnL
        statistics read a silent ``0.0`` — four trades, invisible."""
        session = _mixed_session()
        session.add_ib_account()

        result = ResultsExtractor(
            engine=session, venue=IB, starting_balance=float(STARTING)
        ).extract_results()

        returns_based = ("sharpe_ratio", "profit_factor", "max_drawdown")
        pnl_based = ("expectancy", "avg_win", "avg_loss", "max_winner", "max_loser")
        assert [getattr(result, name) for name in returns_based] == [None, None, None]
        assert [getattr(result, name) for name in pnl_based] == [0.0] * 5

    def test_the_analyzer_reads_only_two_balance_members_from_its_account(self):
        """CANARY (D-C). If Nautilus makes ``calculate_statistics`` read any other
        account member, this raises ``AttributeError``: give ``_Ledger`` that member
        from the session's own ledger — never from IB's ``AccountState``."""
        legs = _mixed_session().all_legs()
        analyzer = PortfolioAnalyzer()
        ledger = _Ledger.over(legs, STARTING)

        analyzer.calculate_statistics(ledger, legs)

        assert analyzer.total_pnl(USD) == 1592.0
        assert analyzer.currencies == [USD]


class TestEveryNettingLegIsATrade:
    """AC #7."""

    def test_snapshotted_legs_count_exactly_as_hedging_positions_do(self):
        host = LiveHost.settle(_mixed_session(), starting_balance=STARTING)

        result = ResultsExtractor(engine=host).extract_results()

        assert result.total_trades == 4
        assert (result.winning_trades, result.losing_trades) == (3, 1)
        assert result.largest_win == pytest.approx(998.0)
        assert result.largest_loss == pytest.approx(-502.0)

    def test_an_open_leg_is_in_the_ledger_but_is_not_a_trade(self):
        session = _mixed_session()
        session.open_leg("300.00", instrument=MSFT)  # reopens MSFT: -1 commission, still open

        host = LiveHost.settle(session, starting_balance=STARTING)
        result = ResultsExtractor(engine=host).extract_results()

        assert result.total_trades == 4
        assert result.final_balance == float(STARTING + Decimal("1592.00") - Decimal("1.00"))


class TestLegsAreReadInTimeOrder:
    """Code review 2026-10-07 — NETTING presents legs by position id with snapshots after;
    the returns series (and so max drawdown, Calmar and the curve) must follow time."""

    @staticmethod
    def _session() -> _Session:
        session = _Session()
        session.round_trip("100.00", "110.00")  # AAPL +10%
        session.round_trip("200.00", "180.00", instrument=MSFT)  # MSFT -10%
        session.round_trip("100.00", "150.00")  # AAPL +50%  (snapshotted on re-entry)
        session.round_trip("100.00", "80.00")  # AAPL -20%  (the current leg)
        return session

    def test_the_raw_cache_presents_the_legs_out_of_time_order(self):
        """Sibling: without sorting, the series below would be scrambled."""
        closes = [leg.ts_closed for leg in self._session().all_legs()]

        assert closes != sorted(closes)

    def test_max_drawdown_is_the_time_ordered_one(self):
        """1.10 → 0.99 → 1.485 → 1.188: the worst fall is 1.485 → 1.188, −20%.
        In cache order (−20%, −10%, +10%, +50%) it would read −10%."""
        host = LiveHost.settle(self._session(), starting_balance=STARTING)

        result = ResultsExtractor(engine=host).extract_results()

        assert result.max_drawdown == pytest.approx(-0.20)

    def test_the_equity_curve_moves_forward_in_time(self):
        host = LiveHost.settle(self._session(), starting_balance=STARTING)

        times = [point["time"] for point in ResultsExtractor(engine=host).extract_equity_curve()]

        assert times == sorted(times)
        assert len(times) == 4


class TestASettledHostIsASnapshot:
    """Code review 2026-10-07 — ``settle`` copies; nothing done to the source later
    reaches the host, and the source is never written."""

    def test_a_fill_after_settle_does_not_change_the_host(self):
        session = _mixed_session()
        session.open_leg("300.00", instrument=MSFT)
        host = LiveHost.settle(session, starting_balance=STARTING)
        before = ResultsExtractor(engine=host).extract_results().to_dict()

        session.close_leg("340.00", instrument=MSFT)  # +4000 lands in the source only

        after = ResultsExtractor(engine=host).extract_results().to_dict()
        assert {k: v for k, v in after.items() if k != "result_id"} == {
            k: v for k, v in before.items() if k != "result_id"
        }
        assert after["total_trades"] == 4

    def test_a_second_settle_does_not_rewrite_the_first(self):
        session = _mixed_session()
        first = LiveHost.settle(session, starting_balance=Decimal("50000"))

        LiveHost.settle(session, starting_balance=Decimal("100000"))

        result = ResultsExtractor(engine=first).extract_results()
        assert result.total_pnl_percentage == pytest.approx(1592.0 / 50_000 * 100)
        assert result.total_return == pytest.approx(1592.0 / 50_000)

    def test_the_source_analyzer_is_never_written(self):
        session = _mixed_session()

        LiveHost.settle(session, starting_balance=STARTING)

        assert len(session.portfolio.analyzer.returns()) == 0
        assert session.portfolio.analyzer.currencies == []


class TestReconciliationOwnedPositionsAreNotTrades:
    """AC #9 — the trades table's own rule (Story 3.6 D-D)."""

    @pytest.mark.parametrize("owner", sorted(RECONCILIATION_STRATEGY_IDS))
    def test_it_contributes_nothing(self, owner):
        session = _mixed_session()
        session.round_trip("50.00", "90.00", instrument=MSFT, strategy=StrategyId(owner))
        session.round_trip("50.00", "10.00", strategy=StrategyId(owner))

        host = LiveHost.settle(session, starting_balance=STARTING)
        result = ResultsExtractor(engine=host).extract_results()

        assert result.total_trades == 4
        assert result.final_balance == float(STARTING + Decimal("1592.00"))
        assert result.max_winner == pytest.approx(998.0)
        assert result.max_loser == pytest.approx(-502.0)


class TestStartingCapitalAndCurrency:
    """AC #10."""

    def test_a_non_usd_leg_refuses_naming_its_currency(self):
        session = _mixed_session()
        session.round_trip("0.85000", "0.86000", qty=10_000, instrument=EURGBP)

        with pytest.raises(ValueError, match="GBP"):
            LiveHost.settle(session, starting_balance=STARTING)

    def test_the_settings_default_never_stands_in_for_a_live_hosts_capital(self):
        host = LiveHost.settle(_mixed_session(), starting_balance=Decimal("50000"))
        settings = SimpleNamespace(default_balance=1_000_000)

        result = ResultsExtractor(engine=host, settings=settings).extract_results()

        assert result.final_balance == float(Decimal("50000") + Decimal("1592.00"))
        assert result.total_return == pytest.approx(1592.0 / 50_000)


class TestALiveHostIsAccepted:
    """AC #1 — anything exposing ``portfolio`` and ``cache``, as a ``TradingNode`` does."""

    def test_a_node_shaped_pair_yields_results_and_an_equity_curve(self):
        session = _mixed_session()
        node = SimpleNamespace(portfolio=session.portfolio, cache=session.cache)

        extractor = ResultsExtractor(engine=LiveHost.settle(node, starting_balance=STARTING))
        result = extractor.extract_results()
        curve = extractor.extract_equity_curve()

        assert result.total_trades == 4
        assert len(curve) == 4

    def test_the_equity_curve_is_scaled_by_the_hosts_own_capital(self):
        small = ResultsExtractor(
            engine=LiveHost.settle(_mixed_session(), starting_balance=Decimal("50000"))
        ).extract_equity_curve()
        large = ResultsExtractor(
            engine=LiveHost.settle(_mixed_session(), starting_balance=Decimal("100000"))
        ).extract_equity_curve()

        assert [point["value"] * 2 for point in small] == pytest.approx(
            [point["value"] for point in large], abs=0.02
        )
