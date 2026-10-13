"""Component tests for the LiveBarObserver actor (Story 1.5).

Component tier: registers the actor against Nautilus test doubles — no broker,
no network, no database (NFR32/NFR34). Verified not to initialise the Nautilus C
logging subsystem, which is what keeps this file in the parallel, non-forked
component suite; the autouse fixture below re-checks that every test.
"""

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_bar_observer import (
    LiveBarObserver,
    LiveBarObserverConfig,
    build_bar_observer_config,
    evaluate_bar_freshness,
)
from src.core.live_market_data import LiveMarketDataError

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
MSFT_1MIN = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Mirrors tests/component/core/test_live_node_builder.py. The assertion is on
    the *delta*, not the absolute state: under ``-n auto`` this file shares a
    worker process with the rest of the component tier, and something else in
    that tier does initialise C logging. What this file must never do is
    *change* the state.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — registering an actor against test "
        "doubles must not touch the C logging subsystem, and the non-forked, parallel "
        "component tier cannot host anything that does."
    )


@pytest.fixture(autouse=True)
def _isolate_market_data_env(monkeypatch):
    """Keep the shell out of the settings fields this file builds from.

    ``_env_file=None`` disables the dotenv file but not ``os.environ``, so an
    exported ``IBKR_RATE_LIMIT`` would otherwise silently change what
    ``build_bar_observer_config`` produces. Both casings are cleared;
    ``case_sensitive: False`` makes them equal aliases.
    """
    for name in ("IBKR_RATE_LIMIT", "IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(**overrides) -> IBKRSettings:
    """Build settings without letting the developer's shell become test input."""
    return IBKRSettings(_env_file=None, **overrides)


def _make_observer(bar_types, *, requests_per_second=45, grace_seconds=120.0):
    """Register an observer against Nautilus test doubles.

    No broker, no network, no database (NFR32/NFR34). Verified not to initialise
    the Nautilus C logging subsystem, which is what keeps this file in the
    parallel component tier — the autouse fixture above re-checks it every test.
    """
    clock = TestClock()
    msgbus = MessageBus(trader_id=TraderId("TESTER-000"), clock=clock)
    cache = Cache(database=None)
    portfolio = Portfolio(msgbus, cache, clock)

    observer = LiveBarObserver(
        LiveBarObserverConfig(
            bar_types=tuple(bar_types),
            requests_per_second=requests_per_second,
            delayed_data_grace_seconds=grace_seconds,
        )
    )
    observer.register_base(portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock)
    return observer, clock, msgbus


def _subscribed_bar_topics(msgbus) -> set[str]:
    """Bar topics the observer itself is subscribed to (Portfolio has its own)."""
    return {
        subscription.topic
        for subscription in msgbus.subscriptions()
        if subscription.topic.startswith("data.bars.")
        and "LiveBarObserver" in repr(subscription.handler)
    }


def _bar(bar_type_str: str, *, ts_event_ns: int, ts_init_ns: int) -> Bar:
    """Build a Bar carrying the two timestamps the freshness guard reads."""
    return Bar(
        bar_type=BarType.from_str(bar_type_str),
        open=Price.from_str("100.00"),
        high=Price.from_str("101.00"),
        low=Price.from_str("99.00"),
        close=Price.from_str("100.50"),
        volume=Quantity.from_int(1_000),
        ts_event=ts_event_ns,
        ts_init=ts_init_ns,
    )


class TestObserverSubscription:
    """AC #3 — the observer subscribes to what it was configured with."""

    @pytest.mark.component
    def test_start_subscribes_to_every_configured_bar_type(self):
        # Arrange
        observer, _clock, msgbus = _make_observer([AAPL_1MIN, MSFT_1MIN])

        # Act
        observer.start()

        # Assert
        assert _subscribed_bar_topics(msgbus) == {
            f"data.bars.{AAPL_1MIN}",
            f"data.bars.{MSFT_1MIN}",
        }

    @pytest.mark.component
    def test_stop_unsubscribes_and_is_idempotent(self):
        # Arrange
        observer, _clock, msgbus = _make_observer([AAPL_1MIN])
        observer.start()

        # Act
        observer.stop()
        observer.on_stop()  # a second call must not raise

        # Assert
        assert _subscribed_bar_topics(msgbus) == set()

    @pytest.mark.component
    def test_stopping_mid_pacing_only_unsubscribes_what_was_dispatched(self):
        """A stop before pacing finishes must not cancel streams never opened."""
        # Arrange — 4 subscriptions at 1/s, so 3 are still queued after start
        bar_types = [f"SYM{index}.NASDAQ-1-MINUTE-LAST-EXTERNAL" for index in range(4)]
        observer, _clock, msgbus = _make_observer(bar_types, requests_per_second=1)
        observer.start()
        assert len(_subscribed_bar_topics(msgbus)) == 1

        # Act
        observer.stop()

        # Assert — the one open stream is closed, and nothing is left subscribed
        assert _subscribed_bar_topics(msgbus) == set()
        assert observer._subscribed == []

    @pytest.mark.component
    def test_invalid_bar_type_is_refused_at_construction_not_at_subscribe(self):
        """Fail before the actor is ever attached to a node, not mid-session."""
        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            LiveBarObserver(
                LiveBarObserverConfig(bar_types=("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",))
            )


class TestObserverPacing:
    """AC #5 — the 45 req/s discipline governs live subscription traffic."""

    @pytest.mark.component
    def test_a_set_within_the_rate_is_dispatched_in_one_batch(self):
        # Arrange
        observer, clock, msgbus = _make_observer([AAPL_1MIN, MSFT_1MIN], requests_per_second=45)

        # Act
        observer.start()

        # Assert
        assert len(_subscribed_bar_topics(msgbus)) == 2
        assert clock.timer_names == []  # nothing left to pace

    @pytest.mark.component
    def test_an_oversized_set_is_dispatched_one_batch_per_second(self):
        """A 100-line budget against 45 req/s is three batches — not hypothetical."""
        # Arrange
        bar_types = [f"SYM{index}.NASDAQ-1-MINUTE-LAST-EXTERNAL" for index in range(5)]
        observer, clock, msgbus = _make_observer(bar_types, requests_per_second=2)

        # Act / Assert — first batch immediately, remainder scheduled
        observer.start()
        assert len(_subscribed_bar_topics(msgbus)) == 2
        assert len(clock.timer_names) == 1

        # Act / Assert — one second later, the next two
        for event in clock.advance_time(clock.timestamp_ns() + 1_000_000_000):
            event.handle()
        assert len(_subscribed_bar_topics(msgbus)) == 4
        assert len(clock.timer_names) == 1

        # Act / Assert — the tail, and pacing stops
        for event in clock.advance_time(clock.timestamp_ns() + 1_000_000_000):
            event.handle()
        assert len(_subscribed_bar_topics(msgbus)) == 5
        assert clock.timer_names == []

    @pytest.mark.component
    def test_pacing_survives_a_clock_that_still_lists_the_fired_timer(self):
        """The production ordering ``TestClock.advance_time()`` hides.

        ``LiveClock`` removes a fired one-shot timer only *after* its callback
        returns, so a scheduler that reuses one timer name re-registers a name
        the clock still holds and raises ``KeyError: 'name' … already contained
        in 'self.timer_names'`` — which Nautilus's timer machinery swallows.
        Batches 1 and 2 dispatch, everything after is stranded, and nothing is
        logged. ``TestClock.advance_time()`` pops the timer *before* invoking the
        callback, so the ordinary pacing test above passes either way.

        This test recreates the live ordering directly: invoke the callback while
        the previous timer is still registered, and require the next batch to
        arm anyway.
        """
        # Arrange — 6 subscriptions at 2/s is three batches, the shape the class
        # docstring calls "not hypothetical" for a 100-line budget at 45/s.
        bar_types = [f"SYM{index}.NASDAQ-1-MINUTE-LAST-EXTERNAL" for index in range(6)]
        observer, clock, msgbus = _make_observer(bar_types, requests_per_second=2)
        observer.start()
        assert len(_subscribed_bar_topics(msgbus)) == 2

        # Act — drive batches 2 and 3 without ever letting the clock forget the
        # timer it just fired, exactly as LiveClock behaves mid-callback. The
        # stale names accumulate here only because nothing in this simulation
        # fires them; LiveClock drops each one as its callback returns.
        armed = [observer._pacer.timer_name]
        for _ in range(2):
            observer._dispatch_batch(None)
            armed.append(observer._pacer.timer_name)

        # Assert — every subscription dispatched, nothing stranded, and each
        # batch was armed under a name the clock was not already holding.
        assert len(_subscribed_bar_topics(msgbus)) == 6
        assert observer._pacer.timer_name is None, "pacing should be finished"
        # Three batches need two armed timers — batch 1 dispatches immediately —
        # and the two names must differ, which is the whole point.
        scheduled = [name for name in armed if name is not None]
        assert len(scheduled) == len(set(scheduled)) == 2

    @pytest.mark.component
    def test_stop_cancels_an_outstanding_pacing_timer(self):
        """A stopped session must not keep opening subscriptions behind itself."""
        # Arrange
        bar_types = [f"SYM{index}.NASDAQ-1-MINUTE-LAST-EXTERNAL" for index in range(4)]
        observer, clock, _msgbus = _make_observer(bar_types, requests_per_second=1)
        observer.start()
        assert len(clock.timer_names) == 1

        # Act
        observer.stop()

        # Assert
        assert clock.timer_names == []

    @pytest.mark.component
    def test_rate_is_sourced_from_settings_not_a_second_literal(self):
        # Arrange
        settings = _settings(ibkr_rate_limit=7)

        # Act
        config = build_bar_observer_config(settings, [AAPL_1MIN])

        # Assert
        assert config.requests_per_second == 7
        assert config.bar_types == (AAPL_1MIN,)

    @pytest.mark.component
    def test_the_default_rate_is_the_existing_forty_five(self):
        """NFR15's "existing discipline" — the same 45 RateLimiter applies."""
        # Act
        config = build_bar_observer_config(_settings(), [AAPL_1MIN])

        # Assert
        assert config.requests_per_second == 45

    @pytest.mark.component
    @pytest.mark.parametrize("rate", [0, -5])
    def test_a_non_positive_rate_is_refused(self, rate):
        """Zero requests per second would dispatch nothing, forever."""
        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            build_bar_observer_config(_settings(ibkr_rate_limit=rate), [AAPL_1MIN])

    @pytest.mark.component
    @pytest.mark.parametrize("rate", [0, -9])
    def test_the_actor_refuses_a_non_positive_rate_rather_than_repairing_it(self, rate):
        """The kernel rebuilds this config from a serialised dict, bypassing the factory.

        Silently repairing 0 to 1 would turn a corrupt config into a 100-second
        subscription ramp instead of an error.
        """
        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            LiveBarObserver(LiveBarObserverConfig(bar_types=(AAPL_1MIN,), requests_per_second=rate))

    @pytest.mark.component
    @pytest.mark.parametrize(
        "grace",
        [
            pytest.param(-1000.0, id="negative"),
            pytest.param(float("inf"), id="inf"),
            pytest.param(float("nan"), id="nan"),
        ],
    )
    def test_an_unusable_grace_is_refused(self, grace):
        """A negative grace stops the session on its very first, perfectly fresh bar."""
        # Act / Assert — refused by the factory...
        with pytest.raises(LiveMarketDataError):
            build_bar_observer_config(_settings(), [AAPL_1MIN], delayed_data_grace_seconds=grace)

        # ...and by the actor, which the kernel builds from the serialised dict
        with pytest.raises(LiveMarketDataError):
            LiveBarObserver(
                LiveBarObserverConfig(bar_types=(AAPL_1MIN,), delayed_data_grace_seconds=grace)
            )

    @pytest.mark.component
    def test_the_factory_validates_bar_types_before_a_socket_can_open(self):
        """The actor is only constructed at kernel build; this layer runs first."""
        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            build_bar_observer_config(_settings(), ["AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"])


class TestObserverBarDelivery:
    """AC #3 — a closed bar reaches the node and is logged with instrument and time."""

    @pytest.mark.component
    def test_a_delivered_bar_is_counted(self):
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()

        # Act
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=60_000_000_000))

        # Assert
        assert observer.total_received == 1
        assert observer.received_count(AAPL_1MIN) == 1
        assert observer.counts_by_bar_type() == {AAPL_1MIN: 1}

    @pytest.mark.component
    def test_counts_are_kept_per_subscription(self):
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN, MSFT_1MIN])
        observer.start()

        # Act
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=60_000_000_000))
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=120_000_000_000, ts_init_ns=120_000_000_000))
        observer.on_bar(_bar(MSFT_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=60_000_000_000))

        # Assert
        assert observer.received_count(AAPL_1MIN) == 2
        assert observer.received_count(MSFT_1MIN) == 1
        assert observer.total_received == 3

    @pytest.mark.component
    def test_the_log_line_carries_the_instrument_and_the_bar_timestamp(self):
        """AC #3's "logged with its instrument and timestamp", asserted literally."""
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()
        bar = _bar(
            AAPL_1MIN,
            ts_event_ns=1_700_000_000_000_000_000,
            ts_init_ns=1_700_000_001_000_000_000,
        )

        # Act
        with capture_logs() as captured:
            observer.on_bar(bar)

        # Assert
        received = [entry for entry in captured if entry["event"] == "live_bars.received"]
        assert len(received) == 1
        assert received[0]["instrument_id"] == "AAPL.NASDAQ"
        assert received[0]["bar_type"] == AAPL_1MIN
        assert received[0]["ts_event"] == "2023-11-14T22:13:20.000000000Z"

    @pytest.mark.component
    def test_a_bar_delivered_twice_is_counted_once(self):
        """The adapter publishes a completed bar twice and nothing de-duplicates.

        `_schedule_bar_completion_timeout` publishes it once
        (`client/market_data.py:1075-1098`), then `_process_bar_data` publishes
        the same bar again as `previous_bar` when the next one arrives
        (`:1162-1166`). Counting both would inflate the very evidence this actor
        exists to provide.
        """
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()
        bar = _bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=121_000_000_000)

        # Act — same bar, delivered twice with a later ts_init the second time
        observer.on_bar(bar)
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=125_000_000_000))

        # Assert
        assert observer.total_received == 1
        assert observer.total_republished == 1

    @pytest.mark.component
    def test_republishes_are_tracked_per_bar_type_and_do_not_block_new_bars(self):
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()

        # Act — bar 1, bar 1 again, bar 2
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=121_000_000_000))
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=125_000_000_000))
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=120_000_000_000, ts_init_ns=181_000_000_000))

        # Assert
        assert observer.received_count(AAPL_1MIN) == 2
        assert observer.total_republished == 1

    @pytest.mark.component
    def test_reset_clears_observation_state(self):
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=60_000_000_000, ts_init_ns=60_000_000_000))
        observer.stop()

        # Act
        observer.reset()

        # Assert
        assert observer.total_received == 0
        assert observer.delayed_data_suspected is False


