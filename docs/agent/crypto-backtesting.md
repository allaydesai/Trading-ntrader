# Crypto backtesting: coins, instruments, account model

Spot crypto on Kraken, planned in `openspec/changes/add-crypto-backtesting/` (milestones
M1-M7; decisions C1-C23 in `design.md` section 13). **Status: M1 implemented** (coin specs,
instruments, catalog dispatch, engine spike). Nothing yet imports crypto data (M2), charges
Kraken fees or enforces spot rules (M3), sizes fractionally (M4) or reports crypto (M5), so
**no crypto backtest is meaningful before M3 merges**.

## Ids and instruments

| Coin | Catalog `ticker` | Instrument id | `raw_symbol` | Bars |
|---|---|---|---|---|
| BTC | `BTC` | `BTC/USD.KRAKEN` | `XBTUSD` | `BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL` |
| ETH | `ETH` | `ETH/USD.KRAKEN` | `ETHUSD` | `...-LAST-EXTERNAL` |
| SOL | `SOL` | `SOL/USD.KRAKEN` | `SOLUSD` | `...-LAST-EXTERNAL` |

The id is `KrakenPairMapper.to_nautilus_id("BTC/USD")`, so the FirstRate catalog (M2) and the
Kraken fetch path (M7) name the same instrument (decision C2).

- `src/services/crypto/coin_specs.yaml` + `coin_specs.py`: per-coin specs (USD quote only,
  price precision 4, size precision 8 / increment 1e-8, modelled spread, Kraken block) and the
  17-tier Kraken Pro spot fee schedule (`get_fee_schedule()`, default tier 1). Invalid files
  raise `CryptoSpecError` naming the dotted field.
- `src/services/crypto/instrument_builder.py`: `build_crypto_pair(spec, nautilus_id=...,
  price_precision=...)` gives a Nautilus `CurrencyPair` (Nautilus `asset_class`
  `CRYPTOCURRENCY`), margins 1 (unlevered), instrument fees 0 (the M3 venue fee model
  charges), `info.asset_class = "CRYPTO"` plus the Kraken terms, the schedule's
  `source`/`as_of`/`default_tier`, and the UNVERIFIED field list.
- `build_instrument("CRYPTO", ...)` (`src/services/firstrate/backtest_loader.py`) routes a
  catalog row to it, with price precision taken from the bars.
- `is_crypto(instrument)` (`src/core/position_sizing.py`, duck-typed on
  `info.asset_class == "CRYPTO"`) is the predicate later milestones key on. `is_fx`,
  `is_fx_pair` and `needs_conversion` stay false for crypto.

## UNVERIFIED values (to confirm before M3 merges)

| Value | Where | Default |
|---|---|---|
| Kraken altname (raw symbol) | `coins.*.kraken.altname` | XBTUSD / ETHUSD / SOLUSD |
| Kraken REST symbol | `coins.*.kraken.rest` | mapper output; SOL gives `SOLZUSD`, Kraken may say `SOLUSD` |
| Order minimum (base) | `coins.*.kraken.min_order_base` | 0.00005 BTC / 0.002 ETH / 0.02 SOL |
| Order minimum (USD) | `coins.*.kraken.min_order_usd` | 0.5 |
| Tick size (live only) | `coins.*.kraken.tick_size` | 0.1 / 0.01 / 0.01 |
| Discounted pair (C23) | `coins.*.kraken.discounted_pair` | false |
| Spread (C4) | `coins.*.typical_spread_bps` | 2 / 3 / 5 bp |
| Fee tier (C21) | `fee_schedule.default_tier` | 1 |
| Fee currency | `fee_schedule.fee_currency` | USD (the page does not say) |

The tier rates (tier 1 0.40% maker / 0.80% taker down to tier 17 0.00% / 0.05%), the 0.02%
discounted-pair rebate and the absence of a minimum fee are VERIFIED against
<https://www.kraken.com/ca/pro/fees> (read 2026-11-24).

## Account model: the M1 spike (decision C8)

`tests/integration/core/test_crypto_engine_acceptance.py` (run `--forked`) settled it:
**model A**, a `MARGIN` venue with `base_currency=USD`, `default_leverage=1`, instrument
`margin_init=1`, **plus the margin gate**.

- A 0.128 BTC market order fills at `0.12800000`; P&L lands in the single USD balance.
- With a prototype gate (shared `MarginGate`, `qty x price` requirement, no-short rule) the
  bracket scenario runs and the over-cash buy (`MARGIN_EXCEEDS_FREE_BALANCE`) and naked sell
  (`CRYPTO_SPOT_NO_SHORT`) are denied. **Without the gate nothing is enforced**: Nautilus skips
  risk checks for margin accounts, so the USD 156k buy fills on USD 10k and the short opens.
- `CASH` is rejected: single-currency refuses the `CurrencyPair`; multi-currency leaves market
  orders unchecked in a bar-only run, allows shorts, stops the run on a negative balance and
  leaves the books inconsistent.
- Under `HEDGING` a `SELL` without a position id is a new short, so M3's rule allows a
  parentless `SELL` only against an open long it does not oversell.
- Bar volume never caps fill size; the part beyond the bar volume fills one increment
  (0.0001) worse. Bar volume must carry precision 8 (the size precision) or the run stops.

Details and the numbers pinned: `design.md` section 1, "Spike result".
