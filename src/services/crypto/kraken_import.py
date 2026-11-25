"""Import Kraken's own OHLCVT history into a crypto catalog (crypto M7, ``kraken-crypto``).

Kraken publishes every spot pair's trade candles as headerless CSVs, one per pair and interval
(``XBTUSD_60.csv``): ``timestamp,open,high,low,close,volume,trades``, the timestamp in UNIX
seconds at the bar open (UTC), intervals with no trade omitted (not zero-filled). Fetch them
with :mod:`src.services.crypto.kraken_ohlcvt_download`.

Everything else is the FirstRate crypto import (``CryptoImportService``): the coin spec gives
the id (``BTC/USD.KRAKEN``) and the catalog row, prices are rounded half-even to the spec's
``price_precision`` and counted, volume is coin units at 8 decimals, the write is chunked and
verified from the Parquet footers, and an unchanged series is skipped on a re-run. What changes:

- discovery: a file is ``{ALTNAME}_{minutes}.csv`` whose altname is a coin spec's
  ``kraken.altname`` (``XBTUSD`` -> ``BTC``) and whose interval is one the catalog records
  (1, 5, 60, 1440 minutes). Kraken's other pairs and intervals (15, 240, 720) are ignored, so
  the whole extracted archive can be pointed at;
- stamps are epoch seconds, and the re-run classifier's source-end probe reads them;
- the seventh column (trade count) is read and dropped.

See ``openspec/changes/add-crypto-backtesting/design.md`` section 11.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.services.crypto.coin_specs import coin_names, get_coin_spec
from src.services.crypto.kraken_ohlcvt_download import INTERVAL_TIMEFRAMES
from src.services.firstrate.crypto_import_service import CryptoImportService, last_timeframe
from src.services.firstrate.parsers.firstrate_crypto_parser import FirstRateCryptoParser

_FILE = re.compile(r"^(?P<alt>[A-Z0-9]+)_(?P<minutes>\d+)\.csv$")


def epoch_stamps(ts: pd.Series) -> pd.Series:
    """UNIX-second strings -> UTC datetimes (``NaT`` where unparseable)."""
    seconds = pd.to_numeric(ts, errors="coerce")
    return pd.to_datetime(seconds, unit="s", utc=True, errors="coerce")


class KrakenOhlcvtParser(FirstRateCryptoParser):
    """The crypto parser reading Kraken's epoch-second stamps (``XBTUSD_60.csv``)."""

    to_utc = staticmethod(epoch_stamps)
    volume_note = "Kraken source"


def altname_coins() -> dict[str, str]:
    """``{"XBTUSD": "BTC", ...}`` for every shipped coin spec."""
    return {get_coin_spec(coin).kraken.altname: coin for coin in coin_names()}


def discover_kraken_files(source_dir: Path) -> list[tuple[str, str, Path]]:
    """``(coin, timeframe, path)`` of every importable file in ``source_dir`` or one below.

    Raises:
        ValueError: Two files feed one series (say a full and a quarterly extract side by
            side): each import rewrites or skips the whole series, so one would be lost.
    """
    coins = altname_coins()
    found: dict[tuple[str, str], list[Path]] = {}
    for path in sorted([*source_dir.glob("*.csv"), *source_dir.glob("*/*.csv")]):
        match = _FILE.match(path.name)
        if match is None or not path.is_file():
            continue
        coin = coins.get(match["alt"])
        timeframe = INTERVAL_TIMEFRAMES.get(int(match["minutes"]))
        if coin is not None and timeframe is not None:
            found.setdefault((coin, timeframe), []).append(path)
    twice = [paths for paths in found.values() if len(paths) > 1]
    if twice:
        names = "; ".join(" and ".join(str(p) for p in paths) for paths in twice)
        raise ValueError(f"One file per Kraken series, found more: {names}")
    return [(coin, tf, paths[0]) for (coin, tf), paths in found.items()]


def kraken_source_last_date(file_path: Path) -> datetime | None:
    """UTC open of the latest row (the file is scanned: order is not assumed), or ``None``."""
    latest: int | None = None
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                first = line.split(",", 1)[0].strip()
                if first.isdigit() and (latest is None or int(first) > latest):
                    latest = int(first)
    except OSError:
        return None
    return None if latest is None else datetime.fromtimestamp(latest, tz=timezone.utc)


class KrakenImportService(CryptoImportService):
    """``import --format kraken --asset-class crypto``: the crypto import over Kraken files."""

    parser_class = KrakenOhlcvtParser

    @staticmethod
    def _source_last(file_path: Path) -> datetime | None:
        return kraken_source_last_date(file_path)

    def _discover_tickers(self, source_dir: Path, timeframe: str | None = None):
        wanted = None if timeframe is None else last_timeframe(timeframe)
        return [
            (coin, path)
            for coin, tf, path in discover_kraken_files(source_dir)
            if wanted is None or tf == wanted
        ]
