"""Streaming parser for FirstRate continuous futures files (futures M2).

FirstRate futures files are headerless CSVs in US/Eastern wall clock: intraday rows have
6 columns (``ts,O,H,L,C,V``) and daily rows 7 (``date,O,H,L,C,V,OI``; open interest is
dropped). A 1-minute file holds up to ~6.4M rows, so :meth:`FirstRateFuturesParser.iter_chunks`
reads it in fixed-size chunks with pandas and yields one batch of bars per chunk; nothing
holds the whole file.

Every price of a root is emitted at ONE precision (the spec's
``continuous_price_precision``: ES/NQ 2, CL 3, GC 1, ZN 6), padded or rounded half-even.
Nautilus' catalog takes a batch's precision from its first bar, so a file whose rows carry
1 and 2 decimals would otherwise come back truncated.

Timestamps convert exactly like ``FirstRateCsvParser._parse_timestamp`` (zoneinfo,
``fold=0``): an ambiguous fall-back time is its first (daylight) occurrence and a
non-existent spring-forward time is shifted one hour forward. Bars stay stamped at the
bar open (``ts_event == ts_init``), the same convention as equities.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from src.models.catalog import AssetClass, ValidationResult
from src.services.firstrate.parsers.base import BaseParser, register_parser
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import continuous_instrument_id

logger = structlog.get_logger(__name__)

#: Rows per chunk. A 250k chunk's DataFrame + Bar objects peak at ~150 MB on top of the
#: ~200 MB interpreter + Nautilus baseline (500k measured ~790 MB peak RSS while parsing).
DEFAULT_CHUNK_ROWS = 250_000

#: Cap on the row-level error strings kept for the import summary.
MAX_ERRORS = 50

_TIMEZONE = "America/New_York"
_INTRADAY_FMT = "%Y-%m-%d %H:%M:%S"
_DAILY_FMT = "%Y-%m-%d"
_NAMES = ["ts", "open", "high", "low", "close", "volume", "oi"]
_PRICES = ["open", "high", "low", "close"]


@dataclass
class FuturesChunk:
    """One parsed chunk: its bars plus what was dropped and why."""

    bars: list[Bar]
    row_count: int
    invalid_rows: int = 0
    out_of_order: int = 0
    errors: list[str] = field(default_factory=list)


def _to_utc_ns(ts: pd.Series) -> pd.Series:
    """US/Eastern wall-clock strings -> UTC epoch ns (``NaT`` where unparseable)."""
    text = ts.astype(str).str.strip()
    intraday = text.str.len() > len("YYYY-MM-DD")
    naive = pd.to_datetime(text.where(intraday), format=_INTRADAY_FMT, errors="coerce")
    if not intraday.all():
        daily = pd.to_datetime(text.where(~intraday), format=_DAILY_FMT, errors="coerce")
        naive = naive.where(intraday, daily)
    local = naive.dt.tz_localize(
        _TIMEZONE,
        ambiguous=np.ones(len(naive), dtype=bool),
        nonexistent=pd.Timedelta(hours=1),
    )
    return local.dt.tz_convert("UTC")


def _clean(frame: pd.DataFrame, precision: int) -> tuple[pd.DataFrame, pd.Series]:
    """Coerce + round; return the frame and a mask of rows that make valid bars."""
    out = pd.DataFrame(index=frame.index)
    out["ts"] = _to_utc_ns(frame["ts"])
    for col in _PRICES:
        out[col] = pd.to_numeric(frame[col], errors="coerce").round(precision)
    out["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    o, h, lo, c = (out[col] for col in _PRICES)
    valid = out.notna().all(axis=1) & (out[_PRICES] > 0).all(axis=1) & (h >= lo)
    valid &= o.between(lo, h) & c.between(lo, h) & (out["volume"] >= 0)
    return out, valid


def _errors_for(frame: pd.DataFrame, invalid: pd.Series, limit: int) -> list[str]:
    rows = frame[invalid].head(limit)
    return [
        f"Row {int(idx) + 1}: invalid bar (" + ",".join(str(v) for v in values[:6]) + ")"
        for idx, values in zip(rows.index, rows.itertuples(index=False))
    ]


def _build_bars(frame: pd.DataFrame, bar_type: BarType, precision: int) -> list[Bar]:
    stamps = frame["ts"].astype("int64").tolist()
    columns = [frame[col].tolist() for col in _PRICES]
    volumes = frame["volume"].tolist()
    return [
        Bar(
            bar_type=bar_type,
            open=Price(o, precision),
            high=Price(h, precision),
            low=Price(lo, precision),
            close=Price(c, precision),
            volume=Quantity(v, 0),
            ts_event=t,
            ts_init=t,
        )
        for o, h, lo, c, v, t in zip(*columns, volumes, stamps)
    ]


def _process_chunk(
    raw: pd.DataFrame, bar_type: BarType, precision: int, after_ns: int | None, errors_left: int
) -> FuturesChunk:
    frame, valid = _clean(raw, precision)
    errors = _errors_for(raw, ~valid, errors_left) if errors_left > 0 else []
    good = frame[valid].sort_values("ts", kind="stable")
    good = good[~good["ts"].duplicated(keep="last")]
    behind = 0
    if after_ns is not None and not good.empty:
        ahead = good["ts"].astype("int64") > after_ns
        behind = int((~ahead).sum())
        good = good[ahead]
    return FuturesChunk(
        bars=_build_bars(good, bar_type, precision),
        row_count=len(raw),
        invalid_rows=int((~valid).sum()),
        out_of_order=behind,
        errors=errors,
    )


def _read_csv_chunks(file_path: Path, chunk_rows: int) -> Iterator[pd.DataFrame]:
    try:
        yield from pd.read_csv(
            file_path,
            header=None,
            names=_NAMES,
            dtype={"ts": str},
            chunksize=chunk_rows,
            skip_blank_lines=True,
            on_bad_lines="skip",
        )
    except pd.errors.EmptyDataError:
        return


@register_parser(asset_class=AssetClass.FUTURES)
class FirstRateFuturesParser(BaseParser):
    """Chunked parser for FirstRate continuous futures files (``ES_full_1min_...txt``)."""

    def __init__(self, chunk_rows: int = DEFAULT_CHUNK_ROWS) -> None:
        if chunk_rows <= 0:
            raise ValueError(f"chunk_rows must be positive, got {chunk_rows}")
        self.chunk_rows = chunk_rows
        self.last_validation = None

    def iter_chunks(
        self, file_path: Path, bar_type: BarType, price_precision: int | None = None
    ) -> Iterator[FuturesChunk]:
        """Yield ascending, non-overlapping bar batches; ``last_validation`` totals them.

        A row at or before the previous chunk's last bar is dropped (``out_of_order``):
        chunks are written as separate Parquet parts that must not overlap.
        """
        precision = price_precision
        if precision is None:
            root = bar_type.instrument_id.symbol.value
            precision = get_contract_spec(root).continuous_price_precision
        totals = ValidationResult(valid=True, errors=[], row_count=0, invalid_rows=0)
        self.last_validation = totals
        last_ns: int | None = None
        for raw in _read_csv_chunks(file_path, self.chunk_rows):
            room = MAX_ERRORS - len(totals.errors)
            chunk = _process_chunk(raw, bar_type, precision, last_ns, room)
            totals.row_count += chunk.row_count
            totals.invalid_rows += chunk.invalid_rows
            totals.errors.extend(chunk.errors)
            totals.valid = totals.invalid_rows == 0
            if chunk.out_of_order:
                logger.warning(
                    "futures_rows_out_of_order", file=str(file_path), dropped=chunk.out_of_order
                )
            if chunk.bars:
                last_ns = chunk.bars[-1].ts_init
            yield chunk

    def parse_file(
        self, file_path: Path, instrument_id: InstrumentId, bar_type: BarType
    ) -> list[Bar]:
        """Whole-file convenience over :meth:`iter_chunks` (small files and tests only)."""
        return [bar for chunk in self.iter_chunks(file_path, bar_type) for bar in chunk.bars]

    def map_instrument_id(self, ticker: str, bar_type_spec: str) -> InstrumentId:
        """The continuous ``{ROOT}.{VENUE}`` id from the contract spec (``ES`` -> ``ES.CME``)."""
        return continuous_instrument_id(get_contract_spec(ticker))
