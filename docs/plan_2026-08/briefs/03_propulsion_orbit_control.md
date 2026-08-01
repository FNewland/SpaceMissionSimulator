# Workstream 3 — Orbit control / propulsion

**Abstract.** The EO-Sat-1 courseware teaches propulsion in depth and ships operational procedures (NOM-004, NOM-005, CTG-006), commands (`PROP_ARM`, `PROP_ISO_CLOSE`, `ORBIT_MANOEUVRE_LOAD`, and the already-catalogued `HEATER_THRUSTER` S8 func 42) and a Propulsion Engineer console position — but the simulator has no propulsion at all, so all of it is un-runnable. This brief closes that gap with a deliberately minimal capability sized to the actual spacecraft: a 1U green-monopropellant (ADN/LMP-103S-class) blowdown module, 0.33→0.14 N, Isp 220→196 s, 0.400 kg propellant, giving 66.9 m/s against a 56.0 m/s requirement dominated by drag make-up at 450 km (21.3 m/s/yr at the design solar activity, computed from B = 77.9 kg/m²). The critical engineering finding is integration, not physics: the simulator, planner and MCS each hold an independent immutable SGP4 `Satrec` built from `orbit.yaml`, and breakpoints save only the propagator clock on the explicit assumption that "SGP4 is stateless given a UTC time" — an assumption propulsion breaks. The recommended mechanism is a per-burn SGP4 differential correction (measured: 4–6 iterations, sub-millimetre residual, 0.51 m drift over 24 h on the identity case, and −33.55 s of along-track timing after 24 h for a 1.0 m/s burn against an analytic −33.9 s), a versioned element set served over `/api/state` to planner and MCS, and that element set serialised into the breakpoint. The brief specifies the S8 function block 85–95, telemetry block 0x0B00, HK SID 7, event block 0x0800, S12 monitors, seven S19 FDIR rules, MCS/planner UI, four ops procedures reconciled against the book's numbering, five training scenarios, twelve work packages with testable acceptance criteria, and fourteen explicit exclusions.

---

## Textbook requirements

Everything below already exists in courseware and is un-runnable today (`PROP_ARM` has **zero** hits in `packages/` or `configs/`; S8 catalogue occupies 0–84 + 100–107 with no propulsion block).

