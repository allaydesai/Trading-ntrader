"""Test doubles for the one-shot connectivity check (Story 1.7).

``TestLiveNode`` mimics only the ``TradingNode`` surface
``src/core/live_check_driver.py`` actually touches — ``build()``,
``run_async()``, ``stop()``, ``is_running()``, ``dispose()``, the two engines'
``check_connected()``, ``trader.actors()`` and ``cache.instruments()`` — so the
driver's sequence, its bounded waits and its shutdown discipline can be
exercised with no broker, no socket and no C extension (NFR32/NFR34).

``TestIBAccountsClient`` is the matching entry for the IB adapter's own
``IB_CLIENTS`` cache, so ``gateway_reported_accounts`` runs for real rather than
being mocked out.

``TestBarObserver`` is a **real** ``LiveBarObserver`` with its counters seeded,
which keeps the driver's ``isinstance`` scan honest — a fake observer would let
that scan rot.

Every class here defines ``__init__``, so pytest declines to collect them
("cannot collect test class ... because it has a __init__ constructor"). That is
the mechanism the repo's ``Test*``-in-``test_*.py`` doubles convention relies on;
do not add a class here without one.
"""

import asyncio
import time
from collections.abc import Mapping, Sequence

from src.core.live_bar_observer import LiveBarObserver, LiveBarObserverConfig


class TestInstrument:
    """The only thing the driver reads off a cached instrument: its id."""

    def __init__(self, instrument_id: str) -> None:
        self.id = instrument_id


class TestIBAccountsClient:
    """An ``IB_CLIENTS`` entry that names accounts, the way the adapter does."""

    def __init__(self, accounts: Sequence[str] = ()) -> None:
        self._accounts = frozenset(accounts)

    def accounts(self) -> frozenset[str]:
        return self._accounts


class TestBarObserver(LiveBarObserver):
    """A real observer with pre-seeded counters.

    Constructed bare — ``register_base`` is deliberately not called, because the
    driver only *reads* the observer (``total_received``,
    ``counts_by_bar_type()``, ``delayed_data_suspected``) and a bare construction
    is verified not to touch the Nautilus C logging subsystem.
    """

    def __init__(
        self,
        bar_types: Sequence[str],
        *,
        counts: Mapping[str, int] | None = None,
        delayed_data: bool = False,
    ) -> None:
        super().__init__(LiveBarObserverConfig(bar_types=tuple(bar_types)))
        if counts:
            self._counts.update(counts)
        self.delayed_data_suspected = delayed_data


class _TestEngine:
    """One of the node's two engines, as far as the driver is concerned.

    ``registered_clients`` exists because ``live_account_gate._placement_refusal``
    reads it *first*: ``check_connected()`` returns True on an empty client dict,
    so on a node whose ``build()`` never ran it would report "connected" with no
    socket ever opened. A truthy placeholder is what lets the account gate run
    for real against this double rather than refusing every time.
    """

    def __init__(self, connects_after: int) -> None:
        self._connects_after = connects_after
        self.poll_count = 0
        self.registered_clients = {"IB": object()}

    def check_connected(self) -> bool:
        self.poll_count += 1
        return self._connects_after >= 0 and self.poll_count > self._connects_after


class _TestKernel:
    def __init__(self, connects_after: int) -> None:
        self.data_engine = _TestEngine(connects_after)
        self.exec_engine = _TestEngine(connects_after)


class _TestTrader:
    """The ``Trader`` surface Story 2.5's runner registers components through.

    ⚠️ ``is_running`` here is an **attribute**, matching ``Trader.is_running``,
    which is a property. Do not confuse it with ``TestLiveNode.is_running()``,
    which is a *method*, matching ``TradingNode.is_running()``. The runner reads
    both, for different questions.

    Every addition is defaulted, because ``TestLiveNode`` is also constructed
    from ``test_live_check_driver.py``, ``test_epic1_ac_cli.py`` and
    ``test_epic1_ac_data.py``, all of which must keep passing unmodified.

    ``add_actor``/``add_strategy`` deliberately do **not** reproduce Nautilus's
    ``if self.is_running and not self._has_controller: return`` guard: a double
    cannot prove that branch, and pretending otherwise would make the
    controller's mutation proof (Task 9) pass vacuously. That proof runs against
    a real ``Trader`` instead.
    """

    def __init__(
        self,
        actors: Sequence[object],
        *,
        starts_running: bool = False,
        starts_after: int = 0,
    ) -> None:
        self._actors = list(actors)
        self._strategies: list[object] = []
        self._starts_after = starts_after
        self._is_running_reads = 0
        self._starts_running = starts_running
        self.added_actors: list[object] = []
        self.started_actors: list[object] = []
        self.added_strategies: list[object] = []
        self.started_strategies: list[object] = []
        self.subscriptions: list[tuple[str, object]] = []
        self.strategy_state_override: dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        """True once polled more than ``starts_after`` times, if it starts at all.

        Read as a *property*, exactly like ``Trader.is_running``, so a runner
        that accidentally calls it gets a ``TypeError`` here rather than a
        silently-truthy bound method in production.
        """
        self._is_running_reads += 1
        return self._starts_running and self._is_running_reads > self._starts_after

    def actors(self) -> list[object]:
        return list(self._actors)

    def strategies(self) -> list[object]:
        return list(self._strategies)

    def strategy_states(self) -> dict[str, str]:
        """``READY`` until started, then ``RUNNING`` — the two states the
        account gate's placement guard actually distinguishes.
        """
        if self.strategy_state_override:
            return dict(self.strategy_state_override)
        started = {str(identifier) for identifier in self.started_strategies}
        states = {}
        for index, strategy in enumerate(self._strategies):
            name = str(getattr(strategy, "id", f"strategy-{index}"))
            states[name] = "RUNNING" if name in started else "READY"
        return states

    def add_actor(self, actor: object) -> None:
        self._actors.append(actor)
        self.added_actors.append(actor)

    def start_actor(self, actor_id: object) -> None:
        self.started_actors.append(actor_id)

    def add_strategy(self, strategy: object) -> None:
        self._strategies.append(strategy)
        self.added_strategies.append(strategy)

    def start_strategy(self, strategy_id: object) -> None:
        self.started_strategies.append(strategy_id)

    def subscribe(self, topic: str, handler: object) -> None:
        self.subscriptions.append((topic, handler))


