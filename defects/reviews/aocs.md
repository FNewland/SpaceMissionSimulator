# AOCS/ADCS Review Report

## Scope and Assumptions

**Mission context:** EO satellite (eosat1) in LEO with optical Earth-observation payloads.

**Satellite AOCS architecture:**
- 4 reaction wheels (tetrahedron configuration) for 3-axis attitude control
- Dual star trackers (ST1, ST2) for fine pointing with cold redundancy and hot-swap capability
- Coarse Sun Sensor (CSS) with 6 body-mounted heads for safe-mode sun acquisition
- Dual magnetometers (Mag A primary, Mag B backup) for sensors and B-dot detumbling
- 3-axis magnetorquers (MTQ) for momentum dumping and initial detumble
- Rate gyroscopes with bias estimation and temperature monitoring
- GPS receiver (cold/warm/hot start support)

**Reference standards:** ECSS-E-ST-60-10C (control performance), ECSS-E-ST-60-30C (AOCS requirements), ECSS-E-ST-70-41C (PUS packet utilization), with 9-mode state machine and comprehensive failure injection.

**Review scope:** Model implementation (aocs_basic.py), telemetry definitions (hk_structures.yaml, parameters.yaml), command routing (S8, S20), and operability for LEOP/commissioning/nominal/contingency workflows.

---

## Category 1 — Described, Implemented, and Works

### 1.1 AOCS State Machine (9 Modes)
- **Status:** Fully implemented with explicit mode constants and automatic transitions.
- **Modes:** OFF (0), SAFE_BOOT (1), DETUMBLE (2), COARSE_SUN (3), NOMINAL (4), FINE_POINT (5), SLEW (6), DESAT (7), ECLIPSE (8).
- **Transitions:** Guard conditions, minimum dwell timers, emergency rate thresholds.
- **Test coverage:** `tests/test_simulator/test_aocs_state_machine.py` validates set_mode, auto-transitions, and state guards.
- **Evidence:** aocs_basic.py:268–322 (mode control), tick() method auto-transition logic.

### 1.2 Reaction Wheel Management
- **Implementation:** 4 active wheels with speed, temperature, and current telemetry.
- **Features:** Enable/disable per wheel, speed bias command, ramp-down command, bearing degradation model.
- **Desaturation:** DESAT mode via magnetorquers (B-dot momentum dump).
- **Momentum tracking:** Total momentum (Nms) computed from wheel speeds and inertia.
- **Test coverage:** Wheel enable/disable, speed control, thermal model validation.
- **Evidence:** aocs_basic.py:762–806, _tick_wheels(), handle_command() wheel control.

### 1.3 Dual Magnetometer (A/B) with Selection
- **Status:** Both magnetometer units modeled with independent biases and noise.
- **Mag A:** Primary, lower noise (50 nT sigma).
- **Mag B:** Backup, higher noise (75 nT sigma).
- **Selection:** Runtime mag_select command (A or B).
- **Failure injection:** Per-unit failure flags (mag_a_failed, mag_b_failed).
- **Evidence:** aocs_basic.py:47–60, handle_command mag_select (lines 1282–1300).

### 1.4 Dual Star Tracker (ST1/ST2) with Cold Redundancy
- **Status:** Fully modeled with OFF/BOOTING/TRACKING/BLIND/FAILED states.
- **Boot sequence:** 60-second boot timer (_ST_BOOT_TIME = 60.0).
- **Selection:** Runtime st_select command to switch primary tracker.
- **Blinding:** Sun/Moon/Earth exclusion angle (15 degrees).
- **Power control:** ST1_POWER (func_id 4), ST2_POWER (func_id 5) via S8.
- **Evidence:** aocs_basic.py:62–76, handle_command() lines 1254–1280, star tracker state transitions.

### 1.5 CSS (Coarse Sun Sensor) with 6 Heads
- **Status:** Modeled with 6 body-mounted heads (px, mx, py, my, pz, mz).
- **Per-head failure injection:** Individual CSS head failures tracked.
- **Composite sun vector:** Aggregated CSS_sun_x/y/z valid flag.
- **Normals:** CSS_NORMALS dict with unit vectors per face.
- **Evidence:** aocs_basic.py:84–89, _tick_css() function, per-head failure tracking.

### 1.6 Magnetorquer (3-axis MTQ) Control
- **Status:** Modeled with duty cycle command (0–100%) and per-axis failure flags.
- **B-dot implementation:** DETUMBLE mode uses rate-proportional duty (lines 557–559).
- **Enable/disable:** mtq_enable / mtq_disable commands.
- **Failure injection:** Per-axis (mtq_x_failed, mtq_y_failed, mtq_z_failed).
- **Evidence:** aocs_basic.py:524–550 (_tick_magnetorquers), handle_command lines 1310–1319.

### 1.7 Attitude Representation (Quaternion)
- **Status:** 4-component quaternion [x, y, z, w] with normalization and SLERP slew.
- **Kinematics:** Quaternion error calculation, axis extraction, rotation application.
- **Telemetry:** att_q1, att_q2, att_q3, att_q4 in HK SID 2.
- **Evidence:** aocs_basic.py:1018–1115 (quaternion math), _tick_slew() SLERP interpolation.

### 1.8 Attitude Error Tracking and Telemetry
- **Status:** Computed per mode (angle between current and target quaternion).
- **Deadband:** Configurable attitude_error_deadband_deg.
- **Telemetry:** att_error (0x0217) in HK SID 2, updated every 4 seconds.
- **Evidence:** aocs_basic.py:612–615 (nominal mode), HK structures line 66.

### 1.9 Rate Gyroscope with Bias Estimation
- **Status:** Gyro bias per-axis with temperature correlation and random walk drift.
- **Telemetry:** gyro_bias_x/y/z (0x0270–0x0272) and gyro_temp (0x0273).
- **Calibration command:** GYRO_CALIBRATION (func_id 13) resets bias to zero.
- **Evidence:** aocs_basic.py:723–738 (_tick_gyro_and_gps), handle_command line 1351–1354.

### 1.10 GPS Receiver Model
- **Status:** Modeled with fix quality (0=none, 1=2D, 2=3D, 3=3D+vel), satellite count, PDOP.
- **Cold start:** No fix until AOCS leaves OFF/SAFE_BOOT.
- **Telemetry:** gps_fix (0x0274), gps_num_sats (0x0276), gps_pdop (0x0275).
- **Evidence:** aocs_basic.py:739–753 (_tick_gyro_and_gps).

