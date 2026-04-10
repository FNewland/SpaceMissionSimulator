# LEOP-002: Initial Health Assessment
**Subsystem:** All / OBDH
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Perform a comprehensive health assessment of all EOSAT-1 subsystems following first
acquisition. Request all housekeeping SIDs, verify power, thermal, AOCS, OBDH, and
TT&C subsystems are in nominal post-separation state. Establish baseline telemetry
values for the mission.

## Prerequisites
- [ ] LEOP-001 (First Acquisition) completed successfully
- [ ] Bidirectional VHF/UHF link active with link margin > 3 dB
- [ ] On-board time synchronized
- [ ] Minimum 8 minutes remaining in current ground station pass
- [ ] MCS telemetry archiving confirmed active

## Procedure Steps

### Step 1 — Request Full EPS Housekeeping
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [27.0V, 29.5V] within 10s
**Verify:** `eps.bat_voltage` (0x0100) in range [14.0V, 16.8V] within 10s
**Verify:** `eps.bat_soc` (0x0101) > 60% within 10s
**Verify:** `eps.power_gen` (0x0107) > 0W (confirms at least partial illumination) within 10s
**GO/NO-GO:** EPS parameters within nominal post-separation limits

### Step 2 — Request AOCS Housekeeping
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 1 (DETUMBLE) within 10s
**Verify:** `aocs.rate_roll` (0x0204) < 5.0 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 5.0 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 5.0 deg/s within 10s
**Verify:** `aocs.att_error` (0x0217) reported (value logged, no threshold at this phase)
**GO/NO-GO:** AOCS active in detumble, body rates below 5 deg/s per axis

### Step 3 — Request Thermal Housekeeping
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** `tcs.temp_obc` (0x0406) in range [-5C, +45C] within 10s
**Verify:** `tcs.temp_battery` (0x0407) in range [0C, +35C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) reported (payload unpowered, expect ambient) within 10s
**GO/NO-GO:** All thermal zones within survival limits

### Step 4 — Request OBDH Housekeeping
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) = 0 (SAFE) or 1 (NOMINAL) within 10s
**Verify:** `obdh.cpu_load` (0x0302) < 70% within 10s
**GO/NO-GO:** OBDH operating within nominal parameters

### Step 5 — Request TT&C Housekeeping
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 1 (LOCKED) within 10s
**Verify:** `ttc.rssi` (0x0502) > -105 dBm within 10s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB within 10s
**GO/NO-GO:** TT&C link nominal with adequate margin

### Step 6 — Request Payload Status Housekeeping
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Note:** Payload is SID 5 (TT&C is SID 6, beacon is SID 11). At cold boot the payload line is OFF, so this S3.27 is rejected by the line gate with error 0x0004 — that rejection is itself the confirmation that payload is dark. Once LEOP-007 powers the payload line and brings it to STANDBY, this step succeeds.
**Verify (post-LEOP-007):** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify (post-LEOP-007):** `payload.fpa_temp` (0x0601) reported within 10s
**GO/NO-GO:** Payload state matches current LEOP step

### Step 7 — Verify Battery Heater Operation
**Action:** If `tcs.temp_battery` (0x0407) < +5C, enable battery heater.
**TC:** `HEATER_CONTROL(circuit=1, on=1)` (Service 8, Subtype 1) — conditional
**Verify:** `tcs.temp_battery` (0x0407) trending upward within 120s if heater commanded
**GO/NO-GO:** Battery temperature managed within operational range [+5C, +35C]

### Step 8 — Verify Reaction Wheel Status
**Action:** Check individual reaction wheel speeds to confirm all four wheels responsive.
**Verify:** `aocs.rw1_speed` (0x0207) responding within 10s
**Verify:** `aocs.rw2_speed` (0x0208) responding within 10s
**Verify:** `aocs.rw3_speed` (0x0209) responding within 10s
**Verify:** `aocs.rw4_speed` (0x020A) responding within 10s
**GO/NO-GO:** All four reaction wheels reporting telemetry

### Step 9 — Compile Health Assessment Report
**Action:** Compare all received telemetry against post-separation expected values. Flag any parameters outside nominal range. Generate Health Assessment Summary for Flight Director.
**GO/NO-GO:** All subsystems nominal — clear to proceed with LEOP-003

## Off-Nominal Handling
- If `eps.bat_soc` < 50%: Defer non-essential operations. Prioritize sun acquisition (LEOP-004). Reduce TM rate to conserve power.
- If any body rate > 10 deg/s: Confirm detumble mode active. If rates not decreasing, verify magnetorquer operation via `GET_PARAM(0x020F)`. Escalate to AOCS engineer.
- If `tcs.temp_battery` < 0C: Immediately command `HEATER_CONTROL(circuit=1, on=1)`. Monitor for temperature rise. If no response, switch to redundant heater circuit.
- If `obdh.cpu_load` > 80%: Check for anomalous task scheduling. Consider reboot via `OBC_SET_MODE(mode=0)` only with Flight Director approval.
- If any HK SID fails to respond: Retry request twice. If still no response, check TM link quality. Log as anomaly for investigation.
- If `payload.mode` != 0 (OFF): Do not attempt to reconfigure payload during LEOP. Log anomaly and continue with platform health checks.

## Post-Conditions
- [ ] All six HK SIDs received and archived
- [ ] EPS: bus voltage, battery voltage, SOC, and power generation nominal
- [ ] AOCS: detumble mode active, body rates below limits
- [ ] TCS: all thermal zones within survival limits, heaters operational
- [ ] OBDH: OBC mode and CPU load nominal
- [ ] TT&C: link stable with adequate margin
- [ ] Payload: confirmed OFF
- [ ] Health Assessment Report distributed to mission team
- [ ] GO decision for LEOP-003 (Solar Array Verification)
