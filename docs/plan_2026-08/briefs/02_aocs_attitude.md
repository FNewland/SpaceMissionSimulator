# Workstream 2 — AOCS: attitude determination & control, fine pointing

## Current state

### Files that constitute the AOCS
| Item | Location |
|---|---|
| Model | `/root/smo/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (2002 lines) |
| HW config | `/root/smo/configs/eosat1/subsystems/aocs.yaml` (30 lines — **no inertia, no torque limits, no sensor specs**) |
| Mode codes (canonical) | `/root/smo/configs/eosat1/aocs/aocs_mode_codes.yaml` |
| S8 routing | `/root/smo/packages/smo-simulator/src/smo_simulator/service_dispatch.py:632-722` |
| Breakpoints | `/root/smo/packages/smo-simulator/src/smo_simulator/breakpoints.py:35-39` |
| Standalone ADCS | `/root/smo/ADCS_failure_sim/cubesat_adcs_sim.py` (819 lines, numpy) |
| Prior art | `docs/sim_fidelity/aocs_fidelity.md`, `defects/reviews/aocs.md`, `defects/32-…md` |

### Pointing modes
Nine modes, `aocs_basic.py:171-185`, matching `aocs_mode_codes.yaml`: OFF 0, SAFE_BOOT 1, DETUMBLE 2, COARSE_SUN 3, NOMINAL 4, FINE_POINT 5, SLEW 6, DESAT 7, ECLIPSE 8. There is **no INERTIAL mode**, and NOMINAL is not nadir — see below. Per-mode tick functions: `:668` OFF, `:675` SAFE_BOOT, `:683` DETUMBLE, `:721` COARSE_SUN, `:732` NOMINAL, `:758` FINE_POINT, `:791` SLEW, `:841` DESAT, `:858` ECLIPSE. Auto-transitions `:330-391` — **FINE_POINT is unreachable autonomously and has no exit branch**; only ground command (`handle_command "set_mode"`, `:1564`) enters it.

Three *contradictory* mode tables ship: the canonical 9-mode YAML; `configs/eosat1/manual/02_aocs.md` §3 (`0=NOMINAL,1=DETUMBLE,2=SAFE_POINT,3=DESAT,4=SLEW`); and the textbook's 5-mode table (`/root/cdf/docs/PART2_SIM_RECONCILIATION.md:259-290`, severity CRITICAL, still open). `procedures/contingency/gps_time_sync_recovery.md:30` publishes a *fourth*.

### Attitude state and "dynamics"
`AOCSState.q` is a scalar-last quaternion `[x,y,z,w]` (`:24`). **There is no dynamics.** There is no inertia tensor anywhere in the repo (`grep inertia` over `configs/` returns nothing but wheel rotor inertia `_rw_inertia = 0.005` at `:222`). No Euler equation, no ω̇ = I⁻¹(τ − ω×(Iω+h)). Instead:

- `_tick_nominal:732-756` and `_tick_fine_point:758-789` **kinematically slew the quaternion straight at the target** by `delta = min(kp·err, cap)·dt` degrees and then *overwrite* body rates with `rate_i = gyro_bias_i + gauss(0,σ)` (`:751-752`, `:784-785`). Body rates are therefore decorative: they are never integrated, never coupled to q, never coupled to the wheels.
- `_tick_wheels:948-1002` advances wheel speed by `dist = random.gauss(0, 0.5)` RPM/s plus friction (`:969-979`). **Wheel speed is a random walk with no torque input.** The `scenario_2_cerise_adcs_fault.yaml:12-15` header states this explicitly.
- `total_momentum` (`:999-1002`) is `Σ I_w·|ω_i|` — a **scalar sum of magnitudes**, not a vector. Four wheels at the 1200 RPM default give 2.51 N·m·s of "momentum" with zero net momentum.
- Only `_tick_slew:791-839` moves attitude coherently (SLERP), and even there rates are back-computed from the error axis rather than integrated.

`_quat_error_axis:1929-1934` returns the normalised *component-wise difference* `q_tgt[:3] − q[:3]`, not the vector part of q_err = q⁻¹⊗q_tgt. It is frame-ambiguous and only accidentally correct for small errors near identity.

### Actuators
4 reaction wheels, no geometry (no distribution matrix, no cant angle, no allocation). `max_speed_rpm: 5500`, `nominal 1200`, `desat 200` (`aocs.yaml`), rotor inertia 0.005 kg·m² ⇒ **2.88 N·m·s per wheel, 11.52 N·m·s scalar total**. No torque limit, no motor lag, no friction torque fed back to the body. Bearing degradation and thermal model exist (`:962-994`) and are the only physically-shaped wheel behaviour.
3 magnetorquers: `_tick_magnetorquers:615-623` is **an empty function** — it zeroes duty when disabled and otherwise does nothing. MTQ dipole is never converted to torque anywhere. In DESAT (`:841-856`) wheel speeds are relaxed toward ±200 RPM by a first-order filter and the MTQ duties are set to `gauss(0,0.3)` random noise — pure theatre.

### Sensors — none of them see the attitude
This is the deepest defect. **Every sensor is computed from orbit phase, not from `q`:**
- `_tick_css:471-524` builds `sun_body` from `solar_beta_deg` and `self._orbit_phase` (a synthetic 360°/5700 s ramp, `:1108-1110`) — **independent of spacecraft attitude**. Rotating the spacecraft does not change the sun sensor.
- `_tick_magnetometer:555-613` + `_compute_igrf_field:526-553`: a hand-rolled lat/lon/phase function, again with no body rotation. Noise σ=50/75 nT and per-unit bias fields exist but bias defaults to (0,0,0).
- `_tick_star_trackers:395-447`: status FSM (OFF/BOOT/TRACK/BLIND/FAILED), 60 s boot, 15° sun-exclusion cone using the fake sun vector. **The star tracker produces no attitude measurement at all** — no quaternion, no noise, no misalignment. It is a boolean `st_valid`.
- Gyros: `_tick_gyro_and_gps:869-944` models only a *bias telemetry* random walk clamped to ±0.1 °/s. There is **no gyro measurement**: no ARW, no scale-factor error, no misalignment, no quantisation, and nothing integrates a gyro.
- GPS: fix/PDOP/sat-count TTFF model (fine), position taken verbatim from truth.

### Estimator
**There is none.** `s.q` is simultaneously truth, estimate and controller state. Consequently there is no attitude *knowledge* error anywhere in the simulator. `attitude_source` (0x0283, `:1298-1304`) is a label, not a data path.

### Control law
`_tick_nominal`: P-only on angle, gain `_kp = 0.02` (`:217`), rate cap 0.5 °/s, plus per-axis multiplicative gains `torque_gain_x/y/z` (`:627-650`) that are actually applied to a *rotation increment*, not a torque. `_tick_fine_point` is the same law with `kp×1.5` and cap 0.3. No derivative term, no feed-forward, no wheel allocation, no anti-windup, no momentum management coupling.

### Verdict on fidelity
**The AOCS is a telemetry generator, not a model.** It is roughly the fidelity of a scripted animation: a first-order relaxation of a scalar `att_error` toward a per-mode target value, plus a decorative quaternion, plus a genuinely good sensor *health/redundancy* state machine (ST boot, A/B mag, per-head CSS, device power gates). Nothing in the loop is physical: no inertia, no torque, no momentum vector, no attitude determination. It is adequate for teaching *sensor redundancy management and mode-transition procedure discipline* and inadequate for anything that says "attitude control".

I confirmed the fine-point behaviour empirically (script in `/tmp/fp_test.py`, model pinned in mode, 600 ticks from a 5.7° initial error):

```
NOMINAL   : converged att_error ≈ 0.018 deg (mean of last 100 ticks)
FINE_POINT: converged att_error ≈ 0.007 deg
```

Both are ~an order of magnitude below every threshold anyone can see.

---

## Root causes

**1. [CRITICAL] `_target_q` is never assigned. NOMINAL "nadir" pointing is inertial identity-hold.**
`aocs_basic.py:216` sets `self._target_q = [0,0,0,1]` in `__init__` and no code path anywhere in the repo ever writes it (`grep -rn "_target_q"` → only reads at `:734,735,754,770,771,787`, plus one test that pokes it directly at `tests/test_adcs_torque_gain.py:99`). So NOMINAL and FINE_POINT both drive the body to the ECI identity attitude and hold it. There is no LVLH frame, no nadir vector, no orbit-rate feed-forward — despite `OrbitState` already carrying `pos_eci`, `vel_eci` and `sun_eci` (`packages/smo-common/src/smo_common/orbit/propagator.py:24-34`) which the AOCS model never reads. Chain dies at `aocs_basic.py:216`.

**2. [CRITICAL] FINE_POINT differs from NOMINAL only by a 1.5× gain and a smaller noise σ, and both differences are invisible.**
`_tick_fine_point:773` uses `kp*1.5`, `:782` injects quaternion noise σ=5e-6 instead of 2e-5, `:788` adds σ=5e-4 deg instead of 2e-3. Measured steady-state: 0.018° vs 0.007°. Every consumer of that number is blind to the difference:
- Payload image quality (`payload_basic.py:443-447`): `att_quality = 1.0` for any `att_error ≤ 0.1°`. Both modes score a perfect 1.0.
- S12 onboard limit (`monitoring/s12_definitions.yaml:130-136`): trip at 1.0°.
- MCS ground limit (`mcs/limits.yaml:21`): yellow ±1.0°, red ±2.0°.
- MCS gauge (`mcs/displays.yaml:264-267`): range `[0, 10]` deg. 0.018 and 0.007 render as the same needle at zero.
- EPS: the `aocs_wheels` line is billed a **flat 6 W** in every mode (`simulation.yaml` `eps.line_power_w.aocs_wheels: 6.0`, applied at `eps_basic.py:481-483`; the module constant is 12 W at `:39`). The four per-wheel currents (0x0250-0x0253) are never billed to EPS at all. So FINE_POINT has no power signature — while the planner already *assumes* one (`smo-planner/src/smo_planner/constraint_checkers.py:43`: `"imaging_pass": 75.0 # 60W payload + 15W AOCS fine point`).
- Nothing gates on `mode == 5`: `payload_basic.py:728-736` (`capture`) checks only payload mode and `fpa_ready`.

