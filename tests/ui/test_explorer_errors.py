"""Explorer error-state UI evidence log (Story 2-4 Task 2.1).

This module is intentionally documentation-only — there are no pytest-collectable
test functions here. Automated coverage for AC #8 / AC #9 lives in
``tests/component/api/test_explorer_routes.py::TestBaseHtmlErrorHandler`` and
``tests/component/api/test_stats_panel_routes.py``. This file exists as a
checked-in record of the manual ``agent-browser`` verification steps taken for
inline error states, so that the audit trail is preserved alongside the code.

Manual verification procedure (run against ``http://127.0.0.1:8000`` with a
populated ``e2e-test`` catalog):

1.  Start the dev server:
        ``uv run uvicorn src.api.web:app --host 127.0.0.1 --port 8000``
2.  Navigate to ``/explorer?catalog=e2e-test``. ``agent-browser snapshot -i``.
3.  Force a 5xx by visiting ``/explorer/chart-panel?catalog=bad&ticker=bad&tf=D``
    (or temporarily patching the route). Confirm the ``#chart-panel`` region is
    replaced with an inline ``Failed to load chart data for …`` message — no
    browser alert and no global toast (AC #8). Screenshot
    ``/tmp/story-2-4-evidence/chart_5xx.png``.
4.  With the DevTools Network panel set to *Offline*, click a ticker row.
    Verify the request retries exactly once (look for the second request
    carrying ``X-NTrader-Retry: 1``). When the retry also fails the chart panel
    shows ``Connection error. Refresh to retry.`` inline (AC #9). Screenshot
    ``/tmp/story-2-4-evidence/chart_offline.png``.
5.  Re-enable the network, ``agent-browser screenshot`` the explorer back in a
    healthy state to confirm recovery.

Pass criteria:
    * Every failure renders inline inside the affected fragment's container.
    * Network failures retry once and only once.
    * No ``alert()``, no toast library, no full-page banner is introduced.
"""
