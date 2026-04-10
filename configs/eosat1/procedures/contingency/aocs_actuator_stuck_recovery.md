# CON-022: Reaction Wheel Stuck/Seizure Recovery
**Subsystem:** AOCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover from a reaction wheel seizure where a wheel bearing has locked and the wheel
stops responding to torque commands. Unlike a bearing degradation (gradual performance
loss), a seizure is a sudden, complete failure: the wheel either freezes at its
current speed or drops to zero RPM, and the AOCS control loop can no longer use that
wheel for torque authority. The remaining three wheels must absorb the lost wheel's
momentum, which if unchecked will drive them toward saturation. This procedure guides
the operator through detection, confirmation, wheel isolation, three-wheel mode
reconfiguration, and momentum management to restore stable attitude control.

EOSAT-1 carries four reaction wheels in a pyramid configuration. Nominal operations
require all four for optimal performance, but the system can maintain three-axis
control with any three operational wheels at slightly relaxed pointing accuracy.

## Prerequisites
- [ ] AOCS telemetry (SID 2) is being received at >= 1 Hz
- [ ] At least three reaction wheels were functional prior to the seizure event
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified of AOCS anomaly
- [ ] Procedure CON-007 (Reaction Wheel Anomaly) reviewed for reference

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| aocs.rw1_speed | 0x0207 | Monitor — identify stuck wheel |
| aocs.rw2_speed | 0x0208 | Monitor — identify stuck wheel |
| aocs.rw3_speed | 0x0209 | Monitor — identify stuck wheel |
| aocs.rw4_speed | 0x020A | Monitor — identify stuck wheel |
| aocs.rw1_temp | 0x0218 | Monitor — seizure causes heating |
| aocs.rw2_temp | 0x0219 | Monitor — seizure causes heating |
| aocs.rw3_temp | 0x021A | Monitor — seizure causes heating |
| aocs.rw4_temp | 0x021B | Monitor — seizure causes heating |
| aocs.mode | 0x020F | Current AOCS mode |
| aocs.att_error | 0x0217 | Monitor — may increase during recovery |
| aocs.rate_roll | 0x0204 | Body roll rate |
| aocs.rate_pitch | 0x0205 | Body pitch rate |
| aocs.rate_yaw | 0x0206 | Body yaw rate |
| aocs.mtq_x | 0x020B | Magnetorquer X dipole |
| aocs.mtq_y | 0x020C | Magnetorquer Y dipole |
| aocs.mtq_z | 0x020D | Magnetorquer Z dipole |
| aocs.mag_valid | 0x0230 | Magnetometer validity (needed for desaturation) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| SET_PARAM | 20 | 1 | — | Set onboard parameter |
| AOCS_3W_MODE | 8 | 1 | 5 | Configure three-wheel control mode |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode |
| AOCS_DESATURATE | 8 | 1 | 3 | Command magnetorquer momentum dump |

## Procedure Steps

### Step 1: Detect and Identify the Stuck Wheel
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** Record all wheel speeds:
- `aocs.rw1_speed` (0x0207) — RW-1 speed in RPM
- `aocs.rw2_speed` (0x0208) — RW-2 speed in RPM
- `aocs.rw3_speed` (0x0209) — RW-3 speed in RPM
- `aocs.rw4_speed` (0x020A) — RW-4 speed in RPM
**Action:** Wait 30 s and request a second sample: `HK_REQUEST(sid=2)`
**Action:** Compare the two samples. A stuck wheel will show:
- Speed that does not change between samples (frozen at the seizure speed)
- OR speed that dropped to 0 RPM suddenly
- While the other three wheels show increasing speed divergence as they compensate
**Verify:** Record all wheel temperatures:
- `aocs.rw1_temp` (0x0218), `aocs.rw2_temp` (0x0219)
- `aocs.rw3_temp` (0x021A), `aocs.rw4_temp` (0x021B)
**Action:** A seized wheel will show elevated or rapidly rising temperature due to
friction at the locked bearing. Correlate the stuck-speed wheel with the hot wheel
to confirm seizure diagnosis.
**Verify:** `aocs.att_error` (0x0217) — record current value. May be increasing as
the control loop tries to command the unresponsive wheel.
**GO/NO-GO:** Exactly one wheel identified as stuck (speed frozen + temperature
rising). Note the wheel index (0-3 = RW-1 through RW-4). If multiple wheels are
stuck, escalate to CON-002 Step 7 (multi-wheel failure).

