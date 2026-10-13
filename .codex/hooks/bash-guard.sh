#!/usr/bin/env bash
set -eo pipefail

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
#
# NOTE ON THE $(...) IDIOM BELOW: `set -e` is active, so a bare
# `OUT=$(failing-cmd)` assignment aborts the whole script at that line — the
# following `$?` check never runs and the hook exits 1 instead of 2. Only exit 2
# blocks a tool call, so writing it that way silently disables the gate. Every
# capture here must stay inside an `if ! OUT=$(...)` condition, where `set -e` is
# suppressed. Same idiom as .githooks/pre-commit.
if echo "$COMMAND" | grep -qE '(^|\s|&&)git\s+commit'; then
  PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  if [[ -z "$PROJECT_DIR" ]]; then
    echo "PRE-COMMIT: Cannot determine project directory." >&2; exit 2
  fi
  cd "$PROJECT_DIR"

  # Snapshot the staged set BEFORE formatting. Only files already in the index can
  # land in this commit, so they are the only ones this gate may block on. Anything
  # else the formatter touches belongs to the working tree and is left alone — that
  # is what allows a partial commit (stage a subset, commit, repeat).
  # bash 3.2 on macOS has no associative arrays, hence the temp file.
  SNAP=$(mktemp -t bashguard) || { echo "PRE-COMMIT: mktemp failed." >&2; exit 2; }
  trap 'rm -f "$SNAP"' EXIT

  git diff --cached --name-only --diff-filter=ACMR 2>/dev/null | while IFS= read -r f; do
    [[ -z "$f" || ! -f "$f" ]] && continue
    # "partial" = the file has unstaged edits too, so re-staging it after the
    # formatter runs would sweep in hunks the user deliberately held back.
    if git diff --quiet -- "$f" 2>/dev/null; then state=clean; else state=partial; fi
    printf '%s\t%s\t%s\n' "$(git hash-object -- "$f")" "$state" "$f"
  done > "$SNAP"

  if ! FORMAT_OUTPUT=$(uv run ruff format . 2>&1); then
    echo "PRE-COMMIT: ruff format failed:" >&2
    echo "$FORMAT_OUTPUT" >&2; exit 2
  fi

  if ! LINT_OUTPUT=$(uv run ruff check . --fix 2>&1); then
    echo "PRE-COMMIT: Unfixable lint errors. Fix before committing:" >&2
    echo "$LINT_OUTPUT" >&2; exit 2
  fi

  # Reconcile only what the formatter actually changed, and only among staged files.
  RESTAGE=""; CONFLICT=""
  while IFS=$'\t' read -r before_hash state f; do
    [[ -z "$f" || ! -f "$f" ]] && continue
    [[ "$(git hash-object -- "$f")" == "$before_hash" ]] && continue
    if [[ "$state" == "clean" ]]; then
      RESTAGE="${RESTAGE}${f}"$'\n'
    else
      CONFLICT="${CONFLICT}${f}"$'\n'
    fi
  done < "$SNAP"

  # A partially-staged file cannot be re-staged safely (that would sweep in the hunks
  # the user held back), but it also does not need to be: what gets committed is the
  # INDEX content, not the worktree the formatter just rewrote. So check the staged
  # bytes directly and only block when those are genuinely unformatted.
  BAD=""
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "$f" in
      *.py|*.pyi) ;;
      *) continue ;;   # ruff format only rewrites Python; nothing to verify
    esac
    if ! git cat-file -p ":$f" 2>/dev/null \
         | uv run ruff format --check --stdin-filename "$f" - >/dev/null 2>&1; then
      BAD="${BAD}${f}"$'\n'
    fi
  done <<< "$CONFLICT"

  if [[ -n "$BAD" ]]; then
    echo "PRE-COMMIT: these files are partially staged AND their staged content is" >&2
    echo "unformatted, so the commit would land unformatted code:" >&2
    printf '%s' "$BAD" >&2
    echo "The hook cannot fix this for you — re-staging would pull in the unstaged" >&2
    echo "hunks you held back. Either 'git add' the formatted file in full, or stash" >&2
    echo "the work-in-progress hunks and retry." >&2; exit 2
  fi

  if [[ -n "$RESTAGE" ]]; then
    while IFS= read -r f; do
      [[ -n "$f" ]] && git add -- "$f"
    done <<< "$RESTAGE"
    echo "PRE-COMMIT: re-staged formatter changes in:"
    printf '%s' "$RESTAGE"
  fi

  if ! MYPY_OUTPUT=$(uv run mypy src/core src/services 2>&1); then
    echo "PRE-COMMIT: Type check errors. Fix before committing:" >&2
    echo "$MYPY_OUTPUT" >&2; exit 2
  fi
fi

exit 0
