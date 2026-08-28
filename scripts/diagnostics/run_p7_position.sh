#!/usr/bin/env bash
# Procedure P7 criterion 2 — the "with a position open" half.
#
# The 2026-08-23 P7 run met five of six criteria but could only run criterion 2
# with **no** session-owned position: it was a Sunday, `use_rth=True` means no
# bar closes, so no session could open one and `sma_crossover.on_stop()`'s
# `close_all_positions()` had nothing of its own to flatten. This script runs
# the missing half inside RTH: it starts a session, waits until the strategy has
# actually opened a position at the broker, records the account state, stops it
# with a real SIGINT, and records the state again.
#
# Expect the position to be FLATTENED, not preserved. `sma_crossover.on_stop()`
# still calls `close_all_positions()` — Story 3.1's to remove — and P7's own
# text says so. The criterion is that broker state is identical *except* for
# what `on_stop()` flattened, so the run has to show which of the two happened.
#
# Usage:  ./scripts/diagnostics/run_p7_position.sh [session-name] [max-wait-seconds]

set -uo pipefail

SESSION="${1:-p7-position-test}"
MAX_WAIT="${2:-2400}"
LOG="logs/p7-position-$(date +%Y%m%d-%H%M%S).log"

cd "$(dirname "$0")/../.." || exit 1
mkdir -p logs

# Last reported account snapshot, which the IB adapter prints on every update.
snapshot() {
    grep -oE "'NetLiquidation': [0-9.]+|'GrossPositionValue': [0-9.]+" "${LOG}" | tail -2 | tr '\n' ' '
    echo
}
positions() { grep -oE 'Position(Opened|Closed)\([^)]*\)|Residual Position\([^)]*\)' "${LOG}" | tail -5; }

echo "== P7 criterion 2: starting '${SESSION}' =="
uv run python -m src.cli.main live start "${SESSION}" > "${LOG}" 2>&1 &
sleep 8
# Signal the CLI process itself, never `uv run`: this procedure's own 2026-08-23
# entry records that `uv run` does not forward signals, so a SIGINT sent to $!
# would never reach the runner and criterion 1's graceful stop would look broken.
RUNNER_PID="$(pgrep -f "bin/python3 -m src.cli.main live start ${SESSION}" | head -1)"
[ -n "${RUNNER_PID}" ] || { echo "could not resolve runner pid"; tail -25 "${LOG}"; exit 1; }
echo "runner pid: ${RUNNER_PID}"
ps -p "${RUNNER_PID}" >/dev/null 2>&1 || { echo "runner died"; tail -25 "${LOG}"; exit 1; }

echo
echo "== waiting up to ${MAX_WAIT}s for the strategy to open a position =="
FOUND=""
ELAPSED=0
while [ "${ELAPSED}" -lt "${MAX_WAIT}" ]; do
    ps -p "${RUNNER_PID}" >/dev/null 2>&1 || { echo "runner exited early"; break; }
    # Wait for a real FILL, not merely a signal. MEASURED 2026-08-28: stopping on
    # `Generated BUY signal` tore the session down ~4s later, and the order never
    # got past `OrderInitialized` — no submission, no fill, no position, so the
    # criterion's whole premise was missing. `PositionOpened` is the event that
    # actually means the broker holds something.
    if grep -qE 'PositionOpened\(' "${LOG}" 2>/dev/null; then
        FOUND=yes
        break
    fi
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    [ $((ELAPSED % 60)) -eq 0 ] && echo "  ${ELAPSED}s: no signal yet (bars: $(grep -c 'live_bars.received' "${LOG}"))"
done

echo
if [ -n "${FOUND}" ]; then
    echo "== a position was opened; letting it settle for 20s before stopping =="
    sleep 20
    grep -E 'Generated (BUY|SELL) signal|PositionOpened|Position sizing|OrderFilled' "${LOG}" | tail -6
else
    echo "== NO position was opened within ${MAX_WAIT}s =="
    echo "   Criterion 2's position-open half cannot be closed by this run; report it as such."
fi

echo
echo "== account snapshot BEFORE stop =="
snapshot
positions

echo
echo "== stopping with a real SIGINT =="
kill -INT "${RUNNER_PID}"
for _ in $(seq 1 90); do ps -p "${RUNNER_PID}" >/dev/null 2>&1 || break; sleep 1; done
ps -p "${RUNNER_PID}" >/dev/null 2>&1 && { echo "did not exit in 90s; forcing"; kill -INT "${RUNNER_PID}"; sleep 10; }
echo "runner exited"

echo
echo "== account snapshot AFTER stop =="
snapshot
echo "-- position events across the whole run --"
grep -oE 'Position(Opened|Closed|Changed)\([^)]*\)|Residual Position\([^)]*\)' "${LOG}" | tail -10
echo "-- did on_stop() flatten? --"
grep -cE 'close_all_positions|Closing position|CLOSE_POSITION' "${LOG}" | sed 's/^/  close-ish lines: /'
grep -E 'session.stopped|Session stopped' "${LOG}" | tail -2
echo
echo "log: ${LOG}"
