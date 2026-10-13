# Venue backfill evidence — 2026-07-25

Record of the IBKR venue sweep that closed the Phase 2 venue-coverage gate, and of
the decisions taken on its output. Kept in-tree because `venue_exclusions.csv` rows
cite `results.csv` as their evidence, and `logs/` is gitignored.

## The sweep

All **4,612** ETF tickers were qualified against IBKR's contract database (Gateway
paper, `secType=STK`, `exchange=SMART`, `currency=USD`, no `primaryExchange` hint).
14.5 minutes, 6 req/s, 20 concurrent lanes, **zero errors, zero reconnects**.

| Outcome | Count |
|---|---|
| resolved | 3,953 |
| not_found | 658 |
| ambiguous | 1 |

Resolved venues: ARCA 2,259 · NASDAQ 949 · BATS 684 · NYSE 59 · PINK 2. Every value
fell inside the reviewed venue set — nothing exotic needed adjudication.

`--include-resolved` re-verified the 1,161 tickers FMP had already resolved. **One**
disagreement (`RWEM`: FMP NYSE → IBKR ARCA), and IBKR independently confirmed both
hand-curated overrides (`SPY`→ARCA, `GLD`→ARCA) that predate this tool.

## Why the conservative map was right

The two dominant corrections are **AMEX→ARCA (2,255 tickers)** and **CBOE→BATS
(683)** — precisely the FirstRate labels ADR-6 refused to guess from. Had the map
been padded to reduce the unresolved count, ~2,900 tickers would have been silently
stamped with the wrong venue.

## Decisions taken (operator, 2026-07-25)

1. **IBKR wins on disagreement with FMP.** FMP reports a listing label; IBKR reports
   the primary exchange, which is what Nautilus qualifies against. Affected one
   ticker (`RWEM`).
2. **The 658 not-found tickers are excluded** via `venue_exclusions.csv`, each with
   a ticker-specific reason and evidence. 606 stopped trading before the catalog's
   2026-05-01 cutoff; 52 traded up to it and were delisted in the ~12 weeks between
   the data capture and the sweep — the register says which is which, because the
   evidence for the second group is weaker. For contrast, 3,949 of the 3,953
   resolved tickers have bars right up to that cutoff, so the not-found set is the
   delisted tail rather than a lookup failure.
3. **`MAGA` → ARCA**, recorded as an explicit operator decision. IBKR returned both
   ARCA and BATS, so the contract database cannot settle it; ARCA was chosen as the
   dominant listing venue in this universe. Revisit if it ever matters.

Accounting: 3,954 overrides + 658 exclusions = 4,612 tickers, all decided.

## Files

- `results.csv` — every ticker's verdict, with contract id, timing, attempts.
- `rejects.csv` — the 659 that needed adjudication (the input to the decisions above).
- `disagreements.csv` — where IBKR contradicted the venue already on record.

Regenerate with:

```bash
uv run python scripts/venue/resolve_venues_ibkr.py \
    --catalog firstrate-etf --include-resolved --state-dir logs/venue-backfill/run1
uv run python scripts/venue/build_exclusions.py \
    --results logs/venue-backfill/run1/results.csv --skip-tickers MAGA
```
