# PROC-PLI-OFF-002: Memory Segment Failure

**Category:** Contingency
**Position Lead:** Payload Operations
**Cross-Position:** (None required — Payload-internal procedure)
**Difficulty:** Intermediate

## Objective
Detect, identify, and isolate a failed memory segment in the payload mass memory. This
procedure marks the defective segment as unusable, verifies that remaining capacity is
sufficient for continued operations, and adjusts the imaging plan to account for reduced
storage capacity.

## Prerequisites
- [ ] Increase in bad memory segment count detected — `payload.mem_segments_bad` (0x0612) > 0
- [ ] TT&C link active — `ttc.link_status` (0x0501) = 1
- [ ] Payload in STANDBY mode — `payload.mode` (0x0600) = 1
- [ ] No active imaging session (abort imaging before executing this procedure)

## Required Telemetry
| Parameter | ID | Expected Value |
|---|---|---|
| payload.mem_segments_bad | 0x0612 | > 0 (increased from previous known value) |
| payload.mem_total_mb | 0x060A | Total memory capacity |
| payload.mem_used_mb | 0x060B | Currently used memory |
| payload.store_used | 0x0604 | Storage usage percentage |
| payload.image_count | 0x0605 | Current stored image count |
| payload.checksum_errors | 0x0609 | May be elevated if corruption occurred |
| payload.mode | 0x0600 | 1 (STANDBY) |

## Required Commands
| Command | Service | Subtype | Func ID | Description |
|---|---|---|---|---|
| HK_REQUEST | 3 | 27 | — | Request one-shot HK report |
| PAYLOAD_MARK_BAD_SEGMENT | 8 | 1 | 25 | Mark memory segment as unusable |
| PAYLOAD_GET_IMAGE_CATALOG | 8 | 1 | 26 | Request stored image catalog |
| PAYLOAD_DELETE_IMAGE | 8 | 1 | 24 | Delete image from affected segment |
| MEM_CHECK | 6 | 9 | — | Memory CRC check |

## Procedure Steps

### Step 1: Detect Memory Segment Failure
**Action:** Request payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mem_segments_bad` (0x0612) — record current value and compare with
previous known value. Calculate the number of newly failed segments.
**Verify:** `payload.mem_total_mb` (0x060A) — record total capacity
**Verify:** `payload.mem_used_mb` (0x060B) — record used capacity
**Verify:** `payload.store_used` (0x0604) — record percentage
**Verify:** `payload.checksum_errors` (0x0609) — record value (may correlate with segment failure)
**Note:** Each memory segment corresponds to approximately 250 MB (total 20,000 MB / 80 segments).
A single bad segment reduces capacity by approximately 1.25%.
**GO/NO-GO:** Segment failure confirmed — proceed to identification.

### Step 2: Identify Failed Segment
**Action:** Request image catalog to determine which segment(s) may be affected:
`PAYLOAD_GET_IMAGE_CATALOG` (func_id 26)
**Action:** Review the catalog for images that reported checksum errors. The segment
hosting the corrupted image is the likely failed segment.
**Action:** If the failed segment cannot be determined from the catalog, perform a
memory CRC check: `MEM_CHECK(memory_id=0, address=SEGMENT_START, length=SEGMENT_SIZE)`
(Service 6, Subtype 9) for each suspect segment.
**Verify:** CRC check result — segments with CRC mismatch are confirmed bad.
**Note:** Segment index ranges from 0 to 79. If the specific segment index is known
from onboard diagnostics, skip the manual CRC check.
**GO/NO-GO:** Failed segment(s) identified — proceed to marking.

### Step 3: Mark Bad Segment
**Action:** Mark the failed segment as unusable:
`PAYLOAD_MARK_BAD_SEGMENT(segment_id=N)` (func_id 25) where N is the segment index (0-79).
**Verify:** Request payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mem_segments_bad` (0x0612) — confirm count reflects the newly marked segment
**Note:** If multiple segments are bad, repeat this step for each identified segment.
**Action:** If any images were stored in the bad segment, delete them:
`PAYLOAD_DELETE_IMAGE(scene_id=X)` (func_id 24) for each affected scene.
**GO/NO-GO:** Bad segment(s) marked and affected images cleaned up — proceed.

### Step 4: Verify Reduced Capacity
**Action:** Request payload housekeeping: `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mem_total_mb` (0x060A) — confirm total reported capacity reflects
reduction (expected decrease of ~250 MB per bad segment)
**Verify:** `payload.store_used` (0x0604) — recalculate percentage based on new total capacity
**Verify:** `payload.mem_used_mb` (0x060B) — confirm used memory is accurate
**Action:** Calculate remaining usable capacity:
- Usable capacity = Total capacity - (bad segments x 250 MB) - Used storage
- Remaining images = Usable capacity / 800 MB per image
**Note:** Record the updated capacity figures for the operations log.
**GO/NO-GO:** Reduced capacity verified and documented — proceed to plan adjustment.

### Step 5: Adjust Imaging Plan
**Action:** Assess impact on current imaging plan:
- If remaining capacity supports all planned imaging: No plan change needed.
- If remaining capacity is insufficient: Prioritize imaging targets. Consider
  scheduling data downlinks between imaging sessions to free storage.
- If more than 10% of segments are bad (> 8 segments): Escalate to mission planning
  team for long-term capacity assessment. Consider reducing image resolution or
  compression settings to reduce per-image storage.
**Action:** Notify Flight Director of the updated capacity and any imaging plan changes.
**Action:** Update the operations log with:
- Bad segment index(es)
- Total bad segment count
- Remaining usable capacity
- Impact assessment on imaging plan
**GO/NO-GO:** Imaging plan adjusted — procedure complete.

## Verification Criteria
- [ ] Failed segment(s) identified and marked via `PAYLOAD_MARK_BAD_SEGMENT`
- [ ] `payload.mem_segments_bad` (0x0612) accurately reflects total bad segments
- [ ] Any corrupted images in bad segments have been deleted
- [ ] Remaining usable capacity calculated and documented
- [ ] Imaging plan adjusted if capacity reduction impacts operations
- [ ] Anomaly report filed with segment failure details

## Contingency
- If bad segment count increases rapidly (multiple new failures in a short period):
  Suspect radiation-induced latch-up or systematic memory failure. Command payload
  to OFF: `PAYLOAD_SET_MODE(mode=0)` (func_id 20). Power cycle payload after 60 s.
  If failures continue after power cycle, escalate to engineering team.
- If more than 20% of segments are bad (> 16 segments): Payload memory is severely
  degraded. Assess whether the imager can still perform useful science. Consider
  operating in a reduced-capacity mode with immediate downlink after each capture.
- If marking a segment fails (command rejected): Retry the command. If still rejected,
  the segment may already be marked or the index may be invalid. Verify the segment
  index and retry. If persistent, escalate.
- If stored images cannot be recovered from a newly failed segment: The images are
  lost. Log the lost scene IDs and coordinate with mission planning for re-acquisition.
