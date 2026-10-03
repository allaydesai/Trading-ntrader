"""NTrader research MCP server (docs/product/NTrader MCP Server — Spec.md).

A local stdio server Claude Desktop launches on the Mac. It validates, runs,
compares and files backtests; every backtest runs in a fresh worker process
on the same code path as ``backtest run``. It never touches live trading.
"""
