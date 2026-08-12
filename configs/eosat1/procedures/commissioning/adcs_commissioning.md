# COM-013: ADCS Full Commissioning

**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Complete ADCS commissioning: verify all sensors individually, commission
attitude determination, verify actuators with sign checks and gain tuning,
establish rate damping, and transition to active pointing control.

## Prerequisites
- [ ] Sequential power-on complete (LEOP-007)
- [ ] AOCS in DETUMBLE (mode 2) or COARSE_SUN (mode 3) — `aocs.mode` (0x020F).
      NOTE: this card used the pre-Phase-C 5-mode numbering throughout
      (1=DETUMBLE, 2=SAFE, 3=NADIR). The canonical codes are in
      `configs/eosat1/aocs/aocs_mode_codes.yaml`: 0 OFF, 1 SAFE_BOOT,
      2 DETUMBLE, 3 COARSE_SUN, 4 NOMINAL, 5 FINE_POINT, 6 SLEW, 7 DESAT,
      8 ECLIPSE, 9 INERTIAL. There is no mode named NADIR or SAFE.
- [ ] Body rates < 1 deg/s on all axes
- [ ] `eps.bat_soc` (0x0101) > 60%
- [ ] OBC in NOMINAL mode
- [ ] Bidirectional S-band link active
- [ ] At least 20 minutes of ground station pass remaining

## Procedure Steps

### Phase 1 — Sensor Verification

### Step 1 — Magnetometer A (Primary) Check
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Action:** AOCS reads magnetometer A (primary). Verify field vector magnitude 25-65 uT (typical LEO range).
**Verify:** `aocs.mag_a_x` (0x0223), `aocs.mag_a_y` (0x0224), `aocs.mag_a_z` (0x0225)
responding within 10 s. (These were cited as 0x0200-0x0202, which are
`aocs.att_q1`-`q3` — the attitude quaternion, not the magnetometer.)
**Verify:** Field magnitude sqrt(x^2+y^2+z^2) in range [25, 65] uT within 10s
**GO/NO-GO:** Magnetometer A providing valid field vector

### Step 2 — Magnetometer B (Backup) Check
**Action:** AOCS reads magnetometer B (backup). Verify field vector and compare with A.
**Verify:** Magnetometer B field vector agrees with A within 5% within 10s
**TC:** `MAG_SELECT(unit=0)` (Service 8, Subtype 1, func_id 7) — select magnetometer A (unit 0) as primary
**GO/NO-GO:** Both magnetometers operational, A selected as primary

### Step 3 — Coarse Sun Sensor Check
**Action:** AOCS reads all 6 CSS heads. Verify illuminated faces show > 0.1 output, shadowed faces ~0.
**Verify:** CSS readings consistent with expected sun vector direction within 10s
**GO/NO-GO:** CSS suite providing valid sun vector

### Step 4 — Star Camera Verification
**Action:** AOCS checks star camera A (zenith). Verify acquisition if rates < 0.1 deg/s.
**Verify:** Star camera A status = TRACKING within 90s (requires low rates)
**Action:** AOCS checks star camera B (nadir). Verify acquisition.
**Verify:** Star camera B status = TRACKING within 90s
**GO/NO-GO:** At least one star camera tracking

### Step 5 — GPS Position Check
**Action:** AOCS reads GPS position. Verify lat/lon/alt plausible for expected 450 km SSO orbit.
**Verify:** GPS altitude in range [440, 460] km within 10s
**GO/NO-GO:** GPS providing valid position fix

### Phase 2 — Attitude Determination Validation

### Step 6 — Enable All Sensor Inputs
**Action:** AOCS enables all sensor inputs for attitude determination.
**Action:** Compare magnetometer-derived attitude with sun sensor attitude.
**Verify:** Coarse attitude solutions from mag and CSS consistent within 10 deg within 30s
**GO/NO-GO:** Coarse attitude determination valid

### Step 7 — Star Camera Cross-Check
**Action:** When star camera acquires, compare star-camera attitude with mag+CSS solution.
**Verify:** Star camera vs coarse solution agreement within 5 deg within 30s
**Verify:** `aocs.att_error` (0x0217) < 2 deg within 60s (attitude solution converged)
**GO/NO-GO:** Attitude determination converged

### Step 8 — Angular Rate Verification
**Action:** Verify angular rate measurement consistent across sensors.
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s within 10s
**GO/NO-GO:** Rate measurements valid and consistent

