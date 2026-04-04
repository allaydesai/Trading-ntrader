# NTrader Documentation

## Product

- [PRD](product/PRD.md) — Product requirements document (MVP scope, objectives, milestones)
- [Product Overview](product/PRODUCT_OVERVIEW.md) — Feature inventory, roadmap status, architecture overview

## Setup

- [IBKR Setup](setup/IBKR_SETUP.md) — Interactive Brokers TWS configuration, API setup, troubleshooting

## Web UI

- [Web UI Specification](webui/NTrader-webui-specification.md) — Detailed page layouts, API endpoints, HTMX interactions, data models
- [Web UI Developer Guide](webui/NTrader_Web_UI–Developer_Guide.md) — FastAPI + Jinja2 + HTMX + TradingView architecture

## Governance

- [Development Principles](governance/development-principles.md) — TDD enforcement, 80% coverage, performance targets, security requirements

## Testing

- [Testing Philosophy](testing/testing-philosophy-trading-engine.md) — Test pyramid, pure logic extraction, component test doubles
- [C Extension Testing Patterns](testing/popular-patterns-for-cextension-testing.md) — Process isolation, pytest-forked, handling segfaults

## QA

- [Comprehensive Test Plan](qa/comprehensive-test-plan.md) — Master QA plan with 500+ test cases
- [Data Validation Summary](qa/data-validation-summary.md) — 10/10 metric validation results
- [TC-DATA-001: Sharpe Ratio](qa/TC-DATA-001-sharpe-ratio-validation.md) — Sharpe ratio calculation validation
- [TC-DATA-002: Max Drawdown](qa/TC-DATA-002-max-drawdown-validation.md) — Max drawdown calculation validation

## Guides

- [Git Worktrees with Claude Code](guides/claude-code-git-worktrees-guide.md) — Parallel development workflow

## Archive

Historical artifacts from completed work — see [archive/](archive/).

---

## Agent Documentation

Agent-consumable reference docs live in [`agent_docs/`](../agent_docs/) (architecture, nautilus, testing, conventions).
Auto-generated implementation rules live in [`_bmad-output/project-context.md`](../_bmad-output/project-context.md).
