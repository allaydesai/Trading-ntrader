# Claude Code + Git Worktrees: Parallel Development Guide

A hands-on guide to supercharging your development workflow by running multiple Claude Code sessions simultaneously using git worktrees.

---

## Table of Contents

1. [What Are Git Worktrees?](#what-are-git-worktrees)
2. [Why Use Worktrees with Claude Code?](#why-use-worktrees-with-claude-code)
3. [Initial Setup](#initial-setup)
4. [Walkthrough: Making Two Changes Simultaneously](#walkthrough-making-two-changes-simultaneously)
5. [Key Benefits to Leverage](#key-benefits-to-leverage)
6. [Merging Changes Back](#merging-changes-back)
7. [Best Practices & Tips](#best-practices--tips)
8. [Quick Reference Commands](#quick-reference-commands)

---

## What Are Git Worktrees?

Git worktrees allow you to check out multiple branches of a repository in separate directories simultaneously. Each worktree has its own working directory but shares the same `.git` history, commits, and remote connections.

**Traditional workflow problem:**
```
Working on feature-A → Need to fix bug → Stash changes → Switch branch → Fix bug → 
Switch back → Pop stash → Remember where you were...
```

**Worktree workflow:**
```
Working on feature-A in ~/projects/repo-feature-a
Simultaneously working on bugfix in ~/projects/repo-bugfix
No context switching. No stashing. No mental overhead.
```

---

## Why Use Worktrees with Claude Code?

Claude Code excels at focused, autonomous work on specific tasks. Combining it with worktrees unlocks:

| Benefit | Description |
|---------|-------------|
| **True Parallelism** | Run multiple Claude Code sessions working on different features simultaneously |
| **Zero Context Switching** | Each Claude session maintains full context of its specific task |
| **Isolated Environments** | Changes in one worktree don't affect another until you merge |
| **Faster Iteration** | Don't wait for one task to complete before starting another |
| **Safer Experimentation** | Test risky changes in isolation without affecting your main work |

---

## Initial Setup

### Prerequisites

- Git 2.5+ (worktrees were added in Git 2.5)
- Claude Code installed and configured
- A git repository to work with

### Step 1: Verify Git Version

```bash
git --version
# Should be 2.5 or higher
```

### Step 2: Choose Your Directory Structure

We recommend keeping worktrees as siblings to your main repo:

```
~/projects/
├── my-app/                 # Main repository (your primary worktree)
├── my-app-feature-auth/    # Worktree for auth feature
├── my-app-feature-api/     # Worktree for API feature
└── my-app-hotfix/          # Worktree for urgent fixes
```

### Step 3: Create Your First Worktree

From your main repository:

```bash
cd ~/projects/my-app

# Create a worktree with an existing branch
git worktree add ../my-app-feature-auth feature/auth

# Or create a worktree with a NEW branch based on main
git worktree add -b feature/new-api ../my-app-feature-api main
```

### Step 4: Verify Your Worktrees

```bash
git worktree list
```

Output:
```
/home/user/projects/my-app                 abc1234 [main]
/home/user/projects/my-app-feature-auth    def5678 [feature/auth]
/home/user/projects/my-app-feature-api     abc1234 [feature/new-api]
```

---

## Walkthrough: Making Two Changes Simultaneously

Let's walk through a realistic scenario: You need to add user authentication AND create a new API endpoint, and you want Claude Code working on both simultaneously.

### Scenario Setup

You have a Node.js application and need to:
1. **Feature A**: Add JWT authentication middleware
2. **Feature B**: Create a new `/api/reports` endpoint

### Step 1: Create Worktrees for Both Features

```bash
cd ~/projects/my-app

# Create worktree for authentication feature
git worktree add -b feature/jwt-auth ../my-app-jwt-auth main

# Create worktree for API endpoint feature  
git worktree add -b feature/reports-api ../my-app-reports-api main
```

### Step 2: Set Up Dependencies in Each Worktree

Each worktree is a separate directory, so each needs its own `node_modules`:

```bash
# Terminal 1: Set up auth worktree
cd ~/projects/my-app-jwt-auth
npm install

# Terminal 2: Set up API worktree
cd ~/projects/my-app-reports-api
npm install
```

### Step 3: Launch Claude Code in Each Worktree

Open two separate terminal windows/tabs:

**Terminal 1 - Authentication Feature:**
```bash
cd ~/projects/my-app-jwt-auth
claude
```

Give Claude its task:
```
Add JWT authentication middleware to this Express app. Create:
1. A middleware function in src/middleware/auth.js
2. Token generation in src/utils/jwt.js
3. Protected route examples
4. Update package.json with jsonwebtoken dependency
```

**Terminal 2 - Reports API Feature:**
```bash
cd ~/projects/my-app-reports-api
claude
```

Give Claude its task:
```
Create a new reports API endpoint. Implement:
1. GET /api/reports - list all reports with pagination
2. GET /api/reports/:id - get single report
3. POST /api/reports - create new report
4. Add proper error handling and validation
```

### Step 4: Monitor Progress

Both Claude Code sessions now work independently:

```
┌─────────────────────────────────────┐  ┌─────────────────────────────────────┐
│  Terminal 1: JWT Auth               │  │  Terminal 2: Reports API            │
│  ~/my-app-jwt-auth                  │  │  ~/my-app-reports-api               │
│                                     │  │                                     │
│  Claude: Creating auth middleware...│  │  Claude: Setting up routes...       │
│  ✓ src/middleware/auth.js          │  │  ✓ src/routes/reports.js            │
│  ✓ src/utils/jwt.js                │  │  ✓ src/controllers/reports.js       │
│  Working on protected routes...     │  │  Adding validation...               │
└─────────────────────────────────────┘  └─────────────────────────────────────┘
```

### Step 5: Review Changes in Each Worktree

**In the auth worktree:**
```bash
cd ~/projects/my-app-jwt-auth
git status
git diff
```

**In the API worktree:**
```bash
cd ~/projects/my-app-reports-api
git status
git diff
```

### Step 6: Commit Changes

**Terminal 1:**
```bash
cd ~/projects/my-app-jwt-auth
git add .
git commit -m "feat: add JWT authentication middleware

- Add auth middleware with token verification
- Create JWT utility functions
- Add protected route examples
- Install jsonwebtoken dependency"
```

**Terminal 2:**
```bash
cd ~/projects/my-app-reports-api
git add .
git commit -m "feat: add reports API endpoints

- Implement CRUD operations for reports
- Add pagination to list endpoint
- Include request validation
- Add comprehensive error handling"
```

---

## Key Benefits to Leverage

### 1. Concurrent Development Without Conflicts

Since each worktree is isolated, Claude can make sweeping changes in one without affecting the other:

```bash
# Auth worktree: Claude refactors entire middleware structure
# API worktree: Claude reorganizes route handlers

# Neither affects the other until merge time
```

### 2. Different Node/Python Versions Per Worktree

Using tools like `nvm` or `pyenv`, each worktree can run different versions:

```bash
# In auth worktree
cd ~/projects/my-app-jwt-auth
nvm use 18

# In API worktree  
cd ~/projects/my-app-reports-api
nvm use 20
```

### 3. Run Tests in Parallel

```bash
# Terminal 1
cd ~/projects/my-app-jwt-auth
npm test -- --watch

# Terminal 2
cd ~/projects/my-app-reports-api
npm test -- --watch
```

### 4. Compare Implementations

Need Claude to try two different approaches? Create two worktrees from the same branch:

```bash
git worktree add -b experiment/approach-a ../my-app-approach-a main
git worktree add -b experiment/approach-b ../my-app-approach-b main
```

Then have Claude implement the same feature differently in each, and compare results.

### 5. Code Review While Developing

```bash
# Keep working on your feature
cd ~/projects/my-app-feature

# Pull down a PR to review in separate worktree
git worktree add ../my-app-pr-review origin/feature/someone-elses-pr

# Review without disrupting your work
cd ~/projects/my-app-pr-review
claude "Review this code for security issues and best practices"
```

### 6. Hotfix Production While Feature Work Continues

```bash
# You're deep in feature work when production breaks
# No need to stash or commit incomplete work

git worktree add -b hotfix/critical-fix ../my-app-hotfix main
cd ../my-app-hotfix
claude "Fix the authentication timeout bug in src/auth/session.js"

# Fix, test, merge to production
# Return to feature work exactly where you left off
```

---

## Merging Changes Back

### Option 1: Merge via Pull Requests (Recommended)

Push branches and create PRs for code review:

**From auth worktree:**
```bash
cd ~/projects/my-app-jwt-auth
git push -u origin feature/jwt-auth
# Create PR on GitHub/GitLab
```

**From API worktree:**
```bash
cd ~/projects/my-app-reports-api
git push -u origin feature/reports-api
# Create PR on GitHub/GitLab
```

After PRs are reviewed and merged, update main:

```bash
cd ~/projects/my-app
git checkout main
git pull origin main
```

### Option 2: Direct Merge to Main

If you're working solo or don't need PR review:

```bash
cd ~/projects/my-app
git checkout main

# Merge auth feature
git merge feature/jwt-auth -m "Merge feature/jwt-auth: Add JWT authentication"

# Merge API feature
git merge feature/reports-api -m "Merge feature/reports-api: Add reports endpoints"
```

### Handling Merge Conflicts

If both features modified the same files:

```bash
cd ~/projects/my-app
git checkout main
git merge feature/jwt-auth  # Merges cleanly

git merge feature/reports-api
# CONFLICT in src/app.js

# Resolve conflicts, then:
git add src/app.js
git commit -m "Merge feature/reports-api with conflict resolution"
```

**Pro tip:** Use Claude to help resolve conflicts:

```bash
claude "Help me resolve the merge conflict in src/app.js - I need to keep both 
the auth middleware setup and the reports routes"
```

### Option 3: Rebase for Linear History

If you prefer a linear git history:

```bash
# From the feature worktree
cd ~/projects/my-app-jwt-auth
git fetch origin
git rebase origin/main

# Resolve any conflicts, then force push
git push --force-with-lease origin feature/jwt-auth
```

### Clean Up After Merging

Remove worktrees you no longer need:

```bash
# Remove the worktree directory and git reference
git worktree remove ../my-app-jwt-auth
git worktree remove ../my-app-reports-api

# Or if you deleted the directory manually, clean up git's records
git worktree prune

# Optionally delete the merged branches
git branch -d feature/jwt-auth
git branch -d feature/reports-api
```

---

## Best Practices & Tips

### Naming Conventions

Use consistent, descriptive names:

```bash
# Pattern: {repo-name}-{branch-type}-{description}
my-app-feature-auth
my-app-feature-api-v2
my-app-hotfix-login
my-app-experiment-new-db
my-app-review-pr-123
```

### Shared vs. Separate Configurations

**Shared (via git):**
- `.gitignore`
- `package.json` / `requirements.txt`
- Source code
- Configuration templates

**Separate (per worktree):**
- `node_modules/` or `venv/`
- `.env` files (copy and modify as needed)
- IDE settings (`.idea/`, `.vscode/`)
- Build artifacts (`dist/`, `build/`)

### Environment Variables

Create a setup script for new worktrees:

```bash
#!/bin/bash
# setup-worktree.sh

WORKTREE_DIR=$1

if [ -z "$WORKTREE_DIR" ]; then
    echo "Usage: ./setup-worktree.sh <worktree-directory>"
    exit 1
fi

cd "$WORKTREE_DIR"

# Copy environment template
cp .env.example .env

# Install dependencies
if [ -f "package.json" ]; then
    npm install
elif [ -f "requirements.txt" ]; then
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
fi

echo "Worktree setup complete in $WORKTREE_DIR"
```

### Avoid Common Pitfalls

| Pitfall | Solution |
|---------|----------|
| Forgetting which worktree you're in | Add branch to your shell prompt |
| Running commands in wrong directory | Use `pwd` and `git worktree list` frequently |
| Committing to wrong branch | Always verify with `git branch` before committing |
| Forgetting to install dependencies | Create a setup script (see above) |
| Too many stale worktrees | Run `git worktree prune` regularly |

### Shell Prompt Enhancement

Add the current branch and worktree indicator to your prompt:

```bash
# Add to ~/.bashrc or ~/.zshrc
parse_git_branch() {
    git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/(\1)/'
}

export PS1="\w \$(parse_git_branch) $ "
```

---

## Quick Reference Commands

### Essential Worktree Commands

```bash
# List all worktrees
git worktree list

# Create worktree with existing branch
git worktree add <path> <branch>

# Create worktree with NEW branch from current HEAD
git worktree add -b <new-branch> <path>

# Create worktree with NEW branch from specific base
git worktree add -b <new-branch> <path> <base-branch>

# Remove a worktree (safe - won't delete if uncommitted changes)
git worktree remove <path>

# Force remove a worktree
git worktree remove --force <path>

# Clean up stale worktree references
git worktree prune

# Lock a worktree to prevent accidental deletion
git worktree lock <path>

# Unlock a worktree
git worktree unlock <path>
```

### Common Workflow Commands

```bash
# Create feature worktree and start Claude Code
git worktree add -b feature/my-feature ../repo-my-feature main && \
cd ../repo-my-feature && \
npm install && \
claude

# Quick cleanup after merge
git worktree remove ../repo-my-feature && \
git branch -d feature/my-feature

# See what's different between worktrees
diff -r ../repo-feature-a/src ../repo-feature-b/src
```

### Emergency Commands

```bash
# I deleted a worktree directory manually - fix git's records
git worktree prune

# I want to move a worktree to a different location
git worktree move <worktree> <new-path>

# I need to check out a branch that's already checked out elsewhere
# (normally prohibited - use this flag if you know what you're doing)
git worktree add --force <path> <branch>
```

---

## Summary

Git worktrees + Claude Code = Development superpowers

1. **Set up worktrees** for each independent task
2. **Run Claude Code** in parallel across worktrees
3. **Work simultaneously** without context switching
4. **Merge changes** back via PR or direct merge
5. **Clean up** worktrees after merging

This workflow lets you leverage Claude Code's autonomous capabilities to their fullest—multiple AI-assisted development streams running in parallel, each with full context and isolation.

---

*Happy parallel coding! 🚀*
