"""Named-catalog-backed bar loader for backtests (Story 3.1).

Resolves a named FirstRate catalog + ticker + bar-type-spec + date range to a
ready-to-run ``DataLoadResult`` (bars + synthesised instrument). The DB-backed
``MetadataService`` is the authoritative source for the Nautilus instrument ID;
``ParquetDataCatalog.bars()`` is the authoritative source for bars. The loader
never falls back to IBKR or the default NAUTILUS_PATH catalog.
"""

import asyncio
from datetime import datetime

import structlog
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity, Instrument
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.fx_rates import conversion_pairs_for, fx_ticker
from src.models.data_load_result import DataLoadResult
from src.services.exceptions import DataNotFoundError, UnknownCatalogError
from src.services.firstrate.catalog_manager import CatalogManager
from src.services.firstrate.metadata_service import MetadataService
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec
from src.services.metadata.backtestable import non_backtestable_reason

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


FUTURES_ASSET_CLASS = "FUTURES"
FX_ASSET_CLASS = "FX"


def build_instrument(asset_class, nautilus_id: str, ticker: str, bars=None):
    """Build the Nautilus instrument for a catalog row, keyed on its ``asset_class``.

    ``FUTURES`` builds a continuous-series ``FuturesContract`` from the hand-curated
    contract spec for ``ticker`` (the futures root, e.g. ``ES``), with precision taken from
    the bars when given. ``FX`` builds a spot ``CurrencyPair`` from the pair spec for
    ``ticker`` (``EURUSD``), likewise. Every other asset class, including ``None``, goes
    through :func:`build_equity` unchanged.
    """
    kind = str(asset_class or "").upper()
    if kind not in (FUTURES_ASSET_CLASS, FX_ASSET_CLASS):
        return build_equity(nautilus_id=nautilus_id, ticker=ticker, bars=bars)
    precision = _infer_price_precision(bars) if bars else None
    if kind == FX_ASSET_CLASS:
        return build_fx_pair(
            get_pair_spec(ticker), nautilus_id=nautilus_id, price_precision=precision
        )
    return build_futures_contract(
        get_contract_spec(ticker), nautilus_id=nautilus_id, price_precision=precision
    )


