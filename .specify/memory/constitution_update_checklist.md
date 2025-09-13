# Constitution Update Checklist

When amending the constitution (`/memory/constitution.md`), ensure all dependent documents are updated to maintain consistency.

## Templates to Update

### When adding/modifying ANY article:
- [x] `.specify/templates/plan-template.md` - Update Constitution Check section
- [x] `.specify/templates/spec-template.md` - Update if requirements/scope affected
- [x] `.specify/templates/tasks-template.md` - Update if new task types needed
- [x] `.claude/commands/plan.md` - Update if planning process changes
- [x] `.claude/commands/tasks.md` - Update if task generation affected
- [x] `CLAUDE.md` - Update runtime development guidelines

### Article-specific updates (Python Backend Constitution):

#### Article I (Simplicity First - KISS/YAGNI):
- [x] Update file/function/class size limits in templates
- [x] Add YAGNI reminders and obvious code requirements
- [x] Include 500-line file limit enforcement

#### Article II (Test-Driven Development):
- [x] Update test order in all templates (Red-Green-Refactor)
- [x] Emphasize TDD as NON-NEGOTIABLE
- [x] Add pytest requirements and test coverage gates
- [x] Include test file naming conventions (test_*.py)

#### Article III (FastAPI-First Architecture):
- [x] Update templates for FastAPI with Pydantic
- [x] Add async/await requirements for I/O
- [x] Include OpenAPI documentation requirements
- [x] Add dependency injection patterns

#### Article IV (Type Safety & Documentation):
- [x] Add type hints requirements (PEP 484)
- [x] Include mypy validation requirements
- [x] Add Google-style docstring requirements
- [x] Include README.md requirements per module

#### Article V (Dependency Discipline):
- [x] Add UV package manager exclusivity
- [x] Include dependency maintenance requirements
- [x] Add version pinning requirements
- [x] Update package management commands

#### Article VI (Fail Fast & Observable):
- [x] Add structured logging with structlog
- [x] Include correlation IDs for request tracing
- [x] Add early validation requirements
- [x] Include custom exception classes

#### Article VII (DRY & Modular Design):
- [x] Add function/class size limits
- [x] Include modular design patterns
- [x] Add reusable component requirements

## Validation Steps

1. **Before committing constitution changes:**
   - [x] All templates reference new requirements
   - [x] Examples updated to match new rules  
   - [x] No contradictions between documents

2. **After updating templates:**
   - [x] Run through a sample implementation plan
   - [x] Verify all constitution requirements addressed
   - [x] Check that templates are self-contained (readable without constitution)

3. **Version tracking:**
   - [x] Update constitution version number (1.0.0 → 1.0.1)
   - [x] Note version in template footers
   - [x] Add amendment to constitution history

## Common Misses

Watch for these often-forgotten updates:
- Command documentation (`/commands/*.md`)
- Checklist items in templates
- Example code/commands
- Domain-specific variations (web vs mobile vs CLI)
- Cross-references between documents

## Template Sync Status

Last sync check: 2025-01-13
- Constitution version: 1.0.1 (Python Backend Development)
- Templates aligned: ✅ (all templates updated for Python/FastAPI/TDD focus)

---

*This checklist ensures the constitution's principles are consistently applied across all project documentation.*