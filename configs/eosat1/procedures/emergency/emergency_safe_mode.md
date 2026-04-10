# PROC-EMG-004: Emergency Safe Mode Entry and Recovery
**Subsystem:** OBDH / All Subsystems
**Phase:** EMERGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Master procedure for EOSAT-1 emergency safe mode entry when multiple concurrent anomalies are
detected or the spacecraft has autonomously transitioned to EMERGENCY mode. This procedure
coordinates OBC emergency mode commanding, verifies automatic load shedding, confirms safe
sun-pointing attitude, establishes communication, and provides an orbit-by-orbit recovery
timeline back to nominal operations.

## Prerequisites
- [ ] Multiple anomaly indicators present OR `obdh.mode` (0x0300) = EMERGENCY (2) confirmed
- [ ] Flight Director has declared spacecraft emergency
- [ ] Emergency Response Team fully staffed (Power, AOCS, TT&C, Thermal, Payload engineers)
- [ ] All ground stations alerted for priority tracking
- [ ] Anomaly log initiated with timeline of events

## Trigger Conditions (Any Two or More)
- `eps.bat_soc` (0x0101) < 15% --- low power
- `aocs.att_error` (0x0217) > 30 deg --- attitude anomaly
- Any body rate > 2 deg/s --- tumbling
- `obdh.reboot_count` (0x030A) incremented by >= 2 in 1 hour --- OBC instability
- `tcs.temp_battery` (0x0407) outside range -10 degC to +45 degC --- thermal violation
- `ttc.link_status` (0x0501) = UNLOCKED for > 1 pass --- communication degraded
- `obdh.mode` (0x0300) = EMERGENCY (2) --- autonomous transition detected

## Procedure Steps

### Step 1 --- Command Emergency Mode (If Not Already Autonomous)
**TC:** `OBC_SET_MODE(mode=2)` (Service 8, Subtype 3) --- EMERGENCY mode.
**Verify:** `obdh.mode` (0x0300) = EMERGENCY (2) within 5s.
**Effect:** Onboard software executes automatic load shedding sequence, reduces telemetry rate,
disables all scheduled operations, and enables survival-only power distribution.
**GO/NO-GO:** OBC emergency mode confirmed. If OBC not responding, proceed with manual commanding at Step 2.

### Step 2 --- Verify Automatic Load Shedding
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- full EPS housekeeping.
**Verify:** `payload.mode` (0x0600) = OFF.
**Verify:** `eps.power_cons` (0x0106) < 50W.
**Action:** If any non-essential load still active, command manually:
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 5) --- payload OFF (if still on).
**TC:** `HEATER_CONTROL(circuit=obc, on=false)` (Service 8, Subtype 7) --- OBC heater OFF (if needed).
**TC:** `HEATER_CONTROL(circuit=thruster, on=false)` (Service 8, Subtype 7) --- thruster heater OFF.
**Verify:** `eps.power_cons` (0x0106) < 50W within 15s of manual commanding.
**GO/NO-GO:** Total spacecraft power consumption confirmed below 50W.

### Step 3 --- Verify AOCS Safe-Pointing
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 1) --- AOCS housekeeping.
**Check:** `aocs.mode` (0x020F) --- should be SAFE_POINT (2) or DETUMBLE (1).
**If SAFE_POINT:** Verify `aocs.att_error` (0x0217) < 10 deg. Proceed to Step 4.
**If DETUMBLE:** Monitor rates. If all rates < 0.1 deg/s, command SAFE_POINT:
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 4) --- SAFE_POINT.
**If tumbling (rates > 2 deg/s):** Execute PROC-EMG-002 before continuing.
**If AOCS OFF:** Command detumble first:
**TC:** `AOCS_SET_MODE(mode=1)` (Service 8, Subtype 4) --- DETUMBLE.
**GO/NO-GO:** AOCS in SAFE_POINT or actively detumbling with rates decreasing.

