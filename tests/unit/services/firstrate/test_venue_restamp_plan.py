"""Unit tests for the venue re-stamp planner.

The planner decides what happens to 39 GB of bars, so the tests that matter most
are the ones about *refusing* to act: an unrecognised directory, a destination that
already exists, or a ticker with no venue on record all mean the tree is not the
shape the plan assumes, and rewriting on that basis is not worth the minutes it
saves.
"""

from pathlib import Path

import pytest

from src.services.firstrate.venue_restamp_plan import (
    build_restamp_plan,
    parse_bar_dir,
)

CATALOG = "firstrate-etf"


def _make_bar_dir(root: Path, name: str, *, size: int = 1024, files: int = 1) -> Path:
    directory = root / "data" / "bar" / name
    directory.mkdir(parents=True)
    for i in range(files):
        (
            directory
            / f"2020-01-0{i + 1}T00-00-00-000000000Z_2021-01-01T00-00-00-000000000Z.parquet"
        ).write_bytes(b"x" * size)
    return directory


def _plan(root: Path, target_venues: dict, **kwargs):
    return build_restamp_plan(
        catalog=CATALOG, catalog_root=root, target_venues=target_venues, **kwargs
    )


@pytest.mark.unit
class TestParseBarDir:
    """Directory-name parsing, including the ticker-with-dots trap."""

    @pytest.mark.parametrize(
        "spec",
        ["1-DAY-LAST", "1-HOUR-LAST", "1-MINUTE-LAST", "5-MINUTE-LAST", "30-MINUTE-LAST"],
    )
    def test_parses_all_five_timeframes(self, spec):
        assert parse_bar_dir(f"AAA.AMEX-{spec}-EXTERNAL") == ("AAA", "AMEX", spec)

    def test_multi_dot_ticker_splits_from_the_right(self):
        """split('.')[0] would give ticker 'BRK' and venue 'B' — silently wrong."""
        assert parse_bar_dir("BRK.B.NYSE-1-DAY-LAST-EXTERNAL") == ("BRK.B", "NYSE", "1-DAY-LAST")

    def test_rejects_non_external_directory(self):
        assert parse_bar_dir("AAA.AMEX-1-DAY-LAST-INTERNAL") is None

    def test_rejects_instrument_id_without_a_venue(self):
        assert parse_bar_dir("AAA-1-DAY-LAST-EXTERNAL") is None

    def test_rejects_garbage(self):
        assert parse_bar_dir("not-a-bar-directory") is None
        assert parse_bar_dir("") is None

    def test_rejects_empty_ticker_or_venue(self):
        assert parse_bar_dir(".AMEX-1-DAY-LAST-EXTERNAL") is None
        assert parse_bar_dir("AAA.-1-DAY-LAST-EXTERNAL") is None


