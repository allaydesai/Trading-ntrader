"""Unit tests for the backfill report artefacts.

Two things here can quietly cause real damage: the overrides proposal must be
parseable by the loader that will actually consume it (a format drift would fail
silently as "no overrides found"), and the merge rewrites a git-tracked file, so
it must never lose a hand-curated row.
"""

import csv

import pytest

from src.services.metadata.providers.ibkr_venue_provider import (
    VenueOutcome,
    VenueQualification,
)
from src.services.metadata.venue_backfill_report import (
    merge_into_overrides,
    needs_review,
    write_disagreements_csv,
    write_overrides_proposal,
    write_rejects_csv,
    write_results_csv,
)
from src.services.metadata.venue_overrides import load_venue_overrides


def _resolved(ticker, venue="ARCA", **kwargs) -> VenueQualification:
    return VenueQualification(
        ticker=ticker,
        outcome=VenueOutcome.RESOLVED,
        venue=venue,
        primary_exchange=venue,
        con_id=1,
        detail="1 contract(s)",
        **kwargs,
    )


def _rejected(ticker, outcome=VenueOutcome.NOT_FOUND, **kwargs) -> VenueQualification:
    return VenueQualification(ticker=ticker, outcome=outcome, detail="nope", **kwargs)


def _read(path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.unit
class TestResultsCsv:
    def test_writes_every_row_sorted(self, tmp_path):
        path = tmp_path / "results.csv"
        count = write_results_csv(path, [_resolved("ZZZ"), _rejected("AAA"), _resolved("MMM")])

        rows = _read(path)
        assert count == 3
        assert [r["ticker"] for r in rows] == ["AAA", "MMM", "ZZZ"]

    def test_candidates_are_pipe_joined(self, tmp_path):
        path = tmp_path / "results.csv"
        write_results_csv(
            path,
            [_rejected("AAA", outcome=VenueOutcome.AMBIGUOUS, candidates=("ARCA", "NYSE"))],
        )
        assert _read(path)[0]["candidates"] == "ARCA|NYSE"

    def test_unexpected_venue_renders_as_yes_no(self, tmp_path):
        path = tmp_path / "results.csv"
        write_results_csv(path, [_resolved("AAA", unexpected_venue=True), _resolved("BBB")])
        rows = {r["ticker"]: r["unexpected_venue"] for r in _read(path)}
        assert rows == {"AAA": "yes", "BBB": "no"}

    def test_empty_input_writes_header_only(self, tmp_path):
        path = tmp_path / "results.csv"
        assert write_results_csv(path, []) == 0
        assert _read(path) == []

    def test_detail_with_a_comma_survives_quoting(self, tmp_path):
        path = tmp_path / "results.csv"
        qual = VenueQualification(ticker="AAA", outcome=VenueOutcome.ERROR, detail="broke, badly")
        write_results_csv(path, [qual])
        assert _read(path)[0]["detail"] == "broke, badly"


@pytest.mark.unit
class TestRejectsCsv:
    """The operator worklist — everything needing a human decision."""

    def test_includes_all_non_resolved_outcomes(self, tmp_path):
        path = tmp_path / "rejects.csv"
        count = write_rejects_csv(
            path,
            [
                _resolved("OK"),
                _rejected("NF", outcome=VenueOutcome.NOT_FOUND),
                _rejected("AM", outcome=VenueOutcome.AMBIGUOUS),
                _rejected("ER", outcome=VenueOutcome.ERROR),
            ],
        )
        assert count == 3
        assert {r["ticker"] for r in _read(path)} == {"NF", "AM", "ER"}

    def test_includes_resolved_but_unexpected_venue(self, tmp_path):
        """Kept as-is, but an unfamiliar code deserves one look before it propagates."""
        path = tmp_path / "rejects.csv"
        write_rejects_csv(path, [_resolved("WEIRD", venue="PEARLX", unexpected_venue=True)])
        assert [r["ticker"] for r in _read(path)] == ["WEIRD"]

    def test_needs_review_predicate(self):
        assert needs_review(_rejected("A")) is True
        assert needs_review(_resolved("B")) is False
        assert needs_review(_resolved("C", unexpected_venue=True)) is True


@pytest.mark.unit
class TestOverridesProposal:
    def test_round_trips_through_the_real_loader(self, tmp_path):
        """Format drift here would show up as a silent 'no overrides found'."""
        path = tmp_path / "proposal.csv"
        write_overrides_proposal(path, [_resolved("SPY", "ARCA"), _resolved("QQQ", "NASDAQ")])

        assert load_venue_overrides(path) == {"SPY": "ARCA", "QQQ": "NASDAQ"}

    def test_excludes_non_resolved(self, tmp_path):
        path = tmp_path / "proposal.csv"
        count = write_overrides_proposal(
            path, [_resolved("OK"), _rejected("NF"), _rejected("AM", VenueOutcome.AMBIGUOUS)]
        )
        assert count == 1
        assert set(load_venue_overrides(path)) == {"OK"}

    def test_includes_agreeing_rows_so_the_file_is_the_authority(self, tmp_path):
        path = tmp_path / "proposal.csv"
        write_overrides_proposal(path, [_resolved("QQQ", "NASDAQ")])
        assert load_venue_overrides(path) == {"QQQ": "NASDAQ"}

    def test_empty_input_is_loadable_as_empty(self, tmp_path):
        path = tmp_path / "proposal.csv"
        assert write_overrides_proposal(path, []) == 0
        assert load_venue_overrides(path) == {}


@pytest.mark.unit
class TestDisagreements:
    def test_reports_only_genuine_contradictions(self, tmp_path):
        path = tmp_path / "disagreements.csv"
        count = write_disagreements_csv(
            path,
            [_resolved("IVV", "ARCA"), _resolved("QQQ", "NASDAQ"), _resolved("NEW", "BATS")],
            {"IVV": "NYSE", "QQQ": "NASDAQ"},  # NEW has no prior venue
        )

        rows = _read(path)
        assert count == 1
        assert rows[0] == {"ticker": "IVV", "current_venue": "NYSE", "ibkr_venue": "ARCA"}

    def test_unresolved_tickers_are_not_disagreements(self, tmp_path):
        path = tmp_path / "disagreements.csv"
        count = write_disagreements_csv(path, [_rejected("AAA")], {"AAA": "NYSE"})
        assert count == 0


@pytest.mark.unit
class TestMergeIntoOverrides:
    """This rewrites a git-tracked file — it must never lose a curated row."""

    def _write_overrides(self, tmp_path, text: str):
        path = tmp_path / "venue_overrides.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_adds_new_rows_and_keeps_existing(self, tmp_path):
        path = self._write_overrides(tmp_path, "ticker,venue\nSPY,ARCA\n")
        report = merge_into_overrides(path, {"QQQ": "NASDAQ"})

        assert report.added == 1
        assert load_venue_overrides(path) == {"SPY": "ARCA", "QQQ": "NASDAQ"}

    def test_matching_row_is_unchanged(self, tmp_path):
        path = self._write_overrides(tmp_path, "ticker,venue\nSPY,ARCA\n")
        report = merge_into_overrides(path, {"SPY": "ARCA"})

        assert (report.added, report.unchanged) == (0, 1)
        assert report.conflicts == {}

    def test_conflict_keeps_existing_and_is_reported(self, tmp_path):
        """A curated row may encode a deliberate decision — never silently overwrite."""
        path = self._write_overrides(tmp_path, "ticker,venue\nSPY,ARCA\n")
        report = merge_into_overrides(path, {"SPY": "NASDAQ"})

        assert report.conflicts == {"SPY": ("ARCA", "NASDAQ")}
        assert load_venue_overrides(path)["SPY"] == "ARCA"

    def test_backup_is_written_before_rewriting(self, tmp_path):
        path = self._write_overrides(tmp_path, "ticker,venue\nSPY,ARCA\n")
        report = merge_into_overrides(path, {"QQQ": "NASDAQ"})

        assert report.backup is not None
        assert report.backup.exists()
        assert "SPY,ARCA" in report.backup.read_text()

    def test_absent_target_is_created_without_a_backup(self, tmp_path):
        path = tmp_path / "venue_overrides.csv"
        report = merge_into_overrides(path, {"SPY": "ARCA"})

        assert report.backup is None
        assert load_venue_overrides(path) == {"SPY": "ARCA"}

    def test_output_is_sorted_for_stable_diffs(self, tmp_path):
        path = tmp_path / "venue_overrides.csv"
        merge_into_overrides(path, {"ZZZ": "ARCA", "AAA": "NYSE", "MMM": "BATS"})

        lines = path.read_text().strip().splitlines()
        assert lines == ["ticker,venue", "AAA,NYSE", "MMM,BATS", "ZZZ,ARCA"]

    def test_no_temp_file_is_left_behind(self, tmp_path):
        path = tmp_path / "venue_overrides.csv"
        merge_into_overrides(path, {"SPY": "ARCA"})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_large_proposal_merges_cleanly(self, tmp_path):
        """The real run folds in thousands of rows at once."""
        path = self._write_overrides(tmp_path, "ticker,venue\nSPY,ARCA\n")
        proposal = {f"T{i:04d}": "BATS" for i in range(3000)}
        report = merge_into_overrides(path, proposal)

        assert report.added == 3000
        assert report.total == 3001
        assert load_venue_overrides(path)["SPY"] == "ARCA"
