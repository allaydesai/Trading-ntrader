"""Unit tests for qualification sync — the bridge between metadata and catalog identity.

Before this existed, ``metadata apply-overrides`` wrote ``instrument_metadata.venue``
but never touched ``catalog_instruments.nautilus_id``, so the coverage gate could
report PASS while ``backtest_loader`` rejected every ticker. These tests pin the
invariant that closes that hole:

    nautilus_id is non-NULL  ⟺  resolution_status is RESOLVED

Both directions matter. Forward: a newly resolved ticker gets its identity. Reverse:
a ticker that *loses* RESOLVED (excluded, or re-resolution failed) has its stale
identity cleared, or it would stay silently backtestable under an obsolete venue.
"""

from dataclasses import dataclass
from typing import Optional

import pytest

from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.qualification_sync import (
    QualificationSyncResult,
    sync_resolved_qualifications,
)

CATALOG = "firstrate-etf"


@dataclass
class FakeMeta:
    """Stand-in for the instrument_metadata ORM row."""

    ticker: str
    venue: Optional[str]
    resolution_status: ResolutionStatus


@dataclass
class FakeCatalogRow:
    """Stand-in for the catalog_instruments ORM row."""

    ticker: str
    nautilus_id: Optional[str]
    exchange: Optional[str]


class FakeMetaRepo:
    def __init__(self, rows: list[FakeMeta]):
        self._rows = rows

    def list_all(self) -> list[FakeMeta]:
        return list(self._rows)


class FakeCatalogRepo:
    def __init__(self, rows: list[FakeCatalogRow]):
        self._rows = rows

    def iter_by_catalog(self, catalog_name: str):
        assert catalog_name == CATALOG
        return iter(self._rows)


class SpyMapper:
    """Records sync_qualification calls and mutates the row like the real mapper."""

    def __init__(self, rows: dict[str, FakeCatalogRow]):
        self._rows = rows
        self.calls: list[tuple[str, str, Optional[str]]] = []

    def sync_qualification(self, ticker: str, catalog_name: str, venue: Optional[str]):
        self.calls.append((ticker, catalog_name, venue))
        row = self._rows[ticker]
        row.nautilus_id = f"{ticker}.{venue}" if venue else None
        row.exchange = venue
        return row.nautilus_id


def _build(metas: list[FakeMeta], catalog_rows: list[FakeCatalogRow]):
    by_ticker = {r.ticker: r for r in catalog_rows}
    mapper = SpyMapper(by_ticker)
    return FakeMetaRepo(metas), FakeCatalogRepo(catalog_rows), mapper


def _run(metas, catalog_rows, tickers=None) -> tuple[QualificationSyncResult, SpyMapper]:
    meta_repo, catalog_repo, mapper = _build(metas, catalog_rows)
    result = sync_resolved_qualifications(
        meta_repo=meta_repo,
        catalog_repo=catalog_repo,
        mapper=mapper,
        catalog_name=CATALOG,
        tickers=tickers,
    )
    return result, mapper


