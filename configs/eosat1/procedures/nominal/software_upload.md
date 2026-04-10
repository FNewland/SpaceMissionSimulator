# PROC-NOM-005: Software Patch Upload
**Subsystem:** OBDH / Memory Management
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Upload a new software patch or application image to OBC memory, verify integrity via
checksum comparison, and optionally activate the new image. This is a high-risk procedure
requiring Flight Director authorisation and step-by-step execution with explicit GO/NO-GO
gates. A failed upload or corrupted image could render the OBC inoperable, so multiple
integrity checks are performed. The procedure supports both partial patches (applied to the
running image) and full application image replacements (loaded into the alternate memory
bank and activated via boot source switch).

## Prerequisites
- [ ] TTC link established with margin > 6 dB: `ttc.link_margin` (0x0503) > 6.0
- [ ] No active imaging session: `payload.mode` (0x0600) != 2
- [ ] OBC in NOMINAL mode: `obdh.mode` (0x0300) = 0
- [ ] Active OBC identified: `obdh.active_obc` (0x030C) recorded (0=A, 1=B)
- [ ] Current software version recorded: `obdh.sw_version` (0x030B)
- [ ] Patch file verified on ground: CRC-32 checksum computed and recorded
- [ ] Patch file size confirmed within available memory: `obdh.mmm_used` (0x0303) < 70 %
- [ ] Flight Director has authorised the upload (signed authorisation on console log)
- [ ] FDIR/Systems operator at console
- [ ] Battery SoC > 60 %: `eps.bat_soc` (0x0101) > 60
- [ ] No eclipse entry expected within the upload window (contact duration sufficient)

## Procedure Steps

### Step 1 --- Pre-Upload Health Assessment
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- request OBDH housekeeping
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL)
**Verify:** `obdh.cpu_load` (0x0302) < 50 % (margin for upload processing)
**Verify:** `obdh.mmm_used` (0x0303) < 70 % (sufficient free memory for image)
**Verify:** `obdh.mem_errors` (0x031E) = 0 (no existing memory faults)
**Verify:** `obdh.heap_usage` (0x031D) < 60 % (sufficient heap for upload task)
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- request EPS housekeeping
**Verify:** `eps.bat_soc` (0x0101) > 60 %
**Verify:** `eps.bus_voltage` (0x0105) > 28.0 V
**GO/NO-GO:** Flight Director confirms GO for upload. If any parameter marginal, HOLD.

### Step 2 --- Halt Non-Critical Onboard Tasks
**TC:** `FUNC_PERFORM` func_id=10 (Service 8, Subtype 1) --- suspend onboard scheduling
**Verify:** Command acceptance within 5 s
**TC:** `PAYLOAD_SET_MODE` mode=0 (Service 8, Subtype 1) --- payload OFF (if not already)
**Verify:** `payload.mode` (0x0600) = 0 (OFF) within 10 s
**Action:** Confirm no time-tagged command sequences remain in the execution queue.
**Note:** Suspending the scheduler prevents any onboard autonomy from interfering with
the upload process. It will be restored in Step 8.
**GO/NO-GO:** Non-critical tasks suspended. OBC resources available for upload.

### Step 3 --- Initiate Memory Load Session
**TC:** `MEM_LOAD_START` base_address=ADDR, length=LEN (Service 6, Subtype 2) --- start load
**Action:** Specify the target memory base address for the patch (provided by OBDH engineer).
**Action:** Specify the total length in bytes of the patch file.
**Verify:** Command acceptance within 5 s
**Verify:** No rejection (Service 1, Subtype 2) --- check for address range violations
**Note:** The onboard memory management service will prepare the target region and report
readiness. If the target region is write-protected, the load will be rejected.
**GO/NO-GO:** Memory load session initialised. OBC ready to receive data segments.

### Step 4 --- Upload Data Segments
**TC:** `MEM_LOAD_DATA` segment_id=N, data=BLOCK (Service 6, Subtype 9) --- upload segment
**Action:** Transmit the patch file in sequential segments of 256 bytes each.
**Action:** For each segment, wait for acknowledgement before sending the next.
**Verify:** Acknowledgement received for each segment within 10 s
**Monitor:** `obdh.cpu_load` (0x0302) --- should remain < 70 % during upload
**Monitor:** `ttc.link_margin` (0x0503) --- abort if margin drops below 3.0 dB
**Monitor:** `obdh.hktm_buf_fill` (0x0312) --- abort if buffer fill > 90 %
**Progress:** Track segment_id against total segment count. Log progress every 10 %.
**Caution:** If any segment is not acknowledged within 30 s, re-transmit that segment
once. If still not acknowledged, HOLD and investigate.
**GO/NO-GO:** All segments uploaded and individually acknowledged.

### Step 5 --- Verify Checksum Integrity
**TC:** `MEM_CHECK` base_address=ADDR, length=LEN (Service 6, Subtype 5) --- request checksum
**Verify:** Checksum report received within 15 s
**Action:** Compare reported onboard CRC-32 with the ground-computed CRC-32.
**Verify:** Checksums match exactly (bit-for-bit)
**Critical:** If checksums do NOT match, the upload is CORRUPT. Do NOT proceed to
activation. Execute Recovery Action 1 (erase and re-upload or abort).
**GO/NO-GO:** Checksum verified --- uploaded image integrity confirmed.

