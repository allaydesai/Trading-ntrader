"""Unit tests for the FMP→Nautilus venue map (Story 1.4, Task 1).

Pure in-code table + ``normalize_venue`` — no network, no DB. Asserts the
no-guess invariant: an unmapped or ambiguous label NEVER yields a venue code.
"""

import pytest

from src.services.metadata.venue_map import (
    AMBIGUOUS_LABELS,
    FMP_EXCHANGE_TO_VENUE,
    normalize_venue,
)


class TestFMPExchangeToVenue:
    """The confidently-mapped label→venue table (seeded conservatively)."""

    def test_seeded_with_nasdaq_and_nyse_only(self):
        assert FMP_EXCHANGE_TO_VENUE == {"NASDAQ": "NASDAQ", "NYSE": "NYSE"}

    def test_amex_is_ambiguous_not_mapped(self):
        assert "AMEX" in AMBIGUOUS_LABELS
        assert "AMEX" not in FMP_EXCHANGE_TO_VENUE


class TestNormalizeVenue:
    """``normalize_venue`` — confident → code, everything else → ``None``."""

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("NASDAQ", "NASDAQ"),
            ("NYSE", "NYSE"),
        ],
    )
    def test_confident_label_maps_to_code(self, label, expected):
        assert normalize_venue(label) == expected

    def test_ambiguous_amex_is_unresolved(self):
        # SPY is labeled AMEX by FMP but its real venue is ARCA — never guess.
        assert normalize_venue("AMEX") is None

    @pytest.mark.parametrize("label", ["", None])
    def test_blank_or_none_is_unresolved(self, label):
        assert normalize_venue(label) is None

    def test_unmapped_label_is_unresolved(self):
        assert normalize_venue("XYZ") is None

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("nasdaq", "NASDAQ"),
            (" NYSE ", "NYSE"),
            ("  amex  ", None),
        ],
    )
    def test_case_and_whitespace_insensitive(self, label, expected):
        assert normalize_venue(label) == expected

    def test_no_guess_invariant_unmapped_never_returns_code(self):
        # Any label not in the confident table must resolve to None — no fallback.
        for label in ["XYZ", "BATS", "ARCA", "OTC", "LSE", "AMEX"]:
            assert normalize_venue(label) is None