So: the mode **is** accepted and stored and **is** read by the tick — the break is that the two branches are physically identical and no downstream consumer resolves the difference. **The chain dies at `aocs_basic.py:758-789` (no distinct physics) and again at `payload_basic.py:443` (no mode gate).**

**3. [CRITICAL] The attitude integration is numerically unstable above ~20× time acceleration, and the sim advertises 1000×.**
`engine.py:1041`: `dt_sim = dt_real * self.speed`, with `dt_real = 1/tick_hz = 1.0 s` (`mission.yaml:50 sim_tick_hz: 1.0`) and `speed.max: 1000.0` (`simulation.yaml`). The whole `dt_sim` is handed to `model.tick()` as a single Euler step. `_tick_detumble:686,706-708` computes `damp = 0.1*dt` and multiplies rates by `(1 − damp·(0.5+0.5|u|))`; for `dt > 20 s` the factor goes negative and diverges. Measured (`/tmp/accel_test.py`, 20 ticks from 0.3 °/s):

```
DETUMBLE dt=  1 s  -> 0.099 deg/s     (ok)
DETUMBLE dt= 10 s  -> 0.003 deg/s     (ok)
DETUMBLE dt= 60 s  -> 8.7e+12 deg/s   (diverged)
DETUMBLE dt=1000 s -> 1.4e+39 deg/s   (diverged)
NOMINAL  dt=1000 s -> att_error 31 deg, q flips sign (no convergence)
```
Also `_tick_off:670-672` and `_tick_safe_boot:677-679` scale a random walk by `dt` instead of `√dt`, and `_tick_detumble:715-716` adds `gauss(0,0.005)` with no `dt` scaling at all. Any redesign must fix this or the "time-accelerated" requirement is unmet today.

**4. [HIGH] Sensors are attitude-blind, so attitude determination cannot exist.**
`_tick_css:492-498` and `_compute_igrf_field:544-551` derive body-frame vectors from `_orbit_phase` alone; `_get_sun_body_vector:456-469` reads back the CSS output. No `q` appears in any sensor function. Therefore no observation of attitude is possible, no estimator can be written against the present sensor layer, and TRIAD/QUEST/MEKF have nothing to consume. This is why there is no knowledge error and why `attitude_source` (0x0283) is cosmetic.

**5. [HIGH] Half the AOCS state is outside the breakpoint.**
`get_state()` (`:1899-1905`) serialises only the `AOCSState` dataclass plus `_gyro_bias`, `_bearing_degradation`, `_gain_corruption_saved`. Verified (`/tmp/st_test.py`) that these are **lost on save/restore**: `_target_q`, `_slew_target_q`, `_slew_start_q`, `_slew_start_time`, `_slew_rate_dps`, `_orbit_phase`, `_kp`, `_att_error_threshold_deg` (which `AOCS_SET_DEADBAND` writes, `:1717`), `_rw_bearing_health`, `_emergency_rate_threshold`, and all nine `_prev_*` edge-detector flags. Concrete consequence: **save a breakpoint mid-slew, restore it, and `_slew_target_q is None` ⇒ `_tick_slew:793-795` immediately aborts to NOMINAL.** Also `_orbit_phase` loss teleports the magnetic field and sun geometry on restore, and the `_prev_*` loss re-fires every edge-triggered S5 event once after a load.

**6. [HIGH] No RNG seeding — the simulator is not reproducible.**
`aocs_basic.py` uses the *global* `random` module (52 call sites). `grep -rn "random.seed\|Random(" packages/` finds seeding only in `smo-rfsim`. Two runs from the same breakpoint diverge. The RNG state is not in `get_state()`. This directly contradicts the determinism half of the stated constraint.

**7. [HIGH] `aocs.wheels_enabled` (0x0260) is declared but never written — FDIR latches the spacecraft SAFE at t=0.**
`configs/eosat1/telemetry/parameters.yaml:379` declares it; `grep -rn "0x0260" packages/` returns nothing. `configs/eosat1/subsystems/fdir.yaml:80-85` has `aocs.wheels_enabled < 2 → safe_mode_aocs` (level 2). `engine.py:1206` reads `self.params.get(param_id, 0.0)` ⇒ `0.0 < 2` is true on tick 1 and forever. FDIR is `enabled: true` by default. Effects: (a) `safe_mode_aocs` fires once, calling `set_mode(2)` = DETUMBLE (`engine.py:648`) — note it commands DETUMBLE, not a sun-safe mode; (b) the level-2 flag never clears, so `_fdir_safe_active` (`engine.py:1247-1255`) is permanently True and `sc_mode` is permanently SAFE.

**8. [HIGH] Every S19 AOCS autonomy rule dispatches its S8 function with *no argument bytes*.**
`service_dispatch.py:1786-1797`: `self._handle_s8(1, bytes([func_id]))`. So in `_route_aocs_cmd:632`:
- rule 2004 `aocs_attitude_error_safe_mode` → func 0 with empty data → `mode = data[0] if data else 0` (`:638`) ⇒ **commands AOCS OFF**, not safe mode.
- rules 2005–2008 (RW1–RW4 overspeed) → func 2 with empty data → `wheel = 0` always ⇒ **any wheel's overspeed disables wheel 0**.
- rules 2002/2003 (ST blind) → func 6 with empty data → `unit = 1` ⇒ ST1-blind selects ST1 (no-op).
Rule 2004's trigger is also wrong: it fires on event `0x0202 RW_BEARING_DEGRADED`, not on attitude error, and the YAML says so in a comment (`monitoring/s19_rules.yaml:90`).

