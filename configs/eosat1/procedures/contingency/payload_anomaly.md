# CON-003: Payload Thermal/FPA Anomaly Recovery
**Subsystem:** Payload / TCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover from a payload focal plane array (FPA) thermal anomaly where the FPA temperature
exceeds the operational limit of -15 deg-C or is rising at an unexpected rate (> 0.5 deg-C
per minute while the cooler is active). The procedure commands the payload OFF, verifies
cooler status, waits for thermal stabilisation, and restarts the payload if conditions permit.
An uncontrolled FPA temperature rise can degrade detector performance and risk permanent
damage above +25 deg-C.

## Prerequisites
- [ ] Payload telemetry (SID 6) is being received at >= 0.5 Hz
- [ ] TCS telemetry (SID 4) is being received confirming thermal sensor validity
- [ ] `payload.mode` (0x0600) is currently 1 (STANDBY) or 2 (IMAGING)
- [ ] EPS power budget has sufficient margin for cooler operation — `eps.bat_soc` (0x0101) > 30%

## Procedure Steps

### Step 1 — Confirm FPA Temperature Exceedance
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 25)
**Verify:** `payload.fpa_temp` (0x0601) — record current value
**Verify:** `tcs.temp_fpa` (0x0408) — cross-check with TCS sensor (values should agree within 2 deg-C)
**GO/NO-GO:** If FPA temp > -15 deg-C or rising > 0.5 deg-C/min, confirm anomaly and proceed

### Step 2 — Command Payload OFF
**TC:** `PAYLOAD_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10s
**Verify:** `eps.power_cons` (0x0106) decreases by >= 15W (payload electronics de-energised)
**GO/NO-GO:** Payload confirmed OFF — proceed to cooler assessment

### Step 3 — Verify Cooler Status and Power
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 25)
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 (active) — if 0, cooler has failed or been commanded off
**Verify:** `eps.power_gen` (0x0107) vs `eps.power_cons` (0x0106) — confirm positive power margin >= 10W for cooler
**GO/NO-GO:** If cooler is inactive, command `SET_PARAM(param_id=tcs.cooler_fpa, value=1)` and verify activation within 15s. If cooler cannot activate, escalate to ground for hardware investigation.

### Step 4 — Monitor Cooldown Period
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 25) — repeat every 60s for up to 20 minutes
**Verify:** `payload.fpa_temp` (0x0601) trending downward at >= 0.3 deg-C/min
**Verify:** `tcs.temp_fpa` (0x0408) consistent with payload sensor
**Verify:** `eps.bat_soc` (0x0101) remains > 25% during cooler operation
**GO/NO-GO:** FPA temperature must reach < -20 deg-C and stabilise (rate < 0.05 deg-C/min) before proceeding

### Step 5 — Restart Payload in Standby
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15s
**Verify:** `payload.fpa_temp` (0x0601) remains < -18 deg-C after payload electronics re-energise
**Verify:** `eps.power_cons` (0x0106) increase consistent with standby power draw (~8W)
**GO/NO-GO:** FPA temperature stable and payload in standby — proceed to imaging readiness check

### Step 6 — Validate Imaging Readiness
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 25) — wait 120s after standby entry
**Verify:** `payload.fpa_temp` (0x0601) < -20 deg-C and stable
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 (active, nominal current draw)
**GO/NO-GO:** If FPA temp stable below -20 deg-C, payload may be commanded to IMAGING for next scheduled acquisition. If temp is still rising, keep in STANDBY and escalate to ground.

## Off-Nominal Handling
- If FPA temp exceeds +10 deg-C at any step: Command `PAYLOAD_SET_MODE(mode=0)` immediately and flag for hardware review — do not re-attempt restart without ground authorisation
- If cooler fails to activate in Step 3: Maintain payload OFF, execute CON-006 (Thermal Limit Exceedance) for zone assessment
- If EPS SoC drops below 20% during cooldown: Execute CON-002 (EPS Safe Mode Recovery) — cooler power may be interrupted
- If payload and TCS FPA sensors disagree by > 5 deg-C: Suspect sensor failure, use the lower reading for safety decisions

## Post-Conditions
- [ ] `payload.fpa_temp` (0x0601) < -20 deg-C and stable
- [ ] `payload.mode` (0x0600) >= 1 (STANDBY or IMAGING)
- [ ] `tcs.cooler_fpa` (0x040C) = 1 (active)
- [ ] Anomaly report filed with thermal trend data and root cause assessment
- [ ] Next imaging opportunity confirmed or deferred pending thermal review

## Recovery Path — Re-Powering Shed Loads
After this procedure has stabilised the bus and the root cause is understood, **any subsystems whose power lines were de-energised during load shed must be brought back up in the controlled order defined by LEOP-007 (Sequential Power-On)**. Do **not** simply re-enable lines ad-hoc: LEOP-007 enforces the correct subsystem→line→mode sequencing, the inrush spacing, the thermal pre-conditioning of the OBC/battery heaters, and the AOCS/payload set_mode exemptions to the two-stage TC power gate. Cross-reference: `configs/eosat1/procedures/leop/sequential_power_on.md` (PROC-LEOP-007).
