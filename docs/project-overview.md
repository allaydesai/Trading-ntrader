# NTrader — Project Overview

## Purpose

Production-grade algorithmic trading backtesting system. Execute, analyze, and compare backtests using historical market data from Interactive Brokers, Kraken, or local files.

## Key Capabilities

- **Multi-source data**: IBKR, Kraken, CSV import, mock data generation
- **Strategy framework**: Pluggable strategies with `@register_strategy`, parameter validation, alias resolution
- **Backtest execution**: Nautilus Trader engine with realistic fill/fee models
- **Results persistence**: PostgreSQL with full configuration snapshots for reproducibility
- **Web UI**: Dashboard, filtered backtest lists, detail views with interactive charts
- **CLI**: Run backtests, import data, compare results, export reports
- **Reproducibility**: Any past backtest can be re-executed from its stored config

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Trading Engine | Nautilus Trader (C/Rust extensions) |
| Web | FastAPI + Jinja2 + HTMX |
| Charts | TradingView Lightweight Charts v5.0.0 |
| Database | PostgreSQL 16 + TimescaleDB |
| ORM | SQLAlchemy 2.0 (async + sync) |
| CLI | Click |
| Package Manager | UV |
| Infrastructure | Docker Compose |

## Architecture

Layered monolith: Presentation (Web/CLI) → Services → Core Logic → Data Layer → PostgreSQL

Data flow: IBKR/Kraken/CSV → Parquet catalog → BacktestEngine → Results DB → Web UI/Reports

## Repository Structure

- `src/` — Application code (api, cli, core, db, models, services, utils)
- `tests/` — Test suite (unit, component, integration, e2e, api, ui, db)
- `templates/` — Jinja2 HTML templates
- `static/` — CSS, JavaScript, vendor libs
- `configs/` — Strategy YAML examples
- `specs/` — Feature specifications (001-013)
- `docs/` — Project documentation
- `agent_docs/` — AI agent reference docs (progressive disclosure)
- `alembic/` — Database migrations

## Documentation Map

| Document | What It Covers |
|----------|---------------|
| [Architecture](architecture.md) | System design, data flow, deployment |
| [API Contracts](api-contracts.md) | REST endpoints, UI routes, CLI commands |
| [Data Models](data-models.md) | DB schema, Pydantic models, repositories |
| [Source Tree](source-tree-analysis.md) | Annotated directory structure |
| [Development Guide](development-guide.md) | Setup, testing, common tasks |
| [Product/PRD](product/PRD.md) | Product requirements |
| [IBKR Setup](setup/IBKR_SETUP.md) | Interactive Brokers configuration |
| [Web UI Spec](webui/NTrader-webui-specification.md) | UI specification |
| [Development Principles](governance/development-principles.md) | TDD, security, performance targets |

## Status

- **Version**: 0.1.0
- **13 feature specs** completed (001-013)
- **~222 Python files** (113 src, 109 tests)
- **4 database migrations** applied
- **2 built-in strategies**: SMA Crossover, SMA Momentum
- **Custom strategies**: Git submodule (`src/core/strategies/custom/`)
