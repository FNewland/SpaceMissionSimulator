# Workstream 1 — EPS power balance

**Abstract.** The EO-Sat-1 power budget does not close, and the 2026-07 "fix" (wings 0.24→0.30 m², TX 20→12 W, hold 900→300 s, SoC 75→90 %, shed 26.5→25.0 V) rested on a duty cycle that no longer exists and on a generation model that is 45 % optimistic. Re-derived from the shipped config with the repo's own propagator: the ground network is **51.5 passes/day at 447 s mean (26.6 % contact, 33.0 % TX duty with the 300 s hold)**, not the ~10–20 passes/day the prior analysis assumed; the orbit is **β = 20.6–49.8°** with eclipse fraction 0.302–0.367, never the β = 0 / 0.38 the regression test asserts. The simulator's generation constant is BOL-only (389.5 W/m² of projected area) against a defensible EOL stack of 267.9 W/m² — a 1.45× overstatement — so the *reported* +10 W margin is a *physical* −10 W deficit; the ±Y deployable wings are mounted on the axis the implemented sun-vector model puts in the orbit plane, wasting 36 % of their area; heaters are billed at 100 % duty (8 W over-bill); and the FDIR rule `eps.bus_voltage < 26 V` sits at **58.4 % SoC**, inside the nominal eclipse band, pre-empting the whole protection ladder. The 0.300 m² wing that was supposed to fix this is not a physically credible 6U part (its own comment mis-states it by 5×). Recommendation: honest loss stack, wings halved to 0.15 m² each on a **single-axis solar array drive about the orbit normal** (the SADA is already half-built and dead), loads trimmed to real 6U values, battery 120→100 Wh, protection ladder un-inverted — giving **+16.6 Wh/orbit and 85 % per-orbit min SoC in nominal ops at the worst β, while payload-through-eclipse or stride-1 dumps still drive the battery to 44.5 % and fire the full shed ladder.** 24 findings, 14 work packages, 8 tests and one in-tree `tools/power_budget.py` that derives the duty cycle instead of assuming it.

---

## Findings

**1. [Critical] The generation constant is BOL-only — 45 % optimistic vs any defensible EOL number.**
`packages/smo-simulator/src/smo_simulator/models/eps_basic.py:409-410` (and the fallback at `:374-376`):
```
panel_pwr = area * self._cell_eff * self._solar_irrad * illumination
panel_pwr *= degradation * s.mppt_efficiency * s.sa_age_factor * noise
```
Root cause: the only multipliers are cell efficiency (0.295, `configs/eosat1/subsystems/eps.yaml:6`), MPPT (0.97, `eps_basic.py:114`) and an aging factor. Missing: inherent degradation / cell mismatch / cover-glass / packing (≈0.85), array temperature coefficient (TJ GaAs ≈ −0.20 %/°C; at 65 °C array temp ≈ 0.926), harness+diode+PCU loss (≈0.95). `sa_age_factor` starts at 1.0 every run so EOL (3 yr, ≈0.92) is never seen.
Evidence: sim constant `K = 1361 × 0.295 × 0.97 = 389.5 W/m²` of projected area. Full loss stack gives `K = 1361 × 0.295 × 0.85 × 0.926 × 0.92 × 0.97 × 0.95 = 267.9 W/m²`. **Ratio 1.45×.** Apply it to the as-built array and orbit-average generation falls from 51.4–66.0 W to **35.4–45.4 W** — below the modelled 51.5 W nominal-ops load at every β. *The budget does not close on honest physics; it only appears to close because generation is a BOL number.*

**2. [Critical] The previous fix's duty cycle is wrong by 5×: the analysis assumed ~10–20 passes/day, the shipped orbit gives 51.5.**
`docs/review_2026-07/power_analysis.md` §1 ("Iqaluit on 2 of 3 orbits (≈10 passes/day), 600 s pass"; P3 = "two-station") vs `configs/eosat1/orbit.yaml:44-90` (five stations).
Measured by me with the repo's own propagator (48 h from `t0_epoch`, 10 s step):

| | value |
|---|---|
| passes | 103 / 48 h = **51.5 /day** |
| mean pass | **447.0 s** (matches the 445–448 s in `ttc.yaml` / `activity_types.yaml`) |
| passes ≥ 10° max-el (HIGH-tier gate) | 83 / 48 h = 41.5 /day |
| passes per orbit | **3.38** |
| contact duty | 1510 s / 5669 s orbit = **26.6 %** |
| TX-on duty incl. 300 s auto-TX hold (merged) | **33.0 %** |
| TX-on duty had the hold stayed 900 s | 52.8 % |

The regression test that "proves" closure (`tests/test_simulator/test_power_budget_regression.py:76-78`) models **one 900 s TX window per half orbit = 31.7 %** — numerically similar, but it was calibrated against a 2-station world and no longer tracks the config. The real error is not TX energy (Δ ≈ +0.8 W) — it is that nothing re-derived the budget after the station list tripled.

**3. [Critical] The regression test runs at β = 0°, an attitude/season this orbit never reaches.**
`tests/test_simulator/test_power_budget_regression.py:45-48` (`_sun_vector` returns `(cos, sin, 0.0)`) and `:23` (`ECLIPSE_FRACTION = 0.38`).
Measured β over one year with the repo propagator: **β ∈ [20.6°, 49.8°]** (LTAN 20:40 computed from the TLE — an 08:40 LTDN orbit, *not* the "dawn-dusk SSO" the manual claims), eclipse fraction **0.302–0.367**, never 0.38.
Consequence: in this model geometry the wings' output scales with `cos β`, so β = 0 is the *best* case, not the worst. The test therefore over-states wing generation by up to **1/cos 49.8° = 1.55×** and simultaneously over-states eclipse. It is not a worst case in either direction — it is an inconsistent one.

