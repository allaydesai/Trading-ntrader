#!/usr/bin/env bash
set -euo pipefail

FILE_PATH=$(cat | jq -r '.tool_input.file_path // empty')
[[ -z "$FILE_PATH" || "$FILE_PATH" != *.py || ! -f "$FILE_PATH" ]] && exit 0

cd "$CLAUDE_PROJECT_DIR"
uv run ruff format "$FILE_PATH" 2>/dev/null || true
uv run ruff check "$FILE_PATH" --fix 2>/dev/null || true
exit 0
