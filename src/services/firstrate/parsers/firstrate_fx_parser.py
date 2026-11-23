"""Streaming parser for FirstRate spot FX files (FX M2).

FirstRate FX files are headerless CSVs in US/Eastern wall clock (with DST) and a compact
date: intraday rows have 7 fields (``YYYYMMDD,HH:MM:SS,O,H,L,C,V``) and daily rows 6
(``YYYYMMDD,O,H,L,C,V``). Bars are stamped at the bar open:

- an intraday row converts exactly as the futures parser converts its wall clock (the row is
  rewritten to the futures ``YYYY-MM-DD HH:MM:SS`` shape and goes through the same
  ``_to_utc_ns``, so DST parity holds by construction);
- a daily row dated ``D`` covers ``D-1 17:00`` to ``D 17:00`` New York and is stamped at its
  session open, ``D-1 17:00`` ET (decision D5; Monday's bar opens Sunday 17:00).

Every price is emitted at the pair spec's ``price_precision`` (5 or 3), padded or rounded
half-even on the decimal value (ties re-quantized, see :func:`quantize_ties`), which also
removes USDJPY's 2014 float noise (``108.33599`` -> ``108.336``).
Validation, de-duplication and chunk ordering are the futures parser's. Two counters are
added, totalled over the file on the parser (D14, nothing is dropped for them):

- ``rounded_rows``: emitted rows whose source prices changed by more than 1e-9;
- ``session_anomalies``: emitted bars that open outside Sunday 17:00 to Friday 17:00 ET
  (Saturday bars, Sunday bars before the open, Sunday- or Saturday-dated daily rows).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import time
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass, ValidationResult
from src.services.firstrate.parsers.base import register_parser
from src.services.firstrate.parsers.firstrate_futures_parser import (
    _PRICES,
    DEFAULT_CHUNK_ROWS,
    MAX_ERRORS,
    FirstRateFuturesParser,
    FuturesChunk,
    _build_bars,
    _clean,
    _errors_for,
)
from src.services.fx.instrument_builder import fx_instrument_id
from src.services.fx.pair_specs import FxSessionSpec, get_pair_spec

logger = structlog.get_logger(__name__)

_FIELDS = [f"f{i}" for i in range(7)]
_COMPACT_DATE = "%Y%m%d"
_COMPACT_INTRADAY = "%Y%m%d %H:%M:%S"
_FUTURES_STAMP = "%Y-%m-%d %H:%M:%S"
_SUNDAY, _SATURDAY, _FRIDAY = 6, 5, 4

#: A rounded price that moved by more than this is counted in ``rounded_rows``.
ROUNDING_TOLERANCE = 1e-9


@dataclass
class FxChunk(FuturesChunk):
    """A futures-style chunk plus the FX counters for its emitted bars."""

    rounded_rows: int = 0
    session_anomalies: int = 0


def pair_of(bar_type: BarType) -> str:
    """The FirstRate pair key of an FX bar type (``EUR/USD.IDEALPRO-...`` -> ``EURUSD``)."""
    return bar_type.instrument_id.symbol.value.replace("/", "")


def wall_clock(raw: pd.DataFrame, session_open: time) -> pd.Series:
    """Naive New York bar-open times; daily rows sit at the previous day's session open."""
    date = raw["f0"].astype(str).str.strip()
    daily = raw["f6"].isna()
    clock = raw["f1"].astype(str).str.strip()
    intraday = pd.to_datetime(
        (date + " " + clock).where(~daily), format=_COMPACT_INTRADAY, errors="coerce"
    )
    if not daily.any():
        return intraday
    opens = pd.Timedelta(hours=session_open.hour, minutes=session_open.minute)
    day = pd.to_datetime(date.where(daily), format=_COMPACT_DATE, errors="coerce")
    return intraday.where(~daily, day - pd.Timedelta(days=1) + opens)


def outside_session(naive: pd.Series, session: FxSessionSpec) -> pd.Series:
    """True where a bar opens outside Sunday ``open`` to Friday ``close`` (wall clock)."""
    weekday = naive.dt.dayofweek
    minutes = naive.dt.hour * 60 + naive.dt.minute
    opens = session.open.hour * 60 + session.open.minute
    closes = session.close.hour * 60 + session.close.minute
    return (
        (weekday == _SATURDAY)
        | ((weekday == _SUNDAY) & (minutes < opens))
        | ((weekday == _FRIDAY) & (minutes >= closes))
    )


def to_futures_shape(raw: pd.DataFrame, naive: pd.Series) -> pd.DataFrame:
    """Rewrite a raw FX chunk to the futures parser's ``ts,O,H,L,C,V`` columns."""
    daily = raw["f6"].isna()
    frame = pd.DataFrame({"ts": naive.dt.strftime(_FUTURES_STAMP)}, index=raw.index)
    for offset, name in enumerate([*_PRICES, "volume"]):
        frame[name] = raw[f"f{offset + 2}"].where(~daily, raw[f"f{offset + 1}"])
    return frame


