# COM-011: Payload Calibration
**Subsystem:** Payload
**Phase:** COMMISSIONING
**Revision:** 1.1 --- 2026-08c: entry/exit states declared; runs AFTER COM-012
(first light) per the definitive commissioning order.
**Approved:** Flight Operations Director

**Entry state:** COM-012 exit state --- imaging chain verified end to end,
payload STANDBY with the cooler regulating (`payload.fpa_ready` 0x0610 = 1),
AOCS commanded NOMINAL, downlink LOW.

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
- [ ] AOCS capable of FINE_POINT — that is **mode 5**, not 4 (4 is NOMINAL) —
      with < 0.1 deg accuracy. Since Phase 1, FINE is legal on a THREE-wheel
      pyramid; `aocs.fine_degraded` (0x0299) = 1 says so, and a degraded FINE is
      still permitted to capture.
- [ ] `eps.bat_soc` (0x0101) > 70%
- [ ] Calibration ground site overpass predicted within current or next orbit
- [ ] Full image downlink pass scheduled within 6 hours
- [ ] Bidirectional S-band link active
- [ ] Payload calibration engineer on console

## Procedure Steps

### Step 1 — Verify Pre-Calibration State
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27)
**Verify:** `payload.mode` (0x0600) = 1 (STANDBY) within 10s
**Verify:** `payload.fpa_temp` (0x0601) in range **[-16.5 C, -14.5 C]** — the band
about the nominal -15 C setpoint — within 10 s
**Verify:** `payload.fpa_ready == 1` (0x0610, timeout 10 s)
**Note:** This step demanded [-32 C, -28 C]. The commandable setpoint envelope is
**-20 C to 0 C** (`subsystems/tcs.yaml`), so -32 to -28 C was outside anything the
spacecraft could be commanded to reach: the check could never pass. Verify
`fpa_ready` rather than a remembered number — readiness follows the ACTIVE
setpoint, so it stays correct if the campaign runs at -20 C instead.
**TC:** `GET_PARAM(param_id=0x060B)` (Service 20, Subtype 3) — `payload.mem_used_mb`
**TC:** `GET_PARAM(param_id=0x060A)` (Service 20, Subtype 3) — `payload.mem_total_mb`
**Verify:** `mem_total_mb - mem_used_mb` > 1000 MB within 10 s. (0x0605 is
`payload.image_count`, not free storage.)
**GO/NO-GO:** Payload and storage ready for calibration sequence

### Step 2 — Close the Shutter for Dark Frame Acquisition
**TC:** `PAYLOAD_SET_SHUTTER(position=0)` (Service 8, Subtype 1, func_id 119) — CLOSED
**Wire:** `77 00`
**TC:** `GET_PARAM(param_id=0x063C)` (Service 20, Subtype 3) — `payload.shutter_pos`
**Verify:** `payload.shutter_pos == 0` (0x063C, timeout 10 s) — CLOSED
**Note:** Func 119 is new in Phase 4 and is an ABSOLUTE position command. This
step used `PAYLOAD_CYCLE_SHUTTER(cycles=1)` and called it "cycle to CLOSED",
which is a coin toss: cycling counts blade TRANSITIONS, so one cycle from an
already-closed shutter ends OPEN. There was previously no way to reach a known
shutter position at all, and the dark-frame precondition below is absolute.
**GO/NO-GO:** Shutter confirmed CLOSED

