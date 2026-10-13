"""Unit tests for the venue backfill runner.

The runner's job is to survive a long job against a flaky service. What is worth
pinning is therefore not "does it call the API" but the three survival properties:
resume without redoing or losing work, keep going when the Gateway wedges, and
overlap the 10-second error-200 stalls instead of queueing behind them.
"""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src.models.instrument_metadata import ResolutionStatus
from src.services.metadata.providers.ibkr_venue_provider import (
    VenueOutcome,
    VenueQualification,
)
from src.services.metadata.venue_backfill import (
    BackfillConfig,
    BackfillTally,
    CheckpointWriter,
    RateLimiter,
    load_checkpoint,
    load_worklist,
    run_backfill,
    select_pending,
    to_record,
)


@dataclass
class FakeMetaRow:
    ticker: str


class FakeMetaRepo:
    def __init__(self, by_status: dict):
        self._by_status = by_status

    def list_by_status(self, status):
        return [FakeMetaRow(t) for t in self._by_status.get(status, [])]


def _qual(ticker: str, outcome: VenueOutcome, venue=None) -> VenueQualification:
    return VenueQualification(ticker=ticker, outcome=outcome, venue=venue)


def _config(tmp_path: Path, **kwargs) -> BackfillConfig:
    """Test config: no pacing, no reconnect backoff — neither is under test here."""
    kwargs.setdefault("backoff_base_s", 0.0)
    return BackfillConfig(state_dir=tmp_path, rate_per_s=0, flush_every=1, **kwargs)


class ScriptedQualifier:
    """Returns a scripted outcome per ticker, recording call order.

    Also records *observed concurrency* — how many qualify() calls were in flight
    at once, and the order in which they completed. Overlap is a property of the
    scheduler, so it is asserted from these counters rather than from wall-clock
    elapsed time: a timing bound cheap enough to be meaningful is also tight
    enough to flake under `pytest -n auto`, where many workers compete for CPU.
    """

    def __init__(self, script: dict, *, delay: dict | None = None):
        self.script = script
        self.delay = delay or {}
        self.calls: list[str] = []
        self.completed: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def qualify(self, ticker: str) -> VenueQualification:
        self.calls.append(ticker)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if ticker in self.delay:
                await asyncio.sleep(self.delay[ticker])
            return self.script.get(ticker, _qual(ticker, VenueOutcome.NOT_FOUND))
        finally:
            self.in_flight -= 1
            self.completed.append(ticker)


@pytest.mark.unit
class TestWorklist:
    """Which tickers are worth asking about."""

    def test_collects_unresolved_and_venue_unresolved_sorted(self):
        repo = FakeMetaRepo(
            {
                ResolutionStatus.VENUE_UNRESOLVED: ["ZZZ", "AAA"],
                ResolutionStatus.UNRESOLVED: ["MMM"],
            }
        )
        assert load_worklist(repo) == ["AAA", "MMM", "ZZZ"]

    def test_resolved_excluded_by_default(self):
        repo = FakeMetaRepo(
            {
                ResolutionStatus.VENUE_UNRESOLVED: ["AAA"],
                ResolutionStatus.RESOLVED: ["SPY"],
            }
        )
        assert load_worklist(repo) == ["AAA"]

    def test_include_resolved_adds_them_for_reverification(self):
        repo = FakeMetaRepo(
            {
                ResolutionStatus.VENUE_UNRESOLVED: ["AAA"],
                ResolutionStatus.RESOLVED: ["SPY"],
            }
        )
        assert load_worklist(repo, include_resolved=True) == ["AAA", "SPY"]

    def test_excluded_tickers_are_never_reasked(self):
        """A human adjudicated these; a machine re-asking does not overturn it."""
        repo = FakeMetaRepo(
            {
                ResolutionStatus.VENUE_UNRESOLVED: ["AAA"],
                ResolutionStatus.EXCLUDED: ["DEAD"],
            }
        )
        assert load_worklist(repo, include_resolved=True) == ["AAA"]

    def test_duplicates_across_statuses_collapse(self):
        repo = FakeMetaRepo(
            {
                ResolutionStatus.VENUE_UNRESOLVED: ["AAA"],
                ResolutionStatus.UNRESOLVED: ["AAA"],
            }
        )
        assert load_worklist(repo) == ["AAA"]


