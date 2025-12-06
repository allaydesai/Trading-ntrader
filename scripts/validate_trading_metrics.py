#!/usr/bin/env python3
"""
TC-DATA-003: Win Rate Calculation Validation
TC-DATA-004: Total Return Calculation Validation
TC-DATA-005: Trade P&L Calculation Validation
TC-DATA-006: CAGR (Annualized Return) Calculation Validation

Validates trading metrics calculations against database values.
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.db.models.backtest import BacktestRun
from src.db.models.trade import Trade


async def validate_win_rate(session: AsyncSession, run_id: str) -> dict:
    """
    TC-DATA-003: Win Rate Calculation

    Formula: win_rate = (winning_trades / total_trades) * 100
    Where winning_trades = trades with profit_loss > 0

    Returns:
        dict: Validation results with actual vs expected values
    """
    print("\n" + "=" * 80)
    print("TC-DATA-003: Win Rate Calculation Validation")
    print("=" * 80)

    # Get backtest run
    result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
    backtest = result.scalar_one_or_none()

    if not backtest:
        return {
            "test_case": "TC-DATA-003",
            "status": "FAILED",
            "error": f"Backtest {run_id} not found",
        }

    # Get performance metrics
    if not backtest.metrics:
        return {
            "test_case": "TC-DATA-003",
            "status": "FAILED",
            "error": "No performance metrics found for backtest",
        }

    # Get stored win rate from database (stored as decimal: 0.60 = 60%)
    stored_win_rate_decimal = backtest.metrics.win_rate
    stored_win_rate = float(stored_win_rate_decimal) * 100 if stored_win_rate_decimal else 0

    # Calculate win rate from trades
    result = await session.execute(select(Trade).where(Trade.backtest_run_id == backtest.id))
    trades = result.scalars().all()

    if not trades:
        return {
            "test_case": "TC-DATA-003",
            "status": "FAILED",
            "error": "No trades found for backtest",
        }

    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
    calculated_win_rate = (winning_trades / total_trades) * 100 if total_trades > 0 else 0

    # Compare values (allow 0.01% tolerance for rounding)
    tolerance = Decimal("0.01")
    match = abs(Decimal(str(calculated_win_rate)) - Decimal(str(stored_win_rate))) <= tolerance

    print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
    print(f"Run ID: {run_id}")
    print("\nTrade Analysis:")
    print(f"  Total Trades: {total_trades}")
    print(f"  Winning Trades: {winning_trades}")
    print(f"  Losing Trades: {total_trades - winning_trades}")
    print("\nWin Rate Calculation:")
    print("  Formula: (winning_trades / total_trades) * 100")
    print(f"  Calculation: ({winning_trades} / {total_trades}) * 100")
    print(f"  Calculated Win Rate: {calculated_win_rate:.2f}%")
    print(f"  Stored Win Rate: {stored_win_rate:.2f}%")
    print(f"  Difference: {abs(calculated_win_rate - stored_win_rate):.4f}%")
    print(f"  Tolerance: {tolerance}%")

    if match:
        print("\n✅ PASSED: Win rate calculation is accurate")
    else:
        print("\n❌ FAILED: Win rate mismatch exceeds tolerance")

    return {
        "test_case": "TC-DATA-003",
        "status": "PASSED" if match else "FAILED",
        "backtest_id": backtest.id,
        "run_id": run_id,
        "strategy": backtest.strategy_name,
        "instrument": backtest.instrument_symbol,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "calculated_win_rate": float(calculated_win_rate),
        "stored_win_rate": float(stored_win_rate),
        "difference": abs(calculated_win_rate - stored_win_rate),
        "tolerance": float(tolerance),
        "match": match,
    }


async def validate_total_return(session: AsyncSession, run_id: str) -> dict:
    """
    TC-DATA-004: Total Return Calculation

    Formula: total_return = ((final_equity - initial_capital) / initial_capital) * 100

    Returns:
        dict: Validation results with actual vs expected values
    """
    print("\n" + "=" * 80)
    print("TC-DATA-004: Total Return Calculation Validation")
    print("=" * 80)

    # Get backtest run
    result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
    backtest = result.scalar_one_or_none()

    if not backtest:
        return {
            "test_case": "TC-DATA-004",
            "status": "FAILED",
            "error": f"Backtest {run_id} not found",
        }

    # Get performance metrics
    if not backtest.metrics:
        return {
            "test_case": "TC-DATA-004",
            "status": "FAILED",
            "error": "No performance metrics found for backtest",
        }

    # Get stored total return (stored as decimal: 0.25 = 25%)
    stored_total_return_decimal = backtest.metrics.total_return
    stored_total_return = (
        float(stored_total_return_decimal) * 100 if stored_total_return_decimal else 0
    )

    # Get initial capital and final balance
    initial_capital = backtest.initial_capital
    final_balance = backtest.metrics.final_balance

    if not initial_capital or not final_balance:
        return {
            "test_case": "TC-DATA-004",
            "status": "FAILED",
            "error": "Missing initial_capital or final_balance",
        }

    # Calculate total return
    calculated_total_return = ((final_balance - initial_capital) / initial_capital) * 100

    # Compare values (allow 0.01% tolerance)
    tolerance = Decimal("0.01")
    match = (
        abs(Decimal(str(calculated_total_return)) - Decimal(str(stored_total_return))) <= tolerance
    )

    print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
    print(f"Run ID: {run_id}")
    print("\nEquity Analysis:")
    print(f"  Initial Capital: ${initial_capital:,.2f}")
    print(f"  Final Balance: ${final_balance:,.2f}")
    print(f"  Profit/Loss: ${final_balance - initial_capital:,.2f}")
    print("\nTotal Return Calculation:")
    print("  Formula: ((final_balance - initial_capital) / initial_capital) * 100")
    calc_str = f"(({final_balance:,.2f} - {initial_capital:,.2f}) / "
    calc_str += f"{initial_capital:,.2f}) * 100"
    print(f"  Calculation: {calc_str}")
    print(f"  Calculated Total Return: {calculated_total_return:.2f}%")
    print(f"  Stored Total Return: {stored_total_return:.2f}%")
    print(f"  Difference: {abs(float(calculated_total_return) - float(stored_total_return)):.4f}%")
    print(f"  Tolerance: {float(tolerance)}%")

    if match:
        print("\n✅ PASSED: Total return calculation is accurate")
    else:
        print("\n❌ FAILED: Total return mismatch exceeds tolerance")

    return {
        "test_case": "TC-DATA-004",
        "status": "PASSED" if match else "FAILED",
        "backtest_id": backtest.id,
        "run_id": run_id,
        "strategy": backtest.strategy_name,
        "instrument": backtest.instrument_symbol,
        "initial_capital": float(initial_capital),
        "final_balance": float(final_balance),
        "profit_loss": float(final_balance - initial_capital),
        "calculated_total_return": float(calculated_total_return),
        "stored_total_return": float(stored_total_return),
        "difference": float(
            abs(Decimal(str(calculated_total_return)) - Decimal(str(stored_total_return)))
        ),
        "tolerance": float(tolerance),
        "match": match,
    }


async def validate_trade_pnl(session: AsyncSession, run_id: str, trade_index: int = 0) -> dict:
    """
    TC-DATA-005: Trade P&L Calculation

    Formula: P&L = (exit_price - entry_price) * quantity - commissions

    Args:
        trade_index: Index of trade to validate (default: first trade)

    Returns:
        dict: Validation results with actual vs expected values
    """
    print("\n" + "=" * 80)
    print("TC-DATA-005: Trade P&L Calculation Validation")
    print("=" * 80)

    # Get backtest run
    result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
    backtest = result.scalar_one_or_none()

    if not backtest:
        return {
            "test_case": "TC-DATA-005",
            "status": "FAILED",
            "error": f"Backtest {run_id} not found",
        }

    # Get trades ordered by entry timestamp
    result = await session.execute(
        select(Trade).where(Trade.backtest_run_id == backtest.id).order_by(Trade.entry_timestamp)
    )
    trades = result.scalars().all()

    if not trades:
        return {
            "test_case": "TC-DATA-005",
            "status": "FAILED",
            "error": "No trades found for backtest",
        }

    if trade_index >= len(trades):
        return {
            "test_case": "TC-DATA-005",
            "status": "FAILED",
            "error": f"Trade index {trade_index} out of range (total trades: {len(trades)})",
        }

    trade = trades[trade_index]

    # Get stored P&L
    stored_pnl = trade.profit_loss

    # Calculate P&L
    if not all([trade.entry_price, trade.exit_price, trade.quantity]):
        return {
            "test_case": "TC-DATA-005",
            "status": "FAILED",
            "error": "Missing entry_price, exit_price, or quantity",
        }

    price_diff = trade.exit_price - trade.entry_price
    gross_pnl = price_diff * trade.quantity
    commission = trade.commission_amount or Decimal("0")
    calculated_pnl = gross_pnl - commission

    # Compare values (allow 0.01 tolerance for rounding)
    tolerance = Decimal("0.01")
    match = abs(calculated_pnl - stored_pnl) <= tolerance

    print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
    print(f"Run ID: {run_id}")
    print(f"Trade: {trade_index + 1} of {len(trades)}")
    print("\nTrade Details:")
    print(f"  Trade ID: {trade.trade_id}")
    print(f"  Side: {trade.order_side}")
    print(f"  Entry Time: {trade.entry_timestamp}")
    print(f"  Exit Time: {trade.exit_timestamp}")
    print(f"  Quantity: {trade.quantity}")
    print(f"  Entry Price: ${trade.entry_price:.2f}")
    print(f"  Exit Price: ${trade.exit_price:.2f}")
    print(f"  Commission: ${commission:.2f}")
    print("\nP&L Calculation:")
    print("  Formula: (exit_price - entry_price) * quantity - commissions")
    print(f"  Price Difference: ${price_diff:.2f}")
    print(f"  Gross P&L: ${gross_pnl:.2f}")
    print(f"  Commission: ${commission:.2f}")
    print(f"  Calculated P&L: ${calculated_pnl:.2f}")
    print(f"  Stored P&L: ${stored_pnl:.2f}")
    print(f"  Difference: ${float(abs(calculated_pnl - stored_pnl)):.4f}")
    print(f"  Tolerance: ${float(tolerance)}")

    if match:
        print("\n✅ PASSED: Trade P&L calculation is accurate")
    else:
        print("\n❌ FAILED: Trade P&L mismatch exceeds tolerance")

    return {
        "test_case": "TC-DATA-005",
        "status": "PASSED" if match else "FAILED",
        "backtest_id": backtest.id,
        "run_id": run_id,
        "strategy": backtest.strategy_name,
        "instrument": backtest.instrument_symbol,
        "trade_index": trade_index,
        "total_trades": len(trades),
        "trade_id": trade.trade_id,
        "side": trade.order_side,
        "quantity": float(trade.quantity),
        "entry_price": float(trade.entry_price),
        "exit_price": float(trade.exit_price),
        "commission": float(commission),
        "calculated_pnl": float(calculated_pnl),
        "stored_pnl": float(stored_pnl),
        "difference": float(abs(calculated_pnl - stored_pnl)),
        "tolerance": float(tolerance),
        "match": match,
    }


async def validate_cagr(session: AsyncSession, run_id: str) -> dict:
    """
    TC-DATA-006: CAGR (Annualized Return) Calculation

    Formula: CAGR = ((final_equity / initial_capital) ^ (1 / years)) - 1) * 100
    Where years = (end_date - start_date).days / 365.25

    Returns:
        dict: Validation results with actual vs expected values
    """
    print("\n" + "=" * 80)
    print("TC-DATA-006: CAGR (Annualized Return) Calculation Validation")
    print("=" * 80)

    # Get backtest run
    result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
    backtest = result.scalar_one_or_none()

    if not backtest:
        return {
            "test_case": "TC-DATA-006",
            "status": "FAILED",
            "error": f"Backtest {run_id} not found",
        }

    # Get performance metrics
    if not backtest.metrics:
        return {
            "test_case": "TC-DATA-006",
            "status": "FAILED",
            "error": "No performance metrics found for backtest",
        }

    # Get stored CAGR (stored as decimal: 0.25 = 25%)
    stored_cagr_decimal = backtest.metrics.cagr
    if not stored_cagr_decimal:
        return {
            "test_case": "TC-DATA-006",
            "status": "FAILED",
            "error": "No CAGR value found in performance metrics",
        }
    stored_cagr = float(stored_cagr_decimal) * 100

    # Get required values
    initial_capital = backtest.initial_capital
    final_balance = backtest.metrics.final_balance
    start_date = backtest.start_date
    end_date = backtest.end_date

    if not all([initial_capital, final_balance, start_date, end_date]):
        return {
            "test_case": "TC-DATA-006",
            "status": "FAILED",
            "error": (
                "Missing required values (initial_capital, final_balance, start_date, or end_date)"
            ),
        }

    # Calculate time period in years
    days = (end_date - start_date).days
    years = days / 365.25

    if years <= 0:
        return {
            "test_case": "TC-DATA-006",
            "status": "FAILED",
            "error": f"Invalid time period: {days} days",
        }

    # Calculate CAGR
    growth_factor = final_balance / initial_capital
    calculated_cagr = (pow(float(growth_factor), 1 / years) - 1) * 100

    # Compare values (allow 0.01% tolerance)
    tolerance = Decimal("0.01")
    match = abs(Decimal(str(calculated_cagr)) - Decimal(str(stored_cagr))) <= tolerance

    print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
    print(f"Run ID: {run_id}")
    print("\nTime Period:")
    print(f"  Start Date: {start_date}")
    print(f"  End Date: {end_date}")
    print(f"  Days: {days}")
    print(f"  Years: {years:.4f}")
    print("\nEquity Analysis:")
    print(f"  Initial Capital: ${initial_capital:,.2f}")
    print(f"  Final Balance: ${final_balance:,.2f}")
    print(f"  Growth Factor: {growth_factor:.6f}")
    print("\nCAGR Calculation:")
    print("  Formula: ((final_balance / initial_capital) ^ (1 / years)) - 1) * 100")
    calc_str = f"(({final_balance:,.2f} / {initial_capital:,.2f}) ^ "
    calc_str += f"(1 / {years:.4f})) - 1) * 100"
    print(f"  Calculation: {calc_str}")
    print(f"  Calculated CAGR: {calculated_cagr:.2f}%")
    print(f"  Stored CAGR: {stored_cagr:.2f}%")
    diff_val = float(abs(Decimal(str(calculated_cagr)) - Decimal(str(stored_cagr))))
    print(f"  Difference: {diff_val:.4f}%")
    print(f"  Tolerance: {float(tolerance)}%")

    if match:
        print("\n✅ PASSED: CAGR calculation is accurate")
    else:
        print("\n❌ FAILED: CAGR mismatch exceeds tolerance")

    return {
        "test_case": "TC-DATA-006",
        "status": "PASSED" if match else "FAILED",
        "backtest_id": backtest.id,
        "run_id": run_id,
        "strategy": backtest.strategy_name,
        "instrument": backtest.instrument_symbol,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "days": days,
        "years": years,
        "initial_capital": float(initial_capital),
        "final_balance": float(final_balance),
        "growth_factor": float(growth_factor),
        "calculated_cagr": float(calculated_cagr),
        "stored_cagr": float(stored_cagr),
        "difference": float(abs(Decimal(str(calculated_cagr)) - Decimal(str(stored_cagr)))),
        "tolerance": float(tolerance),
        "match": match,
    }


async def main():
    """Run all trading metrics validation tests."""
    # Database connection
    database_url = "postgresql+asyncpg://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader_qa"
    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with async_session() as session:
            # Get a sample backtest from QA database with trades and metrics
            result = await session.execute(
                select(BacktestRun)
                .where(BacktestRun.execution_status == "success")
                .order_by(BacktestRun.created_at.desc())
            )
            backtests = result.scalars().all()

            # Find a backtest with trades and complete metrics
            backtest = None
            for bt in backtests:
                if bt.metrics and bt.metrics.total_trades > 0:
                    backtest = bt
                    break

            if not backtest:
                print("❌ No successful backtests with trades found in QA database")
                return

            run_id = backtest.run_id

            print("\n" + "=" * 80)
            print("TRADING METRICS VALIDATION TEST SUITE")
            print("=" * 80)
            print("\nTest Subject:")
            print(f"  Strategy: {backtest.strategy_name}")
            print(f"  Instrument: {backtest.instrument_symbol}")
            print(f"  Run ID: {run_id}")
            print(f"  Date Range: {backtest.start_date} to {backtest.end_date}")

            # Run all validation tests
            results = []

            # TC-DATA-003: Win Rate
            result = await validate_win_rate(session, run_id)
            results.append(result)

            # TC-DATA-004: Total Return
            result = await validate_total_return(session, run_id)
            results.append(result)

            # TC-DATA-005: Trade P&L (first trade)
            result = await validate_trade_pnl(session, run_id, trade_index=0)
            results.append(result)

            # TC-DATA-006: CAGR
            result = await validate_cagr(session, run_id)
            results.append(result)

            # Summary
            print("\n" + "=" * 80)
            print("TEST SUMMARY")
            print("=" * 80)

            passed = sum(1 for r in results if r.get("status") == "PASSED")
            total = len(results)

            for result in results:
                status_icon = "✅" if result.get("status") == "PASSED" else "❌"
                print(f"{status_icon} {result['test_case']}: {result.get('status')}")
                if "error" in result:
                    print(f"   Error: {result['error']}")

            print(f"\nTotal: {passed}/{total} tests passed ({passed / total * 100:.1f}%)")

            if passed == total:
                print("\n✅ All trading metrics validation tests PASSED!")
                return 0
            else:
                print(f"\n❌ {total - passed} test(s) FAILED")
                return 1

    finally:
        await engine.dispose()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
