"""Component tests for the live market-data policy (Story 1.5).

Component tier, not unit: this module imports ``nautilus_trader.model.data`` for
``BarType``, which the unit tier is defined to exclude (pytest.ini:25). Parsing a
bar type and resolving settings does not touch the Nautilus C logging subsystem —
machine-enforced by the autouse fixture below — so it is safe for the parallel,
non-forked component suite.
"""

import pytest
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.data import BarType

from src.config import IBKRSettings
from src.core.live_market_data import (
    LiveMarketDataError,
    instrument_ids_for,
    resolve_live_bar_types,
    resolve_live_market_data_type,
    resolve_live_use_rth,
    validate_market_data_line_budget,
)

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
        f"({before} -> {is_logging_initialized()}) — market-data policy resolution must not "
        "touch the C logging subsystem, and the non-forked, parallel component tier cannot "
        "host anything that does. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_market_data_env(monkeypatch):
    """Keep the shell out of the fields whose *set-ness* this module reads.

    ``_env_file=None`` disables only the dotenv **file** — ``os.environ`` stays an
    active pydantic-settings source. That matters more here than usual, because
    ``resolve_live_market_data_type`` branches on ``model_fields_set``: an
    exported ``IBKR_MARKET_DATA_TYPE=DELAYED_FROZEN`` marks the field *set* and
    turns every "unset, so override" test into a refusal. Verified — without this
    fixture, exporting that key fails four tests in this file and every
    ``market_data_type=None`` build in ``test_live_node_builder.py``.

    Both casings are cleared: ``case_sensitive: False`` makes them equal aliases.
    Tests that need a value present set it themselves *after* this runs.
    """
    for name in (
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IBKR_MARKET_DATA_LINES",
        "IBKR_RATE_LIMIT",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(**overrides) -> IBKRSettings:
    """Build settings without letting the developer's shell become test input.

    Init kwargs outrank the environment in pydantic-settings and ``_env_file=None``
    keeps ``.env`` out, but neither stops ``os.environ`` — the
    ``_isolate_market_data_env`` fixture above does that. Fields left out of
    ``overrides`` therefore stay genuinely *unset*, which is the distinction
    ``resolve_live_market_data_type`` reads.
    """
    return IBKRSettings(_env_file=None, **overrides)


class TestMarketDataTypeResolution:
    """AC #1 — REALTIME, and never a silent downgrade to delayed."""

    @pytest.mark.component
    def test_unset_market_data_type_is_overridden_to_realtime(self):
        """The DELAYED_FROZEN default exists for catalog fetching, not for a session."""
        # Arrange
        settings = _settings()

        # Assert the precondition this test is actually about
        assert settings.ibkr_market_data_type == "DELAYED_FROZEN"

        # Act
        resolved = resolve_live_market_data_type(settings)

        # Assert
        assert resolved == MarketDataTypeEnum.REALTIME

    @pytest.mark.component
    @pytest.mark.parametrize("value", ["REALTIME", "realtime", "  RealTime  "])
    def test_explicit_realtime_is_accepted_in_any_casing(self, value):
        # Arrange
        settings = _settings(ibkr_market_data_type=value)

        # Act / Assert
        assert resolve_live_market_data_type(settings) == MarketDataTypeEnum.REALTIME

    @pytest.mark.component
    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("DELAYED_FROZEN", id="explicit-delayed-frozen"),
            pytest.param("DELAYED", id="explicit-delayed"),
            pytest.param("FROZEN", id="explicit-frozen"),
        ],
    )
    def test_explicitly_configured_non_realtime_fails_loudly(self, value):
        """An operator who asked for delayed data on a live session gets a refusal."""
        # Arrange
        settings = _settings(ibkr_market_data_type=value)

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_market_data_type(settings)

        message = str(excinfo.value)
        assert value in message
        assert "REALTIME" in message

    @pytest.mark.component
    def test_unrecognised_value_fails_loudly_instead_of_coercing_to_delayed(self):
        """The trap this AC exists for.

        ``IBKRSettings.get_market_data_type_enum`` ends in
        ``.get(value, MarketDataTypeEnum.DELAYED_FROZEN)``, so a typo silently
        becomes delayed data. The live path must never reach that behaviour.
        """
        # Arrange
        settings = _settings(ibkr_market_data_type="REALTIEM")

        # Assert the trap is real and still present
        assert settings.get_market_data_type_enum() == MarketDataTypeEnum.DELAYED_FROZEN

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_market_data_type(settings)

        assert "REALTIEM" in str(excinfo.value)

    @pytest.mark.component
    def test_env_supplied_value_counts_as_explicit(self, monkeypatch):
        """`.env` and the environment mark the field set, exactly like an init kwarg."""
        # Arrange
        monkeypatch.setenv("IBKR_MARKET_DATA_TYPE", "DELAYED")
        settings = _settings()

        # Act / Assert
        with pytest.raises(LiveMarketDataError):
            resolve_live_market_data_type(settings)

    @pytest.mark.component
    def test_env_supplied_realtime_is_permitted(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("IBKR_MARKET_DATA_TYPE", "REALTIME")
        settings = _settings()

        # Act / Assert
        assert resolve_live_market_data_type(settings) == MarketDataTypeEnum.REALTIME


class TestUseRthResolution:
    """AC #2 — bars restricted to regular trading hours."""

    @pytest.mark.component
    def test_default_settings_resolve_to_rth_only(self):
        # Act / Assert
        assert resolve_live_use_rth(_settings()) is True

    @pytest.mark.component
    def test_explicitly_disabling_rth_is_refused(self):
        """Extended-hours bars would not match the session basis of the backtest."""
        # Arrange
        settings = _settings(ibkr_use_rth=False)

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_use_rth(settings)

        assert "IBKR_USE_RTH" in str(excinfo.value)


class TestBarTypeResolution:
    """Parsing guardrails — a bad bar type must never reach the adapter."""

    @pytest.mark.component
    def test_valid_bar_types_are_parsed_in_order(self):
        # Act
        resolved = resolve_live_bar_types([AAPL_1MIN, MSFT_1MIN])

        # Assert
        assert resolved == (BarType.from_str(AAPL_1MIN), BarType.from_str(MSFT_1MIN))

    @pytest.mark.component
    def test_empty_input_resolves_to_empty(self):
        """A node with no subscriptions is legal — Story 1.3's callers build one."""
        assert resolve_live_bar_types([]) == ()

    @pytest.mark.component
    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("", id="empty"),
            pytest.param("AAPL", id="instrument-only"),
            pytest.param("AAPL.NASDAQ-1-MINUTE-LAST", id="missing-aggregation-source"),
            pytest.param("AAPL.NASDAQ-1-CENTURY-LAST-EXTERNAL", id="unknown-aggregation"),
        ],
    )
    def test_unparseable_bar_type_raises_with_a_worked_example(self, bad):
        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([bad])

        assert "EXTERNAL" in str(excinfo.value)

    @pytest.mark.component
    def test_internally_aggregated_bar_type_is_refused(self):
        """INTERNAL never reaches the IB adapter — the DataEngine aggregates locally.

        The operator would get bars that are not the venue's bars, with no warning.
        """
        # Arrange
        internal = "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"

        # Assert the premise
        assert BarType.from_str(internal).is_externally_aggregated() is False

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([internal])

        message = str(excinfo.value)
        assert "INTERNAL" in message
        assert "EXTERNAL" in message

    @pytest.mark.component
    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param("AAPL.NASDAQ-100-TICK-LAST-EXTERNAL", id="tick"),
            pytest.param("AAPL.NASDAQ-1000-VOLUME-LAST-EXTERNAL", id="volume"),
            pytest.param("AAPL.NASDAQ-1000-VALUE-LAST-EXTERNAL", id="value"),
        ],
    )
    def test_non_time_aggregated_bar_types_are_refused(self, bad):
        """`spec.timedelta` raises for these, from inside on_bar and inside the adapter.

        The IBKR adapter only streams time-based bars, so accepting one produces a
        session that subscribes, dies inside the adapter's task, and sees nothing.
        """
        # Assert the premise — the property genuinely raises
        with pytest.raises(ValueError):
            BarType.from_str(bad).spec.timedelta

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([bad])

        assert "time-aggregated" in str(excinfo.value)

    @pytest.mark.component
    def test_composite_bar_types_are_refused(self):
        """Nautilus subscribes to the standard form but publishes the composite one.

        The topics never meet, so the session connects, the subscription is
        accepted, and no bar is ever delivered.
        """
        # Arrange
        composite = "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL@1-MINUTE-EXTERNAL"

        # Assert the premise
        assert BarType.from_str(composite).is_composite() is True

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([composite])

        assert "composite" in str(excinfo.value)

    @pytest.mark.component
    def test_case_variant_duplicates_are_refused(self):
        """`BarType.from_str` preserves instrument-id case, so these are one stream.

        Left undeduplicated they burn two market-data lines and put a lowercase
        id into `load_ids` that IBKR will not resolve — a silently dead
        subscription.
        """
        # Arrange
        lowercase = AAPL_1MIN.lower()

        # Assert the premise — the canonical strings genuinely differ
        assert str(BarType.from_str(lowercase)) != str(BarType.from_str(AAPL_1MIN))

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([lowercase, AAPL_1MIN])

        assert "more than once" in str(excinfo.value)

    @pytest.mark.component
    def test_a_bare_string_is_refused_rather_than_iterated_by_character(self):
        """`Sequence[str]` admits a str, which would report "bar type 'A'"."""
        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types(AAPL_1MIN)

        assert "single string" in str(excinfo.value)

    @pytest.mark.component
    def test_duplicate_bar_types_are_refused(self):
        """Two identical subscriptions burn two market-data lines for one stream."""
        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            resolve_live_bar_types([AAPL_1MIN, AAPL_1MIN])

        assert AAPL_1MIN in str(excinfo.value)


