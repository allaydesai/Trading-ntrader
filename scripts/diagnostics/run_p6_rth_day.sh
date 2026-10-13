#!/usr/bin/env bash
# Procedure P6 — run a session unattended for a full RTH day, with evidence capture.
#
# Start this BEFORE 09:30 ET. P6's criterion 2 is "still running 6.5 hours later
# with no operator intervention", so a start after the open cannot satisfy it.
#
# Usage:  ./scripts/diagnostics/run_p6_rth_day.sh [session-name]
#
# What it adds over `uv run python -m src.cli.main live start <name>`:
#   * refuses to start if a precondition is missing, instead of failing 20 minutes in
#   * redirects straight to a file rather than piping to `tee`, so $! is really the
#     runner (Procedure P9's note explains why the pipeline form is a trap)
#   * samples RSS and the session row every 5 minutes into a CSV, which is what
#     criteria 3 and 4 are read from afterwards
#   * watches for the data-farm defect found on 2026-08-28: a transient HMDS or
#     market-data farm drop at subscribe time kills the bar subscription
#     permanently (IB code 10182 on the subscription's req id) and nothing
#     retries it. The session keeps heartbeating and looks healthy while
#     receiving nothing, which would make criterion 4 measure a dead stream.

set -uo pipefail

SESSION="${1:-rth-day-1}"
STAMP="$(date +%Y%m%d)"
LOG="logs/p6-${SESSION}-${STAMP}.log"
SAMPLES="logs/p6-${SESSION}-${STAMP}-samples.csv"
SAMPLE_INTERVAL="${SAMPLE_INTERVAL:-300}"

cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs

fail() { echo "PRECONDITION FAILED: $*" >&2; exit 1; }

echo "== Procedure P6 preconditions =="
nc -z -G 2 127.0.0.1 "${IBKR_PORT:-4002}" 2>/dev/null || fail "no IB Gateway on port ${IBKR_PORT:-4002}"
echo "  IB Gateway port ${IBKR_PORT:-4002}: open"
nc -z -G 2 127.0.0.1 "${REDIS_PORT:-6379}" 2>/dev/null || fail "Redis not reachable (AR10: a live session always uses the Redis cache)"
echo "  Redis ${REDIS_PORT:-6379}: open"
nc -z -G 2 127.0.0.1 5432 2>/dev/null || fail "Postgres not reachable"
echo "  Postgres 5432: open"
uv run alembic current 2>/dev/null | grep -q '(head)' || fail "alembic is not at head; run 'alembic upgrade head'"
echo "  alembic: at head"
echo "  start time: $(TZ=America/New_York date '+%Y-%m-%d %H:%M:%S %Z')"

echo
echo "== Starting session '${SESSION}' =="
# Redirect straight to the file: in a backgrounded pipeline $! names the LAST
# command, so `... | tee log &` would make $! the tee process.
uv run python -m src.cli.main live start "${SESSION}" > "${LOG}" 2>&1 &
sleep 8
# Resolve the CLI process itself rather than trusting $!, which names `uv run`.
# `uv run` does not forward signals (Procedure P7, 2026-08-23), so the SIGTERM
# below would never reach the runner, its teardown would never execute, and the
# row would be left `running` — failing criterion 5 for a harness reason. The
# RSS samples must come from this process too, not from the wrapper.
RUNNER_PID="$(pgrep -f "bin/python3 -m src.cli.main live start ${SESSION}" | head -1)"
[ -n "${RUNNER_PID}" ] || {
    echo "could not resolve runner pid; last lines:" >&2
    tail -20 "${LOG}" >&2
    exit 1
}
echo "  runner pid: ${RUNNER_PID}"
echo "  log:        ${LOG}"
echo "  samples:    ${SAMPLES}"
ps -p "${RUNNER_PID}" -o pid=,command= >/dev/null 2>&1 || {
    echo "runner exited immediately; last lines:" >&2
    tail -20 "${LOG}" >&2
    exit 1
}

echo "sampled_at,rss_kb,status,last_heartbeat_at,last_bar_at,heartbeat_age_s" > "${SAMPLES}"

cleanup() {
    echo
    echo "== Stopping =="
    if ps -p "${RUNNER_PID}" >/dev/null 2>&1; then
        # SIGTERM, never SIGKILL: criterion 5 needs the row to end 'stopped'.
        kill -TERM "${RUNNER_PID}" 2>/dev/null
        for _ in $(seq 1 60); do
            ps -p "${RUNNER_PID}" >/dev/null 2>&1 || break
            sleep 1
        done
    fi
    echo "  runner stopped"
    summarise
}
trap cleanup INT TERM

summarise() {
    echo
    echo "== P6 evidence summary =="
    echo "-- phase records (criterion 1) --"
    grep -oE 'phase=[a-z:]+ .*status=(started|ok|failed)' "${LOG}" | head -20
    echo "-- bars received --"
    echo "  live_bars.received lines: $(grep -c 'live_bars.received' "${LOG}")"
    echo "-- data-farm health (the 2026-08-28 defect) --"
    for code in 10182 2103 2105 366; do
        n=$(grep -c "code: ${code}" "${LOG}")
        [ "${n}" -gt 0 ] && echo "  ⚠️  IB code ${code} seen ${n}x — a killed subscription is likely; bars may have stopped silently"
    done
    echo "-- final row (criteria 3 and 5) --"
    uv run python scripts/diagnostics/p8_query_flags.py "${SESSION}" 2>/dev/null | tail -6
    echo "-- samples --"
    echo "  ${SAMPLES} ($(($(wc -l < "${SAMPLES}") - 1)) samples)"
}

echo
echo "== Sampling every ${SAMPLE_INTERVAL}s (Ctrl-C to stop and summarise) =="
while ps -p "${RUNNER_PID}" >/dev/null 2>&1; do
    rss=$(ps -o rss= -p "${RUNNER_PID}" 2>/dev/null | tr -d ' ')
    row=$(uv run python - "${SESSION}" <<'PY' 2>/dev/null
import sys
from sqlalchemy import text
from src.db.session_sync import get_sync_session
q = """SELECT status, last_heartbeat_at, last_bar_at,
       EXTRACT(EPOCH FROM (now() - last_heartbeat_at)) FROM trading_sessions WHERE name=:n"""
with get_sync_session() as s:
    r = s.execute(text(q), {"n": sys.argv[1]}).one_or_none()
print(",".join("" if v is None else str(v) for v in r) if r else ",,,")
PY
)
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),${rss:-},${row}" >> "${SAMPLES}"
    sleep "${SAMPLE_INTERVAL}"
done

echo "runner exited on its own"
summarise
