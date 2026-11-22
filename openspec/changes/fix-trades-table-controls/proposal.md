# Proposal: trades-table controls request the HTML partial

## Why

On the run detail page, the trades table's page-size select, sort headers and pagination
buttons have never worked:

- they request `/api/backtests/<id>/trades`, which returns JSON, and htmx swaps that JSON
  into the page;
- they target `#trades-table-content`, but the first swap replaces that container with the
  partial, which has no element with that id, so later requests have no target;
- the page-size select's `hx-include="[name='sort_by'],[name='sort_order']"` matches no
  inputs, so changing the page size dropped the sort.

CodeRabbit raised this on PR #53 (outside-diff Major). It predates that PR. Equity and futures
tables both had it.

**Approval: Allay approved on 2026-11-22: the trades-table controls fix plus the equity
golden update.**

## What changes

`templates/partials/trades_table.html` only:

- the partial's root is `<div id="trades-table-content">`, so each `outerHTML` swap keeps the
  target;
- every control requests `/backtests/<id>/trades-table` (the HTML partial);
- the page-size select carries the current sort in its URL and restarts at page 1;
- one `sort_button` macro renders the sort headers. Entry Date, Exit Date and P&L are now
  sortable in the futures table too, and a sort change restarts at page 1.

## Interfaces crossed

- None. No route, model or API change. The `/backtests/<id>/trades-table` route (in-process
  since #53) already accepted these parameters.

## Guard lists / pinned constants

- `FROZEN_VIEWS`: regenerated for `templates/partials/trades_table.html` only, under the
  ruling above.
- The equity golden `tests/fixtures/equity_dashboard_golden/trades_table.html` was recaptured
  under the same ruling. The other equity goldens are unchanged.

## Out of scope

- Changing what the trades API returns. Any other dashboard view.
