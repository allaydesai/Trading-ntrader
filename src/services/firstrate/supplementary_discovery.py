"""Auto-discovery of FirstRate supplementary (dividend/split) source data.

Extracted from ``src.cli.commands.import_data`` so the CLI command file stays
under the 500-line size limit. These helpers locate the supplementary source
directories and derive the ticker list to load; the actual loading lives in
:mod:`src.services.firstrate.supplementary_loader`.
"""

import re
from pathlib import Path

#: Directory names FirstRate uses for supplementary data, by data type.
_DIVIDEND_DIR_NAMES = ("stock_dividends", "etf_dividends")
_SPLIT_DIR_NAMES = ("stock_splits", "etf_splits")

#: How many parent levels to probe when auto-discovering supplementary dirs.
_SUPPLEMENTARY_PROBE_DEPTH = 3

#: A ticker-shaped filename stem (FirstRate symbols are uppercase, e.g. AAPL,
#: BRK.B). Used to keep stray non-ticker ``.txt`` docs in a splits directory
#: (e.g. a non-underscore ``readme.txt``) from being treated as tickers.
_TICKER_STEM_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")


def _probe_supplementary_dir(source_path: Path, names: tuple[str, ...]) -> Path | None:
    """Probe ``source_path`` and its parents for a supplementary data directory.

    FirstRate ships ``stock_dividends`` / ``stock_splits`` (and the ``etf_*``
    equivalents) as siblings of the bar-data directory. Walks up from
    ``source_path`` a few levels looking for any of ``names``.

    Args:
        source_path: Directory passed to the import command.
        names: Candidate directory names to look for at each level.

    Returns:
        First matching directory found, or None.
    """
    bases = [source_path, *list(source_path.parents)[:_SUPPLEMENTARY_PROBE_DEPTH]]
    for base in bases:
        for name in names:
            candidate = base / name
            if candidate.is_dir():
                return candidate
    return None


def _find_supplementary_dirs(source_path: Path) -> tuple[Path | None, Path | None]:
    """Best-effort auto-discovery of dividend/split source directories.

    Probes ``source_path`` and its parents for the FirstRate
    ``stock_dividends``/``stock_splits`` (or ``etf_*``) directories. Explicit
    ``--dividends-dir``/``--splits-dir`` flags override discovery; if nothing is
    found supplementary loading is skipped entirely (AC-6, zero regression).

    Args:
        source_path: Directory passed to the import command.

    Returns:
        ``(dividends_dir, splits_dir)`` — either element may be None.
    """
    return (
        _probe_supplementary_dir(source_path, _DIVIDEND_DIR_NAMES),
        _probe_supplementary_dir(source_path, _SPLIT_DIR_NAMES),
    )


def _discover_supplementary_tickers(
    dividends_dir: Path | None,
    splits_dir: Path | None,
) -> list[str]:
    """Derive the ticker list from the supplementary source directories.

    Collects every ``{TICKER}_divs.txt`` in ``dividends_dir`` and every
    ``{TICKER}.txt`` in ``splits_dir``, taking the union. Files whose name
    starts with ``_`` (e.g. ``_splits_readme.txt``) are ignored.

    Args:
        dividends_dir: Directory holding ``{ticker}_divs.txt`` (or None).
        splits_dir: Directory holding ``{ticker}.txt`` (or None).

    Returns:
        Sorted list of distinct ticker symbols.
    """
    tickers: set[str] = set()
    if dividends_dir is not None:
        for f in dividends_dir.glob("*_divs.txt"):
            if f.name.startswith("_"):
                continue
            symbol = f.name[: -len("_divs.txt")]
            if _TICKER_STEM_RE.match(symbol):
                tickers.add(symbol)
    if splits_dir is not None:
        # ``*.txt`` is unanchored, so guard against stray non-ticker docs
        # (e.g. a non-underscore ``readme.txt``) being treated as tickers.
        for f in splits_dir.glob("*.txt"):
            if f.name.startswith("_") or f.name.endswith("_divs.txt"):
                continue
            if _TICKER_STEM_RE.match(f.stem):
                tickers.add(f.stem)
    return sorted(tickers)
