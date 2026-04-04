Here’s a full developer guide you can hand to “future you” or another dev to build and maintain the UI.

I’ll assume the existing backend as per your docs:
	•	Python 3.11+
	•	FastAPI + Pydantic
	•	PostgreSQL (backtest metadata)
	•	Parquet catalog + Nautilus for market data
	•	CLI/services already implemented in src/core, src/services, src/db, etc.

⸻

NTrader Web UI – Developer Guide

1. Goals & Constraints

Goals
	•	Add a simple, maintainable web UI on top of the existing backtesting system.
	•	Support:
	•	Running backtests (from pre-defined configs).
	•	Browsing backtest history and details.
	•	Comparing runs.
	•	Viewing time series charts (price + indicators + trades + equity).
	•	Inspecting data catalog coverage.
	•	Stay aligned with:
	•	TDD, typed Python, clean architecture you’ve already got.
	•	“Most logic in Python”, minimal JS.

Constraints / Non-goals (for now)
	•	Single-user / dev-tool, not multi-tenant SaaS (no complex auth/roles initially).
	•	No heavy SPA: no React/Redux unless you decide to add it later.
	•	Charts must be good enough for trading analysis, but can be incremental (start with price + trades, then add more).

⸻

2. Tech Stack Overview

Backend
	•	FastAPI – already part of your stack; now used for:
	•	JSON APIs (for charts, runs, etc.).
	•	Server-rendered HTML routes (using Jinja2).
	•	Jinja2 – templating engine for HTML.
	•	SQLAlchemy + PostgreSQL – reuse existing metadata models for backtests/metrics.
	•	Existing services – reuse:
	•	Backtest history/query service.
	•	Parquet catalog/data services.
	•	Backtest runner/reproducer.

Frontend
	•	HTMX – adds interactivity without SPA:
	•	Table filtering/sorting/pagination via hx-get.
	•	“Run backtest” buttons, small inline updates.
	•	Tailwind CSS – utility-first CSS for layout and styling.
	•	Lightweight Charts (TradingView) – charting library for OHLC, overlays, trade markers, equity curves.
	•	Small vanilla JS/TS module – charts.ts compiled to charts.js:
	•	Fetches JSON from FastAPI endpoints.
	•	Renders charts inside designated <div>s.
	•	(Optional later): Alpine.js or htmx-hyperscript for micro-interactions.

⸻

3. High-Level Architecture

3.1 Conceptual diagram

Browser
  ├─ HTML pages (Jinja2)
  │    ▲
  │    │  (GET /backtests, /backtests/{id}, /data/view, /strategies…)
  │    │
  ├─ HTMX partial updates (HTML fragments)
  │    ▲
  │    │  (GET /backtests/fragment?sort=sharpe…)
  │    │
  └─ Chart JS (charts.js)
       ▲
       │  (GET /api/timeseries, /api/trades, /api/indicators, /api/equity)
       │
FastAPI (web app)
  ├─ UI routers (HTML + fragments)
  ├─ API routers (JSON)
  └─ Service layer (already exists)
       ├─ Metadata (PostgreSQL)
       ├─ Parquet catalog
       └─ Backtest engine

3.2 UI Feature Map

HTML pages (Jinja)
	•	/ – Dashboard:
	•	Summary metrics (count of runs, best Sharpe, etc.)
	•	Quick links: “Run backtest”, “View history”, “View data”.
	•	/backtests – Backtest history:
	•	Table with sort/filter/pagination using HTMX.
	•	/backtests/{run_id} – Backtest detail:
	•	Metrics, trades table, config snapshot.
	•	Embedded chart: price + trades + indicators + equity.
	•	/backtests/compare?run_ids=… – Comparison view:
	•	Table of selected runs, small equity sparklines.
	•	/data/view – Data catalog viewer:
	•	Pick symbol, timeframe, date range.
	•	Chart of raw OHLC + volume.
	•	/strategies (optional) – Strategy configs list.
	•	/strategies/{name} (optional) – Config view/edit.

JSON endpoints (for charts & dynamic data)
	•	/api/timeseries – OHLC + volume.
	•	/api/trades – per-run trades.
	•	/api/indicators – per-run indicator series.
	•	/api/equity – equity curve + drawdown.

⸻

4. Project Layout

Add a “web” slice on top of your existing src structure:

src/
├── api/
│   ├── __init__.py
│   ├── web.py             # mount web + api routers here
│   ├── ui/                # HTML routes & views
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── backtests.py
│   │   ├── data_view.py
│   │   └── strategies.py
│   └── rest/              # JSON-only API routers
│       ├── __init__.py
│       ├── timeseries.py
│       ├── indicators.py
│       ├── trades.py
│       └── equity.py
├── core/                  # existing business logic
├── services/              # existing services
├── db/                    # existing SQLAlchemy models
└── utils/

templates/
├── base.html
├── dashboard.html
├── backtests/
│   ├── list.html
│   ├── list_fragment.html   # HTMX partial
│   ├── detail.html
│   └── compare.html
├── data/
│   └── view.html
└── partials/
    ├── nav.html
    ├── metrics_panel.html
    └── tables.html

static/
├── css/
│   └── tailwind.css
├── js/
│   └── charts.js
└── vendor/
    ├── htmx.min.js
    └── lightweight-charts.standalone.production.js

Mount static + templates in your FastAPI startup.

⸻

5. Dependencies & Setup

5.1 Add dependencies (using uv)

# UI dependencies
uv add fastapi jinja2 python-multipart

# For chart JSON endpoints, you already have pydantic & friends
# For HTMX + Tailwind + Lightweight charts: frontend assets only (CDN or npm)

If you want to build Tailwind locally:

# Optional: if you want a Tailwind build pipeline
npm init -y
npm install tailwindcss postcss autoprefixer --save-dev
npx tailwindcss init


⸻

6. FastAPI Integration

6.1 Mounting the UI

In src/api/web.py (or main.py, depending on your entrypoint):

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .ui import dashboard, backtests, data_view, strategies
from .rest import timeseries, indicators, trades, equity

app = FastAPI()

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Attach routers
app.include_router(dashboard.router, prefix="", tags=["ui"])
app.include_router(backtests.router, prefix="/backtests", tags=["ui"])
app.include_router(data_view.router, prefix="/data", tags=["ui"])
app.include_router(strategies.router, prefix="/strategies", tags=["ui"])

app.include_router(timeseries.router, prefix="/api", tags=["api"])
app.include_router(indicators.router, prefix="/api", tags=["api"])
app.include_router(trades.router, prefix="/api", tags=["api"])
app.include_router(equity.router, prefix="/api", tags=["api"])

templates = Jinja2Templates(directory="templates")

Expose app in your usual entrypoint (uvicorn src.api.web:app or similar).

⸻

7. HTML Pages (Jinja2) + HTMX

7.1 Base Layout

templates/base.html (simplified sketch):

<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{% block title %}NTrader{% endblock %}</title>
  <link rel="stylesheet" href="/static/css/tailwind.css">
  <script src="/static/vendor/htmx.min.js" defer></script>
  <script src="/static/vendor/lightweight-charts.standalone.production.js" defer></script>
  <script src="/static/js/charts.js" defer></script>
</head>
<body class="min-h-screen bg-slate-950 text-slate-100">
  {% include "partials/nav.html" %}
  <main class="max-w-6xl mx-auto p-4">
    {% block content %}{% endblock %}
  </main>
</body>
</html>

7.2 Backtest List Page

Route: GET /backtests

templates/backtests/list.html:
	•	Top-level layout with controls.
	•	A <div> that HTMX will update with the table:

{% extends "base.html" %}
{% block title %}Backtests{% endblock %}

{% block content %}
  <h1 class="text-xl font-semibold mb-4">Backtest History</h1>

  <form
    hx-get="/backtests/fragment"
    hx-target="#backtest-table"
    hx-trigger="change delay:300ms, submit"
    class="flex gap-4 mb-4"
  >
    <!-- simple filters -->
    <select name="strategy" class="border rounded px-2 py-1">
      <option value="">All strategies</option>
      {% for s in strategies %}
        <option value="{{ s }}" {% if s == selected_strategy %}selected{% endif %}>
          {{ s }}
        </option>
      {% endfor %}
    </select>

    <select name="sort" class="border rounded px-2 py-1">
      <option value="created_at">Newest</option>
      <option value="return">Return</option>
      <option value="sharpe">Sharpe</option>
    </select>

    <button type="submit" class="px-3 py-1 bg-blue-600 rounded text-sm">
      Apply
    </button>
  </form>

  <div id="backtest-table">
    {% include "backtests/list_fragment.html" %}
  </div>
{% endblock %}

/backtests/fragment returns list_fragment.html with only the table; HTMX replaces #backtest-table.

⸻

8. Chart JSON APIs

You can keep these slim and reuse existing services.

8.1 /api/timeseries

src/api/rest/timeseries.py:

from fastapi import APIRouter, Depends, Query
from datetime import datetime
from pydantic import BaseModel
from typing import List

