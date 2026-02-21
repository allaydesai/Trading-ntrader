#!/usr/bin/env bash
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[[ -z "$COMMAND" ]] && exit 0

# --- Destructive commands ---
if echo "$COMMAND" | grep -qE 'git\s+reset\s+--hard'; then
  echo "BLOCKED: 'git reset --hard' destroys uncommitted changes." >&2; exit 2
fi
if echo "$COMMAND" | grep -qE 'git\s+clean\s+-[a-zA-Z]*f'; then
  echo "BLOCKED: 'git clean -f' deletes untracked files." >&2; exit 2
fi
if echo "$COMMAND" | grep -qE 'git\s+push\s+.*--force.*\s+(main|master)'; then
  echo "BLOCKED: Force-push to main/master is prohibited." >&2; exit 2
fi
if echo "$COMMAND" | grep -qiE 'DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE'; then
  echo "BLOCKED: Destructive SQL operations not allowed." >&2; exit 2
fi

# --- UV-only enforcement ---
if echo "$COMMAND" | grep -qE '(^|\s|&&|\|)pip\s+install'; then
  echo "BLOCKED: Use 'uv add <package>' instead of pip install." >&2; exit 2
fi
if echo "$COMMAND" | grep -qE '(sed|awk|echo|tee).*pyproject\.toml'; then
  echo "BLOCKED: Don't edit pyproject.toml directly. Use 'uv add/remove'." >&2; exit 2
fi

# --- Pre-commit quality gate ---
if echo "$COMMAND" | grep -qE '(^|\s|&&)git\s+commit'; then
  cd "$CLAUDE_PROJECT_DIR"
  uv run ruff format . 2>&1 || true
  LINT_OUTPUT=$(uv run ruff check . --fix 2>&1)
  LINT_EXIT=$?
  if [[ $LINT_EXIT -ne 0 ]]; then
    echo "PRE-COMMIT: Unfixable lint errors. Fix before committing:" >&2
    echo "$LINT_OUTPUT" >&2; exit 2
  fi
  CHANGED=$(git diff --name-only 2>/dev/null || true)
  if [[ -n "$CHANGED" ]]; then
    echo "PRE-COMMIT: Formatting changed files that need staging:" >&2
    echo "$CHANGED" >&2
    echo "Run 'git add' on changed files, then retry commit." >&2; exit 2
  fi
  MYPY_OUTPUT=$(uv run mypy src/core src/strategies 2>&1)
  MYPY_EXIT=$?
  if [[ $MYPY_EXIT -ne 0 ]]; then
    echo "PRE-COMMIT: Type check errors. Fix before committing:" >&2
    echo "$MYPY_OUTPUT" >&2; exit 2
  fi
fi

exit 0
