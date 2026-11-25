"""Streaming import of FirstRate spot crypto files into a Parquet catalog (crypto M2).

The futures import service already streams a file chunk by chunk, verifies it from the
Parquet footers plus sampled read-backs, guards a rewrite with the ``.import-incomplete``
marker and skips a series whose recorded end and on-disk count match the source. This
subclass reuses all of that and changes only what is crypto-specific:

- identity and contract fields come from the coin spec (``coin_specs.yaml``), so the
  ``catalog_instruments`` row is ``ticker BTC``, ``nautilus_id BTC/USD.KRAKEN``,
  ``asset_class CRYPTO``, ``exchange KRAKEN``, ``currency USD`` and ``tick_size`` the price
  increment of the spec's precision (design section 1, decision C11);
- bars are trade prices, ``LAST``: ``BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL``, which Nautilus
  stores under the URI-safe directory ``BTCUSD.KRAKEN-1-HOUR-LAST-EXTERNAL``;
- files are named ``{COIN}_full_{tf}.txt`` (no suffix after the timeframe token);
- the files are **UTC**, so the re-run classifier probes the source as UTC (the inherited
  classifier reads every FirstRate file as New York time and would never skip a crypto
  series);
- the counters of :class:`FirstRateCryptoParser` become warnings (decision C15: kept, never
  dropped or filled).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog
from nautilus_trader.model.data import BarType

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass, ImportResult
from src.services.crypto.coin_specs import CoinSpec, get_coin_spec
from src.services.crypto.instrument_builder import (
    CRYPTO_ASSET_CLASS,
    KRAKEN_VENUE,
    crypto_instrument_id,
)
from src.services.firstrate.coverage import from_ns, timeframe_key
from src.services.firstrate.dry_run import _infer_timeframe
from src.services.firstrate.futures_import_service import (
    _BAR_COUNT_FIELDS,
    INCOMPLETE_MARKER,
    FuturesImportService,
    bar_directory,
    record_coverage,
    series_complete,
    verify_series,
    write_series,
)
from src.services.firstrate.import_service import (
    ClassifierDecision,
    _bar_count_field_for_timeframe,
    _date_range_end_field_for_timeframe,
)
from src.services.firstrate.import_verification import collect_ohlc_warnings
from src.services.firstrate.parsers.firstrate_crypto_parser import (
    GAP_BARS,
    FirstRateCryptoParser,
)
from src.services.firstrate.source_probe import compute_source_last_date

logger = structlog.get_logger(__name__)

#: The price type of every crypto bar: trade prices, as Kraken OHLC and trade bars are.
CRYPTO_PRICE_TYPE = "LAST"


def last_timeframe(timeframe: str) -> str:
    """``1-HOUR-MID`` (or ``1-HOUR``) -> ``1-HOUR-LAST``."""
    return f"{timeframe_key(timeframe)}-{CRYPTO_PRICE_TYPE}"


def apply_coin_spec(row: CatalogInstrument, spec: CoinSpec) -> CatalogInstrument:
    """Stamp the coin spec's identity and contract fields onto a ``catalog_instruments`` row."""
    row.nautilus_id = str(crypto_instrument_id(spec))
    row.asset_class = CRYPTO_ASSET_CLASS
    row.exchange = KRAKEN_VENUE
    row.name = f"{spec.base}/{spec.quote}"
    row.multiplier = Decimal("1")
    row.tick_size = Decimal(1).scaleb(-spec.price_precision)
    row.currency = spec.quote
    row.contract_kind = None
    row.expiry = None
    row.roll_rule = None
    return row


def new_coin_row(spec: CoinSpec, catalog_name: str) -> CatalogInstrument:
    """A fresh row for a coin never imported into ``catalog_name``."""
    counts = {f"bar_count_{suffix}": 0 for suffix in _BAR_COUNT_FIELDS}
    row = CatalogInstrument(ticker=spec.coin, catalog_name=catalog_name, **counts)
    return apply_coin_spec(row, spec)


