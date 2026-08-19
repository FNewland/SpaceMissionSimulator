# EO-Sat-1 Propulsion Guide (Phase D)

**Scope ruling (Flight Director):** propulsion exists for **debris-avoidance
manoeuvres** and **limited orbit maintenance** (drag make-up / semi-major-axis
biasing). It is deliberately minimal. There is **no end-of-life deorbit
capability and none is needed**: natural decay from 450 km at
B = 77.9 kg/m² is 1.7 yr at mean solar activity (4.8 yr solar-min, 0.2 yr
with the wings commanded broadside) — compliant with the FCC 5-year rule at
every solar condition. A propulsive deorbit would cost 42.6+ m/s, a second
complete propulsion system.

> **2026-08 caution:** the decay figures above were derived at the retired
> 450 km altitude declaration. The TLE-true mean altitude is ~513 km
> (orbit.yaml, 2026-08 epoch/altitude ruling), where atmospheric density is
> roughly half and natural decay is several times slower — the solar-min case
> very likely exceeds the FCC 5-year rule. The decay analysis, and with it
> the standing "no deorbit capability" ruling, needs re-deriving at 513 km.
> Owner decision pending; not re-derived in the 2026-08 config sweep.

## Hardware

1U green-monopropellant (LMP-103S/ADN-class) blowdown module, single
thruster on the −X face thrusting along **+X body** — prograde in the
nominal nadir attitude; a retrograde burn requires a deliberate 180° yaw
(INERTIAL hold). The AOCS coupling is intentional and is the teaching point.

| Parameter | Value |
|---|---|
| Thrust | 0.33 N (22 bar BOL) → 0.14 N (9.33 bar EOL), F ∝ P |
| Isp | 220 s BOL → 196 s EOL (208 s mean) |
| Propellant | 0.400 kg, ρ = 1240 kg/m³, 560 cm³ tank, N₂ blowdown 2.36:1 |
| Catalyst bed | preheat ≥ 180 °C, 12 W × ~20 min (4 Wh) via `HEATER_THRUSTER` (S8 func 42) |
| Wet system | 1.55 kg (12.9 % of the 12 kg bus) |
| **Total ΔV** | **66.9 m/s** |

**ΔV budget (2-yr active phase, debris-avoidance + drag-makeup framing):**
drag make-up 42.6 + SMA/LTAN biasing 3.0 + CAM (6 events × 0.1) 0.6 +
commissioning/cal 1.0 + eccentricity control 1.5 + margins 7.3 =
**56.0 m/s required** vs 66.9 capability (**+19.5 %**). The remaining
envelope (`prop.dv_remaining`, 0x0B0C) is first a **collision-avoidance
reserve**: below ~1 m/s the spacecraft has effectively lost its CAM
capability and the conjunction posture changes.

Typical burns: CAM 0.20 m/s ≈ 7.5 s; fortnightly drag make-up 0.85 m/s ≈
32 s (BOL); all fit inside a pass. A 1.0 m/s prograde burn raises SMA
1.79 km and shifts pass times ~34 s/day (measured in-sim: −33.5 s/day,
analytic −33.9).

## State machine and interlocks

`SAFED(0) → ARMED(1) → FIRING(2)`, plus latched `INHIBIT(3)` after a second
abort (cleared only by `PROP_INHIBIT_CLR`). Two-step arming: `PROP_SET_BURN`
must precede `PROP_ARM` (code 0xA5C3) by ≤ 300 s. All eight arm interlocks
(catbed ≥ 180 °C, tank 6–24 bar, propellant > 5 g, AOCS mode ∈ {4,5,9},
att_error < 2°, SoC > 45 %, phase NOMINAL, not in eclipse, a latch branch
open, no inhibit) return **distinct S1(8) codes** and set their bit in
`prop.arm_inhibit_mask` (0x0B15) — the single most useful trainee parameter.

On-board burn aborts (att_error > 2°, feed < 4 bar, catbed > 350 °C,
watchdog, valve loss, EPS `prop` line shed) plus S19 autonomy: rules
8001–8006 (abort on 0x0217 S12 violation, tank pressure, catbed overtemp,
isolate on leak, disarm on safe mode, abort on SoC critical). An aborted
burn's **partial ΔV still perturbs the orbit** — post-burn OD is mandatory.

