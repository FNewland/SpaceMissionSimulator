# EOSAT-1 ADCS Operations Requirements Document

**Document ID:** EOSAT1-REQ-ADCS-001
**Issue:** 1.0
**Date:** 2026-03-12
**Author:** Flight Dynamics (AOCS) Engineer
**Classification:** UNCLASSIFIED -- For Simulation Use Only

---

## 1. Scope and Purpose

This document defines the operational requirements for the EOSAT-1 Attitude Determination
and Control System (ADCS) from the Flight Dynamics Engineer's perspective. It covers the
full lifecycle from LEOP detumble through nominal nadir-pointing operations, including
commissioning sequences, contingency responses, simulator fidelity needs, and MCS tooling
requirements.

EOSAT-1 is a 6U cubesat in a 500 km sun-synchronous dawn-dusk orbit (97.4 deg
inclination, ~94.6 min period) performing ocean current monitoring. The ADCS provides
three-axis attitude determination and control. There is no propulsion system; orbit
determination is performed onboard via GPS with ground-based validation, but no orbital
control is performed.

---

## 2. Equipment Under ADCS Responsibility

### 2.1 Attitude Sensors

| Equipment | Qty | Mounting | Function | Redundancy |
|-----------|-----|----------|----------|------------|
| 3-Axis Magnetometer A | 1 | Boom (primary) | Coarse attitude determination, B-dot detumble reference, magnetic field measurement | Hot redundant with MAG-B |
| 3-Axis Magnetometer B | 1 | Boom (redundant) | Backup magnetometer, identical to MAG-A | Hot redundant with MAG-A |
| Coarse Sun Sensor (CSS) | 6 heads | Body-mounted, one per face (+X, -X, +Y, -Y, +Z, -Z) | Sun vector determination in body frame for safe-mode pointing | 6 heads provide full-sphere coverage; degraded operation possible with fewer heads |
| Star Camera 1 (ST1) | 1 | Zenith face (+Z) | High-accuracy inertial attitude reference, primary | Cold redundant with ST2; 60 s boot time |
| Star Camera 2 (ST2) | 1 | Nadir face (-Z) | High-accuracy inertial attitude reference, backup | Cold redundant with ST1; 60 s boot time |
| Fibre-Optic Gyroscope | 3-axis | Internal, body-aligned | Angular rate measurement, attitude propagation during ST gaps | No redundancy; bias estimated autonomously every 12 hours |
| GPS Receiver | 1 | Zenith antenna (+Z face) | Orbit determination, position/velocity, time reference | No redundancy; single zenith-facing antenna |

### 2.2 Attitude Actuators

| Equipment | Qty | Configuration | Function | Redundancy |
|-----------|-----|---------------|----------|------------|
| Reaction Wheel | 4 | Tetrahedron (pyramid) | Fine three-axis attitude control | 3+1 redundant; nominal 3-wheel control, 4th provides single-fault tolerance |
| Magnetorquer | 3 | Body-aligned (X, Y, Z) | Detumble (B-dot), momentum desaturation, coarse torque | No redundancy per axis |

### 2.3 Equipment Specifications

#### 2.3.1 Reaction Wheels

| Parameter | Value |
|-----------|-------|
| Configuration | 4-wheel tetrahedron (pyramid) |
| Max speed | +/-5500 RPM |
| Nominal speed | ~1200 RPM |
| Desaturation target speed | 200 RPM |
| Yellow limit | +/-5000 RPM |
| Red limit | +/-5500 RPM |
| Wheel inertia | 0.005 kg*m^2 per wheel |
| Temperature yellow limits | 0 to 60 deg C |
| Temperature red limits | -5 to 70 deg C |
| Power line | eps.pl_aocs_wheels (switchable, default ON) |

#### 2.3.2 Star Cameras

| Parameter | Value |
|-----------|-------|
| ST1 mounting | Zenith (+Z face) |
| ST2 mounting | Nadir (-Z face) |
| Boot time | 60 seconds |
| Status states | 0=OFF, 1=BOOTING, 2=TRACKING, 3=BLIND, 4=FAILED |
| Minimum tracked stars for valid solution | 3 |
| Typical tracked stars | 8-20 |
| Attitude accuracy (with ST) | ~0.05 deg (3-sigma) |
| Sun blinding susceptibility | Near-orbital-plane sun (beta < 5 deg), eclipse boundary transitions |
| Redundancy model | Cold redundant -- ST2 starts OFF, powered on by command |

The dual star camera geometry (zenith + nadir) means:
- ST1 (zenith) has its FOV directed away from Earth. Blinding risk comes from direct
  solar illumination when the sun-spacecraft-boresight geometry places the sun within
  or near the FOV. At low solar beta angles (|beta| < 5 deg), the sun passes near the
  orbital plane and intermittent blinding is possible.
- ST2 (nadir) has its FOV directed toward Earth. Earth albedo and horizon effects can
  cause stray light, but the nadir face is shielded from direct sunlight during nominal
  nadir-pointing.
- In eclipse, both cameras should produce valid solutions (dark sky, no solar blinding).
- The zenith/nadir arrangement provides complementary coverage: when one camera is blinded
  by geometry, the other may still have a clear FOV.

#### 2.3.3 Dual Magnetometer Model

| Parameter | Value |
|-----------|-------|
| MAG-A | Primary, boom-mounted |
| MAG-B | Redundant, boom-mounted |
| Measurement axes | 3 (X, Y, Z) body frame |
| Output units | nT |
| Selection | Ground-commanded via MAG_SELECT (func_id 7) |
| Total field magnitude | Derived parameter (0x0277), ~50,000 nT nominal |

Operational notes for the dual magnetometer:
- Only one magnetometer is selected as the active source at any time.
- The active magnetometer provides the B-field vector used for B-dot detumble control
  and magnetic field reference in the attitude determination filter.
- Switching from MAG-A to MAG-B requires a MAG_SELECT command. The operator should
  verify the output of the newly selected unit is consistent (field magnitude within
  expected range for the current orbital position) before relying on it for control.
- A failed magnetometer (mag_failed flag) cannot be re-selected until the failure is
  cleared.

#### 2.3.4 Six Individual CSS Heads

| Head | Face | Body Axis | Normal Direction |
|------|------|-----------|------------------|
| CSS-1 | +X | +X | Starboard |
| CSS-2 | -X | -X | Port |
| CSS-3 | +Y | +Y | Forward (ram) |
| CSS-4 | -Y | -Y | Aft (wake) |
| CSS-5 | +Z | +Z | Zenith |
| CSS-6 | -Z | -Z | Nadir |

Each CSS head is co-located with the body-mounted solar panel on that face. The six heads
collectively provide a full-sphere sun vector measurement in the spacecraft body frame.
The composite sun vector (css_sun_x, css_sun_y, css_sun_z) is computed onboard from the
individual head currents. The css_valid flag indicates whether the composite vector is
reliable (magnitude > threshold).

In eclipse, all CSS heads see zero illumination and css_valid goes to FALSE. The AOCS
must rely on magnetometer and gyro data during eclipse when CSS is unavailable.

Individual CSS head failure degrades the sun vector accuracy but does not necessarily
invalidate the composite measurement, provided the sun is visible to at least 2-3
remaining heads. A full CSS failure (css_failed flag) sets css_valid to FALSE and
eliminates sun vector capability entirely.

#### 2.3.5 GPS Receiver and Orbit Determination

| Parameter | Value |
|-----------|-------|
| Antenna | Zenith (+Z face), single patch |
| Fix types | 0=none, 1=2D, 2=3D, 3=3D+velocity |
| Nominal fix | 3 (3D + velocity) |
| Tracked satellites | 6-12 typical |
| PDOP yellow limit | 4.0 |
| PDOP red limit | 6.0 |