### 1.11 S8 Function Management Commands (0–15)
- **Status:** 16 AOCS functions routed via S8 subtype 1.
- **Implemented:** set_mode (0), desaturate (1), disable_wheel (2), enable_wheel (3), ST power (4/5), ST select (6), mag select (7), RW speed bias (8), MTQ control (9), slew (10), momentum check (11), acquisition (12), gyro calibration (13), RW ramp (14), deadband (15).
- **Power gating:** check_power_state() validates AOCS wheels power line before command dispatch.
- **Evidence:** service_dispatch.py:376–475 (_route_aocs_cmd).

### 1.12 S20 Parameter Management (Set/Get)
- **Status:** S20.1 (set), S20.3 (get) fully functional.
- **Interface:** Direct parameter dictionary access via param_id.
- **Evidence:** service_dispatch.py:1172–1185 (_handle_s20).

### 1.13 HK SID 2 (AOCS Housekeeping)
- **Status:** 51 parameters reported at 4-second interval.
- **Coverage:** Attitude (q, rates), wheels (speed, temp, current, enabled), sensors (ST, CSS, mag), MTQ duty, mode metadata.
- **Packing:** Efficient binary format with per-parameter scaling.
- **Evidence:** hk_structures.yaml:55–123 (SID 2 definition).

### 1.14 Safe Mode Recovery Procedure
- **Status:** Defined FDIR procedure (aocs_safe_mode_recovery.yaml).
- **Steps:** Transition to SAFE mode (func_id 0, mode=2), reset RW commands, reduce payload power.
- **Timeout:** 20 seconds with 2 retries.
- **Evidence:** configs/eosat1/fdir/procedures/aocs_safe_mode_recovery.yaml.

---

## Category 2 — Described but NOT Implemented

### 2.1 TLE (Two-Line Element) Upload and Management
- **Status:** Not implemented.
- **Expected:** Per ECSS, AOCS requires periodic TLE uploads for magnetic field model and orbit propagation.
- **Gap:** No S8 or S20 command for TLE upload/validation; no onboard mag field model using position/time.
- **Impact:** Operators cannot update orbit model in-flight; B-dot control assumes static mag field; GPS almanac/ephemeris initialization unsupported.
- **Reference:** ECSS-E-ST-60-30C § 8.4 (magnetic field model calibration).

### 2.2 Onboard Magnetic Field Model (IGRF/WMM)
- **Status:** Not implemented.
- **Expected:** Dynamic Earth magnetic field model (IGRF-13 or World Magnetic Model) as function of latitude/longitude/altitude/time.
- **Current:** Static mag field magnitude (50000 nT) and constant vector [25000, 10000, -40000] nT.
- **Gap:** B-dot control uses hardcoded field; no position-dependent Bx/By/Bz realism.
- **Impact:** DETUMBLE mode accuracy degraded; MTQ dipole predictions unreliable.
- **Evidence:** aocs_basic.py:42–44 (static mag vector), _tick_magnetometer() no model.

### 2.3 GPS Receiver Detailed State (Cold/Warm/Hot Start Tracking)
- **Status:** GPS fix modeled, but no explicit cold/warm/hot start state machine.
- **Expected:** Distinct TTFF (time-to-first-fix) and channel tracking per start mode.
- **Current:** Fix quality jumps randomly; no progressive acquisition state.
- **Gap:** Operators cannot distinguish true GPS cold-start behavior from receiver anomaly.
- **Impact:** LEOP sequencing cannot validate GPS acquisition progress.
- **Evidence:** aocs_basic.py:739–753 (random fix generation, no start-type tracking).

### 2.4 Magnetometer Bias Calibration and Alignment
- **Status:** Biases exist (mag_a_bias, mag_b_bias tuples) but are never updated or validated.
- **Expected:** S8 or S20 commands to calibrate mag bias (e.g., spin calibration, in-flight alignment).
- **Gap:** No command to set/adjust mag_a_bias or mag_b_bias; no bias validation telemetry.
- **Impact:** Operators cannot correct systematic magnetometer offsets in-flight.
- **Evidence:** aocs_basic.py:55–56 (static bias tuples), no mag_calibration command.

### 2.5 Star Tracker Exclusion Zone Enforcement
- **Status:** Exclusion angle constant exists (_ST_EXCLUSION_DEG = 15.0) but is never applied.
- **Expected:** ST status = BLIND when sun/moon/Earth within exclusion cone.
- **Current:** Sun aspect angle never computed against orbit state; ST_BLIND failure is only injectable, not automatic.
- **Gap:** During sun-pointing slews, ST will not automatically go BLIND (unrealistic for LEO).
- **Impact:** Fine-pointing validation incomplete; operators cannot predict ST blinding windows.
- **Evidence:** aocs_basic.py:182 (constant), _tick_star_tracker() no exclusion check.

### 2.6 CSS Head Individual Validation and Reporting
- **Status:** Individual CSS head failures tracked, but no per-head telemetry in HK.
- **Expected:** Each of 6 CSS heads telemetered independently (0x027A–0x027F defined in parameters but not in HK SID 2).
- **Current:** Only composite css_valid flag reported.
- **Gap:** Operators cannot validate individual CSS head health or diagnose partial CSS failure.
- **Impact:** CSS degradation scenarios not observable; FDIR cannot make granular CSS decisions.
- **Evidence:** parameters.yaml has 0x027A–0x027F; hk_structures.yaml:55–123 omits them.

### 2.7 Slew Maneuver Abort and Rate Limiting Enforcement
- **Status:** Slew command accepted (func_id 10, slew_to_quaternion), but rate limiting not enforced.
- **Expected:** Slew rate _slew_rate_dps checked against system inertia; abort if requested rate infeasible.
- **Current:** slew_rate_dps accepted if 0 < rate <= 10.0 (line 1326), but no torque/momentum check.
- **Gap:** Operator could command slew rate that wheel momentum cannot achieve.
- **Impact:** Slew trajectory invalid in commissioning; momentum saturation not predicted.
- **Evidence:** aocs_basic.py:1322–1333 (rate range only, no torque check).

