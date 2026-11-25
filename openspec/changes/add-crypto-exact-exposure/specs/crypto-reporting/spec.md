## ADDED Requirements

### Requirement: Crypto peak exposure is exact for runs saved with their fill series
A spot crypto run SHALL save, per pair, its fills in engine order with each fill's position,
that position's signed quantity after the fill, the fill price and the USD charged
(`config_snapshot.crypto_pairs[<id>].position_series`). Reports SHALL compute peak notional and
peak exposure (notional over the USD balance) exactly from it, sampled after every fill, and
SHALL say the figures are exact. A run saved without the series SHALL keep the upper-bound
figures and the upper-bound note. Non-crypto runs SHALL be unchanged.

#### Scenario: scale-in and partial reductions on two pairs
- **WHEN** a run scales BTC in from 1 at 20,000 to 2 at an average 25,000 while 5 ETH at 1,000
  are held, then cuts both back (capital 100,000, fees as charged)
- **THEN** peak notional is 55,000 and peak exposure 55,000 / 99,970, where the trade rows'
  upper bound would report 62,000 and 62%

#### Scenario: a run saved before the series
- **WHEN** a crypto run without `position_series` is shown in the CLI, the MCP or the dashboard
- **THEN** its peak figures and note are the upper-bound ones and `peak_exposure_basis` is
  `upper_bound`

#### Scenario: replay matches the account
- **WHEN** a run's series is replayed from its starting capital
- **THEN** the last balance equals the run's final account balance to the cent
