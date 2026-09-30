"""The session-view reader against a fake cache adapter (Story 4.6, AC #1/#4, D-A/D-B/D-I).

``src/core/live_session_view.py`` reads a session's durable engine cache — its
Redis namespace — through Nautilus's ``CacheDatabaseAdapter`` load methods and
converts what it finds into ``SessionView`` at the boundary. Here the adapter
is a fake that serves **real** Nautilus ``Position`` objects (built from real
fills), so the conversion is exercised on the real types without a Redis
socket. The real adapter against a real Redis is the integration tier's
(``tests/integration/core/test_live_session_view_redis.py``).

Nothing here constructs a ``TradingNode``, a ``Logger`` or a real adapter; the
autouse fixture holds the component tier to that.
"""

import ast
import fnmatch
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import AccountId, PositionId, StrategyId, TradeId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.model.position import Position
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs

from src.config import RedisSettings
from src.core import live_session_view
from src.core.live_broker_state import IB_UNSET_DOUBLE
from src.core.live_cache import RedisUnreachableError
from src.core.live_check import EXIT_CODES, classify_failure
from src.core.live_session_view import (
    ACCOUNT_SUMMARY_KEY_PREFIX,
    SessionViewFailure,
    SessionViewUnavailableError,
    normalised_account,
    read_session_view,
    require_engine_state,
)
from src.core.live_trader_id import derive_trader_id
from src.models.broker_state import CashBalance
from src.models.reconciliation import ViewPosition

pytestmark = pytest.mark.component

SESSION_ID = UUID("0e8f1c2a-1111-2222-3333-444455556666")
TRADER_ID = derive_trader_id(SESSION_ID)
ACCOUNT = "DU4076626"
OTHER_ACCOUNT = "DU9999999"
AAPL = TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")
NVDA = TestInstrumentProvider.equity(symbol="NVDA", venue="NASDAQ")
#: Size precision 6: fractional quantities, to prove the netting is exact.
BTC = TestInstrumentProvider.btcusdt_binance()


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()})"
    )


_fills = iter(range(1, 10_000))


def _fill(instrument, side: OrderSide, qty: int | str, strategy: str, account: str):
    n = next(_fills)
    order = TestExecStubs.market_order(
        instrument=instrument,
        order_side=side,
        quantity=Quantity.from_str(str(qty)),
        strategy_id=StrategyId(strategy),
    )
    return TestEventStubs.order_filled(
        order,
        instrument=instrument,
        # The shape the IB exec client stamps: `get_id()` is the bare account.
        account_id=AccountId(f"INTERACTIVE_BROKERS-{account}"),
        trade_id=TradeId(f"T-{n}"),
        position_id=PositionId(f"{instrument.id}-{strategy}"),
        last_px=Price.from_str("100.00"),
    )


def _position(
    instrument,
    *legs: tuple[OrderSide, int | str],
    strategy: str = "S-001",
    account: str = ACCOUNT,
) -> Position:
    """A real Position, replayed from real fills exactly as ``load_position`` does."""
    (side, qty), *rest = legs
    position = Position(instrument, _fill(instrument, side, qty, strategy, account))
    for side, qty in rest:
        position.apply(_fill(instrument, side, qty, strategy, account))
    return position


def _summary(account: str = ACCOUNT, **currencies) -> dict[str, bytes]:
    body = {"": {"AccountType": "INDIVIDUAL"}, **currencies}
    return {f"{ACCOUNT_SUMMARY_KEY_PREFIX}{account}": json.dumps(body).encode()}