### 2.8 Attitude Error Event Generation (Anomaly Threshold)
- **Status:** _att_error_threshold_deg exists (default 5.0) but is never used to generate S5 events.
- **Expected:** Event when att_error exceeds threshold (e.g., loss of ST, CSS failure).
- **Current:** att_error computed but no threshold-triggered event.
- **Gap:** Operators have no automated alert when attitude control degrades.
- **Impact:** FDIR procedures cannot auto-trigger on att_error; manual monitoring required.
- **Evidence:** aocs_basic.py:216 (threshold defined), no event generation in tick().

### 2.9 Momentum Saturation Event and Desaturation Triggering
- **Status:** total_momentum computed; no event when saturation exceeds threshold.
- **Expected:** S5 event when momentum saturation > 80% (common limit), auto-trigger DESAT mode.
- **Current:** Momentum reported in HK; desaturate only via explicit S8 command.
- **Gap:** Operators must manually command desaturation; no autonomous momentum management.
- **Impact:** Risk of momentum saturation causing loss of attitude control during long passes.
- **Evidence:** aocs_basic.py:800–806 (momentum calc), handle_command desaturate (line 1228–1230) is manual only.

### 2.10 Wheel Bearing Health Degradation Reporting
- **Status:** _bearing_degradation tracked internally; not telemetered.
- **Expected:** RW bearing health (0x0284–0x0287) in HK SID 2 for predictive maintenance.
- **Current:** Bearing health only in parameters.yaml stub list; not updated in tick().
- **Gap:** Operators cannot monitor RW bearing wear; cannot predict RW seizure.
- **Impact:** FDIR cannot trigger wheel swap before seizure.
- **Evidence:** aocs_basic.py:192 (bearing_degradation), parameters.yaml:0x0284–0x0287 (defined but not populated).

---

## Category 3 — Not Yet Described But Needed

### 3.1 Sun Aspect Angle Computation and Telemetry
- **Rationale:** LEOP and slew planning require knowledge of Sun angle w.r.t. spacecraft body axes.
- **Missing:** No command to compute/report sun aspect angle or solar beta angle w.r.t. attitude.
- **Recommendation:** Add S20 parameter 0x0216 (solar_beta) computation in tick() based on CSS sun vector and quaternion; report in HK SID 2.
- **Operator need:** Validates sun-safe-hold orientation; predicts ST blinding windows.

### 3.2 Momentum Saturation Prediction (Before Maneuver)
- **Rationale:** Operator needs to validate that a slew maneuver will not saturate RW momentum.
- **Missing:** No S8 function to compute post-slew momentum or predict saturation.
- **Recommendation:** Add S8 func_id for momentum-check-before-slew; return projected saturation % after maneuver.
- **Operator need:** Prevents unplanned desaturation interruptions during imaging.

### 3.3 Fine-Pointing Readiness Check (All Sensors/Actuators)
- **Rationale:** Commissioning requires validation that all sensors/actuators meet fine-pointing requirements.
- **Missing:** No S8 command to check ST healthy, all wheels enabled, momentum < threshold, att_error < limit.
- **Recommendation:** Add S8 func_id for FINE_POINTING_READINESS_CHECK; return bitmask of unmet conditions.
- **Operator need:** Validates transition from NOMINAL to FINE_POINT is safe.

### 3.4 Attitude Target Quaternion Command (for Nadir Holding)
- **Rationale:** Operators need to command target attitude for each orbital phase (nadir-pointing, sun-safe, slew-to-target).
- **Missing:** No S8 or S20 command to set target quaternion; _target_q only used internally for modes.
- **Recommendation:** Add S8 func_id to set target quaternion (4 floats); separate from slew (which includes rate and timing).
- **Operator need:** Enables sun-safe holds, nadir-point calibration, and multi-target imaging sequences.

### 3.5 Wheel Speed Command (Direct RPM Setting)
- **Rationale:** Emergency procedures may need to set all wheels to specific speed (e.g., safe-mode parking).
- **Missing:** Only rw_set_speed_bias (relative) and rw_ramp_down (asymptotic); no absolute speed command.
- **Recommendation:** Add S8 func_id for RW_SET_SPEED_ABSOLUTE (wheel, target_rpm).
- **Operator need:** Rapid momentum dump or safe-mode speed stabilization.

### 3.6 CSS Head Masking and Per-Head Confidence
- **Rationale:** CSS degradation (partial head failure) should allow operator to mask failed heads.
- **Missing:** No command to mask individual CSS heads or report per-head confidence.
- **Recommendation:** Add S8 func_id for CSS_HEAD_MASK (face, enable/disable); report per-head illumination in HK.
- **Operator need:** Graceful CSS degradation; ensures CSS composite remains valid after head failure.

### 3.7 Gyro Bias Trim Command (Fine-Tuning)
- **Rationale:** After commissioning, gyro bias calibration may require small in-flight trim.
- **Missing:** Only GYRO_CALIBRATION (reset to zero); no trim command.
- **Recommendation:** Add S8 func_id for GYRO_BIAS_TRIM (axis, delta_bias_deg_s).
- **Operator need:** Improves NOMINAL and FINE_POINT attitude accuracy post-commissioning.

### 3.8 Magnetic Field Direction and Magnitude Reporting
- **Rationale:** DETUMBLE mode effectiveness depends on accurate Earth mag field knowledge.
- **Missing:** mag_x/y/z (0x020B–0x020D) reported but not tied to position/altitude; no field model accuracy indication.
- **Recommendation:** Add estimated mag field magnitude error (%) and field direction confidence to HK.
- **Operator need:** Validates B-dot effectiveness; supports DETUMBLE diagnostics.

### 3.9 Star Tracker Commissioning Checklist (Per-Unit)
- **Rationale:** ST commissioning requires specific checks: power, boot time, star count, tracking gates.
- **Missing:** No dedicated S8 function for ST commissioning; only power and selection commands.
- **Recommendation:** Add S8 func_id for ST_COMMISSIONING_CHECK (unit); return status struct with boot duration, stars acquired, tracking gate accuracy.
- **Operator need:** Validates ST health before transitioning to FINE_POINT.

### 3.10 Safe Mode Entry Confirmation and Readiness
- **Rationale:** After safe-mode entry, operator needs to confirm system reached safe state and is ready for recovery.
- **Missing:** No S8 command to check safe mode readiness (CSS valid, MTQ operational, rates < threshold).
- **Recommendation:** Add S8 func_id for SAFE_MODE_READINESS_CHECK; return bitmask of safe-mode prerequisites met.
- **Operator need:** Validates safe-mode entry completed before starting recovery procedures.

