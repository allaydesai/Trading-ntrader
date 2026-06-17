# Product

## Register

product

## Users

A quant trader/developer (currently a single power user, potentially a small circle of similar users) running and analyzing algorithmic trading backtests. Context: long focused desk sessions, dark room or evening work, switching between the CLI and the web UI. The job to be done on any screen is one of three workflows: **import/inspect market data** (Explorer), **configure and run a backtest** (Run form), or **analyze results** (Backtests list/detail — metrics, equity curves, trade tables).

## Product Purpose

NTrader is a production-grade backtesting platform built on Nautilus Trader with IBKR/Kraken/FirstRate data. The web UI exists to make backtest results *legible*: browse history, compare runs, read performance metrics, and inspect charts without leaving the browser. Success looks like: the user trusts a number the moment they see it, finds any past run in seconds, and never wonders whether the UI is showing stale or wrong data.

## Brand Personality

**Precise, confident, refined.** A modern quant workbench — the seriousness of a professional terminal with the polish of a premium fintech dashboard (Stripe-dashboard caliber, TradingView chart fluency). Numbers are the heroes; the chrome recedes. Calm under data density: a screen full of metrics should feel organized, not loud.

## Anti-references

- **Crypto-bro trading apps**: neon green/red glow, dark-mode-for-coolness, gamified streaks, hype gradients. Profit/loss color is semantic information, never decoration.
- Cookie-cutter SaaS admin templates: identical stat-card grids, decorative icons on everything, gradient accent abuse.

## Design Principles

1. **Numbers first, chrome last.** Every visual decision is judged by whether it makes the data faster to read. Tabular numerals, aligned decimal columns, restrained surfaces.
2. **Earned familiarity.** Use the affordances quants already know (TradingView charts, terminal-style tables, standard nav). Novelty only where it removes friction, never for flavor.
3. **Semantic color only.** The accent marks actions and selection; red/green mark loss/profit and nothing else. No color as mood.
4. **State is always visible.** Long-running backtests, loading panels, empty catalogs, failed runs — every async state has an honest, designed representation (skeletons, empty states that teach, explicit errors).
5. **Density with hierarchy.** Power users want information-rich screens; deliver density through clear type scale, spacing rhythm, and alignment — never by shrinking everything uniformly.

## Accessibility & Inclusion

- WCAG AA: ≥4.5:1 body-text contrast (≥3:1 for large text) against dark surfaces; visible focus states on all interactive elements.
- `prefers-reduced-motion` respected on every transition.
- Keyboard-operable navigation, forms, and tables.
- Profit/loss encoding should not rely on red/green hue alone where practical (pair with sign/formatting, which the metrics already carry).