### Phase 3 — Actuator Sign Checks

### Step 9 — Reaction Wheel Sign Checks
**Action:** AOCS commands small positive torque on RW1 (+X axis). Verify rate change is positive in X.
**Verify:** Rate response in correct axis and sign within 30s
**Action:** Repeat for RW2 (+Y), RW3 (+Z), RW4 (skew).
**Verify:** All 4 reaction wheel sign checks pass within 120s
**GO/NO-GO:** Reaction wheel signs verified correct

### Step 10 — Magnetorquer Sign Checks
**Action:** AOCS commands small positive dipole on MTQ1. Verify expected field change.
**Verify:** Magnetometer reading changes in expected direction within 30s
**Action:** Repeat for MTQ2, MTQ3.
**Verify:** All 3 magnetorquer sign checks pass within 90s
**GO/NO-GO:** All actuator signs correct

### Phase 4 — Rate Damping Verification

### Step 11 — Detumble Mode Verification
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1, func_id 0) — DETUMBLE (if not already)
**Action:** AOCS monitors body rates decreasing (target < 0.1 deg/s in all axes).
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s within 900s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s within 900s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s within 900s
**Action:** AOCS verifies magnetorquer activity (B-dot control).
**Action:** Record time to achieve rate damping.
**Action:** EPS/TCS monitors power consumption during detumble.
**GO/NO-GO:** Rate damping achieved within 15 minutes

### Phase 5 — Pointing Control

### Step 12 — COARSE_SUN Mode (Sun-Pointing)
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1, func_id 0) — COARSE_SUN (sun-pointing)
**Action:** When rates < 0.1 deg/s, AOCS commands COARSE_SUN (mode 3) for sun-pointing.
**Note:** COARSE_SUN is a HOLD, not a destination. Since the Phase 1 mode-management
fix the model hands itself back to the commanded mode once sun lock and the star
tracker recover, so `aocs.commanded_mode` (0x0297) and `aocs.mode_demoted` (0x0298)
are the pair to watch here, not `aocs.mode` alone.
**Verify:** `aocs.att_error` (0x0217) decreasing towards 0 within 120s
**Action:** Verify solar array illumination increasing (EPS power_gen should increase).
**Verify:** `eps.power_gen` increasing within 120s
**GO/NO-GO:** Sun-pointing achieved, power generation optimal

### Step 13 — NOMINAL Mode (Nadir-Pointing)
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0) — NOMINAL (nadir pointing for imaging)
**Verify:** `aocs.att_error` (0x0217) < 1 deg within 300s
**Action:** FD records pointing performance for commissioning report.
**GO/NO-GO:** Nadir pointing accuracy < 1 deg confirmed

## Off-Nominal Handling
- If magnetometer field magnitude outside 25-65 uT range: Check orbit position (polar regions have higher fields). If consistent mismatch, flag for calibration via COM-003.
- If star cameras fail to acquire: Verify body rates are below 0.1 deg/s. Check for bright objects in FOV. Wait for orbital geometry change. If persistent, proceed with coarse-sensor-only attitude.
- If reaction wheel sign check fails: DO NOT enable that wheel in the control loop. Mark wheel for ground investigation. AOCS can operate with 3 wheels.
- If magnetorquer sign check fails: Verify wiring table against as-built documentation. If sign is reversed, update sign convention parameter on-board. Do not use B-dot control until corrected.
- If rate damping exceeds 15 minutes: Check for residual tip-off rate from separation. Verify magnetorquer authority is sufficient. Consider increasing MTQ duty cycle.
- If nadir pointing accuracy > 2 deg: Remain in SAFE mode. Review sensor calibration data. Schedule extended commissioning pass.

## Post-Conditions
- [ ] All AOCS sensors verified operational and calibrated
- [ ] Both magnetometers operational, primary selected
- [ ] Star cameras tracking, integrated into attitude solution
- [ ] GPS providing valid position fixes
- [ ] All 4 reaction wheel signs verified correct
- [ ] All 3 magnetorquer signs verified correct
- [ ] Rate damping achieved within specification (< 15 min to < 0.1 deg/s)
- [ ] Sun-pointing mode verified with power generation increase
- [ ] Nadir pointing accuracy < 1 deg confirmed
- [ ] ADCS Commissioning Report generated with all test results