**4. [Critical] The deployable wings are mounted on the worst possible axis for the sun-vector model actually implemented.**
`packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:492-497`:
```
sun_body = (cos(beta)*cos(phase), cos(beta)*sin(phase), sin(beta))
```
This puts **body Z on the orbit normal** and body X/Y **in the orbit plane**. The ±Y wings (`eps.yaml:26-27`, 0.300 m² each) therefore sweep through the sun line: only one wing is lit at a time, with a `|sin φ|` profile.
Evidence: orbit-average illumination factor for the wing pair = `cos β · ⟨|sin φ|⟩ ≈ 0.64 cos β`, i.e. **0.66 m² of cells does the work of 0.42 m²**. A single-axis drive about the orbit normal gives `cos β` continuously — **1.56× more energy from the same area**, and removes the alignment sensitivity entirely.
Also contradictory: `eps.yaml:20-25` labels ±Z "zenith/nadir" and `manual/01_eps.md` §2.1 labels ±Y "cross-track", but the implemented frame makes ±Z the cross-track/orbit-normal pair. Three documents, three different body frames.

**5. [Critical] `solar_array_drive_angle` is fully dead — the SADA exists as a command, a device gate and a telemetry parameter, but never touches generation.**
`eps_basic.py:134` (state), `:841-844` (`set_solar_array_drive` command), `:765` (telemetry 0x0133), `:426-432` (device 0x0101 gate that zeroes generation when OFF), `configs/eosat1/telemetry/parameters.yaml:62`.
The angle is stored, echoed to telemetry, and never read by the generation code. The device gate is binary (drive OFF ⇒ 0 W) with no intermediate behaviour. This is the half-built lever that would fix finding 4 at near-zero code cost.

**6. [Major] Generation is completely independent of AOCS attitude/mode.**
`aocs_basic.py:471-524` computes the CSS sun vector from `orbit_state.solar_beta_deg` and `self._orbit_phase` only. Neither the quaternion, `s.mode`, nor `att_error` enter. Consequences: (a) a sun-pointing safe mode generates exactly as much as nadir-pointing imaging; (b) there is no "power-positive attitude" for students to command; (c) `_get_sun_body_vector` (`:456-469`) exposes a "fallback from orbit geometry" that is identical to the primary path.

**7. [Major] The AOCS sun sweep is hard-coded to 5700 s against a 5669.3 s orbit — a non-physical beat that moves orbit-average generation by ±8 W.**
`aocs_basic.py:1108-1109`: `self._orbit_phase + dt * (360.0 / 5700.0)`.
Measured: at β = 49.8° orbit-average generation varies **51.4 → 60.0 W** with sweep phase; at β = 24.2°, **56.6 → 66.0 W**. The 30.7 s/orbit slip walks the full 360° in ~186 orbits (12 days), so the *same* config and the *same* ops plan close or fail depending on run epoch. This is the "both alignments occur within days" hand-wave in `power_analysis.md` §1 — it is a bug, not a physical phenomenon.

**8. [Major] Battery terminal-voltage sign is inverted: the bus sags when charging and rises when discharging.**
`eps_basic.py:552,560`:
```
bat_i = net_power_w / (ocv + 1e-6)     # >0 when CHARGING
v_loaded = ocv - bat_i * self._internal_r
```
Charging (`net>0`) subtracts `i·R`; discharging adds it. Correct is `v = ocv + i·R` on charge, `ocv − |i|·R` on discharge. Magnitude at 50 W / 26 V / 0.05 Ω = **±0.10 V**, i.e. every voltage-based protection (UV flag 26.5 V, shed 25.0 V, FDIR 26/24 V) fires ~1.3 SoC-points *late* on discharge and can false-trip during hard charging. It also makes the "bus sag during imaging" behaviour the code comment at `:564-567` claims to have added physically backwards.

**9. [Major] The FDIR bus-voltage rule at 26.0 V pre-empts everything and sits at 58 % SoC — inside the nominal band.**
`configs/eosat1/subsystems/fdir.yaml:11-15`: `eps.bus_voltage < 26 → safe_mode_eps` (level 2).
On the configured OCV curve (`eps.yaml:11-13`, 21.5–29.2 V) 26.0 V ≡ **SoC 58.4 %**; with the finding-8 sign error under load it trips at ≈57 %. `power_analysis.md` §4 explicitly flagged this ("Optional: lower fdir.yaml bus rules 26/24 V → 24.5/23.5 V") and it was **not applied**. Result: the in-model shed was moved to 25.0 V (45 % SoC) but the *harshest* action still fires first, at 58 %. The protection ladder is inverted:

| trips at | SoC | action | source |
|---|---|---|---|
| 26.0 V | **58.4 %** | EPS safe mode (sheds 2 loads) | `fdir.yaml:13` |
| 25.0 V | 45.5 % | in-model progressive shed | `simulation.yaml:105` |
| 24.0 V | 32.5 % | AOCS safe mode | `fdir.yaml:66-70` |
| — | 30/20/10 % | FDIR SoC stages | `simulation.yaml:111` |

My worst-case run reached a per-orbit minimum of **60.0 %** — 1.6 points above the FDIR trip. Any sweep-alignment drift, any β excursion, or one extra load crosses it. The regression test never exercises this path: `test_power_budget_regression.py` ticks the EPS model alone and only calls `_tick_fdir_advanced` in a *different* test with generation zeroed. **This is the most likely thing the user is actually seeing.**

**10. [Major] Heaters are billed at 100 % duty; TCS computes a thermostat duty that nothing consumes.**
`eps_basic.py:477-483` bills `htr_bat` 6 W + `htr_obc` 4 W whenever the *power line* is ON, ignoring `tcs_basic`'s thermostat (`htr_battery` on 1 °C / off 5 °C, `configs/eosat1/subsystems/tcs.yaml:31-38`) and the duty parameters it publishes (`htr_duty_battery` 0x040E, `htr_duty_obc` 0x040F, `htr_total_power_w` 0x0411).
Evidence: **10.0 W of the 51.5 W nominal load — 19 % of the entire budget — is a heater duty cycle of 100 % that the thermal model says should be ~20 %.** Over-bill ≈ 8 W orbit-average.