**9. [HIGH] Momentum telemetry is unusable at both ends and the auto-desat rule is dead.**
`total_momentum` (0x025B) max is 11.52 N·m·s; nominal (4 wheels @ 1200 RPM) is 2.51 N·m·s.
- S12 (`s12_definitions.yaml:121-127`) sets the limit at ±90 — units confused with percent — so it **can never trip**, so S19 rule 2001 `aocs_momentum_saturation_desaturate` (`s19_rules.yaml:60-67`) is dead autonomy.
- MCS ground limit (`mcs/limits.yaml:27`) is yellow 0.5, red 0.8 N·m·s ⇒ the operator's momentum indicator is **permanently red in nominal ops**.

**10. [MEDIUM] Parameter-ID collision in the bearing-health / slew-state block.**
Model publishes bearing health at 0x028E-0x0291 (`:1306-1308`) and `slew_state` at 0x0285 (`:1322`). `parameters.yaml:169-172` names 0x0284-0x0287 `aocs.rwN_bearing_health`, and HK SID 2 downlinks 0x0284-0x0287 (`hk_structures.yaml`). Net: **`aocs.rw2_bearing_health` on the ground display is actually the slew state**; the other three read whatever stale value is in `params`; 0x028E-0x0291 are undeclared and undownlinked; `aocs.slew_state` has no `parameters.yaml` entry.

**11. [MEDIUM] `AOCS_SET_DEADBAND` (func 15) is misnamed and mis-wired.**
`handle_command:1713-1718` writes `_att_error_threshold_deg`, which is used *only* as the S5 `ATTITUDE_ERROR_HIGH` event threshold (`:1485`). It is not a control deadband, does not affect the control law, and is not saved to breakpoints.

**12. [MEDIUM] `MOMENTUM_DUMP` / `DESAT` never dumps momentum via magnetorquers; `_tick_magnetorquers` is a stub.**
`:615-623`. All magnetorquer physics is absent. `procedures/nominal/momentum_management.md` and the S19 desat rule therefore exercise nothing physical.

**13. [MEDIUM] FDIR recovery procedure step 2 is a silent no-op.**
`configs/eosat1/fdir/procedures/aocs_safe_mode_recovery.yaml:19-26` sends func 8 (RW_SET_SPEED_BIAS) with `data: [0xFF, 0x00, 0x00]` (3 bytes); `service_dispatch.py:666` requires `len(data) >= 5`, and wheel 255 is out of range in any case.

**14. [MEDIUM] MODE_ECLIPSE is only reachable from NOMINAL; FINE_POINT never yields to eclipse.**
`_check_auto_transitions:363-379`. Because the ST blinding check is skipped in eclipse (`:427`), star trackers report TRACKING throughout eclipse, so a spacecraft left in FINE_POINT sails through eclipse claiming ST-grade knowledge with no gyro-only degradation.

**15. [LOW] `inject_failure("st_blind")` is erased on the next tick** — `_tick_star_trackers:436-442` recomputes status from geometry with no persistent blind flag (`defects/32-…md:19`).

---

## Proposed model

Design target: **fixed-step, deterministic, sub-stepped, fully serialisable.** Everything below is chosen against that.

### 0. Spacecraft definition (new `configs/eosat1/subsystems/aocs.yaml` block)
EOSAT-1 is a ~480 kg SSO EO satellite (450 km, i=98°, 9 m GSD from `payload_basic.py:456-464`: 20 µrad IFOV × 5000 px ⇒ 45 km swath). Numbers consistent with the *existing* wheel config (2.88 N·m·s/wheel) and with that class (DMC3/TripleSat, KOMPSAT-3 lite, RapidEye-successor):

```yaml
mass_properties:
  mass_kg: 480.0
  inertia_kgm2: [[120.0, 2.0, -3.0], [2.0, 115.0, 1.0], [-3.0, 1.0, 55.0]]  # +Z = payload boresight (nadir)
reaction_wheels:
  count: 4
  geometry: pyramid            # azimuth 45/135/225/315 deg, elevation 30 deg above XY
  rotor_inertia_kgm2: 0.005    # unchanged -> h_max 2.88 Nms per wheel
  max_speed_rpm: 5500          # unchanged
  max_torque_nm: 0.020
  motor_tau_s: 0.10
  coulomb_friction_nm: 1.0e-4
  viscous_friction_nm_per_rads: 5.0e-7
  # axis authority: X/Y 0.049 Nm & 7.1 Nms ; Z 0.040 Nm & 5.8 Nms
magnetorquers:
  count: 3
  max_dipole_am2: 60.0         # ~1.8e-3 Nm at 30 uT  -> 2 Nms dumped in ~20 min
```
Note: manual §6.1 claims a 5–15 min desat. With any realistic rod (≤60 A·m²) that is **20–40 min / 1–2 orbits**; the manual needs correcting, not the model.

**Nadir-tracking rate the wheels must supply.** From the shipped TLE (`orbit.yaml`, n = 15.24 rev/day ⇒ T = 5669 s): **ω_o = 1.108×10⁻³ rad/s = 0.0635 °/s about −Y_LVLH (pitch)**. The *body* carries H = I_yy·ω_o = 0.127 N·m·s; the wheels do not supply that, they supply the *gyroscopic coupling* torque ω_o × (Iω + h_w) ≈ 3×10⁻⁴ N·m plus the cyclic gravity-gradient. The operationally visible signature: **in NADIR the wheel momentum shows a once-per-orbit ripple about a slowly drifting mean; in INERTIAL the LVLH-fixed disturbances rotate through the body at ω_o and the wheels accumulate a *secular* momentum**, so INERTIAL sessions need desats 2–3× more often. That is the pedagogical payoff of having both modes.

### 1. Sensor models (all functions of the *true* quaternion)
Every sensor becomes `measure(C_true(q_true), environment)`. Values below are for this class; sources cited.

| Sensor | Model | Values | Source |
|---|---|---|---|
| Star tracker ×2 (Sodern Hydra / Ball CT-2020 class) | quaternion measurement + NEA + slowly-varying thermoelastic bias + boresight misalignment; exclusion cones | NEA 1σ: **6″ cross-boresight, 40″ about-boresight**; LF bias 8″ (3σ), 1/e time 1 orbit; mounting misalignment 15″ fixed; update **4 Hz** (FINE) / 1 Hz (NOMINAL); Sun 30°, Earth-limb 25°, Moon 15° exclusion; boot 60 s | vendor NEA classes; manual §2.1.3 quotes **0.05° 3σ** — treat that as the *knowledge budget*, and the modelled ST achieves 0.015° 3σ, giving margin. Manual's 15° exclusion is optimistic for Earth limb — flag, don't silently change |
| Gyro ×3 (Astrix-120 / KVH DSP-1760 FOG; manual §2.1 says fibre-optic) | ω_meas = (I+M)(1+SF)ω + b + n, b random-walk | **ARW 0.002 °/√h**, bias instability 0.005 °/h, **RRW 5×10⁻⁴ °/h/√h**, SF 150 ppm, misalignment 30″, quantisation 1×10⁻⁴ °/s, 8 Hz | FOG class; replaces the current MEMS-comment `0.00005·√dt` (`:873`) |
| CSS ×6 heads | cosine law on the *true* body sun vector, per-head bias + albedo error, 60° half-FOV, dark threshold | per-head 1σ **1.5°**, albedo error up to 6° over the sunlit limb, composite sun-vector accuracy ~2° 1σ | typical photodiode CSS; keeps the existing 6-head structure (`:198-202`) |
| Magnetometer A/B | B_body = C(q)·B_IGRF, + bias + scale + misalignment + noise | noise **50 nT 1σ** (A) / 75 nT (B) *(keep existing `:60-61`)*, bias 200/350 nT, SF 0.3 %, misalignment 0.3°, 4 Hz | manual §2.1.1 (<50 nT, 10 nT res, ±60 µT) |
| GPS | pos/vel from truth + noise | 8 m 1σ pos, 0.05 m/s vel, 1 Hz; keep the existing TTFF model (`:885-939`) unchanged | — |

Environment: replace `_compute_igrf_field:526-553` and `_get_sun_body_vector:456-469` with the tilted-dipole IGRF and analytic sun vector already written and validated in `ADCS_failure_sim/cubesat_adcs_sim.py:304-338` (`sun_vector_eci`, `igrf_dipole_eci`), fed by `orbit_state.pos_eci` / `sun_eci`. Delete `_orbit_phase` entirely.

