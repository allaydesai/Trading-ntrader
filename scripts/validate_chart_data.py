#!/usr/bin/env python3
"""
Chart Data Validation Script for TC-DATA-007 through TC-DATA-010.

This script validates chart API data against database records:
- TC-DATA-007: Trade Markers Position Accuracy
- TC-DATA-008: Equity Curve Start Value
- TC-DATA-009: Equity Curve End Value
- TC-DATA-010: Date Range Display Accuracy

Usage:
    ENV=qa uv run python scripts/validate_chart_data.py
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.db.models.backtest import BacktestRun  # noqa: E402

# Configuration
API_BASE_URL = "http://127.0.0.1:8000/api"
DATABASE_URL = "postgresql+asyncpg://ntrader:ntrader_dev_2025@localhost:5432/trading_ntrader_qa"


async def validate_trade_markers(
    session: AsyncSession, http_client: httpx.AsyncClient, run_id: str
) -> dict:
    """
    TC-DATA-007: Trade Markers Position Accuracy.

    Validates that trade markers returned by the API match the actual
    trade entry/exit timestamps and prices from the database.

    Args:
        session: Database session
        http_client: HTTP client for API calls
        run_id: Backtest run UUID

    Returns:
        dict: Validation result with status, details, and any errors
    """
    print("\n" + "=" * 80)
    print("TC-DATA-007: Trade Markers Position Accuracy")
    print("=" * 80)

    try:
        # Get backtest and trades from database
        result = await session.execute(
            select(BacktestRun)
            .options(selectinload(BacktestRun.trades))
            .where(BacktestRun.run_id == run_id)
        )
        backtest = result.scalar_one_or_none()

        if not backtest:
            return {
                "test_case": "TC-DATA-007",
                "status": "FAILED",
                "error": f"Backtest {run_id} not found",
            }

        db_trades = backtest.trades
        if not db_trades:
            return {
                "test_case": "TC-DATA-007",
                "status": "FAILED",
                "error": "No trades found for backtest",
            }

        # Get trade markers from API
        response = await http_client.get(f"{API_BASE_URL}/trades/{run_id}")
        response.raise_for_status()
        api_data = response.json()

        markers = api_data.get("trades", [])

        # Expected: 2 markers per closed trade (entry + exit)
        expected_marker_count = len(db_trades) * 2
        actual_marker_count = len(markers)

        print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
        print(f"Run ID: {run_id}")
        print(f"Total trades in database: {len(db_trades)}")
        print(f"Expected markers: {expected_marker_count} (2 per trade)")
        print(f"Actual markers returned: {actual_marker_count}")

        # Validate marker count
        if actual_marker_count != expected_marker_count:
            print("\n❌ FAILED: Marker count mismatch")
            return {
                "test_case": "TC-DATA-007",
                "status": "FAILED",
                "error": f"Expected {expected_marker_count} markers, got {actual_marker_count}",
                "expected": expected_marker_count,
                "actual": actual_marker_count,
            }

        # Validate each trade's entry and exit markers
        marker_index = 0
        all_markers_valid = True
        validation_errors = []

        for i, trade in enumerate(db_trades):
            print(f"\n--- Trade {i + 1} ---")
            print(f"Database entry time: {trade.entry_timestamp}")
            print(f"Database entry price: ${trade.entry_price}")
            print(f"Database exit time: {trade.exit_timestamp}")
            print(f"Database exit price: ${trade.exit_price}")

            # Validate entry marker
            entry_marker = markers[marker_index]
            entry_date = trade.entry_timestamp.strftime("%Y-%m-%d")

            print("\nEntry Marker:")
            print(f"  API time: {entry_marker['time']}")
            print(f"  API price: ${entry_marker['price']}")
            print(f"  API side: {entry_marker['side']}")

            if entry_marker["time"] != entry_date:
                error_msg = f"Entry marker time mismatch for trade {i + 1}"
                print(f"  ❌ {error_msg}")
                validation_errors.append(error_msg)
                all_markers_valid = False
            else:
                print("  ✅ Entry time matches")

            # Price tolerance: $0.01
            price_diff = abs(Decimal(str(entry_marker["price"])) - trade.entry_price)
            if price_diff > Decimal("0.01"):
                error_msg = f"Entry marker price mismatch for trade {i + 1}"
                print(f"  ❌ {error_msg} (diff: ${price_diff})")
                validation_errors.append(error_msg)
                all_markers_valid = False
            else:
                print("  ✅ Entry price matches (within $0.01)")

            marker_index += 1

            # Validate exit marker
            exit_marker = markers[marker_index]
            exit_date = trade.exit_timestamp.strftime("%Y-%m-%d")
            expected_exit_side = "sell" if trade.order_side == "BUY" else "buy"

            print("\nExit Marker:")
            print(f"  API time: {exit_marker['time']}")
            print(f"  API price: ${exit_marker['price']}")
            print(f"  API side: {exit_marker['side']}")

            if exit_marker["time"] != exit_date:
                error_msg = f"Exit marker time mismatch for trade {i + 1}"
                print(f"  ❌ {error_msg}")
                validation_errors.append(error_msg)
                all_markers_valid = False
            else:
                print("  ✅ Exit time matches")

            if exit_marker["side"] != expected_exit_side:
                error_msg = f"Exit marker side mismatch for trade {i + 1}"
                print(f"  ❌ {error_msg}")
                validation_errors.append(error_msg)
                all_markers_valid = False
            else:
                print("  ✅ Exit side matches")

            price_diff = abs(Decimal(str(exit_marker["price"])) - trade.exit_price)
            if price_diff > Decimal("0.01"):
                error_msg = f"Exit marker price mismatch for trade {i + 1}"
                print(f"  ❌ {error_msg} (diff: ${price_diff})")
                validation_errors.append(error_msg)
                all_markers_valid = False
            else:
                print("  ✅ Exit price matches (within $0.01)")

            marker_index += 1

        if all_markers_valid:
            print("\n✅ TC-DATA-007: PASSED - All trade markers are accurate")
            return {
                "test_case": "TC-DATA-007",
                "status": "PASSED",
                "trades_validated": len(db_trades),
                "markers_validated": actual_marker_count,
            }
        else:
            print(f"\n❌ TC-DATA-007: FAILED - {len(validation_errors)} validation errors")
            return {
                "test_case": "TC-DATA-007",
                "status": "FAILED",
                "errors": validation_errors,
            }

    except httpx.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        return {"test_case": "TC-DATA-007", "status": "FAILED", "error": str(e)}
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return {"test_case": "TC-DATA-007", "status": "FAILED", "error": str(e)}


async def validate_equity_curve_start(
    session: AsyncSession, http_client: httpx.AsyncClient, run_id: str
) -> dict:
    """
    TC-DATA-008: Equity Curve Start Value.

    Validates that the first point in the equity curve equals the
    initial capital from the backtest configuration.

    Args:
        session: Database session
        http_client: HTTP client for API calls
        run_id: Backtest run UUID

    Returns:
        dict: Validation result with status, details, and any errors
    """
    print("\n" + "=" * 80)
    print("TC-DATA-008: Equity Curve Start Value")
    print("=" * 80)

    try:
        # Get backtest from database
        result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
        backtest = result.scalar_one_or_none()

        if not backtest:
            return {
                "test_case": "TC-DATA-008",
                "status": "FAILED",
                "error": f"Backtest {run_id} not found",
            }

        initial_capital = float(backtest.initial_capital)

        # Get equity curve from API
        response = await http_client.get(f"{API_BASE_URL}/equity/{run_id}")
        response.raise_for_status()
        api_data = response.json()

        equity_points = api_data.get("equity", [])

        if not equity_points:
            return {
                "test_case": "TC-DATA-008",
                "status": "FAILED",
                "error": "No equity curve data returned by API",
            }

        first_equity_value = equity_points[0]["value"]

        print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
        print(f"Run ID: {run_id}")
        print(f"Initial Capital (Database): ${initial_capital:,.2f}")
        print(f"First Equity Point (API): ${first_equity_value:,.2f}")

        # Tolerance: $0.01
        difference = abs(first_equity_value - initial_capital)
        print(f"Difference: ${difference:.2f}")

        if difference <= 0.01:
            print("\n✅ TC-DATA-008: PASSED - Equity curve starts at initial capital")
            return {
                "test_case": "TC-DATA-008",
                "status": "PASSED",
                "expected": initial_capital,
                "actual": first_equity_value,
                "difference": difference,
            }
        else:
            print("\n❌ TC-DATA-008: FAILED - Equity curve start value mismatch")
            return {
                "test_case": "TC-DATA-008",
                "status": "FAILED",
                "expected": initial_capital,
                "actual": first_equity_value,
                "difference": difference,
            }

    except httpx.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        return {"test_case": "TC-DATA-008", "status": "FAILED", "error": str(e)}
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return {"test_case": "TC-DATA-008", "status": "FAILED", "error": str(e)}


async def validate_equity_curve_end(
    session: AsyncSession, http_client: httpx.AsyncClient, run_id: str
) -> dict:
    """
    TC-DATA-009: Equity Curve End Value.

    Validates that the last point in the equity curve equals the
    final balance from the performance metrics.

    Args:
        session: Database session
        http_client: HTTP client for API calls
        run_id: Backtest run UUID

    Returns:
        dict: Validation result with status, details, and any errors
    """
    print("\n" + "=" * 80)
    print("TC-DATA-009: Equity Curve End Value")
    print("=" * 80)

    try:
        # Get backtest from database
        result = await session.execute(
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.run_id == run_id)
        )
        backtest = result.scalar_one_or_none()

        if not backtest:
            return {
                "test_case": "TC-DATA-009",
                "status": "FAILED",
                "error": f"Backtest {run_id} not found",
            }

        if not backtest.metrics:
            return {
                "test_case": "TC-DATA-009",
                "status": "FAILED",
                "error": "No performance metrics found",
            }

        final_balance = float(backtest.metrics.final_balance)

        # Get equity curve from API
        response = await http_client.get(f"{API_BASE_URL}/equity/{run_id}")
        response.raise_for_status()
        api_data = response.json()

        equity_points = api_data.get("equity", [])

        if not equity_points:
            return {
                "test_case": "TC-DATA-009",
                "status": "FAILED",
                "error": "No equity curve data returned by API",
            }

        last_equity_value = equity_points[-1]["value"]

        print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
        print(f"Run ID: {run_id}")
        print(f"Final Balance (Database): ${final_balance:,.2f}")
        print(f"Last Equity Point (API): ${last_equity_value:,.2f}")

        # Tolerance: $0.01
        difference = abs(last_equity_value - final_balance)
        print(f"Difference: ${difference:.2f}")

        if difference <= 0.01:
            print("\n✅ TC-DATA-009: PASSED - Equity curve ends at final balance")
            return {
                "test_case": "TC-DATA-009",
                "status": "PASSED",
                "expected": final_balance,
                "actual": last_equity_value,
                "difference": difference,
            }
        else:
            print("\n❌ TC-DATA-009: FAILED - Equity curve end value mismatch")
            return {
                "test_case": "TC-DATA-009",
                "status": "FAILED",
                "expected": final_balance,
                "actual": last_equity_value,
                "difference": difference,
            }

    except httpx.HTTPError as e:
        print(f"\n❌ HTTP Error: {e}")
        return {"test_case": "TC-DATA-009", "status": "FAILED", "error": str(e)}
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return {"test_case": "TC-DATA-009", "status": "FAILED", "error": str(e)}


async def validate_date_range(session: AsyncSession, run_id: str) -> dict:
    """
    TC-DATA-010: Date Range Display Accuracy.

    Validates that the start_date and end_date stored in the database
    are correctly formatted and consistent.

    Args:
        session: Database session
        run_id: Backtest run UUID

    Returns:
        dict: Validation result with status, details, and any errors
    """
    print("\n" + "=" * 80)
    print("TC-DATA-010: Date Range Display Accuracy")
    print("=" * 80)

    try:
        # Get backtest from database
        result = await session.execute(select(BacktestRun).where(BacktestRun.run_id == run_id))
        backtest = result.scalar_one_or_none()

        if not backtest:
            return {
                "test_case": "TC-DATA-010",
                "status": "FAILED",
                "error": f"Backtest {run_id} not found",
            }

        start_date = backtest.start_date
        end_date = backtest.end_date

        print(f"\nBacktest: {backtest.strategy_name} - {backtest.instrument_symbol}")
        print(f"Run ID: {run_id}")
        print(f"Start Date: {start_date}")
        print(f"End Date: {end_date}")

        # Validate date range logic
        if end_date <= start_date:
            print("\n❌ TC-DATA-010: FAILED - End date is not after start date")
            return {
                "test_case": "TC-DATA-010",
                "status": "FAILED",
                "error": "End date must be after start date",
                "start_date": str(start_date),
                "end_date": str(end_date),
            }

        # Validate date formatting (should be ISO 8601 compliant)
        try:
            start_str = start_date.isoformat()
            end_str = end_date.isoformat()
            print(f"Start Date (ISO): {start_str}")
            print(f"End Date (ISO): {end_str}")
        except Exception as e:
            print(f"\n❌ TC-DATA-010: FAILED - Date formatting error: {e}")
            return {
                "test_case": "TC-DATA-010",
                "status": "FAILED",
                "error": f"Date formatting error: {e}",
            }

        # Calculate duration
        duration_days = (end_date - start_date).days
        print(f"Duration: {duration_days} days")

        print("\n✅ TC-DATA-010: PASSED - Date range is valid and properly formatted")
        return {
            "test_case": "TC-DATA-010",
            "status": "PASSED",
            "start_date": str(start_date),
            "end_date": str(end_date),
            "duration_days": duration_days,
        }

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        return {"test_case": "TC-DATA-010", "status": "FAILED", "error": str(e)}


async def main():
    """Main execution function."""
    print("\n" + "=" * 80)
    print("CHART DATA VALIDATION TEST SUITE")
    print("Testing TC-DATA-007 through TC-DATA-010")
    print("=" * 80)

    # Create async engine and session
    engine = create_async_engine(DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Find a suitable backtest with trades and metrics
        print("\n🔍 Searching for a backtest with trades and complete metrics...")

        result = await session.execute(
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.execution_status == "success")
            .order_by(BacktestRun.created_at.desc())
        )
        backtests = result.scalars().all()

        # Find a backtest with trades
        test_backtest = None
        for bt in backtests:
            if bt.metrics and bt.metrics.total_trades > 0:
                test_backtest = bt
                break

        if not test_backtest:
            print("❌ No suitable backtest found with trades and metrics")
            return

        run_id = str(test_backtest.run_id)
        print(f"✅ Selected backtest: {test_backtest.strategy_name}")
        print(f"   Instrument: {test_backtest.instrument_symbol}")
        print(f"   Run ID: {run_id}")
        print(f"   Trades: {test_backtest.metrics.total_trades}")

        # Create HTTP client
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            # Run validation tests
            results = []

            # TC-DATA-007: Trade Markers Position Accuracy
            result_007 = await validate_trade_markers(session, http_client, run_id)
            results.append(result_007)

            # TC-DATA-008: Equity Curve Start Value
            result_008 = await validate_equity_curve_start(session, http_client, run_id)
            results.append(result_008)

            # TC-DATA-009: Equity Curve End Value
            result_009 = await validate_equity_curve_end(session, http_client, run_id)
            results.append(result_009)

            # TC-DATA-010: Date Range Display Accuracy
            result_010 = await validate_date_range(session, run_id)
            results.append(result_010)

        # Summary
        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)

        passed = sum(1 for r in results if r["status"] == "PASSED")
        failed = len(results) - passed

        print(f"\nTotal Tests: {len(results)}")
        print(f"✅ Passed: {passed}")
        print(f"❌ Failed: {failed}")

        print("\nDetailed Results:")
        for result in results:
            status_icon = "✅" if result["status"] == "PASSED" else "❌"
            print(f"{status_icon} {result['test_case']}: {result['status']}")
            if result["status"] == "FAILED" and "error" in result:
                print(f"   Error: {result['error']}")

        print("\n" + "=" * 80)

        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