**11. [Major] "Solar array degraded" (event 0x010B) fires on the first sunlit tick of every run and latches — a permanent false alarm that masks real degradation.**
`eps_basic.py:648-660`:
```
expected_sa_power = (self._panel_area * self._cell_eff * self._solar_irrad * ...)
```
`self._panel_area` is the **dead legacy `arrays:` sum** (`eps.yaml:4-9`, 0.314 + 0.314 = 0.628 m²), not the 6-panel model. Expected = 0.628 × 0.295 × 1361 × 0.97 = **244.6 W**; threshold = 0.7 × 244.6 = **171.2 W**; measured peak instantaneous generation with wings deployed = **102–129 W**. So `actual < 0.7 × expected` is *always* true in sunlight, the rising-edge fires once at first sun, `_prev_sa_degraded` latches True, and a genuine array failure afterwards produces **no** event.

**12. [Major] The 0.300 m² wing is not a physically credible 6U deployable, and the code comment that justifies it is wrong by 5×.**
`eps.yaml:29-30`: `wing_py_area_m2: 0.300 # +Y deployable wing (2x 3U panels + margin)`.
A 3U panel is 300 × 100 mm = 0.03 m². **2 × 3U = 0.06 m², not 0.300 m².** 0.300 m² per wing = 10 × 3U panel segments, folded against a 300 × 100 mm body face — ten folds. Total deployed cell area 0.90 m² on an 11–16 kg 6U (`~/cdf/configs/validation/eosat_6u_reference.yaml`, `mass.dry_mass_kg: 11.0`, `target_mass_kg: 16.0`). Rigid GaAs panel ≈ 2.2–2.8 kg/m² ⇒ **1.5–1.9 kg of wing** plus hinges/dampers/HDRM. Commercial 6U deployables (MMA eHaWK-27, DHV, EnduroSat) top out at **0.24–0.30 m² total, both wings**. The previous "fix" bought margin by writing down an impossible spacecraft — and still did not close the budget.

**13. [Major] Body-panel areas are geometrically impossible for a 6U.**
`eps.yaml:20-25`: `px/mx = 0.060` ("3U × 2U = 300 × 200 mm") **and** `pz/mz = 0.060`. A box has only *one* pair of 300 × 200 faces. A CDS 6U (100 × 226 × 366 mm) has face pairs of 0.0827 / 0.0366 / 0.0226 m². Declared body total **0.30 m²**; geometrically available **0.283 m²** before packing factor, and the nadir face carries the imager aperture. Realistic body cell area ≈ **0.15–0.20 m²**. Overstated by ≈ 50 %.

**14. [Major] `platform_idle_power_w: 70.0` is dead config that misleads every reader of the hardware sheet.**
`eps.yaml:12`; `eps_basic.py:210` assigns `self._platform_idle_w`, and it is **never read again** (only other reference is the default at `:164`, which is a *different* value, 95.0). An instructor reading eps.yaml concludes the platform draws 70 W idle; `tick()` bills 36 W of lines. Same class: `POWER_LINE_NOMINAL_W` (`eps_basic.py:45`) is built and never used, and `POWER_LINE_DEFS` still advertises `ttc_tx 20.0 W` / `aocs_wheels 12.0 W` (`:34,:39`) which contradict the live values (12 W / 6 W).

**15. [Major] Load-shed restore hysteresis is configured, documented in the code comment, and does not exist.**
`simulation.yaml:106` `restore_hysteresis_v: 0.5` → `eps_basic.py:280` assigns `self._shed_hysteresis_v` → **never read**. The comment at `eps_basic.py:576-578` states "Lines are restored only when bus voltage recovers above threshold + 0.5 V hysteresis" — there is no restore code anywhere. Once shed, a line is dead until an operator re-commands it.

**16. [Major] Telemetry parameter-ID collisions and dead parameters in the EPS block.**

| ID | `parameters.yaml` says | what actually writes it |
|---|---|---|
| 0x0129 | `eps.pdm_unsw_status` (:49) | `engine.py:560,859-896` writes **spacecraft_phase** |
| 0x012A | `eps.spacecraft_phase` (:50) | **nothing** (`eps_basic.py:754` "reserved — leave unset") |
| 0x013A | `eps.eclipse_active` (:351) | `eps_basic.py:753` writes **pdm_unsw_status** (value 3) |
| 0x013B–0x0141 | `power_margin`, `subsys_power_eps/aocs/tcs/ttc/payload/obdh` (:352-358) | **nothing** — 7 dead parameters |

An operator watching "eclipse active" sees a constant 3; per-subsystem power draw, the single most useful power-management display, is declared and never produced.

**17. [Minor] Array voltage telemetry is a tautology.** `eps_basic.py:433-436`: `sa_a_pwr_v = sa_a_current*28.0`, then `sa_a_voltage = sa_a_pwr_v / sa_a_current` ⇒ **exactly 28.0 V** whenever current > 0.01 A, else 0. Published as `eps.sa_a_voltage` (0x010B) in HK SID 1.

**18. [Minor] All array currents are computed at a hard-coded 28.0 V** (`eps_basic.py:390,391,412`) while the bus runs 21.5–29.0 V. `Σ(line currents) × bus_voltage ≠ power_cons`, and `sa_current × bus_voltage ≠ power_gen`. An operator doing the obvious cross-check gets a 10 % error.

**19. [Minor] No coulombic/charge efficiency.** `eps_basic.py:537`: `d_soc = net_power_w/(cap_wh*3600)*100*dt` — round-trip efficiency 1.00. Li-ion charge acceptance is 0.92–0.96. Adds ~4–8 % optimism on top of finding 1. No CV taper either, so the last 20 % of SoC charges at full current.

**20. [Minor] HIGH-rate dumps cost nothing.** `eps_basic.py:452-455` bills a single `transponder_power_w` (12 W) regardless of tier. `ttc.yaml` HIGH is 128 kbps QPSK vs LOW 16 kbps BPSK — 8× the information rate with worse PA backoff. "Too many dumps" is currently free in the power model, which directly defeats the teaching goal.

**21. [Minor] `bat_capacity_wh` (0x010A) publishes remaining energy, not capacity.** `eps_basic.py:714`: `capacity_wh * soc/100`. `parameters.yaml:13` describes it as "Battery capacity".

