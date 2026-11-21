#!/usr/bin/env bash
# Undo what setup/run started: stop this workspace's web server and release its port.
# Shared resources (Postgres, Redis, catalogs in the main checkout) are never touched.
#   --stop-only   stop the server but keep the port reservation
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# The pid is `uv run`, whose child is uvicorn, whose child is the --reload worker:
# signal the whole tree, leaves first.
tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do tree "$c"; done; echo "$1"; }

stop_pid() {
  local pids; pids="$(tree "$1")"
  kill -TERM $pids 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    local alive="" p
    for p in $pids; do kill -0 "$p" 2>/dev/null && alive=1; done
    [[ -n "$alive" ]] || return 0
    sleep 0.5
  done
  kill -KILL $pids 2>/dev/null || true
}

if [[ -s "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  # Only kill it if it is still a uvicorn started from this worktree (PIDs get reused).
  if ps -o command= -p "$pid" 2>/dev/null | grep -q uvicorn \
     && [[ "$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" == "$WORKTREE" ]]; then
    log "stopping web server (pid $pid)"
    stop_pid "$pid"
  fi
  rm -f "$PID_FILE"
fi

if [[ "${1:-}" != "--stop-only" ]]; then
  release_port
  rm -f "$PORT_FILE"
  log "released port reservation"
fi
