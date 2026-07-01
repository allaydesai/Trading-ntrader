"""Unit tests for the resolution-summary reporting helpers (Story 1.6, AC #2).

Pure render + structlog helpers — no DB, no network, no Nautilus. Mirrors the
logger-monkeypatch + structured-call assertion idiom in
``tests/unit/services/metadata/test_instrument_metadata_service.py``.
"""

from unittest.mock import MagicMock

import pytest

from src.cli.commands import import_reporting
from src.cli.commands.import_reporting import (
    build_resolution_summary_text,
    build_summary_text,
    log_resolution_results,
)
from src.models.catalog import ImportResult
from src.models.instrument_metadata import (
    NA_SENTINEL,
    InstrumentMetadata,
    ResolutionStatus,
    ResolutionSummary,
)

pytestmark = pytest.mark.unit


def _resolved(ticker: str = "SPY") -> InstrumentMetadata:
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider="FMP",
        venue="XNAS",
        currency="USD",
        company_name="SPDR S&P 500 ETF Trust",
        sector="Financial Services",
        industry="Asset Management",
        country="US",
        resolution_status=ResolutionStatus.RESOLVED,
    )


def _degraded(ticker: str = "ZZZZ") -> InstrumentMetadata:
    return InstrumentMetadata(
        ticker=ticker,
        metadata_provider="FMP",
        venue=None,
        currency=NA_SENTINEL,
        company_name=NA_SENTINEL,
        sector=NA_SENTINEL,
        industry=NA_SENTINEL,
        country=NA_SENTINEL,
        resolution_status=ResolutionStatus.VENUE_UNRESOLVED,
    )


class TestBuildResolutionSummaryText:
    """build_resolution_summary_text returns a plain-text block with the three counts."""

    def test_contains_all_three_counts(self):
        summary = ResolutionSummary(resolved=7, descriptive_gaps=3, venue_unresolved=2)
        text = build_resolution_summary_text(summary)
        assert "Resolved:" in text
        assert "Descriptive gaps:" in text
        assert "Venue unresolved:" in text
        assert "7" in text
        assert "3" in text
        assert "2" in text

    def test_returns_string_and_prints_nothing(self, capsys):
        text = build_resolution_summary_text(ResolutionSummary())
        assert isinstance(text, str)
        captured = capsys.readouterr()
        assert captured.out == ""


class TestPrintResolutionSummary:
    """_print_resolution_summary renders a Rich table without raising."""

    def test_smoke_call_does_not_raise(self):
        summary = ResolutionSummary(resolved=5, descriptive_gaps=2, venue_unresolved=1)
        import_reporting._print_resolution_summary(summary)


class TestLogResolutionResults:
    """log_resolution_results emits one structlog event per result with four fields."""

    def test_one_info_call_per_result_with_four_fields(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(import_reporting, "logger", fake_logger)
        results = [_resolved("SPY"), _degraded("ZZZZ")]

        log_resolution_results(results)

        assert fake_logger.info.call_count == 2
        fake_logger.info.assert_any_call(
            "metadata_resolution_outcome",
            ticker="SPY",
            provider="FMP",
            resolution_status="RESOLVED",
            error=None,
        )
        fake_logger.info.assert_any_call(
            "metadata_resolution_outcome",
            ticker="ZZZZ",
            provider="FMP",
            resolution_status="VENUE_UNRESOLVED",
            error=None,
        )

    def test_resolution_status_logged_as_string_value(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(import_reporting, "logger", fake_logger)

        log_resolution_results([_resolved("SPY")])

        kwargs = fake_logger.info.call_args.kwargs
        assert kwargs["resolution_status"] == "RESOLVED"
        assert not isinstance(kwargs["resolution_status"], ResolutionStatus)

    def test_empty_results_logs_nothing(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(import_reporting, "logger", fake_logger)

        log_resolution_results([])

        fake_logger.info.assert_not_called()


def _success(ticker: str, *, rows: int = 10, warnings: list[str] | None = None) -> ImportResult:
    return ImportResult(
        ticker=ticker,
        status="success",
        row_count=rows,
        outcome="new",
        warnings=warnings or [],
        duration=0.1,
    )


def _failed(ticker: str, *, error: str) -> ImportResult:
    return ImportResult(
        ticker=ticker,
        status="failed",
        row_count=0,
        error=error,
        duration=0.1,
    )


class TestBuildSummaryTextVerification:
    """Story 2.5 AC4: verification problems are surfaced in the summary."""

    def test_ohlc_flag_surfaced_as_warning_not_failure(self):
        results = [
            _success("SPY", warnings=["bar[0] ts=42: high (90.0) < low (95.0)"]),
            _success("QQQ"),
        ]
        text = build_summary_text(results)
        # Flagged count surfaced; the flagged ticker is not counted as failed.
        assert "Flagged (OHLC):  1" in text
        assert "Failed:          0" in text
        # The specific violation is surfaced (never silent).
        assert "Verification Warnings:" in text
        assert "SPY — bar[0] ts=42: high (90.0) < low (95.0)" in text

    def test_no_warnings_section_when_clean(self):
        text = build_summary_text([_success("SPY"), _success("QQQ")])
        assert "Flagged (OHLC):  0" in text
        assert "Verification Warnings:" not in text

    def test_row_count_failure_still_surfaced_under_failures(self):
        # AC2/AC3 reporting: a verification failure remains a blocking failure.
        results = [_failed("SPY", error="Row count mismatch: wrote 10, read back 8")]
        text = build_summary_text(results)
        assert "Failed:          1" in text
        assert "Failures:" in text
        assert "SPY — Row count mismatch: wrote 10, read back 8" in text

    def test_flagged_and_failed_coexist(self):
        results = [
            _success("SPY", warnings=["bar[1] ts=7: volume (-1.0) < 0"]),
            _failed("QQQ", error="Sample point validation failed"),
        ]
        text = build_summary_text(results)
        assert "Flagged (OHLC):  1" in text
        assert "Failed:          1" in text
        assert "Verification Warnings:" in text
        assert "Failures:" in text

    def test_failed_ticker_with_warnings_reported_once_under_failures(self):
        # A single ticker that both carries OHLC flags AND fails a blocking
        # check is reported under Failures only — not double-counted as Flagged.
        failed_flagged = ImportResult(
            ticker="SPY",
            status="failed",
            row_count=2,
            error="Row count mismatch: wrote 2, read back 1",
            warnings=["bar[0] ts=42: high (90.0) < low (95.0)"],
            duration=0.1,
        )
        text = build_summary_text([failed_flagged])
        assert "Failed:          1" in text
        assert "Flagged (OHLC):  0" in text
        assert "Verification Warnings:" not in text
        assert "SPY — Row count mismatch: wrote 2, read back 1" in text


class TestPrintSummaryVerification:
    """_print_summary renders the warnings table without raising."""

    def test_smoke_call_with_warnings_does_not_raise(self):
        results = [_success("SPY", warnings=["bar[0] ts=42: high (90.0) < low (95.0)"])]
        import_reporting._print_summary(results)