from src.services.data_service import get_ohlc_from_catalog

router = APIRouter()

class Candle(BaseModel):
  time: datetime
  open: float
  high: float
  low: float
  close: float
  volume: float

class TimeSeriesResponse(BaseModel):
  symbol: str
  timeframe: str
  candles: List[Candle]

@router.get("/timeseries", response_model=TimeSeriesResponse)
async def get_timeseries(
    symbol: str = Query(...),
    start: datetime = Query(...),
    end: datetime = Query(...),
    timeframe: str = Query("1_MIN"),
):
    # Service: Parquet/IBKR-backed loader
    candles = await get_ohlc_from_catalog(symbol, start, end, timeframe)
    return TimeSeriesResponse(
        symbol=symbol,
        timeframe=timeframe,
        candles=[
            Candle(
                time=c.timestamp,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
            )
            for c in candles
        ],
    )

8.2 /api/trades, /api/indicators, /api/equity

All follow the same pattern: thin API layer delegating to a service that already knows how to fetch trades and metrics from DB / engine.

You can define:
	•	TradePoint (time, side, price, qty, pnl)
	•	IndicatorSeries (name + list of {time, value})
	•	EquityPoint (time, value), DrawdownPoint (time, value)

⸻

9. Charts: Implementation Pattern

9.1 HTML hooks

On backtest detail page (/backtests/{run_id}):

<div
  id="run-price-chart"
  class="h-80"
  data-chart="run-price"
  data-run-id="{{ run.id }}"
  data-symbol="{{ run.instrument_symbol }}"
  data-start="{{ run.date_range_start.isoformat() }}"
  data-end="{{ run.date_range_end.isoformat() }}"
>
</div>

<div
  id="run-equity-chart"
  class="h-40 mt-4"
  data-chart="run-equity"
  data-run-id="{{ run.id }}"
>
</div>

On data viewer page:

<div
  id="data-view-chart"
  class="h-80"
  data-chart="data-view"
  data-symbol="{{ symbol }}"
  data-start="{{ start.isoformat() }}"
  data-end="{{ end.isoformat() }}"
  data-timeframe="{{ timeframe }}"
>
</div>

9.2 JS bootstrap (static/js/charts.js)

High-level shape (pseudo):

document.addEventListener("DOMContentLoaded", () => {
  const chartDivs = document.querySelectorAll("[data-chart]");
  chartDivs.forEach((el) => {
    const type = el.dataset.chart;
    if (type === "run-price") {
      initRunPriceChart(el);
    } else if (type === "run-equity") {
      initEquityChart(el);
    } else if (type === "data-view") {
      initDataViewChart(el);
    }
  });
});

async function initRunPriceChart(el) {
  const runId = el.dataset.runId;
  const symbol = el.dataset.symbol;
  const start = el.dataset.start;
  const end = el.dataset.end;

  // 1) fetch candles
  const tsResp = await fetch(
    `/api/timeseries?symbol=${symbol}&start=${start}&end=${end}&timeframe=1_MIN`
  ).then((r) => r.json());

  // 2) fetch indicators + trades
  const [indResp, tradesResp] = await Promise.all([
    fetch(`/api/indicators?run_id=${runId}`).then((r) => r.json()),
    fetch(`/api/trades?run_id=${runId}`).then((r) => r.json()),
  ]);

  // 3) create chart
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#020617" }, textColor: "#e5e7eb" },
    grid: {
      vertLines: { color: "#1e293b" },
      horzLines: { color: "#1e293b" },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
  });

  const candleSeries = chart.addCandlestickSeries();
  candleSeries.setData(
    tsResp.candles.map((c) => ({
      time: c.time, // or convert to unix
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))
  );

  // Add indicators as line series
  const smaFastSeries = chart.addLineSeries({ lineWidth: 1 });
  smaFastSeries.setData(
    indResp.indicators.sma_fast.map((p) => ({ time: p.time, value: p.value }))
  );

  const smaSlowSeries = chart.addLineSeries({ lineWidth: 1 });
  smaSlowSeries.setData(
    indResp.indicators.sma_slow.map((p) => ({ time: p.time, value: p.value }))
  );

  // Add trade markers
  const markers = tradesResp.trades.map((t) => ({
    time: t.time,
    position: t.side === "buy" ? "belowBar" : "aboveBar",
    color: t.side === "buy" ? "#22c55e" : "#ef4444",
    shape: t.side === "buy" ? "arrowUp" : "arrowDown",
    text: `${t.side === "buy" ? "B" : "S"} @ ${t.price}`,
  }));
  candleSeries.setMarkers(markers);
}

