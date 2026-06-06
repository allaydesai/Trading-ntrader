"""Loader for FirstRate supplementary data (dividends and stock splits).

Resolves per-ticker dividend/split files, parses them, and persists them via
the sync repositories using idempotent set-replace. Each per-ticker load is
wrapped in its own ``try/except`` so a bad supplementary file can never
propagate into — or abort — the bar-data import (AC-3). Missing files are a
clean no-op for that data type (AC-4).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Sequence

import structlog

from src.db.models.catalog_dividend import CatalogDividend
from src.db.models.catalog_stock_split import CatalogStockSplit
from src.db.repositories.catalog_dividend_repository import (
    SyncCatalogDividendRepository,
)
from src.db.repositories.catalog_stock_split_repository import (
    SyncCatalogStockSplitRepository,
)
from src.services.firstrate.parsers.supplementary_parser import (
    parse_dividends,
    parse_splits,
)

logger = structlog.get_logger(__name__)


@dataclass
class SupplementaryLoadResult:
    """Outcome of loading supplementary data for one ticker.

    Attributes:
        ticker: Trading symbol.
        status: ``"success"`` or ``"failed"`` (failure is isolated, not raised).
        dividend_count: Number of dividend rows persisted.
        split_count: Number of split rows persisted.
        error: Error message if status is ``"failed"``, else ``None``.
    """

    ticker: str
    status: Literal["success", "failed"]
    dividend_count: int = 0
    split_count: int = 0
    error: Optional[str] = None


class SupplementaryDataLoader:
    """Loads dividend/split files into the DB with per-ticker failure isolation.

    Args:
        dividend_repo: Sync repository for dividend rows.
        split_repo: Sync repository for split rows.
    """

    def __init__(
        self,
        dividend_repo: SyncCatalogDividendRepository,
        split_repo: SyncCatalogStockSplitRepository,
    ) -> None:
        self._dividend_repo = dividend_repo
        self._split_repo = split_repo

    def load_for_ticker(
        self,
        ticker: str,
        catalog_name: str,
        dividends_dir: Optional[Path],
        splits_dir: Optional[Path],
    ) -> SupplementaryLoadResult:
        """Load dividend + split data for a single ticker.

        Resolves ``{ticker}_divs.txt`` in ``dividends_dir`` and ``{ticker}.txt``
        in ``splits_dir`` (note the different suffixes). A missing file (or a
        ``None`` directory) is a no-op for that data type (AC-4). The entire
        per-ticker operation is wrapped in ``try/except`` so any failure is
        logged and returned as a flagged result — never propagated (AC-3).

        Args:
            ticker: Trading symbol.
            catalog_name: Catalog scope.
            dividends_dir: Directory holding ``{ticker}_divs.txt`` (or None).
            splits_dir: Directory holding ``{ticker}.txt`` (or None).

        Returns:
            A :class:`SupplementaryLoadResult` describing the outcome.
        """
        try:
            # Wrap this ticker's writes in a SAVEPOINT so a flush-time error
            # (e.g. an IntegrityError that aborts the transaction on PostgreSQL)
            # rolls back only this ticker and leaves the shared transaction
            # usable for the bar import — DB-level isolation for AC-3. Both repos
            # share one session, so either repo's session governs the savepoint.
            with self._dividend_repo.session.begin_nested():
                dividend_count = self._load_dividends(ticker, catalog_name, dividends_dir)
                split_count = self._load_splits(ticker, catalog_name, splits_dir)
            return SupplementaryLoadResult(
                ticker=ticker,
                status="success",
                dividend_count=dividend_count,
                split_count=split_count,
            )
        except Exception as e:  # isolation: never propagate to bar import (AC-3)
            logger.error(
                "supplementary_load_failed",
                ticker=ticker,
                catalog=catalog_name,
                error=str(e),
                exc_info=True,
            )
            return SupplementaryLoadResult(
                ticker=ticker,
                status="failed",
                error=str(e),
            )

    def load_for_tickers(
        self,
        tickers: Sequence[str],
        catalog_name: str,
        dividends_dir: Optional[Path],
        splits_dir: Optional[Path],
    ) -> list[SupplementaryLoadResult]:
        """Load supplementary data for many tickers, aggregating results.

        Each ticker is processed independently; one ticker's failure does not
        abort the batch (AC-3).

        Args:
            tickers: Trading symbols to process.
            catalog_name: Catalog scope.
            dividends_dir: Directory holding ``{ticker}_divs.txt`` (or None).
            splits_dir: Directory holding ``{ticker}.txt`` (or None).

        Returns:
            One :class:`SupplementaryLoadResult` per ticker.
        """
        results: list[SupplementaryLoadResult] = []
        for ticker in tickers:
            results.append(self.load_for_ticker(ticker, catalog_name, dividends_dir, splits_dir))

        total_div = sum(r.dividend_count for r in results)
        total_split = sum(r.split_count for r in results)
        failed = sum(1 for r in results if r.status == "failed")
        logger.info(
            "supplementary_load_complete",
            catalog=catalog_name,
            tickers=len(results),
            dividend_rows=total_div,
            split_rows=total_split,
            failed=failed,
        )
        return results

    def _load_dividends(self, ticker: str, catalog_name: str, dividends_dir: Optional[Path]) -> int:
        """Parse and persist dividends for a ticker; no-op if file absent."""
        if dividends_dir is None:
            return 0
        file_path = dividends_dir / f"{ticker}_divs.txt"
        if not file_path.is_file():
            return 0
        rows = parse_dividends(file_path)
        if not rows:
            # Present-but-empty/all-malformed file: preserve existing rows rather
            # than wiping them via an empty set-replace (guards truncated sources).
            logger.warning(
                "supplementary_file_no_valid_rows_preserving_existing",
                ticker=ticker,
                file=str(file_path),
                kind="dividends",
            )
            return 0
        models = [CatalogDividend(ex_date=r.ex_date, amount=r.amount) for r in rows]
        self._dividend_repo.replace_for_ticker(catalog_name, ticker, models)
        return len(models)

    def _load_splits(self, ticker: str, catalog_name: str, splits_dir: Optional[Path]) -> int:
        """Parse and persist splits for a ticker; no-op if file absent."""
        if splits_dir is None:
            return 0
        file_path = splits_dir / f"{ticker}.txt"
        if not file_path.is_file():
            return 0
        rows = parse_splits(file_path)
        if not rows:
            # Present-but-empty/all-malformed file: preserve existing rows rather
            # than wiping them via an empty set-replace (guards truncated sources).
            logger.warning(
                "supplementary_file_no_valid_rows_preserving_existing",
                ticker=ticker,
                file=str(file_path),
                kind="splits",
            )
            return 0
        models = [CatalogStockSplit(effective_date=r.effective_date, ratio=r.ratio) for r in rows]
        self._split_repo.replace_for_ticker(catalog_name, ticker, models)
        return len(models)
