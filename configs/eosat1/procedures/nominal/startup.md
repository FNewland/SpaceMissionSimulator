# PROC-NOM-001: Ground Pass Startup Sequence
**Subsystem:** ALL
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Establish full ground-to-spacecraft communications at AOS and verify that all
subsystems are in a healthy nominal state before commencing pass activities
(imaging, downlink, or maintenance). This procedure is executed at the start of
every scheduled ground contact.

## Prerequisites
- [ ] Ground station antenna acquired and tracking EOSAT-1
- [ ] Pass plan reviewed and uploaded to MCS
- [ ] Flight dynamics trajectory prediction current (< 4 h old)
- [ ] Flight Director on console and communication loop confirmed

## Procedure Steps

### Step 1 --- Verify OBC Status
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) within 10 s
**Verify:** `obdh.cpu_load` (0x0302) < 80 % within 10 s
**Verify:** `obdh.uptime` (0x0308) consistent with last known value
**GO/NO-GO:** If `obdh.mode` != 0, abort pass and execute CONT-010 OBC Recovery.

### Step 2 --- Verify EPS Health
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- request EPS housekeeping
**Verify:** `eps.bat_soc` (0x0101) > 50 % within 10 s
**Verify:** `eps.bus_voltage` (0x0105) > 27.0 V within 10 s
**Verify:** `eps.power_gen` (0x0107) > `eps.power_cons` (0x0106) (positive power margin)
**GO/NO-GO:** If SoC < 50 % or bus voltage < 27 V, execute CONT-001 EPS Safe Mode
procedure. Do NOT proceed with payload operations.

### Step 3 --- Verify AOCS Pointing
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- request AOCS housekeeping
**Verify:** `aocs.mode` (0x020F) = 0 (NOMINAL / NADIR_POINT) within 10 s
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 10 s
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s
**GO/NO-GO:** If attitude error > 1 deg or any rate > 0.1 deg/s, hold and
evaluate. Consider executing PROC-NOM-004 momentum management before continuing.

### Step 4 --- Establish TTC Link
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1) --- activate primary transponder
**Verify:** `ttc.link_status` (0x0501) = 1 (LINK_UP) within 15 s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 15 s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB within 15 s
**GO/NO-GO:** If link not established within 15 s, command `TTC_SWITCH_REDUNDANT`
and repeat verification. If redundant also fails, declare TTC anomaly.

### Step 5 --- Full Housekeeping Collection
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- EPS
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- AOCS
**TC:** `HK_REQUEST` SID=3 (Service 3, Subtype 25) --- TCS
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- OBDH
**TC:** `HK_REQUEST` SID=5 (Service 3, Subtype 25) --- TTC
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- Payload
**Verify:** All 6 HK packets received within 30 s
**Verify:** No out-of-limit parameters flagged by MCS limit checker

### Step 6 --- Payload Preparation (if imaging or downlink planned)
**TC:** `PAYLOAD_SET_MODE` mode=1 (Service 8, Subtype 1) --- command STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 20 s
**Verify:** `tcs.temp_fpa` (0x0408) trending downward (cooler active)
**Verify:** `payload.store_used` (0x0604) < 90 % (sufficient storage)
**Note:** Do NOT proceed to IMAGING until FPA temp < -10 C (see PROC-NOM-002).

### Step 7 --- Pass Readiness Declaration
**GO/NO-GO Gate --- Flight Director Poll:**
- EPS: SoC > 50 %, bus voltage > 27 V, positive power margin --- GO / NO-GO
- AOCS: NADIR_POINT, att_error < 1 deg, rates < 0.05 deg/s --- GO / NO-GO
- TTC: Link UP, RSSI > -100 dBm, margin > 3 dB --- GO / NO-GO
- TCS: All temps within nominal limits --- GO / NO-GO
- OBDH: NOMINAL, CPU < 80 % --- GO / NO-GO
- Payload: STANDBY (if required), storage < 90 % --- GO / NO-GO

**Decision:** If all GO, proceed with planned pass activities. If any NO-GO,
execute the relevant off-nominal procedure before continuing.

## Off-Nominal Handling
- If `eps.bat_soc` < 30 %: Immediately execute CONT-001 EPS Safe Mode Recovery.
- If `aocs.mode` = 2 (SAFE): Execute CONT-002 AOCS Anomaly Recovery.
- If TTC primary and redundant both fail: Declare loss of comm; wait for next pass.
- If `obdh.cpu_load` > 90 %: Investigate runaway process; consider OBC reset.
- If any HK packet not received: Re-request once; if still missing, flag subsystem.

## Post-Conditions
- [ ] All subsystems confirmed NOMINAL
- [ ] TTC link established with margin > 3 dB
- [ ] Full housekeeping set collected and archived
- [ ] Payload in STANDBY (if operations planned)
- [ ] Flight Director has declared pass GO