### 2. Estimator — **MEKF (6-state), recommended**

| Option | Real-time | Time-accel | Breakpointable | Gives knowledge error? | Verdict |
|---|---|---|---|---|---|
| TRIAD/QUEST + rate integration | cheapest | fine | trivial (q + b) | **no covariance**; needs *two* simultaneous vectors, so it cannot use a single star-tracker quaternion or run in eclipse | Reject as primary — keep TRIAD as the **COARSE_SUN/acquisition** initialiser only |
| Complementary filter | cheap | fine | trivial | no covariance; hand-tuned gains that must be retuned per mode; cannot express "knowledge degrades in eclipse" | Reject |
| **MEKF, 6-state (3 attitude error + 3 gyro bias)** | ~30 µs/update in numpy; runs at sensor rate (4 Hz), not per substep | fine — propagation is a fixed-step linear recursion | **28 doubles** (q_ref 4 + b 3 + P upper-triangle 21), plain floats, JSON-safe | **yes** — 3σ knowledge error straight from √(trace P_att), which is exactly the telemetry the user asked for | **Recommend** |

Specifics: multiplicative error state δθ (rotation-vector), F = [[−[ω×], −I],[0, −I/τ_b]]; discrete Φ = I + F·h + F²h²/2 with h = 0.5 s; process noise Q from ARW/RRW (Farrenkopf); **Joseph-form covariance update** (numerically PSD-stable under float32 telemetry scaling); error-state reset after each update; 3σ innovation gate on the ST quaternion (reuse the gating idea at `cubesat_adcs_sim.py:556-566`). No iteration, no matrix inversion beyond 3×3 — fully deterministic, fixed-cost, fixed-step.

Mode wiring: FINE = ST @4 Hz + gyro; NOMINAL = ST @1 Hz + gyro; COARSE_SUN = CSS+mag TRIAD @1 Hz + gyro; ECLIPSE/ST-blind = **propagate only**, and the covariance grows as ARW·√t + RRW·t — which makes the manual's "gyro-only for up to 60 s" (§7 note 1) a *derived* number instead of an assertion.

### 3. Control law — **quaternion-feedback PD with feed-forward, recommended over LQR**
`τ_cmd = −K_p·sgn(q_e4)·q_e,vec − K_d·(ω̂ − ω_ref) + ω_ref × (Iω̂ + h_w) + İω_ref` (Wie/Weiss form; globally asymptotically stable, no gain scheduling, no Riccati solve, and the gains map one-to-one onto (ω_n, ζ) so an instructor can *explain* what FINE changes). LQR is rejected: it needs an offline Riccati solution per inertia/mode, its gains are opaque to students, and it buys nothing here because the plant is well-conditioned and unconstrained apart from the wheel torque saturation, which the PD handles with a simple clamp + integrator-free structure.

Gains from `K_p = 2·I·ω_n²`, `K_d = 2·ζ·I·ω_n`, ζ = 0.7:

| Mode | ω_n | K_p (X/Y) | K_d (X/Y) | 2 % settle 4/(ζω_n) | steady error under 1×10⁻⁴ N·m |
|---|---|---|---|---|---|
| COARSE_SUN (MTQ only) | 0.005 | — B-dot / sun-seeking magnetic law | — | ~300 s | ~4° |
| NOMINAL | 0.020 | 0.096 | 3.36 | 285 s | 0.060° |
| FINE_POINT | 0.050 | 0.600 | 8.40 | 114 s | 0.0095° |

Wheel allocation: pseudo-inverse of the 3×4 pyramid distribution matrix A, `ω̇_w = A⁺τ_cmd + null(A)·h_bias`, with the null-space term used to hold a small bias momentum (keeps wheels off zero-crossing stiction — a real and teachable behaviour). Torque clamp at 0.020 N·m per wheel *before* allocation feedback; on saturation, scale the whole vector to preserve torque direction. Wheel motor as a **first-order lag with τ_w = 0.1 s integrated analytically** (exact exponential ⇒ A-stable at any step; avoids the only stiff mode in the plant).

Momentum management: cross-product law `m = k·(h_w × B)/|B|²` with k tuned to dump ~2 N·m·s per 20–40 min (validated form at `cubesat_adcs_sim.py:591-594`), `|m| ≤ 60 A·m²` per axis, MTQ torque `τ = m × B_body` fed back into the body. Entry at ≥70 % of the momentum envelope, exit at ≤20 %, both commandable.

### 4. Disturbance torques — model all four
| Torque | Magnitude at 450 km | Model | Worth it? |
|---|---|---|---|
| Gravity gradient | 3μ/R³·(n̂_b × I n̂_b) ⇒ **~2×10⁻⁵ N·m** at 5° off-nadir, 1.1×10⁻⁴ at 45° | exact, free (`cubesat_adcs_sim.py:618-621`) | **Yes** — it is the reason NADIR is cheaper than INERTIAL |
| Aerodynamic | ½ρv²C_dA·d, ρ≈2×10⁻¹² kg/m³, A=5 m², C_d=2.2, d=0.05 m ⇒ **~3×10⁻⁵ N·m** | cannonball + fixed cp–cg offset, ρ from a simple exponential/F10.7 scale | **Yes** — dominant *secular* term at 450 km; drives desat cadence |
| Residual magnetic dipole | m_res × B, m_res = 0.5 A·m², B = 3×10⁻⁵ T ⇒ **1.5×10⁻⁵ N·m** | 3-vector constant + failure-injectable | **Yes** — cheap, and it is the classic desat-fights-itself teaching case |
| Solar radiation pressure | P·C_r·A·d = 4.5×10⁻⁶ × 1.5 × 8 × 0.1 ⇒ **5×10⁻⁶ N·m**, zero in eclipse | flat-plate, sun-vector dependent | **Yes, but lowest priority** — its value is that it switches off at eclipse entry, which is directly visible as a wheel-momentum slope change at the terminator |

Together these give ~5×10⁻⁵ N·m secular ⇒ **~0.28 N·m·s per orbit**, i.e. a desat every ~10–20 orbits in NADIR and every ~4–6 in INERTIAL. Operationally correct cadence.

### 5. NADIR vs INERTIAL
- **NADIR (mode 4 NOMINAL, and mode 5 FINE_POINT when `pointing_frame = LVLH`)**: `q_ref = q_LVLH(r_eci, v_eci)` recomputed every substep (`lvlh_dcm` at `cubesat_adcs_sim.py:385-393`), `ω_ref = C(q̂)·(r×v)/|r|² = 0.0635 °/s about −Y_b`. Feed-forward carries the orbit rate so the PD sees only the residual — without it the tracking lag would be ω_o/ω_n ≈ 0.055 rad = 3.2°, which is the classic student demonstration.
- **INERTIAL (new mode 9)**: `q_ref` = commanded ECI quaternion, `ω_ref = 0`. Used for calibration star fields, off-nadir stereo, and the DESAT hold.
- Mode 9 is **appended**, not renumbered, so `aocs_mode_codes.yaml`, all procedures, and the (already-inconsistent) textbook tables are untouched.