class FakeAdapter:
    """``CacheDatabaseAdapter``'s load surface, recording every call."""

    def __init__(
        self,
        *,
        positions=None,
        general=None,
        other_keys=("instruments:AAPL.NASDAQ",),
        repeat_keys: bool = False,
        accounts=None,
    ):
        self.positions: dict[str, object] = dict(positions or {})
        #: A dict normally; anything else stands in for a drifted `load()` answer.
        self.general: object = dict(general) if isinstance(general, dict) else general or {}
        self.other_keys = tuple(other_keys)
        #: Redis SCAN may return a key more than once (rehashing): model it.
        self.repeat_keys = repeat_keys
        #: Story 4.7, D-C: ``accounts:<id>`` — ``{str(AccountId): account or exception}``.
        self.accounts: dict[str, object] = dict(accounts or {})
        self.calls: list[tuple] = []
        self.closed = False

    def _all_keys(self) -> list[str]:
        prefix = f"trader-{TRADER_ID}:"
        keys = [f"{prefix}positions:{pid}" for pid in self.positions]
        general = self.general if isinstance(self.general, dict) else {}
        keys += [f"{prefix}general:{name}" for name in general]
        keys += [f"{prefix}{key}" for key in self.other_keys]
        return keys

    def keys(self, pattern: str = "*") -> list[str]:
        self.calls.append(("keys", pattern))
        prefix = f"trader-{TRADER_ID}:"
        found = [key for key in self._all_keys() if fnmatch.fnmatchcase(key, prefix + pattern)]
        return found * 2 if self.repeat_keys else found

    def load_position(self, position_id):
        self.calls.append(("load_position", str(position_id)))
        value = self.positions[str(position_id)]
        if isinstance(value, BaseException):
            raise value
        return value

    def load(self):
        self.calls.append(("load",))
        return dict(self.general) if isinstance(self.general, dict) else self.general

    def load_account(self, account_id):
        self.calls.append(("load_account", str(account_id)))
        value = self.accounts.get(str(account_id))
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.calls.append(("close",))
        self.closed = True


class RecordingLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.records.append(("info", event, fields))

    def warning(self, event: str, **fields) -> None:
        self.records.append(("warning", event, fields))

    def error(self, event: str, **fields) -> None:
        self.records.append(("error", event, fields))


def _redis() -> RedisSettings:
    return RedisSettings(_env_file=None)


def _read(adapter: FakeAdapter, *, log=None, reachability=None, account: str = ACCOUNT):
    opened: list[tuple] = []

    def factory(trader_id, config):
        opened.append((trader_id, config))
        return adapter

    view = read_session_view(
        SESSION_ID,
        account,
        _redis(),
        log=log or RecordingLog(),
        adapter_factory=factory,
        reachability=reachability or (lambda host, port: None),
    )
    return view, opened


