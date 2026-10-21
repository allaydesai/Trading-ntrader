"""Two registered strategies on one ``BacktestEngine`` with one account (phase 5b spike).

Research MCP phase 5b owes an engine-backed multi-strategy backtest. Before it is
designed, this probe turns three guesses into measurements:

1. **Sizing** — what each strategy does with shared cash (both built-ins size from
   fixed config, never from the account).
2. **Limits** — what Nautilus's ``RiskEngine`` enforces per account type on bar-only
   data (MARGIN skips every balance check; CASH may skip MARKET orders with no tick).
3. **Storage** — whether the positions / fills / orders reports split cleanly by
   ``strategy_id`` and which per-strategy figures are cheap to derive.

Strategy A is ``sma_crossover`` sized to the whole balance on every entry; strategy B
is ``momentum`` sized at runtime to ~60% of the balance in shares, so combined demand
is ~160% of cash whatever the price level.

One engine per process (CLAUDE.md gotcha 4; ``FillModel`` seeds the global ``random``)::

    uv run python -m scripts.diagnostics.multi_strategy_spike --scenario S1
    uv run python -m scripts.diagnostics.multi_strategy_spike --scenario all   # 4 subprocesses

Scenarios::

    S1  HEDGING + MARGIN   AAPL, both strategies   today's venue; split check, no competition
    S2  HEDGING + CASH     AAPL, both strategies   does the RiskEngine deny, or nothing?
    S3  NETTING + MARGIN   AAPL, both strategies   positions still split per strategy?
    S4  HEDGING + CASH     A on AAPL, B on MSFT    cross-instrument cash competition

Each run writes ``<out>/<scenario>.json`` and the engine's WARNING-level log beside it.
No database is touched: bars come straight from the named catalog directory.
"""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import click
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType, OrderStatus
from nautilus_trader.model.identifiers import InstrumentId, TraderId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading.strategy import Strategy

from src.config import CatalogSettings, get_settings
from src.core.fee_models import IBKRCommissionModel
from src.core.fill_model import make_fill_model
from src.core.results_extractor import ResultsExtractor, _Ledger, metrics_basis
from src.core.strategy_factory import StrategyLoader
from src.core.strategy_registry import StrategyRegistry
from src.services.data_catalog import DataCatalogService
from src.services.firstrate.backtest_loader import build_equity

RISK_SKIP_TEXT = "Cannot check MARKET order risk"
MODULE = "scripts.diagnostics.multi_strategy_spike"
TAG_A = "A"
TAG_B = "B"


@dataclass(frozen=True)
class Scenario:
    name: str
    oms: OmsType
    account: AccountType
    second_instrument: bool
    question: str


SCENARIOS: dict[str, Scenario] = {
    "S1": Scenario(
        "S1", OmsType.HEDGING, AccountType.MARGIN, False, "split by strategy_id; no competition"
    ),
    "S2": Scenario(
        "S2", OmsType.HEDGING, AccountType.CASH, False, "does the RiskEngine deny on bars?"
    ),
    "S3": Scenario(
        "S3", OmsType.NETTING, AccountType.MARGIN, False, "do positions still split under NETTING?"
    ),
    "S4": Scenario(
        "S4", OmsType.HEDGING, AccountType.CASH, True, "cross-instrument cash competition"
    ),
}


@dataclass(frozen=True)
class Leg:
    """One instrument's bars and synthesised instrument."""

    instrument: Instrument
    bars: list[Bar]


def _load_leg(
    catalog_dir: Path, symbol: str, timeframe: str, start: datetime, end: datetime
) -> Leg:
    nautilus_id = f"{symbol}.NASDAQ"
    service = DataCatalogService(catalog_path=catalog_dir)
    bars = service.query_bars(nautilus_id, start, end, timeframe)
    if not bars:
        raise click.ClickException(f"no {timeframe} bars for {nautilus_id} in {catalog_dir}")
    return Leg(build_equity(nautilus_id=nautilus_id, ticker=symbol, bars=bars), bars)


def _build_strategy(name: str, leg: Leg, tag: str, overrides: dict[str, Any]) -> Strategy:
    """The orchestrator's ``_create_strategy`` path, with an explicit ``order_id_tag``."""
    StrategyRegistry.discover()
    canonical = StrategyRegistry.get(name).name
    params = StrategyLoader.build_strategy_params(
        strategy_type=canonical, overrides=overrides, settings=get_settings()
    )
    params.update(
        {"instrument_id": leg.instrument.id, "bar_type": leg.bars[0].bar_type, "order_id_tag": tag}
    )
    return StrategyLoader.create_strategy(canonical, params)


