"""Procedure P8 harness — contain a failing strategy without losing the session.

Why this script exists. P8 needs a **two-strategy** session, and
`ntrader live create --strategy` is deliberately singular (Story 2.7 chose not
to add `multiple=True`), so the CLI cannot express the session under test.
`docs/qa/phase3-live-verification.md` sanctions building the spec in-process
instead. That is all this script does differently: everything after the spec —
the claim, the record, the runner, the eight AR39 phases, the strategy guard,
Redis and Postgres — is the same production code path `live start` runs.

What makes the failure genuine. The raiser is a **real** `sma_crossover` fed a
`0.00` close, which divides by it at `sma_crossover.py:150`
(`position_value / current_price`) and raises `decimal.DivisionByZero` through
the real `Actor.handle_bar` re-raise. No monkeypatched `raise`, no probe
strategy standing in for one. The zero bar is published onto the node's real
`MessageBus` on its own event loop, because no live IBKR feed will ever print a
zero-priced AAPL bar. The sibling `momentum` runs on the same real feed
throughout and is what criterion 1's "the other strategies were unaffected"
half is read from.

Usage:
    PYTHONPATH=. uv run python scripts/diagnostics/live_contain_probe.py \
        --name contain-test-1 --settle-seconds 75 --observe-seconds 150
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

# The probe runs from any working directory, matching live_connection_loss_probe.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import structlog  # noqa: E402

from src.cli.commands.live_start import claim_session, release_quietly  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.core.live_cache import build_cache_config  # noqa: E402
from src.core.live_session_runner import LiveSessionRunner  # noqa: E402
from src.db.exceptions import DuplicateRecordError  # noqa: E402
from src.db.repositories.trading_session_repository_sync import (  # noqa: E402
    SyncTradingSessionRepository,
)
from src.db.session_sync import get_sync_session  # noqa: E402
from src.models.session import SessionSpec, StrategySpec  # noqa: E402
from src.services.session_record import SqlSessionRecord  # noqa: E402

logger = structlog.get_logger(__name__)

RAISER = "sma_crossover"
SIBLING = "momentum"


def _ensure_start_failure_session(name: str, bar_type: str, *, all_fail: bool) -> None:
    """Create a session whose first spec raises in `on_start` (criterion 5).

    With ``all_fail`` false the spec is [raiser, momentum] and the expectation is
    that `momentum` still starts, `trading` reaches `status=ok`, and
    `session.started` names only the strategies that actually started. With it
    true the spec is [raiser] alone and the expectation is the opposite end of
    the same rule: `trading` logs `failed`, the sequence stops, and the CLI exits
    1 with `NoStrategyStartedError` naming the specs.
    """
    import scripts.diagnostics.p8_start_failure_strategy  # noqa: F401  # registers p8_start_raiser

    settings = get_settings()
    raiser = StrategySpec.from_overrides(
        strategy_id="p8_start_raiser", overrides={}, settings=settings, bar_types=(bar_type,)
    )
    strategies = (
        (raiser,)
        if all_fail
        else (
            raiser,
            StrategySpec.from_overrides(
                strategy_id=SIBLING, overrides={}, settings=settings, bar_types=(bar_type,)
            ),
        )
    )
    try:
        with get_sync_session() as session:
            row = SyncTradingSessionRepository(session).create(
                name=name, spec=SessionSpec(strategies=strategies).to_stored()
            )
            print(f"[p8] created start-failure session {name} session_id={row.session_id}")
    except DuplicateRecordError:
        print(f"[p8] session {name} already exists; reusing it")


def _ensure_session(name: str, raiser_bar_type: str, sibling_bar_type: str) -> None:
    """Create the two-strategy row if it does not already exist.

    Uses the same repository `live create` uses, so the row is indistinguishable
    from a CLI-created one apart from carrying two strategies instead of one.

    The two strategies deliberately take **different** instruments.
    `materialise_strategy` derives `instrument_id` from `bar_types[0]`
    (`live_session_node.py:259-262`), and `_generate_sell_signal` closes any
    open long on its own instrument *before* it reaches the division
    (`sma_crossover.py:239-244`). Pointing the raiser at an instrument the
    account holds no position in is what keeps the contained failure from
    submitting a real closing order — which is precisely what criterion 3
    measures.
    """
    settings = get_settings()
    spec = SessionSpec(
        strategies=(
            StrategySpec.from_overrides(
                strategy_id=RAISER, overrides={}, settings=settings, bar_types=(raiser_bar_type,)
            ),
            StrategySpec.from_overrides(
                strategy_id=SIBLING, overrides={}, settings=settings, bar_types=(sibling_bar_type,)
            ),
        )
    )
    try:
        with get_sync_session() as session:
            row = SyncTradingSessionRepository(session).create(name=name, spec=spec.to_stored())
            print(f"[p8] created session {name} session_id={row.session_id}")
    except DuplicateRecordError:
        print(f"[p8] session {name} already exists; reusing it")


def _synthetic_bar(bar_type_str: str, price_str: str, offset_ns: int = 0):
    """A real `Bar` for the subscribed type, flat at `price_str`."""
    from nautilus_trader.model.data import Bar, BarType
    from nautilus_trader.model.objects import Price, Quantity

    price = Price.from_str(price_str)
    now = time.time_ns() + offset_ns
    return Bar(
        bar_type=BarType.from_str(bar_type_str),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Quantity.from_int(1_000),
        ts_event=now,
        ts_init=now,
    )


def _subscriptions(runner: LiveSessionRunner, topics: tuple[str, ...]) -> dict[str, int]:
    """How many handlers each bar topic still has on the real `MessageBus`.

    Criterion 6's "still subscribed to its bar type" is a statement about the
    bus, not about delivery: a DEGRADED component stops *processing* because
    `Actor.handle_bar` gates on its FSM state, while its subscription survives
    so the strategy could resume. Counting handlers is what tells those two
    apart.
    """
    try:
        msgbus = runner._node.kernel.msgbus  # noqa: SLF001 - diagnostic
        return {t: len(msgbus.subscriptions(f"data.bars.{t}")) for t in topics}
    except Exception as exc:  # pragma: no cover - diagnostic only
        return {"<unavailable>": -1, "error": str(exc)}  # type: ignore[dict-item]


def _open_positions(runner: LiveSessionRunner) -> list[str]:
    """Every open position the node's cache knows about, as stable strings."""
    try:
        cache = runner._node.kernel.cache  # noqa: SLF001 - diagnostic
        return sorted(
            f"{p.instrument_id} {p.side.name} {p.quantity} id={p.id}"
            for p in cache.positions_open()
        )
    except Exception as exc:  # pragma: no cover - diagnostic only
        return [f"<unavailable: {exc}>"]


