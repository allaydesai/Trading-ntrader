## ADDED Requirements

### Requirement: Crypto peak exposure is exact for runs saved with their computed peaks
When a spot crypto run is saved, it SHALL replay, per pair, its fills in engine order with each
fill's position, that position's signed quantity after the fill, the fill price and the USD
charged, and SHALL save only the resulting peak notional and peak exposure (notional over the
USD balance, sampled after every fill) with their basis, as exact decimal strings
(`config_snapshot.crypto_peaks = {"basis": "exact", "peak_notional_usd", "peak_exposure"}`),
not the fill series. Reports SHALL show those figures and SHALL say they are exact. A run saved
with the fill series itself (`config_snapshot.crypto_pairs[<id>].position_series`, PR #79)
SHALL be replayed when read and report the same exact figures. A run saved with neither SHALL
keep the upper-bound figures and the upper-bound note. Non-crypto runs SHALL be unchanged.

#### Scenario: scale-in and partial reductions on two pairs
- **WHEN** a run scales BTC in from 1 at 20,000 to 2 at an average 25,000 while 5 ETH at 1,000
  are held, then cuts both back (capital 100,000, fees as charged)
- **THEN** peak notional is 55,000 and peak exposure 55,000 / 99,970, where the trade rows'
  upper bound would report 62,000 and 62%

#### Scenario: saved peaks and a saved series agree
- **WHEN** the same run is read once from its saved `crypto_peaks` and once from a saved
  `position_series`
- **THEN** peak notional, peak exposure and the basis (`exact`) are identical

#### Scenario: a run saved before the series
- **WHEN** a crypto run with neither `crypto_peaks` nor `position_series` is shown in the CLI,
  the MCP or the dashboard
- **THEN** its peak figures and note are the upper-bound ones and `peak_exposure_basis` is
  `upper_bound`

#### Scenario: replay matches the account
- **WHEN** a run's series is replayed from its starting capital
- **THEN** the last balance agrees with the run's final account balance within one cent per
  fill (Nautilus rounds each fill's P&L from floats); a run outside that tolerance is saved
  without peaks and reports the upper bound
