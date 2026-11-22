# Tasks

## 1. Calculation
- [ ] 1.1 Unit (failing first): `tests/unit/services/futures/test_returns.py`: a
      hand-computed ES example (2-lot long win, 1-lot short loss): contracts, notional,
      margin used, side-aware points and return on the underlying, return on margin per trade;
      run net P&L, peak margin, return on margin and the compounded underlying return;
      overlapping trades sum margin; a close-and-reverse on one timestamp does not; equity
      snapshot gives `None`; open trades have no return; dict rows work like ORM rows.
- [ ] 1.2 Implement `src/services/futures/returns.py`.
- [ ] 1.3 Unit: `tests/unit/services/futures/test_display.py`: text rows and the
      ratio-adjustment note. Implement `src/services/futures/display.py`.

## 2. Sizing
- [ ] 2.1 Unit (failing first): `tests/unit/strategies/test_sma_crossover_position_sizing.py`:
      fixed contracts on ES; fixed contracts on an equity raises; unset is unchanged.
- [ ] 2.2 `SMAConfig.contracts`, `_sized_quantity(fixed_contracts)`.
- [ ] 2.3 Unit (failing first): `tests/unit/cli/test_backtest_contracts_option.py`: the flag
      reaches the strategy config (CLI and config mode), `--contracts 0` exits 2, a strategy
      without `contracts` exits 2, a non-futures instrument is a usage error, help text.
- [ ] 2.4 Wire `--contracts` through `backtest run`.

## 3. Renderers (CLI and MCP)
- [ ] 3.1 Unit (failing first): `show`/`report`/`history` render the futures block for a
      futures run and are unchanged for an equity run (`tests/unit/cli/test_futures_reporting_cli.py`).
- [ ] 3.2 Implement them in `show.py`, `report.py`, `history.py`.
- [ ] 3.3 Unit (failing first): MCP `run_view` gains `asset_class`/`futures`; the export adds
      futures columns only when a futures run is exported (`tests/unit/mcp_server/test_futures_fields.py`).
- [ ] 3.4 Implement them in `src/mcp_server/runs.py` and `export.py`.

## 4. Integration
- [ ] 4.1 Integration, forked: a real ES 1-hour `sma_crossover` run with `contracts=1` through
      the orchestrator trades exactly 1 contract, and `summarise_run` on its trades matches a
      hand-recomputed return on margin.

## 5. Docs and manual check
- [ ] 5.1 `docs/agent/futures-backtesting.md` (reporting, `--contracts`), README pointer.
- [ ] 5.2 Manual: ES and CL 1-hour runs via the CLI with `--contracts 1`; `backtest show` and
      `report generate`; the dashboard pages fetched with curl (equity-style until the frozen
      views are ruled on).
- [ ] 5.3 Dashboard (blocked): the frozen-view edits in `design.md`, after a PO ruling.
