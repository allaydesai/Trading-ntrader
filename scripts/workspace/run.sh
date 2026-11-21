#!/usr/bin/env bash
# Launch the web UI on this workspace's port (http://127.0.0.1:<port>).
# Port precedence: $WEB_PORT > $PORT > the port claimed at setup > a fresh claim.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
cd "$WORKTREE"
mkdir -p "$STATE_DIR"

port="${WEB_PORT:-${PORT:-}}"
if [[ -z "$port" ]]; then
  [[ -s "$PORT_FILE" ]] || claim_port >"$PORT_FILE"
  port="$(cat "$PORT_FILE")"
fi
if port_in_use "$port"; then
  echo "[workspace] port $port is already in use:" >&2
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
  echo "[workspace] stop it (./scripts/workspace/teardown.sh --stop-only) or run with WEB_PORT=<n>" >&2
  exit 1
fi

[[ -f static/css/app.css ]] || ./scripts/build-css.sh

echo $$ >"$PID_FILE"
log "web UI: http://127.0.0.1:$port"
exec uv run uvicorn src.api.web:app --reload --host 127.0.0.1 --port "$port"