def _count_bars(runner: LiveSessionRunner, counts: dict[str, int]) -> None:
    """Count deliveries per strategy by rebinding `on_bar` after start.

    `Actor.handle_bar` looks `self.on_bar` up at call time, so a rebind after
    `subscribe_bars` is still reached — unlike `handle_bar` itself, whose bound
    reference the bus captured during `on_start`. The guard therefore stays
    outermost and the real re-raise path is untouched.
    """
    for strategy in runner._node.trader.strategies():  # noqa: SLF001 - diagnostic
        name = type(strategy).__name__
        counts.setdefault(name, 0)
        base = strategy.on_bar

        def on_bar(bar, _base=base, _name=name):
            counts[_name] = counts.get(_name, 0) + 1
            return _base(bar)

        strategy.on_bar = on_bar


def _start_failure_watcher(runner: LiveSessionRunner, observe: float) -> None:
    """Criterion 5: observe which strategies survived a start-path failure.

    Nothing is injected here — the failure happens during `trading`, before this
    thread has anything to do. It waits for the sequence to settle, records what
    started, and stops the session. In `all-fail` mode the runner raises before
    `trader_started` is ever set, so the wait simply times out and `main` reports
    the exception instead.
    """
    deadline = time.monotonic() + 120
    while not runner.trader_started and time.monotonic() < deadline:
        time.sleep(0.5)
    if not runner.trader_started:
        print("[p8] trader never started (expected in all-fail mode)", flush=True)
        return

    time.sleep(min(observe, 45.0))
    try:
        states = runner._node.trader.strategy_states()  # noqa: SLF001 - diagnostic
        print(f"[p8] strategy_states: {states}", flush=True)
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"[p8] strategy_states unavailable: {exc}", flush=True)
    print("[p8] sending SIGINT to stop the session", flush=True)
    os.kill(os.getpid(), signal.SIGINT)


