"""Streaming parser for FirstRate spot crypto files (crypto M2).

FirstRate crypto files use the equity layout: headerless CSVs with intraday rows
``YYYY-MM-DD HH:MM:SS,O,H,L,C,V`` and daily rows ``YYYY-MM-DD,O,H,L,C,V``. Unlike every other
FirstRate series they are in **UTC** (FirstRate FAQ; confirmed against the data, design
section 0), so stamps are read as UTC with no localisation and no DST rules. Bars are stamped
at the bar open; a daily row dated ``D`` opens at ``D 00:00 UTC`` (decision C10).

Every price is emitted at the coin spec's ``price_precision`` (4), padded or rounded
half-even on the decimal value (the FX :func:`quantize_ties`); rows whose prices moved by more
than 1e-9 are counted (expected: 1-minute only, decision C14). Volume is coin units at the
spec's ``size_precision`` (8): the M1 spike showed that a bar volume precision other than the
instrument's size precision stops the run.

Validation, de-duplication and chunk ordering are the futures parser's. Nothing is dropped
or filled for an anomaly (decision C15); :class:`CryptoAnomalies` totals them over the file:
rounded rows, zero-volume bars, gaps longer than :data:`GAP_BARS` missing bars and the first
bar from which every volume is a whole number (FirstRate rounds volume to whole coins from
2026-03-21 00:00 UTC).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass, ValidationResult
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import crypto_instrument_id
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
    _read_csv_chunks,
    naive_stamps,
    ordered_rows,
)
from src.services.firstrate.parsers.firstrate_fx_parser import _rounded, quantize_ties

logger = structlog.get_logger(__name__)

#: A gap is counted when more than this many consecutive bars are missing.
GAP_BARS = 10


def coin_of(bar_type: BarType) -> str:
    """The FirstRate coin key of a crypto bar type (``BTC/USD.KRAKEN-...`` -> ``BTC``)."""
    return bar_type.instrument_id.symbol.value.split("/", 1)[0]


def utc_stamps(ts: pd.Series) -> pd.Series:
    """FirstRate crypto stamps are UTC already: parse them and attach UTC, nothing else."""
    return naive_stamps(ts).dt.tz_localize("UTC")


class CryptoAnomalies:
    """File-level counters of a crypto import, fed one ordered chunk at a time."""

    def __init__(self, step_ns: int = 1) -> None:
        self.step_ns = step_ns
        self.rounded_rows = 0
        self.zero_volume = 0
        self.gaps = 0
        self.longest_gap_bars = 0
        self._last_ns: int | None = None
        self._saw_fractional = False
        self._whole_from: int | None = None

    @property
    def whole_volume_from_ns(self) -> int | None:
        """First bar of the trailing whole-coin volume run, if fractional volumes preceded it."""
        return self._whole_from if self._saw_fractional else None

    def observe(self, stamps: np.ndarray, volumes: np.ndarray) -> None:
        """Count gaps, zero volumes and the volume regime of ascending ``stamps``."""
        if stamps.size == 0:
            return
        series = stamps if self._last_ns is None else np.concatenate(([self._last_ns], stamps))
        missing = np.diff(series) // self.step_ns - 1
        long_gaps = missing[missing > GAP_BARS]
        if long_gaps.size:
            self.gaps += int(long_gaps.size)
            self.longest_gap_bars = max(self.longest_gap_bars, int(long_gaps.max()))
        self.zero_volume += int((volumes == 0).sum())
        fractional = np.flatnonzero(volumes != np.floor(volumes))
        if fractional.size:
            self._saw_fractional = True
            after = int(fractional[-1]) + 1
            self._whole_from = int(stamps[after]) if after < stamps.size else None
        elif self._whole_from is None:
            self._whole_from = int(stamps[0])
        self._last_ns = int(stamps[-1])


@dataclass
class CryptoChunk(FuturesChunk):
    """A futures-style chunk plus the rows of it that rounding changed."""

    rounded_rows: int = 0


def _process_crypto_chunk(
    raw: pd.DataFrame,
    bar_type: BarType,
    precision: int,
    volume_precision: int,
    after_ns: int | None,
    errors_left: int,
    anomalies: CryptoAnomalies,
) -> CryptoChunk:
    frame, valid = _clean(raw, precision, utc_stamps)
    errors = _errors_for(raw, ~valid, errors_left) if errors_left > 0 else []
    good, behind = ordered_rows(frame, valid, after_ns)
    for col in _PRICES:
        good[f"source_{col}"] = pd.to_numeric(raw.loc[good.index, col], errors="coerce")
    good = quantize_ties(good, precision)
    anomalies.observe(good["ts"].astype("int64").to_numpy(), good["volume"].to_numpy())
    return CryptoChunk(
        bars=_build_bars(good, bar_type, precision, volume_precision),
        row_count=len(raw),
        invalid_rows=int((~valid).sum()),
        out_of_order=behind,
        errors=errors,
        rounded_rows=int(_rounded(raw, precision).loc[good.index].sum()),
    )


@register_parser(asset_class=AssetClass.CRYPTO)
class FirstRateCryptoParser(FirstRateFuturesParser):
    """Chunked parser for FirstRate crypto files (``BTC_full_1hour.txt``)."""

    def __init__(self, chunk_rows: int = DEFAULT_CHUNK_ROWS) -> None:
        super().__init__(chunk_rows)
        self.anomalies = CryptoAnomalies()

    def iter_chunks(
        self, file_path: Path, bar_type: BarType, price_precision: int | None = None
    ) -> Iterator[CryptoChunk]:
        """Yield ascending, non-overlapping bar batches; ``anomalies`` totals the file."""
        spec = get_coin_spec(coin_of(bar_type))
        precision = spec.price_precision if price_precision is None else price_precision
        step_ns = int(bar_type.spec.timedelta.total_seconds()) * 1_000_000_000
        totals = ValidationResult(valid=True, errors=[], row_count=0, invalid_rows=0)
        self.last_validation = totals
        self.anomalies = anomalies = CryptoAnomalies(step_ns)
        last_ns: int | None = None
        for raw in _read_csv_chunks(file_path, self.chunk_rows):
            room = MAX_ERRORS - len(totals.errors)
            chunk = _process_crypto_chunk(
                raw, bar_type, precision, spec.size_precision, last_ns, room, anomalies
            )
            totals.row_count += chunk.row_count
            totals.invalid_rows += chunk.invalid_rows
            totals.errors.extend(chunk.errors)
            totals.valid = totals.invalid_rows == 0
            anomalies.rounded_rows += chunk.rounded_rows
            if chunk.out_of_order:
                logger.warning(
                    "crypto_rows_out_of_order", file=str(file_path), dropped=chunk.out_of_order
                )
            if chunk.bars:
                last_ns = chunk.bars[-1].ts_init
            yield chunk

    def map_instrument_id(self, ticker: str, bar_type_spec: str) -> InstrumentId:
        """The Kraken spot id ``{BASE}/USD.KRAKEN`` from the coin spec (``BTC``)."""
        return crypto_instrument_id(get_coin_spec(ticker))