class TestObserverDelayedDataGuard:
    """AC #1's runtime half — a delayed feed fails loudly, it does not pass quietly."""

    @staticmethod
    def _spy_shutdown(monkeypatch, observer):
        calls: list[str] = []
        monkeypatch.setattr(observer, "shutdown_system", lambda reason=None: calls.append(reason))
        return calls

    @pytest.mark.component
    def test_a_fresh_bar_does_not_trip_the_guard(self, monkeypatch):
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — published 2s after its close
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=600_000_000_000, ts_init_ns=602_000_000_000))

        # Assert
        assert observer.delayed_data_suspected is False
        assert calls == []

    @pytest.mark.component
    def test_a_bar_at_the_adapters_honest_worst_case_does_not_trip_the_guard(self, monkeypatch):
        """One bar interval + 1s is the completion-timeout fallback, not a downgrade."""
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — 61s lag on a 1-minute bar, inside 60s + 120s grace
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=600_000_000_000, ts_init_ns=661_000_000_000))

        # Assert
        assert observer.delayed_data_suspected is False
        assert calls == []

    @staticmethod
    def _delayed_bar(index: int) -> Bar:
        """A 1-minute bar on a ~15-minute-delayed feed.

        ``ts_event`` is the bar's OPEN, so a healthy bar arrives at about
        ``open + 60s``. A delayed one arrives at ``open + 60s + 900s``.
        """
        open_ns = 600_000_000_000 + index * 60_000_000_000
        return _bar(
            AAPL_1MIN, ts_event_ns=open_ns, ts_init_ns=open_ns + 60_000_000_000 + 900_000_000_000
        )

    @pytest.mark.component
    def test_a_fifteen_minute_delay_sustained_fails_loudly(self, monkeypatch):
        """IBKR's delayed feed runs ~15 minutes behind — the signature this catches."""
        # Arrange — the default is 3 consecutive offenders
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act
        with capture_logs() as captured:
            for index in range(3):
                observer.on_bar(self._delayed_bar(index))

        # Assert
        assert observer.delayed_data_suspected is True
        assert len(calls) == 1
        assert "Delayed market data suspected" in calls[0]

        errors = [
            entry for entry in captured if entry["event"] == "live_bars.delayed_data_suspected"
        ]
        assert len(errors) == 1
        assert errors[0]["log_level"] == "error"
        # The interval is taken out: what is reported is delay past the close.
        assert errors[0]["delay_past_close_seconds"] == pytest.approx(900.0)
        assert errors[0]["tolerance_seconds"] == pytest.approx(120.0)
        assert errors[0]["consecutive"] == 3

    @pytest.mark.component
    def test_a_single_stale_bar_does_not_stop_the_session(self, monkeypatch):
        """The regression that matters most: an RTH re-open is not a downgrade.

        The IB adapter keeps ``_bar_type_to_last_bar`` for the life of a
        subscription and publishes the *previous* bar when a new one arrives,
        stamped with the wall clock at that moment
        (``client/market_data.py:1141, 1160, 1162-1166``). With ``use_rth=True``
        nothing closes between 16:00 and 09:30, so the first bar of the next
        session republishes yesterday's last bar with today's ``ts_init`` — a
        ~17.5-hour apparent delay on perfectly healthy real-time data. Shutting
        down on the first offender would kill a multi-day paper session every
        morning, which is precisely what this phase exists to run.
        """
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Arrange — yesterday's last bar was already delivered on its own close
        last_bar_open_ns = 600_000_000_000
        observer.on_bar(
            _bar(
                AAPL_1MIN,
                ts_event_ns=last_bar_open_ns,
                ts_init_ns=last_bar_open_ns + 61_000_000_000,
            )
        )

        # Act — next morning the adapter republishes it with today's wall clock,
        # then healthy bars resume
        overnight_ns = 17 * 3600 * 1_000_000_000
        observer.on_bar(
            _bar(
                AAPL_1MIN,
                ts_event_ns=last_bar_open_ns,
                ts_init_ns=last_bar_open_ns + overnight_ns,
            )
        )
        for index in range(1, 4):
            open_ns = last_bar_open_ns + overnight_ns + index * 60_000_000_000
            observer.on_bar(
                _bar(AAPL_1MIN, ts_event_ns=open_ns, ts_init_ns=open_ns + 61_000_000_000)
            )

        # Assert — the republish was discarded, not judged, and the session lives
        assert observer.delayed_data_suspected is False
        assert calls == []
        assert observer.total_received == 4
        assert observer.total_republished == 1

    @pytest.mark.component
    def test_a_healthy_bar_resets_the_consecutive_count(self, monkeypatch):
        """Two late bars either side of a good one are not three in a row."""
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — late, late, healthy, late, late
        observer.on_bar(self._delayed_bar(0))
        observer.on_bar(self._delayed_bar(1))
        healthy_open = 600_000_000_000 + 2 * 60_000_000_000
        observer.on_bar(
            _bar(AAPL_1MIN, ts_event_ns=healthy_open, ts_init_ns=healthy_open + 61_000_000_000)
        )
        observer.on_bar(self._delayed_bar(3))
        observer.on_bar(self._delayed_bar(4))

        # Assert
        assert observer.delayed_data_suspected is False
        assert calls == []

    @pytest.mark.component
    def test_the_real_shutdown_command_reaches_the_message_bus(self):
        """Every other test in this class stubs ``shutdown_system``.

        That leaves the load-bearing safety action asserted against a lambda. This
        one lets the real ``Component.shutdown_system`` run and checks a
        ``ShutdownSystem`` command actually reaches ``commands.system.shutdown``
        carrying this trader's id — the kernel drops it otherwise
        (``system/kernel.py`` compares ``command.trader_id``).
        """
        # Arrange
        observer, _clock, msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        received: list[object] = []
        msgbus.subscribe(topic="commands.system.shutdown", handler=received.append)

        # Act — no stub: the real shutdown_system runs
        for index in range(3):
            observer.on_bar(self._delayed_bar(index))

        # Assert
        assert observer.delayed_data_suspected is True
        assert len(received) == 1
        command = received[0]
        assert str(command.trader_id) == "TESTER-000"
        assert "Delayed market data suspected" in command.reason

    @pytest.mark.component
    def test_the_guard_works_on_hourly_bars_too(self, monkeypatch):
        """The interval cancels, so detection does not degrade with bar size.

        An earlier version of this module claimed a blind spot above ~13-minute
        intervals. That came from leaving the interval in the comparison; taking
        it out makes the test interval-independent.
        """
        # Arrange
        hourly = "AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL"
        observer, _clock, _msgbus = _make_observer([hourly], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — three hourly bars, each 900s late past its close
        for index in range(3):
            open_ns = 3_600_000_000_000 * (index + 1)
            observer.on_bar(
                _bar(
                    hourly,
                    ts_event_ns=open_ns,
                    ts_init_ns=open_ns + 3_600_000_000_000 + 900_000_000_000,
                )
            )

        # Assert
        assert observer.delayed_data_suspected is True
        assert len(calls) == 1

    @pytest.mark.component
    def test_the_guard_latches_so_one_downgrade_is_one_shutdown(self, monkeypatch):
        """Otherwise a delayed feed emits a shutdown command per bar, forever."""
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — six offending bars, well past the three needed to declare
        for index in range(6):
            observer.on_bar(self._delayed_bar(index))

        # Assert — every bar still counted, exactly one shutdown
        assert observer.total_received == 6
        assert len(calls) == 1

    @pytest.mark.component
    def test_a_clock_skewed_bar_is_counted_and_does_not_trip_the_guard(self, monkeypatch):
        """ts_init before ts_event is nonsense, not a downgrade — count it, move on."""
        # Arrange
        observer, _clock, _msgbus = _make_observer([AAPL_1MIN])
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=600_000_000_000, ts_init_ns=599_000_000_000))

        # Assert
        assert observer.received_count(AAPL_1MIN) == 1
        assert observer.delayed_data_suspected is False
        assert calls == []

    @pytest.mark.component
    def test_a_healthy_five_minute_bar_is_not_mistaken_for_a_late_one(self, monkeypatch):
        """The interval is subtracted, so a bigger bar is not automatically 'late'."""
        # Arrange
        five_min = "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        observer, _clock, _msgbus = _make_observer([five_min], grace_seconds=120.0)
        observer.start()
        calls = self._spy_shutdown(monkeypatch, observer)

        # Act — arrives 2s after its close: lag 302s, of which 300s is the interval
        for index in range(4):
            open_ns = 600_000_000_000 + index * 300_000_000_000
            observer.on_bar(
                _bar(five_min, ts_event_ns=open_ns, ts_init_ns=open_ns + 302_000_000_000)
            )

        # Assert
        assert observer.delayed_data_suspected is False
        assert calls == []


