# 84 — TT&C link budget SUBTRACTS the ground-station G/T

## Summary

`models/ttc_basic.py` computed the downlink Eb/N0 like this:

```python
noise_bw    = 10 * math.log10(s.tm_data_rate)
noise_floor = -228.6 + self._gs_gt + noise_bw          # <-- sign
snr         = s.rssi_dbm - 30 - noise_floor
s.eb_n0     = snr + self._coding_gain - ...
s.link_margin_db = s.eb_n0 - 12.0                       # <-- double count
```

with `rssi_dbm = eirp + sc_gain - fspl + 30`. Expanding:

```
snr = EIRP + G_sc - FSPL + 228.6 - G/T - 10log10(R)
```

The correct expression is

```
C/N0  = EIRP + G_sc - FSPL - L_atm - L_pol - L_impl + G/T + 228.6
Eb/N0 = C/N0 - 10log10(R_b)
```

**The ground-station G/T was subtracted where it must be added.** That is a
`2 x G/T = 40 dB` error at the configured 20 dB/K, and — worse than the
magnitude — it inverts the *ordering* of the ground network:

| Station | G/T (dB/K) | old margin ranking | correct ranking |
|---|---|---|---|
| Inuvik 13 m | 22.0 | **worst** | best |
| Svalbard 11.3 m | 21.5 | 4th | 2nd |
| Iqaluit 9 m | 20.0 | 2nd= | 3rd= |
| O'Higgins 9 m | 20.0 | 2nd= | 3rd= |
| Troll 7.3 m | 18.5 | **best** | worst |

A student who booked the biggest dish in the network got the worst link, and
any exercise built on "which station should we use for this pass?" taught the
answer backwards.

Two further errors in the same block:

2. **`link_margin_db = eb_n0 - 12.0` double-counted the coding gain.** The
   6 dB RS(255,223) gain was added into `eb_n0` and then measured against a
   hard-coded 12 dB requirement, so the model's effective uncoded requirement
   was 18 dB — roughly twice the CCSDS/textbook 9.6 dB for BPSK/QPSK at
   BER 1e-5, and unrelated to the modulation actually selected.

3. **No atmospheric, polarisation or implementation loss and no
   low-elevation G/T de-rate**, even though `configs/eosat1/rfsim.yaml`'s
   `link_budget` section defines all three. `ttc_basic` and `rfsim` were
   computing two different budgets from the same configuration.

## Severity

**Major, and blocking.** Every margin the simulator reported, every
`link_margin` limit check, the COM-006 commissioning baselines and the whole
rate/margin trade were computed from an expression with an inverted sign. No
work on the data-rate ladder (defect #85) means anything until this is right,
because the entire point of a rate ladder is that margin responds predictably
to rate and to station.

## Status

**Fixed.**

1. **Sign corrected.** `ttc_basic.tick` now computes

   ```python
   gt_eff = self._gs_gt - self._gt_derate_db(s.elevation_deg)
   losses = (self._atmospheric_loss_at(s.elevation_deg)
             + self._polarization_loss + self._implementation_loss)
   c_n0   = (s.rssi_dbm - 30.0) - losses + gt_eff + 228.6
   snr    = c_n0 - 10*log10(s.tm_data_rate)
   ```

   G/T is added. A 1 dB better station is worth exactly +1 dB of margin.

2. **Requirement no longer double-counted.** `link_margin_db` is measured
   against `required_eb_n0_db` (9.6 dB uncoded, config, plus the modulation's
   own penalty) with the *active tier's* coding gain already credited into
   `eb_n0`: 9.6 - 6.0 = 3.6 dB effective with RS(255,223), 9.6 - 7.5 = 2.1 dB
   with conv(7,1/2)+RS. Implementation loss is carried once, in the loss
   column.

3. **The losses `rfsim.yaml` declares are now applied.**
   `configs/eosat1/subsystems/ttc.yaml` gained `atmospheric_loss_db`,
   `polarization_loss_db`, `implementation_loss_db`,
   `gt_derate_below_10deg_db`, `gt_derate_below_30deg_db` and
   `required_eb_n0_db`, mirroring `rfsim.yaml link_budget`. Atmospheric loss
   is scaled by cosec(elevation) below 30 deg (0.5 dB at zenith, 1.44 dB at
   10 deg, 2.87 dB at 5 deg) and G/T is de-rated 1.5 dB below 10 deg and
   0.5 dB below 30 deg for ground/sky noise pickup.

4. **`TTCBasicModel.link_margin_for(tier, range_km, elevation, gs_gt_db)`**
   exposes the same budget as a deterministic, noise-free closed form so
   tools and tests can check it without ticking the model.

### Effect on the margins

Iqaluit (G/T 20.0), 450 km circular orbit, at the HIGH 128 kbps tier:

| Geometry | slant range | C/N0 | margin |
|---|---|---|---|
| 5 deg | 1944.5 km | 89.86 dB-Hz | **35.2 dB** |
| 10 deg | 1570.0 km | 94.14 dB-Hz | 39.5 dB |
| zenith | 450.0 km | 106.44 dB-Hz | **51.8 dB** |

These reproduce the independent hand calculation in the data-rate research
report (sections B.2-B.7) exactly. The headline consequence is that this link
is **not power-limited** — it is limited by the 5.00 MHz per-mission channel
allocation in 2200-2290 MHz, which is the honest engineering story and a
better lesson than a fake power cliff.

### Verification

* `tools/link_budget_ladder.py` prints the margin for every tier x station x
  elevation and exits non-zero if anything fails to close with 3 dB or
  overruns the 5 MHz channel. It calls `link_margin_for`, so it cannot drift
  from the flight model.
* `tests/test_simulator/test_tm_rate_ladder.py::TestLinkBudget` pins the sign
  (`test_gt_is_added_not_subtracted`), the station ordering
  (`test_stations_rank_by_gt_at_every_elevation`), the researched numbers
  (`test_researched_margins_at_5deg_and_zenith`) and the >=3 dB closure of
  every permitted tier at every station at 5 deg and zenith.
* `tests/test_simulator/test_rf_link_and_power_verification.py` recomputes the
  budget by hand and asserts the model agrees to 0.1 dB, plus a dedicated
  `test_link_budget_adds_the_ground_station_gt` regression.

### Known follow-up (not this defect)

* `eirp_dbw: 10.0` plus `sc_gain_dbi: 3.0` gives an effective 13 dBW, while
  `pa_nominal_power_w: 2.0` into a 3 dBi patch is 6 dBW. Every margin above
  uses the model's 13 dBW; at 6 dBW they all drop 7 dB and no conclusion
  changes. Making `eirp_dbw` derived from `tx_fwd_power` so the TX-power
  command (func 68) actually moves the budget is a separate piece of work.
* The spacecraft antenna is modelled as a flat 3 dBi in every direction. A
  realistic 70 deg-HPBW patch costs 15.1 dB at the 68.5 deg nadir angle that
  corresponds to 5 deg elevation. The margins above are therefore optimistic
  at low elevation by that amount; every tier still closes.
* The textbook's COM-006 baselines (14.5 dB at 1 kbps, 6.8 dB at 64 kbps,
  p. 396) are internally inconsistent — an 18 dB rate ratio cannot give a
  7.7 dB margin difference — and must be re-measured against the corrected
  budget.
