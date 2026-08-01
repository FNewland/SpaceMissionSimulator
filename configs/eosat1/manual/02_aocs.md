# EOSAT-1 Attitude and Orbit Control System (AOCS)

**Document ID:** EOSAT1-UM-AOCS-003
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

The Attitude and Orbit Control System (AOCS) provides three-axis attitude determination and
control for EOSAT-1. It maintains nadir-pointing during nominal ocean imaging operations,
performs detumble after separation, and supports slew manoeuvres for off-nadir target
acquisition. The OBC runs ADCS software for orbit determination (using the onboard GPS
receiver) but does not perform orbital control manoeuvres — EOSAT-1 has no propulsion system.

## 2. Hardware Configuration

### 2.1 Sensors

| Sensor                        | Quantity | Function                                    |
|-------------------------------|----------|---------------------------------------------|
| Star Camera (Zenith)          | 1        | High-accuracy attitude reference (-Z face)  |
| Star Camera (Nadir)           | 1        | High-accuracy attitude reference (+Z face)  |
| 3-Axis Magnetometer A         | 1        | Coarse attitude and magnetic field (primary) |
| 3-Axis Magnetometer B         | 1        | Coarse attitude and magnetic field (redundant) |
| Fibre-Optic Gyros             | 3        | Angular rate measurement (body axes)         |
| Coarse Sun Sensor Heads       | 6        | Sun direction for safe mode (one per face)   |
| GPS Receiver                  | 1        | Orbit determination (zenith antenna)         |

#### 2.1.1 Dual Magnetometers (A/B)

EOSAT-1 carries two redundant 3-axis magnetometers (MAG-A and MAG-B). Only one magnetometer
is active at any time; the other is in cold standby:

| Parameter       | MAG-A (Primary)       | MAG-B (Redundant)     |
|-----------------|----------------------|-----------------------|
| Location        | Deployed boom (tip)   | Deployed boom (mid)   |
| Range           | +/- 60 uT            | +/- 60 uT            |
| Resolution      | 10 nT                 | 10 nT                 |
| Noise           | < 50 nT (1-sigma)    | < 50 nT (1-sigma)    |
| Status Param    | aocs.mag_a_x..z (0x0223-25) | aocs.mag_b_x..z (0x0226-28) |

**Magnetometer Switchover Procedure:**

1. Verify MAG-A health via `mag_a_valid` (0x0214). If invalid or noisy, proceed.
2. Enable MAG-B power via PDM switchable line.
3. Wait 5 seconds for MAG-B stabilisation.
4. Send AOCS configuration command to select MAG-B as active source.
5. Verify `mag_b_valid` (0x0215) = 1 and magnetometer readings are consistent with
   the expected field model.
6. Power off MAG-A to conserve power.

#### 2.1.2 Coarse Sun Sensors — 6-Head Configuration

EOSAT-1 uses 6 individual coarse sun sensor (CSS) heads, one mounted on each spacecraft
face. Each head provides a cosine-response current proportional to the angle between the
Sun vector and the face normal:

| CSS Head | Face | Normal Direction | Param ID |
|----------|------|------------------|----------|
| CSS-1    | +X   | Along-track      | 0x027A   |
| CSS-2    | -X   | Anti-track       | 0x027B   |
| CSS-3    | +Y   | Cross-track      | 0x027C   |
| CSS-4    | -Y   | Anti-cross       | 0x027D   |
| CSS-5    | +Z   | Nadir            | 0x027E   |
| CSS-6    | -Z   | Zenith           | 0x027F   |

The AOCS software computes the Sun vector by combining the responses from all illuminated
CSS heads using geometric projection. A minimum of two illuminated heads is required for
a valid Sun vector determination. In eclipse (all heads dark), the Sun vector is propagated
from the last valid measurement using the orbital model.

#### 2.1.3 Star Cameras — Zenith and Nadir FOV Geometry

EOSAT-1 carries two star cameras mounted on opposing faces:

| Camera   | Face | Boresight  | FOV Half-Angle | Accuracy (3-sigma) |
|----------|------|------------|----------------|---------------------|
| ST-Zenith| -Z   | Zenith     | 15 deg cone    | 0.05 deg            |
| ST-Nadir | +Z   | Nadir      | 15 deg cone    | 0.05 deg            |

**Blinding Zones:** Each star camera is blinded when the Sun, Earth limb, or Moon enters
its field of view (15 deg half-angle cone around boresight):

| Camera    | Blinding Sources                                           |
|-----------|------------------------------------------------------------|
| ST-Zenith | Sun within 15 deg of zenith direction                      |
| ST-Nadir  | Earth limb always in FOV (nadir-pointing); Sun reflection  |

In nominal nadir-pointing, ST-Zenith is the primary attitude reference (looking away from
Earth). ST-Nadir is typically blinded by Earth proximity but becomes useful during slew
manoeuvres or non-nadir attitudes. If both cameras are blinded simultaneously, attitude is
propagated using gyros for up to 60 seconds.

