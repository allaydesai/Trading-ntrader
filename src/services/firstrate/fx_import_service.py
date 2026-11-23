"""Streaming import of FirstRate spot FX files into a Parquet catalog (FX M2).

The futures import service already streams a file chunk by chunk, verifies it from the
Parquet footers plus sampled read-backs, guards a rewrite with the ``.import-incomplete``
marker and skips a series whose recorded end and on-disk count match the source. This
subclass reuses all of that and changes only what is FX-specific:

- identity and contract fields come from the pair spec (``pair_specs.yaml``), so the
  ``catalog_instruments`` row is ``ticker EURUSD``, ``nautilus_id EUR/USD.IDEALPRO``,
  ``asset_class FX``, ``currency`` the quote and ``tick_size`` the IB min tick (decision D11);
- bars are ``MID`` (D2): ``EUR/USD.IDEALPRO-1-HOUR-MID-EXTERNAL``, which Nautilus stores
  under the URI-safe directory ``EURUSD.IDEALPRO-1-HOUR-MID-EXTERNAL``;
- files are named ``{PAIR}_full_{tf}.txt`` (no suffix after the timeframe token);
- the rounding and session-anomaly counts of :class:`FirstRateFxParser` become warnings.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import structlog
from nautilus_trader.model.data import BarType

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass, ImportResult
from src.services.firstrate.coverage import timeframe_key
from src.services.firstrate.dry_run import _infer_timeframe
from src.services.firstrate.futures_import_service import (
    _BAR_COUNT_FIELDS,
    INCOMPLETE_MARKER,
    FuturesImportService,
    bar_directory,
    record_coverage,
    verify_series,
    write_series,
)
from src.services.firstrate.import_service import ClassifierDecision
from src.services.firstrate.import_verification import collect_ohlc_warnings
from src.services.firstrate.parsers.firstrate_fx_parser import FirstRateFxParser
from src.services.fx.instrument_builder import fx_instrument_id
from src.services.fx.pair_specs import FxPairSpec, get_pair_spec

logger = structlog.get_logger(__name__)

#: The price type of every FX bar (decision D2; IB ``MIDPOINT`` bars live).
FX_PRICE_TYPE = "MID"


def mid_timeframe(timeframe: str) -> str:
    """``1-HOUR-LAST`` (or ``1-HOUR``) -> ``1-HOUR-MID``."""
    return f"{timeframe_key(timeframe)}-{FX_PRICE_TYPE}"


def apply_pair_spec(row: CatalogInstrument, spec: FxPairSpec) -> CatalogInstrument:
    """Stamp the pair spec's identity and contract fields onto a ``catalog_instruments`` row."""
    row.nautilus_id = str(fx_instrument_id(spec))
    row.asset_class = AssetClass.FX.value
    row.exchange = spec.venue
    row.name = f"{spec.base}/{spec.quote}"
    row.multiplier = Decimal("1")
    row.tick_size = spec.min_tick
    row.currency = spec.quote
    row.contract_kind = None
    row.expiry = None
    row.roll_rule = None
    return row


def new_pair_row(spec: FxPairSpec, catalog_name: str) -> CatalogInstrument:
    """A fresh row for a pair never imported into ``catalog_name``."""
    counts = {f"bar_count_{suffix}": 0 for suffix in _BAR_COUNT_FIELDS}
    row = CatalogInstrument(ticker=spec.pair, catalog_name=catalog_name, **counts)
    return apply_pair_spec(row, spec)


def fx_warnings(parser: FirstRateFxParser, precision: int, out_of_order: int) -> list[str]:
    """The import-summary warnings of one FX file (OHLC flags, then the FX counters)."""
    warnings = collect_ohlc_warnings(parser.last_validation)
    if out_of_order:
        warnings.append(f"{out_of_order} out-of-order row(s) dropped")
    if parser.rounded_rows:
        warnings.append(f"{parser.rounded_rows} row(s) rounded to {precision} decimals")
    if parser.session_anomalies:
        warnings.append(
            f"{parser.session_anomalies} session anomaly bar(s) kept "
            "(outside Sun 17:00-Fri 17:00 ET)"
        )
    return warnings


class FxImportService(FuturesImportService):
    """Chunked, pair-spec-driven variant of the futures import for ``AssetClass.FX``.

    Timeframes are ``MID`` specs (``1-HOUR-MID``); a ``LAST`` spec is accepted and mapped.
    """

    @staticmethod
    def _matches_timeframe(filename: str, timeframe: str | None) -> bool:
        if timeframe is None:
            return True
        inferred = _infer_timeframe(filename, exact_stem=True)
        return timeframe_key(inferred) == timeframe_key(timeframe)

    def _classify_ticker(
        self, ticker: str, file_path: Path, catalog_name: str, timeframe: str
    ) -> ClassifierDecision:
        return super()._classify_ticker(ticker, file_path, catalog_name, mid_timeframe(timeframe))

    def _ensure_pair_row(self, spec: FxPairSpec, catalog_name: str) -> None:
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.pair)
        row = new_pair_row(spec, catalog_name) if row is None else apply_pair_spec(row, spec)
        self._metadata_service.upsert_instrument_sync(row)

    def _import_ticker(
        self,
        ticker: str,
        file_path: Path,
        catalog_name: str,
        asset_class: AssetClass,
        timeframe: str = "1-DAY-MID",
    ) -> ImportResult:
        start = time.perf_counter()
        try:
            spec = get_pair_spec(ticker.upper())
            self._ensure_pair_row(spec, catalog_name)
            decision = self._classify_ticker(spec.pair, file_path, catalog_name, timeframe)
            if decision == "skipped":
                return self._result(spec.pair, start, status="skipped", outcome="skipped")
            return self._import_pair(spec, file_path, catalog_name, timeframe, decision, start)
        except Exception as e:  # noqa: BLE001 — isolate one pair's failure from the batch
            logger.error("fx_import_failed", ticker=ticker, error=str(e), exc_info=True)
            return self._result(ticker, start, status="failed", error=str(e))

    def _import_pair(
        self,
        spec: FxPairSpec,
        file_path: Path,
        catalog_name: str,
        timeframe: str,
        decision: ClassifierDecision,
        start: float,
    ) -> ImportResult:
        catalog = self._catalog_manager.resolve_catalog(catalog_name)
        timeframe = mid_timeframe(timeframe)
        bar_type = BarType.from_str(f"{fx_instrument_id(spec)}-{timeframe}-EXTERNAL")
        parser = FirstRateFxParser(chunk_rows=self._chunk_rows)
        precision = spec.price_precision
        tally = write_series(catalog, parser, file_path, bar_type, precision)
        warnings = fx_warnings(parser, precision, tally.out_of_order)
        failure = (
            "No bars parsed from file"
            if tally.count == 0
            else verify_series(catalog, bar_type, tally, self._sample_size)
        )
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.pair)
        if failure is None and row is None:
            failure = "catalog_instruments row disappeared during import"
        fields = {"row_count": tally.count, "warnings": warnings}
        if failure is not None or row is None:
            return self._result(spec.pair, start, status="failed", error=failure, **fields)
        record_coverage(row, timeframe, tally)
        self._metadata_service.upsert_instrument_sync(row)
        (bar_directory(catalog, bar_type) / INCOMPLETE_MARKER).unlink(missing_ok=True)
        logger.info("fx_series_imported", ticker=spec.pair, timeframe=timeframe, rows=tally.count)
        return self._result(spec.pair, start, status="success", outcome=decision, **fields)
