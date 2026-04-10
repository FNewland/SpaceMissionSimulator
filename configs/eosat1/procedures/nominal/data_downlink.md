# PROC-NOM-003: Science Data Downlink
**Subsystem:** PAYLOAD / TTC
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Downlink stored science imagery and ancillary data from the onboard mass memory
to the ground station during an AOS window. This procedure commands the payload
into PLAYBACK mode, monitors the transfer progress, and returns the payload to
STANDBY after the downlink session completes or the LOS approaches.

## Prerequisites
- [ ] PROC-NOM-001 Pass Startup completed with all-GO declaration
- [ ] TTC link established: `ttc.link_status` (0x0501) = 1
- [ ] Link margin confirmed: `ttc.link_margin` (0x0503) > 3.0 dB
- [ ] Payload in STANDBY mode: `payload.mode` (0x0600) = 1
- [ ] Onboard storage contains data: `payload.store_used` (0x0604) > 0 %
- [ ] Ground station data recorder armed and receiving

## Procedure Steps

### Step 1 --- Confirm Link Quality
**TC:** `HK_REQUEST` SID=5 (Service 3, Subtype 25) --- request TTC housekeeping
**Verify:** `ttc.link_status` (0x0501) = 1 (LINK_UP) within 5 s
**Verify:** `ttc.rssi` (0x0502) > -100 dBm within 5 s
**Verify:** `ttc.link_margin` (0x0503) > 3.0 dB within 5 s
**GO/NO-GO:** If link margin <= 3.0 dB, HOLD. If margin does not improve within
30 s, attempt `TTC_SWITCH_REDUNDANT`. If still insufficient, defer downlink.

### Step 2 --- Record Baseline Storage State
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- request Payload housekeeping
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 5 s
**Note:** Record `payload.store_used` (0x0604) as S0 (baseline percentage).
**Note:** Record `payload.image_count` (0x0605) as reference.

### Step 3 --- Command Playback Mode
**TC:** `SET_PARAM` param_id=0x0610, value=1 (Service 8, Subtype 1) --- set
downlink rate to nominal (X-band high rate)
**TC:** `PAYLOAD_SET_MODE` mode=3 (Service 8, Subtype 1) --- command PLAYBACK
**Verify:** `payload.mode` (0x0600) = 3 (PLAYBACK) within 15 s
**Note:** Downlink timer starts. Monitor ground station frame sync indicator.

### Step 4 --- Monitor Downlink Progress
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- poll every 60 s
**TC:** `HK_REQUEST` SID=5 (Service 3, Subtype 25) --- poll every 60 s
**Verify:** `payload.store_used` (0x0604) is decreasing over successive polls
**Verify:** `ttc.link_status` (0x0501) = 1 (LINK_UP) maintained
**Verify:** `ttc.link_margin` (0x0503) > 2.0 dB throughout session
**Verify:** `ttc.rssi` (0x0502) > -105 dBm throughout session
**Action:** If `ttc.link_margin` drops below 2.0 dB, pause downlink (Step 6).
**Action:** If `payload.store_used` reaches 0 %, proceed to Step 6 (complete).

### Step 5 --- Verify Ground Reception
**Action:** Confirm with ground station operator that frames are being received
and archived without errors.
**Verify:** Ground station frame error rate < 1.0E-6
**Note:** If ground station reports loss of frame sync, pause and re-evaluate
link conditions before continuing.

### Step 6 --- Terminate Downlink Session
**TC:** `PAYLOAD_SET_MODE` mode=1 (Service 8, Subtype 1) --- command STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15 s
**Note:** Record final `payload.store_used` (0x0604) as Sf.
**Note:** Data volume downlinked = S0 - Sf (as percentage of total capacity).

### Step 7 --- Post-Downlink Assessment
**TC:** `HK_REQUEST` SID=5 (Service 3, Subtype 25) --- final TTC status
**TC:** `HK_REQUEST` SID=6 (Service 3, Subtype 25) --- final Payload status
**Verify:** `ttc.link_status` (0x0501) = 1 (link still up)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY confirmed)
**Log:** Record downlink duration, data volume transferred, average link margin,
and any frame-sync interruptions in the pass log.

## Off-Nominal Handling
- If `payload.mode` does not transition to PLAYBACK within 15 s: Re-send command
  once. If still no response, flag payload anomaly and abort downlink.
- If `payload.store_used` not decreasing: Possible data-path fault. Command
  STANDBY and investigate.
- If `ttc.link_status` drops to 0 (LINK_DOWN) during transfer: Attempt
  `TTC_SWITCH_REDUNDANT`. If link re-established, resume from current point.
  If not, wait and retry at next AOS.
- If `ttc.link_margin` < 2.0 dB sustained: Reduce data rate via `SET_PARAM`
  param_id=0x0610, value=0 (low rate). Continue at reduced rate.
- If LOS approaching (< 2 min remaining): Terminate downlink gracefully. Remaining
  data will be scheduled for next available pass.

## Post-Conditions
- [ ] Payload returned to STANDBY mode
- [ ] Storage usage reduced (Sf < S0)
- [ ] Ground station confirms data archived without corruption
- [ ] TTC link maintained throughout (or gracefully recovered)
- [ ] Pass log updated with downlink session summary