class TestTheSessionsPositionsAreRead:
    def test_open_positions_become_signed_net_quantities_per_instrument(self):
        adapter = FakeAdapter(
            positions={
                "NVDA.NASDAQ-S-001": _position(NVDA, (OrderSide.BUY, 10)),
                "AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.SELL, 3)),
            },
            general=_summary(USD={"TotalCashValue": 1000.5}),
        )

        view, _ = _read(adapter)

        assert view.positions == (
            ViewPosition("AAPL.NASDAQ", Decimal("-3")),
            ViewPosition("NVDA.NASDAQ", Decimal("10")),
        )
        assert view.trader_id == TRADER_ID

    def test_several_open_positions_in_one_instrument_are_summed(self):
        """NETTING keeps one position per strategy, and Nautilus's startup
        reconciliation adds an ``EXTERNAL`` one: the session's view of the
        instrument is their net — what IBKR reports as one row."""
        adapter = FakeAdapter(
            positions={
                "AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 10)),
                "AAPL.NASDAQ-EXTERNAL": _position(AAPL, (OrderSide.BUY, 4), strategy="EXTERNAL"),
                "AAPL.NASDAQ-S-002": _position(AAPL, (OrderSide.SELL, 1), strategy="S-002"),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == (ViewPosition("AAPL.NASDAQ", Decimal("13")),)

    def test_closed_positions_are_not_held(self):
        adapter = FakeAdapter(
            positions={
                "AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 7), (OrderSide.SELL, 7)),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == ()

    def test_a_close_then_reopen_reads_as_the_reopened_quantity(self):
        """F5: replaying the whole fill list resets at FLAT, so the current leg wins."""
        position = _position(AAPL, (OrderSide.BUY, 7), (OrderSide.SELL, 7), (OrderSide.BUY, 3))
        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": position},
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == (ViewPosition("AAPL.NASDAQ", Decimal("3")),)

    def test_offsetting_open_positions_net_to_no_row(self):
        adapter = FakeAdapter(
            positions={
                "AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 5)),
                "AAPL.NASDAQ-S-002": _position(AAPL, (OrderSide.SELL, 5), strategy="S-002"),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == ()

    def test_a_flat_session_with_state_is_a_successful_empty_view(self):
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": 1}))

        view, _ = _read(adapter)

        assert view.positions == ()
        assert view.cash == (CashBalance("USD", Decimal("1")),)

    def test_the_namespace_is_the_sessions_derived_trader_id(self):
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": 1}))

        _, opened = _read(adapter)

        [(trader_id, config)] = opened
        assert trader_id == TRADER_ID
        assert config.use_trader_prefix is True
        assert config.use_instance_id is False
        assert config.flush_on_start is False


class TestTheSessionsCashIsRead:
    def test_total_cash_value_for_the_configured_account(self):
        adapter = FakeAdapter(
            general={
                **_summary(USD={"TotalCashValue": 100000.52, "NetLiquidation": 400000.0}),
                **_summary(OTHER_ACCOUNT, USD={"TotalCashValue": 5.0}),
            }
        )

        view, _ = _read(adapter)

        assert view.cash == (CashBalance("USD", Decimal("100000.52")),)

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), IB_UNSET_DOUBLE, "12.5", None, True]
    )
    def test_an_unusable_value_is_absent_never_zero(self, value):
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": value}))

        view, _ = _read(adapter)

        assert view.cash == ()
        assert view.cash_known is False

    def test_currency_less_tags_and_base_are_skipped(self):
        adapter = FakeAdapter(
            general=_summary(BASE={"TotalCashValue": 9.0}, USD={"TotalCashValue": 2.0})
        )

        view, _ = _read(adapter)

        assert view.cash == (CashBalance("USD", Decimal("2.0")),)

    def test_no_summary_for_the_account_means_unknown_cash_not_a_failure(self):
        adapter = FakeAdapter(general=_summary(OTHER_ACCOUNT, USD={"TotalCashValue": 5.0}))

        view, _ = _read(adapter)

        assert view.cash == ()

    def test_a_malformed_summary_is_unreadable_never_guessed(self):
        adapter = FakeAdapter(general={f"{ACCOUNT_SUMMARY_KEY_PREFIX}{ACCOUNT}": b"{not json"})

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.UNREADABLE


RECORDED = datetime(2026, 9, 26, 20, 0, 1, 250000, tzinfo=UTC)
RECORDED_NANOS = 1_790_452_801_250_000_000
IB_ACCOUNT_KEY = f"INTERACTIVE_BROKERS-{ACCOUNT}"


def _account(*events) -> SimpleNamespace:
    return SimpleNamespace(events=list(events))


def _state(nanos: int, *, reported: bool = True) -> SimpleNamespace:
    return SimpleNamespace(ts_event=nanos, is_reported=reported)


class TestTheSessionsCashSaysWhenItWasRecorded:
    """Story 4.7, D-C (PO ruling A): Story 4.6's routed debt — "a session's local
    cash carries no timestamp". The account's last *reported* ``AccountState``
    is when the broker last pushed the summary, read load-only."""

    def test_the_last_reported_state_is_when_the_cash_was_recorded(self):
        adapter = FakeAdapter(
            general=_summary(USD={"TotalCashValue": 5.0}),
            accounts={
                IB_ACCOUNT_KEY: _account(
                    _state(RECORDED_NANOS - 1),
                    _state(RECORDED_NANOS),
                    _state(RECORDED_NANOS + 9, reported=False),
                )
            },
        )

        view, _ = _read(adapter)

        assert view.cash_recorded_at == RECORDED
        assert ("load_account", IB_ACCOUNT_KEY) in adapter.calls

    @pytest.mark.parametrize(
        "account",
        [None, _account(), _account(_state(0)), _account(_state(5, reported=False))],
        ids=["no-account", "no-events", "zero-ts", "none-reported"],
    )
    def test_an_unknown_time_is_none_never_a_failure(self, account):
        accounts = {} if account is None else {IB_ACCOUNT_KEY: account}
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": 5.0}), accounts=accounts)

        view, _ = _read(adapter)

        assert view.cash == (CashBalance("USD", Decimal("5.0")),)
        assert view.cash_recorded_at is None

    def test_unknown_cash_reads_no_account(self):
        """Nothing to date: the account is not read at all."""
        adapter = FakeAdapter(general=_summary(OTHER_ACCOUNT, USD={"TotalCashValue": 5.0}))

        view, _ = _read(adapter)

        assert view.cash_recorded_at is None
        assert not [call for call in adapter.calls if call[0] == "load_account"]

    @pytest.mark.parametrize(
        "broken",
        [
            RuntimeError(f"decode failed for {ACCOUNT}"),
            SimpleNamespace(events=[SimpleNamespace(ts_event=5)]),  # drifted event shape
        ],
        ids=["load-raises", "event-drift"],
    )
    def test_an_account_that_cannot_be_read_leaves_the_time_unknown_and_says_so(self, broken):
        """Code review (2026-09-28): the time only decorates the cash note, so an
        unreadable account must not fail the whole check (PO note 3: report and
        exit). Positions and cash are read and compared as before; the time is
        unknown, and a WARNING says why — never silent, never a raw account."""
        log = RecordingLog()
        adapter = FakeAdapter(
            general=_summary(USD={"TotalCashValue": 5.0}), accounts={IB_ACCOUNT_KEY: broken}
        )

        view, _ = _read(adapter, log=log)

        assert view.cash == (CashBalance("USD", Decimal("5.0")),)
        assert view.cash_recorded_at is None
        [(level, _, fields)] = [
            r for r in log.records if r[1] == live_session_view.ACCOUNT_UNREADABLE_EVENT
        ]
        assert level == "warning" and fields["error_type"] in ("RuntimeError", "AttributeError")
        assert ACCOUNT not in repr(log.records)
        assert adapter.closed is True

    def test_the_account_is_found_under_the_normalised_account(self):
        adapter = FakeAdapter(
            general=_summary(USD={"TotalCashValue": 5.0}),
            accounts={IB_ACCOUNT_KEY: _account(_state(RECORDED_NANOS))},
        )

        view, _ = _read(adapter, account="  du4076626 ")

        assert view.cash_recorded_at == RECORDED


class TestAMissingOrBrokenViewIsAFailureNeverFlat:
    def test_an_empty_namespace_is_no_engine_state(self):
        adapter = FakeAdapter(other_keys=())

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.NO_ENGINE_STATE
        assert adapter.closed is True

    def test_a_position_that_cannot_be_rebuilt_fails_loudly(self):
        """F4: ``load_positions()`` would silently drop it — a partial "flat"."""
        adapter = FakeAdapter(
            positions={
                "NVDA.NASDAQ-S-001": _position(NVDA, (OrderSide.BUY, 10)),
                "AAPL.NASDAQ-S-001": None,
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.UNREADABLE
        assert "AAPL.NASDAQ-S-001" in raised.value.detail
        assert adapter.closed is True

    def test_a_corrupt_cache_is_unreadable(self):
        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": RuntimeError("Corrupt cache with duplicate event")},
            general=_summary(USD={"TotalCashValue": 1}),
        )

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.UNREADABLE
        assert raised.value.error_type == "RuntimeError"
        assert "duplicate" not in str(raised.value), "never the adapter's text"

    def test_a_moved_load_method_is_adapter_drift(self):
        class Drifted(FakeAdapter):
            load = None  # type: ignore[assignment]

        adapter = Drifted(general={}, positions={})

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.ADAPTER_INCOMPATIBLE
        assert adapter.closed is True

    def test_an_unreachable_redis_is_refused_before_the_adapter_is_built(self):
        """F3: the adapter's constructor blocks forever on a dead Redis."""
        built: list = []

        def unreachable(host, port):
            raise RedisUnreachableError("Redis is not reachable at 127.0.0.1:6379")

        with pytest.raises(RedisUnreachableError):
            read_session_view(
                SESSION_ID,
                ACCOUNT,
                _redis(),
                log=RecordingLog(),
                adapter_factory=lambda *a: built.append(a),
                reachability=unreachable,
            )

        assert built == []

    def test_the_failure_exits_one_not_four(self):
        """A session with no local view is not a broker outage (D-B)."""
        failure = SessionViewUnavailableError(SessionViewFailure.NO_ENGINE_STATE, "x")

        assert EXIT_CODES[classify_failure(failure)] == 1


class TestRecordsAndMasking:
    def test_a_read_emits_one_record_with_counts_and_no_account(self):
        log = RecordingLog()
        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 2))},
            general=_summary(USD={"TotalCashValue": 1}),
        )

        _read(adapter, log=log)

        [(level, event, fields)] = log.records
        assert (level, event) == ("info", "reconcile.session_view_read")
        assert fields["trader_id"] == TRADER_ID
        assert fields["position_count"] == 1
        assert fields["positions"] == {"AAPL.NASDAQ": "2"}
        assert fields["currencies"] == ["USD"]
        assert fields["cash"] == {"USD": "1"}
        assert "elapsed_ms" in fields

    def test_a_failure_emits_one_record_with_the_reason(self):
        log = RecordingLog()

        with pytest.raises(SessionViewUnavailableError):
            _read(FakeAdapter(other_keys=()), log=log)

        [(level, event, fields)] = log.records
        assert (level, event) == ("error", "reconcile.session_view_failed")
        assert fields["reason"] == "no_engine_state"

    def test_no_raw_account_reaches_any_record_or_exception(self):
        """F7: the summary key embeds the raw account."""
        log = RecordingLog()
        cases = [
            FakeAdapter(general=_summary(USD={"TotalCashValue": 1})),
            FakeAdapter(general={f"{ACCOUNT_SUMMARY_KEY_PREFIX}{ACCOUNT}": b"{bad"}),
        ]
        messages = []
        for adapter in cases:
            try:
                _read(adapter, log=log)
            except SessionViewUnavailableError as exc:
                messages.append(str(exc))

        rendered = repr(log.records) + "".join(messages)
        assert ACCOUNT not in rendered
        assert messages, "the malformed case must have raised"

    def test_the_adapter_is_closed_on_success(self):
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": 1}))

        _read(adapter)

        assert adapter.calls[-1] == ("close",)


