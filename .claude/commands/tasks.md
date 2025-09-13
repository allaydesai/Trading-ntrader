---
description: Generate an actionable, dependency-ordered tasks.md for the feature based on available design artifacts.
---

Given the context provided as an argument, do this:

1. Run `.specify/scripts/bash/check-task-prerequisites.sh --json` from repo root and parse FEATURE_DIR and AVAILABLE_DOCS list. All paths must be absolute.
2. Load and analyze available design documents:
   - Always read plan.md for tech stack and libraries
   - IF EXISTS: Read data-model.md for entities
   - IF EXISTS: Read contracts/ for API endpoints
   - IF EXISTS: Read research.md for technical decisions
   - IF EXISTS: Read quickstart.md for test scenarios

   Note: Not all projects have all documents. For example:
   - CLI tools might not have contracts/
   - Simple libraries might not need data-model.md
   - Generate tasks based on what's available

3. Generate tasks following the template:
   - Use `.specify/templates/tasks-template.md` as the base
   - Replace example tasks with actual tasks based on:
     * **Setup tasks**: Python 3.11+ project, UV dependencies, pre-commit hooks
     * **Test tasks [P]**: pytest tests that MUST FAIL first (TDD mandatory)
     * **Core tasks**: Pydantic models, SQLAlchemy entities, FastAPI routers
     * **Integration tasks**: Alembic migrations, async DB, JWT auth, structlog
     * **Polish tasks [P]**: Coverage >80%, mypy, black, ruff, documentation

4. Task generation rules:
   - Each contract file → contract test task marked [P]
   - Each entity in data-model → model creation task marked [P]
   - Each endpoint → implementation task (not parallel if shared files)
   - Each user story → integration test marked [P]
   - Different files = can be parallel [P]
   - Same file = sequential (no [P])

5. Order tasks by dependencies (TDD is NON-NEGOTIABLE):
   - Setup before everything (Python env, UV, pre-commit)
   - Tests MUST come before implementation (Red-Green-Refactor)
   - Pydantic models before SQLAlchemy models
   - Models before async services
   - Services before FastAPI endpoints
   - Core before integration
   - Everything before polish (coverage, type checking, formatting)

6. Include parallel execution examples:
   - Group [P] tasks that can run together
   - Show actual Task agent commands

7. Create FEATURE_DIR/tasks.md with:
   - Correct feature name from implementation plan
   - Numbered tasks (T001, T002, etc.)
   - Clear file paths for each task
   - Dependency notes
   - Parallel execution guidance

Context for task generation: $ARGUMENTS

The tasks.md should be immediately executable - each task must be specific enough that an LLM can complete it without additional context.