**22. [Minor] `num_wheels: 4` (`aocs.yaml:8`) vs `aocs_wheels: 6.0 # 3 wheels x 2 W idle` (`simulation.yaml:92`).** Also the AOCS *suite* (star trackers, IMU, GPS, magnetorquers, AOCS electronics) draws no power at all — only the wheel line exists.

**23. [Minor] Manual vs config divergence, uncorrected after the previous fix.** `configs/eosat1/manual/01_eps.md` §2.1 still states wing area **0.24 m²**, deployed total **0.78 m²**, peak BOL **~64 W** (config: 0.300 / 0.90 / 129 W peak); §2.2 states battery **40 Ah** at 28 V = **1120 Wh** against `eps.yaml:11` **120 Wh** (9.3×); §2.1 calls the orbit **dawn-dusk** (β ≈ 90°) when the TLE gives LTAN 20:40 and β = 20.6–49.8°.

**24. [Minor] Duplicate, divergent eclipse models.** `packages/smo-common/src/smo_common/orbit/eclipse.py` (`is_in_eclipse`, `eclipse_fraction` **with** a 50 km penumbra) is imported by nothing in the simulator; `propagator.py:173` uses its own private cylindrical `_is_eclipse`. Penumbra (~10–20 s per transition at 450 km) is therefore never modelled and the two implementations can drift.

---

## Power budget tables

All figures orbit-average over one 5669.3 s orbit (15.24 rev/day), wings deployed, computed with the simulator's own arithmetic driven by the repo propagator.

### Generation — as-built model (`K = 389.5 W/m²`, BOL, MPPT only)

| face | area m² | illumination law (implemented) | β=49.8° | β=24.2° |
|---|---|---|---|---|
| +X / −X | 0.060 each | `cos β·\|cos φ\|` | 8.3 W | 12.2 W |
| +Y / −Y **incl. wings** | 0.330 each | `cos β·\|sin φ\|`, one wing at a time | 30.6 W | 44.9 W |
| +Z | 0.060 | `sin β` (constant) | 12.4 W | 6.5 W |
| −Z | 0.060 | 0 (never sunward) | 0 W | 0 W |
| **× sunlit fraction** | | `1 − f_ecl` (0.698 / 0.635) | | |
| **orbit-average, sweep-alignment range** | | | **51.4 – 60.0 W** | **56.6 – 66.0 W** |
| peak instantaneous | | | 102 W | 129 W |
| stowed (body only) | 0.30 m² | | 22.0 – 22.9 W | 18.3 – 19.4 W |

### Generation — same array, honest EOL loss stack (`K = 267.9 W/m²`)

| | β=49.8° | β=31.9° | β=24.2° |
|---|---|---|---|
| orbit-average | **35.4 – 41.3 W** | 38.0 – 44.9 W | **39.0 – 45.4 W** |

Loss stack: 1361 W/m² × 0.295 cell × 0.85 inherent/packing × 0.926 temp (65 °C, −0.20 %/°C) × 0.92 EOL (3 yr) × 0.97 MPPT × 0.95 harness.

### Loads — line by line, as billed by `tick()`

| line | rating W | source | (a) quiescent | (b) nominal ops day | (c) worst case |
|---|---|---|---|---|---|
| `obc` | 15.0 | `eps.yaml:11` | 15.00 | 15.00 | 15.00 |
| `ttc_rx` | 5.0 | `eps.yaml:41` | 5.00 | 5.00 | 5.00 |
| `htr_bat` | 6.0 @ 100 % duty | `simulation.yaml:90` | 6.00 | 6.00 | 6.00 |
| `htr_obc` | 4.0 @ 100 % duty | `simulation.yaml:91` | 4.00 | 4.00 | 4.00 |
| `aocs_wheels` | 6.0 | `simulation.yaml:92` | 6.00 | 6.00 | 6.00 |
| `payload` standby | 8.0 | `eps.yaml:38` | 0 | 8.00 | 8.00 |
| `payload` imaging Δ | +37.0 | `eps.yaml:37` | 0 | +2.35 (3×120 s/orbit) | +37.00 (through eclipse) |
| `ttc_tx` | 12.0 | `eps.yaml:44` | 3.19 (26.6 % contact, no hold) | 3.96 (**33.0 %**, 300 s hold) | 6.34 (52.8 %, 900 s hold) |
| `fpa_cooler` | 15.0 | `eps.yaml:39` | 0 | 3.44 (1300 s/orbit) | 15.00 (continuous) |
| **total load** | | | **39.2 W** | **53.7 W** | **102.3 W** |

*(b)* uses the real five-station statistics (51.5 passes/day, 447 s, 33.0 % merged TX duty with the 300 s hold) and the `nominal_ops_day` template's activity mix scaled to a per-orbit imaging duty of 3 × 120 s + 1300 s cooler — i.e. the same imaging duty the regression test asserts. With the template's *literal* 2 acquisitions/day the imaging and cooler terms collapse to +0.16 W and +0.23 W and the total is **48.4 W**.

### Closure

| duty cycle | gen (as-built K) | gen (EOL K) | load | margin, as-built | margin, EOL | per-orbit min SoC (as-built) |
|---|---|---|---|---|---|---|
| (a) quiescent | 51.4–66.0 | 35.4–45.4 | 39.2 | **+12 … +27 W** | −3.8 … +6.2 W | 76.9 – 85.7 % |
| (b) nominal ops day | 51.4–66.0 | 35.4–45.4 | 53.7 | **−2.3 … +12 W** | **−18 … −8 W** | 70.5 – 79.0 % |
| (b) as literally planned | " | " | 48.4 | +3.0 … +18 W | −13 … −3 W | 74.9 – 79.0 % |
| (c) worst case | 51.4–66.0 | 35.4–45.4 | 102.3 | **−51 … −36 W** | −67 … −57 W | flat in ≈2.4 h |

**Diagnosis, per the requested taxonomy:**

