---
name: web-ui-development
description: >
  Use when adding web UI pages, API endpoints, chart routes, or working with HTMX/Jinja2
  templates. Covers the FastAPI app structure, router registration, HTMX patterns, and
  Tailwind CSS build process.
---

# Web UI Development Guide

## Architecture

```
src/api/
├── web.py              # FastAPI app, static files, template init, router registration
├── dependencies.py     # Dependency injection (get_session, get_service, etc.)
├── models/             # Pydantic response models for REST API
├── rest/               # JSON API endpoints (charts, data)
│   ├── equity.py       # Equity curve data
│   ├── indicators.py   # Indicator data
│   ├── timeseries.py   # Time series data
│   └── trades.py       # Trade data
└── ui/                 # Server-rendered HTML pages (HTMX)
    ├── backtests.py    # Backtest list and detail views
    └── dashboard.py    # Dashboard/home page

templates/              # Jinja2 HTML templates
static/                 # CSS, JS, vendor libs
```

## App Setup (`src/api/web.py`)

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="NTrader Web UI")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# UI routers (return HTML)
app.include_router(dashboard.router, tags=["ui"])
app.include_router(backtests.router, prefix="/backtests", tags=["ui"])

# REST routers (return JSON, for charts)
app.include_router(timeseries.router, prefix="/api", tags=["charts"])
app.include_router(trades.router, prefix="/api", tags=["charts"])
app.include_router(equity.router, prefix="/api", tags=["charts"])
app.include_router(indicators.router, prefix="/api", tags=["charts"])
```

## Adding a New UI Page

1. Create route file in `src/api/ui/<name>.py`:
```python
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/<path>")
async def page_name(request: Request):
    # Fetch data...
    return templates.TemplateResponse(
        "<template>.html",
        {"request": request, "data": data},
    )
```

2. Register in `src/api/web.py`:
```python
from src.api.ui import new_module
app.include_router(new_module.router, prefix="/new-path", tags=["ui"])
```

3. Create template in `templates/<template>.html`

## Adding a New REST Endpoint

1. Create route file in `src/api/rest/<name>.py`:
```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/endpoint")
async def get_data():
    return {"key": "value"}
```

2. Create response model in `src/api/models/`:
```python
from pydantic import BaseModel
class MyResponse(BaseModel):
    field: str
```

3. Register in `src/api/web.py`:
```python
from src.api.rest import new_module
app.include_router(new_module.router, prefix="/api", tags=["charts"])
```

## HTMX Pattern

Server returns HTML fragments, client swaps DOM:

```html
<!-- In template: button triggers HTMX request -->
<button hx-get="/backtests/list"
        hx-target="#results"
        hx-swap="innerHTML">
    Load Results
</button>
<div id="results"></div>
```

Server endpoint returns HTML fragment (not full page):
```python
@router.get("/list")
async def backtest_list(request: Request):
    return templates.TemplateResponse(
        "partials/backtest_list.html",
        {"request": request, "backtests": backtests},
    )
```

## Dependency Injection

```python
from src.api.dependencies import get_session, get_service

@router.get("/endpoint")
async def endpoint(session=Depends(get_session)):
    ...
```

## Tailwind CSS

Must build CSS before first run:
```bash
./scripts/build-css.sh
```

Rebuild after template changes that use new Tailwind classes.

## Running the Web UI

```bash
uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port 8000
```

## Deeper Reference

See `agent_docs/architecture.md` Web Stack section for:
- Full template hierarchy
- Static asset organization
- Chart library integration (Lightweight Charts)
- Database session management
