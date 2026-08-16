"""Throwaway: prove the new loop assertion discriminates. Deleted after use."""

import src.core.live_check_node as node_mod

node_mod.close_loop = lambda loop: None  # mutation: skip the loop-closing step

import src.core.live_check_driver as drv  # noqa: E402
from src.config import IBKRSettings  # noqa: E402
from src.core.live_node_builder import LiveNodeConfigError  # noqa: E402
from tests.component.doubles import TestBarObserver, TestLiveNode  # noqa: E402

BAR = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

settings = IBKRSettings(
    _env_file=None,
    ibkr_host="127.0.0.1",
    ibkr_port=7497,
    ibkr_client_id=1,
    ibkr_live_client_id=10,
    ibkr_trading_mode="paper",
    tws_account="DU4076626",
    ntrader_real_money_account="",
    ibkr_rate_limit=45,
    ibkr_market_data_lines=100,
)

calls = []
node = TestLiveNode(actors=[TestBarObserver([BAR])], raise_on_build=LiveNodeConfigError("boom"))


def factory(_settings, **kwargs):
    calls.append(kwargs)
    return node


drv.run_live_check(
    settings, bar_types=[BAR], observe_seconds=0.0, connect_timeout=2.0, node_factory=factory
)

closed = calls[0]["loop"].is_closed()
print("close_loop disabled -> loop.is_closed() =", closed)
print("ASSERTION VACUOUS" if closed else "ASSERTION DISCRIMINATES (would fail)")
