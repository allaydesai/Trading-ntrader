# Design: futures results in reports, CLI and dashboard (M4)

## One calculation, many renderers

Every surface (CLI `show`/`report`/`history`, MCP run views and export, and later the
dashboard) calls `src/services/futures/returns.py`. It is pure: inputs are the run's
`config_snapshot` and duck-typed trade rows (ORM `Trade` or a dict with the same keys), so
renderers can't drift and the hand-computed tests cover them all.

An equity run (no `futures_contracts` key) gives `None` everywhere. Each renderer adds its
futures block only when it gets a summary, so equity output stays byte for byte as before.

## Definitions

Per trade, from the snapshot terms of its `instrument_id`:

| Figure | Formula |
|---|---|
| contracts | `quantity` |
| notional | `quantity x entry_price x multiplier` |
| margin used | `quantity x margin_initial_usd` |
| points (adjusted) | `exit - entry` for a long (`order_side` BUY), `entry - exit` for a short |
| return on the underlying | `points / entry x 100` (side-aware) |
| return on margin | `profit_loss / margin used x 100` (net of commissions) |

Stored `profit_pct` ignores the side (M3, same formula as equities). The futures figures are
side-aware, so a winning short is positive. Only closed trades (exit price and P&L set) have
points and returns.

Per run:

- **net P&L**: the sum of closed trades' `profit_loss`.
- **margin base**: the *peak concurrent* initial margin. A sweep over entry (+margin) and
  exit (-margin) times, with exits before entries at the same timestamp so a close-and-reverse
  on one bar is not counted twice. An open trade holds its margin to the end. For a strategy
  that holds one position at a time this is `max(contracts) x initial margin`, exactly the
  approved `P&L / (contracts x initial margin)`. For overlapping positions it is the margin
  actually tied up at once, which a per-trade max would understate.
- **return on margin** (headline): `net P&L / margin base x 100`.
- **return on the underlying**: the side-aware per-trade returns compounded in exit order,
  `(prod(1 + r_i) - 1) x 100`. It is the unlevered return of trading 1x notional, independent
  of size, and is unaffected by ratio adjustment.

Return on the account's capital (`total_return`), Sharpe, drawdown and so on stay as they are:
they are on the account and still correct.

## Ratio adjustment

Dollar P&L and points of old trades are ratio-scaled. Every renderer that shows dollars or
points for futures labels them "ratio-adjusted" and prints one explanation line
(`RATIO_ADJUSTED_NOTE`). Percentages are shown first.

## `--contracts N`

- `SMAConfig.contracts: int | None = None`. `_sized_quantity(..., fixed_contracts)` returns
  exactly N contracts for a multiplied instrument and raises `ValueError` for a
  multiplier-1 instrument (a backstop: the CLI rejects it first). The `SMACrossover` class
  doesn't grow (baseline 101): the call passes `self.config.contracts`.
- CLI validation, in order:
  - `click.IntRange(min=1)` (exit 2).
  - The strategy's config class must declare `contracts`, otherwise a usage error (exit 2).
  - The symbol must be a futures root in `contract_specs.yaml`, otherwise a usage error
    (exit 2). This runs before any data loads.
  - After the instrument loads, `execute_backtest` checks `check_contracts_instrument`: the
    value is an int >= 1 (a config file can hold anything) and the instrument is multiplied.
    This catches a symbol that only collides with a root (an equity `CL` in another
    catalog). It fails the run before the engine starts ("Backtest failed: ...", exit 1),
    because `run_backtest` (size-capped, baseline 173) has no room for another handler.
  - A margin shortfall is left to M3's gate (`OrderDenied`), as for any futures order.
- Config files: `contracts:` in the strategy `config:` section reaches `SMAConfig` like any
  other key; `--contracts` overrides it in config mode.

## The dashboard: blocked on `FROZEN_VIEWS`

`FROZEN_VIEWS` pins every file in `src/api/**` and `templates/**`, plus
`src/services/backtest_query.py` and `src/cli/commands/compare.py`. Adding a file in those
trees also fails it. The dashboard ACs can't be met without changing frozen views. Per the
task's rule ("STOP and report") and the guard ("only a recorded PO decision may regenerate
it"), this change doesn't touch them. The edits needed once the PO rules:

| Frozen file | Edit | Why it can't be avoided |
|---|---|---|
| `src/api/models/backtest_detail.py` | `BacktestDetailView.futures: FuturesRunSummary \| None`, set in `to_detail_view` from `summarise_run(run.config_snapshot, run.trades)` | the detail view model is built only here |
| `templates/partials/futures_panel.html` (new) | headline return on margin, return on the underlying, contracts, multiplier, notional, peak margin, terms with as-of date, the ratio-adjustment note | a new file in `templates/` is "added" |
| `templates/backtests/detail.html` | `{% if view.futures %}{% include "partials/futures_panel.html" %}{% endif %}` above the metrics panel | the only include point |
| `src/api/ui/backtests.py` (trades-table route) | pass `futures_rows` (`trade_fields` per row) when the run is futures | the route builds the fragment's context |
| `templates/partials/trades_table.html` | futures branch: "Contracts" heading, Points (adj.), Return on underlying, Return on margin; dollar P&L dimmed with "(adj.)" | the only trades table |
| `src/api/rest/trades.py` (`export_trades`) | futures columns in the CSV/JSON export | the dashboard export lives here |
| `src/api/models/backtest_list.py`, `templates/backtests/list_fragment.html` | asset-class badge; return on margin in the Return column for futures | the run list |
| `src/services/backtest_query.py` | load trades for futures runs in the list query | the list's data source |
| `src/cli/commands/compare.py` | asset row, return on margin row | the comparison view |

Each edit is a branch on `view.futures` / `asset_class`, so equity pages render exactly as now.
That can be checked by rendering an equity run before and after. The manifest is then
regenerated under the recorded ruling.

## Alternatives rejected

- **Inject the panel from `static/js`** (not frozen) by fetching existing endpoints. It
  changes the UI, which AR44 forbids, just not where the pin looks. Gaming the guard.
- **Mount a router or template directory outside `src/api`/`templates`.** It would still need
  `web.py` (frozen) to register it. Same objection.
- **Store display-ready strings in `config_snapshot`** so the frozen `additional_params`
  loop shows them. Bloats the record, misses old runs, and still changes what the page shows.
