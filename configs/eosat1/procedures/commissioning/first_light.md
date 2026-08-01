# COM-012: First Light
**Subsystem:** Payload / AOCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Capture the first image with the EOSAT-1 multispectral imager. Transition the
payload from STANDBY to IMAGING mode, acquire a test scene over a known ground
target, verify image data storage on the mass memory, and downlink a preview for
initial quality assessment. This milestone confirms end-to-end imaging capability.

## Prerequisites
- [ ] COM-009 (Payload Power-On) completed — payload in STANDBY
- [ ] COM-010 (FPA Cooler Activation) completed — `payload.fpa_ready` (0x0610) = 1 at the commanded setpoint (-15C: band -16.5C to -14.5C)
- [ ] AOCS in NOMINAL_POINT (mode 3) or FINE_POINT (mode 4) — nadir-pointing
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
**TC:** `GET_PARAM(0x0601)` (Service 20, Subtype 3) — FPA temperature
**Verify:** `payload.fpa_temp` (0x0601) in range [-32C, -28C] within 10s
**GO/NO-GO:** FPA at operational temperature

### Step 3 — Verify Payload Ready for Imaging
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**TC:** `GET_PARAM(0x0604)` (Service 20, Subtype 3) — data interface status
**Verify:** Data interface = READY (value 1) within 10s
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — storage free
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
**Verify:** `GET_PARAM(0x060C)` (Service 20, Subtype 3) — `payload.last_scene_id` = 1 within 30 s
**Verify:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — `payload.image_count` incremented to 1 within 10 s
**GO/NO-GO:** First image captured successfully

### Step 6 — Verify Image Data Written to Storage
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — storage free
**Action:** Compare with pre-capture storage. Difference should correspond to image data volume.
**Verify:** Storage free decreased by 50-200 MB (image stored) within 10s
**TC:** `GET_PARAM(0x060B)` (Service 20, Subtype 3) — `payload.mem_used_mb` (stored size of the new product)
**Verify:** Image file size > 10 MB (not an empty or corrupted file) within 10s
**GO/NO-GO:** Image data stored on mass memory

### Step 7 — Return Payload to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15s
**Action:** Payload returns to STANDBY. FPA cooler continues running. Image data preserved in storage.
**GO/NO-GO:** Payload safely returned to STANDBY after capture

### Step 8 — Downlink Image Preview
**Action:** Request low-resolution preview thumbnail for ground assessment. Full image downlink scheduled for dedicated data dump pass.
**TC:** `PAYLOAD_INITIATE_TRANSFER(scene_id=1)` (Service 8, Subtype 1, func_id 94) — start the on-board transfer of the product  
**Wire:** `5E 00 01`
**Verify:** `GET_PARAM(0x062E)` (Service 20, Subtype 3) — `payload.transfer_progress` reaches 100 % within 30 s
**Action:** Downlink preview via S-band TM. Payload team assesses image for: correct geometry, spectral band presence, no dead pixels in initial inspection, reasonable scene content.
**GO/NO-GO:** Preview image received and shows valid scene content

### Step 9 — First Light Assessment
**Action:** Payload team performs preliminary assessment of the preview image:
- Scene geometry matches predicted ground track position
- All multispectral bands present in data
- No systematic image artifacts (striping, banding, saturation)
- FPA dark current consistent with the -15C operating setpoint
- Image not blank or fully saturated
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27) — final payload state check
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `payload.fpa_temp` (0x0601) in range [-32C, -28C] (cooler maintained) within 10s
**Action:** Record First Light timestamp, image metadata, and preliminary quality assessment. Distribute First Light Report.
**GO/NO-GO:** First Light milestone achieved — imaging chain functional

## Off-Nominal Handling
- If FINE_POINT not achieved before target overpass: Capture image in NOMINAL_POINT (mode 3) with degraded pointing. Image quality will be reduced but first light objective still met. Note pointing accuracy in report.
- If payload does not transition to IMAGING (mode 2): Retry once. If persistent, check `GET_PARAM(0x0603)` self-test and `GET_PARAM(0x0601)` FPA temperature. Payload may refuse IMAGING if FPA not at setpoint. Return to STANDBY and troubleshoot.
- If `payload.last_scene_id` does not advance: check the capture gates — `payload.mode` (0x0600) = 2, `payload.fpa_ready` (0x0610) = 1, `aocs.mode` (0x020F) = 5 FINE_POINT with total error (0x02AA) < 0.05 deg. A refused capture returns TM(1,8) naming the failed gate. If detector integration failed, retry capture. If persistent, check FPA electronics via `HK_REQUEST(sid=6)`. Power cycle payload if needed.
- If image file size = 0 or very small (< 1 MB): Data write failure. Check mass memory interface. Retry capture. If storage full, delete test data and retry.
- If preview shows fully dark image: Check if spacecraft was over nightside. Verify AOCS pointing was nadir. If dayside and dark, investigate FPA detector bias or shutter mechanism.
- If the product is saturated (`payload.snr` 0x0616 collapses): the integration time is too long for the scene. Command `PAYLOAD_SET_INTEGRATION_TIME(blue=1.00, green=1.00, red=1.00, nir=1.00 ms)` (func_id 34, wire `22 00 64 00 64 00 64 00 64`) or reduce the gain with `PAYLOAD_SET_GAIN(gain=0.500)` (func_id 35, wire `23 01 F4`) and retry on the next opportunity.

## Post-Conditions
- [ ] First image captured and stored on mass memory
- [ ] Preview image downlinked and assessed
- [ ] Scene geometry, spectral content, and image quality preliminary PASS
- [ ] Payload returned to STANDBY (cooler still running)
- [ ] Image metadata and quality metrics recorded
- [ ] First Light Report distributed to mission team
- [ ] Full image downlink scheduled for next data dump pass
- [ ] GO decision for COM-011 (Payload Radiometric Calibration)
