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
    log_resolution_results,
)
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
