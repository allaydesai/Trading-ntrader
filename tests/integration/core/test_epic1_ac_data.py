"""Epic-1 acceptance conformance: real-time RTH bars for a configured instrument.

One test per acceptance criterion in Story 1.5 of
``_bmad-output/planning-artifacts/prd-epic1-scope.md``. See
``test_epic1_ac_gate.py`` for why this suite sits alongside the tier suites
rather than replacing them.

Nothing here contacts a broker (NFR32/NFR34): the market-data policy is pure, the
observer is registered against Nautilus's own test doubles, and the one
end-to-end path runs the driver against a node double.
"""

import pytest
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core import live_node_builder
from src.core.live_bar_observer import (
    DEFAULT_DELAYED_DATA_CONSECUTIVE_BARS,
    LiveBarObserver,
    LiveBarObserverConfig,
    build_bar_observer_config,
)
from src.core.live_check import LiveCheckOutcome
from src.core.live_check_driver import run_live_check
from src.core.live_gate import GateDecision, GateMode
from src.core.live_market_data import (
    LiveMarketDataError,
    resolve_live_market_data_type,
    resolve_live_use_rth,
)
from src.core.live_node_builder import build_trading_node_config
from tests.component.doubles import TestBarObserver, TestLiveNode
from tests.integration.core.epic1_criteria import criterion

pytestmark = pytest.mark.integration

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
MSFT_1MIN = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"
TSLA_1MIN = "TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL"
TRADER_ID = "PAPER-a1b2c3d4"
PAPER_ACCOUNT = "DU4076626"

ONE_MINUTE_NS = 60_000_000_000


@pytest.fixture(autouse=True)
def _keep_the_shell_out_of_the_settings(monkeypatch):
    """``resolve_live_market_data_type`` branches on ``model_fields_set``.

    An exported ``IBKR_MARKET_DATA_TYPE`` would therefore change which branch
    these tests take — ``_env_file=None`` disables the dotenv file but not
    ``os.environ``, and ``case_sensitive: False`` makes both casings aliases.
    """
    for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH", "IBKR_RATE_LIMIT"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(**overrides) -> IBKRSettings:
    fields = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_client_id": 1,
        "ibkr_live_client_id": 10,
        "ibkr_trading_mode": "paper",
        "tws_account": PAPER_ACCOUNT,
        "ntrader_real_money_account": "",
        "ibkr_connection_timeout": 300,
        "ibkr_request_timeout": 60,
        "ibkr_market_data_lines": 100,
        "ibkr_rate_limit": 45,
    }
    fields.update(overrides)
    return IBKRSettings(_env_file=None, **fields)


def _registered_observer(bar_types, **config_overrides):
    """An observer wired to Nautilus's own doubles — no broker, no C logging."""
    clock = TestClock()
    msgbus = MessageBus(trader_id=TraderId("TESTER-000"), clock=clock)
    cache = Cache(database=None)
    portfolio = Portfolio(msgbus, cache, clock)

    observer = LiveBarObserver(
        LiveBarObserverConfig(bar_types=tuple(bar_types), **config_overrides)
    )
    observer.register_base(portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock)
    return observer


def _bar(bar_type: str, *, ts_event_ns: int, ts_init_ns: int) -> Bar:
    return Bar(
        bar_type=BarType.from_str(bar_type),
        open=Price.from_str("100.00"),
        high=Price.from_str("101.00"),
        low=Price.from_str("99.00"),
        close=Price.from_str("100.50"),
        volume=Quantity.from_int(1_000),
        ts_event=ts_event_ns,
        ts_init=ts_init_ns,
    )


async def _permits(node, settings, **kwargs) -> GateDecision:
    """An account verifier that permits — Layer 2 has its own acceptance tests."""
    return GateDecision(permitted=True, mode=GateMode.PAPER)


@criterion("1.5a")
def test_the_session_runs_on_realtime_overriding_the_fetch_default():
    """ "...the market data type is REALTIME, explicitly overriding the
    DELAYED_FROZEN default that exists for paper data fetching (FR3, AR21)."
    """
    settings = _settings()

    # The default really is the wrong one for a session — that is the whole
    # point of the override, so it is asserted rather than assumed.
    assert settings.get_market_data_type_enum() is MarketDataTypeEnum.DELAYED_FROZEN
    assert resolve_live_market_data_type(settings) is MarketDataTypeEnum.REALTIME

    config = build_trading_node_config(settings, trader_id=TRADER_ID, bar_types=[AAPL_1MIN])
    assert config.data_clients[IB].market_data_type is MarketDataTypeEnum.REALTIME

    # An operator who asked for real-time in any casing gets it, not a refusal.
    for spelling in ("REALTIME", "realtime", " Realtime "):
        explicit = _settings(ibkr_market_data_type=spelling)
        assert resolve_live_market_data_type(explicit) is MarketDataTypeEnum.REALTIME


