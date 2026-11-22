"""Futures M4 end to end: ``contracts=1`` on real ES bars, then the reporting figures.

The orchestrator runs ``sma_crossover`` with a fixed 1-contract size on the trimmed real
ES 1-hour fixture (M3's fixture). The closed positions are turned into trade rows the way
persistence stores them, and ``summarise_run`` over the run's own ``futures_snapshot`` must
give 1 contract, one contract's margin as the peak and return on margin = net P&L / 27,500.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide

from src.core.futures_costs import futures_snapshot
from src.models.backtest_request import BacktestRequest
from src.services.firstrate.parsers.firstrate_futures_parser import FirstRateFuturesParser
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract
from src.services.futures.returns import summarise_run, trade_rows
from tests.integration.core.test_futures_margin_engine import FIXTURE

pytestmark = pytest.mark.integration

ES = get_contract_spec("ES")


def _trade_row(position) -> dict:
    """A closed position as a ``trades`` row (``backtest_persistence`` field semantics)."""
    return {
        "instrument_id": str(position.instrument_id),
        "order_side": "BUY" if position.entry == OrderSide.BUY else "SELL",
        "quantity": position.peak_qty.as_decimal(),
        "entry_price": Decimal(str(position.avg_px_open)),
        "exit_price": Decimal(str(position.avg_px_close)),
        "profit_loss": position.realized_pnl.as_decimal(),
        "entry_timestamp": position.ts_opened,
        "exit_timestamp": position.ts_closed,
    }


@pytest.mark.asyncio
async def test_fixed_one_contract_es_run_reports_return_on_margin():
    from src.core.backtest_orchestrator import BacktestOrchestrator

    es = build_futures_contract(ES)
    bars = FirstRateFuturesParser().parse_file(
        FIXTURE, es.id, BarType.from_str("ES.CME-1-HOUR-LAST-EXTERNAL")
    )
    request = BacktestRequest(
        strategy_type="sma_crossover",
        strategy_path="src.core.strategies.sma_crossover:SMACrossover",
        config_path="src.core.strategies.sma_crossover:SMAConfig",
        # $100K at 10% is far below one ES contract: without contracts=1 nothing trades.
        strategy_config={"fast_period": 3, "slow_period": 8, "contracts": 1},
        symbol="ES",
        instrument_id="ES.CME",
        start_date=datetime(2025, 3, 6, tzinfo=timezone.utc),
        end_date=datetime(2025, 3, 12, tzinfo=timezone.utc),
        bar_type="1-HOUR-LAST",
        persist=False,
        starting_balance=Decimal("100000"),
        catalog_name="firstrate-futures",
    )
    orchestrator = BacktestOrchestrator()
    try:
        await orchestrator.execute(request, bars, es)
        engine = orchestrator.engine
        assert engine is not None
        positions = engine.cache.positions()
        assert positions, "contracts=1 must trade the fixture week"
        assert {p.peak_qty.as_decimal() for p in positions} == {Decimal(1)}
        closed = [p for p in positions if p.is_closed]
        assert closed
        rows = [_trade_row(p) for p in closed]
    finally:
        orchestrator.dispose()

    snapshot = futures_snapshot([es])
    summary = summarise_run(snapshot, rows)
    net = sum(r["profit_loss"] for r in rows)
    assert summary.max_contracts == 1
    assert summary.closed_trades == len(rows)
    assert summary.peak_margin_usd == ES.margin.initial_usd
    assert summary.net_pnl_usd == net
    assert summary.return_on_margin_pct == net / ES.margin.initial_usd * 100
    for row, fields in zip(rows, trade_rows(snapshot, rows)):
        assert fields["contracts"] == "1"
        assert Decimal(fields["notional_usd"]) == (row["entry_price"] * 50).quantize(
            Decimal("0.01")
        )
        assert fields["margin_used_usd"] == "27500.00"