### Step 3 — Capture the Dark Frame Set
**Note:** Calibration is now gated on the OPTICS, not on the mode word, and it
is **refused from IMAGING** with `code="CAL_REFUSED_IMAGING"` — "a calibration
frame must not see the scene". This step used to command IMAGING first, which
is exactly the defect Phase 4 removed. Start from STANDBY (mode 1); the payload
promotes itself to the new **mode 3 = CALIBRATION** for the duration.
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**TC:** `PAYLOAD_CAL_LAMP(on=0)` (Service 8, Subtype 1, func_id 95) — lamp OFF for a dark frame
**TC:** `PAYLOAD_START_CALIBRATION(mode=0)` (Service 8, Subtype 1, func_id 37) — DARK phase
**Wire:** `25 00`
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**Verify:** `payload.mode == 3` (0x0600, timeout 15 s) — CALIBRATION
**Verify:** `payload.calibration_active == 1` (0x062A, timeout 15 s)
**Note:** The four preconditions the model enforces, in the order it checks
them: the payload must not be OFF; it must not be in IMAGING; the FPA device
(0x0600) must be powered; `fpa_ready` must be 1; and for a DARK phase the
shutter device (0x0603) must be powered and `shutter_pos` must be 0.
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**Verify:** `payload.calibration_active == 0` (0x062A, timeout 120 s) — the run completed
**Verify:** `payload.cal_abort_reason == 0` (0x0645, timeout 10 s) — it completed
rather than stopping early
**Note:** `cal_abort_reason` (0x0645) is the parameter that says WHY a
calibration produced nothing: 1 detector unpowered, 2 shutter moved, 3 lamp
state changed, 4 FPA left its regulation band, 5 operator abort. Without it the
operator sees a progress bar that stopped and no reason anywhere.
**Verify:** `payload.dark_frame_cnt` (0x0642) — dark frames acquired, non-zero
**GO/NO-GO:** Dark frame set captured with `cal_abort_reason` = 0

### Step 3a — Capture the Flat Field Set
**Note:** Either half completes the other. `payload.cal_valid_mask` (0x0646)
reaches `0x0F` when all four bands hold BOTH a bias and a gain coefficient, so
DARK-then-FLAT and FLAT-then-DARK both work; `PAYLOAD_START_CALIBRATION(mode=2)`
does both in one command with the on-board sequencer driving the optics across
the phase boundary.
**TC:** `PAYLOAD_SET_SHUTTER(position=1)` (Service 8, Subtype 1, func_id 119) — OPEN
**TC:** `PAYLOAD_CAL_LAMP(on=1)` (Service 8, Subtype 1, func_id 95) — internal lamp ON
**TC:** `GET_PARAM(param_id=0x0615)` (Service 20, Subtype 3) — `payload.cal_lamp_on`
**Verify:** `payload.cal_lamp_on == 1` (0x0615, timeout 10 s)
**Verify:** `payload.shutter_pos == 1` (0x063C, timeout 10 s) — OPEN
**TC:** `PAYLOAD_START_CALIBRATION(mode=1)` (Service 8, Subtype 1, func_id 37) — FLAT phase
**Wire:** `25 01`
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**Verify:** `payload.calibration_active == 0` (0x062A, timeout 120 s)
**Verify:** `payload.cal_abort_reason == 0` (0x0645, timeout 10 s)
**Verify:** `payload.cal_valid_mask == 15` (0x0646, timeout 10 s) — all four bands
(B G R N) now hold both a bias and a gain coefficient
**TC:** `PAYLOAD_CAL_LAMP(on=0)` (Service 8, Subtype 1, func_id 95) — lamp OFF
**GO/NO-GO:** `cal_valid_mask` = 0x0F — the instrument is fully calibrated

### Step 4 — Verify Dark Frame Data Quality
**TC:** `GET_PARAM(param_id=0x0642)` (Service 20, Subtype 3) — dark frames acquired
**TC:** `GET_PARAM(param_id=0x0616)` (Service 20, Subtype 3) — `payload.snr` (dB) with the dark set applied
**Verify:** Mean dark signal < 100 DN (12-bit scale, 0-4095) within 10s
**Verify:** Dark signal std dev < 10 DN within 10s
**Action:** Low mean indicates minimal dark current at the -15C setpoint. Low std dev indicates uniform FPA response. Record values as baseline.
**GO/NO-GO:** Dark frame statistics within specification

### Step 5 — Open the Shutter and Return to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**TC:** `PAYLOAD_SET_SHUTTER(position=1)` (Service 8, Subtype 1, func_id 119) — OPEN
**Wire:** `77 01`
**TC:** `HK_REQUEST(sid=5)` (Service 3, Subtype 27) — payload housekeeping
**Verify:** `payload.mode == 1` (0x0600, timeout 15 s) — STANDBY
**Verify:** `payload.shutter_pos == 1` (0x063C, timeout 10 s) — OPEN
**GO/NO-GO:** Shutter open, payload in STANDBY