def reconcile_equity_price_precision(instrument, bars):
    """Return ``instrument`` with its price precision aligned to ``bars``.

    The default (no ``--catalog``) path loads the instrument the importer stored in the
    catalog, which carries ``price_precision=2``; FirstRate bars carry 4 decimals and the
    engine rejects the mismatch. When ``instrument`` is an ``Equity`` whose precision differs
    from the bars', it is rebuilt through :func:`build_equity` so this path and the
    named-catalog path derive precision identically. Anything else (non-``Equity``
    instruments, empty bars, an already-matching precision) is returned unchanged.
    """
    if not bars or not isinstance(instrument, Equity):
        return instrument
    if _infer_price_precision(bars) == instrument.price_precision:
        return instrument
    return build_equity(
        nautilus_id=str(instrument.id),
        ticker=instrument.id.symbol.value,
        bars=bars,
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
        # Non-backtestable fail-fast (Story 3.5 exclusion contract, Story 5.1 AC2).
        # ``qualification_sync`` keeps ``nautilus_id`` non-NULL exactly when the venue
        # is RESOLVED, so a null identity IS the exclusion signal — a non-backtestable
        # instrument can never silently enter a run. Raise BEFORE resolving the
        # catalog / reading bars so the filesystem is never touched.
        #
        # The status lookup happens only here, on the failure path: it costs one query
        # that the happy path never pays, and it is what lets the message name the
        # action that would actually fix this ticker. An excluded instrument told to
        # "resolve its venue" sends the operator hunting for something already
        # established not to exist.
        status = await asyncio.to_thread(metadata_service.get_resolution_status_sync, ticker)
        raise DataNotFoundError(
            instrument_id=ticker,
            start=start,
            end=end,
            message=non_backtestable_reason(status, ticker, catalog_name),
            context={
                "venue_unresolved": True,
                "catalog": catalog_name,
                "resolution_status": status.value if status else None,
            },
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

    instrument = build_instrument(row.asset_class, nautilus_id, ticker, bars)
    _check_bar_venue(bars, instrument, ticker, catalog_name)
    rate_sources = await _load_rate_sources(
        instrument, bars, catalog_name, bar_type_spec, start, end, catalog_manager, metadata_service
    )
    return DataLoadResult(
        bars=bars,
        instrument=instrument,
        data_source_used=f"Catalog: {catalog_name}",
        rate_sources=rate_sources,
    )


def _check_bar_venue(bars, instrument, ticker: str, catalog_name: str) -> None:
    """The venue on the first bar MUST match the synthesised instrument.

    Silent venue drift leads to Nautilus' "strict order" error at engine setup; fail fast
    here with a clearer message.
    """
    first_bar_venue = str(bars[0].bar_type.instrument_id.venue)
    instrument_venue = str(instrument.id.venue)
    if first_bar_venue != instrument_venue:
        raise ValueError(
            f"Venue mismatch for '{ticker}' in catalog '{catalog_name}': "
            f"DB says '{instrument_venue}' but bars say '{first_bar_venue}'. "
            "Catalog metadata may be stale — re-run import."
        )


def _bar_dates(bars: list[Bar]) -> tuple[str, str]:
    """The UTC dates of the first and last bar, ISO formatted."""
    first, last = (unix_nanos_to_dt(b.ts_event).date().isoformat() for b in (bars[0], bars[-1]))
    return first, last


def _check_rate_coverage(
    pair: str, rate_bars: list[Bar], instrument: Instrument, traded_bars: list[Bar], **where
) -> None:
    """Refuse a conversion series that does not span the traded pair's dates (QA fix, Q3).

    Compared by UTC date of the first and last bar, so a pair whose hourly edges differ by a
    bar is accepted; a series that starts later or ends earlier than the traded one would
    leave part of the run without a USD rate and fail it at the first signal there.

    Raises:
        DataNotFoundError: The conversion series misses dates the traded pair has.
    """
    covered, traded = _bar_dates(rate_bars), _bar_dates(traded_bars)
    if covered[0] <= traded[0] and covered[1] >= traded[1]:
        return
    ticker = fx_ticker(instrument)
    start, end = where["start"], where["end"]
    window = f"{start.date().isoformat()} to {end.date().isoformat()}"
    overlap = f"{max(covered[0], traded[0])} to {min(covered[1], traded[1])}"
    raise DataNotFoundError(
        instrument_id=pair,
        start=start,
        end=end,
        message=(
            f"FX conversion pair {pair} in catalog '{where['catalog']}' covers {covered[0]} to "
            f"{covered[1]} at {where['spec']}, but {ticker} has bars from {traded[0]} to "
            f"{traded[1]} in the requested window ({window}); {ticker} P&L cannot be "
            f"converted to USD outside {pair}'s dates. Import {pair} for the whole window, or "
            f"run {overlap} only."
        ),
        context={
            "catalog": where["catalog"],
            "conversion_pair_for": str(instrument.id),
            "conversion_coverage": covered,
            "traded_coverage": traded,
        },
    )


async def _load_rate_sources(
    instrument: Instrument,
    traded_bars: list[Bar],
    catalog_name: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    catalog_manager: CatalogManager,
    metadata_service: MetadataService,
) -> list[tuple[list[Bar], Instrument]]:
    """An FX cross's conversion-only pairs (GBPUSD for EURGBP), same catalog and timeframe.

    Every other instrument (USD-quote and USD-base pairs, equities, futures) needs none and
    costs no extra read. A run never proceeds unconverted: a conversion pair the catalog
    lacks, has no bars for in the window, or covers fewer dates than the traded pair's bars
    (:func:`_check_rate_coverage`), fails the load naming it (FX M3, design D4).
    """
    sources: list[tuple[list[Bar], Instrument]] = []
    for pair in conversion_pairs_for([instrument]):
        try:
            loaded = await load_from_catalog(
                catalog_name=catalog_name,
                ticker=pair,
                bar_type_spec=bar_type_spec,
                start=start,
                end=end,
                catalog_manager=catalog_manager,
                metadata_service=metadata_service,
            )
        except DataNotFoundError as e:
            raise DataNotFoundError(
                instrument_id=pair,
                start=start,
                end=end,
                message=(
                    f"FX conversion pair {pair} is not available in catalog '{catalog_name}' "
                    f"at {bar_type_spec}; {fx_ticker(instrument)} P&L cannot be converted to "
                    f"USD without it: {e}"
                ),
                context={"catalog": catalog_name, "conversion_pair_for": str(instrument.id)},
            ) from e
        _check_rate_coverage(
            pair,
            loaded.bars,
            instrument,
            traded_bars,
            catalog=catalog_name,
            spec=bar_type_spec,
            start=start,
            end=end,
        )
        sources.append((loaded.bars, loaded.instrument))
    return sources


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