Orbit determination on EOSAT-1 is GPS-only for onboard use. There is no orbital control
(no propulsion system). The OBC runs ADCS software that processes GPS position and
velocity fixes for:
- Onboard orbit propagation (used for eclipse prediction, ground station visibility
  estimation, and attitude reference frame updates)
- Providing sub-satellite point (latitude, longitude, altitude) in telemetry
- Solar beta angle computation
- GPS velocity components (vx, vy, vz) in telemetry for ground-based orbit
  determination validation

Ground-based orbit determination also uses ranging data from the S-band transponder
(ttc.range_km, 0x0509) and published TLEs. The Flight Dynamics engineer is responsible
for validating the onboard orbit solution against ground-determined orbits and updating
the onboard ephemeris if significant discrepancies are detected.

---

## 3. ADCS Mode State Machine

### 3.1 Mode Definitions

| Mode ID | Mode Name | Sensors Used | Actuators Used | Description |
|---------|-----------|-------------|----------------|-------------|
| 0 | OFF | None | None | No ADCS activity; rates drift; attitude unknown |
| 1 | SAFE_BOOT | Magnetometer | None | Hardware initialisation (30 s); mag calibration |
| 2 | DETUMBLE | Magnetometer | Magnetorquers | B-dot rate damping using magnetorquers |
| 3 | COARSE_SUN | CSS + Magnetometer | Magnetorquers + RW | Coarse sun pointing; ~5 deg accuracy |
| 4 | NOMINAL (NADIR_POINT) | Star Camera + Gyros + RW | Reaction Wheels | Nadir pointing; <0.05 deg accuracy |
| 5 | FINE_POINT | Star Camera + Gyros + RW | Reaction Wheels (all 4) | Tightest control bandwidth; requires all 4 RW + ST valid |
| 6 | SLEW | Star Camera + Gyros + RW | Reaction Wheels | Commanded attitude manoeuvre to target quaternion |
| 7 | DESAT | Magnetometer + Gyros | Magnetorquers + RW | Momentum desaturation; MTQ dump wheel momentum |
| 8 | ECLIPSE | Gyros | Reaction Wheels | Gyro-only propagation during eclipse when ST invalid |

### 3.2 Mode Transition Map

```
                   OFF (0)
                     |
                     v
               SAFE_BOOT (1)
                     | (auto after 30 s)
                     v
               DETUMBLE (2)
                     | (auto: rates < 0.5 deg/s for 30 s)
                     v
              COARSE_SUN (3)
                     | (auto: CSS valid + att_error < 10 deg for 60 s + ST valid)
                     v
              NOMINAL (4) <---------+----------+---------+
                 |    |    |        |          |         |
                 |    |    |        |          |         |
                 v    v    v        |          |         |
            FINE   SLEW  DESAT     |          |         |
           POINT   (6)   (7)      |          |         |
            (5)    |      |        |          |         |
             |     |      |        |          |         |
             +-----+------+--------+          |         |
                                              |         |
              ECLIPSE (8) <---eclipse entry---+         |
                  |                                     |
                  +-----eclipse exit (ST valid)---------+
                  |
                  +-----eclipse exit (no ST)---> COARSE_SUN (3)
```

### 3.3 Transition Guards and Dwell Times

| Transition | Guard Condition | Min Dwell |
|------------|----------------|-----------|
| SAFE_BOOT --> DETUMBLE | time_in_mode >= 30 s | 5 s |
| DETUMBLE --> COARSE_SUN | rate_magnitude < 0.5 deg/s for 30 consecutive seconds | 10 s |
| COARSE_SUN --> NOMINAL | CSS valid AND att_error < 10 deg for 60 s AND ST valid | 20 s |
| NOMINAL --> ECLIPSE | Eclipse entry AND ST not valid | 10 s |
| ECLIPSE --> NOMINAL | Eclipse exit AND ST valid | 5 s |
| ECLIPSE --> COARSE_SUN | Eclipse exit AND ST not valid | 5 s |
| DESAT --> NOMINAL | All active wheel speeds within desat_speed + 100 RPM for 10 s | 10 s |
| Any --> DETUMBLE | Emergency: rate_magnitude > 2.0 deg/s (except OFF, DETUMBLE, SAFE_BOOT) | -- |

### 3.4 Emergency Rate Threshold

If the body rate magnitude exceeds 2.0 deg/s in any mode other than OFF, SAFE_BOOT, or
DETUMBLE, the ADCS autonomously transitions to DETUMBLE mode. This is the highest-priority
autonomous transition and overrides all other mode logic.

---

## 4. Commands and Telemetry (PUS Services)

### 4.1 AOCS Commands (Service 8, Function Commands)

| func_id | Command Name | Parameters | Description |
|---------|-------------|------------|-------------|
| 0 | AOCS_SET_MODE | mode (0-8) | Set ADCS operating mode |
| 1 | AOCS_DESATURATE | (none) | Initiate wheel desaturation (transitions to DESAT mode) |
| 2 | AOCS_DISABLE_WHEEL | wheel_idx (0-3) | Disable specific reaction wheel |
| 3 | AOCS_ENABLE_WHEEL | wheel_idx (0-3) | Enable specific reaction wheel |
| 4 | ST1_POWER | on (0/1) | Power on/off star camera 1 (zenith) |
| 5 | ST2_POWER | on (0/1) | Power on/off star camera 2 (nadir) |
| 6 | ST_SELECT | unit (1/2) | Select primary star camera |
| 7 | MAG_SELECT | on (0/1) | Select magnetometer source (primary/redundant) |
| 8 | RW_SET_SPEED_BIAS | wheel_idx, bias_rpm | Apply speed bias to specific wheel |
| 9 | MTQ_ENABLE | enable (0/1) | Enable or disable magnetorquers globally |

### 4.2 General PUS Services Available to Flight Dynamics

| Service | Subtypes | Purpose |
|---------|----------|---------|
| S1 | TM only | Telecommand verification reports (acceptance, start, completion) |
| S3 | 25 (enable), 26 (disable), 27 (request), 31 (set interval) | Housekeeping management for SID 2 (AOCS), 4 s interval |
| S5 | 5 (enable), 6 (disable), 7 (enable all), 8 (disable all) | Event report control |
| S8 | 1 (function request) | Function management -- all AOCS commands above |
| S11 | 4 (schedule), 5 (delete), 7 (enable), 8 (disable), 16 (delete all), 17 (list) | Time-tagged command scheduling |
| S17 | 1 (connection test) | Link verification |
| S20 | 1 (set), 2 (get) | Direct parameter read/write for AOCS parameters |

### 4.3 Telemetry Parameters

#### 4.3.1 Attitude and Rates

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0200 | aocs.att_q1 | -- | Attitude quaternion X component |
| 0x0201 | aocs.att_q2 | -- | Attitude quaternion Y component |
| 0x0202 | aocs.att_q3 | -- | Attitude quaternion Z component |
| 0x0203 | aocs.att_q4 | -- | Attitude quaternion W (scalar) component |
| 0x0204 | aocs.rate_roll | deg/s | Body rate about X-axis |
| 0x0205 | aocs.rate_pitch | deg/s | Body rate about Y-axis |
| 0x0206 | aocs.rate_yaw | deg/s | Body rate about Z-axis |
| 0x0217 | aocs.att_error | deg | Total attitude pointing error |
| 0x020F | aocs.mode | enum | Current ADCS mode (0-8) |
| 0x0262 | aocs.submode | enum | ADCS sub-mode |
| 0x0264 | aocs.time_in_mode | s | Time in current ADCS mode |
| 0x0216 | aocs.solar_beta | deg | Solar beta angle |

#### 4.3.2 Reaction Wheels

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0207-0x020A | aocs.rw1_speed -- rw4_speed | RPM | Wheel speeds |
| 0x0218-0x021B | aocs.rw1_temp -- rw4_temp | deg C | Wheel temperatures |
| 0x0250-0x0253 | aocs.rw1_current -- rw4_current | A | Wheel current draw |
| 0x0254-0x0257 | aocs.rw1_enabled -- rw4_enabled | bool | Wheel enabled flags |
| 0x025B | aocs.total_momentum | Nms | Total system angular momentum |

