## Summary

The EOSAT-1 power budget did not close under standard operations, and the
protection thresholds were set inside the normal operating band, so every
training session stripped its science loads about an hour in.

Quantified with a standalone reproduction of the simulator's own EPS
arithmetic (72 h, dt = 1 s) and re-verified inside the simulator itself:

1. **Shed threshold inside the operating band.** `LOAD_SHED_VOLTAGE = 26.5 V`
   (`models/eps_basic.py`) is the battery OCV at **64.9 % SoC** — above the
   normal eclipse trough from the 75 % start SoC. Result: payload, FPA cooler,
   TTC TX and reaction wheels were all shed during the FIRST eclipse
   (measured: t = 86 min in the simulator, per-orbit minimum 60.6 %), with no
   auto-restore, so science was dead until an operator manually re-enabled
   every line.
2. **Negative energy trend.** Orbit-average generation was 53.8 W (favourable
   sun/eclipse alignment) to 46.0 W (the alignment the sim drifts into within a
   day, because the AOCS sun sweep of 5700 s and the propagator eclipse of
   5669 s beat against each other). Standard operations with imaging duty drew
   53.3 W, and with the two-station contact fix (defect #41) 56.8 W — i.e.
   **−4.7 to −17.1 Wh/orbit**: net negative at both alignments.
3. **TX shed defeated during passes.** The 900 s auto-TX hold re-armed on every
   accepted TC, forcing the `ttc_tx` line back ON, so the third shed stage
   never actually saved TX energy while in contact.
4. **Unjustified TX line load.** 20 W for a 2 W RF S-band PA implies 10 %
   DC efficiency; 12 W (PA at ~25 % plus transponder housekeeping) is realistic.

Consequences: the shed ladder was firing as routine behaviour rather than as a
contingency (destroying its training value), and the mission could not sustain
its own published imaging + two-station contact plan.

## Severity

**Major** — the simulated spacecraft could not perform standard operations
without stripping its payload every orbit; the training value of load shedding
was lost because it was indistinguishable from normal behaviour.

## Status

**Fixed** by configuration (no physics code changed), applying the six
validated recommendations from the power analysis:

| # | Change | Where | From → To |
|---|---|---|---|
| 1 | SoC shed ladder made live | defect #49 | dead → live |
| 2 | Deployable wing area | `configs/eosat1/subsystems/eps.yaml solar_panels` | 0.240 → **0.300 m²** per wing |
| 3 | TX line load | `eps.yaml transponder_power_w` | 20 → **12 W** |
| 4 | Auto-TX hold | `configs/eosat1/simulation.yaml ttc.auto_tx_hold_s` | 900 → **300 s** |
| 5 | Initial SoC | `simulation.yaml eps.initial.soc_pct` | 75 → **90 %** |
| 6 | Bus-voltage shed | `simulation.yaml eps.load_shed.bus_voltage_v` | 26.5 → **25.0 V** (≈ 45 % SoC) |

Wing area and TX power stayed in `eps.yaml` because that file is the hardware
sheet and already owned those keys — duplicating them into `simulation.yaml`
would have recreated the config-duplication defect (#42). Protection levels,
initial conditions and the shed ladder, which had no config home at all, are
now in `simulation.yaml` (defect #51).

Note that `eps.yaml solar_panels` was previously dropped by the pydantic
schema, so the wings were silently running on the 0.24 m² code default no
matter what the YAML said; the schema now declares the section (defect #51).

Verified in-simulator over 24 h with the two-station + imaging profile
(platform + heaters + wheels + payload standby, TX 900 s per half orbit,
3 × 120 s captures and 1300 s of FPA cooler per orbit):

| Configuration | First load shed | Per-orbit minimum SoC |
|---|---|---|
| Before | t = 86 min (first eclipse), full 4-line cascade | 60.6 % (and only recovers because loads were stripped) |
| After | none in 24 h | converges 77.5 → **72.6 %**, Δ < 0.2 pt/orbit by orbit 12 |

Shedding remains reachable for training: the abuse profile (sustained science)
and injected failures (`bus_short` +80 W, array loss) still drive the bus below
25.0 V and exercise the full shed/FDIR ladder.

## Acceptance criteria

- [x] 24 h of standard operations (nominal loads + contacts + modest imaging
      duty) produces **zero** load-shed events.
- [x] Minimum SoC stays well clear (> 10 points) of the shed band.
- [x] The per-orbit SoC minimum converges instead of walking down.
- [x] The shed ladder still trips under failure/abuse conditions.
- [x] Every applied number lives in a configuration file, not in code.

## Affected areas

- `configs/eosat1/subsystems/eps.yaml` (wing area, TX line load)
- `configs/eosat1/simulation.yaml` (initial SoC, shed voltage/order, auto-TX
  hold, SoC stage ladder, protection and event thresholds)
- `packages/smo-common/src/smo_common/config/schemas.py`
  (`EPSConfig.solar_panels` / `power_lines` now declared)
- `packages/smo-simulator/src/smo_simulator/models/eps_basic.py`
  (protection levels read from config)
- `tests/test_simulator/test_power_budget_regression.py`
  (`TestStandardOperationsBudgetCloses`)
- Analysis: `/home/claude/work/power_analysis.md`, `power_sim/eps_sim.py`

## Related

- Defect #49 (dead SoC shed) — the protection layer that becomes load-bearing
  once the shed voltage is corrected.
- Defect #41 (single-station contact gating) — enabling the second ground
  station is what pushed the pre-fix budget net-negative.
- Defect #51 (hard-coded simulation parameters) — provided the config home for
  the tuning applied here.