### Step 4 --- Verify Sun Acquisition and Power State
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg (sun-pointing achieved).
**Verify:** `eps.sa_a_current` (0x0103) > 0.5A --- solar array A illuminated.
**Verify:** `eps.sa_b_current` (0x0104) > 0.5A --- solar array B illuminated.
**Verify:** `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106) --- power-positive.
**Verify:** `eps.bat_soc` (0x0101) --- record value and start trend tracking.
**TC:** `HEATER_CONTROL(circuit=battery, on=true)` (Service 8, Subtype 7) --- ensure battery thermostat active.
**GO/NO-GO:** Power-positive state confirmed. If not, execute PROC-EMG-003.

### Step 5 --- Establish Stable Communication
**Verify:** `ttc.link_status` (0x0501) = LOCKED.
**Verify:** `ttc.rssi` (0x0502) > -120 dBm.
**Verify:** `ttc.mode` (0x0500) = NOMINAL or SAFE.
**Action:** If link marginal, schedule subsequent passes at high-elevation (> 20 deg) ground stations only.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 1) --- TT&C subsystem housekeeping.
**GO/NO-GO:** Two-way communication stable. If unstable, reference PROC-EMG-001.

### Step 6 --- Full Housekeeping Assessment
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- EPS HK.
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 1) --- AOCS HK.
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 1) --- OBDH HK.
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 1) --- TCS HK.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 1) --- TT&C HK.
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 1) --- Payload HK.
**Action:** Engineering team reviews all parameters against nominal ranges.
**Record:** `obdh.reboot_count` (0x030A) --- note any unexpected increments.
**Record:** All temperatures: `tcs.temp_obc` (0x0406), `tcs.temp_battery` (0x0407).
**Record:** `eps.bat_voltage` (0x0100), `eps.bus_voltage` (0x0105) --- full power state.
**GO/NO-GO:** Anomaly Review Board convenes to assess data and approve recovery plan.

### Step 7 --- Stabilisation Hold (Minimum 3 Orbits)
**Duration:** Maintain emergency safe mode for minimum 3 orbits (~4.75 hours).
**Monitor:** All critical parameters on every ground station pass.
**Confirm:** No new anomalies, no additional OBC reboots, temperatures stable, power trending positive.
**Verify:** `eps.bat_soc` (0x0101) > 25% by end of stabilisation period.
**Action:** Engineering team completes root cause analysis of triggering anomalies.
**GO/NO-GO:** Flight Director approves transition to staged recovery.

## Orbit-by-Orbit Recovery Timeline

### Recovery Orbit R+1 --- Restore Thermal Control (SoC > 30% Required)
**TC:** `HEATER_CONTROL(circuit=obc, on=true)` (Service 8, Subtype 7) --- restore OBC heater.
**Verify:** `tcs.temp_obc` (0x0406) trending toward nominal range (-10 degC to +40 degC).
**Verify:** `eps.power_cons` (0x0106) increase < 10W. Spacecraft remains power-positive.
**GO/NO-GO:** Thermal subsystem nominal, power budget accommodates added load.

### Recovery Orbit R+2 --- Restore Full AOCS (SoC > 40% Required)
**TC:** `OBC_SET_MODE(mode=1)` (Service 8, Subtype 3) --- OBC SAFE mode (from EMERGENCY).
**Verify:** `obdh.mode` (0x0300) = SAFE (1) within 5s.
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 4) --- NADIR_POINT mode.
**Verify:** `aocs.mode` (0x020F) = NADIR_POINT (3) within 5s.
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 20 minutes.
**Verify:** `aocs.rw1_speed` (0x0207) through `aocs.rw4_speed` (0x020A) all within limits.
**GO/NO-GO:** Full attitude control restored, all 4 wheels operational.

### Recovery Orbit R+3 --- Restore Thruster Heater and Full Thermal (SoC > 50% Required)
**TC:** `HEATER_CONTROL(circuit=thruster, on=true)` (Service 8, Subtype 7) --- restore thruster heater.
**Verify:** `eps.power_cons` (0x0106) within nominal budget (< 90W).
**Verify:** All temperatures within nominal ranges.
**GO/NO-GO:** Full thermal control restored, power margin adequate.

### Recovery Orbit R+4 --- Restore OBC Nominal Mode (SoC > 55% Required)
**TC:** `OBC_SET_MODE(mode=0)` (Service 8, Subtype 3) --- OBC NOMINAL mode.
**Verify:** `obdh.mode` (0x0300) = NOMINAL (0) within 5s.
**Verify:** Onboard scheduling resumed, housekeeping at nominal rate.
**Verify:** No anomalous reboot counter increments: `obdh.reboot_count` (0x030A) stable.
**GO/NO-GO:** OBC nominal operations confirmed.

### Recovery Orbit R+5 --- Restore Payload (SoC > 60% Required)
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 5) --- payload STANDBY.
**Verify:** `payload.mode` (0x0600) = STANDBY (1) within 10s.
**Verify:** `eps.power_cons` (0x0106) within full operational budget (< 110W).
**Verify:** Spacecraft remains power-positive across full orbit including eclipse.
**GO/NO-GO:** Full nominal operations restored. Close emergency and resume mission timeline.

## Recovery Timeline Summary
| Phase | Orbit | SoC Gate | Duration from Emergency | Key Action |
|---|---|---|---|---|
| Emergency hold | R+0 to R+3 | -- | 0 to 4.75h | Stabilise, assess, plan |
| Thermal restore | R+1 recovery | > 30% | ~6.3h | OBC heater ON |
| AOCS restore | R+2 recovery | > 40% | ~7.9h | Nadir pointing |
| Full thermal | R+3 recovery | > 50% | ~9.5h | Thruster heater ON |
| OBC nominal | R+4 recovery | > 55% | ~11.1h | Resume scheduling |
| Payload standby | R+5 recovery | > 60% | ~12.6h | Mission resumed |

## Off-Nominal Handling
- If OBC does not accept mode commands: attempt OBC reset via `HK_REQUEST(sid=3)` diagnostic, then re-command. If persistent, spacecraft remains in autonomous emergency mode until next OBC reboot cycle.
- If multiple wheels failed during anomaly: remain in SAFE_POINT indefinitely. Nadir pointing requires minimum 3 wheels. Plan reduced-capability operations.
- If SoC does not reach 30% within 10 orbits: possible solar array degradation. Reassess power budget for long-term reduced operations. Reference PROC-EMG-003.
- If new anomaly occurs during recovery: halt recovery, return to Step 7 stabilisation hold, re-assess before continuing.
- If `obdh.reboot_count` (0x030A) continues incrementing: suspect OBC hardware fault. Maintain emergency mode, reduce commanding to minimum, plan OBC memory patch or cold redundancy switch if available.

## Post-Conditions
- [ ] `obdh.mode` (0x0300) = NOMINAL (0)
- [ ] `aocs.mode` (0x020F) = NADIR_POINT (3) with `aocs.att_error` (0x0217) < 1.0 deg
- [ ] `eps.bat_soc` (0x0101) > 60% and stable
- [ ] `eps.bus_voltage` (0x0105) > 28.0V
- [ ] All heaters operational, temperatures within nominal ranges
- [ ] `payload.mode` (0x0600) = STANDBY (1) or higher
- [ ] `ttc.link_status` (0x0501) = LOCKED on scheduled passes
- [ ] All anomaly root causes identified or formal investigation open
- [ ] Flight Director has formally closed the emergency declaration
- [ ] Lessons learned documented and procedure updates initiated if required

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).