#### 4.3.3 Star Cameras

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0240 | aocs.st1_status | enum | ST1 status (0=off, 1=boot, 2=tracking, 3=blind, 4=failed) |
| 0x0241 | aocs.st1_num_stars | count | ST1 tracked star count |
| 0x0243 | aocs.st2_status | enum | ST2 status |

#### 4.3.4 Coarse Sun Sensors

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0245 | aocs.css_sun_x | -- | CSS composite sun vector X |
| 0x0246 | aocs.css_sun_y | -- | CSS composite sun vector Y |
| 0x0247 | aocs.css_sun_z | -- | CSS composite sun vector Z |
| 0x0248 | aocs.css_valid | bool | CSS composite vector valid flag |

#### 4.3.5 Magnetometer

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x020B | aocs.mag_x | nT | Magnetometer X-axis reading |
| 0x020C | aocs.mag_y | nT | Magnetometer Y-axis reading |
| 0x020D | aocs.mag_z | nT | Magnetometer Z-axis reading |
| 0x0277 | aocs.mag_field_total | nT | Total magnetic field magnitude |

#### 4.3.6 Gyroscope

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0270 | aocs.gyro_bias_x | deg/s | Gyroscope X-axis bias estimate |
| 0x0271 | aocs.gyro_bias_y | deg/s | Gyroscope Y-axis bias estimate |
| 0x0272 | aocs.gyro_bias_z | deg/s | Gyroscope Z-axis bias estimate |
| 0x0273 | aocs.gyro_temp | deg C | Gyroscope assembly temperature |

#### 4.3.7 Magnetorquers

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0258 | aocs.mtq_x_duty | % | Magnetorquer X duty cycle |
| 0x0259 | aocs.mtq_y_duty | % | Magnetorquer Y duty cycle |
| 0x025A | aocs.mtq_z_duty | % | Magnetorquer Z duty cycle |

#### 4.3.8 GPS / Navigation

| Param ID | Name | Units | Description |
|----------|------|-------|-------------|
| 0x0210 | aocs.gps_lat | deg | Sub-satellite latitude |
| 0x0211 | aocs.gps_lon | deg | Sub-satellite longitude |
| 0x0212 | aocs.gps_alt | km | Orbital altitude |
| 0x0213 | aocs.gps_vx | km/s | GPS velocity X component |
| 0x0214 | aocs.gps_vy | km/s | GPS velocity Y component |
| 0x0215 | aocs.gps_vz | km/s | GPS velocity Z component |
| 0x0274 | aocs.gps_fix | enum | GPS fix type (0=none, 1=2D, 2=3D, 3=3D+vel) |
| 0x0275 | aocs.gps_pdop | -- | Position dilution of precision |
| 0x0276 | aocs.gps_num_sats | count | GPS tracked satellite count |

### 4.4 Housekeeping Structure (SID 2 -- AOCS)

The AOCS HK packet (SID 2) is transmitted at a 4-second interval and contains 42
parameters packed using the structure defined in `hk_structures.yaml`. This provides the
primary telemetry stream for the Flight Dynamics position.

### 4.5 Event Reports (AOCS)

| Event ID | Name | Severity | Description |
|----------|------|----------|-------------|
| 0x0200 | AOCS_MODE_CHANGE | LOW | ADCS mode transition occurred |
| 0x0201 | WHEEL_DISABLED | MEDIUM | Reaction wheel has been disabled |
| 0x0202 | ATT_WARNING | MEDIUM | Attitude error exceeded threshold |
| 0x0203 | DESATURATION_START | INFO | Wheel desaturation manoeuvre started |
| 0x0204 | DESATURATION_END | INFO | Wheel desaturation manoeuvre completed |

### 4.6 Limit Monitoring

| Parameter | Yellow Low | Yellow High | Red Low | Red High |
|-----------|-----------|-------------|---------|----------|
| aocs.att_error (deg) | -- | 1.0 | -- | 2.0 |
| aocs.rate_roll/pitch/yaw (deg/s) | -0.5 | 0.5 | -2.0 | 2.0 |
| aocs.rw*_speed (RPM) | -5000 | 5000 | -5500 | 5500 |
| aocs.rw*_temp (deg C) | 0.0 | 60.0 | -5.0 | 70.0 |
| aocs.total_momentum (Nms) | -- | 0.5 | -- | 0.8 |
| aocs.gyro_temp (deg C) | 0.0 | 50.0 | -10.0 | 60.0 |
| aocs.gps_pdop | -- | 4.0 | -- | 6.0 |

### 4.7 FDIR Rules Relevant to ADCS

| Rule | Trigger | Level | Action |
|------|---------|-------|--------|
| Attitude exceedance | aocs.att_error > 5 deg | Level 2 | safe_mode_aocs (transition to COARSE_SUN) |
| RW1 overtemp | aocs.rw1_temp > 65 deg C | Level 1 | disable_rw1 |
| RW2 overtemp | aocs.rw2_temp > 65 deg C | Level 1 | disable_rw2 |
| RW3 overtemp | aocs.rw3_temp > 65 deg C | Level 1 | disable_rw3 |
| RW4 overtemp | aocs.rw4_temp > 65 deg C | Level 1 | disable_rw4 |
| Emergency rate | rate_magnitude > 2.0 deg/s | Autonomous | Transition to DETUMBLE |

---

## 5. Operational Procedures

### 5.1 LEOP Procedures (AOCS Involvement)

#### 5.1.1 LEOP-003: Initial Orbit Determination

**AOCS Role:** Process orbit data, update onboard ephemeris.

**Sequence:**
1. Request AOCS HK (S3, SID 2) to obtain GPS fix status, PDOP, tracked satellites.
2. Verify GPS fix type is 3 (3D + velocity).
3. Confirm PDOP < 4.0 (yellow) and tracked satellite count >= 4.
4. Compare GPS-derived position against ground-determined orbit (ranging + TLE).
5. If discrepancy exceeds threshold, update onboard ephemeris via S20 parameter upload.
6. Report orbit solution quality to Flight Director for approval.

**Coordination:** Flight Director (approval), TT&C (range/Doppler data).

#### 5.1.2 LEOP-005: Sun Acquisition

**AOCS Role:** Command AOCS mode transition, monitor body rates.

**Sequence:**
1. Verify ADCS is in DETUMBLE mode after separation and rate damping.
2. Confirm body rates < 0.5 deg/s on all axes for >= 30 s.
3. Verify CSS valid flag is TRUE (spacecraft in sunlight).
4. Observe autonomous transition from DETUMBLE to COARSE_SUN mode, or command
   AOCS_SET_MODE(mode=3) if automatic transition has not occurred.
5. Monitor att_error converging toward ~5 deg.
6. When att_error < 10 deg stable for 60 s and ST is valid, observe or command
   transition to NOMINAL (mode=4).

**Coordination:** Flight Director (authorisation for mode transitions).

### 5.2 Commissioning Procedures

#### 5.2.1 COM-003: AOCS Sensor Calibration

**AOCS Role:** Execute sensor calibration, validate output.

**Sequence:**
1. Verify ADCS in NOMINAL mode with stable pointing (att_error < 1 deg).
2. Request HK with emphasis on sensor parameters.
3. **Magnetometer calibration:**
   a. Read MAG-A (primary) values: mag_x, mag_y, mag_z, mag_field_total.
   b. Verify total field magnitude is within expected range for current orbital
      position (~25,000-65,000 nT depending on latitude).
   c. Switch to MAG-B via MAG_SELECT command.
   d. Read MAG-B values and compare with MAG-A.
   e. Verify agreement within calibration tolerance.
   f. Switch back to MAG-A (or select the better-performing unit).