## Orbit integration (the part that makes it real)

A finite burn is integrated at ≤ 1 s sub-steps; the resulting ΔV
(along-track × cos θ + lateral × sin θ, θ = att_error) is applied to SGP4 as
**one impulse at the burn mid-point** via a 6-parameter TLE differential
correction (equinoctial-parameterised; 3–6 iterations, sub-mm residual;
divergence → event 0x0812, orbit unchanged). Each applied burn bumps
`epoch_version` (0x0B19, `/api/state`): planner and MCS treat it like a time
jump, drop contact/ground-track caches and adopt the new TLE. Breakpoints
serialise the element set (`orbit_elset`) — "SGP4 is stateless given a UTC
time" stopped being true the day propulsion arrived.

While FIRING the engine **clamps time acceleration to ≤ 10×** (event
0x0811 `PROP_SPEED_CLAMPED`) so burns and aborts stay observable.

## Power and thermal

EPS line 8 `prop` (default OFF, **last** in the shed order — shedding it
mid-burn triggers the abort). Billing follows actuator state, not the line
switch: 0 W quiescent, 2 W valve drivers while ARMED/FIRING, +12 W while the
catbed heater runs. The catbed is the TCS `thruster` circuit
(`tcs.temp_thruster` 0x0409, heater state 0x041B): manual via func 42,
thermostat hold 190/230 °C via `HEATER_AUTO_MODE circuit=2`. A manoeuvre is
a **power activity**: 4 Wh of preheat plus the burn must fit the daylight
budget.

## Interfaces

- **Commands:** S8 funcs **108–118** (`PROP_ARM` 108, `PROP_DISARM` 109,
  `PROP_SET_BURN` 110, `PROP_EXECUTE` 111, `PROP_ABORT` 112,
  `PROP_ISO_OPEN/CLOSE` 113/114, `PROP_SET_LIMITS` 115,
  `PROP_INHIBIT_SET/CLR` 116/117, `PROP_GET_STATUS` 118) + func 42 heater.
  *(Brief 03 planned 85–95; Phase C's AOCS pointing block took 85–91, so
  108–118 is the allocated block.)*
- **Telemetry:** block **0x0B00–0x0B1B**; **HK SID 9** at 8 s (disabled at
  power-up, `HK_ENABLE sid=9`). *(Brief planned SID 7; Phase C took it for
  AOCS.)* EPS line params 0x014A/0x014B; TCS 0x0409/0x041B.
- **Events:** **0x0800–0x0812**. **S12:** tank-pressure band + **delta/rate
  monitor** (the leak detector), catbed, propellant, low thrust, ΔV
  envelope. **S19:** ea_id 8001–8006.
- **Procedures:** **NOM-004** (planning, takes the reserved index gap),
  **NOM-013** (execution; the book's NOM-005 — sim NOM-005 was already HK
  Configuration), **CTG-028** (anomaly; the book's CTG-006 — sim CTG-006 was
  already Payload Anomaly; and "SID 5/propulsion" is SID 9 here). OBCP 4
  (`obcp/04_orbit_manoeuvre.yaml`) is the on-board timed sequence. Planner
  activity `orbit_manoeuvre`.
- **Training scenarios:** `prop_valve_stuck_open`, `prop_slow_leak`,
  `prop_underperformance`, `prop_burn_eclipse_power`,
  `prop_attitude_loss_mid_burn`.

## Model limitations (taught, not simulated)

One thruster, one axis — no TVC, no thruster desaturation (NOM-007 stays
magnetorquer-only). No propulsive disposal, plane changes, orbit raising,
phasing, rendezvous, closed-loop orbit control, or real conjunction
screening (CAM is a scripted scenario input). No combustion/feed transients,
non-isothermal blowdown, slosh/CG migration, or inertia-tensor coupling
(mass is telemetry only). Two latch-valve *branches*, one thruster. Drag
decay is whatever SGP4's B* gives — the ΔV budget above is an offline sizing
calculation, not a live feedback loop.