class _TestCache:
    def __init__(self, instrument_ids: Sequence[str]) -> None:
        self._instruments = [TestInstrument(name) for name in instrument_ids]

    def instruments(self) -> list[TestInstrument]:
        return list(self._instruments)


class TestLiveNode:
    """A stand-in ``TradingNode`` with knobs for every branch the driver has.

    Args:
        actors: What ``trader.actors()`` returns — normally one
            ``TestBarObserver``.
        instrument_ids: What ``cache.instruments()`` reports as loaded. An id
            the driver requested but that is absent here is the "IBKR never
            qualified the contract" case.
        connects_after: How many ``check_connected()`` polls precede a ``True``.
            ``-1`` never connects, which is the exit-code-4 shape.
        raise_on_build / raise_on_dispose / raise_on_stop: Exceptions to raise
            from those calls, so the shutdown discipline can be exercised.
        build_delay_seconds: How long ``build()`` blocks. Stands in for the
            adapter's own connect attempt, which ``client.start()`` drives
            synchronously inside ``node.build()``.
        run_forever: When True, ``run_async()`` blocks until cancelled — what a
            real node does. When False it returns immediately, which is what a
            node that failed to start looks like.
        run_seconds: When set (and ``run_forever`` is True), ``run_async()``
            returns after this long instead of blocking — a node that stops
            itself mid-run, which is what the observer's delayed-feed shutdown
            does to a live session.
        trader_starts: Whether ``trader.is_running`` ever becomes True. ``False``
            is the "connected, but the trader never started" shape — a
            reconciliation failure or a portfolio-init timeout — which
            ``kernel.start_async()`` produces by returning early *without*
            completing the run task. Story 2.5's ``node:connect`` waits on this
            rather than on ``check_connected()``, which turns True partway
            through the same coroutine.
        trader_starts_after: How many ``trader.is_running`` reads precede a
            ``True``.
    """

    def __init__(
        self,
        *,
        actors: Sequence[object] = (),
        instrument_ids: Sequence[str] = (),
        connects_after: int = 0,
        raise_on_build: BaseException | None = None,
        raise_on_dispose: BaseException | None = None,
        raise_on_stop: BaseException | None = None,
        run_forever: bool = True,
        build_delay_seconds: float = 0.0,
        run_seconds: float | None = None,
        trader_starts: bool = True,
        trader_starts_after: int = 0,
    ) -> None:
        self.kernel = _TestKernel(connects_after)
        self.trader = _TestTrader(
            actors, starts_running=trader_starts, starts_after=trader_starts_after
        )
        self.cache = _TestCache(instrument_ids)
        self._raise_on_build = raise_on_build
        self._raise_on_dispose = raise_on_dispose
        self._raise_on_stop = raise_on_stop
        self._run_forever = run_forever
        self._build_delay_seconds = build_delay_seconds
        self._run_seconds = run_seconds
        self._running = False
        self.built = False
        self.stopped = False
        self.disposed = False

    def build(self) -> None:
        self.built = True
        if self._build_delay_seconds:
            time.sleep(self._build_delay_seconds)
        if self._raise_on_build is not None:
            raise self._raise_on_build

    async def run_async(self) -> None:
        self._running = True
        try:
            if not self._run_forever:
                return
            if self._run_seconds is not None:
                await asyncio.sleep(self._run_seconds)
                return
            await asyncio.Event().wait()
        finally:
            self._running = False

    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self.stopped = True
        if self._raise_on_stop is not None:
            raise self._raise_on_stop

    def dispose(self) -> None:
        self.disposed = True
        if self._raise_on_dispose is not None:
            raise self._raise_on_dispose
