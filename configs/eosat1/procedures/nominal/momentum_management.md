# NOM-007: Momentum Management
**Subsystem:** AOCS
**Phase:** NOMINAL
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Manage the accumulated angular momentum in the reaction wheel assembly by
commanding a magnetorquer-based desaturation manoeuvre. This procedure is executed
when any reaction wheel speed approaches the operational limit, preventing wheel
saturation and subsequent loss of fine attitude control. Desaturation is
routinely performed once per orbit or as indicated by trending data.

## Prerequisites
- [ ] NOM-001 Pass Startup completed (or autonomous execution if out of contact)
- [ ] AOCS in NOMINAL mode: `aocs.mode` (0x020F) = **4**. Mode 0 is OFF; the card
      called it "NOMINAL". The canonical codes are in
      `configs/eosat1/aocs/aocs_mode_codes.yaml`.
- [ ] Momentum dump not inhibited: `aocs.desat_inhibit` (0x029A) = 0. The inhibit is
      asserted AUTOMATICALLY while `payload.mode` (0x0600) = 2 (IMAGING), and can be
      latched by the operator with `AOCS_SET_DESAT_INHIBIT` (func 96).
- [ ] AOCS is NOT in FINE_POINT. Since Phase 1 the desaturate command is REFUSED in
      FINE_POINT with `code="DESAT_REFUSED_FINE"`, and automatic desat entry
      covers only NOMINAL, INERTIAL and ECLIPSE — a dump can no longer pre-empt a
      strip on its own.
- [ ] No imaging session in progress: `payload.mode` (0x0600) != 2
- [ ] EPS battery SoC > 40 %: `eps.bat_soc` (0x0101) > 40
- [ ] Magnetorquer coils operational (no known anomaly on MTQ subsystem)

## Procedure Steps

### Step 1 --- Assess Reaction Wheel Momentum State
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- request AOCS housekeeping
**Verify:** Retrieve current wheel speeds:
  - `aocs.rw1_speed` (0x0207) --- record value RPM1
  - `aocs.rw2_speed` (0x0208) --- record value RPM2
  - `aocs.rw3_speed` (0x0209) --- record value RPM3
  - `aocs.rw4_speed` (0x020A) --- record value RPM4
**TC:** `GET_PARAM(param_id=0x029B)` (Service 20, Subtype 3) --- `aocs.h_body_nms`
**Verify:** Record `aocs.h_body_nms` (0x029B) --- stored BODY momentum |A.h_wheel|
**Note:** **Body momentum, not wheel speed, is what a magnetic dump removes.** A
four-wheel pyramid can sit at 5000 rpm on every wheel with ZERO body momentum (the
null-space direction), and it can sit at 1200 rpm with a large one. The wheel-speed
criteria below are kept as a familiar cross-check, but 0x029B is the decision
variable, together with the envelope percentage `aocs.momentum_envelope_pct`
(0x02BA) --- `0x02C5` bit 4 is set once that reaches 70 %, which is the honest
"a dump is coming" warning a planner should act on.
**Evaluate:** If any |RPMn| > 4500 RPM, desaturation is REQUIRED.
**Evaluate:** If all |RPMn| < 3000 RPM, desaturation is OPTIONAL (may defer).
**GO/NO-GO:** Proceed if body momentum is above the dump threshold, or any wheel
exceeds 4500 RPM, or by scheduled maintenance.

### Step 2 --- Pre-Desaturation Attitude Snapshot
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s
**Note:** Record attitude error and rates as pre-desat baseline.

### Step 3 --- Command Desaturation
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1, func_id 1) --- start a momentum dump
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- AOCS housekeeping
**Verify:** `aocs.mode == 7` (0x020F, timeout 10 s) --- DESAT
**Note:** This step commanded `AOCS_SET_MODE mode=3` and called it DESAT. **Mode 3
is COARSE_SUN**; DESAT is mode **7**. Sending mode 3 put the spacecraft into a
sun-pointing hold and dumped no momentum at all, while the card's Step 4 waited for
wheel speeds to fall. Either `AOCS_DESATURATE` (func 1, preferred --- it is the
single path every caller lands on, so the refusals below apply to it) or
`AOCS_SET_MODE(mode=7)` will do it.
**Note:** The command is REFUSED with `code="DESAT_REFUSED_FINE"` in FINE_POINT and
with `code="DESAT_INHIBITED"` while `aocs.desat_inhibit` (0x029A) is set. Both are
correct behaviour, not anomalies.
**Note:** The onboard AOCS activates magnetorquer dipoles to bleed momentum from the
wheels while maintaining coarse attitude control.
**Caution:** Attitude error may temporarily increase to 2-3 deg during desat.