- **(ii) model defects dominate.** Generation is 1.45× optimistic (F1); the wings are on the wrong axis for the implemented sun geometry, wasting 36 % of their area (F4/F5); the sun sweep beats against the orbit giving an ±8 W random offset (F7); heaters are over-billed 8 W (F10); charge efficiency is 1.00 (F19). Net: the *reported* budget is ~+10 W when the *physical* budget is ~−10 W.
- **(iii) unrealistic load values are the second cause.** `obc 15 W` + `ttc_rx 5 W` = 20 W of always-on avionics on a 6U (real: 4–7 W); heaters at 100 % duty; and conversely the AOCS suite outside the wheels is billed at **zero**.
- **(i) genuine sizing problem is real but is the smallest term** — and the previous fix "solved" it by writing an impossible 0.90 m² array onto an 11 kg 6U (F12/F13). At honest losses even that impossible array is ~10 W short.

---

## Recommended fix

**Architecture decision: keep two deployable wings, halve their area to a commercially real 6U part, and put them on a single-axis solar-array drive whose axis is the orbit normal.** Rationale:

- Body-mounted only is arithmetically impossible: 0.20 m² of credible body cells × 267.9 W/m² × geometry ⇒ **≈8–13 W** orbit-average. It cannot even carry the OBC.
- Fixed wings sized for the `|sin φ|` duty need **0.44 m² of wing** to reach 24–33 W — still short, and still physically incredible.
- 0.30 m² of wing **with** a SADA delivers **45.8–56.5 W** at honest EOL losses — with the alignment spread collapsing from 8 W to **1 W**, because `cos β` no longer depends on orbit phase.
- The SADA is already half-implemented (command, device gate, telemetry parameter) — F5 — so this is the cheapest code change available, and it creates a first-class failure mode ("SADA stuck at angle X") that the device gate 0x0101 already supports.
- Physical credibility: 0.15 m² per wing = a 3-segment folding wing of 366 × 136 mm panels, ≈0.55 kg per wing with hinge and HDRM. This is MMA eHaWK-27 / DHV 6U class. Total array 0.30 m² wings + ~0.20 m² body on a 16 kg 6U — defensible at PDR.
- Battery 120 → **100 Wh** (3.6 Ah at 28 V, ≈0.9 kg): squarely inside the 6U product range (GomSpace BPX 77 Wh, ISISpace 85 Wh, Clyde Space Optimus 160 Wh) and it restores per-orbit DoD to a value where eclipse management is a real decision.

### `configs/eosat1/subsystems/eps.yaml`

```yaml
arrays:                        # DELETE — superseded by solar_panels (see F11)
battery:
  capacity_wh: 100.0           # 120.0 -> 100.0  (3.6 Ah @ 28 V, 6U-class)
  charge_efficiency: 0.94      # NEW  (F19)
  discharge_efficiency: 1.0    # NEW
  cv_taper_start_soc_pct: 90.0 # NEW — charge current tapers above this

# NEW: array performance loss stack (F1). Replaces the bare cell_eff * MPPT.
array_losses:
  inherent_degradation: 0.85   # cell mismatch, cover glass, packing, assembly
  temp_coeff_per_c: -0.0020    # TJ GaAs power coefficient
  temp_ref_c: 28.0
  array_temp_sunlit_c: 65.0    # until wired to tcs panel zones
  harness_efficiency: 0.95
  annual_degradation: 0.0275   # already implied by sa_age_factor
  design_life_years: 3.0       # sizing case = EOL

obc_power_w: 6.0               # 15.0 -> 6.0  (OBC 3 W + PDU/EPS housekeeping 3 W)
# platform_idle_power_w: DELETE (F14 — dead key)
transponder_rx_power_w: 1.5    # 5.0 -> 1.5   (S-band RX)
transponder_power_w: 12.0      # LOW tier, unchanged
transponder_hi_power_w: 20.0   # NEW (F20) — HIGH tier 128 kbps QPSK
payload_power_w: 45.0          # unchanged
payload_standby_power_w: 6.0   # 8.0 -> 6.0
fpa_cooler_power_w: 15.0       # unchanged

solar_panels:
  # 6U CDS envelope 100 x 226 x 366 mm; body Z = ORBIT NORMAL (see F4)
  px_area_m2: 0.060    # 366x226 face, 73% packing
  mx_area_m2: 0.060
  py_area_m2: 0.030    # 366x100 face, 82% packing
  my_area_m2: 0.030
  pz_area_m2: 0.018    # 226x100 face, 80% packing
  mz_area_m2: 0.000    # imager aperture + radiator — no cells
  wing_py_area_m2: 0.150   # 0.300 -> 0.150  (3 x 366x136 mm segments, ~0.55 kg)
  wing_my_area_m2: 0.150
  wing_deploy_time_s: 30.0
  # NEW: single-axis solar array drive
  sada_present: true
  sada_axis: z             # rotation axis = body Z = orbit normal
  sada_tracking: true      # false => wings frozen at solar_array_drive_angle
  sada_max_rate_deg_s: 1.0
```

### `configs/eosat1/simulation.yaml`

```yaml
eps:
  initial: { soc_pct: 90.0, bat_temp_c: 15.0 }
  line_power_w:
    htr_bat: 6.0
    htr_obc: 4.0
    aocs_wheels: 12.0     # 6.0 -> 12.0: 4 RW idle 6 W + ST 1.6 + IMU 0.8
                          # + GPS 1.0 + MTQ/AOCS electronics 2.6  (F22)
  heater_duty_from_tcs: true   # NEW (F10) — bill thermostat duty, not 100 %
  load_shed:
    bus_voltage_v: 25.0        # unchanged (45.5 % SoC)
    restore_hysteresis_v: 0.5
    auto_restore: true         # NEW (F15) — make the documented restore real
    soc_stage_thresholds_pct: [30.0, 20.0, 10.0]
```

### `configs/eosat1/subsystems/fdir.yaml` (F9 — un-invert the ladder)

