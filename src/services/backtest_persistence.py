"""
Backtest persistence service.

This module provides the business logic layer for persisting backtest results
to the database, handling metric extraction, validation, and error scenarios.
"""

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.exceptions import ValidationError
from src.db.models.backtest import BacktestRun
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.equity_curve_repository import EquityCurveRepository, EquityPoints
from src.models.backtest_result import BacktestResult
from src.models.config_snapshot import StrategyConfigSnapshot
from src.models.run_provenance import RunProvenance

logger = structlog.get_logger(__name__)


async def store_equity_curve(
    session: AsyncSession, backtest_run_id: int, points: EquityPoints | None
) -> None:
    """Store a run's equity curve, best-effort, without endangering the run itself.

    The run and its metrics are the primary record. A failed flush would leave
    the session unusable and lose them at commit, so the write sits in a
    savepoint and a failure is logged, not raised.
    """
    if not points:
        return
    try:
        async with session.begin_nested():
            await EquityCurveRepository(session).save(backtest_run_id, points)
    except Exception as e:
        logger.warning(f"Failed to store equity curve (run still persisted): {e}", exc_info=True)


def _to_utc(value) -> datetime:
    """Normalise a pandas Timestamp or datetime to timezone-aware UTC."""
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


_CENTS = Decimal("0.01")


def usd_profit_loss(realized_pnl, usd_rate: Decimal | None) -> Decimal:
    """A positions-report ``realized_pnl`` (``"-134.66 USD"``) as USD (FX M3, decision D11).

    No P&L (an open position: None or NaN) is 0.00. A USD amount is parsed as it always was.
    Any other currency (a USDJPY trade's ``"100000.00 JPY"``) converts at ``usd_rate``, the
    USD per unit at the position's exit, rounded to cents like the USD account balance.

    Raises:
        ValueError: A non-USD P&L with no ``usd_rate``; storing it as dollars would be wrong.
    """
    if realized_pnl is None or pd.isna(realized_pnl):
        return Decimal("0.00")
    amount, _, currency = str(realized_pnl).partition(" ")
    if currency.strip() in ("", "USD"):
        return Decimal(amount)
    if usd_rate is None:
        raise ValueError(f"realized P&L {realized_pnl} has no USD rate to convert it with")
    return (Decimal(amount) * usd_rate).quantize(_CENTS)


