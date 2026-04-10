# EOSAT-1 Orbit Operations

**Document ID:** EOSAT1-UM-ORB-008
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Overview

This document describes the orbital characteristics of EOSAT-1 and their operational
implications, including eclipse cycles, ground station contact geometry, imaging target
planning for ocean current monitoring, and pass-based scheduling.

## 2. Orbit Definition

| Parameter              | Value                        |
|------------------------|------------------------------|
| Orbit Type             | Sun-Synchronous (SSO)        |
| Semi-Major Axis        | 6828.14 km                   |
| Altitude               | 450 km (circular)            |
| Inclination            | 98 deg                       |
| Eccentricity           | ~0 (near-circular)           |
| LTAN                   | 06:00 / 18:00 (dawn-dusk)    |
| Orbital Period         | ~93.4 min                    |
| Revolutions per Day    | ~15.4                        |
| Nodal Regression Rate  | ~0.9856 deg/day (= 1 deg/day)|
| Ground Track Velocity  | ~7.07 km/s                   |

### 2.1 Sun-Synchronous Properties

The 98 deg inclination causes the orbital plane to precess eastward at approximately
0.9856 deg/day, matching the Earth's mean motion around the Sun. This ensures that:

- The local solar time at the ascending node (LTAN) remains approximately constant.
- The dawn-dusk configuration maximises solar illumination on the body-mounted panels.
- Ocean imaging targets are observed under consistent lighting conditions across revisits,
  which is critical for ocean colour measurements.

### 2.2 Ground Track

The ground track repeats approximately every 5 days. The sub-satellite point traces a
sinusoidal pattern between latitudes +/- 82 deg (complement of the inclination). Adjacent
ground tracks at the equator are separated by approximately 170 km. The 450 km altitude
provides an improved ground sampling distance (~9 m vs ~10 m at 500 km) for ocean feature
detection.

## 3. Eclipse Cycle

### 3.1 Eclipse Geometry

At 450 km altitude, the Earth's shadow cone produces eclipses when the spacecraft passes
through the anti-solar region. For the dawn-dusk SSO:

| Parameter              | Value                        |
|------------------------|------------------------------|
| Max Eclipse Duration   | ~35 min (near solstice)      |
| Min Eclipse Duration   | ~0 min (near equinox)        |
| Eclipse Season         | Seasonal variation           |
| Orbit Fraction (max)   | ~37% of orbital period       |

### 3.2 Seasonal Variation

The dawn-dusk orbit minimises eclipse duration compared to other SSO configurations.
However, eclipses still occur due to the beta angle variation:

| Beta Angle Range | Eclipse Duration | Notes                        |
|------------------|------------------|------------------------------|
| |beta| > 70 deg  | 0 min            | Full sunlight (no eclipse)   |
| |beta| ~ 60 deg  | ~15 min          | Short eclipse                |
| |beta| ~ 30 deg  | ~30 min          | Moderate eclipse             |
| |beta| < 25 deg  | ~35 min          | Maximum eclipse duration     |

### 3.3 Eclipse Operational Impact

During eclipse:

1. **EPS**: Solar array output drops to zero. Battery provides full spacecraft power.
   Depth of discharge depends on eclipse duration and power consumption.
2. **TCS**: Rapid cooling of exposed surfaces. Battery and OBC heaters may activate.
3. **AOCS**: Star tracker may experience Earth/Sun blinding during eclipse transitions.
   Gyro-only propagation is used for up to 60 seconds.
4. **Payload**: Imaging produces dark frames (useful for detector calibration).

## 4. Ground Station Contact Geometry

### 4.1 Station Locations

| Station    | Latitude   | Longitude  | Min Elev. | Approx. Contacts/Day |
|------------|------------|------------|-----------|----------------------|
| Iqaluit    | 63.747 N   | 68.518 W   | 5 deg     | 2–4                  |
| Troll      | 72.012 S   | 2.535 E    | 5 deg     | 2–4                  |

