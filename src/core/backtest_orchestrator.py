"""
Unified backtest orchestrator.

This module provides a single entry point for executing backtests with optional
persistence, regardless of whether the request comes from CLI arguments or YAML config.
"""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading.strategy import Strategy

from src.config import get_settings
from src.core.fee_models import IBKRCommissionModel
from src.core.fill_model import make_fill_model
from src.core.results_extractor import ResultsExtractor, metrics_basis
from src.core.strategy_factory import StrategyFactory, StrategyLoader
from src.core.strategy_registry import StrategyRegistry
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.session import get_session
from src.models.backtest_request import BacktestRequest
from src.models.backtest_result import BacktestResult
from src.models.run_provenance import RunProvenance
from src.services.backtest_persistence import BacktestPersistenceService, store_equity_curve
from src.services.provenance import run_provenance

logger = structlog.get_logger(__name__)


def _make_json_serializable(obj: Any) -> Any:
    """
    Recursively convert Decimal values to strings for JSON serialization.

    Args:
        obj: Object to convert (dict, list, or scalar)

    Returns:
        JSON-serializable version of the object
    """
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_make_json_serializable(item) for item in obj]
    elif isinstance(obj, Decimal):
        return str(obj)
    return obj


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _run_record_fields(request: BacktestRequest) -> dict[str, Any]:
    """Run-row fields shared by the success and failure persistence paths."""
    return {
        "strategy_name": request.strategy_type.replace("_", " ").title(),
        "strategy_type": request.strategy_type,
        "instrument_symbol": request.symbol,
        "start_date": _utc(request.start_date),
        "end_date": _utc(request.end_date),
        "initial_capital": request.starting_balance,
        "data_source": request.to_persistence_data_source(),
        "config_snapshot": {
            "strategy_path": request.strategy_path,
            "config_path": request.config_path,
            "version": "1.0",
            "config": _make_json_serializable(request.strategy_config),
            "bar_type": request.bar_type,
            "fill_seed": request.fill_seed,
        },
    }


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
        self._starting_balance: float | None = None
        self._extractor: ResultsExtractor | None = None

    async def execute(
        self,
        request: BacktestRequest,
        bars: list[Bar],
        instrument: Instrument,
        provenance: RunProvenance | None = None,
        reproduced_from_run_id: UUID | None = None,
    ) -> tuple[BacktestResult, UUID | None]:
        """
        Execute backtest with optional persistence.

        Args:
            request: Unified backtest request containing all parameters
            bars: Pre-loaded Bar objects from catalog
            instrument: Instrument object for the backtest
            provenance: Git state and config hash to record; computed from the
                working tree and ``request`` when omitted
            reproduced_from_run_id: The run this one reproduces, recorded on the
                persisted run (MCP ``reproduce_run``)

        Returns:
            Tuple of (BacktestResult, run_id if persisted else None)

        Raises:
            ValueError: If bars are empty or strategy cannot be created
        """
        execution_start_time = time.time()
        run_id = uuid4() if request.persist else None

        if not bars:
            raise ValueError("No bars provided for backtest")
        if request.persist and provenance is None:
            # Off the event loop: several git subprocesses, each with a timeout.
            provenance = await asyncio.to_thread(run_provenance, request)

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

            # Store date range and starting balance for results extraction
            self._backtest_start_date = request.start_date
            self._backtest_end_date = request.end_date
            self._starting_balance = float(request.starting_balance)

            # Run backtest
            self.engine.run()

            # Extract results
            self._extractor = self._make_extractor({instrument.id: bars})
            result = self._extract_results(self._starting_balance)

            # Persist if requested
            if request.persist and run_id:
                execution_duration = Decimal(str(time.time() - execution_start_time))
                await self._persist_results(
                    run_id=run_id,
                    request=request,
                    result=result,
                    execution_duration=execution_duration,
                    provenance=provenance,
                    reproduced_from_run_id=reproduced_from_run_id,
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
                    provenance=provenance,
                )
            raise

    async def execute_multi(
        self,
        request: BacktestRequest,
        instruments_data: list[tuple[list[Bar], Instrument]],
    ) -> tuple[BacktestResult, UUID | None]:
        """Execute a single multi-instrument backtest (Story 5.3 — multi-ETF).

        Routes several venue-qualified instruments through **one** engine run:
        each unique venue is added once, every instrument + its bars are added,
        and **one strategy instance per instrument** (each with a distinct
        ``order_id_tag``) is registered before a single ``engine.run()``. This is
        the same engine path single-instrument runs use — no runtime adapter.

        The multi-ETF path does **not** persist (Story 5.4 owns ETF-results
        persistence); ``run_id`` is always ``None``. When the instruments span
        more than one venue (each venue gets its own account seeded with the
        starting balance), the summary ``BacktestResult`` reports the **portfolio
        aggregate** balance/return across all venue accounts — consistent with
        the portfolio-wide trade/PnL stats (see ``_aggregate_multi_venue_balance``).

        Args:
            request: Shared backtest configuration (strategy, dates, balance).
            instruments_data: Ordered ``(bars, instrument)`` pairs — the
                instrument list the run spans. Instrument ids must be unique.

        Returns:
            Tuple of (BacktestResult, None).

        Raises:
            ValueError: No instruments, empty bars, duplicate instrument id, or
                strategy failure.
        """
        if not instruments_data:
            raise ValueError("No instruments provided for multi-instrument backtest")
        seen_ids: set[str] = set()
        for bars, instrument in instruments_data:
            if not bars:
                raise ValueError(
                    f"No bars provided for instrument {getattr(instrument, 'id', '?')}"
                )
            inst_id = str(instrument.id)
            if inst_id in seen_ids:
                raise ValueError(
                    f"Duplicate instrument '{inst_id}' in multi-instrument backtest — "
                    "each instrument may appear at most once."
                )
            seen_ids.add(inst_id)

        # Setup engine: dedup venues, add all instruments + data.
        self._setup_engine_multi(request, instruments_data)
        assert self.engine is not None, "Engine must be initialized"

        # One strategy per instrument, each with a distinct order_id_tag (no id collision).
        for idx, (bars, instrument) in enumerate(instruments_data):
            strategy = self._create_strategy(request, bars, instrument, order_id_tag=str(idx))
            if strategy is None:
                raise ValueError(f"Failed to create strategy: {request.strategy_type}")
            self.engine.add_strategy(strategy)

        self._backtest_start_date = request.start_date
        self._backtest_end_date = request.end_date
        self._starting_balance = float(request.starting_balance)

        self.engine.run()

        self._extractor = self._make_extractor({i.id: bars for bars, i in instruments_data})
        result = self._extract_results(self._starting_balance)
        result = self._aggregate_multi_venue_balance(result, instruments_data)
        return result, None

    def _aggregate_multi_venue_balance(
        self,
        result: BacktestResult,
        instruments_data: list[tuple[list[Bar], Instrument]],
    ) -> BacktestResult:
        """Make the summary balance/return the portfolio aggregate across all venues.

        ``_extract_results`` reads a single venue's account (``self._venue``), but
        the trade/PnL stats it reports are portfolio-wide. For a run spanning
        multiple venues (each with its own account seeded with the starting
        balance), that single-account balance under-reports the portfolio and is
        inconsistent with the aggregated trade stats. Here we sum ``final_balance``
        across every venue account and recompute ``total_return`` against the total
        deployed capital (unique-venue count × starting balance).

        Single-venue runs (all instruments on one account) are returned unchanged —
        the extractor's account is already the whole portfolio.
        """
        if not self.engine:
            return result

        venues = {instrument.id.venue for _bars, instrument in instruments_data}
        if len(venues) <= 1:
            return result

        starting_balance = float(self._starting_balance or 0.0)
        total_start = len(venues) * starting_balance
        total_final = 0.0
        for venue in venues:
            account = self.engine.cache.account_for_venue(venue)
            if account is not None:
                total_final += float(account.balance_total(USD).as_double())

        result.final_balance = total_final
        if total_start:
            result.total_return = (total_final - total_start) / total_start
        return result

    def _make_fee_model(self) -> IBKRCommissionModel:
        """Construct the shared IBKR commission model from settings."""
        return IBKRCommissionModel(
            commission_per_share=self.settings.commission_per_share,
            min_per_order=self.settings.commission_min_per_order,
            max_rate=self.settings.commission_max_rate,
        )

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
        # Configure engine — bypass Nautilus logging if already initialized
        # (e.g., when running from the web server process)
        logging_config = LoggingConfig(bypass_logging=is_logging_initialized())
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=logging_config,
        )

        # Create fill + commission models (shared construction — see helpers)
        fill_model = make_fill_model(request.fill_seed)
        fee_model = self._make_fee_model()

        self.engine = BacktestEngine(config=config)

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

    def _setup_engine_multi(
        self,
        request: BacktestRequest,
        instruments_data: list[tuple[list[Bar], Instrument]],
    ) -> None:
        """Setup one engine for a multi-instrument run — venue dedup, N instruments + data.

        Each unique venue is added exactly once (Nautilus rejects a duplicate
        venue and each venue owns its own account — this isolation IS the
        no-cross-instrument-venue-bleed guarantee). The primary venue (first
        instrument's) drives summary results extraction.
        """
        logging_config = LoggingConfig(bypass_logging=is_logging_initialized())
        config = BacktestEngineConfig(
            trader_id=TraderId("BACKTESTER-001"),
            logging=logging_config,
        )
        fill_model = make_fill_model(request.fill_seed)
        fee_model = self._make_fee_model()
        engine = BacktestEngine(config=config)

        venues_added: set[Venue] = set()
        for bars, instrument in instruments_data:
            venue = instrument.id.venue
            if venue not in venues_added:
                engine.add_venue(
                    venue=venue,
                    oms_type=OmsType.HEDGING,
                    account_type=AccountType.MARGIN,
                    starting_balances=[Money(float(request.starting_balance), USD)],
                    fill_model=fill_model,
                    fee_model=fee_model,
                )
                venues_added.add(venue)
            engine.add_instrument(instrument)
            engine.add_data(bars)

        self.engine = engine
        # Primary venue for summary results extraction = first instrument's venue.
        self._venue = instruments_data[0][1].id.venue

    def _create_strategy(
        self,
        request: BacktestRequest,
        bars: list[Bar],
        instrument: Instrument,
        order_id_tag: str | None = None,
    ) -> Strategy:
        """
        Create strategy based on request configuration.

        Args:
            request: Backtest request with strategy configuration
            bars: Bar data (needed for bar_type)
            instrument: Instrument for strategy
            order_id_tag: Optional distinct Nautilus ``order_id_tag`` so multiple
                strategy instances of the same class (one per instrument in a
                multi-instrument run) do not collide on strategy id. ``None``
                leaves the framework default (single-instrument path unchanged).

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

        # Distinct strategy id per instrument (multi-instrument runs) — omit when
        # None so the single-instrument path keeps the default tag unchanged.
        if order_id_tag is not None:
            config_params["order_id_tag"] = order_id_tag

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
            # Resolve strategy type to canonical name
            StrategyRegistry.discover()
            strategy_def = StrategyRegistry.get(request.strategy_type)
            strategy_name = strategy_def.name

            # Build parameters using StrategyLoader
            loader_params = StrategyLoader.build_strategy_params(
                strategy_type=strategy_name,
                overrides=request.strategy_config,
                settings=self.settings,
            )
            loader_params.update(
                {
                    "instrument_id": instrument.id,
                    "bar_type": bar_type,
                }
            )
            if order_id_tag is not None:
                loader_params["order_id_tag"] = order_id_tag

            return StrategyLoader.create_strategy(strategy_name, loader_params)
        except Exception as e:
            logger.error(f"Failed to create strategy: {e}")
            raise ValueError(f"Failed to create strategy {request.strategy_type}: {e}")

    def _make_extractor(self, bars_by_instrument: dict[Any, list[Bar]]) -> ResultsExtractor:
        """The run's results extractor; given the bars, it marks risk metrics to market."""
        return ResultsExtractor(
            engine=self.engine,
            venue=self._venue,
            settings=self.settings,
            starting_balance=self._starting_balance,
            bars_by_instrument=bars_by_instrument,
        )

    def _extract_results(self, starting_balance: float) -> BacktestResult:
        """Extract comprehensive results from the finished engine run."""
        if not self.engine or self._extractor is None:
            return BacktestResult()
        return self._extractor.extract_results(
            start_date=self._backtest_start_date,
            end_date=self._backtest_end_date,
        )

    def _extract_equity_curve(self, starting_balance: float) -> list[dict[str, int | float]]:
        """Equity points for the chart: ``[{"time": unix_ts, "value": equity}, ...]``."""
        if not self.engine or self._extractor is None:
            return []
        return self._extractor.extract_equity_curve(
            start_date=self._backtest_start_date,
            end_date=self._backtest_end_date,
        )

    async def _persist_results(
        self,
        run_id: UUID,
        request: BacktestRequest,
        result: BacktestResult,
        execution_duration: Decimal,
        provenance: RunProvenance | None = None,
        reproduced_from_run_id: UUID | None = None,
    ) -> None:
        """Persist successful backtest results to database."""
        try:
            fields = _run_record_fields(request)
            equity_curve = self._extract_equity_curve(float(request.starting_balance))
            fields["config_snapshot"]["metrics_basis"] = metrics_basis(self._extractor)
            if request.config_file_path:
                fields["config_snapshot"]["config_file_path"] = request.config_file_path

            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                backtest_run = await service.save_backtest_results(
                    run_id=run_id,
                    **fields,
                    execution_duration_seconds=execution_duration,
                    backtest_result=result,
                    reproduced_from_run_id=reproduced_from_run_id,
                    provenance=provenance,
                )
                await store_equity_curve(session, backtest_run.id, equity_curve)

                # Capture trades from positions report. Trade capture is best-effort
                # — the run and its metrics are the primary record — but the failure
                # must be contained: if a flush inside bulk_create_trades fails, the
                # session is left in a failed state, so swallowing the error here and
                # committing below would raise again and lose the run and metrics
                # too. The savepoint keeps the trade write's failure local.
                if self.engine:
                    try:
                        positions_df = self.engine.trader.generate_positions_report()
                        if positions_df is not None and not positions_df.empty:
                            async with session.begin_nested():
                                await service.save_trades_from_positions(
                                    backtest_run_id=backtest_run.id,
                                    positions_report_df=positions_df,
                                )
                    except Exception as e:
                        logger.warning(
                            f"Failed to capture trades (run and metrics still persisted): {e}",
                            exc_info=True,
                        )

                await session.commit()

            logger.info("Backtest results persisted", run_id=str(run_id))

        except Exception as e:
            logger.warning(f"Failed to persist backtest results: {e}", exc_info=True)

    async def _persist_failed(
        self,
        request: BacktestRequest,
        error_message: str,
        execution_duration: Decimal,
        provenance: RunProvenance | None = None,
    ) -> None:
        """Persist failed backtest to database."""
        try:
            run_id = uuid4()
            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestPersistenceService(repository)

                await service.save_failed_backtest(
                    run_id=run_id,
                    **_run_record_fields(request),
                    execution_duration_seconds=execution_duration,
                    error_message=error_message,
                    provenance=provenance,
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
        self._starting_balance = None
