"""Component tests for the IB ``avg_px`` serialization workaround.

Measured live on 2026-09-01, in the first session that ever received a real
IBKR fill: the fill killed the ``ExecEngine`` outright.

The chain, verified against the installed 1.220.0 wheel rather than inferred:
the IB execution client stores the order's average fill price as a **``Price``
object** (``adapters/interactive_brokers/execution.py:188`` declares
``dict[ClientOrderId, Price]``, written at ``:1037`` via
``instrument.make_price(...)``), then copies that object straight into the fill
event's free-form ``info`` dict (``:1096``, and the two spread paths at
``:1229``/``:1312``). ``OrderFilled.to_dict_c`` stringifies every other field
but passes ``info`` through untouched (``model/events/order.pyx:4810``), so a
raw ``Price`` reaches ``MsgSpecSerializer.serialize``, which cannot encode it.

That only matters because a live session always runs with the Redis-backed
cache database (AR10, ``src/core/live_cache.py``): ``ExecutionEngine``
``_apply_event_to_order`` → ``Cache.update_order`` →
``CacheDatabaseAdapter.update_order`` serializes ``order.last_event_c()`` on
every fill (``cache/database.pyx:1159``). A backtest has no cache database, so
nothing upstream of this ever surfaced it.

The consequence is worse than a logged error, and is why this is worked around
rather than tolerated: ``_apply_event_to_order`` runs *before* the
``publish_c(topic=f"events.order.{...}")`` at ``execution/engine.pyx:1176``, so
the raise means the ``OrderFilled`` is **never published**. Every downstream
consumer — ``OrderEventObserver``'s ``order.filled`` record (Story 3.3),
``Portfolio``, position events — sees nothing at all, while the order really did
fill at the broker.
"""

import inspect
from pathlib import Path

import pytest
from nautilus_trader.adapters.interactive_brokers.execution import (
    InteractiveBrokersExecutionClient,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveExecClientFactory,
)
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import LiquiditySide, OrderSide, OrderType
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientOrderId,
    InstrumentId,
    StrategyId,
    TradeId,
    TraderId,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.serialization.serializer import MsgSpecSerializer

from src.core import live_node_builder
from src.core.live_exec_avg_px import (
    AVG_PX_ATTRIBUTE,
    StringifiedAvgPrices,
    install_avg_px_serialization_fix,
)

NVDA = InstrumentId.from_str("NVDA.NASDAQ")
USD = Currency.from_str("USD")


def _fill(info: dict | None) -> OrderFilled:
    """A fill shaped like the one the IB adapter emitted live on 2026-09-01."""
    return OrderFilled(
        trader_id=TraderId("PAPER-621bb88c"),
        strategy_id=StrategyId("SMACrossover-000"),
        instrument_id=NVDA,
        client_order_id=ClientOrderId("O-20260901-135605-621bb88c-000-5"),
        venue_order_id=VenueOrderId("101"),
        account_id=AccountId("INTERACTIVE_BROKERS-DU4076626"),
        trade_id=TradeId("E-1"),
        position_id=None,
        order_side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        last_qty=Quantity.from_int(23),
        last_px=Price.from_str("216.90"),
        currency=USD,
        commission=Money(1.0, USD),
        liquidity_side=LiquiditySide.TAKER,
        event_id=UUID4(),
        ts_event=0,
        ts_init=0,
        info=info,
    )


def _serializer() -> MsgSpecSerializer:
    """The encoding a live session actually writes with (``live_cache.py:150``)."""
    import msgspec

    return MsgSpecSerializer(encoding=msgspec.msgpack)


