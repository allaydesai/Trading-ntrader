# Tasks

- [x] 1. Component (failing first), equity and futures alike
      (`tests/component/api/test_futures_dashboard.py::TestTradesTableControls`):
      - the fragment root is the swap target;
      - no control requests the JSON API;
      - page size keeps the sort and starts at page 1;
      - each sort header toggles its field, resets the page, and reaches the query;
      - each page button requests its page.
- [x] 2. Fix `templates/partials/trades_table.html`.
- [x] 3. Recapture only the equity `trades_table.html` golden, and regenerate `FROZEN_VIEWS`
      for `trades_table.html` only (both under the ruling).
- [x] 4. Manual (2026-11-22, 127.0.0.1:3000, ES `b3cefbe0`): follow each rendered control
      URL. Next goes to page 2 (rows 21-40), Last to page 4, P&L asc/desc and Entry Date desc
      reorder the rows, and page size 50 returns 50 rows.
