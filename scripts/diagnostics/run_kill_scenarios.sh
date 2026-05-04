#!/usr/bin/env bash
# Story 3.4 — IBKR Gateway SIGKILL-during-fetch reconnect scenarios.
#
# Reproduces the closest analog of Run 1's wedge: a pytest run got
# pkill'd while the IBKR client was actively fetching/streaming bars.
# We test whether reconnecting after that succeeds with the same and
# with a rotated client_id.
set -u

cd "$(dirname "$0")/../.."
PROBE_CMD="PYTHONPATH=. uv run python scripts/diagnostics/ibkr_reconnect_probe.py"

run_kill_scenario() {
    local label="$1"
    local kill_id="$2"
    local reconnect_id="$3"
    local pre_kill_sleep="$4"  # seconds to wait before SIGKILL
    local fetch_log="/tmp/ibkr-kill-${label}-fetch.log"
    local reconnect_log="/tmp/ibkr-kill-${label}-reconnect.log"

    echo "--- ${label}: kill client_id=${kill_id} -> reconnect client_id=${reconnect_id} ---"

    # Spawn fetch-then-hang in background
    bash -c "$PROBE_CMD --mode fetch-then-hang --client-id $kill_id > $fetch_log 2>&1" &
    local victim_pid=$!
    echo "[orchestrator] spawned victim pid=$victim_pid client_id=$kill_id"

    sleep "$pre_kill_sleep"
    echo "[orchestrator] sending SIGKILL to pid=$victim_pid (after ${pre_kill_sleep}s)"
    kill -9 "$victim_pid" 2>/dev/null
    wait "$victim_pid" 2>/dev/null

    # Brief pause before reconnect
    sleep 1
    echo "[orchestrator] attempting reconnect with client_id=$reconnect_id"
    bash -c "$PROBE_CMD --mode handshake-time --client-id $reconnect_id > $reconnect_log 2>&1"
    local rc=$?
    grep -E "^(RESULT:|\[probe\])" "$reconnect_log" || tail -5 "$reconnect_log"
    echo "rc=$rc reconnect_log=$reconnect_log"
    return $rc
}

echo "=== Scenario D1: SIGKILL mid-handshake (1s) same client_id=101 ==="
run_kill_scenario d1-handshake 101 101 1.5

echo
echo "=== Scenario D2: SIGKILL during fetch (5s in) same client_id=102 ==="
run_kill_scenario d2-fetch 102 102 5

echo
echo "=== Scenario E: SIGKILL during fetch + rotate client_id 103 -> 104 ==="
run_kill_scenario e-rotate 103 104 5

echo
echo "=== Scenario F: SIGKILL during fetch + same id, retry after 10s ==="
echo "(tests whether the Gateway reclaims the client_id after a delay)"
echo "--- f-delayed: kill client_id=105 -> sleep 10s -> reconnect client_id=105 ---"
fetch_log=/tmp/ibkr-kill-f-fetch.log
reconnect_log=/tmp/ibkr-kill-f-reconnect.log
bash -c "$PROBE_CMD --mode fetch-then-hang --client-id 105 > $fetch_log 2>&1" &
victim_pid=$!
echo "[orchestrator] spawned victim pid=$victim_pid client_id=105"
sleep 5
echo "[orchestrator] sending SIGKILL"
kill -9 "$victim_pid" 2>/dev/null
wait "$victim_pid" 2>/dev/null
echo "[orchestrator] sleeping 10s before reconnect"
sleep 10
bash -c "$PROBE_CMD --mode handshake-time --client-id 105 > $reconnect_log 2>&1"
rc=$?
grep -E "^(RESULT:|\[probe\])" "$reconnect_log" || tail -5 "$reconnect_log"
echo "rc=$rc reconnect_log=$reconnect_log"
