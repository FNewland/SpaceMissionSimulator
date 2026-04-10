# LEOP-004: Sun Acquisition & Rate Damping
**Subsystem:** AOCS
**Phase:** LEOP
**Revision:** 1.0
**Approved:** Flight Operations Director

## Purpose
Verify that the AOCS detumble mode is actively reducing body rates following launcher
separation. Monitor rate damping progress using gyroscope and magnetometer data.
Once body rates are below threshold, transition to safe_point mode to achieve and
maintain sun-pointing attitude for positive power balance and thermal stability.

## Prerequisites
- [ ] LEOP-001 (First Acquisition) completed successfully
- [ ] LEOP-002 (Initial Health Assessment) completed — AOCS subsystem nominal
- [ ] LEOP-003 (Solar Array Verification) completed — arrays deployed
- [ ] `aocs.mode` (0x020F) = 1 (DETUMBLE) confirmed
- [ ] All four reaction wheels reporting telemetry
- [ ] `eps.bat_soc` (0x0101) > 40% (sufficient power for AOCS operations)
- [ ] Bidirectional VHF/UHF link active

## Procedure Steps

### Step 1 — Confirm AOCS Detumble Mode Active
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.mode` (0x020F) = 1 (DETUMBLE) within 10s
**Action:** If AOCS not in detumble, command it explicitly.
**TC:** `AOCS_SET_MODE(mode=1)` (Service 8, Subtype 1) — conditional, only if mode != 1
**GO/NO-GO:** AOCS confirmed in DETUMBLE mode

### Step 2 — Record Initial Body Rates
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27)
**Verify:** `aocs.rate_roll` (0x0204) reported within 10s — record value
**Verify:** `aocs.rate_pitch` (0x0205) reported within 10s — record value
**Verify:** `aocs.rate_yaw` (0x0206) reported within 10s — record value
**Action:** Log initial rates as baseline. Expected post-separation rates: 0.5 to 5.0 deg/s per axis typical.
**GO/NO-GO:** Rate telemetry valid and consistent with gyro/magnetometer cross-check

### Step 3 — Monitor Rate Damping Progress (5-minute interval)
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — repeat at T+5 min
**Verify:** `aocs.rate_roll` (0x0204) < previous value within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < previous value within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < previous value within 10s
**Action:** Confirm all three axes show decreasing rates. Rate damping via B-dot magnetorquer control should reduce rates by approximately 50% every 10-15 minutes.
**GO/NO-GO:** All body rates trending downward

### Step 4 — Verify Rate Damping Convergence
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — repeat at T+15 min
**Verify:** `aocs.rate_roll` (0x0204) < 0.5 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.5 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.5 deg/s within 10s
**GO/NO-GO:** Body rates below 0.5 deg/s threshold — ready for safe_point transition

### Step 5 — Check Reaction Wheel Readiness
**Action:** Before transitioning to safe_point, confirm all reaction wheels are in standby and ready for three-axis control.
**Verify:** `aocs.rw1_speed` (0x0207) = 0 RPM (idle) within 10s
**Verify:** `aocs.rw2_speed` (0x0208) = 0 RPM (idle) within 10s
**Verify:** `aocs.rw3_speed` (0x0209) = 0 RPM (idle) within 10s
**Verify:** `aocs.rw4_speed` (0x020A) = 0 RPM (idle) within 10s
**GO/NO-GO:** All reaction wheels ready for activation

### Step 6 — Transition to Safe Point Mode
**TC:** `AOCS_SET_MODE(mode=2)` (Service 8, Subtype 1)
**Verify:** `aocs.mode` (0x020F) = 2 (SAFE_POINT) within 30s
**Action:** Safe_point mode commands reaction wheels to orient +Z axis toward the sun using coarse sun sensor data. Monitor attitude convergence.
**GO/NO-GO:** Mode transition accepted, AOCS reports SAFE_POINT

### Step 7 — Monitor Sun Acquisition Convergence
**TC:** `HK_REQUEST(sid=2)` (Service 3, Subtype 27) — repeat every 60s for 5 minutes
**Verify:** `aocs.att_error` (0x0217) decreasing over successive samples within 60s
**Verify:** `aocs.att_error` (0x0217) < 10.0 deg within 300s
**Action:** Attitude error should converge to < 10 degrees within 5 minutes of safe_point entry. Monitor reaction wheel speeds for balanced momentum distribution.
**GO/NO-GO:** Sun-pointing achieved within 10 degree accuracy

### Step 8 — Verify Power Generation Improvement
**TC:** `HK_REQUEST(sid=1)` (Service 3, Subtype 27)
**Verify:** `eps.power_gen` (0x0107) > 80W within 60s (sun-pointed, both arrays illuminated)
**Verify:** `eps.bat_soc` (0x0101) stable or increasing within 120s
**GO/NO-GO:** Positive power balance achieved in safe_point mode

### Step 9 — Log Final AOCS State
**Action:** Record final body rates, attitude error, reaction wheel speeds, and power generation. Confirm stable sun-pointing. Issue Sun Acquisition Report.
**Verify:** `aocs.rate_roll` (0x0204) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_pitch` (0x0205) < 0.1 deg/s within 10s
**Verify:** `aocs.rate_yaw` (0x0206) < 0.1 deg/s within 10s
**GO/NO-GO:** Stable sun-pointing confirmed — LEOP-004 complete

## Off-Nominal Handling
- If body rates not decreasing after 15 min: Verify magnetorquer operation via `GET_PARAM(0x0220)`. Check magnetic field model validity for current orbit position. If magnetorquers non-responsive, attempt `AOCS_SET_MODE(mode=1)` reset. Escalate to AOCS engineer.
- If rates below threshold but safe_point transition fails: Re-check reaction wheel telemetry. If any wheel faulted, attempt `AOCS_SET_MODE(mode=2)` again. If persistent failure, remain in detumble and assess three-wheel contingency.
- If attitude error not converging in safe_point: Verify coarse sun sensor data via `GET_PARAM(0x0215)`. If sun sensor readings invalid (spacecraft in eclipse), wait for next sunlit phase and retry.
- If power generation < 50W after sun-pointing: Check array deployment status (LEOP-003). Verify attitude — may need star tracker commissioning for more precise pointing. Acceptable for LEOP phase if battery SOC maintained above 40%.
- If single reaction wheel anomaly: AOCS can operate on three wheels. Log failure, continue sun acquisition. Schedule detailed wheel checkout during commissioning (COM-006).

## Post-Conditions
- [ ] Body rates damped to < 0.1 deg/s on all axes
- [ ] AOCS in SAFE_POINT mode (mode 2)
- [ ] Sun-pointing achieved with attitude error < 10 degrees
- [ ] Power generation > 80W confirming positive energy balance
- [ ] Battery SOC stable or increasing
- [ ] All four reaction wheels operational
- [ ] Sun Acquisition Report distributed
- [ ] GO decision for LEOP-005 (Initial Orbit Determination)
