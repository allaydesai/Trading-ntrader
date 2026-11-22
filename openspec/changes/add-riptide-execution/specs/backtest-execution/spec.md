## ADDED Requirements

### Requirement: Market-on-open exits execute on the next bar
On a backtest venue using the gap-aware fill model, a market order tagged `MOO_EXIT` and sent
while bar T is handled SHALL fill on bar T+1 at T+1's open: the fill, the position close and
the realised P&L SHALL carry T+1's timestamp. Without the gap-aware model the order SHALL fill
as before (T's close).

#### Scenario: green-candle exit with distinct next opens
- **WHEN** Riptide signals a green-candle exit on bar T, T+1 opens at 125.00 and T+2 at 130.00
- **THEN** the sell fills at 125.00 with T+1's timestamp and the position closes at T+1

#### Scenario: final bar
- **WHEN** an MOO exit is signalled on the last bar of the run
- **THEN** it does not fill and the position is reported open

### Requirement: A stop gapped through fills at the open
On a gap-aware venue a triggered stop-market order SHALL fill at the bar's open when the bar
opens beyond the trigger, and at the trigger when it is reached intrabar.

#### Scenario: overnight gap below a sell stop
- **WHEN** a sell stop rests at about 110 and the next bar opens at 90.00
- **THEN** the stop fills at 90.00

### Requirement: Configs opt in to the gap-aware model
A YAML config's `backtest.fill_model` SHALL select the fill model (`default` or
`gap_aware`, default `default`). A non-default model SHALL be part of the run's config hash
and its `config_snapshot`; a default run's hash and snapshot SHALL be unchanged.

#### Scenario: riptide config
- **WHEN** `configs/riptide.yaml` is run
- **THEN** the venue uses `GapAwareFillModel` seeded like the default, with next-bar execution

#### Scenario: existing configs
- **WHEN** a config without `fill_model` is run
- **THEN** its hash, snapshot and results are identical to before

### Requirement: Per-share slippage drag is equities only
`IBKRCommissionModel` SHALL add `slippage_per_share x quantity` to every fill's commission,
on top of the cap, scaled with the other terms by the cost multiplier. A futures contract's
fee SHALL stay `quantity x commission_per_contract_usd x cost multiplier` whatever the
slippage setting.

#### Scenario: equity fill with slippage
- **WHEN** 100 shares fill with `slippage_per_share` 0.01
- **THEN** the commission is the capped IBKR commission plus 1.00 USD

#### Scenario: futures fill with slippage set
- **WHEN** one ES contract fills on a futures venue whose equity fallback carries slippage
- **THEN** the fee is 2.25 USD