@criterion("1.5b")
def test_a_feed_that_is_not_real_time_fails_loudly_rather_than_falling_back():
    """ "...a session that cannot obtain real-time data fails loudly rather than
    silently falling back to delayed."

    Both halves of "cannot obtain": the operator configured delayed data, and the
    broker downgraded the feed mid-session without telling anyone (IB reports
    error 10167 as a log warning and nothing else).
    """
    for configured in ("DELAYED", "DELAYED_FROZEN", "FROZEN", "not-a-market-data-type"):
        with pytest.raises(LiveMarketDataError, match="REALTIME"):
            build_trading_node_config(
                _settings(ibkr_market_data_type=configured),
                trader_id=TRADER_ID,
                bar_types=[AAPL_1MIN],
            )

    # A broker-side downgrade: bars keep arriving, each ~15 minutes past its
    # close. The observer latches after the required run and stops the session.
    observer = _registered_observer([AAPL_1MIN], delayed_data_grace_seconds=120.0)
    for index in range(DEFAULT_DELAYED_DATA_CONSECUTIVE_BARS):
        ts_event = (index + 1) * ONE_MINUTE_NS
        observer.on_bar(
            _bar(AAPL_1MIN, ts_event_ns=ts_event, ts_init_ns=ts_event + 900 * ONE_MINUTE_NS // 60)
        )
    assert observer.delayed_data_suspected is True

    # ...and the session it stops reports the diagnosis rather than "ok".
    report = run_live_check(
        _settings(),
        bar_types=[AAPL_1MIN],
        observe_seconds=0.0,
        connect_timeout=2.0,
        node_factory=lambda *a, **k: TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN], delayed_data=True)],
            instrument_ids=["AAPL.NASDAQ"],
        ),
        account_verifier=_permits,
    )
    assert report.outcome is not LiveCheckOutcome.OK
    assert report.exit_code != 0
    assert report.delayed_data_suspected is True
    assert "delayed" in report.message.lower()


@criterion("1.5c")
def test_bars_are_restricted_to_regular_trading_hours():
    """ "...they are restricted to regular trading hours via ibkr_use_rth=True
    (FR4, AR21)."
    """
    config = build_trading_node_config(_settings(), trader_id=TRADER_ID, bar_types=[AAPL_1MIN])

    # Passed explicitly rather than left to the adapter's default: a data-integrity
    # property resting on a third-party default is one upgrade from changing.
    assert config.data_clients[IB].use_regular_trading_hours is True
    assert resolve_live_use_rth(_settings()) is True

    with pytest.raises(LiveMarketDataError, match="IBKR_USE_RTH"):
        build_trading_node_config(
            _settings(ibkr_use_rth=False), trader_id=TRADER_ID, bar_types=[AAPL_1MIN]
        )


@criterion("1.5d")
def test_a_closed_bar_is_delivered_and_logged_with_its_instrument_and_timestamp():
    """ "...the bar is delivered to the node and logged with its instrument and
    timestamp (FR1)."
    """
    observer = _registered_observer([AAPL_1MIN, MSFT_1MIN])

    with capture_logs() as records:
        observer.on_bar(_bar(AAPL_1MIN, ts_event_ns=ONE_MINUTE_NS, ts_init_ns=2 * ONE_MINUTE_NS))

    assert observer.received_count(AAPL_1MIN) == 1
    assert observer.received_count(MSFT_1MIN) == 0, "counts are kept per subscription"
    assert observer.total_received == 1

    received = [record for record in records if record.get("event") == "live_bars.received"]
    assert len(received) == 1
    assert received[0]["instrument_id"] == "AAPL.NASDAQ"
    assert received[0]["bar_type"] == AAPL_1MIN
    assert received[0]["ts_event"].startswith("1970-01-01T00:01:00")


@criterion("1.5e")
def test_more_subscriptions_than_market_data_lines_fails_at_startup(monkeypatch):
    """ "...it fails at startup with a message naming the limit and the requested
    count, rather than silently dropping subscriptions (NFR16, NFR30)."

    "At startup" is load-bearing and is asserted structurally: the client configs
    are landmines, so the failure has to happen before either exists — IBKR drops
    the excess without reporting it, so a session that got as far as connecting
    would look healthy and simply never deliver those bars.
    """

    def _landmine(*args, **kwargs):
        raise AssertionError("a client config was constructed before the budget check")

    monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _landmine)
    monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _landmine)

    with pytest.raises(LiveMarketDataError) as refused:
        build_trading_node_config(
            _settings(ibkr_market_data_lines=2),
            trader_id=TRADER_ID,
            bar_types=[AAPL_1MIN, MSFT_1MIN, TSLA_1MIN],
        )

    message = str(refused.value)
    assert "3" in message and "2" in message, f"neither count was named: {message}"
    assert "IBKR_MARKET_DATA_LINES" in message, "the operator is not told which knob to turn"


@criterion("1.5f")
def test_the_existing_forty_five_per_second_pacing_governs_subscriptions():
    """ "...the existing 45 req/s pacing discipline governs them (NFR15)."

    One number, taken from one typed field: ``IBKRSettings.ibkr_rate_limit``, the
    same field the historical fetch client's rate limiter reads.
    """
    assert IBKRSettings.model_fields["ibkr_rate_limit"].default == 45
    assert LiveBarObserverConfig().requests_per_second == 45

    observer_config = build_bar_observer_config(_settings(), [AAPL_1MIN])
    assert observer_config.requests_per_second == 45

    # Sourced, not hard-coded: change the field and the dispatch rate follows.
    assert (
        build_bar_observer_config(_settings(ibkr_rate_limit=7), [AAPL_1MIN]).requests_per_second
        == 7
    )

    # And it survives the trip onto the node, where the kernel rebuilds the
    # actor from a serialised config dict.
    node_config = build_trading_node_config(
        _settings(), trader_id=TRADER_ID, bar_observer=observer_config
    )
    assert node_config.actors[0].config["requests_per_second"] == 45

    # A rate that would dispatch nothing is refused rather than repaired.
    with pytest.raises(LiveMarketDataError, match="IBKR_RATE_LIMIT"):
        build_bar_observer_config(_settings(ibkr_rate_limit=0), [AAPL_1MIN])
