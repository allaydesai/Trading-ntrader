"""The Connors RSI buy threshold is on Nautilus's 0–1 RSI scale.

Found by the research MCP's phase 4 exit check (2026-10-13): ``buy_threshold`` defaulted
to 10.0 and validated 0–100, but ``RelativeStrengthIndex.value`` is 0–1, so ``rsi < 10``
was always true and every grid cell of the ``crsi-qqq`` studies traded identically.
The parameter now follows ``ApoloRSIParameters``: 0.10 means the classic RSI < 10.

``on_bar`` is driven unbound against a duck-typed ``self`` (the pattern of
``test_sma_crossover_position_sizing``): Nautilus Strategy attributes live in Cython slots
that cannot be set on a bare instance, and the signal logic only reads them.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Price, Quantity
from pydantic import ValidationError

# The strategy lives in the ``custom/`` git submodule, which CI does not check out
# (private repo, SSH URL): skip there rather than fail at collection. Resolving it at
# runtime also keeps ruff's import sorting the same whether or not the files exist.
_strategy_module = pytest.importorskip(
    "src.core.strategies.custom.larry_connors_RSI_mean_rev",
    reason="the strategies submodule is not checked out",
)
_models_module = pytest.importorskip(
    "src.core.strategies.custom.models", reason="the strategies submodule is not checked out"
)
LarryConnorsRSIMeanRev = _strategy_module.LarryConnorsRSIMeanRev
ConnorsRSIParameters = _models_module.ConnorsRSIParameters

BAR_TYPE = BarType.from_str("QQQ.NASDAQ-1-DAY-LAST-EXTERNAL")


def _bar(close: float = 150.0) -> Bar:
    return Bar(
        bar_type=BAR_TYPE,
        open=Price(close, precision=2),
        high=Price(close + 1.0, precision=2),
        low=Price(close - 1.0, precision=2),
        close=Price(close, precision=2),
        volume=Quantity(1_000_000, precision=0),
        ts_event=0,
        ts_init=0,
    )


def _indicator(value: float) -> SimpleNamespace:
    return SimpleNamespace(initialized=True, value=value, handle_bar=lambda bar: None)


def _flat_self(*, rsi: float, buy_threshold: float = 0.10) -> SimpleNamespace:
    """A warmed-up, flat strategy in an uptrend (close 150 over a 100 SMA)."""
    cache = MagicMock()
    cache.positions.return_value = []
    return SimpleNamespace(
        config=SimpleNamespace(
            bar_type=BAR_TYPE,
            buy_threshold=buy_threshold,
            max_holding_days=5,
            trade_size=Decimal("100"),
        ),
        instrument_id=BAR_TYPE.instrument_id,
        instrument=MagicMock(),
        rsi=_indicator(rsi),
        sma=_indicator(100.0),
        cache=cache,
        order_factory=MagicMock(),
        submit_order=MagicMock(),
        close_position=MagicMock(),
        log=MagicMock(),
        _prev_high=None,
        _bars_held=0,
    )


@pytest.mark.unit
class TestTheThresholdIsOnTheIndicatorsScale:
    def test_the_default_is_the_classic_rsi_below_ten(self):
        assert ConnorsRSIParameters().buy_threshold == pytest.approx(0.10)

    @pytest.mark.parametrize("threshold", [5.0, 10.0, 15.0, 1.5])
    def test_a_classic_scale_value_is_refused_not_silently_always_true(self, threshold):
        # pydantic's ``le=1.0`` bound speaks first; the validator's wording backs it up.
        with pytest.raises(ValidationError, match=r"less than or equal to 1|0\.0 and 1\.0"):
            ConnorsRSIParameters(buy_threshold=threshold)

    @pytest.mark.parametrize("threshold", [0.0, 0.05, 0.15, 1.0])
    def test_the_indicators_range_is_accepted(self, threshold):
        assert ConnorsRSIParameters(buy_threshold=threshold).buy_threshold == threshold


@pytest.mark.unit
class TestTheBuySignal:
    def test_a_washout_buys(self):
        strategy = _flat_self(rsi=0.05)

        LarryConnorsRSIMeanRev.on_bar(strategy, _bar())

        strategy.order_factory.market.assert_called_once()
        assert strategy.order_factory.market.call_args.kwargs["order_side"] == OrderSide.BUY
        strategy.submit_order.assert_called_once()

    @pytest.mark.parametrize("rsi", [0.10, 0.50, 0.95])
    def test_an_rsi_at_or_over_the_threshold_does_not_buy(self, rsi):
        strategy = _flat_self(rsi=rsi)

        LarryConnorsRSIMeanRev.on_bar(strategy, _bar())

        strategy.submit_order.assert_not_called()

    def test_the_threshold_moves_the_signal(self):
        strategy = _flat_self(rsi=0.12, buy_threshold=0.15)

        LarryConnorsRSIMeanRev.on_bar(strategy, _bar())

        strategy.submit_order.assert_called_once()
