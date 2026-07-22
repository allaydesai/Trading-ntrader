"""Named-catalog-backed bar loader for backtests (Story 3.1).

Resolves a named FirstRate catalog + ticker + bar-type-spec + date range to a
ready-to-run ``DataLoadResult`` (bars + synthesised instrument). The DB-backed
``MetadataService`` is the authoritative source for the Nautilus instrument ID;
``ParquetDataCatalog.bars()`` is the authoritative source for bars. The loader
never falls back to IBKR or the default NAUTILUS_PATH catalog.
"""

import asyncio

import structlog
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.models.data_load_result import DataLoadResult
from src.services.exceptions import DataNotFoundError, UnknownCatalogError
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.metadata_service import MetadataService

logger = structlog.get_logger(__name__)


def _build_bar_type(nautilus_id: str, bar_type_spec: str) -> str:
    """Build the ``{nautilus_id}-{bar_type_spec}-EXTERNAL`` BarType string."""
    return f"{nautilus_id}-{bar_type_spec}-EXTERNAL"


def _infer_price_precision(bars) -> int:
    """Infer price precision from the first bar's Price objects.

    FirstRate 1-minute bars typically carry 4 decimal places; daily bars 2.
    The Equity instrument MUST match or the engine raises
    ``invalid bar.open.precision=N did not match self.instrument.price_precision=M``.
    """
    if not bars:
        return 2
    try:
        return bars[0].open.precision
    except AttributeError:
        # Fallback: parse from string representation.
        s = str(bars[0].open)
        if "." in s:
            return len(s.split(".", 1)[1])
        return 2


def build_equity(nautilus_id: str, ticker: str, bars=None):
    """Synthesise an ``Equity`` instrument for the given nautilus_id.

    Uses ``InstrumentId.from_str`` to parse the venue — correctly handles
    multi-dot tickers like ``BRK.B.NYSE`` where ``split('.')[-1]`` would fail.

    When ``bars`` are provided the returned Equity's ``price_precision`` is
    inferred from ``bars[0].open.precision`` — required because FirstRate
    intraday bars carry precision=4 but ``TestInstrumentProvider.equity``
    defaults to precision=2.
    """
    inst_id = InstrumentId.from_str(nautilus_id)
    venue_str = str(inst_id.venue)

    if bars is None:
        # Legacy callers (and tests that only check venue) get the default equity.
        return TestInstrumentProvider.equity(symbol=ticker, venue=venue_str)

    precision = _infer_price_precision(bars)
    increment_str = "0." + "0" * (precision - 1) + "1" if precision > 0 else "1"

    # Preserve the original ticker in instrument_id so it matches bar_type.instrument_id
    # exactly (strict Nautilus check). No truncation — Nautilus's Symbol accepts
    # the full ticker; truncating would break the instrument.id == bar_type.instrument_id
    # assertion that the engine performs at setup time.
    inst_symbol = Symbol(ticker)

    return Equity(
        instrument_id=InstrumentId(symbol=inst_symbol, venue=Venue(venue_str)),
        raw_symbol=inst_symbol,
        currency=USD,
        price_precision=precision,
        price_increment=Price.from_str(increment_str),
        lot_size=Quantity.from_int(1),
        isin=None,
        ts_event=0,
        ts_init=0,
    )


