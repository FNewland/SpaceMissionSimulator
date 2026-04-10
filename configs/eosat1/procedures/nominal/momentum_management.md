# PROC-NOM-004: Reaction Wheel Momentum Unloading
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
- [ ] PROC-NOM-001 Pass Startup completed (or autonomous execution if out of contact)
- [ ] AOCS in NADIR_POINT mode: `aocs.mode` (0x020F) = 0
- [ ] No imaging session in progress: `payload.mode` (0x0600) != 2
- [ ] EPS battery SoC > 40 %: `eps.bat_soc` (0x0101) > 40
- [ ] Magnetorquer coils operational (no known anomaly on MTQ subsystem)

## Procedure Steps

### Step 1 --- Assess Reaction Wheel Momentum State
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- request AOCS housekeeping
**Verify:** Retrieve current wheel speeds:
  - `aocs.rw1_speed` (0x0207) --- record value RPM1
  - `aocs.rw2_speed` (0x0208) --- record value RPM2
  - `aocs.rw3_speed` (0x0209) --- record value RPM3
  - `aocs.rw4_speed` (0x020A) --- record value RPM4
**Evaluate:** If any |RPMn| > 4500 RPM, desaturation is REQUIRED.
**Evaluate:** If all |RPMn| < 3000 RPM, desaturation is OPTIONAL (may defer).
**GO/NO-GO:** Proceed if any wheel exceeds 4500 RPM or if scheduled maintenance.

### Step 2 --- Pre-Desaturation Attitude Snapshot
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s
**Note:** Record attitude error and rates as pre-desat baseline.

### Step 3 --- Command Desaturation Mode
**TC:** `AOCS_SET_MODE` mode=3 (Service 8, Subtype 1) --- command DESAT mode
**Verify:** `aocs.mode` (0x020F) = 3 (DESAT) within 10 s
**Note:** The onboard AOCS will autonomously activate magnetorquer dipoles to
bleed momentum from the wheels while maintaining coarse attitude control.
**Caution:** Attitude error may temporarily increase to 2-3 deg during desat.

### Step 4 --- Monitor Desaturation Progress
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- poll every 30 s
**Verify:** All wheel speeds trending toward zero crossing or nominal bias:
  - |`aocs.rw1_speed`| (0x0207) decreasing
  - |`aocs.rw2_speed`| (0x0208) decreasing
  - |`aocs.rw3_speed`| (0x0209) decreasing
  - |`aocs.rw4_speed`| (0x020A) decreasing
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg (coarse limit during desat)
**Verify:** `aocs.rate_roll/pitch/yaw` (0x0204-6) < 0.5 deg/s
**Timeout:** If desaturation not complete within 600 s (10 min), proceed to Step 5
regardless and evaluate.

### Step 5 --- Command Return to Nominal Pointing
**TC:** `AOCS_SET_MODE` mode=0 (Service 8, Subtype 1) --- command NOMINAL
**Verify:** `aocs.mode` (0x020F) = 0 (NOMINAL / NADIR_POINT) within 15 s
**Verify:** `aocs.att_error` (0x0217) converging toward < 1.0 deg
**Note:** Allow up to 120 s for attitude re-convergence to fine pointing.

### Step 6 --- Post-Desaturation Verification
**TC:** `HK_REQUEST` SID=2 (Service 3, Subtype 25) --- final AOCS HK
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 120 s of Step 5
**Verify:** All wheel speeds within nominal range:
  - |`aocs.rw1_speed`| (0x0207) < 3000 RPM
  - |`aocs.rw2_speed`| (0x0208) < 3000 RPM
  - |`aocs.rw3_speed`| (0x0209) < 3000 RPM
  - |`aocs.rw4_speed`| (0x020A) < 3000 RPM
**Verify:** `aocs.rate_roll/pitch/yaw` (0x0204-6) < 0.05 deg/s
**Log:** Record pre/post wheel speeds and attitude performance in pass log.

## Off-Nominal Handling
- If `aocs.mode` does not transition to DESAT within 10 s: Re-send command. If
  still no transition, attempt `AOCS_DESATURATE` direct command as fallback.
- If any wheel speed not decreasing after 300 s: Possible magnetorquer fault on
  corresponding axis. Abort desat, return to NOMINAL, and schedule investigation.
- If `aocs.att_error` > 5.0 deg during desat: Abort desat immediately. Command
  `AOCS_SET_MODE` mode=0 to recover pointing. Investigate root cause.
- If a single wheel remains saturated post-desat: May indicate a bearing
  anomaly. Flag for trending and consider reduced-wheel operations.
- If EPS SoC drops below 35 % during desat: Abort and return to NOMINAL.
  Magnetorquer power consumption may be excessive in current orbit geometry.

## Post-Conditions
- [ ] AOCS returned to NADIR_POINT (NOMINAL) mode
- [ ] All reaction wheel speeds < 3000 RPM
- [ ] Attitude error < 1.0 deg and rates < 0.05 deg/s
- [ ] Pre/post desaturation wheel speeds logged
- [ ] Spacecraft ready for imaging or other nominal activities
