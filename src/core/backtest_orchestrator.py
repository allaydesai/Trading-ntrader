"""
Unified backtest orchestrator.

This module provides a single entry point for executing backtests with optional
persistence, regardless of whether the request comes from CLI arguments or YAML config.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading.strategy import Strategy

from src.config import get_settings
from src.core.fee_models import IBKRCommissionModel
from src.core.results_extractor import ResultsExtractor
from src.core.strategy_factory import StrategyFactory, StrategyLoader
from src.core.strategy_registry import StrategyRegistry
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.session import get_session
from src.models.backtest_request import BacktestRequest
from src.models.backtest_result import BacktestResult
from src.models.strategy import StrategyType
from src.services.backtest_persistence import BacktestPersistenceService

logger = structlog.get_logger(__name__)


class BacktestOrchestrator:
    """
    Unified backtest execution with optional persistence.

    This class provides a single entry point for executing backtests from either
    CLI arguments or YAML configuration files. It handles:
    - Engine setup and teardown
    - Strategy creation from various sources
    - Results extraction
    - Optional database persistence

    Example:
        >>> orchestrator = BacktestOrchestrator()
        >>> request = BacktestRequest.from_cli_args(strategy="sma_crossover", ...)
        >>> result, run_id = await orchestrator.execute(request, bars, instrument)
        >>> orchestrator.dispose()
    """

    def __init__(self):
        """Initialize the orchestrator."""
        self.settings = get_settings()
        self.engine: BacktestEngine | None = None
        self._venue: Venue | None = None
        self._backtest_start_date: datetime | None = None
        self._backtest_end_date: datetime | None = None

    async def execute(
        self,
        request: BacktestRequest,
        bars: list[Bar],
        instrument: Instrument,
    ) -> tuple[BacktestResult, UUID | None]:
        """
        Execute backtest with optional persistence.

        Args:
            request: Unified backtest request containing all parameters
            bars: Pre-loaded Bar objects from catalog
            instrument: Instrument object for the backtest

        Returns:
            Tuple of (BacktestResult, run_id if persisted else None)

        Raises:
            ValueError: If bars are empty or strategy cannot be created
        """
        execution_start_time = time.time()
        run_id = uuid4() if request.persist else None

        if not bars:
            raise ValueError("No bars provided for backtest")

        try:
            # Setup engine
            self._setup_engine(request, bars, instrument)

            # Create and add strategy
            strategy = self._create_strategy(request, bars, instrument)
            if strategy is None:
                raise ValueError(f"Failed to create strategy: {request.strategy_type}")

            # Type guard: engine is guaranteed to be set by _setup_engine
            assert self.engine is not None, "Engine must be initialized"
            self.engine.add_strategy(strategy)

            # Store date range for CAGR calculation
            self._backtest_start_date = request.start_date
            self._backtest_end_date = request.end_date

            # Run backtest
            self.engine.run()

            # Extract results
            result = self._extract_results()

            # Persist if requested
            if request.persist and run_id:
                execution_duration = Decimal(str(time.time() - execution_start_time))
                await self._persist_results(
                    run_id=run_id,
                    request=request,
                    result=result,
                    execution_duration=execution_duration,
                )
                logger.info(
                    "Backtest completed and persisted",
                    run_id=str(run_id),
                    strategy=request.strategy_type,
                    symbol=request.symbol,
                )

            return result, run_id

        except Exception as e:
            # Persist failed backtest if persistence was requested
            if request.persist:
                execution_duration = Decimal(str(time.time() - execution_start_time))
                await self._persist_failed(
                    request=request,
                    error_message=str(e),
                    execution_duration=execution_duration,
                )
            raise

    def _setup_engine(
        self,
        request: BacktestRequest,
        bars: list[Bar],
        instrument: Instrument,
    ) -> None:
        """
        Setup the backtest engine with venue, instrument, and data.

        Args:
            request: Backtest request with configuration
            bars: Bar data for the backtest
            instrument: Instrument to trade
        """
        # Configure engine
        config = BacktestEngineConfig(trader_id=TraderId("BACKTESTER-001"))
        self.engine = BacktestEngine(config=config)

        # Create fill model
        fill_model = FillModel(
            prob_fill_on_limit=0.95,
            prob_fill_on_stop=0.95,
            prob_slippage=0.01,
        )

        # Create commission model
        fee_model = IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

        # Determine venue from instrument or bars
        if hasattr(instrument, "id") and hasattr(instrument.id, "venue"):
            venue = instrument.id.venue
        elif bars and hasattr(bars[0], "bar_type"):
            venue = bars[0].bar_type.instrument_id.venue
        else:
            venue = Venue("SIM")

        self._venue = venue

        # Add venue
        self.engine.add_venue(
            venue=venue,
            oms_type=OmsType.HEDGING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(float(request.starting_balance), USD)],
            fill_model=fill_model,
            fee_model=fee_model,
        )

        # Add instrument and data
        self.engine.add_instrument(instrument)
        self.engine.add_data(bars)

    def _create_strategy(
        self,
        request: BacktestRequest,
        bars: list[Bar],
        instrument: Instrument,
    ) -> Strategy:
        """
        Create strategy based on request configuration.

        Args:
            request: Backtest request with strategy configuration
            bars: Bar data (needed for bar_type)
            instrument: Instrument for strategy

        Returns:
            Configured strategy instance

        Raises:
            ValueError: If strategy cannot be created
        """
        # Get bar type from first bar
        bar_type = bars[0].bar_type

        # Build base config parameters
        config_params = {
            "instrument_id": instrument.id,
            "bar_type": bar_type,
        }

        # Add strategy-specific parameters from request
        config_params.update(request.strategy_config)

        # Try to use StrategyFactory for config-based strategies
        if request.strategy_path and request.config_path:
            try:
                return StrategyFactory.create_strategy_from_config(
                    strategy_path=request.strategy_path,
                    config_path=request.config_path,
                    config_params=config_params,
                )
            except Exception as e:
                logger.debug(f"StrategyFactory failed, trying StrategyLoader: {e}")

        # Fallback to StrategyLoader for CLI-based strategies
        try:
            # Resolve strategy type to enum
            StrategyRegistry.discover()
            strategy_def = StrategyRegistry.get(request.strategy_type)
            strategy_enum = StrategyType(strategy_def.name)

            # Build parameters using StrategyLoader
            loader_params = StrategyLoader.build_strategy_params(
                strategy_type=strategy_enum,
                overrides=request.strategy_config,
                settings=self.settings,
            )
            loader_params.update(
                {
                    "instrument_id": instrument.id,
                    "bar_type": bar_type,
                }
            )

            return StrategyLoader.create_strategy(strategy_enum, loader_params)
        except Exception as e:
            logger.error(f"Failed to create strategy: {e}")
            raise ValueError(f"Failed to create strategy {request.strategy_type}: {e}")

    def _extract_results(self) -> BacktestResult:
        """
        Extract comprehensive results from the backtest engine.

        Returns:
            BacktestResult with all available metrics
        """
        if not self.engine:
            return BacktestResult()

        extractor = ResultsExtractor(
            engine=self.engine,
            venue=self._venue,
            settings=self.settings,
        )

        return extractor.extract_results(
            start_date=self._backtest_start_date,
            end_date=self._backtest_end_date,
        )

    def _extract_equity_curve(self) -> list[dict[str, int | float]]:
        """
        Extract equity curve for chart visualization.

        Returns:
            List of equity points: [{"time": unix_ts, "value": equity}, ...]
        """
        if not self.engine:
            return []

        extractor = ResultsExtractor(
            engine=self.engine,
            venue=self._venue,
            settings=self.settings,
        )

        return extractor.extract_equity_curve(
            start_date=self._backtest_start_date,
            end_date=self._backtest_end_date,
        )

    async def _persist_results(
        self,
        run_id: UUID,
        request: BacktestRequest,
        result: BacktestResult,
        execution_duration: Decimal,
    ) -> None:
        """Persist successful backtest results to database."""
        try:
            # Build config snapshot
            config_snapshot: dict[str, Any] = {
                "strategy_path": request.strategy_path,
                "config_path": request.config_path,
                "version": "1.0",
                "config": request.strategy_config,
            }

            # Add equity curve if available
            equity_curve = self._extract_equity_curve()
            if equity_curve:
                config_snapshot["equity_curve"] = equity_curve

            # Add config file path if available
            if request.config_file_path:
                config_snapshot["config_file_path"] = request.config_file_path

            # Ensure dates are timezone-aware
            start_tz = (
                request.start_date
                if request.start_date.tzinfo
                else request.start_date.replace(tzinfo=timezone.utc)
            )
            end_tz = (
                request.end_date
                if request.end_date.tzinfo
                else request.end_date.replace(tzinfo=timezone.utc)
            )

            strategy_display_name = request.strategy_type.replace("_", " ").title()

            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                backtest_run = await service.save_backtest_results(
                    run_id=run_id,
                    strategy_name=strategy_display_name,
                    strategy_type=request.strategy_type,
                    instrument_symbol=request.symbol,
                    start_date=start_tz,
                    end_date=end_tz,
                    initial_capital=request.starting_balance,
                    data_source=request.data_source,
                    execution_duration_seconds=execution_duration,
                    config_snapshot=config_snapshot,
                    backtest_result=result,
                )

                # Capture trades from positions report
                if self.engine:
                    try:
                        positions_df = self.engine.trader.generate_positions_report()
                        if positions_df is not None and not positions_df.empty:
                            await service.save_trades_from_positions(
                                backtest_run_id=backtest_run.id,
                                positions_report_df=positions_df,
                            )
                    except Exception as e:
                        logger.warning(f"Failed to capture trades: {e}")

                await session.commit()

            logger.info("Backtest results persisted", run_id=str(run_id))

        except Exception as e:
            logger.warning(f"Failed to persist backtest results: {e}")

    async def _persist_failed(
        self,
        request: BacktestRequest,
        error_message: str,
        execution_duration: Decimal,
    ) -> None:
        """Persist failed backtest to database."""
        try:
            run_id = uuid4()

            config_snapshot: dict[str, Any] = {
                "strategy_path": request.strategy_path,
                "config_path": request.config_path,
                "version": "1.0",
                "config": request.strategy_config,
            }

            start_tz = (
                request.start_date
                if request.start_date.tzinfo
                else request.start_date.replace(tzinfo=timezone.utc)
            )
            end_tz = (
                request.end_date
                if request.end_date.tzinfo
                else request.end_date.replace(tzinfo=timezone.utc)
            )

            strategy_display_name = request.strategy_type.replace("_", " ").title()

            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                await service.save_failed_backtest(
                    run_id=run_id,
                    strategy_name=strategy_display_name,
                    strategy_type=request.strategy_type,
                    instrument_symbol=request.symbol,
                    start_date=start_tz,
                    end_date=end_tz,
                    initial_capital=request.starting_balance,
                    data_source=request.data_source,
                    execution_duration_seconds=execution_duration,
                    config_snapshot=config_snapshot,
                    error_message=error_message,
                )

                await session.commit()

            logger.info("Failed backtest persisted", run_id=str(run_id))

        except Exception as e:
            logger.warning(f"Failed to persist failed backtest: {e}")

    def dispose(self) -> None:
        """Dispose of engine resources."""
        if self.engine:
            self.engine.dispose()
        self._venue = None
        self._backtest_start_date = None
        self._backtest_end_date = None