```yaml
  - { parameter: eps.bus_voltage, condition: "< 24.5", threshold: 24.5, level: 2, action: safe_mode_eps }    # was < 26  (58.4 % SoC)
  - { parameter: eps.bus_voltage, condition: "< 23.2", threshold: 23.2, level: 2, action: safe_mode_aocs }   # was < 24
```
Resulting monotonic ladder: **45.5 % → in-model shed · 39 % → EPS safe · 30/20/10 % → SoC stages · 22 % → AOCS safe.**

### Code changes

| # | file | change |
|---|---|---|
| C1 | `eps_basic.py:409-410`, `:374-376` | apply the `array_losses` stack; factor into one `_array_constant()` |
| C2 | `eps_basic.py:393-418` | SADA branch: when `sada_tracking`, wing contribution = `A_wing·K·‖ŝ − (ŝ·â)â‖` for drive axis `â`; when not tracking, use `solar_array_drive_angle` to place the wing normal. Body faces keep the existing cosine loop. |
| C3 | `eps_basic.py:552,560` | fix the sign: `v = ocv + bat_i*R` (bat_i > 0 on charge) |
| C4 | `eps_basic.py:537` | `d_soc = net * (charge_eff if net>0 else 1.0) / (cap*3600) * 100 * dt`; add CV taper above `cv_taper_start_soc_pct` |
| C5 | `eps_basic.py:477-483` | multiply heater line power by `shared_params[0x040E/0x040F]/100` when `heater_duty_from_tcs` |
| C6 | `eps_basic.py:452-455` | `transponder_hi_power_w` when the active TM tier is HIGH (read `shared_params[0x0506]`) |
| C7 | `eps_basic.py:648-660` | expected-power reference from `self._panel_areas` + the same loss stack + current sun geometry, not the dead `_panel_area` |
| C8 | `eps_basic.py:578-590` | implement restore at `bus > shed_v + hysteresis`, one line per tick, in reverse shed order, gated on `auto_restore` |
| C9 | `aocs_basic.py:1108` | drive `_orbit_phase` from the propagator's true argument of latitude, not `360/5700` |
| C10 | `eps_basic.py:390,412,433-436` | currents at live `bus_voltage`; delete the tautological `sa_*_voltage` and publish array-side MPPT input voltage or drop the parameter |
| C11 | `eps_basic.py:753`, `parameters.yaml:49-50,351-358` | resolve the 0x0129/0x012A/0x013A collisions; write the seven dead `subsys_power_*` / `power_margin` parameters |
| C12 | `eps_basic.py:164,210`, `:45`, `:31-40` | delete `_platform_idle_w`, `POWER_LINE_NOMINAL_W`; make `POWER_LINE_DEFS` wattages `None` so config is the only source |

### Predicted outcome (computed, 36 h runs, real propagator, dt = 10 s, start SoC 95 %)

| duty cycle | β=49.8° (worst generation) | β=24.2° (max eclipse) |
|---|---|---|
| gen / load / trend | | |
| (a) quiescent | 45.8 / 24.3 W, **+33.9 Wh/orbit** | 56.5 / 24.3 W, +50.7 Wh/orbit |
| (b) nominal ops day | 45.8 / 35.3 W, **+16.6 Wh/orbit** | 56.5 / 35.3 W, +33.5 Wh/orbit |
| (b2) nominal + 3 img/orbit | 45.8 / 36.1 W, **+15.3 Wh/orbit** | 56.5 / 36.9 W, +31.0 Wh/orbit |
| (c) worst case (payload+cooler through eclipse, TX 53 %, 24 dumps/day) | 45.8 / 25.1 W after shedding, **min SoC 44.5 %, 4 shed events** | 56.5 / 52.2 W, min SoC 67.9 % |
| **per-orbit min SoC** | | |
| (a) | **89.0 %** | 84.9 % |
| (b) | **85.1 %** | 75.2 % |
| (b2) | **84.0 %** | 75.2 % |
| (c) | **44.5 %** — full shed ladder fires | 67.9 % |

Margin ratio in nominal ops: **1.30 at the worst β, 1.60 at the best.** ECSS-style power margin at EOL/worst case: **+30 %** — inside the 20 % `margin_policy_percent` the courseware template declares (`~/cdf/configs/templates/6u_eo_cubesat.yaml:target_phase/margin_policy_percent`), and consistent with its `sa_power_eol_w: 60 ± 40 %` reference.

### Teaching consequence (item 5) — the tension is preserved and sharpened

| student action | energy cost | outcome |
|---|---|---|
| nominal plan as written | −18.3 Wh/eclipse on 100 Wh | min SoC 85 %, zero sheds — **the plan works** |
| leave payload imaging + FPA cooler on through one eclipse | 45 + 15 + 19.5 = 79.5 W × 2075 s = **45.8 Wh = 46 % DoD** | min SoC ≈ 49 % → crosses the 45.5 % shed on the second consecutive eclipse |
| book dumps on every gate-clearing pass (`downlink_catchup`, stride 1) | TX duty 33 → 53 %, HIGH 20 W: **+8.4 W orbit-average** | erodes the margin from +16.6 to +3.4 Wh/orbit — visible walk-down over 6–8 orbits |
| both | **−26 W orbit-average** | flat-battery trajectory, full shed ladder, EPS safe mode |
| forget to deploy a wing | generation 45.8 → 27 W | negative at every β — LEOP ordering becomes a real constraint |
| SADA stuck (device 0x0101 OFF, or `sada_tracking: false` at a bad angle) | wings drop to `\|sin φ\|` duty, −40 % | new, physically meaningful failure scenario |

Crucially the *margin is not large enough to hide mistakes*: at 1.30× worst-case, one sustained bad decision costs the whole margin, and the recovery is visible over a handful of orbits rather than instantly or never. The old configuration failed this test in the opposite way — the FDIR bus rule at 58 % SoC (F9) fired during *correct* operations, so students could not distinguish good planning from bad.

---

## Work packages

