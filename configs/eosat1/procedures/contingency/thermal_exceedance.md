# CON-006: Thermal Limit Exceedance Recovery
**Subsystem:** TCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Respond to a thermal limit exceedance in any monitored zone of the EOSAT-1 spacecraft.
Yellow limits indicate the parameter is approaching the operational boundary and corrective
action is required to prevent hardware damage. This procedure identifies the affected zone,
applies appropriate thermal control actions (heater activation for cold cases, load shedding
for hot cases), and monitors the temperature trend until the zone returns within nominal range.

## Prerequisites
- [ ] TCS telemetry (SID 4) is being received at >= 0.5 Hz
- [ ] Thermal limit database is available on console (ref: EOSAT1-TCS-LIM-001)
- [ ] EPS has sufficient power margin for heater activation — `eps.bat_soc` (0x0101) > 25%
- [ ] Current orbit beta angle and eclipse duration are known from Flight Dynamics

### Thermal Yellow Limits Reference
| Zone        | Parameter           | TM ID  | Cold Yellow | Hot Yellow |
|-------------|---------------------|--------|-------------|------------|
| OBC         | `tcs.temp_obc`      | 0x0406 | -5 deg-C    | +55 deg-C  |
| Battery     | `tcs.temp_battery`  | 0x0407 | +2 deg-C    | +42 deg-C  |
| FPA         | `tcs.temp_fpa`      | 0x0408 | -45 deg-C   | -15 deg-C  |

## Procedure Steps

### Step 1 — Identify Affected Thermal Zone
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 25)
**Verify:** `tcs.temp_obc` (0x0406) — record value, check against limits
**Verify:** `tcs.temp_battery` (0x0407) — record value, check against limits
**Verify:** `tcs.temp_fpa` (0x0408) — record value, check against limits
**Action:** Identify which zone(s) are in yellow-limit violation and whether the exceedance is hot or cold
**GO/NO-GO:** At least one zone confirmed in yellow — proceed to appropriate branch

### Step 2A — Cold Case: Enable Heater for Affected Zone
**Condition:** Zone temperature is below cold yellow limit
**TC (Battery cold):** `HEATER_CONTROL(circuit=battery, on=true)` (Service 8, Subtype 1)
**Verify:** `tcs.htr_battery` (0x040A) = 1 (ON) within 5s
**TC (OBC cold):** `HEATER_CONTROL(circuit=obc, on=true)` (Service 8, Subtype 1)
**Verify:** `tcs.htr_obc` (0x040B) = 1 (ON) within 5s
**Verify:** `eps.power_cons` (0x0106) increases by expected heater draw (3-7W per circuit)
**GO/NO-GO:** Heater confirmed active and power budget can sustain — proceed to monitoring

### Step 2B — Hot Case: Reduce Thermal Load in Affected Zone
**Condition:** Zone temperature is above hot yellow limit
**TC (FPA hot):** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1) — remove electronics heat load
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**TC (OBC hot):** `SET_PARAM(param_id=obdh.task_shed, value=1)` — reduce CPU load to lower dissipation
**Verify:** `obdh.cpu_load` (0x0302) decreases within 30s
**Action (Battery hot):** Verify `tcs.htr_battery` (0x040A) = 0 (OFF); if ON, command `HEATER_CONTROL(circuit=battery, on=false)`
**GO/NO-GO:** Load reduction actions confirmed — proceed to monitoring

### Step 3 — Monitor Temperature Trend
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 25) — repeat every 60s for 15 minutes
**Verify:** Affected zone temperature trending toward nominal range at >= 0.2 deg-C/min
**Verify:** No other zones drifting into yellow limits as a secondary effect
**Verify:** `eps.bat_soc` (0x0101) > 20% — heater operation is not depleting battery excessively
**GO/NO-GO:** Temperature trending nominal — continue monitoring. If no improvement after 15 min, proceed to Step 4.

### Step 4 — Adjust Heater Setpoints or Escalate
**TC (Cold case):** `SET_PARAM(param_id=tcs.htr_<zone>_setpoint, value=<+5 from current>)` (Service 20, Subtype 1)
**Verify:** Heater duty cycle increases (more frequent ON cycles observed in telemetry)
**TC (Hot case):** If temperature still rising, command `AOCS_SET_MODE(mode=2)` for SAFE_POINT to optimise spacecraft attitude for thermal dissipation
**Verify:** `aocs.mode` (0x020F) = 2 within 15s
**GO/NO-GO:** If temperature stabilises within additional 15 min, proceed to Step 5. If red limit approached, escalate to EMG-002.

### Step 5 — Confirm Recovery and Restore Operations
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 25)
**Verify:** All zone temperatures within nominal (green) range
**Action:** If heaters were enabled for cold case, set to automatic thermostat mode via `SET_PARAM(param_id=tcs.htr_<zone>_auto, value=1)`
**Action:** If loads were shed for hot case, re-enable per CON-002 load restoration sequence
**GO/NO-GO:** All temperatures nominal and operations restored — recovery complete

## Off-Nominal Handling
- If battery temperature exceeds +45 deg-C (red limit): Command `OBC_SET_MODE(mode=2)` for EMERGENCY — execute EMG-002
- If OBC temperature exceeds +60 deg-C: Command `OBC_SET_MODE(mode=1)` for SAFE immediately
- If heater circuit fails to activate: Suspect heater hardware failure, log anomaly, adjust attitude for passive thermal control
- If multiple zones simultaneously exceed limits: Suspect orbital geometry change or AOCS anomaly — check `aocs.mode` (0x020F) and execute CON-001 if attitude is off-nominal

## Post-Conditions
- [ ] All thermal zones within nominal (green) limits
- [ ] Heater states documented (auto/manual/off for each circuit)
- [ ] Power budget re-assessed with any new heater loads
- [ ] Anomaly report filed with thermal trend plots and root cause hypothesis

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).
