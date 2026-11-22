"""Streaming import of FirstRate continuous futures into a Parquet catalog (futures M2).

Reuses :class:`ImportService`'s batch loop, progress logging and idempotent re-run
classifier, and replaces the per-ticker step so a file is never held whole:

1. upsert the ``catalog_instruments`` row from the contract spec (no company profiles);
2. classify skip / reimport / new (plus an on-disk row-count check, below);
3. delete the bar type's existing Parquet parts, then parse the file chunk by chunk and
   write one part per chunk;
4. verify the row count from Parquet footers and a handful of sampled bars read back by
   timestamp (no full read-back);
5. record coverage (counts, per-timeframe start/end) on the row.

The equity classifier trusts the metadata row alone (ADR-5). A streamed write that dies
after step 3's delete would leave a partial series behind a row that still says
"complete", so for futures a skip additionally requires the Parquet footers to hold
exactly the recorded bar count; otherwise the series is re-imported.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs
import structlog
from nautilus_trader.model.data import Bar, BarType

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.catalog import AssetClass, ImportResult
from src.services.firstrate.coverage import earliest_start, from_ns, start_field_for_timeframe
from src.services.firstrate.import_service import (
    _TIMEFRAME_END_FIELD_MAP,
    ClassifierDecision,
    ImportService,
    _bar_count_field_for_timeframe,
    _date_range_end_field_for_timeframe,
)
from src.services.firstrate.import_verification import collect_ohlc_warnings
from src.services.firstrate.parsers.firstrate_futures_parser import (
    DEFAULT_CHUNK_ROWS,
    FirstRateFuturesParser,
)
from src.services.futures.contract_specs import FuturesContractSpec, get_contract_spec
from src.services.futures.instrument_builder import continuous_instrument_id

if TYPE_CHECKING:
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from src.services.firstrate.catalog_manager import CatalogManager
    from src.services.firstrate.instrument_mapper import InstrumentMapper
    from src.services.firstrate.metadata_service import MetadataService

logger = structlog.get_logger(__name__)

#: ``catalog_instruments.contract_kind`` / ``roll_rule`` for a FirstRate continuous series.
CONTRACT_KIND = "continuous"
ROLL_RULE = "firstrate-ratio-adjusted"

#: Bars read back by timestamp to verify a write (first and last always included).
DEFAULT_SAMPLE_SIZE = 12

_BAR_COUNT_FIELDS = ("daily", "hourly", "minute", "5min", "30min")

#: Touched in a bar type's directory before its parts are deleted and removed only once the
#: rewrite is verified and recorded. Nautilus only lists ``*.parquet``, so it is ignored there.
INCOMPLETE_MARKER = ".import-incomplete"


class _SpecIdentity:
    """Stands in for ``InstrumentMapper``: futures ids come from the spec, not profiles."""

    def is_loaded(self, catalog_name: str) -> bool:
        return True

    def resolve_instrument_id(self, ticker: str, catalog_name: str) -> InstrumentId:
        return continuous_instrument_id(get_contract_spec(ticker))


@dataclass
class _SeriesTally:
    """What a streamed write produced, without holding the bars."""

    count: int = 0
    first_ns: int | None = None
    last_ns: int | None = None
    out_of_order: int = 0
    edges: list[Bar] = field(default_factory=list)

    def add(self, bars: list[Bar], out_of_order: int) -> None:
        self.out_of_order += out_of_order
        if not bars:
            return
        self.count += len(bars)
        self.first_ns = bars[0].ts_init if self.first_ns is None else self.first_ns
        self.last_ns = bars[-1].ts_init
        self.edges.extend([bars[0], bars[-1]] if len(bars) > 1 else [bars[0]])

    def samples(self, limit: int) -> list[Bar]:
        """Up to ``limit`` chunk-edge bars spread evenly, always the first and last."""
        if len(self.edges) <= limit:
            return list(self.edges)
        step = (len(self.edges) - 1) / (limit - 1)
        return [self.edges[round(i * step)] for i in range(limit)]


def apply_spec(row: CatalogInstrument, spec: FuturesContractSpec) -> CatalogInstrument:
    """Stamp the spec's identity and contract fields onto a ``catalog_instruments`` row."""
    row.nautilus_id = str(continuous_instrument_id(spec))
    row.asset_class = AssetClass.FUTURES.value
    row.exchange = spec.venue
    row.name = spec.name
    row.multiplier = spec.multiplier
    row.tick_size = spec.tick_size
    row.currency = spec.currency
    row.contract_kind = CONTRACT_KIND
    row.expiry = None
    row.roll_rule = ROLL_RULE
    return row


