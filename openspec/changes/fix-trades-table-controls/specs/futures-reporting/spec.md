## ADDED Requirements

### Requirement: Trades-table controls update the table in place
The run detail page's trades table SHALL serve its page-size, sort and pagination controls
from the HTML partial route (`/backtests/<id>/trades-table`). Each control SHALL swap the
partial's own root (`#trades-table-content`), for equity and futures runs alike.

#### Scenario: next page
- **WHEN** page 1 of an 80-trade run at 20 per page is shown and Next is clicked
- **THEN** the table shows trades 21-40 and "Page 2 of 4", and its controls still work

#### Scenario: sort by P&L
- **WHEN** the P&L header is clicked
- **THEN** the table shows page 1 ordered by P&L ascending; clicking again orders descending
