# Implementation Plan: Chart APIs

**Branch**: `008-chart-apis` | **Date**: 2025-11-19 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/008-chart-apis/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Implement four JSON API endpoints for TradingView Lightweight Charts: OHLCV time series from Parquet files via DataCatalogService, trade markers from PostgreSQL backtest metadata, equity curves with drawdown calculations, and indicator series overlays. APIs follow existing FastAPI patterns with Pydantic validation and return JSON optimized for direct TradingView consumption.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI 0.109+, Pydantic 2.5+, SQLAlchemy 2.0 (async), structlog
**Storage**: PostgreSQL 16+ (backtest metadata), Parquet files (OHLCV market data)
**Testing**: pytest with pytest-asyncio, 80% minimum coverage
**Target Platform**: Linux server (local development macOS)
**Project Type**: Web application (existing backend API)
**Performance Goals**: Time series <500ms (100k candles), Trades/Equity <200ms, Indicators <300ms
**Constraints**: API response <500ms p95, TradingView-compatible JSON format
**Scale/Scope**: Single-user local development, ~4 new endpoints, reuses existing services

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Requirement | Status | Notes |
|-----------|-------------|--------|-------|
| **I. Simplicity (KISS/YAGNI)** | Simplest working solution, no speculative features | ✅ PASS | Reuses existing DataCatalogService and BacktestQueryService |
| **II. TDD (NON-NEGOTIABLE)** | Tests before implementation, 80% coverage | ✅ PASS | Will follow Red-Green-Refactor for each endpoint |
| **III. FastAPI-First** | Pydantic validation, async I/O, dependency injection | ✅ PASS | Follows existing patterns in src/api/dependencies.py |
| **IV. Type Safety** | Type hints, Google docstrings, mypy passes | ✅ PASS | All endpoints will have full type annotations |
| **V. Dependency Discipline** | UV only, pinned versions, no direct pyproject edits | ✅ PASS | No new dependencies required |
| **VI. Fail Fast** | Early validation, structured logging | ✅ PASS | Validates request params, uses structlog |
| **VII. DRY & Modular** | Functions <50 lines, classes <100 lines, files <500 lines | ✅ PASS | 4 small endpoint files, shared models |

**Gate Result**: ✅ PASS - No violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/008-chart-apis/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── api/
│   ├── rest/                    # NEW: JSON API endpoints for charts
│   │   ├── __init__.py
│   │   ├── timeseries.py        # GET /api/timeseries - OHLCV data
│   │   ├── trades.py            # GET /api/trades/{run_id} - trade markers
│   │   ├── equity.py            # GET /api/equity/{run_id} - equity curve
│   │   └── indicators.py        # GET /api/indicators/{run_id} - indicator series
│   ├── models/
│   │   ├── chart_timeseries.py  # NEW: Pydantic models for OHLCV response
│   │   ├── chart_trades.py      # NEW: Pydantic models for trades response
│   │   ├── chart_equity.py      # NEW: Pydantic models for equity response
│   │   └── chart_indicators.py  # NEW: Pydantic models for indicators response
│   ├── dependencies.py          # EXTEND: Add data catalog service dependency
│   ├── ui/                      # EXISTING: HTML endpoints
│   └── web.py                   # EXTEND: Include rest/ routers
├── services/
│   ├── data_catalog.py          # REUSE: query_bars() for OHLCV
│   └── backtest_query.py        # REUSE: get_backtest_by_id() for metadata
└── models/
    └── trade.py                 # REUSE: TradeModel for trade markers

tests/
├── api/
│   └── rest/                    # NEW: Tests for JSON API endpoints
│       ├── test_timeseries.py
│       ├── test_trades.py
│       ├── test_equity.py
│       └── test_indicators.py
└── conftest.py                  # EXTEND: Add fixtures for chart API tests
```

**Structure Decision**: Extending existing single-project structure per Developer Guide. New JSON API endpoints go in `src/api/rest/` (separate from HTML UI in `src/api/ui/`). Response models in `src/api/models/chart_*.py`. Reuses existing DataCatalogService and BacktestQueryService.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. The design:
- Reuses existing services (DataCatalogService, BacktestQueryService)
- Adds minimal new code (4 endpoint files, 4 model files)
- Follows established patterns from Developer Guide
- No new dependencies required

---

## Post-Design Constitution Re-Check

| Principle | Status | Verification |
|-----------|--------|--------------|
| **I. Simplicity** | ✅ PASS | 4 endpoints, reuses existing services, ~50 lines per file |
| **II. TDD** | ✅ READY | Test structure defined in tests/api/rest/ |
| **III. FastAPI-First** | ✅ PASS | Follows patterns in src/api/ui/backtests.py |
| **IV. Type Safety** | ✅ PASS | All models have full type hints |
| **V. Dependencies** | ✅ PASS | No new dependencies needed |
| **VI. Fail Fast** | ✅ PASS | Pydantic validation + HTTPException pattern |
| **VII. Modular** | ✅ PASS | Each endpoint <100 lines estimated |

**Final Gate Result**: ✅ PASS - Ready for task generation
