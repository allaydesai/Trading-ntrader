---
allowed-tools: Bash(git*), TodoWrite
argument-hint: [type] [scope] [message]
description: Create a properly formatted git commit following project conventions
---

# Git Commit Command

## Current Status
!`git status --porcelain`

## Arguments Processing
Type: $1
Scope: $2
Message: $3

## Instructions

1. **Check Working Directory**:
   - Display current git status
   - Show modified, added, and untracked files
   - If no changes to commit, inform user and stop

2. **Process Arguments**:
   - If $1 is provided, use as commit type
   - If $2 is provided, use as scope (optional)
   - If $3 is provided, use as commit message
   - If arguments missing, ask user interactively

3. **Validate Commit Type**:
   - Must be one of: feat, fix, docs, style, refactor, test, chore
   - If invalid or missing, prompt user to select from valid options

4. **Build Commit Message**:
   - Format: `<type>(<scope>): <subject>`
   - Subject line must be ≤50 characters
   - Use imperative mood (e.g., "add" not "adds")
   - No period at end of subject
   - If scope not provided, format: `<type>: <subject>`

5. **Stage Files**:
   - Show files that will be staged
   - Ask for confirmation before staging
   - Use `git add .` to stage all changes, or allow user to specify files

6. **Create Commit**:
   - Use the formatted commit message
   - Execute: `git commit -m "<formatted_message>"`
   - Show commit hash and summary

7. **Optional Push**:
   - Ask if user wants to push to remote
   - If yes, execute: `git push`

## Commit Types Reference
- **feat**: New feature
- **fix**: Bug fix
- **docs**: Documentation changes
- **style**: Code formatting (no functional changes)
- **refactor**: Code refactoring
- **test**: Adding or updating tests
- **chore**: Maintenance tasks

## Examples
```
/commit feat auth "add user authentication"
→ feat(auth): add user authentication

/commit fix "resolve memory leak"
→ fix: resolve memory leak

/commit docs
→ Interactive mode for documentation changes
```

Follow the project's commit conventions as specified in CLAUDE.md. Never include "claude code" or AI references in commit messages.