def _rounded(frame: pd.DataFrame, precision: int) -> pd.Series:
    source = pd.DataFrame({col: pd.to_numeric(frame[col], errors="coerce") for col in _PRICES})
    return ((source.round(precision) - source).abs() > ROUNDING_TOLERANCE).any(axis=1)


def quantize_ties(good: pd.DataFrame, precision: int) -> pd.DataFrame:
    """Re-round decimal ties half-even from their decimal value, not by float scaling.

    ``Series.round`` scales in binary floating point, so a source tie such as ``128.0105``
    (precision 3) lands just above ``128010.5`` and rounds up to ``128.011``. Prices within
    1e-6 of a tie are re-quantized from their shortest decimal repr with
    ``ROUND_HALF_EVEN`` (``128.010``); every other price, including those already on the
    grid, keeps the float result, which is exact for them.
    """
    step = Decimal(1).scaleb(-precision)
    for col in _PRICES:
        scaled = good[f"source_{col}"].to_numpy() * 10.0**precision
        near = np.abs(scaled - np.floor(scaled) - 0.5) < 1e-6
        if near.any():
            ties = good[f"source_{col}"][near]
            fixed = [float(Decimal(repr(v)).quantize(step, ROUND_HALF_EVEN)) for v in ties]
            good.loc[ties.index, col] = fixed
    return good


def _process_fx_chunk(
    raw: pd.DataFrame,
    bar_type: BarType,
    precision: int,
    session: FxSessionSpec,
    after_ns: int | None,
    errors_left: int,
) -> FxChunk:
    naive = wall_clock(raw, session.open)
    shaped = to_futures_shape(raw, naive)
    frame, valid = _clean(shaped, precision)
    errors = _errors_for(raw, ~valid, errors_left) if errors_left > 0 else []
    good = frame[valid].sort_values("ts", kind="stable")
    good = good[~good["ts"].duplicated(keep="last")]
    behind = 0
    if after_ns is not None and not good.empty:
        ahead = good["ts"].astype("int64") > after_ns
        behind = int((~ahead).sum())
        good = good[ahead]
    for col in _PRICES:
        good[f"source_{col}"] = pd.to_numeric(shaped.loc[good.index, col], errors="coerce")
    good = quantize_ties(good, precision)
    return FxChunk(
        bars=_build_bars(good, bar_type, precision),
        row_count=len(raw),
        invalid_rows=int((~valid).sum()),
        out_of_order=behind,
        errors=errors,
        rounded_rows=int(_rounded(shaped, precision).loc[good.index].sum()),
        session_anomalies=int(outside_session(naive, session).loc[good.index].sum()),
    )


def _read_fx_chunks(file_path: Path, chunk_rows: int) -> Iterator[pd.DataFrame]:
    try:
        yield from pd.read_csv(
            file_path,
            header=None,
            names=_FIELDS,
            dtype={"f0": str, "f1": str},
            chunksize=chunk_rows,
            skip_blank_lines=True,
            on_bad_lines="skip",
        )
    except pd.errors.EmptyDataError:
        return


@register_parser(asset_class=AssetClass.FX)
class FirstRateFxParser(FirstRateFuturesParser):
    """Chunked parser for FirstRate FX files (``EURUSD_full_1hour.txt``)."""

    def __init__(self, chunk_rows: int = DEFAULT_CHUNK_ROWS) -> None:
        super().__init__(chunk_rows)
        self.rounded_rows = 0
        self.session_anomalies = 0

    def iter_chunks(
        self, file_path: Path, bar_type: BarType, price_precision: int | None = None
    ) -> Iterator[FxChunk]:
        """Yield ascending, non-overlapping bar batches; the parser totals the counters."""
        spec = get_pair_spec(pair_of(bar_type))
        precision = spec.price_precision if price_precision is None else price_precision
        totals = ValidationResult(valid=True, errors=[], row_count=0, invalid_rows=0)
        self.last_validation = totals
        self.rounded_rows = self.session_anomalies = 0
        last_ns: int | None = None
        for raw in _read_fx_chunks(file_path, self.chunk_rows):
            room = MAX_ERRORS - len(totals.errors)
            chunk = _process_fx_chunk(raw, bar_type, precision, spec.session, last_ns, room)
            totals.row_count += chunk.row_count
            totals.invalid_rows += chunk.invalid_rows
            totals.errors.extend(chunk.errors)
            totals.valid = totals.invalid_rows == 0
            self.rounded_rows += chunk.rounded_rows
            self.session_anomalies += chunk.session_anomalies
            if chunk.out_of_order:
                logger.warning(
                    "fx_rows_out_of_order", file=str(file_path), dropped=chunk.out_of_order
                )
            if chunk.bars:
                last_ns = chunk.bars[-1].ts_init
            yield chunk

    def map_instrument_id(self, ticker: str, bar_type_spec: str) -> InstrumentId:
        """The IB-native ``{BASE}/{QUOTE}.IDEALPRO`` id from the pair spec."""
        return fx_instrument_id(get_pair_spec(ticker.upper()))
