# NTrader Documentation Index

## Project Overview

- **Type:** Monolith (backend + web UI + CLI)
- **Language:** Python 3.11+
- **Architecture:** Layered monolith (FastAPI + Nautilus Trader + SQLAlchemy)
- **Entry Points:** CLI (`src/cli/main.py`), Web (`src/api/web.py`)

## Agent Documentation

AI agent implementation reference docs (progressive disclosure by topic):

| Work Area | Doc |
|-----------|-----|
| Architecture overview | [agent/architecture.md](agent/architecture.md) |
| Nautilus Trader | [agent/nautilus.md](agent/nautilus.md) |
| Data pipeline | [agent/data-pipeline.md](agent/data-pipeline.md) |
| Web UI patterns | [agent/web-ui.md](agent/web-ui.md) |
| Database & persistence | [agent/persistence.md](agent/persistence.md) |
| Testing | [agent/testing.md](agent/testing.md) |
| Conventions | [agent/conventions.md](agent/conventions.md) |

Auto-generated implementation rules: [`_bmad-output/project-context.md`](../_bmad-output/project-context.md)

## Product

- [PRD](product/PRD.md) — Product requirements (all implemented features, objectives, vNext)
- [Product Overview](product/PRODUCT_OVERVIEW.md) — Feature inventory, milestones, architecture

## Governance

- [Development Principles](governance/development-principles.md) — TDD, 80% coverage, performance targets, security

## Setup

- [IBKR Setup](setup/IBKR_SETUP.md) — Interactive Brokers TWS/Gateway configuration

## Web UI

- [Web UI Specification](webui/NTrader-webui-specification.md) — Page layouts, HTMX interactions, data models
- [Web UI Developer Guide](webui/NTrader_Web_UI–Developer_Guide.md) — FastAPI + Jinja2 + HTMX + TradingView

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

## Generated (Deep Scan Snapshots)

Point-in-time snapshots from automated project analysis:

- [Project Overview](generated/project-overview.md) — Purpose, capabilities, tech stack summary
- [Architecture](generated/architecture.md) — System design, data flow, deployment
- [Source Tree Analysis](generated/source-tree-analysis.md) — Annotated directory tree
- [API Contracts](generated/api-contracts.md) — REST endpoints, UI routes, CLI commands
- [Data Models](generated/data-models.md) — DB tables, Pydantic models, repository pattern
- [Development Guide](generated/development-guide.md) — Setup, testing, Docker, common tasks

## Archive

Historical artifacts from completed work — not implementation references:

- [Legacy Feature Specs](Archive/specs/) — speckit specs 001-013 (archived, features captured in PRD)
- [Other Archives](Archive/) — Completed work documentation
