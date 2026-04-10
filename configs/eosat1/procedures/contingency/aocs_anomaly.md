# CON-001: AOCS Anomaly Recovery
**Subsystem:** AOCS
**Phase:** CONTINGENCY
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Recover the AOCS subsystem following detection of an attitude control anomaly. This procedure
is invoked when any of the following triggers are observed: attitude error exceeds 5 degrees,
any reaction wheel temperature exceeds 60 deg-C, or the star tracker reports a blinded state.
The goal is to restore stable three-axis pointing in NOMINAL mode (nadir pointing) while preserving hardware
safety margins.

## Prerequisites
- [ ] Spacecraft is in an active pass with valid telemetry downlink
- [ ] AOCS telemetry frame (SID 2) is being received at >= 1 Hz
- [ ] Flight Dynamics team is on console and confirming orbit/attitude predictions
- [ ] Procedure CON-002 (EPS Safe Mode Recovery) is available for cross-reference

## Procedure Steps

### Step 1 — Assess Current AOCS State
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.mode` (0x020F) value received within 5s
**Verify:** `aocs.att_error` (0x0217) value received within 5s
**GO/NO-GO:** Telemetry is live and AOCS mode is confirmed — proceed if data is valid

### Step 2 — Evaluate Reaction Wheel Health
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.rw1_speed` (0x0207), `aocs.rw2_speed` (0x0208), `aocs.rw3_speed` (0x0209), `aocs.rw4_speed` (0x020A) — all within +/-6000 rpm
**Verify:** `aocs.rw1_temp` (0x0218), `aocs.rw2_temp` (0x0219), `aocs.rw3_temp` (0x021A), `aocs.rw4_temp` (0x021B) — all < 60 deg-C
**GO/NO-GO:** If any wheel temp >= 60 deg-C or speed oscillating > +/-500 rpm/s, flag that wheel and proceed to Step 3. If all wheels nominal, skip to Step 5.

### Step 3 — Disable Failed Reaction Wheel
**TC:** `SET_PARAM(param_id=aocs.rw<N>_enable, value=0)` (Service 20, Subtype 1)
**Verify:** Affected wheel speed (0x0207-0x020A) decays toward 0 rpm within 30s
**Verify:** `aocs.att_error` (0x0217) does not exceed 10 deg during transition
**GO/NO-GO:** Wheel is confirmed disabled and rates are bounded — proceed

### Step 4 — Command Desaturation Manoeuvre
**TC:** `AOCS_DESATURATE` (Service 8, Subtype 1)
**Verify:** `aocs.rw1_speed` through `aocs.rw4_speed` (0x0207-0x020A) — active wheels trend toward 0 rpm within 120s
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206) — all < 0.5 deg/s during manoeuvre
**GO/NO-GO:** Momentum is reduced to < 20% capacity on all active wheels — proceed

### Step 5 — Verify Body Rates Are Stable
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 25)
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s
**GO/NO-GO:** All body rates are below 0.1 deg/s — proceed to restore pointing

### Step 6 — Restore NOMINAL Mode (Nadir Pointing)
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 4 (NOMINAL) within 10s
**Verify:** `aocs.att_error` (0x0217) converges below 1.0 deg within 120s
**GO/NO-GO:** Attitude error < 1.0 deg and stable — recovery complete

### Step 7 — Escalation: Command DETUMBLE if Multi-Wheel Failure
**TC:** `AOCS_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 1 (DETUMBLE) within 5s
**Verify:** `aocs.rate_roll` (0x0204), `aocs.rate_pitch` (0x0205), `aocs.rate_yaw` (0x0206) — all trending toward 0 within 300s
**GO/NO-GO:** If rates do not decrease below 2.0 deg/s within 300s, escalate to EMG-001

## Off-Nominal Handling
- If attitude error exceeds 15 deg at any step: Immediately command `AOCS_SET_MODE(mode=1)` for DETUMBLE
- If two or more reaction wheels are flagged: Execute Step 7 directly and notify Flight Dynamics
- If EPS bus voltage drops below 26.5V during recovery: Execute CON-002 (EPS Safe Mode Recovery)
- If star tracker remains blinded after DETUMBLE: Execute CON-007 for wheel assessment, plan ground intervention

## Post-Conditions
- [ ] `aocs.mode` (0x020F) = 4 (NOMINAL) or 2 (DETUMBLE) if escalated
- [ ] `aocs.att_error` (0x0217) < 1.0 deg (NOMINAL) or rates < 0.5 deg/s (DETUMBLE)
- [ ] All active reaction wheel temperatures < 55 deg-C
- [ ] Anomaly report filed with wheel status and root cause analysis pending