---

## Category 4 — Implemented but NOT Helpful for This Mission

### 4.1 Orbit-Control Features (Thrusters / Orbit Maneuvers)
- **Status:** No thruster model, orbit maneuver functions, or inclination/altitude change commands exist.
- **Confirmation:** AOCS subsystem is **attitude-only**, no orbit control. ✓ Correct for EO mission.
- **Evidence:** No functions in func_id 0–15; no orbit adjustment in any mode.
- **Assessment:** No issue; mission explicitly excludes orbit control.

### 4.2 Nadir-Pointing (Auto Earth-Pointing)
- **Status:** NADIR_POINTING mentioned in mode names, but no distinct nadir-pointing mode implementation.
- **Clarification:** MODE_NOMINAL (4) serves dual purpose: nadir-hold and inertial-hold depending on target quaternion.
- **Impact:** Operator must command target quaternion for each configuration; no automatic nadir-lock from GPS/CSS.
- **Assessment:** Not a blocker, but adds commissioning burden.

---

## Category 5 — Inconsistent / Incoherent Implementation

### 5.1 Mode Naming vs. Implementation Mismatch
- **Issue:** MODE_NOMINAL (4) name suggests "normal ops," but code enters it with _target_q defaulting to [0,0,0,1] (inertial).
- **Consequence:** Operator command "set_mode 4" does NOT guarantee nadir-pointing; attitude depend on prior _target_q.
- **Evidence:** aocs_basic.py:28, 193 (_target_q initialized to [0,0,0,1] / inertial); _tick_nominal() uses _target_q without validation.
- **Risk:** Operator confusion during commissioning.
- **Recommendation:** Rename to MODE_ATTITUDE_CONTROL or clarify mode semantics; separate nadir-lock setup.

### 5.2 Slew Mode (MODE_SLEW = 6) Auto-Exit Without Explicit Completion
- **Issue:** SLEW mode auto-transitions to NOMINAL when quaternion error falls below threshold, without explicit operator confirmation.
- **Consequence:** Slew completion notification may be missed; operator unsure when maneuver is done.
- **Evidence:** aocs_basic.py:645–720 (_tick_slew), auto-transition at line 712 when err < 0.01 deg.
- **Risk:** Imaging could start before slew truly settled.
- **Recommendation:** Add SLEW_COMPLETE event or explicit slew-done command check.

### 5.3 ECLIPSE Mode (8) Not Connected to Orbit State
- **Issue:** MODE_ECLIPSE exists but is never auto-triggered by orbit_state.in_eclipse.
- **Expected:** Automatic transition to ECLIPSE during spacecraft shadow, back to NOMINAL at sun acquisition.
- **Current:** Operator must manually set_mode 8; _tick_eclipse() handles eclipse-specific control.
- **Evidence:** aocs_basic.py:309 (eclipse detection code exists), but no auto-transition in _check_auto_transitions().
- **Risk:** Operator forgets to enter ECLIPSE mode; wheels desaturate during eclipse, attitude drifts.
- **Recommendation:** Add auto-trigger: `if orbit_state.in_eclipse and mode != ECLIPSE: _set_mode(ECLIPSE)`.

### 5.4 Sensor Selection (ST, Mag) Not Validated Against Mode
- **Issue:** Operator can select mag_select='B' while DETUMBLE is active, even if Mag B is noisier.
- **Expected:** Mode-dependent sensor constraints (e.g., FINE_POINT requires ST1 or ST2 healthy, not CSS-only).
- **Current:** Sensor selection is decoupled from mode checks.
- **Evidence:** handle_command mag_select (lines 1282–1300) does not validate current mode.
- **Risk:** Suboptimal AOCS behavior (e.g., coarse CSS used instead of fine ST).
- **Recommendation:** Add per-mode sensor requirements and validate selection against requirements.

### 5.5 Magnetorquer Failure Does Not Trigger Safe Mode
- **Issue:** Single MTQ axis failure is injectable but does not automatically force DETUMBLE or SAFE_BOOT mode.
- **Expected:** MTQ failure invalidates B-dot control; mode should auto-degrade.
- **Current:** Mode unchanged; B-dot control zeros failed axis but continues.
- **Evidence:** aocs_basic.py:1425–1427 (failure injection), _tick_detumble() handles failed axes (lines 561–572), but mode stays same.
- **Risk:** Operator unaware of MTQ failure; DETUMBLE may not be effective.
- **Recommendation:** Trigger S5 event and/or force safe-mode entry if 2+ MTQ axes fail.

### 5.6 GPS Receiver Not Dependent on AOCS Mode or Power State
- **Issue:** GPS fix quality jumps randomly; not tied to power state, mode, or antenna orientation.
- **Expected:** GPS acquisition depends on antenna availability and mode (OFF → no fix; NOMINAL/higher → fix possible).
- **Current:** gps_fix randomized every tick, independent of power.
- **Evidence:** aocs_basic.py:739–753 (random generation, no antenna/power check).
- **Risk:** Unrealistic GPS behavior during LEOP; commissioning sequences cannot validate GPS acquisition.
- **Recommendation:** Add power-state gating and mode-dependent acquisition delay.

### 5.7 Attitude Control Gain (_kp) and Deadband Not Commandable
- **Issue:** _kp (proportional gain, 0.02) and _att_error_threshold_deg are configured only at startup, not changeable in-flight.
- **Expected:** Fine-tuning gains during commissioning is standard practice.
- **Current:** No S8 or S20 command to adjust gains.
- **Evidence:** aocs_basic.py:194 (_kp = 0.02 hardcoded), handle_command accepts set_deadband (lines 1370–1375) but not set_gain.
- **Risk:** Cannot optimize control response during commissioning.
- **Recommendation:** Add S8 func_id for SET_CONTROL_GAIN (kp_value); add S20 parameter for in-flight read/write.