### Step 6 --- Activate New Software Image (If Full Image Replacement)
**TC:** `FUNC_PERFORM` func_id=20 (Service 8, Subtype 1) --- switch boot source to new image
**Verify:** Command acceptance within 5 s
**Action:** The OBC will reconfigure the boot source register to point to the newly
loaded image bank. The new image becomes active on the next boot.
**Note:** For patches applied to the running image, this step may be replaced with a
direct patch-apply command. Consult OBDH engineer for patch-specific activation.
**Verify:** `obdh.sw_image` (0x0311) still = 1 (application) --- no unintended reboot yet
**GO/NO-GO:** Boot source configured. Ready for controlled reboot.

### Step 7 --- Confirm Boot Into New Software
**TC:** `FUNC_PERFORM` func_id=30 (Service 8, Subtype 1) --- command controlled OBC restart
**Action:** Wait for boot sequence --- OBC will be unresponsive for approximately 60 s.
**Verify:** `obdh.mode` (0x0300) responds within 90 s of reboot command
**Verify:** `obdh.sw_version` (0x030B) = expected new version number
**Verify:** `obdh.sw_image` (0x0311) = 1 (application, not bootloader)
**Verify:** `obdh.last_reboot_cause` (0x0316) = 4 (commanded)
**Verify:** `obdh.active_obc` (0x030C) unchanged (same OBC unit as before)
**Critical:** If OBC does not respond within 120 s, or boots into bootloader (sw_image=0),
the new image may have failed boot validation. Execute Recovery Action 2.
**GO/NO-GO:** OBC running new software version confirmed.

### Step 8 --- Post-Upload Verification and Restore Operations
**TC:** `HK_REQUEST` SID=4 (Service 3, Subtype 25) --- full OBDH health check
**Verify:** `obdh.mode` (0x0300) = 0 (NOMINAL)
**Verify:** `obdh.cpu_load` (0x0302) < 50 %
**Verify:** `obdh.mem_errors` (0x031E) = 0
**Verify:** `obdh.bus_a_status` (0x030F) = 0 (OK) --- CAN bus communication restored
**TC:** `FUNC_PERFORM` func_id=11 (Service 8, Subtype 1) --- resume onboard scheduling
**Verify:** Command acceptance within 5 s
**TC:** `HK_REQUEST` SID=1 (Service 3, Subtype 25) --- verify EPS subsystem communication
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- verify AOCS subsystem communication
**Verify:** All subsystem HK responses received with plausible values within 15 s
**Action:** Log upload completion: old version, new version, upload duration, segment count,
checksum, any anomalies observed.
**GO/NO-GO:** All subsystems communicating normally. Upload procedure complete.

## Recovery Actions

### Recovery Action 1 --- Checksum Mismatch (Corrupt Upload)
1. Do NOT activate the uploaded image.
2. **TC:** `MEM_LOAD_ABORT` (Service 6, Subtype 3) --- abort and erase the uploaded region.
3. Verify memory region cleared.
4. Investigate cause: link degradation during upload? Buffer overflow? Segment re-ordering?
5. If cause identified and correctable: restart from Step 3 with improved conditions.
6. If cause unknown: abort procedure. The existing software remains active and unaffected.

### Recovery Action 2 --- Boot Failure (OBC in Bootloader)
1. If `obdh.sw_image` (0x0311) = 0 (bootloader), the new image failed boot validation.
2. **TC:** `FUNC_PERFORM` func_id=21 (Service 8, Subtype 1) --- revert boot source to previous image.
3. **TC:** `FUNC_PERFORM` func_id=30 (Service 8, Subtype 1) --- command OBC restart.
4. Wait 90 s for boot into previous (known-good) application image.
5. Verify `obdh.sw_version` (0x030B) = previous version and `obdh.sw_image` (0x0311) = 1.
6. If OBC recovers: log failure, erase corrupt image, investigate root cause on ground.
7. If OBC remains in bootloader: escalate to Flight Director. Consider OBC redundancy
   switch to backup unit if available (`obdh.obc_b_status` (0x030D) = 1 STANDBY).

### Recovery Action 3 --- Link Loss During Upload
1. If `ttc.link_status` (0x0501) = UNLOCKED during upload: the upload is incomplete.
2. On next AOS, verify `obdh.mode` (0x0300) --- OBC should still be running previous software.
3. The partially uploaded data remains in memory but is not activated.
4. Resume from last acknowledged segment_id, or erase and restart from Step 3.

## Off-Nominal Handling
- If `obdh.cpu_load` exceeds 70 % during upload: Pause segment transmission for 30 s to
  allow OBC to process buffered data. Resume at reduced rate (one segment per 2 s).
- If single segment re-transmission fails twice: Abort upload, erase target region, and
  re-attempt during a subsequent pass with better link conditions.
- If `eps.bat_soc` drops below 50 % during upload: Abort upload cleanly and restore
  onboard scheduling. The existing software is unaffected.
- If OBC reboots unexpectedly during upload (`obdh.reboot_count` increments): The upload
  is void. Wait for OBC to stabilise, verify it is running the previous software, and
  re-attempt only after root cause analysis of the reboot.

## Post-Conditions
- [ ] OBC running the new software version: `obdh.sw_version` (0x030B) = target version
- [ ] `obdh.mode` (0x0300) = NOMINAL (0)
- [ ] All subsystems communicating normally via CAN bus
- [ ] Onboard scheduling restored and operational
- [ ] `obdh.mem_errors` (0x031E) = 0
- [ ] Checksum of uploaded image verified correct
- [ ] Upload log completed with version numbers, checksums, and duration
- [ ] Previous software image retained as fallback in alternate memory bank
- [ ] Flight Director has signed off on successful upload

---
*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*