def crypto_warnings(parser: FirstRateCryptoParser, precision: int, out_of_order: int) -> list[str]:
    """The import-summary warnings of one crypto file (OHLC flags, then the crypto counters)."""
    warnings = collect_ohlc_warnings(parser.last_validation)
    found = parser.anomalies
    if out_of_order:
        warnings.append(f"{out_of_order} out-of-order row(s) dropped")
    if found.rounded_rows:
        warnings.append(f"{found.rounded_rows} row(s) rounded to {precision} decimals")
    if found.zero_volume:
        warnings.append(f"{found.zero_volume} zero-volume bar(s) kept")
    if found.gaps:
        warnings.append(
            f"{found.gaps} gap(s) longer than {GAP_BARS} bars "
            f"(longest {found.longest_gap_bars} bars missing)"
        )
    if found.whole_volume_from_ns is not None:
        since = from_ns(found.whole_volume_from_ns)
        warnings.append(
            f"volume is whole-coin from {since:%Y-%m-%d %H:%M} UTC ({parser.volume_note})"
        )
    return warnings


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class CryptoImportService(FuturesImportService):
    """Chunked, coin-spec-driven variant of the futures import for ``AssetClass.CRYPTO``.

    Timeframes are ``LAST`` specs (``1-HOUR-LAST``); another price type is mapped to ``LAST``.
    The Kraken importer (M7) swaps the parser and the source-end probe.
    """

    parser_class: type[FirstRateCryptoParser] = FirstRateCryptoParser

    @staticmethod
    def _source_last(file_path: Path) -> datetime | None:
        return compute_source_last_date(file_path, source_tz=timezone.utc)

    @staticmethod
    def _matches_timeframe(filename: str, timeframe: str | None) -> bool:
        if timeframe is None:
            return True
        inferred = _infer_timeframe(filename, exact_stem=True)
        return timeframe_key(inferred) == timeframe_key(timeframe)

    def _classify_ticker(
        self, ticker: str, file_path: Path, catalog_name: str, timeframe: str
    ) -> ClassifierDecision:
        """Skip only when the UTC source end, the recorded end and the on-disk count agree.

        A source with no readable bar for an already-imported series raises (the caller
        reports the file as failed) instead of returning "new", whose rewrite would delete
        the imported bars before finding nothing to write.
        """
        timeframe = last_timeframe(timeframe)
        row = self._metadata_service.get_instrument_sync(catalog_name, ticker)
        if row is None:
            return "new"
        recorded = getattr(row, _bar_count_field_for_timeframe(timeframe)) or 0
        end = getattr(row, _date_range_end_field_for_timeframe(timeframe))
        if end is None or recorded <= 0:
            return "new"
        source_last = self._source_last(file_path)
        if source_last is None:  # refuse before write_series deletes the imported series
            raise ValueError(
                f"{file_path.name} has no readable bars; kept the {recorded} imported "
                f"{timeframe} bars"
            )
        if source_last != _as_utc(end):
            logger.info("crypto_source_changed", ticker=ticker, timeframe=timeframe)
            return "reimported"
        catalog = self._catalog_manager.resolve_catalog(catalog_name)
        directory = bar_directory(catalog, f"{row.nautilus_id}-{timeframe}-EXTERNAL")
        if series_complete(directory, recorded):
            return "skipped"
        logger.warning("crypto_series_incomplete", ticker=ticker, timeframe=timeframe)
        return "reimported"

    def _ensure_coin_row(self, spec: CoinSpec, catalog_name: str) -> None:
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.coin)
        row = new_coin_row(spec, catalog_name) if row is None else apply_coin_spec(row, spec)
        self._metadata_service.upsert_instrument_sync(row)

    def _import_ticker(
        self,
        ticker: str,
        file_path: Path,
        catalog_name: str,
        asset_class: AssetClass,
        timeframe: str = "1-DAY-LAST",
    ) -> ImportResult:
        start = time.perf_counter()
        try:
            spec = get_coin_spec(ticker)
            self._ensure_coin_row(spec, catalog_name)
            decision = self._classify_ticker(spec.coin, file_path, catalog_name, timeframe)
            if decision == "skipped":
                return self._result(spec.coin, start, status="skipped", outcome="skipped")
            return self._import_coin(spec, file_path, catalog_name, timeframe, decision, start)
        except Exception as e:  # noqa: BLE001 — isolate one coin's failure from the batch
            logger.error("crypto_import_failed", ticker=ticker, error=str(e), exc_info=True)
            return self._result(ticker, start, status="failed", error=str(e))

    def _import_coin(
        self,
        spec: CoinSpec,
        file_path: Path,
        catalog_name: str,
        timeframe: str,
        decision: ClassifierDecision,
        start: float,
    ) -> ImportResult:
        catalog = self._catalog_manager.resolve_catalog(catalog_name)
        timeframe = last_timeframe(timeframe)
        bar_type = BarType.from_str(f"{crypto_instrument_id(spec)}-{timeframe}-EXTERNAL")
        parser = self.parser_class(chunk_rows=self._chunk_rows)
        precision = spec.price_precision
        tally = write_series(catalog, parser, file_path, bar_type, precision)
        warnings = crypto_warnings(parser, precision, tally.out_of_order)
        failure = (
            "No bars parsed from file"
            if tally.count == 0
            else verify_series(catalog, bar_type, tally, self._sample_size)
        )
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.coin)
        if failure is None and row is None:
            failure = "catalog_instruments row disappeared during import"
        ohlc = len(collect_ohlc_warnings(parser.last_validation))  # the rest are counters
        fields = {"row_count": tally.count, "warnings": warnings, "ohlc_flags": ohlc}
        if failure is not None or row is None:
            return self._result(spec.coin, start, status="failed", error=failure, **fields)
        record_coverage(row, timeframe, tally)
        self._metadata_service.upsert_instrument_sync(row)
        (bar_directory(catalog, bar_type) / INCOMPLETE_MARKER).unlink(missing_ok=True)
        logger.info(
            "crypto_series_imported", ticker=spec.coin, timeframe=timeframe, rows=tally.count
        )
        return self._result(spec.coin, start, status="success", outcome=decision, **fields)