@pytest.mark.unit
class TestCheckpoint:
    """Crash-safety is a property of the format, not of a commit step."""

    def test_round_trip(self, tmp_path):
        path = tmp_path / "checkpoint.jsonl"
        original = VenueQualification(
            ticker="AAA",
            outcome=VenueOutcome.RESOLVED,
            venue="ARCA",
            primary_exchange="ARCA",
            con_id=42,
            detail="1 contract(s)",
            attempts=1,
            elapsed_s=0.25,
        )
        with CheckpointWriter(path) as writer:
            writer.write(original)

        restored = load_checkpoint(path)["AAA"]
        assert restored.venue == "ARCA"
        assert restored.con_id == 42
        assert restored.outcome is VenueOutcome.RESOLVED

    def test_missing_file_is_a_fresh_run(self, tmp_path):
        assert load_checkpoint(tmp_path / "nope.jsonl") == {}

    def test_torn_final_line_is_dropped_not_fatal(self, tmp_path):
        """The normal shape of a hard crash mid-write."""
        path = tmp_path / "checkpoint.jsonl"
        good = json.dumps(to_record(_qual("AAA", VenueOutcome.RESOLVED, "ARCA")))
        path.write_text(good + '\n{"ticker": "BBB", "outcome": "res\n', encoding="utf-8")

        restored = load_checkpoint(path)

        assert set(restored) == {"AAA"}

    def test_unknown_outcome_value_is_dropped(self, tmp_path):
        path = tmp_path / "checkpoint.jsonl"
        path.write_text('{"ticker": "AAA", "outcome": "banana"}\n', encoding="utf-8")
        assert load_checkpoint(path) == {}

    def test_last_write_wins_per_ticker(self, tmp_path):
        """Concurrent lanes append out of order; the newest record is the truth."""
        path = tmp_path / "checkpoint.jsonl"
        with CheckpointWriter(path) as writer:
            writer.write(_qual("AAA", VenueOutcome.ERROR))
            writer.write(_qual("AAA", VenueOutcome.RESOLVED, "ARCA"))

        assert load_checkpoint(path)["AAA"].outcome is VenueOutcome.RESOLVED

    def test_writer_appends_across_sessions(self, tmp_path):
        path = tmp_path / "checkpoint.jsonl"
        with CheckpointWriter(path) as writer:
            writer.write(_qual("AAA", VenueOutcome.RESOLVED, "ARCA"))
        with CheckpointWriter(path) as writer:
            writer.write(_qual("BBB", VenueOutcome.NOT_FOUND))

        assert set(load_checkpoint(path)) == {"AAA", "BBB"}


@pytest.mark.unit
class TestSelectPending:
    """Resume policy: never re-ask a question IBKR already answered."""

    def test_terminal_outcomes_are_skipped(self):
        done = {
            "AAA": _qual("AAA", VenueOutcome.RESOLVED, "ARCA"),
            "BBB": _qual("BBB", VenueOutcome.NOT_FOUND),
            "CCC": _qual("CCC", VenueOutcome.AMBIGUOUS),
        }
        assert select_pending(["AAA", "BBB", "CCC", "DDD"], done) == ["DDD"]

    def test_errors_are_retried_by_default(self):
        done = {"AAA": _qual("AAA", VenueOutcome.ERROR)}
        assert select_pending(["AAA"], done) == ["AAA"]

    def test_retry_errors_disabled_skips_them(self):
        done = {"AAA": _qual("AAA", VenueOutcome.ERROR)}
        assert select_pending(["AAA"], done, retry_errors=False) == []

    def test_limit_truncates(self):
        assert select_pending(["A", "B", "C"], {}, limit=2) == ["A", "B"]

    def test_empty_checkpoint_means_everything_pending(self):
        assert select_pending(["A", "B"], {}) == ["A", "B"]


@pytest.mark.unit
class TestRateLimiter:
    """Paces outbound requests without wall-clock sleeping in tests."""

    async def test_schedules_at_the_configured_interval(self):
        now = [0.0]
        limiter = RateLimiter(4.0, clock=lambda: now[0])
        waits = []

        async def fake_sleep(d):
            waits.append(d)
            now[0] += d

        original = asyncio.sleep
        asyncio.sleep = fake_sleep  # type: ignore[assignment]
        try:
            for _ in range(3):
                await limiter.acquire()
        finally:
            asyncio.sleep = original  # type: ignore[assignment]

        assert waits == pytest.approx([0.25, 0.25])

    async def test_zero_rate_never_sleeps(self):
        limiter = RateLimiter(0)
        await limiter.acquire()
        await limiter.acquire()


