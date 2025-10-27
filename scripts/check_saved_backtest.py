"""Check the saved backtest data in database."""

import asyncio
import json
from sqlalchemy import text
from src.db.session import get_session


async def check_saved_data():
    """Check what backtest data was saved."""
    async with get_session() as session:
        # Check backtest_runs
        print("Backtest Runs:")
        print("=" * 80)
        result = await session.execute(
            text("""
                SELECT run_id, strategy_name, strategy_type, instrument_symbol,
                       start_date, end_date, initial_capital, data_source,
                       execution_status, execution_duration_seconds, error_message,
                       reproduced_from_run_id, created_at
                FROM backtest_runs
                ORDER BY created_at DESC
            """)
        )
        runs = result.fetchall()

        for run in runs:
            print(f"\nRun ID: {run[0]}")
            print(f"  Strategy: {run[1]} ({run[2]})")
            print(f"  Instrument: {run[3]}")
            print(f"  Period: {run[4]} to {run[5]}")
            print(f"  Initial Capital: ${run[6]:,.2f}")
            print(f"  Data Source: {run[7]}")
            print(f"  Status: {run[8]}")
            print(f"  Duration: {run[9]}s")
            print(f"  Error: {run[10]}")
            print(f"  Reproduced From: {run[11]}")
            print(f"  Created: {run[12]}")

        # Check performance_metrics
        print("\n\nPerformance Metrics:")
        print("=" * 80)
        result = await session.execute(
            text("""
                SELECT pm.*, br.run_id, br.strategy_name
                FROM performance_metrics pm
                JOIN backtest_runs br ON pm.backtest_run_id = br.id
                ORDER BY br.created_at DESC
            """)
        )
        metrics = result.fetchall()

        for metric in metrics:
            print(f"\nRun ID: {metric[-2]}")
            print(f"  Strategy: {metric[-1]}")
            print(f"  Total Return: {metric[2]}")
            print(f"  Final Balance: ${metric[3]:,.2f}")
            print(f"  Sharpe Ratio: {metric[5]}")
            print(f"  Max Drawdown: {metric[6]}")
            print(f"  Total Trades: {metric[10]}")
            print(f"  Winning Trades: {metric[11]}")
            print(f"  Losing Trades: {metric[12]}")
            print(f"  Win Rate: {metric[13]}")

        # Check config_snapshot
        print("\n\nConfig Snapshot:")
        print("=" * 80)
        result = await session.execute(
            text("""
                SELECT run_id, strategy_name, config_snapshot
                FROM backtest_runs
                ORDER BY created_at DESC
                LIMIT 1
            """)
        )
        row = result.fetchone()
        if row:
            print(f"\nRun ID: {row[0]}")
            print(f"Strategy: {row[1]}")
            print(f"Config Snapshot:")
            print(json.dumps(row[2], indent=2))


if __name__ == "__main__":
    asyncio.run(check_saved_data())
