"""
Backtest persistence service.

This module provides the business logic layer for persisting backtest results
to the database, handling metric extraction, validation, and error scenarios.
"""

import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

import pandas as pd
import structlog

from src.db.exceptions import ValidationError
from src.db.models.backtest import BacktestRun
from src.db.repositories.backtest_repository import BacktestRepository
from src.models.backtest_result import BacktestResult
from src.models.config_snapshot import StrategyConfigSnapshot

logger = structlog.get_logger(__name__)


class BacktestPersistenceService:
    """
    Service for persisting backtest results to database.

    Coordinates saving backtest execution metadata and performance metrics,
    with validation and error handling.

    Attributes:
        repository: Data access layer for backtest records

    Example:
        >>> async with get_session() as session:
        ...     repository = BacktestRepository(session)
        ...     service = BacktestPersistenceService(repository)
        ...     run = await service.save_backtest_results(...)
    """

    def __init__(self, repository: BacktestRepository):
        """
        Initialize persistence service.

        Args:
            repository: Repository for database operations
        """
        self.repository = repository

    async def save_backtest_results(
        self,
        run_id: UUID,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal,
        data_source: str,
        execution_duration_seconds: Decimal,
        config_snapshot: dict,
        backtest_result: BacktestResult,
        reproduced_from_run_id: Optional[UUID] = None,
    ) -> BacktestRun:
        """
        Save successful backtest execution results.

        Args:
            run_id: Unique identifier for this backtest run
            strategy_name: Human-readable strategy name
            strategy_type: Strategy category
            instrument_symbol: Trading symbol
            start_date: Backtest period start
            end_date: Backtest period end
            initial_capital: Starting capital
            data_source: Data provider
            execution_duration_seconds: Time taken to execute
            config_snapshot: Strategy configuration
            backtest_result: Backtest execution results
            reproduced_from_run_id: Original run if reproduction

        Returns:
            Created BacktestRun instance

        Raises:
            ValidationError: If metrics contain invalid values (NaN/Infinity)
            DuplicateRecordError: If run_id already exists
        """
        logger.info(
            "Saving backtest results",
            run_id=str(run_id),
            strategy=strategy_name,
            symbol=instrument_symbol,
        )

        # Validate configuration snapshot structure
        validated_config = self._validate_config_snapshot(config_snapshot)

        # Create backtest run record
        backtest_run = await self.repository.create_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            instrument_symbol=instrument_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            data_source=data_source,
            execution_status="success",
            execution_duration_seconds=execution_duration_seconds,
            config_snapshot=validated_config,
            error_message=None,
            reproduced_from_run_id=reproduced_from_run_id,
        )

        # Extract and validate metrics from backtest result
        validated_metrics = self._extract_and_validate_metrics(backtest_result)

        # Create performance metrics record
        await self.repository.create_performance_metrics(
            backtest_run_id=backtest_run.id, **validated_metrics
        )

        logger.info(
            "Backtest results saved successfully",
            run_id=str(run_id),
            backtest_run_id=backtest_run.id,
        )

        return backtest_run

    async def save_failed_backtest(
        self,
        run_id: UUID,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal,
        data_source: str,
        execution_duration_seconds: Decimal,
        config_snapshot: dict,
        error_message: str,
    ) -> BacktestRun:
        """
        Save failed backtest execution.

        Creates a backtest run record with failed status and error message.
        No performance metrics are created for failed backtests.

        Args:
            run_id: Unique identifier for this backtest run
            strategy_name: Human-readable strategy name
            strategy_type: Strategy category
            instrument_symbol: Trading symbol
            start_date: Backtest period start
            end_date: Backtest period end
            initial_capital: Starting capital
            data_source: Data provider
            execution_duration_seconds: Time taken before failure
            config_snapshot: Strategy configuration
            error_message: Error description

        Returns:
            Created BacktestRun instance
        """
        logger.warning(
            "Saving failed backtest",
            run_id=str(run_id),
            strategy=strategy_name,
            error=error_message,
        )

        # Validate configuration snapshot structure
        validated_config = self._validate_config_snapshot(config_snapshot)

        # Create backtest run record with failed status
        backtest_run = await self.repository.create_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            instrument_symbol=instrument_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            data_source=data_source,
            execution_status="failed",
            execution_duration_seconds=execution_duration_seconds,
            config_snapshot=validated_config,
            error_message=error_message,
        )

        logger.info("Failed backtest saved", run_id=str(run_id))

        return backtest_run

    def _validate_config_snapshot(self, config_snapshot: dict) -> dict:
        """
        Validate configuration snapshot structure.

        Args:
            config_snapshot: Configuration dictionary

        Returns:
            Validated configuration dictionary

        Raises:
            ValidationError: If configuration structure is invalid
        """
        try:
            # Validate using Pydantic model
            validated = StrategyConfigSnapshot(**config_snapshot)
            return validated.model_dump()
        except Exception as e:
            raise ValidationError(f"Invalid config snapshot: {e}") from e

    def _extract_and_validate_metrics(self, backtest_result: BacktestResult) -> dict:
        """
        Extract metrics from backtest result and validate values.

        Extracts comprehensive metrics from BacktestResult including all Nautilus Trader
        analytics and validates that numeric values are not NaN or Infinity.

        Args:
            backtest_result: Backtest execution results with full Nautilus metrics

        Returns:
            Dictionary of validated metrics ready for database insertion

        Raises:
            ValidationError: If any required metric is NaN or Infinity
        """
        # Validate required metrics
        total_return = self._validate_metric(backtest_result.total_return, "total_return")
        final_balance = self._validate_metric(backtest_result.final_balance, "final_balance")

        # Calculate win rate
        win_rate = self._calculate_win_rate(
            backtest_result.winning_trades, backtest_result.total_trades
        )

        # Extract and validate all available Nautilus Trader metrics
        # These are now calculated and available in BacktestResult
        return {
            # Required metrics
            "total_return": total_return,
            "final_balance": final_balance,
            "total_trades": backtest_result.total_trades,
            "winning_trades": backtest_result.winning_trades,
            "losing_trades": backtest_result.losing_trades,
            "win_rate": win_rate,
            # Risk metrics (some require custom calculation)
            "cagr": self._validate_optional_metric(backtest_result.cagr, "cagr"),
            "sharpe_ratio": self._validate_optional_metric(
                backtest_result.sharpe_ratio, "sharpe_ratio"
            ),
            "sortino_ratio": self._validate_optional_metric(
                backtest_result.sortino_ratio, "sortino_ratio"
            ),
            "max_drawdown": self._validate_optional_metric(
                backtest_result.max_drawdown, "max_drawdown"
            ),
            "max_drawdown_date": None,  # Not tracking date currently
            "calmar_ratio": self._validate_optional_metric(
                backtest_result.calmar_ratio, "calmar_ratio"
            ),
            "volatility": self._validate_optional_metric(backtest_result.volatility, "volatility"),
            # Returns-based metrics (from get_performance_stats_returns)
            "risk_return_ratio": self._validate_optional_metric(
                backtest_result.risk_return_ratio, "risk_return_ratio"
            ),
            "avg_return": self._validate_optional_metric(backtest_result.avg_return, "avg_return"),
            "avg_win_return": self._validate_optional_metric(
                backtest_result.avg_win_return, "avg_win_return"
            ),
            "avg_loss_return": self._validate_optional_metric(
                backtest_result.avg_loss_return, "avg_loss_return"
            ),
            # Trading performance metrics (from get_performance_stats_pnls)
            "profit_factor": self._validate_optional_metric(
                backtest_result.profit_factor, "profit_factor"
            ),
            "expectancy": self._validate_optional_metric(backtest_result.expectancy, "expectancy"),
            "avg_win": self._validate_optional_metric(backtest_result.avg_win, "avg_win"),
            "avg_loss": self._validate_optional_metric(backtest_result.avg_loss, "avg_loss"),
            # Additional PnL-based metrics (from get_performance_stats_pnls)
            "total_pnl": self._validate_optional_metric(backtest_result.total_pnl, "total_pnl"),
            "total_pnl_percentage": self._validate_optional_metric(
                backtest_result.total_pnl_percentage, "total_pnl_percentage"
            ),
            "max_winner": self._validate_optional_metric(backtest_result.max_winner, "max_winner"),
            "max_loser": self._validate_optional_metric(backtest_result.max_loser, "max_loser"),
            "min_winner": self._validate_optional_metric(backtest_result.min_winner, "min_winner"),
            "min_loser": self._validate_optional_metric(backtest_result.min_loser, "min_loser"),
        }

    def _validate_metric(self, value: float, field_name: str) -> Decimal:
        """
        Validate that a metric is not NaN or Infinity.

        Args:
            value: Numeric value to validate
            field_name: Name of field for error messages

        Returns:
            Decimal representation of validated value

        Raises:
            ValidationError: If value is NaN or Infinity
        """
        if math.isnan(value):
            raise ValidationError(f"Invalid metric value: {field_name} is NaN")
        if math.isinf(value):
            raise ValidationError(f"Invalid metric value: {field_name} is Infinity")
        return Decimal(str(value))

    def _validate_optional_metric(
        self, value: Optional[float], field_name: str
    ) -> Optional[Decimal]:
        """
        Validate optional metric.

        Args:
            value: Optional numeric value to validate
            field_name: Name of field for error messages

        Returns:
            Decimal representation if not None, otherwise None

        Raises:
            ValidationError: If value is NaN or Infinity
        """
        if value is None:
            return None
        return self._validate_metric(value, field_name)

    def _calculate_win_rate(self, winning_trades: int, total_trades: int) -> Optional[Decimal]:
        """
        Calculate win rate from trade counts.

        Args:
            winning_trades: Count of winning trades
            total_trades: Total trade count

        Returns:
            Win rate as decimal (0.0-1.0), or None if no trades
        """
        if total_trades == 0:
            return None
        return Decimal(str(winning_trades / total_trades))

    async def save_trades_from_positions(
        self,
        backtest_run_id: int,
        positions_report_df,
    ) -> int:
        """
        Save trades from Nautilus Trader positions report to database.

        Converts positions report DataFrame to Trade records with complete
        entry/exit data and realized PnL.

        Args:
            backtest_run_id: ID of the backtest run these trades belong to
            positions_report_df: Pandas DataFrame from trader.generate_positions_report()

        Returns:
            Number of trades saved

        Raises:
            ValidationError: If positions report data is invalid

        Example:
            >>> positions_df = trader.generate_positions_report()
            >>> count = await service.save_trades_from_positions(
            ...     backtest_run_id=123,
            ...     positions_report_df=positions_df
            ... )
        """
        from src.db.models.trade import Trade as TradeDB
        from src.models.trade import TradeCreate

        if positions_report_df is None or positions_report_df.empty:
            logger.info("No positions to save", backtest_run_id=backtest_run_id)
            return 0

        logger.info(
            "Saving trades from positions report",
            backtest_run_id=backtest_run_id,
            position_count=len(positions_report_df),
            columns=list(positions_report_df.columns),
        )

        # Log first row as sample for debugging
        if len(positions_report_df) > 0:
            first_row = positions_report_df.iloc[0]
            logger.info(
                "Sample position row",
                backtest_run_id=backtest_run_id,
                sample_data={k: str(v) for k, v in first_row.to_dict().items()},
            )

        trades_to_save = []
        skipped_count = 0

        try:
            # Convert DataFrame rows to Trade models
            for position_id, row in positions_report_df.iterrows():
                # Skip unclosed positions - trades require both entry and exit
                if pd.isna(row["ts_closed"]):
                    logger.debug(
                        "Skipping unclosed position",
                        position_id=str(position_id),
                        instrument_id=str(row.get("instrument_id", "unknown")),
                    )
                    skipped_count += 1
                    continue

                # Convert timestamps to datetime
                # ts_opened is already pandas Timestamp from positions report
                entry_timestamp = row["ts_opened"]
                if hasattr(entry_timestamp, "to_pydatetime"):
                    entry_timestamp = entry_timestamp.to_pydatetime()
                if entry_timestamp.tzinfo is None:
                    entry_timestamp = entry_timestamp.replace(tzinfo=timezone.utc)

                # Convert exit timestamp (ts_closed)
                exit_timestamp = row["ts_closed"]
                if hasattr(exit_timestamp, "to_pydatetime"):
                    exit_timestamp = exit_timestamp.to_pydatetime()
                if exit_timestamp.tzinfo is None:
                    exit_timestamp = exit_timestamp.replace(tzinfo=timezone.utc)

                # Extract commission from commissions list
                commission_amount = Decimal("0.00")
                commission_currency = "USD"
                if "commissions" in row and row["commissions"]:
                    # commissions is a list of Money objects
                    commissions_list = row["commissions"]
                    if isinstance(commissions_list, list) and len(commissions_list) > 0:
                        # Take first commission from list and convert to string
                        comm_str = str(commissions_list[0])
                        parts = comm_str.split()
                        if len(parts) >= 2:
                            commission_amount = Decimal(parts[0])
                            commission_currency = parts[1]
                        elif len(parts) == 1:
                            commission_amount = Decimal(parts[0])
                    else:
                        # If it's not a list, try parsing as string directly
                        commissions_str = str(commissions_list).strip("[]")
                        parts = commissions_str.split()
                        if len(parts) >= 2:
                            commission_amount = Decimal(parts[0])
                            commission_currency = parts[1]
                        elif len(parts) == 1 and parts[0]:
                            commission_amount = Decimal(parts[0])

                # Calculate holding period in seconds from duration_ns
                holding_period_seconds = None
                if "duration_ns" in row and row["duration_ns"]:
                    holding_period_seconds = int(row["duration_ns"] / 1_000_000_000)

                # Extract realized PnL (format: "-134.66 USD")
                profit_loss = Decimal("0.00")
                if "realized_pnl" in row and row["realized_pnl"]:
                    pnl_str = str(row["realized_pnl"])
                    parts = pnl_str.split()
                    if len(parts) >= 1:
                        profit_loss = Decimal(parts[0])

                # Calculate profit percentage
                profit_pct = None
                entry_price = Decimal(str(row["avg_px_open"]))
                exit_price = Decimal(str(row["avg_px_close"]))
                if entry_price > 0:
                    profit_pct = ((exit_price - entry_price) / entry_price) * Decimal("100")

                # Create TradeCreate model for validation
                trade_data = TradeCreate(
                    backtest_run_id=backtest_run_id,
                    instrument_id=str(row["instrument_id"]),
                    trade_id=str(position_id),  # Use position_id as trade_id
                    venue_order_id=str(row["opening_order_id"]),
                    client_order_id=str(row["closing_order_id"]),
                    order_side=str(row["entry"]),  # BUY or SELL
                    quantity=Decimal(str(row["peak_qty"])),  # Use peak_qty as quantity
                    entry_price=entry_price,
                    exit_price=exit_price,
                    commission_amount=commission_amount,
                    commission_currency=commission_currency,
                    fees_amount=Decimal("0.00"),
                    entry_timestamp=entry_timestamp,
                    exit_timestamp=exit_timestamp,
                )

                # Create database model
                trade_db = TradeDB(
                    backtest_run_id=trade_data.backtest_run_id,
                    instrument_id=trade_data.instrument_id,
                    trade_id=trade_data.trade_id,
                    venue_order_id=trade_data.venue_order_id,
                    client_order_id=trade_data.client_order_id,
                    order_side=trade_data.order_side,
                    quantity=trade_data.quantity,
                    entry_price=trade_data.entry_price,
                    exit_price=trade_data.exit_price,
                    commission_amount=trade_data.commission_amount,
                    commission_currency=trade_data.commission_currency,
                    fees_amount=trade_data.fees_amount,
                    profit_loss=profit_loss,
                    profit_pct=profit_pct,
                    holding_period_seconds=holding_period_seconds,
                    entry_timestamp=trade_data.entry_timestamp,
                    exit_timestamp=trade_data.exit_timestamp,
                )

                trades_to_save.append(trade_db)

            # Log skipped unclosed positions if any
            if skipped_count > 0:
                logger.info(
                    "Skipped unclosed positions",
                    backtest_run_id=backtest_run_id,
                    skipped_count=skipped_count,
                )

            # Bulk insert all trades
            if trades_to_save:
                logger.info(
                    "Calling bulk_create_trades",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                    skipped_unclosed=skipped_count,
                )
                await self.repository.bulk_create_trades(trades_to_save)

                logger.info(
                    "Trades saved successfully",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                    skipped_unclosed=skipped_count,
                )
            else:
                logger.warning(
                    "No trades to save after processing positions",
                    backtest_run_id=backtest_run_id,
                    skipped_unclosed=skipped_count,
                )

            return len(trades_to_save)

        except Exception as e:
            logger.error(
                "Failed to save trades from positions",
                backtest_run_id=backtest_run_id,
                error=str(e),
            )
            raise ValidationError(f"Failed to save trades: {e}") from e