### 6. Integration scheme, step size, and the acceleration argument
- **Truth dynamics:** RK4 on the 7-state (q, ω) with **fixed h = 0.5 s**, wheel speeds by exact first-order exponential, quaternion renormalised each step.
- **Sub-stepping:** `N = ceil(dt_sim / 0.5)`, capped at `max_substeps = 120`. So full-fidelity attitude physics up to **dt_sim = 60 s, i.e. 60× acceleration**.
- **Stability at h = 0.5 s:** the fastest retained closed-loop mode is ω_n = 0.05 rad/s ⇒ ω_n·h = 0.025 against the RK4 imaginary-axis limit of 2√2 = 2.83 — **113× margin**. The stiff wheel-motor pole (τ_w = 0.1 s, i.e. 10 rad/s) is removed from the RK4 by integrating it analytically. Rigid-body nutation for this inertia and momentum is ≪ 1 rad/s. There is no stiffness left in the fixed step.
- **Above 60×:** enter **SECULAR MODE**. The fast attitude loop is bypassed; `q_true` is set to `q_ref(t)` plus a deterministic error drawn from the mode's *closed-loop* error distribution (whose σ comes from the same gain/noise parameters, so it is consistent, not made up); the **slow** states are integrated exactly over the full `dt_sim`: wheel momentum from ∫τ_disturbance dt, gyro bias random walk (with √dt scaling), MEKF covariance to its steady state, wheel and gyro temperatures. An S5 event `AOCS_FAST_LOOP_BYPASSED` is emitted at entry/exit so the bypass is *honest and visible*, and a telemetry flag `aocs.fidelity_mode` (0=full, 1=secular) is downlinked. This is what makes "run 12 h of orbit in 45 s to reach the next pass" both fast and physically defensible — a 1000× run is a fast-forward, not an attitude demonstration.
- **Cost check at the boundary:** 120 substeps × (RK4 with 4 derivative evals of a 7-state + 4 wheel states) ≈ 120 × 60 µs ≈ **7 ms per 1 s wall tick** — under 1 % of the budget. At 1000× in secular mode the cost is one arithmetic block per tick.
- **Determinism:** replace the global `random` module with a per-model `random.Random(seed)` (seed from a new `simulation.yaml` `seed:` key, defaulting to a fixed value), and put `rng.getstate()` in `get_state()`. Two runs from the same breakpoint then produce bit-identical telemetry.
- **Breakpoint:** all new state moves into the `AOCSState` dataclass (which `get_state()` already serialises wholesale via `dataclasses.asdict`) — `q_true`, `omega_true[3]`, `q_est[4]`, `gyro_bias_est[3]`, `P_upper[21]`, `h_wheel[4]`, `wheel_torque_state[4]`, `q_ref[4]`, `omega_ref[3]`, `pointing_frame`, `pointing_target`, all `_prev_*` edge flags, RNG state, and the substep phase accumulator. Total added: **~60 floats.** Everything else that today lives on the model object (WP-2 below) moves in too.

---

## Telemetry & commands to add

### New parameters — block 0x02A0–0x02C7 (verified free; declared block ends at 0x028D)
| ID | Name | Units | Notes |
|---|---|---|---|
| 0x02A0-A3 | `aocs.q_est_1..4` | — | **estimated** quaternion (what the FSW believes) |
| 0x02A4-A6 | `aocs.rate_est_x/y/z` | deg/s | bias-corrected gyro-derived rate estimate |
| 0x02A7 | `aocs.knowledge_err_3sig` | **arcsec** | 3σ from √(trace P_att) — *the* new number |
| 0x02A8 | `aocs.control_err` | deg | ∠(q̂, q_ref) — control/pointing error |
| 0x02A9 | `aocs.control_err_arcsec` | arcsec | same, fine-resolution channel for FINE ops |
| 0x02AA | `aocs.total_pointing_err_3sig` | deg | RSS of knowledge + control |
| 0x02AB-AD | `aocs.q_ref_x/y/z` (+0x02AE `_w`) | — | commanded reference attitude |
| 0x02AF | `aocs.pointing_frame` | enum | 0=INERTIAL, 1=LVLH_NADIR, 2=TARGET_TRACK, 3=SUN |
| 0x02B0-B2 | `aocs.nadir_body_x/y/z` | — | nadir unit vector in body frame |
| 0x02B3-B5 | `aocs.sun_body_true_x/y/z` | — | true sun vector in body (distinct from the CSS estimate at 0x0245-47) |
| 0x02B6-B8 | `aocs.h_body_x/y/z` | N·m·s | **vector** system momentum (replaces the scalar-sum lie at 0x025B) |
| 0x02B9 | `aocs.h_body_mag` | N·m·s | |
| 0x02BA | `aocs.h_env_frac` | % | fraction of the momentum envelope, per-axis-aware |
| 0x02BB-BD | `aocs.mtq_dipole_x/y/z` | A·m² | actual commanded dipole (0x0258-5A stay as duty %) |
| 0x02BE-C0 | `aocs.tau_dist_x/y/z` | µN·m | total disturbance torque (instructor-grade insight) |
| 0x02C1 | `aocs.est_source` | bitmask | bit0 ST, bit1 CSS, bit2 MAG, bit3 GYRO-only |
| 0x02C2 | `aocs.est_innov_rej_count` | — | rejected ST fixes since boot |
| 0x02C3 | `aocs.rate_stability_3sig` | deg/s | jitter metric over the last 60 s |
| 0x02C4 | `aocs.settle_time_s` | s | time since last mode/target change until inside the gate |
| 0x02C5 | `aocs.fine_ready` | bitmask | unmet FINE preconditions (0 = ready) |
| 0x02C6 | `aocs.fidelity_mode` | enum | 0=full, 1=secular (accel bypass) |
| 0x02C7 | `aocs.geoloc_err_m` | m | alt × total pointing error — the operator's real currency |

Also **write** the already-declared-but-dead `0x0260 aocs.wheels_enabled` (fixes root cause 7).

### HK SID assignment
**Create SID 7 `AOCS_DETERMINATION`, interval 4.0 s, default disabled** (enabled by TC(3,5) like SIDs 2–6, per `simulation.yaml housekeeping.default_enabled_sids: [1, 11]`). Put 0x02A0–0x02C7 there. **Do not extend SID 2** — it is referenced verbatim by procedures, by `tests/test_mcs_hk_round_trip.py`, and by the textbook (`PART2_SIM_RECONCILIATION.md:234` "SID 2 = AOCS ✓"), and `simulation.yaml` documents a bit-budget that SID growth would break (1351.5 bps → 156 % of the beacon tier).
Exception, as *fixes* not additions: correct the model to publish bearing health at 0x0284-0x0287 (already in SID 2) and move `slew_state` to 0x0292 with a new `parameters.yaml` entry.

### New / changed telecommands
| Name | S8 func_id | Fields | Purpose |
|---|---|---|---|
| `AOCS_SET_POINTING_FRAME` | **85** (free) | `frame:u8` (0=INERTIAL,1=LVLH_NADIR,2=TARGET,3=SUN) | Make the reference frame explicit — the fix for root cause 1 |
| `AOCS_SET_TARGET_QUAT` | **86** | `qx,qy,qz,qw:f32` | Inertial hold target (was structurally impossible) |
| `AOCS_SET_TARGET_GEO` | **87** | `lat,lon:f32`, `alt_m:f32` | Ground-target tracking (off-nadir imaging) |
| `AOCS_SET_CONTROL_BW` | **88** | `omega_n:f32`, `zeta:f32` | The commissioning gain-tuning step the manual §8 step 10 already requires (`defects/reviews/aocs.md` §5.7) |
| `AOCS_FINE_READY_CHECK` | **89** | — | Returns the 0x02C5 bitmask as TM(8,130); this is `defects/reviews/aocs.md:217` |
| `AOCS_SET_MOM_MGMT` | **90** | `enable:u8`, `entry_pct:u8`, `exit_pct:u8` | Autonomous desat thresholds |
| `AOCS_EST_RESET` | **91** | `mode:u8` (0=cold/TRIAD, 1=warm) | Estimator reinitialisation after a time jump (`gps_time_sync_recovery.md` needs this) |
| `AOCS_SET_MODE` | 0 (unchanged) | mode 0–**9** | Accept the new INERTIAL=9; range check `:1566` widens |
| `AOCS_SET_DEADBAND` | 15 (**semantics fixed**) | `deadband_deg:f32` | Becomes a real control deadband *and* keeps writing the event threshold; rename the field in the catalog description |

S8 routing: `service_dispatch.py:574-575` currently routes `range(0,16)` to AOCS. Extend with `elif func_id in range(85, 92): return self._route_aocs_cmd(...)` alongside the existing func-84 special case (`:594-595`). **Fix `_s19_dispatch` (`:1786-1797`) to carry an `action_data` byte string from the rule** — add `action_data: [0x03]` etc. to `s19_rules.yaml` — otherwise every autonomy rule stays broken (root cause 8).