def new_instrument_row(spec: FuturesContractSpec, catalog_name: str) -> CatalogInstrument:
    """A fresh row for a root never imported into ``catalog_name``."""
    counts = {f"bar_count_{suffix}": 0 for suffix in _BAR_COUNT_FIELDS}
    row = CatalogInstrument(ticker=spec.root, catalog_name=catalog_name, **counts)
    return apply_spec(row, spec)


def bar_directory(catalog: ParquetDataCatalog, bar_type: BarType | str) -> Path:
    """``{catalog}/data/bar/{bar_type}`` where Nautilus writes a bar type's parts."""
    return Path(catalog.path) / "data" / "bar" / str(bar_type)


def rows_on_disk(directory: Path) -> int:
    """Total rows across a bar type's Parquet parts, read from the footers only."""
    if not directory.is_dir():
        return 0
    return sum(pq.read_metadata(part).num_rows for part in directory.glob("*.parquet"))


def series_complete(directory: Path, recorded: int) -> bool:
    """True when no rewrite is pending and the readable footers hold ``recorded`` rows.

    A rewrite that died after the delete leaves the marker behind (even if the parts it
    managed to write happen to add up to the old count); a damaged footer counts as
    incomplete so the series is rewritten instead of failing on every run.
    """
    if (directory / INCOMPLETE_MARKER).exists():
        return False
    try:
        return rows_on_disk(directory) == recorded
    except (OSError, ValueError):  # pyarrow.ArrowInvalid is a ValueError
        return False


def write_series(
    catalog: ParquetDataCatalog,
    parser: FirstRateFuturesParser,
    file_path: Path,
    bar_type: BarType,
    precision: int,
) -> _SeriesTally:
    """Replace ``bar_type``'s parts with the file's bars, one Parquet part per chunk.

    Leaves :data:`INCOMPLETE_MARKER` in place; the caller removes it once verified.
    """
    directory = bar_directory(catalog, bar_type)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / INCOMPLETE_MARKER).touch()
    catalog.delete_data_range(data_cls=Bar, identifier=str(bar_type))
    tally = _SeriesTally()
    for chunk in parser.iter_chunks(file_path, bar_type, precision):
        if chunk.bars:
            catalog.write_data(chunk.bars)
        tally.add(chunk.bars, chunk.out_of_order)
    return tally


def verify_series(
    catalog: ParquetDataCatalog, bar_type: BarType, tally: _SeriesTally, sample_size: int
) -> str | None:
    """``None`` when the written series checks out, else the failure reason."""
    on_disk = rows_on_disk(bar_directory(catalog, bar_type))
    if on_disk != tally.count:
        return f"Row count mismatch: wrote {tally.count}, Parquet footers hold {on_disk}"
    for expected in tally.samples(sample_size):
        ts = expected.ts_init
        found = catalog.bars(bar_types=[str(bar_type)], start=ts, end=ts)
        if len(found) != 1 or not ImportService._bars_match(expected, found[0]):
            return f"Sample point validation failed at ts={ts}"
        if found[0].open.precision != expected.open.precision:
            return f"Precision mismatch at ts={ts}: {found[0].open.precision}"
    return None


def record_coverage(row: CatalogInstrument, timeframe: str, tally: _SeriesTally) -> None:
    """Stamp this timeframe's count and first/last bar onto the row."""
    assert tally.first_ns is not None and tally.last_ns is not None
    setattr(row, start_field_for_timeframe(timeframe), from_ns(tally.first_ns))
    row.date_range_start = earliest_start(row)
    setattr(row, _date_range_end_field_for_timeframe(timeframe), from_ns(tally.last_ns))
    ends = [getattr(row, name) for name in _TIMEFRAME_END_FIELD_MAP.values()]
    row.date_range_end = max(end for end in ends if end is not None)
    setattr(row, _bar_count_field_for_timeframe(timeframe), tally.count)


def discover_futures_files(source_dir: Path) -> list[Path]:
    """``*_full_*.txt`` files directly in ``source_dir`` or one directory below it."""
    candidates = [*source_dir.glob("*.txt"), *source_dir.glob("*/*.txt")]
    return sorted(p for p in candidates if p.is_file() and "_full_" in p.stem)