#: ``CacheDatabaseAdapter`` members that write, flush or delete (D-I).
ADAPTER_MUTATORS = ("add", "update", "delete", "flush", "heartbeat", "index_", "snapshot")


def _mutating_calls(source: str) -> list[str]:
    return [
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith(ADAPTER_MUTATORS)
    ]


class TestTheReaderNeverWrites:
    """D-I: load methods only. The integration tier proves it with MONITOR."""

    def test_the_module_calls_no_adapter_mutator(self):
        source = Path(live_session_view.__file__).read_text(encoding="utf-8")

        assert "load_position" in source, "the scan is not looking at the reader"
        assert _mutating_calls(source) == []

    @pytest.mark.parametrize(
        "planted",
        ["adapter.add_position(p)", "adapter.flush()", "db.update_account(a)", "a.delete_order(o)"],
    )
    def test_the_scan_can_fail(self, planted):
        assert _mutating_calls(planted) != []

    def test_the_fake_saw_only_reads(self):
        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 2))},
            general=_summary(USD={"TotalCashValue": 1}),
        )

        _read(adapter)

        assert {call[0] for call in adapter.calls} <= {
            "keys",
            "load_position",
            "load",
            "load_account",
            "close",
        }


class TestTheAccountIsTheOneTheSessionKnew:
    """Review fix (HIGH): the session's exec client knew its account trimmed and
    upper-cased; the summary key and every position carry that form."""

    @pytest.mark.parametrize("raw", ["du4076626", "  DU4076626 ", "Du4076626\n"])
    def test_a_lowercase_or_padded_account_still_finds_the_sessions_cash(self, raw):
        adapter = FakeAdapter(general=_summary(USD={"TotalCashValue": 5.0}))

        view, _ = _read(adapter, account=raw)

        assert view.cash == (CashBalance("USD", Decimal("5.0")),)

    @pytest.mark.parametrize("raw", ["DU4076626", "du4076626", "  DU4076626 ", "Du4076626\n"])
    def test_the_normalisation_is_the_node_builders_own(self, raw):
        """A duplicated rule needs its own equality pin (CLAUDE.md)."""
        from src.config import IBKRSettings
        from src.core.live_node_builder import _resolve_account

        settings = IBKRSettings(
            _env_file=None, tws_account=raw, ibkr_client_id=1, ibkr_live_client_id=10
        )

        assert normalised_account(raw) == _resolve_account(settings)

    def test_positions_held_for_another_account_are_left_out_and_counted(self):
        log = RecordingLog()
        adapter = FakeAdapter(
            positions={
                "AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 5)),
                "NVDA.NASDAQ-S-001": _position(NVDA, (OrderSide.BUY, 3), account=OTHER_ACCOUNT),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter, log=log)

        assert view.positions == (ViewPosition("AAPL.NASDAQ", Decimal("5")),)
        # The count rides on the view too, so `live reconcile` can print it
        # (PR #35 code review, P9).
        assert view.positions_skipped == 1
        [warning] = [r for r in log.records if r[1] == "reconcile.session_view_other_account"]
        assert warning[0] == "warning"
        assert warning[2]["positions_skipped"] == 1
        assert OTHER_ACCOUNT not in repr(log.records)


class TestEachPositionCountsOnceAndExactly:
    def test_a_key_the_scan_returns_twice_is_counted_once(self):
        """Redis ``SCAN`` may repeat a key; Nautilus's own ``load_positions``
        dedupes by id, and so must this reader."""
        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": _position(AAPL, (OrderSide.BUY, 7))},
            general=_summary(USD={"TotalCashValue": 1}),
            repeat_keys=True,
        )

        view, _ = _read(adapter)

        assert view.positions == (ViewPosition("AAPL.NASDAQ", Decimal("7")),)
        loads = [call for call in adapter.calls if call[0] == "load_position"]
        assert loads == [("load_position", "AAPL.NASDAQ-S-001")]

    def test_fractional_quantities_sum_exactly(self):
        """No tolerance anywhere the netting happens (AC #3)."""
        adapter = FakeAdapter(
            positions={
                "BTCUSDT.BINANCE-S-001": _position(BTC, (OrderSide.BUY, "0.500001")),
                "BTCUSDT.BINANCE-S-002": _position(
                    BTC, (OrderSide.BUY, "0.249999"), strategy="S-002"
                ),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == (ViewPosition("BTCUSDT.BINANCE", Decimal("0.750000")),)

    def test_a_net_of_one_millionth_is_not_flat(self):
        adapter = FakeAdapter(
            positions={
                "BTCUSDT.BINANCE-S-001": _position(BTC, (OrderSide.BUY, "0.500001")),
                "BTCUSDT.BINANCE-S-002": _position(
                    BTC, (OrderSide.SELL, "0.500000"), strategy="S-002"
                ),
            },
            general=_summary(USD={"TotalCashValue": 1}),
        )

        view, _ = _read(adapter)

        assert view.positions == (ViewPosition("BTCUSDT.BINANCE", Decimal("0.000001")),)


class TestEveryReadFailureIsTypedAndLogged:
    """Review fix: nothing escapes the reader untyped, unlogged, or with adapter text."""

    def test_an_adapter_that_cannot_be_opened_is_unreadable(self):
        log = RecordingLog()

        def factory(trader_id, config):
            raise RuntimeError(f"auth failed for {ACCOUNT}")

        with pytest.raises(SessionViewUnavailableError) as raised:
            read_session_view(
                SESSION_ID,
                ACCOUNT,
                _redis(),
                log=log,
                adapter_factory=factory,
                reachability=lambda host, port: None,
            )

        assert raised.value.reason is SessionViewFailure.UNREADABLE
        assert raised.value.error_type == "RuntimeError"
        [(level, event, _)] = log.records
        assert (level, event) == ("error", "reconcile.session_view_failed")
        assert ACCOUNT not in str(raised.value) + repr(log.records)

    def test_a_general_cache_that_is_not_a_mapping_is_unreadable(self):
        adapter = FakeAdapter(general=["not", "a", "mapping"])

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.UNREADABLE
        assert adapter.closed is True

    def test_a_position_whose_fields_cannot_be_read_is_unreadable(self):
        class Drifted:
            @property
            def is_open(self):
                raise AttributeError("moved")

        adapter = FakeAdapter(
            positions={"AAPL.NASDAQ-S-001": Drifted()},
            general=_summary(USD={"TotalCashValue": 1}),
        )

        with pytest.raises(SessionViewUnavailableError) as raised:
            _read(adapter)

        assert raised.value.reason is SessionViewFailure.UNREADABLE
        assert raised.value.error_type == "AttributeError"
        assert "moved" not in str(raised.value)


class TestTheEngineStatePrecheck:
    """Review fix: a session with nothing to compare is refused before any IB connection."""

    @staticmethod
    def _check(adapter, *, log=None, reachability=None):
        require_engine_state(
            SESSION_ID,
            _redis(),
            log=log or RecordingLog(),
            adapter_factory=lambda trader_id, config: adapter,
            reachability=reachability or (lambda host, port: None),
        )

    def test_a_namespace_with_state_passes_reading_keys_only(self):
        adapter = FakeAdapter()

        self._check(adapter)

        assert adapter.closed is True
        assert {call[0] for call in adapter.calls} == {"keys", "close"}

    def test_an_empty_namespace_is_refused_and_logged(self):
        log = RecordingLog()
        adapter = FakeAdapter(other_keys=())

        with pytest.raises(SessionViewUnavailableError) as raised:
            self._check(adapter, log=log)

        assert raised.value.reason is SessionViewFailure.NO_ENGINE_STATE
        [(level, event, fields)] = log.records
        assert (level, event, fields["reason"]) == (
            "error",
            "reconcile.session_view_failed",
            "no_engine_state",
        )
        assert adapter.closed is True

    def test_an_unreachable_redis_is_refused_before_the_adapter(self):
        built: list = []

        def unreachable(host, port):
            raise RedisUnreachableError("Redis is not reachable")

        with pytest.raises(RedisUnreachableError):
            require_engine_state(
                SESSION_ID,
                _redis(),
                log=RecordingLog(),
                adapter_factory=lambda *args: built.append(args),
                reachability=unreachable,
            )

        assert built == [], "the adapter's constructor blocks forever on a dead Redis"


def _adapter_members(source: str) -> set[str]:
    """Every adapter member the module names: ``adapter.<x>(...)`` calls and the
    string names it dispatches through ``_member(adapter, "<x>")``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "adapter"
        ):
            names.add(func.attr)
        if isinstance(func, ast.Name) and func.id == "_member":
            names.update(
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return names


class TestTheLoadSurfaceIsPinned:
    """Review fix: the members this module reaches are an exact, mutator-free set."""

    def test_load_methods_are_exactly_the_four_reads(self):
        """Story 4.7, D-C (PO ruling A): ``load_account`` joins deliberately, in
        the constant and in this pin together."""
        assert live_session_view.LOAD_METHODS == ("keys", "load_position", "load", "load_account")
        assert not [m for m in live_session_view.LOAD_METHODS if m.startswith(ADAPTER_MUTATORS)]

    def test_every_adapter_member_named_is_a_load_method_or_close(self):
        source = Path(live_session_view.__file__).read_text(encoding="utf-8")

        named = _adapter_members(source)

        assert {"close", "keys"} <= named, "the scan is not looking at the reader"
        assert named <= {*live_session_view.LOAD_METHODS, "close"}

    def test_the_member_scan_can_fail(self):
        planted = 'adapter.add_position(p)\n_member(adapter, "flush")\n'
        assert _adapter_members(planted) == {"add_position", "flush"}


class TestTheSummaryKeyIsTheAdapters:
    """Canary (the 4.1 precedent): the key and bytes the reader depends on are what
    the real IB exec client writes. Fails by name if the adapter moves them."""

    async def test_the_real_exec_client_writes_the_bytes_the_reader_parses(self):
        from src.core.live_broker_state import _cash_from
        from tests.component.core.test_live_broker_state_adapter import (
            _push_account_summary,
            _stack,
        )

        stack = _stack()
        _push_account_summary(stack.exec_client)
        account = stack.exec_client.account_id.get_id()

        raw = stack.exec_client._cache.get(f"{ACCOUNT_SUMMARY_KEY_PREFIX}{account}")

        assert raw is not None, (
            "CANARY: the IB exec client no longer caches its account summary under "
            "'accountSummary:<account>', so the session view's cash would read unknown"
        )
        parsed = live_session_view._cash(raw)
        assert parsed == _cash_from(stack.exec_client._account_summary)
        assert parsed == (CashBalance("USD", Decimal("100000.52")),)


class TestTheAccountTimeIsTheAdapters:
    """Canaries for Story 4.7's "session cash as of <time>" (D-B, D-C): the id the
    account is stored under, and the reported ``AccountState`` the push emits."""

    def test_the_factory_names_the_account_after_the_client_key(self):
        """The session's account is ``AccountId(f"{name or IB_VENUE.value}-…")``
        (``factories.py:304``); the node builder's only exec-client key is ``IB``
        (pinned in ``test_live_node_builder.py``), which is ``IB_EXEC_CLIENT_ID``."""
        import inspect

        from nautilus_trader.adapters.interactive_brokers.common import IB
        from nautilus_trader.adapters.interactive_brokers.factories import (
            InteractiveBrokersLiveExecClientFactory,
        )

        from src.core.live_broker_state import IB_EXEC_CLIENT_ID

        source = inspect.getsource(InteractiveBrokersLiveExecClientFactory.create)
        assert 'AccountId(f"{name or IB_VENUE.value}-{ib_account}")' in source, (
            "CANARY: the IB factory no longer names the account after its client key, "
            "so `live reconcile` would look for the session's account under the wrong id"
        )
        assert IB == IB_EXEC_CLIENT_ID

    async def test_a_summary_push_emits_a_reported_state_the_time_is_read_from(self):
        from nautilus_trader.portfolio.portfolio import Portfolio

        from src.core.live_broker_state import cash_recorded_at
        from tests.component.core.test_live_broker_state_adapter import (
            _push_account_summary,
            _stack,
        )

        stack = _stack()
        cache = stack.exec_client._cache
        Portfolio(stack.exec_client._msgbus, cache, stack.exec_client._clock)

        _push_account_summary(stack.exec_client)

        account = cache.account(stack.exec_client.account_id)
        assert account is not None, "CANARY: the summary push no longer reaches the account"
        assert str(stack.exec_client.account_id) == IB_ACCOUNT_KEY
        assert account.last_event.is_reported is True
        recorded = cash_recorded_at(account)
        assert recorded is not None and recorded.tzinfo is not None
