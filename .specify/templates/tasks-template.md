# Tasks: [FEATURE NAME]

**Input**: Design documents from `/specs/[###-feature-name]/`
**Prerequisites**: plan.md (required), research.md, data-model.md, contracts/

## Execution Flow (main)
```
1. Load plan.md from feature directory
   → If not found: ERROR "No implementation plan found"
   → Extract: tech stack, libraries, structure
2. Load optional design documents:
   → data-model.md: Extract entities → model tasks
   → contracts/: Each file → contract test task
   → research.md: Extract decisions → setup tasks
3. Generate tasks by category:
   → Setup: project init, dependencies, linting
   → Tests: contract tests, integration tests
   → Core: models, services, CLI commands
   → Integration: DB, middleware, logging
   → Polish: unit tests, performance, docs
4. Apply task rules:
   → Different files = mark [P] for parallel
   → Same file = sequential (no [P])
   → Tests before implementation (TDD)
5. Number tasks sequentially (T001, T002...)
6. Generate dependency graph
7. Create parallel execution examples
8. Validate task completeness:
   → All contracts have tests?
   → All entities have models?
   → All endpoints implemented?
9. Return: SUCCESS (tasks ready for execution)
```

## Format: `[ID] [P?] Description`
- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions

## Path Conventions
- **Single project**: `src/`, `tests/` at repository root
- **Web app**: `backend/src/`, `frontend/src/`
- **Mobile**: `api/src/`, `ios/src/` or `android/src/`
- Paths shown below assume single project - adjust based on plan.md structure

## Phase 3.1: Setup
- [ ] T001 Create Python project structure (src/, tests/, scripts/, docs/)
- [ ] T002 Initialize Python 3.11+ project with UV package manager
- [ ] T003 [P] Configure pre-commit hooks (black, ruff, mypy)
- [ ] T004 [P] Setup .python-version and pyproject.toml
- [ ] T005 [P] Create .env.example for environment variables

## Phase 3.2: Tests First (TDD) ⚠️ MUST COMPLETE BEFORE 3.3
**CRITICAL: Tests MUST be written BEFORE implementation (Red-Green-Refactor)**
**All tests MUST FAIL initially - this proves they are testing something**
- [ ] T006 [P] Write failing test for POST /api/users in tests/test_users_api.py
- [ ] T007 [P] Write failing test for GET /api/users/{id} in tests/test_users_api.py
- [ ] T008 [P] Write failing integration test for user registration in tests/test_registration.py
- [ ] T009 [P] Write failing test for authentication flow in tests/test_auth.py
- [ ] T010 [P] Setup pytest fixtures in tests/conftest.py

## Phase 3.3: Core Implementation (ONLY after tests are RED)
- [ ] T011 [P] Create Pydantic model for User in src/models/user.py
- [ ] T012 [P] Create SQLAlchemy model in src/db/models.py
- [ ] T013 [P] Implement UserService with async methods in src/services/user_service.py
- [ ] T014 Create FastAPI router for users in src/api/users.py
- [ ] T015 Implement POST /api/users endpoint with Pydantic validation
- [ ] T016 Implement GET /api/users/{id} endpoint with error handling
- [ ] T017 Add structured logging with structlog
- [ ] T018 Create CLI commands using Click in scripts/user_cli.py

## Phase 3.4: Integration
- [ ] T019 Setup Alembic migrations in src/db/migrations/
- [ ] T020 Configure async SQLAlchemy session with connection pooling
- [ ] T021 Implement JWT authentication with refresh tokens
- [ ] T022 Add correlation IDs for request tracing
- [ ] T023 Configure CORS and security headers in FastAPI
- [ ] T024 Setup rate limiting with slowapi
- [ ] T025 Add OpenAPI documentation customization

## Phase 3.5: Polish & Verification
- [ ] T026 [P] Verify test coverage >80% with pytest-cov
- [ ] T027 Run performance tests (API <200ms, DB queries <100ms)
- [ ] T028 [P] Generate API documentation from OpenAPI schema
- [ ] T029 [P] Add Google-style docstrings to all public functions
- [ ] T030 Run type checking with mypy
- [ ] T031 Run formatting with black and linting with ruff
- [ ] T032 Update README.md with setup instructions
- [ ] T033 Document architecture decisions in docs/architecture/decisions/

## Dependencies
- Tests (T006-T010) MUST complete before implementation (T011-T018)
- T011-T012 (models) before T013 (service)
- T014-T016 (API) requires T013 (service)
- T019-T020 (DB setup) before T021 (auth)
- All implementation before polish (T026-T033)

## Parallel Example
```
# Launch T006-T010 together (all test files are independent):
Task: "Write failing test for POST /api/users endpoint using pytest"
Task: "Write failing test for GET /api/users/{id} endpoint using pytest"
Task: "Write failing integration test for user registration flow"
Task: "Write failing test for JWT authentication flow"
Task: "Setup pytest fixtures for async database sessions"
```

## Notes
- [P] tasks = different files, no dependencies
- **TDD is NON-NEGOTIABLE**: Tests must be RED before writing implementation
- Use UV for all package management (uv add, uv remove, uv sync)
- Commit after each task showing test-first approach
- Functions <50 lines, classes <100 lines, files <500 lines
- Use ripgrep (rg) for searching, never grep/find

## Task Generation Rules (Python Backend)
*Applied during main() execution*

1. **From API Contracts**:
   - Each endpoint → pytest test file (MUST FAIL first) [P]
   - Each endpoint → FastAPI router implementation
   - Request/Response → Pydantic models
   
2. **From Data Model**:
   - Each entity → Pydantic schema model [P]
   - Each entity → SQLAlchemy model [P]
   - Relationships → async service methods
   
3. **From User Stories**:
   - Each story → integration test with pytest [P]
   - Auth flows → JWT implementation with refresh tokens
   - Quickstart → end-to-end test scenarios

4. **Python-Specific Requirements**:
   - All functions with type hints (mypy must pass)
   - Async/await for all I/O operations
   - Structured logging with correlation IDs
   - UV for package management exclusively

5. **TDD Ordering (MANDATORY)**:
   - Write tests FIRST (Red phase)
   - Implement minimal code (Green phase)
   - Refactor if needed (keep tests green)
   - NO implementation without failing test

## Validation Checklist
*GATE: Checked by main() before returning*

- [ ] All contracts have corresponding tests
- [ ] All entities have model tasks
- [ ] All tests come before implementation
- [ ] Parallel tasks truly independent
- [ ] Each task specifies exact file path
- [ ] No task modifies same file as another [P] task