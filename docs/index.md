# NTrader Documentation Index

## Project Overview

- **Type:** Monolith (backend + web UI + CLI)
- **Language:** Python 3.11+
- **Architecture:** Layered monolith (FastAPI + Nautilus Trader + SQLAlchemy)
- **Entry Points:** CLI (`src/cli/main.py`), Web (`src/api/web.py`)

## Generated Documentation

- [Project Overview](project-overview.md) — Purpose, capabilities, tech stack summary, repo structure
- [Architecture](architecture.md) — System design, data flow, deployment, key design decisions
- [Source Tree Analysis](source-tree-analysis.md) — Annotated directory tree (~222 Python files)
- [API Contracts](api-contracts.md) — 9 REST endpoints, 11 UI routes, 13 CLI commands
- [Data Models](data-models.md) — 3 DB tables, ~30 Pydantic models, repository pattern
- [Development Guide](development-guide.md) — Setup, testing, Docker, common tasks

## Product

- [PRD](product/PRD.md) — Product requirements (MVP scope, objectives, milestones)
- [Product Overview](product/PRODUCT_OVERVIEW.md) — Feature inventory, roadmap, architecture

## Setup

- [IBKR Setup](setup/IBKR_SETUP.md) — Interactive Brokers TWS/Gateway configuration

## Web UI

- [Web UI Specification](webui/NTrader-webui-specification.md) — Page layouts, HTMX interactions, data models
- [Web UI Developer Guide](webui/NTrader_Web_UI–Developer_Guide.md) — FastAPI + Jinja2 + HTMX + TradingView

## Governance

- [Development Principles](governance/development-principles.md) — TDD, 80% coverage, performance targets, security

## Testing

- [Testing Philosophy](testing/testing-philosophy-trading-engine.md) — Test pyramid, pure logic extraction, component doubles
- [C Extension Testing Patterns](testing/popular-patterns-for-cextension-testing.md) — Process isolation, pytest-forked, segfault handling

## QA

- [Comprehensive Test Plan](qa/comprehensive-test-plan.md) — Master QA plan (500+ test cases)
- [Data Validation Summary](qa/data-validation-summary.md) — 10/10 metric validations
- [TC-DATA-001: Sharpe Ratio](qa/TC-DATA-001-sharpe-ratio-validation.md) — Sharpe ratio validation
- [TC-DATA-002: Max Drawdown](qa/TC-DATA-002-max-drawdown-validation.md) — Max drawdown validation

## Guides

- [Git Worktrees with Claude Code](guides/claude-code-git-worktrees-guide.md) — Parallel development workflow

## Archive

Historical artifacts from completed work — see [archive/](archive/).

---

## Agent Documentation

Agent-consumable reference docs (progressive disclosure by topic):

| Work Area | Agent Doc |
|-----------|-----------|
| Architecture overview | [`agent_docs/architecture.md`](../agent_docs/architecture.md) |
| Nautilus Trader | [`agent_docs/nautilus.md`](../agent_docs/nautilus.md) |
| Data pipeline | [`agent_docs/data-pipeline.md`](../agent_docs/data-pipeline.md) |
| Web UI patterns | [`agent_docs/web-ui.md`](../agent_docs/web-ui.md) |
| Database & persistence | [`agent_docs/persistence.md`](../agent_docs/persistence.md) |
| Testing | [`agent_docs/testing.md`](../agent_docs/testing.md) |
| Conventions | [`agent_docs/conventions.md`](../agent_docs/conventions.md) |

Auto-generated implementation rules: [`_bmad-output/project-context.md`](../_bmad-output/project-context.md)