### 5.8 COARSE_SUN Mode (3) Lacks Realistic CSS Control Law
- **Issue:** _tick_coarse_sun() simply damps attitude_error toward 5 degrees; does not compute actual CSS-based control torques.
- **Expected:** CSS control law should use CSS illumination values to compute MTQ dipoles (magnetic control only in COARSE_SUN).
- **Current:** Placeholder implementation.
- **Evidence:** aocs_basic.py:589–598 (no CSS-to-MTQ mapping).
- **Risk:** COARSE_SUN mode unrealistic; cannot validate sun-pointing sequences.
- **Recommendation:** Implement proper CSS illumination-to-control law; use CSS heads to derive sun direction error.

### 5.9 Eclipse Entry/Exit Hysteresis Not Implemented
- **Issue:** MODE_ECLIPSE could toggle in/out if orbit_state.in_eclipse is on boundary.
- **Expected:** Hysteresis timer prevents mode thrashing near eclipse boundaries.
- **Current:** _prev_in_eclipse boolean exists (aocs_basic.py:204) but not used in mode transition logic.
- **Evidence:** aocs_basic.py:309 (code exists), but _check_auto_transitions() does not reference it.
- **Risk:** Wheel desaturation commands repeated; attitude oscillates near eclipse edge.
- **Recommendation:** Add hysteresis timer in _check_auto_transitions() for eclipse boundary.

### 5.10 Total Momentum Calculation Assumes Inertia Constant
- **Issue:** total_momentum = sum(I * omega) uses constant _rw_inertia (0.005 kg·m²) per wheel.
- **Expected:** Inertia should decrease with bearing degradation or account for wheel mass shift.
- **Current:** Bearing degradation affects friction and temperature, not inertia.
- **Evidence:** aocs_basic.py:800–806, _bearing_degradation used for friction only (line 774).
- **Risk:** Momentum saturation predictions inaccurate after bearing degradation.
- **Impact:** Minor (momentum management still functional, but less accurate).
- **Recommendation:** Scale inertia with bearing health: I_eff = I_nominal * (1.0 - bearing_degradation * 0.1).

---

## Top-5 Prioritized Defects

### Defect #1 (CRITICAL): AOCS Mode Default = NOMINAL (4) Without Explicit Safe Initialization

**Severity:** CRITICAL (operability blocker)

**Title:** AOCSState.mode Default = 4 (NOMINAL) — Not Safe for Boot

**Description:**
The AOCS state machine initializes to MODE_NOMINAL (4) at power-up ("start in NOMINAL for backwards compat," aocs_basic.py:28) instead of MODE_OFF (0) or MODE_SAFE_BOOT (1). This is dangerous because:
1. Operator expects spacecraft to be dormant on initial power, not in active attitude control.
2. No sensors are necessarily powered or healthy at startup.
3. Reaction wheels might spin up to nominal speed (1200 RPM) on boot, consuming power and heat before commissioning.
4. Fine-pointing mode (5) could be entered by accident if sensor selection is inconsistent.

**Cause:** Backwards compatibility with earlier versions; insufficient LEOP/commissioning workflow design.

**Impact:**
- Operators cannot safely command early-LEOP sequence; risk of unexpected attitude control maneuvers.
- MCS displays may show "NOMINAL" incorrectly, masking actual boot status.
- Commissioning procedures assume OFF or SAFE_BOOT entry but will conflict with NOMINAL default.

**Files:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:28`
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/tests/test_simulator/test_aocs_state_machine.py:54–58`

**Suggested Fix:**
Change line 28 from:
```python
mode: int = 4  # start in NOMINAL for backwards compat
```
to:
```python
mode: int = 0  # start in OFF for safe boot; LEOP procedure transitions to SAFE_BOOT → DETUMBLE
```
Update tests to expect mode 0 at startup; add LEOP procedure with explicit mode transitions.

---

### Defect #2 (HIGH): TLE Upload and Magnetic Field Model Not Implemented

**Severity:** HIGH (mission-critical for B-dot)

**Title:** No TLE Upload or Onboard Magnetic Field Model — B-Dot Accuracy Degraded

**Description:**
Per ECSS-E-ST-60-30C, AOCS requires periodic Two-Line Element (TLE) uploads and an onboard magnetic field model (IGRF/WMM) to:
1. Update orbit propagation for accurate position/velocity telemetry.
2. Compute expected Earth magnetic field strength/direction as function of latitude/longitude/altitude/time.
3. Validate B-dot control law detumbling effectiveness.

**Current state:**
- No S8 or S20 command to upload TLE.
- Magnetic field modeled as static 50000 nT vector [25000, 10000, -40000]; no position dependency.
- DETUMBLE mode uses hardcoded B-field; magnetorquer dipoles are not position-aware.

