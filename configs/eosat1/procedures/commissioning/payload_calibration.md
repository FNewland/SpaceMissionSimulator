# COM-011: Payload Calibration
**Subsystem:** Payload
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Perform initial radiometric calibration of the multispectral imager. Capture dark
frames with the shutter closed to characterize FPA dark current and fixed-pattern
noise at the operational temperature. Acquire images over a known radiometric
calibration site for absolute calibration verification. Validate detector linearity
and inter-band registration.

## Prerequisites
- [ ] COM-012 (First Light) completed — imaging chain verified
- [ ] Payload in STANDBY (mode 1) with FPA cooler running
- [ ] `payload.fpa_temp` (0x0601) stable at the commanded setpoint (-15C: band -16.5C to -14.5C) and `payload.fpa_ready` (0x0610) = 1 for > 10 minutes
- [ ] AOCS capable of FINE_POINT (mode 4) with < 0.1 deg accuracy
- [ ] `eps.bat_soc` (0x0101) > 70%
- [ ] Calibration ground site overpass predicted within current or next orbit
- [ ] Full image downlink pass scheduled within 6 hours
- [ ] Bidirectional S-band link active
- [ ] Payload calibration engineer on console

## Procedure Steps

### Step 1 — Verify Pre-Calibration State
**TC:** `HK_REQUEST(sid=6)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `payload.fpa_temp` (0x0601) in range [-32C, -28C] within 10s
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — storage free
**Verify:** Storage free > 1000 MB (calibration data set ~500 MB) within 10s
**GO/NO-GO:** Payload and storage ready for calibration sequence

### Step 2 — Close Shutter for Dark Frame Acquisition
**TC:** `PAYLOAD_CYCLE_SHUTTER(cycles=1)` (Service 8, Subtype 1, func_id 93) — cycle to CLOSED  
**Wire:** `5D 01`
**Verify:** `GET_PARAM(0x063C)` (Service 20, Subtype 3) — `payload.shutter_pos` = 0 (CLOSED) within 10 s
**Action:** With shutter closed, detector sees no external light. Any signal is dark current plus readout noise.
**GO/NO-GO:** Shutter confirmed closed

### Step 3 — Capture Dark Frame Set
**TC:** `PAYLOAD_SET_MODE(mode=2)` (Service 8, Subtype 1) — IMAGING
**Verify:** `payload.mode` (0x0600) = 2 (IMAGING) within 30s
**TC:** `PAYLOAD_START_CALIBRATION(mode=0 DARK)` (Service 8, Subtype 1, func_id 37) — acquire the dark-frame set (10 frames)  
**Wire:** `25 00`
**Action:** Payload captures 10 consecutive dark frames at the standard integration time. Multiple frames allow averaging for noise characterization.
**Verify:** `GET_PARAM(0x062A)` (Service 20, Subtype 3) — `payload.calibration_active` returns to 0 within 60 s
**Verify:** `GET_PARAM(0x0642)` (Service 20, Subtype 3) — `payload.dark_frame_cnt` = 10 within 60 s
**GO/NO-GO:** Dark frame set captured

### Step 4 — Verify Dark Frame Data Quality
**TC:** `GET_PARAM(0x0642)` (Service 20, Subtype 3) — dark frames acquired
**TC:** `GET_PARAM(0x0616)` (Service 20, Subtype 3) — `payload.snr` (dB) with the dark set applied
**Verify:** Mean dark signal < 100 DN (12-bit scale, 0-4095) within 10s
**Verify:** Dark signal std dev < 10 DN within 10s
**Action:** Low mean indicates minimal dark current at the -15C setpoint. Low std dev indicates uniform FPA response. Record values as baseline.
**GO/NO-GO:** Dark frame statistics within specification

### Step 5 — Open Shutter and Return to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1) — STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15s
**TC:** `PAYLOAD_CYCLE_SHUTTER(cycles=1)` (Service 8, Subtype 1, func_id 93) — cycle back to OPEN  
**Wire:** `5D 01`
**Verify:** `GET_PARAM(0x063C)` (Service 20, Subtype 3) — `payload.shutter_pos` = 1 (OPEN) within 10 s
**GO/NO-GO:** Shutter open, payload in STANDBY

### Step 6 — Prepare for Calibration Site Overpass
**Action:** Transition AOCS to FINE_POINT for calibration image acquisition over known ground site.
**TC:** `AOCS_SET_MODE(mode=5)` (Service 8, Subtype 1) — check
`aocs.fine_ready` (0x02C5) = 0 first; the command is refused otherwise
**Verify:** `aocs.mode` (0x020F) = 5 (FINE_POINT) within 30s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 300s
**Action:** Wait for calibration site to enter imaging swath. Ground predicts overpass at T=XX:XX UTC +/- 30s.
**GO/NO-GO:** FINE_POINT achieved, awaiting calibration site overpass

### Step 7 — Capture Calibration Site Image
**TC:** `PAYLOAD_SET_MODE(mode=2)` (Service 8, Subtype 1) — IMAGING
**Verify:** `payload.mode` (0x0600) = 2 (IMAGING) within 30s
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (Service 8, Subtype 1, func_id 28) — trigger the capture at the site overpass  
**Wire:** `1C 00 00 08 00` (scene_id 0 = on-board auto-assign; geolocation is stamped on board)
**Verify:** `GET_PARAM(0x060C)` (Service 20, Subtype 3) — `payload.last_scene_id` increments within 30 s
**Action:** Record image timestamp, spacecraft position (GPS), sun angle, and view geometry for radiometric analysis.
**GO/NO-GO:** Calibration site image captured

### Step 8 — Capture Second Calibration Image (Different Integration Time)
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue=1.00, green=1.00, red=1.00, nir=1.00 ms)` (func_id 34) — half nominal  
**Wire:** `22 00 64 00 64 00 64 00 64` (0.01 ms/LSB)
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (func_id 28) — trigger capture  
**Wire:** `1C 00 00 08 00`
**Verify:** `GET_PARAM(0x0616)` (Service 20, Subtype 3) — `payload.snr` drops ~3 dB per halving
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue=4.00, green=4.00, red=4.00, nir=4.00 ms)` (func_id 34) — double nominal  
**Wire:** `22 01 90 01 90 01 90 01 90`
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (func_id 28) — trigger capture  
**Wire:** `1C 00 00 08 00`
**Verify:** `GET_PARAM(0x0616)` (Service 20, Subtype 3) — `payload.snr` rises ~3 dB per doubling
**Action:** Three images at different integration times (nominal, half, double) allow detector linearity assessment. If signal doubles when integration time doubles, detector response is linear.
**GO/NO-GO:** Linearity image set captured

### Step 9 — Return Payload to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1) — STANDBY
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 15s
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue=2.00, green=2.00, red=2.00, nir=2.00 ms)` (func_id 34) — restore nominal  
**Wire:** `22 00 C8 00 C8 00 C8 00 C8`
**Verify:** Integration time restored within 10s
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1) — return to NOMINAL_POINT
**Verify:** `aocs.mode` (0x020F) = 3 (NOMINAL_POINT) within 30s
**GO/NO-GO:** Payload and AOCS returned to standard configuration

