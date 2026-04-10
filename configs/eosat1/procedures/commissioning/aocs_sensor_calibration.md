# COM-005: AOCS Sensor Calibration
**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Calibrate the AOCS sensor suite: perform gyroscope bias estimation, magnetometer
calibration against the on-board IGRF model, and star tracker verification for
fine attitude determination. Establish calibrated sensor baselines required for
transitioning to fine-pointing mode.

## Prerequisites
- [ ] COM-001 through COM-004 completed
- [ ] AOCS in SAFE_POINT mode (mode 2) with stable sun-pointing
- [ ] Body rates < 0.1 deg/s on all axes
- [ ] `eps.bat_soc` (0x0101) > 60%
- [ ] OBC in NOMINAL mode
- [ ] Bidirectional VHF/UHF link active
- [ ] At least 15 minutes of ground station pass remaining

## Procedure Steps

### Step 1 — Record Pre-Calibration AOCS State
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 10s
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s within 10s
**Action:** Record baseline rates. These include gyro bias contribution.
**GO/NO-GO:** AOCS stable for calibration

### Step 2 — Initiate Gyroscope Bias Calibration
**TC:** `SET_PARAM(0x0250, 1)` (Service 20, Subtype 3) — start gyro bias estimation
**Action:** On-board algorithm averages gyro output over a quiescent period (body rates near zero) to estimate bias offsets for all three axes. Duration: 5 minutes.
**Verify:** `GET_PARAM(0x0251)` — gyro cal status = IN_PROGRESS (value 1) within 10s
**Action:** Wait 300 seconds for calibration to complete.
**Verify:** `GET_PARAM(0x0251)` — gyro cal status = COMPLETE (value 2) within 310s
**GO/NO-GO:** Gyro bias estimation completed

### Step 3 — Verify Gyro Bias Values
**TC:** `GET_PARAM(0x0252)` (Service 20, Subtype 1) — gyro X bias (deg/s)
**TC:** `GET_PARAM(0x0253)` (Service 20, Subtype 1) — gyro Y bias (deg/s)
**TC:** `GET_PARAM(0x0254)` (Service 20, Subtype 1) — gyro Z bias (deg/s)
**Verify:** |gyro X bias| < 0.5 deg/s within 10s
**Verify:** |gyro Y bias| < 0.5 deg/s within 10s
**Verify:** |gyro Z bias| < 0.5 deg/s within 10s
**Action:** Record bias values. Typical MEMS gyro bias: 0.01 to 0.1 deg/s. Values > 0.5 deg/s indicate potential sensor issue.
**GO/NO-GO:** Gyro bias values within expected range

### Step 4 — Initiate Magnetometer Calibration
**TC:** `SET_PARAM(0x0260, 1)` (Service 20, Subtype 3) — start magnetometer calibration
**Action:** On-board algorithm compares measured magnetic field vector with IGRF model prediction for current orbit position. Estimates hard-iron and soft-iron correction parameters. Requires ~10 minutes (partial orbit) for adequate geometric diversity.
**Verify:** `GET_PARAM(0x0261)` — mag cal status = IN_PROGRESS (value 1) within 10s
**Action:** Wait 600 seconds. Spacecraft will continue in SAFE_POINT mode during calibration.
**Verify:** `GET_PARAM(0x0261)` — mag cal status = COMPLETE (value 2) within 620s
**GO/NO-GO:** Magnetometer calibration completed

### Step 5 — Verify Magnetometer Calibration Quality
**TC:** `GET_PARAM(0x0262)` (Service 20, Subtype 1) — mag calibration residual (nT)
**Verify:** Mag calibration residual < 500 nT within 10s
**Action:** Record residual. A residual < 200 nT indicates excellent calibration. Values 200-500 nT are acceptable. Above 500 nT requires recalibration with more orbit arc.
**GO/NO-GO:** Magnetometer calibration residual within acceptable limits

### Step 6 — Power On Star Tracker
**TC:** `SET_PARAM(0x0270, 1)` (Service 20, Subtype 3) — star tracker power ON
**Action:** Star tracker requires 60 seconds for boot and initial star acquisition.
**Verify:** `GET_PARAM(0x0271)` — star tracker status = TRACKING (value 2) within 90s
**TC:** `GET_PARAM(0x0272)` (Service 20, Subtype 1) — number of tracked stars
**Verify:** Tracked stars >= 5 within 90s
**GO/NO-GO:** Star tracker operational and tracking stars

### Step 7 — Verify Star Tracker Attitude Solution
**TC:** `GET_PARAM(0x0273)` (Service 20, Subtype 1) — star tracker quaternion quality
**Verify:** Quaternion quality flag = VALID (value 1) within 10s
**Action:** Compare star tracker attitude solution with magnetometer/sun sensor coarse solution. Cross-check angular difference.
**TC:** `GET_PARAM(0x0274)` (Service 20, Subtype 1) — attitude cross-check error (deg)
**Verify:** Cross-check error < 5.0 deg within 10s (coarse sensors vs star tracker)
**GO/NO-GO:** Star tracker solution consistent with coarse attitude estimate

### Step 8 — Enable Star Tracker in AOCS Loop
**TC:** `SET_PARAM(0x0275, 1)` (Service 20, Subtype 3) — enable star tracker in AOCS
**Verify:** `aocs.att_error` (0x0217) improves (decreases) within 60s
**Action:** With star tracker enabled, attitude determination accuracy improves from ~2 degrees (coarse) to < 0.01 degrees. Monitor for improved pointing accuracy.
**GO/NO-GO:** Star tracker integrated into AOCS attitude determination

### Step 9 — Post-Calibration Attitude Verification
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 30s
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s within 10s
**Action:** Record calibrated AOCS performance baseline.
**GO/NO-GO:** AOCS sensor calibration complete — improved pointing confirmed

## Off-Nominal Handling
- If gyro bias > 0.5 deg/s on any axis: Re-run calibration after ensuring rates are truly quiescent. If persistent, flag gyro unit for monitoring. AOCS can compensate for moderate bias with star tracker aiding.
- If magnetometer residual > 500 nT: Extend calibration over a full orbit for more geometric diversity. Check for spacecraft-generated magnetic interference (reaction wheel proximity). If residual remains high, use reduced magnetometer weighting in AOCS filter.
- If star tracker fails to acquire stars within 120s: Verify no bright objects (sun, moon) in field of view. Check baffle contamination flag via `GET_PARAM(0x0276)`. If sun in FOV, wait for attitude evolution. If persistent, check star tracker optics temperature.
- If star tracker quaternion quality = INVALID: Check tracked star count. If < 3 stars, may indicate FOV obstruction or catalogue mismatch. Power cycle star tracker via `SET_PARAM(0x0270, 0)` then `SET_PARAM(0x0270, 1)`.
- If cross-check error > 10 deg: Large discrepancy between coarse and fine attitude. Do not enable star tracker in AOCS loop. Investigate sensor alignment calibration. May require ground-based attitude reconstruction.

## Post-Conditions
- [ ] Gyroscope bias calibrated — values within specification
- [ ] Magnetometer calibrated — residual < 500 nT
- [ ] Star tracker operational — tracking >= 5 stars
- [ ] Star tracker attitude solution validated against coarse sensors
- [ ] Star tracker integrated into AOCS determination loop
- [ ] Post-calibration attitude error < 1.0 degree
- [ ] Sensor Calibration Report generated with all bias/residual values
- [ ] GO decision for COM-006 (Reaction Wheel Commissioning)
