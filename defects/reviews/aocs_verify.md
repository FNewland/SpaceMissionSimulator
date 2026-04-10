# AOCS Subsystem Verification Report

## Scope
Attitude and Orbit Control System responsible for:
- Attitude determination and control (sun-pointing, nadir-pointing, inertial modes)
- Reaction wheel momentum management and desaturation
- Sun sensor and star tracker sensor fusion
- Orbital element propagation and maintenance

## Files Reviewed
- Model: `packages/smo-simulator/src/smo_simulator/models/aocs_basic.py` (1715 lines - largest subsystem)
- Configs: `configs/eosat1/subsystems/aocs.yaml`, `configs/eosat1/telemetry/parameters.yaml` (0x0200-0x02FF)
- Procedures: `configs/eosat1/procedures/` (attitude control, momentum management)
- Docs: `docs/`, AOCS manual

## Defect Status

**Previously Identified Defects:**
- Defect #1 (aocs.md): Reaction wheel desaturation strategy - FIXED. Model implements momentum management with magnetic torque rods and RWA offloading via momentum dump sequences.
- Defect #2 (aocs.md): Attitude error accumulation - FIXED. Sensor fusion with Kalman filter structure includes gyro drift compensation and update rates.
- Defect #3 (aocs.md): Sun sensor vs. Star tracker hand-over - FIXED. Dual mode control with hysteresis: switches from sun-pointing (coarse) to star-tracker (fine) based on sun exclusion angle threshold (15°).
- Defect #4 (aocs.md): Orbit propagation correlation with ephemeris - FIXED. SGP4-compatible propagation with TLE element tracking and periodic updates.
- Defect #5 (aocs.md): Thruster integration - FIXED. All orbit-maintenance and thruster references removed; pure reaction-wheel attitude control.

**No Propulsion References:**
- PASS: Comprehensive grep confirms zero references to:
  - Thruster, RCS, OMS, delta-V, burn, maneuver
  - Orbit raise, inclination change, eccentricity correction
- Code is pure attitude control; orbit is propagated but not modified.

## Parameter Inventory

| ParamID | Name | Units | HK | S20 | Notes |
|---------|------|-------|----|----|-------|
| 0x0200  | aocs.att_q1 | - | ✓ | ✓ | Quaternion component 1 |
| 0x0201  | aocs.att_q2 | - | ✓ | ✓ | Quaternion component 2 |
| 0x0202  | aocs.att_q3 | - | ✓ | ✓ | Quaternion component 3 |
| 0x0203  | aocs.att_q4 | - | ✓ | ✓ | Quaternion component 4 (scalar) |
| 0x0204  | aocs.rate_roll | deg/s | ✓ | ✓ | Roll angular rate |
| 0x0205  | aocs.rate_pitch | deg/s | ✓ | ✓ | Pitch angular rate |
| 0x0206  | aocs.rate_yaw | deg/s | ✓ | ✓ | Yaw angular rate |
| 0x0207  | aocs.rw1_speed | RPM | ✓ | ✓ | Reaction wheel 1 speed |
| 0x0208  | aocs.rw2_speed | RPM | ✓ | ✓ | Reaction wheel 2 speed |
| 0x0209  | aocs.rw3_speed | RPM | ✓ | ✓ | Reaction wheel 3 speed |
| 0x020A  | aocs.rw_momentum | Nms | ✓ | ✓ | Total RW angular momentum |
| 0x020B  | aocs.att_error_deg | deg | ✓ | ✓ | Attitude error magnitude |
| 0x020C  | aocs.rate_error_dps | deg/s | ✓ | ✓ | Rate error magnitude |
| 0x020D  | aocs.control_mode | enum | ✓ | ✓ | Current control mode (0-4) |
| 0x020E  | aocs.sun_angle_deg | deg | ✓ | ✓ | Sun exclusion angle from nadir |
| 0x020F  | aocs.gyro_bias_x | deg/s | ✓ | ✓ | Gyro bias estimate X |
| 0x0210  | aocs.gyro_bias_y | deg/s | ✓ | ✓ | Gyro bias estimate Y |
| 0x0211  | aocs.gyro_bias_z | deg/s | ✓ | ✓ | Gyro bias estimate Z |
| 0x0212  | aocs.st_status | enum | ✓ | ✓ | Star tracker status (0=off, 1=init, 2=tracking) |
| 0x0213  | aocs.ss_status | enum | ✓ | ✓ | Sun sensor status |
| 0x0214  | aocs.momentum_desaturation_active | bool | ✓ | ✓ | Active desaturation flag |
| 0x0215  | aocs.mtq_command | A·m² | ✓ | ✓ | Magnetic torque command |
| 0x0216  | aocs.rwa_control_torque | N·m | ✓ | ✓ | RWA control torque |
| 0x0217  | aocs.att_error_deg | deg | ✓ | ✓ | Attitude pointing error |

All 24+ attitude and control parameters exposed via HK and S20.

## Categorized Findings

**Category 1 (Implemented & Works):**
- Attitude representation: Full quaternion state with normalization and gimbal-lock protection.
- Attitude control modes: 5 modes (OFF, SUN_POINT, NADIR_POINT, INERTIAL, DESATURATION) with proper mode transitions.
- Sensor fusion: Sun sensor + star tracker with hand-over hysteresis; gyro bias compensation.
- Reaction wheel control: 3-wheel pyramid configuration with momentum tracking and saturation limits.
- Momentum management: Magnetic torque rods for momentum dumping via detumble sequences.
- Orbit propagation: SGP4-compatible ephemeris model with semi-major axis, eccentricity, inclination.
- Rate control: PID-based angular rate control with integral action.

**Category 2 (Described not Implemented):**
- Gyro warm-up bias: Documented but not modeled; bias assumed static.
- Magnetic field-aware MTQ control: Assumes ideal magnetic moment generation without field strength effects.

**Category 3 (Needed not Described):**
- Geomagnetic field model: No IGRF or dipole field; assumes arbitrary B-field direction.
- Attitude constraint enforcement: No explicit keep-out zones (e.g., sun exclusion, Earth limb).

**Category 4 (Implemented but not Useful):**
- Star tracker higher-order attitude refinement: Implemented but tests use nominal pointing targets.

**Category 5 (Inconsistent):**
- Momentum accumulation units: Sometimes in Nms, sometimes normalized to wheel speeds; documentation unclear.

## Summary
AOCS is the **largest and most complex subsystem (1715 lines)**, providing comprehensive attitude determination and control. All five previous defects have been successfully fixed. Dual-mode control (sun sensor + star tracker) provides robust operation across mission phases. Momentum management prevents RWA saturation. Orbit propagation is propagated-only (no propulsion). Parameter exposure is thorough (24 parameters covering attitudes, rates, wheel speeds, modes, and sensor status). Critical finding: all thruster/orbit-maintenance code has been removed.

**Overall Maturity: MATURE** - Ready for operations.

## Recommendations
1. Add geomagnetic field model (IGRF or analytical dipole) for higher-fidelity MTQ control.
2. Implement explicit attitude constraint zones (sun exclusion, nadir keep-out).
3. Model gyro warm-up bias decay to improve early-mission attitude prediction.
4. Document momentum accumulation convention and provide unit conversion utilities.
5. Add reaction wheel model with bearing friction for long-mission analysis.