4. **CSS calibration:**
   a. Verify css_valid = TRUE (not in eclipse).
   b. Record css_sun_x, css_sun_y, css_sun_z vector.
   c. Cross-check sun vector direction against expected sun direction from orbit
      geometry and solar beta angle.
5. **Gyroscope calibration:**
   a. Read gyro_bias_x, gyro_bias_y, gyro_bias_z.
   b. Verify bias magnitudes < 0.01 deg/s (initial expectation).
   c. Record gyro_temp and note temperature sensitivity.
6. **Star camera validation:**
   a. Verify ST1 status = TRACKING, st1_num_stars >= 8.
   b. Power on ST2 (ST2_POWER, on=1). Wait 60 s for boot.
   c. Verify ST2 status = TRACKING after boot.
   d. Select ST2 as primary (ST_SELECT, unit=2).
   e. Verify attitude solution remains consistent (att_error unchanged).
   f. Select ST1 back as primary (ST_SELECT, unit=1).
   g. Power off ST2 (ST2_POWER, on=0) to conserve power.
7. **GPS validation:**
   a. Verify gps_fix = 3 (3D + velocity).
   b. Record gps_lat, gps_lon, gps_alt and compare with ground-computed ephemeris.
   c. Verify PDOP < 4.0.

**PUS Services Used:** S3, S8, S20.
**Coordination:** Flight Director (authorisation).

#### 5.2.2 COM-004: AOCS Actuator Checkout

**AOCS Role:** Test reaction wheels, magnetorquers.

**Sequence:**
1. Verify ADCS in NOMINAL mode with stable pointing.
2. **Reaction wheel checkout (per wheel, RW1-RW4):**
   a. Read current speed, temperature, current draw, enabled flag.
   b. Apply small speed bias via RW_SET_SPEED_BIAS (e.g., +100 RPM).
   c. Verify speed increases by expected amount within 10 s.
   d. Verify current draw increases proportionally.
   e. Verify temperature remains within limits.
   f. Remove bias (apply -100 RPM bias).
   g. Verify return to nominal speed.
3. **3-wheel mode test:**
   a. Disable RW4 (AOCS_DISABLE_WHEEL, wheel=3).
   b. Verify ADCS maintains pointing in NOMINAL mode with 3 wheels.
   c. Re-enable RW4 (AOCS_ENABLE_WHEEL, wheel=3).
4. **Magnetorquer checkout:**
   a. Ensure MTQ enabled (MTQ_ENABLE).
   b. Command DESAT mode briefly to observe MTQ duty cycle activity.
   c. Verify mtq_x_duty, mtq_y_duty, mtq_z_duty respond (non-zero values).
   d. Return to NOMINAL mode.

**PUS Services Used:** S3, S8, S20.
**Coordination:** Flight Director (authorisation for actuator tests).

#### 5.2.3 COM-005: AOCS Mode Transitions

**AOCS Role:** Execute mode transitions, verify stability at each mode.

**Sequence:**
1. Starting from NOMINAL mode (mode=4):
   a. Record att_error, body rates, mode, submode, time_in_mode.
2. **Transition to FINE_POINT (mode=5):**
   a. Verify all 4 RW enabled and ST valid.
   b. Command AOCS_SET_MODE(mode=5).
   c. Verify mode = FINE_POINT, att_error converges below 0.01 deg.
   d. Verify body rates are minimal (< 0.001 deg/s).
3. **Transition to SLEW (mode=6):**
   a. Command AOCS_SET_MODE(mode=6) with target quaternion.
   b. Verify mode = SLEW, body rates increase during slew.
   c. Verify automatic return to NOMINAL when slew complete.
4. **Transition to DESAT (mode=7):**
   a. Command AOCS_DESATURATE.
   b. Verify mode = DESAT, wheel speeds decreasing.
   c. Verify MTQ duty cycles active.
   d. Verify automatic return to NOMINAL when desat complete.
5. **Transition to COARSE_SUN (mode=3):**
   a. Command AOCS_SET_MODE(mode=3).
   b. Verify mode = COARSE_SUN, att_error ~5 deg.
   c. Command AOCS_SET_MODE(mode=4) to return to NOMINAL.
6. **Emergency rate test (if simulation permits):**
   a. Verify that high body rates (> 2.0 deg/s) trigger automatic DETUMBLE.
   b. Verify rate damping and recovery sequence.

**PUS Services Used:** S3, S8, S20.
**Coordination:** Flight Director (authorisation for each mode change).

### 5.3 Full ADCS Commissioning Sequence

This is the end-to-end commissioning sequence that the Flight Dynamics engineer executes,
ordered to build confidence progressively from sensors through determination to actuators
and finally closed-loop control.

#### Phase 1: Sensor Checkout

1. **Magnetometer A verification:**
   - Read mag_x, mag_y, mag_z and mag_field_total.
   - Verify field magnitude consistent with IGRF model for current orbital position.
   - Record calibration baseline values.

2. **Magnetometer B cross-check:**
   - Command MAG_SELECT to switch to MAG-B.
   - Read mag_x, mag_y, mag_z and compare with MAG-A readings.
   - Verify agreement within manufacturer-specified tolerance.
   - Command MAG_SELECT back to MAG-A (primary).

3. **CSS head validation (6 heads):**
   - Verify css_valid = TRUE (not in eclipse).
   - Read composite sun vector (css_sun_x, css_sun_y, css_sun_z).
   - Compare against expected sun direction from orbit geometry.
   - Note: Individual head telemetry is not available in current simulator; only
     composite vector is validated.

4. **Gyroscope checkout:**
   - Read gyro_bias_x, gyro_bias_y, gyro_bias_z.
   - Read gyro_temp and verify within operating range (0-50 deg C yellow).
   - Verify bias magnitudes are small (< 0.01 deg/s initial).

5. **Star camera 1 (zenith) validation:**
   - Verify st1_status = TRACKING (2).
   - Verify st1_num_stars >= 8.

6. **Star camera 2 (nadir) validation:**
   - Power on ST2: ST2_POWER(on=1).
   - Wait 60 s for boot (monitor st2_status transition: OFF -> BOOTING -> TRACKING).
   - Verify st2_status = TRACKING (2).
   - Verify star count is reasonable.

7. **GPS receiver validation:**
   - Verify gps_fix = 3 (3D + velocity).
   - Verify gps_num_sats >= 6.
   - Verify gps_pdop < 4.0.
   - Cross-check gps_lat, gps_lon, gps_alt with ground-determined orbit.

#### Phase 2: Attitude Determination Verification

8. **Single-ST attitude solution:**
   - With ST1 as primary (st_selected=1), verify att_error is small (<0.05 deg).
   - Record quaternion (att_q1..q4) as reference.

9. **ST switchover test:**
   - Select ST2 as primary: ST_SELECT(unit=2).
   - Verify att_error remains consistent (no discontinuity).
   - Select ST1 back as primary: ST_SELECT(unit=1).

10. **Gyro propagation test:**
    - Power off ST1 momentarily: ST1_POWER(on=0).
    - Observe attitude propagated by gyros only (mode may transition to ECLIPSE).
    - Verify att_error grows slowly (~0.001 deg/s drift).
    - Power ST1 back on: ST1_POWER(on=1). Wait 60 s for boot.
    - Verify attitude convergence back to <0.05 deg.

#### Phase 3: Actuator Checkout

11. **Reaction wheel individual spin-up test (RW1-RW4):**
    - For each wheel i (0-3):
      a. Read rw_speed, rw_current, rw_temp, rw_enabled.
      b. Apply speed bias: RW_SET_SPEED_BIAS(wheel=i, bias=+200).
      c. Verify speed increases by ~200 RPM.
      d. Verify current draw increases.
      e. Remove bias: RW_SET_SPEED_BIAS(wheel=i, bias=-200).
      f. Verify return to nominal speed.