**WP-1 — Honest array performance model (loss stack)** — **M**
Files: `eps_basic.py` (`configure`, generation branches), `configs/eosat1/subsystems/eps.yaml` (`array_losses`), `smo-common/.../config/schemas.py` (`EPSConfig.array_losses`), `docs/SIMULATION_CONFIG.md`.
Acceptance: with `array_losses` at the recommended values and `sa_lifetime_hours` set to 3 years, `EPSBasicModel` at normal-incidence full sun on 1.0 m² returns **267.9 ± 1 W**; with `array_losses` omitted it returns the legacy 389.5 W (back-compat).
Depends on: none.

**WP-2 — Solar array drive: make `solar_array_drive_angle` load-bearing** — **M**
Files: `eps_basic.py` (generation, `set_solar_array_drive`, device gate 0x0101), `eps.yaml` (`sada_*`), `configs/eosat1/commands/tc_catalog.yaml`, `configs/eosat1/manual/01_eps.md`.
Acceptance: (a) with `sada_tracking: true`, wing generation over one orbit at fixed β is constant to within **±2 %** (no `|sin φ|` lobes); (b) commanding `set_solar_array_drive(angle=90)` with tracking off reduces wing generation by **≥ 60 %** orbit-average; (c) device 0x0101 OFF still yields 0 W.
Depends on: WP-1.

**WP-3 — Rebalance the hardware sheet (areas, wings, battery, line ratings)** — **S**
Files: `configs/eosat1/subsystems/eps.yaml`, `configs/eosat1/simulation.yaml`, `configs/eosat1/manual/01_eps.md`.
Acceptance: `sum(solar_panels body areas) ≤ 0.20 m²`; `wing_*_area_m2 == 0.15`; a new config-consistency test asserts the manual's tabulated wing area / deployed area / battery capacity **equal** the YAML values (F23).
Depends on: WP-1, WP-2.

**WP-4 — Fix battery electrical model (voltage sign, charge efficiency, CV taper)** — **S**
Files: `eps_basic.py:537,552,560`.
Acceptance: at 50 W net charge the bus reads **above** OCV, at 50 W net discharge **below** OCV; charging 100 Wh of net energy into an empty 100 Wh battery ends at **94 %**, not 100 %.
Depends on: none.

**WP-5 — Couple heater power to the TCS thermostat duty** — **S**
Files: `eps_basic.py:477-483`, `tcs_basic.py` (publish 0x040E/0x040F/0x0411 before EPS ticks), `simulation.yaml` (`heater_duty_from_tcs`).
Acceptance: with the battery zone held at 15 °C the `htr_bat` line current is **0 A** while the line is ON; with the zone at −5 °C it is 6 W/V_bus; `Σ line power == power_cons` still holds.
Depends on: model tick-order check in `engine.py`.

**WP-6 — Un-invert the protection ladder** — **S**
Files: `configs/eosat1/subsystems/fdir.yaml`, `configs/eosat1/simulation.yaml`, `configs/eosat1/fdir/fault_propagation.yaml`, `docs/OPERATIONS_GUIDE.md`.
Acceptance: a test converts every voltage threshold to SoC via the configured OCV curve and asserts the sequence is **strictly monotonic decreasing** in SoC (shed 45.5 % > EPS-safe 39 % > SoC-stage-1 30 % > AOCS-safe 22 % > stage-2 20 % > stage-3 10 %), and that the mildest action has the highest threshold.
Depends on: WP-4 (voltage sign changes the mapping by ~1.3 pts).

**WP-7 — Load-shed restore** — **S**
Files: `eps_basic.py:578-590`, `simulation.yaml` (`auto_restore`).
Acceptance: after a shed at 24.9 V, raising the bus to 25.6 V restores lines in reverse shed order, one per tick; with `auto_restore: false` nothing restores. `_shed_hysteresis_v` is read.
Depends on: WP-4.

**WP-8 — Phase-lock the AOCS sun sweep to the true orbit** — **S**
Files: `aocs_basic.py:1108-1109`, `_tick_css`.
Acceptance: over 100 orbits the orbit-average generation at fixed β varies by **< 1 %** (currently ±8 %); the sun vector is a deterministic function of `orbit_state`, not of accumulated `dt`.
Depends on: none. *Blocks meaningful regression testing.*

**WP-9 — Attitude-dependent generation + power-positive attitude mode** — **L**
Files: `aocs_basic.py` (`_tick_css` to use the attitude quaternion; add `MODE_SUN_POINT`), `eps_basic.py`, `configs/eosat1/aocs/aocs_mode_codes.yaml`, `configs/eosat1/procedures/contingency/progressive_load_shed.md`.
Acceptance: commanding sun-pointing mode raises orbit-average generation by **≥ 25 %** over nadir-pointing at β = 49.8°; commanding a 40° off-nadir slew reduces it measurably; `att_error > 30°` degrades generation.
Depends on: WP-2, WP-8.

**WP-10 — Fix the false "solar array degraded" alarm** — **S**
Files: `eps_basic.py:648-660`; delete `arrays:` from `eps.yaml` and `_panel_area` from the model.
Acceptance: a clean 24 h nominal run emits **zero** 0x010B events; injecting `solar_panel_loss(face='py', magnitude=0.5)` emits exactly one.
Depends on: WP-1, WP-3.

**WP-11 — EPS telemetry repair and completion** — **M**
Files: `eps_basic.py` (telemetry block), `configs/eosat1/telemetry/parameters.yaml`, `configs/eosat1/telemetry/hk_structures.yaml` (SID 1), `configs/eosat1/xtce/eosat1.xml`, `configs/eosat1/smdl/eps.xml`, `configs/eosat1/mcs/displays.yaml`.
Deliver: resolve 0x0129 / 0x012A / 0x013A collisions; populate `power_margin` and the six `subsys_power_*`; **add** per-wing array current (+Y wing, −Y wing separately from the body faces), total bus current, net battery charge current in cyclic HK (0x0143 is written but sits behind `on_demand` panels), predicted end-of-eclipse SoC, and `wing_status`/`sada_angle` in SID 1.
Acceptance: every parameter declared with `subsystem: eps` in `parameters.yaml` is written by a model on every tick or is explicitly marked `on_demand`; a test asserts `abs(Σ line_current × bus_voltage − power_cons) < 0.5 W` and `abs(Σ panel_current × bus_voltage − power_gen) < 0.5 W`.
Depends on: WP-1, WP-2, C10.

