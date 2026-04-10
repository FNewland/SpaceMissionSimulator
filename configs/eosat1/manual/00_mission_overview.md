# EOSAT-1 Mission Overview

**Document ID:** EOSAT1-UM-MIS-001
**Issue:** 2.0
**Date:** 2026-03-13
**Classification:** UNCLASSIFIED — For Simulation Use Only

---

## 1. Purpose

This document provides a top-level overview of the EOSAT-1 (Earth Observation Satellite 1) mission,
including mission objectives, spacecraft configuration, orbit parameters, ground segment, and
mission phases. It serves as the entry point into the EOSAT-1 spacecraft user manual suite.

## 2. Mission Objectives

EOSAT-1 is a 6U multispectral imaging cubesat in low-Earth orbit designed for ocean current
monitoring and coastal zone observation. Primary mission objectives are:

- Monitor ocean currents through systematic multispectral imaging of sea surface colour
  signatures, exploiting ocean colour bands (blue 443 nm, green 560 nm, red 665 nm, NIR 865 nm).
- Acquire medium-resolution multispectral imagery of coastal zones and ocean regions with a
  revisit time of approximately 5 days.
- Demonstrate autonomous onboard data handling and scene-dependent image compression.
- Support ground-based mission planning, pass-based scheduling, and data retrieval exercises.
- Provide imaging data for ocean current velocity estimation, upwelling detection, and
  chlorophyll concentration mapping.

## 3. Orbit Parameters

| Parameter              | Value                        |
|------------------------|------------------------------|
| Orbit Type             | Sun-Synchronous (SSO)        |
| Altitude               | 450 km (nominal)             |
| Inclination            | 98 deg                       |
| LTAN                   | Dawn-dusk (~06:00/18:00 LT)  |
| Orbital Period         | ~93.4 min                    |
| Eclipse Duration       | ~35 min (seasonal variation)  |
| Repeat Cycle           | ~5 days                      |
| Ground Track Velocity  | ~7.07 km/s                   |

The 450 km altitude provides an improved ground sampling distance compared to higher orbits,
while maintaining a sun-synchronous orbit at 98 deg inclination. The dawn-dusk SSO provides
favourable illumination conditions for the multispectral imager and maximises solar panel
exposure, reducing the depth of discharge on the battery during eclipse periods.

## 4. Spacecraft Bus Overview

EOSAT-1 is a 6U cubesat with three-axis stabilised platform and the following primary subsystems:

| Subsystem                          | Abbreviation | Description                                      |
|------------------------------------|--------------|--------------------------------------------------|
| Electrical Power Subsystem         | EPS          | 28V regulated bus, 6 body-mounted GaAs panels, Li-Ion battery, cold-redundant PDM |
| Attitude and Orbit Control System  | AOCS         | 4 reaction wheels, dual magnetometers (A/B), 2 star cameras, 6 CSS heads, gyros |
| On-Board Data Handling             | OBDH         | Cold-redundant OBC (A/B), dual CAN bus, mass memory, TC/TM processing |
| Thermal Control Subsystem          | TCS          | Passive MLI/radiators, battery heaters (active), orientation-based passive thermal |
| Telemetry, Tracking and Command    | TTC          | Cold-redundant VHF/UHF transponder, dedicated PDM command channel, burn wire antenna |
| Payload                            | PLD          | Multispectral imager (4 ocean colour bands), nadir-mounted |

### 4.1 Spacecraft Coordinate Frame

The spacecraft body frame is defined as:

- **+X**: Along-track (velocity vector in nominal nadir pointing)
- **+Y**: Cross-track (towards the starboard solar panel)
- **+Z**: Nadir (towards Earth in nominal attitude); nadir star camera and payload mounted here
- **-Z**: Zenith; zenith star camera and GPS antenna mounted here

Each of the six body faces (+X, -X, +Y, -Y, +Z, -Z) carries a body-mounted solar panel and
a coarse sun sensor head.

### 4.2 Mass and Power Budget

| Parameter        | Value          |
|------------------|----------------|
| Dry Mass         | ~12 kg (6U)   |
| Peak Power Gen.  | ~40 W (sunlit) |
| Avg. Power Cons. | ~20 W          |
| Battery Capacity | 40 Ah @ 28V    |

### 4.3 Redundancy Architecture

EOSAT-1 employs cold redundancy for critical subsystems:

| Component       | Redundancy    | Switchover                                    |
|-----------------|---------------|-----------------------------------------------|
| OBC             | A/B (cold)    | Ground command or FDIR after 3 watchdog resets|
| Transponder     | Primary/Redundant (cold) | Ground command or FDIR after link loss |
| PDM             | A/B path (cold) | Ground command                              |
| Magnetometer    | A/B (cold)    | Ground command                                |
| Star Camera     | Zenith/Nadir  | Automatic selection based on FOV availability |

## 5. Ground Segment

### 5.1 Ground Stations

| Station    | Location                    | Min Elevation | Primary Role       |
|------------|-----------------------------|---------------|--------------------|
| Iqaluit    | 63.747 N, 68.518 W          | 5 deg         | TT&C + Data Dump   |
| Troll      | 72.012 S, 2.535 E           | 5 deg         | TT&C + Data Dump   |

EOSAT-1 uses a two-station ground segment providing polar coverage in both hemispheres:

- **Iqaluit** (Nunavut, Canada): Northern hemisphere station at 63.747 deg N latitude.
  Provides primary TT&C and science data downlink for northern passes. The high latitude
  enables multiple contacts per day with the polar sun-synchronous orbit.
- **Troll** (Queen Maud Land, Antarctica): Southern hemisphere station at 72.012 deg S latitude.
  Provides complementary TT&C and data dump coverage for southern passes, reducing the
  maximum gap between ground contacts.