async function initEquityChart(el) {
  const runId = el.dataset.runId;
  const resp = await fetch(`/api/equity?run_id=${runId}`).then((r) => r.json());
  const chart = LightweightCharts.createChart(el, {
    layout: { background: { color: "#020617" }, textColor: "#e5e7eb" },
    grid: { vertLines: { color: "#0f172a" }, horzLines: { color: "#0f172a" } },
  });
  const equitySeries = chart.addLineSeries();
  equitySeries.setData(
    resp.equity.map((p) => ({ time: p.time, value: p.value }))
  );
}

Note: You can keep configuration DRY with helper functions, but this is the general shape.

9.3 Handling HTMX swaps

If you replace chart containers via HTMX (e.g., changing symbol/date), your DOMContentLoaded handler won’t run again. Easiest solution:
	•	Listen to htmx events, e.g.:

document.body.addEventListener("htmx:afterSwap", (evt) => {
  const el = evt.target;
  const chartDivs = el.querySelectorAll("[data-chart]");
  chartDivs.forEach((child) => {
    const type = child.dataset.chart;
    if (type === "run-price") initRunPriceChart(child);
    if (type === "run-equity") initEquityChart(child);
    if (type === "data-view") initDataViewChart(child);
  });
});


⸻

10. HTMX Patterns

Key patterns you’ll use:
	1.	Filtering tables

<form
  hx-get="/backtests/fragment"
  hx-target="#backtest-table"
  hx-push-url="true"
  hx-trigger="change delay:300ms, submit"
>
  <!-- filters -->
</form>

	2.	Sorting by clicking table headers

<th
  hx-get="/backtests/fragment?sort=sharpe"
  hx-target="#backtest-table"
  hx-swap="outerHTML"
  class="cursor-pointer"
>
  Sharpe
</th>

	3.	Inline “re-run” button

<button
  hx-post="/backtests/{{ run.id }}/rerun"
  hx-target="#run-{{ run.id }}-status"
  hx-swap="outerHTML"
  class="text-xs px-2 py-1 border rounded"
>
  Re-run
</button>

<td id="run-{{ run.id }}-status">
  {{ run.status }}
</td>

Back-end returns small HTML snippet for the status cell.

⸻

11. Testing Strategy

11.1 API & HTML routes (pytest + httpx)
	•	Use FastAPI’s TestClient or httpx.AsyncClient.
	•	Tests for:
	•	/backtests returns 200 and includes expected HTML markers.
	•	/api/timeseries returns valid JSON given known sample data.
	•	/api/trades, /api/indicators, /api/equity behave correctly with seeded DB data.

Example:

from httpx import AsyncClient
import pytest
from src.api.web import app

@pytest.mark.asyncio
async def test_backtests_list_renders(client: AsyncClient):
    resp = await client.get("/backtests")
    assert resp.status_code == 200
    assert "Backtest History" in resp.text

11.2 E2E / UI checks (optional but nice)
	•	Use Playwright (Python) for very small smoke tests:
	•	Open /backtests, apply filter, ensure table updates.
	•	Open a run detail, verify chart container exists and calls APIs (you can intercept requests or just check the API endpoints directly in separate tests).

⸻

12. Deployment & Config
	•	UI runs on the same FastAPI app as your REST API.
	•	Serve static files via FastAPI’s StaticFiles in dev; behind nginx or similar in prod if desired.
	•	No special environment variables beyond what you already have (DB, IBKR, etc.).
	•	Frontend assets:
	•	HTMX + Lightweight Charts via local static/vendor or CDN.
	•	Tailwind:
	•	Start simple with a prebuilt CSS (e.g., compiled once and committed).
	•	If you add a build step, document it in README.md.

⸻

13. Implementation Order (Recommended)
	1.	Scaffold UI skeleton
	•	base.html, nav, /backtests simple list (no HTMX).
	2.	Wire to metadata
	•	Use existing DB services to populate backtest table.
	3.	Add HTMX for filtering & sorting
	•	/backtests/fragment, forms, headers.
	4.	Implement /backtests/{run_id} detail
	•	Metrics and trades table.
	5.	Implement chart APIs
	•	/api/timeseries, /api/trades, /api/indicators, /api/equity.
	6.	Add Lightweight Charts integration
	•	Run detail (price + equity).
	7.	Add Data Viewer page
	•	/data/view with OHLC chart from Parquet.
	8.	Polish
	•	Tailwind layout, a couple of small convenience buttons.
	•	Optional comparison view.