async def load_from_catalog(
    *,
    catalog_name: str,
    ticker: str,
    bar_type_spec: str,
    start,
    end,
    catalog_manager: CatalogManager,
    metadata_service: MetadataService,
) -> DataLoadResult:
    """Load bars + instrument from a named catalog.

    Args:
        catalog_name: Named catalog (e.g., ``"e2e-test"``).
        ticker: Instrument ticker (e.g., ``"AAPL"``).
        bar_type_spec: Bar type spec (e.g., ``"1-MINUTE-LAST"``).
        start: Backtest start (timezone-aware datetime).
        end: Backtest end (timezone-aware datetime).
        catalog_manager: Injected ``CatalogManager`` (testability).
        metadata_service: Injected ``MetadataService`` (testability).

    Returns:
        ``DataLoadResult`` with bars, synthesised instrument, and
        ``data_source_used=f"Catalog: {catalog_name}"``.

    Raises:
        ValueError: Catalog name not found (surfaced with available list).
        DataNotFoundError: Ticker absent from the catalog's DB metadata or
            catalog read returns zero bars for the requested window.
    """
    # DB-authoritative ticker resolution FIRST — cheaper than a catalog miss and
    # avoids touching the filesystem when the ticker was never imported.
    row = await asyncio.to_thread(metadata_service.get_instrument_sync, catalog_name, ticker)
    if row is None:
        raise DataNotFoundError(
            instrument_id=ticker,
            start=start,
            end=end,
            message=(
                f"Ticker '{ticker}' not found in catalog '{catalog_name}'. "
                "Import it via `ntrader data import-csv` or choose a different catalog."
            ),
            context={"missing_from_catalog": catalog_name},
        )

    nautilus_id = row.nautilus_id
    if not nautilus_id:
        # Unresolved-venue fail-fast (Story 3.5 exclusion contract, Story 5.1 AC2).
        # ``InstrumentMapper.sync_qualification`` nulls ``nautilus_id`` for a
        # ``VENUE_UNRESOLVED`` ticker (ETF or stock) — leaving the on-disk Parquet
        # intact but the identity unqualified. Nulling the identity IS the exclusion
        # mechanism this loader honors: a non-backtestable instrument can never
        # silently enter a run. Raise BEFORE resolving the catalog / reading bars so
        # the filesystem is never touched for an excluded instrument. The
        # ``venue_unresolved`` context flag lets callers/tests distinguish this
        # intentional exclusion from a plain missing-ticker or empty-window miss.
        raise DataNotFoundError(
            instrument_id=ticker,
            start=start,
            end=end,
            message=(
                f"'{ticker}' in catalog '{catalog_name}' has an unresolved venue "
                "(non-backtestable, Story 3.5) — its nautilus_id is unset, so it is "
                "excluded from backtests. Resolve its venue (metadata resolution / "
                "venue_overrides.csv) and re-import to admit it."
            ),
            context={"venue_unresolved": True, "catalog": catalog_name},
        )

    try:
        catalog = catalog_manager.resolve_catalog(catalog_name)
    except FileNotFoundError:
        raise UnknownCatalogError(catalog_name, catalog_manager.list_catalogs()) from None

    bar_type_str = _build_bar_type(nautilus_id, bar_type_spec)

    logger.info(
        "catalog_backtest_loader_querying",
        catalog=catalog_name,
        ticker=ticker,
        nautilus_id=nautilus_id,
        bar_type=bar_type_str,
        start=start.isoformat() if hasattr(start, "isoformat") else str(start),
        end=end.isoformat() if hasattr(end, "isoformat") else str(end),
    )

    bars = await asyncio.to_thread(catalog.bars, bar_types=[bar_type_str], start=start, end=end)

    if not bars:
        raise DataNotFoundError(
            instrument_id=ticker,
            start=start,
            end=end,
            message=(
                f"No bars for '{ticker}' in catalog '{catalog_name}' "
                f"between {start.isoformat()} and {end.isoformat()}. "
                f"Catalog metadata covers {row.date_range_start} → {row.date_range_end}."
            ),
            context={
                "catalog": catalog_name,
                "metadata_range": (row.date_range_start, row.date_range_end),
            },
        )

    instrument = build_equity(nautilus_id=nautilus_id, ticker=ticker, bars=bars)

    # Defensive: the venue on the first bar MUST match the synthesised instrument.
    # Silent venue drift leads to Nautilus' "strict order" error at engine setup —
    # fail fast here with a clearer message.
    first_bar_venue = str(bars[0].bar_type.instrument_id.venue)
    instrument_venue = str(instrument.id.venue)
    if first_bar_venue != instrument_venue:
        raise ValueError(
            f"Venue mismatch for '{ticker}' in catalog '{catalog_name}': "
            f"DB says '{instrument_venue}' but bars say '{first_bar_venue}'. "
            "Catalog metadata may be stale — re-run import."
        )

    return DataLoadResult(
        bars=bars,
        instrument=instrument,
        data_source_used=f"Catalog: {catalog_name}",
    )


async def load_many_from_catalog(
    *,
    catalog_name: str,
    tickers: list[str],
    bar_type_spec: str,
    start,
    end,
    catalog_manager: CatalogManager,
    metadata_service: MetadataService,
) -> list[DataLoadResult]:
    """Load bars + instrument for several tickers from one named catalog (Story 5.3).

    A thin, order-preserving loop over :func:`load_from_catalog` — one
    ``DataLoadResult`` per ticker, each carrying its own venue-qualified
    ``Equity`` (mixed venues supported, e.g. ``IVV.ARCA`` + ``TQQQ.NASDAQ``).
    No new resolution logic and no runtime adapter: the multi-ETF path reuses
    the exact single-instrument loader per ticker.

    Any unresolved-venue / missing ticker fails fast (``DataNotFoundError``
    from ``load_from_catalog``) so no instrument is silently dropped from a
    multi-ETF run. Ticker order is preserved; the caller's list is authoritative
    (no dedup — venue dedup is the orchestrator's concern).

    Raises:
        ValueError: ``tickers`` is empty.
        DataNotFoundError / UnknownCatalogError: propagated per ticker.
    """
    if not tickers:
        raise ValueError("load_many_from_catalog requires at least one ticker")

    results: list[DataLoadResult] = []
    for ticker in tickers:
        results.append(
            await load_from_catalog(
                catalog_name=catalog_name,
                ticker=ticker,
                bar_type_spec=bar_type_spec,
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )
        )
    return results
