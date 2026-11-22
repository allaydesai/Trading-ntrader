# Tasks

## 1. Calculation
- [x] 1.1 Unit (failing first): `tests/unit/services/futures/test_returns.py`: a
      hand-computed ES example (2-lot long win, 1-lot short loss): contracts, notional,
      margin used, side-aware points and return on the underlying, return on margin per trade;
      run net P&L, peak margin, return on margin and the compounded underlying return;
      overlapping trades sum margin; a close-and-reverse on one timestamp does not; equity
      snapshot gives `None`; open trades have no return; dict rows work like ORM rows.
- [x] 1.2 Implement `src/services/futures/returns.py`.
- [x] 1.3 Unit: `tests/unit/services/futures/test_display.py`: text rows and the
      ratio-adjustment note. Implement `src/services/futures/display.py`.

## 2. Sizing
- [x] 2.1 Unit (failing first): `tests/unit/strategies/test_sma_crossover_position_sizing.py`:
      fixed contracts on ES; fixed contracts on an equity raises; unset is unchanged.
- [x] 2.2 `SMAConfig.contracts`, `_sized_quantity(fixed_contracts)`.
- [x] 2.3 Unit (failing first): `tests/component/cli/test_backtest_contracts_option.py` (component: imports Nautilus): the flag
      reaches the strategy config (CLI and config mode), `--contracts 0` exits 2, a strategy
      without `contracts` exits 2, an equity symbol exits 2, a loaded non-futures instrument
      or a bad config-file value fails the run, help text.
- [x] 2.4 Wire `--contracts` through `backtest run`.

## 3. Renderers (CLI and MCP)
- [x] 3.1 Unit (failing first): `show`/`report`/`history` render the futures block for a
      futures run and are unchanged for an equity run (`tests/unit/cli/test_futures_reporting_cli.py`).
- [x] 3.2 Implement them in `show.py`, `report.py`, `history.py`.
- [x] 3.3 Unit (failing first): MCP `run_view` gains `asset_class`/`futures`; the export adds
      futures columns only when a futures run is exported (`tests/unit/mcp_server/test_futures_fields.py`).
- [x] 3.4 Implement them in `src/mcp_server/runs.py` and `export.py`.

## 4. Integration
- [x] 4.1 Integration, forked: a real ES 1-hour `sma_crossover` run with `contracts=1` through
      the orchestrator trades exactly 1 contract, and `summarise_run` on its trades matches a
      hand-recomputed return on margin.

## 5. Docs and manual check
- [x] 5.1 `docs/agent/futures-backtesting.md` (reporting, `--contracts`), README pointer.
- [x] 5.2 Manual (2026-11-22): ES and CL 1-hour, 2024-01-01..06-30, `--contracts 1`,
      $100k. ES `b3cefbe0`: 80 trades, net $15,420.50 (315.61 pts x 50 - 80 x 4.50), RoM
      +56.07%. CL `c1a7648e`: 78 trades, net $328.28, RoM +4.97% (margin 6,600). `show`,
      `history`, `report` (csv/json) checked. Dashboard pages return 200 equity-style. Found:
      the trades-table fragment calls `localhost:8000` (hardcoded, frozen
      `src/api/ui/backtests.py`), so on port 3000 it 500s unless an 8000 instance runs.
- [ ] 5.3 Dashboard (blocked): the frozen-view edits in `design.md`, after a PO ruling.