def _logging(out: Path, scenario: str, verbose: bool) -> LoggingConfig:
    return LoggingConfig(
        log_level="INFO" if verbose else "WARNING",
        log_level_file="WARNING",
        log_directory=str(out),
        log_file_name=f"{scenario}_nautilus",
        log_colors=False,
        clear_log_file=True,
    )


def _build_engine(scenario: Scenario, legs: list[Leg], balance: int, logging: LoggingConfig):
    settings = get_settings()
    engine = BacktestEngine(
        config=BacktestEngineConfig(trader_id=TraderId("SPIKE-001"), logging=logging)
    )
    venue = legs[0].instrument.id.venue
    engine.add_venue(
        venue=venue,
        oms_type=scenario.oms,
        account_type=scenario.account,
        starting_balances=[Money(float(balance), USD)],
        fill_model=make_fill_model(42),
        fee_model=IBKRCommissionModel(
            commission_per_share=settings.commission_per_share,
            min_per_order=settings.commission_min_per_order,
            max_rate=settings.commission_max_rate,
        ),
    )
    for leg in legs:
        engine.add_instrument(leg.instrument)
    for leg in legs:
        engine.add_data(leg.bars)
    return engine, venue


def _add_strategies(engine: BacktestEngine, legs: list[Leg], balance: int) -> list[Strategy]:
    leg_a, leg_b = legs[0], legs[-1]
    first_close = float(leg_b.bars[0].close)
    shares_b = max(1, math.floor(0.6 * balance / first_close))
    strategy_a = _build_strategy(
        "sma_crossover",
        leg_a,
        TAG_A,
        {"portfolio_value": Decimal(balance), "position_size_pct": Decimal(100)},
    )
    strategy_b = _build_strategy("momentum", leg_b, TAG_B, {"trade_size": Decimal(shares_b)})
    engine.add_strategy(strategy_a)
    engine.add_strategy(strategy_b)
    return [strategy_a, strategy_b]