### 2.2 Actuators

| Actuator             | Quantity | Function                                    |
|----------------------|----------|---------------------------------------------|
| Reaction Wheels      | 4        | Fine attitude control (3 + 1 redundant)     |
| Magnetorquers        | 3        | Momentum desaturation and detumble          |

The four reaction wheels are arranged in a pyramid configuration providing full three-axis
control with single-fault tolerance. Nominal wheel speed range is +/-4000 RPM; the yellow
limit is set at +/-5000 RPM to allow margin before desaturation is required.

## 3. AOCS Modes

Mode codes follow the canonical table in `configs/eosat1/aocs/aocs_mode_codes.yaml`
(this section previously carried a contradictory five-mode numbering and has been
corrected):

| Mode ID | Mode Name   | Description                                             |
|---------|-------------|---------------------------------------------------------|
| 0       | OFF         | No attitude control; sensors and actuators unpowered    |
| 1       | SAFE_BOOT   | Post-boot stabilisation, no actuation                   |
| 2       | DETUMBLE    | B-dot rate damping using magnetorquers after separation |
| 3       | COARSE_SUN  | Sun-pointing using CSS + magnetometer + magnetorquers   |
| 4       | NOMINAL     | LVLH nadir tracking: star tracker + MEKF + wheels       |
| 5       | FINE_POINT  | High-precision pointing: ST @4 Hz, all 4 wheels, gated  |
| 6       | SLEW        | Rate-limited manoeuvre to a target quaternion           |
| 7       | DESAT       | Wheel momentum dump via magnetorquers, attitude held    |
| 8       | ECLIPSE     | Nadir hold on gyro-only knowledge (no ST updates)       |
| 9       | INERTIAL    | Hold a commanded ECI quaternion (calibration, stereo)   |

### 3.1 Mode Transitions

```
DETUMBLE --> COARSE_SUN --> NOMINAL <--> ECLIPSE
                               |
                               +--> SLEW --> NOMINAL
                               +--> DESAT --> NOMINAL
                               +--> FINE_POINT (gated) / INERTIAL
```

- **DETUMBLE to COARSE_SUN**: Autonomous when body rates < 0.5 deg/s for 30 seconds.
- **COARSE_SUN to NOMINAL**: Autonomous once CSS is valid, attitude error < 10 deg
  for 60 s and a star tracker is TRACKING (or ground commanded, mode=4).
- **NOMINAL to FINE_POINT**: Ground commanded (`AOCS_SET_MODE` mode=5). The command
  is REFUSED with TM(1,8) unless the `aocs.fine_ready` precondition bitmask
  (0x02C5) reads 0 — use `AOCS_FINE_READY_CHECK` (func 89) first.
- **NOMINAL to SLEW**: Ground commanded via `AOCS_SLEW_TO` (func 10, mode=6).
- **NOMINAL to DESAT**: Ground commanded via `AOCS_DESATURATE` or autonomous when
  the momentum envelope (0x02BA) exceeds 70 %.
- **NOMINAL/FINE to ECLIPSE**: Autonomous on eclipse entry (5 s debounce); returns
  to NOMINAL at eclipse exit.
- Any mode reverts to **COARSE_SUN** on FDIR-triggered safe mode entry.

## 4. Telemetry Parameters

| Param ID | Name           | Unit     | Description                              |
|----------|----------------|----------|------------------------------------------|
| 0x0200   | quat_q1        | —        | Attitude quaternion component q1         |
| 0x0201   | quat_q2        | —        | Attitude quaternion component q2         |
| 0x0202   | quat_q3        | —        | Attitude quaternion component q3         |
| 0x0203   | quat_q4        | —        | Attitude quaternion component q4 (scalar)|
| 0x0204   | rate_roll      | deg/s    | Body rate about X-axis                   |
| 0x0205   | rate_pitch     | deg/s    | Body rate about Y-axis                   |
| 0x0206   | rate_yaw       | deg/s    | Body rate about Z-axis                   |
| 0x0207   | rw1_speed      | RPM      | Reaction wheel 1 speed                   |
| 0x0208   | rw2_speed      | RPM      | Reaction wheel 2 speed                   |
| 0x0209   | rw3_speed      | RPM      | Reaction wheel 3 speed                   |
| 0x020A   | rw4_speed      | RPM      | Reaction wheel 4 speed                   |
| 0x020B   | mag_x          | uT       | Magnetometer X-axis reading              |
| 0x020C   | mag_y          | uT       | Magnetometer Y-axis reading              |
| 0x020D   | mag_z          | uT       | Magnetometer Z-axis reading              |
| 0x020F   | aocs_mode      | enum     | Current AOCS mode (see mode table)       |
| 0x0210   | sc_lat         | deg      | Sub-satellite latitude                   |
| 0x0211   | sc_lon         | deg      | Sub-satellite longitude                  |
| 0x0212   | sc_alt         | km       | Orbital altitude                         |
| 0x0217   | att_error      | deg      | Total attitude pointing error            |
| 0x0218   | rw1_temp       | deg C    | Reaction wheel 1 temperature             |
| 0x0219   | rw2_temp       | deg C    | Reaction wheel 2 temperature             |
| 0x021A   | rw3_temp       | deg C    | Reaction wheel 3 temperature             |
| 0x021B   | rw4_temp       | deg C    | Reaction wheel 4 temperature             |

