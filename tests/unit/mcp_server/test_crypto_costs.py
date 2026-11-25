"""The research MCP's ``fee_tier``/``spread_bps``: the CLI's ``--fee-tier``/``--spread-bps``.

Same validation (one shared function, :mod:`src.services.crypto.cost_overrides`), same request
fields, same config hash: part of a run's identity only when set, so every hash computed
before these fields existed still names the same run.
"""

import math
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.mcp_server import worker
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, resolve
from src.mcp_server.studies.references import _with_crypto_costs
from src.mcp_server.tools import build_spec
from src.models.backtest_request import BacktestRequest
from src.services.crypto.cost_overrides import CryptoOverrideError, apply_crypto_overrides
from src.services.provenance import compute_config_hash

pytestmark = pytest.mark.unit

#: Config hashes of these requests computed on origin/main before the fields existed.
PRE_CHANGE_HASHES = {
    "etf_sma": "9ff36e3cc419c90db921c06f1b0c7d8468bf0a7b911029b850999fc7b1c014cd",
    "btc_sma": "b0158357f51571617aa5d9fa657285591b3a2dee6f2b0caa1a77893aba974df1",
    "btc_units": "596824aab757c95a71520f23b2fbc5c40291b96eeac63603479bbf7ea29ef969",
}
WINDOW = dict(start=date(2023, 1, 1), end=date(2024, 12, 31))
SPECS = {
    "etf_sma": dict(
        strategy="sma",
        symbol="QQQ",
        start=date(2000, 1, 1),
        end=date(2015, 12, 31),
        catalog="firstrate-etf",
        params={"fast_period": 5, "slow_period": 30},
    ),
    "btc_sma": dict(strategy="sma", symbol="BTC", catalog="firstrate-crypto", **WINDOW),
    "btc_units": dict(
        strategy="sma", symbol="BTC/USD", catalog="firstrate-crypto", units=0.25, **WINDOW
    ),
}


def _btc(**overrides) -> BacktestSpec:
    return BacktestSpec(**{**SPECS["btc_sma"], **overrides})


def _resolve(spec: BacktestSpec):
    return resolve(spec, default_catalog="")


class TestHashStability:
    @pytest.mark.parametrize("name", sorted(SPECS))
    def test_a_request_without_them_keeps_its_pre_change_hash(self, name):
        resolved = _resolve(BacktestSpec(**SPECS[name]))
        assert resolved.config_hash == PRE_CHANGE_HASHES[name]
        assert "fee_tier" not in resolved.summary() and "spread_bps" not in resolved.summary()

    def test_each_one_set_changes_the_hash(self):
        hashes = {
            _resolve(_btc()).config_hash,
            _resolve(_btc(fee_tier=4)).config_hash,
            _resolve(_btc(spread_bps=1)).config_hash,
            _resolve(_btc(fee_tier=4, spread_bps=1)).config_hash,
        }
        assert len(hashes) == 4

    def test_the_same_spread_spelled_as_int_or_float_is_the_same_run(self):
        assert _resolve(_btc(spread_bps=1)).config_hash == (
            _resolve(_btc(spread_bps=1.0)).config_hash
        )


class TestResolve:
    def test_they_reach_the_request_and_the_summary(self):
        resolved = _resolve(_btc(fee_tier=4, spread_bps=1.5))
        assert resolved.request.crypto_fee_tier == 4
        assert resolved.request.crypto_spread_bps == Decimal("1.5")
        summary = resolved.summary()
        assert summary["fee_tier"] == 4 and Decimal(str(summary["spread_bps"])) == Decimal("1.5")

    def test_a_zero_spread_is_a_setting_not_unset(self):
        assert _resolve(_btc(spread_bps=0)).request.crypto_spread_bps == Decimal("0")

    def test_they_match_the_cli_request_exactly(self):
        """The CLI applies the same function, so the MCP request is the CLI's request."""
        mcp = _resolve(_btc(fee_tier=4, spread_bps=1)).request
        plain = _resolve(_btc()).request
        cli = apply_crypto_overrides(plain, 4, 1.0, names=("--fee-tier", "--spread-bps"))
        assert (mcp.crypto_fee_tier, str(mcp.crypto_spread_bps)) == (
            cli.crypto_fee_tier,
            str(cli.crypto_spread_bps),
        )
        assert str(mcp.crypto_spread_bps) == "1.0"  # saved alike, not "1" beside the CLI's "1.0"

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("fee_tier", 0, "fee_tier must be a Kraken tier 1..17, got 0"),
            ("fee_tier", 18, "fee_tier must be a Kraken tier 1..17, got 18"),
            ("spread_bps", -0.5, "spread_bps must be a finite number of bps >= 0"),
            ("spread_bps", math.inf, "spread_bps must be a finite number of bps >= 0"),
            ("spread_bps", math.nan, "spread_bps must be a finite number of bps >= 0"),
        ],
    )
    def test_out_of_range_values_are_refused_naming_the_field(self, field, value, message):
        with pytest.raises(ToolFailure) as exc:
            _resolve(_btc(**{field: value}))
        assert exc.value.code == "invalid_params"
        assert message in exc.value.message

    @pytest.mark.parametrize("field", ["fee_tier", "spread_bps"])
    def test_a_non_crypto_symbol_is_refused(self, field):
        with pytest.raises(ToolFailure) as exc:
            _resolve(BacktestSpec(**SPECS["etf_sma"], **{field: 2}))
        assert exc.value.code == "invalid_params"
        assert f"{field} is for spot crypto only; 'QQQ'" in exc.value.message
        assert "BTC" in exc.value.message and "spot crypto symbol" in exc.value.fix

    @pytest.mark.parametrize(
        ("field", "bad"),
        [("fee_tier", True), ("fee_tier", 4.0), ("fee_tier", "4"), ("spread_bps", "1")],
    )
    def test_a_wrong_type_is_refused_not_coerced(self, field, bad):
        with pytest.raises(ToolFailure) as exc:
            build_spec(**SPECS["btc_sma"], **{field: bad})
        assert exc.value.code == "invalid_request" and field in exc.value.message


