"""FX M6 (task 6.4): the submodule strategies on real FirstRate daily FX bars.

Through the orchestrator's production venue wiring (IB spot costs, 3% margin gate):

- ``bollinger_reversal`` on trimmed real EURUSD daily bars places brackets at 5 dp that the
  RiskEngine accepts (2-dp brackets were denied on every FX order) and trades.
- ``riptide`` on trimmed real USDJPY daily bars sizes every entry at its USD notional
  (10% of USD 1M = 100,000 units, not the ~690 of ``notional / price``), stops at 3 dp.

The bars come from the FirstRate FX parser (daily rows stamped at the 17:00 New York open),
excerpts of ``/srv/market-data/raw/fx`` under ``tests/fixtures/firstrate_fx/strategies/``.
The strategies live in the private submodule, so the tests skip without it (CI). Run with
``--forked`` (C extensions).
"""

from __future__ import annotations

from decimal import Decimal
from itertools import chain
from pathlib import Path

import pandas as pd
import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, OrderStatus, OrderType
from nautilus_trader.model.instruments import CurrencyPair

from src.models.backtest_request import BacktestRequest
from src.services.firstrate.parsers.firstrate_fx_parser import FirstRateFxParser
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "firstrate_fx" / "strategies"
CUSTOM = "src.core.strategies.custom"


def _daily_bars(pair: CurrencyPair, name: str) -> list[Bar]:
    bar_type = BarType.from_str(f"{pair.id}-1-DAY-MID-EXTERNAL")
    chunks = FirstRateFxParser().iter_chunks(FIXTURES / name, bar_type)
    return list(chain.from_iterable(chunk.bars for chunk in chunks))


def _request(
    strategy: str, module: str, cls: str, pair: CurrencyPair, bars, config
) -> BacktestRequest:
    return BacktestRequest(
        strategy_type=strategy,
        strategy_path=f"{CUSTOM}.{module}:{cls}",
        config_path=f"{CUSTOM}.{module}:{cls.removesuffix('Strategy')}Config",
        strategy_config={"portfolio_value": Decimal("1000000"), **config},
        symbol=f"{pair.base_currency.code}{pair.quote_currency.code}",
        instrument_id=str(pair.id),
        start_date=pd.Timestamp(bars[0].ts_event, tz="UTC").to_pydatetime(),
        end_date=pd.Timestamp(bars[-1].ts_event, tz="UTC").to_pydatetime(),
        bar_type="1-DAY-MID",
        persist=False,
        starting_balance=Decimal("1000000"),
    )


async def _run(request: BacktestRequest, bars: list[Bar], pair: CurrencyPair):
    from src.core.backtest_orchestrator import BacktestOrchestrator

    orchestrator = BacktestOrchestrator()
    try:
        result, _ = await orchestrator.execute(request, bars, pair)
        cache = orchestrator.engine.cache
        return result, list(cache.orders()), list(cache.positions())
    finally:
        orchestrator.dispose()


async def test_bollinger_reversal_eurusd_brackets_are_accepted_and_trade() -> None:
    pytest.importorskip(f"{CUSTOM}.bollinger_reversal")
    pair = build_fx_pair(get_pair_spec("EURUSD"))
    bars = _daily_bars(pair, "EURUSD_2023-07_2024-03_1day.txt")
    config = {"weekly_ma_period": 4, "weekly_ma_tolerance_pct": 0.05}
    request = _request(
        "bollinger_reversal", "bollinger_reversal", "BollingerReversalStrategy", pair, bars, config
    )

    result, orders, positions = await _run(request, bars, pair)

    assert not [o for o in orders if o.status == OrderStatus.DENIED]
    stops = [o for o in orders if o.order_type == OrderType.STOP_MARKET]
    targets = [o for o in orders if o.order_type == OrderType.LIMIT]
    assert stops and targets, "bollinger_reversal should place brackets on this excerpt"
    assert {o.trigger_price.precision for o in stops} == {5}
    assert {o.price.precision for o in targets} == {5}
    assert any(p.is_closed for p in positions)
    assert result.total_trades >= 1


async def test_riptide_usdjpy_entries_are_sized_at_usd_notional() -> None:
    pytest.importorskip(f"{CUSTOM}.riptide")
    pair = build_fx_pair(get_pair_spec("USDJPY"))
    bars = _daily_bars(pair, "USDJPY_2023-01_2023-09_1day.txt")
    config = {
        "position_size_pct": Decimal("10"),  # USD 100,000 an entry
        "sma_trend_period": 50,
        "pullback_period": 5,
        "limit_offset_pct": Decimal("0.3"),  # 2% under a USDJPY daily close rarely fills
        "min_adv_dollars": 0,
    }
    request = _request("riptide", "riptide", "Riptide", pair, bars, config)

    result, orders, positions = await _run(request, bars, pair)

    assert not [o for o in orders if o.status == OrderStatus.DENIED]
    entries = [o for o in orders if o.order_type == OrderType.LIMIT and o.side == OrderSide.BUY]
    assert entries, "riptide should find a setup on this excerpt"
    assert {o.quantity.as_decimal() for o in entries} == {Decimal(100_000)}
    assert {o.price.precision for o in entries} == {3}
    stops = [o for o in orders if o.order_type == OrderType.STOP_MARKET]
    assert stops and {o.trigger_price.precision for o in stops} == {3}
    assert any(p.is_closed for p in positions)
    assert result.total_trades >= 1