@pytest.mark.unit
class TestRunBackfill:
    """The survival properties."""

    async def _run(self, tickers, qualifier, tmp_path, **cfg_kwargs):
        config = _config(tmp_path, **cfg_kwargs)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            return await run_backfill(
                tickers=tickers, qualifier=qualifier, config=config, writer=writer
            )

    async def test_tallies_every_outcome(self, tmp_path):
        qualifier = ScriptedQualifier(
            {
                "AAA": _qual("AAA", VenueOutcome.RESOLVED, "ARCA"),
                "BBB": _qual("BBB", VenueOutcome.NOT_FOUND),
                "CCC": _qual("CCC", VenueOutcome.AMBIGUOUS),
                "DDD": _qual("DDD", VenueOutcome.ERROR),
            }
        )
        tally, results = await self._run(["AAA", "BBB", "CCC", "DDD"], qualifier, tmp_path)

        assert (tally.resolved, tally.not_found, tally.ambiguous, tally.error) == (1, 1, 1, 1)
        assert tally.attempted == 4
        assert set(results) == {"AAA", "BBB", "CCC", "DDD"}

    async def test_every_ticker_is_checkpointed_exactly_once(self, tmp_path):
        tickers = [f"T{i}" for i in range(20)]
        qualifier = ScriptedQualifier({t: _qual(t, VenueOutcome.RESOLVED, "ARCA") for t in tickers})
        config = _config(tmp_path, concurrency=4)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            await run_backfill(tickers=tickers, qualifier=qualifier, config=config, writer=writer)

        lines = config.checkpoint_path.read_text().strip().splitlines()
        assert len(lines) == 20
        assert sorted(qualifier.calls) == sorted(tickers)

    async def test_concurrency_overlaps_stalls(self, tmp_path):
        """The load-bearing property: 4 lanes turn 8 stalls into ~2 stall-periods.

        Sequentially this would be 8 x 0.05s; chunked-gather would also serialise
        badly. With independent lanes all four must be stalled *simultaneously*.

        Asserted from observed concurrency, not elapsed time. Every ticker sleeps
        here, so a lane that has claimed one cannot progress until that sleep
        resolves — if all 4 lanes overlap, the peak is exactly the configured
        concurrency. That holds on a single-threaded event loop no matter how
        contended the machine is, whereas the wall-clock bound this replaced
        (< 0.2s against a ~0.1s ideal) flaked under `pytest -n auto`.
        """
        tickers = [f"T{i}" for i in range(8)]
        qualifier = ScriptedQualifier(
            {t: _qual(t, VenueOutcome.NOT_FOUND) for t in tickers},
            delay={t: 0.05 for t in tickers},
        )
        await self._run(tickers, qualifier, tmp_path, concurrency=4)

        assert qualifier.max_in_flight == 4, (
            f"peak concurrency was {qualifier.max_in_flight}, expected 4 — "
            "lanes are not overlapping"
        )
        assert len(qualifier.completed) == 8

    async def test_uneven_stalls_do_not_block_other_lanes(self, tmp_path):
        """One slow ticker must not hold up the fast ones behind it."""
        tickers = ["SLOW"] + [f"F{i}" for i in range(6)]
        qualifier = ScriptedQualifier(
            {t: _qual(t, VenueOutcome.RESOLVED, "ARCA") for t in tickers},
            delay={"SLOW": 0.2},
        )
        await self._run(tickers, qualifier, tmp_path, concurrency=3)

        # Bounded by the single slow request, not by slow + everything after it:
        # SLOW is claimed first and sleeps, so if its lane-mates are independent
        # every fast ticker resolves while SLOW is still in flight, leaving SLOW
        # last to complete. Ordering is deterministic; elapsed time is not.
        assert qualifier.completed[-1] == "SLOW", (
            f"completion order was {qualifier.completed} — a stall blocked its lane-mates"
        )
        assert set(qualifier.completed[:-1]) == {f"F{i}" for i in range(6)}

    async def test_reconnect_fires_after_five_consecutive_errors(self, tmp_path):
        tickers = [f"E{i}" for i in range(6)]
        qualifier = ScriptedQualifier({t: _qual(t, VenueOutcome.ERROR) for t in tickers})
        reconnects = []

        async def reconnect():
            reconnects.append(True)

        config = _config(tmp_path, concurrency=1)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            tally, _ = await run_backfill(
                tickers=tickers,
                qualifier=qualifier,
                config=config,
                writer=writer,
                reconnect=reconnect,
            )

        assert len(reconnects) == 1
        assert tally.reconnects == 1

    async def test_success_resets_the_error_streak(self, tmp_path):
        """Scattered errors are ticker problems, not Gateway problems."""
        script = {f"E{i}": _qual(f"E{i}", VenueOutcome.ERROR) for i in range(4)}
        script["OK"] = _qual("OK", VenueOutcome.RESOLVED, "ARCA")
        order = ["E0", "E1", "OK", "E2", "E3"]
        reconnects = []

        async def reconnect():
            reconnects.append(True)

        config = _config(tmp_path, concurrency=1)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            await run_backfill(
                tickers=order,
                qualifier=ScriptedQualifier(script),
                config=config,
                writer=writer,
                reconnect=reconnect,
            )

        assert reconnects == []

    async def test_max_reconnects_caps_the_retry_budget(self, tmp_path):
        tickers = [f"E{i}" for i in range(30)]
        qualifier = ScriptedQualifier({t: _qual(t, VenueOutcome.ERROR) for t in tickers})
        reconnects = []

        async def reconnect():
            reconnects.append(True)

        config = _config(tmp_path, concurrency=1, max_reconnects=2)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            tally, _ = await run_backfill(
                tickers=tickers,
                qualifier=qualifier,
                config=config,
                writer=writer,
                reconnect=reconnect,
            )

        assert len(reconnects) == 2
        assert tally.reconnects == 2

    async def test_stop_flag_drains_and_reports_partial(self, tmp_path):
        tickers = [f"T{i}" for i in range(10)]
        stop = asyncio.Event()

        class StopAfterThree(ScriptedQualifier):
            async def qualify(self, ticker):
                result = await super().qualify(ticker)
                if len(self.calls) >= 3:
                    stop.set()
                return result

        qualifier = StopAfterThree({t: _qual(t, VenueOutcome.RESOLVED, "ARCA") for t in tickers})
        config = _config(tmp_path, concurrency=1)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            tally, _ = await run_backfill(
                tickers=tickers,
                qualifier=qualifier,
                config=config,
                writer=writer,
                stop_flag=stop,
            )

        assert tally.stopped_early is True
        assert tally.attempted < 10
        assert "partial" in tally.as_result_line(1.0)

    async def test_already_done_count_flows_into_the_tally(self, tmp_path):
        qualifier = ScriptedQualifier({"AAA": _qual("AAA", VenueOutcome.RESOLVED, "ARCA")})
        config = _config(tmp_path)
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            tally, _ = await run_backfill(
                tickers=["AAA"],
                qualifier=qualifier,
                config=config,
                writer=writer,
                already_done=99,
            )

        assert tally.skipped == 99

    async def test_empty_worklist_is_a_clean_noop(self, tmp_path):
        tally, results = await self._run([], ScriptedQualifier({}), tmp_path)
        assert tally.attempted == 0
        assert results == {}