@pytest.mark.unit
class TestForwardSync:
    """RESOLVED metadata must produce a qualified catalog identity."""

    def test_resolved_with_null_id_is_synced(self):
        """The exact state of all 3,451 unresolved-then-resolved ETF tickers."""
        metas = [FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("AAA", None, None)]
        result, mapper = _run(metas, rows)

        assert result.synced == 1
        assert rows[0].nautilus_id == "AAA.ARCA"
        assert rows[0].exchange == "ARCA"
        assert mapper.calls == [("AAA", CATALOG, "ARCA")]

    def test_drifted_venue_is_resynced(self):
        """A venue corrected by IBKR must overwrite the stale FMP-derived identity."""
        metas = [FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("AAA", "AAA.AMEX", "AMEX")]
        result, _ = _run(metas, rows)

        assert result.synced == 1
        assert rows[0].nautilus_id == "AAA.ARCA"

    def test_already_correct_is_unchanged_and_never_written(self):
        """The idempotency proof: a re-run must not touch a single row.

        upsert() copies every column, so a pointless write would churn updated_at
        across 4,612 rows and make 'did anything change?' unanswerable.
        """
        metas = [FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("AAA", "AAA.ARCA", "ARCA")]
        result, mapper = _run(metas, rows)

        assert (result.synced, result.unchanged) == (0, 1)
        assert mapper.calls == []

    def test_exchange_drift_alone_triggers_a_sync(self):
        """nautilus_id right but exchange stale — still out of sync, still repaired."""
        metas = [FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("AAA", "AAA.ARCA", "AMEX")]
        result, _ = _run(metas, rows)

        assert result.synced == 1
        assert rows[0].exchange == "ARCA"

    def test_multi_dot_ticker_qualifies_correctly(self):
        """BRK.B must become BRK.B.NYSE, not BRK.NYSE."""
        metas = [FakeMeta("BRK.B", "NYSE", ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("BRK.B", None, None)]
        _run(metas, rows)

        assert rows[0].nautilus_id == "BRK.B.NYSE"


@pytest.mark.unit
class TestReverseSync:
    """Losing RESOLVED must clear the identity, or the ticker stays wrongly loadable."""

    def test_excluded_ticker_has_stale_identity_cleared(self):
        metas = [FakeMeta("ZZZZ", None, ResolutionStatus.EXCLUDED)]
        rows = [FakeCatalogRow("ZZZZ", "ZZZZ.AMEX", "AMEX")]
        result, _ = _run(metas, rows)

        assert result.cleared == 1
        assert rows[0].nautilus_id is None
        assert rows[0].exchange is None

    def test_venue_unresolved_ticker_has_stale_identity_cleared(self):
        metas = [FakeMeta("YYYY", None, ResolutionStatus.VENUE_UNRESOLVED)]
        rows = [FakeCatalogRow("YYYY", "YYYY.AMEX", "AMEX")]
        result, _ = _run(metas, rows)

        assert result.cleared == 1
        assert rows[0].nautilus_id is None

    def test_already_null_non_resolved_is_unchanged(self):
        metas = [FakeMeta("YYYY", None, ResolutionStatus.VENUE_UNRESOLVED)]
        rows = [FakeCatalogRow("YYYY", None, None)]
        result, mapper = _run(metas, rows)

        assert (result.cleared, result.unchanged) == (0, 1)
        assert mapper.calls == []

    def test_resolved_status_but_null_venue_is_treated_as_unqualified(self):
        """Defensive: RESOLVED with no venue is incoherent — never fabricate an id."""
        metas = [FakeMeta("AAA", None, ResolutionStatus.RESOLVED)]
        rows = [FakeCatalogRow("AAA", "AAA.AMEX", "AMEX")]
        result, _ = _run(metas, rows)

        assert result.cleared == 1
        assert rows[0].nautilus_id is None


@pytest.mark.unit
class TestEdgeCases:
    """Row-mismatch, scoping, and no-op behaviour."""

    def test_metadata_row_without_catalog_row_is_reported(self):
        metas = [FakeMeta("GHOST", "ARCA", ResolutionStatus.RESOLVED)]
        result, mapper = _run(metas, [])

        assert result.missing_row == ["GHOST"]
        assert mapper.calls == []

    def test_catalog_row_without_metadata_row_is_left_alone(self):
        """Resolution never ran for this ticker — not our business to clear it."""
        rows = [FakeCatalogRow("AAA", "AAA.AMEX", "AMEX")]
        result, mapper = _run([], rows)

        assert (result.synced, result.cleared) == (0, 0)
        assert rows[0].nautilus_id == "AAA.AMEX"
        assert mapper.calls == []

    def test_tickers_subset_restricts_scope(self):
        metas = [
            FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED),
            FakeMeta("BBB", "BATS", ResolutionStatus.RESOLVED),
        ]
        rows = [FakeCatalogRow("AAA", None, None), FakeCatalogRow("BBB", None, None)]
        result, mapper = _run(metas, rows, tickers=["AAA"])

        assert result.synced == 1
        assert [c[0] for c in mapper.calls] == ["AAA"]
        assert rows[1].nautilus_id is None

    def test_empty_store_is_a_clean_no_op(self):
        result, mapper = _run([], [])

        assert result == QualificationSyncResult()
        assert mapper.calls == []

    def test_second_run_is_a_total_no_op(self):
        """Convergence: run twice, the second must write nothing."""
        metas = [
            FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED),
            FakeMeta("ZZZZ", None, ResolutionStatus.EXCLUDED),
        ]
        rows = [FakeCatalogRow("AAA", None, None), FakeCatalogRow("ZZZZ", "ZZZZ.AMEX", "AMEX")]
        meta_repo, catalog_repo, mapper = _build(metas, rows)

        first = sync_resolved_qualifications(
            meta_repo=meta_repo, catalog_repo=catalog_repo, mapper=mapper, catalog_name=CATALOG
        )
        calls_after_first = len(mapper.calls)
        second = sync_resolved_qualifications(
            meta_repo=meta_repo, catalog_repo=catalog_repo, mapper=mapper, catalog_name=CATALOG
        )

        assert (first.synced, first.cleared) == (1, 1)
        assert (second.synced, second.cleared) == (0, 0)
        assert second.unchanged == 2
        assert len(mapper.calls) == calls_after_first

    def test_result_exposes_whether_anything_changed(self):
        metas = [FakeMeta("AAA", "ARCA", ResolutionStatus.RESOLVED)]
        result, _ = _run(metas, [FakeCatalogRow("AAA", None, None)])
        assert result.changed == 1
        assert QualificationSyncResult().changed == 0