The two-station network provides bipolar coverage:

- **Iqaluit** (Nunavut, Canada): Covers northern hemisphere passes. Benefits from the polar
  orbit's frequent high-latitude ground track crossings.
- **Troll** (Queen Maud Land, Antarctica): Covers southern hemisphere passes. The 72 deg S
  latitude provides excellent coverage of the descending orbital node.

### 4.2 Typical Pass Profile

A typical ground station contact follows this elevation profile:

```
Elevation
(deg)
  80 |          ___
  60 |        /     \
  40 |      /         \
  20 |    /             \
   5 |--/                 \--
   0 +-------------------------> Time
     AOS     TCA         LOS
```

| Phase | Abbreviation | Description                              |
|-------|--------------|------------------------------------------|
| AOS   | Acquisition  | Signal acquired at minimum elevation     |
| TCA   | Closest      | Maximum elevation (shortest slant range) |
| LOS   | Loss         | Signal lost below minimum elevation      |

### 4.3 Contact Duration vs. Maximum Elevation

| Max Elevation | Approx. Duration | Link Quality     |
|---------------|-------------------|------------------|
| 5–10 deg      | 3–5 min           | Marginal         |
| 10–30 deg     | 5–8 min           | Acceptable       |
| 30–60 deg     | 8–10 min          | Good             |
| 60–90 deg     | 10–12 min         | Excellent        |

Higher maximum elevation passes provide longer contact times and better link margins.
Passes with maximum elevation below 10 deg should be used only for critical commanding.
Low-rate mode (1 kbps) extends the usable contact window for low-elevation passes.

### 4.4 Visibility and Scheduling

The bipolar station network provides complementary coverage. Combined, the two stations
provide:

- **Total contacts per day**: 4–8
- **Cumulative contact time**: 30–60 min/day
- **Maximum gap between contacts**: ~4 hours (typical), ~6 hours (worst case)

The contact window timeline in the MCS displays a 24-hour horizontal bar showing Iqaluit
passes (blue) and Troll passes (green), with AOS/LOS times and maximum elevation annotated.

## 5. Orbit Determination

Orbit determination is performed using:

- **Onboard GPS**: The zenith-mounted GPS receiver provides continuous position and velocity
  measurements (sub-satellite latitude, longitude, altitude). This is the primary orbit
  determination source.
- **Ranging data**: Slant range measurements from the VHF/UHF transponder during contacts,
  used for independent verification.
- **Two-line elements (TLE)**: Published by space surveillance networks, used as backup.

The `range_km` (0x0509) and `contact_elevation` (0x050A) telemetry parameters provide
real-time range and elevation data during ground contacts. The GPS-derived position is
available in `sc_lat` (0x0210), `sc_lon` (0x0211), and `sc_alt` (0x0212).

The OBC runs ADCS software for orbit determination only. There is no orbital control
capability — EOSAT-1 does not carry a propulsion system.

## 6. Orbit Maintenance

EOSAT-1 does NOT carry a propulsion system for orbit control or station-keeping. The satellite
is a passive payload platform operating under natural orbital decay.

**Orbital Decay Characteristics:**

| Parameter             | Value                        |
|-----------------------|------------------------------|
| Drag Area             | ~0.06 m² (6U, 1U face)      |
| Ballistic Coefficient | ~200 kg/m²                   |
| Expected Decay Rate   | 2–5 km/year                  |
| Mission Lifetime      | ~7–15 years from 450 km      |

**Operational Implications:**

1. **No Orbit Correction Capability:** The mission has no thruster system. Altitude decay is
   natural and unavoidable. Ground operations cannot perform station-keeping maneuvers.
2. **Altitude Monitoring:** Continuous GPS-based altitude tracking is essential for:
   - Predicting mission end-of-life and deorbit window
   - Accurate ground track prediction (critical for imaging target planning)
   - Early warning of anomalous decay (potential sign of atmosphere disturbance event)