### Step 6 — Prepare for Calibration Site Overpass
**Action:** Transition AOCS to FINE_POINT for calibration image acquisition over known ground site.
**TC:** `AOCS_FINE_READY_CHECK` (Service 8, Subtype 1, func_id 89) — read the precondition mask
**Verify:** `aocs.fine_ready == 0` (0x02C5, timeout 10 s) — every precondition bit clear
**TC:** `AOCS_SET_MODE(mode=5)` (Service 8, Subtype 1, func_id 0) — FINE_POINT
**Verify:** `aocs.mode` (0x020F) = 5 (FINE_POINT) within 30s
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 300s
**Action:** Wait for calibration site to enter imaging swath. Ground predicts overpass at T=XX:XX UTC +/- 30s.
**GO/NO-GO:** FINE_POINT achieved, awaiting calibration site overpass

### Step 7 — Capture Calibration Site Image
**TC:** `PAYLOAD_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 26) — IMAGING
**Verify:** `payload.mode == 2` (0x0600, timeout 30 s) — IMAGING
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (Service 8, Subtype 1, func_id 28) — trigger the capture at the site overpass  
**Wire:** `1C 00 00 08 00` (scene_id 0 = on-board auto-assign; geolocation is stamped on board)
**Verify:** `GET_PARAM(param_id=0x060C)` (Service 20, Subtype 3) — `payload.last_scene_id` increments within 30 s
**Action:** Record image timestamp, spacecraft position (GPS), sun angle, and view geometry for radiometric analysis.
**GO/NO-GO:** Calibration site image captured

### Step 8 — Capture Second Calibration Image (Different Integration Time)
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue_ms=1.00, green_ms=1.00, red_ms=1.00, nir_ms=1.00)` (Service 8, Subtype 1, func_id 34) — half nominal  
**Wire:** `22 00 64 00 64 00 64 00 64` (0.01 ms/LSB)
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (Service 8, Subtype 1, func_id 28) — trigger capture  
**Wire:** `1C 00 00 08 00`
**Verify:** `GET_PARAM(param_id=0x0616)` (Service 20, Subtype 3) — `payload.snr` drops ~3 dB per halving
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue_ms=4.00, green_ms=4.00, red_ms=4.00, nir_ms=4.00)` (Service 8, Subtype 1, func_id 34) — double nominal  
**Wire:** `22 01 90 01 90 01 90 01 90`
**TC:** `PAYLOAD_CAPTURE(scene_id=0, lines=2048)` (Service 8, Subtype 1, func_id 28) — trigger capture  
**Wire:** `1C 00 00 08 00`
**Verify:** `GET_PARAM(param_id=0x0616)` (Service 20, Subtype 3) — `payload.snr` rises ~3 dB per doubling
**Action:** Three images at different integration times (nominal, half, double) allow detector linearity assessment. If signal doubles when integration time doubles, detector response is linear.
**GO/NO-GO:** Linearity image set captured

### Step 9 — Return Payload to STANDBY
**TC:** `PAYLOAD_SET_MODE(mode=1)` (Service 8, Subtype 1, func_id 26) — STANDBY
**Verify:** `payload.mode == 1` (0x0600, timeout 15 s) — STANDBY
**TC:** `PAYLOAD_SET_INTEGRATION_TIME(blue_ms=2.00, green_ms=2.00, red_ms=2.00, nir_ms=2.00)` (Service 8, Subtype 1, func_id 34) — restore nominal  
**Wire:** `22 00 C8 00 C8 00 C8 00 C8`
**Verify:** Integration time restored within 10s
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) — return to NOMINAL
**Verify:** `aocs.mode == 4` (0x020F, timeout 30 s) — NOMINAL
**Note:** Mode 3 is COARSE_SUN, a sun-pointing HOLD with no nadir pointing; the
nadir-pointing operational mode is 4. The card called mode 3 "NOMINAL".
**GO/NO-GO:** Payload and AOCS returned to standard configuration

### Step 10 — Verify Data Stored and Schedule Downlink
**TC:** `GET_PARAM(param_id=0x0647)` (Service 20, Subtype 3) — `payload.product_mb`
**Action:** Confirm all calibration data stored (10 dark frames + 3 calibration images).
**Verify:** Storage consumption consistent with ~13 frames within 10s
**TC:** `GET_PARAM(param_id=0x0648)` (Service 20, Subtype 3) — `payload.product_count`
**Action:** Schedule full data downlink during next available ground station pass. Calibration engineer will perform offline analysis including:
- Dark current map generation from averaged dark frames
- Fixed-pattern noise characterization
- Absolute radiometric calibration against known site reflectance
- Detector linearity assessment from multi-integration-time set
- Inter-band registration check
**GO/NO-GO:** Calibration data collected and downlink scheduled

## Off-Nominal Handling
- If dark frame mean > 200 DN: FPA temperature may have drifted warm. Check `payload.fpa_temp` (0x0601). If it is warmer than -14.5 C, wait for the cooler to re-stabilise and check
  `payload.cooler_margin_k` (0x064B). If at setpoint, elevated dark current may indicate FPA degradation — log anomaly, proceed with calibration.
- If the shutter does not close: re-send `PAYLOAD_SET_SHUTTER(position=0)` (func 119).
  Do NOT use `PAYLOAD_CYCLE_SHUTTER` to reach a known position — it counts blade
  transitions, so an odd count ends on the OPPOSITE position from where it started. If persistent, shutter mechanism may be stuck. Skip dark frame calibration. Acquire dark frames during eclipse nightside pass as alternative (natural darkness instead of mechanical shutter).
- If calibration site obscured by cloud: Proceed with capture anyway for detector exercise. Schedule repeat calibration over an alternative clear site (e.g., Sahara, Antarctica, ocean). Multiple calibration opportunities exist per week.
- If linearity images show non-linear response (> 5% deviation from expected): Log anomaly. May indicate detector saturation, ADC issue, or compression artifact. Capture additional images at finer integration time steps for detailed characterization.
- If image frame count does not increment: Data storage write failure. Check mass memory via `GET_PARAM(param_id=0x060B)` — `payload.mem_used_mb` — and
  `payload.product_count` (0x0648), which counts CATALOGUED products rather than
  raw fill. If storage interface fault, attempt payload power cycle. If persistent, investigate OBDH mass memory controller.
- If FPA temperature drifts during calibration: Cooler may be struggling under imaging load (detector dissipation). Monitor the trend. A calibration that loses the regulation band aborts itself with
  `cal_abort_reason` (0x0645) = 4 and discards the half-made coefficients, which is
  correct behaviour. Check `payload.cooler_margin_k` (0x064B): the imaging load adds
  2 W and costs about 1.1 K of achievable lift, so a campaign that holds while idle
  can lose regulation the moment the detector is read out. Investigate cooler capacity.

## Post-Conditions
- [ ] Dark frame set captured at the operational FPA setpoint (-15 C) with
      `payload.cal_abort_reason` (0x0645) = 0
- [ ] Flat field set captured; `payload.cal_valid_mask` (0x0646) = 0x0F
- [ ] Dark current mean < 100 DN, std dev < 10 DN
- [ ] Calibration site image captured with metadata (position, geometry, timestamp)
- [ ] Multi-integration-time image set captured for linearity assessment
- [ ] All calibration data stored on mass memory
- [ ] Data downlink scheduled
- [ ] Payload returned to STANDBY, cooler running
- [ ] AOCS returned to NOMINAL (mode 4)
- [ ] Radiometric Calibration Report to be generated after data analysis

## Exit state
- Payload STANDBY, cooler running, shutter OPEN, integration times restored to
  nominal (2.00 ms), cal lamp OFF
- AOCS returned to commanded NOMINAL (mode 4)
- Calibration products stored on board awaiting the scheduled dump pass

**NEXT:** COM-013 (ADCS Commissioning Closure & NOMINAL Declaration)
