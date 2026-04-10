# CON-021: ADCS Sensor Failure Detection and Recovery
**Subsystem:** AOCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Respond to the failure of one or more AOCS sensors (star trackers, coarse sun sensor
heads, magnetometers). EOSAT-1 carries redundant attitude sensors: two star trackers
(ST1/ST2), six coarse sun sensor heads (+X, -X, +Y, -Y, +Z, -Z), and two
magnetometers (MAG-A, MAG-B). This procedure provides a systematic approach to
detecting which sensor has failed, switching to redundant units where available,
assessing cumulative impact on attitude determination capability, and deciding
whether the remaining sensor suite supports mission operations or requires fallback
to a degraded pointing mode.

This procedure is particularly relevant for cascading failures where multiple sensors
fail in sequence. Each failure is handled independently, but the cumulative effect
must be evaluated after each recovery action.

## Prerequisites
- [ ] AOCS telemetry (SID 2) is being received at >= 1 Hz
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Flight Director notified of attitude sensor anomaly
- [ ] Spacecraft bus power is nominal — `eps.bus_voltage` (0x0105) > 27.0 V
- [ ] Procedure CON-002 (AOCS Anomaly Recovery) has been reviewed for escalation paths
- [ ] Procedure CON-008 (Star Tracker Failure) has been reviewed for ST-specific steps

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| aocs.st1_status | 0x0240 | 2 (TRACKING) or 4 (FAILED) |
| aocs.st1_num_stars | 0x0241 | > 3 when tracking |
| aocs.st2_status | 0x0243 | 0 (OFF), 1 (BOOT), 2 (TRACKING), or 4 (FAILED) |
| aocs.mode | 0x020F | Current AOCS mode |
| aocs.att_error | 0x0217 | Monitor — increases with sensor loss |
| aocs.rate_roll | 0x0204 | Body roll rate |
| aocs.rate_pitch | 0x0205 | Body pitch rate |
| aocs.rate_yaw | 0x0206 | Body yaw rate |
| aocs.css_valid | 0x0248 | CSS sun vector validity |
| aocs.css_px | 0x027A | CSS +X face illumination |
| aocs.css_mx | 0x027B | CSS -X face illumination |
| aocs.css_py | 0x027C | CSS +Y face illumination |
| aocs.css_my | 0x027D | CSS -Y face illumination |
| aocs.css_pz | 0x027E | CSS +Z face illumination |
| aocs.css_mz | 0x027F | CSS -Z face illumination |
| aocs.mag_a_x | 0x0223 | Magnetometer A X-axis |
| aocs.mag_a_y | 0x0224 | Magnetometer A Y-axis |
| aocs.mag_a_z | 0x0225 | Magnetometer A Z-axis |
| aocs.mag_b_x | 0x0226 | Magnetometer B X-axis |
| aocs.mag_b_y | 0x0227 | Magnetometer B Y-axis |
| aocs.mag_b_z | 0x0228 | Magnetometer B Z-axis |
| aocs.mag_select | 0x0229 | Active magnetometer (0=A, 1=B) |
| aocs.mag_valid | 0x0230 | Magnetometer validity flag |
| aocs.sun_body_x | 0x0220 | Sun vector body X (from CSS composite) |
| aocs.sun_body_y | 0x0221 | Sun vector body Y (from CSS composite) |
| aocs.sun_body_z | 0x0222 | Sun vector body Z (from CSS composite) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| ST2_POWER | 8 | 1 | 5 | Star tracker 2 power on/off |
| ST_SELECT | 8 | 1 | 6 | Select primary star tracker |
| ST1_POWER | 8 | 1 | 4 | Star tracker 1 power on/off |
| MAG_SELECT | 8 | 1 | 7 | Select active magnetometer source |
| AOCS_SET_MODE | 8 | 1 | 0 | Set AOCS mode |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Set payload mode (for safe mode entry) |

## Procedure Steps

### Step 1: Initial Sensor Health Assessment
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.st1_status` (0x0240) — record status (0=OFF, 1=BOOT, 2=TRACKING, 3=BLIND, 4=FAILED)
**Verify:** `aocs.st1_num_stars` (0x0241) — record tracked star count
**Verify:** `aocs.st2_status` (0x0243) — record status
**Verify:** `aocs.css_valid` (0x0248) — record CSS validity (1=valid)
**Verify:** `aocs.mag_select` (0x0229) — record active magnetometer (0=A, 1=B)
**Verify:** `aocs.mag_valid` (0x0230) — record magnetometer validity
**Verify:** `aocs.mode` (0x020F) — record current AOCS mode
**Verify:** `aocs.att_error` (0x0217) — record current attitude error
**Action:** Document all sensor states to establish which sensors are healthy and
which have failed or are degraded.
**GO/NO-GO:** Sensor health baseline established — proceed to specific failure handling.

### Step 2: Star Tracker Failure Response
**Condition:** `aocs.st1_status` (0x0240) = 4 (FAILED) or `aocs.st1_num_stars` (0x0241) = 0

**Step 2a — Verify ST2 Availability:**
**Verify:** `aocs.st2_status` (0x0243) — check if ST2 is already active
- If ST2 = 2 (TRACKING): FDIR has auto-switched. Proceed to Step 2c.
- If ST2 = 0 (OFF): Must manually power on. Proceed to Step 2b.
- If ST2 = 4 (FAILED): Both STs failed. Skip to Step 5.

**Step 2b — Power On and Select ST2:**
**Action:** Command ST2 power on: `ST2_POWER(on=1)` (func_id 5)
**Action:** Wait 60 s for boot sequence.
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.st2_status` (0x0243) transitions to 2 (TRACKING) within 120 s
**Action:** If TRACKING confirmed, select ST2 as primary: `ST_SELECT(unit=1)` (func_id 6)
**Verify:** `aocs.att_error` (0x0217) begins decreasing within 30 s