**WP-12 — Tier-dependent transmitter power** — **S**
Files: `eps_basic.py:452-455`, `eps.yaml` (`transponder_hi_power_w`), `ttc_basic.py` (publish active tier).
Acceptance: with the HIGH tier active the `ttc_tx` line draws 20 W; at LOW, 12 W; a 24 h `downlink_catchup` plan costs **≥ 5 Wh/orbit** more than `nominal_ops_day`.
Depends on: none.

**WP-13 — Dead-config and dead-code sweep** — **S**
Files: `eps_basic.py:31-45,164,210`, `eps.yaml:4-9,12`.
Acceptance: `platform_idle_power_w`, `arrays:`, `POWER_LINE_NOMINAL_W`, `_platform_idle_w`, `_panel_area` are gone; a config-lint test fails if any key in `subsystems/*.yaml` is never read by a model.
Depends on: WP-1, WP-10.

**WP-14 — Single eclipse model** — **S**
Files: `smo-common/.../orbit/eclipse.py`, `propagator.py:173`.
Acceptance: `propagator._is_eclipse` is deleted and `is_in_eclipse` used; optionally expose `eclipse_fraction` so generation ramps through penumbra. Existing eclipse tests unchanged.
Depends on: none.

---

## Verification

### Tests to write / rewrite

1. **`tests/test_simulator/test_power_budget_regression.py` — rebuild on the real propagator.**
   Replace the synthetic `_Orbit` / `_sun_vector` / `ECLIPSE_FRACTION = 0.38` fixtures (which encode findings 3 and 7) with the configured `OrbitPropagator`. Parametrise over `[t0, t0+90d, t0+270d]` so **β = 49.8 / 31.9 / 24.2°** are all exercised, and derive the TX timeline from `contact_windows()` + `simulation.yaml ttc.auto_tx_hold_s` rather than a hard-coded half-orbit.
   Assertions: per-orbit min SoC **> 70 %** for quiescent and nominal-ops-day at every β; **zero** shed events; per-orbit minimum converging (`|min[-1] − min[-2]| < 0.3 pt` by orbit 12); orbit-average margin **≥ +10 W** at the worst β.

2. **`test_power_budget_abuse.py` (new) — the tension must survive.**
   Assert that payload-imaging-through-eclipse *and* stride-1 dumps drive min SoC **below 50 %** and trip **≥ 1** shed stage within 6 orbits; and that a stowed-wing profile is net-negative at every β.

3. **`test_eps_generation_physics.py` (new).**
   - Loss stack: 1 m² at normal incidence, EOL ⇒ **267.9 ± 1 W** (WP-1).
   - SADA: orbit-average wing power flat to ±2 % vs orbit phase at fixed β (WP-2).
   - Sun-vector determinism: generation is a pure function of `(β, true anomaly)` — same value after 100 orbits (WP-8).
   - Geometry sanity: `sum(body areas) ≤ 0.20 m²`; deployed total ≤ 0.40 m²; wing area per wing ≤ 0.16 m².

4. **`test_eps_battery_physics.py` (new).**
   Voltage sign on charge vs discharge (WP-4); coulombic efficiency (100 Wh in ⇒ 94 % SoC); `power_gen − power_cons` equals `bat_current × bat_voltage` to within 2 %.

5. **`test_protection_ladder_monotonic.py` (new).**
   Load `fdir.yaml`, `simulation.yaml`, `fault_propagation.yaml`; convert every bus-voltage threshold to SoC through the configured OCV curve; assert strict monotonicity and that action severity increases as SoC falls (WP-6). This is the test that would have caught finding 9.

6. **`test_eps_telemetry_completeness.py` (new).**
   Every `subsystem: eps` parameter in `parameters.yaml` is written each tick or flagged `on_demand`; no two parameters share an ID across `parameters.yaml` / model writes / `engine.py` (catches 0x0129/0x012A/0x013A); `Σ line_current × V_bus == power_cons`; `Σ panel_current × V_bus == power_gen`.

7. **`test_config_manual_consistency.py` (extend existing content tests).**
   Parse the tables in `configs/eosat1/manual/01_eps.md` and assert wing area, deployed area, battery capacity and peak BOL power match `eps.yaml` within 5 % (catches finding 23 permanently).

8. **`test_no_spurious_eps_events.py` (new).**
   24 h nominal run emits zero 0x010B "array degraded" events; a single injected panel loss emits exactly one (WP-10).

### Analysis script for `tools/`

**`tools/power_budget.py`** — the durable replacement for the one-off `docs/review_2026-07/eps_sim.py`, living in-tree and CI-runnable:

```
python -m tools.power_budget --config configs/eosat1 \
       --profile quiescent|nominal|worst|<plan-id> \
       --start t0 --days 3 --sweep-beta --report md|json
```

Responsibilities:
- Load `orbit.yaml`, `subsystems/eps.yaml`, `simulation.yaml`, `planning/activity_types.yaml`, `planning/standard_plans.yaml` — **no duplicated constants**.
- Derive contact statistics from the propagator (passes/day, mean duration, ≥10° fraction, merged TX duty including `auto_tx_hold_s`) instead of assuming them — the exact step whose absence caused this defect.
- Sweep β over a year and report the **worst-case** orbit-average generation, eclipse fraction and per-orbit minimum SoC, not a single epoch.
- Emit the two tables above (generation line-by-line per face; load line-by-line per power line with its duty cycle), plus the margin ratio and the ECSS power margin at EOL.
- Exit non-zero if worst-case nominal-ops margin < +10 % or if any shed stage trips in the nominal profile — so CI, not a human, owns the closure claim.
- Optionally emit `--check-manual` to diff the computed peak/EOL power against `manual/01_eps.md`.

Also add **`tools/pass_gap_analysis.py --power`** (or fold into the above) so that changing `orbit.yaml ground_stations` immediately re-prints the TX duty cycle that feeds the budget — the coupling that was missed when the station list went from 2 to 5.
