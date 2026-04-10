# PROC-PLI-OFF-001: Corrupted Image Handling

**Category:** Contingency
**Position Lead:** Payload Operations
**Cross-Position:** Power & Thermal (EPS/TCS)
**Difficulty:** Intermediate

## Objective
Investigate and respond to corrupted image data detected by onboard checksum errors.
This procedure determines whether the corruption is caused by focal plane array (FPA)
thermal conditions, memory segment failure, or a transient event, and takes corrective
action including aborting imaging if necessary and re-capturing the scene.

## Prerequisites
- [ ] Checksum errors detected — `payload.checksum_errors` (0x0609) has increased
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Payload in STANDBY or IMAGING mode — `payload.mode` (0x0600) >= 1
- [ ] Payload operator on console

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| payload.checksum_errors | 0x0609 | Increased from baseline (anomaly trigger) |
| payload.fpa_temp | 0x0601 | < -25.0 C for imaging quality |
| payload.fpa_ready | 0x0610 | 1 (FPA in range) |
| payload.imager_temp | 0x0603 | < 30 C |
| payload.mem_segments_bad | 0x0612 | 0 nominal; > 0 indicates memory issue |
| payload.store_used | 0x0604 | Current storage level |
| payload.image_count | 0x0605 | Current image count |
| payload.last_scene_id | 0x060C | Last captured scene |
| payload.last_scene_quality | 0x060D | Quality metric of last image |
| payload.mode | 0x0600 | Current payload mode |
| tcs.temp_fpa | 0x0408 | < -25.0 C (cross-check with payload sensor) |
| tcs.cooler_fpa | 0x040C | 1 (cooler active) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_SET_MODE | 8 | 1 | 20 | Set payload mode |
| PAYLOAD_CAPTURE | 8 | 1 | 22 | Re-trigger image capture |
| PAYLOAD_DELETE_IMAGE | 8 | 1 | 24 | Delete corrupted image |
| FPA_COOLER | 8 | 1 | 33 | Control FPA cooler |
| PAYLOAD_MARK_BAD_SEGMENT | 8 | 1 | 25 | Mark memory segment as unusable |

## Procedure Steps

### Step 1: Detect and Quantify Checksum Errors
**Action:** Request payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.checksum_errors` (0x0609) — record current value and compare with
previous baseline. Calculate delta (number of new errors).
**Verify:** `payload.last_scene_id` (0x060C) — record the scene that may be affected
**Verify:** `payload.last_scene_quality` (0x060D) — record quality metric
**Verify:** `payload.image_count` (0x0605) — record current count
**Note:** A single checksum error may be a transient event (SEU). Multiple errors on the
same scene suggest a systematic issue.
**GO/NO-GO:** Checksum errors confirmed — proceed to root cause investigation.

### Step 2: Check FPA Temperature
**Action:** Request payload and TCS housekeeping:
- `HK_REQUEST(sid=6)` — payload HK
- `HK_REQUEST(sid=3)` — TCS HK
**Verify:** `payload.fpa_temp` (0x0601) — record value
**Verify:** `tcs.temp_fpa` (0x0408) — cross-check with TCS sensor
**Verify:** `payload.fpa_ready` (0x0610) — check if FPA is in operational range
**Verify:** `tcs.cooler_fpa` (0x040C) — confirm cooler is active
**Action:** Assess FPA thermal condition:
- If `payload.fpa_temp` > -20.0 C: FPA is too warm for quality imaging. Proceed to
  Step 3 (abort imaging, wait for cooldown).
- If `payload.fpa_temp` < -25.0 C and `payload.fpa_ready` = 1: FPA is nominal.
  Proceed to Step 4 (check memory).
**GO/NO-GO:** FPA assessment complete — follow appropriate branch.

### Step 3: FPA Warm — Abort Imaging and Cool Down
**Action:** If payload is in IMAGING mode, abort: `PAYLOAD_SET_MODE(mode=1)` (func_id 20)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10 s
**Action:** Verify cooler is active: If `tcs.cooler_fpa` (0x040C) = 0, enable:
`FPA_COOLER(on=1)` (func_id 33)
**Verify:** `tcs.cooler_fpa` (0x040C) = 1 within 5 s
**Action:** Monitor FPA temperature cooldown at 60 s intervals: `HK_REQUEST(sid=6)`
**Verify:** `payload.fpa_temp` (0x0601) trending downward toward target (-5 C cooler
setpoint, operational threshold -25 C)
**Note:** Cooldown from ambient (~5 C) to operational (< -25 C) takes approximately
100-150 s per the thermal model (tau ~100 s).
**Action:** Wait until `payload.fpa_temp` < -25.0 C and `payload.fpa_ready` = 1
**GO/NO-GO:** FPA cooled to operational temperature — proceed to Step 5 (re-capture).

### Step 4: Check Memory Segments
**Action:** Request payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mem_segments_bad` (0x0612) — record current value
**Verify:** `payload.store_used` (0x0604) — record storage usage
**Action:** If `payload.mem_segments_bad` > 0:
- Memory segment failure is a possible cause of corruption.
- Follow PROC-PLI-OFF-002 (Memory Segment Failure) to identify and mark the bad
  segment before continuing.