| Artefact | ID / name | Source | What it demands of the sim |
|---|---|---|---|
| Ops card | **NOM-004 — Orbit Maintenance Manoeuvre Planning (passive)** | `~/cdf/docs/course/orbit_north_week3.md:5682–5700` | FDS ΔV plan + execution time; FD authorisation; **S11 time-tagged** upload of manoeuvre commands; propulsion *health flag* gating (defer → CTG-006) |
| Ops card | **NOM-005 — Orbit Maintenance Manoeuvre Execution** | `orbit_north_week3.md:5707–5726` | Step 1 read SID 2 (attitude for burn); Step 2 **`PROP_ARM`** → "Arm flag set"; Step 3 OBC executes time-tagged burn; Step 4 "capture post-burn state vector (read SID 6 / GPS), ΔV vs plan"; Step 5 GO NOM-007 if wheels near saturation. Deviations: burn doesn't start → *check propulsion enable*; burn under-performs → re-plan; **AOCS off-axis during burn → CTG-002 / safe-mode candidate** |
| Ops card | **CTG-006 — Propulsion Anomaly** | `orbit_north_week3.md:6075–6093` | Trigger = "propulsion FDIR rule fires (**low thrust, valve fault, tank pressure out-of-band**)"; Step 1 read *all valve/pressure/temperature TM* from **"SID 5 / propulsion"**; Step 2 **`PROP_ISO_CLOSE`** "both branches"; Step 3 compute remaining usable ΔV envelope; stuck-open valve → isolate + bound remaining propellant; rising tank pressure → CTG-004 + EMG-001 |
| Command | **`PROP_ARM`**, **`PROP_ISO_CLOSE`** | as above | Two named TCs |
| Command | **`ORBIT_MANOEUVRE_LOAD` (S20 parameter set)** | `orbit_north_week3.md:912` | Upload of a pre-planned manoeuvre vector produced by FDS |
| Command | **`HEATER_THRUSTER` (S8 func 42)** | `orbit_north_week3.md:1021, 1850, 7073`; **exists already** at `configs/eosat1/commands/tc_catalog.yaml:805–814`, circuit `2=thruster` at :825 | Thruster/catbed heater — currently a *false success*: `models/tcs_basic.py:496` forces `htr_thruster=False`, and `_NO_HEATER_CIRCUITS={"thruster"}` (`:114`) rejects setpoint/auto commands (defect #31) |
| Console position | **Propulsion Engineer** | `facilitator_book_expanded.md:9191–9250`, `:639` | Owns ΔV budget, propellant choice, thruster sizing, Tsiolkovsky, plume plan. Not in `configs/eosat1/mcs/positions.yaml` |
| Console position | **FDS / Orbit Analyst** | `orbit_north_week3.md:1175–1210`, `:2069` | "Produces the manoeuvre vector the AOCS Engineer uploads"; FDS functions table lists *Manoeuvre planning* ("Compute ΔV for orbit maintenance — inputs: target orbit, **propulsion model**") and *End-of-life planning* ("**remaining propellant**") |
| Mode | Spacecraft **Manoeuvre** mode | `orbit_north_week3.md:440`, `:452`, `:698` | "Orbit adjustment or desaturation, 15–25 min, HK only, scheduled, constraints met"; listed in the minimum operational-mode set |
| Case study | §16.8 "the manoeuvre that wasn't needed" | `orbit_north_week3.md:7356–7380` | Requires that planning a burn *costs* propellant and perturbs AOCS |
| Design chapter | Ch 3.2 §6 "Propulsion System Design" | `facilitator_book_expanded.md:5452–5643` | Tsiolkovsky; ΔV-need table (orbit maintenance 5–15 m/s/yr, deorbit 50–150, CAM 1–5/event, station-keeping 1–10/yr); five-technology comparison table (Isp / thrust / prop mass / dry mass / burn time / **power during firing** / cost / TRL); worked 100 m/s comparison; "**Orbit < 500 km: natural decay provides FCC 5-year compliance**" |
| Design chapter | Ch 2.2 §5 Hohmann/deorbit | `facilitator_book_expanded.md:3326–3348` | 600→200 km = 113 m/s worked example |
| Regulatory | IADC 25-yr / FCC 5-yr, P(disposal) ≥ 0.9 | `facilitator_book_expanded.md:1551–1602` | Disposal argument must be made explicitly |

**Numbering conflict to resolve.** The sim's `procedures/procedure_index.yaml` has **NOM-004 deliberately reserved** (index skips NOM-003 → NOM-005) — confirmed by `~/cdf/docs/PART2_SIM_RECONCILIATION.md:846–851`. But sim NOM-005 = *HK Configuration*, NOM-006 = *Software Upload*, NOM-007 = *Momentum Management* (NOM-007 matches the book), and sim **CTG-006 = Payload Anomaly** whereas book CTG-006 = *Propulsion Anomaly*. See "Procedures" in the interface spec for the recommended reconciliation.

---

## Hardware recommendation

### Spacecraft & environment (from the repo, not assumed)

| Quantity | Value | Source |
|---|---|---|
| Form factor / dry mass | 6U, ~12 kg | `configs/eosat1/manual/00_mission_overview.md` §4.2 |
| Orbit | 450 km circular SSO, i = 98°, dawn–dusk LTAN, T = 93.59 min, v = 7640 m/s, a = 6828.14 km | `configs/eosat1/orbit.yaml:13–18`, `manual/07_orbit_ops.md` §2 |
| Ram area (nadir-pointing, +X along-track, wings edge-on) | A ≈ 0.07 m² (+X body face 0.060 m² + wing edges) | `subsystems/eps.yaml:25` `px_area_m2: 0.060`; frame def `manual/00…:4.1` |
| Ballistic coefficient | **B = m/(C_D A) = 12.0/(2.2 × 0.07) = 77.9 kg/m²** | derived |
| Power state | orbit-avg generation **46.0–53.8 W**, load 39.6–56.8 W, margin **−17 … +22 Wh/orbit**; battery 120 Wh | `docs/review_2026-07/power_analysis.md` §2 |
| Mean pass length | 445 s (5-station network, 103 passes/48 h) | `configs/eosat1/orbit.yaml:35–37` |

**Power is the binding constraint.** A budget that swings ±20 Wh per 93.6-min orbit ⇒ ~±13 W of orbit-average headroom, and P2/P3 profiles are already net-negative at unfavourable sun/eclipse alignment. Any thruster needing tens of watts *continuously for weeks* is disqualified on arithmetic, not taste.

### Trade

| Option | Isp (s) | Thrust | Power while firing | m_prop for 57 m/s on 12 kg | System dry | Burn time for 0.85 m/s | Verdict |
|---|---|---|---|---|---|---|---|
| Cold gas N₂ (30 MPa COPV) | 40–75 | 10–100 mN | <1 W | **0.71 kg** (Isp 60) | 0.3 kg + COPV | ~3 min | ✗ propellant + 300-bar COPV = 8–10 % of a 12 kg bus; tank is a safety/volume problem in 6U |
| Cold gas R-236fa (VACCO MiPS) | 40 | 25 mN | <1 W | **1.09 kg** | 0.3 kg | ~7 min | ✗ worst propellant mass |
| Warm gas / butane resistojet (Busek BGT-X5-class, NanoAvionics EPSS-butane) | 80–100 | 5–50 mN | **5–15 W for the whole burn** | 0.47 kg (Isp 90) | 0.5 kg | 5–30 min | △ viable but the heater *is* the thruster: a 20-min burn at 15 W = 5 Wh ≈ half the orbit margin, every burn; and 30 mN cannot finish a 3 m/s burn inside a 445 s pass |
| **Green monoprop, ADN/LMP-103S class (NanoAvionics EPSS C1, Bradford HPGP)** | **213 (blowdown-avg)** | **0.33 → 0.14 N** | **12 W preheat ×20 min, ~2 W during burn** | **0.40 kg** | **1.15 kg (1U incl. tank+valves)** | **32 s (BOL) / 73 s (EOL)** | ✅ **selected** |
| Hydrazine | 220–230 | 0.5–1 N | 8–20 W catbed | 0.39 kg | 2–3 kg | 20 s | ✗ toxic; CubeSat-deployer rule "no toxic propellants" (`facilitator_book_expanded.md:1491`); ground-handling cost kills a teaching programme |
| AF-M315E (Aerojet MPS-130) | 235 | 1 N | 2–10 W | 0.36 kg | **3.0 kg = 25 % of bus** | 5 s | ✗ system dry mass dominates at 12 kg (the book's own worked example makes this point) |
| PPT | 500–1000 | 10s of µN | 1–10 W | 0.07 kg | 0.5 kg | days | ✗ total impulse ~1–10 N·s; needs ~700 N·s |
| Resistojet (water, Pale Blue) | 70–80 | 1–4 mN | 15–20 W | 0.55 kg | 1.2 kg | ~1 h | ✗ power × duration |
| Electrospray / FEEP (Enpulsion NANO R3) | 1000–5000 | 0.35 mN | **40 W continuous** | 0.06 kg | 0.9 kg | **~8 h per burn, months for the campaign** | ✗ 40 W ≫ available orbit-average margin; burns cannot be observed in a pass |
| Iodine gridded ion (ThrustMe NPT30-I2) | 1000–2400 | 1.1 mN | **65 W** | 0.04 kg | 1.2 kg | ~2.5 h | ✗ 65 W is more than total orbit-average generation minus platform load |
| Hall (Exotrail ExoMG-nano, Busek BHT-200) | 800–1370 | 13–40 mN | **60–200 W** | 0.05 kg | 1.0–1.5 kg | ~10 min | ✗ 1.5–4× the entire power generation |
| **No propulsion (baseline)** | — | — | — | 0 | 0 | — | ✗ *only* fails because the courseware needs it — see disposal note |

### Selected: 1U green-monopropellant module, blowdown-pressurised

| Parameter | Value | Working |
|---|---|---|
| Propellant | LMP-103S / ADN blend, ρ = 1240 kg/m³ | `facilitator_book_expanded.md:5520` table |
| Thrusters | **1 ×**, mounted on −X, thrust along **+X body** (= prograde in nominal nadir attitude). Retrograde burns require a 180° yaw. | deliberate AOCS coupling |
| Thrust | **0.33 N (22 bar BOL) → 0.14 N (9.3 bar EOL)**, F ∝ P_feed | blowdown |
| Isp | **220 s BOL → 196 s EOL, 208 s mean** | |
| Propellant load | **0.400 kg** (323 cm³) | |
| Tank | **560 cm³**, ullage 237 cm³, N₂ blowdown **22.0 → 9.33 bar (2.36 : 1)**, isothermal P·V_ull = const | 22 × 237/560 = 9.33 bar |
| Catalyst bed | preheat to ≥ 200 °C, **12 W for 20 min = 4.0 Wh**; arm refused below min bed temperature | reuses `HEATER_THRUSTER` func 42 |
| Wet system mass | 1.15 kg dry + 0.40 kg prop = **1.55 kg (12.9 % of 12 kg)**, 1U of 6U | |
| ṁ | F/(Isp g₀) = 0.33/2157 = **0.153 g/s** at BOL | |
| **Total ΔV capability** | **v_e ln(m₀/m_f) = 2040 × ln(12.40/12.00) = 66.9 m/s** | Isp_avg 208 s → v_e 2040 m/s |

### ΔV budget

Drag: `a_D = ½ρv²/B`; make-up ΔV = ∫a_D dt (exact for circular orbits: `da/dt = −2 a_D a/v`, and a tangential ΔV gives `da = 2a ΔV/v`, so the two cancel to ΔV = ∫a_D dt).

At 450 km, v = 7640 m/s, B = 77.9 kg/m²:

| Solar activity | ρ (kg/m³) | a_D (m/s²) | **ΔV (m/s/yr)** | decay rate |
|---|---|---|---|---|
| Solar min (F10.7 ≈ 70) | 3.5 × 10⁻¹³ | 1.31 × 10⁻⁷ | **4.1** | −7.4 km/yr |
| CIRA-72 mean (Vallado exp. atm., 450 km) | 1.585 × 10⁻¹² | 5.94 × 10⁻⁷ | **18.7** | −33.5 km/yr |
| Moderate-high (F10.7 ≈ 150) — **design case, declining cycle 25, 2026-29** | 1.8 × 10⁻¹² | 6.74 × 10⁻⁷ | **21.3** | −38 km/yr |
| Solar max (F10.7 ≈ 220) | 6.0 × 10⁻¹² | 2.25 × 10⁻⁶ | **70.9** | −127 km/yr |

| # | Task | ΔV (m/s) | Working |
|---|---|---|---|
| 1 | **Drag make-up**, 2 yr of active altitude control | **42.6** | 2 × 21.3, ρ = 1.8e-12 |
| 2 | Repeat-ground-track / **LTAN control by semi-major-axis biasing** | 3.0 | phasing allowance; see note |
| 3 | **Collision avoidance**, 6 events × (0.05 out + 0.05 back) | 0.6 | 500 m along-track miss at ≥3 h lead: δv = Δx/(3T) = 500/(3×10800) = **15 mm/s**; 0.05 m/s is 3× margin |
| 4 | Commissioning + thrust-calibration burns, 2 × 0.5 | 1.0 | |
| 5 | Eccentricity-vector / frozen-orbit control | 1.5 | |
| | **Deterministic subtotal** | **48.7** | |
| 6 | ECSS Phase-C margin, 5 % | 2.4 | |
| 7 | Ops/dispersion reserve, 10 % | 4.9 | |
| | **Total requirement** | **56.0** | |
| 8 | **Propulsive disposal** | **0** | see below |
| | **Capability** | **66.9** | |
| | **Margin** | **+10.9 m/s = +19.5 %** | |

**Why no plane-change / no deorbit ΔV — the two scoping decisions that keep this "minimal".**

- *Inclination*: ΔV = 2v sin(Δi/2) ⇒ **0.01° = 1.33 m/s, 0.03° = 4.00 m/s, 0.1° = 13.3 m/s**. Lunisolar inclination drift on an SSO is ~0.02–0.03 °/yr, so direct LTAN control would cost ~8 m/s/yr — 40 % of the entire budget. Correct engineering answer: **control LTAN through nodal-regression rate, i.e. through semi-major axis**, which the drag-make-up control law already commands. Budget line 2 only, no plane change.
- *Disposal*: natural decay from 450 km at B = 77.9 kg/m² (numerically integrating `da/dt = −2a_D a/v` down to 120 km) = **1.7 yr (mean), 4.8 yr (solar min), 0.6 yr (solar max)** — compliant with the FCC 5-yr rule at every solar condition, and **0.2 yr if the wings are commanded broadside** (A = 0.66 m² ⇒ B = 8.3), giving a controllable backup. A propulsive deorbit is **42.6 m/s (450→300 km perigee) / 57.0 m/s (→250) / 71.6 m/s (→200)** — i.e. a second complete propulsion system. Explicitly disclaimed; the disposal argument is passive + attitude-biased drag.

**Burn arithmetic (what the operator will actually see).**

| | BOL (0.33 N, 12.40 kg, a = 26.6 mm/s²) | EOL (0.14 N, 12.02 kg, a = 11.6 mm/s²) |
|---|---|---|
| 0.20 m/s (CAM) | 7.5 s, 1.1 g | 17.2 s, 1.3 g |
| 0.85 m/s (fortnightly drag make-up) | **31.9 s, 4.9 g** | 73.0 s, 5.3 g |
| 3.00 m/s (monthly campaign) | 112.7 s, 17.2 g | **257.7 s**, 18.8 g |

All fit inside the 445 s mean pass — NOM-005's "typically over a ground station for direct observation" is satisfiable. ~26 burns/yr at 0.85 m/s; 0.40 kg supports ~75–80 burns.

**Observability (the post-burn OD check in NOM-005 step 4).** Δa = 2a·ΔV/v, ΔT = 3π·Δa/v:

| ΔV | Δa | ΔT/orbit | AOS drift | after 7 days |
|---|---|---|---|---|
| 0.2 m/s | 0.36 km | 0.44 s | 6.8 s/day | 0.8 min |
| **1.0 m/s** | **1.79 km** | **2.20 s** | **33.9 s/day** | **4.0 min** |
| 3.0 m/s | 5.36 km | 6.61 s | 101.8 s/day | 11.9 min |

Verified numerically against SGP4 (see Model design): a 1.0 m/s prograde burn gives −33.55 s of along-track timing after 24 h vs the analytic −33.9 s.

---

## Model design

### The critical integration problem (with exact file:line)

| Consumer | Where | How it gets the orbit | Effect of a ΔV today |
|---|---|---|---|
| Simulator engine | `packages/smo-simulator/src/smo_simulator/engine.py:153–160` | builds `OrbitPropagator(tle_line1, tle_line2, …)` from `load_orbit_config` | — |
| Propagation | `smo-common/src/smo_common/orbit/propagator.py:75` `Satrec.twoline2rv(...)`; `:164` `self._sat.sgp4(jd, fr)` | **single immutable `Satrec`** built from the TLE strings | any ΔV is invisible |
| Contact windows | `propagator.py:100–157` (`contact_windows`, `:132` `self._sat.sgp4`) | same `Satrec` | passes unchanged |
| Eclipse (live) | `propagator.py:173` `_is_eclipse(r, sun_eci, …)`; forward scan `:221–261` `next_eclipse_transition` (`:244` `self._sat.sgp4`) | same `Satrec` | eclipse unchanged |
| Power | `eps_basic` reads `orbit_state.in_eclipse` → param `0x0108` | via `OrbitState` | unchanged |
| Imaging opportunities / ground track | `smo-planner/server.py:565–612` (`_compute_contacts`, `_compute_ground_track`) construct **fresh `OrbitPropagator(self._tle1, self._tle2, …)` per call**, from `self._tle1/_tle2` cached at `server.py:145–146` | **its own copy of the TLE strings from `orbit.yaml`** | planner would never see the burn |
| MCS orbit display / MET | `smo-mcs/server.py:735–801` `_init_orbit_propagator` — raw `yaml.safe_load` of `orbit.yaml`, `:801` `OrbitPropagator(tle1, tle2, …)`; `:817+` `_compute_orbital_state` calls `reset(ground_utc)` | **a third independent copy** | MCS would never see the burn |
| Breakpoints | `smo-simulator/breakpoints.py:88–93` saves only `state["orbit_utc"]`; `:150–161` restores via `orbit.reset(utc)` | comment at `:84–87`: *"SGP4 is stateless given a UTC time, so the propagator's `_sim_utc` is sufficient"* — **this assumption is broken by propulsion** | a restored breakpoint silently reverts every burn |

**Therefore three things must change together, or the feature is cosmetic:** (1) the propagator must own a *mutable, versioned* element set; (2) that element set must be served over the wire to planner and MCS; (3) it must be serialised into the breakpoint.

### How a ΔV is applied to SGP4 — measured, not assumed

Naïve approach (take SGP4 osculating r,v, apply ΔV, convert to Keplerian, write those as TLE **mean** elements) **fails**: measured on the shipped `orbit.yaml` TLE, the round-trip residual is **12.5 km / 4.5 mm/s at the epoch itself**, growing to **335 km (≈44 s of AOS timing) after 24 h** — because SGP4 mean elements ≠ osculating elements (short-period J2 terms). A burn would produce a visible ~1.6 s ground-track jump plus a 44 s/day bogus drift.

**Recommended: SGP4 differential correction (a 6-parameter TLE fit), run once per burn.** Seed with the osculating elements, iterate `p ← p + J⁺·(target − sgp4(p))` with numerically differenced J. Measured on this repo's TLE:

| Case | iterations | residual | 24 h drift vs original TLE |
|---|---|---|---|
| No-burn re-fit (identity test) | **4** | **0.0022 m / 0.0021 mm/s** | **0.51 m** |
| ΔV = 1.0 m/s prograde | **6** | 0.0005 m / 0.0003 mm/s | −255.1 km along-track = **−33.55 s** (analytic prediction: −33.9 s ✔) |

Cost: ~40 SGP4 evaluations per burn, i.e. microseconds, **once**, not per tick. Everything downstream stays on the *single existing code path* (SGP4 on a `Satrec`). No state-vector propagation mode, no dual physics.

### `OrbitPropagator` changes (`smo-common/orbit/propagator.py`)

```
+ self._epoch_version : int            # increments on every burn
+ self._elset         : dict           # {epoch_iso, a_km, ecc, incl, raan, argp, M, bstar, ndot}
+ def apply_delta_v(dv_eci_km_s: np.ndarray, utc: datetime) -> dict
      r, v  = sgp4 at utc
      v'    = v + dv_eci
      elset = _fit_tle(utc, r, v', bstar, ndot)      # differential correction, ≤10 iters
      self._sat = _satrec_from_elset(elset); self._epoch_version += 1
      return {"a_before_km","a_after_km","da_km","dv_applied_m_s","epoch_version"}
+ def get_elset() / set_elset(d)        # breakpoint + wire serialisation
```
`contact_windows`, `next_eclipse_transition`, `_elevation_at`, `_propagate_internal` need **no change** — they already go through `self._sat`.

Sanity guards (reject the fit, keep the old elset, raise event `PROP_ORBIT_FIT_FAILED`): residual > 1 m; |Δa| > 50 km; ecc > 0.02; fit non-convergent in 10 iterations.

### `models/prop_basic.py` — state machine

```
SAFED(0) ─PROP_ARM──────► ARMED(1) ─PROP_EXEC──► FIRING(2) ─┬─ nominal ─► ARMED
   ▲            (interlocks)     ▲                          ├─ PROP_ABORT ─► SAFED
   └── PROP_DISARM / abort ──────┘                          └─ FDIR abort ─► SAFED (latched INHIBIT(3))
INHIBIT(3) cleared only by PROP_INHIBIT_CLR (ground)
```
Arm interlocks (all must hold; each failure has its own rejection code, per `format_command_rejection` in `smo_common/protocol/pus_services.py`):

| Interlock | Threshold | Source |
|---|---|---|
| Catalyst-bed temperature | `tcs.temp_thruster` (0x0409) ≥ 180 °C | reuses the existing thruster thermal zone |
| Tank pressure in band | 6.0 ≤ P ≤ 24.0 bar | |
| Propellant remaining | > 5 g | |
| AOCS mode ∈ {NOMINAL(4), FINE_POINT(5)} | `aocs.aocs_mode` 0x020F | `aocs_basic.py:170–177` mode constants |
| Attitude error | `aocs.att_error` (0x0217) < 2.0° | |
| Battery SoC | `eps.bat_soc` (0x0101) > 45 % | |
| Spacecraft phase | ≥ 6 (NOMINAL) | `engine.SC_PHASE_NOMINAL` |
| Not in eclipse (configurable) | `orbit_state.in_eclipse == False` | teaching scenario overrides this |

### Equations

| Quantity | Equation |
|---|---|
| Feed pressure (blowdown, isothermal) | `P = P₀ · V_ull0 / (V_tank − m_p/ρ)` |
| Thrust | `F = F₀ · P/P₀`, clamped ≥ 0; × `perf_factor` (failure knob) |
| Specific impulse | `Isp = Isp_EOL + (Isp_BOL − Isp_EOL)·(P − P_EOL)/(P₀ − P_EOL)` |
| Mass flow | `ṁ = F/(Isp·g₀)` |
| Propellant depletion | `m_p ← m_p − ṁ·dt_sub` |
| Spacecraft mass | `m = m_dry + m_p` (published as TM; consumed by the ΔV integrator) |
| ΔV accumulation | `dv_body += (F/m)·dt_sub` along **+X body** |
| **Attitude coupling** | effective along-track `Δv_at = Δv·cos θ`, lateral `Δv_lat = Δv·sin θ`, where `θ = aocs.att_error`. Both are integrated and applied: the ECI ΔV vector is `Δv_at·v̂ + Δv_lat·ĥ` (ĥ = orbit normal, so lateral error shows as inclination/RAAN change). **Loss = 1−cos θ: 0.06 % @2°, 0.38 % @5°, 1.5 % @10°, 13.4 % @30°.** |
| Disturbance torque back on AOCS | thrust offset `d = 5 mm` ⇒ `T = F·d = 1.65 mN·m` at BOL; injected as an attitude-error bump and wheel-momentum accumulation `ΔH = T·t_burn` (32 s ⇒ 0.053 N·m·s ≈ 10 % of the 0.5 N·m·s S12 yellow limit on 0x025B) — this is the mechanical reason NOM-005 step 5 says "GO NOM-007 if wheels approached saturation" |
| Catbed thermal | `dT/dt = (P_htr·η + Q_combustion − (T−T_env)/R)/C`; unfired zone reverts to the existing passive law at `tcs_basic.py:497` |
| Inertia | `I = I_dry + (m_p/m_p0)·ΔI_prop`; **reported in TM only** — the AOCS model is kinematic (`_tick_nominal`, `aocs_basic.py:732–756`, propagates the quaternion by a proportional correction, no inertia tensor anywhere in the repo), so there is nothing to feed it into. Documented, not faked. |

### Impulsive vs finite burn — recommendation

**Finite burn, integrated with fixed 1 s sub-steps, with a speed clamp.** Reasoning:

- `engine._run_loop` (`engine.py:1043`) computes `dt_sim = dt_real × self.speed`, and `simulation.yaml:27` allows `speed.max: 1000.0`. At 1000× a single tick is 1000 s of sim time — longer than any burn. A pure impulsive model would silently apply the whole ΔV at an arbitrary point ±1000 s from the commanded epoch, and the "burn abort on attitude error" teaching moment would be unobservable.
- Therefore: while `prop.state == FIRING`, `prop_basic.tick()` sub-steps internally at `min(dt, 1.0) s` (bounded to 600 sub-steps/tick), **and** the engine clamps `self.speed ≤ prop.max_speed_during_burn` (default 10×) with an event `PROP_SPEED_CLAMPED`. A 32 s burn at 10× is 3.2 s of wall time — still fast enough to be usable, slow enough to be watched.
- The ΔV is nevertheless applied to the orbit **as a single impulse at the burn mid-point** (`apply_delta_v` once, at burn end, timestamped at t_start + t_burn/2). Rationale: the finite-burn gravity loss for a 32 s burn on a 93.6-min orbit is (1 − sinc(Δθ/2)) with Δθ = 2π·32/5615 = 2.05°, i.e. **0.005 %** — utterly negligible, and re-fitting the TLE once per burn instead of once per second keeps the propagator single-source and cheap. Document the mid-point convention in `prop.yaml`.

### Cross-subsystem integration

| Subsystem | Coupling | Where |
|---|---|---|
| **Orbit** | `apply_delta_v` at burn end; `epoch_version` bump | `engine._tick_once` after subsystem ticks, alongside the existing `eps.set_bat_ambient_temp(tcs.get_battery_temp())` coupling block (`engine.py:1140–1143`) |
| **AOCS** | reads `att_error` 0x0217 + `aocs_mode` 0x020F for the arm gate and the cos θ loss; writes disturbance torque + wheel momentum back | `prop_basic.tick(dt, orbit_state, shared_params)` reads/writes the shared param store like every other model |
| **EPS** | new power line `prop` (catbed heater 12 W + valve 2 W). Add to `eps.yaml:51 power_lines`, `simulation.yaml:89 line_power_w`, and the load-shed order list (`simulation.yaml:99`) — **after** `aocs_wheels`, so shedding propulsion is the last resort and shedding it mid-burn triggers the abort |
| **TCS** | make `htr_thruster` real: delete `s.htr_thruster = False` at `tcs_basic.py:496`, remove `"thruster"` from `_NO_HEATER_CIRCUITS` (`:114`), run `_thermostat_control("thruster")`. This retires half of defect #31 and makes S8 func 42 load-bearing |
| **Planner** | new activity type `orbit_manoeuvre`; contact/imaging recompute on `epoch_version` change |
| **MCS** | new `propulsion` display + `propulsion` console position |

### Breakpoint serialisation (the stated constraint)

`breakpoints.py` needs **two** additions, not one:

1. `state["subsystems"]["prop"]` — comes free from the existing loop at `:35–39` (`model.get_state()`), provided `prop_basic` implements `get_state`/`set_state` from `SubsystemModel` (`smo_common/models/subsystem.py:73–81`). Must carry: `state`, `m_prop_kg`, `tank_p_bar`, `catbed_temp_c`, `latch_a/latch_b`, `fcv_open`, `inhibit_latched`, `dv_accum_m_s`, `burn_count`, `total_on_time_s`, `pending_burn{dv, t_start, mode}`, `burn_elapsed_s`, `failure flags`.
2. **`state["orbit_elset"]` — new, and mandatory.** Replace the `orbit_utc`-only block at `breakpoints.py:88–93` with:
   ```
   state["orbit_utc"]   = orbit.utc.isoformat()
   state["orbit_elset"] = orbit.get_elset()        # None when never manoeuvred
   ```
   and on load (`:150–161`), `orbit.set_elset(...)` **before** `orbit.reset(utc)`. The comment at `:84–87` ("SGP4 is stateless given a UTC time, so the propagator's `_sim_utc` is sufficient") must be rewritten — it is now false. Backward compatibility: absent `orbit_elset` ⇒ fall back to the `orbit.yaml` TLE (old snapshots restore correctly as un-manoeuvred).

Regression test extends `tests/test_breakpoint_orbit.py`: burn → save → tick 3 orbits → load → assert `|r_restored − r_saved| < 1 m` **and** `params[0x0108]` agrees with the freshly propagated `in_eclipse` (the existing invariant), **and** the next AOS time matches the post-burn schedule not the pre-burn one.

### Serving the manoeuvred orbit to planner and MCS

`engine` already exposes `/api/state`; add `orbit_elset` + `epoch_version` to `_publish_snapshot` (`engine.py:1186`) and to the `/api/state` payload (`engine.py:2838`). Then:

- **Planner** (`server.py:145–146`): replace the frozen `self._tle1/_tle2` with a `self._elset` refreshed from `/api/state` on the existing sim-anchor poll. It already has the machinery — `self._plan_dirty` / `_last_jump_utc` / `_jump_count` (`server.py:126–130`) exist for breakpoint/scenario time jumps. **A burn is exactly the same class of event**: set `_plan_dirty = True` when `epoch_version` changes, which drops `_contacts_cache` and `_ground_track_cache` immediately. This is the single highest-leverage reuse in the whole work package.
- **MCS** (`server.py:735–801`): the same — the hand-rolled `yaml.safe_load` + `tle_line1`/`TLE_LINE1` fallback (already flagged as DI-13) is replaced by an elset fetched from `/api/state`, with the `orbit.yaml` TLE as cold-start fallback.

---

## Interface spec

### Telecommands — S8 function IDs **85–95**

Free contiguous block confirmed by enumerating `tc_catalog.yaml`: used = 0–84 ∪ 100–107; **free = 85–99 ∪ 108–127**. 85–95 is the natural next subsystem block after AOCS's 84, matching the "one contiguous range per subsystem" convention (AOCS 0–15, EPS 16–25, PLD 26–39, TCS 40–49, OBDH 50–62, TTC 63–79).

Data-byte layout follows `smo_mcs/tc_lengths.py:90` — **byte 0 is the `func_id`**, then the declared fields, big-endian, `float32` = IEEE-754 BE (as `RW_SET_SPEED_BIAS` func 8 already does).

| func_id | Name | Fields | Bytes | Semantics |
|---|---|---|---|---|
| **85** | **`PROP_ARM`** | `arm_code : uint16` | 1+2 = **3** | Two-step interlock: must equal 0xA5C3, must follow a `PROP_SET_BURN` within 300 s. Runs all 8 interlocks; each failure returns a distinct S1(8) code. **This is the book's `PROP_ARM`.** |
| **86** | `PROP_DISARM` | — | **1** | ARMED → SAFED; closes FCV, leaves latch valves |
| **87** | `PROP_SET_BURN` | `dv_m_s : float32`, `direction : uint8` (0=prograde,1=retrograde), `mode : uint8` (0=open-loop timed, 1=closed-loop ΔV cut-off), `t_start_cuc : uint32` (0 = on `PROP_EXECUTE`) | 1+4+1+1+4 = **11** | Loads the manoeuvre. Sim computes `t_burn` from current P/Isp/mass and publishes it as TM before arming — the operator verifies the number the ground tool predicted. |
| **88** | `PROP_EXECUTE` | — | **1** | ARMED → FIRING. Rejected if not ARMED. Normally delivered by **S11 time-tagged schedule** (NOM-004 step 3). |
| **89** | **`PROP_ABORT`** | — | **1** | FIRING → SAFED immediately; closes FCV; latches `abort_reason` |
| **90** | `PROP_ISO_OPEN` | `branch : uint8` (0=A, 1=B, 2=both) | 1+1 = **2** | Latch valve open |
| **91** | **`PROP_ISO_CLOSE`** | `branch : uint8` (0=A, 1=B, **2=both**) | **2** | **This is the book's `PROP_ISO_CLOSE`, "both branches" (CTG-006 step 2).** |
| **92** | `PROP_SET_LIMITS` | `att_err_max_deg : float32`, `p_min_bar : float32`, `catbed_min_c : float32`, `t_burn_max_s : float32` | 1+16 = **17** | Onboard abort thresholds; also the FDIR configuration hook |
| **93** | `PROP_INHIBIT_SET` | `on : uint8` | **2** | Ground-latched inhibit (the deployer's "3 independent inhibits" lesson) |
| **94** | `PROP_INHIBIT_CLR` | `key : uint16` | **3** | Clears both `PROP_INHIBIT_SET` and an FDIR-latched inhibit |
| **95** | `PROP_GET_STATUS` | — | **1** | On-demand full propulsion status → TM(8,?) / triggers the SID-7 HK |
| *(existing)* **42** | `HEATER_THRUSTER` | `on : uint8` | 2 | Becomes load-bearing (catbed preheat) |
| *(new S20)* | **`ORBIT_MANOEUVRE_LOAD`** | S20,3 parameter set: `dv_x/y/z : float32` + `epoch_cuc : uint32` | — | The book's FDS→AOCS manoeuvre-vector upload (`orbit_north_week3.md:912`). Optional; `PROP_SET_BURN` is the primary path. |

Dispatcher change (`service_dispatch.py:568–602`): one new branch
```python
elif func_id in range(85, 96):
    return self._route_prop_cmd(func_id, data[1:])
```
placed after the `func_id == 84` branch. Note this file is the *fourth* hand-maintained copy of the func-ID map (roadmap L-… / DI-09); adding propulsion means coordinated edits to `service_dispatch._route_*`, `engine._check_tc_acceptance`, the bootloader allowlist, `tc_catalog.yaml` and `mcs/positions.yaml`. Add all five in one commit and add a content test.

### Telemetry parameters — block **0x0B00**

Convention (verified across all 330 params): 0x01=EPS, 0x02=AOCS, 0x03=TCS/OBDH, 0x04=TCS/TTC, 0x05=TTC, 0x06=payload, 0x07=FDIR, 0x08=contact, 0x09=procedure, 0x0A=OBDH. **0x0B is free** and is the next unused subsystem block.

| ID | Name | Units | Pack/scale | Notes |
|---|---|---|---|---|
| 0x0B00 | `prop.state` | — | B / 1 | 0=SAFED,1=ARMED,2=FIRING,3=INHIBIT |
| 0x0B01 | `prop.tank_pressure` | bar | H / 100 | 22.00 → 9.33 |
| 0x0B02 | `prop.propellant_mass` | g | H / 10 | 400.0 → 0.0 |
| 0x0B03 | `prop.propellant_pct` | % | H / 100 | derived, for the MCS bar |
| 0x0B04 | `prop.tank_temp` | °C | h / 100 | |
| 0x0B05 | `prop.catbed_temp` | °C | h / 10 | ties to `tcs.temp_thruster` 0x0409 |
| 0x0B06 | `prop.latch_a_state` | — | B / 1 | 0=closed,1=open,2=fault |
| 0x0B07 | `prop.latch_b_state` | — | B / 1 | |
| 0x0B08 | `prop.fcv_state` | — | B / 1 | flow control valve (the firing valve) |
| 0x0B09 | `prop.thrust_est` | mN | H / 10 | live F(P) |
| 0x0B0A | `prop.isp_est` | s | H / 10 | live Isp(P) |
| 0x0B0B | `prop.dv_accum` | m/s | H / 100 | **accumulated ΔV — NOM-005 step 4 "ΔV vs plan"** |
| 0x0B0C | `prop.dv_remaining` | m/s | H / 100 | Tsiolkovsky on live m_prop — **CTG-006 step 3 "remaining usable ΔV envelope"** |
| 0x0B0D | `prop.burn_status` | — | B / 1 | 0=idle,1=armed,2=firing,3=complete,4=aborted |
| 0x0B0E | `prop.burn_dv_cmd` | m/s | H / 100 | commanded |
| 0x0B0F | `prop.burn_dv_achieved` | m/s | H / 100 | last burn |
| 0x0B10 | `prop.burn_time_cmd` | s | H / 10 | predicted duration |
| 0x0B11 | `prop.burn_time_elapsed` | s | H / 10 | |
| 0x0B12 | `prop.burn_count` | — | H / 1 | |
| 0x0B13 | `prop.total_on_time` | s | H / 10 | |
| 0x0B14 | `prop.abort_reason` | — | B / 1 | 0=none,1=att_err,2=low_P,3=overtemp,4=low_SoC,5=eclipse,6=ground,7=timeout,8=valve |
| 0x0B15 | `prop.arm_inhibit_mask` | — | H / 1 | **bitmask of which interlock is blocking arm** — the single most useful debugging parameter for a trainee |
| 0x0B16 | `prop.leak_rate` | mg/s | H / 100 | 0 nominal; non-zero under the leak failure |
| 0x0B17 | `prop.thrust_offset_torque` | µN·m | H / 1 | AOCS coupling, visible |
| 0x0B18 | `prop.sc_mass` | kg | H / 1000 | m_dry + m_prop |
| 0x0B19 | `prop.orbit_epoch_version` | — | H / 1 | increments per applied ΔV — planner/MCS cache key |
| 0x0B1A | `prop.sma_km` | km | H / 10 (offset) | post-burn semi-major axis from the re-fit |

### HK SID placement

The book's CTG-006 step 1 says *"read SID 5 / propulsion"* — but sim **SID 5 = Payload** (`telemetry/hk_structures.yaml`; the SID 5/6 swap is already a known book error, `PART2_SIM_RECONCILIATION.md:1380`). Do **not** overload SID 5.

**Add SID 7 "PROP", `interval_s: 8.0`** (matching AOCS 4 s / Platform 8 s / Payload 8 s cadence), carrying 0x0B00–0x0B15 + 0x0B18–0x0B19 ≈ **24 parameters ≈ 40 bytes** ⇒ ~40 bps. Existing HK total is 1351.5 bps (`simulation.yaml:50`), so this is +3 % — safe at the LOW (16 kbps) tier, and the SID is *disabled by default* per `simulation.yaml:73` (`default_enabled_sids: [1, 11]`), commanded on during commissioning like every other SID. Fix CTG-006 step 1 to say SID 7.

### Events — block **0x0800** (free; 0x07 is OBCP, 0x0F is FDIR/scenario)

| ID | Name | Sev |
|---|---|---|
| 0x0800 | `PROP_ARMED` | MEDIUM |
| 0x0801 | `PROP_DISARMED` | INFO |
| 0x0802 | `PROP_ARM_REJECTED` | MEDIUM |
| 0x0803 | `PROP_BURN_START` | MEDIUM |
| 0x0804 | `PROP_BURN_COMPLETE` | INFO |
| 0x0805 | `PROP_BURN_ABORTED` | HIGH |
| 0x0806 | `PROP_TANK_PRESSURE_LOW` | MEDIUM |
| 0x0807 | `PROP_TANK_PRESSURE_HIGH` | HIGH |
| 0x0808 | `PROP_CATBED_UNDERTEMP` | MEDIUM |
| 0x0809 | `PROP_CATBED_OVERTEMP` | HIGH |
| 0x080A | `PROP_VALVE_FAULT` | HIGH |
| 0x080B | `PROP_LEAK_DETECTED` | HIGH |
| 0x080C | `PROP_UNDERPERFORMANCE` | MEDIUM |
| 0x080D | `PROP_PROPELLANT_LOW` | MEDIUM |
| 0x080E | `PROP_PROPELLANT_DEPLETED` | HIGH |
| 0x080F | `PROP_ORBIT_UPDATED` | INFO (carries Δa, epoch_version) |
| 0x0810 | `PROP_INHIBIT_LATCHED` | HIGH |
| 0x0811 | `PROP_SPEED_CLAMPED` | INFO (instructor-facing) |
| 0x0812 | `PROP_ORBIT_FIT_FAILED` | HIGH (TLE re-fit did not converge) |

### S12 monitors (`monitoring/s12_definitions.yaml`) and MCS limits (`mcs/limits.yaml`)

| param | check | low | high | severity | rationale |
|---|---|---|---|---|---|
| 0x0B01 tank_pressure | 0 abs | 6.0 | 24.0 | WARNING | operating band |
| 0x0B01 tank_pressure | 0 abs | 4.0 | 26.0 | ALARM | CTG-006 "tank pressure out-of-band" |
| 0x0B01 tank_pressure | **1 delta** | −0.20 | +0.20 bar/8 s | ALARM | **leak / burst-disc detection — the delta check is what makes a slow leak visible** |
| 0x0B05 catbed_temp | 0 abs | −20 | 300 | WARNING | |
| 0x0B05 catbed_temp | 0 abs | −30 | 350 | ALARM | over-temperature |
| 0x0B02 propellant_mass | 0 abs | 20.0 | 400.0 | WARNING | low propellant |
| 0x0B09 thrust_est | 0 abs | 100 | 400 mN | WARNING | **"low thrust" trigger named by CTG-006** |
| 0x0B0C dv_remaining | 0 abs | 5.0 | 100.0 | WARNING | usable envelope |
| 0x0217 att_error | 0 abs | −1.0 | 1.0 | ALARM | *existing* limit; the burn-abort rule consumes it |

`mcs/limits.yaml` mirrors with yellow/red pairs, `check_interval_s: 4` (8 for temperatures), same style as the EPS block.

### S19 FDIR rules (`monitoring/s19_rules.yaml`) — `ea_id` 8001+

Follows the file's mandatory `trigger.kind` discriminator (`event` vs `param`) introduced by defect #70.

| ea_id | name | trigger | action_func_id | description |
|---|---|---|---|---|
| 8001 | `prop_burn_abort_on_attitude_error` | `{kind: param, id: 0x0217}` | **89** (`PROP_ABORT`) | **Attitude error out of limits during burn → abort.** The teaching moment the book names ("AOCS goes off-axis during burn → CTG-002") |
| 8002 | `prop_abort_on_low_tank_pressure` | `{kind: param, id: 0x0B01}` | 89 | Tank pressure below the ALARM floor → abort |
| 8003 | `prop_abort_on_catbed_overtemp` | `{kind: event, id: 0x0809}` | 89 | Over-temperature → abort |
| 8004 | `prop_isolate_on_leak` | `{kind: event, id: 0x080B}` | **91** (`PROP_ISO_CLOSE`, both) | Leak → isolate; matches CTG-006 step 2 |
| 8005 | `prop_disarm_on_safe_mode` | `{kind: event, id: 0x0F0A}` (`SAFE_MODE_ENTRY`) | **86** (`PROP_DISARM`) | Safing must disarm propulsion |
| 8006 | `prop_abort_on_soc_critical` | `{kind: event, id: 0x0102}` (`BATTERY_SOC_CRITICAL`) | 89 | Burn during eclipse with insufficient power |
| 8007 | `prop_inhibit_after_second_abort` | `{kind: event, id: 0x0805}` ×2 | **93** (`PROP_INHIBIT_SET`) | Two aborts ⇒ latched inhibit; only ground clears it |

Also add `obcp/04_orbit_manoeuvre.yaml` (S18) — the on-board timed burn, mirroring `02_safe_mode_entry.yaml`:
```yaml
procedure: {id: 4, name: orbit_manoeuvre, description: "Preheat, arm, burn, disarm"}
  steps:
    - {delay_s:    0, service: 8, subtype: 1, func_id: 42, data_hex: "01"}  # catbed heater ON
    - {delay_s: 1200, service: 8, subtype: 1, func_id: 85, data_hex: "A5C3"} # PROP_ARM
    - {delay_s:   10, service: 8, subtype: 1, func_id: 88, data_hex: ""}     # PROP_EXECUTE
    - {delay_s:  120, service: 8, subtype: 1, func_id: 86, data_hex: ""}     # PROP_DISARM
    - {delay_s:    5, service: 8, subtype: 1, func_id: 42, data_hex: "00"}   # heater OFF
```

### Planner

New activity type in `planning/activity_types.yaml`, in the shipped style (real `func_id`s, `pre_conditions`, `conflicts_with`):

```yaml
- name: orbit_manoeuvre
  description: "Orbit-maintenance delta-V burn"
  duration_s: 1500          # 1200 s catbed preheat + 120 s burn + margin
  min_duration_s: 900
  max_duration_s: 2400
  power_w: 14               # 12 W catbed + 2 W valves
  data_volume_mb: 0
  requires_attitude: nominal
  requires_daylight: true   # power + observability
  requires_contact: true    # NOM-005: "typically over a ground station"
  requires_subsystems: [prop, aocs, eps]
  priority: high
  procedure_ref: NOM-004
  command_sequence:
    - {service: 8, subtype: 1, func_id: 0x2A, params: [1], description: "HEATER_THRUSTER_ON"}
    - {wait_s: 1200}
    - {wait_for: {parameter: prop.catbed_temp, value: 180, timeout_s: 600}}
    - {service: 8, subtype: 1, func_id: 0x57, params: [...], description: "PROP_SET_BURN"}
    - {service: 8, subtype: 1, func_id: 0x55, params: [0xA5, 0xC3], description: "PROP_ARM"}
    - {service: 8, subtype: 1, func_id: 0x58, description: "PROP_EXECUTE"}
    - {service: 8, subtype: 1, func_id: 0x56, description: "PROP_DISARM"}
  conflicts_with: [imaging_pass, data_dump, momentum_desaturation, calibration]
  pre_conditions:
    - "aocs.mode == 4"
    - "aocs.att_error < 2.0"
    - "eps.bat_soc > 45"
    - "prop.tank_pressure > 6.0"
    - "prop.dv_remaining > 1.0"
  aocs_constraints: {required_gap_s_after: 300}
```
Plus a **propellant budget track** in `budget_tracker.py` alongside the existing data/power budgets, and `_plan_dirty = True` on `epoch_version` change.

### MCS

- `mcs/displays.yaml`: new `propulsion` display — pages **"Tank & Valves"** (gauges 0x0B01 P, 0x0B03 %, 0x0B04/0x0B05 temps; status indicators 0x0B06/0x0B07/0x0B08), **"Burn"** (0x0B0D status, 0x0B0E/0x0B0F cmd-vs-achieved, 0x0B10/0x0B11 time, 0x0B15 inhibit mask, line chart of 0x0B09 thrust + 0x0217 att_error over 300 s — the abort story on one plot), **"ΔV Budget"** (0x0B0B accumulated, 0x0B0C remaining, 0x0B12 burn count, 0x0B1A SMA trend).
- `mcs/positions.yaml`: new position **`propulsion`** (the book's Propulsion Engineer) — `allowed_services: [1,3,5,8,11,17,20]`, `allowed_func_ids: [42, 85..95]`, `visible_tabs: [overview, propulsion, commanding, procedures, manual]`, `manual_sections: [07_orbit_ops, 13_propulsion]`. Add 85–95 to `flight_director` (which is `allowed_commands: "all"`, so free) and add **42** stays with `eps_tcs`. Give the `aocs` (Flight Dynamics) position **read** access to the propulsion tab — FDS produces the ΔV, Propulsion executes it, exactly as the book's cross-position dependency describes.
- New manual chapter `configs/eosat1/manual/13_propulsion.md` (the other twelve exist).

---

## Work packages

Ordering is a strict chain for WP1→WP3; WP4–WP7 parallelise after WP3.

| # | Title | Files | Acceptance criteria (testable) | Effort | Depends on |
|---|---|---|---|---|---|
| **WP0** | **Hardware sheet + config schema** | `configs/eosat1/subsystems/prop.yaml` (new: `model: prop_basic`, dry/wet mass, F₀, Isp BOL/EOL, tank V/P₀, ρ, thrust axis `[1,0,0]`, offset 5 mm, interlock thresholds, `param_ids` block); `smo_common/config/schemas.py` (`PropConfig`); `loader.py:55` add `"prop": PropConfig` to the `load_all_subsystem_configs` mapping; `simulation.yaml` `prop:` block (max_speed_during_burn, initial propellant, failure onsets) | `load_all_subsystem_configs(configs/eosat1)` returns a `prop` key; `tests/test_common/test_config_validation.py` passes; `tests/test_common/test_config_integrity.py` finds no param-ID or func-ID collision | **S** | — |
| **WP1** | **`OrbitPropagator.apply_delta_v` + TLE differential correction** | `smo-common/src/smo_common/orbit/propagator.py` (+`_fit_elset`, `_satrec_from_elset`, `get_elset`/`set_elset`, `_epoch_version`) | (a) no-burn re-fit residual **< 0.01 m / 0.01 mm/s** and 72 h drift **< 5 m** vs the original TLE (measured: 0.0022 m, 1.49 m); (b) ΔV = 1.0 m/s prograde ⇒ Δa = 1.79 ± 0.02 km and −33.6 ± 0.5 s along-track at +24 h; (c) `contact_windows` AOS for the next Iqaluit pass shifts by the predicted amount; (d) fit converges in ≤ 10 iterations for ΔV ∈ [0.01, 10] m/s both signs; (e) divergent fit leaves the elset unchanged and raises 0x0812 | **M** | WP0 |
| **WP2** | **`models/prop_basic.py`** — state machine, blowdown, depletion, finite-burn sub-stepping, AOCS coupling | new `packages/smo-simulator/src/smo_simulator/models/prop_basic.py`; register in `smo_common/models/registry.py:27` builtins + the `pyproject.toml` entry point | (a) SAFED→ARMED refused with a distinct code for each of the 8 interlocks, and 0x0B15 names the blocker; (b) a 0.85 m/s BOL burn runs **31.9 ± 0.5 s** and consumes **4.9 ± 0.2 g**; (c) tank pressure follows 22.00 → 9.33 bar over the full 400 g, monotonic; (d) `att_error = 30°` during a 1.0 m/s burn yields along-track ΔV = 0.866 ± 0.005 m/s and a non-zero lateral component; (e) at `speed=1000` a burn still executes in ≤ 1 s sub-steps and total ΔV error < 0.5 % (speed clamp event 0x0811 raised); (f) `get_state`/`set_state` round-trip is bit-identical | **M** | WP0 |
| **WP3** | **Engine wiring + breakpoint serialisation** | `engine.py` (tick coupling after subsystem ticks; `orbit_elset`+`epoch_version` in `_publish_snapshot` and `/api/state`; speed clamp; EPS `prop` power line); `breakpoints.py:88–93` and `:150–161`; `eps.yaml`, `simulation.yaml` | (a) burn → `save()` → 3 orbits → `load()` ⇒ `|r_restored − r_saved| < 1 m` **and** `params[0x0108]` agrees with freshly propagated `in_eclipse` (extends `tests/test_breakpoint_orbit.py`); (b) a pre-propulsion snapshot (no `orbit_elset`) still loads; (c) shedding the `prop` power line mid-burn aborts with `abort_reason=4`; (d) `/api/state` exposes `epoch_version` | **M** | WP1, WP2 |
| **WP4** | **Command path — TCs 85–95** | `configs/eosat1/commands/tc_catalog.yaml`; `service_dispatch.py:568–602` + new `_route_prop_cmd`; `engine._check_tc_acceptance`; bootloader allowlist; `mcs/positions.yaml` | (a) `tests/test_acceptance/test_commands.py` drives all 11 new commands through real dispatch with correct byte counts (3,1,11,1,1,2,2,17,2,3,1); (b) `tc_lengths.expected_data_bytes` returns exactly those numbers; (c) every command rejected in the bootloader; (d) no func_id collision test passes; (e) `PROP_EXECUTE` without a prior `PROP_ARM` returns S1(8) with a specific code, not silence | **M** | WP2 |
| **WP5** | **Telemetry, HK SID 7, events, monitors, FDIR** | `telemetry/parameters.yaml` (0x0B00–0x0B1A); `telemetry/hk_structures.yaml` (SID 7); `events/event_catalog.yaml` (0x0800–0x0812); `monitoring/s12_definitions.yaml`; `monitoring/s19_rules.yaml`; `mcs/limits.yaml`; `simulation.yaml` HK defaults | (a) `tests/test_acceptance/test_hk_completeness.py` — every 0x0B parameter is written each tick and appears in SID 7; (b) SID 7 disabled at power-up, enabled by TC(3,5); (c) `tests/test_content/test_s19_rules_content.py` passes with no new KNOWN_MISROUTED entries; (d) driving `att_error` past 1.0° during a burn fires rule 8001 and the burn aborts within 2 ticks; (e) `test_mcs_hk_round_trip` decodes SID 7 to the same values | **M** | WP4 |
| **WP6** | **TCS: make the thruster heater real** | `models/tcs_basic.py:114` (`_NO_HEATER_CIRCUITS`), `:496–497`, `_thermostat_control` call site `:388`; `subsystems/tcs.yaml` heaters block | (a) `HEATER_THRUSTER on=1` raises `tcs.temp_thruster` (0x0409) toward the catbed setpoint and `htr_thruster` (0x040D) reads 1; (b) `HEATER_SET_SETPOINT circuit=2` and `HEATER_AUTO_MODE circuit=2` now succeed **and take effect** (retires the third leg of defect #31); (c) `htr_total_power_w` (0x0411) includes the 12 W and its S12 yellow limit (15 W) is re-based | **S** | WP2 |
| **WP7** | **Planner: manoeuvre activity + propellant budget + burn-aware caches** | `planning/activity_types.yaml`; `smo_planner/server.py:145–146, 565–612, 867`; `budget_tracker.py`; `planner.yaml` | (a) `tests/test_planner/test_activity_funcids.py` accepts the new func IDs; (b) scheduling `orbit_manoeuvre` conflicts with `imaging_pass`/`data_dump`; (c) after a burn, `epoch_version` changes ⇒ `_plan_dirty` set ⇒ `/api/contacts` returns AOS times shifted by the predicted amount within one poll (reuses the defect-#59 jump machinery); (d) propellant budget decrements by the modelled ṁ·t | **M** | WP3 |
| **WP8** | **MCS: display + Propulsion position + orbit source** | `mcs/displays.yaml`; `mcs/positions.yaml`; `smo-mcs/server.py:735–801` (elset from `/api/state`, retire the raw `yaml.safe_load` TLE fallback); `smo_mcs/static/index.html` | (a) the `propulsion` position sees only funcs 42 + 85–95 (`test_position_filtering`); (b) MCS ground-track and orbital state follow the burn (`test_mcs_page_behaviour`); (c) all three propulsion display pages render with live values (`test_mcs_server`) | **M** | WP5, WP7 |
| **WP9** | **Ops procedures + book reconciliation** | `procedures/nominal/orbit_manoeuvre_planning.md` (**NOM-004**), `procedures/nominal/orbit_manoeuvre_execution.md` (**NOM-013**), `procedures/contingency/propulsion_anomaly.md` (**CTG-028**), `procedures/procedure_index.yaml`, `manual/13_propulsion.md`, `obcp/04_orbit_manoeuvre.yaml`, and a `PART3_PROPULSION_RECONCILIATION.md` note for `~/cdf` | (a) `tests/test_content/test_procedures_content.py` and `test_procedure_links_content.py` pass; (b) `tools/procedure_audit.py` reports every **TC:** annotation resolvable to a real catalogue command; (c) `runnable: {kind: activity_type, ref: orbit_manoeuvre}` on NOM-004 executes end-to-end from the MCS Procedures tab; (d) procedure count claim updated 64 → 67 | **M** | WP4, WP5 |
| **WP10** | **Training scenarios** | 5 new files in `configs/eosat1/scenarios/` (below) + failure modes in `prop_basic.inject_failure` | (a) `tests/test_content/test_scenarios_generic.py` and `scenario_engine.load_scenarios_from_dir` load all five with **zero** `SCENARIO_CONFIG_INVALID` (0x0F20) events; (b) each scenario's `expected_responses` maps to a real procedure step; (c) each fires its intended S19 rule | **M** | WP5, WP9 |
| **WP11** | **FDS ground tool: ΔV planner + post-burn OD** | `tools/orbit_tools.py` (new `manoeuvre` sub-command, reusing `OrbitAnalyzer.compute_orbital_elements` at `:66`); `tools/pass_gap_analysis.py` accepts an elset | (a) `orbit_tools manoeuvre --target-alt 450.5` emits ΔV, burn duration at current tank pressure, propellant, and the predicted AOS shift; (b) fed the post-burn state vector it reports achieved-vs-planned ΔV within 1 % (closes NOM-005 step 4) | **S** | WP1 |

### Procedure reconciliation (WP9) — recommended resolution of the ID clash

| Book | Sim today | Recommendation |
|---|---|---|
| NOM-004 Manoeuvre Planning | **reserved gap** | **Take it.** Create sim NOM-004 = *Orbit Maintenance Manoeuvre Planning*. Book unchanged. |
| NOM-005 Manoeuvre Execution | NOM-005 = HK Configuration (shipped, referenced by tests and `activity_types.yaml`) | **Do not renumber the sim.** Create sim **NOM-013 = Orbit Maintenance Manoeuvre Execution**; amend the book card to NOM-013 with an editor's note. Renumbering a shipped procedure breaks `procedure_index.yaml`, `standard_plans.yaml`, four test files and the learner workbook. |
| CTG-006 Propulsion Anomaly | CTG-006 = Payload Anomaly (shipped) | Create sim **CTG-028 = Propulsion Anomaly**; amend the book card. |
| CTG-006 step 1 "SID 5 / propulsion" | SID 5 = Payload | Book fix: **SID 7**. |
| CTG-002 (book) referenced by NOM-005 for off-attitude | sim CTG-002 = AOCS Anomaly Recovery ✔ | No change — the link already works. |
| Propulsion Engineer position (App A) | absent | Added by WP8. |

New procedure content (all four required by the book):
- **NOM-004** — FDS ΔV plan (`tools/orbit_tools.py manoeuvre`), FD authorisation, **S11 time-tagged upload** of `PROP_SET_BURN`+`PROP_ARM`+`PROP_EXECUTE`, propulsion health check (0x0B00/0x0B01/0x0B0C/0x0B15).
- **NOM-013** — arming (catbed preheat T−20 min, verify 0x0B05 ≥ 180 °C; `PROP_ARM` 0xA5C3; verify 0x0B00 = 1 **and** 0x0B15 = 0), execution (`PROP_EXECUTE` or S11 fire; watch 0x0B09 thrust and 0x0217 att_error live), **abort** (`PROP_ABORT`, verify 0x0B0D = 4 and 0x0B14 reason), post-burn OD (0x0B0F achieved vs 0x0B0E commanded; 0x0B1A SMA; predicted-vs-observed AOS shift at the next pass), `PROP_DISARM` + heater off, GO NOM-007 if 0x025B momentum > 0.4 N·m·s.
- **CTG-028** — read SID 7; `PROP_ISO_CLOSE branch=2`; recompute usable ΔV envelope (0x0B0C, bounded for a stuck-open valve); ticket + EMG-001 escalation on rising pressure.
- Amend **NOM-007** (Momentum Management) prerequisites to note post-burn momentum, and **EMG-001**/`fdir/procedures/safe_mode_entry.yaml` to include `PROP_DISARM`.

### Training scenarios (WP10) — `configs/eosat1/scenarios/`

| File | Difficulty | Failure | Teaching point |
|---|---|---|---|
| `prop_valve_stuck_open.yaml` | ADVANCED | `prop.latch_stuck_open` at T+300, magnitude 1.0 | Continuous propellant loss; must recognise from the 0x0B01 **delta** monitor, close the other branch, bound remaining ΔV. Runs CTG-028 |
| `prop_slow_leak.yaml` | INTERMEDIATE | `prop.leak`, `onset: gradual`, `onset_duration_s: 600`, 2 mg/s | Slow signal buried in noise across several passes; trending discipline. Only the delta check catches it |
| `prop_underperformance.yaml` | INTERMEDIATE | `prop.thrust_degraded` 0.55 (catbed poisoning) | Burn completes but achieved ΔV (0x0B0F) is 55 % of plan; post-burn OD reveals it; re-plan a trim burn under NOM-004. Directly implements the book's "burn under-performs" deviation |
| `prop_burn_eclipse_power.yaml` | ADVANCED | initial `eps.bat_soc = 48`, burn scheduled into eclipse entry | Catbed preheat (12 W × 20 min = 4 Wh) plus platform load drives SoC below the arm interlock / trips S19 rule 8006 mid-burn. Teaches that a manoeuvre is a *power* activity. Exercises the −17 Wh/orbit worst case from `power_analysis.md` |
| `prop_attitude_loss_mid_burn.yaml` | ADVANCED | `aocs.st_blind` + `rw_seizure` at burn start + 10 s | Attitude error grows past 1.0° → S19 rule 8001 auto-aborts → partial ΔV with a lateral component → CTG-002 then re-plan. The headline coupling demo |

Follow the shipped schema exactly (`name`, `difficulty` ∈ `simulation.yaml:157`, `duration_s`, `briefing`, `events[].{time_offset_s, action, params}`, `expected_responses[].{category, description}` — as in `scenarios/aocs_wheel_failure.yaml`).

---

## Out of scope

Explicitly excluded to keep "minimal capability" bounded. Each should be written into `subsystems/prop.yaml` as a comment and into `manual/13_propulsion.md` §Limitations.

1. **Multi-thruster / thrust-vector control.** One thruster, one axis (+X body). No 4-thruster attitude-control-by-thruster, no torque authority, no thruster-based desaturation (NOM-007 stays magnetorquer-only, as it already is).
2. **Propulsive disposal / deorbit burns.** Justified by the numbers: natural decay 1.7 yr (mean) / 4.8 yr (solar min) < FCC 5 yr; a 450→300 km burn is 42.6 m/s, a second propulsion system. Disposal is passive + wing-broadside drag augmentation.
3. **Plane changes / direct LTAN or inclination control.** 0.03° = 4.0 m/s; LTAN is controlled through semi-major axis only.
4. **Orbit raising, constellation phasing, rendezvous, formation flight.**
5. **Closed-loop autonomous orbit control.** No on-board orbit-determination-driven guidance. Burns are ground-planned, ground-authorised, ground- or S11/S18-triggered. `mode=1` (closed-loop ΔV cut-off) means *accelerometer-style ΔV integration cut-off within one burn*, nothing more.
6. **Real conjunction screening.** No TLE catalogue, no covariance, no Pc computation. CAM is a scripted scenario input, not a computed one.
7. **Combustion / feed-system physics.** No two-phase flow, no chamber transients, no ignition delay/tail-off shape beyond a linear 0.5 s ramp, no thermal soak-back, no plume impingement or contamination model (the book raises plume impingement for the Propulsion Engineer; it is taught, not simulated).
8. **Non-isothermal blowdown.** Adiabatic pressurant cooling and tank thermal state are not modelled; P·V_ull = const with a fixed tank temperature.
9. **Slosh, CG migration, flexible-body effects.** The propellant CG shift is not fed to AOCS.
10. **Inertia-tensor coupling.** `prop.sc_mass` and the derived inertia are published as telemetry only — the AOCS model (`aocs_basic.py:732`) is kinematic and has no inertia tensor to perturb. Do **not** build rigid-body dynamics as part of this work package.
11. **Propellant loading/servicing, ground safety, pyro valve, burst disc actuation.** Burst-disc over-pressure appears only as an S12 alarm and an event, not as a modelled rupture.
12. **Redundant thruster branch.** Two *latch valve* branches (A/B) exist because CTG-006 requires isolating "both branches"; there is only one thruster behind them.
13. **Higher-fidelity propagation.** No numerical propagator, no NRLMSISE-00, no drag as a live force. Atmospheric decay remains whatever SGP4's B* gives; the drag ΔV budget above is an offline sizing calculation, not a simulated feedback loop. (If the course later wants a *visible* decaying orbit, that is a separate work package: adjust B* in the elset, not a new propagator.)
14. **Retiring the four-way duplicated func-ID map** (roadmap DI-09). WP4 adds a fifth consumer consistently and a content test; it does not fix the underlying duplication.