12. **Sign checks (torque direction verification):**
    - For each wheel, apply a small positive and negative speed bias.
    - Verify that the resulting body rate change is in the expected direction
      for the wheel's position in the tetrahedron geometry.
    - This confirms the control law sign conventions match the physical
      wheel mounting.

13. **Magnetorquer polarity check:**
    - Enable magnetorquers: MTQ_ENABLE.
    - Enter DESAT mode briefly: AOCS_DESATURATE.
    - Observe mtq_x_duty, mtq_y_duty, mtq_z_duty responses.
    - Verify duty cycle signs are consistent with expected magnetic torque
      direction for the current B-field orientation.
    - Return to NOMINAL.

#### Phase 4: Control Law Verification

14. **Gain check -- step response:**
    - In NOMINAL mode, apply a small disturbance via RW speed bias.
    - Observe attitude error response: overshoot, settling time, steady-state error.
    - Verify control gain (Kp=0.02 nominal) produces acceptable damping.

15. **Rate damping test:**
    - If possible, induce a small attitude disturbance.
    - Verify body rates are damped back to < 0.5 deg/s within expected time.
    - Verify no oscillation or instability.

#### Phase 5: Closed-Loop Active Control

16. **Nadir pointing verification:**
    - Verify NOMINAL mode maintains nadir pointing with att_error < 0.05 deg.
    - Monitor for at least one full orbit period (~95 min) to observe eclipse
      transition behaviour.

17. **Fine pointing verification:**
    - Command FINE_POINT mode (requires all 4 RW + ST valid).
    - Verify att_error < 0.01 deg.
    - Verify body rates < 0.0001 deg/s.

18. **Slew test:**
    - Command slew to a target quaternion.
    - Verify slew execution, rate profile, and settling at target.
    - Verify automatic return to NOMINAL.

19. **Desaturation test:**
    - Command desaturation.
    - Verify wheel speeds converge toward desat target (200 RPM).
    - Verify MTQ duty cycles are active during desaturation.
    - Verify automatic return to NOMINAL.

20. **3-wheel mode test:**
    - Disable one wheel (AOCS_DISABLE_WHEEL).
    - Verify ADCS maintains pointing in NOMINAL mode with 3 wheels.
    - Verify FINE_POINT mode is unavailable (falls back to NOMINAL).
    - Re-enable wheel (AOCS_ENABLE_WHEEL).

### 5.4 Nominal Operations Procedures

#### 5.4.1 NOM-002: Imaging Session

**AOCS Role:** Ensure pointing for imaging windows.

Pre-conditions for imaging GO:
- aocs.mode = NOMINAL (4) or FINE_POINT (5)
- aocs.att_error < 1.0 deg (yellow limit)
- All required RW healthy and enabled
- ST tracking with sufficient star count
- Total momentum < 0.5 Nms (yellow limit)

#### 5.4.2 NOM-005: Momentum Management

**AOCS Role:** Execute momentum desaturation.

**Sequence:**
1. Monitor total_momentum approaching yellow limit (0.5 Nms).
2. Monitor individual wheel speeds approaching +/-5000 RPM.
3. When desaturation is needed, verify not in eclipse (MTQ needs B-field reference).
4. Command AOCS_DESATURATE (func_id 1).
5. Monitor wheel speeds converging toward desat target (200 RPM). Typical duration:
   5-15 min.
6. Monitor MTQ duty cycles during desaturation.
7. Verify automatic return to NOMINAL upon completion.
8. Confirm total_momentum has decreased to acceptable level.

#### 5.4.3 NOM-008: Eclipse Transition

**AOCS Role:** Monitor attitude during eclipse entry and exit.

**Eclipse entry checklist:**
- Verify momentum budget is adequate for eclipse duration.
- Confirm CSS will go invalid (expected).
- If ST remains valid (dark sky, no blinding), ADCS stays in NOMINAL.
- If ST goes blind during transition, ADCS may transition to ECLIPSE mode
  (gyro-only propagation for up to 60 s).
- Monitor att_error drift rate during eclipse (gyro drift ~0.001 deg/s).

**Eclipse exit checklist:**
- Monitor CSS returning to valid as spacecraft enters sunlight.
- Verify ST recovers to TRACKING after sun blinding clears.
- If ST does not recover within 60 s, evaluate COARSE_SUN fallback.
- Confirm att_error returns to pre-eclipse levels.

### 5.5 Contingency Procedures

#### 5.5.1 CTG-002: AOCS Anomaly Recovery

**AOCS Role:** Diagnose and recover ADCS.

**Diagnostic checklist:**
1. Identify current mode and sub-mode.
2. Check att_error magnitude and trend.
3. Check body rates for all three axes.
4. Check all sensor statuses (ST1, ST2, CSS, MAG, GPS).
5. Check all actuator statuses (RW1-4 enabled/speed/temp/current, MTQ enabled/duty).
6. Check FDIR event history (S5 event reports).
7. Check total momentum.

**Recovery decision tree:**
- If rates > 2.0 deg/s: Verify DETUMBLE mode is active. If not, command
  AOCS_SET_MODE(mode=2).
- If single RW failed: Disable failed wheel, verify 3-wheel operation.
- If multiple RW failed: Transition to COARSE_SUN (mode=3) for safe pointing.
- If ST1 failed: Power on ST2, select as primary.
- If both ST failed: Operate in COARSE_SUN mode; CSS + MAG + gyro only.
- If MAG failed: Switch to redundant MAG via MAG_SELECT.
- If CSS failed: Ensure ST is available; cannot enter COARSE_SUN without CSS.

#### 5.5.2 CTG-007: Reaction Wheel Anomaly

**AOCS Role:** Diagnose RW issue, switch to backup configuration.

**Symptoms:**
- RW temperature rising above 60 deg C (yellow)
- RW current increasing anomalously
- RW speed anomaly (not tracking commanded value)
- Bearing degradation indicators (current increase at constant speed)

**Response:**
1. Identify the affected wheel from telemetry.
2. Disable the affected wheel (AOCS_DISABLE_WHEEL).
3. Verify ADCS maintains pointing with 3 remaining wheels.
4. If total momentum is high, command desaturation.
5. If FINE_POINT was active, accept fallback to NOMINAL.
6. If 2 or more wheels fail, transition to COARSE_SUN.
7. Report to Flight Director.

#### 5.5.3 CTG-008: Star Tracker Failure

**AOCS Role:** Switch to backup ST, recalibrate.

**Symptoms:**
- ST status changes to FAILED (4) or persistent BLIND (3).
- att_error increasing.
- num_stars drops to 0.

**Response:**
1. Confirm ST1 failure vs. temporary blinding (check duration, event reports).
2. If ST1 confirmed failed:
   a. Power on ST2: ST2_POWER(on=1).
   b. Wait 60 s for ST2 boot.
   c. Verify ST2 reaches TRACKING status.
   d. Select ST2 as primary: ST_SELECT(unit=2).
   e. Verify attitude solution converges (att_error decreasing).
3. If ST2 also fails: Operate in COARSE_SUN mode (CSS + MAG + gyro).

### 5.6 Emergency Procedures

#### 5.6.1 EMG-005: Loss of Attitude

**AOCS Role:** Execute detumble and reacquisition.

**Symptoms:**
- Body rates > 2.0 deg/s (emergency threshold).
- att_error > 5.0 deg.
- ADCS has autonomously entered DETUMBLE mode.

**Response:**
1. Confirm DETUMBLE mode is active (mode=2).
2. If not, command AOCS_SET_MODE(mode=2).
3. Verify MTQ enabled for B-dot control.
4. Verify MAG valid for B-field reference.
5. Monitor body rates damping toward < 0.5 deg/s.
6. Once rates stable for 30 s, observe auto-transition to COARSE_SUN.
7. Monitor CSS valid and att_error converging to ~5 deg.
8. When ST is valid and att_error < 10 deg for 60 s, observe or command transition
   to NOMINAL.
