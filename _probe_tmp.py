"""Throwaway probe: what structlog events does the driver emit? Deleted after use."""

import structlog
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_check_driver import run_live_check
from src.core.live_gate import GateDecision, GateMode
from tests.component.doubles import TestBarObserver, TestLiveNode

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

node = TestLiveNode(
    actors=[TestBarObserver([BAR], counts={BAR: 2})],
    instrument_ids=["AAPL.NASDAQ"],
    connects_after=1,
)


async def verifier(node_, settings_, **kwargs):
    return GateDecision(permitted=True, mode=GateMode.PAPER)


with capture_logs() as captured:
    report = run_live_check(
        settings,
        bar_types=[BAR],
        observe_seconds=0.0,
        connect_timeout=2.0,
        node_factory=lambda s, **kw: node,
        account_verifier=verifier,
    )

print("outcome:", report.outcome)
for entry in captured:
    print(entry)
print("---- structlog configured processors ----")
print(structlog.get_config()["processors"])