### Step 4 --- Monitor Desaturation Progress
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- poll every 30 s
**Verify:** All wheel speeds trending toward zero crossing or nominal bias:
  - |`aocs.rw1_speed`| (0x0207) decreasing
  - |`aocs.rw2_speed`| (0x0208) decreasing
  - |`aocs.rw3_speed`| (0x0209) decreasing
  - |`aocs.rw4_speed`| (0x020A) decreasing
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg (coarse limit during desat)
**Verify:** `aocs.rate_roll/pitch/yaw` (0x0204-6) < 0.5 deg/s
**Verify:** `aocs.h_body_nms` (0x029B) decreasing toward the 5.0e-4 N.m.s deadband
**Note:** **A dump takes tens of minutes, not ten.** Measured on the corrected model:
about **25 min to 0.005 N.m.s and 48 min to 0.001 N.m.s** from the 0.020 N.m.s
reference loading. The old "5-8 min" figure was instantaneous-torque arithmetic
(0.020 / 4.8e-5) and ignored that tau = m x B can only remove the component of
momentum PERPENDICULAR to the local field, so the dump waits on the field sweeping
through the body at about 2 x orbit rate. The on-board timeout is
`desaturation.timeout_s` = **3600 s**, and a dump that hits it raises event
**0x020E AOCS_DESAT_INEFFECTIVE** and sets `aocs.desat_ineffective` (0x029C) rather
than parking in DESAT for ever.
**Timeout:** Do not intervene before 3600 s unless a Verify above fails. The old
600 s deadline aborted every healthy dump.

### Step 5 --- Confirm the Automatic Return to the Pre-Desat Mode
**Note:** **Do not command a mode here.** Since Phase 1, DESAT returns to the mode it
was entered FROM, not unconditionally to NOMINAL, and the spacecraft re-promotes
itself to FINE_POINT once the `0x02C5` preconditions have held for 30 s. Commanding
NOMINAL here would OVERWRITE the ground's commanded mode and cancel that
re-promotion --- which is exactly the mode-management defect that left the live run
sun-pointing for six orbits.
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- AOCS housekeeping
**Verify:** `aocs.mode_demoted == 0` (0x0298, timeout 300 s) --- the spacecraft is
back in `aocs.commanded_mode` (0x0297). Event **0x020F AOCS_MODE_RESTORED** marks it.
**Verify:** `aocs.att_error` (0x0217) converging toward < 1.0 deg
**Note:** Allow up to 300 s for attitude re-convergence. If the return does NOT
happen, `aocs.mode` (0x020F) and the 0x0200 event description say where the
spacecraft went and why; only then command the mode by hand.

### Step 6 --- Post-Desaturation Verification
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) --- final AOCS HK
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 120 s of Step 5
**Verify:** All wheel speeds within nominal range:
  - |`aocs.rw1_speed`| (0x0207) < 3000 RPM
  - |`aocs.rw2_speed`| (0x0208) < 3000 RPM
  - |`aocs.rw3_speed`| (0x0209) < 3000 RPM
  - |`aocs.rw4_speed`| (0x020A) < 3000 RPM
**Verify:** `aocs.rate_roll/pitch/yaw` (0x0204-6) < 0.05 deg/s
**Log:** Record pre/post wheel speeds and attitude performance in pass log.

## Off-Nominal Handling
- If `aocs.mode` (0x020F) does not transition to 7 (DESAT) within 10 s: check the
  refusal reason on the S1.8 failure notice. `DESAT_REFUSED_FINE` means the
  spacecraft is in FINE_POINT --- command NOMINAL (mode 4) first.
  `DESAT_INHIBITED` means `aocs.desat_inhibit` (0x029A) is set, either
  automatically because the payload is IMAGING or by a latched
  `AOCS_SET_DESAT_INHIBIT` (func 96); clear the cause, not the symptom.
- If `aocs.h_body_nms` (0x029B) is not decreasing after 900 s: possible
  magnetorquer fault. Note that a flat h_body for a few minutes is normal --- the
  dump can only act on the component perpendicular to the local field, so progress
  is not monotonic over short windows. If the dump reaches
  `desaturation.timeout_s` the spacecraft raises 0x020E AOCS_DESAT_INEFFECTIVE and
  sets `aocs.desat_ineffective` (0x029C) by itself; that flag, not a stopwatch, is
  the trigger to investigate.
- If `aocs.att_error` (0x0217) > 5.0 deg during desat: abort the dump and recover
  pointing with `AOCS_SET_MODE(mode=3)` (COARSE_SUN, the sun-safe hold). Mode 0 is
  OFF --- commanding it would switch attitude control off on a spacecraft that has
  just lost pointing.
- If a single wheel remains saturated post-desat: May indicate a bearing
  anomaly. Flag for trending and consider reduced-wheel operations.
- If EPS SoC drops below 35 % during desat: Abort and return to NOMINAL.
  Magnetorquer power consumption may be excessive in current orbit geometry.

## Post-Conditions
- [ ] AOCS returned to its COMMANDED mode by itself --- `aocs.mode_demoted`
      (0x0298) = 0, `aocs.mode` (0x020F) = `aocs.commanded_mode` (0x0297)
- [ ] `aocs.h_body_nms` (0x029B) below the 5.0e-4 N.m.s deadband
- [ ] `aocs.desat_ineffective` (0x029C) = 0
- [ ] All reaction wheel speeds < 3000 RPM
- [ ] Attitude error < 1.0 deg and rates < 0.05 deg/s
- [ ] Pre/post desaturation wheel speeds logged
- [ ] Spacecraft ready for imaging or other nominal activities