def _optional_str(value) -> Optional[str]:
    """Stringify, mapping absent values to ``None``.

    ``str(None)`` yields the literal ``"None"``, which then persists as a
    plausible-looking order id that refers to nothing.
    """
    if value is None or pd.isna(value):
        return None
    return str(value)


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
        provenance: Optional[RunProvenance] = None,
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
            provenance: Git commit, dirty state and config hash of the run

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
            provenance=provenance,
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
        provenance: Optional[RunProvenance] = None,
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
            provenance: Git commit, dirty state and config hash of the run

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
            provenance=provenance,
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
        usd_rates: Mapping[str, Decimal] | None = None,
    ) -> int:
        """
        Save trades from Nautilus Trader positions report to database.

        Converts positions report DataFrame to Trade records with complete
        entry/exit data and realized PnL.

        Args:
            backtest_run_id: ID of the backtest run these trades belong to
            positions_report_df: Pandas DataFrame from trader.generate_positions_report()
            usd_rates: USD per quote unit at each FX position's exit, by position id
                (``src/core/fx_results.exit_rates``); non-USD P&L converts with it

        Returns:
            Number of trades saved

        Raises:
            ValidationError: If positions report data is invalid, or a non-USD
                realized P&L has no rate in ``usd_rates``

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
        open_count = 0

        try:
            # Convert DataFrame rows to Trade models
            for position_id, row in positions_report_df.iterrows():
                # An open position is a real trade with an entry and no exit yet.
                # The trades schema was built for this (exit_price / exit_timestamp
                # are nullable, with a CHECK that tolerates NULL), so record it
                # rather than dropping it — previously a run that ended holding a
                # position persisted its metrics but silently no trades at all.
                is_open = pd.isna(row["ts_closed"])
                if is_open:
                    open_count += 1

                # Convert timestamps to datetime
                # ts_opened is already pandas Timestamp from positions report
                entry_timestamp = _to_utc(row["ts_opened"])
                exit_timestamp = None if is_open else _to_utc(row["ts_closed"])

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

                # Calculate holding period in seconds from duration_ns.
                # NOTE: `if row["duration_ns"]` does NOT filter NaN — float('nan')
                # is truthy, so int(nan) would raise. pandas coerces the column to
                # float64 as soon as one open position is present.
                holding_period_seconds = None
                duration_ns = row.get("duration_ns")
                if duration_ns is not None and not pd.isna(duration_ns):
                    holding_period_seconds = int(duration_ns / 1_000_000_000)

                # Realized PnL in USD (open positions: 0.00, the honest value)
                rate = (usd_rates or {}).get(str(position_id))
                profit_loss = usd_profit_loss(row.get("realized_pnl"), rate)

                # Quantize to 8 decimal places to avoid float artifacts
                # from Nautilus C extension float → Decimal conversion
                max_dp = Decimal("0.00000001")

                entry_price = Decimal(str(row["avg_px_open"])).quantize(max_dp)
                quantity = Decimal(str(row["peak_qty"])).quantize(max_dp)
                # Decimal("NaN").quantize() raises InvalidOperation, so NaN must be
                # filtered before conversion, not after.
                avg_px_close = row.get("avg_px_close")
                exit_price = (
                    None
                    if avg_px_close is None or pd.isna(avg_px_close)
                    else Decimal(str(avg_px_close)).quantize(max_dp)
                )

                # Undefined until there is an exit to measure against.
                profit_pct = None
                if exit_price is not None and entry_price > 0:
                    profit_pct = ((exit_price - entry_price) / entry_price) * Decimal("100")

                # Create TradeCreate model for validation
                trade_data = TradeCreate(
                    backtest_run_id=backtest_run_id,
                    instrument_id=str(row["instrument_id"]),
                    trade_id=str(position_id),  # Use position_id as trade_id
                    venue_order_id=str(row["opening_order_id"]),
                    # str(None) would persist the literal string "None".
                    client_order_id=_optional_str(row.get("closing_order_id")),
                    strategy_id=_optional_str(row.get("strategy_id")),
                    order_side=str(row["entry"]),  # BUY or SELL
                    quantity=quantity,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    commission_amount=commission_amount,
                    commission_currency=commission_currency,
                    fees_amount=Decimal("0.00"),
                    entry_timestamp=entry_timestamp,
                    exit_timestamp=exit_timestamp,
                )

                # Create database model — every TradeCreate field is a column
                # (the seal's ``open_trade`` precedent).
                trade_db = TradeDB(
                    **trade_data.model_dump(),
                    profit_loss=profit_loss,
                    profit_pct=profit_pct,
                    holding_period_seconds=holding_period_seconds,
                )

                trades_to_save.append(trade_db)

            # Surfaced at info, not debug: a run ending on an open position is a
            # normal outcome, but "why does this trade have no exit?" should be
            # answerable from the default-level log.
            if open_count > 0:
                logger.info(
                    "Recorded still-open positions",
                    backtest_run_id=backtest_run_id,
                    open_count=open_count,
                )

            # Bulk insert all trades
            if trades_to_save:
                logger.info(
                    "Calling bulk_create_trades",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                    open_positions=open_count,
                )
                await self.repository.bulk_create_trades(trades_to_save)

                logger.info(
                    "Trades saved successfully",
                    backtest_run_id=backtest_run_id,
                    trade_count=len(trades_to_save),
                    open_positions=open_count,
                )
            else:
                logger.warning(
                    "No trades to save after processing positions",
                    backtest_run_id=backtest_run_id,
                )

            return len(trades_to_save)

        except Exception as e:
            logger.error(
                "Failed to save trades from positions",
                backtest_run_id=backtest_run_id,
                error=str(e),
            )
            raise ValidationError(f"Failed to save trades: {e}") from e
