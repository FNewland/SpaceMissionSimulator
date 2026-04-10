# COM-007: AOCS Mode Transition Testing
**Subsystem:** AOCS
**Phase:** COMMISSIONING
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Systematically test all commanded AOCS mode transitions to verify the flight software
handles each transition correctly. Validate the five AOCS modes (IDLE, DETUMBLE,
SAFE_POINT, NOMINAL_POINT, FINE_POINT) and confirm attitude performance in each mode.
Verify autonomous safe mode reversion works correctly.

## Prerequisites
- [ ] COM-005 (Sensor Calibration) and COM-006 (Wheel Commissioning) completed
- [ ] AOCS in SAFE_POINT mode (mode 2) with stable attitude
- [ ] Star tracker active and integrated into AOCS loop
- [ ] All four reaction wheels verified operational
- [ ] `eps.bat_soc` (0x0101) > 65%
- [ ] OBC in NOMINAL mode
- [ ] Bidirectional VHF/UHF link active with link margin > 6 dB
- [ ] Minimum 20 minutes of ground station pass remaining

## Procedure Steps

### Step 1 — Confirm Starting State (SAFE_POINT)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 10s
**Verify:** `aocs.att_error` (0x0217) < 5.0 deg within 10s
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s within 10s
**GO/NO-GO:** Starting state confirmed — SAFE_POINT with stable attitude

### Step 2 — Transition: SAFE_POINT to NOMINAL_POINT (Mode 3)
**TC:** `AOCS_SET_MODE(mode=3)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 3 (NOMINAL_POINT) within 30s
**Action:** NOMINAL_POINT uses reaction wheels with star tracker for nadir-pointing. Monitor attitude convergence to nadir target.
**Verify:** `aocs.att_error` (0x0217) < 1.0 deg within 120s
**Verify:** `aocs.rate_roll` (0x0204) < 0.05 deg/s within 120s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.05 deg/s within 120s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.05 deg/s within 120s
**GO/NO-GO:** NOMINAL_POINT achieved with nadir-pointing < 1 degree error

### Step 3 — Transition: NOMINAL_POINT to FINE_POINT (Mode 4)
**TC:** `AOCS_SET_MODE(mode=4)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 4 (FINE_POINT) within 30s
**Action:** FINE_POINT tightens control bandwidth for imaging. Requires star tracker and all four wheels. Monitor for improved pointing stability.
**Verify:** `aocs.att_error` (0x0217) < 0.1 deg within 180s
**Verify:** `aocs.rate_roll` (0x0204) < 0.01 deg/s within 180s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.01 deg/s within 180s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.01 deg/s within 180s
**GO/NO-GO:** FINE_POINT achieved with sub-0.1 degree accuracy

### Step 4 — Hold FINE_POINT for Stability Assessment
**Action:** Maintain FINE_POINT for 5 minutes. Sample attitude error and rates every 30 seconds to assess pointing stability and jitter.
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — repeat every 30s for 5 min
**Verify:** `aocs.att_error` (0x0217) remains < 0.1 deg over 300s
**Verify:** Max rate on any axis < 0.01 deg/s over 300s
**Action:** Record pointing stability statistics (mean, max, std dev) for payload team.
**GO/NO-GO:** Pointing stability meets imaging requirements

### Step 5 — Transition: FINE_POINT to SAFE_POINT (Mode 2)
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 30s
**Verify:** `aocs.att_error` (0x0217) < 10.0 deg within 60s
**Action:** Spacecraft returns to sun-pointing. Attitude error reference changes from nadir to sun vector.
**GO/NO-GO:** Safe reversion to SAFE_POINT confirmed

### Step 6 — Transition: SAFE_POINT to DETUMBLE (Mode 1)
**TC:** `AOCS_SET_MODE(mode=1)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 1 (DETUMBLE) within 15s
**Action:** DETUMBLE activates B-dot magnetorquer control. Reaction wheels may spin down. This is a contingency mode test — only hold briefly.
**Verify:** Body rates remain < 0.5 deg/s (no excitation introduced) within 30s
**GO/NO-GO:** DETUMBLE mode entry confirmed

### Step 7 — Transition: DETUMBLE back to SAFE_POINT (Mode 2)
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 30s
**Verify:** `aocs.att_error` (0x0217) < 10.0 deg within 120s
**Action:** Recovery from DETUMBLE to sun-pointing. This validates the recovery path after a safe mode event.
**GO/NO-GO:** Recovery from DETUMBLE to SAFE_POINT successful

### Step 8 — Transition: SAFE_POINT to IDLE (Mode 0)
**TC:** `AOCS_SET_MODE(mode=0)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 0 (IDLE) within 15s
**Action:** IDLE disables all AOCS actuators. Only hold for 30 seconds — spacecraft will begin drifting. This tests the lowest-level mode entry.
**Verify:** All wheel speeds trending to 0 RPM within 30s
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1) — restore SAFE_POINT immediately
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 30s
**GO/NO-GO:** IDLE mode entry and recovery verified

### Step 9 — Document Mode Transition Matrix
**Action:** Compile results into mode transition verification matrix showing each tested transition, transition time, and attitude performance achieved. Confirm all nominal operational transitions are validated.

| From | To | Result | Transition Time |
|------|-----|--------|----------------|
| SAFE_POINT | NOMINAL_POINT | PASS | ~Xs |
| NOMINAL_POINT | FINE_POINT | PASS | ~Xs |
| FINE_POINT | SAFE_POINT | PASS | ~Xs |
| SAFE_POINT | DETUMBLE | PASS | ~Xs |
| DETUMBLE | SAFE_POINT | PASS | ~Xs |
| SAFE_POINT | IDLE | PASS | ~Xs |
| IDLE | SAFE_POINT | PASS | ~Xs |

**GO/NO-GO:** All mode transitions verified — AOCS commissioning complete

## Off-Nominal Handling
- If any mode transition rejected: Check AOCS mode transition preconditions via `GET_PARAM(0x020F)`. Some transitions have guards (e.g., FINE_POINT requires star tracker active). Resolve precondition and retry.
- If attitude error > 5 deg in FINE_POINT: Star tracker may have lost lock. Check `GET_PARAM(0x0271)` star tracker status. If lost lock, revert to NOMINAL_POINT and investigate.
- If body rates increase in any mode: Check for reaction wheel anomaly. If single wheel failed, revert to SAFE_POINT. AOCS can operate in NOMINAL_POINT on three wheels but not FINE_POINT.
- If IDLE mode does not transition to SAFE_POINT within 60s: Command DETUMBLE (mode 1) as intermediate step. If AOCS completely unresponsive, escalate to AOCS engineer. Check OBC for software fault.
- If spacecraft begins tumbling during IDLE test: Immediately command `AOCS_SET_MODE(mode=1)` for DETUMBLE. Do not attempt direct jump to SAFE_POINT from tumble.

## Post-Conditions
- [ ] All five AOCS modes entered and verified
- [ ] Mode transition matrix completed — all transitions PASS
- [ ] FINE_POINT stability meets imaging requirements (< 0.1 deg, < 0.01 deg/s)
- [ ] NOMINAL_POINT nadir-pointing verified (< 1 deg)
- [ ] Recovery paths (DETUMBLE/IDLE to SAFE_POINT) confirmed
- [ ] Mode Transition Report generated
- [ ] AOCS subsystem fully commissioned
- [ ] GO decision for COM-008 (FDIR Configuration)
