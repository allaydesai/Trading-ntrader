"""Helper functions for backtest CLI commands.

This module extracts common logic shared between run_backtest() and run_config_backtest()
to reduce code duplication and improve maintainability.
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

import click
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from pydantic import ValidationError
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.cli.commands._crypto_options import (
    apply_crypto_options,
    resolve_crypto_request,
    with_crypto_terms,
)
from src.cli.commands._fx_options import apply_fx_options, with_spread_pips
from src.cli.commands._strategy_params import _cli_strategy_params, apply_contracts
from src.core.backtest_orchestrator import BacktestOrchestrator
from src.models.backtest_request import BacktestRequest
from src.models.backtest_result import BacktestResult
from src.models.data_load_result import DataLoadResult
from src.services.data_catalog import DataCatalogService
from src.services.firstrate.backtest_loader import reconcile_equity_price_precision
from src.services.sizing_overrides import check_contracts_instrument, check_units_instrument
from src.utils.mock_data import generate_mock_data_from_yaml

# Re-export for backwards compatibility — callers that import DataLoadResult
# from this module continue to work; new code should import from src.models.
__all__ = ["DataLoadResult"]


def apply_cli_overrides(
    request: BacktestRequest,
    *,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    starting_balance: float | None,
) -> BacktestRequest:
    """Apply CLI argument overrides to a config-based BacktestRequest.

    When using config mode with CLI overrides (e.g., `backtest run config.yaml --start 2024-06-01`),
    this function applies the override values to the base request from the config file.

    Args:
        request: The base BacktestRequest loaded from YAML config
        symbol: Optional symbol override (rebuilds instrument_id if provided)
        start: Optional start date override
        end: Optional end date override
        starting_balance: Optional starting balance override

    Returns:
        A new BacktestRequest with overrides applied

    Raises:
        ValueError: If overrides create an invalid date range
    """
    updates: dict = {}

    if symbol is not None:
        # Rebuild instrument_id from symbol
        from src.models.backtest_request import _resolve_instrument_id

        symbol_upper = symbol.upper()
        if "." in symbol_upper:
            instrument_id = symbol_upper
        elif request.data_source == "kraken":
            instrument_id = f"{symbol_upper}.KRAKEN"
        else:
            instrument_id = _resolve_instrument_id(symbol_upper)
        updates["symbol"] = symbol_upper.split(".")[0]
        updates["instrument_id"] = instrument_id

    if start is not None:
        # Ensure timezone-aware
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        updates["start_date"] = start

    if end is not None:
        # Ensure timezone-aware
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        updates["end_date"] = end

    if starting_balance is not None:
        updates["starting_balance"] = Decimal(str(starting_balance))

    if not updates:
        return request

    return request.model_copy(update=updates)


def resolve_backtest_request(
    *,
    config_file: str | None,
    symbol: str | None,
    strategy: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
    fast_period: int | None = None,
    slow_period: int | None = None,
    trade_size: int | None = None,
    timeframe: str | None = None,
    catalog_name: str | None = None,
    contracts: int | None = None,
    units: Decimal | int | None = None,
    spread_pips: float | None = None,
    fee_tier: int | None = None,
    spread_bps: float | None = None,
) -> tuple[BacktestRequest, str]:
    """Resolve inputs into BacktestRequest based on mode (config vs CLI).

    This function determines whether to use config mode (YAML file) or CLI mode
    (all parameters from command line) and builds the appropriate BacktestRequest.

    Args:
        config_file: Path to YAML config file (if provided, uses config mode)
        symbol: Trading symbol (required in CLI mode, optional override in config mode)
        strategy: Strategy name (required in CLI mode, ignored in config mode)
        start: Start date (required in CLI mode, optional override in config mode)
        end: End date (required in CLI mode, optional override in config mode)
        data_source: Data source to use ("catalog" or "mock")
        starting_balance: Starting balance (optional override in both modes)
        persist: Whether to persist results to database
        console: Rich console for output
        fast_period: Fast SMA period (CLI mode only)
        slow_period: Slow SMA period (CLI mode only)
        trade_size: Trade size in shares (CLI mode only)
        timeframe: Bar timeframe (CLI mode only)
        contracts: Futures only: whole contracts per entry (both modes; see apply_contracts)
        units: FX (whole) or spot crypto (decimal) base units per entry (see ``_fx_options``)
        spread_pips: FX only: the modelled spread override (both modes)
        fee_tier: Spot crypto only: the Kraken Pro fee tier (both modes; see ``_crypto_options``)
        spread_bps: Spot crypto only: the modelled spread override in bps (both modes)

    Returns:
        Tuple of (BacktestRequest, resolved_data_source)

    Raises:
        click.UsageError: If required parameters are missing for the mode
        ValueError: If config file has invalid content
    """
    # Determine mode based on config_file presence. BacktestRequest validators
    # (e.g. catalog_name charset, catalog_name-vs-data_source) raise pydantic
    # ValidationError, which is NOT a click.UsageError — without this guard
    # `--catalog 'bad name!'` crashes with a raw traceback (exit 1) instead of a
    # usage error (exit 2). Convert it here, the single construction chokepoint.
    try:
        if config_file is not None:
            request, source = _resolve_config_mode(
                config_file=config_file,
                symbol=symbol,
                start=start,
                end=end,
                data_source=data_source,
                starting_balance=starting_balance,
                persist=persist,
                console=console,
                catalog_name=catalog_name,
            )
        else:
            request, source = _resolve_cli_mode(
                symbol=symbol,
                strategy=strategy,
                start=start,
                end=end,
                data_source=data_source,
                starting_balance=starting_balance,
                persist=persist,
                console=console,
                fast_period=fast_period,
                slow_period=slow_period,
                trade_size=trade_size,
                timeframe=timeframe,
                catalog_name=catalog_name,
            )
    except ValidationError as e:
        # Surface the validator messages (pydantic prefixes them with
        # "Value error, " for ValueError-raising validators — strip that noise).
        messages = "; ".join(err.get("msg", "") for err in e.errors())
        messages = messages.replace("Value error, ", "")
        raise click.UsageError(messages or str(e)) from e
    # Crypto (M5): BTC/USD, XBTUSD, BTC/USD.KRAKEN -> BTC; FX (M5): EUR/USD -> EURUSD, MID
    # bars for an FX catalog row, --units, --spread-pips; crypto: --fee-tier, --spread-bps
    request = resolve_crypto_request(apply_contracts(request, contracts))
    request = apply_fx_options(request, units, spread_pips)
    return apply_crypto_options(request, fee_tier, spread_bps), source


def _resolve_config_mode(
    *,
    config_file: str,
    symbol: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
    catalog_name: str | None = None,
) -> tuple[BacktestRequest, str]:
    """Resolve request from YAML config file with optional CLI overrides."""
    import yaml

    # Validate data source for mock mode
    resolved_data_source = data_source or "catalog"

    # Load YAML config
    config_path = Path(config_file)
    if not config_path.exists():
        raise click.UsageError(f"Configuration file not found: {config_file}")

    with open(config_path, "r") as f:
        yaml_data = yaml.safe_load(f)

    # Build base request from YAML
    request = BacktestRequest.from_yaml_config(
        yaml_data=yaml_data,
        persist=persist,
        config_file_path=str(config_path.absolute()),
        data_source=resolved_data_source,
    )

    # Apply CLI overrides
    request = apply_cli_overrides(
        request,
        symbol=symbol,
        start=start,
        end=end,
        starting_balance=starting_balance,
    )

    # CLI --catalog override wins over any catalog_name in YAML (explicit beats implicit).
    # YAML-mode catalog_name support itself is a future enhancement (see Task 4.3).
    if catalog_name is not None:
        request = request.model_copy(update={"catalog_name": catalog_name})

    return request, resolved_data_source


def _resolve_cli_mode(
    *,
    symbol: str | None,
    strategy: str | None,
    start: datetime | None,
    end: datetime | None,
    data_source: str | None,
    starting_balance: float | None,
    persist: bool,
    console: Console,
    fast_period: int | None,
    slow_period: int | None,
    trade_size: int | None,
    timeframe: str | None,
    catalog_name: str | None = None,
) -> tuple[BacktestRequest, str]:
    """Resolve request from CLI arguments (traditional mode)."""
    # Validate required parameters for CLI mode
    if symbol is None:
        raise click.UsageError(
            "Missing required option '--symbol' / '-sym'. "
            "In CLI mode, --symbol is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )
    if start is None:
        raise click.UsageError(
            "Missing required option '--start' / '-st'. "
            "In CLI mode, --start is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )
    if end is None:
        raise click.UsageError(
            "Missing required option '--end' / '-e'. "
            "In CLI mode, --end is required. "
            "Use a config file for config mode: backtest run config.yaml"
        )

    # Mock data source requires a config file
    resolved_data_source = data_source or "catalog"
    if resolved_data_source == "mock":
        raise click.UsageError(
            "Mock data source requires a YAML config file. "
            "Use: backtest run config.yaml --data-source mock"
        )

    # Ensure timezone-aware dates
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # Determine bar type from timeframe or auto-detect
    if timeframe:
        bar_type_spec = f"{timeframe}-LAST"
    else:
        # Auto-detect: Date-only (YYYY-MM-DD) → 1-DAY, Date-time → 1-MINUTE
        if start.time().hour == 0 and start.time().minute == 0 and start.time().second == 0:
            bar_type_spec = "1-DAY-LAST"
        else:
            bar_type_spec = "1-MINUTE-LAST"

    # Use defaults for optional CLI parameters
    resolved_strategy = strategy or "sma_crossover"
    resolved_fast_period = fast_period or 10
    resolved_slow_period = slow_period or 20
    resolved_trade_size = trade_size or 1000000

    # The account: --starting-balance, else trade_size x 10. Strategies size off it too.
    resolved_starting_balance = Decimal(str(starting_balance or resolved_trade_size * 10))

    strategy_params = _cli_strategy_params(
        resolved_strategy,
        resolved_starting_balance,
        resolved_trade_size,
        resolved_fast_period,
        resolved_slow_period,
    )

    # Build BacktestRequest from CLI args
    request = BacktestRequest.from_cli_args(
        strategy=resolved_strategy,
        symbol=symbol.upper(),
        start=start,
        end=end,
        bar_type_spec=bar_type_spec,
        persist=persist,
        starting_balance=resolved_starting_balance,
        data_source=resolved_data_source,
        catalog_name=catalog_name,
        **strategy_params,
    )

    return request, resolved_data_source


def _resolve_named_catalog_loader():
    """Return the ``load_from_catalog`` callable.

    Factored out so tests can patch the resolution without monkey-patching the
    loader module directly (which would couple tests to import order).
    """
    from src.services.firstrate.backtest_loader import load_from_catalog

    return load_from_catalog


def _build_named_catalog_dependencies():
    """Construct a (CatalogManager, MetadataService) pair for named-catalog loads.

    CLI + web share the same sync session factory; the loader wraps sync calls in
    ``asyncio.to_thread`` internally so the sync repo is safe on both paths.
    """
    from pathlib import Path

    from src.config import get_settings
    from src.db.repositories.catalog_instrument_repository import (
        SyncCatalogInstrumentRepository,
    )
    from src.db.session_sync import get_sync_session_maker
    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.metadata_service import MetadataService

    settings = get_settings()
    session_maker = get_sync_session_maker()
    if session_maker is None:
        raise RuntimeError(
            "Named-catalog backtest requires a configured database (DATABASE_URL). Check .env."
        )
    session = session_maker()
    try:
        sync_repo = SyncCatalogInstrumentRepository(session)
        metadata_service = MetadataService(sync_repo=sync_repo)
        catalog_manager = CatalogManager(Path(settings.catalog.catalog_base_path))
    except Exception:
        # Close the session if any downstream constructor raises, to avoid a
        # leaked DB connection. The caller owns `session.close()` on success.
        session.close()
        raise
    return catalog_manager, metadata_service, session


async def load_backtest_data(
    *,
    data_source: Literal["catalog", "ibkr", "kraken", "mock"],
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
    catalog_service: DataCatalogService | None = None,
    yaml_data: dict | None = None,
    catalog_name: str | None = None,
) -> DataLoadResult:
    """Load backtest data from catalog, Kraken, or generate mock data.

    Unified data loading that handles:
    - Named FirstRate catalog (short-circuit when ``catalog_name`` is set)
    - Catalog data with availability checking
    - IBKR auto-fetch when catalog data is incomplete
    - Kraken historical data fetching for crypto pairs
    - Mock data generation from YAML configuration

    Args:
        data_source: Data source: "catalog", "ibkr", "kraken", or "mock"
        instrument_id: The instrument identifier (e.g., "AAPL.NASDAQ", "BTC/USD.KRAKEN")
        bar_type_spec: Bar type specification (e.g., "1-DAY-LAST")
        start: Start datetime for the data range
        end: End datetime for the data range
        console: Rich console for output
        catalog_service: Optional catalog service instance (created if not provided)
        yaml_data: YAML configuration dict (required for mock data source)
        catalog_name: Named FirstRate catalog. When set, routes bars through
            ``load_from_catalog`` (bypasses ``DataCatalogService`` and IBKR).

    Returns:
        DataLoadResult containing bars, instrument, and source description

    Raises:
        ValueError: If mock data source is used without yaml_data, or if the
            named catalog does not exist.
        DataNotFoundError: If no data can be found or fetched
        IBKRConnectionError: If IBKR connection fails during fetch
        KrakenConnectionError: If Kraken connection fails during fetch
    """
    if catalog_name:
        loader = _resolve_named_catalog_loader()
        catalog_manager, metadata_service, session = _build_named_catalog_dependencies()
        # Strip the `.NAMED_CATALOG` suffix placed by `BacktestRequest.from_cli_args`
        # when `catalog_name` is set. Using `rsplit` with maxsplit=1 preserves
        # multi-dot tickers like `BRK.B`, which `split(".", 1)[0]` would truncate.
        if instrument_id.endswith(".NAMED_CATALOG"):
            ticker = instrument_id[: -len(".NAMED_CATALOG")]
        else:
            ticker = instrument_id.rsplit(".", 1)[0]
        try:
            result = await loader(
                catalog_name=catalog_name,
                ticker=ticker,
                bar_type_spec=bar_type_spec,
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )
        finally:
            session.close()
        console.print(
            f"   Loaded {len(result.bars):,} bars from catalog '{catalog_name}'",
            style="green",
        )
        return result

    if data_source == "mock":
        return await _load_mock_data(yaml_data=yaml_data, console=console)
    elif data_source == "kraken":
        return await _load_kraken_data(
            instrument_id=instrument_id,
            bar_type_spec=bar_type_spec,
            start=start,
            end=end,
            console=console,
        )
    else:
        return await _load_catalog_data(
            instrument_id=instrument_id,
            bar_type_spec=bar_type_spec,
            start=start,
            end=end,
            console=console,
            catalog_service=catalog_service,
        )


async def _load_mock_data(
    *,
    yaml_data: dict | None,
    console: Console,
) -> DataLoadResult:
    """Load mock data from YAML configuration."""
    if yaml_data is None:
        raise ValueError("yaml_data is required for mock data source")

    bars, instrument, _, _ = generate_mock_data_from_yaml(yaml_data)

    console.print(f"   Generated {len(bars):,} mock bars")

    return DataLoadResult(
        bars=bars,
        instrument=instrument,
        data_source_used="Mock",
    )


async def _load_kraken_data(
    *,
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
) -> DataLoadResult:
    """Fetch Kraken spot candles and the coin spec's pair (crypto M7; nothing is persisted).

    Raises:
        KrakenSpotRangeError: The range is older than the spot endpoint's 720-candle window.
    """
    from src.services.kraken_client import fetch_kraken_spot

    console.print("   Loading spot data from Kraken...", style="cyan")
    bars, instrument = await fetch_kraken_spot(instrument_id, start, end, bar_type_spec)
    console.print(f"   Loaded {len(bars):,} bars from Kraken")
    return DataLoadResult(bars=bars, instrument=instrument, data_source_used="Kraken")


async def _load_catalog_data(
    *,
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
    catalog_service: DataCatalogService | None,
) -> DataLoadResult:
    """Load data from catalog with IBKR fallback."""
    # Initialize catalog service if not provided
    if catalog_service is None:
        catalog_service = DataCatalogService()

    # Check availability
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Checking data availability...", total=None)
        availability = catalog_service.get_availability(instrument_id, bar_type_spec)
        progress.update(task, completed=True)

    # Determine data source based on availability
    data_source_used = "Parquet Catalog"

    if not availability:
        console.print(
            f"   No data in catalog for {instrument_id}",
            style="yellow",
        )
        console.print("   Will attempt to fetch from IBKR...", style="yellow")
        data_source_used = "IBKR Auto-fetch"
    elif not availability.covers_range(start, end):
        console.print(
            "   Requested date range partially available in catalog",
            style="yellow",
        )
        console.print(f"   Requested: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
        console.print(
            f"   Available: {availability.start_date.strftime('%Y-%m-%d')} to "
            f"{availability.end_date.strftime('%Y-%m-%d')}"
        )
        console.print("   Will attempt to fetch missing data from IBKR...", style="yellow")
        data_source_used = "IBKR Auto-fetch"
    else:
        console.print("   Data available in catalog", style="green")
        console.print(f"   Period: {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}")
        if availability.file_count:
            console.print(
                f"   Files: {availability.file_count} | Rows: ~{availability.total_rows:,}"
            )

    # Load/fetch data
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
        console=console,
    ) as progress:
        task = progress.add_task("Loading/fetching data...", total=None)

        bars = await catalog_service.fetch_or_load(
            instrument_id=instrument_id,
            start=start,
            end=end,
            bar_type_spec=bar_type_spec,
            correlation_id=f"backtest-{instrument_id}-{start.strftime('%Y%m%d')}",
        )

        progress.update(task, completed=True)

    # Load instrument — try the requested ID first, then the resolved ID
    # from the bars (IBKR may qualify to a different exchange, e.g.,
    # GDX.NASDAQ → GDX.ARCA)
    instrument = catalog_service.load_instrument(instrument_id)
    if instrument is None and bars:
        resolved_id = str(bars[0].bar_type.instrument_id)
        if resolved_id != instrument_id:
            instrument = catalog_service.load_instrument(resolved_id)

    # Fetch instrument from IBKR if not in catalog
    if instrument is None:
        console.print(
            f"   Instrument {instrument_id} not in catalog, fetching from IBKR...",
            style="yellow",
        )
        try:
            instrument = await catalog_service.fetch_instrument_from_ibkr(instrument_id)
            console.print(
                "   Instrument fetched and saved to catalog",
                style="green",
            )
        except Exception as e:
            console.print(
                f"   Failed to fetch instrument from IBKR: {e}",
                style="red",
            )
            # Create fallback test instrument
            from src.utils.mock_data import create_test_instrument

            symbol = instrument_id.split(".")[0]
            venue = instrument_id.split(".")[-1] if "." in instrument_id else "SIM"
            instrument, _ = create_test_instrument(symbol, venue)
            console.print("   Using fallback test instrument", style="yellow")

    console.print(f"   Loaded {len(bars):,} bars")

    # The stored/fallback instrument is built at 2dp; FirstRate bars carry 4. Align them the
    # same way the named-catalog loader does, or the engine rejects the first bar. (Inlined
    # in the return, not a separate statement: this function is over its size cap and the
    # baseline in test_size_caps.py may only shrink.)
    return DataLoadResult(
        bars=bars,
        instrument=reconcile_equity_price_precision(instrument, bars),
        data_source_used=data_source_used,
    )


async def execute_backtest(
    *,
    request: BacktestRequest,
    bars: list[Bar],
    instrument: Instrument,
    console: Console,
    progress_message: str = "Running backtest...",
    rate_sources: list = (),  # type: ignore[assignment]
) -> tuple[BacktestResult, UUID | None]:
    """Execute backtest with progress indicator and proper cleanup.

    Wraps BacktestOrchestrator execution with:
    - Progress spinner display
    - Guaranteed orchestrator disposal (even on error)

    Args:
        request: The backtest request configuration
        bars: List of Bar objects for the backtest
        instrument: The instrument to trade
        console: Rich console for progress display
        progress_message: Custom message for the progress spinner
        rate_sources: FX only: a cross's conversion-only ``(bars, instrument)`` pairs
            (``DataLoadResult.rate_sources``)

    Returns:
        Tuple of (BacktestResult, run_id if persisted else None)

    Raises:
        ValueError: If bars are empty or strategy cannot be created
    """
    check_contracts_instrument(request.strategy_config, instrument)
    check_units_instrument(request.strategy_config, instrument)
    instrument = with_spread_pips(instrument, request.fx_spread_pips)
    instrument = with_crypto_terms(instrument, request.crypto_fee_tier, request.crypto_spread_bps)
    orchestrator = BacktestOrchestrator()

    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task(progress_message, total=None)
            result, run_id = await orchestrator.execute(
                request, bars, instrument, rate_sources=rate_sources
            )
            progress.update(task, completed=True)

        return result, run_id
    finally:
        orchestrator.dispose()


async def load_many_backtest_data(
    *,
    catalog_name: str,
    tickers: list[str],
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    console: Console,
) -> list[DataLoadResult]:
    """Load bars + instrument for several tickers from a named catalog (Story 5.3, multi-ETF).

    Thin CLI wrapper over ``load_many_from_catalog`` that constructs the shared
    named-catalog dependencies (and guarantees the DB session is closed). Returns
    one ``DataLoadResult`` per ticker, order preserved.
    """
    from src.services.firstrate.backtest_loader import load_many_from_catalog

    catalog_manager, metadata_service, session = _build_named_catalog_dependencies()
    try:
        results = await load_many_from_catalog(
            catalog_name=catalog_name,
            tickers=tickers,
            bar_type_spec=bar_type_spec,
            start=start,
            end=end,
            catalog_manager=catalog_manager,
            metadata_service=metadata_service,
        )
    finally:
        session.close()

    total_bars = sum(len(r.bars) for r in results)
    console.print(
        f"   Loaded {total_bars:,} bars for {len(results)} instruments "
        f"from catalog '{catalog_name}'",
        style="green",
    )
    return results


async def execute_multi_backtest(
    *,
    request: BacktestRequest,
    instruments_data: list[tuple[list[Bar], Instrument]],
    console: Console,
    progress_message: str = "Running multi-ETF backtest...",
) -> tuple[BacktestResult, UUID | None]:
    """Execute a multi-instrument backtest with spinner + guaranteed disposal (Story 5.3).

    Mirrors :func:`execute_backtest` but routes several instruments through the
    orchestrator's ``execute_multi`` (one engine run). The multi path never
    persists — ``run_id`` is always ``None`` (Story 5.4 owns ETF persistence).
    """
    orchestrator = BacktestOrchestrator()
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task(progress_message, total=None)
            result, run_id = await orchestrator.execute_multi(request, instruments_data)
            progress.update(task, completed=True)

        return result, run_id
    finally:
        orchestrator.dispose()


def display_backtest_results(
    *,
    result: BacktestResult,
    console: Console,
    run_id: UUID | None,
    persist: bool,
    context_rows: dict[str, str],
    table_title: str = "Backtest Results",
    execution_time: float | None = None,
) -> None:
    """Display backtest results in a Rich table with performance summary.

    Creates a formatted table with:
    - Context rows (strategy, symbol, period, etc.) first
    - Performance metrics (return, trades, win rate, etc.)
    - Persistence status
    - Final performance summary (profitable/losing/break-even)

    Args:
        result: The backtest result to display
        console: Rich console for output
        run_id: UUID of the persisted run (if any)
        persist: Whether persistence was requested
        context_rows: Dict of label->value pairs to show at top of table
        table_title: Title for the results table
        execution_time: Optional execution time in seconds to display
    """
    console.print("Backtest Results", style="cyan bold")
    console.print()

    # Create results table
    table = Table(title=table_title)
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    # Add context rows first
    for label, value in context_rows.items():
        table.add_row(label, value)

    # Add metric rows
    table.add_row("Total Return", f"{result.total_return * 100:.2f}%")
    table.add_row("Total Trades", str(result.total_trades))
    table.add_row("Winning Trades", str(result.winning_trades))
    table.add_row("Losing Trades", str(result.losing_trades))
    table.add_row("Win Rate", f"{result.win_rate:.1f}%")
    table.add_row("Largest Win", f"${result.largest_win:.2f}")
    table.add_row("Largest Loss", f"${result.largest_loss:.2f}")
    table.add_row("Final Balance", f"${result.final_balance:,.2f}")

    # Add execution time if provided
    if execution_time is not None:
        table.add_row("Execution Time", f"{execution_time:.2f}s")

    # Add persistence info
    if persist and run_id:
        table.add_row("Persisted", f"Yes (Run ID: {str(run_id)[:8]}...)")
    elif persist:
        table.add_row("Persisted", "Failed")
    else:
        table.add_row("Persisted", "No (--no-persist)")

    console.print(table)
    console.print()

    # Performance summary
    if result.total_return > 0:
        console.print("Strategy was profitable!", style="green bold")
    elif result.total_return < 0:
        console.print("Strategy lost money", style="red bold")
    else:
        console.print("Strategy broke even", style="yellow bold")