All ground stations communicate via VHF/UHF (449 MHz uplink / 401.5 MHz downlink). Typical
contact durations range from 5 to 12 minutes depending on pass geometry and maximum
elevation angle. The two-station network provides approximately 4-8 contacts per day with
a maximum gap of approximately 4-6 hours between contacts.

### 5.2 Mission Control System

The Mission Control System (MCS) provides:

- Real-time telemetry monitoring and limit checking via 6 operator positions.
- Telecommand generation, validation, and uplink with PUS command builder.
- Flight dynamics support (orbit determination via onboard GPS).
- Pass-based mission planning and payload scheduling with power/data budget tracking.
- Alarm journal with S5 event and S12 monitoring violation tracking.
- Stored TM playback and orbit-level trending.
- GO/NO-GO coordination workflow for critical operations.
- Contact window timeline with per-station visibility display.

### 5.3 Operator Positions

The MCS supports 6 operator positions with role-based access control:

| Position          | Abbreviation | Responsibility                              |
|-------------------|--------------|---------------------------------------------|
| Flight Director   | FD           | Overall mission authority, GO/NO-GO polls   |
| Power/Thermal     | PT           | EPS and TCS monitoring and commanding       |
| AOCS Operator     | AOCS         | Attitude determination and control           |
| TTC Operator      | TTC          | Communications link management               |
| OBDH Operator     | OBDH         | OBC health, data handling, software mgmt     |
| Payload Operator  | PLD          | Imaging operations and data management       |

## 6. Mission Phases

| Phase                | Duration       | Description                                          |
|----------------------|----------------|------------------------------------------------------|
| Separation           | T+0 to T+30min| Separation from launcher, 30-min timer, unswitchable line power-on |
| LEOP                 | 0–3 days       | Launch and Early Orbit Phase: first contact, bootloader ops, detumble |
| Commissioning        | 3–30 days      | Subsystem checkout, ADCS calibration, payload activation |
| Nominal Operations   | 30 days–EOM    | Routine ocean imaging, data downlink, orbit monitoring |
| Extended Operations  | EOM+           | Reduced operations if mission is extended            |
| Decommissioning      | Final phase    | Passivation and controlled deorbit                   |

### 6.1 Separation Sequence

The separation sequence from the launcher follows a 7-phase state machine:

| Phase             | Duration   | Description                                       |
|-------------------|------------|---------------------------------------------------|
| PRE_SEPARATION    | —          | Awaiting separation signal from launcher          |
| SEP_DETECTED      | Instant    | Separation switches open, timer starts            |
| POWER_STABILIZE   | ~30 s      | EPS initialises, unswitchable lines power OBC+RX  |
| TIMER_WAIT        | ~30 min    | Regulatory hold: no transmissions for 30 minutes  |
| ANTENNA_DEPLOY    | ~60 s      | Burn wire antenna deployment                      |
| BEACON_START      | ~10 s      | Begin beacon transmissions (SID 11, low-rate)     |
| NOMINAL           | —          | Transition to LEOP operations                     |

During the 30-minute timer wait, only the OBC (via unswitchable PDM line) and receiver
(via dedicated PDM command channel) are powered. No RF transmissions occur.

### 6.2 LEOP Concept

During LEOP, the spacecraft initially operates in BOOTLOADER mode with restricted command
set and beacon-only telemetry (SID 11). The LEOP sequence proceeds as follows:

1. **First Contact**: Ground acquires RF signal from beacon packet. Verify spacecraft health
   from beacon HK parameters.
2. **Application Boot**: Ground commands transition from BOOTLOADER to application software.
   Verify successful boot via full housekeeping telemetry.
3. **Detumble**: AOCS operates in DETUMBLE mode using magnetorquers. Monitor body rates
   converging to below 0.5 deg/s.
4. **Sun Acquisition**: AOCS transitions to SAFE_POINT mode using CSS and magnetorquers
   for sun-pointing.
5. **Sequential Power-On**: Enable subsystems one at a time, verifying health at each step.
6. **ADCS Commissioning**: Commission attitude sensors and actuators in sequence (see
   `09_leop.md` for detailed procedure).
7. **Transition to Nominal**: After all GO/NO-GO checkpoints pass, command transition to
   NADIR_POINT mode and begin nominal operations.

### 6.3 Nominal Operations Concept

In nominal operations the spacecraft maintains nadir-pointing attitude (NADIR_POINT mode)
for ocean current monitoring. Imaging targets are planned based on ocean current regions
of interest and scheduled on a pass-by-pass basis accounting for power and data budgets.
The payload is activated approximately 60 seconds before the imaging window and returned
to STANDBY afterwards. Stored image data is downlinked during Iqaluit and Troll ground
station passes.

Typical daily operations include:

- 4-8 ground contacts across Iqaluit and Troll stations
- 2-6 imaging opportunities over target ocean regions
- Continuous housekeeping telemetry storage for playback during contacts
- Power and data budget monitoring for next-orbit planning

## 7. Related Documents

| Document                          | Reference              |
|-----------------------------------|------------------------|
| EPS Operations Manual             | `01_eps.md`            |
| AOCS Operations Manual            | `02_aocs.md`           |
| TCS Operations Manual             | `03_tcs.md`            |
| OBDH Operations Manual            | `04_obdh.md`           |
| TTC Operations Manual             | `05_ttc.md`            |
| Payload Operations Manual         | `06_payload.md`        |
| Orbit Operations                  | `07_orbit_ops.md`      |
| FDIR Reference                    | `08_fdir.md`           |
| LEOP Operations Guide             | `09_leop.md`           |
| Command and Telemetry Reference   | `10_command_reference.md` |

---

*End of Document — EOSAT1-UM-MIS-001*