**Action:** If `payload.mem_segments_bad` = 0 and FPA is cold:
- The corruption is likely a transient event (single event upset, radiation hit).
- Proceed to Step 5 (re-capture).
**GO/NO-GO:** Memory assessment complete — proceed to re-capture if appropriate.

### Step 5: Delete Corrupted Image and Re-Capture
**Action:** Delete the corrupted image: `PAYLOAD_DELETE_IMAGE(scene_id=N)` (func_id 24)
where N is the affected scene ID from Step 1.
**Verify:** `payload.store_used` (0x0604) decreases (storage freed)
**Verify:** `payload.image_count` (0x0605) decreases by 1
**Action:** If the imaging window is still available and all conditions are met:
- `payload.fpa_temp` (0x0601) < -25.0 C
- `payload.fpa_ready` (0x0610) = 1
- `payload.mem_segments_bad` (0x0612) = 0 (or bad segments marked)
- `aocs.att_error` (0x0217) < 0.1 deg
**Action:** Re-capture the scene: `PAYLOAD_CAPTURE(scene_id=N, lines=L)` (func_id 22)
where N is the scene ID and L is the required number of CCD lines.
**Verify:** `payload.image_count` (0x0605) increments by 1
**Verify:** `payload.checksum_errors` (0x0609) does NOT increase
**Verify:** `payload.last_scene_quality` (0x060D) — record quality metric for new image
**GO/NO-GO:** Re-capture successful with no new errors — procedure complete.

### Step 6: Post-Capture Verification
**Action:** Request final payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.checksum_errors` (0x0609) — no further increase
**Verify:** `payload.last_scene_quality` (0x060D) — acceptable quality
**Verify:** `payload.store_used` (0x0604) — within capacity
**Action:** Log the incident: original scene ID, error count, root cause assessment
(thermal / memory / transient), and re-capture result.

## Verification Criteria
- [ ] Root cause identified (FPA thermal, memory segment, or transient)
- [ ] Corrupted image deleted from storage
- [ ] Re-captured image (if window available) has no checksum errors
- [ ] `payload.fpa_temp` (0x0601) < -25.0 C confirmed
- [ ] `payload.fpa_ready` (0x0610) = 1
- [ ] `payload.mem_segments_bad` (0x0612) addressed (marked bad or confirmed 0)
- [ ] Anomaly report filed

## Contingency
- If checksum errors continue after re-capture with cold FPA and no bad segments:
  Possible imager electronics issue. Command payload to STANDBY. Power cycle payload:
  `PAYLOAD_SET_MODE(mode=0)`, wait 30 s, `PAYLOAD_SET_MODE(mode=1)`. If errors persist,
  escalate to engineering team.
- If FPA cooler fails to cool FPA below -25 C: Cooler may be degraded. Check
  `payload.cooler_pwr` (0x0602) for expected power draw. If cooler power is 0 and
  cooler is commanded ON, suspect cooler hardware failure. Escalate.
- If multiple images are corrupted: Check if all corrupted images map to the same memory
  region. If so, likely a localized memory failure — mark the segment as bad per
  PROC-PLI-OFF-002.
- If imaging window has passed and re-capture is not possible: Log the lost scene.
  Coordinate with mission planning to schedule re-acquisition on a future pass.
