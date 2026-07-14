"""Unit tests for venue_overrides loader + merge core (Story 3.3)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.services.metadata.venue_overrides import (
    VenueOverrideError,
    VenueOverrideMergeResult,
    load_venue_overrides,
    merge_venue_overrides,
)


def _write(path, text: str, encoding: str = "utf-8") -> None:
    path.write_text(text, encoding=encoding)


@pytest.mark.unit
class TestLoadVenueOverrides:
    """`load_venue_overrides` — pure CSV parse with normalization + skips."""

    def test_valid_file_normalizes_ticker_and_venue(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\n spy , arca \nqqq,NASDAQ\n")
        assert load_venue_overrides(f) == {"SPY": "ARCA", "QQQ": "NASDAQ"}

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_venue_overrides(tmp_path / "nope.csv") == {}

    def test_header_only_returns_empty(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\n")
        assert load_venue_overrides(f) == {}

    def test_bom_header_is_tolerated(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\nSPY,ARCA\n", encoding="utf-8-sig")
        assert load_venue_overrides(f) == {"SPY": "ARCA"}

    def test_malformed_header_raises(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "symbol,exchange\nSPY,ARCA\n")
        with pytest.raises(VenueOverrideError):
            load_venue_overrides(f)

    def test_blank_ticker_venue_and_na_rows_skipped(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\n,ARCA\nSPY,\nQQQ,N/A\nIWM,BATS\n")
        assert load_venue_overrides(f) == {"IWM": "BATS"}

    def test_duplicate_ticker_last_wins(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\nSPY,NYSE\nSPY,ARCA\n")
        assert load_venue_overrides(f) == {"SPY": "ARCA"}

    def test_ragged_short_row_skipped(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\nSPY\nQQQ,NASDAQ\n")
        assert load_venue_overrides(f) == {"QQQ": "NASDAQ"}

    def test_empty_file_is_noop_not_error(self, tmp_path):
        f = tmp_path / "venue_overrides.csv"
        _write(f, "")
        assert load_venue_overrides(f) == {}

    def test_oversize_venue_skipped(self, tmp_path):
        # Venue longer than the venue column (String(20)) is garbage — skip, don't
        # push it to the DB where Postgres would DataError / SQLite silently truncate.
        f = tmp_path / "venue_overrides.csv"
        _write(f, "ticker,venue\nSPY," + "X" * 21 + "\nQQQ,ARCA\n")
        assert load_venue_overrides(f) == {"QQQ": "ARCA"}

    def test_non_utf8_file_degrades_to_override_error(self, tmp_path):
        # Invalid bytes must surface as VenueOverrideError (caught by both callers),
        # never a raw UnicodeDecodeError that aborts the import.
        f = tmp_path / "venue_overrides.csv"
        f.write_bytes(b"ticker,venue\nSPY,\xff\xfe\n")
        with pytest.raises(VenueOverrideError):
            load_venue_overrides(f)


@pytest.mark.unit
class TestMergeVenueOverrides:
    """`merge_venue_overrides` — tallies repo outcomes, deterministic order."""

    def test_tallies_applied_unchanged_unmatched(self):
        ts = datetime(2026, 7, 13, tzinfo=timezone.utc)
        repo = MagicMock()
        outcomes = {"AAA": "applied", "BBB": "unchanged", "CCC": "unmatched"}
        repo.apply_venue_override.side_effect = lambda t, v, r: outcomes[t]
        result = merge_venue_overrides({"BBB": "NYSE", "AAA": "ARCA", "CCC": "IEX"}, repo, ts)
        assert result.applied == 1
        assert result.unchanged == 1
        assert result.unmatched == ["CCC"]
        assert result.total == 3

    def test_processes_tickers_in_sorted_order_and_threads_clock(self):
        ts = datetime(2026, 7, 13, tzinfo=timezone.utc)
        repo = MagicMock()
        repo.apply_venue_override.return_value = "applied"
        merge_venue_overrides({"ZZZ": "ARCA", "AAA": "NYSE"}, repo, ts)
        calls = repo.apply_venue_override.call_args_list
        assert [c.args[0] for c in calls] == ["AAA", "ZZZ"]
        # resolved_at threaded through unchanged (clock lives in the caller).
        assert all(c.args[2] == ts for c in calls)

    def test_empty_overrides_is_noop(self):
        ts = datetime(2026, 7, 13, tzinfo=timezone.utc)
        repo = MagicMock()
        result = merge_venue_overrides({}, repo, ts)
        assert result == VenueOverrideMergeResult()
        repo.apply_venue_override.assert_not_called()
