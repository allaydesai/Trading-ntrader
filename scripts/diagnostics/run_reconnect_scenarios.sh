#!/usr/bin/env bash
# Story 3.4 — IBKR Gateway reconnect scenarios.
#
# Each scenario uses a distinct client_id so failures don't poison the
# others. Output goes to /tmp/ibkr-probe-<scenario>.log; the final
# RESULT lines are surfaced via stdout.
set -u

cd "$(dirname "$0")/../.."

PROBE="PYTHONPATH=. uv run python scripts/diagnostics/ibkr_reconnect_probe.py"

run_probe() {
    local label="$1"; shift
    local logfile="/tmp/ibkr-probe-${label}.log"
    echo "--- ${label} ---"
    bash -c "$PROBE $* > $logfile 2>&1"
    local rc=$?
    grep -E "^(RESULT:|\[probe\])" "$logfile" || tail -5 "$logfile"
    echo "rc=$rc log=$logfile"
    return $rc
}

echo "=== Scenario A: clean-then-clean (baseline) client_id=91 ==="
run_probe a1-clean --mode clean --client-id 91
run_probe a2-clean --mode clean --client-id 91

echo
echo "=== Scenario B: no-stop-then-reconnect (reproduction) client_id=92 ==="
run_probe b1-no-stop --mode no-stop --client-id 92
echo "[orchestrator] sleeping 1s before reconnect"
sleep 1
run_probe b2-clean --mode handshake-time --client-id 92

echo
echo "=== Scenario C: client_id rotation workaround client_id=93 -> 94 ==="
run_probe c1-no-stop --mode no-stop --client-id 93
echo "[orchestrator] sleeping 1s before reconnect"
sleep 1
run_probe c2-rotate --mode handshake-time --client-id 94
