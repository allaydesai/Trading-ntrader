"""A crypto run's tier, pair terms and annualisation survive the snapshot; other rows gain no key.

``BacktestPersistenceService`` stores ``StrategyConfigSnapshot(**snapshot).model_dump()``, so a
key the model does not declare is silently dropped (the futures lesson). Crypto M3 (task 3.7)
declares ``fee_tier``, ``crypto_pairs`` and ``periods_per_year`` (``account_currency`` is FX's,
shared), each omitted while unset so equity, futures and FX rows stay byte-identical. The exact
peak exposure follow-up adds ``crypto_peaks`` (the replayed peaks, saved instead of the series).
"""

import pytest

from src.models.config_snapshot import StrategyConfigSnapshot

pytestmark = pytest.mark.unit

BASE = dict(strategy_path="src.x:Y", config_path="config/x.yaml", config={"a": 1})
BTC = {
    "BTC/USD.KRAKEN": {
        "base": "BTC",
        "quote": "USD",
        "fee_tier": 2,
        "maker_pct": "0.30",
        "taker_pct": "0.60",
        "typical_spread_bps": "2",
        "costs_usd": {
            "commission": "120.57",
            "commission_maker": "0.00",
            "commission_taker": "120.57",
            "spread": "2.01",
        },
    }
}
CRYPTO_KEYS = ("fee_tier", "crypto_pairs", "periods_per_year", "crypto_peaks")


def test_a_non_crypto_snapshot_dumps_no_crypto_keys():
    dumped = StrategyConfigSnapshot(**BASE).model_dump()
    assert not set(CRYPTO_KEYS) & set(dumped)
    assert "account_currency" not in dumped


def test_a_crypto_snapshot_keeps_its_tier_pairs_and_annualisation():
    dumped = StrategyConfigSnapshot(
        **BASE, account_currency="USD", fee_tier=2, crypto_pairs=BTC, periods_per_year=365
    ).model_dump()
    assert dumped["account_currency"] == "USD"
    assert dumped["fee_tier"] == 2
    assert dumped["crypto_pairs"] == BTC
    assert dumped["periods_per_year"] == 365


def test_a_crypto_snapshot_keeps_its_saved_peaks():
    from src.services.crypto.exposure import PEAKS_KEY

    peaks = {"basis": "exact", "peak_notional_usd": "10000.0", "peak_exposure": "0.1038"}
    dumped = StrategyConfigSnapshot(**BASE, crypto_pairs=BTC, **{PEAKS_KEY: peaks}).model_dump()
    assert dumped[PEAKS_KEY] == peaks


def test_a_crypto_snapshot_without_peaks_dumps_no_peaks_key():
    assert "crypto_peaks" not in StrategyConfigSnapshot(**BASE, crypto_pairs=BTC).model_dump()
