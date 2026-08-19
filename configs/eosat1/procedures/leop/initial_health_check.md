# LEOP-002: Initial Health Check
**Subsystem:** All / OBDH
**Phase:** LEOP
**Revision:** 2.0 --- 2026-08c flow redesign (entry/exit states; AOCS and payload
are DARK at this point and are no longer "verified"; wheel checks moved to
COM-004 where the wheels are actually enabled)
**Approved:** Flight Operations Director

**Entry state:** LEOP-001 exit state --- application image running, link on the
LOW rung, HK SIDs 1/3/4/6 enabled, clock unset. AOCS (`aocs_wheels`), payload,
FPA-cooler, heater and prop lines all OFF.

## Purpose
Perform a health assessment of the subsystems that are POWERED at this point in
LEOP --- EPS, TCS, OBDH and TT&C --- and positively confirm that everything else is
still dark. Establish baseline telemetry values for the mission. This card does
not power anything on and does not command any mode: it observes.

## Prerequisites
- [ ] LEOP-001 (First Acquisition) completed successfully
- [ ] Bidirectional S-band link active with link margin > 3 dB
- [ ] Minimum 8 minutes remaining in current scheduled ground station pass
- [ ] MCS telemetry archiving confirmed active

## Procedure Steps

### Step 1 --- Request Full EPS Housekeeping
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.bus_voltage` (0x0105) in range [26.0V, 30.0V] within 10s
**Verify:** `eps.bat_voltage` (0x0100) reported within 10s
**Verify:** `eps.bat_soc` (0x0101) > 50% within 10s
**Verify:** `eps.power_gen` (0x0107) > 0W (confirms at least partial illumination) within 10s
**GO/NO-GO:** EPS parameters within nominal post-separation limits

### Step 2 --- Confirm the Unpowered Subsystems Are Dark
**Action:** The same SID 1 report carries every PDM line flag. Confirm the
cold-boot power state is still exactly what LEOP-001 left: nothing beyond the
OBC, the receiver and the (auto-armed) transmitter is energised.
**Verify:** `eps.pl_aocs_wheels` (0x0117) = 0 --- AOCS suite unpowered (powered in LEOP-007)
**Verify:** `eps.pl_payload` (0x0113) = 0 --- payload line off (powered in COM-009)
**Verify:** `eps.pl_fpa_cooler` (0x0114) = 0 --- cooler line off (powered in COM-010)
**Verify:** `eps.pl_prop` (0x014A) = 0 --- propulsion line off (energised only for manoeuvres)
**Note:** Do NOT request HK SIDs 2 (AOCS) or 5 (Payload) here: those SIDs are
not enabled yet (only 1/3/4/6/11 are), and their subsystems are unpowered --- the
line flags above are the honest health check for a dark unit. There is no AOCS
mode to verify and no wheel telemetry to read at this point; the old card's
"verify `aocs.mode` = DETUMBLE" and "verify all four reaction wheels
responding" steps described a spacecraft state that does not exist until
LEOP-007 and COM-004 respectively.
**GO/NO-GO:** Power state matches the LEOP plan --- nothing energised early

### Step 3 --- Request Thermal Housekeeping
**TC:** `HK_REQUEST(sid=3)` (Service 3, Subtype 27)
**Verify:** `tcs.temp_obc` (0x0406) in range [-5C, +45C] within 10s
**Verify:** `tcs.temp_battery` (0x0407) in range [0C, +35C] within 10s
**Verify:** `tcs.temp_fpa` (0x0408) reported (payload unpowered, expect ambient) within 10s
**Note:** All heater LINES are off. If `tcs.temp_battery` is trending toward
0C, flag it for LEOP-007 Step 2, which powers the battery-heater line as
needed --- do not power it from this card.
**GO/NO-GO:** All thermal zones within survival limits

### Step 4 --- Request OBDH Housekeeping
**TC:** `HK_REQUEST(sid=4)` (Service 3, Subtype 27)
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL) or 1 (SAFE) within 10s
**Verify:** `obdh.cpu_load` (0x0302) < 70% within 10s
**Note:** `OBC_SET_MODE` (func 50) encodes 0 = NOMINAL, 1 = SAFE, 2 =
EMERGENCY, and `obdh.mode` (0x0300) reads back the same encoding.
**GO/NO-GO:** OBDH operating within nominal parameters

### Step 5 --- Request TT&C Housekeeping
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 10s
**Verify:** `ttc.rssi` (0x0502) > -110 dBm within 10s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB within 10s
**GO/NO-GO:** TT&C link nominal with adequate margin

### Step 6 --- Compile Health Assessment Report
**Action:** Compare all received telemetry against post-separation expected values. Flag any parameters outside nominal range. Generate Health Assessment Summary for Flight Director.
**GO/NO-GO:** All powered subsystems nominal --- clear to proceed with LEOP-004

## Off-Nominal Handling
- If `eps.bat_soc` < 50%: Defer non-essential operations. Prioritize wing deployment (LEOP-004) and sun acquisition (LEOP-005). Reduce TM demand to conserve power.
- If `tcs.temp_battery` < 0C: escalate to LEOP-007 Step 2 immediately --- that step powers the `htr_bat` line (`EPS_POWER_ON(line_index=5)`) and enables the battery heater. Monitor for temperature rise.
- If `obdh.cpu_load` > 80%: Check for anomalous task scheduling. An OBC excursion to SAFE (`OBC_SET_MODE(mode=1)`) requires Flight Director approval; restore NOMINAL with `OBC_SET_MODE(mode=0)` afterwards.
- If any enabled HK SID fails to respond: Retry request twice. If still no response, check TM link quality. Log as anomaly for investigation.
- If any of the Step 2 line flags reads 1: something was energised out of sequence. Identify who commanded it, log the anomaly, and power the line back off (`EPS_POWER_OFF`) unless a later card has already legitimately claimed it.

## Exit state
- IDENTICAL to the entry state --- this card commands no state change. All
  observed values archived as the mission baseline.

## Post-Conditions
- [ ] HK SIDs 1, 3, 4, 6 received and archived as the mission baseline
- [ ] EPS, TCS, OBDH, TT&C confirmed nominal
- [ ] AOCS / payload / cooler / prop lines positively confirmed OFF
- [ ] Health Assessment Report distributed to mission team

**NEXT:** LEOP-004 (Solar Array Verification)