### Step 10 — Verify Data Stored and Schedule Downlink
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — storage free
**Action:** Confirm all calibration data stored (10 dark frames + 3 calibration images).
**Verify:** Storage consumption consistent with ~13 frames within 10s
**TC:** `GET_PARAM(0x0605)` (Service 20, Subtype 3) — `payload.image_count`
**Action:** Schedule full data downlink during next available ground station pass. Calibration engineer will perform offline analysis including:
- Dark current map generation from averaged dark frames
- Fixed-pattern noise characterization
- Absolute radiometric calibration against known site reflectance
- Detector linearity assessment from multi-integration-time set
- Inter-band registration check
**GO/NO-GO:** Calibration data collected and downlink scheduled

## Off-Nominal Handling
- If dark frame mean > 200 DN: FPA temperature may have drifted warm. Check `payload.fpa_temp` (0x0601). If > -28C, wait for cooler to re-stabilize. If at setpoint, elevated dark current may indicate FPA degradation — log anomaly, proceed with calibration.
- If shutter does not close: Attempt `PAYLOAD_CYCLE_SHUTTER(cycles=1)` again. If persistent, shutter mechanism may be stuck. Skip dark frame calibration. Acquire dark frames during eclipse nightside pass as alternative (natural darkness instead of mechanical shutter).
- If calibration site obscured by cloud: Proceed with capture anyway for detector exercise. Schedule repeat calibration over an alternative clear site (e.g., Sahara, Antarctica, ocean). Multiple calibration opportunities exist per week.
- If linearity images show non-linear response (> 5% deviation from expected): Log anomaly. May indicate detector saturation, ADC issue, or compression artifact. Capture additional images at finer integration time steps for detailed characterization.
- If image frame count does not increment: Data storage write failure. Check mass memory via `GET_PARAM(0x0604)` (Service 20, Subtype 3). If storage interface fault, attempt payload power cycle. If persistent, investigate OBDH mass memory controller.
- If FPA temperature drifts during calibration: Cooler may be struggling under imaging load (detector dissipation). Monitor trend. If > -25C, halt calibration and return to STANDBY. Investigate cooler capacity.

## Post-Conditions
- [ ] 10 dark frames captured at the operational FPA setpoint (-15C)
- [ ] Dark current mean < 100 DN, std dev < 10 DN
- [ ] Calibration site image captured with metadata (position, geometry, timestamp)
- [ ] Multi-integration-time image set captured for linearity assessment
- [ ] All calibration data stored on mass memory
- [ ] Data downlink scheduled
- [ ] Payload returned to STANDBY, cooler running
- [ ] AOCS returned to NOMINAL_POINT
- [ ] Radiometric Calibration Report to be generated after data analysis
- [ ] Payload commissioning complete — GO for nominal operations phase
