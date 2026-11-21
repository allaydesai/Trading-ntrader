#!/usr/bin/env bash
# Shared helpers for the workspace lifecycle scripts (setup / teardown / run).
#
# Tool-neutral: works under Superset, T3 Code, or bare git (paths come from git itself).
#   ROOT      = the main checkout that holds untracked files (.env, catalogs, tailwindcss)
#   WORKTREE  = the workspace being set up / torn down / run
# Per-workspace state lives in $WORKTREE/.workspace/ (gitignored).

set -euo pipefail

_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve from git first: the script always lives in the worktree it serves, and every
# worktree's common git dir is <main checkout>/.git. Tool env vars are only a fallback,
# because they leak (e.g. T3 Code started from a Superset terminal inherits SUPERSET_*).
WORKTREE="$(git -C "$_script_dir" rev-parse --show-toplevel 2>/dev/null \
  || echo "${SUPERSET_WORKSPACE_PATH:-${T3CODE_WORKTREE_PATH:-}}")"
_common_dir="$(git -C "$_script_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$_common_dir" ]]; then
  ROOT="$(dirname "$_common_dir")"
else
  ROOT="${SUPERSET_ROOT_PATH:-${T3CODE_PROJECT_ROOT:-}}"
fi
[[ -n "$WORKTREE" && -n "$ROOT" ]] || { echo "[workspace] cannot locate worktree/root" >&2; exit 1; }
WORKTREE="$(cd "$WORKTREE" && pwd -P)"
ROOT="$(cd "$ROOT" && pwd -P)"

STATE_DIR="$WORKTREE/.workspace"
PORT_FILE="$STATE_DIR/port"
PID_FILE="$STATE_DIR/web.pid"

# Machine-wide port registry: one file per claimed port, holding the owning worktree path.
PORT_REGISTRY="${XDG_STATE_HOME:-$HOME/.local/state}/ntrader/ports"
DEFAULT_WEB_PORT=8000   # reserved for the main checkout
PORT_RANGE_START=8001
PORT_RANGE_END=8099

log() { printf '[workspace] %s\n' "$*"; }

is_main_checkout() { [[ "$ROOT" == "$WORKTREE" ]]; }

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# Drop registry entries whose worktree no longer exists (covers tools with no teardown hook).
gc_ports() {
  mkdir -p "$PORT_REGISTRY"
  local f owner
  for f in "$PORT_REGISTRY"/*; do
    [[ -f "$f" ]] || continue
    owner="$(cat "$f" 2>/dev/null || true)"
    [[ -n "$owner" && -d "$owner" ]] || rm -f "$f"
  done
}

# Print the port registered to this worktree, claiming a free one if needed.
claim_port() {
  if is_main_checkout; then echo "$DEFAULT_WEB_PORT"; return; fi
  gc_ports
  local f p
  for f in "$PORT_REGISTRY"/*; do
    [[ -f "$f" && "$(cat "$f")" == "$WORKTREE" ]] && { basename "$f"; return; }
  done
  for ((p = PORT_RANGE_START; p <= PORT_RANGE_END; p++)); do
    port_in_use "$p" && continue
    # noclobber makes the create atomic: two workspaces racing for one port can't both win
    if (set -o noclobber; echo "$WORKTREE" >"$PORT_REGISTRY/$p") 2>/dev/null; then
      echo "$p"; return
    fi
  done
  echo "no free port in $PORT_RANGE_START-$PORT_RANGE_END" >&2
  return 1
}

release_port() {
  local f
  for f in "$PORT_REGISTRY"/*; do
    [[ -f "$f" && "$(cat "$f")" == "$WORKTREE" ]] && rm -f "$f"
  done
  return 0
}
