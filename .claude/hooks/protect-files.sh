#!/usr/bin/env bash
set -euo pipefail

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[[ -z "$FILE_PATH" ]] && exit 0

REL_PATH="${FILE_PATH#"$CLAUDE_PROJECT_DIR"/}"
BASENAME=$(basename "$FILE_PATH")

# Protected directories
for dir in "alembic/versions/" "src/core/strategies/custom/" ".claude/hooks/"; do
  if [[ "$REL_PATH" == "$dir"* ]]; then
    echo "BLOCKED: '$REL_PATH' is in protected directory '$dir'." >&2
    exit 2
  fi
done

# Protected file patterns
case "$BASENAME" in
  .env*) echo "BLOCKED: Environment files contain secrets." >&2; exit 2 ;;
  *.pem|*.key) echo "BLOCKED: Private key files must not be modified." >&2; exit 2 ;;
  uv.lock) echo "BLOCKED: uv.lock is managed by UV. Use 'uv add/remove/sync'." >&2; exit 2 ;;
  pyproject.toml) echo "BLOCKED: pyproject.toml is managed by UV. Use 'uv add <pkg>' or 'uv remove <pkg>'." >&2; exit 2 ;;
esac

exit 0
