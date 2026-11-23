"""QA fix (c): the worker re-checks ``units``/``contracts`` against the loaded instrument.

``resolve`` refuses them by symbol; an equity row named like a futures root (``CL``) only
shows itself once loaded, so the worker refuses it there, as ``backtest run`` does.
"""

from types import SimpleNamespace

import pytest

from src.mcp_server import worker
from src.mcp_server.errors import ToolFailure

pytestmark = pytest.mark.unit

EQUITY = SimpleNamespace(id="CL.NYSE", info={}, multiplier=1)


@pytest.mark.parametrize(
    ("config", "message"),
    [({"contracts": 1}, "futures only"), ({"units": 1000}, "FX only")],
)
async def test_a_size_override_on_the_wrong_instrument_fails_before_the_engine(
    config, message, monkeypatch
):
    monkeypatch.setattr(
        "src.core.backtest_orchestrator.BacktestOrchestrator",
        lambda: pytest.fail("the engine must not start"),
    )
    resolved = SimpleNamespace(request=SimpleNamespace(strategy_config=config))
    with pytest.raises(ToolFailure) as exc:
        await worker._execute(resolved, SimpleNamespace(instrument=EQUITY))
    assert exc.value.code == "invalid_params"
    assert message in exc.value.message