def _by_strategy(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or "strategy_id" not in frame:
        return {}
    return {str(k): int(v) for k, v in frame["strategy_id"].fillna("").value_counts().items()}


def _positions_section(engine: BacktestEngine) -> dict[str, Any]:
    """Positions and fills reports: the split, and whether every position's opener agrees."""
    positions = engine.trader.generate_positions_report()
    fills = engine.trader.generate_order_fills_report()
    section: dict[str, Any] = {
        "rows": int(len(positions)),
        "by_strategy": _by_strategy(positions),
        "unattributed": 0,
        "snapshots": 0,
        "opener_mismatches": [],
        "fills_rows": int(len(fills)),
        "fills_by_strategy": _by_strategy(fills),
        "position_ids_sample": [str(i) for i in list(positions.index)[:4]],
    }
    if positions.empty:
        return section
    section["unattributed"] = int((positions["strategy_id"].fillna("") == "").sum())
    if "is_snapshot" in positions:
        section["snapshots"] = int(positions["is_snapshot"].astype(bool).sum())
    if "side" in positions:
        sides = positions.groupby(["strategy_id", "side"]).size()
        section["sides_by_strategy"] = {f"{k[0]}:{k[1]}": int(v) for k, v in sides.items()}
    for position_id, row in positions.iterrows():
        opener = str(row.get("opening_order_id"))
        if opener in fills.index:
            opener_strategy = str(fills.loc[opener, "strategy_id"])
            if opener_strategy != str(row["strategy_id"]):
                section["opener_mismatches"].append(
                    {
                        "position": str(position_id),
                        "position_strategy": str(row["strategy_id"]),
                        "opener_strategy": opener_strategy,
                    }
                )
    return section


def _orders_section(engine: BacktestEngine) -> dict[str, Any]:
    counts: dict[str, int] = {}
    denied: list[dict[str, str]] = []
    for order in engine.cache.orders():
        key = f"{order.strategy_id}:{order.status.name}"
        counts[key] = counts.get(key, 0) + 1
        if order.status == OrderStatus.DENIED:
            denied.append(
                {
                    "order": str(order.client_order_id),
                    "strategy": str(order.strategy_id),
                    "reason": str(getattr(order.last_event, "reason", "")),
                }
            )
    return {"by_strategy_status": dict(sorted(counts.items())), "denied": denied}


def _money_sum(values: list[Money]) -> float:
    return float(sum((v.as_double() for v in values if v.currency == USD), 0.0))


def _strategy_section(engine: BacktestEngine, strategy: Strategy, balance: int) -> dict[str, Any]:
    """Per-strategy figures: cache filters, then the analyzer on that strategy's legs alone."""
    closed = engine.cache.positions_closed(strategy_id=strategy.id)
    open_ = engine.cache.positions_open(strategy_id=strategy.id)
    legs = [
        copy.deepcopy(p)
        for p in [*engine.cache.positions(), *engine.cache.position_snapshots()]
        if p.strategy_id == strategy.id
    ]
    section: dict[str, Any] = {
        "closed_positions": len(closed),
        "open_positions": len(open_),
        "realized_pnl": _money_sum([p.realized_pnl for p in closed]),
        "commissions": _money_sum([c for p in closed for c in p.commissions()]),
        "peak_qty_max": max((float(p.peak_qty) for p in closed), default=0.0),
    }
    if legs:
        analyzer = copy.deepcopy(engine.portfolio.analyzer)
        analyzer.calculate_statistics(_Ledger.over(legs, Decimal(balance)), legs)
        section["analyzer_returns"] = {
            k: _jsonable(v) for k, v in analyzer.get_performance_stats_returns().items()
        }
        section["analyzer_pnls"] = {
            k: _jsonable(v) for k, v in analyzer.get_performance_stats_pnls(USD).items()
        }
    return section


def _account_section(engine: BacktestEngine, venue: Venue, balance: int) -> dict[str, Any]:
    account = engine.cache.account_for_venue(venue)
    report = engine.trader.generate_account_report(venue)
    free_seen = [float(v) for v in report["free"]] if "free" in report else []
    all_closed = engine.cache.positions_closed()
    realized = _money_sum([p.realized_pnl for p in all_closed])
    final_total = account.balance_total(USD).as_double()
    return {
        "type": account.type.name,
        "final_total": final_total,
        "final_free": account.balance_free(USD).as_double(),
        "min_free_seen": min(free_seen) if free_seen else None,
        "balance_rows": int(len(report)),
        "realized_pnl_all_closed": realized,
        "account_delta": final_total - balance,
        "delta_minus_realized": round(final_total - balance - realized, 2),
        "open_positions_at_end": len(engine.cache.positions_open()),
    }


def _combined_section(
    engine: BacktestEngine, venue: Venue, legs: list[Leg], balance: int, start, end
) -> dict[str, Any]:
    bars_by_instrument: dict[InstrumentId, list[Bar]] = {
        leg.instrument.id: leg.bars for leg in legs
    }
    extractor = ResultsExtractor(
        engine=engine,
        venue=venue,
        starting_balance=float(balance),
        bars_by_instrument=bars_by_instrument,
    )
    result = extractor.extract_results(start, end)
    curve = extractor.extract_equity_curve(start, end)
    return {
        "metrics_basis": metrics_basis(extractor),
        "total_trades": _jsonable(result.total_trades),
        "total_pnl": _jsonable(result.total_pnl),
        "final_balance": _jsonable(result.final_balance),
        "sharpe_ratio": _jsonable(result.sharpe_ratio),
        "max_drawdown": _jsonable(result.max_drawdown),
        "equity_points": len(curve),
        "equity_first": curve[0]["value"] if curve else None,
        "equity_last": curve[-1]["value"] if curve else None,
    }


def _log_section(out: Path, scenario: str) -> dict[str, Any]:
    files = sorted(out.glob(f"{scenario}_nautilus*"))
    text = "\n".join(f.read_text(errors="replace") for f in files)
    return {
        "files": [f.name for f in files],
        "risk_skips": text.count(RISK_SKIP_TEXT),
        "denied_lines": text.count("DENIED"),
        "warning_lines": sum(1 for line in text.splitlines() if "WARN" in line),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Money):
        return value.as_double()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value


def _run_scenario(
    scenario: Scenario, legs: list[Leg], balance: int, out: Path, verbose: bool, start, end
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "scenario": scenario.name,
        "question": scenario.question,
        "oms": scenario.oms.name,
        "account_type": scenario.account.name,
        "instruments": [str(leg.instrument.id) for leg in legs],
        "bars": [len(leg.bars) for leg in legs],
        "starting_balance": balance,
        "first_close": [float(leg.bars[0].close) for leg in legs],
        "error": None,
    }
    engine, venue = _build_engine(scenario, legs, balance, _logging(out, scenario.name, verbose))
    try:
        strategies = _add_strategies(engine, legs, balance)
        report["strategy_ids"] = [str(s.id) for s in strategies]
        report["shares_b"] = int(strategies[1].config.trade_size)
        try:
            engine.run()
        except Exception as exc:  # the exception is the finding
            report["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "trace": traceback.format_exc().splitlines()[-3:],
            }
        report["positions"] = _positions_section(engine)
        report["orders"] = _orders_section(engine)
        report["account"] = _account_section(engine, venue, balance)
        report["per_strategy"] = {
            str(s.id): _strategy_section(engine, s, balance) for s in strategies
        }
        if report["error"] is None:
            report["combined"] = _combined_section(engine, venue, legs, balance, start, end)
    finally:
        engine.dispose()
    report["log"] = _log_section(out, scenario.name)
    return report


def _print_summary(report: dict[str, Any]) -> None:
    click.echo(
        f"== {report['scenario']} {report['oms']}/{report['account_type']}: {report['question']}"
    )
    click.echo(f"   strategies {report.get('strategy_ids')}  shares_b={report.get('shares_b')}")
    if report["error"]:
        click.echo(f"   RUN RAISED {report['error']['type']}: {report['error']['message']}")
    pos = report["positions"]
    click.echo(
        f"   positions {pos['rows']} by strategy {pos['by_strategy']} "
        f"unattributed={pos['unattributed']} snapshots={pos['snapshots']} "
        f"opener_mismatches={len(pos['opener_mismatches'])}"
    )
    click.echo(f"   sides {pos.get('sides_by_strategy')}")
    click.echo(f"   orders {report['orders']['by_strategy_status']}")
    for d in report["orders"]["denied"][:5]:
        click.echo(f"     denied {d['strategy']}: {d['reason']}")
    acct = report["account"]
    click.echo(
        f"   account final={acct['final_total']:.2f} min_free={acct['min_free_seen']} "
        f"delta-realized={acct['delta_minus_realized']} open_at_end="
        f"{acct['open_positions_at_end']}"
    )
    for sid, s in report["per_strategy"].items():
        click.echo(
            f"   {sid}: closed={s['closed_positions']} pnl={s['realized_pnl']:.2f} "
            f"peak_qty={s['peak_qty_max']} "
            f"sharpe={s.get('analyzer_returns', {}).get('Sharpe Ratio (252 days)')}"
        )
    if "combined" in report:
        c = report["combined"]
        click.echo(
            f"   combined basis={c['metrics_basis']} trades={c['total_trades']} "
            f"pnl={c['total_pnl']} sharpe={c['sharpe_ratio']} dd={c['max_drawdown']}"
        )
    click.echo(
        f"   log risk_skips={report['log']['risk_skips']} "
        f"denied_lines={report['log']['denied_lines']}"
    )


@click.command()
@click.option("--scenario", type=click.Choice([*SCENARIOS, "all"]), required=True)
@click.option("--out", type=click.Path(path_type=Path), default=Path("data/spike/multi_strategy"))
@click.option("--catalog", default="e2e-test", show_default=True)
@click.option("--symbol", default="AAPL", show_default=True)
@click.option("--second-symbol", default="MSFT", show_default=True)
@click.option("--timeframe", default="1-DAY-LAST", show_default=True)
@click.option("--start", type=click.DateTime(["%Y-%m-%d"]), default="2018-01-01")
@click.option("--end", type=click.DateTime(["%Y-%m-%d"]), default="2019-12-31")
@click.option("--balance", type=int, default=20_000, show_default=True)
@click.option("--verbose", is_flag=True, help="Nautilus INFO logging on the console.")
def main(scenario, out, catalog, symbol, second_symbol, timeframe, start, end, balance, verbose):
    """Run one scenario in this process, or every scenario in its own subprocess."""
    out.mkdir(parents=True, exist_ok=True)
    if scenario == "all":
        passthrough = sys.argv[1:]
        for name in SCENARIOS:
            args = [a if a != "all" else name for a in passthrough]
            subprocess.run([sys.executable, "-m", MODULE, *args], check=False)
        return
    start = start.replace(tzinfo=timezone.utc)
    end = end.replace(tzinfo=timezone.utc)
    chosen = SCENARIOS[scenario]
    catalog_dir = Path(CatalogSettings().catalog_base_path) / catalog
    legs = [_load_leg(catalog_dir, symbol, timeframe, start, end)]
    if chosen.second_instrument:
        legs.append(_load_leg(catalog_dir, second_symbol, timeframe, start, end))
    report = _run_scenario(chosen, legs, balance, out, verbose, start, end)
    (out / f"{scenario}.json").write_text(json.dumps(report, indent=2, default=str))
    _print_summary(report)


if __name__ == "__main__":
    main()
