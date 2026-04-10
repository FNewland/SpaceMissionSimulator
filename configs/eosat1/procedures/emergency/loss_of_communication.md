# PROC-EMG-001: Loss of Communication Recovery
**Subsystem:** TT&C / Ground Segment
**Phase:** EMERGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recovery procedure when no telemetry has been received from EOSAT-1 for two or more
consecutive pass windows. This procedure defines a structured escalation from ground
station verification through blind commanding and autonomous recovery assessment,
with decision gates at 6h, 12h, 24h, and 48h elapsed time since last contact.

## Prerequisites
- [ ] Confirmed loss of telemetry for >= 2 consecutive pass windows (~3 hours)
- [ ] Ground station network status page reviewed for planned outages
- [ ] Current Two-Line Element set available (propagated < 24h ago)
- [ ] Blind command stack prepared and validated in simulator
- [ ] Flight Director on console and Emergency Response Team notified

## Procedure Steps

### Step 1 --- Verify Ground Station Infrastructure
**Action:** Confirm operational status of all primary ground stations.
**Check:** Svalbard (SV1), Troll (TR1), Inuvik (IN1), O'Higgins (OH1) link status.
**Verify:** Antenna pointing calibration nominal; receiver lock threshold set to minimum (-130 dBm).
**GO/NO-GO:** At least two ground stations fully operational with clear line-of-sight for next pass.

### Step 2 --- Attempt Contact on All Available Ground Stations
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- transmitted blind on each pass.
**Sequence:** Schedule uplink attempts on every visible pass (min elevation 5 deg) across
Svalbard, Troll, Inuvik, and O'Higgins in priority order based on maximum elevation.
**Verify:** `ttc.rssi` (0x0502) > -125 dBm and `ttc.link_status` (0x0501) = 2 (LOCKED) within 30s of AOS.
**GO/NO-GO:** If telemetry received on any station, exit emergency and transition to PROC-EMG-004 assessment. If no response after full ground station rotation, proceed to Step 3.

### Step 3 --- Blind Command on Primary Transponder
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1) --- blind, repeated 3x with 10s spacing.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- transmitted 15s after switch command.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 45s.
**Timing:** Execute at pass with elevation > 20 deg to maximise link margin.
**GO/NO-GO:** If link acquired, proceed to full HK dump. If no response, proceed to Step 4.

### Step 4 --- Blind Command on Redundant Transponder
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 2) --- blind, repeated 3x with 10s spacing.
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 1) --- transmitted 15s after switch command.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 45s.
**Timing:** Execute at next available high-elevation pass (> 20 deg).
**GO/NO-GO:** If link acquired, remain on redundant transponder and assess primary. If no response, proceed to Step 5.

### Step 5 --- Wait for Autonomous Recovery Timer
**Action:** EOSAT-1 onboard autonomy triggers a communication recovery sequence after 6 hours
of no ground contact. The spacecraft will cycle through transponder configurations and
increase beacon power.
**Monitor:** All ground stations in listen-only mode on both primary and redundant frequencies.
**Verify:** Beacon detection on any station --- `ttc.rssi` (0x0502) readings logged continuously.
**Duration:** Wait through at least 4 orbit periods (~6.3 hours) after onboard timer expected activation.

### Step 6 --- Polar Pass Focused Campaign (12h Threshold)
**Action:** If 12 hours elapsed since last contact, concentrate all resources on high-latitude
stations with optimal geometry.
**Stations:** Svalbard and Troll --- both provide near-polar pass coverage every ~95 min.
**TC:** Alternate between `TTC_SWITCH_PRIMARY` and `TTC_SWITCH_REDUNDANT` on successive passes.
**TC:** `OBC_SET_MODE(mode=2)` (Service 8, Subtype 3) --- blind emergency mode command.
**Verify:** Any downlink signal within pass window.
**GO/NO-GO:** If contact restored, proceed to PROC-EMG-004 for safe mode recovery.

### Step 7 --- Emergency Network Escalation (24h Threshold)
**Action:** Request support from partner agency ground stations (ESA ESTRACK, NASA NEN).
**Expand:** Add Malindi, Maspalomas, and South Point stations to tracking schedule.
**TC:** Continue blind commanding rotation on all passes.
**Notify:** ESA Space Debris Office of potential uncontrolled spacecraft status.
**GO/NO-GO:** If 24h exceeded with no contact, convene Anomaly Review Board.

### Step 8 --- Extended Loss Assessment (48h Threshold)
**Action:** If 48 hours elapsed with no signal on any station or frequency:
**Assess:** Spacecraft may be in under-voltage lockout or unrecoverable tumble.
**Radar:** Request space surveillance radar tracking to confirm orbit and attitude state.
**Plan:** Develop long-term recovery plan assuming spacecraft will exit safe mode only when
power-positive conditions resume (sunlit orbit phase with favourable beta angle).
**Monitor:** Maintain passive listening campaign for minimum 30 days.

## Decision Tree Summary
| Elapsed Time | Action | Escalation |
|---|---|---|
| 0--6h | Cycle all ground stations, blind command both transponders | Flight Director |
| 6--12h | Wait for onboard autonomous recovery timer | Emergency Response Team |
| 12--24h | Polar pass focused campaign, blind emergency mode command | Mission Manager |
| 24--48h | Emergency network escalation, partner agency support | Anomaly Review Board |
| >48h | Extended loss assessment, radar tracking, 30-day listen | Programme Director |

## Off-Nominal Handling
- If ground station hardware fault confirmed: reroute to backup antenna feed; contact station provider for emergency maintenance.
- If spacecraft detected by radar but no RF signal: assume total transponder failure; plan uplink on VHF/UHF backup if available.
- If telemetry received but corrupt/fragmented: reduce data rate, increase FEC coding, attempt narrow-bandwidth beacon mode.
- If spacecraft orbit decayed significantly from predicted: re-acquire TLE from space surveillance network before next pass attempt.

## Post-Conditions
- [ ] Two-way communication re-established on at least one transponder
- [ ] `ttc.mode` (0x0500) confirmed in NOMINAL or SAFE
- [ ] `ttc.link_status` (0x0501) = 2 (LOCKED) with stable `ttc.rssi` (0x0502) > -120 dBm
- [ ] Full housekeeping dump (all SIDs 1--6) received and reviewed
- [ ] Root cause of communication loss identified or investigation initiated
- [ ] Transition to PROC-EMG-004 for staged recovery if spacecraft was in autonomous safe mode