@pytest.mark.unit
class TestResumeEndToEnd:
    """Resume must neither redo nor lose work."""

    async def test_second_run_skips_terminal_and_retries_errors(self, tmp_path):
        tickers = ["AAA", "BBB", "CCC"]
        config = _config(tmp_path, concurrency=1)

        first = ScriptedQualifier(
            {
                "AAA": _qual("AAA", VenueOutcome.RESOLVED, "ARCA"),
                "BBB": _qual("BBB", VenueOutcome.NOT_FOUND),
                "CCC": _qual("CCC", VenueOutcome.ERROR),
            }
        )
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            await run_backfill(
                tickers=select_pending(tickers, {}),
                qualifier=first,
                config=config,
                writer=writer,
            )

        done = load_checkpoint(config.checkpoint_path)
        pending = select_pending(tickers, done)
        second = ScriptedQualifier({"CCC": _qual("CCC", VenueOutcome.RESOLVED, "BATS")})
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            await run_backfill(tickers=pending, qualifier=second, config=config, writer=writer)

        assert pending == ["CCC"]
        assert second.calls == ["CCC"]
        final = load_checkpoint(config.checkpoint_path)
        assert final["CCC"].venue == "BATS"
        assert final["AAA"].venue == "ARCA"

    async def test_rerun_after_full_completion_asks_nothing(self, tmp_path):
        tickers = ["AAA", "BBB"]
        config = _config(tmp_path, concurrency=1)
        qualifier = ScriptedQualifier({t: _qual(t, VenueOutcome.RESOLVED, "ARCA") for t in tickers})
        with CheckpointWriter(config.checkpoint_path, flush_every=1) as writer:
            await run_backfill(tickers=tickers, qualifier=qualifier, config=config, writer=writer)

        pending = select_pending(tickers, load_checkpoint(config.checkpoint_path))

        assert pending == []


@pytest.mark.unit
class TestTally:
    """The single machine-parseable output line."""

    def test_result_line_reports_ok_when_complete(self):
        tally = BackfillTally(resolved=10, not_found=2)
        line = tally.as_result_line(123.45)
        assert line.startswith("RESULT: ok ")
        assert "resolved=10" in line
        assert "elapsed=123.5" in line

    def test_result_line_reports_partial_when_stopped(self):
        tally = BackfillTally(resolved=1, stopped_early=True)
        assert tally.as_result_line(1.0).startswith("RESULT: partial ")

    def test_attempted_sums_the_four_outcomes(self):
        tally = BackfillTally(resolved=1, not_found=2, ambiguous=3, error=4, skipped=99)
        assert tally.attempted == 10