---

## Work packages

Ordering is dependency-driven; WP-1…WP-4 are prerequisites for everything else and are individually shippable.

**WP-0 — Determinism & breakpoint completeness** · S · no deps
Files: `aocs_basic.py` (`__init__`, `get_state:1899`, `set_state:1907`), `configs/eosat1/simulation.yaml`, `breakpoints.py` (no change needed if state moves into the dataclass).
Move `_target_q, _slew_target_q, _slew_start_q, _slew_start_time, _slew_rate_dps, _orbit_phase, _kp, _att_error_threshold_deg, _rw_bearing_health, _emergency_rate_threshold`, all `_prev_*` into `AOCSState`; add a per-model `random.Random(seed)` with `getstate()/setstate()` in the snapshot; add `seed:` to `simulation.yaml`.
Accept: (a) `save → tick 100 → load → tick 100` produces bit-identical telemetry to `save → tick 100`; (b) a breakpoint saved mid-SLEW resumes slewing to the same target (today it aborts to NOMINAL); (c) `set(vars(model)) - set(model.get_state())` contains only `_state`, `_param_ids` and immutable config.

**WP-1 — Fixed-step sub-stepping + secular fallback** · M · deps: WP-0
Files: `aocs_basic.py:1087` (`tick`), `configs/eosat1/subsystems/aocs.yaml` (`integration: {h_s: 0.5, max_substeps: 120}`), `configs/eosat1/telemetry/parameters.yaml` (0x02C6).
Accept: (a) `_tick_detumble` from 0.3 °/s converges monotonically for dt_sim ∈ {1, 10, 60, 600, 1000} — today it diverges to 1.4e39 at dt=1000 (`/tmp/accel_test.py`); (b) trajectory at dt_sim=1 s and dt_sim=60 s agree to < 0.01° after 3600 s of sim time; (c) wall-clock cost per tick at speed=60 is < 20 ms; (d) 0x02C6 reads 1 above 60× and an `AOCS_FAST_LOOP_BYPASSED` event is emitted once per transition.

**WP-2 — Rigid-body dynamics + wheel/MTQ actuator physics** · L · deps: WP-1
Files: `aocs_basic.py` (new `_integrate_dynamics`, rewrite `_tick_wheels:948`, `_tick_magnetorquers:615`), `aocs.yaml` (mass properties, wheel geometry/torque, MTQ dipole).
Euler equation with wheel momentum coupling; pyramid distribution matrix + pseudo-inverse allocation; analytic wheel motor lag; MTQ dipole → torque; vector `h_body`.
Accept: (a) with zero control torque and zero disturbance, |Iω + h_w| is conserved to 1×10⁻⁹ over 10⁴ s; (b) commanding +0.01 N·m about X for 100 s produces ω_x = 0.01×100/120 = 8.33×10⁻³ rad/s = 0.477 °/s ±1 %; (c) four wheels at equal speed in the pyramid give |h_body| < 1×10⁻⁶ N·m·s (today the scalar sum reports 2.51); (d) a 60 A·m² dipole in a 30 µT field produces 1.8×10⁻³ N·m ±1 %.

**WP-3 — Attitude-dependent sensor models** · L · deps: WP-2
Files: `aocs_basic.py:395-613` (ST/CSS/mag), new `_tick_gyro_measurement`; port `sun_vector_eci`, `igrf_dipole_eci`, `quat_to_dcm` from `ADCS_failure_sim/cubesat_adcs_sim.py:241-338`; `aocs.yaml` sensor spec block.
Accept: (a) rotating `q_true` by 90° about X changes `css_sun_*` and `mag_*` accordingly (today they are invariant); (b) ST blinding onset/exit times match a hand-computed Sun/Earth-limb geometry to ±1 tick; (c) gyro Allan deviation over a 10⁴ s run matches the configured ARW/RRW to within 20 %; (d) `_orbit_phase` no longer exists.

**WP-4 — 6-state MEKF** · L · deps: WP-3
Files: new `packages/smo-simulator/src/smo_simulator/models/aocs_estimator.py`; wired from `aocs_basic.tick`.
Accept: (a) steady-state 3σ knowledge error with ST @4 Hz ≤ 0.02°, and the *reported* 0x02A7 is within 30 % of the *actual* |q̂ ⊗ q_true⁻¹| statistics over 3600 s (covariance consistency / NEES test); (b) with ST removed, knowledge 3σ grows as ARW√t and reaches ≥ 0.1° within 300 s; (c) P stays symmetric-PSD for 10⁵ steps (Joseph form); (d) an injected 45° ST fix is rejected by the innovation gate and 0x02C2 increments.

**WP-5 — PD control law, LVLH/inertial reference generator, mode 9** · M · deps: WP-2, WP-4
Files: `aocs_basic.py:721-856` (all pointing tick functions), `aocs_mode_codes.yaml` (append mode 9), `tc_catalog.yaml` (funcs 85–88), `service_dispatch.py:574` + `:632`.
Accept: (a) in NADIR the body pitch rate settles at 0.0635 °/s ±2 % and `nadir_body_z → 1.0` within 0.01; (b) removing the ω_ref feed-forward reproduces the 3.2° tracking lag (regression guard for the teaching case); (c) 30° slew: NOMINAL 285 s to 1.0°, FINE 114 s to 0.1° (±20 %); (d) INERTIAL mode 9 accumulates ≥ 2.5× the secular wheel momentum per orbit that NADIR does.

**WP-6 — COARSE vs FINE differentiation + payload gating** · M · deps: WP-4, WP-5 (details in the next section)
Files: `aocs_basic.py` (mode config table), `payload_basic.py:443-456` and `:728-736`, `eps_basic.py:481` + `simulation.yaml eps.line_power_w`, `configs/eosat1/telemetry/parameters.yaml` (0x02C5).
Accept: (a) `capture` returns `success: False, code=POINTING_NOT_FINE` when `aocs.mode != 5`; (b) AOCS line power reads 4 W (COARSE) / 9 W (NOMINAL) / 15 W (FINE) — the 15 W matching `constraint_checkers.py:43`; (c) `fine_ready` bitmask enumerates ST-not-tracking, <4 wheels, covariance-not-converged, rates-too-high, momentum-too-high, and is 0 exactly when a FINE transition would succeed.

**WP-7 — Disturbance torque model** · S · deps: WP-2
Files: `aocs_basic.py` (new `_disturbance_torques`), `aocs.yaml` (`residual_dipole_am2`, `cp_cg_offset_m`, `drag_area_m2`, `srp_area_m2`, `Cd`, `Cr`).
Accept: (a) GG torque at 45° off-nadir equals 1.13×10⁻⁴ N·m ±5 %; (b) SRP steps to zero at eclipse entry and the wheel-momentum slope changes visibly; (c) secular momentum accumulation is 0.2–0.4 N·m·s per orbit in NADIR.

**WP-8 — Telemetry, HK SID 7, MCS display** · M · deps: WP-4, WP-5
Files: `configs/eosat1/telemetry/parameters.yaml`, `configs/eosat1/telemetry/hk_structures.yaml` (new SID 7), `configs/eosat1/mcs/displays.yaml:257-300`, `configs/eosat1/mcs/limits.yaml`, `simulation.yaml housekeeping`.
Also fix: publish bearing health at 0x0284-87, move slew_state to 0x0292, write 0x0260.
Accept: (a) `tests/test_mcs_hk_round_trip.py` extended to SID 7 passes; (b) the AOCS display shows knowledge error and control error as *separate* gauges with arcsec ranges; (c) `aocs.rw2_bearing_health` no longer reads the slew state.

