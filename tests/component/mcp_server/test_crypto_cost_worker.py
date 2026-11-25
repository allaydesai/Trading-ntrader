"""The MCP worker rebuilds the loaded pair at a run's ``fee_tier``/``spread_bps``.

Component tier: real Nautilus instruments, an orchestrator double in place of the engine.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.mcp_server import worker
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, resolve

pytestmark = pytest.mark.component


def _btc(**overrides):
    spec = dict(
        strategy="sma",
        symbol="BTC",
        catalog="firstrate-crypto",
        start=date(2023, 1, 1),
        end=date(2024, 12, 31),
    )
    return BacktestSpec(**{**spec, **overrides})


def _resolve(spec: BacktestSpec):
    return resolve(spec, default_catalog="")


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