**Step 2c — Isolate Failed ST1:**
**Action:** Power off ST1: `ST1_POWER(on=0)` (func_id 4)
**Verify:** `aocs.st1_status` (0x0240) = 0 (OFF) within 5 s
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg with ST2 providing attitude
**Action:** Log: "ST1 FAILED — operating on ST2 only. No star tracker redundancy."
**GO/NO-GO:** ST2 operational and providing valid attitude — proceed to next
failure if applicable. If ST2 did not reach TRACKING, proceed to Step 5.

### Step 3: CSS Head Failure Response
**Condition:** One or more CSS heads showing anomalous readings

**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** Check each CSS head illumination value:
- `aocs.css_px` (0x027A) — +X face
- `aocs.css_mx` (0x027B) — -X face
- `aocs.css_py` (0x027C) — +Y face
- `aocs.css_my` (0x027D) — -Y face
- `aocs.css_pz` (0x027E) — +Z face
- `aocs.css_mz` (0x027F) — -Z face

**Action:** For each illuminated face (face with expected sun exposure based on
current attitude), verify CSS reading is reasonable:
- A reading of 0.0 on a face that should be illuminated indicates a failed head
- A reading that is erratic or stuck indicates a degraded head

**Action:** Document which CSS head(s) have failed and the affected face(s).
**Verify:** `aocs.css_valid` (0x0248) — still 1 if composite sun vector can be computed
from remaining heads.

**Assessment of CSS head loss impact:**
- 1 head lost: Composite sun vector is degraded but usable. Sun vector accuracy
  reduced to approximately 5-10 degrees for specific attitude ranges.
- 2 heads lost on opposite faces (e.g., +X and -X): Sun vector determination for
  that axis is lost. COARSE_SUN mode will have reduced accuracy.
- 3+ heads lost: CSS may be invalid. Verify `aocs.css_valid` (0x0248). If invalid
  (= 0), sun pointing is not possible in eclipse-exit phase.

**Note:** CSS head failure does not affect fine pointing (ST-based modes) but will
degrade safe mode performance (COARSE_SUN relies on CSS).
**GO/NO-GO:** CSS degradation assessed. If css_valid = 1, proceed. If css_valid = 0
and both STs are failed, proceed to Step 5.

### Step 4: Magnetometer Failure Response
**Condition:** `aocs.mag_valid` (0x0230) = 0 or magnetometer readings are invalid

**Step 4a — Identify Failed Magnetometer:**
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mag_select` (0x0229) — identify which magnetometer is currently active
**Verify:** `aocs.mag_a_x` (0x0223), `aocs.mag_a_y` (0x0224), `aocs.mag_a_z` (0x0225)
— check MAG-A readings. If all near zero or NaN, MAG-A has failed.
**Verify:** `aocs.mag_b_x` (0x0226), `aocs.mag_b_y` (0x0227), `aocs.mag_b_z` (0x0228)
— check MAG-B readings.
**Verify:** `aocs.mag_field_total` (0x0277) — should be approximately 25000-65000 nT
for LEO. If near zero, the active magnetometer has failed.
**Action:** Determine which magnetometer is failed:
- If active is A (select=0) and readings invalid: MAG-A failed, switch to MAG-B
- If active is B (select=1) and readings invalid: MAG-B failed, switch to MAG-A

**Step 4b — Switch to Redundant Magnetometer:**
**Action:** Command magnetometer switch: `MAG_SELECT(select=1)` (func_id 7) for MAG-B,
or `MAG_SELECT(select=0)` for MAG-A.
**Verify:** `aocs.mag_select` (0x0229) reflects the new selection within 5 s
**Action:** Wait 10 s for new magnetometer readings to stabilise.
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)`
**Verify:** `aocs.mag_valid` (0x0230) = 1 — magnetometer is now providing valid data
**Verify:** `aocs.mag_field_total` (0x0277) — in expected range (25000-65000 nT)
**Action:** Log: "MAG-[A/B] FAILED — switched to MAG-[B/A]. No magnetometer redundancy."

