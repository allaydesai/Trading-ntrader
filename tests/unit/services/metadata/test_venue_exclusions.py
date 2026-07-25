"""Unit tests for the venue exclusion register loader + merge core.

The exclusion register is the PRD-amended second exit from VENUE_UNRESOLVED: a
ticker that no authoritative source can qualify (delisted, untradeable) may be
excluded, but only with a written reason and evidence. These tests pin the thing
that keeps an exclusion honest — a row missing either field is NOT an exclusion
and must be refused, not silently defaulted.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.services.metadata.venue_exclusions import (
    ExclusionRecord,
    VenueExclusionError,
    VenueExclusionMergeResult,
    load_venue_exclusions,
    merge_venue_exclusions,
)

_HEADER = "ticker,reason,evidence\n"


def _write(path, text: str, encoding: str = "utf-8"):
    path.write_text(text, encoding=encoding)
    return path


@pytest.mark.unit
class TestLoadVenueExclusions:
    """`load_venue_exclusions` — pure CSV parse; refuses unjustified rows."""

    def test_valid_file_normalizes_ticker_and_keeps_prose(self, tmp_path):
        f = _write(
            tmp_path / "venue_exclusions.csv",
            _HEADER + " zzzz , Delisted 2019-04 , https://example.com/notice \n",
        )
        result = load_venue_exclusions(f)
        assert result == {
            "ZZZZ": ExclusionRecord(
                ticker="ZZZZ",
                reason="Delisted 2019-04",
                evidence="https://example.com/notice",
            )
        }

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_venue_exclusions(tmp_path / "nope.csv") == {}

    def test_header_only_returns_empty(self, tmp_path):
        assert load_venue_exclusions(_write(tmp_path / "e.csv", _HEADER)) == {}

    def test_empty_file_is_noop_not_error(self, tmp_path):
        assert load_venue_exclusions(_write(tmp_path / "e.csv", "")) == {}

    def test_bom_header_is_tolerated(self, tmp_path):
        f = _write(tmp_path / "e.csv", _HEADER + "ZZZZ,gone,url\n", encoding="utf-8-sig")
        assert set(load_venue_exclusions(f)) == {"ZZZZ"}

    def test_malformed_header_raises(self, tmp_path):
        f = _write(tmp_path / "e.csv", "ticker,venue\nZZZZ,ARCA\n")
        with pytest.raises(VenueExclusionError):
            load_venue_exclusions(f)

    def test_blank_reason_is_refused(self, tmp_path):
        """An exclusion without a reason is a guess wearing a CSV row."""
        f = _write(tmp_path / "e.csv", _HEADER + "ZZZZ,,https://example.com\nYYYY,gone,url\n")
        assert set(load_venue_exclusions(f)) == {"YYYY"}

    def test_blank_evidence_is_refused(self, tmp_path):
        f = _write(tmp_path / "e.csv", _HEADER + "ZZZZ,gone,\nYYYY,gone,url\n")
        assert set(load_venue_exclusions(f)) == {"YYYY"}

    def test_blank_ticker_is_refused(self, tmp_path):
        f = _write(tmp_path / "e.csv", _HEADER + ",gone,url\nYYYY,gone,url\n")
        assert set(load_venue_exclusions(f)) == {"YYYY"}

    def test_ragged_short_row_skipped(self, tmp_path):
        f = _write(tmp_path / "e.csv", _HEADER + "ZZZZ,gone\nYYYY,gone,url\n")
        assert set(load_venue_exclusions(f)) == {"YYYY"}

    def test_duplicate_ticker_last_wins(self, tmp_path):
        f = _write(tmp_path / "e.csv", _HEADER + "ZZZZ,first,u1\nZZZZ,second,u2\n")
        assert load_venue_exclusions(f)["ZZZZ"].reason == "second"

    def test_unreadable_file_raises_exclusion_error(self, tmp_path):
        """A bad-bytes file degrades like a bad header — never a raw traceback."""
        f = tmp_path / "e.csv"
        f.write_bytes(b"ticker,reason,evidence\n\xff\xfe\x00bad\n")
        with pytest.raises(VenueExclusionError):
            load_venue_exclusions(f)


@pytest.mark.unit
class TestMergeVenueExclusions:
    """`merge_venue_exclusions` — tally over the repository's per-row outcome."""

    @staticmethod
    def _record(ticker: str) -> ExclusionRecord:
        return ExclusionRecord(ticker=ticker, reason="delisted", evidence="url")

    def test_tallies_applied_unchanged_unmatched(self):
        repo = MagicMock()
        repo.apply_venue_exclusion.side_effect = ["applied", "unchanged", "unmatched"]
        result = merge_venue_exclusions(
            {t: self._record(t) for t in ("AAA", "BBB", "CCC")},
            repo,
            datetime.now(timezone.utc),
        )
        assert (result.applied, result.unchanged, result.unmatched) == (1, 1, ["CCC"])
        assert result.total == 3

    def test_processes_tickers_in_sorted_order(self):
        repo = MagicMock()
        repo.apply_venue_exclusion.return_value = "applied"
        merge_venue_exclusions(
            {t: self._record(t) for t in ("ZZZ", "AAA", "MMM")},
            repo,
            datetime.now(timezone.utc),
        )
        assert [c.args[0] for c in repo.apply_venue_exclusion.call_args_list] == [
            "AAA",
            "MMM",
            "ZZZ",
        ]

    def test_empty_map_is_a_noop(self):
        repo = MagicMock()
        result = merge_venue_exclusions({}, repo, datetime.now(timezone.utc))
        assert result == VenueExclusionMergeResult()
        repo.apply_venue_exclusion.assert_not_called()

    def test_reason_is_passed_through_to_the_repository(self):
        """The reason reaches the store — an audit trail, not just a CSV comment."""
        repo = MagicMock()
        repo.apply_venue_exclusion.return_value = "applied"
        at = datetime.now(timezone.utc)
        merge_venue_exclusions({"ZZZZ": self._record("ZZZZ")}, repo, at)
        repo.apply_venue_exclusion.assert_called_once_with("ZZZZ", "delisted", at)


@pytest.mark.unit
class TestOverridePrecedence:
    """A ticker cannot be both resolved and excluded — the override wins."""

    def test_shadowed_exclusions_are_dropped_and_reported(self):
        from src.services.metadata.venue_exclusions import drop_overridden_exclusions

        exclusions = {
            "SPY": ExclusionRecord(ticker="SPY", reason="delisted", evidence="url"),
            "ZZZZ": ExclusionRecord(ticker="ZZZZ", reason="delisted", evidence="url"),
        }
        kept, shadowed = drop_overridden_exclusions(exclusions, {"SPY": "ARCA"})
        assert set(kept) == {"ZZZZ"}
        assert shadowed == ["SPY"]

    def test_no_overlap_keeps_everything(self):
        from src.services.metadata.venue_exclusions import drop_overridden_exclusions

        exclusions = {"ZZZZ": ExclusionRecord(ticker="ZZZZ", reason="d", evidence="u")}
        kept, shadowed = drop_overridden_exclusions(exclusions, {"SPY": "ARCA"})
        assert set(kept) == {"ZZZZ"}
        assert shadowed == []
