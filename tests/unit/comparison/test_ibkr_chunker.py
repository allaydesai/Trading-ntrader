"""Unit tests for the IBKR chunked-fetch helpers in the parity harness.

Story 3.4 — workaround for the upstream Nautilus
``_calculate_duration_segments`` truncation bug. The chunker is pure
logic over datetimes, so we test it without touching IBKR.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


class TestIterChunks:
    """Tests for `_iter_chunks(start, end, chunk_days)`."""

    @pytest.mark.unit
    def test_chunks_align_to_midnight_for_daystart_range(self) -> None:
        """Midnight-to-midnight ranges yield clean ``N D`` segments only."""
        from scripts.verify_aapl_2018_reference import _iter_chunks

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 1, 31, tzinfo=timezone.utc)  # exactly 30 days

        chunks = list(_iter_chunks(start, end, chunk_days=30))

        assert len(chunks) == 1
        assert chunks[0] == (start, end)
        # Each chunk is a whole multiple of days — guarantees Nautilus's
        # `_calculate_duration_segments` emits a single "N D" segment
        # rather than spilling into seconds (the useRTH-ignored path).
        assert (chunks[0][1] - chunks[0][0]).total_seconds() % 86400 == 0

    @pytest.mark.unit
    def test_full_year_with_subday_end_rounds_up_to_next_midnight(self) -> None:
        """End at 23:59:59 must extend to next midnight so chunks stay day-aligned."""
        from scripts.verify_aapl_2018_reference import _iter_chunks

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        chunks = list(_iter_chunks(start, end, chunk_days=30))

        # 365 days from Jan 1 2018 to Jan 1 2019 → ceil(365/30) = 13 chunks.
        assert len(chunks) == 13
        # First chunk starts at requested start (already midnight-aligned).
        assert chunks[0][0] == start
        # Last chunk ends at NEXT midnight after `end`, not `end` itself —
        # this is the critical fix: a 23:59:59 end was previously
        # generating a `86399 S` sub-segment that ignored useRTH.
        assert chunks[-1][1] == datetime(2019, 1, 1, tzinfo=timezone.utc)
        # All boundaries are midnight; durations are whole-day multiples.
        for chunk_start, chunk_end in chunks:
            assert chunk_start.time() == datetime.min.time()
            assert chunk_end.time() == datetime.min.time()
            assert (chunk_end - chunk_start).total_seconds() % 86400 == 0

    @pytest.mark.unit
    def test_chunks_tile_without_overlap_or_gap(self) -> None:
        """Adjacent chunks share boundaries (end-exclusive), no gaps or overlaps."""
        from scripts.verify_aapl_2018_reference import _iter_chunks

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        chunks = list(_iter_chunks(start, end, chunk_days=30))

        for prev, curr in zip(chunks, chunks[1:]):
            # End-exclusive tiling: chunk N's end IS chunk N+1's start.
            assert prev[1] == curr[0]

    @pytest.mark.unit
    def test_smaller_chunk_size_yields_more_chunks(self) -> None:
        """Smaller chunks yield more chunks; the relationship is monotonic."""
        from scripts.verify_aapl_2018_reference import _iter_chunks

        start = datetime(2018, 1, 1, tzinfo=timezone.utc)
        end = datetime(2018, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

        seven_day_chunks = list(_iter_chunks(start, end, chunk_days=7))
        thirty_day_chunks = list(_iter_chunks(start, end, chunk_days=30))

        assert len(seven_day_chunks) > len(thirty_day_chunks)
        # Both extend past `end` to the next midnight.
        expected_end = datetime(2019, 1, 1, tzinfo=timezone.utc)
        assert seven_day_chunks[-1][1] == expected_end
        assert thirty_day_chunks[-1][1] == expected_end

    @pytest.mark.unit
    def test_subday_range_expands_to_containing_day(self) -> None:
        """start==end mid-day expands to the full day containing it.

        Sub-day ranges always round to a [day_start, next_day_start)
        window so the chunker only ever emits whole-day durations
        (avoids the seconds-segment useRTH bug).
        """
        from scripts.verify_aapl_2018_reference import _iter_chunks

        moment = datetime(2018, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        chunks = list(_iter_chunks(moment, moment, chunk_days=30))

        assert len(chunks) == 1
        assert chunks[0] == (
            datetime(2018, 6, 15, tzinfo=timezone.utc),
            datetime(2018, 6, 16, tzinfo=timezone.utc),
        )
