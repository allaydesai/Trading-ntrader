#!/usr/bin/env bash
# Procedure P9 — `live status` against a genuinely running session, then a `kill -9`.
#
# The artifact under test is still the pair of CLI commands; this script only
# sequences them, captures the transcript, and does the two things that are easy
# to get wrong by hand:
#
#   * it redirects `live start` straight to a file instead of piping to `tee`.
#     In a backgrounded pipeline `$!` is the PID of the LAST command, so the
#     `tee` would be killed and the runner would survive — a false negative on
#     pass criterion 3 for a reason unrelated to the code.
#   * it records `last_started_at` at every step, which is what criterion 4
#     ("the row is untouched by the query") is read from.
#
# Usage:  ./scripts/diagnostics/run_p9_status.sh [session-name]
# Run inside RTH so a bar actually closes and `health` can reach `trading`.

set -uo pipefail

SESSION="${1:-p9-status-test}"
LOG="logs/p9-${SESSION}-$(date +%Y%m%d-%H%M%S).log"
STALE_WAIT="${STALE_WAIT:-95}"
BAR_WAIT="${BAR_WAIT:-100}"

cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs

cli() { uv run python -m src.cli.main "$@"; }

started_at() {
    uv run python - "${SESSION}" <<'PY' 2>/dev/null
import sys
from sqlalchemy import text
from src.db.session_sync import get_sync_session
with get_sync_session() as s:
    r = s.execute(text("SELECT last_started_at FROM trading_sessions WHERE name=:n"),
                  {"n": sys.argv[1]}).one_or_none()
print(r[0] if r else "")
PY
}

echo "== P9: starting '${SESSION}' =="
# NOT `cli live start ... &`: backgrounding a shell *function* makes bash fork a
# subshell, and $! then names that subshell rather than the runner. MEASURED on
# 2026-08-28 — $! gave a `bash run_p9_status.sh` PID whose child was `uv run`
# whose child was the real python process. `kill -9` on it would have killed the
# subshell while the runner kept heartbeating, which is a false negative on
# criterion 3 for a reason unrelated to the code. This is the same trap the
# procedure documents for `| tee`, arriving by a different route.
uv run python -m src.cli.main live start "${SESSION}" > "${LOG}" 2>&1 &
sleep 8
# `uv run` also spawns its own python child and does not forward signals, so
# resolve the PID of the process that IS the runner rather than trusting $!.
RUNNER_PID="$(pgrep -f "bin/python3 -m src.cli.main live start ${SESSION}" | head -1)"
[ -n "${RUNNER_PID}" ] || { echo "could not resolve runner pid"; tail -20 "${LOG}"; exit 1; }
echo "runner pid: ${RUNNER_PID}"
ps -p "${RUNNER_PID}" -o pid=,command= || { echo "runner died immediately"; tail -20 "${LOG}"; exit 1; }

echo
echo "== waiting up to ${BAR_WAIT}s for a bar to close so health can reach 'trading' =="
waited=0
while [ "${waited}" -lt "${BAR_WAIT}" ]; do
    grep -q 'live_bars.received' "${LOG}" 2>/dev/null && { echo "  a bar closed after ${waited}s"; break; }
    sleep 5
    waited=$((waited + 5))
done
grep -q 'live_bars.received' "${LOG}" 2>/dev/null || echo "  no bar closed in ${BAR_WAIT}s — health will read 'idle', which is still a legitimate answer"

START_BEFORE="$(started_at)"
echo "last_started_at before any query: ${START_BEFORE}"

echo
echo "== criterion 1+2: status from a second process, runner ALIVE =="
time cli live status "${SESSION}"
echo "--- json ---"
cli live status "${SESSION}" --json

echo
echo "== second query, past one heartbeat tick =="
sleep 35
cli live status "${SESSION}"

START_AFTER_QUERIES="$(started_at)"
echo "last_started_at after queries: ${START_AFTER_QUERIES}"

echo
echo "== criterion 3: kill -9 the runner (no teardown) =="
ps -p "${RUNNER_PID}" -o pid=,command=
kill -9 "${RUNNER_PID}"
sleep 2
ps -p "${RUNNER_PID}" >/dev/null 2>&1 && echo "WARNING: runner survived kill -9" || echo "runner is gone"

echo
echo "== waiting ${STALE_WAIT}s for the heartbeat to exceed the 90s staleness threshold =="
sleep "${STALE_WAIT}"
cli live status "${SESSION}"
echo "--- json ---"
cli live status "${SESSION}" --json

START_AFTER_KILL="$(started_at)"
echo
echo "== criterion 4: last_started_at across the whole procedure =="
echo "  before queries: ${START_BEFORE}"
echo "  after queries:  ${START_AFTER_QUERIES}"
echo "  after kill -9:  ${START_AFTER_KILL}"
if [ "${START_BEFORE}" = "${START_AFTER_QUERIES}" ] && [ "${START_BEFORE}" = "${START_AFTER_KILL}" ]; then
    echo "  UNCHANGED -> criterion 4 holds"
else
    echo "  CHANGED -> criterion 4 FAILS (a query wrote to the row)"
fi

echo
echo "runner log: ${LOG}"
