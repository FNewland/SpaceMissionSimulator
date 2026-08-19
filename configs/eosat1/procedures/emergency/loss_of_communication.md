# EMG-004: Loss of Communication
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
**Check:** Iqaluit (IQ1), Troll (TR1) link status.
**Verify:** Antenna pointing calibration nominal; receiver lock threshold set to minimum (-130 dBm).
**GO/NO-GO:** At least two ground stations fully operational with clear line-of-sight for next pass.

### Step 2 --- Attempt Contact on All Available Ground Stations
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- transmitted blind on each pass.
**Sequence:** Schedule uplink attempts on every visible pass (min elevation 5 deg) across
Iqaluit and Troll in priority order based on maximum elevation.
**Verify:** `ttc.rssi` (0x0502) > -125 dBm and `ttc.link_status` (0x0501) = 2 (LOCKED) within 30s of AOS.
**GO/NO-GO:** If telemetry received on any station, exit emergency and transition to EMG-001 assessment. If no response after full ground station rotation, proceed to Step 3.

### Step 3 --- Blind Command on Primary Transponder
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63) --- blind, repeated 3x with 10s spacing.
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- transmitted 15s after switch command.
**Verify:** `ttc.link_status` (0x0501) = 2 (LOCKED) within 45s.
**Timing:** Execute at pass with elevation > 20 deg to maximise link margin.
**GO/NO-GO:** If link acquired, proceed to full HK dump. If no response, proceed to Step 4.

### Step 4 --- Blind Command on Redundant Transponder
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1, func_id 64) --- blind, repeated 3x with 10s
spacing. (Was annotated Subtype 2; every S8 function-perform is Subtype 1.)
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) --- transmitted 15s after switch command.
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
**Stations:** Iqaluit and Troll --- both provide near-polar pass coverage every ~95 min.
**Note:** Alternate between the two transponders on successive passes: send the
primary select on one pass and the redundant select on the next.
**TC:** `TTC_SWITCH_PRIMARY` (Service 8, Subtype 1, func_id 63) --- odd-numbered passes
**TC:** `TTC_SWITCH_REDUNDANT` (Service 8, Subtype 1, func_id 64) --- even-numbered passes
**TC:** `OBC_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 50) --- blind emergency mode command.
(Was annotated Subtype 3, which is not an S8 subtype this suite dispatches.)
**Verify:** Any downlink signal within pass window.
**GO/NO-GO:** If contact restored, proceed to EMG-001 for safe mode recovery.

### Step 7 --- Emergency Network Escalation (24h Threshold)
**Action:** Request support from partner agency ground stations (ESA ESTRACK, NASA NEN).
**Expand:** Add Malindi, Maspalomas, and South Point stations to tracking schedule.
**Note:** Continue the Step 6 blind commanding rotation on all passes --- the
commands are `TTC_SWITCH_PRIMARY` (func 63) and `TTC_SWITCH_REDUNDANT`
(func 64), alternating. Nothing new is sent here, so this step carries no
`**TC:**` line of its own.
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
- If spacecraft detected by radar but no RF signal: assume total transponder failure; plan uplink on S-band backup if available.
- If telemetry received but corrupt/fragmented: step the TM rate ladder down to LOW
  (`TTC_SET_DATA_RATE(rate=0)`, func_id 65 — 16 kbps BPSK conv(7,1/2)+RS, which is both
  9.03 dB of rate margin and 1.5 dB of extra coding gain relative to HIGH). If that
  still does not lock, command beacon mode (`TTC_SET_BEACON_MODE(on=1)`, func_id 70):
  it forces the SAFE rung (ex-BEACON) — 1 000 bps, 1 kBd, ~3.1 kHz occupied bandwidth — which is
  the narrowest, most robust waveform the spacecraft has. Re-configure the ground
  demodulator to 1 kBd before expecting lock.
- If spacecraft orbit decayed significantly from predicted: re-acquire TLE from space surveillance network before next pass attempt.

## Post-Conditions
- [ ] Two-way communication re-established on at least one transponder
- [ ] `ttc.mode` (0x0500) confirmed in NOMINAL or SAFE
- [ ] `ttc.link_status` (0x0501) = 2 (LOCKED) with stable `ttc.rssi` (0x0502) > -120 dBm
- [ ] Full housekeeping dump (all SIDs 1--6) received and reviewed
- [ ] Root cause of communication loss identified or investigation initiated
- [ ] Transition to EMG-001 for staged recovery if spacecraft was in autonomous safe mode