9. Verify nominal pointing recovered (att_error < 0.05 deg).
10. Report to Flight Director.

---

## 6. Training Scenarios

### 6.1 Existing Scenarios

| Scenario File | Difficulty | Duration | Description | Key Skills |
|---------------|-----------|----------|-------------|------------|
| aocs_star_tracker_failure.yaml | ADVANCED | 2400 s | ST1 hardware failure at T+120 s | Detect ST status change, switch to backup ST2, verify attitude recovery |
| rw_bearing.yaml | ADVANCED | 2700 s | RW2 bearing degradation at T+180 s (gradual onset, 300 s) | Detect temperature anomaly, correlate with current, disable wheel, desaturate |
| aocs_wheel_failure.yaml | ADVANCED | 2400 s | RW3 bearing seizure at T+200 s (gradual onset, 60 s) | Detect speed anomaly, confirm failure, disable wheel, verify 3-wheel pointing |

### 6.2 Required Additional Training Scenarios

#### 6.2.1 LEOP Detumble and Sun Acquisition (BEGINNER)

**Objective:** Practice the LEOP attitude acquisition sequence from separation.
**Setup:** ADCS starts in SAFE_BOOT mode with elevated body rates (~1.5 deg/s).
**Expected actions:**
1. Observe SAFE_BOOT hardware initialisation (30 s).
2. Observe auto-transition to DETUMBLE.
3. Monitor rate damping via magnetorquers.
4. Observe auto-transition to COARSE_SUN when rates < 0.5 deg/s for 30 s.
5. Verify CSS valid, monitor att_error convergence.
6. Power on ST1, wait for boot, observe auto-transition to NOMINAL.

#### 6.2.2 Dual Magnetometer Switchover (INTERMEDIATE)

**Objective:** Practice magnetometer switchover during nominal operations.
**Setup:** Nominal operations. MAG-A failure injected at T+120 s.
**Expected actions:**
1. Detect mag_valid going FALSE or field magnitude anomaly.
2. Command MAG_SELECT to switch to MAG-B.
3. Verify field readings return to expected values.
4. Verify ADCS control modes continue normally.

#### 6.2.3 Eclipse Transition with ST Blinding (INTERMEDIATE)

**Objective:** Practice eclipse entry/exit handling when star tracker is blinded.
**Setup:** Nominal operations approaching eclipse entry.
**Expected actions:**
1. Prepare for eclipse (verify momentum budget, confirm ECLIPSE mode readiness).
2. Observe eclipse entry, CSS goes invalid.
3. Observe ST blinding during transition, ADCS enters ECLIPSE mode.
4. Monitor gyro-only attitude propagation, att_error drift.
5. At eclipse exit, verify ST recovery and attitude reconvergence.

#### 6.2.4 Multi-Wheel Failure (ADVANCED)

**Objective:** Practice safe mode management after 2-wheel failure.
**Setup:** Nominal operations. Two wheels fail sequentially.
**Expected actions:**
1. Detect first wheel failure, disable, verify 3-wheel operation.
2. Detect second wheel failure.
3. Accept automatic transition to COARSE_SUN mode.
4. Verify safe sun-pointing with CSS + MTQ.
5. Manage operations in degraded mode.
6. Report to Flight Director.

#### 6.2.5 GPS Loss and Orbit Determination Degradation (INTERMEDIATE)

**Objective:** Practice GPS anomaly response and orbit determination fallback.
**Setup:** Nominal operations. GPS fix degrades to 0 at T+180 s.
**Expected actions:**
1. Detect gps_fix dropping from 3 to 0.
2. Note PDOP increase and satellite count decrease.
3. Verify ADCS attitude control continues normally (GPS loss does not affect attitude).
4. Report orbit determination degradation to Flight Director.
5. Request ground-based orbit determination support from TT&C (ranging).

#### 6.2.6 Momentum Saturation Emergency (INTERMEDIATE)

**Objective:** Practice momentum management when wheels approach saturation.
**Setup:** Nominal operations with elevated initial wheel speeds (near 5000 RPM).
**Expected actions:**
1. Detect total_momentum approaching yellow limit (0.5 Nms).
2. Detect individual wheel speeds approaching +/-5000 RPM yellow limit.
3. Command desaturation before autonomous FDIR triggers.
4. Monitor desaturation progress.
5. Verify return to NOMINAL with acceptable momentum levels.

---

## 7. MCS Display and Tool Requirements

### 7.1 Current Display Configuration

The Flight Dynamics position has three display pages defined in `displays.yaml`:

**Page 1 -- Attitude:**
- Attitude error gauge (0-10 deg)
- Value table: body rates, mode, submode, time_in_mode
- Sensor table: ST1 status, ST1 star count, ST2 status, CSS valid
- Actuator table: MTQ duty cycles (X, Y, Z), total momentum

**Page 2 -- Reaction Wheels:**
- Wheel speeds line chart (4 traces, 10 min duration)
- Wheel speed value table (RW1-RW4)
- Wheel current value table (RW1-RW4)
- Wheel enabled value table (RW1-RW4)

**Page 3 -- Attitude Trends:**
- Attitude error line chart (10 min duration)
- Body rates line chart (3 traces, 5 min duration)

### 7.2 Display Enhancement Requirements

#### REQ-DISP-001: Gyroscope Monitoring Panel
Add a display widget showing gyro_bias_x, gyro_bias_y, gyro_bias_z and gyro_temp.
The gyro bias trend over time is essential for detecting bias drift and validating
autonomous calibration. Include a line chart for gyro bias trend (30 min duration).

#### REQ-DISP-002: GPS/Navigation Panel
Add a display page or widget showing all GPS parameters:
- gps_fix type with colour-coded status indicator
- gps_pdop gauge (0-6 range, with yellow/red zones)
- gps_num_sats gauge (0-12 range)
- gps_lat, gps_lon, gps_alt value table
- gps_vx, gps_vy, gps_vz value table
The ground track should be visible on the Overview tab world map (already implemented
via Leaflet).

#### REQ-DISP-003: Magnetometer Comparison Panel
Add a display widget showing:
- mag_x, mag_y, mag_z current values
- mag_field_total gauge (20,000-65,000 nT range)
- Indication of which magnetometer (A/B) is currently selected
- Magnetometer field magnitude line chart (5 min duration) for anomaly detection

#### REQ-DISP-004: Star Camera Detailed Status
Enhance the sensor table to show:
- ST1 status with colour coding (OFF=grey, BOOTING=yellow, TRACKING=green,
  BLIND=orange, FAILED=red)
- ST2 status with same colour coding
- ST1 num_stars count
- Which ST is currently selected as primary (visual indicator)
- ST boot progress (if BOOTING, show elapsed/total boot time)

#### REQ-DISP-005: Reaction Wheel Temperature Trend
Add a wheel temperature line chart (RW1-RW4 temps, 30 min duration) to the Reaction
Wheels page. Temperature trends are the earliest indicator of bearing degradation and
must be readily visible.

#### REQ-DISP-006: Mode State Machine Visualisation
Add a graphical mode state machine diagram showing:
- All 9 modes as nodes
- Transition arrows between modes
- Current mode highlighted
- Time in current mode displayed
- Guard condition status (met/not met) for active transitions

#### REQ-DISP-007: Momentum Budget Dashboard
Add a summary widget showing:
- Total momentum gauge (0-1.0 Nms, yellow at 0.5, red at 0.8)
- Individual wheel momentum contribution (bar chart)
- Estimated time to saturation (based on current accumulation rate)
- Desaturation status (idle/active/complete)

#### REQ-DISP-008: CSS Sun Vector 3D Visualisation
Add a 3D unit-sphere widget showing the CSS sun vector direction in the spacecraft
body frame. This provides immediate visual confirmation of sun direction relative to
the spacecraft body, which is valuable during COARSE_SUN mode and eclipse transitions.