class TestMarketDataLineBudget:
    """AC #4 — fail at startup, naming the limit and the requested count."""

    @pytest.mark.component
    def test_within_budget_is_permitted(self):
        # Arrange
        bar_types = resolve_live_bar_types([AAPL_1MIN, MSFT_1MIN])

        # Act / Assert — no exception
        validate_market_data_line_budget(bar_types, budget=2)

    @pytest.mark.component
    def test_exceeding_budget_names_the_limit_and_both_counts(self):
        # Arrange
        bar_types = resolve_live_bar_types([AAPL_1MIN, MSFT_1MIN])

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            validate_market_data_line_budget(bar_types, budget=1)

        message = str(excinfo.value)
        assert "1" in message  # the limit
        assert "2" in message  # the requested subscription count
        assert "instrument" in message.lower()

    @pytest.mark.component
    def test_several_timeframes_on_one_instrument_each_consume_a_line(self):
        """AC #4 says "instrument count"; lines are consumed per subscription.

        The two diverge exactly here, so the message must report both rather than
        letting a two-timeframe session believe it costs one line.
        """
        # Arrange
        bar_types = resolve_live_bar_types([AAPL_1MIN, "AAPL.NASDAQ-5-MINUTE-LAST-EXTERNAL"])

        # Act / Assert
        with pytest.raises(LiveMarketDataError) as excinfo:
            validate_market_data_line_budget(bar_types, budget=1)

        message = str(excinfo.value)
        assert "2 streaming subscription" in message
        assert "1 instrument" in message

    @pytest.mark.component
    def test_empty_subscription_set_is_permitted(self):
        # Act / Assert — no exception
        validate_market_data_line_budget((), budget=1)


class TestInstrumentIdsFor:
    """The instrument provider must load every contract we will subscribe to."""

    @pytest.mark.component
    def test_returns_distinct_ids_in_first_seen_order(self):
        # Arrange
        bar_types = resolve_live_bar_types(
            [MSFT_1MIN, AAPL_1MIN, "MSFT.NASDAQ-5-MINUTE-LAST-EXTERNAL"]
        )

        # Act
        ids = instrument_ids_for(bar_types)

        # Assert
        assert ids == ("MSFT.NASDAQ", "AAPL.NASDAQ")

    @pytest.mark.component
    def test_empty_input_yields_empty(self):
        assert instrument_ids_for(()) == ()