class TestSharedValidator:
    def _request(self, symbol: str = "BTC") -> BacktestRequest:
        return _resolve(_btc(symbol=symbol)).request

    def test_unset_returns_the_request_itself(self):
        request = self._request()
        assert apply_crypto_overrides(request, None, None) is request

    def test_the_cli_names_its_flags(self):
        with pytest.raises(CryptoOverrideError, match=r"^--fee-tier must be a Kraken tier"):
            apply_crypto_overrides(self._request(), 18, names=("--fee-tier", "--spread-bps"))
        with pytest.raises(CryptoOverrideError, match=r"^--spread-bps must be a finite"):
            apply_crypto_overrides(self._request(), None, -1, names=("--fee-tier", "--spread-bps"))

    @pytest.mark.parametrize("bad", [10**400, Decimal("sNaN"), Decimal("-1"), "x"])
    def test_an_unconvertible_spread_is_refused_not_raised(self, bad):
        with pytest.raises(CryptoOverrideError, match="finite number of bps"):
            apply_crypto_overrides(self._request(), None, bad)

    def test_negative_zero_is_the_zero_spread(self):
        zero = apply_crypto_overrides(self._request(), None, 0)
        negative = apply_crypto_overrides(self._request(), None, -0.0)
        assert str(negative.crypto_spread_bps) == str(zero.crypto_spread_bps) == "0.0"
        assert compute_config_hash(negative) == compute_config_hash(zero)

    def test_a_bool_is_not_a_tier(self):
        with pytest.raises(CryptoOverrideError, match="Kraken tier"):
            apply_crypto_overrides(self._request(), True)  # type: ignore[arg-type]


class TestWorker:
    """The worker rebuilds the pair at the run's tier and spread, as ``backtest run`` does."""

    @pytest.fixture
    def engine(self, monkeypatch):
        seen = {}

        class Orchestrator:
            async def execute(self, request, bars, instrument, **_):
                seen["instrument"] = instrument
                return None, "run-1"

            def dispose(self):
                pass

        monkeypatch.setattr("src.core.backtest_orchestrator.BacktestOrchestrator", Orchestrator)
        return seen

    def _data(self, instrument):
        return SimpleNamespace(instrument=instrument, bars=[], rate_sources=[])

    async def test_the_pair_charges_the_requested_terms(self, engine):
        from src.core.crypto_costs import crypto_terms
        from src.services.crypto.coin_specs import get_coin_spec
        from src.services.crypto.instrument_builder import build_crypto_pair

        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=2)
        resolved = _resolve(_btc(fee_tier=4, spread_bps=1))
        assert await worker._execute(resolved, self._data(pair)) == "run-1"
        terms = crypto_terms(engine["instrument"])
        assert terms.fee_tier == 4 and terms.spread_bps == Decimal("1")
        assert engine["instrument"].id == pair.id

    async def test_unset_passes_the_loaded_instrument_through(self, engine):
        from src.services.crypto.coin_specs import get_coin_spec
        from src.services.crypto.instrument_builder import build_crypto_pair

        pair = build_crypto_pair(get_coin_spec("BTC"))
        await worker._execute(_resolve(_btc()), self._data(pair))
        assert engine["instrument"] is pair

    async def test_a_loaded_non_crypto_instrument_is_refused_before_the_engine(self, engine):
        from nautilus_trader.test_kit.providers import TestInstrumentProvider

        equity = TestInstrumentProvider.equity("BTC", "XNAS")  # a BTC row that is no pair
        with pytest.raises(ToolFailure) as exc:
            await worker._execute(_resolve(_btc(fee_tier=4)), self._data(equity))
        assert exc.value.code == "invalid_params" and "spot crypto only" in exc.value.message
        assert "instrument" not in engine


class TestReproduction:
    """``reproduce_run`` recovers the overrides the stored hash says the run set."""

    SNAPSHOT_TERMS = {"BTC/USD.KRAKEN": {"typical_spread_bps": "1"}}

    @pytest.mark.parametrize(
        "given",
        [{}, {"fee_tier": 4}, {"spread_bps": 1}, {"fee_tier": 4, "spread_bps": 1}],
    )
    def test_the_spec_that_matches_the_stored_hash_is_rebuilt(self, given):
        original = _resolve(_btc(**given))
        snapshot = {
            "fee_tier": given.get("fee_tier", 2),
            "crypto_pairs": {
                "BTC/USD.KRAKEN": {"typical_spread_bps": str(given.get("spread_bps", "6"))}
            },
        }
        rebuilt = _with_crypto_costs(_btc(), snapshot, original.config_hash)
        assert _resolve(rebuilt).config_hash == original.config_hash

    def test_a_non_crypto_snapshot_is_left_alone(self):
        spec = BacktestSpec(**SPECS["etf_sma"])
        assert _with_crypto_costs(spec, {"config": {}}, "anything") is spec