### 7.3 Commanding Tool Requirements

#### REQ-CMD-001: Mode Transition Confirmation Dialog
When the operator commands a mode transition via AOCS_SET_MODE, the MCS should display
a confirmation dialog showing:
- Current mode and time_in_mode
- Target mode
- Pre-conditions for the target mode (e.g., ST valid for FINE_POINT, all 4 RW
  for FINE_POINT)
- Warning if pre-conditions are not met

#### REQ-CMD-002: Desaturation Pre-Check
Before sending AOCS_DESATURATE, the MCS should automatically check:
- MTQ enabled (mtq_enabled = TRUE)
- MAG valid (mag_valid = TRUE)
- Not in eclipse (CSS valid or eclipse_flag = FALSE)
- Display wheel speeds and total momentum in the confirmation dialog

#### REQ-CMD-003: Wheel Enable/Disable Safeguard
When the operator disables a reaction wheel:
- Display warning showing how many wheels will remain active
- If disabling would leave fewer than 3 active wheels, display a red warning that
  the ADCS will fall back to COARSE_SUN mode
- Require explicit confirmation

---

## 8. Planner Requirements

### 8.1 Activity Types Requiring ADCS Support

| Activity | ADCS Requirement | Pre-conditions |
|----------|-----------------|----------------|
| imaging_pass | Nominal or Fine Point mode, att_error within limits | aocs.mode == 4 or 5, att_error < 1 deg |
| calibration | Nominal mode, ST tracking | aocs.mode == 4, ST status = TRACKING |
| momentum_desaturation | MTQ enabled, MAG valid, not in eclipse | aocs.total_momentum > 0.5 Nms |

### 8.2 Planner Integration Requirements

#### REQ-PLAN-001: Momentum Prediction
The planner shall include a momentum accumulation model to predict when desaturation
will be needed. Desaturation activities should be automatically scheduled when the
predicted total_momentum exceeds 0.4 Nms (below the 0.5 Nms yellow limit) and placed
during non-eclipse periods.

#### REQ-PLAN-002: Eclipse-Aware Scheduling
The planner shall not schedule desaturation activities during eclipse periods (MTQ
requires B-field reference and CSS is unavailable). Imaging activities requiring
FINE_POINT should not be scheduled during eclipse or within 120 s of eclipse
entry/exit boundaries (ST blinding risk).

#### REQ-PLAN-003: Attitude Settling Time
The planner shall account for attitude settling time when transitioning between modes.
After a SLEW, allow at least 60 s for attitude error to converge below 0.05 deg before
scheduling imaging. After desaturation, allow at least 30 s for NOMINAL mode re-entry
and stabilisation.

#### REQ-PLAN-004: Wheel Speed Constraints
The planner shall monitor predicted wheel speed evolution and avoid scheduling activities
that would increase momentum accumulation rate when any wheel speed is predicted to
exceed 4500 RPM.

#### REQ-PLAN-005: GPS Fix Requirement for Orbit Events
The planner shall verify gps_fix >= 2 (3D fix) before relying on GPS-derived orbital
events (eclipse entry/exit times, ground station visibility windows). If GPS fix is
degraded, the planner should fall back to ground-computed ephemeris.

---

## 9. Simulator Fidelity Requirements

### 9.1 Current Simulator Capabilities

The current AOCS simulator model (`aocs_basic.py`, class `AOCSBasicModel`) provides:

- 9-mode state machine with transition guards and dwell times
- Quaternion attitude representation with rotation dynamics
- Dual star trackers (cold redundant) with boot time, blinding, and failure modes
- CSS sun vector from orbit geometry with noise
- Single magnetometer model with orbit-varying B-field
- 4 reaction wheels with speed, temperature, current, bearing degradation
- 3-axis magnetorquers with per-axis failure
- Gyroscope bias estimation and drift
- GPS receiver model with fix quality, PDOP, satellite count
- Total momentum calculation
- Emergency rate threshold detection
- Eclipse-aware mode transitions
- Comprehensive failure injection (ST, RW, CSS, MAG, MTQ, gyro)

### 9.2 Simulator Enhancement Requirements

#### REQ-SIM-001: Dual Magnetometer Model
**Priority: HIGH**

The current simulator models a single magnetometer. The mission hardware includes
redundant magnetometers A and B. The simulator should model:
- Two independent magnetometer units (MAG-A and MAG-B) with separate readings
- MAG_SELECT command switching between units
- Independent failure injection per unit (mag_a_failed, mag_b_failed)
- Slight calibration differences between units (random offset, scale factor)
- Cross-check capability: the operator should be able to compare MAG-A and MAG-B
  readings to verify consistency

Currently the `mag_select` command handler toggles `mag_valid` on a single unit.
This should be refactored to maintain two separate magnetometer state vectors and
route the active unit's readings to the telemetry parameters.

#### REQ-SIM-002: Individual CSS Head Telemetry
**Priority: MEDIUM**

The current simulator produces only the composite CSS sun vector (css_sun_x/y/z).
The mission has 6 individual CSS heads, one per face. The simulator should:
- Model 6 individual CSS head illumination values based on sun angle and face normal
- Apply cosine response per head (illumination = max(0, dot(sun_vec, face_normal)))
- Compute the composite sun vector from the 6 head values
- Support individual head failure injection (e.g., CSS head 3 stuck at zero)
- Add telemetry parameters for individual head readings (6 new parameters)

This enables training scenarios where partial CSS failure degrades the sun vector
accuracy without fully invalidating it.

#### REQ-SIM-003: Star Camera FOV Geometry
**Priority: MEDIUM**

The current simulator uses a simplified blinding model based on solar beta angle.
Enhance to model the actual zenith/nadir mounting geometry:
- ST1 (zenith): Boresight along +Z. Blinding occurs when the sun enters the FOV
  cone (typically 10-20 deg half-angle). Model as a function of the angle between
  the sun vector in body frame and the +Z axis.
- ST2 (nadir): Boresight along -Z. Blinding occurs when the sun enters the FOV
  cone from below (e.g., during attitude anomalies). Earth albedo effects in nadir
  FOV should be modeled as reduced star count (not full blinding).
- Eclipse transition blinding: Both cameras may experience brief blinding (~10 s)
  at eclipse entry/exit due to rapid illumination changes.

#### REQ-SIM-004: Gyroscope Temperature-Dependent Bias
**Priority: LOW**

The current gyro bias model uses a random walk. Enhance to include temperature-dependent
bias variation:
- Bias magnitude scales with temperature deviation from calibration temperature
- Calibration events (every 12 hours) reset the estimated bias
- Large temperature transients (e.g., eclipse entry/exit) cause temporary bias
  excursions

#### REQ-SIM-005: GPS Antenna Obscuration
**Priority: LOW**

The GPS antenna is mounted on the zenith (+Z) face. Model antenna obscuration effects:
- During attitude anomalies (non-nadir pointing), the zenith antenna may point toward
  Earth, reducing the visible GPS satellite count
- During high body rates (tumble), GPS fix quality degrades due to signal tracking
  difficulty
- Model occasional GPS tracking gaps (drop to 0 satellites for 1-2 s) as occurs
  with real GPS receivers in LEO

#### REQ-SIM-006: Reaction Wheel Speed-Dependent Friction
**Priority: LOW**

The current model includes bearing degradation as a multiplicative friction factor.
Enhance to model:
- Speed-dependent friction curve (Coulomb + viscous + windage)
- Stiction effects at zero crossing (brief speed stall)
- Current draw increase at high speeds (back-EMF effects)

#### REQ-SIM-007: Magnetorquer Magnetic Moment Model
**Priority: MEDIUM**

The current MTQ model applies duty cycles but does not model the actual magnetic
moment generation. Enhance to model:
- Dipole moment proportional to duty cycle and coil current
- B-cross-M torque computation (torque perpendicular to both B-field and dipole)
- This is critical for realistic detumble and desaturation timing

