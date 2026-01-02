# Implementation Plan: Multi-Condition Signal Validation

**Branch**: `011-signal-validation` | **Date**: 2025-12-27 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/011-signal-validation/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Build a multi-condition signal validation system for the Nautilus Trader backtesting framework that:
1. Enables defining individual signal conditions (SignalComponent) with evaluation logic
2. Combines conditions into composite signals with AND/OR logic
3. Captures complete audit trail during backtest execution
4. Provides post-backtest analysis: trigger rates, blocking conditions, near-miss identification
5. Exports evaluation history to CSV for offline analysis

## Technical Context

**Language/Version**: Python 3.11+ (consistent with existing codebase)
**Primary Dependencies**: Nautilus Trader (event-driven backtest engine), FastAPI 0.109+, Pydantic 2.5+, SQLAlchemy 2.0 (async), structlog
**Storage**: PostgreSQL 16+ (existing `backtest_runs` and `trades` tables), Parquet (market data via Nautilus catalog)
**Testing**: pytest with pytest-asyncio, minimum 80% coverage (TDD mandatory per constitution)
**Target Platform**: Linux server (Docker containerization available)
**Project Type**: Single project (extends existing `src/` structure)
**Performance Goals**: Evaluate 10+ conditions per bar without degradation (<1ms overhead per bar); handle 100,000+ bar backtests
**Constraints**: Memory-bounded audit storage (periodic flush for long backtests); <500MB typical workload
**Scale/Scope**: Up to 10 conditions per composite signal; backtests with millions of bars supported via chunked export

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Requirement | Status |
|-----------|------------------|--------|
| **I. Simplicity First (KISS/YAGNI)** | Start with simplest working solution; files <500 lines; avoid clever code | ✅ PASS - Component-based design is minimal and composable |
| **II. Test-Driven Development** | Tests BEFORE implementation; Red-Green-Refactor; 80% coverage | ✅ PASS - Will follow TDD; tests defined in acceptance scenarios |
| **III. FastAPI-First Architecture** | APIs with Pydantic; OpenAPI docs; async/await for I/O | ✅ PASS - Export and analysis endpoints will use FastAPI |
| **IV. Type Safety & Documentation** | Type hints (PEP 484); Google-style docstrings; mypy passing | ✅ PASS - All signal models fully typed |
| **V. Dependency Discipline** | UV only for packages; no direct pyproject.toml edits | ✅ PASS - No new dependencies required (uses existing stack) |
| **VI. Fail Fast & Observable** | Early error checking; structured logging with structlog | ✅ PASS - Config validation before backtest; signal evaluation logging |
| **VII. DRY & Modular Design** | Functions <50 lines; classes <100 lines; single purpose | ✅ PASS - Each SignalComponent is isolated and reusable |

**Pre-Design Gate Status**: ✅ ALL GATES PASS - Proceed to Phase 0

## Project Structure

### Documentation (this feature)

```text
specs/011-signal-validation/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
│   └── openapi.yaml     # Signal evaluation API contracts
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/
├── core/
│   ├── signals/                    # NEW: Signal validation module
│   │   ├── __init__.py
│   │   ├── components.py           # SignalComponent base + concrete implementations
│   │   ├── composite.py            # CompositeSignalGenerator (AND/OR logic)
│   │   ├── collector.py            # SignalCollector (audit trail during backtest)
│   │   ├── evaluation.py           # SignalEvaluation dataclass
│   │   └── analysis.py             # Post-backtest statistics (trigger rates, blocking analysis)
│   └── strategies/                 # EXISTING: Strategy implementations
│       └── *.py                    # Will be modified to use signal components
├── db/
│   └── models/
│       └── signal_evaluation.py    # NEW: ORM model for persisted evaluations
├── models/
│   └── signal.py                   # NEW: Pydantic models for signal components/evaluations
├── services/
│   └── signal_export.py            # NEW: CSV export service
└── api/
    └── rest/
        └── signals.py              # NEW: API endpoints for signal analysis

tests/
├── unit/
│   └── signals/                    # NEW: Unit tests for signal module
│       ├── test_components.py
│       ├── test_composite.py
│       ├── test_collector.py
│       └── test_analysis.py
└── integration/
    └── test_signal_backtest.py     # NEW: Integration with backtest engine
```

**Structure Decision**: Single project extending existing `src/` structure. New `src/core/signals/` module contains all signal validation logic, following existing patterns from `src/core/strategies/`. Tests mirror source structure in `tests/unit/signals/`.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations - all constitution gates pass. Design follows KISS principles:
- Simple component-based architecture (one class per condition type)
- No complex inheritance hierarchies (composition over inheritance)
- Database storage only for export, in-memory during backtest for performance

## Post-Design Constitution Re-Check

*Re-evaluation after Phase 1 design completion.*

| Principle | Design Artifact | Status |
|-----------|----------------|--------|
| **I. KISS/YAGNI** | data-model.md: 4 core entities, no extra features | ✅ PASS |
| **II. TDD** | quickstart.md includes test patterns | ✅ PASS |
| **III. FastAPI-First** | contracts/openapi.yaml: Full REST API spec | ✅ PASS |
| **IV. Type Safety** | data-model.md: All Pydantic models typed | ✅ PASS |
| **V. Dependency Discipline** | research.md: No new deps required | ✅ PASS |
| **VI. Fail Fast** | data-model.md: Validation invariants defined | ✅ PASS |
| **VII. DRY/Modular** | Project structure: 5 focused files in signals/ | ✅ PASS |

**Post-Design Gate Status**: ✅ ALL GATES PASS - Ready for Phase 2 task generation

## Generated Artifacts

| Artifact | Path | Description |
|----------|------|-------------|
| Research | `specs/011-signal-validation/research.md` | Integration patterns, design decisions |
| Data Model | `specs/011-signal-validation/data-model.md` | Entity definitions, DB schema, Pydantic models |
| API Contracts | `specs/011-signal-validation/contracts/openapi.yaml` | OpenAPI 3.1 spec for signal analysis endpoints |
| Quickstart | `specs/011-signal-validation/quickstart.md` | Usage examples and integration guide |

## Next Steps

1. **Run `/speckit.tasks`** to generate implementation tasks
2. **Start TDD cycle** with failing tests for core entities
3. **Implement in priority order**: P1 (evaluation) → P2 (audit trail) → P3 (analysis)