**Consequence:**
- B-dot detumbling accuracy is unrealistic (Earth's field varies ±30% by latitude/altitude).
- Operator cannot validate B-dot effectiveness with current orbit state.
- LEOP commissioning lacks critical diagnostic parameter.

**Files:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:42–44` (static mag)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:482–523` (_tick_magnetometer, no model)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/service_dispatch.py` (no TLE handler)

**Suggested Fix:**
1. Add S8 func_id 16 for TLE_UPLOAD (new, in range 0–15 reserved for AOCS).
2. Parse TLE into orbital elements; store onboard.
3. Implement minimal IGRF-13 lookup table (altitude/latitude bins).
4. Modify _tick_magnetometer() to compute B-field from orbit_state.lat/lon/alt_km + time.
5. Report mag_field_total and estimated model uncertainty in HK.

---

### Defect #3 (HIGH): Eclipse Auto-Transition Not Implemented

**Severity:** HIGH (operational risk during eclipse)

**Title:** ECLIPSE Mode (8) Not Automatically Triggered by orbit_state.in_eclipse

**Description:**
The AOCS has MODE_ECLIPSE (8) defined and a tick function (_tick_eclipse), but the state machine does NOT automatically transition to ECLIPSE when the spacecraft enters shadow. The operator must manually set_mode 8.

**Consequence:**
- During eclipse, reaction wheels continue to desaturate via magnetorquers (which are ineffective in shadow).
- Attitude drifts during eclipse; operators have not been trained to manually enter ECLIPSE mode for every pass.
- Momentum not efficiently managed; desaturation incomplete by next sun acquisition.
- Imaging pass after eclipse might find AOCS out of control due to unmanaged momentum.

**Files:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:290–322` (_check_auto_transitions, no eclipse logic)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:667–690` (_tick_eclipse exists but unreachable)

**Suggested Fix:**
In _check_auto_transitions(), add:
```python
# Eclipse detection hysteresis
if orbit_state.in_eclipse and s.mode != MODE_ECLIPSE:
    s._guard_timer += dt
    if s._guard_timer > 5.0:  # Debounce 5 seconds
        self._set_mode(MODE_ECLIPSE)
elif not orbit_state.in_eclipse and s.mode == MODE_ECLIPSE:
    s._guard_timer += dt
    if s._guard_timer > 5.0:
        self._set_mode(MODE_NOMINAL)  # or prior mode
```

---

### Defect #4 (MEDIUM): CSS Head Telemetry Not Reported in HK SID 2

**Severity:** MEDIUM (diagnostic gap)

**Title:** CSS Individual Head Health Not Visible in Housekeeping — Cannot Diagnose Partial CSS Failure

**Description:**
The parameter definition includes per-CSS-head telemetry (0x027A–0x027F: css_px, css_mx, css_py, css_my, css_pz, css_mz), but the HK SID 2 structure (hk_structures.yaml) omits these parameters. Only composite CSS sun vector (0x0245–0x0248) is reported.

**Consequence:**
- Operators cannot diagnose individual CSS head failures or degradation.
- If one CSS head fails, operator cannot identify which; cannot mask it.
- FDIR cannot make granular CSS decisions (e.g., use CSS for sun-safe if 5/6 heads healthy).
- No visibility into partial CSS sensor failure modes.

**Files:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/telemetry/hk_structures.yaml:55–123` (SID 2, missing 0x027A–0x027F)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/configs/eosat1/telemetry/parameters.yaml` (0x027A–0x027F defined in parameter list)
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:84–89` (css_heads dict exists, not telemetered)

**Suggested Fix:**
Add to HK SID 2 parameters list:
```yaml
- { param_id: 0x027A, pack_format: H, scale: 1000 }  # css_px
- { param_id: 0x027B, pack_format: H, scale: 1000 }  # css_mx
- { param_id: 0x027C, pack_format: H, scale: 1000 }  # css_py
- { param_id: 0x027D, pack_format: H, scale: 1000 }  # css_my
- { param_id: 0x027E, pack_format: H, scale: 1000 }  # css_pz
- { param_id: 0x027F, pack_format: H, scale: 1000 }  # css_mz
```
Populate css_heads dict in _tick_css() from CSS illumination model.

---

### Defect #5 (MEDIUM): COARSE_SUN Mode (3) Lacks Realistic CSS Control Law

**Severity:** MEDIUM (commissioning validation blocker)

**Title:** COARSE_SUN Attitude Control Unrealistic — No CSS-to-MTQ Mapping

**Description:**
MODE_COARSE_SUN (3) is used during LEOP to acquire the sun using CSS and magnetorquers. However, the implementation (_tick_coarse_sun, lines 589–598) simply damps attitude_error toward a hardcoded 5-degree target; it does not compute actual torques from CSS illumination nor command MTQ dipoles.

**Consequence:**
- Operators cannot validate sun acquisition dynamics during commissioning.
- Control law is not representative of real spacecraft; testing is unrealistic.
- LEOP commissioning procedures are not validated against actual CSS-driven control.
- COARSE_SUN mode appears to work, but underlying physics is wrong.

**Files:**
- `/sessions/beautiful-dazzling-edison/mnt/SpaceMissionSimulation/packages/smo-simulator/src/smo_simulator/models/aocs_basic.py:589–598` (_tick_coarse_sun)

**Suggested Fix:**
Implement CSS control law:
1. Read css_heads illumination per body face.
2. Compute sun direction error (CSS composite frame vs. desired orientation).
3. Compute required MTQ dipole: `dipole = k_css * sun_error_cross_body_axis`.
4. Command MTQ duty proportional to dipole magnitude.
5. Apply sun-acquisition dynamics: attitude_error decays as sun_error → 0.

---

## Parameter / Command Coverage Table

| Parameter ID | Name | Subsystem | Units | In HK SID 2? | Commandable S8? | Commandable S20? | MCS Display | Notes |
|---|---|---|---|---|---|---|---|---|
| 0x0200 | att_q1 | aocs | — | ✓ | — | — | ✓ | Quaternion X |
| 0x0201 | att_q2 | aocs | — | ✓ | — | — | ✓ | Quaternion Y |
| 0x0202 | att_q3 | aocs | — | ✓ | — | — | ✓ | Quaternion Z |
| 0x0203 | att_q4 | aocs | — | ✓ | — | — | ✓ | Quaternion W |
| 0x0204 | rate_roll | aocs | deg/s | ✓ | — | — | ✓ | Angular rate X |
| 0x0205 | rate_pitch | aocs | deg/s | ✓ | — | — | ✓ | Angular rate Y |
| 0x0206 | rate_yaw | aocs | deg/s | ✓ | — | — | ✓ | Angular rate Z |
| 0x0207 | rw1_speed | aocs | RPM | ✓ | ✓ (bias) | — | ✓ | Wheel 1 speed; use bias cmd or ramp cmd |
| 0x0208 | rw2_speed | aocs | RPM | ✓ | ✓ (bias) | — | ✓ | Wheel 2 speed |
| 0x0209 | rw3_speed | aocs | RPM | ✓ | ✓ (bias) | — | ✓ | Wheel 3 speed |
| 0x020A | rw4_speed | aocs | RPM | ✓ | ✓ (bias) | — | ✓ | Wheel 4 speed |
| 0x020B | mag_x | aocs | nT | ✓ | — | — | — | Magnetometer X; on-demand only |
| 0x020C | mag_y | aocs | nT | ✓ | — | — | — | Magnetometer Y |
| 0x020D | mag_z | aocs | nT | ✓ | — | — | — | Magnetometer Z |
| 0x020F | mode | aocs | — | ✓ | ✓ (set_mode, func 0) | ✓ (S20.1) | ✓ | Current AOCS mode (0–8) |
| 0x0210 | gps_lat | aocs | deg | ✓ | — | — | — | GPS latitude; on-demand |
| 0x0211 | gps_lon | aocs | deg | ✓ | — | — | — | GPS longitude |
| 0x0212 | gps_alt | aocs | km | ✓ | — | — | — | GPS altitude |
| 0x0216 | solar_beta | aocs | deg | ✗ | — | — | — | Solar beta angle; NOT in HK; needed for sun-safe validation |
| 0x0217 | att_error | aocs | deg | ✓ | ✓ (deadband) | — | ✓ | Attitude error magnitude |
| 0x0218 | rw1_temp | aocs | °C | ✓ | — | — | ✓ | Wheel 1 temperature |
| 0x0219 | rw2_temp | aocs | °C | ✓ | — | — | ✓ | Wheel 2 temperature |
| 0x021A | rw3_temp | aocs | °C | ✓ | — | — | ✓ | Wheel 3 temperature |
| 0x021B | rw4_temp | aocs | °C | ✓ | — | — | ✓ | Wheel 4 temperature |
| 0x0240 | st1_status | aocs | — | ✓ | ✓ (power) | — | ✓ | ST1: 0=OFF, 1=BOOTING, 2=TRACKING, 3=BLIND, 4=FAILED |
| 0x0241 | st1_num_stars | aocs | — | ✓ | — | — | ✓ | ST1 tracked star count |
| 0x0243 | st2_status | aocs | — | ✓ | ✓ (power) | — | ✓ | ST2: 0=OFF, 1=BOOTING, 2=TRACKING, 3=BLIND, 4=FAILED |
| 0x0245 | css_sun_x | aocs | — | ✓ | — | — | ✓ | Composite CSS sun X |
| 0x0246 | css_sun_y | aocs | — | ✓ | — | — | ✓ | Composite CSS sun Y |
| 0x0247 | css_sun_z | aocs | — | ✓ | — | — | ✓ | Composite CSS sun Z |
| 0x0248 | css_valid | aocs | — | ✓ | — | — | ✓ | CSS sun vector validity flag |
| 0x0250 | rw1_current | aocs | A | ✓ | — | — | — | Wheel 1 current draw |
| 0x0251 | rw2_current | aocs | A | ✓ | — | — | — | Wheel 2 current |
| 0x0252 | rw3_current | aocs | A | ✓ | — | — | — | Wheel 3 current |
| 0x0253 | rw4_current | aocs | A | ✓ | — | — | — | Wheel 4 current |
| 0x0254 | rw1_enabled | aocs | — | ✓ | ✓ (enable) | — | ✓ | Wheel 1 enabled flag |
| 0x0255 | rw2_enabled | aocs | — | ✓ | ✓ (enable) | — | ✓ | Wheel 2 enabled flag |
| 0x0256 | rw3_enabled | aocs | — | ✓ | ✓ (enable) | — | ✓ | Wheel 3 enabled flag |
| 0x0257 | rw4_enabled | aocs | — | ✓ | ✓ (enable) | — | ✓ | Wheel 4 enabled flag |
| 0x0258 | mtq_x_duty | aocs | % | ✓ | — | — | — | Magnetorquer X duty cycle |
| 0x0259 | mtq_y_duty | aocs | % | ✓ | — | — | — | Magnetorquer Y duty cycle |
| 0x025A | mtq_z_duty | aocs | % | ✓ | — | — | — | Magnetorquer Z duty cycle |
| 0x025B | total_momentum | aocs | Nms | ✓ | ✓ (check) | — | — | Total RW angular momentum |
| 0x0260 | wheels_enabled | aocs | — | ✗ | — | — | — | Number of enabled wheels; NOT in HK |
| 0x0261 | mtq_dipole_total | aocs | Am² | ✗ | — | — | — | MTQ total dipole; stub, not implemented |
| 0x0262 | submode | aocs | — | ✓ | — | — | — | AOCS sub-mode (0 for most modes) |
| 0x0264 | time_in_mode | aocs | s | ✓ | — | — | — | Duration in current mode |
| 0x0270 | gyro_bias_x | aocs | deg/s | ✓ | — | — | — | Gyro X bias estimate |
| 0x0271 | gyro_bias_y | aocs | deg/s | ✓ | — | — | — | Gyro Y bias estimate |
| 0x0272 | gyro_bias_z | aocs | deg/s | ✓ | — | — | — | Gyro Z bias estimate |
| 0x0273 | gyro_temp | aocs | °C | ✓ | — | — | — | Gyro assembly temperature |
| 0x0274 | gps_fix | aocs | — | ✓ | — | — | ✓ | GPS fix type: 0=none, 1=2D, 2=3D, 3=3D+vel |
| 0x0275 | gps_pdop | aocs | — | ✓ | — | — | — | GPS PDOP (position dilution of precision) |
| 0x0276 | gps_num_sats | aocs | — | ✓ | — | — | — | GPS satellite count |
| 0x0277 | mag_field_total | aocs | nT | ✓ | — | — | — | Earth mag field magnitude |
| 0x0220 | sun_body_x | aocs | — | ✗ | — | — | — | Sun body X; stub |
| 0x0221 | sun_body_y | aocs | — | ✗ | — | — | — | Sun body Y; stub |
| 0x0222 | sun_body_z | aocs | — | ✗ | — | — | — | Sun body Z; stub |
| 0x0223 | mag_a_x | aocs | nT | ✗ | — | — | — | Mag A X; NOT in HK SID 2 |
| 0x0224 | mag_a_y | aocs | nT | ✗ | — | — | — | Mag A Y |
| 0x0225 | mag_a_z | aocs | nT | ✗ | — | — | — | Mag A Z |
| 0x0226 | mag_b_x | aocs | nT | ✗ | — | — | — | Mag B X |
| 0x0227 | mag_b_y | aocs | nT | ✗ | — | — | — | Mag B Y |
| 0x0228 | mag_b_z | aocs | nT | ✗ | — | — | — | Mag B Z |
| 0x0229 | mag_select | aocs | — | ✗ | ✓ (mag_select func 7) | — | — | Active mag (0=A, 1=B); NOT in HK |
| 0x027A | css_px | aocs | — | ✗ | — | — | — | CSS +X head; NOT in HK |
| 0x027B | css_mx | aocs | — | ✗ | — | — | — | CSS -X head |
| 0x027C | css_py | aocs | — | ✗ | — | — | — | CSS +Y head |
| 0x027D | css_my | aocs | — | ✗ | — | — | — | CSS -Y head |
| 0x027E | css_pz | aocs | — | ✗ | — | — | — | CSS +Z head |
| 0x027F | css_mz | aocs | — | ✗ | — | — | — | CSS -Z head |
| 0x0280 | slew_time_remaining | aocs | s | ✗ | — | — | — | Slew ETA; stub |
| 0x0281 | slew_progress | aocs | % | ✗ | — | — | — | Slew progress; stub |
| 0x0282 | momentum_saturation_pct | aocs | % | ✗ | — | — | — | Momentum saturation; stub |
| 0x0283 | attitude_source | aocs | — | ✗ | — | — | — | Attitude source (ST/CSS/Gyro); stub |
| 0x0284 | rw1_bearing_health | aocs | % | ✗ | — | — | — | Wheel 1 bearing health; NOT updated |
| 0x0285 | rw2_bearing_health | aocs | % | ✗ | — | — | — | Wheel 2 bearing health |
| 0x0286 | rw3_bearing_health | aocs | % | ✗ | — | — | — | Wheel 3 bearing health |
| 0x0287 | rw4_bearing_health | aocs | % | ✗ | — | — | — | Wheel 4 bearing health |
| 0x0288 | css_head_health_px | aocs | % | ✗ | — | — | — | CSS +X head health; stub |
| 0x0289 | css_head_health_mx | aocs | % | ✗ | — | — | — | CSS -X head health; stub |
| 0x028A | css_head_health_py | aocs | % | ✗ | — | — | — | CSS +Y head health; stub |
| 0x028B | css_head_health_my | aocs | % | ✗ | — | — | — | CSS -Y head health; stub |
| 0x028C | css_head_health_pz | aocs | % | ✗ | — | — | — | CSS +Z head health; stub |
| 0x028D | css_head_health_mz | aocs | % | ✗ | — | — | — | CSS -Z head health; stub |

**S8 Function Management Commands (AOCS, func_id 0–15):**

| Func ID | Command | Data Format | Response | Notes |
|---|---|---|---|---|
| 0 | AOCS_SET_MODE | 1 byte (mode 0–8) | S1 acceptance | Set AOCS mode |
| 1 | AOCS_DESATURATE | — | S1 acceptance | Enter DESAT mode |
| 2 | AOCS_DISABLE_WHEEL | 1 byte (wheel 0–3) | S1 acceptance | Disable reaction wheel |
| 3 | AOCS_ENABLE_WHEEL | 1 byte (wheel 0–3) | S1 acceptance | Enable reaction wheel |
| 4 | AOCS_ST1_POWER | 1 byte (on/off) | S1 acceptance | Power ST1 on/off; boot if on |
| 5 | AOCS_ST2_POWER | 1 byte (on/off) | S1 acceptance | Power ST2 on/off |
| 6 | AOCS_ST_SELECT | 1 byte (unit 1–2) | S1 acceptance | Select primary star tracker |
| 7 | AOCS_MAG_SELECT | 1 byte (A/B or bool) | S1 acceptance | Select active magnetometer |
| 8 | AOCS_RW_SET_SPEED_BIAS | 5 bytes (wheel, bias_f32) | S1 acceptance | Add relative bias to wheel speed |
| 9 | AOCS_MTQ_ENABLE / DISABLE | 1 byte (on/off) | S1 acceptance | Enable/disable magnetorquers |
| 10 | AOCS_SLEW_TO_QUATERNION | 20 bytes (q[4] + rate_f32) | S1 acceptance | Slew to target quaternion at rate |
| 11 | AOCS_CHECK_MOMENTUM | — | S8.130 response | Return momentum (Nms) + saturation % |
| 12 | AOCS_BEGIN_ACQUISITION | — | S1 acceptance | Start LEOP sequence (DETUMBLE→COARSE_SUN) |
| 13 | AOCS_GYRO_CALIBRATION | — | S1 acceptance | Reset gyro bias to zero |
| 14 | AOCS_RW_RAMP_DOWN | 5 bytes (wheel, target_rpm_f32) | S1 acceptance | Ramp wheel to target RPM (255=all) |
| 15 | AOCS_SET_DEADBAND | 4 bytes (deadband_deg_f32) | S1 acceptance | Set attitude error deadband |

**S20 Parameter Management (AOCS):**

| Param ID | Read (S20.3) | Write (S20.1) | Description |
|---|---|---|---|
| 0x0200–0x0203 | ✓ | ✗ | Quaternion (read-only) |
| 0x0204–0x0206 | ✓ | ✗ | Angular rates (read-only) |
| 0x0207–0x020A | ✓ | ✗ | RW speeds (read-only) |
| 0x020B–0x020D | ✓ | ✗ | Mag field (read-only) |
| 0x020F | ✓ | ✓ | AOCS mode (settable via S20.1) |
| 0x0210–0x0212 | ✓ | ✗ | GPS position (read-only) |
| 0x0216 | ✓ | ✗ | Solar beta (read-only) |
| 0x0217 | ✓ | ✗ | Attitude error (read-only) |
| 0x0218–0x021B | ✓ | ✗ | RW temperatures (read-only) |
| 0x0240–0x0248 | ✓ | ✗ | ST/CSS status (read-only) |
| 0x0250–0x025B | ✓ | ✗ | RW currents/momentum (read-only) |
| All other defined parameters | ✓ | ✗ | Read-only |

**MCS Display Coverage:**
- System Overview Dashboard: ✓ AOCS subsystem health, att_error, mode.
- Attitude control screen: Not implemented (stub).
- Sensor health panel: ✓ ST status, CSS validity, GPS fix.
- Wheel management: ✓ Speed, temperature, enabled flags; ramp-down available.
- Momentum monitor: ✓ total_momentum in HK; no saturation prediction.

---

## Summary

**Operability Assessment:**
- **Strengths:** Core 9-mode state machine works; dual star trackers, magnetometer redundancy, reaction wheel management, and S8/S20 commanding functional for baseline operations.
- **Critical gaps:** Default mode unsafe; TLE/magnetic field model missing; Eclipse auto-transition absent; CSS head diagnostics hidden.
- **Commissioning risk:** COARSE_SUN control law unrealistic; no per-head CSS telemetry; FINE_POINT readiness check absent.
- **Diagnostic gaps:** Wheel bearing health not telemetered; slew completion not signaled; momentum saturation event not generated.

**Recommendation:** Address Top-5 defects before LEOP, especially Defect #1 (mode default) and Defect #3 (eclipse auto-transition). Defect #2 (TLE/mag model) required for realistic B-dot validation. Remaining medium-priority defects improve commissioning visibility but do not block initial operations.