def _injector(
    runner: LiveSessionRunner,
    raiser_bar_type: str,
    settle: float,
    observe: float,
    counts: dict[str, int],
    prime_bars: int,
    prime_price: str,
    topics: tuple[str, ...] = (),
) -> None:
    """Wait for trading, prime the SMAs flat, then drop one 0.00 bar on them.

    The priming bars are all at the **same** price, which is what makes the
    failure deterministic rather than a coin flip. `on_bar` returns early until
    both SMAs are initialised (`sma_crossover.py:105-106`), and a crossover is
    only detected on a strict inequality — so a flat run initialises both
    averages to the identical value and triggers neither branch, submitting no
    orders. The 0.00 bar then drags the fast average below the slow one from
    `prev_fast == prev_slow`, which is exactly the bearish branch's guard
    (`prev_fast >= prev_slow and fast < slow`), reaching
    `_calculate_position_size` and dividing by the zero close.
    """
    deadline = time.monotonic() + 300
    while not runner.trader_started and time.monotonic() < deadline:
        time.sleep(0.5)
    if not runner.trader_started:
        print("[p8] FAIL trader never started", flush=True)
        os.kill(os.getpid(), signal.SIGINT)
        return

    _count_bars(runner, counts)
    print(f"[p8] trading; letting real bars flow for {settle}s before injecting", flush=True)
    time.sleep(settle)
    print(f"[p8] bars before injection: {dict(counts)}", flush=True)
    print(f"[p8] open positions BEFORE: {_open_positions(runner)}", flush=True)
    print(f"[p8] bar subscriptions BEFORE: {_subscriptions(runner, topics)}", flush=True)

    loop = runner._loop  # noqa: SLF001 - diagnostic
    msgbus = runner._node.kernel.msgbus  # noqa: SLF001 - diagnostic
    topic = f"data.bars.{raiser_bar_type}"

    def _publish_sequence() -> None:
        for i in range(prime_bars):
            msgbus.publish(topic, _synthetic_bar(raiser_bar_type, prime_price, offset_ns=i * 1000))
        msgbus.publish(topic, _synthetic_bar(raiser_bar_type, "0.00", offset_ns=prime_bars * 1000))

    print(
        f"[p8] priming {prime_bars} flat bars at {prime_price} then one 0.00 bar on {topic}",
        flush=True,
    )
    loop.call_soon_threadsafe(_publish_sequence)

    print(f"[p8] observing {observe}s after the failure", flush=True)
    time.sleep(observe)
    print(f"[p8] bars after injection: {dict(counts)}", flush=True)
    print(f"[p8] open positions AFTER: {_open_positions(runner)}", flush=True)
    print(f"[p8] bar subscriptions AFTER: {_subscriptions(runner, topics)}", flush=True)

    try:
        states = runner._node.trader.strategy_states()  # noqa: SLF001 - diagnostic
        print(f"[p8] strategy_states: {states}", flush=True)
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"[p8] strategy_states unavailable: {exc}", flush=True)

    print("[p8] sending SIGINT to stop the session", flush=True)
    os.kill(os.getpid(), signal.SIGINT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="contain-test-1")
    parser.add_argument(
        "--raiser-bar-type",
        default="MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        help="The failing strategy's instrument. Must be one the account holds no position in.",
    )
    parser.add_argument(
        "--sibling-bar-type",
        default="AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL",
        help="The surviving strategy's instrument, on a real feed throughout.",
    )
    parser.add_argument("--settle-seconds", type=float, default=75.0)
    parser.add_argument("--observe-seconds", type=float, default=150.0)
    parser.add_argument("--prime-bars", type=int, default=25)
    parser.add_argument("--prime-price", default="500.00")
    parser.add_argument(
        "--create-only",
        action="store_true",
        help="Create the two-strategy row and exit, without starting a session.",
    )
    parser.add_argument(
        "--mode",
        choices=("contain", "start-failure", "all-fail"),
        default="contain",
        help=(
            "contain: criteria 1-4 and 6, a running strategy raising on a bar. "
            "start-failure: criterion 5's first half, one spec raising in on_start "
            "beside a healthy sibling. all-fail: criterion 5's second half, every "
            "spec raising, expecting NoStrategyStartedError and exit 1."
        ),
    )
    args = parser.parse_args()

    if args.mode == "contain":
        _ensure_session(args.name, args.raiser_bar_type, args.sibling_bar_type)
    else:
        _ensure_start_failure_session(
            args.name, args.sibling_bar_type, all_fail=args.mode == "all-fail"
        )
    if args.create_only:
        return 0

    # The start-failure modes must resolve `p8_start_raiser` when the stored spec
    # is read back, so the registration has to be in this process too.
    if args.mode != "contain":
        import scripts.diagnostics.p8_start_failure_strategy  # noqa: F401  # registers the strategy

    settings = get_settings()
    session_id, spec_payload, started_at = claim_session(args.name)
    record = SqlSessionRecord(session_id, started_at=started_at)
    try:
        runner = LiveSessionRunner(
            settings.ibkr,
            session_id=session_id,
            spec=SessionSpec.from_stored(spec_payload),
            record=record,
            started_at=started_at,
            cache=build_cache_config(settings.redis),
        )
    except Exception:
        release_quietly(SqlSessionRecord(session_id, started_at=started_at))
        raise

    counts: dict[str, int] = {}
    if args.mode == "contain":
        watcher = threading.Thread(
            target=_injector,
            args=(
                runner,
                args.raiser_bar_type,
                args.settle_seconds,
                args.observe_seconds,
                counts,
                args.prime_bars,
                args.prime_price,
                (args.raiser_bar_type, args.sibling_bar_type),
            ),
            daemon=True,
        )
    else:
        watcher = threading.Thread(
            target=_start_failure_watcher, args=(runner, args.observe_seconds), daemon=True
        )
    watcher.start()

    try:
        runner.run()
    except Exception as exc:
        # `all-fail` is expected to land here with NoStrategyStartedError. Report
        # it rather than letting the traceback be the only record.
        print(f"[p8] runner raised {type(exc).__name__}: {exc}", flush=True)
        print(f"RESULT: raised mode={args.mode} error={type(exc).__name__}")
        return 1
    finally:
        print(f"[p8] final bar counts: {dict(counts)}", flush=True)
        failures = runner.contained_failures
        print(f"[p8] contained_failures={len(failures)} all_failed={runner.all_strategies_failed}")
        for failure in failures:
            print(f"[p8]   {failure}")
    print(f"RESULT: ok mode={args.mode} contained={len(runner.contained_failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