**Note:** Magnetometer data is used for:
- Magnetic field model updates for attitude determination
- Magnetorquer control (momentum dumping, detumble mode)
- If both magnetometers fail, detumble mode and momentum management are severely impaired.

**GO/NO-GO:** Redundant magnetometer active and providing valid data — proceed to
cumulative assessment.

### Step 5: Cumulative Sensor Assessment and Mode Decision
**Action:** Request AOCS housekeeping: `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Action:** Evaluate the remaining sensor suite against mode requirements:

**Fine Pointing (NOMINAL mode=4, nadir pointing) requires:**
- At least one star tracker tracking (ST1 or ST2 status = 2)
- Gyroscope operational (bias-corrected rates available)
- Magnetometer valid (for periodic desaturation)
- If all requirements met: Fine pointing is available.

**Coarse Sun Pointing (COARSE_SUN, mode=3) requires:**
- CSS valid (at least 3-4 CSS heads providing composite sun vector)
- Magnetometer valid (for attitude estimation supplement)
- If met: Coarse pointing is available but payload imaging is NOT possible.

**Detumble Only (DETUMBLE, mode=2) requires:**
- Magnetometer valid (for B-dot control law)
- If met: Rate damping is possible. No pointing capability.

**Action:** Based on remaining sensors, select the best available mode:
1. If ST available: Maintain or return to NOMINAL — `AOCS_SET_MODE(mode=4)`
2. If no ST but CSS valid: Transition to COARSE_SUN — `AOCS_SET_MODE(mode=3)`
3. If no ST and CSS invalid: Transition to DETUMBLE — `AOCS_SET_MODE(mode=2)`

**Verify:** `aocs.mode` (0x020F) = commanded mode within 15 s
**Verify:** `aocs.att_error` (0x0217) — acceptable for selected mode:
- NOMINAL: < 1.0 deg
- COARSE_SUN: < 5.0 deg
- DETUMBLE: rates < 1.0 deg/s

**Action:** If degraded to COARSE_SUN or DETUMBLE, disable payload:
`PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1, func_id 20)

**GO/NO-GO:** Best available mode established and stable. If degraded below
NOMINAL, notify Flight Director and mission planning of reduced capability.

### Step 6: Post-Recovery Monitoring
**Action:** Monitor AOCS telemetry at 60 s intervals for 10 minutes: `HK_REQUEST(sid=2)`
**Verify:** `aocs.att_error` (0x0217) — stable or improving
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s (for pointing modes)
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s
**Verify:** Active star tracker (if available) continues tracking — `aocs.st*_num_stars` > 3
**Verify:** Active magnetometer continues providing valid data
**Action:** Document all sensor states for the anomaly report:
- Which sensors failed and at what time
- Recovery actions taken
- Current operating mode and degradation level
- Remaining sensor redundancy status
**GO/NO-GO:** AOCS stable in selected mode for 10 minutes — procedure complete.

## Verification Criteria
- [ ] All failed sensors identified and documented
- [ ] Redundant sensors activated where available (ST2, MAG-B)
- [ ] Failed sensors isolated (powered off) to prevent interference
- [ ] AOCS operating in best available mode for remaining sensor suite
- [ ] `aocs.att_error` (0x0217) within acceptable limits for current mode
- [ ] Body rates within limits for current mode
- [ ] Remaining sensor redundancy status documented
- [ ] No FDIR safe mode triggers during recovery

## Off-Nominal Handling
- If both star trackers fail: CSS is the only attitude reference in sunlit phase.
  Command `AOCS_SET_MODE(mode=3)` (COARSE_SUN). Payload imaging is not possible.
  This is a mission-degrading configuration — escalate to engineering team.
- If ST2 does not reach TRACKING within 120 s: Power cycle ST2 — `ST2_POWER(on=0)`,
  wait 10 s, `ST2_POWER(on=1)`. If still fails, treat as dual ST failure.
- If both magnetometers fail: Detumble mode is not possible (B-dot requires mag data).
  Reaction wheels can still provide attitude control if operational. Momentum cannot
  be dumped via magnetorquers. This is critical — plan for graceful momentum build-up.
- If CSS and both magnetometers fail in eclipse: No attitude reference available.
  Spacecraft is uncontrolled until eclipse exit. Wait for sunlit phase — CSS should
  acquire sun vector if heads are functional. Command `AOCS_SET_MODE(mode=2)` in
  advance so DETUMBLE engages automatically when mag data returns.
- If attitude error exceeds 10 deg during sensor switching: FDIR may trigger
  autonomous safe mode. Allow safe mode entry, then recover using CON-002
  (AOCS Anomaly Recovery).
- If power budget does not support redundant sensor power-on: Check
  `eps.bat_soc` (0x0101). If < 40%, do not power both star trackers simultaneously.
  Operate on a single ST until battery is sufficient.