class TestTheWheelDefect:
    """Pins the upstream behaviour this module exists to work around.

    These are canaries, not aspirations. If a nautilus_trader upgrade fixes the
    adapter or teaches the serializer about ``Price``, they go red **by name**
    and this whole module — plus its wiring in ``live_node_builder`` — becomes
    deletable. That is the intended way for this workaround to die.
    """

    @pytest.mark.component
    def test_a_raw_price_inside_order_filled_info_cannot_be_serialized(self):
        """The exact failure that took the node down on 2026-09-01."""
        fill = _fill({"avg_px": Price.from_str("216.90")})

        with pytest.raises(TypeError, match="Price"):
            _serializer().serialize(fill)

    @pytest.mark.component
    def test_the_same_fill_serializes_once_avg_px_is_a_string(self):
        """Anti-tautology twin: the ONLY difference is the type of that one
        value, so the test above is pinning the ``Price``, not some unrelated
        defect in the fill fixture.
        """
        fill = _fill({"avg_px": "216.90"})

        assert _serializer().serialize(fill)

    @pytest.mark.component
    def test_to_dict_passes_info_through_without_stringifying_it(self):
        """Why the fix belongs at the adapter's map and not at the event: every
        other field is stringified by ``to_dict_c`` (``order.pyx:4790-4812``);
        ``info`` alone is passed through by reference.
        """
        price = Price.from_str("216.90")

        assert OrderFilled.to_dict(_fill({"avg_px": price}))["info"]["avg_px"] is price

    @pytest.mark.component
    def test_the_adapter_still_keeps_average_prices_under_the_patched_name(self):
        """Drift pin. The fix swaps one private attribute *by name*, so a
        rename upstream would make ``install_avg_px_serialization_fix`` a
        silent no-op and the crash would return live — where it costs an RTH
        window to rediscover. Read from the adapter's own source rather than
        from an instance, which needs a running loop and a socket.
        """
        source = Path(inspect.getfile(InteractiveBrokersExecutionClient)).read_text()

        assert f"self.{AVG_PX_ATTRIBUTE}" in source

    @pytest.mark.component
    def test_the_adapter_only_ever_reads_average_prices_into_the_info_dict(self):
        """Why stringifying is safe. If a future version does arithmetic on a
        stored value, ``str`` breaks it — so pin that every read site is an
        ``info["avg_px"] = ...`` assignment and nothing else.
        """
        source = Path(inspect.getfile(InteractiveBrokersExecutionClient)).read_text()
        reads = [
            line.strip()
            for line in source.splitlines()
            if f"self.{AVG_PX_ATTRIBUTE}[" in line and not line.strip().startswith("self.")
        ]

        assert reads, "expected at least one read site; the attribute may have been renamed"
        assert all(line.startswith('info["avg_px"] =') for line in reads), reads


class TestStringifiedAvgPrices:
    @pytest.mark.component
    def test_a_price_is_stored_as_its_string_form(self):
        prices = StringifiedAvgPrices()
        prices[ClientOrderId("O-1")] = Price.from_str("216.90")

        assert prices[ClientOrderId("O-1")] == "216.90"

    @pytest.mark.component
    def test_the_stored_value_is_what_the_adapter_would_have_rendered(self):
        """``str(Price)`` is the same text ``to_dict_c`` uses for ``last_px``
        (``order.pyx:4803``), so the transcript reader sees one price format.
        """
        price = Price.from_str("216.90")
        prices = StringifiedAvgPrices()
        prices[ClientOrderId("O-1")] = price

        assert prices[ClientOrderId("O-1")] == str(price)

    @pytest.mark.component
    def test_a_fill_built_from_the_patched_map_serializes(self):
        """The end-to-end claim, through the real serializer."""
        prices = StringifiedAvgPrices()
        prices[ClientOrderId("O-1")] = Price.from_str("216.90")
        info = {"avg_px": prices[ClientOrderId("O-1")]}

        assert _serializer().serialize(_fill(info))

    @pytest.mark.component
    def test_a_value_that_is_already_a_string_is_left_alone(self):
        prices = StringifiedAvgPrices()
        prices[ClientOrderId("O-1")] = "216.90"

        assert prices[ClientOrderId("O-1")] == "216.90"