3. **Mission Planning:** All payload scheduling, imaging window availability, and contact
   window geometry assume the natural, gradually-decaying orbit. The planner uses current
   orbit state to predict future pass availability.

**Telemetry for Orbit Monitoring:**
- `sc_alt` (0x0212): Orbital altitude from GPS (km)
- `sc_lat` (0x0210): Sub-satellite latitude (deg)
- `sc_lon` (0x0211): Sub-satellite longitude (deg)

## 7. Imaging Target Planning for Ocean Currents

### 7.1 Target Ocean Regions

EOSAT-1 imaging is planned around ocean current monitoring regions:

| Region                      | Latitude Range   | Target Feature                   |
|-----------------------------|------------------|----------------------------------|
| Gulf Stream (western boundary)| 25–45 N         | Current meander, eddies          |
| Kuroshio Current            | 25–40 N          | Western Pacific boundary current |
| Antarctic Circumpolar Current| 45–65 S         | Frontal zones, upwelling         |
| Benguela Current            | 15–35 S          | Eastern boundary upwelling       |
| North Atlantic Drift        | 45–60 N          | Subpolar gyre circulation        |

### 7.2 Imaging Opportunity Computation

Imaging opportunities are computed by the mission planner based on:

1. **Orbital geometry**: Sub-satellite point within target region.
2. **Solar illumination**: Target must be sunlit (solar elevation > 10 deg at target).
3. **Spacecraft constraints**: Battery SoC > 40%, AOCS in NADIR_POINT, att_error < 0.5 deg.
4. **Data budget**: Sufficient mass memory and downlink capacity for the image data volume.
5. **Contact timing**: Downlink pass available within a reasonable time after acquisition.

## 8. Pass-Based Scheduling

The mission planner uses a pass-based scheduling approach where activities are allocated
to individual orbital passes:

### 8.1 Pass Types

| Pass Type     | Activities                                              |
|---------------|---------------------------------------------------------|
| Imaging Pass  | Payload activation, image acquisition, return to standby|
| Downlink Pass | Science data dump, stored TM playback (Iqaluit or Troll)|
| Combined Pass | Imaging followed by downlink (if contact window allows) |
| Idle Pass     | Housekeeping only, battery charging                     |

### 8.2 Scheduling Constraints

The scheduler must satisfy the following constraints:

| Constraint          | Rule                                                   |
|---------------------|--------------------------------------------------------|
| Power budget        | SoC must remain > 25% at all times (predict per orbit) |
| Data volume         | Mass memory must not exceed 90% capacity               |
| Thermal             | Payload duty cycle limited to 10 min per imaging window|
| Contact conflicts   | No imaging during active downlink pass                 |
| Minimum gap         | At least 5 min between end of imaging and start of downlink |

### 8.3 Power and Data Budget Tracking

The planner tracks two budgets across the planning horizon:

- **Power budget**: Predicted battery SoC trajectory based on solar generation model,
  eclipse timing, and planned load profile. The planner will not schedule imaging if
  the predicted SoC drops below 40% at any point.
- **Data budget**: Accumulated data volume from imaging vs. downlink capacity.
  Scene-dependent compression ratios are estimated based on the target type (ocean,
  coastal, land). Downlink capacity is computed from contact duration and data rate mode.

## 9. Relevant Telemetry

| Param ID | Name              | Unit | Description                          |
|----------|-------------------|------|--------------------------------------|
| 0x0210   | sc_lat            | deg  | Sub-satellite latitude (GPS)         |
| 0x0211   | sc_lon            | deg  | Sub-satellite longitude (GPS)        |
| 0x0212   | sc_alt            | km   | Orbital altitude (GPS)               |
| 0x0509   | range_km          | km   | Slant range to ground station        |
| 0x050A   | contact_elevation | deg  | Ground station elevation angle       |
| 0x0108   | eclipse_flag      | bool | Eclipse state                        |

---

*End of Document — EOSAT1-UM-ORB-008*
