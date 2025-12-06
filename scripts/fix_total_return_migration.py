"""
Data migration script to fix incorrect total_return values in QA database.

ISSUE-001: Total Return Calculation/Storage
- Problem: Legacy backtests (created 2025-11-01) store dollar amounts instead of decimal percentages
- Example: -18540000.000000 should be -18.54 (decimal for -1854%)
- Cause: Earlier bug in backtest_runner.py (now fixed)
- Solution: Recalculate total_return = (final_balance - initial_capital) / initial_capital

This script:
1. Identifies affected backtests (total_return outside normal range)
2. Recalculates correct total_return from final_balance and initial_capital
3. Updates performance_metrics table with corrected values
"""

import asyncio
import sys
from decimal import Decimal

import structlog
from sqlalchemy import select, update

from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.db.session import get_session

logger = structlog.get_logger(__name__)


async def fix_total_return_values() -> None:
    """
    Fix incorrect total_return values in performance_metrics table.

    Identifies backtests where total_return is stored as dollar amount instead of
    decimal percentage and recalculates the correct value.
    """
    logger.info("Starting total_return migration")

    async with get_session() as session:
        # Find all backtests with performance metrics
        # Join with backtest_runs to get initial_capital
        stmt = (
            select(BacktestRun, PerformanceMetrics)
            .join(PerformanceMetrics, BacktestRun.id == PerformanceMetrics.backtest_run_id)
            .where(
                # Find potentially incorrect values:
                # - total_return < -1.0 (less than -100%, unrealistic for most strategies)
                # - total_return > 10.0 (more than 1000%, unrealistic)
                # Normal range for total_return is approximately -1.0 to 10.0 (decimal)
                (PerformanceMetrics.total_return < Decimal("-1.0"))
                | (PerformanceMetrics.total_return > Decimal("10.0"))
            )
        )

        result = await session.execute(stmt)
        rows = result.all()

        if not rows:
            logger.info("No incorrect total_return values found - migration not needed")
            return

        logger.info(f"Found {len(rows)} backtests with incorrect total_return values")

        # Track statistics
        fixed_count = 0
        skipped_count = 0

        for backtest_run, metrics in rows:
            # Calculate correct total_return
            initial_capital = backtest_run.initial_capital
            final_balance = metrics.final_balance

            # Guard against division by zero
            if initial_capital == 0:
                logger.warning(f"Skipping backtest {backtest_run.run_id}: initial_capital is zero")
                skipped_count += 1
                continue

            # Calculate correct total_return as decimal
            # Example: (963993.20 - 1000000.00) / 1000000.00 = -0.036007 (-3.6%)
            correct_total_return = (final_balance - initial_capital) / initial_capital

            logger.info(
                f"Fixing backtest {backtest_run.run_id}",
                old_value=float(metrics.total_return),
                new_value=float(correct_total_return),
                initial_capital=float(initial_capital),
                final_balance=float(final_balance),
            )

            # Update the performance metrics record
            update_stmt = (
                update(PerformanceMetrics)
                .where(PerformanceMetrics.id == metrics.id)
                .values(total_return=correct_total_return)
            )
            await session.execute(update_stmt)
            fixed_count += 1

        # Commit all updates
        await session.commit()

        logger.info(
            "Migration completed",
            fixed_count=fixed_count,
            skipped_count=skipped_count,
            total_processed=len(rows),
        )

        # Verify results
        verify_stmt = select(PerformanceMetrics).where(
            (PerformanceMetrics.total_return < Decimal("-1.0"))
            | (PerformanceMetrics.total_return > Decimal("10.0"))
        )
        verify_result = await session.execute(verify_stmt)
        remaining = verify_result.all()

        if remaining:
            logger.warning(
                f"Warning: {len(remaining)} backtests still have out-of-range total_return values"
            )
        else:
            logger.info("✅ All total_return values are now in normal range")


async def main() -> None:
    """Main entry point for migration script."""
    try:
        await fix_total_return_values()
        logger.info("✅ Migration script completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Migration script failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