class TestInstall:
    @pytest.mark.component
    def test_the_map_is_replaced_with_the_stringifying_one(self):
        class _Client:
            def __init__(self) -> None:
                self._order_avg_prices: dict = {}

        client = _Client()
        install_avg_px_serialization_fix(client)

        assert isinstance(client._order_avg_prices, StringifiedAvgPrices)

    @pytest.mark.component
    def test_entries_written_before_the_swap_are_carried_over_and_stringified(self):
        """The client is patched at construction, so this should never have
        anything to carry — but a silent drop would be a data loss that no
        other test would see.
        """

        class _Client:
            def __init__(self) -> None:
                self._order_avg_prices: dict = {
                    ClientOrderId("O-1"): Price.from_str("216.90"),
                }

        client = _Client()
        install_avg_px_serialization_fix(client)

        assert client._order_avg_prices[ClientOrderId("O-1")] == "216.90"

    @pytest.mark.component
    def test_a_client_without_the_attribute_is_refused_rather_than_silently_skipped(self):
        """Fail loudly at build time, not invisibly at the first fill. A
        no-op here would reproduce the exact 2026-09-01 outcome — an order that
        reaches the broker and a node that dies acknowledging it.
        """

        class _Renamed:
            pass

        with pytest.raises(AttributeError, match=AVG_PX_ATTRIBUTE):
            install_avg_px_serialization_fix(_Renamed())

    @pytest.mark.component
    def test_the_builder_registers_the_patched_factory_not_the_stock_one(self):
        """The wiring pin.

        Story 3.1's review found AC #6's runner wiring guarded by nothing —
        deleting the call site left 904 tests green. The same hole here would be
        worse: every automated test in this repo passes with the wrapper
        unwired, because no test tier has a cache database, and the failure
        would resurface only against a live gateway.
        """
        source = Path(inspect.getfile(live_node_builder)).read_text()
        registrations = [
            line.strip()
            for line in source.splitlines()
            if "add_exec_client_factory" in line and not line.strip().startswith("#")
        ]

        assert len(registrations) == 1, registrations
        assert "InteractiveBrokersLiveExecClientFactory" in registrations[0]
        assert issubclass(
            live_node_builder.InteractiveBrokersLiveExecClientFactory,
            InteractiveBrokersLiveExecClientFactory,
        )
        assert (
            live_node_builder.InteractiveBrokersLiveExecClientFactory
            is not InteractiveBrokersLiveExecClientFactory
        ), "the wrapper was replaced by the stock factory; the avg_px patch is unwired"

    @pytest.mark.component
    def test_the_wrapper_keeps_the_class_name_nautilus_branches_on(self):
        """The regression that cost four live sessions on 2026-09-01.

        Nautilus decides whether to call ``cache.set_specific_venue(...)`` by
        comparing ``factory.__name__`` to this literal string. Naming the
        subclass anything else silently disables it, and the only symptom is a
        session that connects, reports both engines healthy, and never starts
        trading — 120s later, with no exception anywhere.
        """
        assert (
            live_node_builder.InteractiveBrokersLiveExecClientFactory.__name__
            == "InteractiveBrokersLiveExecClientFactory"
        )

    @pytest.mark.component
    def test_nautilus_still_branches_on_that_name(self):
        """The canary for the mechanism above.

        This is upstream's own ``# Temporary handling`` block. If an upgrade
        removes or reworks it, the name constraint may no longer be needed —
        but that must be a decision, not a discovery made against a live
        gateway.
        """
        import nautilus_trader.live.node_builder as nautilus_node_builder

        source = Path(inspect.getfile(nautilus_node_builder)).read_text()

        assert 'factory.__name__ == "InteractiveBrokersLiveExecClientFactory"' in source
        assert "set_specific_venue" in source

    @pytest.mark.component
    def test_the_factory_wrapper_patches_the_client_it_returns(self, monkeypatch):
        """Drives our ``create`` for real, with only the upstream call stubbed —
        so it fails if the wrapper stops calling through, stops patching, or
        stops returning the client.
        """

        class _Client:
            def __init__(self) -> None:
                self._order_avg_prices: dict = {}

        built = _Client()
        monkeypatch.setattr(
            InteractiveBrokersLiveExecClientFactory,
            "create",
            staticmethod(lambda **kwargs: built),
        )

        returned = live_node_builder.InteractiveBrokersLiveExecClientFactory.create(
            loop=None, name="IB", config=None, msgbus=None, cache=None, clock=None
        )

        assert returned is built
        assert isinstance(built._order_avg_prices, StringifiedAvgPrices)

    @pytest.mark.component
    def test_installing_twice_is_harmless(self):
        class _Client:
            def __init__(self) -> None:
                self._order_avg_prices: dict = {}

        client = _Client()
        install_avg_px_serialization_fix(client)
        client._order_avg_prices[ClientOrderId("O-1")] = Price.from_str("216.90")
        install_avg_px_serialization_fix(client)

        assert client._order_avg_prices[ClientOrderId("O-1")] == "216.90"
