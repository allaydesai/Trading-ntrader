"""Robustness analyses over stored backtest results (research MCP phase 4).

Pure numpy / pandas / scipy: parameter grids, Monte Carlo over trades,
bootstrap confidence intervals, the deflated Sharpe ratio, parameter
sensitivity and sliced walk-forward. No Nautilus, no database: callers pass
what a stored run holds (closed trades, a daily equity curve, metrics).
"""
