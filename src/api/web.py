"""
Main FastAPI web application for NTrader UI.

Provides server-rendered HTML pages for browsing backtest results, viewing
dashboard statistics, and navigating the system.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from nautilus_trader.common.component import init_logging, is_logging_initialized

from src.api.rest import equity, indicators, timeseries, trades
from src.api.rest import explorer as explorer_api
from src.api.ui import backtests, dashboard, explorer
from src.utils.logging import set_nautilus_log_guard


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Claim the Nautilus logging subsystem once, for the server's lifetime.

    Pre-initializing prevents "Logging subsystem already initialized" errors when
    the process later runs multiple backtests — each ``BacktestEngine`` /
    ``HistoricInteractiveBrokersClient`` would otherwise try to re-initialize.

    Deliberately in the lifespan and **not at module import time**. Import-time
    initialization made the integration tier nondeterministic: under
    ``pytest -n auto --forked``, module imports run once in the persistent xdist
    worker during *collection*, before any per-test fork, and ``--forked``
    isolates only the test call. A worker that had collected any
    ``from src.api.web import app`` test then handed every later forked child a
    process image with ``is_logging_initialized()`` already ``True`` — while the
    native background threads the subsystem needs do not survive ``fork()``.
    Tests that built a real ``BacktestEngine`` or ``TradingNode`` afterwards died
    with ``SIGTRAP``, and *which* tests died depended on how xdist happened to
    distribute modules across workers in that particular run — so the failing set
    changed between identical runs. Lifespan runs in the serving process only, so
    importing the app — which is all a test collector does — is inert.

    The guard matters: re-initializing panics in Rust, and a caller that legitimately
    claimed logging first (a backtest run in-process, per ``set_nautilus_log_guard``'s
    first-wins contract) must not be overwritten.
    """
    if not is_logging_initialized():
        set_nautilus_log_guard(init_logging())
    yield


app = FastAPI(
    title="NTrader Web UI",
    description="Web interface for NTrader backtesting system",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files for CSS, JS, and vendor libraries
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Register UI routers
app.include_router(dashboard.router, tags=["ui"])
app.include_router(explorer.router, prefix="/explorer", tags=["ui"])
app.include_router(backtests.router, prefix="/backtests", tags=["ui"])

# Register REST API routers for chart data
app.include_router(explorer_api.router, prefix="/api", tags=["explorer-api"])
app.include_router(timeseries.router, prefix="/api", tags=["charts"])
app.include_router(trades.router, prefix="/api", tags=["charts"])
app.include_router(equity.router, prefix="/api", tags=["charts"])
app.include_router(indicators.router, prefix="/api", tags=["charts"])
