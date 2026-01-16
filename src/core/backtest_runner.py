"""Minimal backtest engine wrapper for Nautilus Trader."""

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Literal, Optional
from uuid import UUID, uuid4

import structlog
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money

from src.config import get_settings
from src.core.fee_models import IBKRCommissionModel
from src.core.signals.analysis import SignalAnalyzer, SignalStatistics
from src.core.signals.collector import SignalCollector
from src.core.strategies.sma_crossover import SMAConfig, SMACrossover
from src.core.strategy_factory import StrategyFactory
from src.core.strategy_registry import StrategyRegistry
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.session import get_session
from src.models.backtest_result import BacktestResult
from src.models.strategy import StrategyType
from src.services.backtest_persistence import BacktestPersistenceService
from src.services.data_service import DataService
from src.utils.config_loader import ConfigLoader, StrategyConfigWrapper
from src.utils.mock_data import create_test_instrument, generate_mock_bars

logger = structlog.get_logger(__name__)


class MinimalBacktestRunner:
    """Minimal backtest runner using Nautilus Trader."""

    def __init__(self, data_source: Literal["mock", "database", "catalog"] = "mock"):
        """
        Initialize the backtest runner.

        Args:
            data_source: Data source to use ('mock', 'database', or 'catalog')
        """
        self.settings = get_settings()
        self.data_source = data_source
        self.data_service = DataService() if data_source == "database" else None
        self.engine: BacktestEngine | None = None
        self._results: BacktestResult | None = None
        self._venue: Venue | None = None  # Track venue used in backtest
        self._backtest_start_date: datetime | None = None  # Track backtest date range
        self._backtest_end_date: datetime | None = None
        self._signal_collector: SignalCollector | None = None
        self._signal_statistics: SignalStatistics | None = None

    @property
    def signal_collector(self) -> SignalCollector | None:
        """Get the signal collector for the current backtest."""
        return self._signal_collector

    @property
    def signal_statistics(self) -> SignalStatistics | None:
        """Get signal statistics from the last backtest run.

        Returns:
            SignalStatistics if signals were enabled and collected, None otherwise.
        """
        return self._signal_statistics

    @staticmethod
    def _resolve_strategy_type(strategy_type: str) -> StrategyType:
        """
        Resolve strategy alias to canonical StrategyType enum.

        Args:
            strategy_type: Strategy name or alias (e.g., "crsi", "connors_rsi_mean_rev")

        Returns:
            StrategyType enum value

        Raises:
            ValueError: If strategy type is not found in registry or not a valid enum
        """
        # Handle legacy "sma" alias for backward compatibility
        if strategy_type == "sma":
            strategy_type = "sma_crossover"

        # Resolve alias to canonical name using registry
        StrategyRegistry.discover()  # Ensure strategies are loaded
        try:
            strategy_def = StrategyRegistry.get(strategy_type)
            canonical_name = strategy_def.name
        except KeyError:
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

        # Validate against StrategyType enum
        try:
            return StrategyType(canonical_name)
        except ValueError:
            raise ValueError(f"Unsupported strategy type: {strategy_type}")

    async def _persist_backtest_results(
        self,
        result: BacktestResult,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        execution_duration_seconds: Decimal,
        strategy_config: Dict[str, Any],
        reproduced_from_run_id: Optional[UUID] = None,
    ) -> Optional[UUID]:
        """
        Persist backtest results to database.

        Args:
            result: Backtest execution results
            strategy_name: Human-readable strategy name
            strategy_type: Strategy category
            instrument_symbol: Trading symbol
            start_date: Backtest period start
            end_date: Backtest period end
            execution_duration_seconds: Time taken to execute
            strategy_config: Strategy configuration parameters
            reproduced_from_run_id: Optional UUID of original backtest if reproduction

        Returns:
            UUID of created backtest run, or None if persistence fails
        """
        try:
            run_id = uuid4()

            # Build config snapshot for JSONB storage
            config_snapshot: dict[str, Any] = {
                "strategy_path": f"src.core.strategies.{strategy_type}",
                "config_path": "runtime_config",
                "version": "1.0",
                "config": strategy_config,
            }

            # Extract and store equity curve if engine is available
            if self.engine:
                analyzer = self.engine.portfolio.analyzer
                starting_balance = float(self.settings.default_balance)
                equity_curve = self._extract_equity_curve(analyzer, starting_balance)
                if equity_curve:
                    config_snapshot["equity_curve"] = equity_curve

            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                backtest_run = await service.save_backtest_results(
                    run_id=run_id,
                    strategy_name=strategy_name,
                    strategy_type=strategy_type,
                    instrument_symbol=instrument_symbol,
                    start_date=start_date,
                    end_date=end_date,
                    initial_capital=Decimal(str(self.settings.default_balance)),
                    data_source=self.data_source,
                    execution_duration_seconds=execution_duration_seconds,
                    config_snapshot=config_snapshot,
                    backtest_result=result,
                    reproduced_from_run_id=reproduced_from_run_id,
                )

                # Capture individual trades from fills report
                try:
                    logger.info(
                        "Starting trade capture",
                        run_id=str(run_id),
                        has_engine=bool(self.engine),
                    )

                    if self.engine:
                        # Generate positions report from Nautilus Trader
                        logger.info(
                            "Calling generate_positions_report",
                            run_id=str(run_id),
                        )
                        positions_report_df = self.engine.trader.generate_positions_report()

                        logger.info(
                            "Positions report generated",
                            run_id=str(run_id),
                            report_type=type(positions_report_df).__name__,
                            is_none=positions_report_df is None,
                            is_empty=positions_report_df.empty
                            if positions_report_df is not None
                            else "N/A",
                            row_count=len(positions_report_df)
                            if positions_report_df is not None
                            else 0,
                        )

                        if positions_report_df is not None and not positions_report_df.empty:
                            logger.info(
                                "Positions report has data, saving trades",
                                run_id=str(run_id),
                                columns=list(positions_report_df.columns)
                                if hasattr(positions_report_df, "columns")
                                else "N/A",
                            )

                            # Save trades to database
                            trade_count = await service.save_trades_from_positions(
                                backtest_run_id=backtest_run.id,
                                positions_report_df=positions_report_df,
                            )

                            logger.info(
                                "Trades captured from backtest",
                                run_id=str(run_id),
                                trade_count=trade_count,
                            )
                        else:
                            logger.warning(
                                "No positions to capture - positions report empty or None",
                                run_id=str(run_id),
                            )
                except Exception as trade_error:
                    # Log but don't fail the backtest if trade capture fails
                    logger.error(
                        "Failed to capture trades",
                        run_id=str(run_id),
                        error=str(trade_error),
                        error_type=type(trade_error).__name__,
                        exc_info=True,
                    )

                # Commit the transaction
                await session.commit()

            logger.info(
                "Backtest results persisted",
                run_id=str(run_id),
                strategy=strategy_name,
                symbol=instrument_symbol,
            )

            return run_id

        except Exception as e:
            # Log but don't fail the backtest if persistence fails
            logger.warning(
                "Failed to persist backtest results",
                error=str(e),
                strategy=strategy_name,
                symbol=instrument_symbol,
            )
            return None

    async def _persist_failed_backtest(
        self,
        error_message: str,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        execution_duration_seconds: Decimal,
        strategy_config: Dict[str, Any],
    ) -> Optional[UUID]:
        """
        Persist failed backtest execution to database.

        Args:
            error_message: Error description
            strategy_name: Human-readable strategy name
            strategy_type: Strategy category
            instrument_symbol: Trading symbol
            start_date: Backtest period start
            end_date: Backtest period end
            execution_duration_seconds: Time taken before failure
            strategy_config: Strategy configuration parameters

        Returns:
            UUID of created backtest run, or None if persistence fails
        """
        try:
            run_id = uuid4()

            # Build config snapshot for JSONB storage
            config_snapshot: dict[str, Any] = {
                "strategy_path": f"src.core.strategies.{strategy_type}",
                "config_path": "runtime_config",
                "version": "1.0",
                "config": strategy_config,
            }

            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                await service.save_failed_backtest(
                    run_id=run_id,
                    strategy_name=strategy_name,
                    strategy_type=strategy_type,
                    instrument_symbol=instrument_symbol,
                    start_date=start_date,
                    end_date=end_date,
                    initial_capital=Decimal(str(self.settings.default_balance)),
                    data_source=self.data_source,
                    execution_duration_seconds=execution_duration_seconds,
                    config_snapshot=config_snapshot,
                    error_message=error_message,
                )

                # Commit the transaction
                await session.commit()

            logger.info(
                "Failed backtest persisted",
                run_id=str(run_id),
                strategy=strategy_name,
                symbol=instrument_symbol,
                error=error_message,
            )

            return run_id

        except Exception as e:
            # Log but don't fail further if persistence fails
            logger.warning(
                "Failed to persist failed backtest",
                error=str(e),
                strategy=strategy_name,
                symbol=instrument_symbol,
            )
            return None

    def run_sma_backtest(
        self,
        fast_period: Optional[int] = None,
        slow_period: Optional[int] = None,
        trade_size: Optional[Decimal] = None,
        portfolio_value: Optional[Decimal] = None,
        position_size_pct: Optional[Decimal] = None,
        num_bars: Optional[int] = None,
    ) -> BacktestResult:
        """
        Run a simple SMA crossover backtest with mock data.

        Parameters
        ----------
        fast_period : int, optional
            Fast SMA period. Defaults to config value.
        slow_period : int, optional
            Slow SMA period. Defaults to config value.
        trade_size : Decimal, optional
            Trade size (deprecated - use position_size_pct instead).
        portfolio_value : Decimal, optional
            Starting portfolio value for position sizing. Defaults to config value.
        position_size_pct : Decimal, optional
            Position size as % of portfolio. Defaults to config value.
        num_bars : int, optional
            Number of mock data bars. Defaults to config value.

        Returns
        -------
        BacktestResult
            Results of the backtest.
        """
        # Use config defaults if not specified
        if fast_period is None:
            fast_period = self.settings.fast_ema_period
        if slow_period is None:
            slow_period = self.settings.slow_ema_period
        if portfolio_value is None:
            portfolio_value = self.settings.portfolio_value
        if position_size_pct is None:
            position_size_pct = self.settings.position_size_pct
        if num_bars is None:
            num_bars = self.settings.mock_data_bars

        # Create test instrument
        instrument, instrument_id = create_test_instrument()

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,  # 95% fill probability on stop orders
            prob_slippage=0.01,  # 1% slippage probability
        )

        # Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Add venue
        venue = Venue("SIM")
        self._venue = venue  # Store for result extraction
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Add instrument
        self.engine.add_instrument(instrument)

        # Generate mock data
        # Must match the bar type created in generate_mock_bars
        bar_type_str = f"{instrument_id}-15-MINUTE-MID-EXTERNAL"
        # Create bar type for strategy configuration
        BarType.from_str(bar_type_str)  # Validate format
        bars = generate_mock_bars(instrument_id, num_bars=num_bars)

        # Add bars to engine
        self.engine.add_data(bars)

        # Configure strategy
        strategy_config = SMAConfig(
            instrument_id=instrument_id,
            bar_type=BarType.from_str(bar_type_str),
            fast_period=fast_period,
            slow_period=slow_period,
            portfolio_value=portfolio_value,
            position_size_pct=position_size_pct,
        )

        # Create and add strategy
        strategy = SMACrossover(config=strategy_config)
        self.engine.add_strategy(strategy=strategy)

        # Store backtest date range (not available for mock data, skip CAGR)
        self._backtest_start_date = None
        self._backtest_end_date = None

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    async def run_backtest_with_database(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        fast_period: Optional[int] = None,
        slow_period: Optional[int] = None,
        trade_size: Optional[Decimal] = None,
        portfolio_value: Optional[Decimal] = None,
        position_size_pct: Optional[Decimal] = None,
    ) -> BacktestResult:
        """
        Run backtest using real data from database.

        Args:
            symbol: Trading symbol (e.g., AAPL)
            start: Start datetime for backtest
            end: End datetime for backtest
            fast_period: Fast SMA period
            slow_period: Slow SMA period
            trade_size: Trade size (deprecated - use position_size_pct)
            portfolio_value: Starting portfolio value for position sizing
            position_size_pct: Position size as % of portfolio

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If data source is not 'database' or no data available
            ConnectionError: If database is not accessible
        """
        if self.data_source != "database":
            raise ValueError("Data source must be 'database' for this method")

        if not self.data_service:
            raise ValueError("Data service not initialized")

        # Use config defaults if not specified
        if fast_period is None:
            fast_period = self.settings.fast_ema_period
        if slow_period is None:
            slow_period = self.settings.slow_ema_period
        if portfolio_value is None:
            portfolio_value = self.settings.portfolio_value
        if position_size_pct is None:
            position_size_pct = self.settings.position_size_pct

        # Validate data availability
        validation = await self.data_service.validate_data_availability(symbol, start, end)
        if not validation["valid"]:
            raise ValueError(f"Data validation failed: {validation['reason']}")

        # Fetch data from database
        market_data = await self.data_service.get_market_data(symbol, start, end)

        if not market_data:
            raise ValueError(f"No market data found for {symbol} between {start} and {end}")

        # Create test instrument for the actual symbol with SIM venue
        # This ensures the instrument matches the data being loaded
        if "/" in symbol and len(symbol.split("/")) == 2:
            # For FX pairs, create using the original symbol but force SIM venue
            base_instrument, _ = create_test_instrument(symbol)
            # The FX instrument should already have SIM venue from create_test_instrument
            instrument = base_instrument
        else:
            # For equity symbols, create with the actual symbol name for SIM venue
            clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

            # Create using TestInstrumentProvider but override with correct symbol and SIM venue
            from nautilus_trader.test_kit.providers import TestInstrumentProvider

            try:
                # Use TestInstrumentProvider to create a properly configured equity with SIM venue
                # This is a simpler approach that maintains all the correct instrument properties
                instrument = TestInstrumentProvider.equity(
                    symbol=clean_symbol,
                    venue="SIM",  # venue parameter expects string, not Venue object
                )
            except Exception:
                # Fallback: create a generic instrument but with the correct symbol
                # Use FX template but override the symbol to maintain compatibility
                base_fx = TestInstrumentProvider.default_fx_ccy("EUR/USD")
                # For fallback, we'll use the FX instrument but it will work for backtesting
                instrument = base_fx

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,  # 95% fill probability on stop orders
            prob_slippage=0.01,  # 1% slippage probability
        )

        # Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Add venue
        venue = Venue("SIM")
        self._venue = venue  # Store for result extraction
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Add instrument
        self.engine.add_instrument(instrument)

        # Convert database data to Nautilus Bar objects
        # Use the actual instrument.id that matches the instrument we added
        bars = self.data_service.convert_to_nautilus_bars(market_data, instrument.id, instrument)

        if not bars:
            raise ValueError("No bars were created from the data")

        # Add bars to engine
        self.engine.add_data(bars)

        # Create bar type string for strategy configuration
        # Must match the bar type created in convert_to_nautilus_bars
        # Use the actual instrument.id to ensure consistency
        bar_type_str = f"{instrument.id}-1-MINUTE-MID-EXTERNAL"

        # Configure strategy
        strategy_config = SMAConfig(
            instrument_id=instrument.id,
            bar_type=BarType.from_str(bar_type_str),
            fast_period=fast_period,
            slow_period=slow_period,
            portfolio_value=portfolio_value,
            position_size_pct=position_size_pct,
        )

        # Create and add strategy
        strategy = SMACrossover(config=strategy_config)
        self.engine.add_strategy(strategy=strategy)

        # Store backtest date range for CAGR calculation
        self._backtest_start_date = start
        self._backtest_end_date = end

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    def _extract_results(self) -> BacktestResult:
        """
        Extract comprehensive results from Nautilus Trader's PortfolioAnalyzer.

        Returns
        -------
        BacktestResult
            Extracted results with all available Nautilus Trader metrics.
        """
        if not self.engine:
            return BacktestResult()

        # Get account for analysis using the venue that was used in the backtest
        venue = self._venue if self._venue else Venue("SIM")
        account = self.engine.cache.account_for_venue(venue)

        if not account:
            return BacktestResult()

        # Calculate basic metrics
        starting_balance = float(self.settings.default_balance)
        final_balance = float(account.balance_total(USD).as_double())
        # Store total_return as percentage (0.25 = 25%), not dollar amount
        total_return = (final_balance - starting_balance) / starting_balance

        # Get trade statistics from closed positions
        closed_positions = self.engine.cache.positions_closed()

        # Total trades = closed positions only (actual executed and completed trades)
        # Open positions are NOT counted until they close
        total_trades = len(closed_positions)

        winning_trades = 0
        losing_trades = 0
        largest_win = 0.0
        largest_loss = 0.0

        for position in closed_positions:
            # Use realized PnL for closed positions
            pnl = (
                position.realized_pnl.as_double()
                if hasattr(position, "realized_pnl") and position.realized_pnl
                else 0.0
            )

            if pnl > 0:
                winning_trades += 1
                largest_win = max(largest_win, pnl)
            else:
                # Count breakeven (pnl == 0) and losing trades together
                # Breakeven trades are considered losses since they didn't generate profit
                losing_trades += 1
                if pnl < 0:
                    largest_loss = min(largest_loss, pnl)

        # Extract advanced metrics from Nautilus Trader's PortfolioAnalyzer
        analyzer = self.engine.portfolio.analyzer

        # Get comprehensive statistics from Nautilus Trader
        try:
            stats_returns = analyzer.get_performance_stats_returns()
            stats_pnls = analyzer.get_performance_stats_pnls(currency=USD)
        except Exception as e:
            logger.warning(f"Could not extract advanced metrics: {e}")
            stats_returns = {}
            stats_pnls = {}

        # Helper function to safely extract metric
        def safe_float(value) -> float | None:
            if value is None or value == "" or (isinstance(value, float) and value != value):
                return None  # None, empty string, or NaN
            try:
                result = float(value)
                # Filter out NaN and Inf values
                if result != result or abs(result) == float("inf"):
                    return None
                return result
            except (ValueError, TypeError):
                return None

        # Extract return-based metrics (from get_performance_stats_returns)
        sharpe_ratio = safe_float(stats_returns.get("Sharpe Ratio (252 days)"))
        sortino_ratio = safe_float(stats_returns.get("Sortino Ratio (252 days)"))
        volatility = safe_float(stats_returns.get("Returns Volatility (252 days)"))
        profit_factor = safe_float(stats_returns.get("Profit Factor"))
        risk_return_ratio = safe_float(stats_returns.get("Risk Return Ratio"))
        avg_return = safe_float(stats_returns.get("Average (Return)"))
        avg_win_return = safe_float(stats_returns.get("Average Win (Return)"))
        avg_loss_return = safe_float(stats_returns.get("Average Loss (Return)"))

        # Extract PnL-based metrics (from get_performance_stats_pnls)
        total_pnl = safe_float(stats_pnls.get("PnL (total)"))
        total_pnl_percentage = safe_float(stats_pnls.get("PnL% (total)"))
        expectancy = safe_float(stats_pnls.get("Expectancy"))
        avg_win = safe_float(stats_pnls.get("Avg Winner"))
        avg_loss = safe_float(stats_pnls.get("Avg Loser"))
        max_winner = safe_float(stats_pnls.get("Max Winner"))
        max_loser = safe_float(stats_pnls.get("Max Loser"))
        min_winner = safe_float(stats_pnls.get("Min Winner"))
        min_loser = safe_float(stats_pnls.get("Min Loser"))

        # Calculate custom metrics not provided by Nautilus Trader
        # These require access to the full equity curve and time series data
        max_drawdown = self._calculate_max_drawdown(analyzer, account)

        # Calculate CAGR using stored backtest dates or attempt to extract from data
        cagr = None
        if self._backtest_start_date and self._backtest_end_date:
            cagr = self._calculate_cagr(
                starting_balance,
                final_balance,
                self._backtest_start_date,
                self._backtest_end_date,
            )
        else:
            logger.debug("Backtest date range not available, skipping CAGR calculation")

        calmar_ratio = self._calculate_calmar_ratio(cagr, max_drawdown)
        max_drawdown_duration = None  # Not tracking duration currently

        # Total commissions/fees tracking
        total_fees = None  # Not directly available in current Nautilus output

        return BacktestResult(
            # Basic metrics
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            largest_win=largest_win,
            largest_loss=largest_loss,
            final_balance=final_balance,
            # Returns-based metrics (from get_performance_stats_returns)
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            volatility=volatility,
            profit_factor=profit_factor,
            risk_return_ratio=risk_return_ratio,
            avg_return=avg_return,
            avg_win_return=avg_win_return,
            avg_loss_return=avg_loss_return,
            # PnL-based metrics (from get_performance_stats_pnls)
            total_pnl=total_pnl,
            total_pnl_percentage=total_pnl_percentage,
            expectancy=expectancy,
            avg_win=avg_win,
            avg_loss=avg_loss,
            max_winner=max_winner,
            max_loser=max_loser,
            min_winner=min_winner,
            min_loser=min_loser,
            # Custom calculated metrics (not available from Nautilus)
            max_drawdown=max_drawdown,
            max_drawdown_duration_days=max_drawdown_duration,
            cagr=cagr,
            calmar_ratio=calmar_ratio,
            # Cost metrics
            total_fees=total_fees,
            total_commissions=total_fees,  # Same as total_fees
        )

    def get_detailed_results(self) -> Dict[str, Any]:
        """
        Get detailed results from the last backtest run.

        Returns
        -------
        Dict[str, Any]
            Detailed results dictionary.
        """
        if not self.engine or not self._results:
            return {}

        # Future: Could get account for detailed analysis via Venue("SIM")

        return {
            "basic_metrics": {
                "total_return": self._results.total_return,
                "total_trades": self._results.total_trades,
                "win_rate": self._results.win_rate,
                "largest_win": self._results.largest_win,
                "largest_loss": self._results.largest_loss,
                "final_balance": self._results.final_balance,
            },
            "account_summary": {
                "starting_balance": float(self.settings.default_balance),
                "final_balance": self._results.final_balance,
                "currency": "USD",
            },
            "positions": len(self.engine.cache.positions_closed()),
            "orders": len(self.engine.cache.orders()),
        }

    def run_from_config_file(self, config_file_path: str) -> BacktestResult:
        """
        Run backtest from YAML configuration file.

        Args:
            config_file_path: Path to YAML configuration file

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If configuration is invalid
        """
        # Load configuration from file
        config_obj = ConfigLoader.load_from_file(config_file_path)
        return self.run_from_config_object(config_obj)

    def _calculate_max_drawdown(self, analyzer, account) -> float | None:
        """
        Calculate maximum drawdown from account equity history.

        Maximum drawdown is the largest peak-to-trough decline in portfolio value.
        Formula: MDD = (Trough Value - Peak Value) / Peak Value

        Args:
            analyzer: Nautilus Trader PortfolioAnalyzer
            account: Account object with balance history

        Returns:
            Maximum drawdown as negative decimal (e.g., -0.15 for 15% drawdown)
            or None if calculation fails
        """
        try:
            # Try to get returns series from analyzer
            returns = analyzer.returns()

            if returns is None or len(returns) == 0:
                logger.debug("No returns data available for max drawdown calculation")
                return None

            # Calculate cumulative returns to build equity curve
            cumulative_returns = (1 + returns).cumprod()

            # Track running maximum (peak) and calculate drawdowns
            running_max = cumulative_returns.expanding().max()
            drawdowns = (cumulative_returns - running_max) / running_max

            # Maximum drawdown is the minimum value (most negative)
            max_drawdown = float(drawdowns.min())

            logger.debug(f"Calculated max drawdown: {max_drawdown:.4f}")
            return max_drawdown if max_drawdown < 0 else 0.0

        except Exception as e:
            logger.warning(f"Could not calculate max drawdown: {e}")
            return None

    def _extract_equity_curve(
        self, analyzer, starting_balance: float
    ) -> list[dict[str, int | float]]:
        """
        Extract equity curve time series from portfolio analyzer.

        Builds equity curve from cumulative returns data for chart visualization.
        Falls back to building curve from closed positions if analyzer returns are empty.

        Args:
            analyzer: Nautilus Trader PortfolioAnalyzer
            starting_balance: Initial portfolio value

        Returns:
            List of equity points: [{"time": 1705276800, "value": 100500.0}, ...]
            Returns empty list if extraction fails
        """
        try:
            # Try method 1: Get returns series from analyzer
            returns = analyzer.returns()

            if returns is not None and len(returns) > 0:
                # Calculate cumulative returns to build equity curve
                cumulative_returns = (1 + returns).cumprod()

                # Convert to equity values
                equity_values = cumulative_returns * starting_balance

                # Build list of equity points for JSON storage
                equity_curve = []
                for timestamp, value in equity_values.items():
                    # Convert timestamp to Unix timestamp (seconds since epoch)
                    if hasattr(timestamp, "timestamp"):
                        time_unix = int(timestamp.timestamp())
                    elif isinstance(timestamp, int):
                        time_unix = timestamp
                    else:
                        # Try to parse as string and convert
                        try:
                            from datetime import datetime

                            dt = datetime.fromisoformat(str(timestamp))
                            time_unix = int(dt.timestamp())
                        except (ValueError, AttributeError):
                            logger.warning(f"Could not parse timestamp: {timestamp}")
                            continue

                    equity_curve.append({"time": time_unix, "value": round(float(value), 2)})

                logger.debug(f"Extracted equity curve with {len(equity_curve)} points")
                return equity_curve

            # Method 2: Build equity curve from closed positions
            logger.debug("No returns data available, building equity curve from positions")
            return self._build_equity_curve_from_positions(starting_balance)

        except Exception as e:
            logger.warning(f"Could not extract equity curve: {e}")
            # Try fallback method
            try:
                return self._build_equity_curve_from_positions(starting_balance)
            except Exception as fallback_error:
                logger.warning(f"Fallback equity curve failed: {fallback_error}")
                return []

    def _build_equity_curve_from_positions(
        self, starting_balance: float
    ) -> list[dict[str, int | float]]:
        """
        Build equity curve from closed positions.

        Creates equity curve by tracking cumulative PnL from each closed position.

        Args:
            starting_balance: Initial portfolio value

        Returns:
            List of equity points sorted by time
        """
        if not self.engine:
            return []

        # Get all closed positions
        closed_positions = self.engine.cache.positions_closed()

        if not closed_positions:
            logger.debug("No closed positions to build equity curve")
            return []

        # Build equity points from positions
        equity_points = []
        cumulative_pnl = 0.0

        # Add starting point using backtest start date
        if self._backtest_start_date:
            equity_points.append(
                {
                    "time": int(self._backtest_start_date.timestamp()),
                    "value": round(starting_balance, 2),
                }
            )

        # Sort positions by close time
        sorted_positions = sorted(
            [p for p in closed_positions if hasattr(p, "ts_closed") and p.ts_closed],
            key=lambda x: x.ts_closed,
        )

        for position in sorted_positions:
            # Get realized PnL
            pnl = (
                position.realized_pnl.as_double()
                if hasattr(position, "realized_pnl") and position.realized_pnl
                else 0.0
            )

            cumulative_pnl += pnl
            equity_value = starting_balance + cumulative_pnl

            # Convert Nautilus timestamp to Unix timestamp
            # ts_closed is in nanoseconds, convert to seconds
            time_unix = int(position.ts_closed / 1_000_000_000)

            equity_points.append({"time": time_unix, "value": round(equity_value, 2)})

        # Add ending point using backtest end date if we have it
        if self._backtest_end_date and equity_points:
            final_equity = equity_points[-1]["value"]
            equity_points.append(
                {
                    "time": int(self._backtest_end_date.timestamp()),
                    "value": final_equity,
                }
            )

        logger.debug(
            f"Built equity curve from {len(sorted_positions)} positions, "
            f"{len(equity_points)} total points"
        )

        return equity_points

    def _calculate_cagr(
        self, starting_balance: float, final_balance: float, start_date, end_date
    ) -> float | None:
        """
        Calculate Compound Annual Growth Rate (CAGR).

        Formula: CAGR = (Ending Value / Beginning Value) ^ (1 / Years) - 1

        Args:
            starting_balance: Initial portfolio value
            final_balance: Final portfolio value
            start_date: Backtest start date
            end_date: Backtest end date

        Returns:
            CAGR as decimal (e.g., 0.25 for 25% annual return)
            or None if calculation fails
        """
        try:
            # Validate inputs
            if starting_balance <= 0:
                logger.warning("Starting balance must be positive for CAGR calculation")
                return None

            if final_balance <= 0:
                logger.warning("Final balance is non-positive, CAGR calculation skipped")
                return None

            # Calculate years (use fractional years for accuracy)
            days = (end_date - start_date).days
            if days <= 0:
                logger.warning("Backtest duration must be positive for CAGR calculation")
                return None

            years = days / 365.25  # Account for leap years

            # Calculate CAGR
            cagr = (final_balance / starting_balance) ** (1 / years) - 1

            logger.debug(
                f"Calculated CAGR: {cagr:.4f} "
                f"({starting_balance:.2f} -> {final_balance:.2f} over {years:.2f} years)"
            )
            return float(cagr)

        except Exception as e:
            logger.warning(f"Could not calculate CAGR: {e}")
            return None

    def _calculate_calmar_ratio(
        self, cagr: float | None, max_drawdown: float | None
    ) -> float | None:
        """
        Calculate Calmar Ratio (return per unit of downside risk).

        Formula: Calmar Ratio = CAGR / abs(Maximum Drawdown)

        Args:
            cagr: Compound annual growth rate as decimal
            max_drawdown: Maximum drawdown as negative decimal

        Returns:
            Calmar ratio (e.g., 2.0 means 2% return per 1% drawdown)
            or None if calculation fails
        """
        try:
            # Both metrics must be available
            if cagr is None or max_drawdown is None:
                return None

            # Avoid division by zero
            if max_drawdown == 0:
                logger.debug("Max drawdown is zero, Calmar ratio undefined")
                return None

            # Calculate Calmar ratio
            calmar = cagr / abs(max_drawdown)

            logger.debug(
                f"Calculated Calmar ratio: {calmar:.2f} (CAGR: {cagr:.4f}, MDD: {max_drawdown:.4f})"
            )
            return float(calmar)

        except Exception as e:
            logger.warning(f"Could not calculate Calmar ratio: {e}")
            return None

    def run_from_config_object(self, config_obj: StrategyConfigWrapper) -> BacktestResult:
        """
        Run backtest from loaded configuration object.

        Args:
            config_obj: Loaded strategy configuration wrapper

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If strategy cannot be created or configuration is invalid
        """
        # Create test instrument
        instrument, instrument_id = create_test_instrument()

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,  # 95% fill probability on stop orders
            prob_slippage=0.01,  # 1% slippage probability
        )

        # Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Add venue
        venue = Venue("SIM")
        self._venue = venue  # Store for result extraction
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Add instrument
        self.engine.add_instrument(instrument)

        # Generate mock data
        # Must match the bar type created in generate_mock_bars
        bar_type_str = f"{instrument_id}-15-MINUTE-MID-EXTERNAL"
        # Create bar type for strategy configuration
        BarType.from_str(bar_type_str)  # Validate format
        bars = generate_mock_bars(instrument_id, num_bars=self.settings.mock_data_bars)

        # Add bars to engine
        self.engine.add_data(bars)

        # Use the StrategyFactory to create the strategy with proper config
        # Create config parameters dictionary with mock data values
        config_params = {
            "instrument_id": instrument_id,
            "bar_type": BarType.from_str(bar_type_str),
        }

        # Copy specific strategy parameters from the loaded config
        if hasattr(config_obj.config, "fast_period"):
            config_params["fast_period"] = config_obj.config.fast_period
        if hasattr(config_obj.config, "slow_period"):
            config_params["slow_period"] = config_obj.config.slow_period
        if hasattr(config_obj.config, "lookback_period"):
            config_params["lookback_period"] = config_obj.config.lookback_period
        if hasattr(config_obj.config, "num_std_dev"):
            config_params["num_std_dev"] = config_obj.config.num_std_dev
        if hasattr(config_obj.config, "rsi_period"):
            config_params["rsi_period"] = config_obj.config.rsi_period
        if hasattr(config_obj.config, "oversold_threshold"):
            config_params["oversold_threshold"] = config_obj.config.oversold_threshold
        if hasattr(config_obj.config, "overbought_threshold"):
            config_params["overbought_threshold"] = config_obj.config.overbought_threshold
        if hasattr(config_obj.config, "trade_size"):
            config_params["trade_size"] = config_obj.config.trade_size

        # RSI Mean Reversion specific parameters
        if hasattr(config_obj.config, "order_id_tag"):
            config_params["order_id_tag"] = config_obj.config.order_id_tag
        if hasattr(config_obj.config, "rsi_buy_threshold"):
            config_params["rsi_buy_threshold"] = config_obj.config.rsi_buy_threshold
        if hasattr(config_obj.config, "exit_rsi"):
            config_params["exit_rsi"] = config_obj.config.exit_rsi
        if hasattr(config_obj.config, "sma_trend_period"):
            config_params["sma_trend_period"] = config_obj.config.sma_trend_period
        if hasattr(config_obj.config, "warmup_days"):
            config_params["warmup_days"] = config_obj.config.warmup_days
        if hasattr(config_obj.config, "cooldown_bars"):
            config_params["cooldown_bars"] = config_obj.config.cooldown_bars

        # SMA Momentum specific parameters
        if hasattr(config_obj.config, "allow_short"):
            config_params["allow_short"] = config_obj.config.allow_short

        # Create strategy using factory method
        strategy = StrategyFactory.create_strategy_from_config(
            strategy_path=config_obj.strategy_path,
            config_path=config_obj.config_path,
            config_params=config_params,
        )
        self.engine.add_strategy(strategy=strategy)

        # Store backtest date range (not available from config, skip CAGR)
        self._backtest_start_date = None
        self._backtest_end_date = None

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    def reset(self) -> None:
        """Reset the engine for a new backtest."""
        if self.engine:
            self.engine.reset()
        self._results = None
        self._venue = None
        self._backtest_start_date = None
        self._backtest_end_date = None
        self._signal_collector = None
        self._signal_statistics = None

    async def run_backtest_with_strategy_type(
        self,
        strategy_type: str,
        symbol: str,
        start: datetime,
        end: datetime,
        **strategy_params,
    ) -> BacktestResult:
        """
        Run backtest with specific strategy type using database data.

        Args:
            strategy_type: Type of strategy
                ("sma_crossover", "mean_reversion", "momentum", or "sma")
            symbol: Trading symbol
            start: Start datetime
            end: End datetime
            **strategy_params: Strategy-specific parameters

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If strategy type is not supported or data is invalid
        """
        from src.core.strategy_factory import StrategyLoader

        # Resolve strategy alias to canonical StrategyType
        strategy_enum = self._resolve_strategy_type(strategy_type)

        if self.data_source != "database":
            raise ValueError("This method requires 'database' data source")

        if not self.data_service:
            raise ValueError("Data service not initialized")

        # Validate data availability
        validation = await self.data_service.validate_data_availability(symbol, start, end)
        if not validation["valid"]:
            raise ValueError(f"Data validation failed: {validation['reason']}")

        # Fetch data from database
        market_data = await self.data_service.get_market_data(symbol, start, end)

        if not market_data:
            raise ValueError(f"No market data found for {symbol} between {start} and {end}")

        # Create test instrument for the actual symbol with SIM venue
        if "/" in symbol and len(symbol.split("/")) == 2:
            # For FX pairs
            base_instrument, _ = create_test_instrument(symbol)
            instrument = base_instrument
        else:
            # For equity symbols
            clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

            from nautilus_trader.test_kit.providers import TestInstrumentProvider

            try:
                instrument = TestInstrumentProvider.equity(
                    symbol=clean_symbol,
                    venue="SIM",
                )
            except Exception:
                # Fallback
                base_fx = TestInstrumentProvider.default_fx_ccy("EUR/USD")
                instrument = base_fx

        # Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Initialize engine
        self.engine = BacktestEngine(config=config)

        # Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,  # 95% fill probability on stop orders
            prob_slippage=0.01,  # 1% slippage probability
        )

        # Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Add venue
        venue = Venue("SIM")
        self._venue = venue  # Store for result extraction
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Add instrument
        self.engine.add_instrument(instrument)

        # Convert database data to Nautilus Bar objects
        bars = self.data_service.convert_to_nautilus_bars(market_data, instrument.id, instrument)

        if not bars:
            raise ValueError("No bars were created from the data")

        # Add bars to engine
        self.engine.add_data(bars)

        # Create bar type string
        bar_type_str = f"{instrument.id}-1-MINUTE-MID-EXTERNAL"

        # Use StrategyLoader to build parameters dynamically
        config_params = StrategyLoader.build_strategy_params(
            strategy_type=strategy_enum,
            overrides=strategy_params,
            settings=self.settings,
        )

        # Add required base parameters
        from nautilus_trader.model.data import BarType

        config_params.update(
            {
                "instrument_id": instrument.id,
                "bar_type": BarType.from_str(bar_type_str),
            }
        )

        # Create strategy using StrategyLoader
        strategy = StrategyLoader.create_strategy(strategy_enum, config_params)
        self.engine.add_strategy(strategy=strategy)

        # Store backtest date range for CAGR calculation
        self._backtest_start_date = start
        self._backtest_end_date = end

        # Run the backtest
        self.engine.run()

        # Extract and return results
        self._results = self._extract_results()
        return self._results

    async def run_backtest_with_catalog_data(
        self,
        bars: list,
        strategy_type: str,
        symbol: str,
        start: datetime,
        end: datetime,
        instrument: object | None = None,
        reproduced_from_run_id: Optional[UUID] = None,
        enable_signals: bool = False,
        signal_export_path: str | None = None,
        **strategy_params,
    ) -> tuple[BacktestResult, Optional[UUID]]:
        """
        Run backtest with pre-loaded bars from Parquet catalog.

        Args:
            bars: Pre-loaded Bar objects from catalog
            strategy_type: Type of strategy
                ("sma_crossover", "mean_reversion", "momentum", or "sma")
            symbol: Trading symbol
            start: Start datetime (for display purposes)
            end: End datetime (for display purposes)
            instrument: Optional Nautilus Instrument object (if None, creates test instrument)
            reproduced_from_run_id: Optional UUID of original backtest if reproduction
            enable_signals: Enable signal validation and audit capture
            signal_export_path: Path to export signal audit trail CSV
            **strategy_params: Strategy-specific parameters

        Returns:
            BacktestResult: Results of the backtest

        Raises:
            ValueError: If strategy type is not supported or bars are empty
        """
        from src.core.strategy_factory import StrategyLoader

        # Track execution time for persistence
        execution_start_time = time.time()

        # Resolve strategy alias to canonical StrategyType
        strategy_enum = self._resolve_strategy_type(strategy_type)

        if not bars:
            raise ValueError("No bars provided for backtest")

        # Reason: Use provided instrument or create test instrument as fallback
        if instrument is None:
            # Try to extract venue from first bar if available
            venue_str = "SIM"  # Default fallback
            if bars and hasattr(bars[0], "bar_type"):
                try:
                    venue_str = str(bars[0].bar_type.instrument_id.venue)
                except (AttributeError, IndexError):
                    pass  # Keep SIM as fallback

            # Reason: Create test instrument for the symbol with matching venue
            if "/" in symbol and len(symbol.split("/")) == 2:
                # For FX pairs
                base_instrument, _ = create_test_instrument(symbol)
                instrument = base_instrument
            else:
                # For equity symbols
                clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

                from nautilus_trader.test_kit.providers import TestInstrumentProvider

                try:
                    instrument = TestInstrumentProvider.equity(
                        symbol=clean_symbol,
                        venue=venue_str,  # Use extracted venue
                    )
                except Exception:
                    # Fallback
                    base_fx = TestInstrumentProvider.default_fx_ccy("EUR/USD")
                    instrument = base_fx

        # Reason: Configure backtest engine
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
        )

        # Reason: Initialize engine
        self.engine = BacktestEngine(config=config)

        # Reason: Create fill model for realistic execution simulation
        fill_model = FillModel(
            prob_fill_on_limit=0.95,  # 95% fill probability on limit orders
            prob_fill_on_stop=0.95,  # 95% fill probability on stop orders
            prob_slippage=0.01,  # 1% slippage probability
        )

        # Reason: Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Reason: Add venue with dynamic detection
        # Instruments from IBKR will have their actual venue (e.g., NASDAQ)
        # Test instruments will use SIM venue
        # Dynamic venue detection based on instrument or bars
        if instrument and hasattr(instrument, "id") and hasattr(instrument.id, "venue"):
            # Use venue from the actual instrument (e.g., NASDAQ from IBKR)
            venue = instrument.id.venue
        elif (
            bars
            and hasattr(bars[0], "bar_type")
            and hasattr(bars[0].bar_type.instrument_id, "venue")
        ):
            # Fallback: Extract venue from bar data if instrument is missing
            venue = bars[0].bar_type.instrument_id.venue
        else:
            # Final fallback: Use SIM for test scenarios
            venue = Venue("SIM")

        # Store venue for result extraction
        self._venue = venue

        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(self.settings.default_balance, USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Reason: Add instrument (currency is handled by venue setup)
        self.engine.add_instrument(instrument)

        # Reason: Add pre-loaded bars to engine
        self.engine.add_data(bars)

        # Reason: Create bar type string from first bar
        first_bar = bars[0]
        bar_type_str = str(first_bar.bar_type)

        # Reason: Prepare strategy configuration parameters
        from nautilus_trader.model.data import BarType

        # Reason: Ensure instrument is set (guaranteed by this point)
        assert instrument is not None, "Instrument must be set by this point"
        assert hasattr(instrument, "id"), "Instrument must have id attribute"

        # Use StrategyLoader to build parameters dynamically
        config_params = StrategyLoader.build_strategy_params(
            strategy_type=strategy_enum,
            overrides=strategy_params,
            settings=self.settings,
        )

        # Add required base parameters
        config_params.update(
            {
                "instrument_id": instrument.id,
                "bar_type": BarType.from_str(bar_type_str),
            }
        )

        # Reason: Create strategy using StrategyLoader
        strategy = StrategyLoader.create_strategy(strategy_enum, config_params)
        self.engine.add_strategy(strategy=strategy)

        # Create signal collector if signals enabled
        if enable_signals:
            self._signal_collector = SignalCollector(flush_threshold=10_000)
            logger.info("Signal validation enabled for backtest")

        try:
            # Store backtest date range for CAGR calculation
            self._backtest_start_date = start
            self._backtest_end_date = end

            # Reason: Run the backtest
            self.engine.run()

            # Reason: Extract and return results
            self._results = self._extract_results()

            # Handle signal statistics and export after backtest
            signal_stats_dict: dict[str, Any] | None = None
            if enable_signals and self._signal_collector is not None:
                # Calculate signal statistics
                if self._signal_collector.evaluation_count > 0:
                    analyzer = SignalAnalyzer(
                        self._signal_collector.evaluations,
                        near_miss_threshold=0.75,
                    )
                    self._signal_statistics = analyzer.get_statistics()
                    # Convert to dict for JSON serialization
                    from dataclasses import asdict

                    signal_stats_dict = asdict(self._signal_statistics)
                    logger.info(
                        "Signal statistics calculated",
                        total_evaluations=self._signal_statistics.total_evaluations,
                        signal_rate=f"{self._signal_statistics.signal_rate:.1%}",
                        primary_blocker=self._signal_statistics.primary_blocker,
                    )
                else:
                    logger.info("No signal evaluations recorded during backtest")

                # Export to CSV if path provided
                if signal_export_path:
                    from pathlib import Path

                    export_path = Path(signal_export_path)
                    self._signal_collector.export_csv(export_path)
                    logger.info(
                        "Signal audit trail exported",
                        path=str(export_path),
                        evaluation_count=self._signal_collector.evaluation_count,
                    )

            # Persist results to database
            execution_duration = Decimal(str(time.time() - execution_start_time))

            # Ensure start and end are timezone-aware
            start_tz = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
            end_tz = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

            # Determine strategy name from type
            strategy_display_name = strategy_type.replace("_", " ").title()

            # For persistence, we can now use the resolved config_params directly
            # but we filter out the base objects (instrument_id, bar_type)
            # to keep the JSON serializable
            strategy_config_dict = {
                k: str(v) if isinstance(v, (Decimal, UUID)) else v
                for k, v in config_params.items()
                if k not in ["instrument_id", "bar_type"]
            }

            # Add signal statistics to config for WebUI access (T110)
            if signal_stats_dict is not None:
                strategy_config_dict["signal_statistics"] = signal_stats_dict

            run_id = await self._persist_backtest_results(
                result=self._results,
                strategy_name=strategy_display_name,
                strategy_type=strategy_type,
                instrument_symbol=symbol,
                start_date=start_tz,
                end_date=end_tz,
                execution_duration_seconds=execution_duration,
                strategy_config=strategy_config_dict,
                reproduced_from_run_id=reproduced_from_run_id,
            )

            if run_id:
                logger.info(
                    "Backtest completed and persisted",
                    run_id=str(run_id),
                    strategy=strategy_display_name,
                    symbol=symbol,
                )

            # Return both results and run_id for caller
            return self._results, run_id

        except Exception as e:
            # Calculate execution duration at failure point
            execution_duration = Decimal(str(time.time() - execution_start_time))

            # Ensure start and end are timezone-aware
            start_tz = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
            end_tz = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

            # Determine strategy name from type
            strategy_display_name = strategy_type.replace("_", " ").title()

            # Build strategy config for persistence
            strategy_config_dict = {
                "trade_size": str(strategy_params.get("trade_size", 1000000)),
            }

            # Persist failed backtest
            await self._persist_failed_backtest(
                error_message=str(e),
                strategy_name=strategy_display_name,
                strategy_type=strategy_type,
                instrument_symbol=symbol,
                start_date=start_tz,
                end_date=end_tz,
                execution_duration_seconds=execution_duration,
                strategy_config=strategy_config_dict,
            )

            # Re-raise the exception
            raise

    def dispose(self) -> None:
        """Dispose of the engine resources."""
        if self.engine:
            self.engine.dispose()
        self._results = None
