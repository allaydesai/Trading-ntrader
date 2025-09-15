---
description: Execute implementation tasks from plan.md and tasks.md within a spec folder following TDD principles.
---

# Implementation Command

## Current Status
Execute implementation tasks from specification folders systematically, following Test-Driven Development and constitution requirements.

## Arguments Processing
- `$1`: Target (optional) - spec folder path, milestone number, or task ID
- `$2`: Mode (optional) - --parallel, --dry-run, --validate, --milestone, --task
- `$3`: Additional options (optional)

## Instructions

1. **Determine Target Spec Folder**:
   - If $1 provided and looks like path: Use specified spec folder
   - If $1 provided and looks like branch name: Use specs/$1/
   - If no $1: Use specs/{current_branch}/
   - Verify folder exists with plan.md and tasks.md

2. **Load Specification Documents**:
   - Read plan.md for technical context and constitution requirements
   - Parse tasks.md for implementation tasks and dependencies
   - Load data-model.md, contracts/, quickstart.md if available
   - Extract project structure, tech stack, and testing requirements

3. **Parse Implementation Tasks**:
   - Extract task IDs (T001, T002, etc.) and descriptions
   - Identify task dependencies and execution order
   - Group parallel tasks marked with [P]
   - Map tasks to milestones based on task grouping
   - Validate task references to files and entities

4. **Constitution Compliance Check**:
   - Verify TDD requirements: Tests before implementation
   - Check file size limits (500 lines max)
   - Validate function limits (50 lines) and class limits (100 lines)
   - Ensure UV dependency management (no direct pyproject.toml edits)
   - Confirm FastAPI + Pydantic + async/await patterns
   - Validate structured logging and error handling

5. **Execution Modes**:

   ### Default Mode (Sequential Execution)
   ```bash
   /implement
   ```
   - Execute all tasks in dependency order
   - Stop on first failure
   - Show progress with TodoWrite tool

   ### Milestone Mode
   ```bash
   /implement --milestone 1
   /implement 1  # Short form
   ```
   - Execute specific milestone only
   - Show milestone summary before execution
   - Ask for confirmation

   ### Task Mode
   ```bash
   /implement --task T001
   /implement T001  # Short form
   ```
   - Execute single task only
   - Show task details and dependencies
   - Verify prerequisites completed

   ### Parallel Mode
   ```bash
   /implement --parallel
   ```
   - Execute [P] marked tasks concurrently using parallel Task tool calls
   - Group tasks by milestone and parallel compatibility
   - Monitor all parallel executions

   ### Validation Mode
   ```bash
   /implement --validate
   ```
   - Check task definitions against constitution
   - Verify file paths and dependencies exist
   - Report potential issues without execution

   ### Dry Run Mode
   ```bash
   /implement --dry-run
   ```
   - Show execution plan without implementing
   - Display task order and dependencies
   - Estimate execution time

6. **Task Execution Workflow**:

   For each task in order:

   a. **Pre-execution Checks**:
   - Verify dependencies are completed
   - Check if task is test or implementation
   - Validate file paths exist or can be created

   b. **Test-First Enforcement** (for implementation tasks):
   - Look for corresponding test task (usually preceding)
   - If test doesn't exist, create failing test first
   - Run test to ensure it fails (Red phase)

   c. **Implementation Phase**:
   - Create/modify files as specified in task
   - Follow task instructions exactly
   - Respect file size and complexity limits
   - Use proper imports and dependencies

   d. **Verification Phase**:
   - Run pytest for affected test files
   - Verify tests pass (Green phase)
   - Check code quality with ruff/mypy if available
   - Update TodoWrite progress

   e. **Refactor Phase** (if needed):
   - Improve code while keeping tests green
   - Ensure compliance with constitution

7. **Progress Tracking**:
   - Use TodoWrite tool to track all tasks
   - Mark tasks as: pending, in_progress, completed
   - Show real-time progress updates
   - Provide milestone completion summaries

8. **Error Handling**:
   - Stop execution on test failures
   - Log all actions and errors clearly
   - Provide rollback suggestions for failures
   - Show clear next steps to fix issues

9. **Parallel Execution Logic**:
   - Group [P] tasks within same milestone
   - Execute using single message with multiple Task tool calls
   - Monitor all parallel tasks completion
   - Don't proceed to next milestone until all [P] tasks complete

10. **Integration Points**:
    - Update CLAUDE.md with implementation progress
    - Run `.specify/scripts/bash/update-agent-context.sh claude` after completion
    - Generate implementation summary report
    - Create commit with proper message format

## Task Parsing Examples

### Task Format Recognition
```markdown
### T001: Initialize Python Project Structure
**File**: Project root structure
**Dependencies**: None
```

### Parallel Task Detection
```markdown
### T005: Write First Integration Test [P]
### T006: Create SMA Strategy Model [P]
```

### Milestone Grouping
```markdown
## Milestone 1: Basic CLI with Simple Backtest (T001-T012)
```

## Execution Flow Examples

### Sequential Implementation
1. Parse tasks.md → Extract T001-T050
2. Group by milestones → M1: T001-T012, M2: T013-T025
3. Execute M1 tasks in order
4. Verify M1 completion with tests
5. Proceed to M2

### Parallel Implementation
1. Identify [P] tasks in current milestone
2. Execute parallel tasks concurrently:
   ```
   Task("implement test T005")
   Task("implement model T006")
   Task("implement config T007")
   ```
3. Wait for all parallel tasks to complete
4. Proceed to next sequential task

## Constitution Enforcement

The command MUST enforce these principles:

- **TDD Non-Negotiable**: Tests written before implementation code
- **KISS/YAGNI**: Simple solutions, features only when needed
- **File Limits**: 500 lines max, split if approaching
- **Function Limits**: 50 lines max per function
- **Class Limits**: 100 lines max per class
- **UV Only**: Never edit pyproject.toml directly
- **Type Safety**: All functions require type hints
- **Error Handling**: Fail fast with specific exceptions
- **Async/Await**: All I/O operations must be async

## Output Format

### Progress Updates
```
🔄 Milestone 1: Basic CLI with Simple Backtest
   ✅ T001: Initialize Python Project Structure
   🔄 T002: Setup UV and Core Dependencies
   ⏳ T003: Create Minimal Configuration (pending)
   ⏳ T004: Create Basic CLI Entry Point (pending)
```

### Completion Summary
```
✅ Implementation Complete: Milestone 1

Tasks Completed: 12/12
Tests Passing: 8/8
Code Coverage: 85%
Constitution Compliance: 100%

Next Steps:
- Run: uv run pytest to verify all tests
- Run: /implement --milestone 2 to continue
```

## Error Recovery

If execution fails:
1. Show clear error message with task context
2. Provide specific fix recommendations
3. Offer to resume from failed task
4. Suggest rollback options if needed

## Notes

- Always respect task dependencies and execution order
- Use absolute file paths from repository root
- Follow existing project patterns and conventions
- Update progress tracking in real-time
- Generate detailed execution logs for debugging
- Never skip test phases in TDD workflow
- Ensure each milestone produces working, testable functionality

The command ensures systematic, constitution-compliant implementation while maintaining the rapid feedback loop essential for effective Test-Driven Development.