## 5. Limit Definitions

| Parameter          | Yellow Low | Yellow High | Red Low | Red High |
|--------------------|------------|-------------|---------|----------|
| att_error (deg)    | -1         | 1           | -2      | 2        |
| rate_roll (deg/s)  | -0.5       | 0.5         | -2      | 2        |
| rate_pitch (deg/s) | -0.5       | 0.5         | -2      | 2        |
| rate_yaw (deg/s)   | -0.5       | 0.5         | -2      | 2        |
| rw_speed (RPM)     | -5000      | 5000        | -5500   | 5500     |
| rw_temp (deg C)    | 0          | 60          | -5      | 70       |

A sustained attitude error above 1 deg triggers a yellow alarm. Errors above 5 deg in
NOMINAL mode will cause the FDIR to command a transition to COARSE_SUN.

## 6. Commands

| Command           | Service  | Parameters             | Description                          |
|-------------------|----------|------------------------|--------------------------------------|
| AOCS_SET_MODE     | S8,S1    | mode (0–9)             | Set AOCS operating mode              |
| AOCS_DESATURATE   | S8,S1    | —                      | Initiate RW desaturation manoeuvre   |
| HK_REQUEST        | S3,S27   | sid=2                  | Request AOCS housekeeping packet     |
| GET_PARAM         | S20,S3   | param_id (0x0200–021B) | Read individual AOCS parameter       |
| SET_PARAM         | S20,S1   | param_id, value        | Modify AOCS configuration parameter  |

### 6.1 Desaturation Procedure

1. Verify current wheel speeds via HK or `GET_PARAM` for 0x0207–0x020A.
2. If any wheel exceeds +/-4500 RPM, schedule desaturation.
3. Send `AOCS_DESATURATE` — the AOCS transitions to DESAT mode (mode=7).
4. Monitor wheel speeds converging towards the ±200 RPM desat bias (typical
   duration: 5–15 min at the 1.6 A·m² rod dipole).
5. AOCS autonomously returns to NOMINAL upon completion.

## 7. Operational Notes

1. Each star camera requires a minimum of 3 identified stars for a valid attitude solution.
   During eclipse exit (Sun blinding), attitude is propagated using gyros only for up to 60 s.
2. Gyro bias calibration is performed autonomously every 12 hours using star tracker data.
3. In DETUMBLE mode, only magnetorquers are active. Reaction wheels are not used for
   control until NOMINAL mode is entered.
4. Attitude knowledge accuracy budget is 0.05 deg (3-sigma) in NOMINAL with the zenith
   star camera available; the flight system typically achieves ~0.015 deg 3-sigma
   (downlinked at `aocs.knowledge_err_3sig`, 0x02A7).
5. Only one magnetometer (A or B) is active at a time. Switchover is a ground-commanded
   operation (see Section 2.1.1).
6. The GPS receiver provides position and velocity for orbit determination. The ADCS
   software uses this data for orbit propagation but does not perform orbital control.

## 8. ADCS Commissioning Sequence

The ADCS commissioning sequence is performed during the transition from LEOP to nominal
operations. It follows a strict order to verify each component before enabling the next:

| Step | Activity                          | Prerequisite                      | GO/NO-GO |
|------|-----------------------------------|-----------------------------------|----------|
| 1    | Sensor power-on and health check  | OBC in APPLICATION mode           | Yes      |
| 2    | Magnetometer calibration          | MAG-A powered, rates < 0.5 deg/s | Yes      |
| 3    | CSS verification (all 6 heads)    | Sunlit portion of orbit           | Yes      |
| 4    | Star camera commissioning         | Rates < 0.1 deg/s, eclipse-free  | Yes      |
| 5    | Gyro bias calibration             | Star camera providing valid fix   | Yes      |
| 6    | Attitude determination validation | All sensors operational           | Yes      |
| 7    | Actuator power-on                 | Determination validated           | Yes      |
| 8    | Magnetorquer sign check           | Known magnetic field region       | Yes      |
| 9    | Reaction wheel spin-up test       | Magnetorquers verified            | Yes      |
| 10   | Control gain verification         | All actuators verified            | Yes      |
| 11   | Rate damping test                 | Gains verified                    | Yes      |
| 12   | Active attitude control           | All checks passed                 | Yes      |

Each step requires a GO/NO-GO checkpoint. The Flight Director initiates a poll of all
positions before proceeding to the next step. See `09_leop.md` for detailed procedures.

---

*End of Document — EOSAT1-UM-AOCS-003*
