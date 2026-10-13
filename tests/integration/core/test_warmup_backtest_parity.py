"""Story 4.4, AC #3 — the warm-up rewrite changes nothing a backtest can see.

Integration tier and ``--forked``: real ``BacktestEngine`` runs.

Why "identical" and not merely "explainable" (story Measured facts F1): a
backtest's ``DataEngine`` answers ``request_bars`` with an empty response,
synchronously, inside ``on_start`` — no production backtest registers a
catalog with it (``grep -rn register_catalog src/`` is empty). So the history
callback subscribes before ``on_start`` returns, with cold indicators, and every
bar is delivered exactly as before. The strongest claim the AC admits is
therefore the literal one: the same fills, byte for byte.

The fingerprints below were captured from the **pre-change** strategy code,
before either file was edited (story Task 1.3). They are constants on purpose:
the "before" code no longer exists to re-run.

The equivalence half ("any difference explained solely by indicators being warm
at the first traded bar") is proven by the one engine configuration in which a
backtest *does* warm: a ``ParquetDataCatalog`` holding the history, registered
with the engine's ``DataEngine`` (F15). A strategy warmed that way must make
exactly the decisions, bar for bar, that the same strategy makes after running
continuously through that history.
"""

import math
from decimal import Decimal

import pytest
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

pytestmark = pytest.mark.integration

AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
BAR_TYPE = BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL")
T0 = 1_700_000_000 * 1_000_000_000
MINUTE = 60_000_000_000
#: A deterministic price path with a crossover every ~19 bars at 3/5 periods.
CLOSES = tuple(round(100 + 10 * math.sin(i / 6), 2) for i in range(240))

#: `sma_crossover` fast=3/slow=5 over `CLOSES`, captured from the code as it
#: stood at c4c6afa — (ts_last, side, filled_qty, avg_px).
CROSSOVER_BEFORE = [
    (1700000780000000000, "SELL", "916", "109.09"),
    (1700001920000000000, "BUY", "916", "91.01"),
    (1700003060000000000, "SELL", "918", "108.87"),
    (1700004200000000000, "BUY", "918", "91.25"),
    (1700005280000000000, "SELL", "914", "109.35"),
    (1700006420000000000, "BUY", "914", "90.74"),
    (1700007560000000000, "SELL", "916", "109.16"),
    (1700008700000000000, "BUY", "916", "90.94"),
    (1700009840000000000, "SELL", "917", "108.95"),
    (1700010980000000000, "BUY", "917", "91.17"),
    (1700012120000000000, "SELL", "919", "108.71"),
    (1700013200000000000, "BUY", "919", "90.68"),
    (1700014340000000000, "SELL", "915", "109.22"),
]

#: `momentum` fast=3/slow=5 over the same path, captured from the same code:
#: **no fills at all** across 240 bars and 13 crossovers. This is the defect
#: D-G fixes (story F8), pinned so the record of what "before" meant survives
#: the fix: the old hand-rolled average summed every close instead of a
#: window, so fast (sum/3) sat above slow (sum/5) forever and never crossed.
MOMENTUM_BEFORE: list = []


def _bars(closes, first_index: int = 0) -> list[Bar]:
    out = []
    for offset, close in enumerate(closes):
        price = Price.from_str(f"{close:.2f}")
        ts = T0 + (first_index + offset + 1) * MINUTE
        out.append(Bar(BAR_TYPE, price, price, price, price, Quantity.from_int(1_000), ts, ts))
    return out


def _engine() -> BacktestEngine:
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    engine.add_venue(
        Venue("NASDAQ"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(10_000_000, USD)],
        base_currency=USD,
    )
    engine.add_instrument(AAPL)
    return engine


def _fills(engine: BacktestEngine) -> list[tuple]:
    return sorted(
        (o.ts_last, o.side_string(), str(o.filled_qty), f"{o.avg_px:.2f}")
        for o in engine.cache.orders()
        if o.filled_qty.as_double() > 0
    )


def _crossover() -> SMACrossover:
    return SMACrossover(
        SMAConfig(instrument_id=AAPL.id, bar_type=BAR_TYPE, fast_period=3, slow_period=5)
    )


def _momentum() -> SMAMomentum:
    return SMAMomentum(
        SMAMomentumConfig(
            instrument_id=AAPL.id,
            bar_type=BAR_TYPE,
            trade_size=Decimal("10"),
            order_id_tag="002",
            fast_period=3,
            slow_period=5,
        )
    )


