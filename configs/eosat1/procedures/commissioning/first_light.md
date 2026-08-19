# COM-012: First Light
**Subsystem:** Payload / AOCS
**Phase:** COMMISSIONING
**Revision:** 2.0 --- 2026-08c: the image-transfer step now flies the REAL
Service-13 chain (S13.1 create -> 0x062D raises -> 0x062E drains -> S13.5
end), which works over the wire since the 2026-08c simulator fixes ---
`PAYLOAD_INITIATE_TRANSFER` (func 94) remains the on-board spool command but
the ground-facing downlink of a product is S13. Mode codes and FPA bands
corrected. Entry/exit states declared. Runs BEFORE COM-011 (calibration
needs a verified imaging chain --- and "first light" means the FIRST image).
**Approved:** Flight Operations Director

**Entry state:** COM-010 exit state --- payload STANDBY, cooler RUNNING with
`payload.fpa_ready` (0x0610) = 1 at the -15C setpoint, AOCS commanded
NOMINAL, downlink LOW, DOWNLINK mode off.

## Purpose
Capture the first image with the EOSAT-1 multispectral imager. Transition the
payload from STANDBY to IMAGING mode, acquire a test scene over a known ground
target, verify image data storage on the mass memory, and downlink a preview for
initial quality assessment. This milestone confirms end-to-end imaging capability.

## Prerequisites
- [ ] COM-009 (Payload Power-On) completed — payload in STANDBY
- [ ] COM-010 (FPA Cooler Activation) completed — `payload.fpa_ready` (0x0610) = 1 at the commanded setpoint (-15C: band -16.5C to -14.5C)
- [ ] AOCS in NOMINAL (mode 4) — Step 1 takes it to FINE_POINT (mode 5)
- [ ] `aocs.att_error` (0x0217) < 0.5 deg (pointing accuracy sufficient for imaging)
- [ ] `eps.bat_soc` (0x0101) > 70%
- [ ] Spacecraft approaching a known calibration ground site (e.g., Libya-4, Railroad Valley)
- [ ] On-board storage verified available (COM-009 Step 7)
- [ ] Bidirectional S-band link active
- [ ] Payload and AOCS engineers on console

## Procedure Steps

### Step 1 — Transition AOCS to FINE_POINT
**TC:** `AOCS_FINE_READY_CHECK` (func 89) — verify the 0x02C5 precondition
bitmask reads 0 (mode 5 is otherwise refused with TM(1,8))
**TC:** `AOCS_SET_MODE(mode=5)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 5 (FINE_POINT) within 30s
**Verify:** `aocs.total_pointing_err_3sig` (0x02AA) < 0.05 deg within 300s
(the payload capture gate)
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 180s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.01 deg/s within 180s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.01 deg/s within 180s
**GO/NO-GO:** FINE_POINT achieved — pointing accuracy meets imaging requirement

### Step 2 — Verify FPA Temperature Before Imaging
**TC:** `GET_PARAM(param_id=0x0601)` (Service 20, Subtype 3) — FPA temperature
**Verify:** `payload.fpa_temp` (0x0601) in range [-16.5C, -14.5C] within 10s —
the -15C setpoint band (`payload.fpa_ready` 0x0610 = 1 is the same statement)
**GO/NO-GO:** FPA at operational temperature

### Step 3 — Verify Payload Ready for Imaging
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**TC:** `GET_PARAM(param_id=0x0604)` (Service 20, Subtype 3) — data interface status
**Verify:** Data interface = READY (value 1) within 10s
**TC:** `GET_PARAM(param_id=0x0605)` (Service 20, Subtype 3) — storage free
**Verify:** Storage free > 500 MB (sufficient for test image) within 10s
**GO/NO-GO:** Payload ready to transition to IMAGING

### Step 4 — Command Payload to IMAGING Mode
**TC:** `PAYLOAD_SET_MODE(mode=2)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 2 (IMAGING) within 30s
**Action:** Payload detector integration begins. Image acquisition will start at the next valid scene trigger or commanded capture.
**GO/NO-GO:** Payload in IMAGING mode

### Step 5 — Trigger First Image Capture
**TC:** `PAYLOAD_CAPTURE(scene_id=1, lines=2048)` (Service 8, Subtype 1, func_id 28) — trigger the first-light acquisition  
**Wire:** `1C 00 01 08 00` (ground-assigned product id 1; geolocation is stamped on board from the ephemeris at shutter open)
**Action:** Payload captures one full-frame multispectral image. Integration time is automatic based on scene radiance estimate. Expected data volume: 50-200 MB depending on compression.
**Verify:** `GET_PARAM(param_id=0x060C)` (Service 20, Subtype 3) — `payload.last_scene_id` = 1 within 30 s
**Verify:** `GET_PARAM(param_id=0x0605)` (Service 20, Subtype 3) — `payload.image_count` incremented to 1 within 10 s
**GO/NO-GO:** First image captured successfully

### Step 6 — Verify Image Data Written to Storage
**TC:** `GET_PARAM(param_id=0x0605)` (Service 20, Subtype 3) — storage free
**Action:** Compare with pre-capture storage. Difference should correspond to image data volume.
**Verify:** Storage free decreased by 50-200 MB (image stored) within 10s
**TC:** `GET_PARAM(param_id=0x060B)` (Service 20, Subtype 3) — `payload.mem_used_mb` (stored size of the new product)
**Verify:** Image file size > 10 MB (not an empty or corrupted file) within 10s
**GO/NO-GO:** Image data stored on mass memory

### Step 7 — Return Payload to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15s
**Action:** Payload returns to STANDBY. FPA cooler continues running. Image data preserved in storage.
**GO/NO-GO:** Payload safely returned to STANDBY after capture