**WP-9 — Fix the momentum limits and the dead autonomy** · S · deps: WP-2
Files: `configs/eosat1/monitoring/s12_definitions.yaml:121-127` (monitor 0x02BA `h_env_frac` at 70/90 %, not 0x025B at ±90 N·m·s), `configs/eosat1/mcs/limits.yaml:27`, `configs/eosat1/monitoring/s19_rules.yaml` (rule 2001 trigger; rule 2004 trigger id + `action_data`; rules 2005-2008 per-wheel `action_data`), `service_dispatch.py:1786-1797` (`_s19_dispatch` must pass `action_data`), `configs/eosat1/subsystems/fdir.yaml:80-85`, `engine.py:648` (`safe_mode_aocs` → mode 3 COARSE_SUN, not 2 DETUMBLE), `configs/eosat1/fdir/procedures/aocs_safe_mode_recovery.yaml:19-26`.
Accept: (a) letting momentum grow past 70 % fires S19 rule 2001 and the spacecraft desaturates autonomously; (b) RW3 overspeed disables **wheel 2**, not wheel 0; (c) rule 2004 no longer commands AOCS OFF; (d) a fresh engine start does **not** latch `_fdir_safe_active` (root cause 7).

**WP-10 — Test suite** · M · deps: all
Files: rewrite `tests/test_simulator/test_aocs_state_machine.py` (test 19 `total_momentum > 0` becomes a vector-momentum assertion), rewrite `tests/test_adcs_torque_gain.py:89-140` (it pokes `_target_q` directly), new `tests/test_simulator/test_aocs_dynamics.py`, `test_aocs_estimator.py`, `test_aocs_accel_stability.py`, `test_aocs_breakpoint_roundtrip.py`.

**WP-11 — Documentation reconciliation** · M · deps: WP-5, WP-6 — see Risks.

---

## COARSE vs FINE — the required distinction

Physically real *and* operationally visible. Numbers derived from §Proposed model, not asserted.

| | COARSE_SUN (3) | NOMINAL / nadir (4) | FINE_POINT (5) |
|---|---|---|---|
| Sensors in the loop | CSS (6 heads) + magnetometer + gyro, TRIAD update @1 Hz | **ST @1 Hz** + gyro, MEKF | **ST @4 Hz** + gyro, MEKF, both ST powered (primary + hot spare) |
| Actuators | magnetorquers only (wheels idle) | 3 wheels min, ω_n = 0.020 rad/s | **all 4 wheels**, ω_n = 0.050 rad/s, deadband 0.005° |
| Knowledge error (3σ) | **2.5°** | 0.020° (72″) | **0.008° (29″)** |
| Control error (3σ) | 4° | 0.060° | **0.010°** |
| Total pointing (3σ) | ~5° | 0.065° | **0.013°** |
| Geolocation error @450 km | 39 km | 510 m | **100 m** |
| Rate stability (3σ) | 0.05 °/s | 0.010 °/s | **0.002 °/s** |
| Settling after a 30° slew | n/a | 285 s to 1.0° | **114 s to 0.05°, 165 s to gate** |
| AOCS line power | **4 W** (wheels idle, 1 ST off) | **9 W** | **15 W** (2 ST @8 W + high wheel duty) |
| Momentum cost | none (magnetic) | 0.3 N·m·s/orbit | 0.3 N·m·s/orbit + slew transients |

FINE entry preconditions (the 0x02C5 bitmask, and the physics behind each):
bit0 selected ST TRACKING with ≥ 5 stars; bit1 all 4 wheels active; bit2 MEKF converged (`knowledge_err_3sig < 0.03°` sustained 30 s); bit3 |ω| < 0.01 °/s; bit4 `h_env_frac < 70 %`; bit5 not in eclipse-only propagation; bit6 slew idle for ≥ 60 s. Non-zero ⇒ `AOCS_SET_MODE(5)` is **refused** with a TM(1,8) failure code, instead of the current silent acceptance-then-drop-back at `:760-768`.

Payload gating (`payload_basic.py`):
- `capture` **refuses** unless `aocs.mode == 5` AND `total_pointing_err_3sig < 0.05°` AND `rate_stability_3sig < 0.005 °/s` AND slew settled. Distinct failure code so the operator sees *why*.
- If FINE is lost mid-strip, the payload continues but flags the image `status = 1 (PARTIAL)` and stamps the actual pointing error into the catalog entry (`ImageMetadata` at `payload_basic.py:27-36` already has `quality` and `status` fields — populate them from the real number).
- Image quality curve (`:443-453`) rescaled to the new error magnitudes: full quality below 0.02°, knee at 0.05°, zero above 0.2°. Keep the *shape*; change the breakpoints so FINE and NOMINAL actually separate.

---

## Reuse of `ADCS_failure_sim`

**Reuse the algorithms; reject every number.**

| Component | Verdict |
|---|---|
| `quat_mult/conj/to_dcm/dcm_to_quat/quat_normalize` (`:223-278`), `skew`, `julian_date`, `gmst_rad` | **Port verbatim.** Correct, tested, numpy-based. Replaces the hand-rolled and partly-wrong helpers at `aocs_basic.py:1917-1948` (notably `_quat_error_axis:1929`). |
| `lvlh_dcm` (`:385-393`), `sun_vector_eci` (`:304`), `igrf_dipole_eci` (`:315`), `in_eclipse` (`:395`) | **Port.** Exactly the reference-frame and environment machinery the main sim lacks. |
| RK4 Euler dynamics with wheel-momentum coupling (`:626-641`) | **Port the structure.** This is the correct `Iω̇ = −ω×(Iω+h) + M + τ_w`. |
| PD quaternion law + MTQ cross-product dump (`:572-594`) | **Port the structure.** |
| Gravity-gradient / residual-dipole / aero torques (`:616-624`) | **Port the GG form**; re-derive the residual/aero constants for 480 kg. |
| TRIAD (`:406-419`) + predict/correct with innovation gating (`:520-568`) | **Reuse as the COARSE_SUN/acquisition path only.** Its `q_est = 0.7·q_pred + 0.3·q_meas` blend is a hand-tuned complementary filter with no covariance — superseded by the MEKF for NOMINAL/FINE. |
| **Mass properties** `INERTIA = diag(0.0419, 0.0419, 0.0067)` (`:77`) | **Reject.** 3U CubeSat, ~3000× too small. |
| **Wheels** `RW_MAX_TORQUE 1e-3`, `RW_MAX_MOMENTUM 3e-2`, `RW_INERTIA 5e-6` (`:94-96`) | **Reject.** EOSAT-1 wheels are 2.88 N·m·s / 0.005 kg·m², ~100× larger. |
| **MTQ** `MTQ_MAX_DIPOLE 0.2 A·m²` (`:99`) | **Reject.** Needs ~60 A·m² at this mass. |
| **Gains** `KP=[0.012,0.012,0.004]`, `KD=[0.045,…]` (`:103-104`) | **Reject.** Sized for I≈0.04; would give ω_n ≈ 0.4 rad/s here. |
| **Sensor noise** MAG 250 nT, SUN 0.5°, `EST_ATT_NOISE_DEG 0.4` (`:113-115`) | **Reject.** No star tracker at all in that model — 0.4° residual is CubeSat-grade, ~50× worse than EOSAT-1's ST. |
| 3-wheel (per-axis) architecture, `omega_meas = omega + noise` with no gyro bias | **Reject.** EOSAT-1 has 4 wheels in a pyramid and FOGs with bias — the bias is the reason the MEKF exists. |
| Global module state: `rng` at module level (`:220`), constants read from module globals inside `run()` (INERTIA, KP, KD, RW_* are *not* in `resolve_params`) | **Must not be imported as-is.** `run()` is a batch function returning a full log; it has no per-tick step, no `get_state`/`set_state`, and mutating module globals would leak between the viewer and the main sim. Extract the pure functions into a shared `smo_common.attitude` module; leave `cubesat_adcs_sim.py` untouched so `tools/adcs_sim_viewer.py` and `tests/test_adcs_sim_viewer.py` keep working. |

