"""
Main FastAPI web application for NTrader UI.

Provides server-rendered HTML pages for browsing backtest results, viewing
dashboard statistics, and navigating the system.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.ui import dashboard, backtests
from src.api.rest import timeseries, trades, equity, indicators

app = FastAPI(
    title="NTrader Web UI",
    description="Web interface for NTrader backtesting system",
    version="0.1.0",
)

# Mount static files for CSS, JS, and vendor libraries
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Register UI routers
app.include_router(dashboard.router, tags=["ui"])
app.include_router(backtests.router, prefix="/backtests", tags=["ui"])

# Register REST API routers for chart data
app.include_router(timeseries.router, prefix="/api", tags=["charts"])
app.include_router(trades.router, prefix="/api", tags=["charts"])
app.include_router(equity.router, prefix="/api", tags=["charts"])
app.include_router(indicators.router, prefix="/api", tags=["charts"])