### Step 2: Disable the Stuck Wheel
**Action:** Disable the seized wheel to remove it from the control loop:
`AOCS_DISABLE_WHEEL(wheel_idx=N)` (Service 8, Subtype 1, func_id 2)
where N is the stuck wheel index (0-3).
**Verify:** Wait 10 s, then request AOCS housekeeping: `HK_REQUEST(sid=2)`
**Verify:** The disabled wheel's speed should begin to decay (friction will slow it
down since no torque is being applied). Note: for a fully seized bearing, speed may
already be at 0.
**Verify:** `aocs.att_error` (0x0217) — monitor for transient increase. An increase
up to 5 deg is acceptable during the transition as the control loop adapts.
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205),
`aocs.rate_yaw` (0x0206) — all body rates must remain below 1.0 deg/s.
**Note:** If rates exceed 1.0 deg/s, command `AOCS_SET_MODE(mode=2)` (DETUMBLE)
immediately for safe attitude, then continue with Step 3 from DETUMBLE mode.
**GO/NO-GO:** Wheel disabled, spacecraft rates bounded below 1.0 deg/s — proceed
to three-wheel reconfiguration.

### Step 3: Command Three-Wheel Control Mode
**Action:** Command AOCS to three-wheel mode, excluding the failed wheel:
`AOCS_3W_MODE(exclude_wheel=<N>)` (Service 8, Subtype 1, func_id 5)
where N is the disabled wheel index (0-3).
**Verify:** Wait 15 s for the control loop to reconfigure.
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)`
**Verify:** `aocs.att_error` (0x0217) — should begin converging. Target < 2.0 deg
within 60 s of reconfiguration.
**Verify:** Three remaining wheel speeds are adjusting — momentum is being
redistributed across the three operational wheels.
**Verify:** No remaining wheel speed exceeds 5000 RPM (saturation approach).
**Note:** Three-wheel mode provides slightly reduced pointing accuracy (typically
1.0-1.5 deg vs 0.5 deg for four-wheel) and reduced momentum storage capacity.
**GO/NO-GO:** Three-wheel mode active, attitude error converging below 2.0 deg —
proceed to momentum management.

### Step 4: Perform Momentum Dump with Magnetorquers
**Action:** Check momentum state of remaining wheels: `HK_REQUEST(sid=2)`
**Verify:** `aocs.rw1_speed` through `aocs.rw4_speed` (active wheels only) — if any
active wheel exceeds 3000 RPM, desaturation is required.
**Verify:** `aocs.mag_valid` (0x0230) = 1 — magnetometer must be operational for
magnetorquer-based momentum dumping.
**Action:** Command momentum dump: `AOCS_DESATURATE` (Service 8, Subtype 1, func_id 3)
**Verify:** Active wheel speeds trending toward 0 RPM over 120-300 s
**Verify:** `aocs.mtq_x` (0x020B), `aocs.mtq_y` (0x020C), `aocs.mtq_z` (0x020D)
— magnetorquers are generating dipole moments (non-zero values during dump)
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205),
`aocs.rate_yaw` (0x0206) — all < 0.5 deg/s during desaturation manoeuvre
**Verify:** No remaining wheel exceeds 5000 RPM during redistribution.
**Action:** Wait for desaturation to complete — all active wheels below 2000 RPM.
**Note:** With only three wheels, the momentum envelope is reduced. Desaturation
may need to be performed more frequently (nominally every 3-4 orbits instead of
every 6-8 orbits in four-wheel mode).
**GO/NO-GO:** Momentum dumped successfully — all active wheels < 2000 RPM, body
rates < 0.2 deg/s — proceed.

### Step 5: Verify Stable Pointing in Three-Wheel Mode
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` — sample every 60 s for
5 minutes.
**Verify:** `aocs.att_error` (0x0217) < 1.5 deg sustained (relaxed from nominal
1.0 deg tolerance for three-wheel operations)
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s
**Verify:** All active wheel temperatures < 55 deg-C
**Verify:** All active wheel speeds < 4000 RPM and stable
**Action:** If currently in DETUMBLE mode, attempt transition to NOMINAL (nadir):
`AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0)
**Verify:** `aocs.mode` (0x020F) = 4 (NOMINAL) within 15 s
**Verify:** `aocs.att_error` (0x0217) converges < 1.5 deg within 120 s
**GO/NO-GO:** Pointing performance acceptable for three-wheel mission operations —
proceed to post-recovery planning.

### Step 6: Establish Three-Wheel Operations Plan
**Action:** Confirm the disabled wheel index and document in the anomaly log.
**Action:** Update operational constraints for three-wheel mode:
- Pointing accuracy: relaxed to 1.5 deg (from 1.0 deg)
- Momentum dump frequency: every 3-4 orbits (increased from 6-8 orbits)
- Maximum slew rate: reduced by 25% to avoid wheel saturation during manoeuvres
**Action:** Notify mission planning:
- Payload imaging is available but with reduced pointing accuracy
- Agility manoeuvres (target-to-target slews) will take longer
- Momentum management windows must be scheduled more frequently
**Action:** Assess wheel recovery feasibility:
- Seizure (bearing lock): Generally not recoverable in orbit. Plan for permanent
  three-wheel operations.
- If temperature returns to normal after several orbits: Bearing may have partially
  freed. Recovery attempt requires ground authorisation (see Step 7).
**Verify:** Monitor disabled wheel temperature over next 2 orbits — if temp drops
below 45 deg-C, flag for potential recovery assessment.
**GO/NO-GO:** Three-wheel operations plan established — procedure complete.

### Step 7: Wheel Recovery Attempt (Ground-Authorised Only)
**Note:** This step should only be attempted after explicit authorisation from the
ground engineering team and at least 2 orbits of stable three-wheel operations.
**Condition:** Disabled wheel temperature has returned to < 45 deg-C, suggesting
the bearing may not be permanently seized.
**Action:** Re-enable the wheel at low speed: `AOCS_ENABLE_WHEEL(wheel_idx=N)` (Service 8, Subtype 1, func_id 3)
**Verify:** Wheel begins spinning up within 10 s
**Verify:** Wheel speed reaches commanded value smoothly (no oscillation, no stalling)
**Verify:** Wheel temperature does not rise more than 5 deg-C during spin-up
**Action:** Monitor for 5 minutes at commanded speed.
**Verify:** Speed is stable (no jitter > +/- 100 RPM)
**Verify:** Temperature is stable and < 50 deg-C
**Action:** If recovery successful, restore four-wheel configuration:
`SET_PARAM(param_id=aocs.wheel_config, value=4)` (Service 20, Subtype 1)
**Verify:** `aocs.att_error` (0x0217) improves to < 1.0 deg within 120 s
**Action:** If recovery fails (speed stalls, temperature spikes, oscillation):
Immediately disable wheel again: `AOCS_DISABLE_WHEEL(wheel_idx=N)` (Service 8, Subtype 1, func_id 2)
Maintain three-wheel operations permanently.
**GO/NO-GO:** If recovery successful — four-wheel ops restored. If failed — three-wheel
ops confirmed as permanent configuration.

## Verification Criteria
- [ ] Stuck/seized wheel identified (RW-1/2/3/4) and documented
- [ ] Failed wheel disabled and removed from control loop
- [ ] Three-wheel control mode confirmed active and stable
- [ ] `aocs.att_error` (0x0217) < 1.5 deg in NADIR_POINT (three-wheel)
- [ ] All active wheel speeds < 4000 RPM after desaturation
- [ ] All active wheel temperatures < 55 deg-C
- [ ] Momentum dump completed successfully
- [ ] Operations plan updated for three-wheel constraints
- [ ] Wheel recovery feasibility assessed and documented

## Off-Nominal Handling
- If a second wheel fails during this procedure: Command `AOCS_SET_MODE(mode=1)`
  (DETUMBLE) immediately. Two-wheel control does not provide three-axis pointing.
  Escalate to CON-002 Step 7 and EMG-005 (Loss of Attitude). This is mission-critical.
- If attitude error exceeds 10 deg during wheel disable: Command
  `AOCS_SET_MODE(mode=2)` (COARSE_SUN) immediately. Wait for rates to stabilise
  below 0.5 deg/s before attempting three-wheel mode.
- If desaturation is ineffective (wheels still > 4000 RPM after one attempt):
  The orbit geometry may not allow efficient momentum dumping at current position.
  Plan extended desaturation over the next 3 orbits using continuous magnetorquer
  control.
- If three-wheel pointing error exceeds 3.0 deg: Reduce payload imaging requirements
  and notify mission planning. The fourth wheel's loss may have exposed an
  asymmetric momentum bias that requires updated control parameters.
- If disabled wheel temperature continues to rise above 70 deg-C: Risk of thermal
  damage to adjacent components. Power off the wheel power line completely:
  `EPS_POWER_OFF(line_index=7)` (func_id 14) — note this disables ALL wheels on
  that power line. If wheels are on separate lines, isolate only the affected wheel
  via SET_PARAM.
- If magnetometer is not valid (needed for desaturation): Switch to redundant mag
  first — `MAG_SELECT(select=1)` (func_id 7). If both mags are invalid,
  desaturation via magnetorquers is not possible. Momentum will accumulate until
  wheel saturation forces safe mode.
