# CON-007: Reaction Wheel Bearing/Speed Anomaly Recovery
**Subsystem:** AOCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover from an individual reaction wheel anomaly characterised by speed oscillation
(jitter > +/-500 rpm over 10s), unexpected speed divergence, or wheel bearing overtemperature
(> 60 deg-C). EOSAT-1 carries four reaction wheels in a pyramid configuration; nominal
operations require a minimum of three. This procedure isolates the failed wheel, transitions
to three-wheel control mode, performs desaturation of remaining wheels, and establishes a
plan for potential wheel recovery.

## Prerequisites
- [ ] AOCS telemetry (SID 2) is being received at >= 1 Hz
- [ ] At least three reaction wheels are confirmed functional prior to anomaly
- [ ] Flight Dynamics has current momentum state estimate
- [ ] Procedure CON-001 (AOCS Anomaly Recovery) has been reviewed for escalation paths

## Procedure Steps

### Step 1 — Identify the Affected Reaction Wheel
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.rw1_speed` (0x0207) — record value and rate of change
**Verify:** `aocs.rw2_speed` (0x0208) — record value and rate of change
**Verify:** `aocs.rw3_speed` (0x0209) — record value and rate of change
**Verify:** `aocs.rw4_speed` (0x020A) — record value and rate of change
**Verify:** `aocs.rw1_temp` (0x0218), `aocs.rw2_temp` (0x0219), `aocs.rw3_temp` (0x021A), `aocs.rw4_temp` (0x021B)
**Action:** Flag any wheel with: speed oscillation > +/-500 rpm/10s, speed > 6500 rpm, or temp > 60 deg-C
**GO/NO-GO:** Exactly one wheel is anomalous — proceed. If multiple wheels anomalous, escalate to CON-001 Step 7.

### Step 2 — Disable the Anomalous Wheel
**TC:** `AOCS_DISABLE_WHEEL(wheel_idx=N)` (Service 8, Subtype 1, func_id 2)
**Verify:** Affected wheel speed (0x0207-0x020A) begins to decay within 10s
**Verify:** `aocs.att_error` (0x0217) — monitor for transient increase (acceptable up to 5 deg during transition)
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206) — rates remain < 1.0 deg/s
**GO/NO-GO:** Wheel disabled and spacecraft rates bounded — proceed

### Step 3 — Command Three-Wheel Control Mode
**TC:** `SET_PARAM(param_id=aocs.wheel_config, value=3)` (Service 20, Subtype 1)
**Verify:** AOCS control loop reconfigures within 15s (internal mode flag update)
**Verify:** `aocs.att_error` (0x0217) begins converging back below 2.0 deg within 60s
**Verify:** Three remaining wheel speeds are redistributing momentum appropriately
**GO/NO-GO:** Three-wheel mode active and attitude converging — proceed

### Step 4 — Desaturate Remaining Wheels
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1)
**Verify:** Active wheel speeds (three remaining) trending toward 0 rpm over 120s
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206) — all < 0.5 deg/s during manoeuvre
**Verify:** No wheel exceeds 5000 rpm during redistribution
**GO/NO-GO:** Momentum dumped successfully — all active wheels < 2000 rpm and rates < 0.2 deg/s

### Step 5 — Verify Stable Pointing in Three-Wheel Mode
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25) — sample at 60s intervals for 5 minutes
**Verify:** `aocs.att_error` (0x0217) < 1.5 deg (relaxed from nominal 1.0 deg for three-wheel ops)
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s
**Verify:** Active wheel temperatures all < 55 deg-C
**GO/NO-GO:** Pointing performance acceptable for mission operations — proceed

### Step 6 — Confirm NOMINAL Mode (Nadir) Compatibility
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1, func_id 0)
**Verify:** `aocs.mode` (0x020F) = 4 (NOMINAL) within 15s
**Verify:** `aocs.att_error` (0x0217) converges < 1.5 deg within 120s
**GO/NO-GO:** NOMINAL (nadir) achieved in three-wheel mode — proceed to wheel recovery planning

### Step 7 — Assess Wheel Recovery Feasibility
**Action:** If anomaly was overtemperature: monitor `aocs.rw<N>_temp` passively for 2 orbits — if temp drops below 45 deg-C, wheel may be recoverable
**Action:** If anomaly was speed oscillation: flag for bearing degradation analysis — wheel recovery requires ground authorisation
**Action:** If anomaly was speed divergence: suspect driver electronics — do not re-enable without software patch or parameter update from ground
**TC (Recovery attempt, ground-authorised only):** `SET_PARAM(param_id=aocs.rw<N>_enable, value=1)`
**Verify:** Wheel spins up smoothly to commanded speed within 30s, no oscillation
**GO/NO-GO:** If recovery successful, restore four-wheel config via `SET_PARAM(param_id=aocs.wheel_config, value=4)`. If recovery fails, maintain three-wheel ops.

## Off-Nominal Handling
- If a second wheel fails during this procedure: Command `AOCS_SET_MODE(mode=2)` for DETUMBLE immediately and execute CON-001 Step 7
- If attitude error exceeds 10 deg during wheel disable: Command `AOCS_SET_MODE(mode=2)` for DETUMBLE until rates stabilise
- If desaturation is ineffective (wheels still > 4000 rpm): Plan extended magnetic torquer desaturation over next 3 orbits
- If three-wheel pointing error exceeds 3 deg: Reduce payload imaging requirements and notify mission planning

## Post-Conditions
- [ ] Anomalous wheel identified and documented (RW1/2/3/4)
- [ ] Three-wheel control mode confirmed stable
- [ ] `aocs.att_error` (0x0217) < 1.5 deg in NOMINAL (nadir)
- [ ] All active wheel temperatures < 55 deg-C and speeds < 4000 rpm
- [ ] Wheel recovery plan established with ground team decision timeline