#### REQ-SIM-008: Orbit Determination Propagation
**Priority: MEDIUM**

Model the onboard orbit propagator:
- GPS fixes feed an orbit determination filter
- Between fixes, propagate using Keplerian elements with J2 perturbation
- Model propagation error growth between GPS fixes
- Model the onboard orbit solution divergence if GPS is lost for extended periods

---

## 10. Inter-Position Coordination

### 10.1 Coordination Matrix

| Scenario | Position | Coordination Detail |
|----------|----------|-------------------|
| Initial orbit determination (LEOP-003) | Flight Director, TT&C | TT&C provides range/Doppler data; AOCS processes orbit solution; FD approves |
| Sun acquisition (LEOP-005) | Flight Director | FD authorises attitude manoeuvre; AOCS commands mode transition |
| Imaging session (NOM-002) | Payload Ops | AOCS ensures fine_point or nominal mode for pointing; Payload triggers capture |
| First light (COM-012) | Flight Director, Payload Ops | AOCS confirms pointing accuracy; Payload captures image |
| Eclipse transition (NOM-010) | Power & Thermal | AOCS monitors attitude during eclipse; EPS/TCS manages power balance |
| Momentum management (NOM-007) | (independent) | Typically autonomous; inform FD if momentum approaching limits |
| RW anomaly (CTG-007) | Flight Director | FD authorises; AOCS disables faulty wheel, reconfigures |
| ST failure (CTG-008) | Flight Director | FD authorises; AOCS switches to backup tracker |
| Loss of attitude (EMG-005) | Flight Director | FD authorises emergency recovery; AOCS executes detumble and reacquisition |
| AOCS wheels power (EPS coordination) | Power & Thermal | EPS controls power line eps.pl_aocs_wheels; AOCS cannot operate wheels if power line is OFF |

### 10.2 GO/NO-GO Criteria

The Flight Dynamics position provides GO/NO-GO input to the Flight Director for:

| Decision Point | GO Criteria | NO-GO Criteria |
|---------------|-------------|----------------|
| Imaging readiness | ADCS in NOMINAL or FINE_POINT, att_error < 1.0 deg, all required RW healthy, ST tracking (star count >= 3) | att_error > 1.0 deg, ST blind/failed, < 3 RW active |
| Orbit manoeuvre | GPS fix = 3D, orbit solution validated, momentum within budget | GPS fix degraded, orbit solution not validated |
| Mode transition | Current mode stable (time_in_mode sufficient, rates converged), target mode pre-conditions met | Rates not converged, pre-conditions not met |
| Eclipse entry | Attitude stable, momentum margin adequate for eclipse duration, ECLIPSE mode ready | High momentum (risk of saturation during eclipse), attitude unstable |
| Post-anomaly recovery | Attitude converged, rates damped, sensor suite nominal | Attitude not converged, sensor failures unresolved |

### 10.3 Critical Decision Thresholds

| Threshold | Value | Action |
|-----------|-------|--------|
| total_momentum yellow | > 0.5 Nms | Recommend desaturation to FD |
| RW speed yellow | > +/-5000 RPM | Recommend momentum management |
| att_error yellow | > 1.0 deg | Recommend aborting imaging, investigate |
| att_error red | > 2.0 deg | Escalate to FD; FDIR may trigger safe mode |
| Both ST blind/failed | -- | Recommend COARSE_SUN mode, abort fine pointing |
| GPS fix lost | gps_fix = 0 | Report orbit determination degradation |

---

## 11. Applicable Document References

| Document ID | Title | Relevance |
|-------------|-------|-----------|
| EOSAT1-UM-AOCS-003 | AOCS User Manual | Primary ADCS reference (configs/eosat1/manual/02_aocs.md) |
| EOSAT1-UM-ORB-008 | Orbit Operations Manual | Orbit characteristics, eclipse, ground contacts (configs/eosat1/manual/07_orbit_ops.md) |
| -- | AOCS config | Subsystem configuration (configs/eosat1/subsystems/aocs.yaml) |
| -- | Parameters definition | Telemetry parameter database (configs/eosat1/telemetry/parameters.yaml) |
| -- | HK structures | Housekeeping packet definitions, SID 2 (configs/eosat1/telemetry/hk_structures.yaml) |
| -- | Event catalog | AOCS event definitions (configs/eosat1/events/event_catalog.yaml) |
| -- | Procedure index | All procedures with AOCS role assignments (configs/eosat1/procedures/procedure_index.yaml) |
| -- | MCS positions | AOCS operator position configuration (configs/eosat1/mcs/positions.yaml) |
| -- | Display configuration | AOCS display pages (configs/eosat1/mcs/displays.yaml) |
| -- | Activity types | Planner activities requiring ADCS support (configs/eosat1/planning/activity_types.yaml) |
| -- | AOCS role analysis | Flight Dynamics role analysis (configs/eosat1/mcs/role_analysis/aocs_role.md) |
| -- | AOCS simulator model | Simulator implementation (packages/smo-simulator/src/smo_simulator/models/aocs_basic.py) |

---

## 12. Requirements Traceability

| Req ID | Category | Description | Source |
|--------|----------|-------------|--------|
| REQ-DISP-001 | MCS Display | Gyroscope monitoring panel | Sensor commissioning needs |
| REQ-DISP-002 | MCS Display | GPS/Navigation panel | Orbit determination operations |
| REQ-DISP-003 | MCS Display | Magnetometer comparison panel | Dual magnetometer operations |
| REQ-DISP-004 | MCS Display | Star camera detailed status | ST switchover operations |
| REQ-DISP-005 | MCS Display | RW temperature trend chart | Bearing degradation detection |
| REQ-DISP-006 | MCS Display | Mode state machine visualisation | Mode transition monitoring |
| REQ-DISP-007 | MCS Display | Momentum budget dashboard | Momentum management |
| REQ-DISP-008 | MCS Display | CSS sun vector 3D visualisation | COARSE_SUN mode monitoring |
| REQ-CMD-001 | MCS Commanding | Mode transition confirmation dialog | Safe mode transitions |
| REQ-CMD-002 | MCS Commanding | Desaturation pre-check | Safe desaturation commanding |
| REQ-CMD-003 | MCS Commanding | Wheel disable safeguard | Prevent accidental RW loss |
| REQ-PLAN-001 | Planner | Momentum prediction | Proactive momentum management |
| REQ-PLAN-002 | Planner | Eclipse-aware scheduling | Desaturation/imaging constraints |
| REQ-PLAN-003 | Planner | Attitude settling time | Post-manoeuvre scheduling |
| REQ-PLAN-004 | Planner | Wheel speed constraints | Saturation prevention |
| REQ-PLAN-005 | Planner | GPS fix requirement | Orbit event reliability |
| REQ-SIM-001 | Simulator | Dual magnetometer model | Mission hardware fidelity |
| REQ-SIM-002 | Simulator | Individual CSS head telemetry | Partial failure training |
| REQ-SIM-003 | Simulator | Star camera FOV geometry | Blinding realism |
| REQ-SIM-004 | Simulator | Gyro temperature-dependent bias | Thermal effects fidelity |
| REQ-SIM-005 | Simulator | GPS antenna obscuration | Attitude-dependent GPS |
| REQ-SIM-006 | Simulator | RW speed-dependent friction | Wheel dynamics realism |
| REQ-SIM-007 | Simulator | Magnetorquer magnetic moment | Detumble/desat timing |
| REQ-SIM-008 | Simulator | Orbit determination propagation | OD filter fidelity |

---

*AIG --- Artificial Intelligence Generated Content*
*Reference: https://mpeters.uqo.ca/en/logos-ia-en-peters-2023/*

---

*End of Document -- EOSAT1-REQ-ADCS-001*
