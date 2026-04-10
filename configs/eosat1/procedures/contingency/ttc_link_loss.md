# CON-004: TT&C Link Loss Recovery
**Subsystem:** TT&C
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover communications with the EOSAT-1 spacecraft when the TT&C link is lost during
a predicted acquisition-of-signal (AOS) window. Link loss is confirmed when
`ttc.link_status` = 0 persists for more than 30 seconds after predicted AOS, or when an
established link drops unexpectedly mid-pass. This procedure attempts transponder switching
and link restoration before escalating to emergency autonomous operations.

## Prerequisites
- [ ] Ground station antenna is confirmed tracking the spacecraft with valid pointing
- [ ] Orbit prediction is current (propagated within last 6 hours) and AOS/LOS times are valid
- [ ] Ground station RF chain has been verified nominal (uplink power, frequency, polarisation)
- [ ] At least 5 minutes of pass time remaining for recovery attempt
- [ ] Emergency procedure EMG-001 is available for escalation

## Procedure Steps

### Step 1 — Confirm Link Loss Is Not a Ground Issue
**Action:** Verify ground station status — antenna tracking, uplink transmitter power, receiver lock
**Action:** Confirm Doppler pre-compensation is applied correctly for current pass
**Action:** Check for local RFI or weather-related attenuation at the ground station
**GO/NO-GO:** If ground station anomaly identified, resolve locally. If ground confirmed nominal, proceed with spacecraft-side recovery.

### Step 2 — Attempt Re-Acquisition on Primary Transponder
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 25) — blind command, no telemetry expected
**Action:** Wait 30s for transponder to process and attempt downlink re-acquisition
**Verify:** `ttc.link_status` (0x0501) = 1 within 30s
**Verify:** `ttc.rssi` (0x0502) > -95 dBm if link restores
**GO/NO-GO:** If link restores, proceed to Step 5 for health check. If no response, proceed to Step 3.

### Step 3 — Switch to Redundant Transponder
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1) — blind command
**Action:** Wait 45s for redundant transponder power-up and frequency lock
**Verify:** `ttc.link_status` (0x0501) = 1 within 45s
**Verify:** `ttc.mode` (0x0500) indicates redundant unit active
**GO/NO-GO:** If link restores on redundant, proceed to Step 5. If no response, proceed to Step 4.

### Step 4 — Second Attempt: Retry Primary Transponder
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1) — blind command
**Action:** Wait 45s for primary transponder re-initialisation
**Verify:** `ttc.link_status` (0x0501) = 1 within 45s
**GO/NO-GO:** If link restores, proceed to Step 5. If still no link after both transponders attempted, proceed to Step 6 (Escalation).

### Step 5 — Verify Link Quality and Subsystem Health
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 25)
**Verify:** `ttc.link_status` (0x0501) = 1
**Verify:** `ttc.rssi` (0x0502) > -90 dBm (nominal signal strength)
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB
**Verify:** `ttc.mode` (0x0500) — record which transponder is active
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 25)
**Verify:** `eps.bus_voltage` (0x0105) > 27.0V — confirm no EPS anomaly caused the link loss
**Verify:** `obdh.mode` (0x0300) — confirm OBC did not enter safe/emergency mode
**GO/NO-GO:** Link margin > 3 dB and all subsystems nominal — recovery complete

### Step 6 — Escalation: No Link Recovery
**Action:** Log the failed recovery attempt with timestamps of all blind commands sent
**Action:** Notify Mission Director and Flight Dynamics of sustained link loss
**Action:** Schedule next available ground station pass for re-acquisition attempt
**Action:** If spacecraft has autonomous safe mode timer (24h no-contact), confirm onboard timer is running
**GO/NO-GO:** If no link after two consecutive passes, escalate to EMG-001 (Loss of Communication Emergency)

## Off-Nominal Handling
- If link restores but RSSI < -95 dBm or margin < 1.5 dB: Reduce data rate via `SET_PARAM(param_id=ttc.data_rate, value=1)` (low rate) to improve margin
- If OBC is found in SAFE mode after link restoration: Execute CON-002 or CON-005 as appropriate before resuming nominal operations
- If redundant transponder also fails: Spacecraft will rely on autonomous safe mode; coordinate multi-station recovery campaign via EMG-001
- If link is intermittent (toggling): Suspect antenna deployment anomaly or attitude issue — request AOCS telemetry and check `aocs.att_error` (0x0217)

## Post-Conditions
- [ ] `ttc.link_status` (0x0501) = 1 (link established)
- [ ] `ttc.link_margin` (0x0503) > 3.0 dB
- [ ] Active transponder identified and logged
- [ ] Root cause documented (ground issue, transponder failure, attitude, or EPS)
- [ ] If unrecovered: EMG-001 escalation initiated with next pass plan