class FuturesImportService(ImportService):
    """Chunked, spec-driven variant of :class:`ImportService` for ``AssetClass.FUTURES``."""

    def __init__(
        self,
        catalog_manager: CatalogManager,
        metadata_service: MetadataService,
        *,
        chunk_rows: int = DEFAULT_CHUNK_ROWS,
        sample_size: int = DEFAULT_SAMPLE_SIZE,
    ) -> None:
        super().__init__(
            catalog_manager, metadata_service, cast("InstrumentMapper", _SpecIdentity())
        )
        self._chunk_rows = chunk_rows
        self._sample_size = sample_size

    def _discover_tickers(self, source_dir: Path, timeframe: str | None = None):
        files = discover_futures_files(source_dir)
        return [
            (self._extract_ticker(f), f)
            for f in files
            if self._matches_timeframe(f.name, timeframe)
        ]

    def _classify_ticker(
        self, ticker: str, file_path: Path, catalog_name: str, timeframe: str
    ) -> ClassifierDecision:
        decision = super()._classify_ticker(ticker, file_path, catalog_name, timeframe)
        if decision != "skipped":
            return decision
        row = self._metadata_service.get_instrument_sync(catalog_name, ticker)
        if row is None:  # parent only skips when a row exists; defensive for mypy
            return "new"
        recorded = getattr(row, _bar_count_field_for_timeframe(timeframe))
        catalog = self._catalog_manager.resolve_catalog(catalog_name)
        bar_type = f"{row.nautilus_id}-{timeframe}-EXTERNAL"
        if series_complete(bar_directory(catalog, bar_type), recorded):
            return "skipped"
        logger.warning("futures_series_incomplete", ticker=ticker, timeframe=timeframe)
        return "reimported"

    def _ensure_row(self, spec: FuturesContractSpec, catalog_name: str) -> None:
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.root)
        row = new_instrument_row(spec, catalog_name) if row is None else apply_spec(row, spec)
        self._metadata_service.upsert_instrument_sync(row)

    def _result(self, ticker: str, start: float, **fields) -> ImportResult:
        return ImportResult(ticker=ticker, duration=time.perf_counter() - start, **fields)

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
            spec = get_contract_spec(ticker)
            self._ensure_row(spec, catalog_name)
            decision = self._classify_ticker(ticker, file_path, catalog_name, timeframe)
            if decision == "skipped":
                return self._result(ticker, start, status="skipped", outcome="skipped")
            return self._import_series(spec, file_path, catalog_name, timeframe, decision, start)
        except Exception as e:  # noqa: BLE001 — isolate one root's failure from the batch
            logger.error("futures_import_failed", ticker=ticker, error=str(e), exc_info=True)
            return self._result(ticker, start, status="failed", error=str(e))

    def _import_series(
        self,
        spec: FuturesContractSpec,
        file_path: Path,
        catalog_name: str,
        timeframe: str,
        decision: ClassifierDecision,
        start: float,
    ) -> ImportResult:
        catalog = self._catalog_manager.resolve_catalog(catalog_name)
        bar_type = BarType.from_str(f"{continuous_instrument_id(spec)}-{timeframe}-EXTERNAL")
        parser = FirstRateFuturesParser(chunk_rows=self._chunk_rows)
        precision = spec.continuous_price_precision
        tally = write_series(catalog, parser, file_path, bar_type, precision)
        warnings = collect_ohlc_warnings(parser.last_validation)
        if tally.out_of_order:
            warnings.append(f"{tally.out_of_order} out-of-order row(s) dropped")
        failure = (
            "No bars parsed from file"
            if tally.count == 0
            else verify_series(catalog, bar_type, tally, self._sample_size)
        )
        row = self._metadata_service.get_instrument_sync(catalog_name, spec.root)
        if failure is None and row is None:
            failure = "catalog_instruments row disappeared during import"
        if failure is not None or row is None:
            return self._result(
                spec.root,
                start,
                status="failed",
                error=failure,
                row_count=tally.count,
                warnings=warnings,
            )
        record_coverage(row, timeframe, tally)
        self._metadata_service.upsert_instrument_sync(row)
        (bar_directory(catalog, bar_type) / INCOMPLETE_MARKER).unlink(missing_ok=True)
        logger.info(
            "futures_series_imported", ticker=spec.root, timeframe=timeframe, rows=tally.count
        )
        return self._result(
            spec.root,
            start,
            status="success",
            outcome=decision,
            row_count=tally.count,
            warnings=warnings,
        )