@pytest.mark.unit
class TestPlanClassification:
    """Every directory lands in exactly one bucket."""

    def test_venue_change_becomes_an_action(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL", size=500)
        plan = _plan(tmp_path, {"AAA": "ARCA"})

        assert len(plan.actions) == 1
        action = plan.actions[0]
        assert (action.ticker, action.old_venue, action.new_venue) == ("AAA", "AMEX", "ARCA")
        assert action.dst_dir.name == "AAA.ARCA-1-DAY-LAST-EXTERNAL"
        assert action.old_bar_type == "AAA.AMEX-1-DAY-LAST-EXTERNAL"
        assert action.new_bar_type == "AAA.ARCA-1-DAY-LAST-EXTERNAL"
        assert action.total_bytes == 500

    def test_matching_venue_is_a_no_op(self, tmp_path):
        """If IBKR confirms the provisional venue, zero bytes move."""
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        plan = _plan(tmp_path, {"AAA": "AMEX"})

        assert plan.actions == ()
        assert plan.no_ops == 1

    def test_excluded_ticker_is_left_alone_and_named(self, tmp_path):
        """Excluded tickers keep their bars — not orphans, and not a reason to abort."""
        _make_bar_dir(tmp_path, "DEAD.AMEX-1-DAY-LAST-EXTERNAL")
        plan = _plan(tmp_path, {}, excluded_tickers=frozenset({"DEAD"}))

        assert plan.excluded == ("DEAD.AMEX-1-DAY-LAST-EXTERNAL",)
        assert plan.orphans == ()
        assert plan.actions == ()

    def test_ticker_with_no_target_venue_is_an_orphan(self, tmp_path):
        _make_bar_dir(tmp_path, "GHOST.AMEX-1-DAY-LAST-EXTERNAL")
        plan = _plan(tmp_path, {})

        assert plan.orphans == ("GHOST.AMEX-1-DAY-LAST-EXTERNAL",)
        assert plan.is_safe is False

    def test_existing_destination_is_a_collision(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "AAA.ARCA-1-DAY-LAST-EXTERNAL")
        plan = _plan(tmp_path, {"AAA": "ARCA"})

        assert plan.collisions == ("AAA.AMEX-1-DAY-LAST-EXTERNAL -> AAA.ARCA-1-DAY-LAST-EXTERNAL",)
        assert plan.actions == ()
        assert plan.is_safe is False

    def test_stray_file_makes_a_directory_unsafe(self, tmp_path):
        directory = _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        (directory / "notes.txt").write_text("hello")
        plan = _plan(tmp_path, {"AAA": "ARCA"})

        assert plan.unsafe == ("AAA.AMEX-1-DAY-LAST-EXTERNAL",)
        assert plan.actions == ()

    def test_empty_directory_is_unsafe(self, tmp_path):
        (tmp_path / "data" / "bar" / "AAA.AMEX-1-DAY-LAST-EXTERNAL").mkdir(parents=True)
        plan = _plan(tmp_path, {"AAA": "ARCA"})

        assert plan.unsafe == ("AAA.AMEX-1-DAY-LAST-EXTERNAL",)

    def test_unrecognised_directory_is_reported(self, tmp_path):
        (tmp_path / "data" / "bar" / "mystery-dir").mkdir(parents=True)
        plan = _plan(tmp_path, {})

        assert plan.unparsed == ("mystery-dir",)
        assert plan.is_safe is False

    def test_clean_plan_is_safe(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "BBB.NASDAQ-1-DAY-LAST-EXTERNAL")
        plan = _plan(tmp_path, {"AAA": "ARCA", "BBB": "NASDAQ"})

        assert plan.is_safe is True
        assert len(plan.actions) == 1
        assert plan.no_ops == 1

    def test_missing_bar_root_yields_an_empty_plan(self, tmp_path):
        plan = _plan(tmp_path, {"AAA": "ARCA"})
        assert plan.actions == ()
        assert plan.is_safe is True


@pytest.mark.unit
class TestStagedRollout:
    """A partial run must stay safe — that is the whole point of running one."""

    def test_only_tickers_scopes_without_orphaning_the_rest(self, tmp_path):
        """Unlisted tickers are skipped, NOT reclassified as orphans.

        Trimming target_venues instead would make every ticker outside the subset
        look unaccounted-for, tripping the safety check and forcing --force — which
        would turn the cautious option into the dangerous one.
        """
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "BBB.CBOE-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "CCC.AMEX-1-DAY-LAST-EXTERNAL")

        plan = _plan(
            tmp_path,
            {"AAA": "ARCA", "BBB": "BATS", "CCC": "ARCA"},
            only_tickers=frozenset({"AAA"}),
        )

        assert plan.is_safe is True
        assert [a.ticker for a in plan.actions] == ["AAA"]
        assert plan.orphans == ()
        assert plan.no_ops == 0

    def test_subset_still_reports_its_own_problems(self, tmp_path):
        """Scoping must not suppress a real problem inside the subset."""
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "AAA.ARCA-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "BBB.CBOE-1-DAY-LAST-EXTERNAL")

        plan = _plan(tmp_path, {"AAA": "ARCA", "BBB": "BATS"}, only_tickers=frozenset({"AAA"}))

        assert plan.collisions
        assert plan.is_safe is False

    def test_no_subset_covers_everything(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL")
        _make_bar_dir(tmp_path, "BBB.CBOE-1-DAY-LAST-EXTERNAL")

        plan = _plan(tmp_path, {"AAA": "ARCA", "BBB": "BATS"})

        assert len(plan.actions) == 2


@pytest.mark.unit
class TestPlanArithmetic:
    """The numbers the operator reads before approving 39 GB of rewriting."""

    def test_bytes_and_ticker_counts(self, tmp_path):
        for spec in ("1-DAY-LAST", "1-HOUR-LAST"):
            _make_bar_dir(tmp_path, f"AAA.AMEX-{spec}-EXTERNAL", size=1000)
        _make_bar_dir(tmp_path, "BBB.CBOE-1-DAY-LAST-EXTERNAL", size=500)
        plan = _plan(tmp_path, {"AAA": "ARCA", "BBB": "BATS"})

        assert len(plan.actions) == 3
        assert plan.tickers_affected == 2
        assert plan.bytes_to_rewrite == 2500

    def test_peak_extra_bytes_is_the_largest_n(self, tmp_path):
        for i, size in enumerate([100, 500, 900]):
            _make_bar_dir(tmp_path, f"T{i}.AMEX-1-DAY-LAST-EXTERNAL", size=size)
        plan = _plan(tmp_path, {f"T{i}": "ARCA" for i in range(3)})

        assert plan.peak_extra_bytes(1) == 900
        assert plan.peak_extra_bytes(2) == 1400
        assert plan.peak_extra_bytes(10) == 1500

    def test_peak_extra_bytes_of_an_empty_plan_is_zero(self, tmp_path):
        assert _plan(tmp_path, {}).peak_extra_bytes(6) == 0

    def test_actions_are_ordered_largest_first(self, tmp_path):
        """A parallel run must not end with one huge file finishing alone."""
        for i, size in enumerate([100, 900, 500]):
            _make_bar_dir(tmp_path, f"T{i}.AMEX-1-DAY-LAST-EXTERNAL", size=size)
        plan = _plan(tmp_path, {f"T{i}": "ARCA" for i in range(3)})

        assert [a.total_bytes for a in plan.actions] == [900, 500, 100]

    def test_ordering_is_deterministic_for_equal_sizes(self, tmp_path):
        for name in ("CCC", "AAA", "BBB"):
            _make_bar_dir(tmp_path, f"{name}.AMEX-1-DAY-LAST-EXTERNAL", size=100)
        plan = _plan(tmp_path, {n: "ARCA" for n in ("AAA", "BBB", "CCC")})

        assert [a.ticker for a in plan.actions] == ["AAA", "BBB", "CCC"]

    def test_moves_by_venue_pair_summarises(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL", size=100)
        _make_bar_dir(tmp_path, "BBB.AMEX-1-DAY-LAST-EXTERNAL", size=200)
        _make_bar_dir(tmp_path, "CCC.CBOE-1-DAY-LAST-EXTERNAL", size=50)
        plan = _plan(tmp_path, {"AAA": "ARCA", "BBB": "ARCA", "CCC": "BATS"})

        assert plan.moves_by_venue_pair() == {
            ("AMEX", "ARCA"): (2, 300),
            ("CBOE", "BATS"): (1, 50),
        }

    def test_multi_file_directory_sums_and_lists_every_file(self, tmp_path):
        _make_bar_dir(tmp_path, "AAA.AMEX-1-DAY-LAST-EXTERNAL", size=100, files=3)
        plan = _plan(tmp_path, {"AAA": "ARCA"})

        assert len(plan.actions[0].files) == 3
        assert plan.actions[0].total_bytes == 300