def _run(strategy, data: list[Bar]) -> list[tuple]:
    engine = _engine()
    engine.add_data(data)
    engine.add_strategy(strategy)
    engine.run()
    fills = _fills(engine)
    engine.dispose()
    return fills


def _reference_crossovers(closes, fast: int, slow: int) -> list[tuple[int, str]]:
    """Pure-Python golden and death crosses, independent of Nautilus."""
    crosses = []
    prev = None
    for i in range(slow - 1, len(closes)):
        f = sum(closes[i - fast + 1 : i + 1]) / fast
        s = sum(closes[i - slow + 1 : i + 1]) / slow
        if prev is not None:
            if prev[0] <= prev[1] and f > s:
                crosses.append((T0 + (i + 1) * MINUTE, "UP"))
            elif prev[0] >= prev[1] and f < s:
                crosses.append((T0 + (i + 1) * MINUTE, "DOWN"))
        prev = (f, s)
    return crosses


class TestSmaCrossoverIsUnchanged:
    def test_the_fills_are_byte_identical_to_the_pre_change_code(self):
        assert _run(_crossover(), _bars(CLOSES)) == CROSSOVER_BEFORE


class TestMomentumChangedDeliberately:
    """D-G, ruled A by the PO on 2026-09-22 — the one disclosed AC #3 exception."""

    def test_before_this_story_momentum_could_not_trade(self):
        """The record, not a live assertion: nothing re-runs the deleted code."""
        assert MOMENTUM_BEFORE == []
        assert len(_reference_crossovers(CLOSES, 3, 5)) > 10

    def test_momentum_now_trades_every_reference_crossover_long_only(self):
        """Long-only default: buy on each golden cross while flat, sell on the
        next death cross while long. Market orders fill on the bar that
        signalled, so fill timestamps are the reference crossover timestamps."""
        fills = _run(_momentum(), _bars(CLOSES))

        expected = []
        long = False
        for ts, direction in _reference_crossovers(CLOSES, 3, 5):
            if direction == "UP" and not long:
                expected.append((ts, "BUY"))
                long = True
            elif direction == "DOWN" and long:
                expected.append((ts, "SELL"))
                long = False
        assert [(ts, side) for ts, side, _qty, _px in fills] == expected
        assert {qty for _ts, _side, qty, _px in fills} == {"10"}


def _decision_trace(strategy, prev_names: tuple[str, str]) -> list[tuple]:
    """Record, at each ``on_bar``, everything a signal decision reads."""
    trace: list[tuple] = []
    base = strategy.on_bar

    def on_bar(bar):
        fast, slow = strategy.registered_indicators
        trace.append(
            (
                bar.ts_event,
                fast.initialized and round(fast.value, 9),
                slow.initialized and round(slow.value, 9),
                getattr(strategy, prev_names[0]),
                getattr(strategy, prev_names[1]),
            )
        )
        return base(bar)

    strategy.on_bar = on_bar
    return trace


@pytest.mark.parametrize(
    ("make", "prev_names"),
    [
        pytest.param(_crossover, ("_prev_fast_sma", "_prev_slow_sma"), id="sma_crossover"),
        pytest.param(_momentum, ("_prev_fast", "_prev_slow"), id="momentum"),
    ],
)
class TestWarmIsTheSameAsHavingRunThroughTheHistory:
    """AC #3's "explained solely by indicators being warm at the first traded bar"."""

    SPLIT = 120

    def test_a_catalog_warmed_strategy_decides_exactly_like_a_continuous_one(
        self, make, prev_names, tmp_path
    ):
        history, live = _bars(CLOSES[: self.SPLIT]), _bars(CLOSES[self.SPLIT :], self.SPLIT)

        continuous = make()
        continuous_trace = _decision_trace(continuous, prev_names)
        _run(continuous, history + live)

        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([AAPL])
        catalog.write_data(history)
        engine = _engine()
        engine.add_data(live)
        engine.kernel.data_engine.register_catalog(catalog)
        warmed = make()
        warmed_trace = _decision_trace(warmed, prev_names)
        engine.add_strategy(warmed)
        engine.run()
        engine.dispose()

        live_start = live[0].ts_event
        assert warmed_trace, "the warmed strategy saw no live bar"
        # Warm at the very first live bar — the whole point of the story.
        assert warmed_trace[0][1] is not False and warmed_trace[0][3] is not None
        assert warmed_trace == [row for row in continuous_trace if row[0] >= live_start]