class TestBarFreshnessBoundary:
    """The lag test itself, pinned at the boundary rather than only side-on."""

    GRACE_NS = 120 * 1_000_000_000
    INTERVAL_NS = 60 * 1_000_000_000
    # ts_event is the bar's OPEN, so a bar sitting exactly on the tolerance
    # arrives one interval (open -> close) plus the grace after ts_event.
    THRESHOLD_NS = INTERVAL_NS + GRACE_NS

    @pytest.mark.component
    def test_a_delay_exactly_at_the_tolerance_is_accepted(self):
        """The comparison is <=, so the tolerance itself is the last good value."""
        # Arrange
        bar = _bar(AAPL_1MIN, ts_event_ns=0, ts_init_ns=self.THRESHOLD_NS)

        # Act / Assert
        assert evaluate_bar_freshness(bar, grace_ns=self.GRACE_NS) is None

    @pytest.mark.component
    def test_a_delay_one_nanosecond_over_the_tolerance_is_reported(self):
        # Arrange
        bar = _bar(AAPL_1MIN, ts_event_ns=0, ts_init_ns=self.THRESHOLD_NS + 1)

        # Act
        verdict = evaluate_bar_freshness(bar, grace_ns=self.GRACE_NS)

        # Assert
        assert verdict is not None
        assert verdict.lag_ns == self.THRESHOLD_NS + 1
        # The reported number takes the interval out — it is delay past the close.
        assert verdict.delay_past_close_ns == self.GRACE_NS + 1
        assert verdict.threshold_ns == self.GRACE_NS
        assert "Delayed market data suspected" in verdict.reason

    @pytest.mark.component
    def test_a_negative_lag_is_clamped_rather_than_reported(self):
        """ts_init before ts_event is clock nonsense, not a 'very fresh' bar."""
        # Arrange
        bar = _bar(AAPL_1MIN, ts_event_ns=600_000_000_000, ts_init_ns=599_000_000_000)

        # Act / Assert
        assert evaluate_bar_freshness(bar, grace_ns=self.GRACE_NS) is None

    @pytest.mark.component
    def test_zero_grace_still_tolerates_one_bar_interval(self):
        """The interval is the open-to-close conversion, not slack to be spent."""
        # Arrange — a bar arriving exactly at its close, no grace at all
        bar = _bar(AAPL_1MIN, ts_event_ns=0, ts_init_ns=self.INTERVAL_NS)

        # Act / Assert
        assert evaluate_bar_freshness(bar, grace_ns=0) is None

    @pytest.mark.component
    def test_the_tolerance_is_the_same_number_for_every_bar_interval(self):
        """Interval-independence, asserted directly rather than inferred.

        A delayed feed is ~900s behind whatever the bar size, and the interval
        cancels out of the comparison, so the same 901s delay must be reported
        identically for a 1-minute and a 1-hour bar.
        """
        # Arrange
        cases = {
            AAPL_1MIN: 60 * 1_000_000_000,
            "AAPL.NASDAQ-1-HOUR-LAST-EXTERNAL": 3_600 * 1_000_000_000,
        }

        # Act / Assert
        for bar_type_str, interval_ns in cases.items():
            bar = _bar(
                bar_type_str,
                ts_event_ns=0,
                ts_init_ns=interval_ns + 901 * 1_000_000_000,
            )
            verdict = evaluate_bar_freshness(bar, grace_ns=self.GRACE_NS)
            assert verdict is not None, bar_type_str
            assert verdict.delay_past_close_ns == 901 * 1_000_000_000, bar_type_str
