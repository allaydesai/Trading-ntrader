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

import structlog

from src.models.backtest_result import BacktestResult
from src.db.exceptions import ValidationError
from src.db.models.backtest import BacktestRun
from src.db.repositories.backtest_repository import BacktestRepository
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
        total_return = self._validate_metric(
            backtest_result.total_return, "total_return"
        )
        final_balance = self._validate_metric(
            backtest_result.final_balance, "final_balance"
        )

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
            "volatility": self._validate_optional_metric(
                backtest_result.volatility, "volatility"
            ),
            # Returns-based metrics (from get_performance_stats_returns)
            "risk_return_ratio": self._validate_optional_metric(
                backtest_result.risk_return_ratio, "risk_return_ratio"
            ),
            "avg_return": self._validate_optional_metric(
                backtest_result.avg_return, "avg_return"
            ),
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
            "expectancy": self._validate_optional_metric(
                backtest_result.expectancy, "expectancy"
            ),
            "avg_win": self._validate_optional_metric(
                backtest_result.avg_win, "avg_win"
            ),
            "avg_loss": self._validate_optional_metric(
                backtest_result.avg_loss, "avg_loss"
            ),
            # Additional PnL-based metrics (from get_performance_stats_pnls)
            "total_pnl": self._validate_optional_metric(
                backtest_result.total_pnl, "total_pnl"
            ),
            "total_pnl_percentage": self._validate_optional_metric(
                backtest_result.total_pnl_percentage, "total_pnl_percentage"
            ),
            "max_winner": self._validate_optional_metric(
                backtest_result.max_winner, "max_winner"
            ),
            "max_loser": self._validate_optional_metric(
                backtest_result.max_loser, "max_loser"
            ),
            "min_winner": self._validate_optional_metric(
                backtest_result.min_winner, "min_winner"
            ),
            "min_loser": self._validate_optional_metric(
                backtest_result.min_loser, "min_loser"
            ),
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

    def _calculate_win_rate(
        self, winning_trades: int, total_trades: int
    ) -> Optional[Decimal]:
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

    async def save_trades_from_fills(
        self,
        backtest_run_id: int,
        fills_report_df,
    ) -> int:
        """
        Save trades from Nautilus Trader fills report to database.

        Converts fills report DataFrame to Trade records, calculates metrics,
        and performs bulk insert to database.

        Args:
            backtest_run_id: ID of the backtest run these trades belong to
            fills_report_df: Pandas DataFrame from trader.generate_fills_report()

        Returns:
            Number of trades saved

        Raises:
            ValidationError: If fills report data is invalid

        Example:
            >>> fills_df = trader.generate_fills_report()
            >>> count = await service.save_trades_from_fills(
            ...     backtest_run_id=123,
            ...     fills_report_df=fills_df
            ... )
        """
        from src.db.models.trade import Trade as TradeDB
        from src.models.trade import TradeCreate, calculate_trade_metrics

        if fills_report_df is None or fills_report_df.empty:
            logger.info("No fills to save", backtest_run_id=backtest_run_id)
            return 0

        logger.info(
            "Saving trades from fills report",
            backtest_run_id=backtest_run_id,
            fill_count=len(fills_report_df),
            columns=list(fills_report_df.columns),
        )

        # Log first row as sample for debugging
        if len(fills_report_df) > 0:
            first_row = fills_report_df.iloc[0]
            logger.info(
                "Sample fill row",
                backtest_run_id=backtest_run_id,
                sample_data={k: str(v) for k, v in first_row.to_dict().items()},
            )

        trades_to_save = []

        try:
            # Convert DataFrame rows to Trade models
            for idx, row in fills_report_df.iterrows():
                # Convert timestamp to datetime
                # Handle both pandas Timestamp and raw nanosecond int
                ts_event = row["ts_event"]
                if isinstance(ts_event, int):
                    # Raw nanosecond timestamp
                    entry_timestamp = datetime.fromtimestamp(
                        ts_event / 1e9, tz=timezone.utc
                    )
                else:
                    # pandas Timestamp or datetime - convert to datetime with UTC
                    entry_timestamp = ts_event.to_pydatetime().replace(
                        tzinfo=timezone.utc
                    )

                # Extract commission (handle Money object or string format)
                commission_amount = None
                commission_currency = None
                if "commission" in row and row["commission"] is not None:
                    commission_str = str(row["commission"])
                    # Commission format: "1.00 USD" - split to get amount and currency
                    parts = commission_str.split()
                    if len(parts) >= 2:
                        commission_amount = Decimal(parts[0])
                        commission_currency = parts[1]
                    elif len(parts) == 1:
                        # Just amount, no currency
                        commission_amount = Decimal(parts[0])
                        commission_currency = "USD"  # Default
                    else:
                        # Empty or invalid
                        commission_amount = Decimal("0.00")
                        commission_currency = "USD"

                # Create TradeCreate model for validation
                trade_data = TradeCreate(
                    backtest_run_id=backtest_run_id,
                    instrument_id=str(row["instrument_id"]),
                    trade_id=str(row["trade_id"]),
                    venue_order_id=str(row["venue_order_id"]),
                    client_order_id=str(row.get("client_order_id"))
                    if row.get("client_order_id")
                    else None,
                    order_side=str(row["order_side"]),
                    quantity=Decimal(str(row["last_qty"])),
                    entry_price=Decimal(str(row["last_px"])),
                    exit_price=None,  # Will be populated for closed positions
                    commission_amount=commission_amount,
                    commission_currency=commission_currency,
                    fees_amount=Decimal("0.00"),
                    entry_timestamp=entry_timestamp,
                    exit_timestamp=None,  # Will be populated for closed positions
                )

                # Calculate metrics (will be None for open trades)
                metrics = calculate_trade_metrics(trade_data)

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
                    profit_loss=metrics["profit_loss"],
                    profit_pct=metrics["profit_pct"],
                    holding_period_seconds=metrics["holding_period_seconds"],
                    entry_timestamp=trade_data.entry_timestamp,
                    exit_timestamp=trade_data.exit_timestamp,
                )

                trades_to_save.append(trade_db)

            # Bulk insert all trades
            if trades_to_save:
                logger.info(
                    "Calling bulk_create_trades",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                )
                await self.repository.bulk_create_trades(trades_to_save)

                logger.info(
                    "Trades saved successfully",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                )
            else:
                logger.warning(
                    "No trades to save after processing fills",
                    backtest_run_id=backtest_run_id,
                )

            return len(trades_to_save)

        except Exception as e:
            logger.error(
                "Failed to save trades from fills",
                backtest_run_id=backtest_run_id,
                error=str(e),
            )
            raise ValidationError(f"Failed to save trades: {e}") from e