**FDIR/S19 that will break or is already broken** — see WP-9. Summary of AOCS-touching rules audited:
`s19_rules.yaml` 2001 (dead: 0x025B limit unreachable), 2002/2003 (no-op: no unit byte), 2004 (wrong trigger id 0x0202, and commands AOCS OFF), 2005–2008 (all disable wheel 0). `fdir.yaml:28-32` (`att_error > 5 → safe_mode_aocs` → DETUMBLE — semantically wrong, should be COARSE_SUN), `:43-62` (RW temp > 65 → disable_rwN, fine), `:80-85` (dead param 0x0260, latches SAFE at t=0), `:66-70` (`bus_voltage < 24 → safe_mode_aocs`, fine). `fdir/procedures/aocs_safe_mode_recovery.yaml` step 1 commands mode 2 labelled "safe" (wrong mode name), step 2 is a malformed no-op. `fault_propagation.yaml:77-84` keys on `aocs.mode` for "safe mode entry" with no mode value bound to the canonical table.

---

## Risks

### Must NOT be silently changed — `att_error` (0x0217) semantics
0x0217 is referenced by **32 procedure steps across 24 files**. Its per-mode expected magnitudes are baked into GO/NO-GO gates. Any redesign must keep 0x0217 as "total attitude pointing error, degrees" with these bands intact, and publish the *new* decomposition on the new IDs instead:

| Threshold | Procedures that assert it |
|---|---|
| `< 0.1°` fine/imaging | `nominal/imaging_session.md:26,30,57,89` (NOM-002), `nominal/eclipse_transition.md:121` (NOM-010), `nominal/shift_handover.md:31`, `contingency/corrupted_image.md:110`, `contingency/star_tracker_failure.md:85,113` (CTG-008), `manual/commissioning_sequence_walkthrough.md:558` (§12.5) |
| `< 1.0°` nominal | `nominal/startup.md:39,74`, `nominal/routine_health_check.md:43`, `nominal/eclipse_transition.md:35,81`, `nominal/momentum_management.md:35,63,68` (NOM-005), `contingency/aocs_anomaly.md:56,73` (CTG-002), `emergency/emergency_safe_mode.md:112,155` |
| `< 1.5°` three-wheel | `contingency/reaction_wheel_anomaly.md:56,66,86` (CTG-007) |
| `< 2.0°` | `contingency/solar_panel_loss_response.md:20,85`, `contingency/solar_array_degradation.md:42` |
| `< 5.0°` / `< 10°` acquisition | `emergency/loss_of_attitude.md:76,79,106` (EMG-005), `leop/sun_acquisition.md:70-71` (LEOP-005), `emergency/total_power_failure.md:71`, `emergency/emergency_safe_mode.md:53,62` |
| `> 30°` anomaly declaration | `emergency/emergency_safe_mode.md:23` |

**Consequence of WP-5/WP-6:** the new NOMINAL steady-state error is 0.065° and FINE is 0.013°, so every threshold above still passes — *but* the transient behaviour changes completely (real settling, real overshoot, real disturbance response). Procedures with **time-bounded** gates need re-timing against the new model: `nominal/startup.md:39` ("< 1.0° within 10 s" — the new NOMINAL takes 285 s to settle from a disturbed state and **will fail as written**), `nominal/eclipse_transition.md:121` ("< 0.1° within 120 s"), `commissioning/first_light.md:28` ("mode 5 within 30 s"), `commissioning/payload_calibration.md:68`, `commissioning/aocs_mode_transitions.md:44` (COM-005), `nominal/momentum_management.md:64` ("120 s for re-convergence to fine pointing"). **These six must be re-validated and re-timed, not left to fail.**

### Affected ops cards (by ID)
LEOP-003, **LEOP-005** (sun acquisition), LEOP-007; **COM-003** (AOCS sensor calibration), **COM-004** (actuator checkout), **COM-005** (mode transitions), COM-010, **COM-011/COM-012** (first light); **NOM-002** (imaging session), **NOM-005/NOM-007** (momentum management), **NOM-010** (eclipse transition); **CTG-002** (AOCS anomaly), **CTG-007** (RW anomaly), **CTG-008** (ST failure); **EMG-001**, **EMG-005** (loss of attitude). Source: `configs/eosat1/mcs/role_analysis/aocs_role.md:17-49`, `10_desk_procedures.md:69,93,147,160-169`.

### Textbook (`/root/cdf`) exposure
- `docs/PART2_SIM_RECONCILIATION.md:259-290` — the AOCS mode table is already flagged **CRITICAL** with 14 textbook locations wrong (§5.4, §6.3, §10.20, §13.1 LEOP-005/007, §13.2 COM-005/COM-011, §13.4 CTG-002/CTG-008, §13.5 EMG-001/002). **Appending INERTIAL as mode 9 is safe; renumbering anything is not.**
- `:161-171` — textbook §5.7 quotes a "NOMINAL pointing budget < 0.5° 3σ". The new NOMINAL 0.065° 3σ is *inside* it; harmless, but the reconciliation note about budget-vs-alarm should be updated to reference the new 0x02AA.
- `:317-325` — textbook quotes a **0.32° pointing-error baseline** that "the trainee won't see anywhere". With the new model the trainee will see 0.065°/0.013°. Either the textbook's 0.32° becomes the *end-of-life degraded* budget or it is corrected. Decide before implementing WP-6.
- `docs/orbit_north/week3/breakpoint_spec.md` — **BP-1 asserts "AOCS mode: NADIR pointing, body rates < 0.01 °/s, reaction wheels spun up"**. Under the current model NADIR does not exist; under the new one, nadir tracking means **pitch rate 0.0635 °/s**, which *violates BP-1 as written*. BP-1's rate criterion must become "rate error vs LVLH < 0.01 °/s". Same for `student_procedure_quickref.md`, `sim_commissioning_instructor.md`, `training_day1/2.md`.
- `configs/eosat1/manual/02_aocs.md` — carries a **third, wrong mode table** (§3), star-camera face assignments **inverted** vs the code (manual: ST-Zenith on −Z; code `aocs_basic.py:429-434`: ST1 zenith on +Z), CSS param IDs (0x0220-0x0225) that collide with `aocs.sun_body_*`, and `mag_a_valid/mag_b_valid` at 0x0214/0x0215 which are actually `gps_vy/gps_vz`. Fixing these is unavoidable and is a *doc* change, not a model change.

### Test exposure
`tests/test_adcs_torque_gain.py` (19 tests) pokes `model._target_q` directly at `:99` and asserts on the kinematic `_tick_nominal` — **will not survive WP-5** and must be rewritten against commanded torque. `tests/test_simulator/test_aocs_state_machine.py` test 19 asserts `total_momentum > 0` — fails once momentum is a vector and the wheels start balanced. `tests/test_simulator/test_device_power_gates.py`, `test_s12_s19_integration.py`, `test_new_failure_modes.py`, `test_performance.py` and the scenario tests all touch AOCS params.

### Scenario exposure
`configs/eosat1/scenarios/scenario_2_cerise_adcs_fault.yaml:12-23,44-49` **documents the current physics gap in its own header** ("the model does NOT integrate control effort into reaction-wheel speed … does NOT spin a wheel to saturation"). Once WP-2 lands, the CERISE gain-corruption fault will do what the real CERISE anomaly did — over-torque the corrupted axis to wheel saturation and auto-safe. That is a *large improvement*, but the scenario's briefing text, expected-signature notes and instructor answer key must be rewritten, as must `debris_impact_hermes3.yaml:72-83` (which currently drives `aocs.rw2_speed` by puppet `set_parameter`, a technique that will be overwritten by real dynamics), plus `aocs_actuator_stuck.yaml`, `aocs_sensor_cascade.yaml`, `aocs_star_tracker_failure.yaml`, `aocs_wheel_failure.yaml`, `rw_bearing.yaml`.

### Regression risk to fix first
WP-9's `aocs.wheels_enabled` fix will **change `sc_mode` for every existing session** (today the spacecraft is silently latched FDIR-SAFE from tick 1). Any scenario or test that has been implicitly calibrated against a permanently-SAFE spacecraft will move. Land WP-9 early and alone so the delta is attributable.agentId: a1fb98384538b6354 (use SendMessage with to: 'a1fb98384538b6354', summary: '<5-10 word recap>' to continue this agent)
<usage>subagent_tokens: 258202
tool_uses: 97
duration_ms: 941078</usage>