### Step 8 — Downlink the Product Over Service 13
**Action:** Downlink the first-light product with the real bulk-data chain.
Raise the ladder first: HIGH needs DOWNLINK mode + the deployed antenna
(COM-006); the 10-deg elevation rule is GROUND arm-through policy, not an
on-board gate.
**TC:** `TTC_SET_DOWNLINK_MODE(on=1)` (Service 8, Subtype 1, func_id 79)
**TC:** `TTC_SET_DATA_RATE(rate=1)` (Service 8, Subtype 1, func_id 65) — HIGH
**Verify:** `ttc.tm_data_rate` (0x0506) = 128000 bps within 10 s
**TC:** `S13_CREATE_TRANSFER(scene_id=1, size_mb=0)` (Service 13, Subtype 1) — open the transfer against the catalogued first-light product
**Verify:** TM(13,2) transfer-open report received within 10 s — record the
transfer id, `total_blocks` and the CRC-32 (the ground checks the reassembled
product against it)
**Verify:** `payload.transfer_active` (0x062D) = 1 within 15 s
**Verify:** `payload.transfer_progress` (0x062E) increasing over successive
SID 5 samples — the transfer drains at the ACTIVE information rate and stalls
if the link drops
**TC:** `S13_REPORT_STATUS(transfer_id=T)` (Service 13, Subtype 9) — status mid-dump
**TC:** `S13_END_TRANSFER(transfer_id=T)` (Service 13, Subtype 5) — close the session
**Verify:** `payload.transfer_active` (0x062D) = 0 within 15 s
**Note:** Closing a FULLY-dumped session deletes the product on board and
returns its storage; a partial session leaves it for the next pass.
**TC:** `TTC_SET_DATA_RATE(rate=0)` (Service 8, Subtype 1, func_id 65) — back to LOW
**TC:** `TTC_SET_DOWNLINK_MODE(on=0)` (Service 8, Subtype 1, func_id 79) — release the gate
**Verify:** `ttc.tm_data_rate` (0x0506) = 16000 bps and `ttc.downlink_mode` (0x053C) = 0
**Action:** CRC-32 the reassembled product on the ground against the TM(13,2)
value; payload team assesses geometry, band presence, dead pixels, content.
**GO/NO-GO:** Product downlinked over S13, checksum OK, HIGH released

### Step 9 — First Light Assessment
**Action:** Payload team performs preliminary assessment of the preview image:
- Scene geometry matches predicted ground track position
- All multispectral bands present in data
- No systematic image artifacts (striping, banding, saturation)
- FPA dark current consistent with the -15C operating setpoint
- Image not blank or fully saturated
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) — release FINE_POINT, back to commanded NOMINAL
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — AOCS state readback
**Verify:** `aocs.mode` (0x020F) = 4 within 30s
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — final payload state check
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `payload.fpa_temp` (0x0601) in range [-16.5C, -14.5C] (cooler maintained) within 10s
**Action:** Record First Light timestamp, image metadata, and preliminary quality assessment. Distribute First Light Report.
**GO/NO-GO:** First Light milestone achieved — imaging chain functional

## Off-Nominal Handling
- If FINE_POINT not achieved before target overpass: the capture gate REQUIRES FINE_POINT with total error < 0.05 deg — it will refuse in NOMINAL. Re-run the fine-ready check, fix the named mask bit, and take the next overpass.
- If payload does not transition to IMAGING (mode 2): Retry once. If persistent, check `GET_PARAM(param_id=0x0603)` self-test and `GET_PARAM(param_id=0x0601)` FPA temperature. Payload may refuse IMAGING if FPA not at setpoint. Return to STANDBY and troubleshoot.
- If `payload.last_scene_id` does not advance: check the capture gates — `payload.mode` (0x0600) = 2, `payload.fpa_ready` (0x0610) = 1, `aocs.mode` (0x020F) = 5 FINE_POINT with total error (0x02AA) < 0.05 deg. A refused capture returns TM(1,8) naming the failed gate. If detector integration failed, retry capture. If persistent, check FPA electronics via `HK_REQUEST(sid=5)`. Power cycle payload if needed.
- If image file size = 0 or very small (< 1 MB): Data write failure. Check mass memory interface. Retry capture. If storage full, delete test data and retry.
- If preview shows fully dark image: Check if spacecraft was over nightside. Verify AOCS pointing was nadir. If dayside and dark, investigate FPA detector bias or shutter mechanism.
- If the product is saturated (`payload.snr` 0x0616 collapses): the integration time is too long for the scene. Command `PAYLOAD_SET_INTEGRATION_TIME(blue=1.00, green=1.00, red=1.00, nir=1.00 ms)` (func_id 34, wire `22 00 64 00 64 00 64 00 64`) or reduce the gain with `PAYLOAD_SET_GAIN(gain=0.500)` (func_id 35, wire `23 01 F4`) and retry on the next opportunity.

## Post-Conditions
- [ ] First image captured and stored on mass memory
- [ ] Product downlinked over S13 with CRC-32 verified
- [ ] Scene geometry, spectral content, and image quality preliminary PASS
- [ ] Payload returned to STANDBY (cooler still running)
- [ ] Image metadata and quality metrics recorded
- [ ] First Light Report distributed to mission team

## Exit state
- Payload STANDBY, cooler running, `payload.transfer_active` (0x062D) = 0
- AOCS returned to commanded NOMINAL (mode 4) after the imaging window
- Downlink back at LOW, DOWNLINK mode released — the HIGH gate is never left
  open at card exit
- First-light product dumped (deleted on board if the session completed)

**NEXT:** COM-011 (Payload Radiometric Calibration)
